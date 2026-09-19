# keepalived (floating VIP)

A VRRP-based floating IP (`keepalived_vip`) that moves to whichever node's
HAProxy is currently healthy (checked via a script that just confirms the
`haproxy` process is running - HAProxy's own health checks handle
which *backend* gets traffic; keepalived only handles which *node* holds
the IP). `nopreempt` is set so the VIP doesn't bounce back to a recovered
node and cause an unnecessary connection blip.
