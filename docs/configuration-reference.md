# Configuration reference

Every variable below lives in `roles/postgres_ha/defaults/main.yml` and can
be overridden in `group_vars/all/vars.yml` (non-secret) or
`group_vars/all/vault.yml` (secret, `vault_` prefix).

### Patroni (`roles/postgres_ha/templates/config.yml.j2` -> `/etc/patroni/config.yml`)

Patroni owns PostgreSQL's actual runtime parameters - the `postgresql.conf`
you'd normally edit by hand is instead generated from this file's
`bootstrap.dcs.postgresql.parameters` block on every node consistently.

| Setting | Variable | Notes |
|---|---|---|
| Cluster name | `patroni_scope` | Must be identical on every node; it's the etcd key prefix. |
| Leader TTL | `patroni_ttl` (30s) | How long a leader lock is valid without renewal before another node can take over. Lower = faster failover, more sensitive to network blips. |
| HA loop interval | `patroni_loop_wait` (10s) | How often Patroni re-evaluates cluster state. |
| DCS request timeout | `patroni_retry_timeout` (10s) | How long Patroni waits on an etcd request before giving up. |
| Sync replication | `patroni_synchronous_mode` (`quorum`) | `off` / `on` / `quorum`. Quorum-based sync tolerates one sync replica failing without losing durability guarantees; classic `on` sync mode does not. |
| Strict sync | `patroni_synchronous_mode_strict` (`true`) | If true, writes **block** when no sync replica is available, instead of silently falling back to async (and risking data loss on failover). |
| Failsafe mode | `patroni_failsafe_mode` (`true`) | Keeps the leader serving writes if etcd becomes unreachable but the leader can still see all members directly. |
| Watchdog | `patroni_watchdog_mode` (`automatic`) | `off` / `automatic` / `required`. Fencing: resets the host if Patroni stops responding. `required` refuses to promote without a working watchdog device. |
| Max lag before failover eligible | `patroni_maximum_lag_on_failover` (1MB) | A replica lagging more than this many bytes of WAL will not be considered for promotion. |
| REST API auth | `patroni_restapi_auth_enabled` (`true`) | HTTP basic auth on the unsafe endpoints (switchover/failover/restart). |
| Memory-derived settings | `pg_memory_budget_percent`, `pg_shared_buffers_percent`, `pg_effective_cache_size_percent`, `pg_work_mem_percent` | Computed once per host from `ansible_memtotal_mb` in `tasks/patroni.yml` - not hardcoded megabyte values, so the same config scales across differently-sized nodes. |
| pg_hba rules | (generated) | `scram-sha-256` for every network connection; local socket uses OS `peer` auth. No `trust`, no `0.0.0.0/0`. Add extra allowed networks via `pg_hba_extra_networks`. |

Full list of PostgreSQL GUCs set here: connection limits, WAL/checkpoint
tuning, autovacuum aggressiveness, `pg_stat_statements` +
`auto_explain` for observability, and logging. Read the template directly
for exact values - it's the single source of truth, this table only covers
the settings you're likely to actually change.

### PostgreSQL config files (Debian family only - `pg_hba.conf.j2`, `postgresql.conf.j2`)

On Debian/Ubuntu these are deployed once, before Patroni's first bootstrap,
as a wrapper (`postgresql.conf` `include`s `postgresql.base.conf`, which
Patroni then writes). On RedHat, Patroni owns both files directly inside
the data directory - see "Supported operating systems" above. Either way
the *content* Patroni writes is identical; only where it's stored differs.

### PgBouncer (`pgbouncer.ini.j2` -> `{{ pgbouncer_conf_dir }}/pgbouncer.ini`)

| Setting | Value | Why |
|---|---|---|
| `pool_mode` | `transaction` | Server connection returned to the pool after each transaction, not each client disconnect - lets a small number of real PostgreSQL connections serve far more client connections. See the caveats in "Connecting your application" above. |
| `auth_type` / `auth_query` | `scram-sha-256` / `user_search()` | PgBouncer looks up credentials live from PostgreSQL via a `SECURITY DEFINER` function rather than keeping its own copy of every password - so password changes in PostgreSQL take effect immediately, no PgBouncer config re-deploy needed. |
| `max_client_conn` | 80000 | How many application connections PgBouncer accepts. Tune down on smaller hosts. |
| `default_pool_size` | 700 | Real PostgreSQL connections per database/user pair. Must fit under PostgreSQL's `max_connections` ({{ pg_max_connections }}) across all PgBouncer instances that connect to it. |

PgBouncer runs on **every** node (not just the primary) and always points
at that node's own local PostgreSQL over the Unix socket - it's HAProxy,
not PgBouncer, that decides which node's PgBouncer a connection reaches.

### HAProxy (`haproxy.cfg.j2` -> `/etc/haproxy/haproxy.cfg`)

Four TCP listeners, each backed by an `httpchk` against Patroni's REST API
(not against PostgreSQL directly - Patroni is the one source of truth for
"who is primary right now"):

| Listener | Port var | Health check | Balancing |
|---|---|---|---|
| `master` | `haproxy_primary_port` (5000) | `GET /primary`, expect 200 | none - exactly one node ever passes |
| `replicas` | `haproxy_replicas_port` (5001) | `GET /replica`, expect 200 | round-robin across all passing replicas |
| `replicas_sync` | `haproxy_replicas_sync_port` (5002) | `GET /sync`, expect 200 | round-robin (usually one node) |
| `replicas_async` | `haproxy_replicas_async_port` (5003) | `GET /async`, expect 200 | round-robin |
| `stats` | `haproxy_stats_port` (7000) | n/a | HTTP basic auth, see `patroni_restapi_username`/`vault_patroni_restapi_password` |

