"""Syringe needle on the tool changer — the CARRIED end of a fluid
path.

Geometry comes from ``Gripper``; the fluidics come from ``PumpedTool``,
which binds this needle to one pump component and one valve port
(scene keys ``pump`` / ``pump_port``). The needle owns no device and
takes no bus row — the pump component is the sole owner of the device
id (device-guide §4). See ``components/pump/pump_link.py``.

Scene yaml::

    gripper_syringe_needle_1:
      type: "gripper_syringe_needle"
      has_tool_changer: true
      pump: "syringe_pump_1"     # "" -> unplumbed, geometry only
      pump_port: 3               # valve port this tube lands on

Because the fluid method names match the pipettor's, a project can
drive this needle with the existing ``PipettingSite`` recipe — motion
targets the vessel, ``aspirate`` / ``dispense`` resolve through
``core.current_tool()`` — instead of a parallel recipe family.
"""

from copy import deepcopy
from mergedeep import merge
from workspace.components.factory import register
from workspace.components.gripper.gripper import Gripper
from workspace.components.pump.needle import Needle
from workspace.components.pump.pump_link import PumpedTool


@register("gripper_syringe_needle")
class GripperSyringeNeedle(PumpedTool, Gripper):
    DEFAULTS = dict(
        anchors={"body": {"center": [0, 0, 0, 0, 0, 0], "tcp":[0, 0, 157.751, 0, 0, 0],  "tip":[0, 0, 157.751, 0, 0, 0]}},
        collision_box =
            {"body":[
                {"pose":[0.0, 0.0, 31.50/2, 0.0, 0.0, 0.0], "scale":[43.0, 43.0, 31.50]},
                {"pose":[0.0, 0.0, 31.50+(14.0/2), 0.0, 0.0, 0.0], "scale":[40.0, 66.0, 14.0]},
                {"pose":[0.0, 0.0, 45.50+(98.251/2), 0.0, 0.0, 0.0], "scale":[7.0, 63.0, 98.251]},
                {"pose":[0.0, 0.0, 143.751+(14.0/2), 0.0, 0.0, 0.0], "scale":[66.0, 66.0, 14.0]},

        ]},
        #cfg
        has_tool_changer = False,
        output_enable=[[None, None, 0], [None, None, 0]],
        output_disable=[[None, None, 0], [None, None, 0]],
        # ── fluid path ───────────────────────────────────────────────
        # "" -> unplumbed: the needle still mounts and moves, it just
        # has no pump behind it (and every fluid call says so plainly).
        pump="",
        pump_port=None,
        # ── the fitted needle ────────────────────────────────────────
        # A consumable, so it lives in the scene rather than the CAD.
        # ``needle_length`` is measured from the tip plane the GLB
        # already ends at (157.751), so 0.0 is "the tool as modelled"
        # and nothing moves until a real needle is declared. It is
        # GEOMETRY: the tcp/tip anchors travel with it and so does every
        # immerse / IK solve. ``needle_gauge`` is a property, not
        # geometry — its od is a fraction of a millimetre. See
        # components/pump/needle.py.
        needle_gauge=None,     # e.g. 22
        needle_length=0.0,     # mm past the tip plane, e.g. 50.8 for 2"
    )

    def __init__(self, name: str, cfg: dict, workspace, **kwargs):
        # prm
        prm = deepcopy(Gripper.DEFAULTS) # default
        merge(prm, self.DEFAULTS) # self
        merge(prm, cfg) # cfg
        merge(prm, kwargs) # kwargs

        # update type
        prm.setdefault("type", getattr(self.__class__, "_registered_type", cfg.get("type")))

        # The fitted needle extends the tool: tcp/tip move out by its
        # length, and it gets a collision box of its own so the planner
        # knows the steel is there. Mounting anchors and ``center`` stay
        # put — only the working point travels. Applied to prm BEFORE
        # super().__init__ so the assembly is built with the real
        # geometry rather than patched afterwards.
        needle = Needle(gauge=prm.get("needle_gauge"),
                        length=prm.get("needle_length", 0.0))
        if needle.length:
            body = prm["anchors"]["body"]
            tip_z = body["tip"][2]
            for a in ("tcp", "tip"):
                if a in body:
                    body[a] = needle.extend_anchor(body[a])
            box = needle.collision_box(tip_z)
            if box:
                prm["collision_box"]["body"] = list(prm["collision_box"]["body"]) + [box]

        super().__init__(
            name=name,
            workspace=workspace,
            **prm
        )

        # Bind the fluid path (lazy: the pump may be built after us).
        self._init_pump_link(workspace, prm)
        self.needle = needle
