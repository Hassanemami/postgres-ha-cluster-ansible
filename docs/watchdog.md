# Watchdog (fencing)

Loads the kernel `softdog` module if no hardware watchdog device exists,
sets `/dev/watchdog` ownership to `postgres` (persisted via a udev rule so
it survives reboots), and wires the device path into Patroni's config.
`patroni_watchdog_mode: required` will refuse to let a node become leader
at all if the watchdog can't be armed - verify this on every node in a
non-production test before flipping it on.
