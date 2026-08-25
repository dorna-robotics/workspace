from copy import deepcopy
from mergedeep import merge
from workspace.components.factory import register
from workspace.components.rack.rack import Rack

@register("rack_liquid")
class RackLiquid(Rack):
    """Storage-solution cup the pH probe parks in between reads.

    Measured off storage_liquid_container.glb (mm, origin at the base,
    centered in XY):
        outer            30.0 x 85.0 x 38.0
        wall             2.0 all round
        well             26.0 x 81.0, floor at z=8.0, open rim at z=38.0
        mounting holes   5.0 dia, 6.1 deep, at y=+23.5 and y=-26.5

    One solid collision box, measured off rack_liquid.glb: the body is
    30 x 85 x 38 mm, origin at the XY centre with z = 0 the bottom
    face. Nothing dips into this rack — nozzles dispense from above —
    so a solid block is correct (and the previous 192 mm-tall box was
    from some other part entirely).
    """

    DEFAULTS = dict(
        anchors =
            {"body": {
                "center": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
                "top": [0.0, 0.0, 38.0, 0.0, 0.0, 0.0],
                "hole_0": [0.0, 23.5, 0.0, 0.0, 0.0, 0.0],
                "hole_1": [0.0, -26.5, 0.0, 0.0, 0.0, 0.0],
        }},
        collision_box =
                    {"body":[
                        {"pose":[0.0, 0.0, 38.0/2, 0.0, 0.0, 0.0], "scale":[30.0, 85.0, 38.0], "padding_enabled": True},
                ]},
        offset=[0, 0, 45],
        pitch=[0, 0, 0],
        rvec_safe = [0, 0, 0],
        rows=[chr(c) for c in range(ord("A"), ord("A") + 1)],
        cols= [i for i in range(1, 1+1)],
    )

    def __init__(self, name: str, cfg: dict, workspace, **kwargs):
        # prm
        prm = deepcopy(Rack.DEFAULTS) # default
        merge(prm, self.DEFAULTS) # self
        merge(prm, cfg) # cfg
        merge(prm, kwargs) # kwargs
        
        # type
        prm.setdefault("type", getattr(self.__class__, "_registered_type", prm.get("type")))
        
        # init
        super().__init__(name=name, workspace=workspace, **prm)

