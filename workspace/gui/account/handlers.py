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
import inspect
import json

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


async def _off_loop(fn, *a):
    return await tornado.ioloop.IOLoop.current().run_in_executor(None, fn, *a)


class _JsonHandler(tornado.web.RequestHandler):
    def set_default_headers(self):
        self.set_header("Cache-Control", "no-store")

    def body(self):
        try:
            return json.loads(self.request.body or b"{}") or {}
        except ValueError:
            return {}


class StatusHandler(_JsonHandler):
    async def get(self):
        force = self.get_argument("refresh", "") in ("1", "true")
        self.write(await _off_loop(account.status, force))


class LoginStartHandler(_JsonHandler):
    async def post(self):
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
    return [
        (r"/account/api/status", StatusHandler),
        (r"/account/api/login/start", LoginStartHandler),
        (r"/account/api/login/poll", LoginPollHandler),
        (r"/account/api/logout", LogoutHandler),
        (r"/account/api/components", ComponentsHandler),
    ]
