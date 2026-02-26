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
    def __init__(self, name: str, path_to_file: str, port: int):
        self.name = name
        self.path_to_file = path_to_file
        self.port = port
        self.process: Optional[subprocess.Popen] = None
        self.log_path: str = f"/tmp/{name}.log"
        self.started_at: Optional[float] = None  # unix seconds (timer starts at LAUNCH)
        self.last_error: Optional[str] = None

class Orchestrator:
    """Manages multiple workspaces as separate OS processes."""
    def __init__(self):
        self.workspaces: Dict[str, WorkspaceInfo] = {}

    # ---------------- Workspace management ----------------

    def add_workspace(self, name: str, path_to_file: str, port: int):
        if name in self.workspaces:
            raise ValueError(f"Workspace {name} already exists.")
        if not os.path.isfile(path_to_file):
            raise FileNotFoundError(f"{path_to_file} does not exist.")
        self.workspaces[name] = WorkspaceInfo(name, path_to_file, port)

    def is_launched(self, name: str) -> bool:
        ws = self.workspaces[name]
        return ws.process is not None and ws.process.poll() is None

    def start_workspace_process(self, name: str):
        """
        Launch the OS process (LAUNCH state).
        Does NOT start runtime workflow (no motion).
        Always runs with sudo.
        """
        ws = self.workspaces[name]
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

        ws.started_at = time.time()   # timer starts at LAUNCH
        ws.last_error = None

    def wait_until_ready(self, name: str, timeout: float = 8.0) -> bool:
        """
        Wait until workspace runtime server responds on /status.
        """
        ws = self.workspaces[name]
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

    def launch_workspace(self, name: str):
        """
        Public LAUNCH command. Spawns process and waits for /status.
        """
        ws = self.workspaces[name]
        if self.is_launched(name):
            ready = self.wait_until_ready(name, timeout=1.0)
            return {
                "status": "ok",
                "launched": True,
                "ready": bool(ready),
                "port": ws.port,
                "log": ws.log_path,
                "note": "already running",
            }

        self.start_workspace_process(name)

        ready = self.wait_until_ready(name, timeout=8.0)
        if not ready:
            raise RuntimeError(
                f"Workspace {name} launched but did not become ready on port {ws.port}. "
                f"Check logs: {ws.log_path}"
            )

        return {"status": "ok", "launched": True, "ready": True, "port": ws.port, "log": ws.log_path}

    def stop_workspace(self, name: str):
        ws = self.workspaces[name]
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
    

    def restart_workspace(self, name: str):
        """
        Restart = kill + launch (still NOT starting workflow).
        """
        self.stop_workspace(name)
        return self.launch_workspace(name)

    # ---------------- Runtime commands via HTTP ----------------

    def _send_cmd(self, ws: WorkspaceInfo, cmd: str):
        url = f"http://127.0.0.1:{ws.port}/cmd"
        r = requests.post(url, json={"cmd": cmd}, timeout=3)
        r.raise_for_status()
        return r.json()

    def start_runtime(self, name: str):
        """
        START workflow (motion). Requires LAUNCH first.
        """
        ws = self.workspaces[name]
        if not self.is_launched(name):
            raise RuntimeError(f"Workspace {name} is not launched. Send cmd=launch first.")
        if not self.wait_until_ready(name, timeout=2.0):
            raise RuntimeError(f"Workspace {name} is launched but not responding. Check logs: {ws.log_path}")
        return self._send_cmd(ws, "start")

    def pause_runtime(self, name: str):
        ws = self.workspaces[name]
        if not self.is_launched(name):
            raise RuntimeError(f"Workspace {name} is not launched.")
        return self._send_cmd(ws, "pause")

    def resume_runtime(self, name: str):
        ws = self.workspaces[name]
        if not self.is_launched(name):
            raise RuntimeError(f"Workspace {name} is not launched.")
        return self._send_cmd(ws, "resume")


    def get_status(self, name: str):
        ws = self.workspaces[name]

        # If not launched, return orchestrator-level status
        if not self.is_launched(name):
            return {
                "state": "NOT_LAUNCHED",
                "last_error": ws.last_error,
                "port": ws.port,
                "log": ws.log_path,
                "started_at": None,
                "uptime_s": None,
            }

        # launched -> compute uptime from orchestrator start time
        uptime_s = None
        if ws.started_at is not None:
            uptime_s = max(0.0, time.time() - ws.started_at)

        url = f"http://127.0.0.1:{ws.port}/status"
        try:
            r = requests.get(url, timeout=3)
            r.raise_for_status()
            out = r.json()

            # attach orchestrator metadata + timing (always present)
            out["_orch"] = {"launched": True, "port": ws.port, "log": ws.log_path}
            out["started_at"] = ws.started_at
            out["uptime_s"] = uptime_s
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
            }


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
            self.orch.add_workspace(name, path_to_file, port)
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
                self.write(self.orch.launch_workspace(name))
                return
            if cmd == "start":
                self.write(self.orch.start_runtime(name))
                return
            if cmd == "pause":
                self.write(self.orch.pause_runtime(name))
                return
            if cmd == "resume":
                self.write(self.orch.resume_runtime(name))
                return
            if cmd == "kill":
                self.orch.stop_workspace(name)
                self.write({"status": "ok", "killed": True})
                return
            if cmd == "restart":
                self.write(self.orch.restart_workspace(name))
                return

            raise ValueError("Unknown cmd")
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

            ws = self.orch.workspaces[name]
            tail = int(self.get_argument("tail", 200))

            if not os.path.isfile(ws.log_path):
                self.write({"text": ""})
                return

            # read last N lines efficiently enough for small logs
            with open(ws.log_path, "r", errors="replace") as f:
                lines = f.readlines()[-tail:]
            self.write({"text": "".join(lines)})

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
        print("GUI: http://<host>:9000/  (or /web/orchestrator.html)")
        tornado.ioloop.IOLoop.current().start()

    def run_in_thread(self):
        t = Thread(target=self.run, daemon=True)
        t.start()
        return t


if __name__ == "__main__":
    server = OrchestratorHTTPServer()
    server.run()