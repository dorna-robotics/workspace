import numpy as np
from copy import deepcopy
from mergedeep import merge
from workspace.components.factory import register
from workspace.components.tube.tube import Tube

@register("printer_axon_1")
class PrinterAxon1(Tube):
    DEFAULTS = dict(
        anchors={
            "body": {"center":[0, 0, 0, 0, 0, 0], "top": [0, 0, 190, 0, 0, 0], "place":[132.865, 34.16, 93.5, 0, 0, 0]},
        }
    )

    def __init__(self, name: str, cfg: dict, workspace, **kwargs):
        # prm
        prm = deepcopy(Tube.DEFAULTS) # default
        merge(prm, self.DEFAULTS) # self
        merge(prm, cfg) # cfg
        merge(prm, kwargs) # kwargs

        # type
        prm.setdefault("type", getattr(self.__class__, "_registered_type", prm.get("type")))
        
        # init
        super().__init__(name=name, workspace=workspace, **prm)


    def place_offset(self, radius):
        return(
            [np.cos(np.deg2rad(30))*(np.sqrt(radius**2 + 7.1) - 100),
            np.sin(np.deg2rad(30))*(np.sqrt(radius**2 + 7.1) - 100),
            0,
            0,
            0,
            0]
        )