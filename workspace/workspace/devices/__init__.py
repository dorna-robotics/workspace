"""Device integration — workspace-side glue plus producer infrastructure.

The producer-side primitives (``MQTTDeviceAdapter``, ``AutoRecover``,
``Device`` protocol, etc.) live in the standalone ``dorna_devices``
package so device services (vision Pi, future printer / syringe Pis)
can install them without dragging in the full workspace orchestrator.
This module re-exports those names for back-compat — existing code
doing ``from workspace.devices import MQTTDeviceAdapter`` keeps
working.

# TODO(dorna_devices migration): once every external caller has been
# moved to ``from dorna_devices import …`` (vision_service, Pi
# services, recipe code), drop the re-exports and require imports
# from ``dorna_devices`` directly. Tracked for the next minor release.

What stays workspace-internal (still defined here, not in
``dorna_devices``):

  * ``MQTTOrchestrator`` — subscribes to the bus, tracks state, wires
    into :class:`workspace.runtime.Runtime` (workspace-specific).
  * ``DeviceComponent`` / ``component_device_ids`` /
    ``component_device_claim`` — recipe component contract.
  * ``attach_device`` / ``attach_sim_stub`` / ``DeviceAttachment`` —
    workspace-side wiring helpers that combine the adapter, recovery,
    and conflict detection into a single canonical attach point.

See ``docs/device-guide.md`` for the integration playbook.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any, Callable, Optional

from workspace.devices.orchestrator import (
    DeviceEntry,
    MQTTOrchestrator,
)
from workspace.devices.component_contract import (
    DeviceComponent,
    component_device_ids,
    component_device_claim,
)
# Re-export from dorna_devices so existing call sites keep working.
# New code should import directly from dorna_devices.
from dorna_devices import (
    MQTTDeviceAdapter,
    DevicePublisherConflict,
    detect_publisher_conflict,
    make_publisher_id,
    AutoRecover,
)

__all__ = [
    "MQTTOrchestrator",
    "DeviceEntry",
    "MQTTDeviceAdapter",
    "AutoRecover",
    "DeviceComponent",
    "component_device_ids",
    "component_device_claim",
    "attach_device",
    "attach_sim_stub",
    "DeviceAttachment",
    "DevicePublisherConflict",
    "make_publisher_id",
]


log = logging.getLogger(__name__)


class _StubDevice:
    """Minimal Device-shaped object for sim-only bus entries.

    Used when the workspace needs to publish a SIM placeholder for a
    device whose real publisher is a separate daemon that isn't running
    (e.g. an authored-sim camera — the camera daemon doesn't open the
    USB device, so nobody else is on the bus for that id).

    Always reports ``state="ok"``. ``recover``/``release`` are no-ops
    that succeed, so the operator clicking "Recover" on a sim row
    just confirms it's already fine. ``on_state_change`` is a no-op
    sink — sim devices don't transition.
    """

    def __init__(self, device_id: str):
        self.id = device_id
        self.state = "ok"
        # Leave msg blank — the SIM pill in the panel already tells
        # the operator this is a sim entry, and a duplicate "simulation"
        # text next to it is just visual noise.
        self.msg = ""

    def on_state_change(self, _cb) -> None:  # noqa: D401
        return None

    def recover(self) -> bool:
        return True

    def release(self) -> None:
        return None


# Seconds between idle liveness checks. Only devices that opt in (by
# implementing ``ping()``) are polled, and only while genuinely idle —
# see DeviceAttachment's heartbeat docstring.
HEARTBEAT_INTERVAL_S = 5.0


class DeviceAttachment:
    """Handle returned by :func:`attach_device`.

    Owns the MQTT adapter (always present), the AutoRecover loop
    (present only when ``sim=False``) and the idle heartbeat (only for
    devices that implement ``ping()``). Closing the handle cleans all
    three up, mirroring what a service teardown needs.

    The ``set_sim`` method handles the operator-toggled-mid-run case:
    flipping a real device into sim suspends AutoRecover + heartbeat and
    republishes info with ``sim=true``; flipping back re-arms both.

    **The heartbeat** exists because a request/response device (serial
    meter, TCP balance, pH circuit) is only *touched* when an action
    reads it: unplug one between reads and the panel keeps showing the
    last known state — green — until something happens to talk to it.
    The robot doesn't have this problem because motion traffic exercises
    its link constantly.

    The contract keeps it cheap enough to be invisible:

      * **Opt-in.** No ``ping()`` on the device object → no thread, no
        behaviour change. Existing devices are untouched.
      * **Idle-only.** ``ping()`` is expected to no-op (return True)
        when the device has seen real traffic within the interval, so a
        busy device pays NOTHING — the run's own reads are the
        heartbeat. Only a genuinely idle link gets a probe.
      * **Never collides.** ``ping()`` must take the device's I/O lock
        non-blockingly and report alive if the lock is held: a probe
        must never interleave with a real read on the same port.
      * **Off in sim**, off while the device is not ``ok`` (a down
        device is AutoRecover's business, not the heartbeat's).

    On a failed probe the device transitions itself to ``down`` (the
    same path any failed op takes) and the heartbeat nudges
    ``AutoRecover`` — so a serial device now comes back by itself after
    a replug instead of waiting for an operator's Recover press.
    """

    def __init__(
        self,
        adapter: MQTTDeviceAdapter,
        recover: Optional[AutoRecover],
        recover_factory: Optional[Callable[[], AutoRecover]] = None,
        device: Any = None,
        heartbeat_interval: float = HEARTBEAT_INTERVAL_S,
    ):
        self.adapter = adapter
        self.recover = recover
        self._recover_factory = recover_factory
        self._device = device
        self._hb_interval = float(heartbeat_interval)
        self._hb_stop = threading.Event()
        self._hb_thread: Optional[threading.Thread] = None
        if not self.adapter.sim:
            self._start_heartbeat()

    # ── Idle heartbeat ────────────────────────────────────────────────

    def _start_heartbeat(self) -> None:
        dev = self._device
        if dev is None or not callable(getattr(dev, "ping", None)):
            return                      # device didn't opt in
        if self._hb_thread is not None and self._hb_thread.is_alive():
            return
        self._hb_stop.clear()
        self._hb_thread = threading.Thread(
            target=self._heartbeat_loop, name=f"heartbeat-{self.adapter.device_id}",
            daemon=True,
        )
        self._hb_thread.start()

    def _stop_heartbeat(self) -> None:
        self._hb_stop.set()
        self._hb_thread = None

    def _heartbeat_loop(self) -> None:
        dev = self._device
        while not self._hb_stop.wait(self._hb_interval):
            try:
                if self.adapter.sim:
                    continue
                state = getattr(dev, "state", None)
                if state == "ok":
                    # ping() reports False only when it actually reached
                    # the hardware and got silence; it also set the
                    # device's own state to down on the way out.
                    if dev.ping() is False and self.recover is not None:
                        self.recover.trigger()
                elif state == "down" and self.recover is not None:
                    # Missed trigger (e.g. the failure came from an op
                    # that had no recovery hook) — nudge the loop; it
                    # no-ops when already running.
                    self.recover.trigger()
            except Exception:
                log.exception("DeviceAttachment[%s]: heartbeat raised",
                              getattr(self.adapter, "device_id", "?"))

    @property
    def sim(self) -> bool:
        return self.adapter.sim

    def set_sim(self, sim: bool) -> None:
        """Toggle sim mode. Republishes info; suspends/arms AutoRecover.

        Going real→sim: stops the AutoRecover loop (no real device to
        recover). Going sim→real: spins up a new AutoRecover via the
        factory passed to ``attach_device``. If no factory was given
        (caller didn't author the device with recovery), the loop just
        stays absent — same as ``attach_device(..., sim=True)``.
        """
        new_sim = bool(sim)
        if new_sim == self.adapter.sim:
            return
        self.adapter.set_sim(new_sim)
        if new_sim:
            # Suspend recovery + heartbeat — sim has nothing to poll.
            self._stop_heartbeat()
            if self.recover is not None:
                try:
                    self.recover.stop()
                except Exception:
                    log.exception("DeviceAttachment: recover.stop() raised")
                self.recover = None
        else:
            # Re-arm recovery if the caller authored a factory.
            if self.recover is None and self._recover_factory is not None:
                try:
                    self.recover = self._recover_factory()
                except Exception:
                    log.exception("DeviceAttachment: recover_factory raised")
                    self.recover = None
            self._start_heartbeat()

    def close(self) -> None:
        """Stop the heartbeat, AutoRecover and the adapter. Safe twice."""
        self._stop_heartbeat()
        if self.recover is not None:
            try:
                self.recover.stop()
            except Exception:
                pass
            self.recover = None
        try:
            self.adapter.close()
        except Exception:
            pass


def attach_device(
    device: Any,
    *,
    kind: str,
    sim: bool,
    critical: bool = True,
    meta: Optional[dict[str, Any]] = None,
    recover_factory: Optional[Callable[[], AutoRecover]] = None,
    broker_host: Optional[str] = None,
    broker_port: Optional[int] = None,
    detect_conflict: bool = True,
    conflict_timeout_s: float = 0.75,
) -> DeviceAttachment:
    """Canonical wiring entry point for a workspace-side device.

    Every device-owning component (Core, CameraPool, Pipettor, …) calls
    this exactly once per device. The helper encodes the contract:

      * **One publisher per device id.** Before publishing anything,
        the helper subscribes briefly to the device's retained topics
        and checks for an existing live publisher. If a different
        ``publisher_id`` is already on the bus with ``online: true``,
        it raises :class:`DevicePublisherConflict` and refuses to
        attach. This is the load-bearing guard against the "two
        publishers stomp on the same retained topic, last writer wins,
        panel shows green when reality is red" bug.
      * The MQTT adapter is wired regardless of ``sim`` (sim devices
        still publish — they're the rightful owner of their id and
        carry ``sim=true`` in info).
      * AutoRecover is wired only when ``sim=False``. Sim has no
        recovery semantics. The caller passes a ``recover_factory``
        (a no-arg callable returning a configured AutoRecover) so
        ``set_sim(False)`` can re-arm recovery without leaking the
        adapter's lifecycle into the caller.
      * ``critical`` is published as authored. Sim devices simply
        never publish ``state=down``, so a critical sim device is a
        no-op.

    Args:
        device: Any ``Device``-shaped object (``id``, ``state``, ``msg``,
            ``on_state_change``, ``recover``, ``release``).
        kind: Short device family (``"robot"``, ``"camera"``, …).
        sim: Authored simulation intent. Failures must NOT flip this.
        critical: Whether a non-sim, non-reachable transition pauses
            the runtime. Published as-is.
        meta: Free-form info-payload dict (model, IP, USB port, …).
        recover_factory: Optional zero-arg callable returning a
            configured ``AutoRecover``. Called in real mode and on
            ``set_sim(False)``.
        broker_host / broker_port: MQTT broker overrides.
        detect_conflict: Run the publisher-conflict check before
            attaching. Defaults True; disable only for tests where you
            know there's no other publisher. Set the env var
            ``DEVICE_BUS_DETECT_CONFLICT=0`` to disable globally.
        conflict_timeout_s: How long to wait for retained payloads
            during the conflict check. 0.75s is empirically sufficient
            for a local broker and bounds the worst-case startup hit.

    Returns:
        A :class:`DeviceAttachment` owning the adapter and (when
        non-sim) the AutoRecover loop.

    Raises:
        DevicePublisherConflict: Another live publisher already owns
            this device id on the bus.
    """
    # Determine the canonical device_id the adapter will use, so the
    # conflict check looks up the same retained topic the adapter will
    # publish to. Mirrors the normalization in MQTTDeviceAdapter.
    device_id = getattr(device, "id", None)
    if not device_id:
        raise ValueError("device.id must be a non-empty string before attach_device")
    device_id = str(device_id)
    if ":" not in device_id:
        device_id = f"{kind}:{device_id}"
    our_publisher_id = make_publisher_id(device_id)

    env_disable = os.environ.get("DEVICE_BUS_DETECT_CONFLICT", "1").strip().lower() in {
        "0", "false", "no", "off"
    }
    if detect_conflict and not env_disable:
        owner = detect_publisher_conflict(
            device_id,
            our_publisher_id,
            broker_host=broker_host,
            broker_port=broker_port,
            timeout_s=conflict_timeout_s,
        )
        if owner is not None:
            raise DevicePublisherConflict(device_id, owner, our_publisher_id)

    adapter = MQTTDeviceAdapter(
        device,
        kind=kind,
        critical=critical,
        sim=bool(sim),
        meta=meta,
        broker_host=broker_host,
        broker_port=broker_port,
    )
    recover: Optional[AutoRecover] = None
    if not sim and recover_factory is not None:
        try:
            recover = recover_factory()
        except Exception:
            log.exception("attach_device[%s]: recover_factory raised", kind)
            recover = None
    return DeviceAttachment(
        adapter=adapter,
        recover=recover,
        recover_factory=recover_factory,
        device=device,
    )


def attach_sim_stub(
    *,
    id: str,
    kind: str,
    critical: bool = True,
    meta: Optional[dict[str, Any]] = None,
    broker_host: Optional[str] = None,
    broker_port: Optional[int] = None,
) -> DeviceAttachment:
    """Publish a SIM placeholder for a device with no real publisher.

    Use when the authored-sim component does NOT own a real device
    object (the canonical case is a sim camera — the camera daemon
    isn't running for that USB device, so nobody else is on the bus
    for ``camera:<serial>``). This helper builds a minimal stub that
    satisfies the Device protocol, attaches it as ``sim=True``, and
    returns the same :class:`DeviceAttachment` handle you'd get from
    :func:`attach_device`.

    Cleaning up the returned attachment removes the stub from the bus
    (publishes ``online: false`` on the retained state topic).

    Note on conflicts: if the real publisher (e.g. camera daemon)
    starts publishing for the same id while this stub is live, the
    last writer wins on the retained ``info`` topic. Operators should
    not run both side-by-side; sim is meant to mean "I'm not running
    the daemon for this one."

    Args:
        id: Full ``<kind>:<natural-id>`` device id. Must match the id
            the real publisher would use, so the panel UI and any
            consumers see one consistent entry.
        kind / critical / meta: Same as :func:`attach_device`.
        broker_host / broker_port: MQTT broker overrides; default to
            env / localhost.
    """
    stub = _StubDevice(id)
    return attach_device(
        stub,
        kind=kind,
        sim=True,
        critical=critical,
        meta=meta,
        recover_factory=None,
        broker_host=broker_host,
        broker_port=broker_port,
    )
