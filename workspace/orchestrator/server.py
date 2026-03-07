# orchestrator_server.py
import shlex
import os
import json
import subprocess
import time
from threading import Thread
from typing import Dict, Optional, List

import requests
import tornado.web
import tornado.ioloop

# -------------------- Workspace Orchestrator --------------------

ORCH_TOKEN = os.environ.get("ORCH_TOKEN", "").strip()  # if empty => auth disabled
REG_PATH = os.environ.get("ORCH_REG_PATH", "/tmp/orchestrator_registry.json")
MAX_LOG_BYTES = int(os.environ.get("ORCH_MAX_LOG_BYTES", str(2 * 1024 * 1024)))  # 2MB


def _now_str():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _truncate_log_if_needed(path: str, max_bytes: int):
    try:
        if max_bytes <= 0:
            return
        if not os.path.isfile(path):
            return
        sz = os.path.getsize(path)
        if sz <= max_bytes:
            return
        keep = max_bytes // 2
        with open(path, "rb") as f:
            f.seek(max(0, sz - keep))
            tail = f.read()
        with open(path, "wb") as f:
            f.write(b"--- LOG TRUNCATED " + _now_str().encode("utf-8") + b" ---\n")
            f.write(tail)
    except Exception:
        # never crash orchestrator due to log housekeeping
        pass


class WorkspaceInfo:
    """Holds workspace process info and metadata."""
    def __init__(self, name: str, path_to_file: str, port: int, node_url: Optional[str] = None, label: str = "", args: str = ""):
        self.name = name
        self.path_to_file = path_to_file
        self.port = int(port)
        self.label = label or ""
        self.args = (args or "").strip()

        # If set => this workspace is remote, and commands/status/logs are proxied to that orchestrator
        self.node_url: Optional[str] = node_url.strip().rstrip("/") if node_url else None

        # Local process handle (only for local workspaces)
        self.process: Optional[subprocess.Popen] = None

        # Local log file (only for local workspaces)
        self.log_path: str = f"/tmp/{name}.log"

        # Orchestrator-level timing / error
        self.started_at: Optional[float] = None  # unix seconds; starts at LAUNCH for local
        self.last_error: Optional[str] = None

    def is_remote(self) -> bool:
        return bool(self.node_url)

    def to_dict(self) -> Dict:
        return {
            "name": self.name,
            "label": self.label,
            "path_to_file": self.path_to_file,
            "port": self.port,
            "node_url": self.node_url or "",
        }


