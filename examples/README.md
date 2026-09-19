# Example application connections

Runnable snippets showing the dual-pool pattern described in the main
README's "Connecting your application" section: one pool for writes (hits
the primary), one pool for reads (load-balanced across replicas).

Replace the placeholders (`DB_HOST`, ports, credentials) with your actual
values - these ports match the cluster's defaults
(`group_vars/all/vars.yml`): 5000 = write, 5001 = read.

- `pgcat_single_pool.py` - Option A (PgCat): one pool, automatic routing.
