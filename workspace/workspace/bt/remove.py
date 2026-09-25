"""Remove — an item leaves the run, and the plan carries on without it.

A project action decides that an item cannot continue (a barcode that
never reads, a vial lost in transfer). It does the PHYSICAL recovery
itself — the project knows where a rejected item rests — and then ends
in an outcome branch whose effects assert the platform's reserved fact::

    from workspace.bt import removed

    def eff(self, tube):
        return {
            "read":       (+scanned(tube),),
            "unreadable": (+removed(tube), +hand_empty()),
        }

Everything after that is the platform's, one rule for every project:

* **Finished means done or removed.** ``setup()``'s ``item_done`` is
  wrapped once (:func:`with_removed`), and every reader of it — the phase
  machinery, the window picker, the planning goal, ``bt.replay`` — sees
  the wrapped one. A removed item is in no phase scope and no window,
  so no step is ever planned for it again; whatever is derived from it
  (its receiver, its product, its place in a shaker bank read from the
  window) goes with it.
* **The run's goal owns the items** (:func:`run_goal`): the run is over
  when every item is done or removed AND the project's ``goal`` holds.
  A project's ``goal`` therefore states only what lies beyond the items
  (``started``, ``parked``) — it never loops over them, or a removed
  item would keep it false forever.
* **Dependents follow.** ``setup()`` may return
  ``"dependents": fn(item) -> items`` — other items of the batch that
  cannot continue without this one. Removing an item removes them too,
  transitively (:func:`apply`).
* **Stations it held are released.** Every capacity fact
  (``predicate(..., capacity=True)``) an action bound to the item took
  and no action has given back is re-asserted at the removal
  (:class:`Ledger`) — derived from what actually ran, never guessed.
  The project's recovery made the station physically free; this makes
  the facts say so.
* **The run replans at once** — the leaf that asserted ``removed`` raises
  a replan even when its branch is the default one.

Nothing here moves hardware. The project owns the physical recovery,
the platform owns the plan (bt-framework-guide §8.5).
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

from workspace.bt.dsl import _CAPACITY_PREDICATE_NAMES, predicate

log = logging.getLogger(__name__)

#: The platform-reserved fact: ``removed(item)`` — the item left the run.
removed = predicate("removed")
REMOVED = removed.name


def is_removed(state, item) -> bool:
    return (REMOVED, item) in state


def with_removed(item_done: Optional[Callable]) -> Optional[Callable]:
    """``item_done`` that also counts a removed item as finished."""
    if item_done is None:
        return None
    if getattr(item_done, "_counts_removed", False):
        return item_done

    def done(state, item) -> bool:
        return (REMOVED, item) in state or item_done(state, item)

    done._counts_removed = True
    done.__wrapped__ = item_done
    return done


def run_goal(goal_fn: Callable, all_items: Iterable[Any], done: Optional[Callable]) -> Callable:
    """The run's goal: every item done-or-removed, then the project's
    own ``goal`` (what lies beyond the items)."""
    items = list(all_items)
    if done is None or not items:
        return goal_fn

    def goal(state) -> bool:
        return all(done(state, it) for it in items) and goal_fn(state)

    return goal


def open_items(state, all_items: Iterable[Any]) -> List[Any]:
    """The batch's items that are still in the run (not removed)."""
    return [it for it in all_items if (REMOVED, it) not in state]


class Ledger:
    """Capacity facts held per item: taken by an action bound to the
    item (the fact removed), given back by any action (re-added). A
    remove releases what its items still hold."""

    def __init__(self) -> None:
        self.held: Dict[Tuple[Any, ...], Any] = {}

    def note(self, item: Any, removed: Iterable[Tuple], added: Iterable[Tuple]) -> None:
        for f in added:
            if f[0] in _CAPACITY_PREDICATE_NAMES:
                self.held.pop(f, None)
        if item is None:
            return
        for f in removed:
            if f[0] in _CAPACITY_PREDICATE_NAMES:
                self.held[f] = item

    def release(self, items: Iterable[Any]) -> List[Tuple]:
        items = set(items)
        out = sorted((f for f, it in self.held.items() if it in items), key=repr)
        for f in out:
            del self.held[f]
        return out


def state_of(ctx) -> Dict[str, Any]:
    """The run's remove bookkeeping on ``ctx.meta`` — created on first use."""
    meta = ctx.meta if getattr(ctx, "meta", None) is not None else {}
    st = meta.get("remove")
    if st is None:
        st = meta["remove"] = {"ledger": Ledger(), "dependents": None, "log": {}}
    return st


def install(ctx, dependents: Optional[Callable] = None) -> None:
    """Called once per run by the launcher / replay / bench."""
    if dependents is not None and not callable(dependents):
        raise TypeError(
            f"setup() returned dependents of type {type(dependents).__name__} — "
            "expected a callable: ``def dependents(item): return [...]``")
    st = state_of(ctx)
    st["dependents"] = dependents


def note_effects(ctx, item: Any, removed: Iterable[Tuple], added: Iterable[Tuple]) -> None:
    """Every applied action's effects go through here (leaf, replay)."""
    state_of(ctx)["ledger"].note(item, removed, added)


