# PgBouncer

| Setting | Value | Why |
|---|---|---|
| `pool_mode` | `transaction` | Server connection returned to the pool after each transaction, not each client disconnect - lets a small number of real PostgreSQL connections serve far more client connections. See the caveats in "Connecting your application" above. |
| `auth_type` / `auth_query` | `scram-sha-256` / `user_search()` | PgBouncer looks up credentials live from PostgreSQL via a `SECURITY DEFINER` function rather than keeping its own copy of every password - so password changes in PostgreSQL take effect immediately, no PgBouncer config re-deploy needed. |
| `max_client_conn` | 80000 | How many application connections PgBouncer accepts. Tune down on smaller hosts. |
| `default_pool_size` | 700 | Real PostgreSQL connections per database/user pair. Must fit under PostgreSQL's `max_connections` ({{ pg_max_connections }}) across all PgBouncer instances that connect to it. |

PgBouncer runs on **every** node (not just the primary) and always points
at that node's own local PostgreSQL over the Unix socket - it's HAProxy,
not PgBouncer, that decides which node's PgBouncer a connection reaches.
