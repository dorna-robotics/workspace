"""Workspace-side contract for components that own remote devices.

A ``DeviceComponent`` is any workspace component (Inspection, Core, future
Pipette / Printer / Scale) that depends on one or more devices on the
device bus. The contract is the narrowest possible surface — a single
``device_ids`` property listing the ``<kind>:<natural-id>`` strings the
component claims.

Why a Protocol and not a base class:

* Components in ``workspace/components/`` are heterogeneous (devices,
  fixtures, racks, adapters). Forcing a base on the device subset is
  artificial.
* Structural typing (PEP 544) lets a component conform without inheriting
  anything — same approach the device contract uses on the device-service
  side.
* The scanner that walks ``workspace.components`` for the device panel
  reads ``device_ids`` defensively (``getattr(component, "device_ids",
  [])``), so non-device components remain untouched.

How a component declares its devices: see ``docs/device-guide.md`` §8
("Workspace-side: declaring the device").
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class DeviceComponent(Protocol):
    """A workspace component that depends on one or more remote devices.

    Implementations expose ``device_ids`` as a property returning a list of
    ``<kind>:<natural-id>`` strings — one entry per device the component
    relies on. Components claiming no remote device need not implement
    this Protocol.

    Example::

        class Inspection:
            @property
            def device_ids(self) -> list[str]:
                sn = self.vision.serial_number
                return [f"camera:{sn}"] if sn else []
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
