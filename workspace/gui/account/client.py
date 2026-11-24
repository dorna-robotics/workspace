"""The robot's Dorna account: device-code login, the token, the cached status.

Offline is the default. With no token this unit only runs existing
projects; logging in (an OAuth-style device-code flow against the account
server, ``DORNA_ACCOUNT_SERVER``) turns on the AI chat and Developer mode.

    start_login()  → POST /s/device/start        {user_code, verify_url, ...}
    poll_login()   → POST /s/device/token        pending | ok | expired | denied
    status()       → GET  /s/device/api/me       (cached ~30 s)
    components()   → GET  /s/device/api/components (cached; offline → last copy)
    logout()       → POST /s/device/api/logout   (and the token is forgotten)
    report_projects() → POST /s/device/api/projects (after login, then at most
                     every ~5 min on a status refresh, in its own thread; silent)

The token lives in ``~/.workspace/account.json`` (mode 600) and is never
handed to a page: only this module and the AI client read it. Everything
here is synchronous (urllib) — handlers call it off the IOLoop.
"""
import base64
import hashlib
import io
import json
import os
import re
import socket
import stat
import threading
import time
import urllib.error
import urllib.request

from gui.data_home import data_path

DEFAULT_SERVER = "https://builder.developmenttest.online"
STATUS_TTL_S = 30          # /api/me is asked at most this often
COMPONENTS_TTL_S = 300
HTTP_TIMEOUT_S = 8
PROJECTS_EVERY_S = 300     # the project list goes to the server at most this often
PROJECTS_SAME_S = 3600     # ... and an unchanged one only this often
THUMB_MAX = 60 * 1024      # the server's cap on a project picture


def server_url() -> str:
    return (os.environ.get("DORNA_ACCOUNT_SERVER") or DEFAULT_SERVER).rstrip("/")


class AccountError(Exception):
    pass


