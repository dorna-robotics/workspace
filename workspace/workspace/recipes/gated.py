"""The gate between a recipe and its component — where work meets the
robot.

    The tail lives only between two robot motions. The next motion
    consumes or flushes it (core's rule). The first non-motion WORK
    settles it. Observability never touches it.

A held motion tail is a deferred exit: the robot still stands at the
last deposit pose while the model already says it has left. A device
asked to act or measure at that moment — a balance read with the
gripper over the pan, a shaker head about to swing — acts on the wrong
world. So every component op a recipe calls is WORK: it settles the
tail and observes pause before it runs. Nobody writes that per call and
nobody can forget it: ``Recipe.__init__`` hands the recipe this proxy,
not the raw component, and a recipe written as ``self.component.weight()``
is gated. Attribute reads pass straight through (anchors, assembly,
name), so the kinematic paths see the real object.

``@pure`` (workspace.components.work) marks a helper that does not touch
the world; it skips the gate. ``settle=False`` is not a knob here on
purpose: a device op that must run under a held tail has no meaning —
it belongs BEFORE the verb's exit, inside the verb (``actions=``).

Workflow thread only — operator calls (device-panel buttons) execute
immediately, as they always did; a paused workflow's held tail must not
move the robot because an operator pressed Weigh.
"""

from __future__ import annotations

import functools
from typing import Any

from workspace.components.work import is_pure


class Gated:
    """``component`` as a recipe sees it. See the module doc."""

    __slots__ = ("_obj", "_recipe")

    def __init__(self, obj: Any, recipe: Any):
        object.__setattr__(self, "_obj", obj)
        object.__setattr__(self, "_recipe", recipe)

    # ── the gate ──────────────────────────────────────────────────────
    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._obj, name)
        if not callable(attr) or is_pure(attr) or isinstance(attr, type):
            return attr

        @functools.wraps(attr)
        def work(*a, **k):
            rt = getattr(self._recipe, "rt", None)
            if rt is not None:
                rt.settle(f"work: {getattr(self._obj, 'name', type(self._obj).__name__)}.{name}")
                rt.checkpoint()
            return attr(*a, **k)
        return work

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(self._obj, name, value)

    # ── transparency: the proxy IS the component to everything else ──
    @property
    def __class__(self):
        return self._obj.__class__

    @property
    def raw(self) -> Any:
        """The component itself — for identity checks and hand-offs
        that must see the real object."""
        return self._obj

    def __eq__(self, other: Any) -> bool:
        return self._obj == (other._obj if isinstance(other, Gated) else other)

    def __hash__(self) -> int:
        return hash(self._obj)

    def __repr__(self) -> str:
        return f"Gated({self._obj!r})"

    def __bool__(self) -> bool:
        return bool(self._obj)


def gated(obj: Any, recipe: Any) -> Any:
    """``obj`` behind the gate; ``None`` stays ``None``; already gated
    stays as is."""
    if obj is None or isinstance(obj, Gated):
        return obj
    return Gated(obj, recipe)
