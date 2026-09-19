# PgCat: query-level read/write splitting

## What problem this solves

HAProxy (used elsewhere in this cluster) operates on raw TCP bytes. It
cannot look inside an already-open connection and decide "this particular
query is a SELECT, send it to a replica" - a TCP connection is pinned to
one backend for its whole life, and HAProxy has no notion of a "query" at
all. This is confirmed directly by HAProxy's own ecosystem documentation:

> "HAProxy is not SQL-aware. This means it does not inspect or interpret
> SQL queries, so it cannot differentiate between reads (SELECT) and
> writes (INSERT, UPDATE, etc.)... You must also adjust your client
> applications to send read and read/write requests to different HAProxy
> ports." - Percona, *Load balancing in Percona Operator for MySQL*

That's why the rest of this project uses **port-based** splitting (see the
main README's "Connecting your application"): your app decides per query
which port to use, and HAProxy just carries each connection to a node with
the right *role* (primary or replica).

**PgCat is different: it actually speaks the PostgreSQL wire protocol.**
It parses the SQL text out of each query message, decides whether it's a
read or a write, and routes it accordingly - inside a single, ordinary
application connection. Your app just runs queries; it doesn't need to
know or care which physical node handles which one.

## How it works in this cluster

```
App
 │  one connection, port 6433 (or the VIP)
 ▼
HAProxy "smart_pool" listener
 │  plain TCP health check, round-robins across every live PgCat instance
 ▼
PgCat (running on every node)
 │  parses each query's SQL text
 │  SELECT ────────────────────────► a healthy replica
 │  everything else (INSERT/UPDATE/DELETE/DDL/explicit transactions) ──► the primary
 ▼
PostgreSQL (primary or replica, chosen per query)
```

Every node runs its own PgCat instance, and every instance connects
**directly** to all three PostgreSQL nodes (not through the per-node
PgBouncer used elsewhere) - PgCat is itself a full connection pooler, so
stacking PgBouncer underneath it would just add a redundant hop.

### How PgCat knows which node is the primary right now

PgCat's own config format expects a static list of servers, each tagged
`primary` or `replica` - it does not talk to Patroni on its own. Since
Patroni can promote a different node at any time, something has to keep
that list current. This role solves it with `pgcat-sync-topology.py`
(deployed to every node):

1. **Every `pgcat_sync_interval_seconds` (default 5s)**, a systemd timer
   runs the script, which asks the *local* Patroni's REST API
   (`GET /cluster` - the same endpoint `patronictl list` itself uses) who
   the current primary and replicas are, regenerates `/etc/pgcat/pgcat.toml`
   if anything changed, and sends PgCat a `SIGHUP` to reload.
2. **On every role change**, Patroni's `on_role_change` callback (wired
   into `config.yml.j2`) runs the same script immediately on the node
   whose role just changed - so a failover propagates in roughly a second,
   not up to 5.
3. If Patroni can't be reached, the script leaves the existing config
   alone rather than guessing or writing an empty server list.

This is the same pattern used by HAProxy itself in this project (ask
Patroni's REST API, don't infer role from querying Postgres directly) -
querying `pg_is_in_recovery()` as a role oracle is fragile during
failovers and network partitions, per PgBouncer/HAProxy read-write-split
guidance; asking Patroni directly is what keeps routing atomic with actual
leader changes.

## Trade-offs you should know about

### Client authentication is MD5, not SCRAM

PgCat's connection *to* PostgreSQL supports modern SCRAM-SHA-256. Its
connection *from* your application does not - as of this writing PgCat
only implements MD5 auth on the client-facing side. This is a real,
current limitation of the project, not a misconfiguration in this role.

Practical consequences:
- PgCat needs the **plaintext password** for every user in its config
  (MD5 verification requires it), unlike PgBouncer's `auth_query` approach
  used elsewhere in this project. That's why `pgcat_sync_topology.py`
  bakes credentials directly into `/etc/pgcat/pgcat.toml` (mode `0640`,
  owned by the `pgcat` user, never written to logs).
- The wire-level auth handshake between your app and PgCat is MD5, which
  is weaker than SCRAM. The connection itself can still be wrapped in TLS
  for transport security regardless (see PgCat's TLS options if you need
  this) - MD5 vs SCRAM only affects how the password challenge/response
  itself is computed.
- If your compliance requirements mandate SCRAM end-to-end with no
  exceptions, use the HAProxy port-based split instead (which preserves
  SCRAM all the way from the app to PostgreSQL) rather than PgCat.

