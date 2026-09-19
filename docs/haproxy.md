# HAProxy

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
