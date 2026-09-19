# PostgreSQL HA Cluster (Patroni + etcd + HAProxy + PgBouncer + PgCat)

Ansible project that deploys a self-healing, highly available PostgreSQL
cluster across 3+ nodes, with two ways for applications to connect: a
classic HAProxy read/write port split, and PgCat for automatic
per-statement routing over a single connection.

```
Client
  │
  ├──────────────┬─────────────────────────────┐
  ▼ (port 6433)  ▼ (port 5000/5001/5002/5003)   │
PgCat            HAProxy  (routes by role: primary / replica / sync / async)
(SQL-aware,      │
per-statement    ▼
routing)         PgBouncer  (connection pooling, one instance per node)
  │                │
  └────────┬───────┘
           ▼
     PostgreSQL  (managed by Patroni)
            ▲
            │  leader election / config distribution
            ▼
          etcd  (3+ node cluster)
```

## Layout

```
.
├── ansible.cfg              # sane defaults (inventory path, retry files off, ...)
├── site.yml                 # top-level playbook
├── requirements.yml         # required Ansible collections
├── inventory/hosts.yml      # your cluster's hosts - edit this
├── group_vars/all/
│   ├── vars.yml             # non-secret cluster-wide settings
│   └── vault.yml.example    # copy -> vault.yml, fill in, then ansible-vault encrypt
├── docs/
│   ├── CONFIGURATION.md     # full settings reference for every component
│   └── PGCAT.md             # deep dive on query-level read/write splitting
├── examples/                # runnable app connection snippets
└── roles/postgres_ha/       # the actual role (see roles/postgres_ha/README.md)
```


## Supported operating systems

The role detects `ansible_os_family` and branches automatically - you don't
configure anything:

| | Debian family | RedHat family |
|---|---|---|
| Distros | Debian, Ubuntu, ... | RHEL, CentOS, Rocky, AlmaLinux, ... |
| Package manager | `apt` | `dnf` |
| PostgreSQL repo | `apt.postgresql.org` | `pgdg-redhat-repo` RPM (+ `dnf module disable postgresql` on EL8/9) |
| Data directory | `/data/postgresql/<ver>/main` | `/var/lib/pgsql/<ver>/data` |
| Binaries | `/usr/lib/postgresql/<ver>/bin` | `/usr/pgsql-<ver>/bin` |
| Config files | `/etc/postgresql/<ver>/main` (role deploys them) | inside the data dir (Patroni writes them) |
| Packaged service | `postgresql@<ver>-main` | `postgresql-<ver>` |
| PgBouncer runs as | `postgres` | `pgbouncer` |
| firewalld / SELinux | n/a | handled (`manage_firewall`, `manage_selinux`) |

You can mix both families in the same inventory - each host gets the right
packages and paths. The per-family values live in
`roles/postgres_ha/vars/Debian.yml` and `roles/postgres_ha/vars/RedHat.yml`.

Note that etcd is installed from upstream GitHub release tarballs on *both*
families, since neither ships a current etcd package.

## Quick start

1. Install the required collections:
   ```bash
   ansible-galaxy collection install -r requirements.yml
   ```
2. Edit `inventory/hosts.yml` with your real hosts (3 or 5, always odd).
3. Set up secrets:
   ```bash
   cp group_vars/all/vault.yml.example group_vars/all/vault.yml
   $EDITOR group_vars/all/vault.yml     # set real passwords
   ansible-vault encrypt group_vars/all/vault.yml
   ```
4. Review `group_vars/all/vars.yml` (versions, ports, memory budget,
   `pg_hba_extra_networks`).
5. Run it:
   ```bash
   ansible-playbook site.yml --ask-vault-pass
   ```

You can target a subset of the stack with tags, e.g.
`ansible-playbook site.yml --ask-vault-pass --tags patroni`.
Available tags: `etcd`, `postgresql`, `patroni`, `pgbouncer`, `haproxy`.

## Versions stay current automatically

You don't need to hand-edit version numbers to keep the stack current:

- PostgreSQL, Patroni, PgBouncer and HAProxy are installed with
  `state: latest` by default (via apt on Debian, dnf on RedHat), so every
  run installs whatever is newest in the configured repos.
- etcd is fetched straight from its GitHub releases and defaults to
  `etcd_version: latest`, so it also always grabs the newest tag.
- PostgreSQL's *major* version (e.g. 16 vs. 18) is auto-detected on a
  node's first run and then pinned to a marker file on that host, so
  re-running the playbook never attempts a surprise major-version upgrade
  on a live cluster - see `roles/postgres_ha/README.md` for details on how
  to move to a new major version on purpose.
- **PgCat is the one exception**: `pgcat_version` defaults to a pinned tag,
  not `latest`, because PgCat's own docs describe some features as
  experimental. See docs/PGCAT.md if you want to opt it into the same
  auto-update behavior as everything else.

