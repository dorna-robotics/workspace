"""
Hardware-locked license system for Dorna Workspace.

- generate(): reads CPU serial, signs with secret key, writes license file
- verify(): reads CPU serial + license file, checks signature
- CLI: sudo python3 -m workspace.license generate
"""

import hashlib
import hmac
import os
import sys

# Secret key — embedded here, compiled to .so in release builds
_SECRET = b"IeXIrWk2wHJWvRwTO4dUQTmtZK-n3cXaqIbDem49MzA="

LICENSE_PATH = "/etc/dorna/.license"


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


def _sign(serial: str) -> str:
    """Sign a CPU serial with the secret key."""
    return hmac.new(_SECRET, serial.encode(), hashlib.sha256).hexdigest()


def generate():
    """Generate a license file for this Pi's CPU serial."""
    serial = _cpu_serial()
    signature = _sign(serial)

    os.makedirs(os.path.dirname(LICENSE_PATH), exist_ok=True)
    with open(LICENSE_PATH, "w") as f:
        f.write(f"{serial}\n{signature}\n")

    print(f"License generated for serial: {serial}")
    print(f"Written to: {LICENSE_PATH}")


def verify():
    """Verify the license matches this Pi's CPU serial. Raises on failure."""
    serial = _cpu_serial()

    if not os.path.isfile(LICENSE_PATH):
        raise RuntimeError(
            f"No license file found at {LICENSE_PATH}. "
            f"Run: sudo python3 -m workspace.license generate"
        )

    with open(LICENSE_PATH, "r") as f:
        lines = f.read().strip().split("\n")

    if len(lines) < 2:
        raise RuntimeError("Invalid license file format.")

    licensed_serial = lines[0].strip()
    licensed_sig = lines[1].strip()

    if licensed_serial != serial:
        raise RuntimeError(
            f"License mismatch — licensed for {licensed_serial}, "
            f"this device is {serial}."
        )

    expected_sig = _sign(serial)
    if not hmac.compare_digest(licensed_sig, expected_sig):
        raise RuntimeError("License signature invalid.")


# CLI entry point: sudo python3 -m workspace.license generate
if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "generate":
        generate()
    elif len(sys.argv) > 1 and sys.argv[1] == "verify":
        verify()
        print("License OK.")
    else:
        print("Usage:")
        print("  sudo python3 -m workspace.license generate   # create license for this Pi")
        print("  sudo python3 -m workspace.license verify     # check license")
