"""Provision this host as the site's device-bus broker.

    sudo python3 -m workspace.devices.provision_broker

Idempotent: writes /etc/mosquitto/conf.d/dorna-bus.conf so mosquitto
listens on the LAN (device units publish here via the bus_connect
handshake — device-guide §8), then restarts mosquitto only if the file
actually changed. Part of the standard workspace upgrade
(docs/upgrade-guide.md); safe to run on every upgrade.
"""

from __future__ import annotations

import os
import subprocess
import sys

CONF_PATH = "/etc/mosquitto/conf.d/dorna-bus.conf"
CONF_BODY = (
    "# Dorna device bus — the workspace host is the site's broker.\n"
    "# Written by workspace.devices.provision_broker (upgrade step).\n"
    "listener 1883 0.0.0.0\n"
    "allow_anonymous true\n"
)


def main() -> int:
    if os.geteuid() != 0:
        print("run with sudo: sudo python3 -m workspace.devices.provision_broker")
        return 1
    try:
        current = open(CONF_PATH).read()
    except FileNotFoundError:
        current = None
    if current == CONF_BODY:
        print(f"[broker] {CONF_PATH} already provisioned — nothing to do")
        return 0
    os.makedirs(os.path.dirname(CONF_PATH), exist_ok=True)
    with open(CONF_PATH, "w") as f:
        f.write(CONF_BODY)
    print(f"[broker] wrote {CONF_PATH}")
    try:
        subprocess.run(["systemctl", "restart", "mosquitto"], check=True)
        print("[broker] mosquitto restarted — device bus listening on the LAN")
    except Exception as ex:
        print(f"[broker] wrote config but mosquitto restart failed ({ex}) — "
              f"is mosquitto installed? sudo apt install mosquitto")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
