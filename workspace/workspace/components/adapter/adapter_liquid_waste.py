from copy import deepcopy
from mergedeep import merge
from workspace.components.factory import register
from workspace.components.adapter.adapter import Adapter


@register("adapter_liquid_waste")
class AdapterLiquidWaste(Adapter):
    """Riser adapter for the liquid-waste management stand: four 100 mm
    standoffs on the stand's own (+/-12.5, +/-12.5) hole grid.

    Measured off adapter_liquid_waste.glb (mm, origin at the standoff
    base plane, centered in XY):

        footprint   32.0 x 32.0
        standoffs   four at (+/-12.5, +/-12.5), z 0..100
        studs       extend to z=-7 below the base plane

    ``hole_0``-``hole_3`` at the standoff bases (z=0), CCW from
    (-x,-y) — the same ordering as liquid_waste_management's
    ``hole_*``, so the stand bolts on anchor-for-anchor. ``place`` and
    ``top`` at the standoff tops (z=100). ``clb_0``/``clb_1`` are the
    calibration anchors on the top plane at the two diagonal standoffs.
    """
    DEFAULTS = dict(
        anchors={"body": {"center":[0, 0, 0, 0, 0, 0], "place": [0, 0, 100, 0, 0, 0], "top": [0, 0, 100, 0, 0, 0],
                        "hole_0":[-12.5, -12.5, 0, 0, 0, 0], "hole_1": [12.5, -12.5, 0, 0, 0, 0],
                        "hole_2": [12.5, 12.5, 0, 0, 0, 0], "hole_3": [-12.5, 12.5, 0, 0, 0, 0],
                        "clb_0": [12.5, 12.5, 100, 0, 0, 0], "clb_1": [-12.5, -12.5, 100, 0, 0, 0]}},
        collision_box=
            {"body":[
                {"pose":[0.0, 0.0, 46.5, 0.0, 0.0, 0.0], "scale":[32.0, 32.0, 107.0]}#[xyzabc] , [lx,ly,lz]
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
