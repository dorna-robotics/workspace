"""The on-device assistant's routes — all 401 while this unit is offline.

    POST /ai/api/chat     {session?, text, context?: {page, project?, tab?}} → NDJSON events
    GET  /ai/api/attach   ?id=&from=   → a running turn's events from `from` on (after a reload)
    POST /ai/api/cancel   {session}
    GET  /ai/api/session  ?id=         → {id, project, transcript, busy, live_seq} (a new one if unknown)
    POST /ai/api/session               → a fresh conversation
"""
import json

import tornado.ioloop
import tornado.iostream
import tornado.web

from gui.account import account, login_required
from gui.account.handlers import guard_post, same_origin, token_ok

from . import agent, tools


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
            await agent.run_turn(s, text, emit, ctx)
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


def routes(orch):
    setup(orch)
    return [
        (r"/ai/api/chat", ChatHandler),
        (r"/ai/api/attach", AttachHandler),
        (r"/ai/api/cancel", CancelHandler),
        (r"/ai/api/session", SessionHandler),
    ]
