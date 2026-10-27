"""Vision server — the dorna_vision server the cameras' detections run
on, as a DEVICE on the bus (device-guide §10 shape A: the workspace
publishes it).

One component per server. Each camera names its server
(``camera_cfg.server``) instead of carrying the server's ip / port, so
the address is written once. Two cards, two truths:

  * ``camera:<sn>`` — the camera HARDWARE (USB, sensor). Published by
    the vision server process itself; unchanged.
  * ``vision_server:<ip>@<port>`` — this one: everything the workspace
    asks the server to do. A failed capture or detection run (a crashed
    model, a bad preset, a lost socket) puts it ``down``, the message
    naming the camera, the detection and the server's error; critical →
    the run pauses (project-guide §8, layer 1). **Recover** re-establishes
    every camera session on this server — connect, re-add the camera,
    re-register its detections (each model loads again) — and the
    action that failed decides what Resume does (layer 2: a read
    returns None, its action False, the planner selects it again).

No AutoRecover: a crashed detection is not a lost link, and nothing
here retries behind the operator's back.

Scene — declared ABOVE the cameras that name it (components build in
scene order):

    vision_server_1:
      type: "vision_server"
      simulation: false
      critical: true
      ip: "10.0.3.40"
      port: 80

    inspection_vertical_144mm_1:
      type: "inspection_vertical_144mm"
      simulation: false
      camera_cfg:
        server: "vision_server_1"
        serial_number: "130322274110"
"""

from __future__ import annotations

import logging
from copy import deepcopy
from typing import Callable

from mergedeep import merge

from workspace.components.factory import register
from workspace.devices import attach_device


log = logging.getLogger(__name__)


class VisionServerStation:
    """The Device (device-guide §2) for one vision server. Holds the
    camera stations (VisionStation) that run on it: their failures put it
    down (:meth:`fault`), its :meth:`recover` re-establishes them."""

    KIND = "vision_server"

    def __init__(self, ip: str, port: int, simulation: bool, label: str):
        self.ip = ip
        self.port = int(port)
        self.simulation = bool(simulation)
        self.label = label
        # Bus state is the server's truth regardless of sim (device-guide
        # §16). Down until the component's first recover() connects.
        self.state: str = "down"
        self.msg: str = "not connected"
        self._listeners: list[Callable[[str, str], None]] = []
        self._cameras: list = []          # VisionStation, every real camera on this server

    # ── Device protocol ────────────────────────────────────────────────

    @property
    def id(self) -> str:
        """``vision_server:<ip>@<port>`` — slash-free (device-guide §9);
        sim does not change it."""
        return f"{self.KIND}:{self.ip}@{self.port}"

    def on_state_change(self, cb: Callable[[str, str], None]) -> None:
        self._listeners.append(cb)

    def _set_state(self, new_state: str, msg: str = "") -> None:
        if new_state == self.state and msg == self.msg:
            return
        self.state, self.msg = new_state, msg
        for cb in list(self._listeners):
            try:
                cb(new_state, msg)
            except Exception:
                log.exception("VisionServerStation[%s]: listener raised", self.label)

    def recover(self) -> bool:
        """Re-establish the server: a fresh connect, then every camera's
        session (VisionStation.reconnect: connect, re-add the camera,
        re-register its detections). Fires ``recovering → result`` so the
        panel always sees the event."""
        self._set_state("recovering", "reconnecting")
        try:
            self.probe()
            for cam in self._cameras:
                cam.reconnect()
        except Exception as ex:
            self._set_state("down", f"recover failed: {type(ex).__name__}: {ex}")
            return False
        self._set_state("ok", "")
        return True

    def release(self) -> None:
        """Close every camera session on this server and mark it down."""
        for cam in self._cameras:
            cam.close()
        self._set_state("down", "released")

    # ── The cameras' side ──────────────────────────────────────────────

    def probe(self) -> None:
        """Connect and close — the server answers, or this raises."""
        from dorna_vision_client import VisionClient
        client = VisionClient()
        try:
            client.connect(host=self.ip, port=self.port)
        finally:
            client.close()

    def add_camera(self, cam) -> None:
        """A real camera's session lives on this server from now on."""
        self._cameras.append(cam)

    def fault(self, msg: str) -> None:
        """A capture, detection run or session call failed — down, with
        what failed. The camera says it; the operator recovers here."""
        log.warning("vision server %s: %s", self.id, msg)
        self._set_state("down", msg)


@register("vision_server")
class VisionServer:
    """The vision server as a scene component — no geometry, one device."""

    DEFAULTS = dict(
        ip="",
        port=80,
        simulation=True,
        # A server whose detections fail is a fault worth pausing for —
        # every read on it is wrong until it is recovered.
        critical=True,
    )

    def __init__(self, name: str, cfg: dict, workspace, **kwargs):
        prm = deepcopy(self.DEFAULTS)
        merge(prm, cfg)
        merge(prm, kwargs)
        prm.setdefault("type", getattr(self.__class__, "_registered_type", cfg.get("type")))

        self.name = name
        self.workspace = workspace
        self.type = prm["type"]
        self.assembly = {}

        self._ip = str(prm["ip"] or "")
        self._simulation_mode = bool(prm["simulation"])
        self.server = VisionServerStation(self._ip, prm["port"], self._simulation_mode, label=name)

        # Gated on ip, like every workspace-owned device: no ip, no claim,
        # no panel row (the cameras on it are then in simulation). The
        # first connect is attempted whatever the sim flag — the bus dot
        # is the server's truth (device-guide §16); a failure is a red
        # dot, never a raise.
        self._attachment = None
        if self._ip:
            self.server.recover()
            try:
                self._attachment = attach_device(
                    self.server,
                    kind=VisionServerStation.KIND,
                    sim=self._simulation_mode,
                    critical=bool(prm["critical"]),
                    meta={"ip": self._ip, "port": self.server.port},
                )
            except Exception:
                log.exception("VisionServer[%s]: attach_device failed", self.name)

    # ── DeviceComponent contract ──────────────────────────────────────

    @property
    def device_ids(self) -> list[str]:
        return [self.server.id] if self._ip else []

    def device_claim(self, device_id: str) -> str:
        if device_id == self.server.id:
            return "sim" if self._simulation_mode else "real"
        return "real"

    # ── Teardown ──────────────────────────────────────────────────────

    def close(self):
        try:
            if self._attachment is not None:
                self._attachment.close()
        except Exception:
            log.exception("VisionServer[%s]: attachment close raised", self.name)
        finally:
            self._attachment = None
            self.server.release()
