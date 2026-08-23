from copy import deepcopy
from mergedeep import merge
from workspace.components.factory import register
from workspace.components.adapter.adapter import Adapter


@register("adapter_plate_amber_40ml_4x7")
class AdapterPlateAmber40ml4x7(Adapter):
    DEFAULTS = dict(
        anchors={"body": {"center":[0, 0, 0, 0, 0, 0], "place": [0, 0, 0, 0, 0, 0], "top": [0, 0, 0, 0, 0, 0],
                "hole_0": [100, 0, 0, 0, 0, 0], "hole_1": [-100, 0, 0, 0, 0, 0],
                # Calibration anchors on the plate's 25 mm hole grid, at z = 100
                # (100 mm above the plate face). hole_0/hole_1 (the screws at x = +/-100)
                # take their own grid holes; these use OTHER free holes, every
                # coordinate a multiple of 25 so each lands on a real hole. 4 at
                # the corners of a 250 x 150 grid rectangle (widest grid holes
                # inside the 285 x 165 footprint) + 2 near the centre, clear of
                # the +/-100 screws.
                "clb_0": [125, 75, 100, 0, 0, 0], "clb_1": [-125, 75, 100, 0, 0, 0], "clb_2": [-125, -75, 100, 0, 0, 0], "clb_3": [125, -75, 100, 0, 0, 0],
                "clb_4": [50, 0, 100, 0, 0, 0], "clb_5": [-50, 0, 100, 0, 0, 0]}},
    )

    def __init__(self, name: str, cfg: dict, workspace,**kwargs):
        # prm
        prm = deepcopy(Adapter.DEFAULTS) # default
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
