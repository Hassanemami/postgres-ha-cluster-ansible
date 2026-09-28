# Troubleshooting

Failures that have actually happened during deployments, what causes them,
and what the role now does about each one.

## HAProxy: "Job for haproxy.service failed because the control process exited with error code"

The Ansible template task succeeds (its `validate: haproxy -c -f %s` passes)
and then the service refuses to start. `journalctl -u haproxy` shows:

```
cannot bind UNIX socket (No such file or directory) [/run/haproxy/admin.sock]
Some protocols failed to start their listeners! Exiting.
```

**Cause.** On EL9 the haproxy RPM's tmpfiles entry creates `/var/lib/haproxy`
only, and its unit declares no `RuntimeDirectory=`. Nothing creates
`/run/haproxy`, so the `stats socket` line cannot bind. `haproxy -c` never
binds anything, which is why validation passes.

**Fix (in the role).** `tasks/copy_haproxy.yml` installs a systemd drop-in
with `RuntimeDirectory=haproxy`, so systemd creates and cleans up the
directory on every start. The task also captures `journalctl -u haproxy` and
fails with the real error instead of systemd's "see journalctl" message.

To check by hand:

```bash
systemctl cat haproxy | grep RuntimeDirectory
```

## HAProxy in `connection_mode: pgcat`

Earlier versions installed HAProxy in both connection modes and gave it a
`smart_pool` listener bound to `pgcat_port` - the same port PgCat binds on
the same host. That can only ever fail with "address already in use".
HAProxy is now installed in `connection_mode: haproxy` only, and keepalived's
health check follows whatever serves traffic in the chosen mode.

## Patroni: PostgreSQL will not start, TLS error

```
FATAL: could not load server certificate file "server.crt": No such file or directory
```

**Cause.** `pg_ssl_enabled` defaults to true, which sets `ssl = on`, but
neither `initdb` nor Patroni creates a certificate, and RHEL has no
snakeoil cert.

**Fix (in the role).** `tasks/pg_ssl.yml` generates a self-signed pair in
`pg_ssl_self_signed_dir` (`/etc/postgresql-ssl`) before Patroni starts, and
the Patroni config points `ssl_cert_file`/`ssl_key_file` at it - in both the
`bootstrap.dcs` section (new clusters) and the local `postgresql.parameters`
section, so an already-bootstrapped cluster picks it up on the next restart.
Set `pg_ssl_cert_file`/`pg_ssl_key_file` to use your own CA-issued
certificate instead.

## pgBackRest: stuck processes and lock conflicts in /tmp/pgbackrest

Symptoms: `unable to acquire lock`, background archive processes that never
finish, lock files whose owner no longer exists.

**Causes, both fixed here.**

1. `archive_command` is live from the moment Patroni bootstraps, so WAL is
   pushed before the stanza exists. Every push fails, and with
   `archive-async` on, failures accumulate.
2. pgBackRest's built-in `lock-path` is `/tmp/pgbackrest`. systemd units
   with `PrivateTmp=yes` get their own `/tmp`, so the postmaster's archive
   processes and a hand-run `pgbackrest` see *different* lock directories.

**Fix (in the role).** `lock-path` is set explicitly (`pgbackrest_lock_path`,
default `/run/pgbackrest`, recreated at boot via tmpfiles), the old
`/tmp/pgbackrest` is removed once nothing is running, and after the stanza is
created the role reports `pg_stat_archiver` so you can see whether archiving
actually recovered.

To clear a wedged state by hand:

```bash
sudo systemctl stop patroni
sudo pkill -x pgbackrest
sudo rm -rf /tmp/pgbackrest /run/pgbackrest/*
sudo systemctl start patroni
sudo -u postgres pgbackrest --stanza=<scope> stanza-create
sudo -u postgres pgbackrest --stanza=<scope> check
```

## pgBackRest: "Permission denied (publickey)" between nodes

The stanza lists every node (`pg1-host`, `pg2-host`, ...), which makes
pgBackRest reach its peers over SSH as the `postgres` user - and nothing had
set that up.

**Fix (in the role).** `tasks/pgbackrest_ssh.yml` generates an ed25519 key
for `postgres` on each node, authorizes every node's key everywhere, writes
`known_hosts` and an SSH client config, then verifies each hop and reports
the ones that still fail. Turn it off with
`pgbackrest_manage_ssh_keys: false`, or set `pgbackrest_cluster_aware: false`
for a local-only stanza that needs no SSH at all.

Check by hand:

```bash
sudo -u postgres ssh -o BatchMode=yes <other-node> true
```

## pgBackRest install fails on EL9: unresolved libssh2

The PGDG `pgbackrest` RPM depends on `libssh2`, which lives in CRB
(PowerTools on EL8, `codeready-builder-...` on subscribed RHEL).

**Fix (in the role).** `tasks/os_repos.yml` enables CRB/PowerTools and EPEL
before installing anything. Disable with `manage_os_repos: false` /
`epel_enabled: false` if your hosts get these from an internal mirror.

## SELinux: restorecon fails on a path that does not exist

On RHEL the PostgreSQL data directory does not exist until Patroni runs
`initdb`, but `tasks/security.yml` runs long before that. The file-context
rule is still registered; the `restorecon` is now skipped until the
directory exists.

## PgBouncer: "password authentication failed for user pgbouncer"

PgBouncer's `auth_query` connection needs credentials of its own. Without
an `auth_file` it can only connect where `pg_hba` says `trust` - which
Patroni never writes. The role now deploys
`<pgbouncer_conf_dir>/userlist.txt` holding just the `pgbouncer` user's
password, and `pg_hba.conf` no longer contains any `trust` line on either
deployment path.
