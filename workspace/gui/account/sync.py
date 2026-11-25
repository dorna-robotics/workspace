"""This unit's files, copied to the Dorna account server for the Dorna team.

While the unit is logged in, a background thread sends, for every project on
the home page (the orchestrator's registry) and for this unit's own state
folder (``~/.workspace``: the AI chats and their uploads, logs, the
registry), a manifest — each file's relative path, size, mtime and sha256,
and what was left out and why. The server answers with the contents it
doesn't have yet, and only those are uploaded, one file per request:

    POST /s/device/api/sync/manifest        {unit, tree, files, skipped} → {need: [sha256…]}
    POST /s/device/api/sync/blob/<sha256>   the file's bytes

So the first pass uploads everything, later passes only what changed. It
runs a minute after login (or after the server starts, when logged in), then
every SYNC_EVERY_S, and soon after a save in Developer mode or an AI turn
(``kick()``). It never blocks a page or a run: its own thread at the lowest
CPU priority, files read in small pieces, uploads throttled (slower still
while a project is launched), and when the server can't be reached it backs
off and tries later.

Never sent: secrets (.env*, account.json, keys, *key*/*secret*/*token*
files), .git internals, Python caches, temp files, files over
DORNA_SYNC_MAX_FILE_MB. Each is listed in the manifest's ``skipped`` with
the reason, not dropped silently. A log over LOG_TAIL is sent as its tail.

Settings (environment): DORNA_SYNC=0 turns it off; DORNA_SYNC_EVERY_MIN,
DORNA_SYNC_KBPS, DORNA_SYNC_MAX_FILE_MB.
"""
import hashlib
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request

from gui.data_home import data_home, data_path

SYNC_EVERY_S = float(os.environ.get("DORNA_SYNC_EVERY_MIN", "20")) * 60
FIRST_DELAY_S = float(os.environ.get("DORNA_SYNC_FIRST_S", "60"))
KICK_DELAY_S = 90                     # after a save: wait for the edits to settle
KBPS = float(os.environ.get("DORNA_SYNC_KBPS", "512"))       # upload bandwidth
BUSY_KBPS = min(KBPS, 128.0)          # ... while a project is launched
MAX_FILE = int(float(os.environ.get("DORNA_SYNC_MAX_FILE_MB", "50")) * (1 << 20))
MAX_TREE = 2 << 30                    # one project, all files together
MAX_FILES = 20000                     # one project
LOG_TAIL = 8 << 20                    # a bigger log: its last 8 MB
BACKOFF_MAX_S = 3600

SKIP_DIRS = {"__pycache__": "python cache", ".git": "git internals", ".ipynb_checkpoints": "notebook checkpoints",
             "node_modules": "node packages", ".venv": "virtualenv", "venv": "virtualenv",
             ".mypy_cache": "cache", ".pytest_cache": "cache", ".cache": "cache"}
SKIP_SUFFIX = (".pyc", ".pyo", ".tmp", ".part", ".swp", ".lock")
LOG_SUFFIX = (".log", ".jsonl", ".txt", ".csv")
# the same test the server applies (builder/device_sync.py _SECRET)
SECRET = re.compile(r"(^|/)(\.env(\..*)?|account\.json|\.netrc|id_[a-z0-9]+(\.pub)?|[^/]*\.(pem|p12|pfx))$"
                    r"|(^|/)[^/]*(key|secret|token|passw|credential)[^/]*$", re.I)
# ~/.workspace: what isn't worth sending
UNIT_SKIP = {"component_meshes": "mesh cache (rebuilt on demand)", "trash": "deleted projects (kept in history)",
             "sync_cache.json": "this sync's own cache", "pycache": "python cache"}


def enabled():
    return os.environ.get("DORNA_SYNC", "1").strip() not in ("0", "false", "no", "off")


def unit_id():
    """A stable id for this unit (survives logging in again): a hash of the machine id."""
    for p in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        try:
            with open(p) as f:
                mid = f.read().strip()
            if mid:
                return hashlib.sha256(("dorna-unit:" + mid).encode()).hexdigest()[:24]
        except OSError:
            pass
    path = data_path("unit_id")
    try:
        with open(path) as f:
            u = f.read().strip()
        if re.fullmatch(r"[0-9a-f]{24}", u):
            return u
    except OSError:
        pass
    u = os.urandom(12).hex()
    try:
        with open(path, "w") as f:
            f.write(u)
    except OSError:
        pass
    return u