def _http(method, path, body=None, token=None, timeout=HTTP_TIMEOUT_S):
    """(status, json-or-None). Raises OSError when the server can't be reached."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(server_url() + path, data=data, method=method)
    req.add_header("Accept", "application/json")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw, code = r.read(), r.status
    except urllib.error.HTTPError as e:
        raw, code = e.read(), e.code
    try:
        return code, json.loads(raw.decode() or "null")
    except ValueError:
        return code, None


class Account:
    def __init__(self):
        self._lock = threading.Lock()
        self._pending = None           # the login in progress: device_code, user_code, ...
        self._status = None            # last /api/me answer, and when
        self._status_at = 0.0
        self._components = None
        self._components_at = 0.0
        self._reported_at = 0.0        # the project list: when last sent, its hash, a run in flight
        self._reported_hash = None
        self._reported_sent = 0.0
        self._reporting = False
        self._thumbs = {}              # thumb path → (mtime, size, base64 or None)

    # ── the stored token ─────────────────────────────────────────────
    @property
    def path(self):
        return data_path("account.json")

    def _load(self):
        try:
            with open(self.path) as f:
                d = json.load(f)
            return d if d.get("token") else None
        except (OSError, ValueError):
            return None

    def _save(self, d):
        tmp = self.path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(d, f)
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)
        try:                            # a sudo-run server: the file stays the user's
            pw = __import__("gui.data_home", fromlist=["_invoker"])._invoker()[1]
            if pw and os.geteuid() == 0:
                os.chown(self.path, pw.pw_uid, pw.pw_gid)
        except Exception:
            pass

    def _forget(self):
        try:
            os.remove(self.path)
        except OSError:
            pass
        self._status, self._status_at = None, 0.0
        self._components, self._components_at = None, 0.0
        self._reported_at, self._reported_hash, self._reported_sent = 0.0, None, 0.0
        try:
            os.remove(data_path("account_components.json"))
        except OSError:
            pass

    def token(self):
        d = self._load()
        return d["token"] if d else None

    def logged_in(self) -> bool:
        """A token is stored (fast: no network). The server may still have
        revoked it — status() finds that out and logs the unit out."""
        return self._load() is not None

    def invalidate(self):
        """The server refused the token (revoked): back to offline."""
        with self._lock:
            self._forget()

    # ── login ────────────────────────────────────────────────────────
    def start_login(self, device_name=None):
        name = device_name or socket.gethostname()
        try:
            code, j = _http("POST", "/s/device/start", {"device_name": name})
        except OSError as e:
            raise AccountError(f"Can't reach the Dorna account server ({server_url()}): {e}")
        if code != 200 or not isinstance(j, dict) or not j.get("device_code"):
            raise AccountError((j or {}).get("error") if isinstance(j, dict) else f"login start failed ({code})")
        with self._lock:
            self._pending = {
                "device_code": j["device_code"], "user_code": j.get("user_code", ""),
                "verify_url": j.get("verify_url", ""),
                "expires_at": time.time() + float(j.get("expires_in") or 600),
                "interval": float(j.get("interval") or 3), "polled": 0.0,
            }
            p = self._pending
        return {"user_code": p["user_code"], "verify_url": p["verify_url"],
                "expires_in": int(p["expires_at"] - time.time()), "interval": p["interval"]}

    def poll_login(self):
        """→ {status: pending|ok|expired|denied|none, ...}"""
        with self._lock:
            p = self._pending
        if not p:
            return {"status": "ok" if self.logged_in() else "none"}
        if time.time() > p["expires_at"]:
            with self._lock:
                self._pending = None
            return {"status": "expired"}
        # the server's interval holds even when the page polls faster
        if time.time() - p["polled"] < p["interval"] * 0.9:
            return {"status": "pending"}
        p["polled"] = time.time()
        try:
            code, j = _http("POST", "/s/device/token", {"device_code": p["device_code"]})
        except OSError:
            return {"status": "pending", "reachable": False}
        j = j if isinstance(j, dict) else {}
        if code == 200 and j.get("token"):
            prev = self._load()
            if prev and prev.get("token") and prev["token"] != j["token"]:
                try:            # never leave a replaced token valid on the server
                    _http("POST", "/s/device/api/logout", {}, token=prev["token"], timeout=4)
                except OSError:
                    pass
            with self._lock:
                self._forget()
                self._save({"token": j["token"], "email": j.get("email"), "account": j.get("account"),
                            "is_admin": bool(j.get("is_admin")), "server": server_url(), "at": time.time()})
                self._pending = None
            self.report_projects_soon()      # outside the lock: it takes it
            return {"status": "ok", "email": j.get("email")}
        if code == 428:
            return {"status": "pending"}
        if code == 410:
            with self._lock:
                self._pending = None
            return {"status": j.get("status") if j.get("status") in ("expired", "denied") else "expired"}
        return {"status": "pending", "error": j.get("error") or f"server answered {code}"}

    def logout(self):
        d = self._load()
        if d:
            try:
                _http("POST", "/s/device/api/logout", {}, token=d["token"], timeout=4)
            except OSError:
                pass                    # offline: forgotten here; the server's copy can be revoked by an admin
        with self._lock:
            self._forget()
            self._pending = None
        return {"ok": True}

    # ── status ───────────────────────────────────────────────────────
    def status(self, force=False):
        d = self._load()
        base = {"server": server_url()}
        if not d:
            return {**base, "logged_in": False}
        with self._lock:
            if not force and self._status and time.time() - self._status_at < STATUS_TTL_S:
                return dict(self._status)
        try:
            code, j = _http("GET", "/s/device/api/me", token=d["token"], timeout=5)
        except OSError:
            # a token but no server: still logged in (the last known identity)
            st = {**base, "logged_in": True, "reachable": False, "email": d.get("email"),
                  "account": d.get("account"), "is_admin": bool(d.get("is_admin"))}
            if self._status and self._status.get("usage"):
                st["usage"] = self._status["usage"]
            with self._lock:
                self._status, self._status_at = st, time.time() - STATUS_TTL_S + 5   # retry soon
            return dict(st)
        if code == 401:
            self.invalidate()
            return {**base, "logged_in": False, "revoked": True}
        if code != 200 or not isinstance(j, dict):
            st = {**base, "logged_in": True, "reachable": False, "email": d.get("email"),
                  "account": d.get("account"), "is_admin": bool(d.get("is_admin"))}
            if self._status and self._status.get("usage"):
                st["usage"] = self._status["usage"]
            with self._lock:            # a server error: ask again in ~5 s, not on every call
                self._status, self._status_at = st, time.time() - STATUS_TTL_S + 5
            return dict(st)
        usage = j.get("usage") or {}
        st = {**base, "logged_in": True, "reachable": True, "email": j.get("email") or d.get("email"),
              "account": j.get("account") or d.get("account"), "is_admin": bool(j.get("is_admin")),
              "usage": {"unlimited": bool(usage.get("unlimited")),
                        "percent": max(0.0, min(100.0, float(usage.get("percent") or 0)))}}
        if st["is_admin"]:              # admins (Dorna staff) may see the credits; customers never do
            for k in ("credits_left", "credits_granted"):
                if k in usage:
                    st["usage"][k] = usage[k]
        with self._lock:
            self._status, self._status_at = st, time.time()
        self.report_projects_soon()
        return dict(st)

    # ── the account's components ─────────────────────────────────────
    def components(self, force=False):
        """{all, types, library} — what this account may place. Offline → the
        last copy; logged out → nothing."""
        d = self._load()
        if not d:
            return {"all": False, "types": [], "library": []}
        with self._lock:
            if not force and self._components and time.time() - self._components_at < COMPONENTS_TTL_S:
                return self._components
        cache = data_path("account_components.json")
        try:
            code, j = _http("GET", "/s/device/api/components", token=d["token"], timeout=6)
        except OSError:
            code, j = None, None
        if code == 401:
            self.invalidate()
            return {"all": False, "types": [], "library": []}
        if code == 200 and isinstance(j, dict):
            comp = {"all": bool(j.get("all")), "types": list(j.get("types") or []),
                    "library": list(j.get("library") or [])}
            try:
                with open(cache, "w") as f:
                    json.dump(comp, f)
            except OSError:
                pass
        else:
            try:
                with open(cache) as f:
                    comp = json.load(f)
                comp["offline"] = True
            except (OSError, ValueError):
                comp = {"all": False, "types": [], "library": [], "offline": True}
        with self._lock:
            self._components, self._components_at = comp, time.time()
        return comp

    def allowed_types(self):
        """None = every type (an admin's account); else the set of component
        types this account was given. Logged out → an empty set."""
        if not self.logged_in():
            return set()
        c = self.components()
        if c.get("all"):
            return None
        return set(c.get("types") or []) | {p.get("type") for p in c.get("library") or [] if p.get("type")}

    # ── the project list (the admins' view of this account) ──────────
    def report_projects_soon(self):
        """Send the project list in a background thread when it is due. Never blocks."""
        with self._lock:
            if self._reporting or time.time() - self._reported_at < PROJECTS_EVERY_S:
                return
            self._reporting = True
        threading.Thread(target=self._report, name="account-projects", daemon=True).start()

    def _report(self):
        try:
            self.report_projects()
        except Exception:               # noqa: BLE001 — failures are silent; the next refresh tries again
            pass
        finally:
            with self._lock:
                self._reporting = False

    def report_projects(self):
        """POST this unit's projects to the account server: [{name, title, core,
        updated, git_remote, thumb}]. An unchanged list goes at most hourly."""
        d = self._load()
        if not d:
            return
        with self._lock:
            self._reported_at = time.time()
        projects = local_projects(self._thumbs)
        h = hashlib.sha256(json.dumps(projects, sort_keys=True).encode()).hexdigest()
        if h == self._reported_hash and time.time() - self._reported_sent < PROJECTS_SAME_S:
            return
        code, _ = _http("POST", "/s/device/api/projects", {"projects": projects}, token=d["token"], timeout=20)
        if code == 200:
            self._reported_hash, self._reported_sent = h, time.time()


def _registry_path():
    try:
        from gui.orchestrator.orchestrator import REG_PATH
        return REG_PATH
    except Exception:                   # noqa: BLE001 — standalone tests
        return os.environ.get("ORCH_REG_PATH", data_path("registry.json"))


def _core_of(folder):
    try:
        with open(os.path.join(folder, "launch.yaml"), errors="ignore") as f:
            text = "\n".join(line.split("#", 1)[0] for line in f.read(65536).splitlines())   # not the comments
    except OSError:
        return ""
    m = re.search(r"\bcore_[0-9a-z]+", text)
    return m.group(0) if m else ""


def _git_remote(folder):
    """origin's URL from .git/config, without any user:token@ in it."""
    try:
        with open(os.path.join(folder, ".git", "config"), errors="ignore") as f:
            text = f.read(65536)
    except OSError:
        return ""
    m = re.search(r'\[remote "origin"\][^\[]*?\burl\s*=\s*(\S+)', text)
    return re.sub(r"//[^/@]*@", "//", m.group(1)) if m else ""


