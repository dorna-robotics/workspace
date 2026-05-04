"""Device integration — both producer and consumer infrastructure.

See ``docs/device-guide.md`` for the full integration playbook (device
contract, adapter pattern, ID convention, end-to-end examples).

This package is the canonical home for all device infrastructure:

  * ``MQTTOrchestrator`` — orchestrator side, subscribes to the bus,
    tracks state, exposes ``recover``/``release``.
  * ``MQTTDeviceAdapter`` — producer side, publishes a ``Device``-shaped
    object's state to the bus and routes commands back. Used by every
    device service (camera today, printer/etc. tomorrow).
  * ``AutoRecover`` — generic exponential-backoff helper that turns a
    device's ``recover()`` into a self-healing loop. Plug into any
    device service that wants auto-recovery on hotplug or polling.
  * ``DeviceComponent`` — component-side protocol for declaring which
    device ids a recipe component depends on.
"""

from workspace.devices.orchestrator import (
    DeviceEntry,
    MQTTOrchestrator,
)
from workspace.devices.component_contract import (
    DeviceComponent,
    component_device_ids,
)
from workspace.devices.adapter import MQTTDeviceAdapter
from workspace.devices.recovery import AutoRecover

__all__ = [
    "MQTTOrchestrator",
    "DeviceEntry",
    "MQTTDeviceAdapter",
    "AutoRecover",
    "DeviceComponent",
    "component_device_ids",
]