def _low_priority():
    """This thread only (Linux: a thread's nice is its own): CPU and disk at the back of the queue."""
    try:
        os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), 19)
    except (AttributeError, OSError):
        pass
    try:                                # IOPRIO_CLASS_IDLE for this thread
        import ctypes
        libc = ctypes.CDLL(None, use_errno=True)
        nr = {"aarch64": 31, "x86_64": 251, "armv7l": 315}.get(os.uname().machine)
        if nr:
            libc.syscall(nr, 1, threading.get_native_id(), 3 << 13)
    except Exception:                   # noqa: BLE001 — best effort
        pass


# ── reading the folders ───────────────────────────────────────────────
class HashCache:
    """sha256 by (path, size, mtime_ns), kept in ~/.workspace/sync_cache.json: an unchanged
    file is never read again."""
    def __init__(self):
        self.path = data_path("sync_cache.json")
        try:
            with open(self.path) as f:
                self.d = json.load(f)
        except (OSError, ValueError):
            self.d = {}
        self.used, self.dirty = set(), False

    def get(self, path, st, tail):
        key = f"{path}|{tail}"
        self.used.add(key)
        v = self.d.get(key)
        if v and v[0] == st.st_size and v[1] == st.st_mtime_ns:
            return v[2]
        h = _sha(path, st.st_size - tail if tail else 0)
        if h:
            self.d[key] = [st.st_size, st.st_mtime_ns, h]
            self.dirty = True
        return h

    def save(self):
        if not self.dirty and len(self.used) == len(self.d):
            return
        self.d = {k: v for k, v in self.d.items() if k in self.used}
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w") as f:
                json.dump(self.d, f)
            os.replace(tmp, self.path)
        except OSError:
            pass
        self.dirty = False


def _sha(path, offset=0):
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            f.seek(offset)
            n = 0
            while True:
                b = f.read(256 << 10)
                if not b:
                    break
                h.update(b)
                n += len(b)
                if n >= 8 << 20:        # a pause every 8 MB: the disk stays free for a run
                    time.sleep(0.02)
                    n = 0
    except OSError:
        return None
    return h.hexdigest()


def scan(root, cache, prefix="", skip_top=None, out=None):
    """Walk ``root`` → (files, skipped); files carry the absolute path in "_abs"."""
    files, skipped = out if out else ([], [])
    total = sum(f["s"] for f in files)
    for dirpath, dirs, names in os.walk(root, followlinks=False):
        rel_dir = os.path.relpath(dirpath, root)
        rel_dir = "" if rel_dir == "." else rel_dir.replace(os.sep, "/") + "/"
        keep = []
        for d in sorted(dirs):
            rel = prefix + rel_dir + d
            why = SKIP_DIRS.get(d) or (skip_top.get(d) if skip_top and not rel_dir else None)
            if why:
                skipped.append({"p": rel + "/", "s": 0, "why": why})
            elif os.path.islink(os.path.join(dirpath, d)):
                skipped.append({"p": rel + "/", "s": 0, "why": "symlink"})
            else:
                keep.append(d)
        dirs[:] = keep
        for n in sorted(names):
            ap = os.path.join(dirpath, n)
            rel = prefix + rel_dir + n
            try:
                st = os.lstat(ap)
            except OSError:
                continue
            if not os.path.isfile(ap) or os.path.islink(ap):
                skipped.append({"p": rel, "s": 0, "why": "symlink" if os.path.islink(ap) else "not a file"})
                continue
            why = None
            if SECRET.search(rel):
                why = "secret"
            elif skip_top and not rel_dir and n in skip_top:
                why = skip_top[n]
            elif n.endswith(SKIP_SUFFIX):
                why = "temporary"
            tail = 0
            if not why and st.st_size > MAX_FILE:
                if n.endswith(LOG_SUFFIX):
                    tail = min(LOG_TAIL, MAX_FILE)
                else:
                    why = f"over {MAX_FILE >> 20} MB"
            if not why and len(files) >= MAX_FILES:
                why = f"project over {MAX_FILES} files"
            if not why and total + min(st.st_size, tail or st.st_size) > MAX_TREE:
                why = f"project over {MAX_TREE >> 30} GB"
            if why:
                skipped.append({"p": rel, "s": st.st_size, "why": why})
                continue
            h = cache.get(ap, st, tail)
            if not h:
                continue
            f = {"p": rel, "s": tail or st.st_size, "m": st.st_mtime, "h": h, "_abs": ap}
            if tail:
                f["tail_of"] = st.st_size
            files.append(f)
            total += f["s"]
    return files, skipped


