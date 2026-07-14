from copy import deepcopy
from mergedeep import merge
from dorna2 import Solid


class Feeder:
    DEFAULTS = dict(
        anchors={"body": {"center": [0, 0, 0, 0, 0, 0], "place":[0, 0, 0, 0, 0, 0], "top": [0, 0, 0, 0, 0, 0]}},
        # All axis-related config grouped under one dict (mirrors core's
        # rail_cfg). ``offset`` is the home-position offset passed to
        # ``home_with_encoder_index``; the rest are motor / encoder /
        # PID parameters forwarded to ``set_axis`` / ``set_pid`` at
        # startup. See projects/syringe/startup.ipynb for usage.
        axis_cfg = {"axis": 7, "offset": 0, "usem":1, "pprm":4000, "tprm":360, "usee":1, "ppre":4000, "tpre":360, "p":0.01, "i":0.0001, "d":0, "duration":100 , "threshold":100},
        num_slots = 16,
        pick_offset = 0,
        vaj=[300, 4000, 10000],
    )
    def __init__(self, name: str, workspace, type=None, **kwargs):
        # prm
        prm = deepcopy(self.DEFAULTS) # default
        merge(prm, kwargs) # self

        # init
        self.name = name
        self.workspace = workspace
        self.type = type

        # assembly
        self.assembly = {
            k: Solid(type=self.type, anchors=prm["anchors"][k], component=self.name, **({"collision_box": cb[k]} if (cb := prm.get("collision_box")) and k in cb else {})) for k in prm["anchors"]
        }

        # slot
        self.slot = {
              "body": ["place"]
        }

        # axis cfg (offset lives inside as ``axis_cfg["offset"]``)
        self.axis_cfg = prm["axis_cfg"]

        # number of positions
        self.num_slots = prm["num_slots"]

        # angular offset of slot 0 in the robot's joint frame
        # (calibration — where the pick anchor lands in the axis cycle)
        self.pick_offset = prm["pick_offset"]

        # vaj
        self.vaj = prm["vaj"]

    def rotate(self, step: int = 1, vaj=None):
        """Rotate the feeder by ``step`` slots, snapped to the slot grid.

        Target is anchored at ``pick_offset`` so successive calls don't
        drift off the slot positions even if the axis is currently nudged
        between slots. ``vaj`` overrides the default speed — used by
        recipe workflows (e.g. mix) that want a slower agitation pace.
        """
        rt = self.workspace.rt
        axis = self.axis_cfg["axis"]
        current = rt.joint()
        new = current[:]
        current_steps = round((new[axis] - self.pick_offset) * (self.num_slots / 360))
        new[axis] = (step + current_steps) * (360 / self.num_slots) + self.pick_offset
        rt.checkpoint()
        vaj = vaj or self.vaj
        return rt.jmove(joint=new, vel=vaj[0], accel=vaj[1], jerk=vaj[2])

    def advance(self): return self.rotate(+1)
    def reverse(self): return self.rotate(-1)

    def operator_actions(self) -> list[dict]:
        return [
            {"label": "Advance", "method": "advance", "icon": "forward"},
            {"label": "Reverse", "method": "reverse", "icon": "backward"},
        ]
