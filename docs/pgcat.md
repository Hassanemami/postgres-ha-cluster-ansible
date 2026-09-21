# PgCat - SQL-aware read/write splitting

## What this is and why it exists

Everywhere else in this project, read/write splitting works by **port**:
your application connects to port 5000 to write (always the primary) or
port 5001 to read (load-balanced replicas). That's implemented by HAProxy,
and HAProxy can do it *only* at that granularity, because it's a plain TCP
proxy - it forwards bytes without understanding SQL. Once a connection is
open, every query on it goes to the same backend for the life of that
connection. See `docs/haproxy.md` for the full explanation of why this is
a hard technical limit, not a configuration gap.

**PgCat is different**: it actually speaks the PostgreSQL wire protocol,
parses the text of every query that comes in, and decides per-query
whether it's a `SELECT` (send to a replica) or something else (send to the
primary) - inside a single, long-lived client connection. Your application
can open one connection and just run whatever SQL it wants; PgCat handles
the routing transparently.

This capability is real (it uses the `sqlparser` crate to genuinely parse
SQL, not a regex hack), but it comes from a **fundamentally different,
heavier component** than everything else in this stack, with its own
sharp edges. Read this whole document before pointing production traffic
at it - the two-port HAProxy approach is simpler, more battle-tested, and
still the recommended default. PgCat is here because you asked for
per-query routing specifically; it is not a strict upgrade.

## How it's wired into this project

```
App --(port 6433, one connection, mixed SQL)--> HAProxy (pgcat_smart_pool)
                                                        |
                                     round-robins to any healthy node's
                                                        |
                                        PgCat container (port 6432)
                                          |virtual pool "app"|
                                          parses every query
                                    SELECT -> a replica (10.x.x.x:5432)
                                    else    -> the primary (10.x.x.x:5432)
```

- PgCat runs as a **Podman container** on every node (`tasks/pgcat.yml`),
  because upstream ships no apt/yum package - only a container image
  (`ghcr.io/postgresml/pgcat`) or source you'd compile with Rust's
  `cargo`. Podman was chosen over Docker because it's daemonless and
  packaged natively on both Debian and RedHat.
- HAProxy gets one more listener, `pgcat_smart_pool` (port `pgcat_port`,
  default 6433), round-robining across every node's local PgCat
  container. Because PgCat itself decides primary vs. replica per query,
  this HAProxy listener does a **plain TCP health check** - it doesn't
  need to know or care which node is the current primary.
- One PgCat "pool" is generated per entry in `postgres_databases` (see
  `group_vars/all/vars.yml`); if you haven't declared any, a single
  `postgres` pool is generated as a fallback so the container has
  something valid to run.

## The problem that took the most care to get right: failover

PgCat's own server list (`pgcat.toml`) assigns each backend a **static**
`"primary"` or `"replica"` tag. PgCat does not talk to Patroni, does not
run its own election, and does not automatically detect a promotion. From
a PgCat maintainer, directly:

> "It doesn't [handle failover]. Currently, you need to use another
> primary failover tool, e.g. Patroni and update pgcat config
> accordingly." - postgresml/pgcat discussion #468

Left alone, that means: Patroni promotes node B, but PgCat everywhere
still thinks node A is the primary, and every write PgCat routes
"automatically" keeps hitting a demoted replica - which will reject them.
**This would make read/write splitting actively unsafe during a failover**
if nothing closed that gap.