def _launch_dirs(folder):
    """launch.yaml's records / replays / uploads / folders / core_dir → {key: path} (as written)."""
    try:
        import yaml
        with open(os.path.join(folder, "launch.yaml")) as f:
            cfg = yaml.safe_load(f) or {}
    except Exception:                   # noqa: BLE001 — no yaml / a broken file: the folder alone
        return {}
    out = {}
    for k in ("records", "replays", "uploads", "core_dir"):
        if isinstance(cfg.get(k), str) and cfg[k].strip():
            out[k] = cfg[k].strip()
    for x in cfg.get("folders") or []:
        if isinstance(x, dict) and isinstance(x.get("path"), str) and x.get("key"):
            out[f"folder:{x['key']}"[:32]] = x["path"].strip()
    return out


def project_tree(card, cache):
    folder = os.path.dirname(card.get("path_to_file") or "")
    if not folder or card.get("node_url") or not os.path.isdir(folder):
        return None
    root = os.path.realpath(folder)
    files, skipped = scan(root, cache)
    dirs, shown = _launch_dirs(root), {}
    for key, p in dirs.items():
        ap = os.path.realpath(p if os.path.isabs(p) else os.path.join(root, p))
        if ap == root or ap.startswith(root + os.sep):
            shown[key] = os.path.relpath(ap, root).replace(os.sep, "/")
        elif os.path.isdir(ap):         # a run folder outside the project: under _ext/<key>/
            tag = re.sub(r"[^A-Za-z0-9_-]", "_", key)
            scan(ap, cache, prefix=f"_ext/{tag}/", out=(files, skipped))
            shown[key] = f"_ext/{tag}"
    from gui.account.client import _core_of, _git_remote
    meta = {"kind": "project", "name": str(card["name"])[:128], "title": str(card.get("label") or "")[:128],
            "core": _core_of(root), "root": root, "git_remote": _git_remote(root), "dirs": shown}
    return meta, files, skipped


def unit_tree(cache):
    root = data_home()
    files, skipped = scan(root, cache, skip_top=UNIT_SKIP)
    return {"kind": "unit", "name": "workspace", "title": "This unit (~/.workspace)", "root": root}, files, skipped


def _registry_cards():
    from gui.account.client import _registry_path
    try:
        with open(_registry_path(), encoding="utf-8") as f:
            cards = json.load(f).get("workspaces") or []
    except (OSError, ValueError, AttributeError):
        return []
    return [c for c in cards if isinstance(c, dict) and c.get("name")]


def _name_ok(name):
    return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._ -]{0,127}", name))


# ── talking to the server ─────────────────────────────────────────────
def busy():
    """A project is launched on this unit (its runtime is up): sync slower."""
    tools = sys.modules.get("gui.ai.tools")
    orch = getattr(tools, "ORCH", None) if tools else None
    try:
        return bool(orch) and any(orch.is_launched(n) for n in list(orch.workspaces))
    except Exception:                   # noqa: BLE001
        return False


class Offline(Exception):
    pass


class SignedOut(Exception):
    pass


def _post(path, body, token, timeout, content_type="application/json", length=None):
    from gui.account.client import server_url
    req = urllib.request.Request(server_url() + path, data=body, method="POST")
    req.add_header("Content-Type", content_type)
    req.add_header("Authorization", f"Bearer {token}")
    if length is not None:
        req.add_header("Content-Length", str(length))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw, code = r.read(), r.status
    except urllib.error.HTTPError as e:
        raw, code = e.read(), e.code
    except OSError as e:
        raise Offline(str(e))
    if code == 401:
        raise SignedOut()
    try:
        return code, json.loads(raw.decode() or "null")
    except ValueError:
        return code, None


def _throttled(path, offset, length, stop):
    """The file's bytes in 64 KB pieces, at most KBPS (BUSY_KBPS while a project is launched)."""
    with open(path, "rb") as f:
        f.seek(offset)
        left, t0, sent = length, time.monotonic(), 0
        while left > 0:
            if stop.is_set():
                raise Offline("stopping")
            b = f.read(min(64 << 10, left))
            if not b:
                break
            left -= len(b)
            sent += len(b)
            yield b
            rate = (BUSY_KBPS if busy() else KBPS) * 1024
            ahead = sent / rate - (time.monotonic() - t0)
            if ahead > 0:
                time.sleep(min(ahead, 2.0))


