# orchestrator_server.py
import os
import json
import subprocess
import time
from threading import Thread
from typing import Dict, Optional

import requests
import tornado.web
import tornado.ioloop

# -------------------- Workspace Orchestrator --------------------


class WorkspaceInfo:
    """Holds workspace process info and metadata."""
    def __init__(self, name: str, path_to_file: str, port: int, node_url: Optional[str] = None):
        self.name = name
        self.path_to_file = path_to_file
        self.port = port

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


class Orchestrator:
    """Manages multiple workspaces as local OS processes OR remote orchestrator proxies."""
    def __init__(self):
        self.workspaces: Dict[str, WorkspaceInfo] = {}

    # ---------------- Workspace management ----------------

    def add_workspace(self, name: str, path_to_file: str, port: int, node_url: Optional[str] = None):
        if name in self.workspaces:
            raise ValueError(f"Workspace {name} already exists.")

        ws = WorkspaceInfo(name=name, path_to_file=path_to_file, port=port, node_url=node_url)

        # Local workspace must have a valid file path
        if not ws.is_remote():
            if not os.path.isfile(path_to_file):
                raise FileNotFoundError(f"{path_to_file} does not exist.")

        # For remote, we try to add the workspace on the remote orchestrator too
        # (so the remote node knows about it)
        if ws.is_remote():
            payload = {"name": name, "path_to_file": path_to_file, "port": int(port)}
            try:
                r = requests.post(f"{ws.node_url}/add_workspace", json=payload, timeout=4)
                r.raise_for_status()
            except Exception as e:
                raise RuntimeError(f"Failed to add workspace on remote node {ws.node_url}: {e}")

        self.workspaces[name] = ws

    def is_launched(self, name: str) -> bool:
        ws = self.workspaces[name]
        if ws.is_remote():
            # "launched" is a node concept; master will infer from /status
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

        cmd = ["sudo", "python3", ws.path_to_file]

        env = os.environ.copy()
        env["PORT"] = str(ws.port)  # workspace main.py should read this

        # Append logs so you can see past runs
        log_f = open(ws.log_path, "a", buffering=1)
        log_f.write(
            f"\n--- LAUNCH {time.strftime('%Y-%m-%d %H:%M:%S')} cmd={cmd} port={ws.port} ---\n"
        )

        ws.process = subprocess.Popen(
            cmd,
            cwd=os.path.dirname(ws.path_to_file),
            env=env,
            stdout=log_f,
            stderr=subprocess.STDOUT,
            text=True,
        )

        # Start timer at launch (what you asked for)
        ws.started_at = time.time()
        ws.last_error = None

    def wait_until_ready(self, name: str, timeout: float = 8.0) -> bool:
        """
        Wait until workspace runtime server responds on /status (LOCAL only).
        """
        ws = self.workspaces[name]
        if ws.is_remote():
            # master doesn't wait for remote runtime directly
            return True

        url = f"http://127.0.0.1:{ws.port}/status"
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

    def _proxy_cmd_to_node(self, ws: WorkspaceInfo, cmd: str):
        url = self._orch_url(ws, f"/workspace/{requests.utils.quote(ws.name)}/cmd")
        r = requests.post(url, json={"cmd": cmd}, timeout=6)
        r.raise_for_status()
        return r.json()

    def _proxy_status_from_node(self, ws: WorkspaceInfo):
        url = self._orch_url(ws, f"/workspace/{requests.utils.quote(ws.name)}/status")
        r = requests.get(url, timeout=6)
        r.raise_for_status()
        return r.json()

    def _proxy_logs_from_node(self, ws: WorkspaceInfo, tail: int = 200):
        url = self._orch_url(ws, f"/workspace/{requests.utils.quote(ws.name)}/logs?tail={int(tail)}")
        r = requests.get(url, timeout=6)
        r.raise_for_status()
        # expect {"text": "..."}
        return r.json()

    # ---------------- Public Commands ----------------

    def launch_workspace(self, name: str):
        ws = self.workspaces[name]

        # Remote: forward launch to that node orchestrator
        if ws.is_remote():
            out = self._proxy_cmd_to_node(ws, "launch")
            # attach master hint
            out["_orch"] = {"node_url": ws.node_url, "mode": "remote"}
            return out

        # Local: existing behavior
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

        # Remote: forward kill to node orchestrator
        if ws.is_remote():
            return self._proxy_cmd_to_node(ws, "kill")

        # Local: stop process
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

    def restart_workspace(self, name: str):
        ws = self.workspaces[name]

        # Remote: forward restart
        if ws.is_remote():
            out = self._proxy_cmd_to_node(ws, "restart")
            out["_orch"] = {"node_url": ws.node_url, "mode": "remote"}
            return out

        # Local: kill + launch
        self.stop_workspace(name)
        return self.launch_workspace(name)

    # ---------------- Runtime commands ----------------

    def _send_runtime_cmd_local(self, ws: WorkspaceInfo, cmd: str):
        url = f"http://127.0.0.1:{ws.port}/cmd"
        r = requests.post(url, json={"cmd": cmd}, timeout=3)
        r.raise_for_status()
        return r.json()

    def start_runtime(self, name: str):
        ws = self.workspaces[name]

        # Remote: orchestrator on node will handle prerequisites
        if ws.is_remote():
            return self._proxy_cmd_to_node(ws, "start")

        # Local:
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

        # Remote: pass-through node status
        if ws.is_remote():
            try:
                out = self._proxy_status_from_node(ws)
                out["_orch"] = {"node_url": ws.node_url, "mode": "remote"}
                return out
            except Exception as e:
                ws.last_error = f"Remote status failed: {e}"
                return {
                    "state": "REMOTE_OFFLINE",
                    "last_error": ws.last_error,
                    "_orch": {"node_url": ws.node_url, "mode": "remote"},
                }

        # Local: orchestrator-level status + runtime status if reachable
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
            r = requests.get(url, timeout=3)
            r.raise_for_status()
            out = r.json()

            out["started_at"] = ws.started_at
            out["uptime_s"] = uptime_s
            out["_orch"] = {"launched": True, "port": ws.port, "log": ws.log_path, "mode": "local"}

            # prefer runtime last_error, fallback to orchestrator last_error
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

        # Remote: pass-through node logs
        if ws.is_remote():
            out = self._proxy_logs_from_node(ws, tail=tail)
            # keep as {"text": "..."}
            return out

        # Local: read /tmp/<name>.log
        if not os.path.isfile(ws.log_path):
            return {"text": ""}

        with open(ws.log_path, "r", errors="replace") as f:
            lines = f.readlines()[-int(tail):]
        return {"text": "".join(lines)}


