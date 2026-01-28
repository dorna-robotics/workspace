from copy import deepcopy
from mergedeep import merge
from workspace.components.factory import register
from workspace.components.syringe.arm import Arm


@register("syringe_dispense_arm")
class SuctionGripper(Arm):
    DEFAULTS = dict(
        anchors={"body":{"center":[0, 0, 0, 0, 0, 0], "place": [75, 0, 0, 0, 0, 0], "top": [0, 0, 170, 0, 0, 0],
                "hole_0":[25, 25, 0, 0, 0, 0], "hole_1": [-25, 25, 0, 0, 0, 0], "hole_2": [-25, -25, 0, 0, 0, 0], "hole_3": [25, -25, 0, 0, 0, 0]}},
        collision_box = 
            {"body":[
                {"pose":[0.0, 0.0, 4.15, 0.0, 0.0, 0.0], "scale":[67.93, 67.93, 10.724]},   #[xyzabc] , [lx,ly,lz]
                {"pose":[0.0, 0.0, 39.19, 0.0, 0.0, 0.0], "scale":[44.7, 44.7, 71.38]},
                {"pose":[0.0, 0.0, 90.15, 0.0, 0.0, 0.0], "scale":[18.3, 18.3, 33.56]},
                {"pose":[38.49, 0.0, 100.24, 0.0, 0.0, 0.0], "scale":[93.62, 18.31, 11.25]},
                {"pose":[75.40, 0.0 , 70.72, 0.0, 0.0, 0.0], "scale":[8.67, 8.67, 83.10]}
        ]},
        # cfg
        output_enable = [[None, None, 0.1]], # [[pin, index, time]]
        output_disable = [[None, None, 0.1]], # [[pin, index, time]]
    )

    def __init__(self, name: str, cfg: dict, workspace, **kwargs):
        # prm
        prm = deepcopy(Arm.DEFAULTS) # default
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