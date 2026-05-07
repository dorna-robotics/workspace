"""WorkspaceInfo — per-project metadata + log helpers.

The data layer the orchestrator manages. One ``WorkspaceInfo`` per
registered project. Owns:

  * Identity (name, path_to_file, port, label, optional remote node).
  * Process handle (local) or remote node URL.
  * Log file path under ``<project_dir>/status/workspace.log`` and the
    daemon thread that timestamps every line of stdout/stderr.

Module-level helpers here are intentionally small and stdlib-only:

  * ``_now_str`` — single source of timestamp formatting.
  * ``_truncate_log_if_needed`` — prevents logs from growing without
    bound; runs once per launch.
  * ``_log_pump`` — daemon thread body that pulls Popen lines and
    timestamps them. Hardened to keep draining the pipe even if a
    single line raises.
  * ``_free_port`` — lsof+kill stragglers before a fresh launch.
"""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Dict, Optional

import yaml


MAX_LOG_BYTES = int(os.environ.get("ORCH_MAX_LOG_BYTES", str(2 * 1024 * 1024)))  # 2MB


def _now_str() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _truncate_log_if_needed(path: str, max_bytes: int) -> None:
    """Cap ``path`` at ``max_bytes`` by keeping the trailing half. No-op
    if the file is missing or already under the cap. Failures swallow —
    log housekeeping must never crash the orchestrator."""
    try:
        if max_bytes <= 0:
            return
        if not os.path.isfile(path):
            return
        sz = os.path.getsize(path)
        if sz <= max_bytes:
            return
        keep = max_bytes // 2
        with open(path, "rb") as f:
            f.seek(max(0, sz - keep))
            tail = f.read()
        with open(path, "wb") as f:
            f.write(b"--- LOG TRUNCATED " + _now_str().encode("utf-8") + b" ---\n")
            f.write(tail)
    except Exception:
        pass


def _log_pump(process: subprocess.Popen, log_f) -> None:
    """Drain a Popen's merged stdout/stderr line-by-line, prepend a
    wall-clock timestamp, write to ``log_f``.

    Must keep draining the pipe even if a single line raises during
    encode/format — letting the buffer fill (~64 KB on Linux) would
    block the workspace process on its next ``print`` and freeze the
    workflow silently. Hence the inner try/except around the per-line
    work.

    Exits cleanly when ``process.stdout`` returns "" (EOF — process
    has closed its end of the pipe). The outer finally writes a
    sentinel and flushes so the file ends on a complete line.
    """
    try:
        out = process.stdout
        if out is None:
            return
        for line in out:
            try:
                log_f.write(f"[{_now_str()}] {line}")
            except Exception:
                # never let a single bad line kill the pump — the pipe
                # MUST keep draining or the workspace will block.
                pass
    except Exception:
        pass
    finally:
        try:
            log_f.write(f"--- EOF {_now_str()} ---\n")
            log_f.flush()
        except Exception:
            pass


