"""Device integration — MQTT-based health monitoring.

See ``docs/device-mqtt-spec.md`` for the wire protocol. Workspace runtime
consumes device state via :class:`MQTTOrchestrator`.
"""

from workspace.devices.orchestrator import (
    DeviceEntry,
    MQTTOrchestrator,
)

__all__ = ["MQTTOrchestrator", "DeviceEntry"]