class Orchestrator:
    """Manages multiple workspaces as local OS processes OR remote orchestrator proxies."""
    def __init__(self):
        self.workspaces: Dict[str, WorkspaceInfo] = {}

    # ---------------- Persistence ----------------

    def save_registry(self):
        data = {
            "version": 1,
            "saved_at": time.time(),
            "workspaces": [ws.to_dict() for ws in self.workspaces.values()],
        }
        tmp = REG_PATH + ".tmp"
        os.makedirs(os.path.dirname(REG_PATH) or ".", exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, REG_PATH)

    def load_registry(self):
        if not os.path.isfile(REG_PATH):
            return
        try:
            with open(REG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            arr = data.get("workspaces", [])
            if not isinstance(arr, list):
                return
            for item in arr:
                try:
                    self.add_workspace(
                        name=item["name"],
                        path_to_file=item.get("path_to_file", ""),
                        port=int(item.get("port", 0)),
                        node_url=item.get("node_url") or None,
                        label=item.get("label", "") or "",
                        sync_remote=False,   # IMPORTANT: don't re-add remotely on startup
                        persist=False,       # we'll persist once after bulk load
                    )
                except Exception:
                    # keep loading others
                    pass
            self.save_registry()
        except Exception:
            pass

    # ---------------- Workspace management ----------------

    def add_workspace(
        self,
        name: str,
        path_to_file: str,
        port: int,
        node_url: Optional[str] = None,
        label: str = "",
        args: str = "",
        sync_remote: bool = True,
        persist: bool = True,
    ):
        if name in self.workspaces:
            raise ValueError(f"Workspace {name} already exists.")

        ws = WorkspaceInfo(name=name, path_to_file=path_to_file, port=int(port), node_url=node_url, label=label, args=args)

        # Local workspace must have a valid file path
        if not ws.is_remote():
            if not os.path.isfile(path_to_file):
                raise FileNotFoundError(f"{path_to_file} does not exist.")

        # For remote, optionally add the workspace on the remote orchestrator too
        if ws.is_remote() and sync_remote:
            payload = {"name": name, "path_to_file": path_to_file, "port": int(port), "args": (args or "").strip()}
            if label:
                payload["label"] = label
            try:
                hdrs = {}
                if ORCH_TOKEN:
                    hdrs["X-Orch-Token"] = ORCH_TOKEN
                r = requests.post(f"{ws.node_url}/add_workspace", json=payload, timeout=6, headers=hdrs)
                r.raise_for_status()
            except Exception as e:
                raise RuntimeError(f"Failed to add workspace on remote node {ws.node_url}: {e}")

        self.workspaces[name] = ws
        if persist:
            self.save_registry()

    def remove_workspace(self, name: str):
        if name not in self.workspaces:
            raise ValueError(f"Unknown workspace: {name}")
        ws = self.workspaces[name]
        # Safety: refuse removing launched/running workspace
        if not ws.is_remote() and self.is_launched(name):
            raise RuntimeError("Refuse to remove: workspace is launched/running. Kill it first.")
        # Remote: you may still want to keep it registered remotely; we won't delete remotely here.
        del self.workspaces[name]
        self.save_registry()
        return {"status": "ok", "removed": True, "name": name}

    def list_workspaces(self) -> List[Dict]:
        return [ws.to_dict() for ws in self.workspaces.values()]

    def is_launched(self, name: str) -> bool:
        ws = self.workspaces[name]
        if ws.is_remote():
            return False
        return ws.process is not None and ws.process.poll() is None

    # ---------------- Local launch helpers ----------------

    def start_workspace_process(self, name: str):
        """
        Launch the OS process (LAUNCH state).
        Does NOT start runtime workflow (no motion).
        Always runs with sudo.
        """
        ws = self.workspaces[name]
        if ws.is_remote():
            raise RuntimeError("start_workspace_process called on remote workspace (bug).")

        if self.is_launched(name):
            return

        cmd = ["sudo", "python3", ws.path_to_file, "--port", str(ws.port)]
        if ws.args:
            cmd += shlex.split(ws.args)

        env = os.environ.copy()  # keep env normal; no PORT needed anymore
        
        # log cap + append marker
        _truncate_log_if_needed(ws.log_path, MAX_LOG_BYTES)

        log_f = open(ws.log_path, "a", buffering=1)
        log_f.write(f"\n--- LAUNCH {_now_str()} cmd={cmd} port={ws.port} ---\n")

        ws.process = subprocess.Popen(
            cmd,
            cwd=os.path.dirname(ws.path_to_file),
            env=env,
            stdout=log_f,
            stderr=subprocess.STDOUT,
            text=True,
        )

        ws.started_at = time.time()
        ws.last_error = None

    def wait_until_ready(self, name: str, timeout: float = 8.0) -> bool:
        """
        Wait until workspace responds on /healthz (LOCAL only).
        """
        ws = self.workspaces[name]
        if ws.is_remote():
            return True

        url = f"http://127.0.0.1:{ws.port}/healthz"
        t0 = time.time()
        while time.time() - t0 < timeout:
            if ws.process is not None and ws.process.poll() is not None:
                return False
            try:
                r = requests.get(url, timeout=0.5)
                if r.status_code == 200:
                    return True
            except Exception:
                pass
            time.sleep(0.1)
        return False

    # ---------------- Remote orchestrator proxy helpers ----------------

    def _orch_url(self, ws: WorkspaceInfo, path: str) -> str:
        assert ws.node_url
        return f"{ws.node_url}{path}"

    def _auth_headers(self) -> Dict[str, str]:
        return {"X-Orch-Token": ORCH_TOKEN} if ORCH_TOKEN else {}

    def _proxy_cmd_to_node(self, ws: WorkspaceInfo, cmd: str):
        url = self._orch_url(ws, f"/workspace/{requests.utils.quote(ws.name)}/cmd")
        r = requests.post(url, json={"cmd": cmd}, timeout=10, headers=self._auth_headers())
        r.raise_for_status()
        return r.json()

    def _proxy_status_from_node(self, ws: WorkspaceInfo):
        url = self._orch_url(ws, f"/workspace/{requests.utils.quote(ws.name)}/status")
        r = requests.get(url, timeout=8, headers=self._auth_headers())
        r.raise_for_status()
        return r.json()

    def _proxy_logs_from_node(self, ws: WorkspaceInfo, tail: int = 200):
        url = self._orch_url(ws, f"/workspace/{requests.utils.quote(ws.name)}/logs?tail={int(tail)}")
        r = requests.get(url, timeout=8, headers=self._auth_headers())
        r.raise_for_status()
        return r.json()

    # ---------------- Public Commands ----------------

    def launch_workspace(self, name: str):
        ws = self.workspaces[name]

        if ws.is_remote():
            out = self._proxy_cmd_to_node(ws, "launch")
            out["_orch"] = {"node_url": ws.node_url, "mode": "remote"}
            return out

        if self.is_launched(name):
            ready = self.wait_until_ready(name, timeout=1.0)
            return {
                "status": "ok",
                "launched": True,
                "ready": bool(ready),
                "port": ws.port,
                "log": ws.log_path,
                "note": "already running",
                "started_at": ws.started_at,
                "uptime_s": (time.time() - ws.started_at) if ws.started_at else None,
            }

        self.start_workspace_process(name)

        ready = self.wait_until_ready(name, timeout=8.0)
        if not ready:
            ws.last_error = f"Workspace {name} launched but not ready. Check logs: {ws.log_path}"
            raise RuntimeError(ws.last_error)

        return {
            "status": "ok",
            "launched": True,
            "ready": True,
            "port": ws.port,
            "log": ws.log_path,
            "started_at": ws.started_at,
            "uptime_s": (time.time() - ws.started_at) if ws.started_at else None,
        }

    def stop_workspace(self, name: str):
        ws = self.workspaces[name]

        if ws.is_remote():
            return self._proxy_cmd_to_node(ws, "kill")

        if ws.process is None or ws.process.poll() is not None:
            ws.process = None
            ws.started_at = None
            return

        ws.process.terminate()
        try:
            ws.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            ws.process.kill()
            ws.process.wait()
        ws.process = None
        ws.started_at = None

    def relaunch_workspace(self, name: str):
        ws = self.workspaces[name]

        if ws.is_remote():
            out = self._proxy_cmd_to_node(ws, "restart")  # keep cmd name for compatibility
            out["_orch"] = {"node_url": ws.node_url, "mode": "remote"}
            return out

        self.stop_workspace(name)
        return self.launch_workspace(name)

    # ---------------- Runtime commands ----------------

    def _send_runtime_cmd_local(self, ws: WorkspaceInfo, cmd: str):
        url = f"http://127.0.0.1:{ws.port}/cmd"
        r = requests.post(url, json={"cmd": cmd}, timeout=4)
        r.raise_for_status()
        return r.json()

    def start_runtime(self, name: str):
        ws = self.workspaces[name]
        if ws.is_remote():
            return self._proxy_cmd_to_node(ws, "start")

        if not self.is_launched(name):
            raise RuntimeError(f"Workspace {name} is not launched. Send cmd=launch first.")
        if not self.wait_until_ready(name, timeout=2.0):
            raise RuntimeError(f"Workspace {name} is launched but not responding. Check logs: {ws.log_path}")
        return self._send_runtime_cmd_local(ws, "start")

    def pause_runtime(self, name: str):
        ws = self.workspaces[name]
        if ws.is_remote():
            return self._proxy_cmd_to_node(ws, "pause")
        if not self.is_launched(name):
            raise RuntimeError(f"Workspace {name} is not launched.")
        return self._send_runtime_cmd_local(ws, "pause")

    def resume_runtime(self, name: str):
        ws = self.workspaces[name]
        if ws.is_remote():
            return self._proxy_cmd_to_node(ws, "resume")
        if not self.is_launched(name):
            raise RuntimeError(f"Workspace {name} is not launched.")
        return self._send_runtime_cmd_local(ws, "resume")

    def get_status(self, name: str):
        ws = self.workspaces[name]

        if ws.is_remote():
            try:
                out = self._proxy_status_from_node(ws)
                out["_orch"] = {"node_url": ws.node_url, "mode": "remote"}
                return out
            except Exception as e:
                ws.last_error = f"Remote status failed: {e}"
                return {"state": "REMOTE_OFFLINE", "last_error": ws.last_error, "_orch": {"node_url": ws.node_url, "mode": "remote"}}

        uptime_s = (time.time() - ws.started_at) if ws.started_at else None

        if not self.is_launched(name):
            return {
                "state": "NOT_LAUNCHED",
                "last_error": ws.last_error,
                "port": ws.port,
                "log": ws.log_path,
                "started_at": ws.started_at,
                "uptime_s": uptime_s,
                "_orch": {"launched": False, "port": ws.port, "log": ws.log_path, "mode": "local"},
            }

        url = f"http://127.0.0.1:{ws.port}/status"
        try:
            r = requests.get(url, timeout=4)
            r.raise_for_status()
            out = r.json()
            out["started_at"] = ws.started_at
            out["uptime_s"] = uptime_s
            out["_orch"] = {"launched": True, "port": ws.port, "log": ws.log_path, "mode": "local"}
            out["last_error"] = out.get("last_error") or ws.last_error
            return out
        except Exception as e:
            ws.last_error = f"Failed to get status: {e}"
            return {
                "state": "LAUNCHED_NOT_READY",
                "last_error": ws.last_error,
                "port": ws.port,
                "log": ws.log_path,
                "started_at": ws.started_at,
                "uptime_s": uptime_s,
                "_orch": {"launched": True, "port": ws.port, "log": ws.log_path, "mode": "local"},
            }

    def get_logs(self, name: str, tail: int = 200) -> Dict:
        ws = self.workspaces[name]

        if ws.is_remote():
            return self._proxy_logs_from_node(ws, tail=tail)

        if not os.path.isfile(ws.log_path):
            return {"text": ""}

        with open(ws.log_path, "r", errors="replace") as f:
            lines = f.readlines()[-int(tail):]
        return {"text": "".join(lines)}


# -------------------- Auth base --------------------

class AuthedHandler(tornado.web.RequestHandler):
    def require_auth(self) -> bool:
        if not ORCH_TOKEN:
            return True  # auth disabled
        tok = self.request.headers.get("X-Orch-Token", "")
        return tok == ORCH_TOKEN

    def ensure_auth(self) -> bool:
        if self.require_auth():
            return True
        self.set_status(401)
        self.write({"error": "Unauthorized"})
        return False


# -------------------- Tornado Handlers --------------------

class WorkspacesListHandler(tornado.web.RequestHandler):
    def initialize(self, orch: Orchestrator):
        self.orch = orch

    async def get(self):
        self.write({"workspaces": self.orch.list_workspaces()})


class AddWorkspaceHandler(AuthedHandler):
    def initialize(self, orch: Orchestrator):
        self.orch = orch

    async def post(self):
        if not self.ensure_auth():
            return
        try:
            data = json.loads(self.request.body.decode())
            args = data.get("args", "")
            name = data["name"]
            path_to_file = data["path_to_file"]
            port = int(data["port"])
            node_url = data.get("node_url")
            label = data.get("label", "") or ""

            self.orch.add_workspace(name, path_to_file, port, node_url=node_url, label=label, args=args, sync_remote=True, persist=True)
            self.write({"status": "ok"})
        except Exception as e:
            self.set_status(400)
            self.write({"error": str(e)})


class RemoveWorkspaceHandler(AuthedHandler):
    def initialize(self, orch: Orchestrator):
        self.orch = orch

    async def post(self):
        if not self.ensure_auth():
            return
        try:
            data = json.loads(self.request.body.decode())
            name = data["name"]
            self.write(self.orch.remove_workspace(name))
        except Exception as e:
            self.set_status(400)
            self.write({"error": str(e)})


class WorkspaceCmdHandler(AuthedHandler):
    def initialize(self, orch: Orchestrator):
        self.orch = orch

    async def post(self, name):
        if not self.ensure_auth():
            return
        try:
            data = json.loads(self.request.body.decode())
            cmd = data["cmd"].lower()

            if name not in self.orch.workspaces:
                raise ValueError(f"Unknown workspace: {name}")

            if cmd == "launch":
                self.write(self.orch.launch_workspace(name)); return
            if cmd == "start":
                self.write(self.orch.start_runtime(name)); return
            if cmd == "pause":
                self.write(self.orch.pause_runtime(name)); return
            if cmd == "resume":
                self.write(self.orch.resume_runtime(name)); return
            if cmd == "kill":
                out = self.orch.stop_workspace(name) or {"status": "ok", "killed": True}
                self.write(out); return
            if cmd in ("restart", "relaunch"):
                self.write(self.orch.relaunch_workspace(name)); return

            raise ValueError("Unknown cmd")
        except Exception as e:
            self.set_status(400)
            self.write({"error": str(e)})


class WorkspaceStatusHandler(tornado.web.RequestHandler):
    def initialize(self, orch: Orchestrator):
        self.orch = orch

    async def get(self, name):
        try:
            if name not in self.orch.workspaces:
                raise ValueError(f"Unknown workspace: {name}")
            self.write(self.orch.get_status(name))
        except Exception as e:
            self.set_status(400)
            self.write({"error": str(e)})


class WorkspaceLogsHandler(tornado.web.RequestHandler):
    def initialize(self, orch: Orchestrator):
        self.orch = orch

    async def get(self, name):
        try:
            if name not in self.orch.workspaces:
                raise ValueError(f"Unknown workspace: {name}")
            tail = int(self.get_argument("tail", 200))
            self.write(self.orch.get_logs(name, tail=tail))
        except Exception as e:
            self.set_status(400)
            self.write({"error": str(e)})


# -------------------- Tornado HTTP Server --------------------

class OrchestratorHTTPServer:
    def __init__(self, host="0.0.0.0", port=9000):
        self.orch = Orchestrator()
        self.orch.load_registry()

        self.host = host
        self.port = port

        base_dir = os.path.dirname(os.path.abspath(__file__))
        web_dir = os.path.join(base_dir, "web")

        self.app = tornado.web.Application([
            # ---- GUI (serve ./web) ----
            (r"/", tornado.web.RedirectHandler, {"url": "/web/admin/index.html"}),
            (r"/web/(.*)", tornado.web.StaticFileHandler, {"path": web_dir}),

            # ---- API ----
            (r"/workspaces", WorkspacesListHandler, dict(orch=self.orch)),
            (r"/add_workspace", AddWorkspaceHandler, dict(orch=self.orch)),
            (r"/remove_workspace", RemoveWorkspaceHandler, dict(orch=self.orch)),
            (r"/workspace/([^/]+)/cmd", WorkspaceCmdHandler, dict(orch=self.orch)),
            (r"/workspace/([^/]+)/status", WorkspaceStatusHandler, dict(orch=self.orch)),
            (r"/workspace/([^/]+)/logs", WorkspaceLogsHandler, dict(orch=self.orch)),
        ])

    def run(self):
        self.app.listen(self.port, address=self.host)
        print(f"Orchestrator server running on {self.host}:{self.port}")
        print(f"GUI: http://{self.host}:{self.port}/web/orchestrator.html")
        if ORCH_TOKEN:
            print("Auth: ENABLED (X-Orch-Token required for add/cmd/remove)")
        else:
            print("Auth: disabled (set ORCH_TOKEN to enable)")
        print("Registry:", REG_PATH)
        tornado.ioloop.IOLoop.current().start()

    def run_in_thread(self):
        t = Thread(target=self.run, daemon=True)
        t.start()
        return t


if __name__ == "__main__":
    server = OrchestratorHTTPServer(port=5000)
    server.run()