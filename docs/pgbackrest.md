# pgBackRest (backups)

| Setting | Variable | Notes |
|---|---|---|
| Enabled | `pgbackrest_enabled` (`true`) | When false, `archive_command` falls back to a no-op - **no backups, no PITR**. |
| Repo type | `pgbackrest_repo_type` (`posix`) | Local/NFS path by default. Set to `s3` (fill in `pgbackrest_s3_*` + vault S3 keys) for real off-host durability. |
| Retention | `pgbackrest_retention_full` (2), `pgbackrest_retention_diff` (6) | Keeps the last 2 full backups and 6 diffs between them. |
| Async archiving | `pgbackrest_archive_async` (`true`) | WAL pushes are queued through a local spool directory so a slow repo doesn't stall the primary's WAL writer. |
| Schedule | `pgbackrest_full_backup_schedule` (Sundays 01:00), `pgbackrest_diff_backup_schedule` (other days 01:00) | Implemented as systemd timers that run on every node but the wrapper script checks Patroni's `/primary` first and exits immediately on non-leaders. |
| Replica rebuild | `create_replica_methods: [pgbackrest, basebackup]` in Patroni | A failed/rebuilt replica restores from the pgBackRest repo first (doesn't load the primary), falling back to `pg_basebackup` only if that's not possible. |

Verify it's actually working: `sudo -u postgres pgbackrest --stanza=<scope> check` and `... info`. The role runs `check` automatically on the leader during deployment and logs the result.
