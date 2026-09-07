from copy import deepcopy
from mergedeep import merge
from workspace.components.factory import register
from workspace.components.inspection.inspection import Inspection


# Horizontal PoE camera station, measured off inspection_poe_horizontal.glb:
# body spans x 0..35, y -43.5..23.46, z -23..23. Sibling of
# inspection_poe_vertical — same anchor scheme, no top anchor.
@register("inspection_poe_horizontal")
class InspectionPoeHorizontal(Inspection):
    DEFAULTS = dict(
        anchors={"body":{"center":[0, 0, 0, 0, 0, 0],
                "lens": [9.6, 0, 20.5, 0, 0, 0]}},
        collision_box = {"body":[
                {"pose":[17.5, -10.02, 0.0, 0, 0, 0], "scale":[35.0, 66.96, 46.0], "padding_enabled": True},#[xyzabc] , [lx,ly,lz]
        ]},
        # This station carries a Hikrobot GigE (PoE) camera — the driver
        # type is the component's fact, not the scene's: the scene
        # authors only serial_number / ip / port.
        camera_cfg={"type": "hikrobot"},
    )

    def __init__(self, name: str, cfg: dict, workspace, **kwargs):
        # prm
        prm = deepcopy(Inspection.DEFAULTS) # default
        merge(prm, self.DEFAULTS) # self
        merge(prm, cfg) # cfg
        merge(prm, kwargs) # kwargs

        # this module only has center/lens — drop the base's extras
        for h in ("place", "top", "hole_0", "hole_1", "hole_2", "hole_3"):
            prm["anchors"]["body"].pop(h, None)

        # type
        prm.setdefault("type", getattr(self.__class__, "_registered_type", prm.get("type")))

        # init
        super().__init__(name=name, workspace=workspace, **prm)
