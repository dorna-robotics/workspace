"""Read a project's scene — the one way, for everything that needs it.

A scene is the list of files in launch.yaml's ``scene:`` (one path or a
list), relative to the project folder. Each file is rendered (``j2.py``:
imports resolved against its own folder, undefined names fail) and
parsed as yaml; the files merge IN ORDER, a later file's component
replacing an earlier one of the same name.

The workspace builds from this, and so do the tools that must see the
same scene without building it — the orchestrator (a project screen's
``api.scene``), the scene builder, ``recipes.solve``. One reader, so
none of them can drift from what the run builds.

Light on purpose: jinja2 and yaml only — the orchestrator imports it
without the robot stack.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, List, Union

import yaml

from workspace.j2 import render_text

PathLike = Union[str, Path]


def scene_paths(project_dir: PathLike, launch: dict) -> List[Path]:
    """launch.yaml's ``scene:`` as absolute paths, in order."""
    scene = (launch or {}).get("scene") or []
    if isinstance(scene, (str, Path)):
        scene = [scene]
    return [Path(project_dir) / str(p) for p in scene]


def load_scene(paths: Iterable[PathLike]) -> dict:
    """Render and merge the scene files ``paths``, in order — later files
    override earlier ones component by component. Raises on a file that
    cannot be read, rendered or parsed (the run could not build either)."""
    merged: dict = {}
    for path in paths:
        path = Path(path)
        text = path.read_text()
        if path.suffix == ".j2" or "{%" in text or "{{" in text:
            text = render_text(text, path.parent)
        merged.update(yaml.safe_load(text) or {})
    return merged
