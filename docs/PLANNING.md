# Planning your deployment

Two variables in `group_vars/all/vars.yml` decide the entire shape of what
gets installed. Run `./plan.py` from the repo root for an interactive
wizard that sets them (and writes `inventory/hosts.yml`) for you, or edit
them by hand - this document is the reasoning behind each choice either
way.

```
                    ┌─────────────────────┐
                    │ How many nodes?     │
                    └──────────┬──────────┘
                   1 node       │       3+ nodes
              ┌─────────────────┴─────────────────┐
              ▼                                    ▼
      deployment_mode:                     deployment_mode:
      single_node                          cluster
              │                                    │
     (skip to "single_node"                        ▼
      below)                          ┌──────────────────────────┐
                                       │ How should apps connect? │
                                       └─────────────┬────────────┘
                                    explicit control  │  simplest app code
                                    + strict SCRAM     │  + high query volume
                                       ┌───────────────┴───────────────┐
                                       ▼                                ▼
                             connection_mode:                 connection_mode:
                             haproxy                          pgcat
```

## `deployment_mode: single_node`

**Choose this when:** you have one server, this is dev/staging, or you
want the simplest possible thing running today with room to grow later.

**What you get:** PostgreSQL, tuned for the box's actual RAM, with
PgBouncer pooling, pgBackRest backups, and monitoring - the same
production-hygiene pieces as the cluster path, just without any of the HA
machinery. No etcd, no Patroni, no watchdog, no keepalived, no
HAProxy/PgCat.

**What you explicitly do not get:** automatic failover. If this box goes
down, PostgreSQL goes down with it until someone intervenes. That's the
trade you're making for radically less operational complexity.

**Growing out of it later:** this role does not do an automated
single-node-to-cluster migration - going from `single_node` to `cluster`
means standing up new nodes running Patroni, making the existing database
a source for the initial basebackup, and formally adopting it into the
Patroni-managed cluster. That is a manual, supervised operation (broadly:
back up, deploy `cluster` mode fresh, restore into it, cut over), not a
config flip. If you know you'll need HA within the next few months, it is
usually less total work to start on `cluster` with 3 small nodes than to
start single and migrate later.

## `deployment_mode: cluster`

**Choose this when:** you need the database to survive a node failing
without a human paging in at 3am.

**What you get:** everything in single_node, plus Patroni managing
PostgreSQL directly, etcd for consensus and leader election, automatic
failover, a watchdog for fencing (split-brain protection), and one of the
two connection layers below.

**Cost of admission:** at least 3 nodes (etcd needs an odd number to hold
quorum - a 2-node "cluster" cannot survive a single failure, so this role
refuses to deploy one), and meaningfully more to reason about
operationally: leader elections, replication lag, failover runbooks.

### `connection_mode: haproxy` (the default)

**Choose this when:** you want the most battle-tested path, need full
SCRAM-SHA-256 authentication end-to-end (compliance requirements often
mandate this), or your application already cleanly separates read and
write code paths (e.g. separate repository classes, CQRS).

**What it installs:** HAProxy with separate ports for primary
(read-write) and replicas (read-only, load-balanced), PgBouncer for
connection pooling on every node.

**What your app needs to do:** maintain two connection pools and pick the
right one per query - see the main README's "Connecting your application"
and `examples/`.

### `connection_mode: pgcat`

**Choose this when:** query volume is high enough that you want
connection pooling AND read/write splitting to be one less thing your
application code manages, and you can accept the two trade-offs below.

**What it installs:** PgCat (built from source - see docs/PGCAT.md for
why), which replaces PgBouncer entirely (PgCat pools its own connections
directly) and replaces HAProxy's primary/replica listeners specifically -
HAProxy still runs, but only with a plain TCP listener that load-balances
across the PgCat instances themselves.

**What your app needs to do:** nothing special - run queries on one
connection, PgCat routes each one.

**The two trade-offs, spelled out (full detail in docs/PGCAT.md):**
1. Client-facing authentication is MD5, not SCRAM - a PgCat limitation,
   not a misconfiguration. If your compliance posture requires SCRAM
   end-to-end with no exceptions, use `haproxy` instead.
2. PgCat can't see inside an explicit transaction, so once your app sends
   `BEGIN`, everything until `COMMIT` goes to the primary. An ORM that
   wraps read-only work in an unnecessary transaction loses the replica
   benefit for it.

**If you're not sure:** start with `haproxy`. It's the classic, most
battle-tested Patroni pattern, and you can switch to `pgcat` later if
managing two connection pools in application code turns out to be more
friction than the trade-offs above are worth. Switching is a re-run of the
playbook with `connection_mode` changed - HAProxy and PgBouncer get
disabled/removed as appropriate and PgCat gets installed, or vice versa;
review the diff of a `--check` run first since this changes what's
listening on which ports.

## What `./plan.py` actually does

It only touches two files:

- `group_vars/all/vars.yml` - sets `deployment_mode`, `connection_mode`,
  and (if you opt into a virtual IP) `keepalived_vip` /
  `keepalived_enabled`. It edits these lines in place if they already
  exist, or appends them if not - it never rewrites the rest of the file.
- `inventory/hosts.yml` - writes the hosts you enter. It asks before
  overwriting an existing inventory.

It does not run Ansible, install anything, or touch secrets - you still
do those steps yourself (it tells you which, at the end). Everything it
does you could do by hand in a text editor; it exists to save you from
needing to know the exact variable names on a first deployment, not to
hide what's happening.

```bash
./plan.py                    # interactive
./plan.py --mode cluster --connection haproxy \
          --nodes 10.0.0.11,10.0.0.12,10.0.0.13 \
          --vip 10.0.0.10 --force   # non-interactive, e.g. for CI
```
