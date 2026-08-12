"""Syringe dispense arm — the FIXED end of a fluid path.

Geometry + the pneumatic down/up IO come from ``Arm``; the fluidics
come from ``PumpedTool``, which binds this nozzle to one pump component
and one valve port (scene keys ``pump`` / ``pump_port``). The arm owns
no device and takes no bus row — the pump component is the sole owner
of the device id (device-guide §4). See
``components/pump/pump_link.py``.

Kinematically this is the mirror of ``gripper_syringe_needle``: the
robot carries the needle to the liquid, whereas here the robot brings
the vessel under a stationary nozzle. They share no base class — a
carried tool and a bench fixture are different things — only the fluid
capability, which is why it is composed in rather than inherited.

Scene yaml::

    syringe_dispense_arm_1:
      type: "syringe_dispense_arm"
      pump: "syringe_pump_1"     # "" -> unplumbed, geometry + IO only
      pump_port: "output"        # valve port this tube lands on

The ``DispenseArm`` recipe drives it: ``down()`` / ``up()`` for the
pneumatics, ``dispense(ul)`` for the pump.
"""

from copy import deepcopy
from mergedeep import merge
from workspace.components.factory import register
from workspace.components.pump.needle import Needle
from workspace.components.pump.pump_link import PumpedTool
from workspace.components.syringe.arm import Arm


@register("syringe_dispense_arm")
class SyringeDispenseArm(PumpedTool, Arm):
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
            # Same two numbers as the carried needle, same meanings
            # (components/pump/needle.py). The difference is where the
            # length points: this arm is stationary and doses DOWNWARD
            # into whatever stands under it, so the needle hangs from
            # the nozzle and ``needle_tip`` is where liquid actually
            # leaves. ``nozzle_z`` says how high that nozzle sits above
            # the arm's base plane — the GLB does not model the nozzle,
            # so it has to be declared to be usable.
            needle_gauge=None,     # e.g. 16
            needle_length=0.0,     # mm below the nozzle face
            nozzle_z=None,         # mm above the base plane; None -> unknown
        )

    def __init__(self, name: str, cfg: dict, workspace, **kwargs):
        # prm
        prm = deepcopy(Arm.DEFAULTS) # default
        merge(prm, self.DEFAULTS) # self
        merge(prm, cfg) # cfg
        merge(prm, kwargs) # kwargs
        
        # update type
        prm.setdefault("type", getattr(self.__class__, "_registered_type", cfg.get("type")))
        
        # Where the liquid leaves, as an anchor a recipe can target:
        # the nozzle, over the ``place`` spot, minus the needle. Added
        # only when ``nozzle_z`` is declared — an invented height would
        # be a number that reads like a measurement.
        nz = prm.get("nozzle_z")
        if nz is not None:
            body = prm["anchors"]["body"]
            place = body.get("place", [0, 0, 0, 0, 0, 0])
            drop = float(nz) - float(prm.get("needle_length", 0.0) or 0.0)
            body["needle_tip"] = [place[0], place[1], drop, 0, 0, 0]

        super().__init__(
            name=name,
            workspace=workspace,
            **prm
        )

        # Bind the fluid path (lazy: the pump may be built after us).
        self._init_pump_link(workspace, prm)
        self.needle = Needle(gauge=prm.get("needle_gauge"),
                             length=prm.get("needle_length", 0.0))
        self.nozzle_z = prm.get("nozzle_z")
