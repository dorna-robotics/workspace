"""The on-device assistant's routes — all 401 while this unit is offline.

    POST /ai/api/chat     {session?, text, context?: {page, project?, tab?}, attachments?: [upload id]} → NDJSON events
    POST /ai/api/upload   ?session=&name=&project=   raw file body (application/octet-stream, ≤ 50 MB)
                          → {ok, session, upload: {id, name, kind, size, project_path?}}
    GET  /ai/api/uploads  ?session=  → {uploads: [...]}
    GET  /ai/api/upload_file ?session=&id=  → an uploaded image (the chat's thumbnails)
    GET  /ai/api/attach   ?id=&from=   → a running turn's events from `from` on (after a reload)
    POST /ai/api/cancel   {session}
    GET  /ai/api/session  ?id=         → {id, project, transcript, busy, live_seq} (a new one if unknown)
    POST /ai/api/session               → a fresh conversation
"""
import json
import os
import tempfile

import tornado.ioloop
import tornado.iostream
import tornado.web

from gui.account import account, login_required
from gui.data_home import data_path
from gui.account.handlers import guard_post, same_origin, token_ok

from . import agent, tools, uploads


def setup(orch):
    """Give the tools the server's Orchestrator (project folders, the port)."""
    tools.ORCH = orch


def _owner():
    d = account._load() or {}
    return d.get("email") or d.get("account") or "device"


class _Json(tornado.web.RequestHandler):
    """The assistant can launch projects and edit code, so every route needs
    what the orchestrator's write routes need (X-Orch-Token when ORCH_TOKEN is
    set), same-origin callers only, and JSON bodies for POSTs."""

    def set_default_headers(self):
        self.set_header("Cache-Control", "no-store")

    def prepare(self):
        if self.request.method == "POST":
            guard_post(self)
        else:
            if not same_origin(self):
                raise tornado.web.HTTPError(403, "cross-origin request refused")
            if not token_ok(self):
                raise tornado.web.HTTPError(403, "orchestrator token required")

    def write_error(self, status_code, **kwargs):
        reason = self._reason
        exc = kwargs.get("exc_info")
        if exc and isinstance(exc[1], tornado.web.HTTPError) and exc[1].log_message:
            reason = exc[1].log_message
        self.set_header("Content-Type", "application/json")
        self.finish({"error": reason})

    def body(self):
        try:
            d = json.loads(self.request.body or b"{}") or {}
        except ValueError:
            raise tornado.web.HTTPError(400, "bad JSON")
        if not isinstance(d, dict):
            raise tornado.web.HTTPError(400, "bad JSON")
        return d


class ChatHandler(_Json):
    @login_required
    async def post(self):
        data = self.body()
        text = (data.get("text") or "").strip()
        att = data.get("attachments") or []
        if not isinstance(att, list) or len(att) > 20 or not all(isinstance(a, str) and len(a) <= 32 for a in att):
            raise tornado.web.HTTPError(400, "attachments: a list of up to 20 upload ids")
        if not text and att:
            text = "(I attached these files.)"
        if not text:
            self.set_status(400)
            self.write({"error": "empty message"})
            return
        s = agent.get_session(data.get("session") or data.get("id"), owner=_owner())
        if s.lock.locked():
            self.set_status(409)
            self.write({"error": "the assistant is still working on the previous message", "session": s.id})
            return
        self.set_header("Content-Type", "application/x-ndjson")
        self.set_header("X-Accel-Buffering", "no")
        gone = False

        async def emit(ev):
            nonlocal gone
            s.publish(ev)
            if gone:
                return
            try:
                self.write(json.dumps(ev) + "\n")
                await self.flush()
            except tornado.iostream.StreamClosedError:
                gone = True                # the page went away; the turn carries on

        async with s.lock:
            s.live = []
            await emit({"t": "session", "id": s.id})
            ctx = data.get("context") if isinstance(data.get("context"), dict) else None
            await agent.run_turn(s, text, emit, ctx, att)
        # the turn used credits: the next status (the usage bar) asks the server again
        await tornado.ioloop.IOLoop.current().run_in_executor(None, lambda: account.status(force=True))


class AttachHandler(_Json):
    @login_required
    async def get(self):
        s = agent.get_session(self.get_argument("id", None), owner=_owner(), create=False)
        self.set_header("Content-Type", "application/x-ndjson")
        if s is None:
            self.write(json.dumps({"t": "done"}) + "\n")
            return
        try:
            i = max(0, int(self.get_argument("from", "0") or 0))
        except ValueError:
            i = 0
        try:
            while True:
                while i < len(s.live):
                    ev = s.live[i]
                    i += 1
                    self.write(json.dumps(ev) + "\n")
                    if ev.get("t") == "done":
                        await self.flush()
                        return
                await self.flush()
                if not s.lock.locked():
                    self.write(json.dumps({"t": "done"}) + "\n")
                    return
                await s.wait_live(5)
        except tornado.iostream.StreamClosedError:
            pass


