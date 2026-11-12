"""Component-side contract for operator-callable actions.

``operator_actions()`` is an optional method on any workspace
component (device-backed or not). It declares which of the
component's own methods should appear as buttons where the operator
works a component — the desktop sidebar's Operator Controls and the
pendant's Devices tab.

The contract is intentionally lightweight:

* Method returns ``list[dict]``, two required string fields per entry
  and the rest optional:
    ``label``   — display name for the button
    ``method``  — name of a callable on the component, called with no
                  arguments, or with its declared ``params``
    ``icon``    — OPTIONAL name from the UI's named icon set
                  (power, power-off, zap, zap-off, link, link-off,
                  eye, forward, backward, rotate, activity). Unknown
                  or missing names render the button text-only —
                  icons are progressive enhancement, never required.
    ``group``   — OPTIONAL: the actions that belong together
                  (Motors, Fluid, Connection). The pendant's Devices
                  tab heads a section with it; the sidebar puts a
                  group's buttons on one row. No behavior attached.
    ``hint``    — OPTIONAL one line under the label: what it does.
    ``params``  — OPTIONAL list: the fields the operator fills in
                  before Run, DECLARED — never guessed from the
                  method's signature. Each ``{name, type, default?,
                  unit?, label?, options?, min?, max?}``; ``type`` is
                  int | float | str | bool | choice (``options`` for a
                  choice; a choice's value is sent as declared, so
                  ``1.0`` stays a float). Run calls ``method(**values)``,
                  each value converted to its type and checked
                  (``coerce_args``). An action without ``params`` is
                  called with none. A field that cannot be used (no
                  identifier name, an unknown type, a choice without
                  options) is left out with a warning in the log naming
                  it — fix the declaration.

* ``operator_facts()`` — OPTIONAL, any component: a few things worth
  seeing at a glance (the pump's last op, barrel and line volumes; the
  robot's tool and address) as ``[{"label": ..., "value": ...}]``. The
  pendant's Devices tab shows them above the actions and asks again
  every 2 s while they are on screen — so BOOKKEEPING ONLY, never a
  hardware query (a serial or network round-trip there could collide
  with a move on the same link). Live readings belong in an action.

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

import keyword
import logging

log = logging.getLogger(__name__)
_BAD_FIELDS_LOGGED: set[str] = set()     # each mis-declared field is warned about once

PARAM_TYPES = ("int", "float", "str", "bool", "choice")


def _param(entry) -> dict | None:
    """One declared field, normalized — None when it is not usable (no
    identifier name, an unknown type, a choice without options)."""
    if not isinstance(entry, dict):
        return None
    name = str(entry.get("name", "") or "").strip()
    ptype = str(entry.get("type", "") or "").strip()
    if not name.isidentifier() or keyword.iskeyword(name) or ptype not in PARAM_TYPES:
        return None
    p = {"name": name, "type": ptype, "label": str(entry.get("label") or name).strip()}
    if ptype == "choice":
        opts = entry.get("options")
        if not isinstance(opts, (list, tuple)) or not opts:
            return None
        p["options"] = [o if isinstance(o, (int, float, bool)) else str(o) for o in opts]
    for k in ("default", "unit", "min", "max"):
        if entry.get(k) is not None:
            p[k] = entry[k]
    return p


def coerce_args(action: dict, args: dict | None) -> dict:
    """The keyword arguments for ``action``'s method from what the
    operator sent: every declared field, converted to its type, checked
    against ``options`` / ``min`` / ``max``; a field left empty takes its
    ``default``. Raises ValueError naming the field — an undeclared name,
    a missing value, a wrong type, out of range."""
    args = dict(args or {})
    params = action.get("params") or []
    known = {p["name"] for p in params}
    extra = sorted(set(args) - known)
    if extra:
        raise ValueError(f"not a field of {action['label']}: {', '.join(extra)}")
    out = {}
    for p in params:
        raw = args.get(p["name"])
        if raw is None or raw == "":
            if "default" not in p:
                raise ValueError(f"{p['label']}: a value is required")
            raw = p["default"]
        t = p["type"]
        try:
            if t == "int":
                v = int(raw) if not isinstance(raw, float) or raw.is_integer() else None
                if v is None:
                    raise ValueError
            elif t == "float":
                v = float(raw)
            elif t == "bool":
                if isinstance(raw, bool):
                    v = raw
                elif str(raw).lower() in ("true", "1", "yes", "on"):
                    v = True
                elif str(raw).lower() in ("false", "0", "no", "off"):
                    v = False
                else:
                    raise ValueError
            elif t == "choice":
                # the client sends an option as declared (JSON keeps
                # 1.0 a number, True a bool): match it by value
                match = [o for o in p["options"] if o == raw]
                if not match:
                    raise ValueError
                v = match[0]
            else:
                v = str(raw)
        except (TypeError, ValueError):
            want = f"one of {', '.join(map(str, p['options']))}" if t == "choice" else f"a {t}"
            raise ValueError(f"{p['label']}: {raw!r} is not {want}")
        if t in ("int", "float"):
            if p.get("min") is not None and v < p["min"]:
                raise ValueError(f"{p['label']}: {v} is below {p['min']}")
            if p.get("max") is not None and v > p["max"]:
                raise ValueError(f"{p['label']}: {v} is above {p['max']}")
        out[p["name"]] = v
    return out


def component_operator_facts(component) -> list[dict] | None:
    """The component's ``operator_facts()`` as ``[{label, value}]`` with
    the values as text; ``None`` when it declares none. Raises what the
    component raises — the caller shows it, it is not swallowed."""
    fn = getattr(component, "operator_facts", None)
    if not callable(fn):
        return None
    out = []
    for entry in (fn() or []):
        if isinstance(entry, dict) and str(entry.get("label", "")).strip():
            v = entry.get("value")
            out.append({"label": str(entry["label"]).strip(), "value": "—" if v is None else str(v)})
    return out


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
        item = {"label": label, "method": method}
        icon = str(entry.get("icon", "") or "").strip()
        if icon:
            item["icon"] = icon
        group = str(entry.get("group", "") or "").strip()
        if group:
            item["group"] = group
        hint = str(entry.get("hint", "") or "").strip()
        if hint:
            item["hint"] = hint
        params = []
        for x in (entry.get("params") or []):
            p = _param(x)
            if p is None:
                bad = f"{getattr(component, 'name', '?')}.{method}: {x!r}"
                if bad not in _BAD_FIELDS_LOGGED:        # read on every snapshot: say it once
                    _BAD_FIELDS_LOGGED.add(bad)
                    log.warning("%s — not a usable field (an identifier name, a type of %s, "
                                "a choice's options); left out", bad, "/".join(PARAM_TYPES))
            else:
                params.append(p)
        if params:
            item["params"] = params
        out.append(item)
    return out
