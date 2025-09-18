# workspace/components/core.py
from dorna2 import Solid, Dorna
from workspace.components.factory import register


@register("microtube")
class microtube:
    """
    the tube
    """

    def __init__(self, name: str, cfg: dict):
        self.name = name
        self.type = "microtube"
        self.assembly = {}

        anchors = {}
        anchors["center"] = [0,0,0,0,0,0]


        self.assembly["microtube"] = Solid(name="microtube", type="microtube", anchors=anchors, component=self.name)