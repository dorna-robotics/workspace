"""Orchestrator WebSocket handlers + status broadcast loop.

Three roles:

  * **WorkspaceLogsWebSocket** — one workspace's console, live: a
    reader of its ``LogStream`` (workspace_info.py), each line pushed
    the moment the log pump writes it.

  * **StatusWebSocket** — orchestrator-level broadcast of all workspace
    statuses. Dashboard subscribes here for sub-100 ms updates instead
    of polling ``/workspaces/status``.

  * **broadcast_status / _subscribe_workspace_status** — the engine
    that ties everything together. ``_subscribe_workspace_status``
    holds a per-workspace WS to ``/ws/status`` of the workspace's own
    runtime; every message there triggers a fresh ``broadcast_status``
    so all dashboard clients see the change immediately.

Module state lives here (not in the Orchestrator class) because it's
fundamentally about WS clients + the cross-workspace cache:

  * ``_ws_clients`` — connected dashboard WS clients
  * ``_ws_last_snapshot`` — last broadcast JSON, dedup
  * ``_ws_prev_states`` — per-workspace last seen runtime state
    (powers the auto-kill RUNNING→IDLE detection)
  * ``_workspace_status_subscribers`` — per-workspace subscriber tasks
"""
from __future__ import annotations

import asyncio
import json

import tornado.ioloop
import tornado.web
import tornado.websocket

from gui.orchestrator.orchestrator import (
    Orchestrator,
    _cmd_pool,
    _status_pool,
)


# ── Module state ─────────────────────────────────────────────────────
_ws_clients: set = set()
_ws_last_snapshot: str = ""    # JSON of last broadcast (skip if unchanged)
_ws_prev_states: dict = {}     # name → last known runtime state

# Per-workspace WS subscriber tasks. Each subscriber holds an open
# WebSocket to the workspace's own /ws/status and triggers
# broadcast_status() on every push, so dashboard cards + project pages
# react to state transitions in milliseconds (no 2-sec poll lag).
_workspace_status_subscribers: dict = {}


# ── Per-workspace log tail ───────────────────────────────────────────

class WorkspaceLogsWebSocket(tornado.websocket.WebSocketHandler):
    """WS /orchestrator/ws/logs/<name>?since=tail|boot|run — a workspace's
    console, live: a reader of its LogStream from that mark (the Logs
    panel reads the tail, the launch card this launch or this run).

    Messages:
      ``{"type":"snapshot","text":..., "since_s":..., "last_s":...}``  on open
      ``{"type":"append","text":"<line>"}``   each line as it is written
      ``{"type":"reset","text":"<line>"}``    the section started over (the
          next launch / run, or Clear): drop what you have, start from text
    ``since_s`` / ``last_s``: seconds since the mark's line and since the
    newest line, on the orchestrator's clock (None when there is none).
    """

    def check_origin(self, origin):
        return True

    def initialize(self, orch: Orchestrator):
        self.orch = orch
        self._stream = None

    def open(self, name):
        ws_info = self.orch.workspaces.get(name)
        since = self.get_argument("since", "tail")
        if ws_info is None or since not in ws_info.log.MARKS:
            self.close()
            return
        loop = tornado.ioloop.IOLoop.current()
        # Called on the pump's thread: only hand the line to the IOLoop.
        self._reader = lambda kind, text: loop.add_callback(self._send, kind, text)
        self._stream = ws_info.log
        snap = self._stream.subscribe(self._reader, since)
        self._send("snapshot", **snap)

    def _send(self, kind, text, **extra):
        if self.ws_connection is None:
            return
        self.write_message(json.dumps({"type": kind, "text": text, **extra}))

    def on_close(self):
        if self._stream is not None:
            self._stream.unsubscribe(self._reader)
            self._stream = None


# ── Per-workspace runtime status subscriber ──────────────────────────

async def _subscribe_workspace_status(orch: Orchestrator, name: str):
    """Long-lived task: subscribe to ws://localhost:<port>/ws/status of
    the named workspace and call broadcast_status() on every message.

    Reconnects with exponential backoff if the connection drops (e.g.
    workspace is restarting). Exits if the workspace is removed or the
    process is gone permanently.
    """
    backoff = 0.5
    while True:
        ws_info = orch.workspaces.get(name)
        if ws_info is None:
            return  # workspace was removed
        if ws_info.is_remote():
            return  # remote workspaces broadcast via their own orchestrator
        if not orch.is_launched(name):
            # process not running yet — wait and retry. Killed-on-purpose
            # is handled by _stop_status_subscriber cancelling this task.
            await asyncio.sleep(0.5)
            continue
        url = f"ws://127.0.0.1:{ws_info.port}/ws/status"
        try:
            client = await tornado.websocket.websocket_connect(
                url, connect_timeout=3,
            )
        except Exception:
            await asyncio.sleep(backoff)
            backoff = min(backoff * 1.5, 5.0)
            continue
        backoff = 0.5
        try:
            while True:
                msg = await client.read_message()
                if msg is None:
                    break  # connection closed cleanly
                # Workspace pushed a fresh status — broadcast to all
                # orchestrator-level WS clients (dashboard + per-project).
                try:
                    await broadcast_status(orch)
                except Exception:
                    pass
        except Exception:
            pass
        finally:
            try:
                client.close()
            except Exception:
                pass
        # Connection dropped — workspace might be restarting. Wait briefly
        # and retry from the top of the loop.
        await asyncio.sleep(0.5)


