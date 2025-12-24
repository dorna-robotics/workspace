from workspace.recipes.recipe import Recipe

class Feeder(Recipe):
    def __init__(self, workspace, core, component,
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
        **kwargs
        ):

        super().__init__(
            workspace=workspace, 
            core=core,
            component=component,
            target_solid_name=target_solid_name,
            target_anchor=target_anchor,
            target_offset=target_offset,
            initial_joints=initial_joints,
            # IK
            left_approach=left_approach,
            base_distance=base_distance,
            rail_step=rail_step,
            rail_span=rail_span,        
            # motion
            motion_type=motion_type,
            speed_factor=speed_factor,
            jmove_vaj=jmove_vaj,
            lmove_vaj=lmove_vaj,
            **kwargs
        )