# postgres_ha

Ansible role that builds a highly available PostgreSQL cluster out of:

- **etcd** - distributed consensus store used as Patroni's DCS (Distributed
  Configuration Store)
- **Patroni** - manages PostgreSQL, handles leader election and failover
- **PostgreSQL** - the database itself
- **PgBouncer** - connection pooler in front of PostgreSQL on every node
- **HAProxy** - routes traffic to the current primary / replicas based on
  Patroni's REST health-check endpoints

## Requirements

- Debian/Ubuntu (apt) or RHEL/Rocky/AlmaLinux (dnf) target hosts
- An odd number of nodes (3, 5, ...) so etcd keeps quorum
- Ansible collections in `requirements.yml` at the repo root:
  `ansible-galaxy collection install -r requirements.yml`
- Passwordless sudo (or `ansible_become_password`) on all target hosts

## Role variables

All tunables live in `defaults/main.yml` and are documented there. Notable
ones:

| Variable | Default | Purpose |
|---|---|---|
| `pg_version` | `auto` | PostgreSQL major version. `auto` detects the newest one PGDG offers on a node's first run and then pins it (see below); set a number (e.g. `18`) to skip detection |
| `etcd_version` | `v3.6.14` | etcd release to install. Pinned on purpose - set `latest` to always fetch the newest GitHub release instead |
| `auto_update_packages` | `false` | when true, every run upgrades PostgreSQL (within its pinned major), Patroni, PgBouncer and HAProxy to the newest package available. Off by default so re-running the playbook to change one setting doesn't also upgrade a live cluster |
| `pgbackrest_cluster_aware` | `true` | stanza lists every node (needed for backup-standby / remote restore) and the role distributes `postgres` SSH keys to make that work |
| `pgcat_binary_src` | `""` | path on the control node to a prebuilt PgCat binary; set this instead of building Rust on the database nodes |
| `pg_hba_extra_networks` | `[]` | extra CIDRs allowed to reach PostgreSQL |
| `pg_memory_budget_percent` | `70` | % of RAM Patroni is allowed to plan around |

### How versioning works

- **PostgreSQL major version**: on a node's first run, the role asks the
  PGDG repository for the highest `postgresql-XX` it offers and writes that
  number to `/etc/postgres-ha-pg-version` on the host. Every later run reads
  that file instead of re-detecting, so a future PGDG release never gets
  pulled onto an already-running cluster by surprise - bumping a running
  cluster to a new major version is a manual operation (`pg_dumpall` /
  `pg_upgrade` or logical replication). To move deliberately, delete the
  marker file or set `pg_version` explicitly as part of a planned upgrade.
- **PostgreSQL minor/patch, Patroni, PgBouncer, HAProxy**: installed with
  `state: present` by default, i.e. installed once and left alone. Set
  `auto_update_packages: true` to install with `state: latest` on every run
  instead - convenient for a lab, risky on a live cluster, since an
  unrelated re-run then also upgrades and restarts these services.
- **etcd**: fetched from GitHub releases and pinned to `etcd_version`.
  etcd's release notes call out behaviour changes across minor series
  (3.5 → 3.6 → 3.7), so upgrade one step at a time and test a rolling
  upgrade off production first. `etcd_version: latest` restores automatic
  tracking if you want it.

Secrets (`vault_postgres_superuser_password`, `vault_postgres_replicator_password`,
`vault_pgbouncer_password`) must be supplied via an Ansible Vault file - see
the repo-level `group_vars/all/vault.yml.example`. The role intentionally
ships with `"CHANGE_ME"` defaults so a run without a vault fails loudly
instead of deploying a cluster with a known password.

## Known limitations / things to review before production

- **TLS is off for etcd and the Patroni REST API** (`etcd_tls_enabled`,
  `patroni_restapi_tls_enabled`, both `false`), and this role does not run a
  CA. Plain-HTTP etcd means whoever reaches port 2379 owns your cluster
  state.
- **PostgreSQL's own certificate is self-signed** unless you set
  `pg_ssl_cert_file` / `pg_ssl_key_file`. The role generates a pair (in
  `pg_ssl_self_signed_dir`) because `ssl = on` without a certificate is a
  hard startup failure - but self-signed only encrypts, it proves nothing
  about server identity, so clients cannot meaningfully use
  `sslmode=verify-full` against it. `pg_require_ssl` is also `false` by
  default, so non-TLS connections are still accepted.
- **The pgBackRest repository is on the database host itself**
  (`pgbackrest_repo_type: posix`). A backup on the same machine as the
  database is not a backup - set `pgbackrest_repo_type: s3` (or point
  `pgbackrest_repo_path` at off-host storage) for real durability, and
  restore-test it.
- **PgCat's client-facing auth is MD5, not SCRAM**, and everything inside an
  explicit `BEGIN`/`COMMIT` goes to the primary. See `docs/PGCAT.md` before
  choosing `connection_mode: pgcat`.
- **Failover has not been validated by an automated test.** CI lints the
  role and renders/validates every template; it does not stand up a cluster
  and kill a primary. Run your own failover drill before you depend on it.

## Example playbook

```yaml
- hosts: all
  become: true
  roles:
    - postgres_ha
```

## License

GPL-3.0
