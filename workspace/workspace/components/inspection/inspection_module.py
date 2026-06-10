from copy import deepcopy
from mergedeep import merge
from workspace.components.factory import register
from workspace.components.inspection.inspection import Inspection


@register("inspection_module")
class InspectionModule(Inspection):
    DEFAULTS = dict(
        anchors={"body":{"center":[0, 0, 0, 0, 0, 0], "camera": [9, 0, 27, 0, 0, 180], "place": [9, 0, 37, 0, 0, 180], "top": [0, 0, 27, 0, 0, 0],
                "hole_0": [0, 31.25, 0, 0, 0, 0], "hole_1": [0, 56.25, 0, 0, 0, 0]}},
        collision_box = {"body":[
                {"pose":[-(38/2), 22.5, 27/2, 0, 0, 0], "scale":[45.5+39, 91.5, 27]},#[xyzabc] , [lx,ly,lz]
        ]},
        camera = {
            "stream": {"width":848, "height":480, "fps":15},
            "K": None,
            "D": None,
            "mode": "bgrd",
            "filter": {},
            "exposure": None,
            "native_res": None,
        },
        # cfg
        camera_serial_number="",
        simulation=True,
    )

    def __init__(self, name: str, cfg: dict, workspace, **kwargs):
        # prm
        prm = deepcopy(Inspection.DEFAULTS) # default
        merge(prm, self.DEFAULTS) # self
        merge(prm, cfg) # cfg
        merge(prm, kwargs) # kwargs

        # this module only has hole_0/hole_1 — drop the base's extra holes
        for h in ("hole_2", "hole_3"):
            prm["anchors"]["body"].pop(h, None)

        # type
        prm.setdefault("type", getattr(self.__class__, "_registered_type", prm.get("type")))

        # init
        super().__init__(name=name, workspace=workspace, **prm)
