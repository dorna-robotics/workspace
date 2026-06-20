from copy import deepcopy
from mergedeep import merge
from workspace.components.factory import register
from workspace.components.inspection.inspection import Inspection


@register("inspection_horizontal")
class InspectionHorizontal(Inspection):
    DEFAULTS = dict(
        anchors={"body":{"center":[0, 0, 0, 0, 0, 0], "camera": [-9, 0, 27-3.7, 0, 0, 0], "place": [-9, 0, 27-3.7+75, 0, 0, 0], "top": [0, 0, 27-3.7+70, 0, 0, 0],
                "hole_0": [0, 31.25, 0, 0, 0, 0], "hole_1": [0, 56.25, 0, 0, 0, 0]}},
        collision_box = {"body":[
                {"pose":[0, 22.5, 27/2, 0, 0, 0], "scale":[46.5, 91.5, 27]},
                {"pose":[-38, 0, 20/2, 0, 0, 0], "scale":[38, 24, 20]},
        ]},
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
