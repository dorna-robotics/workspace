from copy import deepcopy
from mergedeep import merge
from workspace.components.factory import register
from workspace.components.adapter.adapter import Adapter


@register("adapter_plate_amber_40ml_autosampler_2ml")
class AdapterPlateAmber40mlAutosampler2ml(Adapter):
    """Adapter for the amber-40mL/autosampler-2mL two-vial rack: a pair
    of threaded standoffs, 25 mm apart — the rack's own hole spacing.

    Measured off adapter_plate_amber_40ml_autosampler_2ml.glb (mm,
    centered in XY):

        envelope    8.5 x 33.5 x 17.0  (z -5..+12)
        standoffs   two at (0, +/-12.5): threads z -5..6.8, knurled
                    heads to z=12

    ``hole_0``/``hole_1`` at the standoff axes on the z=0 plane, -y
    first. ``place`` at [13, 0, 0] — the rack's origin lands there so
    its holes (x=13, y=+/-12.5) line up over the standoffs. ``top`` at
    the head tops (z=12). ``clb_0``/``clb_1`` at x = +/-25 on the fixture's 25 mm
    grid, the free holes either side.
    """
    DEFAULTS = dict(
        anchors={"body": {"center":[0, 0, 0, 0, 0, 0], "place": [-13, 0, 0, 0, 0, 0], "top": [0, 0, 12, 0, 0, 0],
                        "hole_0":[0, -12.5, 0, 0, 0, 0], "hole_1": [0, 12.5, 0, 0, 0, 0],
                        "clb_0": [25, 0, 0, 0, 0, 0], "clb_1": [-25, 0, 0, 0, 0, 0]}},
        collision_box=
            {"body":[
                {"pose":[0.0, 0.0, 3.5, 0.0, 0.0, 0.0], "scale":[8.5, 33.5, 17.0]}#[xyzabc] , [lx,ly,lz]
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
