# PostgreSQL configuration files

On Debian/Ubuntu these are deployed once, before Patroni's first bootstrap,
as a wrapper (`postgresql.conf` `include`s `postgresql.base.conf`, which
Patroni then writes). On RedHat, Patroni owns both files directly inside
the data directory - see "Supported operating systems" above. Either way
the *content* Patroni writes is identical; only where it's stored differs.
