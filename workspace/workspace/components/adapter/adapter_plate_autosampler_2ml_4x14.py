from copy import deepcopy
from mergedeep import merge
from workspace.components.factory import register
from workspace.components.adapter.adapter import Adapter


# NOTE: adapter_plate_autosampler_2ml_4x14.glb currently exports only the two
# fasteners at x=+/-50 (no plate body), so collision_box / place / top below
# are UNMEASURED placeholders carried over from the 5x10 sibling. Re-export the
# plate and these can be measured off the mesh. (clb IS set: grid-aligned probe
# points sized to the 236x71 tube footprint — see the anchors comment.)
@register("adapter_plate_autosampler_2ml_4x14")
class AdapterPlateAutosampler2ml4x14(Adapter):
    DEFAULTS = dict(
        anchors={"body": {"center":[0, 0, 0, 0, 0, 0], "place": [0, 0, 0, 0, 0, 0], "top": [0, 0, 6, 0, 0, 0],
                        "hole_0":[50, 0, 0, 0, 0, 0], "hole_1": [-50, 0, 0, 0, 0, 0],
                        # Calibration anchors on the plate's 25 mm hole grid, at
                        # z = 0. hole_0/hole_1 (the screws at x = +/-50) take
                        # their own grid holes; these use OTHER free holes, every
                        # coordinate a multiple of 25. 4 at the corners of a
                        # 200 x 50 grid rectangle (widest holes inside the
                        # 236 x 71 footprint) + 2 near the centre, clear of the
                        # +/-50 screws. (Same layout as the 4x14 capholder
                        # adapter — identical footprint and screw grid.)
                        "clb_0": [100, 25, 0, 0, 0, 0], "clb_1": [-100, 25, 0, 0, 0, 0], "clb_2": [-100, -25, 0, 0, 0, 0], "clb_3": [100, -25, 0, 0, 0, 0],
                        "clb_4": [25, 0, 0, 0, 0, 0], "clb_5": [-25, 0, 0, 0, 0, 0]}},
        collision_box=
            {"body":[
                {"pose":[0.0, 0.0, 0, 0.0, 0.0, 0.0], "scale":[105.0, 5.0, 12.0]}#[xyzabc] , [lx,ly,lz]
        ]}
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
