from copy import deepcopy
from mergedeep import merge
from workspace.components.factory import register
from workspace.components.inspection.inspection import Inspection


# Robot-mounted inspection camera (D405 + camholder bracket), drawn from
# its own lightweight GLB. Auto-added by Core when has_camera is true and
# bolted to robot_A5's camholder holes — fixed vs robot-mounted is purely
# where the component sits in the kinematic tree (see Inspection.lens_pose),
# so no code differs from a fixed station.
@register("inspection_d405_robot")
class InspectionD405Robot(Inspection):
    DEFAULTS = dict(
        anchors={"body":{"center":[0, 0, 0, 0, 0, 0], "lens": [-9, 0, 27-3.7, 0, 0, 0],
                "hole_0": [0, 38, 5, 0, 0, 0], "hole_1": [0, 58, 5, 0, 0, 0]}},
        collision_box = {
            #"body":[
            #{"pose":[0, 20, 27/2, 0, 0, 0], "scale":[46.5, 86.5, 27], "padding_enabled": True}]
        },
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
