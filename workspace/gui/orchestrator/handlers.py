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
from pathlib import Path

import requests
import tornado.ioloop
import tornado.web
import tornado.websocket

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


class ProjectSetupFileHandler(tornado.web.RequestHandler):
    """Serves a project's run-setup screen (``setup:``) and the files
    beside it, out of the declared screen's own folder (usually the
    project's ``hmi/``; a sibling's, e.g. ``../_bna/hmi/setup.js``).

    The Parameters modal is used BEFORE launch, so the runtime server
    that serves the pendant screen is not up yet — the orchestrator
    serves these itself, which also keeps them same-origin with the
    modal (no CORS, and ``import()`` of a project module just works).
    Read-only, and confined to that one folder.
    """

    def initialize(self, orch: Orchestrator):
        self.orch = orch

    async def get(self, name, rel):
        try:
            ws = self.orch.workspaces.get(name)
            spec = ws.setup_spec() if ws else None
            if not spec:
                raise ValueError("no setup screen declared")
            root = Path(spec["dir"]).resolve()
            target = (root / rel).resolve()
            # Confine to the declared folder — a project screen may pull
            # in its own siblings, never anything above them.
            if not str(target).startswith(str(root) + os.sep) or not target.is_file():
                raise ValueError("not found")
            ctype = {".html": "text/html", ".htm": "text/html",
                     ".js": "text/javascript", ".css": "text/css",
                     ".svg": "image/svg+xml", ".png": "image/png",
                     ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                     ".json": "application/json"}.get(target.suffix.lower(),
                                                      "application/octet-stream")
            self.set_header("Content-Type", ctype)
            self.set_header("Cache-Control", "no-store")
            self.write(target.read_bytes())
        except Exception as e:
            self.set_status(404)
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
            has_park = ws.has_trigger("park")
            self.write({
                "kwargs_schema": schema or {},
                "kwargs_values": ws.kwargs_values or {},
                "has_park": has_park,
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
            elif cmd == "park":
                out = await loop.run_in_executor(_cmd_pool, self.orch.park_runtime, name)
            elif cmd == "pause":
                out = await loop.run_in_executor(_cmd_pool, self.orch.pause_runtime, name)
            elif cmd == "resume":
                out = await loop.run_in_executor(_cmd_pool, self.orch.resume_runtime, name)
            elif cmd == "replan":
                out = await loop.run_in_executor(_cmd_pool, self.orch.replan_runtime, name)
            elif cmd == "remove":
                out = await loop.run_in_executor(_cmd_pool, self.orch.remove_runtime, name,
                                                 data.get("items") or [], data.get("reason") or "")
            elif cmd == "replan_cancel":
                out = await loop.run_in_executor(_cmd_pool, self.orch.replan_cancel_runtime, name)
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


# ── The project's folders: live over a WebSocket, bytes over HTTP ────
# fslive.py (twin of dorna_vision/server/fslive.py) does the work; this
# part only names the three roots (data / results / rec) of a workspace
# and answers for REMOTE workspaces by relaying to the node that has the
# files — this orchestrator holds none of its own. EVERY path goes
# through ``safe_join`` before it reaches the filesystem.

from gui.orchestrator import fslive  # noqa: E402


def _project_roots(ws):
    """The workspace's three folders, created on demand."""
    from workspace.project_dirs import project_dirs
    project_dir = os.path.dirname(ws.path_to_file)
    if not project_dir:
        raise ValueError("this workspace has no project folder")
    return project_dirs(project_dir, ensure=True)


def _root_path(ws, root: str):
    roots = _project_roots(ws)
    if root not in roots:
        raise ValueError(f"unknown folder: {root}")
    return roots[root]


def _token_ok(handler) -> bool:
    """``X-Orch-Token`` (or ``?token=`` — a browser WebSocket cannot set
    headers) when auth is on; always true when it is off."""
    if not ORCH_TOKEN:
        return True
    tok = handler.request.headers.get("X-Orch-Token", "") or handler.get_argument("token", "")
    return tok == ORCH_TOKEN


def _node_ws_url(ws, name: str) -> str:
    """The remote node's folder socket: its ``…/orchestrator/api`` base →
    ``ws(s)://…/orchestrator/ws/files/<name>``."""
    from urllib.parse import quote, urlsplit, urlunsplit
    u = urlsplit(ws.node_url.rstrip("/"))
    path = u.path[:-len("/api")] if u.path.endswith("/api") else u.path
    return urlunsplit(("wss" if u.scheme == "https" else "ws", u.netloc,
                       f"{path}/ws/files/{quote(name)}", "", ""))


class ProjectFilesHandler(AuthedHandler):
    """The BYTES of a project folder's files (listing and actions are the
    WebSocket's — ``ProjectFilesSocket``).

    ``GET  …/files/<root>?path=f.csv&download=1`` → the file, streamed
    ``GET  …/files/<root>?path=sub/dir&zip=1``    → the folder, streamed as a zip
    ``GET  …/files/<root>?path=f.csv&preview=1``  → parsed rows / text (capped)
    """

    PREVIEW_BYTES = 2 << 20      # a preview reads at most this much of a file

    def initialize(self, orch: Orchestrator):
        self.orch = orch

    def _ws(self, name):
        if name not in self.orch.workspaces:
            raise ValueError(f"Unknown workspace: {name}")
        return self.orch.workspaces[name]

    async def get(self, name, root):
        if not self.ensure_auth():
            return
        try:
            ws = self._ws(name)
            rel = self.get_argument("path", "")
            if ws.is_remote():
                await self._proxy_get(ws, name, root)
                return
            base = _root_path(ws, root)
            target = fslive.safe_join(base, rel)
            if target.is_dir() and self.get_argument("zip", ""):
                await fslive.send_zip(self, target, target.name if target != base else root)
                return
            if not target.is_file():
                raise ValueError("no such file")
            if self.get_argument("preview", ""):
                self.write(await asyncio.get_running_loop().run_in_executor(
                    None, self._preview, target, self.PREVIEW_BYTES))
                return
            await fslive.send_file(self, target)
        except Exception as e:
            if not self._headers_written:
                self.set_status(400)
                self.write({"error": str(e)})

    @staticmethod
    def _preview(target: Path, limit: int) -> dict:
        """Read a file well enough to judge it without downloading it —
        at most ``limit`` bytes, so a huge log never loads whole.

        A run's two files are the point: ``records.csv`` is already a
        table, and ``records.jsonl`` is one JSON object per line, which
        is a table too once you take the union of its keys. Everything
        else falls back to text. A file we cannot read says so rather
        than rendering half a table.
        """
        import csv
        import io
        import json as _json
        MAX_ROWS = 500
        MAX_TEXT = 20000
        try:
            with open(target, "rb") as fp:
                raw = fp.read(limit + 1)
        except OSError as ex:
            return {"kind": "error", "error": str(ex)}
        clipped = len(raw) > limit
        text = raw[:limit].decode(errors="replace")
        if clipped:
            text = text[:text.rfind("\n") + 1] or text      # no half last line
        suffix = target.suffix.lower()

        if suffix in (".csv", ".tsv"):
            rows = list(csv.reader(io.StringIO(text),
                                   delimiter="\t" if suffix == ".tsv" else ","))
            if not rows:
                return {"kind": "table", "columns": [], "rows": [], "truncated": False}
            return {"kind": "table", "columns": rows[0], "rows": rows[1:MAX_ROWS + 1],
                    "truncated": clipped or len(rows) - 1 > MAX_ROWS,
                    "total": len(rows) - 1, "note": "first 2 MB read" if clipped else ""}

        if suffix in (".jsonl", ".ndjson"):
            objs, bad = [], 0
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    o = _json.loads(line)
                except ValueError:
                    bad += 1
                    continue
                objs.append(o if isinstance(o, dict) else {"value": o})
            if not objs:
                return {"kind": "text", "text": text[:MAX_TEXT],
                        "truncated": clipped or len(text) > MAX_TEXT}
            cols: list = []
            for o in objs:                      # union of keys, first-seen order
                for k in o:
                    if k not in cols:
                        cols.append(k)

            def cell(v):
                if v is None:
                    return ""
                if isinstance(v, (dict, list)):
                    return _json.dumps(v, separators=(",", ":"))
                return str(v)
            notes = [n for n in (f"{bad} unreadable line(s)" if bad else "",
                                 "first 2 MB read" if clipped else "") if n]
            return {"kind": "table", "columns": cols,
                    "rows": [[cell(o.get(c)) for c in cols] for o in objs[:MAX_ROWS]],
                    "truncated": clipped or len(objs) > MAX_ROWS, "total": len(objs),
                    "note": " · ".join(notes)}

        if suffix == ".json" and not clipped:
            try:
                text = _json.dumps(_json.loads(text), indent=2)
            except ValueError:
                pass                            # show it raw; it is not valid JSON
        return {"kind": "text", "text": text[:MAX_TEXT],
                "truncated": clipped or len(text) > MAX_TEXT}

    async def _proxy_get(self, ws, name, root):
        """The node's bytes, relayed as they arrive — never buffered."""
        from tornado.httpclient import AsyncHTTPClient, HTTPRequest
        from urllib.parse import urlencode
        url = self.orch._orch_url(
            ws, f"/workspace/{requests.utils.quote(name)}/files/{requests.utils.quote(root)}")
        args = {k: v[0].decode() for k, v in self.request.arguments.items()}
        url += "?" + urlencode(args)

        def on_header(line: str):
            k, _, v = line.partition(":")
            if k.strip().lower() in ("content-type", "content-disposition", "content-length"):
                self.set_header(k.strip(), v.strip())

        def on_chunk(chunk: bytes):
            self.write(chunk)
            self.flush()

        await AsyncHTTPClient().fetch(HTTPRequest(
            url, headers=self.orch._auth_headers(), header_callback=on_header,
            streaming_callback=on_chunk, request_timeout=3600))


class ProjectUploadHandler(fslive.UploadHandler):
    """``PUT …/files/<root>/upload?path=<folder>&name=<file>`` — the file
    as the raw body, streamed to disk (fslive.UploadHandler). A REMOTE
    workspace's upload is spooled to a temp file here (bounded memory)
    and then streamed on to the node."""

    def initialize(self, orch: Orchestrator):
        self.orch = orch

    def authorized(self) -> bool:
        return _token_ok(self)

    def _ws(self):
        name = self.path_args[0]
        if name not in self.orch.workspaces:
            raise ValueError(f"Unknown workspace: {name}")
        return self.orch.workspaces[name]

    def resolve(self, name, root) -> Path:
        return _root_path(self._ws(), root)

    def created(self, path: Path) -> None:
        from workspace.project_dirs import hand_back
        hand_back(path)

    def prepare(self):
        self._remote = None
        try:
            ws = self._ws()
        except Exception:
            ws = None
        if ws is not None and ws.is_remote():
            import tempfile
            self.request.connection.set_max_body_size(fslive.MAX_BODY)
            self._err = None if self.authorized() else PermissionError("Unauthorized")
            self._fp = tempfile.TemporaryFile()
            self._remote = ws
            return
        super().prepare()

    async def put(self, name, root):
        if self._remote is None:
            return super().put(name, root)
        if self._err is not None:
            self.set_status(401)
            self.write({"error": str(self._err)})
            return
        from tornado.httpclient import AsyncHTTPClient, HTTPRequest
        from urllib.parse import urlencode
        fp, self._fp = self._fp, None
        fp.seek(0)
        url = self.orch._orch_url(
            self._remote, f"/workspace/{requests.utils.quote(name)}/files/"
                          f"{requests.utils.quote(root)}/upload")
        url += "?" + urlencode({"path": self.get_argument("path", ""),
                                "name": self.get_argument("name", "")})

        async def body(write):
            while chunk := fp.read(fslive.CHUNK):
                await write(chunk)
        try:
            r = await AsyncHTTPClient().fetch(HTTPRequest(
                url, method="PUT", body_producer=body, headers=self.orch._auth_headers(),
                request_timeout=3600), raise_error=False)
            self.set_status(r.code)
            self.set_header("Content-Type", "application/json")
            self.write(r.body)
        finally:
            fp.close()


class ProjectFilesSocket(fslive.FilesSocket):
    """``WS /orchestrator/ws/files/<name>`` — a workspace's folders, live
    (fslive.FilesSocket: open / mkdir / delete, changes pushed). A
    REMOTE workspace's socket is a relay to the node's own, which does
    the watching."""

    def initialize(self, orch: Orchestrator):
        self.orch = orch
        self._relay = None
        self._ws = None

    def authorized(self) -> bool:
        return _token_ok(self)

    def resolve(self, root: str) -> Path:
        return _root_path(self._ws, root)

    def meta(self, root: str, base: Path) -> dict:
        from workspace.project_dirs import ROOT_LABELS, declared_roots
        return {"label": ROOT_LABELS.get(root, ""), "base": str(base),
                "declared": declared_roots(os.path.dirname(self._ws.path_to_file)).get(root, False)}

    def created(self, path: Path) -> None:
        from workspace.project_dirs import hand_back
        hand_back(path)

    async def open(self, name):
        super().open(name)
        if name not in self.orch.workspaces:
            self.close(4404, "Unknown workspace")
            return
        self._ws = self.orch.workspaces[name]
        if self._ws.is_remote():
            from tornado.httpclient import HTTPRequest
            from tornado.websocket import websocket_connect
            try:
                self._relay = await websocket_connect(HTTPRequest(
                    _node_ws_url(self._ws, name), headers=self.orch._auth_headers()))
            except Exception as ex:
                self.close(4502, f"node unreachable: {ex}")
                return
            tornado.ioloop.IOLoop.current().spawn_callback(self._pump)

    async def _pump(self):
        """Node → page, until either side closes."""
        while self._relay is not None:
            msg = await self._relay.read_message()
            if msg is None:
                self.close(4502, "node closed")
                return
            try:
                self.write_message(msg)
            except tornado.websocket.WebSocketClosedError:
                return

    async def on_message(self, raw):
        if self._relay is not None:
            await self._relay.write_message(raw)
            return
        await super().on_message(raw)

    def on_close(self):
        if self._relay is not None:
            self._relay.close()
            self._relay = None
        super().on_close()
