"""The account's local routes (/account/api/*) and the login gate.

    GET  /account/api/status        {logged_in, email?, account?, is_admin?, usage?, server, reachable?}
    POST /account/api/login/start   {user_code, verify_url, expires_in}
    POST /account/api/login/poll    {status: pending|ok|expired|denied}
    POST /account/api/logout        {ok}
    GET  /account/api/components    {all, types, library}

``login_required`` wraps a handler method: 401 {error: "login required"}
when this unit is offline (Developer mode and the AI use it).
"""
import functools
import hmac
import inspect
import json
import os
from urllib.parse import urlsplit

import tornado.ioloop
import tornado.web

from .client import AccountError, account


def _deny(handler):
    handler.set_status(401)
    handler.set_header("Content-Type", "application/json")
    handler.write({"error": "login required"})


def login_required(method):
    if inspect.iscoroutinefunction(method):
        @functools.wraps(method)
        async def wrapper(self, *a, **kw):
            if not account.logged_in():
                return _deny(self)
            return await method(self, *a, **kw)
    else:
        @functools.wraps(method)
        def wrapper(self, *a, **kw):
            if not account.logged_in():
                return _deny(self)
            return method(self, *a, **kw)
    return wrapper


def _orch_token():
    try:
        from gui.orchestrator.orchestrator import ORCH_TOKEN
    except Exception:                       # noqa: BLE001 — standalone tests
        ORCH_TOKEN = os.environ.get("ORCH_TOKEN", "").strip()
    return ORCH_TOKEN


def token_ok(handler):
    """True when ORCH_TOKEN is unset, or the caller sent a matching X-Orch-Token
    (the same rule as the orchestrator's write routes)."""
    want = _orch_token()
    if not want:
        return True
    got = handler.request.headers.get("X-Orch-Token", "")
    return hmac.compare_digest(got.encode(), want.encode())


def same_origin(handler):
    """A cross-site page must not reach these routes: an Origin header, when
    present, must name this host."""
    origin = handler.request.headers.get("Origin")
    if not origin:
        return True
    try:
        o = urlsplit(origin)
    except ValueError:
        return False
    return bool(o.netloc) and o.netloc.lower() == (handler.request.host or "").lower()


def guard_post(handler, need_token=True):
    """Every POST under /account/api and /ai/api: JSON only (a text/plain POST
    needs no CORS preflight), same origin, and the orchestrator token when on.
    Raises HTTPError; returns None when the request may go on."""
    ctype = (handler.request.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    if ctype != "application/json":
        raise tornado.web.HTTPError(415, "Content-Type must be application/json")
    if not same_origin(handler):
        raise tornado.web.HTTPError(403, "cross-origin request refused")
    if need_token and not token_ok(handler):
        raise tornado.web.HTTPError(403, "orchestrator token required")


async def _off_loop(fn, *a):
    return await tornado.ioloop.IOLoop.current().run_in_executor(None, fn, *a)


class _JsonHandler(tornado.web.RequestHandler):
    def set_default_headers(self):
        self.set_header("Cache-Control", "no-store")

    def prepare(self):
        if self.request.method == "POST":
            guard_post(self)
        elif not same_origin(self):
            raise tornado.web.HTTPError(403, "cross-origin request refused")

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
            return {}
        return d if isinstance(d, dict) else {}


class StatusHandler(_JsonHandler):
    async def get(self):
        force = self.get_argument("refresh", "") in ("1", "true")
        self.write(await _off_loop(account.status, force))


class LoginStartHandler(_JsonHandler):
    async def post(self):
        if account.logged_in():
            # switching accounts goes through Log out (which revokes the token)
            self.set_status(409)
            self.write({"error": "already logged in — log out first"})
            return
        try:
            self.write(await _off_loop(account.start_login, self.body().get("device_name")))
        except AccountError as e:
            self.set_status(502)
            self.write({"error": str(e)})


class LoginPollHandler(_JsonHandler):
    async def post(self):
        self.write(await _off_loop(account.poll_login))


class LogoutHandler(_JsonHandler):
    async def post(self):
        self.write(await _off_loop(account.logout))


class ComponentsHandler(_JsonHandler):
    @login_required
    async def get(self):
        self.write(await _off_loop(account.components, self.get_argument("refresh", "") in ("1", "true")))


def routes():
    if account.logged_in():             # the server started logged in: the file sync runs from now
        from .sync import syncer
        syncer().start()
    return [
        (r"/account/api/status", StatusHandler),
        (r"/account/api/login/start", LoginStartHandler),
        (r"/account/api/login/poll", LoginPollHandler),
        (r"/account/api/logout", LogoutHandler),
        (r"/account/api/components", ComponentsHandler),
    ]
