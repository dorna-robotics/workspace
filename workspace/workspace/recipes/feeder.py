from copy import deepcopy
from workspace.recipes.recipe import Recipe

class Feeder(Recipe):
    DEFAULTS = dict(
        # ref joints
        target_solid_name="body",
        target_anchor="center",
        target_offset=[0, 0, 50, 0, 180, 0],
        initial_joints = [0, 0, 0, 0, 0, 0, 0, 0],
        # IK
        left_approach=True,
        base_distance=250,
        rail_step=5.0,
        rail_span=10,        
        # motion
        motion_type="lmove",
        speed_factor=0.5,
        jmove_vaj=[200, 5000, 50000],
        lmove_vaj=[200, 5000, 50000],
    )

    def __init__(self, workspace, core, component, **kwargs):
        # parent defaults
        prm = deepcopy(Recipe.DEFAULTS)
        # child defaults
        prm.update(self.DEFAULTS)
        # user defaults
        prm.update(kwargs)

        super().__init__(
            workspace=workspace,
            core=core,
            component=component,
            **prm
        )