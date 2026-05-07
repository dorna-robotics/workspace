"""Orchestrator HTTP handlers — every Tornado RequestHandler that
serves the admin GUI / API lives here.

Two groups:

  * **Auth-gated handlers** (extend ``AuthedHandler``): write actions
    that need ``X-Orch-Token`` when ``ORCH_TOKEN`` is set —
    add_workspace, remove_workspace, command, kwargs, file upload.
  * **Read handlers** (extend ``tornado.web.RequestHandler``): list /
    status / logs / launch_config / device proxy. No auth.

The shared thread pools (``_status_pool``, ``_cmd_pool``) live in
``orchestrator`` so this module imports them from there alongside the
``Orchestrator`` class itself.
"""
from __future__ import annotations

import asyncio
import json
import os

import requests
import tornado.web

from gui.orchestrator.orchestrator import (
    ORCH_TOKEN,
    Orchestrator,
    _cmd_pool,
    _status_pool,
)


class AuthedHandler(tornado.web.RequestHandler):
    """Base for handlers that require ``X-Orch-Token`` when auth is on.
    When ``ORCH_TOKEN`` is unset, ``require_auth`` always returns True."""

    def require_auth(self) -> bool:
        if not ORCH_TOKEN:
            return True
        tok = self.request.headers.get("X-Orch-Token", "")
        return tok == ORCH_TOKEN

    def ensure_auth(self) -> bool:
        if self.require_auth():
            return True
        self.set_status(401)
        self.write({"error": "Unauthorized"})
        return False


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
            name = data["name"]
            path_to_file = data["path_to_file"]
            port = int(data["port"])
            node_url = data.get("node_url")
            label = data.get("label", "") or ""

            self.orch.add_workspace(
                name, path_to_file, port,
                node_url=node_url, label=label,
                sync_remote=True, persist=True,
            )
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


class LaunchConfigHandler(tornado.web.RequestHandler):
    """Returns the kwargs schema from the workspace's launch.yaml."""
    def initialize(self, orch: Orchestrator):
        self.orch = orch

    async def get(self, name):
        try:
            if name not in self.orch.workspaces:
                raise ValueError(f"Unknown workspace: {name}")
            ws = self.orch.workspaces[name]
            schema = ws.launch_config()
            has_end = ws.has_trigger("end")
            self.write({
                "kwargs_schema": schema or {},
                "kwargs_values": ws.kwargs_values or {},
                "has_end": has_end,
            })
        except Exception as e:
            self.set_status(400)
            self.write({"error": str(e)})


class FileUploadHandler(AuthedHandler):
    """Upload a file for a kwargs field. Saves to <project_dir>/_uploads/<field>/."""
    def initialize(self, orch: Orchestrator):
        self.orch = orch

    async def post(self, name, field):
        if not self.ensure_auth():
            return
        try:
            if name not in self.orch.workspaces:
                raise ValueError(f"Unknown workspace: {name}")
            ws = self.orch.workspaces[name]

            if not self.request.files or "file" not in self.request.files:
                raise ValueError("No file uploaded")

            uploaded = self.request.files["file"][0]
            filename = os.path.basename(uploaded["filename"])
            if not filename:
                raise ValueError("Empty filename")

            if ws.is_remote():
                # Forward upload to remote node
                import io
                url = self.orch._orch_url(
                    ws,
                    f"/workspace/{requests.utils.quote(name)}/upload/{requests.utils.quote(field)}"
                )
                r = requests.post(
                    url,
                    files={"file": (
                        filename,
                        io.BytesIO(uploaded["body"]),
                        uploaded.get("content_type", "application/octet-stream"),
                    )},
                    timeout=30,
                    headers=self.orch._auth_headers(),
                )
                r.raise_for_status()
                result = r.json()
            else:
                # Save locally to project _uploads dir
                project_dir = os.path.dirname(ws.path_to_file)
                upload_dir = os.path.join(project_dir, "_uploads", field)
                os.makedirs(upload_dir, exist_ok=True)

                # Remove old files for this field
                for old in os.listdir(upload_dir):
                    try:
                        os.remove(os.path.join(upload_dir, old))
                    except Exception:
                        pass

                dest = os.path.join(upload_dir, filename)
                with open(dest, "wb") as f:
                    f.write(uploaded["body"])
                result = {"status": "ok", "path": dest, "filename": filename}

            # Store the path in kwargs_values (in-memory)
            ws.kwargs_values[field] = result["path"]

            self.write(result)
        except Exception as e:
            self.set_status(400)
            self.write({"error": str(e)})


