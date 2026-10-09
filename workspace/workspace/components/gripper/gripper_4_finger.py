from copy import deepcopy
from mergedeep import merge
from workspace.components.factory import register
from workspace.components.gripper.gripper import Gripper


@register("gripper_4_finger")
class Gripper4Finger(Gripper):
    DEFAULTS = dict(
        #anchors={"body": {"center": [0, 0, 0, 0, 0, 0], "tcp":[0+0.7-0.3, 0.3+0.7, 66.5, 0, 0, -45], "tip": [0, 0, 69.5, 0, 0, -45]}},
        anchors={"body": {"center": [0, 0, 0, 0, 0, 0], "tcp":[0, 0, 66.5-1+2.5, 0, 0, -45], "tip": [0, 0, 69.5+2.5, 0, 0, -45]}},
        # two boxes, from the mesh: the body (±23.3 mm) up to 53.5 mm above the plate, then
        # the four fingers (±10.7 mm closed; 26 mm square leaves them room to open) to the tip.
        # One 47 mm box down to the tip made every corner slot of racks spaced 9 mm apart
        # "hit" the neighbouring rack.
        collision_box =
            {"body":[
                {"pose":[0.0, 0.0, (-1.5+56)/2, 0.0, 0.0, 0.0], "scale":[47.0, 47.0, 56+1.5]},#[xyzabc] , [lx,ly,lz]
                {"pose":[0.0, 0.0, (56+72)/2, 0.0, 0.0, 0.0], "scale":[26.0, 26.0, 72-56]},
        ]},
        #cfg
        has_tool_changer = False,
        output_enable=[[1, 0, 0], [0, 0, 0.5]],
        output_disable=[[0, 1, 0.5], [1, 1, 0.75], [1, 0, 0.1]],
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