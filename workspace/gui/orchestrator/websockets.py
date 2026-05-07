"""Orchestrator WebSocket handlers + status broadcast loop.

Three roles:

  * **WorkspaceLogsWebSocket** — live tail of one workspace's log file.
    Polls the file at 250 ms cadence (cheap on Pi; doesn't depend on
    inotify) and pushes each appended chunk as a JSON message.

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
import os
import time
from typing import Optional

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
    """WS /orchestrator/ws/logs/<name> — live tail of a workspace's log.

    Pushes an initial snapshot of the last ~16 KB on connect, then
    streams every appended chunk as the workspace process writes to
    stdout. Replaces the HTTP /logs?tail polling so the LOGS panel
    updates instantly rather than every 1.5 s.

    Messages:
      ``{"type":"snapshot","text":"<initial tail>"}``  on open
      ``{"type":"append","text":"<new chunk>"}``       per write
    """

    # Polling cadence for file size changes. The orchestrator can't
    # rely on inotify in every deployment, and this is cheap (one
    # os.stat + small read per tick). 250 ms is the sweet spot
    # between perceived latency and CPU.
    POLL_INTERVAL = 0.25

    # How much of the existing log to send on connect. Larger lets
    # operators see context immediately; smaller keeps initial render
    # snappy. 16 KB ≈ 200 lines of typical log output.
    INITIAL_TAIL_BYTES = 16 * 1024

    def check_origin(self, origin):
        return True

    def initialize(self, orch: Orchestrator):
        self.orch = orch
        self._tail_task: Optional[asyncio.Task] = None
        self._name: Optional[str] = None

    def open(self, name):
        if name not in self.orch.workspaces:
            self.close()
            return
        self._name = name
        self._tail_task = asyncio.ensure_future(self._tail_loop())

    def on_close(self):
        if self._tail_task is not None and not self._tail_task.done():
            self._tail_task.cancel()
        self._tail_task = None

    async def _tail_loop(self):
        """Send initial snapshot, then poll the log file for appended
        bytes and push each chunk as a JSON message."""
        ws_info = self.orch.workspaces.get(self._name)
        if ws_info is None:
            return
        path = ws_info.log_path

        pos = 0
        # Initial snapshot: last INITIAL_TAIL_BYTES so the operator
        # immediately sees recent context, not an empty panel.
        if os.path.isfile(path):
            try:
                size = os.path.getsize(path)
                start = max(0, size - self.INITIAL_TAIL_BYTES)
                with open(path, "r", errors="replace") as f:
                    f.seek(start)
                    initial = f.read()
                pos = size
                try:
                    self.write_message(json.dumps({
                        "type": "snapshot", "text": initial,
                    }))
                except Exception:
                    return
            except Exception:
                pos = 0

        # Live tail: poll for new bytes.
        while True:
            try:
                if not self.ws_connection:
                    return
                if os.path.isfile(path):
                    size = os.path.getsize(path)
                    if size < pos:
                        # File rotated / truncated — restart from start.
                        pos = 0
                    if size > pos:
                        with open(path, "r", errors="replace") as f:
                            f.seek(pos)
                            chunk = f.read()
                            pos = f.tell()
                        if chunk:
                            try:
                                self.write_message(json.dumps({
                                    "type": "append", "text": chunk,
                                }))
                            except Exception:
                                return
                await asyncio.sleep(self.POLL_INTERVAL)
            except asyncio.CancelledError:
                return
            except Exception:
                # Best-effort: a transient read error shouldn't kill
                # the tail. Fall through to the sleep + retry path.
                await asyncio.sleep(self.POLL_INTERVAL)


# ── Per-workspace status subscriber ──────────────────────────────────

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
    """Schedule a per-workspace status subscriber on the IO loop. Idempotent
    — replaces any existing task for the name."""
    existing = _workspace_status_subscribers.pop(name, None)
    if existing is not None and not existing.done():
        existing.cancel()
    try:
        loop = asyncio.get_event_loop()
    except Exception:
        return
    task = loop.create_task(_subscribe_workspace_status(orch, name))
    _workspace_status_subscribers[name] = task


def _stop_status_subscriber(name: str) -> None:
    """Cancel and remove a workspace's status subscriber task."""
    task = _workspace_status_subscribers.pop(name, None)
    if task is not None and not task.done():
        task.cancel()


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