def _start_status_subscriber(orch: Orchestrator, name: str) -> None:
    """Start a per-workspace status subscriber on the orchestrator's
    IOLoop — callable from any thread (a Launch runs in _cmd_pool).
    Idempotent — replaces any existing task for the name."""
    def start():
        existing = _workspace_status_subscribers.pop(name, None)
        if existing is not None and not existing.done():
            existing.cancel()
        _workspace_status_subscribers[name] = asyncio.ensure_future(
            _subscribe_workspace_status(orch, name))
    orch.loop.add_callback(start)


def _stop_status_subscriber(orch: Orchestrator, name: str) -> None:
    """Cancel and remove a workspace's status subscriber task — on the
    orchestrator's IOLoop, from any thread."""
    def stop():
        task = _workspace_status_subscribers.pop(name, None)
        if task is not None and not task.done():
            task.cancel()
    orch.loop.add_callback(stop)


# ── Orchestrator-level status WS ─────────────────────────────────────

class StatusWebSocket(tornado.websocket.WebSocketHandler):
    """Clients connect here for instant status pushes instead of polling."""
    def initialize(self, orch: Orchestrator):
        self.orch = orch

    def check_origin(self, origin):
        return True  # allow any origin (same LAN)

    def open(self):
        _ws_clients.add(self)
        # Send current status immediately on connect
        asyncio.ensure_future(self._send_current())

    async def _send_current(self):
        try:
            loop = asyncio.get_running_loop()
            names = list(self.orch.workspaces.keys())
            async def fetch_one(n):
                try:
                    return n, await loop.run_in_executor(_status_pool, self.orch.get_status, n)
                except Exception as e:
                    return n, {"state": "OFFLINE", "last_error": str(e)}
            pairs = await asyncio.gather(*[fetch_one(n) for n in names])
            statuses = dict(pairs)
            msg = json.dumps({"type": "status", "statuses": statuses})
            if self.ws_connection:
                await self.write_message(msg)
        except Exception:
            pass

    def on_message(self, message):
        pass  # clients don't send anything

    def on_close(self):
        _ws_clients.discard(self)


# ── Broadcast loop ───────────────────────────────────────────────────

async def broadcast_status(orch: Orchestrator):
    """Fetch all statuses and push to every connected WS client."""
    global _ws_last_snapshot
    try:
        loop = asyncio.get_running_loop()
        names = list(orch.workspaces.keys())
        async def fetch_one(n):
            try:
                return n, await loop.run_in_executor(_status_pool, orch.get_status, n)
            except Exception as e:
                return n, {"state": "OFFLINE", "last_error": str(e)}
        pairs = await asyncio.gather(*[fetch_one(n) for n in names])
        statuses = dict(pairs)

        # No auto-kill. When the workflow finishes (RUNNING → IDLE) the
        # workspace process stays alive in IDLE state — its 3D viewer,
        # steps panel, progress bar, and devices state all keep
        # rendering via the live ``/status`` and ``/ws/*`` endpoints
        # the workspace serves. Operator clicks Start to re-run, or
        # Kill+Launch to reload code. Live data is the source of truth.
        # Per-run timing (started_at / finished_at) is now tracked by
        # the runtime itself and reported in /status — no race with
        # this loop, so we just track prev state for any future logic
        # that needs it.
        for name, st in statuses.items():
            cur = (st.get("state") or "").upper()
            _ws_prev_states[name] = cur

        # No step caching here. The workspace process stays alive after
        # a run finishes, so its ``/status`` is always the source of
        # truth. When the runtime clears its step list at the start of
        # the next run, we want the dashboard to see that empty state
        # immediately — caching the previous run's steps would replay
        # them on top of a fresh run. (When the process is genuinely
        # dead → NOT_LAUNCHED → no step → panel shows "No steps yet",
        # which is the correct cleared view.)

        if not _ws_clients:
            return
        msg = json.dumps({"type": "status", "statuses": statuses}, sort_keys=True)
        if msg == _ws_last_snapshot:
            return  # nothing changed, skip
        _ws_last_snapshot = msg

        async def _safe_send(c, m):
            try:
                await c.write_message(m)
            except Exception:
                _ws_clients.discard(c)

        tasks = []
        for c in list(_ws_clients):
            if not c.ws_connection:
                _ws_clients.discard(c)
            else:
                tasks.append(asyncio.ensure_future(_safe_send(c, msg)))
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
    except Exception:
        pass


async def _ws_poll_loop(orch: Orchestrator):
    """Server-side periodic broadcast — catches external state changes."""
    while True:
        await asyncio.sleep(2)
        await broadcast_status(orch)