What this role does about it: `pgcat-sync.service`
(`templates/pgcat-sync.sh.j2`), a small daemon installed on every node
alongside PgCat itself. It polls every node's Patroni REST API
(`GET /primary`, the exact same endpoint HAProxy's own health checks use)
every `pgcat_sync_interval_seconds` (default 5s). When a node's answer
differs from what's currently written in `pgcat.toml`, the daemon patches
just that node's tag in place and sends PgCat a `SIGHUP`, which reloads
the config live without dropping other pools' connections.

This is a real mitigation, not a cosmetic one, but be clear-eyed about
what it is: **a purpose-built workaround for a gap in a beta project**,
polling on an interval, not an event-driven, atomic handoff the way
Patroni's own leader lock is. During the `pgcat_sync_interval_seconds`
window right after a real failover, PgCat can still route a write to the
old (now-demoted) primary; that write will fail immediately with a
straightforward "read-only" error from PostgreSQL - not silently
succeed on stale data - and the application's retry lands correctly once
the sync daemon catches up. If your durability requirements can't tolerate
even that narrow, fail-loud window, use the two-port HAProxy split
instead, where the health check that decides routing runs on every new
connection, not on a polling interval.

## Authentication: the other real trade-off

PgCat's client-facing authentication is **MD5 only** as of this writing -
it cannot speak SCRAM-SHA-256 to clients, even though the rest of this
project standardizes on SCRAM everywhere (PostgreSQL, PgBouncer, etcd).
MD5 is weaker: it's vulnerable to replay if an attacker captures the
handshake and doesn't benefit from SCRAM's channel binding. PgCat *can*
speak SCRAM-SHA-256 when connecting onward to PostgreSQL - that side is
unaffected - so the weaker hop is specifically application-to-PgCat.

Mitigations actually available, both worth doing together:
- Restrict `pgcat_port` to your application network only
  (`pg_hba_extra_networks`-style firewalling - see `docs/host-tuning.md`
  and adjust the firewalld rules in `tasks/security.yml` if your app tier
  isn't part of the cluster's own subnet).
- Terminate TLS in front of PgCat. PgCat's `[general]` section supports
  `tls_certificate` / `tls_private_key` for exactly this; this role
  doesn't wire it up automatically because it doesn't provision
  certificates for you (see `docs/security-and-tls.md`) - add them and
  set `server_tls`/the cert paths once you have real certs.

## The other limitation to know before you rely on this

**Query parsing stops at the transaction boundary.** The instant your
application sends `BEGIN`, PgCat can no longer prove that a later
statement in that same transaction won't be a write, so *the entire
transaction* is routed to the primary - including every `SELECT` inside
it. This is correct and necessary (there's no safe way to split a
transaction across two different PostgreSQL instances), but it means
PgCat only actually offloads read traffic for **autocommit-style queries
run outside an explicit transaction**. An application that wraps
everything in transactions (many ORMs do, by default) will see little or
no read offload from PgCat even though it's fully configured and working
exactly as designed. Check your actual query patterns before assuming
this buys you the read scaling you're expecting.

## Configuration reference

| Setting | Variable | Notes |
|---|---|---|
| Enabled | `pgcat_enabled` (`true`) | Set `false` to skip installing PgCat entirely; the two-port HAProxy split (docs/haproxy.md) keeps working either way and is unaffected by this toggle. |
| Image / version | `pgcat_image`, `pgcat_image_tag` (pinned) | Pinned rather than `:latest` deliberately - this is beta software from a small team; an unreviewed silent upgrade is a bad idea for a database-adjacent component. Bump the tag intentionally. |
| App-facing port | `pgcat_port` (6433) | What your application actually connects to, via HAProxy + the VIP. |
| Per-node container port | `pgcat_backend_port` (6432) | Internal - each node's own PgCat instance, fronted by the HAProxy listener above. Kept distinct from `pgcat_port` so HAProxy and the local PgCat container don't fight over the same port on the same host. |
| Pool mode | `pgcat_pool_mode` (`transaction`) | Same trade-offs as PgBouncer's transaction mode - see `docs/pgbouncer.md`. |
| Default role for unrouted queries | `pgcat_default_role` (`any`) | `any` round-robins primary+replicas, `replica` never touches the primary, `primary` effectively disables the point of using PgCat. |
| Include primary in the read pool | `pgcat_primary_reads_enabled` (`true`) | `true` keeps read capacity available if every replica is down, at the cost of not fully offloading the primary. `false` fully offloads it but reads fail outright during a replica outage. |
| Sync interval | `pgcat_sync_interval_seconds` (5) | How often the failover-sync daemon polls Patroni. Lower = faster failover reaction, more REST API load on every node. |
| Admin console | `pgcat_admin_username`, `vault_pgcat_admin_password` | Connect to this "database" name with `psql` to run `SHOW POOLS`, `SHOW STATS`, etc. against the running PgCat instance. |
| Prometheus | `pgcat_prometheus_port` (9930) | PgCat's own metrics endpoint, separate from `postgres_exporter`. |

Pools and users are generated automatically from `postgres_databases` /
`postgres_users` in `group_vars/all/vars.yml` - the same declarations used
to provision the databases themselves (`docs/configuration-reference.md`
covers that provisioning). There is nothing PgCat-specific to declare
beyond the toggles above.

## Operating it

```bash
# From any node, check what PgCat currently thinks the topology is:
psql "host=127.0.0.1 port=6432 dbname=pgcat user=pgcat_admin sslmode=disable" -c "SHOW POOLS"

# Watch the sync daemon react to a manual switchover:
journalctl -u pgcat-sync -f
patronictl -c /etc/patroni/config.yml switchover

# If PgCat ever looks stuck on a stale topology, force a resync:
systemctl restart pgcat-sync
```

## Should you actually turn this on?

Turn it on if: your application genuinely runs a lot of autocommit
`SELECT`s outside explicit transactions, you've measured that the primary
is CPU/IO-bound on reads specifically, and you're comfortable operating an
extra beta-quality component with a custom failover-sync daemon this
project had to build for it.

Stick with the plain two-port HAProxy split (the default everywhere else
in this project, and still fully configured even when `pgcat_enabled:
true`) if: your app already knows which queries are reads vs. writes at
the code level (most do, since a repository/DAO layer typically already
separates them), or most of your traffic runs inside transactions anyway,
in which case PgCat would add a component and an authentication weak
point without actually offloading much.