class Syncer:
    def __init__(self, account):
        self.account = account
        self._lock = threading.Lock()
        self._thread = None
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._next = 0.0
        self._passing = False
        self._kick_at = None
        self.last = {}                  # the last pass: {at, trees, uploaded, bytes, error}

    def start(self, delay=FIRST_DELAY_S):
        """Start the background thread (once), its first pass after ``delay`` s."""
        if not enabled():
            return
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            self._next = time.time() + delay
            self._thread = threading.Thread(target=self._loop, name="account-sync", daemon=True)
            self._thread.start()

    def kick(self, delay=KICK_DELAY_S):
        """Something was saved: a pass soon (never sooner than ``delay`` s from now)."""
        if not enabled() or not self.account.logged_in():
            return
        self.start(delay)
        with self._lock:
            at = time.time() + delay
            self._kick_at = at if self._passing else None
            self._next = min(self._next, at) if self._next > time.time() else at
        self._wake.set()

    def stop(self):
        self._stop.set()
        self._wake.set()

    def _loop(self):
        _low_priority()
        backoff = 120.0
        while not self._stop.is_set():
            wait = self._next - time.time()
            if wait > 0:
                self._wake.wait(min(wait, 60))
                self._wake.clear()
                continue
            if not self.account.logged_in():
                return
            try:
                self._passing = True
                self.sync_once()
                backoff = 120.0
                with self._lock:        # a save during the pass: another one soon
                    self._next = min(time.time() + SYNC_EVERY_S, self._kick_at or float("inf"))
                    self._kick_at = None
            except SignedOut:
                self.account.invalidate()
                return
            except Exception as e:      # noqa: BLE001 — offline / server trouble: later, less often
                self.last = {"at": time.time(), "error": str(e)[:200]}
                self._next = time.time() + backoff
                backoff = min(backoff * 2, BACKOFF_MAX_S)
            finally:
                self._passing = False

    def sync_once(self):
        """One pass over every tree → {trees, uploaded, bytes}. Raises Offline / SignedOut."""
        token = self.account.token()
        if not token:
            raise SignedOut()
        cache, unit = HashCache(), unit_id()
        stats = {"at": time.time(), "trees": 0, "uploaded": 0, "bytes": 0, "skipped": 0, "error": None}
        trees = [lambda: unit_tree(cache)]
        seen = set()
        for c in _registry_cards():
            if _name_ok(str(c["name"])) and c["name"] not in seen:
                seen.add(c["name"])
                trees.append(lambda c=c: project_tree(c, cache))
        try:
            for make in trees:
                if self._stop.is_set():
                    break
                t = make()
                if t is None:
                    continue
                meta, files, skipped = t
                self._send(token, unit, meta, files, skipped, stats)
                stats["trees"] += 1
                stats["skipped"] += len(skipped)
        finally:
            cache.save()
        self.last = stats
        return stats

    def _send(self, token, unit, meta, files, skipped, stats):
        body = {"unit": unit, "tree": meta, "skipped": skipped,
                "files": [{k: v for k, v in f.items() if k != "_abs"} for f in files]}
        code, j = _post("/s/device/api/sync/manifest", json.dumps(body).encode(), token, timeout=60)
        if code != 200 or not isinstance(j, dict):
            raise Offline(f"manifest: server answered {code}")
        need, done = set(j.get("need") or []), set()
        for f in files:
            h = f["h"]
            if h not in need or h in done:
                continue
            done.add(h)
            offset = (f["tail_of"] - f["s"]) if f.get("tail_of") else 0
            try:
                gen = _throttled(f["_abs"], offset, f["s"], self._stop)
                code, j = _post(f"/s/device/api/sync/blob/{h}", gen, token,
                                timeout=max(60, f["s"] / max(BUSY_KBPS * 1024, 1) * 2),
                                content_type="application/octet-stream", length=f["s"])
            except FileNotFoundError:
                continue                # gone since the scan: the next pass has it right
            if code == 200:
                stats["uploaded"] += 1
                stats["bytes"] += f["s"]
            elif code == 507:
                raise Offline("the server's storage for this account is full")
            # 400 (changed since the scan) / 409: the next pass sends it again


_syncer = None


def syncer():
    global _syncer
    if _syncer is None:
        from gui.account.client import account
        _syncer = Syncer(account)
    return _syncer


def kick():
    """Call after a project was saved: a sync pass soon (no-op when offline)."""
    try:
        syncer().kick()
    except Exception:                   # noqa: BLE001 — never break the caller
        pass
