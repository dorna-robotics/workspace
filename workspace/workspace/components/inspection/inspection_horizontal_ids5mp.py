from copy import deepcopy
from mergedeep import merge
from workspace.components.factory import register
from workspace.components.inspection.inspection import Inspection


# Horizontal inspection station carrying the IDS uEye XS (5MP) — the
# same station shape as inspection_horizontal, different camera holder.
# Mesh: static/CAD/inspection_horizontal_ids5mp.glb (the viewer resolves
# the GLB from the component type).
#
# Measured off the mesh: body 23 x 76.75 x 26.65, x +-11.5, y -14..62.75,
# z 0..26.65 — the collision box below is that envelope exactly.
# The lens / hole anchors below are PLACEHOLDERS; adjust them for this
# holder's real geometry (the lens anchor is what camera_in_world and
# roi.box projection stand on — calibrate it).
@register("inspection_horizontal_ids5mp")
class InspectionHorizontalIDS5MP(Inspection):
    DEFAULTS = dict(
        anchors={"body":{"center":[0, 0, 0, 0, 0, 0], "lens": [0, -3.75, 25.4, 0, 0, 0],
                "hole_1": [0, 37.5, 5, 0, 0, 0], "hole_3": [0, 57.5, 5, 0, 0, 0],
                "hole_0": [0, 22.5, 0, 0, 0, 0], "hole_2": [0, 47.5, 0, 0, 0, 0]}},
        collision_box = {"body":[
                {"pose":[0, 24.375, 26.65/2, 0, 0, 0], "scale":[23.0, 76.75, 26.65], "padding_enabled": True}]},
        # This holder carries the IDS uEye XS — the driver type is the
        # component's fact, not the scene's: the scene authors only
        # serial_number / ip / port (and optionally focus).
        camera_cfg={"type": "ueye_xs"},
    )

    def __init__(self, name: str, cfg: dict, workspace, **kwargs):
        # prm
        prm = deepcopy(Inspection.DEFAULTS) # default
        merge(prm, self.DEFAULTS) # self
        merge(prm, cfg) # cfg
        merge(prm, kwargs) # kwargs

        # type
        prm.setdefault("type", getattr(self.__class__, "_registered_type", prm.get("type")))

        # init
        super().__init__(name=name, workspace=workspace, **prm)
