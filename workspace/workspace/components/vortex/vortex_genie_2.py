from __future__ import annotations
from copy import deepcopy
from mergedeep import merge
from dorna2 import Solid
from workspace.components.factory import register


@register("vortex_genie_2")
class VortexGenie2:

    DEFAULTS = dict(
        # IO — the Genie's mains switch through a robot output pin
        # ([[pin, index, time]] rows, same shape as the shaker's). The
        # scene entry sets the real pin; with the defaults nothing
        # physically switches.
        output_enable=[[None, None, 0.1]],
        output_disable=[[None, None, 0.1]],
        anchors={
            "body": {
                "center": [0, 0, 0, 0, 0, 0],
                "top":    [0, 0, 164.4, 0, 0, 0],   # cup platform, top face
                "place":  [0, 37, 160.0, 0, 0, 0],  # item sits on the cup
                "hole_0": [ 25,  75, 0, 0, 0, 0],  # placeholder
                "hole_1": [-25,  75, 0, 0, 0, 0],  # placeholder
                "hole_2": [-25, -75, 0, 0, 0, 0],  # placeholder
                "hole_3": [ 25, -75, 0, 0, 0, 0],  # placeholder
            },
        },
        collision_box={
            "body": [
                # full-body AABB measured from CAD; [x,y,z,a,b,c], [lx,ly,lz]
                {"pose": [0.0, -6.119, 78.0, 0.0, 0.0, 0.0], "scale": [120.0, 177.238, 156.0], "padding_enabled": True},
            ],
        },
    )

    def __init__(self, name: str, cfg: dict, workspace, **kwargs):
        # prm
        prm = deepcopy(self.DEFAULTS) # default
        merge(prm, cfg) # cfg
        merge(prm, kwargs) # kwargs

        # type
        prm.setdefault("type", getattr(self.__class__, "_registered_type", cfg.get("type")))

        self.name = name
        self.workspace = workspace
        self.type = prm["type"]

        # assembly
        self.assembly = {
            k: Solid(
                type=self.type,
                anchors=prm["anchors"][k],
                component=self.name,
                **({"collision_box": cb[k]} if (cb := prm.get("collision_box")) and k in cb else {}),
            )
            for k in prm["anchors"]
        }

        # slot — you place items on the cup
        self.slot = {"body": ["place"]}

        # IO — NOT named output_state on purpose: that attribute would
        # wire these rows into every pick/place trigger_io sequence
        # (recipe._build_io_config), and the Genie's mains switch has
        # no business firing at touch-down.
        self.output_enable = prm["output_enable"]
        self.output_disable = prm["output_disable"]
        self._run_state = 0  # 1 running, 0 off

    # ── atomic ops (component-guide §7) ───────────────────────────────
    # The component owns the switch; the Vortex recipe owns the
    # workflow (flush the held exit, run for a duration, stop).

    def run_state(self, state=None):
        if state is None:
            return self._run_state
        self._run_state = state
        return self._run_state

    def enable(self) -> bool:
        """Switch the vortexer ON. Idempotent."""
        if self._run_state != 1:
            self.workspace.rt.output(config=self.output_enable)
            self._run_state = 1
        return True

    def disable(self) -> bool:
        """Switch the vortexer OFF. Idempotent."""
        if self._run_state != 0:
            self.workspace.rt.output(config=self.output_disable)
            self._run_state = 0
        return True

    def operator_actions(self) -> list[dict]:
        return [
            {"label": "Enable",  "method": "enable",  "icon": "power",     "group": "vortex"},
            {"label": "Disable", "method": "disable", "icon": "power-off", "group": "vortex"},
        ]
