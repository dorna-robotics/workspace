from dorna2 import pose as dorna_pose
from workspace.recipes.recipe import Recipe


class Adapter(Recipe):
    def __init__(self, workspace, core, component,
        # ref joints
        target_solid_name="body",
        target_anchor="center",
        target_offset=[0, 0, 75, 0, 180, 0],
        initial_joints = [0, 0, 0, 0, 0, 0, 0, 0],
        # IK
        left_approach=True,
        base_distance=300,
        rail_step=5,
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
        

    def pick_from(self, anchor="place", solid_name="body", approach=True, exit=True, attachment=True, trigger_io=True, padding=75, gap=2, **kwargs):
        # pick parameters
        motion_prm = self.pick_setting(anchor=anchor, solid_name=solid_name, approach=approach, exit=exit, attachment=attachment, trigger_io=trigger_io, padding=padding, gap=gap, **kwargs)
        if not motion_prm:
            return False

        # update approach
        motion_prm["approach_path"] = [[10, 0, max(motion_prm["height_load"], motion_prm["height_container"]) + padding, 0, 0, 0], 
                                    [10, 0, motion_prm["height_load"] + motion_prm["height_tool"] + gap, 0, 0, 0],
                                    [10, 0, motion_prm["height_load"], 0, 0, 0]]

        # run touch
        return self.touch(**motion_prm)


    def place_in(self, anchor="place", solid_name="body", approach=True, exit=True, attachment=True, trigger_io=True, padding=75, gap=2, load_anchor="center", **kwargs):
            # place parameters
            motion_prm = self.place_setting(anchor=anchor, solid_name=solid_name, approach=approach, exit=exit, attachment=attachment, trigger_io=trigger_io, padding=padding, gap=gap, load_anchor=load_anchor, **kwargs)
            if not motion_prm:
                return False

            # update exit
            motion_prm["exit_path"] = [[10, 0, motion_prm["height_load"], 0, 0, 0], 
                        [0, 0, 0, max(motion_prm["height_load"], motion_prm["height_container"]) + padding, 0, 0, 0]]

            # run touch
            return self.touch(**motion_prm)