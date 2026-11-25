"""A stand-in for the Dorna account server, for local tests (never the real API).

    python3 workspace/gui/ai/tests/fake_account_server.py [port=8132]

Device login: /s/device/start, /s/device/token (pending once, then ok),
bearer /s/device/api/me|components|logout, and /s/device/api/ai/v1/messages
streaming a canned turn: a tool_use (list_projects) first, then — once the
tool result comes back — a short text reply. Every AI request is logged to
/fake/requests (the headers that matter and the body, image data replaced by
its size, plus how many images the request carried) for the test to check.

A test can script the model instead: POST /fake/script {"turns": [{"text"?,
"tools"?: [{"name", "input"}]}, …]} — each request takes the next turn: its
text (``{result}`` = the last tool result's text), then its tool calls.
"""
import asyncio
import json
import sys

import tornado.ioloop
import tornado.web

TOKEN = "fake-token-123"
STATE = {"polls": 0, "approved": False, "requests": [], "revoked": False, "script": []}


def _images(x):
    """(how many image blocks, a copy with their data replaced by its size)."""
    if isinstance(x, list):
        n, out = 0, []
        for v in x:
            k, c = _images(v)
            n, out = n + k, out + [c]
        return n, out
    if isinstance(x, dict):
        if x.get("type") == "image" and isinstance(x.get("source"), dict):
            return 1, {**x, "source": {**x["source"], "data": f"<{len(x['source'].get('data', ''))} b64 chars>"}}
        n, out = 0, {}
        for k, v in x.items():
            m, out[k] = _images(v)
            n += m
        return n, out
    return 0, x


def _result_text(msg):
    for b in msg.get("content") or []:
        if isinstance(b, dict) and b.get("type") == "tool_result":
            c = b.get("content")
            if isinstance(c, list):
                return " ".join(x.get("text", "") for x in c if isinstance(x, dict) and x.get("type") == "text")
            return str(c)
    return ""


def _authed(h):
    ok = h.request.headers.get("Authorization") == f"Bearer {TOKEN}" and not STATE["revoked"]
    if not ok:
        h.set_status(401)
        h.write({"error": "bad token"})
    return ok


class Start(tornado.web.RequestHandler):
    def post(self):
        STATE["polls"] = 0
        self.write({"device_code": "dev-code-xyz", "user_code": "ABCD-1234",
                    "verify_url": f"http://{self.request.host}/gw/device?code=ABCD-1234",
                    "expires_in": 600, "interval": 1})


class Token(tornado.web.RequestHandler):
    def post(self):
        body = json.loads(self.request.body or b"{}")
        if body.get("device_code") != "dev-code-xyz":
            self.set_status(410)
            self.write({"status": "expired"})
            return
        STATE["polls"] += 1
        if STATE["polls"] < 2:
            self.set_status(428)
            self.write({"status": "pending"})
            return
        STATE["revoked"] = False
        self.write({"token": TOKEN, "email": "op@customer.test", "account": "customer", "is_admin": False})


class Me(tornado.web.RequestHandler):
    def get(self):
        if _authed(self):
            self.write({"email": "op@customer.test", "account": "customer", "is_admin": False,
                        "usage": {"unlimited": False, "percent": 42.0}})


class Components(tornado.web.RequestHandler):
    def get(self):
        if _authed(self):
            self.write({"all": False, "types": ["core", "rack_vial_20ml"], "library": []})


class Logout(tornado.web.RequestHandler):
    def post(self):
        if _authed(self):
            STATE["revoked"] = True
            self.write({"ok": True})


def _sse(ev, data):
    return f"event: {ev}\ndata: {json.dumps(data)}\n\n"


