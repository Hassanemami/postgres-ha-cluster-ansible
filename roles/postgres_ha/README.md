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

- Ubuntu/Debian target hosts (uses `apt`)
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
| `etcd_version` | `latest` | etcd release to install. `latest` always fetches the newest GitHub release; pin a tag (e.g. `v3.6.13`) to lock it |
| `auto_update_packages` | `true` | when true, every run upgrades PostgreSQL (within its pinned major), Patroni, PgBouncer and HAProxy to the newest package available, and etcd to the newest GitHub release. Set to `false` to install once and never auto-upgrade |
| `pg_hba_extra_networks` | `[]` | extra CIDRs allowed to reach PostgreSQL |
| `pg_memory_budget_percent` | `70` | % of RAM Patroni is allowed to plan around |

### How the "always latest" versioning works

- **PostgreSQL major version**: on a node's first run, the role queries the
  PGDG apt repository for the highest `postgresql-XX` package it offers and
  writes that number to `/etc/postgres-ha-pg-version` on the host. Every
  later run reads that file instead of re-detecting, so a future PGDG
  release (say, PostgreSQL 19) never gets pulled onto an already-running
  cluster by surprise - bumping a running cluster to a new major version is
  a manual operation (`pg_dumpall`/`pg_upgrade` or logical replication),
  not something Patroni or this role does for you. To move to a new major
  version deliberately, delete that marker file (or bump `pg_version`
  explicitly) as part of a planned upgrade.
- **PostgreSQL minor/patch version, Patroni, PgBouncer, HAProxy**: installed
  with `state: latest` whenever `auto_update_packages` is true, so each
  playbook run pulls whatever is newest in the configured apt repos - no
  version numbers to edit by hand. Set `auto_update_packages: false` if you
  want fully pinned, install-once behaviour instead.
- **etcd**: has no apt package here (it's fetched straight from GitHub
  releases), so `etcd_version: latest` always grabs the newest tag. Note
  etcd's own release notes sometimes call out breaking changes across minor
  series (3.5 → 3.6 → 3.7) - test a rolling upgrade on a non-production
  cluster first if you want to be cautious, or pin a tag to control timing.

Secrets (`vault_postgres_superuser_password`, `vault_postgres_replicator_password`,
`vault_pgbouncer_password`) must be supplied via an Ansible Vault file - see
the repo-level `group_vars/all/vault.yml.example`. The role intentionally
ships with `"CHANGE_ME"` defaults so a run without a vault fails loudly
instead of deploying a cluster with a known password.

## Known limitations / things to review before production

- `archive_command` in the Patroni template is a no-op placeholder. WAL
  archiving is enabled (`archive_mode: on`) but nothing is actually
  archived - wire up `pgbackrest`, `wal-g`, or similar before relying on
  this for PITR/backups.
- The bundled SSL certificate is the Debian/Ubuntu self-signed "snakeoil"
  cert - it encrypts traffic but does not authenticate the server. Replace
  `ssl_cert_file` / `ssl_key_file` with real certificates.
- Local `trust` authentication is used for the `postgres`, `pgbouncer`,
  and local `replicator` connections in `pg_hba.conf`, matching the
  common Patroni pattern where OS-level access to the box is the trust
  boundary. Make sure shell access to these hosts is tightly controlled.

## Example playbook

```yaml
- hosts: all
  become: true
  roles:
    - postgres_ha
```

## License

MIT
