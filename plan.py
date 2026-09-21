#!/usr/bin/env python3
"""
Interactive wizard for planning a postgres_ha deployment.

Sets deployment_mode / connection_mode in group_vars/all/vars.yml and
writes inventory/hosts.yml - the two things nearly every deployment needs
to get right before touching anything else. Nothing this script does is
required: you can edit those files by hand instead, and re-running the
wizard is safe (it asks before overwriting anything).

Usage:
    ./plan.py                  interactive wizard
    ./plan.py --help           show non-interactive flags for CI/scripting
"""
import argparse
import ipaddress
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
VARS_FILE = REPO_ROOT / "group_vars" / "all" / "vars.yml"
INVENTORY_FILE = REPO_ROOT / "inventory" / "hosts.yml"


def ask(prompt, default=None, choices=None):
    suffix = f" [{default}]" if default is not None else ""
    while True:
        raw = input(f"{prompt}{suffix}: ").strip()
        if not raw and default is not None:
            return default
        if choices and raw not in choices:
            print(f"  Please enter one of: {', '.join(choices)}")
            continue
        if raw:
            return raw
        print("  This needs an answer.")


def ask_yesno(prompt, default=True):
    d = "Y/n" if default else "y/N"
    while True:
        raw = input(f"{prompt} [{d}]: ").strip().lower()
        if not raw:
            return default
        if raw in ("y", "yes"):
            return True
        if raw in ("n", "no"):
            return False
        print("  Please answer y or n.")


def ask_ip(prompt, default=None):
    while True:
        raw = ask(prompt, default=default)
        try:
            ipaddress.ip_address(raw)
            return raw
        except ValueError:
            print(f"  '{raw}' doesn't look like a valid IP address.")


def wizard():
    print(__doc__.split("Usage:")[0].strip())
    print()
    print("=" * 78)
    print("Step 1 of 3 - deployment shape")
    print("=" * 78)
    print("""
  1) single_node  - one plain PostgreSQL instance. No etcd, no Patroni, no
                     failover, no watchdog, no keepalived, no HAProxy/PgCat.
                     Still gets PgBouncer pooling, pgBackRest backups, and
                     monitoring. Simplest option - good for dev/staging or
                     a first deployment.
  2) cluster      - full Patroni + etcd high availability across 3+ nodes,
                     with automatic failover.
""")
    mode_choice = ask("Which one?", default="2", choices=["1", "2"])
    deployment_mode = "single_node" if mode_choice == "1" else "cluster"

    connection_mode = "haproxy"
    if deployment_mode == "cluster":
        print()
        print("=" * 78)
        print("Step 2 of 3 - how applications connect")
        print("=" * 78)
        print("""
  1) haproxy - classic pattern: separate write (5000) and read (5001+)
               ports, your app picks which per query. Full SCRAM-SHA-256
               auth end-to-end. Installs HAProxy + PgBouncer.
  2) pgcat   - one smart endpoint (6433): PgCat parses every query itself
               and routes reads to a replica, writes to the primary,
               inside a single connection - no dual-pool app logic needed.
               Client auth is MD5 only (a PgCat limitation, not a
               misconfiguration - see docs/PGCAT.md). Installs PgCat
               instead of PgBouncer; HAProxy still runs, but only to
               load-balance across PgCat instances.

  Not sure? Start with haproxy - it's the more battle-tested, SCRAM-
  everywhere path. See docs/PLANNING.md for the full comparison.
""")
        conn_choice = ask("Which one?", default="1", choices=["1", "2"])
        connection_mode = "haproxy" if conn_choice == "1" else "pgcat"
    else:
        print()
        print("(Skipping connection-method question - not used in single_node mode.)")

    print()
    print("=" * 78)
    print("Step 3 of 3 - inventory")
    print("=" * 78)
    hosts = []
    if deployment_mode == "single_node":
        print("\nsingle_node mode needs exactly one host.\n")
        ip = ask_ip("IP address of the server")
        hosts = [("db1", ip)]
    else:
        print("""
cluster mode needs an ODD number of nodes, 3 or more (etcd quorum: a
2-node cluster can't survive a single failure, and even numbers beyond
that don't add fault tolerance over the odd number below them).
""")
        while True:
            n_raw = ask("How many nodes?", default="3")
            try:
                n = int(n_raw)
            except ValueError:
                print("  Enter a number.")
                continue
            if n < 3 or n % 2 == 0:
                print("  Needs to be odd and at least 3 (3, 5, 7, ...).")
                continue
            break
        for i in range(1, n + 1):
            ip = ask_ip(f"  IP address of node {i}")
            hosts.append((f"db{i}", ip))

    keepalived_vip = ""
    if deployment_mode == "cluster":
        print()
        if ask_yesno(
            "Use a floating virtual IP (keepalived) so apps have one stable "
            "address that always points at a healthy node?",
            default=True,
        ):
            keepalived_vip = ask_ip(
                "  VIP address (must be free on the same subnet as your nodes, "
                "not one of the node IPs above)"
            )

    return deployment_mode, connection_mode, hosts, keepalived_vip


