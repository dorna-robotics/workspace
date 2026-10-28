from copy import deepcopy
from mergedeep import merge
from workspace.components.factory import register
from workspace.components.adapter.adapter import Adapter


# The 2x3 cap holder's adapter: two 5 mm pins, 50 mm apart, at
# x = +/-25 (the capholder's pin holes). Mesh is
# adapter_capholder_amber_40ml_4x7's pins moved in from +/-100.
@register("adapter_capholder_amber_40ml_2x3")
class AdapterCapholderAmber40ml2x3(Adapter):
    DEFAULTS = dict(
        anchors={"body": {"center":[0, 0, 0, 0, 0, 0], "place": [0, 0, 0, 0, 0, 0], "top": [0, 0, 0, 0, 0, 0],
                "hole_0": [25, 0, 0, 0, 0, 0], "hole_1": [-25, 0, 0, 0, 0, 0],
                # Calibration anchors on the plate's 25 mm hole grid, at z = 0,
                # probed bare with the capholder lifted off (like
                # adapter_capholder_amber_40ml_4x7): the 4 corners of a
                # 100 x 50 grid rectangle inside the capholder's footprint +
                # the centre, clear of the pins at x = +/-25.
                "clb_0": [50, 25, 0, 0, 0, 0], "clb_1": [-50, 25, 0, 0, 0, 0], "clb_2": [-50, -25, 0, 0, 0, 0], "clb_3": [50, -25, 0, 0, 0, 0],
                "clb_4": [0, 0, 0, 0, 0, 0]}},
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
