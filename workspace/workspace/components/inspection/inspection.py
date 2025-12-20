from dorna2 import Solid
from camera import Camera

class Inspection:
    """
    the tube_cap
    """

    def __init__(self, name: str, workspace,
            type=None,
            anchors={"solid_0":{"center":[0, 0, 0, 0, 0, 0], "camera": [0, 0, 0, 0, 0, 0], "place": [0, 0, 0, 0, 0, 0]}},
            serial_number="",
            stream= {"width":848, "height":480, "fps":15},
            K= None,
            D= None,
            mode="bgrd", 
            filter={}, 
            exposure=None,
            native_res=None,
            simulation=True,
            **kwargs
            ):

        self.name = name
        self.type = type
        self.workspace = workspace

        # solid
        self.assembly = {
            k: Solid(type=self.type, anchors=anchors[k], component=self.name) for k in anchors
        }

        # camera parameter
        self.serial_number = serial_number
        self.stream = stream
        self.K = K
        self.D = D
        self.native_res = native_res
        self.mode = mode
        self.exposure = exposure
        self.filter=filter
        
        # simulation
        self.simulation = simulation

        # connect
        self.camera = None
        if not self.simulation:
            # init camera
            self.camera = Camera()
            self._connect()
            

    # connect to the robot
    def _connect(self):
        return self.camera.connect(
            serial_number=self.serial_number,
            stream=self.stream,
            K=self.K,
            D=self.D,
            native_res=self.native_res,
            mode=self.mode,
            exposure=self.exposure,
            filter=self.filter
        )


    def _close(self):
        return self.camera.close()