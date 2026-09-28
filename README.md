# PostgreSQL HA Cluster (Patroni + etcd + HAProxy/PgCat + PgBouncer)

Ansible project that deploys either a single plain PostgreSQL instance or a
self-healing, highly available cluster across 3+ nodes - you choose which,
plus how apps connect, with two variables (or an interactive wizard, see
"Planning your deployment" below). Nothing you don't need gets installed:
a `single_node` deployment skips etcd/Patroni/HAProxy/PgCat entirely, and
choosing `connection_mode: pgcat` skips PgBouncer and HAProxy's read/write
listeners rather than running everything side by side.

```
                    Client
                      │
        ┌─────────────┴──────────────┐
        │ connection_mode:            │ connection_mode:
        │ pgcat (port 6433)           │ haproxy (ports 5000/5001/...)
        ▼                             ▼
      PgCat                       HAProxy  (routes by role via Patroni's REST API)
   (SQL-aware,                        │
   per-statement                      ▼
    routing)                     PgBouncer  (pooling, one instance per node)
        │                             │
        └──────────────┬──────────────┘
                        ▼
                  PostgreSQL  (managed by Patroni)
                        ▲
                        │  leader election / config distribution
                        ▼
                      etcd  (3+ node cluster)

  deployment_mode: single_node instead skips everything above etcd/Patroni:
  Client → PgBouncer (optional) → PostgreSQL, one instance, no failover.
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
├── plan.py                  # interactive wizard: sets deployment_mode/connection_mode + inventory
├── docs/
│   ├── PLANNING.md          # deployment_mode / connection_mode decision guide
│   ├── CONFIGURATION.md     # full settings reference for every component
│   ├── PGCAT.md             # deep dive on query-level read/write splitting
│   ├── offline-and-mirrors.md  # running behind a filtered network / mirrors
│   └── troubleshooting.md   # failures seen in real runs, and their fixes
├── examples/                # runnable app connection snippets
├── tests/                   # template render + validation test (also run in CI)
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

## Planning your deployment

Two variables decide the entire shape of the deployment - see the top of
`roles/postgres_ha/defaults/main.yml` for the full explanation, or
**[docs/PLANNING.md](docs/PLANNING.md)** for a guided decision walkthrough:

- **`deployment_mode`**: `single_node` (one plain PostgreSQL instance - no
  etcd/Patroni/watchdog/keepalived/HAProxy/PgCat, just PgBouncer + backups
  + monitoring; simplest option, good for dev/staging or a first
  deployment) or `cluster` (full Patroni + etcd HA across 3+ nodes).
- **`connection_mode`** (cluster only): `haproxy` (classic dual-port
  read/write split, full SCRAM auth) or `pgcat` (one smart endpoint,
  automatic per-query routing - replaces HAProxy's read/write listeners
  *and* PgBouncer entirely, see docs/PGCAT.md).

Run the interactive wizard to set these (and generate your inventory)
without hand-editing YAML:

```bash
./plan.py
```

It writes `deployment_mode`/`connection_mode` into
`group_vars/all/vars.yml` and, for a fresh setup, `inventory/hosts.yml` -
then tells you exactly what to do next (vault, run the playbook). You can
skip it and edit those two variables by hand instead; nothing about the
wizard is required.

## Quick start

1. Plan the deployment (see above): `./plan.py`, or hand-edit
   `deployment_mode`/`connection_mode` in `group_vars/all/vars.yml`.
2. Install the required collections:
   ```bash
   ansible-galaxy collection install -r requirements.yml
   ```
3. Edit `inventory/hosts.yml` with your real hosts (one for `single_node`;
   3 or 5, always odd, for `cluster`) - `./plan.py` does this for you.
4. Set up secrets:
   ```bash
   cp group_vars/all/vault.yml.example group_vars/all/vault.yml
   $EDITOR group_vars/all/vault.yml     # set real passwords
   ansible-vault encrypt group_vars/all/vault.yml
   ```
5. Review the rest of `group_vars/all/vars.yml` (versions, ports, memory
   budget, `pg_hba_extra_networks`).
6. Run it:
   ```bash
   ansible-playbook site.yml --ask-vault-pass
   ```

You can target a subset of the stack with tags, e.g.
`ansible-playbook site.yml --ask-vault-pass --tags patroni`.
Available tags: `etcd`, `postgresql`, `patroni`, `pgbouncer`, `haproxy`,
`pgcat`, `backup`, `monitoring`, `security`, `tuning`.

## Version policy

Nothing upgrades itself behind your back by default:

- PostgreSQL, Patroni, PgBouncer and HAProxy are installed with
  `state: present` - installed once, then left alone. Set
  `auto_update_packages: true` to install with `state: latest` on every
  run instead; understand that this means a re-run to change one setting
  also upgrades and restarts those services on a live cluster.
- etcd is pinned by `etcd_version` (currently `v3.6.14`). Set it to
  `latest` to track the newest GitHub release, but note that etcd's minor
  series (3.5 → 3.6 → 3.7) have their own upgrade paths - step through
  them deliberately.
- PostgreSQL's *major* version (e.g. 16 vs. 18) is auto-detected on a
  node's first run and then pinned to a marker file on that host, so
  re-running the playbook never attempts a surprise major-version upgrade
  on a live cluster - see `roles/postgres_ha/README.md` for details on how
  to move to a new major version on purpose.
- **PgCat** is pinned to a release tag and, by default, is *not* built on
  the database nodes at all: set `pgcat_binary_src` to a binary you built
  elsewhere, or opt into on-host building with
  `pgcat_build_from_source: true`. See docs/PGCAT.md.

If your hosts sit behind a filtered network or cannot reach some of these
sources, see **[docs/offline-and-mirrors.md](docs/offline-and-mirrors.md)** -
every download URL is a variable, and preflight checks reachability before
installing anything.


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

How you connect depends on the `deployment_mode` / `connection_mode` you
picked when planning the cluster (see "Planning your deployment" above) -
each deployment exposes exactly one of these, not all three:

### single_node

```
postgresql://app_user:secret@<host>:5432/app?sslmode=require
# or, pooled through PgBouncer:
postgresql://app_user:secret@<host>:6432/app?sslmode=require
```

One instance, no routing decisions to make.

### cluster with connection_mode: haproxy (the default)

| What you want | Connect to | Port | Goes to |
|---|---|---|---|
| Writes (INSERT/UPDATE/DELETE/DDL) | VIP or any node | `5000` | current primary only, via PgBouncer |
| Reads (SELECT) | VIP or any node | `5001` | round-robin across healthy replicas, via PgBouncer |
| Reads that must include the sync replica | VIP or any node | `5002` | the synchronous replica only |
| Reads where any lag is acceptable | VIP or any node | `5003` | async replicas only |
| Cluster status / stats page | any node | `7000` | HAProxy's stats UI (HTTP basic auth) |

HAProxy asks each node's Patroni `/primary` and `/replica` REST endpoints
"are you the primary right now?" and routes each new connection
accordingly - your application picks the port per query (or per
repository/DAO method). Full SCRAM-SHA-256 auth end-to-end. See
`examples/` for runnable snippets (`python_sqlalchemy.py`, `node_pg.js`),
and `examples/libpq_target_session_attrs.md` for a variant that skips
HAProxy entirely for libpq-based clients.

```python
# Python (SQLAlchemy) - see examples/python_sqlalchemy.py for the full version
write_engine = create_engine(f"postgresql+psycopg2://app_user:{pw}@{host}:5000/app?sslmode=require")
read_engine  = create_engine(f"postgresql+psycopg2://app_user:{pw}@{host}:5001/app?sslmode=require")
```

```javascript
// Node.js (pg) - see examples/node_pg.js for the full version
const writePool = new Pool({ host, port: 5000, ...common });
const readPool  = new Pool({ host, port: 5001, ...common });
```

### cluster with connection_mode: pgcat

```
postgresql://app_user:secret@<vip-or-host>:6433/app?sslmode=require
```

Just run queries normally, on one connection. PgCat parses each one and
sends `SELECT` to a replica, everything else to the primary - no dual-pool
logic in your app. Full details, including two real limitations you
should know about before choosing this mode (MD5-only client auth, and
explicit transactions always going to the primary), are in
**[docs/PGCAT.md](docs/PGCAT.md)**. This mode installs neither PgBouncer
nor HAProxy: PgCat pools its own connections and is itself the endpoint on
every node, so keepalived's VIP follows PgCat directly.

```python
# Python (SQLAlchemy) - see examples/pgcat_single_pool.py for the full version
engine = create_engine(f"postgresql+psycopg2://app_user:{pw}@{host}:6433/app?sslmode=require")
```

**Why can't HAProxy alone do this?** It operates on TCP connections, not
SQL - it cannot look inside an already-open connection mid-stream and
decide "this particular query is a SELECT, reroute it." A TCP connection
is pinned to one backend for its whole lifetime; this is a structural
limit of every L4 proxy, not a gap in configuration. PgCat solves this by
actually speaking the PostgreSQL wire protocol - see docs/PGCAT.md.

Point any of the above at the **VIP** if `keepalived_enabled: true`, or at
any individual node's address otherwise - every node has the same view of
cluster state.

### Things that will bite you if you ignore them

- **Replication lag is real**, in cluster mode. A replica can be
  milliseconds to seconds behind the primary. If your app writes something
  and immediately reads it back expecting to see it, that read must go to
  the primary (port 5000, or an explicit transaction with PgCat) - not a
  replica. This is the single most common bug in read/write-split
  applications.
- **Explicit transactions and PgCat**: once you send `BEGIN`, PgCat routes
  everything until `COMMIT` to the primary, since it can't know in advance
  whether a later statement will write. An ORM that wraps read-only work
  in an unnecessary transaction loses the replica benefit for it - see
  docs/PGCAT.md.
- **PgBouncer pools in `transaction` mode** (single_node and
  connection_mode: haproxy): the server-side connection returns to the
  pool at the end of each transaction, not each client disconnect -
  session-level features like bare `SET`, prepared statements kept open
  across transactions, `LISTEN`/`NOTIFY`, and advisory locks held outside
  a transaction can all behave unexpectedly. Use `SET LOCAL` inside the
  transaction it applies to.
- **`sslmode=require`** encrypts using the certificate this role deploys -
  a self-signed pair generated in `/etc/postgresql-ssl` unless you set
  `pg_ssl_cert_file`/`pg_ssl_key_file`. It does not verify server identity,
  so `sslmode=verify-full` needs a real CA-issued certificate.
- **Provisioning app users/databases**: this role does not create your
  application's database or role unless you tell it to. Add entries to
  `postgres_users` and `postgres_databases` in `group_vars/all/vars.yml`
  (passwords go in vault.yml) - required in every mode, PgCat pools are
  built from these same entries.


## Configuration reference

Full per-component settings tables (Patroni, PostgreSQL, PgBouncer,
HAProxy, PgCat, etcd, pgBackRest, watchdog, keepalived, monitoring, host
tuning) live in [docs/CONFIGURATION.md](docs/CONFIGURATION.md), so this
file doesn't try to hold all of it. Query-level read/write splitting via
PgCat has its own deep-dive at [docs/PGCAT.md](docs/PGCAT.md).


## Before you go to production

See "Known limitations" in `roles/postgres_ha/README.md` - in particular,
**WAL archiving is enabled via pgBackRest by default** (`archive_command`
is configured for you), but the SSL certificate is self-signed. Both are
worth reviewing for a real production deployment.

If something fails during deployment, check
**[docs/troubleshooting.md](docs/troubleshooting.md)** first - it covers
real failures this role has hit (HAProxy on EL9, Patroni TLS, pgBackRest
lock conflicts and SSH between nodes, SELinux, PgBouncer auth) and exactly
what the role now does about each one.

## Tearing everything down

`cleanup.yml` completely reverses `site.yml` - stops and removes every
service, package, and config file this role installed, and (by default)
destroys the live database, etcd data, and pgBackRest backups too. There
is no undo. It never runs as a side effect of anything else, and refuses
to run at all without an explicit confirmation:

```bash
ansible-playbook cleanup.yml -e cleanup_confirm=yes-tear-it-down
```

To tear down services/packages but keep the data or backups on a given
run, opt back out explicitly:

```bash
ansible-playbook cleanup.yml -e cleanup_confirm=yes-tear-it-down \
                              -e cleanup_wipe_data=false \
                              -e cleanup_wipe_backups=false
```

After a full teardown, `ansible-playbook site.yml` on the same inventory
starts a completely fresh deployment.

## License

See `LICENSE`.
