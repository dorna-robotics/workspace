from copy import deepcopy
from mergedeep import merge
from workspace.components.factory import register
from workspace.components.adapter.adapter import Adapter


# Cap-holder adapter for the 4x14 autosampler capholder. Like the standard
# two-mount adapter (adapter_plate_autosampler_2ml_4x14, same glb family), but
# the two mounts are SCREWS, not just alignment pins — the CAD exports the
# actual screw heads (no plate body between them), so they get a collision box.
#
# adapter_capholder_autosampler_2ml_4x14.glb, measured with its export node
# transform applied (scale 10, +90 deg about X — the standard library
# convention, so anchors are authored in physical mm and the mesh is NOT
# stripped): the whole assembly is the two screws, spanning
#     X -55 .. 55   (heads Ø10 at x = +/-50 -> 100 mm centre-to-centre)
#     Y  -5 .. 5
#     Z -5.2 .. 2.8 (heads top out at 2.8, shanks drop to -5.2)
@register("adapter_capholder_autosampler_2ml_4x14")
class AdapterCapholderAutosampler2ml4x14(Adapter):
    DEFAULTS = dict(
        anchors={"body": {
            "center": [0, 0, 0, 0, 0, 0],
            "place":  [0, 0, 0, 0, 0, 0],     # capholder seats here (base plane)
            "top":    [0, 0, 2.8, 0, 0, 0],   # top of the screw heads (measured)
            # the two screw mounts — 100 mm apart, centre-to-centre
            "hole_0": [50, 0, 0, 0, 0, 0],
            "hole_1": [-50, 0, 0, 0, 0, 0],
            # Calibration anchors on the plate's 25 mm hole grid, at z = 0
            # (the plate face) — probe-touch points for locating the adapter.
            # hole_0/hole_1 (the two screws at x = +/-50) occupy their own grid
            # holes; these clb points use OTHER free holes. Every coordinate is
            # a multiple of 25, so each lands on a real plate hole. 4 at the
            # corners of a 200 x 50 grid rectangle (a wide baseline for good
            # angular accuracy) + 2 near the centre, clear of the +/-50 screws.
            "clb_0": [100, 25, 0, 0, 0, 0],    # corner
            "clb_1": [-100, 25, 0, 0, 0, 0],   # corner
            "clb_2": [-100, -25, 0, 0, 0, 0],  # corner
            "clb_3": [100, -25, 0, 0, 0, 0],   # corner
            "clb_4": [25, 0, 0, 0, 0, 0],      # middle
            "clb_5": [-25, 0, 0, 0, 0, 0],     # middle
        }},
        # one box per screw (there is no body between them), each Ø10 head over
        # the 8 mm screw height. pose z = (-5.2 + 2.8)/2 = -1.2.
        collision_box={"body": [
            {"pose": [50, 0, -1.2, 0, 0, 0], "scale": [10.0, 10.0, 8.0]},
            {"pose": [-50, 0, -1.2, 0, 0, 0], "scale": [10.0, 10.0, 8.0]},
        ]},
    )

    def __init__(self, name: str, cfg: dict, workspace, **kwargs):
        # prm
        prm = deepcopy(Adapter.DEFAULTS)  # default
        merge(prm, self.DEFAULTS)         # self
        merge(prm, cfg)                   # cfg
        merge(prm, kwargs)                # kwargs

        # update type
        prm.setdefault("type", getattr(self.__class__, "_registered_type", cfg.get("type")))

        super().__init__(
            name=name,
            workspace=workspace,
            **prm
        )
