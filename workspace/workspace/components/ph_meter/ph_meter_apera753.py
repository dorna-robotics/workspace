"""Apera 753 pH probe — a robot-mounted dip tool.

Kinematic-only sibling of ``ph_meter_atlas``: the tool-changer,
anchors and collision boxes come from ``Gripper``. No device link —
the Apera reads out on its own meter, not over a workspace serial
port; if that changes, follow ``ph_meter_atlas`` (driver → station →
component, device-guide §10 shape A).

Geometry, measured off ph_meter_apera753.glb (mm, z from the flange
face):
      0.0 -  10.0   mount flange       43.0 dia
     10.0 - 100.0   slotted guard cage 38.5 dia over the ribs
    100.0 - 120.0   electrode body     44.5 dia at its widest
    120.0 - 173.5   shaft, tapering to the tip at 173.5
"""

from __future__ import annotations

from copy import deepcopy

from mergedeep import merge

from workspace.components.factory import register
from workspace.components.gripper.gripper import Gripper


@register("ph_meter_apera753")
class PhMeterApera753(Gripper):
    DEFAULTS = dict(
        anchors={"body": {"center": [0, 0, 0, 0, 0, 0], "tcp": [0, 0, 173.5, 0, 0, 0], "tip": [0, 0, 173.5, 0, 0, 0]}},
        # Two boxes: everything wide (flange + cage + electrode body)
        # in one block, the slim tapering shaft in the other, split at
        # the z=120 shoulder.
        collision_box =
            {"body":[
                {"pose":[0.0, 0.0, 120.0/2, 0.0, 0.0, 0.0], "scale":[44.5, 44.5, 120.0]},
                {"pose":[0.0, 0.0, (120.0+173.5)/2, 0.0, 0.0, 0.0], "scale":[12.0, 12.0, 173.5-120.0]},
        ]},
        #cfg
        has_tool_changer = False,
        # Passive dip tool — no IO of its own, so enable/disable are no-ops.
        output_enable=[[None, None, 0], [None, None, 0]],
        output_disable=[[None, None, 0], [None, None, 0]],
        # Wrist roll (j5, degrees) pinned during immerse/retract — same
        # contract as the atlas probe: a glass electrode must not roll
        # while it is inside a tube. null frees the wrist.
        lock_j5=None,
    )

    def __init__(self, name: str, cfg: dict, workspace, **kwargs):
        # prm
        prm = deepcopy(Gripper.DEFAULTS) # default
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

        # Wrist-roll pin for immerse/retract (None = unconstrained).
        v = prm.get("lock_j5")
        self.lock_j5 = None if v is None else float(v)
