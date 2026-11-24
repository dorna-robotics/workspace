"""The main page — the projects page at ``/`` — and its API.

    GET  /                                    the page (web/index.html)
    GET  /home/<file>                         its script + styles
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
    def initialize(self, version: str = ""):
        self.version = version

    def get(self):
        with open(os.path.join(WEB_DIR, "index.html"), encoding="utf-8") as f:
            html = f.read()
        self.set_header("Content-Type", "text/html; charset=utf-8")
        self.set_header("Cache-Control", "no-store")
        self.write(html.replace("__WS_VERSION__", self.version))


class _Base(AuthedHandler):
    def initialize(self, orch):
        self.orch = orch

    def body(self) -> dict:
        try:
            return json.loads(self.request.body.decode() or "{}")
        except ValueError:
            raise P.ProjectError("The request body is not JSON.")

    async def run(self, fn, *args):
        """``fn(orch, *args)`` off the IOLoop; a refusal → 400 {error}."""
        if not self.ensure_auth():
            return
        try:
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
            path = P.thumb_path(self.orch, name)
        except P.ProjectError as e:
            raise tornado.web.HTTPError(404, reason=str(e))
        if not os.path.isfile(path):
            raise tornado.web.HTTPError(404)
        self.set_header("Content-Type", "image/png")
        self.set_header("Cache-Control", "no-cache")
        with open(path, "rb") as f:
            self.write(f.read())

    async def post(self, name):
        b = self.body()
        await self.run(P.save_thumb, name, b.get("image") or b.get("data_url") or "")


class ProjectActionHandler(_Base):
    async def post(self, name, action):
        b = self.body()
        if action == "rename":
            await self.run(P.rename_project, name, str(b.get("name") or ""))
        elif action == "copy":
            await self.run(P.copy_project, name, str(b.get("name") or ""))
        elif action == "delete":
            await self.run(P.delete_project, name, str(b.get("confirm") or ""))
        else:
            raise tornado.web.HTTPError(404)


class NewProjectHandler(_Base):
    async def post(self):
        b = self.body()
        await self.run(P.new_project, str(b.get("chassis") or ""), str(b.get("name") or ""))


class ImportHandler(_Base):
    async def post(self, kind):
        if kind == "zip":
            files = self.request.files.get("file") or []
            if not files:
                self.set_status(400)
                self.write({"error": "No zip file uploaded."})
                return
            f = files[0]
            name = self.get_body_argument("name", "")
            await self.run(P.import_zip, f["filename"], f["body"], name)
            return
        b = self.body()
        if kind == "git":
            await self.run(P.import_git, str(b.get("url") or ""), str(b.get("name") or ""))
        elif kind == "path":
            await self.run(P.import_path, str(b.get("path") or ""), str(b.get("name") or ""))
        else:
            raise tornado.web.HTTPError(404)


def home_routes(orch, version: str = ""):
    """The main page's routes, for gui/server.py make_app()."""
    o = dict(orch=orch)
    return [
        (r"/", HomePageHandler, dict(version=version)),
        (r"/home/api/projects", ProjectsListHandler, o),
        (r"/home/api/projects/([^/]+)/thumb", ProjectThumbHandler, o),
        (r"/home/api/projects/([^/]+)/(rename|copy|delete)", ProjectActionHandler, o),
        (r"/home/api/new", NewProjectHandler, o),
        (r"/home/api/import/(git|zip|path)", ImportHandler, o),
        (r"/home/(.*)", NoCacheStaticFileHandler, {"path": WEB_DIR}),
    ]