class CancelHandler(_Json):
    @login_required
    def post(self):
        data = self.body()
        s = agent.get_session(data.get("session") or data.get("id"), owner=_owner(), create=False)
        if s is not None:
            s.cancel = True
        self.write({"ok": True})


class SessionHandler(_Json):
    @login_required
    def get(self):
        self.write(agent.get_session(self.get_argument("id", None), owner=_owner()).public())

    @login_required
    def post(self):
        s = agent.get_session(None, owner=_owner())
        proj = self.body().get("project")
        if proj:
            s.project = str(proj)
            s.save()
        self.write(s.public())


def _project_dir(name):
    """The local folder of a project on this unit, or None."""
    if not name:
        return None
    try:
        return tools.project_dir(str(name))
    except tools.ToolError:
        return None


@tornado.web.stream_request_body
class UploadHandler(tornado.web.RequestHandler):
    """One file, streamed to disk as it arrives (a 50 MB model never sits in
    memory). The body is the raw file — ``application/octet-stream``, which
    a cross-site form cannot send without a CORS preflight — and the same
    rules as the other routes: same origin, the orchestrator token, logged in."""

    def set_default_headers(self):
        self.set_header("Cache-Control", "no-store")

    def write_error(self, status_code, **kwargs):
        reason = self._reason
        exc = kwargs.get("exc_info")
        if exc and isinstance(exc[1], tornado.web.HTTPError) and exc[1].log_message:
            reason = exc[1].log_message
        self.set_header("Content-Type", "application/json")
        self.finish({"error": reason})

    def prepare(self):
        self._tmp = None
        self._n = 0
        ctype = (self.request.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype != "application/octet-stream":
            raise tornado.web.HTTPError(415, "Content-Type must be application/octet-stream")
        if not same_origin(self):
            raise tornado.web.HTTPError(403, "cross-origin request refused")
        if not token_ok(self):
            raise tornado.web.HTTPError(403, "orchestrator token required")
        if not account.logged_in():
            raise tornado.web.HTTPError(401, "login required")
        try:
            length = int(self.request.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length > uploads.MAX_BYTES:
            raise tornado.web.HTTPError(413, f"the file is larger than {uploads.MAX_BYTES // (1024 * 1024)} MB")
        self.request.connection.set_max_body_size(uploads.MAX_BYTES + 1)
        d = os.path.dirname(data_path("ai", "uploads", ".incoming", "x"))
        os.makedirs(d, exist_ok=True)
        fd, self._tmp = tempfile.mkstemp(dir=d, prefix="up_")
        self._f = os.fdopen(fd, "wb")

    def data_received(self, chunk):
        self._n += len(chunk)
        if self._n > uploads.MAX_BYTES:
            raise tornado.web.HTTPError(413, "the file is too large")
        self._f.write(chunk)

    def on_finish(self):
        self._cleanup()

    def on_connection_close(self):
        self._cleanup()

    def _cleanup(self):
        f = getattr(self, "_f", None)
        if f is not None and not f.closed:
            f.close()
        if self._tmp and os.path.exists(self._tmp):
            try:
                os.unlink(self._tmp)
            except OSError:
                pass

    async def post(self):
        self._f.close()
        name = self.get_argument("name", "") or "file"
        s = agent.get_session(self.get_argument("session", None), owner=_owner())
        proj = self.get_argument("project", "") or s.project
        pdir = _project_dir(proj)
        try:
            rec = await tornado.ioloop.IOLoop.current().run_in_executor(
                None, uploads.save, s.id, name, self._tmp, pdir, proj if pdir else None)
        except uploads.UploadError as e:
            raise tornado.web.HTTPError(400, str(e))
        self._tmp = None                       # moved into the store
        self.write({"ok": True, "session": s.id, "upload": uploads.public(rec)})


class UploadsHandler(_Json):
    @login_required
    def get(self):
        s = agent.get_session(self.get_argument("id", None) or self.get_argument("session", None),
                              owner=_owner(), create=False)
        self.write({"uploads": [uploads.public(r) for r in uploads.records(s.id)] if s else []})


class UploadFileHandler(_Json):
    """An uploaded image, for the chat's own thumbnails (images only)."""
    @login_required
    def get(self):
        s = agent.get_session(self.get_argument("session", None), owner=_owner(), create=False)
        rec = uploads.get(s.id, self.get_argument("id", "")) if s else None
        if rec is None or rec["kind"] != "image" or not os.path.isfile(rec["path"]):
            raise tornado.web.HTTPError(404, "no such image")
        self.set_header("Content-Type", rec["mime"])
        self.set_header("Cache-Control", "private, max-age=3600")
        with open(rec["path"], "rb") as f:
            self.finish(f.read())


def routes(orch):
    setup(orch)
    return [
        (r"/ai/api/chat", ChatHandler),
        (r"/ai/api/attach", AttachHandler),
        (r"/ai/api/cancel", CancelHandler),
        (r"/ai/api/session", SessionHandler),
        (r"/ai/api/upload", UploadHandler),
        (r"/ai/api/uploads", UploadsHandler),
        (r"/ai/api/upload_file", UploadFileHandler),
    ]
