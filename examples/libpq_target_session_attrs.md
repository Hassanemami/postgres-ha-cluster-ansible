# Alternative: connect straight to PostgreSQL with libpq multi-host

If your driver is libpq-based (native `psql`, most C/C++/Rust clients, or
Python's psycopg with libpq) you can skip HAProxy for the write path
entirely and let the client itself find the primary, using
`target_session_attrs`:

```
postgresql://app_user:secret@db1:5432,db2:5432,db3:5432/app?target_session_attrs=read-write&sslmode=require
```

libpq tries each host in the list in order and connects to the first one
that satisfies `target_session_attrs`. `read-write` finds the current
primary; `any` (the default) connects to whichever host answers first,
which is what you want for read replicas:

```
postgresql://app_user:secret@db1:5432,db2:5432,db3:5432/app?target_session_attrs=any&sslmode=require
```

Trade-offs vs. going through HAProxy:
- No extra hop, slightly lower latency per query.
- Failover detection depends on the client re-resolving the host list on
  reconnect - it does NOT continuously health-check like HAProxy does, so a
  connection that's already open won't notice a promotion until it breaks.
- Bypasses PgBouncer, so you lose connection pooling unless you add it
  yourself in front of this connection string.
- Only works with libpq-based drivers. JDBC's pgjdbc supports the same
  parameter (`targetServerType=primary`); most other language drivers do
  not implement it and need the HAProxy approach instead.

For most setups, HAProxy + PgBouncer (the default in this project) is the
simpler and more portable choice - use this only if you specifically want
to avoid the extra network hop.
