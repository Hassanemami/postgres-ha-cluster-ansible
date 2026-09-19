# Monitoring

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
