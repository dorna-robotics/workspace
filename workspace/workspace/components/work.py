"""``@pure`` — a component method that does NOT touch the world.

A recipe reaches its component through a gate (``workspace.recipes
.gated``): every method call is WORK — it settles any held robot motion
and observes pause before it runs — unless the method is marked pure.
Pure is for bookkeeping the component does from memory: a material
name, a syringe volume, a port lookup. Anything that talks to hardware,
reads a sensor or moves a thing is work and stays unmarked.

Forgetting the mark is safe: a pure helper then settles needlessly, and
the flush names it (``work: pump_1.tube_volume``) so it gets marked the
first time it shows. Marking a hardware op pure is the one mistake this
cannot catch — do not.

    from workspace.components.work import pure

    class Pump(...):
        @pure
        def material_at(self, outlet=None): ...
"""

from __future__ import annotations

from typing import Callable, TypeVar

F = TypeVar("F", bound=Callable)


def pure(fn: F) -> F:
    """Mark a component method as not touching the world (see module doc)."""
    fn.__work__ = "pure"          # type: ignore[attr-defined]
    return fn


def is_pure(fn) -> bool:
    return getattr(fn, "__work__", None) == "pure"