### The query parser cannot see inside explicit transactions

Per PgCat's own documentation, once a client sends `BEGIN`, everything
until `COMMIT`/`ROLLBACK` goes to the primary - PgCat can't know whether a
later statement in that same transaction will write, so it plays it safe.
This means:
- Code that wraps read-only queries in an explicit transaction for no
  reason (a common ORM default) loses the read/replica benefit for that
  transaction.
- This is the correct, safe behavior, not a bug - the alternative
  (guessing) risks sending a write to a replica, which fails outright
  since replicas are read-only.
- If a specific code path is performance-critical and read-only, consider
  removing the unnecessary explicit transaction wrapper for it in your
  application code, or use the manual override below.

### Manual override

When the parser can't or shouldn't decide, PgCat accepts a custom SQL
extension your app can send on that connection before the query it
concerns:

```sql
SET SERVER ROLE TO 'primary';   -- next transaction must hit the primary
SET SERVER ROLE TO 'replica';   -- next transaction may hit a replica
```

### Build time and disk usage

There is no stable apt/dnf package for PgCat on either Debian or RedHat
(its own documentation points at Docker or building from source). This
role builds it from source with Rust:
- Needs outbound access to `github.com` (source) and `static.rust-lang.org`
  / `sh.rustup.rs` (the Rust toolchain installer) from every node that
  builds it.
- A cold build takes roughly 5-15 minutes and 1-2GB of disk for the Rust
  toolchain and build artifacts, on top of PostgreSQL/Patroni/etc.
- To avoid paying that cost on every node, the role builds **once** and
  distributes the binary - but only when every node shares the same OS
  family and CPU architecture (a binary built on Debian will not reliably
  run on RHEL, and vice versa, due to differing glibc/OpenSSL ABIs). In a
  mixed-OS cluster, each node builds its own copy instead; see
  `roles/postgres_ha/tasks/pgcat.yml`.

### Version pinning

`pgcat_version` defaults to a pinned tag rather than `"latest"` (unlike
etcd elsewhere in this project), because PgCat's own documentation
describes some of its features as actively-developed/experimental. Set
`pgcat_version: latest` in `group_vars/all/vars.yml` if you want it to
track new GitHub releases automatically like everything else here - just
be aware there's less of a stability track record to lean on than with
etcd or PostgreSQL itself.

## Should you use PgCat, the HAProxy port split, or both?

Both are deployed and running whenever `pgcat_enabled: true` and
`keepalived_enabled`/HAProxy are on - they are not mutually exclusive,
they're different ports on the same cluster:

| | HAProxy port split (5000/5001) | PgCat (6433) |
|---|---|---|
| Routing granularity | Per connection | Per statement |
| App changes needed | Two connection pools, app picks per query | One connection, PgCat decides |
| Client auth | Full SCRAM-SHA-256 | MD5 only |
| Explicit transactions | App controls entirely | Forced to primary (parser can't see inside) |
| Extra moving parts | None beyond what's already deployed | PgCat process + topology-sync timer per node |
| Best for | Compliance-sensitive auth requirements; apps that already separate read/write repositories | High query volume where you want simpler application code and don't want to hand-route every query |

A reasonable default: point new application code at PgCat (port 6433) for
simplicity, and keep the HAProxy ports available for anything that needs
explicit control or strict SCRAM auth.

## Operating PgCat

- **Admin console**: connect to any node's PgCat port with the
  `pgcat_admin_username` / `vault_pgcat_admin_password` credentials and
  run `SHOW POOLS`, `SHOW STATS`, `SHOW SERVERS`, or `RELOAD` (equivalent
  to sending it a `SIGHUP`).
- **Metrics**: Prometheus format at `http://<node>:9930/metrics`
  (`pgcat_prometheus_port`).
- **Logs**: `journalctl -u pgcat` and `journalctl -u pgcat-sync-topology`.
- **Force a topology refresh manually**:
  `sudo /usr/bin/python3 /usr/local/bin/pgcat-sync-topology.py`
- **Check current routing config**: `cat /etc/pgcat/pgcat.toml` (contains
  plaintext credentials - readable by root only).

## References

- [PgCat GitHub repository](https://github.com/postgresml/pgcat)
- [PgCat configuration reference](https://github.com/postgresml/pgcat/blob/main/CONFIG.md)
- [Patroni REST API documentation](https://patroni.readthedocs.io/en/latest/rest_api.html) (the `/cluster` endpoint this role polls)
