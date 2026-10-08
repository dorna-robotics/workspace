from copy import deepcopy
from mergedeep import merge
from workspace.components.factory import register
from workspace.components.tool_rack.tool_rack import ToolRack



@register("tool_rack_144mm")
class ToolRack144mm(ToolRack):
    DEFAULTS = dict(
        anchors = {"body": {"center": [0,0,0,0,0,0], "place": [0, 46, 153, 127.27922061357856, -127.27922061357854, 0],
            "hole_0":[25, 25, 0, 0, 0, 0], "hole_1": [-25, 25, 0, 0, 0, 0], "hole_2": [-25, -25, 0, 0, 0, 0], "hole_3": [25, -25, 0, 0, 0, 0],
            "clb_0": [0, 15, 154, 0, 0, 0]}},
        # Two boxes, as tool_rack_192mm (48 mm lower: the tool docks at z 153, not
        # 201): the rack with its docked tool (up to the changer's top, 168), and
        # the column behind the tool where a wired tool's cable runs up (rack back
        # y -52.5 to the changer's back face y 22). A tool without a cable (a
        # gripper) overrides this with the first box only, in the scene.
        collision_box = 
            {"body":[
                {"pose":[0, 13.75, 168/2, 0.0, 0.0, 0.0], "scale":[65, 92.5+40, 168], "padding_enabled": True},#[xyzabc] , [lx,ly,lz]
                {"pose":[0, (-52.5+22)/2, (158+600)/2, 0.0, 0.0, 0.0], "scale":[65, 22+52.5, 158+600], "padding_enabled": True},
        ]}
    )

    def __init__(self, name: str, cfg: dict, workspace, **kwargs):
        # prm
        prm = deepcopy(ToolRack.DEFAULTS) # default
        merge(prm, self.DEFAULTS) # self
        merge(prm, cfg) # cfg
        merge(prm, kwargs) # kwargs

        # type
        prm.setdefault("type", getattr(self.__class__, "_registered_type", prm.get("type")))

        # init
        super().__init__(name=name, workspace=workspace, **prm)