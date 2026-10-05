"""Render a .j2 file — the one way every template in a project is rendered.

A template can import or include files from ITS OWN folder, the plain
Jinja way::

    {# scene/bench.j2 — git-ignored: this machine's values #}
    {% set robot_ip = "192.168.1.10" %}
    {% set vision_port = 4001 %}

    {# scene/core_1000.j2 #}
    {% import "bench.j2" as bench %}
    core:
      ip: {{ bench.robot_ip }}

Paths in ``import`` / ``include`` are relative to the importing file's
folder. Undefined names FAIL (StrictUndefined): a typo such as
``bench.robot_ipx`` stops the render naming it, instead of quietly
rendering an empty value. Used by the workspace, the launcher, replay,
the orchestrator and the scene builder alike, so a scene renders the
same everywhere.
"""
from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined


def render_text(text: str, folder, **context) -> str:
    """Render ``text`` with imports resolved against ``folder``."""
    env = Environment(loader=FileSystemLoader(str(folder)), undefined=StrictUndefined,
                      keep_trailing_newline=True)
    return env.from_string(text).render(**context)


def render_file(path, **context) -> str:
    """Render the file at ``path`` (imports relative to its folder)."""
    path = Path(path)
    return render_text(path.read_text(), path.parent, **context)
