# Connecting your application

### Endpoints

| What you want | Connect to | Port | Goes to |
|---|---|---|---|
| Writes (INSERT/UPDATE/DELETE/DDL) | VIP or any HAProxy node | `5000` | current primary only, via PgBouncer |
| Reads (SELECT) | VIP or any HAProxy node | `5001` | round-robin across healthy replicas, via PgBouncer |
| Reads that must include the sync replica | VIP or any HAProxy node | `5002` | the synchronous replica only |
| Reads where any lag is acceptable, including async replicas | VIP or any HAProxy node | `5003` | async replicas only |
| Cluster status / stats page | any HAProxy node | `7000` | HAProxy's own stats UI (HTTP basic auth) |

Point your application at the **VIP** if `keepalived_enabled: true` (it
always resolves to whichever node currently runs a healthy HAProxy), or at
any individual node's address otherwise - HAProxy on every node has the
same view of cluster state via Patroni's REST API, so it doesn't matter
which one you pick.

### Why not "one connection, auto-routed by query"?

HAProxy operates on TCP connections, not SQL. It cannot look inside an
already-open connection and decide "this particular query is a SELECT,
reroute it" - by the time HAProxy sees a query, the connection is already
pinned to one backend for its lifetime. **This is a hard limitation of
every TCP-level proxy** (HAProxy, or a plain L4 load balancer), not a gap
in this configuration.

What actually happens in every real Patroni deployment (this one included)
is exactly the two-port design above: HAProxy continuously asks each
node's Patroni `/primary` and `/replica` REST endpoints "are you the
primary right now?" and routes each new connection on port 5000 or 5001
accordingly. Your application decides which port to use per query (or per
repository/DAO method) - see the examples below. If a node is promoted or
demoted, HAProxy notices within a few seconds (`inter 3s` health checks)
and starts routing new connections to the right place; it does not migrate
already-open connections.

If you want genuine per-statement routing inside a single connection
(the app just runs `SELECT`/`INSERT` and something else decides where each
one goes), that requires a SQL-aware proxy such as **pgpool-II** or
**PgCat**, sitting in front of PgBouncer, parsing each statement. That is a
meaningfully heavier component (another moving part with its own failure
modes, and pgpool-II's load balancing gives up as soon as a query is
inside an explicit transaction, since it can't know in advance whether a
later statement in the same transaction will write). It is not included by
default - say so if you want it added, and where in the stack it should sit.

### Example connections

See `examples/` in this repo for runnable snippets. The shape is always
the same: **two connection pools, one per port.**

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

```
# Any driver, as a plain connection string
postgresql://app_user:secret@<vip-or-host>:5000/app?sslmode=require   # writes
postgresql://app_user:secret@<vip-or-host>:5001/app?sslmode=require   # reads
```

An alternative that skips HAProxy entirely for libpq-based clients
(`target_session_attrs`) is documented in
`examples/libpq_target_session_attrs.md` - it has different trade-offs
(no pooling, faster per-query, weaker failover detection).

### Things that will bite you if you ignore them

- **Replication lag is real.** A replica can be milliseconds to seconds
  behind the primary. If your app writes something and immediately reads
  it back expecting to see it, read from the **write** pool (port 5000)
  for that specific query, not the read pool. This is the single most
  common bug in read/write-split applications.
- **PgBouncer pools in `transaction` mode** (this role's default): the
  server-side connection is returned to the pool at the end of each
  transaction, not each client disconnect. That means session-level
  features do not work reliably across queries: `SET` (outside a
  transaction), prepared statements kept open across transactions,
  `LISTEN`/`NOTIFY`, and advisory locks held outside a transaction can all
  behave unexpectedly. Wrap `SET LOCAL` inside the transaction it applies
  to, and use your driver's native prepared-statement support (most
  connection-pool-aware drivers handle this correctly already).
- **`sslmode=require`** encrypts the connection using the certificate this
  role deploys - a self-signed pair generated in `/etc/postgresql-ssl`
  unless you set `pg_ssl_cert_file`/`pg_ssl_key_file`. It does not verify
  server identity.
  Use `sslmode=verify-full` with a real CA once you've set one up
  (`pg_ssl_ca_file`).
- **Provisioning app users/databases**: this role does not create your
  application's database or role for you unless you tell it to. Add
  entries to `postgres_users` and `postgres_databases` in
  `group_vars/all/vars.yml` (passwords go in vault.yml) - see the example
  in that file. Without this, you'll need to create them by hand with
  `psql` against the write endpoint.
