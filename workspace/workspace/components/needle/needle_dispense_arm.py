"""Needle dispense arm — the FIXED end of a fluid path.

Geometry + the pneumatic down/up IO come from ``Arm``; the fluidics
come from ``PumpedTool``, which binds this nozzle to one pump component
and one valve port (scene keys ``pump`` / ``pump_port``). The arm owns
no device and takes no bus row — the pump component is the sole owner
of the device id (device-guide §4). See
``components/pump/pump_link.py``.

Kinematically this is the mirror of ``needle_gripper``: the
robot carries the needle to the liquid, whereas here the robot brings
the vessel under a stationary nozzle. They share no base class — a
carried tool and a bench fixture are different things — only the fluid
capability, which is why it is composed in rather than inherited.

Scene yaml::

    needle_dispense_arm_1:
      type: "needle_dispense_arm"
      pump: "pump_1"     # "" -> unplumbed, geometry + IO only
      pump_port: 2               # valve port this tube lands on (number
                                 # or a name from the pump's valve_ports)

The ``DispenseArm`` recipe drives it: ``down()`` / ``up()`` for the
pneumatics, ``dispense(ul)`` for the pump.
"""

from copy import deepcopy
from mergedeep import merge
from workspace.components.factory import register
from workspace.components.needle.needle import Needle
from workspace.components.pump.pump_link import PumpedTool
from workspace.components.needle.arm import Arm


@register("needle_dispense_arm")
class NeedleDispenseArm(PumpedTool, Arm):
    DEFAULTS = dict(
            anchors={"body":{"center":[0, 0, 0, 0, 0, 0], "place": [75, 0, 0, 0, 0, 0], "top": [0, 0, 120, 0, 0, 0],
                    "hole_0":[25, 25, 0, 0, 0, 0], "hole_1": [-25, 25, 0, 0, 0, 0], "hole_2": [-25, -25, 0, 0, 0, 0], "hole_3": [25, -25, 0, 0, 0, 0]}},
            collision_box = 
                {"body":[
                    {"pose":[0, 0, (65+6.5)/2, 0.0, 0.0, 0.0], "scale":[65, 65, 65+6.5], "padding_enabled": True},#[xyzabc] , [lx,ly,lz]
                    {"pose":[0, 35.0-(15/2), 120/2, 0.0, 0.0, 0.0], "scale":[13.5, 134-13.5, 120], "padding_enabled": True},#[xyzabc] , [lx,ly,lz]
            ]},
            # cfg
            output_enable = [[None, None, 0.1]], # [[pin, index, time]]
            output_disable = [[None, None, 0.1]], # [[pin, index, time]]
            # ── fluid path ───────────────────────────────────────────
            pump="",
            pump_port=None,
            # ── the fitted needle ────────────────────────────────────
            # Same two numbers as the carried needle, same meaning:
            # declarative only (components/needle/needle.py).
            needle_gauge=None,     # e.g. 16
            needle_length=0.0,     # mm
        )

    def __init__(self, name: str, cfg: dict, workspace, **kwargs):
        # prm
        prm = deepcopy(Arm.DEFAULTS) # default
        merge(prm, self.DEFAULTS) # self
        merge(prm, cfg) # cfg
        merge(prm, kwargs) # kwargs
        
        # update type
        prm.setdefault("type", getattr(self.__class__, "_registered_type", cfg.get("type")))
        
        super().__init__(
            name=name,
            workspace=workspace,
            **prm
        )

        # Bind the fluid path (lazy: the pump may be built after us).
        self._init_pump_link(workspace, prm)
        self.needle = Needle(gauge=prm.get("needle_gauge"),
                             length=prm.get("needle_length", 0.0))

    # ── atomic ops (component-guide §7) ───────────────────────────────
    # The component owns the pneumatics, same as Gripper.enable /
    # Decapper.open; the DispenseArm recipe adds the workflow
    # checkpoint and delegates here. The fluid verbs (aspirate /
    # dispense / prime) come from PumpedTool. Idempotent: a repeated
    # call in the reached state is a no-op.

    def down(self) -> bool:
        """Extend the arm (pneumatic output HIGH)."""
        if self.output_state() != 1:
            self.workspace.rt.output(config=self.output_enable)
            self.output_state(1)
        return True

    def up(self) -> bool:
        """Retract the arm (pneumatic output LOW)."""
        if self.output_state() != 0:
            self.workspace.rt.output(config=self.output_disable)
            self.output_state(0)
        return True

    def operator_actions(self) -> list[dict]:
        return [
            {"label": "Down", "method": "down", "icon": "arrow-down", "group": "arm"},
            {"label": "Up",   "method": "up",   "icon": "arrow-up",   "group": "arm"},
        ]
