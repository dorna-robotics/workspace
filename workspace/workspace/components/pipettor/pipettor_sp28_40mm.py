from copy import deepcopy
from mergedeep import merge
from workspace.components.factory import register
from workspace.components.pipettor.pipettor import Pipettor
from workspace.components.pipettor.keyto_wrapper import Keyto



@register("pipettor_sp28_40mm")
class PipettorSP2840mm(Pipettor):
    DEFAULTS = dict(
        anchors={"body": {"center": [0, 0, 0, 0, 0, 0], "tcp":[0, -104.2, 26.5, 90, 0, 0], "top": [0, -110.2, 26.5, 90, 0, 0]}},
        collision_box = {"body":[
                {"pose":[0,-20,30,0,0,0], "scale":[40,180,60]}
        ]},
        #cfg
        has_tool_changer = True,
        port="",
        simulation= True,
    )

    def __init__(self, name: str, cfg: dict, workspace, **kwargs):
        # prm
        prm = deepcopy(Pipettor.DEFAULTS) # default
        merge(prm, self.DEFAULTS) # self
        merge(prm, cfg) # cfg
        merge(prm, kwargs) # kwargs
        
        # update type
        prm.setdefault("type", getattr(self.__class__, "_registered_type", cfg.get("type")))
        
        super().__init__(
            name=name,
            workspace=workspace,
            **prm
        )

        # simulation
        self.simulation = prm["simulation"]

        # device
        self.device_port = prm["port"]
        self.device = None
        if not self.simulation:
            # init camera
            self.device = Keyto(port=self.device_port)
            self.connect()


    def connect(self):
        if self.device is not None and self.device.connect():
            print("pipette connected")
            return True
        print("pipette connecting failed")
        return False


    def close(self):
        if self.device is not None and self.device.close():
            print("pipette closed")
            return True
        print("pipette closing failed")
        return False



