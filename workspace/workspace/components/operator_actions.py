"""Component-side contract for operator-callable actions.

``operator_actions()`` is an optional method on any workspace
component (device-backed or not). It declares which of the
component's own methods should appear as buttons in the
**Operator Actions** UI surface — the sidebar section and the
pendant Controls modal.

The contract is intentionally lightweight:

* Method returns ``list[dict]`` with two string fields per entry:
    ``label``   — display name for the button
    ``method``  — name of a no-arg callable on the component

* Buttons are gated by workflow state on the orchestrator side
  (disabled while RUNNING); the component never enforces that.

* Same scanning pattern as ``device_ids`` on the device contract,
  but conceptually separate: operator actions are about *UI
  surfaces*, not about device-bus dependencies. A fixture with no
  device dependency can still expose operator actions; a
  device-backed component need not expose any.

See ``docs/component-guide.md`` for the contract and examples.
"""

from __future__ import annotations


def component_operator_actions(component) -> list[dict]:
    """Read ``operator_actions`` from ``component`` defensively.

    Entries with a missing label / method, or pointing at an
    attribute that isn't actually callable on this instance, are
    dropped silently — a misbehaving component must not break the
    orchestrator's panel. Returns ``[]`` when the component declares
    nothing (the default for non-operable components).
    """
    fn = getattr(component, "operator_actions", None)
    if fn is None:
        return []
    try:
        raw = fn()
    except Exception:
        return []
    out: list[dict] = []
    for entry in (raw or []):
        if not isinstance(entry, dict):
            continue
        label = str(entry.get("label", "") or "").strip()
        method = str(entry.get("method", "") or "").strip()
        if not label or not method:
            continue
        if not callable(getattr(component, method, None)):
            continue
        out.append({"label": label, "method": method})
    return out
