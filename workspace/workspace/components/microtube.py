# workspace/components/microtube.py
from dorna2 import Solid, Dorna
from workspace.components.factory import register


@register("microtube")
class MicroTube:
    """
    the tube
    """

    def __init__(self, name: str, cfg: dict, workspace):
        self.name = name
        self.type = "microtube"
        self.workspace = workspace
        self.assembly = {}

        anchors = {}
        anchors["center"] = [0,0,0,0,0,0]
        anchors["gripping_point"] = [0,0,42,180,0,0]



        self.assembly["microtube"] = Solid(name="microtube", type="microtube", anchors=anchors, component=self.name)