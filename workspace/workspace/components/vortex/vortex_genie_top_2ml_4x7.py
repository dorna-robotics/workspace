from copy import deepcopy
from mergedeep import merge
from workspace.components.factory import register
from workspace.components.rack.rack import Rack


# Vortex-Genie 4x7 2ml tube top — the multi-tube plate that clamps onto the
# vortexer in place of the single-tube cup (see vortex_genie_top_2ml).
#
# Every number below is measured off vortex_genie_top_2ml_4x7.glb (least-
# squares circle fits, max residual 0.02 mm), origin at the XY centre with
# z = 0 the bottom face — the same convention the 2ml racks use:
#   envelope   71.0 x 120.5 x 26.0; the z 0..4 foot is narrower (71 x 78.34),
#              full width from z = 4 up, so the collision box below is the
#              full extent (simple + conservative, as on the 4x14 rack)
#   sockets    28 bores, d 12.963, floor at z = 7.0, open to the z = 26 rim
#   grid       16.5 pitch on BOTH axes — 4 cols along x = +/-8.25, +/-24.75;
#              7 rows along y = 0, +/-16.5, +/-33, +/-49.5 (same pitch as the
#              rack_autosampler_2ml_* family)
#   clb holes  4 x d 4.218 at (+/-16.5, +/-41.25), blind from the z = 26 top
#              face down to z ~ 12.7 — clb_0..3 sit on the top face, ordered
#              (+x,+y) -> (-x,+y) -> (-x,-y) -> (+x,-y) like the adapter plates
#   (also present, not anchored: a d 5.536 through-bore at (0, -8.25) — the
#    vortexer shaft fixing, off-centre by exactly half a grid pitch)
#
# NOTE the glb ships rotated +90 deg about z from the original CAD export, so
# the long axis runs along y. That makes the grid 7 rows x 4 cols under the
# platform's rows-along-y / cols-along-x convention, even though the type name
# reads "4x7" — the name follows the glb filename, which is the type glue.
@register("vortex_genie_top_2ml_4x7")
class VortexGenieTop2ml4x7(Rack):
    DEFAULTS = dict(
        anchors={"body": {"center": [0, 0, 0, 0, 0, 0], "place": [0, 0, 7, 0, 0, 0], "top": [0, 0, 26, 0, 0, 0],
                        # Calibration anchors on the four d 4.2 mounting holes,
                        # at z = 26 (the top face) — the vortex-top convention,
                        # where clb sits on the part's own rim.
                        "clb_0": [16.5, 41.25, 26, 0, 0, 0], "clb_1": [-16.5, 41.25, 26, 0, 0, 0],
                        "clb_2": [-16.5, -41.25, 26, 0, 0, 0], "clb_3": [16.5, -41.25, 26, 0, 0, 0]}},
        collision_box =
            {"body":[
                {"pose":[0.0, 0.0, 26/2, 0.0, 0.0, 0.0], "scale":[71.0, 120.5, 26.0], "padding_enabled": True}#[xyzabc] , [lx,ly,lz]
        ]},
        offset=[-16.5*(4-1)/2, -16.5*(7-1)/2, 7],
        pitch=[16.5, 16.5, 0],
        rvec_safe=[0, 0, 45],
        # A1..D7, same naming as the 2ml racks — a vial keeps its own
        # anchor. The physical grid is 4 bores along x and 7 along y
        # (rotated glb, see the NOTE above), so rows A-D must run
        # along x: transpose swaps the base generator's axes. A1 =
        # (-24.75, -49.5), D7 = (+24.75, +49.5), all 28 on the
        # measured bores.
        rows=[chr(c) for c in range(ord("A"), ord("D") + 1)],
        cols= [i for i in range(1, 7+1)],
        transpose=True,
    )

    def __init__(self, name: str, cfg: dict, workspace, **kwargs):
        # prm
        prm = deepcopy(Rack.DEFAULTS) # default
        merge(prm, self.DEFAULTS) # self
        merge(prm, cfg) # cfg
        merge(prm, kwargs) # kwargs

        # type
        prm.setdefault("type", getattr(self.__class__, "_registered_type", prm.get("type")))

        # init
        super().__init__(name=name, workspace=workspace, **prm)
