"""Skip — an item leaves the run, and the plan carries on without it.

A project action decides that an item cannot continue (a barcode that
never reads, a vial lost in transfer). It does the PHYSICAL recovery
itself — the project knows where a rejected item rests — and then ends
in an outcome branch whose effects assert the platform's reserved fact::

    from workspace.bt import skipped

    def eff(self, tube):
        return {
            "read":       (+scanned(tube),),
            "unreadable": (+skipped(tube), +hand_empty()),
        }

Everything after that is the platform's, one rule for every project:

* **Finished means done or skipped.** ``setup()``'s ``item_done`` is
  wrapped once (:func:`with_skip`), and every reader of it — the phase
  machinery, the window picker, the planning goal, ``bt.replay`` — sees
  the wrapped one. A skipped item is in no phase scope and no window,
  so no step is ever planned for it again; whatever is derived from it
  (its receiver, its product, its place in a shaker bank read from the
  window) goes with it.
* **The run's goal owns the items** (:func:`run_goal`): the run is over
  when every item is done or skipped AND the project's ``goal`` holds.
  A project's ``goal`` therefore states only what lies beyond the items
  (``started``, ``parked``) — it never loops over them, or a skipped
  item would keep it false forever.
* **Dependents follow.** ``setup()`` may return
  ``"dependents": fn(item) -> items`` — other items of the batch that
  cannot continue without this one. Skipping an item skips them too,
  transitively (:func:`apply`).
* **Stations it held are released.** Every capacity fact
  (``predicate(..., capacity=True)``) an action bound to the item took
  and no action has given back is re-asserted at the skip
  (:class:`Ledger`) — derived from what actually ran, never guessed.
  The project's recovery made the station physically free; this makes
  the facts say so.
* **The run replans at once** — the leaf that asserted the skip raises
  a replan even when its branch is the default one.

Nothing here moves hardware. The project owns the physical recovery,
the platform owns the plan (bt-framework-guide §8.5).
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

from workspace.bt.dsl import _CAPACITY_PREDICATE_NAMES, predicate

log = logging.getLogger(__name__)

#: The platform-reserved fact: ``skipped(item)`` — the item left the run.
skipped = predicate("skipped")
SKIPPED = skipped.name


def is_skipped(state, item) -> bool:
    return (SKIPPED, item) in state


def with_skip(item_done: Optional[Callable]) -> Optional[Callable]:
    """``item_done`` that also counts a skipped item as finished."""
    if item_done is None:
        return None
    if getattr(item_done, "_counts_skipped", False):
        return item_done

    def done(state, item) -> bool:
        return (SKIPPED, item) in state or item_done(state, item)

    done._counts_skipped = True
    done.__wrapped__ = item_done
    return done


def run_goal(goal_fn: Callable, all_items: Iterable[Any], done: Optional[Callable]) -> Callable:
    """The run's goal: every item done-or-skipped, then the project's
    own ``goal`` (what lies beyond the items)."""
    items = list(all_items)
    if done is None or not items:
        return goal_fn

    def goal(state) -> bool:
        return all(done(state, it) for it in items) and goal_fn(state)

    return goal


def open_items(state, all_items: Iterable[Any]) -> List[Any]:
    """The batch's items that are still in the run (not skipped)."""
    return [it for it in all_items if (SKIPPED, it) not in state]


class Ledger:
    """Capacity facts held per item: taken by an action bound to the
    item (the fact removed), given back by any action (re-added). A
    skip releases what its items still hold."""

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
    """The run's skip bookkeeping on ``ctx.meta`` — created on first use."""
    meta = ctx.meta if getattr(ctx, "meta", None) is not None else {}
    st = meta.get("skip")
    if st is None:
        st = meta["skip"] = {"ledger": Ledger(), "dependents": None, "log": {}}
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
    """The consequences of ``skipped(item)`` for ``items`` just asserted
    in ``facts`` (mutated in place): dependents skipped transitively,
    held capacity released, the skip logged. Returns every item that
    left the run with this skip, the asserted ones first."""
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
        facts.add((SKIPPED, it))
        st["log"][it] = {"by": by, "outcome": outcome, "phase": phase, "because_of": parent}
        if dependents is not None:
            for d in dependents(it) or ():
                if d not in order:
                    queue.append((d, it))
    released = st["ledger"].release(order)
    facts.update(released)
    log.warning("SKIP: %s left the run (%s -> %s, phase %s)%s%s",
                order[0] if len(order) == 1 else order, by, outcome, phase,
                f"; dependents {order[1:]}" if len(order) > 1 else "",
                f"; released {[f[0] + str(f[1:]) for f in released]}" if released else "")
    return order


def info(ctx, item: Any) -> Optional[Dict[str, Any]]:
    """How ``item`` left the run — ``{"by", "outcome", "phase",
    "because_of"}`` — or ``None`` if it did not."""
    return state_of(ctx)["log"].get(item)


__all__ = ["skipped", "SKIPPED", "is_skipped", "with_skip", "run_goal", "open_items",
           "Ledger", "install", "note_effects", "apply", "info"]
