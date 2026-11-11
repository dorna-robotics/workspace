"""The bench's system — what ``upgrade`` installed, and running it.

The admin's System page (``system.html``) reads :func:`state`, starts
:func:`start`, and follows :func:`log`.

NOTHING HERE KNOWS THE PARTS. The ``upgrade`` repo's top-level setup.sh
writes ``RECORD`` at the end of every upgrade: each part's entry is
written by the part itself, from its own variables (``lib/record.sh`` —
name, label, folder, repo, branch, commit, exit code). A part added to
the upgrade appears here after the next upgrade; before any recorded
upgrade there are no parts to show, and the page says so.

THE UPGRADE IS ITS OWN SYSTEM JOB. It stops and restarts the
orchestrator, then reboots the unit, so it never runs as a child of
this process — that would end it halfway. ``systemd-run`` gives it its
own unit (``UNIT``): it outlives the orchestrator's restart, a second one
cannot start while it runs, and its output is in the journal, which
outlives the reboot — the page reads it back from there.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from gui.orchestrator import project_git

RECORD = "/var/lib/dorna/upgrade.json"
HELPER = "/usr/local/bin/upgrade"
UNIT = "dorna-upgrade"


def _record() -> Optional[dict]:
    try:
        with open(RECORD) as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except Exception as e:                      # present but unreadable: say so
        return {"error": f"{RECORD}: {e}"}


def running() -> bool:
    """Is an upgrade started from here still running?"""
    p = subprocess.run(["systemctl", "is-active", "--quiet", UNIT])
    return p.returncode == 0


def _uptime_s() -> Optional[float]:
    try:
        with open("/proc/uptime") as f:
            return float(f.read().split()[0])
    except Exception:
        return None


def state(fetch: bool = True) -> dict:
    """The unit, the last upgrade's record, and for each part the record
    lists, how its folder compares with what the upgrade installs
    (``project_git.part_state`` — fetched in parallel)."""
    rec = _record()
    parts = list((rec or {}).get("parts") or [])

    def one(p):
        e = dict(p)
        if p.get("dir"):
            try:
                e["now"] = project_git.part_state(p["dir"], p.get("branch") or "", fetch)
            except project_git.GitError as ex:
                e["now"] = {"exists": True, "git": False, "error": str(ex)}
        return e

    with ThreadPoolExecutor(max_workers=max(1, len(parts))) as pool:
        parts = list(pool.map(one, parts))
    return {
        "host": socket.gethostname(),
        "uptime_s": _uptime_s(),
        "helper": os.path.isfile(HELPER) and os.access(HELPER, os.X_OK),
        "helper_path": HELPER,
        "record": {k: v for k, v in (rec or {}).items() if k != "parts"} if rec else None,
        "parts": parts,
        "running": running(),
        "unit": UNIT,
        "now": time.time(),
    }


def start() -> dict:
    """Start ``upgrade`` as its own system job. Refused when it is not
    installed or one is already running — everything else (launched
    projects, local changes) is the page's warning, confirmed past."""
    if not (os.path.isfile(HELPER) and os.access(HELPER, os.X_OK)):
        raise RuntimeError(f"{HELPER} is not installed on this unit — run the first-time "
                           f"upgrade from a terminal (the upgrade repo's README)")
    if running():
        raise RuntimeError("an upgrade is already running")
    started = time.time()
    p = subprocess.run(
        ["systemd-run", f"--unit={UNIT}", "--collect",
         "--description=Dorna upgrade (admin System page)", HELPER],
        capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError((p.stderr or p.stdout).strip() or "systemd-run failed")
    return {"started_at": started, "unit": UNIT}


def log(since: float, cursor: str = "") -> dict:
    """The upgrade's output since ``since`` (epoch s) — or after
    ``cursor`` — from the journal: it survives the orchestrator's restart
    and the reboot. Lines in order, and the cursor to ask from next."""
    cmd = ["journalctl", "-u", UNIT, "-o", "json", "--no-pager"]
    cmd += ["--after-cursor", cursor] if cursor else ["--since", f"@{int(since)}"]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    lines, last = [], cursor
    for raw in p.stdout.splitlines():
        try:
            e = json.loads(raw)
        except ValueError:
            continue
        msg = e.get("MESSAGE", "")
        if isinstance(msg, list):               # non-UTF-8 output arrives as bytes
            msg = bytes(msg).decode("utf-8", "replace")
        lines.append({"t": int(e.get("__REALTIME_TIMESTAMP", "0")) / 1e6, "text": msg})
        last = e.get("__CURSOR", last)
    return {"lines": lines, "cursor": last, "running": running()}
