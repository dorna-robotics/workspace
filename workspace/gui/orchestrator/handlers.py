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


class ProjectSetupFileHandler(tornado.web.RequestHandler):
    """Serves a project's own run-setup screen (``setup:``) and the
    files beside it, out of the project's ``hmi/`` folder.

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


# ── The project's folders, over HTTP ──────────────────────────────────
# One handler for all three roots (data / results / rec). Every request
# names its root and a path inside it, and EVERY path goes through
# ``safe_join`` before it reaches the filesystem — see project_dirs.py.
#
# Remote workspaces are proxied like the upload handler: this
# orchestrator holds no files of its own, it answers for the machine
# that has them.

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


def _entry(p: Path, rel_to: Path) -> dict:
    """One row for the browser: what it is, how big, how old."""
    st = p.stat()
    return {
        "name": p.name,
        "path": str(p.relative_to(rel_to)),
        "dir": p.is_dir(),
        "size": 0 if p.is_dir() else st.st_size,
        "mtime": st.st_mtime,
    }


class ProjectFilesHandler(AuthedHandler):
    """List / download a folder or file under one of the project roots.

    ``GET  …/files/<root>?path=sub/dir``        → listing
    ``GET  …/files/<root>?path=f.csv&download=1`` → the bytes
    ``GET  …/files/<root>?path=f.csv&preview=1``  → parsed CSV rows
    """

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
                self._proxy_get(ws, name, root, rel)
                return
            from workspace.project_dirs import ROOT_LABELS, declared_roots, safe_join
            base = _root_path(ws, root)
            target = safe_join(base, rel)

            if target.is_file():
                if self.get_argument("preview", ""):
                    self.write(self._preview(target))
                    return
                self.set_header("Content-Type", "application/octet-stream")
                self.set_header("Content-Disposition",
                                f'attachment; filename="{target.name}"')
                with open(target, "rb") as fp:
                    while chunk := fp.read(1 << 16):
                        self.write(chunk)
                await self.flush()
                return

            if not target.exists():
                # An absent folder lists EMPTY rather than 404: the
                # operator asked "what is in results?", and "nothing
                # yet" is the honest answer, not an error.
                entries = []
            else:
                entries = sorted(
                    (_entry(c, base) for c in target.iterdir()
                     if not c.name.startswith(".")),
                    key=lambda e: (not e["dir"], -e["mtime"]),
                )
            self.write({
                "root": root,
                "label": ROOT_LABELS.get(root, ""),
                "declared": declared_roots(os.path.dirname(ws.path_to_file)).get(root, False),
                "abs": str(base),
                "path": rel,
                "entries": entries,
            })
        except Exception as e:
            self.set_status(400)
            self.write({"error": str(e)})

    @staticmethod
    def _preview(target: Path) -> dict:
        """Read a file well enough to judge it without downloading it.

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
            text = target.read_text(errors="replace")
        except OSError as ex:
            return {"kind": "error", "error": str(ex)}
        suffix = target.suffix.lower()

        if suffix in (".csv", ".tsv"):
            rows = list(csv.reader(io.StringIO(text),
                                   delimiter="\t" if suffix == ".tsv" else ","))
            if not rows:
                return {"kind": "table", "columns": [], "rows": [], "truncated": False}
            return {"kind": "table", "columns": rows[0], "rows": rows[1:MAX_ROWS + 1],
                    "truncated": len(rows) - 1 > MAX_ROWS, "total": len(rows) - 1}

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
                        "truncated": len(text) > MAX_TEXT}
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
            return {"kind": "table", "columns": cols,
                    "rows": [[cell(o.get(c)) for c in cols] for o in objs[:MAX_ROWS]],
                    "truncated": len(objs) > MAX_ROWS, "total": len(objs),
                    "note": f"{bad} unreadable line(s)" if bad else ""}

        if suffix == ".json":
            try:
                text = _json.dumps(_json.loads(text), indent=2)
            except ValueError:
                pass                            # show it raw; it is not valid JSON
        return {"kind": "text", "text": text[:MAX_TEXT],
                "truncated": len(text) > MAX_TEXT}

    def _proxy_get(self, ws, name, root, rel):
        url = self.orch._orch_url(
            ws, f"/workspace/{requests.utils.quote(name)}/files/{requests.utils.quote(root)}")
        r = requests.get(url, params=dict(self.request.arguments and
                                          {k: v[0].decode() for k, v in
                                           self.request.arguments.items()} or {}),
                         timeout=30, headers=self.orch._auth_headers())
        r.raise_for_status()
        ctype = r.headers.get("Content-Type", "application/json")
        self.set_header("Content-Type", ctype)
        if "application/json" not in ctype:
            self.set_header("Content-Disposition",
                            r.headers.get("Content-Disposition", ""))
        self.write(r.content)


