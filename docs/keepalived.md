# keepalived (floating VIP)

A VRRP-based floating IP (`keepalived_vip`) that moves to whichever node's
endpoint process is currently healthy - HAProxy in `connection_mode:
haproxy`, PgCat in `connection_mode: pgcat` (checked via a script that just
confirms that process is running; the proxy's own health checks handle
which *backend* gets traffic; keepalived only handles which *node* holds
the IP). `nopreempt` is set so the VIP doesn't bounce back to a recovered
node and cause an unnecessary connection blip.
