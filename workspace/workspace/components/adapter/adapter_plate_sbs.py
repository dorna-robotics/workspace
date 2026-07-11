from copy import deepcopy
from mergedeep import merge
from workspace.components.factory import register
from workspace.components.adapter.adapter import Adapter


@register("adapter_plate_sbs")
class AdapterPlateSBS(Adapter):
    # Calibration grid: 18 clb anchors on a 3-row (Y = -37.5, 0, +37.5) x
    # 6-column (X = -62.5, -37.5, -12.5, 12.5, 37.5, 62.5) lattice over the
    # plate face. Numbered clb_0..clb_17 in a boustrophedon snake starting
    # at the bottom-left corner (min X, min Y): the bottom row runs left ->
    # right, the middle row right -> left, the top row left -> right, so
    # consecutive indices are always physical neighbours (short probe hops,
    # no diagonal back-tracking). The middle row (Y = 0) is the six added
    # points. Flip the per-row X direction here if the bench's "left" is the
    # opposite edge.
    DEFAULTS = dict(
        anchors={"body": {"center":[0, 0, 0, 0, 0, 0], "place": [0, 0, 4.5, 0, 0, 0], "top": [0, 0, 8, 0, 0, 0],
                        "hole_0":[37.5, 37.5, 0, 0, 0, 0], "hole_1": [-37.5, 37.5, 0, 0, 0, 0], "hole_2": [-37.5, -37.5, 0, 0, 0, 0], "hole_3": [37.5, -37.5, 0, 0, 0, 0],
                        # row Y = -37.5 (bottom), left -> right
                        "clb_0": [-62.5, -37.5, 0, 0, 0, 0], "clb_1": [-37.5, -37.5, 0, 0, 0, 0], "clb_2": [-12.5, -37.5, 0, 0, 0, 0], "clb_3": [12.5, -37.5, 0, 0, 0, 0], "clb_4": [37.5, -37.5, 0, 0, 0, 0], "clb_5": [62.5, -37.5, 0, 0, 0, 0],
                        # row Y = 0 (middle, added points), right -> left
                        "clb_6": [62.5, 0, 0, 0, 0, 0], "clb_7": [37.5, 0, 0, 0, 0, 0], "clb_8": [12.5, 0, 0, 0, 0, 0], "clb_9": [-12.5, 0, 0, 0, 0, 0], "clb_10": [-37.5, 0, 0, 0, 0, 0], "clb_11": [-62.5, 0, 0, 0, 0, 0],
                        # row Y = +37.5 (top), left -> right
                        "clb_12": [-62.5, 37.5, 0, 0, 0, 0], "clb_13": [-37.5, 37.5, 0, 0, 0, 0], "clb_14": [-12.5, 37.5, 0, 0, 0, 0], "clb_15": [12.5, 37.5, 0, 0, 0, 0], "clb_16": [37.5, 37.5, 0, 0, 0, 0], "clb_17": [62.5, 37.5, 0, 0, 0, 0]}},
        collision_box=
            {"body":[
                {"pose":[0.0, 0.0, 4.0, 0.0, 0.0, 0.0], "scale":[150.0, 100.0, 8.0]}#[xyzabc] , [lx,ly,lz]
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