class ProjectFilesActionHandler(AuthedHandler):
    """Upload / delete / new folder under a project root.

    ``POST …/files/<root>/upload``  multipart ``file``, ``path`` = folder
    ``POST …/files/<root>/mkdir``   json ``{"path": "sub/new"}``
    ``POST …/files/<root>/delete``  json ``{"path": "sub/f.csv"}``

    Delete removes a file or an EMPTY folder only. A run's folder full
    of records cannot go in one click — emptying it is a deliberate
    sequence, not a mis-click.
    """

    def initialize(self, orch: Orchestrator):
        self.orch = orch

    async def post(self, name, root, action):
        if not self.ensure_auth():
            return
        try:
            if name not in self.orch.workspaces:
                raise ValueError(f"Unknown workspace: {name}")
            ws = self.orch.workspaces[name]
            if ws.is_remote():
                self.write(self._proxy_post(ws, name, root, action))
                return
            from workspace.project_dirs import hand_back, safe_join
            base = _root_path(ws, root)

            if action == "upload":
                if not self.request.files or "file" not in self.request.files:
                    raise ValueError("No file uploaded")
                folder = safe_join(base, self.get_argument("path", ""))
                folder.mkdir(parents=True, exist_ok=True)
                up = self.request.files["file"][0]
                filename = os.path.basename(up["filename"] or "")
                if not filename:
                    raise ValueError("Empty filename")
                dest = safe_join(folder, filename)
                with open(dest, "wb") as fp:
                    fp.write(up["body"])
                hand_back(dest)
                self.write({"ok": True, "path": str(dest.relative_to(base)),
                            "name": dest.name, "abs": str(dest)})
                return

            body = json.loads(self.request.body or b"{}")
            rel = body.get("path") or ""
            target = safe_join(base, rel)

            if action == "mkdir":
                if not rel:
                    raise ValueError("name is required")
                target.mkdir(parents=True, exist_ok=True)
                hand_back(target)
                self.write({"ok": True, "path": str(target.relative_to(base))})
                return

            if action == "delete":
                if target == base:
                    raise ValueError("cannot delete the folder itself")
                if not target.exists():
                    raise ValueError("no such file")
                if target.is_dir():
                    if any(target.iterdir()):
                        raise ValueError("folder is not empty — empty it first")
                    target.rmdir()
                else:
                    target.unlink()
                self.write({"ok": True})
                return

            raise ValueError(f"unknown action: {action}")
        except Exception as e:
            self.set_status(400)
            self.write({"error": str(e)})

    def _proxy_post(self, ws, name, root, action):
        url = self.orch._orch_url(
            ws, f"/workspace/{requests.utils.quote(name)}/files/"
                f"{requests.utils.quote(root)}/{requests.utils.quote(action)}")
        if action == "upload" and self.request.files:
            import io
            up = self.request.files["file"][0]
            r = requests.post(url, params={"path": self.get_argument("path", "")},
                              files={"file": (os.path.basename(up["filename"]),
                                              io.BytesIO(up["body"]),
                                              up.get("content_type",
                                                     "application/octet-stream"))},
                              timeout=60, headers=self.orch._auth_headers())
        else:
            r = requests.post(url, data=self.request.body, timeout=30,
                              headers={**self.orch._auth_headers(),
                                       "Content-Type": "application/json"})
        r.raise_for_status()
        return r.json()
