# Host tuning

Applied to every node regardless of OS family: Transparent Huge Pages
disabled (both at runtime and persisted via a systemd unit that runs
before PostgreSQL starts), `vm.overcommit_memory=2` with an 80% ratio (so
the OOM killer doesn't get to choose the postmaster and take down the
whole instance), conservative dirty-page writeback ratios, `swappiness=1`,
and raised `nofile`/`nproc` limits for the `postgres` user. Toggle off
entirely with `tune_kernel_parameters: false` if you tune the OS yourself.