class Messages(tornado.web.RequestHandler):
    async def post(self):
        h = self.request.headers
        body = json.loads(self.request.body)
        n_img, logged = _images(body)
        STATE["requests"].append({"authorization": h.get("Authorization"), "x-api-key": h.get("X-Api-Key"),
                                  "anthropic-beta": h.get("Anthropic-Beta"), "path": self.request.path,
                                  "images": n_img, "body": logged})
        if not _authed(self):
            return
        last = body["messages"][-1]
        slow = "slow" in json.dumps(body["messages"][0])       # a long reply, to test cancel
        has_result = isinstance(last.get("content"), list) and any(
            b.get("type") == "tool_result" for b in last["content"])
        self.set_header("Content-Type", "text/event-stream")
        usage = {"input_tokens": 10, "output_tokens": 0}
        self.write(_sse("message_start", {"type": "message_start", "message": {
            "id": "msg_fake", "type": "message", "role": "assistant", "model": body["model"], "content": [],
            "stop_reason": None, "stop_sequence": None, "usage": usage}}))
        if STATE["script"]:
            turn = STATE["script"].pop(0)
            i, stop = 0, "end_turn"
            text = (turn.get("text") or "").replace("{result}", _result_text(last)[:600])
            if text:
                self.write(_sse("content_block_start", {"type": "content_block_start", "index": 0,
                                                        "content_block": {"type": "text", "text": ""}}))
                for k in range(0, len(text), 40):
                    self.write(_sse("content_block_delta", {"type": "content_block_delta", "index": 0,
                                                            "delta": {"type": "text_delta", "text": text[k:k + 40]}}))
                self.write(_sse("content_block_stop", {"type": "content_block_stop", "index": 0}))
                i = 1
            for t in turn.get("tools") or []:
                tid = f"toolu_{len(STATE['requests'])}_{i}"
                self.write(_sse("content_block_start", {"type": "content_block_start", "index": i, "content_block": {
                    "type": "tool_use", "id": tid, "name": t["name"], "input": {}}}))
                self.write(_sse("content_block_delta", {"type": "content_block_delta", "index": i, "delta": {
                    "type": "input_json_delta", "partial_json": json.dumps(t.get("input") or {})}}))
                self.write(_sse("content_block_stop", {"type": "content_block_stop", "index": i}))
                i += 1
                stop = "tool_use"
        elif not has_result:
            self.write(_sse("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {
                "type": "tool_use", "id": "toolu_fake1", "name": "list_projects", "input": {}}}))
            self.write(_sse("content_block_delta", {"type": "content_block_delta", "index": 0,
                                                    "delta": {"type": "input_json_delta", "partial_json": "{}"}}))
            self.write(_sse("content_block_stop", {"type": "content_block_stop", "index": 0}))
            stop = "tool_use"
        else:
            result = last["content"][0].get("content")
            text = f"You have these projects: {result}" + (" and more" * 60 if slow else "")
            self.write(_sse("content_block_start", {"type": "content_block_start", "index": 0,
                                                    "content_block": {"type": "text", "text": ""}}))
            for i in range(0, len(text), 12):
                self.write(_sse("content_block_delta", {"type": "content_block_delta", "index": 0,
                                                        "delta": {"type": "text_delta", "text": text[i:i + 12]}}))
                await self.flush()
                if slow:
                    await asyncio.sleep(0.1)
            self.write(_sse("content_block_stop", {"type": "content_block_stop", "index": 0}))
            stop = "end_turn"
        self.write(_sse("message_delta", {"type": "message_delta", "delta": {"stop_reason": stop, "stop_sequence": None},
                                          "usage": {"output_tokens": 5}}))
        self.write(_sse("message_stop", {"type": "message_stop"}))


class Requests(tornado.web.RequestHandler):
    def get(self):
        self.write({"requests": STATE["requests"]})


class Script(tornado.web.RequestHandler):
    def post(self):
        STATE["script"] = list(json.loads(self.request.body or b"{}").get("turns") or [])
        self.write({"ok": True, "turns": len(STATE["script"])})


def app():
    return tornado.web.Application([
        (r"/s/device/start", Start), (r"/s/device/token", Token),
        (r"/s/device/api/me", Me), (r"/s/device/api/components", Components),
        (r"/s/device/api/logout", Logout), (r"/s/device/api/ai/v1/messages", Messages),
        (r"/fake/requests", Requests), (r"/fake/script", Script),
    ])


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8132
    app().listen(port, address="127.0.0.1")
    print(f"fake account server on 127.0.0.1:{port}")
    tornado.ioloop.IOLoop.current().start()
