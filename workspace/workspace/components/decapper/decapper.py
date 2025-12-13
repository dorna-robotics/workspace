from workspace.components.factory import register
from dorna2 import Solid

@register("decapper")
class Decapper:
    def __init__(self, name: str, cfg: dict, workspace,
            anchors = {
                "solid_0": {"center": [0, 0, 0, 0, 0, 0], "place":[0, 0, 45, 0, 0, 0], "top":[0, 0, 55, 0, 0, 0]},
                },
            height=0,
            enable=[0, 0],
            **kwargs
            ):
        self.name = name
        self.type = getattr(self.__class__, "_registered_type", cfg.get("type"))
        self.workspace = workspace
        
        # assembly
        self.assembly = {
            k: Solid(type=self.type, anchors=anchors[k], component=self.name) for k in anchors
        }
        
        # height
        self.height = height
        
        # open and close
        self.enable = [[cfg.get("enable", enable)[0], cfg.get("enable", enable)[1], 0.25]]
        self.disable = [[cfg.get("enable", enable)[0], int(not cfg.get("enable", enable)[1]), 0.25]]
