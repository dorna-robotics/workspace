from copy import deepcopy
from mergedeep import merge
from workspace.components.factory import register
from workspace.components.adapter.adapter import Adapter


@register("adapter_plate_amber_40ml_1x6")
class AdapterPlateAmber40ml1x6(Adapter):
    DEFAULTS = dict(
        anchors={"body": {"center":[0, 0, 0, 0, 0, 0], "place": [0, 0, 0, 0, 0, 0], "top": [0, 0, 0, 0, 0, 0],
                "hole_0": [37.5, 0, 0, 0, 0, 0], "hole_1": [-37.5, 0, 0, 0, 0, 0],
                # Calibration anchors along the single row, on the plate hole
                # grid at z = 100 (100 mm above the plate face). This plate's holes fall at x = +/-12.5, +/-37.5,
                # +/-62.5, ... (25 mm pitch, offset 12.5 from centre — which is
                # why the screws sit at +/-37.5). CENTRE (x=0) is therefore NOT
                # a hole, so a 3-point end/middle/end line is impossible; use 4
                # in a line instead: the two ends (+/-112.5, widest holes inside
                # the 245 mm footprint) and +/-12.5 bracketing the centre. All
                # clear of the +/-37.5 screws.
                "clb_0": [112.5, 0, 100, 0, 0, 0], "clb_1": [12.5, 0, 100, 0, 0, 0], "clb_2": [-12.5, 0, 100, 0, 0, 0], "clb_3": [-112.5, 0, 100, 0, 0, 0]}},
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