Set `auto_update_packages: false` in `group_vars/all/vars.yml` if you'd
rather pin everything and upgrade manually.


## What makes this production-grade

These are the things a bare Patroni tutorial leaves out, and the reason the
earlier version of this repo was not safe to run in production:

| Area | What's configured | Why it matters |
|---|---|---|
| **Backups** | pgBackRest: WAL archiving, scheduled full/diff backups via systemd timers, retention, `stanza-create` + verification | The previous `archive_command` was `/bin/true`. Replicas are not a backup - they replicate `DROP TABLE` too. Without this there is no PITR and no recovery from logical corruption. |
| **Fencing** | Watchdog (softdog or hardware), armed by Patroni before promotion | If Patroni hangs or is OOM-killed, PostgreSQL can keep accepting writes after its leader lease expired. The watchdog resets the box instead. Last line of defence against split-brain. |
| **DCS failsafe** | `failsafe_mode: true` | Without it, losing etcd takes the whole cluster read-only even when every database node is healthy. With it, the leader keeps serving writes while it can reach all members. |
| **REST API auth** | HTTP basic auth on unsafe endpoints | Previously anyone who could reach port 8008 could `POST /switchover` and fail over your production cluster. GET stays open so HAProxy health checks still work. |
| **No `trust` auth** | `peer` for local socket, `scram-sha-256` everywhere else | The old `pg_hba.conf` had `trust` lines and a `0.0.0.0/0` rule. |
| **Single endpoint** | keepalived floating VIP with an HAProxy health check | Otherwise HAProxy on one node is a single point of failure and clients must know every node's address. |
| **etcd durability** | Raised backend quota, periodic auto-compaction, weekly staggered defrag timer, alarm disarm | etcd's DB never shrinks by itself. Hitting the default 2GB quota trips a NOSPACE alarm, which takes Patroni read-only. |
| **Data safety** | `remove_data_directory_on_rewind_failure: false`, `data-checksums` at initdb | Never let automation wipe the only copy of unreplicated data. |
| **Host tuning** | THP disabled (persisted via systemd unit), `vm.overcommit_memory=2`, swappiness, dirty ratios, `nofile` limits | THP causes latency spikes; overcommit settings stop the OOM killer choosing the postmaster and taking down the whole instance. |
| **Monitoring** | Patroni `/metrics`, etcd metrics port, postgres_exporter with the `pg_monitor` role | You cannot operate what you cannot see. Uses `pg_monitor`, not superuser. |
| **Query-level routing** | PgCat, parsing every query and splitting SELECT/write traffic automatically over one connection | HAProxy alone can only split by connection, not by statement - fine for apps willing to manage two pools, but many high-throughput apps want this handled for them. See docs/PGCAT.md. |
| **Preflight checks** | Odd node count, placeholder secrets, VIP set, time sync running | Catches the mistakes that are painful to debug after the cluster is half-built. |

### Things you still must decide

- **Watchdog mode** defaults to `automatic` (arm it if available, carry on if
  not). The stricter `required` refuses to promote a node that can't arm the
  watchdog - correct for enterprise, but verify `/dev/watchdog` works on every
  node first, because it will hard-reset the host on failure.
- **`synchronous_mode_strict: true`** means writes **block** when no sync
  standby is available. That is deliberate - it is the difference between an
  outage and silent data loss - but confirm it matches your durability policy.
- **TLS is off by default** (`etcd_tls_enabled`, `patroni_restapi_tls_enabled`,
  `pg_require_ssl`). This role does not run a CA. Point them at real
  certificates before going to production; plain-HTTP etcd means anyone who
  reaches port 2379 owns your cluster state.
- **pgBackRest repo is local by default** (`posix`). A backup on the same
  host as the database is not a backup. Set `pgbackrest_repo_type: s3` (or
  point `repo1-path` at off-host storage) for real durability.
- **Restore drills.** Backups you have never restored are not backups. Test
  `pgbackrest restore` on a throwaway host on a schedule.


## Connecting your application

There are **two ways** to connect, and this cluster runs both at once -
pick whichever fits, or mix them per service:

### Option A: PgCat - one endpoint, automatic per-query routing (recommended default)

```
postgresql://app_user:secret@<vip-or-host>:6433/app?sslmode=require
```

Just run queries normally. PgCat parses each one and sends `SELECT` to a
replica, everything else to the primary, inside a single connection - no
dual-pool logic in your app. Full details, including two real limitations
you should know about before choosing this (MD5-only client auth, and
explicit transactions always going to the primary), are in
**[docs/PGCAT.md](docs/PGCAT.md)**.

### Option B: HAProxy port split - two endpoints, you choose per query

