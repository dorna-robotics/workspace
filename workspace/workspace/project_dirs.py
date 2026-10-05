"""The project's folders and paths — declared in launch.yaml, never guessed.

Two separate things, each explicit:

1. WHERE THE PLATFORM WRITES — one launch.yaml key per job, naming a
   folder (relative to the project folder, or absolute)::

       records:  results      # each run's rt.record files: <run>/records.jsonl, .csv
       replays:  rec          # replay recordings, rec_<start time>.jsonl
       uploads:  data         # where a file parameter's Open browses

   A key that is not declared means OFF: run records stay in memory (the
   pendant and ``rt.records()`` still show them), no recording is written,
   the Open button has no folder. There is no default folder.

2. WHAT THE FILE BROWSER SHOWS — ``folders:``, display only::

       folders:
         - {key: results,  label: Results,  path: results,     read_only: false}
         - {key: captures, label: Captures, path: ../captures, read_only: false}
         - {key: model,    label: Models,   path: ../model,    read_only: true}

   Each entry, all four fields written out: ``key`` (a plain name, the
   tab's identity), ``label`` (its text), ``path`` (relative to the
   project folder, or absolute), ``read_only`` (true: browse, preview and
   download only — upload, new folder and delete are refused by the
   server; read on every request, so a flip takes effect on the next
   open). One tab per entry, in list order; Files opens on the first.
   Listing a folder never makes anything be saved into it — a run's
   records go where ``records:`` says, a detection's images where its own
   path says. No ``folders:`` key = no tabs.

The old ``data_dir`` / ``results_dir`` / ``rec_dir`` / ``captures_dir``
keys are refused with the line to write instead.

WHY A MODULE. Several processes need the same answer — the runtime
server writes records and replays, the scene builder lists replays, the
orchestrator's file browser lists and serves the folders. One contract,
one place.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from dataclasses import dataclass
from typing import Dict, List, Optional

import yaml

# launch.yaml keys naming where the platform writes / reads, by job.
PATHS = ("records", "replays", "uploads")

_FIELDS = ("key", "label", "path", "read_only")
_GONE = {"results_dir": "records: <folder>", "rec_dir": "replays: <folder>",
         "data_dir": "uploads: <folder>",
         "captures_dir": "a folders: entry - {key: captures, label: Captures, path: <folder>, read_only: false}"}
_KEY_RE = re.compile(r"^[A-Za-z0-9_-]+$")


@dataclass(frozen=True)
class Folder:
    key: str
    label: str
    path: Path          # resolved
    read_only: bool


def hand_back(path) -> None:
    """Give a folder the platform just made to the HUMAN who launched it.

    The workspace server runs under ``sudo``, so everything it creates
    is owned by root — and the operator's file browser, which may not
    be root, then cannot delete a run it can see. ``SUDO_UID`` is who
    actually asked, so hand the folder to them.

    Best effort and silent: a platform that is not running as root, or
    a filesystem that will not chown, is not a reason to fail a run.
    """
    try:
        if os.geteuid() != 0:
            return
        uid, gid = os.environ.get("SUDO_UID"), os.environ.get("SUDO_GID")
        if uid is None:
            return
        os.chown(path, int(uid), int(gid) if gid is not None else -1)
    except (OSError, ValueError, AttributeError):
        pass


def _launch_of(project_dir: Path) -> dict:
    """``launch.yaml`` as a dict, or {} — never raises. A project whose
    launch file is missing or broken still gets its default folders;
    the launcher is what reports a broken launch.yaml, not this."""
    try:
        f = project_dir / "launch.yaml"
        if f.is_file():
            return yaml.safe_load(f.read_text()) or {}
    except Exception:
        pass
    return {}


def _check_gone(launch: dict) -> None:
    for k, line in _GONE.items():
        if k in launch:
            raise ValueError(f"launch.yaml: {k} is gone — write {line} instead")


def _resolve(project_dir: Path, rel) -> Path:
    p = Path(str(rel)).expanduser()
    return (p if p.is_absolute() else (project_dir / p)).resolve()


def project_paths(project_dir, launch: Optional[dict] = None,
                  ensure: bool = False) -> Dict[str, Optional[Path]]:
    """``{"records": Path|None, "replays": Path|None, "uploads": Path|None}``
    — the folders launch.yaml names for the platform's jobs; None = not
    declared = off. ``ensure`` creates the declared ones."""
    project_dir = Path(project_dir)
    launch = _launch_of(project_dir) if launch is None else (launch or {})
    _check_gone(launch)
    out: Dict[str, Optional[Path]] = {}
    for k in PATHS:
        v = launch.get(k)
        if v is None or v is False or v == "":
            out[k] = None
            continue
        if not isinstance(v, str):
            raise ValueError(f"launch.yaml: {k} must be a folder path")
        out[k] = _resolve(project_dir, v)
        if ensure:
            try:
                fresh = not out[k].exists()
                out[k].mkdir(parents=True, exist_ok=True)
                if fresh:
                    hand_back(out[k])
            except OSError:
                pass
    return out


def project_folders(project_dir, launch: Optional[dict] = None,
                    ensure: bool = False) -> List[Folder]:
    """The file browser's tabs: launch.yaml ``folders:``, in order. Raises
    ValueError on a malformed list — a typo must not quietly drop a tab.
    Read on every call (read_only is live). ``ensure`` creates them."""
    project_dir = Path(project_dir)
    launch = _launch_of(project_dir) if launch is None else (launch or {})
    _check_gone(launch)
    raw = launch.get("folders")
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("launch.yaml: folders must be a list of {key, label, path, read_only}")
    out: List[Folder] = []
    seen = set()
    for i, e in enumerate(raw):
        where = f"launch.yaml folders[{i}]"
        if not isinstance(e, dict):
            raise ValueError(f"{where}: an entry is {{key, label, path, read_only}}")
        extra = sorted(set(e) - set(_FIELDS))
        missing = [f for f in _FIELDS if f not in e]
        if extra or missing:
            raise ValueError(f"{where}: " + "; ".join(
                ([f"unknown field(s) {', '.join(extra)}"] if extra else []) +
                ([f"missing {', '.join(missing)}"] if missing else [])))
        key, label, path, ro = e["key"], e["label"], e["path"], e["read_only"]
        if not isinstance(key, str) or not _KEY_RE.match(key):
            raise ValueError(f"{where}: key must be a plain name (letters, digits, _ -)")
        if key in seen:
            raise ValueError(f"{where}: key {key!r} is listed twice")
        if not isinstance(label, str) or not label:
            raise ValueError(f"{where} ({key}): label must be text")
        if not isinstance(path, str) or not path:
            raise ValueError(f"{where} ({key}): path must be text")
        if not isinstance(ro, bool):
            raise ValueError(f"{where} ({key}): read_only must be true or false")
        seen.add(key)
        out.append(Folder(key, label, _resolve(project_dir, path), ro))
    if ensure:
        for f in out:
            try:
                fresh = not f.path.exists()
                f.path.mkdir(parents=True, exist_ok=True)
                if fresh:
                    hand_back(f.path)
            except OSError:
                pass            # read-only mount: listing still works
    return out


def workspace_project_dir(workspace) -> Optional[Path]:
    """The project folder of a running Workspace: the one it was started
    with (``Workspace(project_dir=...)`` — main.py, Bench), else the
    folder holding its first scene file (``scene/`` stepped over), the
    same fallback the core folder uses for a notebook that built a
    Workspace by hand. None when neither is known."""
    declared = getattr(workspace, "project_dir", None)
    if declared:
        return Path(declared).resolve()
    paths = getattr(workspace, "config_paths", None) or []
    if not paths:
        return None
    proj = Path(paths[0]).resolve().parent
    return proj.parent if proj.name == "scene" else proj
