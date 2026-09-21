# Running behind a filtered network (mirrors and offline installs)

A default run reaches out to several places on the public internet. From
some networks - corporate egress filtering, air-gapped environments, or
upstreams that geoblock the client (which is the normal case for several of
these from Iran) - one or more of them will simply not answer, and the run
dies halfway through with a half-built cluster.

Preflight now probes the endpoints the current run actually needs *before*
anything is installed, and fails with the list of what it could not reach.
Set `preflight_check_connectivity: false` to skip that probe.

## What gets fetched, and the variable that points at it

| What | Variable | Default | When |
|---|---|---|---|
| PGDG apt repo | `pgdg_apt_uri` | `http://apt.postgresql.org/pub/repos/apt` | Debian family |
| PGDG signing key | `pgdg_apt_key_url` | `https://www.postgresql.org/media/keys/ACCC4CF8.asc` | Debian family |
| PGDG yum repo RPM | `pgdg_yum_repo_rpm` | `https://download.postgresql.org/.../pgdg-redhat-repo-latest.noarch.rpm` | RedHat family |
| EPEL | `epel_release_package` | `epel-release` | RedHat family, `epel_enabled: true` |
| etcd binaries | `etcd_download_base_url` | `https://github.com/etcd-io/etcd/releases/download` | cluster mode |
| GitHub API | `github_api_url` | `https://api.github.com` | only when `etcd_version: latest` or `pgcat_version: latest` |
| rustup | `rustup_install_url` | `https://sh.rustup.rs` | only when building PgCat on the nodes |
| crates.io | (cargo's own default) | - | only when building PgCat on the nodes |

## Recipes

**Point at internal mirrors.** In `group_vars/all/vars.yml`:

```yaml
pgdg_apt_uri: "http://mirror.internal/postgresql/apt"
pgdg_apt_key_url: "http://mirror.internal/postgresql/ACCC4CF8.asc"
pgdg_yum_repo_rpm: "http://mirror.internal/postgresql/pgdg-redhat-repo-latest.noarch.rpm"
etcd_download_base_url: "http://mirror.internal/etcd/releases/download"
epel_release_package: "http://mirror.internal/epel/epel-release-latest-9.noarch.rpm"
```

**Never call the GitHub API.** Keep `etcd_version` and `pgcat_version`
pinned to a tag (the defaults are pinned). `latest` is the only thing that
hits `api.github.com`.

**Don't build PgCat on the database nodes.** This is the default now.
Build it once somewhere with working network access:

```bash
git clone https://github.com/postgresml/pgcat && cd pgcat
git checkout v1.3.0
cargo build --release
```

then set `pgcat_binary_src: /path/to/pgcat` on the control node. That
removes rustup, crates.io, and the build itself from the target hosts.

**Fully offline.** Mirror the PGDG repo and EPEL locally, place the etcd
tarball on an internal HTTP server, set `pgcat_binary_src`, and set
`manage_os_repos: false` if your hosts already have the repositories they
need. Preflight's endpoint list is computed from the same variables, so it
follows your overrides automatically.