| What you want | Connect to | Port | Goes to |
|---|---|---|---|
| Writes (INSERT/UPDATE/DELETE/DDL) | VIP or any HAProxy node | `5000` | current primary only, via PgBouncer |
| Reads (SELECT) | VIP or any HAProxy node | `5001` | round-robin across healthy replicas, via PgBouncer |
| Reads that must include the sync replica | VIP or any HAProxy node | `5002` | the synchronous replica only |
| Reads where any lag is acceptable, including async replicas | VIP or any HAProxy node | `5003` | async replicas only |
| Cluster status / stats page | any HAProxy node | `7000` | HAProxy's own stats UI (HTTP basic auth) |

This is the classic Patroni+HAProxy pattern: HAProxy asks each node's
Patroni `/primary` and `/replica` REST endpoints "are you the primary
right now?" and routes each new connection accordingly - your application
picks the port per query (or per repository/DAO method). It keeps full
SCRAM-SHA-256 auth end-to-end and gives your app explicit control, at the
cost of writing two-pool logic yourself. See `examples/` for runnable
snippets, and `examples/libpq_target_session_attrs.md` for a variant that
skips HAProxy entirely for libpq-based clients.

Point either option at the **VIP** if `keepalived_enabled: true`, or at
any individual node's address otherwise - every node has the same view of
cluster state.

### Why HAProxy alone can't do what PgCat does

HAProxy operates on TCP connections, not SQL - it cannot look inside an
already-open connection mid-stream and decide "this particular query is a
SELECT, reroute it." A TCP connection is pinned to one backend for its
whole lifetime; this is a structural limit of every L4 proxy, not a gap in
this configuration. PgCat solves this by actually speaking the PostgreSQL
wire protocol and parsing each query - see docs/PGCAT.md for exactly how.

### Example connections

```python
# Python (SQLAlchemy), Option A - PgCat, single pool
engine = create_engine(f"postgresql+psycopg2://app_user:{pw}@{host}:6433/app?sslmode=require")

# Option B - HAProxy port split, two pools - see examples/python_sqlalchemy.py
write_engine = create_engine(f"postgresql+psycopg2://app_user:{pw}@{host}:5000/app?sslmode=require")
read_engine  = create_engine(f"postgresql+psycopg2://app_user:{pw}@{host}:5001/app?sslmode=require")
```

```javascript
// Node.js (pg), Option B - see examples/node_pg.js for the full version
const writePool = new Pool({ host, port: 5000, ...common });
const readPool  = new Pool({ host, port: 5001, ...common });
```

### Things that will bite you if you ignore them

- **Replication lag is real**, with either option. A replica can be
  milliseconds to seconds behind the primary. If your app writes something
  and immediately reads it back expecting to see it, that read must go to
  the primary (port 5000, or `SET SERVER ROLE TO 'primary'` with PgCat) -
  not a replica. This is the single most common bug in read/write-split
  applications.
- **Explicit transactions and PgCat**: once you send `BEGIN`, PgCat routes
  everything until `COMMIT` to the primary, since it can't know in advance
  whether a later statement will write. An ORM that wraps read-only work
  in an unnecessary transaction loses the replica benefit for it - see
  docs/PGCAT.md.
- **PgBouncer pools in `transaction` mode** (Option B's pooling layer):
  the server-side connection returns to the pool at the end of each
  transaction, not each client disconnect - session-level features like
  bare `SET`, prepared statements kept open across transactions,
  `LISTEN`/`NOTIFY`, and advisory locks held outside a transaction can all
  behave unexpectedly. Use `SET LOCAL` inside the transaction it applies
  to.
- **`sslmode=require`** encrypts using the certificate this role deploys
  (the Debian/Ubuntu snakeoil cert by default). It does not verify server
  identity - use `sslmode=verify-full` with a real CA once you have one.
- **Provisioning app users/databases**: this role does not create your
  application's database or role unless you tell it to. Add entries to
  `postgres_users` and `postgres_databases` in `group_vars/all/vars.yml`
  (passwords go in vault.yml). PgCat pools are built from these same
  entries, so this is required either way.


## Configuration reference

Full per-component settings tables (Patroni, PostgreSQL, PgBouncer,
HAProxy, PgCat, etcd, pgBackRest, watchdog, keepalived, monitoring, host
tuning) live in [docs/CONFIGURATION.md](docs/CONFIGURATION.md), so this
file doesn't try to hold all of it. Query-level read/write splitting via
PgCat has its own deep-dive at [docs/PGCAT.md](docs/PGCAT.md).


## Before you go to production

See "Known limitations" in `roles/postgres_ha/README.md` - in particular,
**WAL archiving is not actually wired up** by default and the SSL
certificate is self-signed. Both need real configuration for a
production deployment.

## License

See `LICENSE`.
