# Patroni

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
