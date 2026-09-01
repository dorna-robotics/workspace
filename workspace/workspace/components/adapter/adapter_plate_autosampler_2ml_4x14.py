from copy import deepcopy
from mergedeep import merge
from workspace.components.factory import register
from workspace.components.adapter.adapter import Adapter


@register("adapter_plate_autosampler_2ml_4x14")
class AdapterPlateAutosampler2ml4x14(Adapter):
    """Same physical adapter as the amber 40 mL 4x7 one: two screws
    200 mm apart. Copied from that component; only the clb corner rows
    are pulled in for the narrower 4x14 rack.
    """
    DEFAULTS = dict(
        anchors={"body": {"center":[0, 0, 0, 0, 0, 0], "place": [0, 0, 0, 0, 0, 0], "top": [0, 0, 0, 0, 0, 0],
                "hole_0": [100, 0, 0, 0, 0, 0], "hole_1": [-100, 0, 0, 0, 0, 0],
                # Calibration anchors on the plate's 25 mm hole grid, at z = 100
                # (100 mm above the plate face). hole_0/hole_1 (the screws at x = +/-100)
                # take their own grid holes; these use OTHER free holes, every
                # coordinate a multiple of 25 so each lands on a real hole. 4 at
                # the corners of a 250 x 100 grid rectangle (the 285 x 85 rack
                # footprint sits inside in y) + 2 near the centre, clear of
                # the +/-100 screws.
                "clb_0": [125, 50, 100, 0, 0, 0], "clb_1": [-125, 50, 100, 0, 0, 0], "clb_2": [-125, -50, 100, 0, 0, 0], "clb_3": [125, -50, 100, 0, 0, 0],
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
