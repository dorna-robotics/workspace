from copy import deepcopy
from mergedeep import merge
from workspace.components.factory import register
from workspace.components.adapter.adapter import Adapter


@register("adapter_plate_autosampler_2ml_7x8")
class AdapterPlateAutosampler2ml7x8(Adapter):
    """Adapter for the 7x8 autosampler 2 mL rack: the standard
    two-standoff bar, 100 mm apart — same physical part as the 4x14
    adapter, so its GLB is a renamed copy of that one.

    Measured off the GLB (mm; origin at MID-thickness, centered):

        bar        105.0 x 5.0 x 12.0  (z -6..+6)
        standoffs  dia 5.0 bores at (+/-50, 0) — 100 mm apart, on the
                   25 mm fixture grid

    Anchor heights follow the 4x14 sibling (z=0, mid-bar): ``place`` at
    the same height as the holes, ``top`` at the top face. ``clb_*``
    are calibration anchors on the 25 mm grid: 4 at the corners of the
    widest grid rectangle inside the rack's 165 x 145 footprint
    (+/-75, +/-50) + 2 near the centre, clear of the +/-50 standoffs.
    """
    DEFAULTS = dict(
        anchors={"body": {"center":[0, 0, 0, 0, 0, 0], "place": [0, 0, 0, 0, 0, 0], "top": [0, 0, 6, 0, 0, 0],
                        "hole_0":[50, 0, 0, 0, 0, 0], "hole_1": [-50, 0, 0, 0, 0, 0],
                        "clb_0": [75, 75, 0, 0, 0, 0], "clb_1": [-75, 75, 0, 0, 0, 0],
                        "clb_2": [-75, -75, 0, 0, 0, 0], "clb_3": [75, -75, 0, 0, 0, 0],
                        "clb_4": [25, 0, 0, 0, 0, 0], "clb_5": [-25, 0, 0, 0, 0, 0]}},
        collision_box=
            {"body":[
                {"pose":[0.0, 0.0, 0, 0.0, 0.0, 0.0], "scale":[105.0, 5.0, 12.0]}#[xyzabc] , [lx,ly,lz]
        ]}
    )

    def __init__(self, name: str, cfg: dict, workspace, **kwargs):
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
