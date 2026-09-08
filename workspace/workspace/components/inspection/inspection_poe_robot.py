from copy import deepcopy
from mergedeep import merge
from workspace.components.factory import register
from workspace.components.inspection.inspection import Inspection


# Robot-mounted PoE camera, measured off inspection_poe_robot.glb:
# body spans x +/-16, y -14.5..61.5, z -58.5..3.96 — it hangs BELOW
# the origin. The lens barrel (r ~13, same as the poe_vertical) is
# centered on (0, 0) in the TOP z=3.96 face, looking along +z; the
# lens reference sits 13.86 behind the face (the poe-family recess).
# Bolts to the robot camholder via the two holes on the z=-5 flange
# plane, like inspection_d405_robot.
@register("inspection_poe_robot")
class InspectionPoeRobot(Inspection):
    DEFAULTS = dict(
        anchors={"body":{"center":[0, 0, 0, 0, 0, 0],
                "lens": [0, 0, -9.9, 0, 0, 0],
                # where the presented item goes: 70 mm out along the
                # lens axis (+z), same convention as the poe stations
                "place": [0, 0, -9.9 + 70, 0, 0, 0],
                "hole_0": [0, 36.25, 0, 0, 0, 0], "hole_1": [0, 56.25, 0, 0, 0, 0]}},
        collision_box = {"body":[
                {"pose":[0.0, 23.5, -27.27, 0, 0, 0], "scale":[32.0, 76.0, 62.46], "padding_enabled": False},#[xyzabc] , [lx,ly,lz]
        ]},
        # This is a Hikrobot GigE (PoE) camera — same driver block as
        # its station siblings: color-only (mode "bgr", not "bgrd"),
        # and stream None keeps the native full frame (a stream dict
        # would be applied as a sensor ROI crop).
        camera_cfg={"type": "hikrobot", "mode": "bgr", "stream": None},
    )

    def __init__(self, name: str, cfg: dict, workspace, **kwargs):
        # prm
        prm = deepcopy(Inspection.DEFAULTS) # default
        merge(prm, self.DEFAULTS) # self
        merge(prm, cfg) # cfg
        merge(prm, kwargs) # kwargs

        # this module only has hole_0/hole_1/place — drop the base's extras
        for h in ("top", "hole_2", "hole_3"):
            prm["anchors"]["body"].pop(h, None)

        # type
        prm.setdefault("type", getattr(self.__class__, "_registered_type", prm.get("type")))

        # init
        super().__init__(name=name, workspace=workspace, **prm)
