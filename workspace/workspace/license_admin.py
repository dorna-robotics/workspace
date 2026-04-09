"""
License generation tool — private repo only, never shipped to customers.

Usage (on each Pi before shipping):
    sudo python3 -m workspace.license_admin generate
"""

import sys
from workspace.license import _cpu_serial, _sign, LICENSE_PATH
import os


def generate():
    """Generate a license file for this Pi's CPU serial."""
    serial = _cpu_serial()
    signature = _sign(serial)

    os.makedirs(os.path.dirname(LICENSE_PATH), exist_ok=True)
    with open(LICENSE_PATH, "w") as f:
        f.write(f"{serial}\n{signature}\n")

    print(f"License generated for serial: {serial}")
    print(f"Written to: {LICENSE_PATH}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "generate":
        generate()
    else:
        print("Usage:")
        print("  sudo python3 -m workspace.license_admin generate")
