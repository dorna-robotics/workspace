"""The project's folders — declared in launch.yaml, never guessed.

``launch.yaml`` lists every folder the project exposes, in the order the
file browser shows them as tabs::

    folders:
      - {key: results,  label: Results,    path: results,     read_only: false}
      - {key: data,     label: Data,       path: data,        read_only: false}
      - {key: captures, label: Captures,   path: ../captures, read_only: false}
      - {key: rec,      label: Recordings, path: rec,         read_only: false}
      - {key: model,    label: Models,     path: model,       read_only: true}

Each entry, all four fields written out:

    key        the folder's identity, unique. Four keys mean something to
               the platform (PLATFORM below):
                 results   one folder per run — records.jsonl / .csv
                 data      operator INPUT files; a file parameter's Open
                           picks from here
                 rec       replay recordings
                 captures  the images the detections keep: a preset's
                           relative display.client_save_img lands here
               Any other key is the project's own: a tab, nothing more.
    label      the tab's text.
    path       relative to the project folder (``../x`` shares a parent's,
               the way recipes.j2 is shared), or absolute.
    read_only  true: browse, preview and download only — upload, new
               folder and delete are refused by the server. Read on every
               request, so flipping it takes effect on the next open.

The list IS the browser: only listed folders get a tab, in list order,
and the first one is where the Files button opens. A platform key that
is not listed still works at its default path (``<project>/<key>``) —
the platform needs somewhere to write a run's records — but has no tab,
and the runtime server says so once at launch. A launch.yaml with no
``folders:`` key at all gets the platform's four as tabs (PLATFORM
order, default paths, writable) — exactly what every project had before
the list existed. The old ``data_dir`` /
``results_dir`` / ``rec_dir`` / ``captures_dir`` keys are gone: a
launch.yaml that still has one is refused with the line to write
instead.

WHY A MODULE. Several processes need the same answer — the runtime
server writes run records and recordings, the vision station resolves
client saves, the orchestrator's file browser lists and serves them.
One contract, one place.
"""

from __future__ import annotations

import os
from pathlib import Path
from dataclasses import dataclass
from typing import Dict, List, Optional

import yaml

# The platform's own folders: key -> (default path, default tab label).
# A project that does not list one still gets it, at this path, untabbed.
PLATFORM: Dict[str, tuple] = {
    "results":  ("results",  "Results"),
    "data":     ("data",     "Data"),
    "captures": ("captures", "Captures"),
    "rec":      ("rec",      "Recordings"),
}

_FIELDS = ("key", "label", "path", "read_only")
_GONE_KEYS = ("data_dir", "results_dir", "rec_dir", "captures_dir")


@dataclass(frozen=True)
class Folder:
    key: str
    label: str
    path: Path          # resolved
    read_only: bool
    shown: bool         # listed in launch.yaml folders: — has a tab


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


def project_folders(project_dir, launch: Optional[dict] = None,
                    ensure: bool = False) -> List[Folder]:
    """Every folder of one project: the listed ones in list order
    (``shown``), then any platform folder the list leaves out, at its
    default path (not shown). Raises ValueError on a malformed list —
    a typo must not quietly drop a folder.

    ``launch`` is the already-parsed launch.yaml when the caller has it;
    otherwise it is read here (on every call — read_only is live).
    ``ensure`` creates the folders, so a listing of an absent one is
    empty rather than an error."""
    project_dir = Path(project_dir)
    launch = _launch_of(project_dir) if launch is None else (launch or {})
    gone = [k for k in _GONE_KEYS if k in launch]
    if gone:
        k = gone[0]
        raise ValueError(
            f"launch.yaml: {k} is gone — list the folder under folders: instead, e.g. "
            f"- {{key: {k[:-4]}, label: {PLATFORM[k[:-4]][1]}, path: {launch[k]}, read_only: false}}")
    raw = launch.get("folders")
    if raw is None:
        # No folders: key at all — the platform's four, as tabs, in
        # PLATFORM order: what every project had before the list existed.
        raw = [{"key": k, "label": lbl, "path": d, "read_only": False}
               for k, (d, lbl) in PLATFORM.items()]
    if not isinstance(raw, list):
        raise ValueError("launch.yaml: folders must be a list of {key, label, path, read_only}")

    def resolve(rel) -> Path:
        p = Path(str(rel)).expanduser()
        return (p if p.is_absolute() else (project_dir / p)).resolve()

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
        if not isinstance(key, str) or not key or "/" in key:
            raise ValueError(f"{where}: key must be a plain name")
        if key in seen:
            raise ValueError(f"{where}: key {key!r} is listed twice")
        if not isinstance(label, str) or not label:
            raise ValueError(f"{where} ({key}): label must be text")
        if not isinstance(path, str) or not path:
            raise ValueError(f"{where} ({key}): path must be text")
        if not isinstance(ro, bool):
            raise ValueError(f"{where} ({key}): read_only must be true or false")
        seen.add(key)
        out.append(Folder(key, label, resolve(path), ro, True))
    for key, (default, label) in PLATFORM.items():
        if key not in seen:
            out.append(Folder(key, label, resolve(default), False, False))
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


def project_dirs(project_dir, launch: Optional[dict] = None,
                 ensure: bool = False) -> Dict[str, Path]:
    """``{key: Path}`` for every folder of the project (project_folders),
    the platform's four always included."""
    return {f.key: f.path for f in project_folders(project_dir, launch, ensure)}


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


def client_save_path(value, captures: Optional[Path]):
    """Resolve one ``display.client_save_img`` / ``client_save_img_roi``
    value against the project's captures folder — the one rule, applied
    where a detection is registered:

        False / 0 / ""      -> unchanged (off)
        True / 1            -> "<captures>/"   (one file per run inside it)
        "sub/" or "f.jpg"   -> "<captures>/sub/" / "<captures>/f.jpg"
        "/abs/..." "~/..."  -> unchanged (taken as given)

    A trailing "/" is kept: it is what says "a folder, one file per run".
    With no captures folder known the value passes through untouched."""
    if not value or captures is None:
        return value
    captures = Path(captures)
    if value is True or value == 1:
        return str(captures) + os.sep
    if not isinstance(value, str):
        return value
    if os.path.isabs(os.path.expanduser(value)):
        return value
    out = str(captures / value)
    if value.endswith(("/", os.sep)) and not out.endswith(os.sep):
        out += os.sep
    return out

