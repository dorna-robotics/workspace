from copy import deepcopy
from mergedeep import merge
from workspace.components.factory import register
from workspace.components.decapper.decapper import Decapper


@register("decapper_two_slot")
class DecapperTwoSlot(Decapper):
    """Two-slot decapper — the 1-slot unit's IO and behavior with a
    second jaw station.

    Measured off decapper_two_slot.glb (mm, centered in XY):

        body    70.0 x 82.0, z -6.0..56.1
        slots   two, centered on (0, -24) and (0, +24) — jaw fingers
                at x +/-11..16 in four symmetric y bands
        holes   dia 5.0 at (+/-25, +/-25), z=0 — same fixture grid as
                the 1-slot

    ``a1`` (-y) / ``a2`` (+y) are the slot anchors at the 1-slot's
    working height (z=45, yaw 90 like its ``place``); ``place`` is the
    midpoint between them, ``top`` the measured top.
    """
    DEFAULTS = dict(
        anchors={"body": {"center": [0, 0, 0, 0, 0, 0], "place":[0, 0, 40, 0, 0, 90], "top":[0, 0, 70, 0, 0, 90],
            "A1": [0, -24, 40, 0, 0, 90], "A2": [0, 24, 40, 0, 0, 90],
            "hole_0":[25, 25, 0, 0, 0, 0], "hole_1": [-25, 25, 0, 0, 0, 0], "hole_2": [-25, -25, 0, 0, 0, 0], "hole_3": [25, -25, 0, 0, 0, 0]}},
        collision_box =
            {"body":[
                {"pose":[0.0, 0.0, (((62.1-56.1+67)/2)-6), 0.0, 0.0, 0.0], "scale":[70.0, 82.0, 62.1-56.1+67], "padding_enabled": True}   #[xyzabc] , [lx,ly,lz]
        ]},
    )

    def __init__(self, name: str, cfg: dict, workspace, **kwargs):
        # prm
        prm = deepcopy(Decapper.DEFAULTS) # default
        merge(prm, self.DEFAULTS) # self
        merge(prm, cfg) # cfg
        merge(prm, kwargs) # kwargs

        # update type
        prm.setdefault("type", getattr(self.__class__, "_registered_type", cfg.get("type")))

        # init
        super().__init__(name=name, cfg=prm, workspace=workspace)

        # both jaw stations are placeable
        self.slot = {
           "body": ["A1", "A2"]
        }
