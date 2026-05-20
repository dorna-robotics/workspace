"""Orchestrator — owns the workspace registry and lifecycle.

Manages multiple workspaces as local OS processes OR remote
orchestrator proxies. Single source of truth for:

  * Add / remove / list workspaces (persisted in
    ``ORCH_REG_PATH`` JSON registry, loaded on startup).
  * Local launch / kill (Popen with the timestamped log pump from
    ``workspace_info``).
  * Remote proxy: forwards cmd/status/logs to the registered node.
  * Run-outcome bookkeeping: ``_record_last_run`` writes
    ``<project_dir>/status/<name>.last_run.json`` whenever a run ends,
    so the dashboard card can show "Last run ✓ 14:10".

WS subscriber lifecycle (``_start_status_subscriber`` /
``_stop_status_subscriber`` in ``websockets``) is wired via deferred
imports inside the launch / stop methods — that's the only
intermodule cycle and keeping it deferred makes orchestrator.py
importable on its own for unit tests that don't need WS.

Note on device-health monitoring: it's intentionally per-workspace
(each workspace process subscribes to its own broker and exposes
its own /devices endpoint). The admin doesn't run a lab-wide MQTT
subscriber because workspaces can live on different systems with
different brokers — a single admin-side subscriber would only ever
see one of them.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import platform
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Thread
from typing import Dict, List, Optional

import requests

from gui.orchestrator.workspace_info import (
    MAX_LOG_BYTES,
    WorkspaceInfo,
    _log_pump,
    _now_str,
    _free_port,
    _truncate_log_if_needed,
)


# Shared thread pools used by both the orchestrator's command paths and
# the websockets module's broadcast loop. Lives here so handlers and
# websockets can both import without circularity.
_status_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="orch-status")
_cmd_pool    = ThreadPoolExecutor(max_workers=4, thread_name_prefix="orch-cmd")

# Workspace registry persistence.
REG_PATH = os.environ.get("ORCH_REG_PATH", "/tmp/orchestrator_registry.json")

# Auth token. ``""`` (default) disables auth entirely. When set, every
# write endpoint requires an ``X-Orch-Token`` header that matches.
# Defined here so both orchestrator (proxy headers) and handlers (auth
# check) can read it without circular imports.
ORCH_TOKEN = os.environ.get("ORCH_TOKEN", "").strip()


class Orchestrator:
    """Manages multiple workspaces as local OS processes OR remote orchestrator proxies."""

    def __init__(self, port: int = 5000):
        self.workspaces: Dict[str, WorkspaceInfo] = {}
        self._orch_port = port

    # ---------------- Persistence ----------------

    def save_registry(self) -> None:
        data = {
            "version": 1,
            "saved_at": time.time(),
            "workspaces": [ws.to_dict() for ws in self.workspaces.values()],
        }
        tmp = REG_PATH + ".tmp"
        os.makedirs(os.path.dirname(REG_PATH) or ".", exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, REG_PATH)

    def load_registry(self) -> None:
        if not os.path.isfile(REG_PATH):
            return
        try:
            with open(REG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            arr = data.get("workspaces", [])
            if not isinstance(arr, list):
                return
            for item in arr:
                try:
                    self.add_workspace(
                        name=item["name"],
                        path_to_file=item.get("path_to_file", ""),
                        port=int(item.get("port", 0)),
                        node_url=item.get("node_url") or None,
                        label=item.get("label", "") or "",
                        sync_remote=False,   # IMPORTANT: don't re-add remotely on startup
                        persist=False,       # we'll persist once after bulk load
                    )
                except Exception:
                    # keep loading others
                    pass
            self.save_registry()
        except Exception:
            pass

    # ---------------- Workspace management ----------------

    def add_workspace(
        self,
        name: str,
        path_to_file: str,
        port: int,
        node_url: Optional[str] = None,
        label: str = "",
        sync_remote: bool = True,
        persist: bool = True,
    ) -> None:
        if name in self.workspaces:
            raise ValueError(f"Workspace {name} already exists.")

        ws = WorkspaceInfo(name=name, path_to_file=path_to_file, port=int(port), node_url=node_url, label=label)

        # Local workspace must have a valid file path
        if not ws.is_remote():
            if not os.path.isfile(path_to_file):
                raise FileNotFoundError(f"{path_to_file} does not exist.")

        # For remote, optionally add the workspace on the remote orchestrator too
        if ws.is_remote() and sync_remote:
            payload = {"name": name, "path_to_file": path_to_file, "port": int(port)}
            if label:
                payload["label"] = label
            try:
                hdrs = {}
                if ORCH_TOKEN:
                    hdrs["X-Orch-Token"] = ORCH_TOKEN
                r = requests.post(f"{ws.node_url}/add_workspace", json=payload, timeout=6, headers=hdrs)
                r.raise_for_status()
            except Exception as e:
                raise RuntimeError(f"Failed to add workspace on remote node {ws.node_url}: {e}")

        self.workspaces[name] = ws
        if persist:
            self.save_registry()

    def remove_workspace(self, name: str) -> Dict:
        if name not in self.workspaces:
            raise ValueError(f"Unknown workspace: {name}")
        ws = self.workspaces[name]
        # Safety: refuse removing launched/running workspace
        if not ws.is_remote() and self.is_launched(name):
            raise RuntimeError("Refuse to remove: workspace is launched/running. Kill it first.")
        # Remote: you may still want to keep it registered remotely; we won't delete remotely here.
        del self.workspaces[name]
        self.save_registry()
        return {"status": "ok", "removed": True, "name": name}

    def list_workspaces(self) -> List[Dict]:
        return [ws.to_dict() for ws in self.workspaces.values()]

    def is_launched(self, name: str) -> bool:
        ws = self.workspaces[name]
        if ws.is_remote():
            return False
        return ws.process is not None and ws.process.poll() is None

    # ---------------- Local launch helpers ----------------

    def start_workspace_process(self, name: str) -> None:
        """Launch the OS process (LAUNCH state).
        Does NOT start runtime workflow (no motion).
        Always runs with sudo."""
        ws = self.workspaces[name]
        if ws.is_remote():
            raise RuntimeError("start_workspace_process called on remote workspace (bug).")

        if self.is_launched(name):
            return

        # Reap zombies: a previous workspace process can leave its port
        # held even after our ``ws.process`` handle died (segfault, hard
        # kill, parent exit before child cleanup, etc.). Without this
        # check the next launch hits ``OSError [Errno 98] Address
        # already in use`` and the user has to ``lsof | kill`` by hand.
        # We kill anything currently bound to ws.port before spawning.
        _free_port(ws.port)

        # Fresh start — clear any stale uploads from a previous run
        ws.clear_uploads()

        # ``-u`` forces unbuffered stdout/stderr in the spawned Python.
        # Without it, prints from the workspace process are block-buffered
        # (4-8KB) when redirected to a file, so low-traffic logs never
        # reach disk until the process exits — making the LOGS panel
        # appear empty even when the process is happily running and
        # printing. ``-u`` flushes per write, matching tty behavior.
        if platform.system() == "Windows":
            cmd = [sys.executable, "-u", ws.path_to_file, "--port", str(ws.port)]
        else:
            cmd = ["sudo", "python3", "-u", ws.path_to_file, "--port", str(ws.port)]

        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"  # belt-and-suspenders for transitive children

        # Make sure <project_dir>/status/ exists before opening the log.
        ws.ensure_status_dir()

        # log cap + append marker
        _truncate_log_if_needed(ws.log_path, MAX_LOG_BYTES)

        log_f = open(ws.log_path, "a", buffering=1, encoding="utf-8")
        log_f.write(f"\n[{_now_str()}] --- LAUNCH cmd={cmd} port={ws.port} ---\n")
        log_f.flush()

        # PIPE + daemon thread so we can prepend a wall-clock timestamp
        # to every line. Direct ``stdout=log_f`` is faster (kernel pipes
        # straight to disk) but gives no place to inject per-line
        # metadata. The pump thread is read()-blocked between lines so
        # CPU cost is negligible. text=True + bufsize=1 makes Popen
        # yield decoded strings line-by-line; errors stay merged with
        # stdout so tracebacks are timestamped alongside normal output.
        ws.process = subprocess.Popen(
            cmd,
            cwd=os.path.dirname(ws.path_to_file),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,           # line-buffered text mode
            errors="replace",    # malformed UTF-8 won't kill the pump
        )
        ws._log_f = log_f
        ws._log_thread = Thread(
            target=_log_pump,
            args=(ws.process, log_f),
            name=f"log-pump-{ws.name}",
            daemon=True,
        )
        ws._log_thread.start()

        # ``started_at`` / ``finished_at`` are per-RUN, not per-process.
        # Stay None until the first IDLE → RUNNING transition; the
        # broadcast loop handles them. Reset here so the previous
        # process's last run doesn't bleed into a fresh Launch.
        ws.started_at = None
        ws.finished_at = None
        ws.last_error = None

        # Open a WS subscriber to the workspace's /ws/status — gives us
        # push-based state updates instead of polling at 2 sec cadence.
        # Every push triggers broadcast_status so the dashboard cards
        # and per-project pages reflect state changes within ms.
        # Deferred import: websockets imports orchestrator (this
        # module) for type hints; calling at function-call time
        # avoids the circular at module-load time.
        from gui.orchestrator.websockets import _start_status_subscriber
        _start_status_subscriber(self, name)

    def wait_until_ready(self, name: str, timeout: float = 8.0) -> bool:
        """Wait until workspace responds on /healthz (LOCAL only)."""
        ws = self.workspaces[name]
        if ws.is_remote():
            return True

        url = f"http://127.0.0.1:{ws.port}/healthz"
        t0 = time.time()
        while time.time() - t0 < timeout:
            if ws.process is not None and ws.process.poll() is not None:
                return False
            try:
                r = requests.get(url, timeout=0.5)
                if r.status_code == 200:
                    return True
            except Exception:
                pass
            time.sleep(0.1)
        return False

    # ---------------- Remote orchestrator proxy helpers ----------------

    def _orch_url(self, ws: WorkspaceInfo, path: str) -> str:
        assert ws.node_url
        return f"{ws.node_url}{path}"

    def _auth_headers(self) -> Dict[str, str]:
        return {"X-Orch-Token": ORCH_TOKEN} if ORCH_TOKEN else {}

    def _proxy_cmd_to_node(self, ws: WorkspaceInfo, cmd: str, kwargs: Optional[Dict] = None):
        url = self._orch_url(ws, f"/workspace/{requests.utils.quote(ws.name)}/cmd")
        payload = {"cmd": cmd}
        if kwargs:
            payload["kwargs"] = kwargs
        r = requests.post(url, json=payload, timeout=10, headers=self._auth_headers())
        r.raise_for_status()
        return r.json()

    def _proxy_status_from_node(self, ws: WorkspaceInfo):
        url = self._orch_url(ws, f"/workspace/{requests.utils.quote(ws.name)}/status")
        r = requests.get(url, timeout=8, headers=self._auth_headers())
        r.raise_for_status()
        return r.json()

    def _proxy_logs_from_node(self, ws: WorkspaceInfo, tail: int = 200):
        url = self._orch_url(ws, f"/workspace/{requests.utils.quote(ws.name)}/logs?tail={int(tail)}")
        r = requests.get(url, timeout=8, headers=self._auth_headers())
        r.raise_for_status()
        return r.json()

    # ---------------- Public Commands ----------------

    def launch_workspace(self, name: str):
        ws = self.workspaces[name]

        if ws.is_remote():
            out = self._proxy_cmd_to_node(ws, "launch")
            out["_orch"] = {"node_url": ws.node_url, "mode": "remote"}
            return out

        if self.is_launched(name):
            ready = self.wait_until_ready(name, timeout=1.0)
            return {
                "status": "ok",
                "launched": True,
                "ready": bool(ready),
                "port": ws.port,
                "log": ws.log_path,
                "note": "already running",
                "started_at": ws.started_at,
                "uptime_s": (time.time() - ws.started_at) if ws.started_at else None,
            }

        self.start_workspace_process(name)

        ready = self.wait_until_ready(name, timeout=30.0)
        if not ready:
            ws.last_error = f"Workspace {name} launched but not ready. Check logs: {ws.log_path}"
            raise RuntimeError(ws.last_error)

        return {
            "status": "ok",
            "launched": True,
            "ready": True,
            "port": ws.port,
            "log": ws.log_path,
            "started_at": ws.started_at,
            "uptime_s": (time.time() - ws.started_at) if ws.started_at else None,
        }

    def stop_workspace(self, name: str):
        ws = self.workspaces[name]

        # Cancel the WS status subscriber FIRST so it doesn't keep
        # retrying against a process we're about to kill.
        from gui.orchestrator.websockets import _stop_status_subscriber
        _stop_status_subscriber(name)

        if ws.is_remote():
            return self._proxy_cmd_to_node(ws, "kill")

        if ws.process is None or ws.process.poll() is not None:
            ws.process = None
            if ws.started_at and not ws.finished_at:
                ws.finished_at = time.time()
            ws.close_log()
            ws.clear_uploads()
            return

        ws.process.terminate()
        try:
            ws.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            ws.process.kill()
            ws.process.wait()
        ws.process = None
        ws.finished_at = time.time()

        ws.close_log()
        ws.clear_uploads()

    def relaunch_workspace(self, name: str):
        ws = self.workspaces[name]

        if ws.is_remote():
            out = self._proxy_cmd_to_node(ws, "restart")  # keep cmd name for compatibility
            out["_orch"] = {"node_url": ws.node_url, "mode": "remote"}
            return out

        self.stop_workspace(name)
        return self.launch_workspace(name)

    # ---------------- Runtime commands ----------------

    def _send_runtime_cmd_local(self, ws: WorkspaceInfo, cmd: str, kwargs: Optional[Dict] = None):
        url = f"http://127.0.0.1:{ws.port}/cmd"
        payload = {"cmd": cmd}
        if kwargs:
            payload["kwargs"] = kwargs
        r = requests.post(url, json=payload, timeout=4)
        r.raise_for_status()
        return r.json()

    def start_runtime(self, name: str, kwargs: Optional[Dict] = None):
        ws = self.workspaces[name]
        if ws.is_remote():
            return self._proxy_cmd_to_node(ws, "start", kwargs=kwargs)

        if not self.is_launched(name):
            raise RuntimeError(f"Workspace {name} is not launched. Send cmd=launch first.")
        if not self.wait_until_ready(name, timeout=2.0):
            raise RuntimeError(f"Workspace {name} is launched but not responding. Check logs: {ws.log_path}")

        # Build effective kwargs in priority order so an operator
        # who set batch_size=10 once doesn't lose it on the second
        # Start click (page might send nothing because the in-memory
        # _wsKwargsValues was reset by a refresh). Layers, low to high:
        #   1. launch.yaml schema defaults — fallback when nothing
        #      else has a value.
        #   2. ws.kwargs_values — last operator-set values, persists
        #      across Start clicks within the same workspace process.
        #   3. ``kwargs`` from this call — only present when the page
        #      explicitly sent kwargs (operator hit Set + Start with
        #      the dialog populated).
        # Saved values are ONLY overwritten when (3) is present —
        # otherwise the layer-2 carry-over keeps the previous setting.
        schema = ws.launch_config() or {}
        merged: Dict = {}
        for key, spec in schema.items():
            if isinstance(spec, dict) and "default" in spec:
                merged[key] = spec["default"]
        if ws.kwargs_values:
            merged.update(ws.kwargs_values)
        if kwargs:
            merged.update(kwargs)
            ws.kwargs_values = dict(merged)

        return self._send_runtime_cmd_local(ws, "start", kwargs=merged or None)

    def pause_runtime(self, name: str):
        ws = self.workspaces[name]
        if ws.is_remote():
            return self._proxy_cmd_to_node(ws, "pause")
        if not self.is_launched(name):
            raise RuntimeError(f"Workspace {name} is not launched.")
        return self._send_runtime_cmd_local(ws, "pause")

    def resume_runtime(self, name: str):
        ws = self.workspaces[name]
        if ws.is_remote():
            return self._proxy_cmd_to_node(ws, "resume")
        if not self.is_launched(name):
            raise RuntimeError(f"Workspace {name} is not launched.")
        return self._send_runtime_cmd_local(ws, "resume")

    def park_runtime(self, name: str):
        ws = self.workspaces[name]
        if ws.is_remote():
            return self._proxy_cmd_to_node(ws, "park")
        if not self.is_launched(name):
            raise RuntimeError(f"Workspace {name} is not launched.")
        return self._send_runtime_cmd_local(ws, "park")

    def get_status(self, name: str) -> Dict:
        ws = self.workspaces[name]

        if ws.is_remote():
            try:
                out = self._proxy_status_from_node(ws)
                out["_orch"] = {"node_url": ws.node_url, "mode": "remote"}
                return out
            except Exception as e:
                ws.last_error = f"Remote status failed: {e}"
                return {
                    "state": "REMOTE_OFFLINE",
                    "last_error": ws.last_error,
                    "_orch": {"node_url": ws.node_url, "mode": "remote"},
                }

        if not self.is_launched(name):
            # Frozen uptime: show how long it ran (started → finished)
            frozen_uptime = (ws.finished_at - ws.started_at) if (ws.started_at and ws.finished_at) else None
            return {
                "state": "NOT_LAUNCHED",
                "last_error": ws.last_error,
                "port": ws.port,
                "log": ws.log_path,
                "started_at": ws.started_at,
                "uptime_s": frozen_uptime,
                "_orch": {"launched": False, "port": ws.port, "log": ws.log_path, "mode": "local"},
            }

        # Fallback uptime, computed from the orchestrator-side cache —
        # used only when the workspace's HTTP /status is unreachable
        # (LAUNCHED_NOT_READY transient states). The success path below
        # overwrites with values from the runtime itself.
        if ws.started_at:
            if ws.finished_at and ws.finished_at >= ws.started_at:
                fallback_uptime = ws.finished_at - ws.started_at
            else:
                fallback_uptime = time.time() - ws.started_at
        else:
            fallback_uptime = None

        url = f"http://127.0.0.1:{ws.port}/status"
        try:
            r = requests.get(url, timeout=4)
            r.raise_for_status()
            out = r.json()
            # Per-run timing comes from the runtime itself (set on
            # RTState transitions). Single source of truth — no race
            # with our polling loop. Cache the values on WorkspaceInfo
            # so when the process dies the NOT_LAUNCHED branch can
            # show the last known frozen run.
            run_started = out.get("run_started_at")
            run_finished = out.get("run_finished_at")
            if run_started:
                ws.started_at = run_started
            if run_finished:
                ws.finished_at = run_finished
            elif run_started and not run_finished:
                # Run is in flight or paused — clear any prior end.
                ws.finished_at = None
            # Up timer: live while the run is in flight, frozen at
            # the run-end moment when finished_at is set.
            if run_started:
                if run_finished and run_finished >= run_started:
                    out["uptime_s"] = run_finished - run_started
                else:
                    out["uptime_s"] = time.time() - run_started
            else:
                out["uptime_s"] = None
            out["started_at"] = run_started
            out["_orch"] = {"launched": True, "port": ws.port, "log": ws.log_path, "mode": "local"}
            ws.last_error = out.get("last_error") or None
            out["last_error"] = ws.last_error
            return out
        except requests.exceptions.ConnectionError:
            # Process is running but HTTP server not ready yet — normal during startup
            return {
                "state": "LAUNCHED_NOT_READY",
                "last_error": ws.last_error,
                "port": ws.port,
                "log": ws.log_path,
                "started_at": ws.started_at,
                "uptime_s": fallback_uptime,
                "_orch": {"launched": True, "port": ws.port, "log": ws.log_path, "mode": "local"},
            }
        except Exception as e:
            ws.last_error = f"Failed to get status: {e}"
            return {
                "state": "LAUNCHED_NOT_READY",
                "last_error": ws.last_error,
                "port": ws.port,
                "log": ws.log_path,
                "started_at": ws.started_at,
                "uptime_s": fallback_uptime,
                "_orch": {"launched": True, "port": ws.port, "log": ws.log_path, "mode": "local"},
            }

    def get_logs(self, name: str, tail: int = 200) -> Dict:
        ws = self.workspaces[name]

        if ws.is_remote():
            return self._proxy_logs_from_node(ws, tail=tail)

        if not os.path.isfile(ws.log_path):
            return {"text": ""}

        with open(ws.log_path, "r", errors="replace") as f:
            lines = f.readlines()[-int(tail):]
        return {"text": "".join(lines)}
