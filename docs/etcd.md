# etcd

| Setting | Variable | Notes |
|---|---|---|
| Version | `etcd_version` (`latest`) | Fetched from GitHub releases directly - see "Versions stay current automatically". |
| Backend quota | hardcoded 8GiB in the template | Raised from etcd's stock 2GB default. Hitting the quota trips a `NOSPACE` alarm that takes the whole Patroni cluster read-only. |
| Auto-compaction | `periodic`, 1h retention | Keeps old MVCC revisions from accumulating. |
| Defrag | weekly systemd timer (`etcd-defrag.timer`), staggered per node | Compaction alone doesn't shrink the on-disk file; defrag does. Never defrag all members simultaneously - it briefly stalls the member being defragged. |
| TLS | `etcd_tls_enabled` (`false`) | Off by default; see "Before you go to production". |
| Election tuning | `ETCD_ELECTION_TIMEOUT=5000`, `ETCD_HEARTBEAT_INTERVAL=1000` | More forgiving than etcd's LAN-tuned stock defaults (1000ms election) - reduces false-positive leader elections on a loaded network. |
