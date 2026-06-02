"""Workspace-side contract for components that own remote devices.

A ``DeviceComponent`` is any workspace component (Inspection, Core, future
Pipette / Printer / Scale) that depends on one or more devices on the
device bus. The contract has two parts:

* ``device_ids`` — list of ``<kind>:<natural-id>`` strings the component
  depends on.
* ``device_claim(device_id)`` — optional. Returns ``"sim"`` or
  ``"real"`` (default ``"real"``) describing how the component is using
  that device in the current process. This is the **project-level**
  sim signal: it lives in workspace state, not on the bus, so a
  workspace's "I'm using this camera in sim mode" doesn't overwrite
  the camera daemon's truth on the bus. The panel reads it alongside
  the bus snapshot to render a SIM pill, and the orchestrator reads
  it to skip auto-pause for sim claims.

(Operator actions — `operator_actions()` — are a *component-level*
concern, not a device-level one. Components without any device-bus
dependency can still expose operator buttons. See
``workspace.components.operator_actions`` and ``docs/component-guide.md``.)

Why Protocol instead of base class:

* Components in ``workspace/components/`` are heterogeneous (devices,
  fixtures, racks, adapters). Forcing a base on the device subset is
  artificial.
* Structural typing (PEP 544) lets a component conform without inheriting
  anything — same approach the device contract uses on the device-service
  side.
* The scanner that walks ``workspace.components`` reads both surfaces
  defensively (``getattr(...)``), so non-device components remain
  untouched.

How a component declares its devices: see ``docs/device-guide.md`` §8
("Workspace-side: declaring the device").
"""

from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable


# Allowed claim modes. ``real`` is the default — the component uses the
# real device. ``sim`` means the component uses canned values locally;
# the auto-pause path skips this device, and the panel adds a SIM pill.
_CLAIM_MODES = ("real", "sim")


@runtime_checkable
class DeviceComponent(Protocol):
    """A workspace component that depends on one or more remote devices.

    Implementations expose ``device_ids`` as a property returning a list
    of ``<kind>:<natural-id>`` strings — one entry per device the
    component relies on. Components may also implement
    ``device_claim(device_id)`` to declare per-device sim/real intent.

    Example::

        class Inspection:
            @property
            def device_ids(self) -> list[str]:
                sn = self.vision.serial_number
                return [f"camera:{sn}"] if sn else []

            def device_claim(self, device_id: str) -> str:
                return "sim" if self.vision.simulation else "real"
    """

    @property
    def device_ids(self) -> list[str]: ...


def component_device_ids(component) -> list[str]:
    """Read ``device_ids`` from ``component`` defensively.

    Returns an empty list when the component doesn't declare any. Used by
    the project-page scanner that walks ``workspace.components.values()``
    and unions everything into the project's device set.
    """
    ids = getattr(component, "device_ids", []) or []
    # Coerce to a list so a generator / tuple works too. Strip falsy entries
    # (empty strings) since they're never valid MQTT ids.
    return [str(i) for i in ids if i]


def component_device_claim(component, device_id: str) -> Optional[str]:
    """Read this component's claim mode for ``device_id``, or None.

    Returns one of ``"real"`` / ``"sim"`` if the component declares a
    claim, else ``None`` (caller decides what default to apply).
    Catches and ignores any exception from the component's accessor —
    a misbehaving component's claim must not break the panel.

    Aggregation rule (applied by the workspace, not here): if any
    component claims ``"real"`` for a given id, the project's net claim
    is ``"real"`` — the strictest claim wins. ``"sim"`` only takes
    effect when every claim for that id is ``"sim"``.
    """
    fn = getattr(component, "device_claim", None)
    if fn is None:
        return None
    try:
        result = fn(device_id)
    except Exception:
        return None
    if result is None:
        return None
    result = str(result).strip().lower()
    return result if result in _CLAIM_MODES else None


