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
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from gui.orchestrator import project_git

RECORD = "/var/lib/dorna/upgrade.json"
HELPER = "/usr/local/bin/upgrade"
UNIT = "dorna-upgrade"
# How stale the nav's "parts to update" may get before it is checked again
# (one git fetch per part, in the background). The System page's own
# check refreshes it at once.
CHECK_EVERY_S = 3600

_cache = {"to_update": None, "checked_at": None}
_lock = threading.Lock()
_checking = False


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


def needs_update(part: dict) -> bool:
    """THE rule for "this part is not up to date": its folder is missing
    (a new install), behind what the upgrade installs, or on another
    branch. Unknown (no folder to compare, the remote not reachable) is
    not counted. The System page's status and the nav's badge both read
    it from here."""
    if not part.get("dir"):
        return False
    n = part.get("now") or {}
    if not n.get("exists", True):
        return True
    if not n.get("git") or n.get("reachable") is False:
        return False
    return bool(n.get("behind") or n.get("switches"))


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
    to_update = [p["name"] for p in parts if needs_update(p)]
    if fetch:
        with _lock:
            _cache.update(to_update=to_update, checked_at=time.time())
    return {
        "to_update": to_update,
        "uptime_s": _uptime_s(),
        "helper": os.path.isfile(HELPER) and os.access(HELPER, os.X_OK),
        "helper_path": HELPER,
        "record": {k: v for k, v in (rec or {}).items() if k != "parts"} if rec else None,
        "parts": parts,
        "running": running(),
        "unit": UNIT,
        "now": time.time(),
    }


def summary() -> dict:
    """The nav's glance: the parts to update as of the last check, and
    whether an upgrade is running. A check older than ``CHECK_EVERY_S``
    starts a fresh one in the background and answers with what is known
    (``to_update`` is None until the first check is in)."""
    global _checking
    with _lock:
        stale = _cache["checked_at"] is None or time.time() - _cache["checked_at"] > CHECK_EVERY_S
        start_check = stale and not _checking
        if start_check:
            _checking = True
        out = dict(_cache)

    def check():
        global _checking
        try:
            state(fetch=True)
        except Exception:
            pass
        finally:
            with _lock:
                _checking = False

    if start_check:
        threading.Thread(target=check, daemon=True, name="system-check").start()
    out["running"] = running()
    return out


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
