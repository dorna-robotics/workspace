from copy import deepcopy
from mergedeep import merge
from workspace.components.factory import register
from workspace.components.rack.rack import Rack

@register("rack_autosampler_2ml_7x8")
class RackAutosampler2ml7x8(Rack):
    """7x8 autosampler 2 mL rack.

    Measured off rack_autosampler_2ml_7x8.glb (mm, origin at the base,
    centered in XY): block 165.0 x 145.0 x 15.0, pocket floors at
    z=2.0, 8 columns (1-8, +x) by 7 rows (A-G, +y) on a 20.0 mm pitch,
    A1 at (-70.0, -60.0).
    """
    DEFAULTS = dict(
        anchors={"body": {"center":[0, 0, 0, 0, 0, 0], "place": [0, 0, 2, 0, 0, 0], "top": [0, 0, 15, 0, 0, 0]}},
        collision_box =
            {"body":[
                {"pose":[0.0, 0.0, 15/2, 0.0, 0.0, 0.0], "scale":[165.0, 145.0, 15], "padding_enabled": True}#[xyzabc] , [lx,ly,lz]
        ]},
        offset=[-20.0*(8-1)/2, -20.0*(7-1)/2, 2],
        pitch=[20.0, 20.0, 0],
        rvec_safe=[0, 0, 45],
        rows=[chr(c) for c in range(ord("A"), ord("G") + 1)],
        cols= [i for i in range(1, 8+1)],
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
