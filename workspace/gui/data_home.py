"""One machine-local data directory for every workspace surface.

``~/.workspace/`` — in the INVOKING user's home, SUDO_USER-resolved so
the sudo-run servers and plain dev runs land on the same files — holds
every piece of machine-local state the platform persists outside a
project folder: the orchestrator registry, status/logs for remote or
bare-name workspaces, scene-builder perf samples. One place, on
purpose: /tmp is tmpfs (wiped every reboot) and install trees are
replaced by upgrades — both have silently eaten user data before
(orchestrator.py's registry history).

Deliberate exception: bytecode caches stay in ``/tmp/pycache`` — that
is volatile BY DESIGN, protecting the Pi's SD card.
"""

import os


def _invoker():
    """(name, pwd entry) of the sudo invoker, or (None, None)."""
    user = os.environ.get("SUDO_USER", "").strip()
    if user:
        try:
            import pwd
            return user, pwd.getpwnam(user)
        except Exception:
            pass
    return None, None


def data_home() -> str:
    """``~/.workspace`` of the invoking user, created on demand and
    handed to that user even when a sudo-run server creates it."""
    _, pw = _invoker()
    home = pw.pw_dir if pw else os.path.expanduser("~")
    path = os.path.join(home, ".workspace")
    try:
        os.makedirs(path, exist_ok=True)
        if pw and os.geteuid() == 0:
            os.chown(path, pw.pw_uid, pw.pw_gid)
    except Exception:
        pass
    return path


def data_path(*parts: str) -> str:
    """A path under :func:`data_home`; parent dirs created (and owned
    by the invoking user) on the way."""
    path = os.path.join(data_home(), *parts)
    parent = os.path.dirname(path)
    try:
        os.makedirs(parent, exist_ok=True)
        _, pw = _invoker()
        if pw and os.geteuid() == 0:
            os.chown(parent, pw.pw_uid, pw.pw_gid)
    except Exception:
        pass
    return path
