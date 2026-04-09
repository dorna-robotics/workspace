"""
License generation tool — private repo only, never shipped to customers.

Usage:
    sudo python3 -m workspace.license_admin generate [tier]

Tier defaults to "default" if not specified.
"""

import sys
from workspace.license import _cpu_serial, _sign, LICENSE_PATH
import os


def generate(tier: str = "default"):
    """Generate a license file for this Pi's CPU serial."""
    serial = _cpu_serial()
    signature = _sign(serial, tier)

    os.makedirs(os.path.dirname(LICENSE_PATH), exist_ok=True)
    with open(LICENSE_PATH, "w") as f:
        f.write(f"{serial}\n{tier}\n{signature}\n")

    print(f"License generated for serial: {serial}")
    print(f"Tier: {tier}")
    print(f"Written to: {LICENSE_PATH}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "generate":
        tier = sys.argv[2] if len(sys.argv) > 2 else "default"
        generate(tier)
    else:
        print("Usage:")
        print("  sudo python3 -m workspace.license_admin generate [tier]")
        print("  tier: default, basic, pro, trial, edu (default: 'default')")
