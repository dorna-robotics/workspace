from copy import deepcopy
from mergedeep import merge
from dorna2 import Solid


class Inspection:
    DEFAULTS = dict(
        anchors={"body":{"center":[0, 0, 0, 0, 0, 0], "camera": [0, 0, 0, 0, 0, 0], "place": [0, 0, 0, 0, 0, 0],
                "hole_0":[25, 25, 0, 0, 0, 0], "hole_1": [-25, 25, 0, 0, 0, 0], "hole_2": [-25, -25, 0, 0, 0, 0], "hole_3": [25, -25, 0, 0, 0, 0],}},
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
            k: Solid(type=self.type, anchors=prm["anchors"][k], component=self.name, **({"collision_box": cb[k]} if (cb := prm.get("collision_box")) and k in cb else {})) for k in prm["anchors"]
        }

        # slot
        self.slot = {
           "body": ["place"]
        }

        # simulation
        self.simulation = prm["simulation"]

        # camera parameter
        self.camera_cfg = prm["camera_cfg"]
        self.camera_serial_number = prm["camera_serial_number"]

        # initialize the camera
        self.camera = None
        if self.camera_serial_number:
            try:
                from camera import Camera
                # init camera
                self.camera = Camera()
                if not self.camera.connect(serial_number=self.camera_serial_number, **self.camera_cfg):
                   self.camera = None 
            except Exception as e:
                self.camera = None
                print(f"camera connection failed {e}")

            if self.camera is not None:
                print(f"✅ {self.name} connected @ {self.camera_serial_number}")
            else:
                print(f"❌ {self.name} connection failed @ {self.camera_serial_number}")


    # close the camera
    def close(self):
        if self.camera is not None:
            return self.camera.close()