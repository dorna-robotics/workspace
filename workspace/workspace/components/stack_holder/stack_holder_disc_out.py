from copy import deepcopy
from mergedeep import merge
from workspace.components.factory import register
from workspace.components.stack_holder.stack_holder import StackHolder

@register("stack_holder_disc_out")
class StackHolderDiscOut(StackHolder):
    DEFAULTS = dict(
        anchors={"body": {"center":[0, 0, 0, 0, 0, 0], "place": [0, 0, 8, 0, 0, 0], "top": [0, 0, 68, 0, 0, 0]}},
        collision_box =
            {"body":[
                {"pose":[0, 0, 34, 0, 0, 0], "scale":[237, 42, 68]}#[xyzabc] , [lx,ly,lz]
        ]},
        offset=[-83.139, 0, 8],            # A1: x (3*pitch so col 4 lands on 0), y, slot z
        pitch=[27.713, 0],               # x step per col, y step per row
        rvec_safe=[0, 0, 0],
        rows=[chr(c) for c in range(ord("A"), ord("A") + 1)], # A — single row (1 deep in Y)
        cols= [i for i in range(1, 7 + 1)],                   # 1-7 columns along X → A1..A7
    )

    def __init__(self, name: str, cfg: dict, workspace, **kwargs):
        # prm
        prm = deepcopy(StackHolder.DEFAULTS) # default
        merge(prm, self.DEFAULTS) # self
        merge(prm, cfg) # cfg
        merge(prm, kwargs) # kwargs

        # type
        prm.setdefault("type", getattr(self.__class__, "_registered_type", prm.get("type")))

        # init
        super().__init__(name=name, workspace=workspace, **prm)
