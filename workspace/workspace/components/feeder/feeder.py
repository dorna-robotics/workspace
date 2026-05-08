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

        # vaj
        self.vaj = prm["vaj"]
