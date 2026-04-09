"""
Hardware-locked license verification for Dorna Workspace.

- verify(): reads CPU serial + tier + signature, checks validity
- get_tier(): returns the current license tier
- This file is compiled to .so in release builds

License file format (/etc/dorna/.license):
    line 1: CPU serial
    line 2: tier (e.g. "default", "basic", "pro")
    line 3: HMAC signature of serial+tier

For license generation, see license_admin.py (private repo only).
"""

import hashlib
import hmac
import os
import sys

# Secret key — compiled to .so in release, not readable
_SECRET = b"IeXIrWk2wHJWvRwTO4dUQTmtZK-n3cXaqIbDem49MzA="

LICENSE_PATH = "/etc/dorna/.license"

# Cached tier after first verify() call
_cached_tier = None


def _cpu_serial() -> str:
    """Read the Raspberry Pi CPU serial from /proc/cpuinfo."""
    try:
        with open("/proc/cpuinfo", "r") as f:
            for line in f:
                if line.startswith("Serial"):
                    return line.strip().split(":")[-1].strip()
    except FileNotFoundError:
        pass
    raise RuntimeError("Could not read CPU serial — is this a Raspberry Pi?")


def _sign(serial: str, tier: str) -> str:
    """Sign a CPU serial + tier with the secret key."""
    payload = f"{serial}:{tier}"
    return hmac.new(_SECRET, payload.encode(), hashlib.sha256).hexdigest()


def verify():
    """Verify the license matches this Pi's CPU serial. Raises on failure."""
    global _cached_tier
    serial = _cpu_serial()

    if not os.path.isfile(LICENSE_PATH):
        raise RuntimeError(
            f"No license file found at {LICENSE_PATH}. "
            f"Contact Dorna support to activate this device."
        )

    with open(LICENSE_PATH, "r") as f:
        lines = f.read().strip().split("\n")

    if len(lines) < 3:
        raise RuntimeError("Invalid license file format.")

    licensed_serial = lines[0].strip()
    licensed_tier = lines[1].strip()
    licensed_sig = lines[2].strip()

    if licensed_serial != serial:
        raise RuntimeError(
            f"License mismatch — licensed for {licensed_serial}, "
            f"this device is {serial}."
        )

    expected_sig = _sign(serial, licensed_tier)
    if not hmac.compare_digest(licensed_sig, expected_sig):
        raise RuntimeError("License signature invalid.")

    _cached_tier = licensed_tier


def get_tier() -> str:
    """Return the license tier. Call verify() first."""
    if _cached_tier is None:
        verify()
    return _cached_tier


# CLI: sudo python3 -m workspace.license verify
if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "verify":
        verify()
        print(f"License OK. Tier: {get_tier()}")
    else:
        print("Usage:")
        print("  sudo python3 -m workspace.license verify   # check license")
