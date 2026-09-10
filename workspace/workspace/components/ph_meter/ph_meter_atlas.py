"""Atlas Scientific pH probe (Lab Grade) — geometry for the shared
``PhMeter`` family base.

Everything that talks to hardware — the EZO device link, the bus
attachment, the sim-agnostic atomic ops, the operator buttons — lives in
``ph_meter.PhMeter``. This module contributes the mechanical envelope and
nothing else. See that module's docstring for the scene-yaml shape.

Geometry, measured off ph_meter_atlas.glb (mm, z from the flange face):
    0.0 -  40.0   mount body        43.0 dia
   40.0 -  53.5   collar / flange   47.6 dia
   41.5 -  77.1   taper             16.3 dia at its widest
   77.1 - 192.1   probe shaft       12.2 dia
  182.1 - 190.1   glass bulb inside the guard sleeve
"""

from __future__ import annotations

from copy import deepcopy

from mergedeep import merge

from workspace.components.factory import register
from workspace.components.gripper.gripper import Gripper
from workspace.components.ph_meter.ph_meter import PhMeter


@register("ph_meter_atlas")
class PhMeterAtlas(PhMeter):
    DEFAULTS = dict(
        anchors={"body": {"center": [0, 0, 0, 0, 0, 0], "tcp": [0, 0, 192.1, 0, 0, 0], "tip": [0, 0, 192.1, 0, 0, 0]}},
        collision_box =
            {"body":[
                {"pose":[0.0, 0.0, (53.5+192.1)/2, 0.0, 0.0, 0.0], "scale":[16.3, 16.3, 192.1-53.5]},
                {"pose":[0.0, 0.0, (40.0+53.5)/2, 0.0, 0.0, 0.0], "scale":[47.6, 47.6, 53.5-40.0]},
                {"pose":[0.0, 0.0, 40.0/2, 0.0, 0.0, 0.0], "scale":[43.0, 43.0, 40.0]},

        ]},
    )

    def __init__(self, name: str, cfg: dict, workspace, **kwargs):
        # prm
        prm = deepcopy(Gripper.DEFAULTS) # base
        merge(prm, PhMeter.DEFAULTS) # family
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
