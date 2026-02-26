# workspace/runtime_server.py
import json
from threading import Thread
from typing import Callable, Any, Optional

from workspace.runtime import Runtime
import tornado.ioloop
import tornado.web

class CmdHandler(tornado.web.RequestHandler):
    def initialize(self, rt: Runtime, workflow_fn: Callable[..., Any], workflow_thread_holder: dict, workspace: Any):
        self.rt = rt
        self.workflow_fn = workflow_fn
        self._workflow_thread_holder = workflow_thread_holder
        self.workspace = workspace

    async def post(self):
        try:
            data = json.loads(self.request.body.decode("utf-8"))
            cmd = data.get("cmd", "").lower()
        except Exception:
            self.set_status(400)
            self.write({"error": "Invalid JSON"})
            return

        if cmd == "start":
            self.rt.start()
            th = self._workflow_thread_holder.get("thread")
            if th is None or not th.is_alive():
                # PASS REAL WORKSPACE HERE
                self._workflow_thread_holder["thread"] = self.rt.run_workflow_thread(
                    self.workflow_fn, workspace=self.workspace
                )

        elif cmd == "pause":
            self.rt.pause()
        elif cmd == "resume":
            self.rt.resume()
        elif cmd == "kill":
            self.rt.kill()
        else:
            self.set_status(400)
            self.write({"error": "Unknown cmd"})
            return

        self.write({"status": "ok", "state": self.rt.state})


class StatusHandler(tornado.web.RequestHandler):
    def initialize(self, rt: Runtime):
        self.rt = rt

    async def get(self):
        self.write({"state": self.rt.state, "last_error": self.rt.status.last_error})


class RuntimeServer:
    def __init__(self, runtime: Runtime, workflow_fn: Callable[..., Any], workspace: Any,
                 host: str = "0.0.0.0", port: int = 8000):
        self.rt = runtime
        self.workflow_fn = workflow_fn
        self.workspace = workspace
        self.host = host
        self.port = port
        self._workflow_thread_holder = {}

        self.app = tornado.web.Application([
            (r"/cmd", CmdHandler, dict(
                rt=self.rt,
                workflow_fn=self.workflow_fn,
                workflow_thread_holder=self._workflow_thread_holder,
                workspace=self.workspace,   # <-- add
            )),
            (r"/status", StatusHandler, dict(rt=self.rt)),
        ])

    def run(self):
        self.app.listen(self.port, address=self.host)
        tornado.ioloop.IOLoop.current().start()

    def run_in_thread(self):
        t = Thread(target=self.run, daemon=True)
        t.start()
        return t