def render_inventory(hosts):
    lines = ["---", "all:", "  hosts:"]
    for name, ip in hosts:
        lines.append(f"    {name}:")
        lines.append(f"      ansible_host: {ip}")
    lines += [
        "",
        "  vars:",
        "    ansible_python_interpreter: /usr/bin/python3",
        "    # ansible_user: ubuntu",
        "    # ansible_ssh_private_key_file: ~/.ssh/id_ed25519",
        "",
    ]
    return "\n".join(lines)


def write_inventory(hosts, force=False):
    if INVENTORY_FILE.exists() and not force:
        print(f"\n{INVENTORY_FILE} already exists.")
        if not ask_yesno("Overwrite it with the hosts you just entered?", default=False):
            print("Leaving the existing inventory untouched.")
            return
    INVENTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    INVENTORY_FILE.write_text(render_inventory(hosts))
    print(f"Wrote {INVENTORY_FILE}")


def update_vars_file(deployment_mode, connection_mode, keepalived_vip):
    if not VARS_FILE.exists():
        print(f"ERROR: {VARS_FILE} not found - is this the repo root?", file=sys.stderr)
        sys.exit(1)

    text = VARS_FILE.read_text()

    def set_or_append(text, key, value):
        pattern = rf'^{re.escape(key)}:\s*.*$'
        replacement = f'{key}: {value}'
        new_text, count = re.subn(pattern, replacement, text, count=1, flags=re.MULTILINE)
        if count == 0:
            new_text = new_text.rstrip("\n") + f"\n{replacement}\n"
        return new_text

    text = set_or_append(text, "deployment_mode", f'"{deployment_mode}"')
    text = set_or_append(text, "connection_mode", f'"{connection_mode}"')

    if keepalived_vip:
        text = set_or_append(text, "keepalived_vip", f'"{keepalived_vip}"')
        text = set_or_append(text, "keepalived_enabled", "true")
    elif deployment_mode == "cluster":
        # Explicit "false" is required here, not just omission: the default
        # in roles/postgres_ha/defaults/main.yml is keepalived_enabled: true,
        # and preflight.yml fails the whole run if that default is left on
        # with no VIP configured.
        text = set_or_append(text, "keepalived_enabled", "false")

    VARS_FILE.write_text(text)
    print(f"Updated {VARS_FILE}")


def print_next_steps(deployment_mode, connection_mode, hosts):
    print()
    print("=" * 78)
    print("Done. Next steps:")
    print("=" * 78)
    print(f"""
  1. Install required Ansible collections:
       ansible-galaxy collection install -r requirements.yml

  2. Set up secrets:
       cp group_vars/all/vault.yml.example group_vars/all/vault.yml
       $EDITOR group_vars/all/vault.yml
       ansible-vault encrypt group_vars/all/vault.yml

  3. Declare at least one application database/user in
     group_vars/all/vars.yml (postgres_databases / postgres_users) -
     required for PgCat mode, optional but recommended otherwise.

  4. Review group_vars/all/vars.yml for anything else you want to change
     (memory budget, backup retention, TLS, ...) - see docs/CONFIGURATION.md.

  5. Run it:
       ansible-playbook site.yml --ask-vault-pass

Summary of what you chose:
  deployment_mode:  {deployment_mode}
  connection_mode:  {connection_mode if deployment_mode == "cluster" else "n/a (single_node)"}
  nodes:            {", ".join(ip for _, ip in hosts)}
""")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mode", choices=["single_node", "cluster"], help="skip the interactive prompt for step 1")
    parser.add_argument("--connection", choices=["haproxy", "pgcat"], help="skip the interactive prompt for step 2 (cluster mode only)")
    parser.add_argument("--nodes", help="comma-separated IPs, skips step 3, e.g. --nodes 10.0.0.11,10.0.0.12,10.0.0.13")
    parser.add_argument("--vip", default="", help="keepalived VIP address (cluster mode only)")
    parser.add_argument("--force", action="store_true", help="overwrite inventory/hosts.yml without asking")
    args = parser.parse_args()

    if args.mode and args.nodes:
        deployment_mode = args.mode
        connection_mode = args.connection or "haproxy"
        ip_list = [ip.strip() for ip in args.nodes.split(",") if ip.strip()]
        for ip in ip_list:
            ipaddress.ip_address(ip)  # raises ValueError on bad input
        if deployment_mode == "single_node" and len(ip_list) != 1:
            parser.error("single_node needs exactly one --nodes IP")
        if deployment_mode == "cluster" and (len(ip_list) < 3 or len(ip_list) % 2 == 0):
            parser.error("cluster needs an odd number of --nodes IPs, 3 or more")
        hosts = [(f"db{i}", ip) for i, ip in enumerate(ip_list, start=1)]
        keepalived_vip = args.vip
    else:
        deployment_mode, connection_mode, hosts, keepalived_vip = wizard()

    update_vars_file(deployment_mode, connection_mode, keepalived_vip)
    write_inventory(hosts, force=args.force)
    print_next_steps(deployment_mode, connection_mode, hosts)


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled.")
        sys.exit(1)
