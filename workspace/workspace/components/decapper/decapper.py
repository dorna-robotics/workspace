from copy import deepcopy
from mergedeep import merge
from workspace.components.factory import register
from dorna2 import Solid


@register("decapper")
class Decapper:
    DEFAULTS = dict(
        anchors={"body": {"center": [0, 0, 0, 0, 0, 0], "place":[0, 0, 45, 0, 0, 0], "top":[0, 0, 55, 0, 0, 0],
            "hole_0":[25, 25, 0, 0, 0, 0], "hole_1": [-25, 25, 0, 0, 0, 0], "hole_2": [-25, -25, 0, 0, 0, 0], "hole_3": [25, -25, 0, 0, 0, 0],
            "clb_0": [0, 25, 7, 0, 0, 0], "clb_1": [0, -25, 7, 0, 0, 0]}},
        collision_box = 
            {"body":[
                {"pose":[0.0, 0.0, 24.0, 0.0, 0.0, 0.0], "scale":[72, 68, 62]}   #[xyzabc] , [lx,ly,lz]
        ]},
        # cfg
        output_enable = [[None, None, 0.1]], # [[pin, index, time]]
        output_disable = [[None, None, 0.25], [None, None, 0.1]], # [[pin, index, time]]
    )

    def __init__(self, name: str, cfg: dict, workspace, **kwargs):
        # prm
        prm = deepcopy(self.DEFAULTS) # default
        merge(prm, cfg) # cfg
        merge(prm, kwargs) # kwargs

        # update type
        prm.setdefault("type", getattr(self.__class__, "_registered_type", cfg.get("type")))

        # init
        self.name = name
        self.workspace = workspace
        self.type = prm["type"]
        
        # assembly
        self.assembly = {
            k: Solid(type=self.type, anchors=prm["anchors"][k], component=self.name, **({"collision_box": cb[k]} if (cb := prm.get("collision_box")) and k in cb else {})) for k in prm["anchors"]
        }

        # slot
        self.slot = {
           "body": ["place"] 
        }

        # open and close
        self.output_enable = prm["output_enable"]
        self.output_disable = prm["output_disable"]

        # io state
        self._output_state = None


    # set or get output state
    def output_state(self, state=None):
        if state is None:
            return self._output_state
        self._output_state = state
        return self._output_state