`inter 3s fastinter 1s fall 3 rise 4` means a node is marked down after 3
failed checks (roughly 3-9s) and back up after 4 successful ones -
tune via the template if you want faster/slower failover detection at the
cost of more false positives.

### etcd (`etcd.conf.j2` -> `/etc/etcd/etcd.conf`)

| Setting | Variable | Notes |
|---|---|---|
| Version | `etcd_version` (`latest`) | Fetched from GitHub releases directly - see "Versions stay current automatically". |
| Backend quota | hardcoded 8GiB in the template | Raised from etcd's stock 2GB default. Hitting the quota trips a `NOSPACE` alarm that takes the whole Patroni cluster read-only. |
| Auto-compaction | `periodic`, 1h retention | Keeps old MVCC revisions from accumulating. |
| Defrag | weekly systemd timer (`etcd-defrag.timer`), staggered per node | Compaction alone doesn't shrink the on-disk file; defrag does. Never defrag all members simultaneously - it briefly stalls the member being defragged. |
| TLS | `etcd_tls_enabled` (`false`) | Off by default; see "Before you go to production". |
| Election tuning | `ETCD_ELECTION_TIMEOUT=5000`, `ETCD_HEARTBEAT_INTERVAL=1000` | More forgiving than etcd's LAN-tuned stock defaults (1000ms election) - reduces false-positive leader elections on a loaded network. |

### pgBackRest (`pgbackrest.conf.j2` -> `/etc/pgbackrest/pgbackrest.conf`)

| Setting | Variable | Notes |
|---|---|---|
| Enabled | `pgbackrest_enabled` (`true`) | When false, `archive_command` falls back to a no-op - **no backups, no PITR**. |
| Repo type | `pgbackrest_repo_type` (`posix`) | Local/NFS path by default. Set to `s3` (fill in `pgbackrest_s3_*` + vault S3 keys) for real off-host durability. |
| Retention | `pgbackrest_retention_full` (2), `pgbackrest_retention_diff` (6) | Keeps the last 2 full backups and 6 diffs between them. |
| Async archiving | `pgbackrest_archive_async` (`true`) | WAL pushes are queued through a local spool directory so a slow repo doesn't stall the primary's WAL writer. |
| Schedule | `pgbackrest_full_backup_schedule` (Sundays 01:00), `pgbackrest_diff_backup_schedule` (other days 01:00) | Implemented as systemd timers that run on every node but the wrapper script checks Patroni's `/primary` first and exits immediately on non-leaders. |
| Replica rebuild | `create_replica_methods: [pgbackrest, basebackup]` in Patroni | A failed/rebuilt replica restores from the pgBackRest repo first (doesn't load the primary), falling back to `pg_basebackup` only if that's not possible. |

Verify it's actually working: `sudo -u postgres pgbackrest --stanza=<scope> check` and `... info`. The role runs `check` automatically on the leader during deployment and logs the result.

### Watchdog (`roles/postgres_ha/tasks/watchdog.yml`)

Loads the kernel `softdog` module if no hardware watchdog device exists,
sets `/dev/watchdog` ownership to `postgres` (persisted via a udev rule so
it survives reboots), and wires the device path into Patroni's config.
`patroni_watchdog_mode: required` will refuse to let a node become leader
at all if the watchdog can't be armed - verify this on every node in a
non-production test before flipping it on.

### keepalived (`keepalived.conf.j2` -> `/etc/keepalived/keepalived.conf`)

A VRRP-based floating IP (`keepalived_vip`) that moves to whichever node's
HAProxy is currently healthy (checked via a script that just confirms the
`haproxy` process is running - HAProxy's own health checks handle
which *backend* gets traffic; keepalived only handles which *node* holds
the IP). `nopreempt` is set so the VIP doesn't bounce back to a recovered
node and cause an unnecessary connection blip.

### Monitoring (`roles/postgres_ha/tasks/monitoring.yml`)

- Patroni exposes Prometheus-format metrics natively at
  `http://<node>:8008/metrics` - no extra component needed.
- etcd exposes its own metrics on `etcd_metrics_port` (2381), separate from
  the client port.
- `postgres_exporter` (installed from GitHub releases, version pinned via
  `postgres_exporter_version`) covers PostgreSQL internals: replication
  lag, table/index bloat estimates, connection counts,
  `pg_stat_statements`. It connects using a dedicated `pg_exporter` role
  granted the built-in `pg_monitor` role - read-only access to stats views,
  not superuser.

### Host tuning (`roles/postgres_ha/tasks/tuning.yml`)

Applied to every node regardless of OS family: Transparent Huge Pages
disabled (both at runtime and persisted via a systemd unit that runs
before PostgreSQL starts), `vm.overcommit_memory=2` with an 80% ratio (so
the OOM killer doesn't get to choose the postmaster and take down the
whole instance), conservative dirty-page writeback ratios, `swappiness=1`,
and raised `nofile`/`nproc` limits for the `postgres` user. Toggle off
entirely with `tune_kernel_parameters: false` if you tune the OS yourself.
