"""The project's own folders — declared in launch.yaml, never guessed.

A project owns three folders besides its code, and until now every one
of them was a hardcoded name inside the platform:

    data/      the operator's INPUT files. Uploads land here and stay;
               the next run browses to the same file instead of the
               operator hunting for it on their laptop again.
    results/   one folder per run, named by its start time
               (2026-09-12_15-35-17) — records.jsonl, records.csv.
    rec/       replay recordings, rec_<start time>.jsonl.

``launch.yaml`` names all three, the same way it names ``core_dir``::

    data_dir:     data
    results_dir:  results
    rec_dir:      rec

Relative paths resolve against the project folder, so a SUBPROJECT can
keep its own (``data``) or share the parent's (``../data``) exactly the
way ``recipes.j2`` is already shared — nothing in here decides that for
it. Absolute paths are taken as given.

Undeclared falls back to the plain name next to launch.yaml, so a
project that says nothing gets ``<project>/data``. That is a DEFAULT,
not a guess: it is one line in the file away from being explicit, and
``project_dirs()`` reports which keys were declared so a UI can say so.

WHY A MODULE. Two processes need the same answer — the runtime server
writes run records and recordings, the orchestrator's file browser
lists and serves them. Two readers of one contract, so the contract
lives in one place.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Optional

import yaml

# key in launch.yaml -> folder name when the key is absent
ROOTS: Dict[str, str] = {
    "data":    "data",
    "results": "results",
    "rec":     "rec",
}

# What each root is FOR, in one operator-facing line. The browser panel
# shows these; they live here so the words and the paths can never drift
# apart.
ROOT_LABELS: Dict[str, str] = {
    "data":    "Input files you upload and re-use between runs",
    "results": "One folder per run — records and measurements",
    "rec":     "Replay recordings",
}


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


def project_dirs(project_dir, launch: Optional[dict] = None,
                 ensure: bool = False) -> Dict[str, Path]:
    """``{"data": Path, "results": Path, "rec": Path}`` for one project.

    ``launch`` is the already-parsed launch.yaml when the caller has it
    (the runtime server does); otherwise it is read here. ``ensure``
    creates the folders — a browser that lists an absent folder should
    show it empty, not 404, and a run that writes one should not have to
    care whether an operator made it first.
    """
    project_dir = Path(project_dir)
    launch = _launch_of(project_dir) if launch is None else (launch or {})
    out: Dict[str, Path] = {}
    for root, default in ROOTS.items():
        declared = launch.get(f"{root}_dir")
        rel = str(declared) if declared else default
        p = Path(rel)
        out[root] = (p if p.is_absolute() else (project_dir / p)).resolve()
    if ensure:
        for p in out.values():
            try:
                fresh = not p.exists()
                p.mkdir(parents=True, exist_ok=True)
                if fresh:
                    hand_back(p)
            except OSError:
                pass            # read-only mount: listing still works
    return out


def declared_roots(project_dir, launch: Optional[dict] = None) -> Dict[str, bool]:
    """Which of the three folders launch.yaml names explicitly. The UI
    marks the rest as defaults so "where does this go?" is answerable
    from the screen."""
    launch = _launch_of(Path(project_dir)) if launch is None else (launch or {})
    return {root: bool(launch.get(f"{root}_dir")) for root in ROOTS}


def safe_join(root: Path, rel: str) -> Path:
    """``root / rel``, or raise — the ONE gate between a web request and
    the filesystem.

    Everything reachable from the browser goes through here. A relative
    path that climbs out of its root (``../../.ssh/id_rsa``), an
    absolute path, or a symlink pointing outside is refused: the check
    is on the RESOLVED path, so a link cannot smuggle a caller out of
    the folder it was given.
    """
    root = Path(root).resolve()
    rel = (rel or "").strip().lstrip("/")
    if not rel or rel == ".":
        return root
    if os.path.isabs(rel) or ".." in Path(rel).parts:
        raise ValueError("path escapes the folder")
    target = (root / rel).resolve()
    if target != root and root not in target.parents:
        raise ValueError("path escapes the folder")
    return target