class UpdateKwargsHandler(AuthedHandler):
    """Update stored kwargs_values for a workspace."""
    def initialize(self, orch: Orchestrator):
        self.orch = orch

    async def post(self, name):
        if not self.ensure_auth():
            return
        try:
            if name not in self.orch.workspaces:
                raise ValueError(f"Unknown workspace: {name}")
            data = json.loads(self.request.body.decode())
            ws = self.orch.workspaces[name]
            ws.kwargs_values = data.get("kwargs_values", {})
            self.write({"status": "ok"})
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

            # Deferred import to avoid the orchestrator/websockets
            # circular at module-load time.
            from gui.orchestrator.websockets import broadcast_status

            loop = asyncio.get_running_loop()
            out = None

            if cmd == "launch":
                out = await loop.run_in_executor(_cmd_pool, self.orch.launch_workspace, name)
            elif cmd == "start":
                kwargs = data.get("kwargs")
                out = await loop.run_in_executor(_cmd_pool, self.orch.start_runtime, name, kwargs)
            elif cmd == "end":
                out = await loop.run_in_executor(_cmd_pool, self.orch.end_runtime, name)
            elif cmd == "pause":
                out = await loop.run_in_executor(_cmd_pool, self.orch.pause_runtime, name)
            elif cmd == "resume":
                out = await loop.run_in_executor(_cmd_pool, self.orch.resume_runtime, name)
            elif cmd == "kill":
                out = await loop.run_in_executor(_cmd_pool, self.orch.stop_workspace, name)
                out = out or {"status": "ok", "killed": True}
            elif cmd in ("restart", "relaunch"):
                out = await loop.run_in_executor(_cmd_pool, self.orch.relaunch_workspace, name)
            else:
                raise ValueError("Unknown cmd")

            self.write(out)
            # Broadcast updated status to all WS clients immediately
            asyncio.ensure_future(broadcast_status(self.orch))
        except Exception as e:
            self.set_status(400)
            self.write({"error": str(e)})


class WorkspaceDevicesHandler(tornado.web.RequestHandler):
    """GET /orchestrator/api/workspace/<name>/devices → proxy to the workspace's /devices.

    Wraps the workspace's HTTP endpoint so the admin browser doesn't
    have to reach the workspace port cross-origin (CORS-free).
    """
    def initialize(self, orch: Orchestrator):
        self.orch = orch

    async def get(self, name):
        ws = self.orch.workspaces.get(name)
        if ws is None:
            self.set_status(404)
            self.write({"devices": []})
            return
        try:
            if ws.is_remote():
                url = self.orch._orch_url(
                    ws, f"/workspace/{requests.utils.quote(name)}/devices"
                )
                r = requests.get(url, timeout=5, headers=self.orch._auth_headers())
            else:
                if not ws.port:
                    self.write({"devices": []})
                    return
                r = requests.get(f"http://127.0.0.1:{ws.port}/devices", timeout=5)
            self.set_header("Content-Type", "application/json")
            self.write(r.text)
        except Exception as ex:
            self.set_status(502)
            self.write({"devices": [], "error": str(ex)})


class WorkspaceDeviceCmdHandler(tornado.web.RequestHandler):
    """POST /orchestrator/api/workspace/<name>/devices/<id>/<action>.

    Action ∈ {recover, release}. Forwards to the workspace's
    /devices/<id>/<action> and returns the device's reply payload.
    """
    def initialize(self, orch: Orchestrator):
        self.orch = orch

    async def post(self, name, device_id, action):
        ws = self.orch.workspaces.get(name)
        if ws is None:
            self.set_status(404)
            self.write({"ok": False, "msg": f"Unknown workspace: {name}"})
            return
        try:
            if ws.is_remote():
                url = self.orch._orch_url(
                    ws,
                    f"/workspace/{requests.utils.quote(name)}/devices/"
                    f"{requests.utils.quote(device_id)}/{action}",
                )
                r = requests.post(url, timeout=35, headers=self.orch._auth_headers())
            else:
                if not ws.port:
                    self.set_status(503)
                    self.write({"ok": False, "msg": "workspace not running"})
                    return
                r = requests.post(
                    f"http://127.0.0.1:{ws.port}/devices/"
                    f"{requests.utils.quote(device_id)}/{action}",
                    timeout=35,
                )
            self.set_header("Content-Type", "application/json")
            self.write(r.text)
        except Exception as ex:
            self.set_status(502)
            self.write({"ok": False, "msg": f"{type(ex).__name__}: {ex}"})


class WorkspacesStatusHandler(tornado.web.RequestHandler):
    """Bulk status: returns all workspace statuses in one request (Pi-friendly)."""
    def initialize(self, orch: Orchestrator):
        self.orch = orch

    async def get(self):
        names = list(self.orch.workspaces.keys())
        if not names:
            self.write({"statuses": {}})
            return
        loop = asyncio.get_running_loop()

        async def fetch_one(name):
            try:
                st = await loop.run_in_executor(_status_pool, self.orch.get_status, name)
                return name, st
            except Exception as e:
                return name, {"state": "OFFLINE", "last_error": str(e)}

        pairs = await asyncio.gather(*[fetch_one(n) for n in names])
        self.write({"statuses": dict(pairs)})


class WorkspaceStatusHandler(tornado.web.RequestHandler):
    def initialize(self, orch: Orchestrator):
        self.orch = orch

    async def get(self, name):
        try:
            if name not in self.orch.workspaces:
                raise ValueError(f"Unknown workspace: {name}")
            loop = asyncio.get_running_loop()
            st = await loop.run_in_executor(_status_pool, self.orch.get_status, name)
            self.write(st)
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

    async def delete(self, name):
        try:
            if name not in self.orch.workspaces:
                raise ValueError(f"Unknown workspace: {name}")
            ws = self.orch.workspaces[name]
            if os.path.isfile(ws.log_path):
                open(ws.log_path, "w").close()
            self.write({"ok": True})
        except Exception as e:
            self.set_status(400)
            self.write({"error": str(e)})
