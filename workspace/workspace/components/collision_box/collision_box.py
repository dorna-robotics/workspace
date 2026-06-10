from copy import deepcopy
from mergedeep import merge
from dorna2 import Solid
from workspace.components.factory import register


@register("collision_box")
class CollisionBox:
    DEFAULTS = dict(
        anchors={
            "body": {"center": [0, 0, 0, 0, 0, 0]},
        },
        size=[100, 100, 100],   # [dx, dy, dz]
        # Box-center offset from the component origin, [dx, dy, dz].
        # Default [0, 0, 0] = box centered on the origin in X/Y with its
        # bottom face on the origin in Z. The scene builder sets this
        # when you resize a single face (asymmetric resize) so the
        # origin stays put while one face moves.
        box_offset=[0, 0, 0],
    )

    def __init__(self, name: str, cfg: dict, workspace, **kwargs):
        # prm
        prm = deepcopy(self.DEFAULTS) # default
        merge(prm, cfg) # cfg
        merge(prm, kwargs) # kwargs
        
        # type
        prm.setdefault("type", getattr(self.__class__, "_registered_type", prm.get("type")))

        # init
        self.name = name
        self.workspace = workspace
        self.type = prm["type"]

        # size
        self.size = prm["size"]
        sx, sy, sz = self.size

        # box-center offset (default keeps the box centered in X/Y with
        # the bottom face on the origin in Z)
        bx, by, bz = (list(prm.get("box_offset") or [0, 0, 0]) + [0, 0, 0])[:3]

        # collision box: centre at (bx, by, sz/2 + bz) so box_offset
        # [0,0,0] reproduces the legacy "bottom face on the origin"
        # placement, and a non-zero offset shifts the box while the
        # component origin stays fixed.
        collision_box = {"body": [
            {"pose": [float(bx), float(by), sz / 2 + float(bz), 0.0, 0.0, 0.0], "scale": [float(sx), float(sy), float(sz)]}
        ]}

        # assembly
        self.assembly = {
            k: Solid(type=self.type, anchors=prm["anchors"][k], component=self.name, **({"collision_box": collision_box[k]} if k in collision_box else {})) for k in prm["anchors"]
        }
