"""The main page — the projects page at ``/`` — and its API.

    GET  /                                    the page (web/index.html)
    GET  /home/<file>                         its script + styles
    GET  /home/api/version                    {version} — this Workspace (the top bar's Updates panel)
    GET  /home/api/projects                   the cards: registry + thumb, chassis, git
    GET  /home/api/projects/<n>/thumb         <project>/.workspace/thumb.png (404 if none)
    POST /home/api/projects/<n>/thumb         {image: "data:image/png;base64,…"}
    POST /home/api/projects/<n>/rename        {name}
    POST /home/api/projects/<n>/copy          {name?}
    POST /home/api/projects/<n>/delete        {confirm: <the project's name>}
    POST /home/api/new                        {chassis: core_500|core_1000, name?}
    POST /home/api/import/git                 {url, name?}
    POST /home/api/import/zip                 multipart "file" (+ name?)
    POST /home/api/import/path                {path, name?}

Writes use the orchestrator's ``X-Orch-Token`` rule (AuthedHandler).
Offline (no account login) the page only runs existing projects: new,
import, copy, rename and delete answer 401 {error: "login required"}.
The logic is projects.py; run controls stay on the orchestrator API.
"""
from __future__ import annotations

import asyncio
import json
import os

import tornado.web

from gui.orchestrator.handlers import AuthedHandler
from gui.orchestrator.server import NoCacheStaticFileHandler
from gui.home import projects as P

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")


class HomePageHandler(tornado.web.RequestHandler):
    def get(self):
        self.set_header("Content-Type", "text/html; charset=utf-8")
        self.set_header("Cache-Control", "no-store")
        with open(os.path.join(WEB_DIR, "index.html"), encoding="utf-8") as f:
            self.write(f.read())


class VersionHandler(tornado.web.RequestHandler):
    """The installed Workspace version (workspace.version) — read by the
    top bar's Updates panel (vendor/nav.js) on every page."""
    def initialize(self, version: str = ""):
        self.version = version

    def get(self):
        self.set_header("Cache-Control", "no-store")
        self.write({"version": self.version})


def logged_in() -> bool:
    """The unit's account login (builder C's gui.account). Without that
    module the unit is in the offline mode."""
    try:
        from gui.account import account
    except ImportError:
        return False
    try:
        return bool(account.logged_in())
    except Exception:
        return False


class _Base(AuthedHandler):
    def initialize(self, orch):
        self.orch = orch
        P.set_loop(asyncio.get_event_loop())

    def body(self) -> dict:
        try:
            b = json.loads(self.request.body.decode() or "{}")
        except (ValueError, UnicodeDecodeError):
            raise P.ProjectError("The request body is not JSON.")
        if not isinstance(b, dict):
            raise P.ProjectError("The request body is not a JSON object.")
        return b

    def refuse(self, status: int, msg: str) -> None:
        self.set_status(status)
        self.write({"error": msg})

    def gate(self) -> bool:
        """Project management needs a login; False once refused."""
        if not self.ensure_auth():
            return False
        if not logged_in():
            self.refuse(401, "login required")
            return False
        return True

    async def run(self, fn, *args, gated: bool = True):
        """``fn(orch, *args)`` off the IOLoop; a refusal → 400 {error}.
        ``args`` may be a callable returning them (the body, parsed here)."""
        if not (self.gate() if gated else self.ensure_auth()):
            return
        try:
            if len(args) == 1 and callable(args[0]):
                args = args[0]()
            out = await asyncio.get_running_loop().run_in_executor(None, fn, self.orch, *args)
            self.write(out)
        except (P.ProjectError, ValueError, RuntimeError, FileNotFoundError, OSError) as e:
            self.set_status(400)
            self.write({"error": str(e)})


class ProjectsListHandler(_Base):
    async def get(self):
        self.set_header("Cache-Control", "no-store")
        self.write(P.listing(self.orch))


class ProjectThumbHandler(_Base):
    def get(self, name):
        try:
            data = P.read_thumb(self.orch, name)
        except P.ProjectError:
            data = None
        if not data or not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise tornado.web.HTTPError(404)
        self.set_header("Content-Type", "image/png")
        self.set_header("Cache-Control", "no-cache")
        self.write(data)

    async def post(self, name):
        # The viewer saves the picture of the scene — not a paid feature.
        def args():
            b = self.body()
            return (name, str(b.get("image") or b.get("data_url") or ""))
        await self.run(P.save_thumb, args, gated=False)


class ProjectActionHandler(_Base):
    async def post(self, name, action):
        fn, key = {"rename": (P.rename_project, "name"), "copy": (P.copy_project, "name"),
                   "delete": (P.delete_project, "confirm")}[action]
        await self.run(fn, lambda: (name, str(self.body().get(key) or "")))


class NewProjectHandler(_Base):
    async def post(self):
        def args():
            b = self.body()
            return (str(b.get("chassis") or ""), str(b.get("name") or ""))
        await self.run(P.new_project, args)


class ImportHandler(_Base):
    async def post(self, kind):
        if kind == "zip":
            def args():
                files = self.request.files.get("file") or []
                if not files:
                    raise P.ProjectError("No zip file uploaded.")
                f = files[0]
                return (f["filename"], f["body"], self.get_body_argument("name", ""))
            await self.run(P.import_zip, args)
            return
        fn, key = {"git": (P.import_git, "url"), "path": (P.import_path, "path")}[kind]

        def jargs():
            b = self.body()
            return (str(b.get(key) or ""), str(b.get("name") or ""))
        await self.run(fn, jargs)


def home_routes(orch, version: str = ""):
    """The main page's routes, for gui/server.py make_app()."""
    o = dict(orch=orch)
    return [
        (r"/", HomePageHandler),
        (r"/home/api/version", VersionHandler, dict(version=version)),
        (r"/home/api/projects", ProjectsListHandler, o),
        (r"/home/api/projects/([^/]+)/thumb", ProjectThumbHandler, o),
        (r"/home/api/projects/([^/]+)/(rename|copy|delete)", ProjectActionHandler, o),
        (r"/home/api/new", NewProjectHandler, o),
        (r"/home/api/import/(git|zip|path)", ImportHandler, o),
        (r"/home/(.*)", NoCacheStaticFileHandler, {"path": WEB_DIR}),
    ]
