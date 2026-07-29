from copy import deepcopy
from mergedeep import merge
from workspace.components.factory import register
from workspace.components.inspection.inspection import Inspection


# Robot-mounted inspection camera. Geometry is identical to
# inspection_horizontal (same anchors, same two collision boxes) — the only
# difference is that this one has NO GLB, so it draws nothing in the viewer.
# Fixed vs robot-mounted is purely where the component sits in the kinematic
# tree (see Inspection.lens_pose), so no code differs: attach it to the
# flange in scene yaml instead of a fixture plate and lens_pose() follows
# the arm.
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

        # no CAD model: solid type "" makes the viewer keep an empty
        # group (meshUrl None) — pose/anchors stay live, nothing drawn,
        # no GLB fetch. The component type above stays for the factory.
        for solid in self.assembly.values():
            solid.type = ""