def apply(ctx, facts: Set[Tuple], items: Iterable[Any], *, by: str, outcome: str) -> List[Any]:
    """The consequences of ``removed(item)`` for ``items`` just asserted
    in ``facts`` (mutated in place): dependents removed transitively,
    held capacity released, the removal logged. Returns every item that
    left the run with this remove, the asserted ones first."""
    st = state_of(ctx)
    dependents = st["dependents"]
    phase = (getattr(ctx, "meta", None) or {}).get("current_phase")
    order: List[Any] = []
    queue = [(it, None) for it in items]
    while queue:
        it, parent = queue.pop(0)
        if it in order:
            continue
        order.append(it)
        facts.add((REMOVED, it))
        st["log"][it] = {"by": by, "outcome": outcome, "phase": phase, "because_of": parent}
        if dependents is not None:
            for d in dependents(it) or ():
                if d not in order:
                    queue.append((d, it))
    released = st["ledger"].release(order)
    facts.update(released)
    log.warning("REMOVED: %s left the run (%s -> %s, phase %s)%s%s",
                order[0] if len(order) == 1 else order, by, outcome, phase,
                f"; dependents {order[1:]}" if len(order) > 1 else "",
                f"; released {[f[0] + str(f[1:]) for f in released]}" if released else "")
    return order


def dependents_of(ctx, item: Any) -> List[Any]:
    """Every item that would leave the run with ``item`` (transitive,
    ``item`` itself excluded) — a preview, nothing applied."""
    dependents = state_of(ctx)["dependents"]
    out: List[Any] = []
    queue = list(dependents(item) or ()) if dependents is not None else []
    while queue:
        d = queue.pop(0)
        if d == item or d in out:
            continue
        out.append(d)
        queue.extend(dependents(d) or ())
    return out


def offer(ctx, state, all_items: Iterable[Any], done: Optional[Callable], phases=(),
          label: Optional[Callable] = None) -> List[Dict[str, Any]]:
    """The operator's Replan list: every item of the batch still in
    the run, as ``{"item", "label", "phase", "phase_index", "in_phase",
    "done", "with", "holds"}`` — the last phase it reached (and its
    place in the ROUTE, -1 before the first), the phase it is in now,
    whether it is finished (removing a
    finished item changes only its record), what would leave with it,
    and the capacity it still holds. Read from the facts, the same ones
    the planner reads — the list cannot disagree with the plan."""
    ledger = state_of(ctx)["ledger"]
    out = []
    for it in all_items:
        if (REMOVED, it) in state:
            continue
        reached, reached_i = None, -1
        for i, ph in enumerate(phases):
            try:
                if ph.reached(state, [it]):
                    reached, reached_i = ph.name, i
            except Exception:
                pass
        try:
            name = str(label(it)) if label is not None else str(it)
        except Exception:
            name = str(it)
        out.append({
            "item": it,
            "label": name,
            "phase": reached,
            "phase_index": reached_i,
            "in_phase": (phases[reached_i + 1].name if 0 <= reached_i + 1 < len(phases) else None),
            "done": bool(done(state, it)) if done is not None else False,
            "with": [str(label(d)) if label is not None else str(d) for d in dependents_of(ctx, it)],
            "holds": [f[0] + (str(f[1:]) if len(f) > 1 else "") for f, owner in ledger.held.items() if owner == it],
        })
    return out


def scene_check(workspace, names: List[str]) -> List[str]:
    """Check that the 3D models ``names`` can be removed from the scene
    together, BEFORE anything is changed; returns them ordered children
    first. Raises ValueError naming the first problem:

    * a name that is not a component of the scene;
    * ``core``, a tool mounted on the robot, or a device-backed
      component (the same refusals as ``Workspace.remove_component``);
    * a component ATTACHED UNDER one of them that is not in the list —
      it would be left orphaned (a cap on a removed vial). The project's
      ``item_components`` must name it too.
    """
    comps = getattr(workspace, "components", {}) or {}
    owner: Dict[int, str] = {}
    for cname, comp in comps.items():
        for solid in (getattr(comp, "assembly", {}) or {}).values():
            owner[id(solid)] = cname
    names = list(dict.fromkeys(names))
    chosen = set(names)
    from workspace.devices import component_device_ids
    for n in names:
        if n not in comps:
            raise ValueError(f"3D model {n!r} is not in the scene")
        if n == "core":
            raise ValueError("'core' cannot be removed")
        comp = comps[n]
        if component_device_ids(comp):
            raise ValueError(f"{n!r} is a device-backed component — it cannot be removed during a run")
        mounted = getattr(workspace, "_is_mounted_on_robot", None)
        if callable(mounted) and mounted(comp):
            raise ValueError(f"{n!r} is the tool mounted on the robot — it cannot be removed")

    def parent_of(cname):
        for solid in (getattr(comps[cname], "assembly", {}) or {}).values():
            ps = (getattr(solid, "parent", None) or {}).get("parent_solid")
            if ps is not None and id(ps) in owner:
                return owner[id(ps)]
        return None

    for cname in comps:
        if cname in chosen:
            continue
        p = parent_of(cname)
        if p in chosen:
            raise ValueError(f"{cname!r} is attached to {p!r} and would be left behind — "
                             f"item_components must name it too")

    def depth(cname, seen=()):
        p = parent_of(cname)
        return 0 if p is None or p in seen else 1 + depth(p, seen + (cname,))
    return sorted(names, key=depth, reverse=True)


def info(ctx, item: Any) -> Optional[Dict[str, Any]]:
    """How ``item`` left the run — ``{"by", "outcome", "phase",
    "because_of"}`` — or ``None`` if it did not."""
    return state_of(ctx)["log"].get(item)


__all__ = ["removed", "REMOVED", "is_removed", "with_removed", "run_goal", "open_items",
           "Ledger", "install", "note_effects", "apply", "dependents_of", "offer",
           "scene_check", "info"]