def _thumb(folder, cache):
    """The project's .workspace/thumb.png as base64 ≤ THUMB_MAX: as it is when
    small enough, else a smaller JPEG (PIL), else none. Cached by mtime."""
    path = os.path.join(folder, ".workspace", "thumb.png")
    try:
        if os.path.islink(os.path.dirname(path)):
            return None
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None
    with os.fdopen(fd, "rb") as f:
        st = os.fstat(f.fileno())
        if not stat.S_ISREG(st.st_mode) or st.st_size > 8 * 1024 * 1024:
            return None
        key = (st.st_mtime, st.st_size)
        if path in cache and cache[path][0] == key:
            return cache[path][1]
        raw = f.read()
    out = None
    if len(raw) <= THUMB_MAX:
        out = raw
    else:
        try:
            from PIL import Image
            im = Image.open(io.BytesIO(raw)).convert("RGB")
            for width, quality in ((480, 75), (360, 70), (240, 60)):
                small = im.copy()
                small.thumbnail((width, width))
                buf = io.BytesIO()
                small.save(buf, "JPEG", quality=quality)
                if buf.tell() <= THUMB_MAX:
                    out = buf.getvalue()
                    break
        except Exception:               # noqa: BLE001 — no PIL, or not an image: no picture
            out = None
    b64 = base64.b64encode(out).decode() if out else None
    cache[path] = (key, b64)
    return b64


def local_projects(thumb_cache=None):
    """This unit's projects as the home page lists them (the orchestrator's registry)."""
    try:
        with open(_registry_path(), encoding="utf-8") as f:
            cards = json.load(f).get("workspaces") or []
    except (OSError, ValueError, AttributeError):
        return []
    cache = {} if thumb_cache is None else thumb_cache
    out = []
    for c in cards if isinstance(cards, list) else []:
        if not isinstance(c, dict) or not c.get("name"):
            continue
        row = {"name": str(c["name"]), "title": str(c.get("label") or "")}
        folder = os.path.dirname(c.get("path_to_file") or "")
        if folder and not c.get("node_url") and os.path.isdir(folder):
            times = [os.path.getmtime(p) for p in (c["path_to_file"], os.path.join(folder, "launch.yaml"))
                     if os.path.isfile(p)]
            row.update(core=_core_of(folder), updated=max(times) if times else None,
                       git_remote=_git_remote(folder))
            t = _thumb(folder, cache)
            if t:
                row["thumb"] = t
        out.append(row)
    return out


account = Account()