def _free_port(port: int) -> None:
    """Best-effort: kill any process currently bound to ``port`` so the
    next workspace launch on this port doesn't hit ``[Errno 98] Address
    already in use``.

    Uses ``lsof`` + SIGTERM (then SIGKILL after a brief wait). Failures
    are swallowed — if there's nothing to kill, or if we don't have
    permission, the next ``listen()`` call surfaces the real error.
    """
    try:
        out = subprocess.check_output(
            ["sudo", "lsof", "-tiTCP:%d" % int(port), "-sTCP:LISTEN"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except subprocess.CalledProcessError:
        return  # nobody listening
    except Exception:
        return  # lsof missing or not allowed
    pids = [int(p) for p in out.split() if p.isdigit()]
    if not pids:
        return
    for pid in pids:
        try:
            subprocess.run(["sudo", "kill", "-TERM", str(pid)], check=False)
        except Exception:
            pass
    # Brief wait, then force-kill any stragglers.
    time.sleep(0.5)
    for pid in pids:
        try:
            subprocess.run(["sudo", "kill", "-KILL", str(pid)],
                           check=False, stderr=subprocess.DEVNULL)
        except Exception:
            pass


class WorkspaceInfo:
    """Holds workspace process info and metadata."""

    def __init__(self, name: str, path_to_file: str, port: int,
                 node_url: Optional[str] = None, label: str = ""):
        self.name = name
        self.path_to_file = path_to_file
        self.port = int(port)
        self.label = label or ""

        # If set => this workspace is remote, and commands/status/logs are proxied to that orchestrator
        self.node_url: Optional[str] = node_url.strip().rstrip("/") if node_url else None

        # Current kwargs values (in-memory, set via modal, sent on start)
        self.kwargs_values: Dict = {}

        # Local process handle (only for local workspaces)
        self.process: Optional[subprocess.Popen] = None

        # Log file lives in <project_dir>/status/workspace.log. Fixed
        # name regardless of the workspace's registered identifier so
        # renaming or re-registering doesn't orphan files. ``/tmp``
        # fallback applies for remote workspaces and the rare case
        # where path_to_file isn't a real local file (namespace by
        # ``<name>`` there because /tmp is shared).
        self._status_dir: str = self._compute_status_dir(path_to_file)
        if self._status_dir == "/tmp":
            self.log_path: str = os.path.join(self._status_dir, f"{name}.log")
        else:
            self.log_path: str = os.path.join(self._status_dir, "workspace.log")

        # Orchestrator-level timing / error
        self.started_at: Optional[float] = None  # unix seconds; starts at LAUNCH for local
        self.finished_at: Optional[float] = None  # unix seconds; set when process stops
        self.last_error: Optional[str] = None

    @staticmethod
    def _compute_status_dir(path_to_file: str) -> str:
        """Project's status/ directory, or a /tmp fallback for remote /
        bare-name workspaces. Created lazily on first launch."""
        try:
            parent = Path(path_to_file).parent
            if str(parent) and parent.exists():
                return str(parent / "status")
        except Exception:
            pass
        return "/tmp"

    def ensure_status_dir(self) -> None:
        """Create <project_dir>/status/ on demand. Safe to call repeatedly."""
        try:
            Path(self._status_dir).mkdir(parents=True, exist_ok=True)
        except Exception:
            # Don't crash launch on a bookkeeping failure.
            pass

    def is_remote(self) -> bool:
        return bool(self.node_url)

    def has_trigger(self, trigger_name: str) -> bool:
        """Check if the protocol has a trigger state with the given name."""
        try:
            protocol_path = Path(self.path_to_file).parent / "protocol.yaml"
            if not protocol_path.is_file():
                return False
            with open(protocol_path) as f:
                data = yaml.safe_load(f)
            raw = data.get("states") or []
            states = [{"name": k, **v} for k, v in raw.items()] if isinstance(raw, dict) else raw
            for s in states:
                if s.get("trigger") == trigger_name:
                    return True
            return False
        except Exception:
            return False

    def launch_config(self) -> Optional[Dict]:
        """Read launch.yaml from the workspace directory and return kwargs schema."""
        try:
            launch_path = Path(self.path_to_file).parent / "launch.yaml"
            if not launch_path.is_file():
                return None
            with open(launch_path) as f:
                data = yaml.safe_load(f)
            return data.get("kwargs") if isinstance(data, dict) else None
        except Exception:
            return None

    def file_kwargs_keys(self) -> set:
        """Return the set of kwargs keys that are type=file."""
        schema = self.launch_config() or {}
        return {k for k, v in schema.items() if isinstance(v, dict) and v.get("type") == "file"}

    def close_log(self) -> None:
        """Stop the log pump (if running) then close the file.

        Order matters: the daemon pump thread holds the only writer
        handle to ``log_f`` and writes one timestamped line at a
        time. We join it (briefly) before closing so the last
        partial buffer can flush; the timeout guards against a
        wedged readline (e.g. a stuck child whose stdout never
        closed). After timeout we close anyway — daemon=True
        ensures the thread can't keep the orchestrator alive.
        """
        t = getattr(self, "_log_thread", None)
        if t is not None and t.is_alive():
            try:
                t.join(timeout=2.0)
            except Exception:
                pass
        self._log_thread = None
        if hasattr(self, "_log_f") and self._log_f:
            try:
                self._log_f.close()
            except Exception:
                pass
            self._log_f = None

    def clear_uploads(self) -> None:
        """Remove the _uploads folder and clear file kwargs from kwargs_values."""
        try:
            upload_dir = Path(self.path_to_file).parent / "_uploads"
            if upload_dir.is_dir():
                import shutil
                shutil.rmtree(upload_dir, ignore_errors=True)
        except Exception:
            pass
        # Clear file kwargs from stored values
        file_keys = self.file_kwargs_keys()
        for k in file_keys:
            self.kwargs_values.pop(k, None)

    def to_dict(self) -> Dict:
        return {
            "name": self.name,
            "label": self.label,
            "path_to_file": self.path_to_file,
            "port": self.port,
            "node_url": self.node_url or "",
        }
