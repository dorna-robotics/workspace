import numpy as np
from copy import deepcopy
from mergedeep import merge
from workspace.components.factory import register
from workspace.components.printer.printer import Printer
from workspace.components.printer.cab_wrapper import Cab


@register("printer_axon_1")
class PrinterAxon1(Printer):
    DEFAULTS = dict(
        anchors={
            "body": {"center":[0, 0, 0, 0, 0, 0], "top": [0, 0, 190, 0, 0, 0], "place":[132.865, 34.16, 93.5, 0, 0, 0]},
        },
        # cfg
        ip="127.0.0.1",
        simulation=True,
    )

    def __init__(self, name: str, cfg: dict, workspace, **kwargs):
        # prm
        prm = deepcopy(Printer.DEFAULTS) # default
        merge(prm, self.DEFAULTS) # self
        merge(prm, cfg) # cfg
        merge(prm, kwargs) # kwargs

        # type
        prm.setdefault("type", getattr(self.__class__, "_registered_type", prm.get("type")))
        
        # init
        super().__init__(name=name, workspace=workspace, **prm)

        # simulation
        self.simulation = prm["simulation"]

        # printer
        self.device = None
        if not self.simulation:
            # init camera
            self.device = Cab(ip=prm["ip"], simulation=self.simulation)


    def _place_offset(self, radius):
        return(
            [np.cos(np.deg2rad(30))*(np.sqrt((radius + 7.1)**2 - 100)),
            np.sin(np.deg2rad(30))*(np.sqrt((radius + 7.1)**2 - 100)),
            0,
            0,
            0,
            0]
        )
    
