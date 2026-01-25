from copy import deepcopy
from mergedeep import merge
from workspace.components.factory import register
from workspace.components.syringe.arm import Arm


@register("syringe_dispense_arm")
class SuctionGripper(Arm):
    DEFAULTS = dict(
        anchors={"body":{"center":[0, 0, 0, 0, 0, 0], "place": [75, 0, 0, 0, 0, 0],
                "hole_0":[25, 25, 0, 0, 0, 0], "holde_1": [-25, 25, 0, 0, 0, 0], "holse_2": [-25, -25, 0, 0, 0, 0], "holde_3": [25, -25, 0, 0, 0, 0]}},
        collision_box = 
            {"body":[
                {"pose":[0.0, 0.0, 4.0, 0.0, 0.0, 0.0], "scale":[68, 68, 10.5]},   #[xyzabc] , [lx,ly,lz]
                {"pose":[0.0, 0.0, 39.0, 0.0, 0.0, 0.0], "scale":[44.5, 44.5, 71.5]},
                {"pose":[0.0, 0.0, 90.0, 0.0, 0.0, 0.0], "scale":[18.5, 18.5, 33.5]},
                {"pose":[38.5, 0.0, 100.0, 0.0, 0.0, 0.0], "scale":[93.5, 18.5, 11]},
                {"pose":[75.5, 0.0 , 70.5, 0.0, 0.0, 0.0], "scale":[8.5, 8.5, 83]}
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