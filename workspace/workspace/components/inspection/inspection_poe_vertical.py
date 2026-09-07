from copy import deepcopy
from mergedeep import merge
from workspace.components.factory import register
from workspace.components.inspection.inspection import Inspection


# Vertical PoE camera station, measured off inspection_poe_vertical.glb:
# body spans x -29..6, y -43.5..14.5, z -28.5..33.96; the lens barrel
# (r ~13) sits on the top z=33.96 face, centered on (x=-14.5, y=-29),
# looking up.
@register("inspection_poe_vertical")
class InspectionPoeVertical(Inspection):
    DEFAULTS = dict(
        anchors={"body":{"center":[0, 0, 0, 0, 0, 0],
                "lens": [-14.5, -29, 20.1, 0, 0, 0],
                "top": [-14.5, -29, 33.96, 0, 0, 0],
                # where the presented item goes: 70 mm out along the
                # lens axis (+z, looking up)
                "place": [-14.5, -29, 20.1 + 70, 0, 0, 0]}},
        collision_box = {"body":[
                {"pose":[-11.5, -14.5, 2.73, 0, 0, 0], "scale":[35.0, 58.0, 62.46], "padding_enabled": True},#[xyzabc] , [lx,ly,lz]
        ]},
        # This station carries a Hikrobot GigE (PoE) camera — the driver
        # type is the component's fact, not the scene's: the scene
        # authors only serial_number / ip / port. The base defaults are
        # D405-shaped and wrong for this device: it is color-only
        # (mode "bgr", not "bgrd"), and a stream dict would be taken as
        # a sensor ROI — None keeps the native full frame.
        camera_cfg={"type": "hikrobot", "mode": "bgr", "stream": None},
    )

    def __init__(self, name: str, cfg: dict, workspace, **kwargs):
        # prm
        prm = deepcopy(Inspection.DEFAULTS) # default
        merge(prm, self.DEFAULTS) # self
        merge(prm, cfg) # cfg
        merge(prm, kwargs) # kwargs

        # this module only has center/lens/top/place — drop the base's extras
        for h in ("hole_0", "hole_1", "hole_2", "hole_3"):
            prm["anchors"]["body"].pop(h, None)

        # type
        prm.setdefault("type", getattr(self.__class__, "_registered_type", prm.get("type")))

        # init
        super().__init__(name=name, workspace=workspace, **prm)
