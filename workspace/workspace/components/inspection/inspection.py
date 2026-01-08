from copy import deepcopy
from mergedeep import merge
from dorna2 import Solid


class Inspection:
    DEFAULTS = dict(
        anchors={"body":{"center":[0, 0, 0, 0, 0, 0], "camera": [0, 0, 0, 0, 0, 0], "place": [0, 0, 0, 0, 0, 0]}},
        camera_cfg={
            "stream": {"width":848, "height":480, "fps":15},
            "K": None,
            "D": None,
            "mode": "bgrd", 
            "filter": {}, 
            "exposure": None,
            "native_res": None,
        },
        # cfg
        camera_serial_number="",
        simulation=True,
    )

    def __init__(self, name: str, workspace, type=None, **kwargs):
        # prm
        prm = deepcopy(self.DEFAULTS) # default
        merge(prm, kwargs) # self

        # init
        self.name = name
        self.workspace = workspace
        self.type = type

        # assembly
        self.assembly = {
            k: Solid(type=self.type, anchors=prm["anchors"][k], component=self.name) for k in prm["anchors"]
        }
        
        # simulation
        self.simulation = prm["simulation"]

        # camera parameter
        self.camera_cfg = prm["camera_cfg"]
        self.camera_serial_number = prm["camera_serial_number"]

        # initialize the camera
        self.camera = None
        if not self.simulation:
            try:
                from camera import Camera
                # init camera
                self.camera = Camera()
                self.connect()
            except Exception as ex:
                print(f"[Camera disabled] {e}")
            

    # connect to the camera
    def connect(self):
        if self.camera.connect(serial_number=self.camera_serial_number, **self.camera_cfg):
            print("camera connected")
            return True
        print("can not connect to the camera")
        return False


    # close the camera
    def close(self):
        if self.camera is not None:
            return self.camera.close()