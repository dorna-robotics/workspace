from copy import deepcopy
from mergedeep import merge
from workspace.components.factory import register
from workspace.components.pipettor.pipettor import Pipettor
from workspace.components.pipettor.keyto_wrapper import Keyto



@register("pipettor_sp28_40mm")
class PipettorSP2840mm(Pipettor):
    DEFAULTS = dict(
        anchors={"body": {"center": [0, 0, 0, 0, 0, 0], "tcp":[0, 0, 174+1, 0, 0, 0], "tip": [0, 0, 185.375-1.25, 0, 0, 0]}},
        collision_box = {"body":[
                {"pose":[0,-3.863,90+3.25,0,0,0], "scale":[43,55,180+6.45]}
        ]},
        #cfg
        has_tool_changer = True,
        port=None, # connect only if there is a port
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
        self._simulation_mode = prm["simulation"]

        # device
        self.device_port = prm["port"]
        self.device = None
        connection_status = False
        if self.device_port is not None:
            # init device
            self.device = Keyto(port=self.device_port)
            if self.device.connect():
                connection_status = True
                print(f"✅ {self.name} connected @ {self.device_port}")
            else:
                print(f"❌ {self.name} connection failed @ {self.device_port}")

        if not connection_status:
            self.device = None


    def close(self):
        if self.device is not None and self.device.close():
            print("pipette closed")
            return True
        print("pipette closing failed")
        return False
    
    def simulation(self, mode):
        self._simulation_mode = mode




