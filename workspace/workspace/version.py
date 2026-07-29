"""Platform version — auto-derived from git, no manual bumps.

Format: <base>.<commit count>+<short sha>  (e.g. 1.0.412+ab12cd3)

The patch number is the commit count on the checked-out branch, so the
version changes on EVERY commit; the sha pins the exact build. Deployed
benches are git clones (the upgrade syncs by fetch/reset), so git is
always available there; a repo-less copy falls back to the bare base.
The vision server versions itself the same way (dorna_vision/__init__).
"""

import os
import subprocess

BASE = "1.0"

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def version(base=BASE):
    try:
        n = subprocess.check_output(
            ["git", "-C", _ROOT, "rev-list", "--count", "HEAD"],
            stderr=subprocess.DEVNULL, text=True).strip()
        sha = subprocess.check_output(
            ["git", "-C", _ROOT, "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL, text=True).strip()
        return f"{base}.{n}+{sha}"
    except Exception:
        return base
