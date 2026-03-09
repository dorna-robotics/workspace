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
        size=[100, 100, 100],  # [dx, dy, dz]
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

        # collision box centered with bottom face at origin
        collision_box = {"body": [
            {"pose": [0.0, 0.0, sz / 2, 0.0, 0.0, 0.0], "scale": [float(sx), float(sy), float(sz)]}
        ]}

        # assembly
        self.assembly = {
            k: Solid(type=self.type, anchors=prm["anchors"][k], component=self.name, **({"collision_box": collision_box[k]} if k in collision_box else {})) for k in prm["anchors"]
        }