# -------------------- Tornado Handlers --------------------

class AddWorkspaceHandler(tornado.web.RequestHandler):
    def initialize(self, orch: Orchestrator):
        self.orch = orch

    async def post(self):
        try:
            data = json.loads(self.request.body.decode())
            name = data["name"]
            path_to_file = data["path_to_file"]
            port = int(data["port"])
            node_url = data.get("node_url")  # optional

            self.orch.add_workspace(name, path_to_file, port, node_url=node_url)
            self.write({"status": "ok"})
        except Exception as e:
            self.set_status(400)
            self.write({"error": str(e)})


class WorkspaceCmdHandler(tornado.web.RequestHandler):
    def initialize(self, orch: Orchestrator):
        self.orch = orch

    async def post(self, name):
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
            if cmd == "restart":
                self.write(self.orch.restart_workspace(name)); return

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
        self.host = host
        self.port = port

        base_dir = os.path.dirname(os.path.abspath(__file__))
        web_dir = os.path.join(base_dir, "web")

        self.app = tornado.web.Application([
            # ---- GUI (serve ./web) ----
            (r"/", tornado.web.RedirectHandler, {"url": "/web/orchestrator.html"}),
            (r"/web/(.*)", tornado.web.StaticFileHandler, {"path": web_dir}),

            # ---- API ----
            (r"/add_workspace", AddWorkspaceHandler, dict(orch=self.orch)),
            (r"/workspace/([^/]+)/cmd", WorkspaceCmdHandler, dict(orch=self.orch)),
            (r"/workspace/([^/]+)/status", WorkspaceStatusHandler, dict(orch=self.orch)),
            (r"/workspace/([^/]+)/logs", WorkspaceLogsHandler, dict(orch=self.orch)),
        ])

    def run(self):
        self.app.listen(self.port, address=self.host)
        print(f"Orchestrator server running on {self.host}:{self.port}")
        print(f"GUI: http://{self.host}:{self.port}/web/orchestrator.html")
        tornado.ioloop.IOLoop.current().start()

    def run_in_thread(self):
        t = Thread(target=self.run, daemon=True)
        t.start()
        return t


if __name__ == "__main__":
    server = OrchestratorHTTPServer(port=5000)
    server.run()