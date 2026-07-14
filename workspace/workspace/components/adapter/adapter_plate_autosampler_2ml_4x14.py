from copy import deepcopy
from mergedeep import merge
from workspace.components.factory import register
from workspace.components.adapter.adapter import Adapter


# NOTE: adapter_plate_autosampler_2ml_4x14.glb currently exports only the two
# fasteners at x=+/-50 (no plate body), so collision_box / place / top / clb
# below are UNMEASURED placeholders carried over from the 5x10 sibling.
# Re-export the plate and these can be measured off the mesh.
@register("adapter_plate_autosampler_2ml_4x14")
class AdapterPlateAutosampler2ml4x14(Adapter):
    DEFAULTS = dict(
        anchors={"body": {"center":[0, 0, 0, 0, 0, 0], "place": [0, 0, 0, 0, 0, 0], "top": [0, 0, 6, 0, 0, 0],
                        "hole_0":[50, 0, 0, 0, 0, 0], "hole_1": [-50, 0, 0, 0, 0, 0],
                        "clb_0": [67.13, 19.18, 21, 0, 0, -45], "clb_1": [-67.13, 19.18, 21, 0, 0, -135], "clb_2": [-67.13, -19.18, 21, 0, 0, -45], "clb_3": [67.13, -19.18, 21, 0, 0, -135]}},
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
