import workspace.recipes.util as util
from workspace.recipes.plate import Plate


class Feedert(Plate):

    def __init__(self, workspace, core, 
        container, # component
        solid_name = None,
        anchor = "place",
        padding = 50,
        gap = 2, # mm
        ref_joints = [0, 0, 0, 0, 0, 0, 0, 0],
        speed_factor=0.5,
        left_approach=True,
        base_distance=250,
        rail_step=5.0,
        rail_span=10,
        jmove_vaj=[200, 5000, 50000],
        lmove_vaj=[200, 5000, 50000],
        motion="lmove",
        **kwargs
        ):

        super().__init__(
            workspace=workspace,
            core=core,
            container=container,
            solid_name=solid_name,
            anchor=anchor,
            padding=padding,
            gap=gap,
            ref_joints=ref_joints,
            speed_factor=speed_factor,
            left_approach=left_approach,
            base_distance=base_distance,
            rail_step=rail_step,
            rail_span=rail_span,
            jmove_vaj=jmove_vaj,
            lmove_vaj=lmove_vaj,
            motion=motion,
            **kwargs
        )