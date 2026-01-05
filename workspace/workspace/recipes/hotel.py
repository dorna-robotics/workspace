from copy import deepcopy
from mergedeep import merge
from workspace.recipes.recipe import Recipe


class Hotel(Recipe):
    DEFAULTS = dict(
        # ref joints
        target_solid_name="body",
        target_anchor="center",
        target_offset=[0, 0, 10, 0, 180, 0],
        initial_joints = [0, 0, 0, 0, 0, 0, 0, 0],
        # IK
        left_approach=True,
        base_distance=350,
        rail_step=10,
        rail_span=20,        
        # motion
        motion_type="lmove",
        speed_factor=0.5,
        jmove_vaj=[200, 5000, 50000],
        lmove_vaj=[200, 5000, 50000],
        # calibration
        calibration=True,
        calibration_targets={}, # {solid_name: {anchor_1:..., anchor_2:...},...}
        calibration_target_offset=[0, 0, -30, 0, 0, 0],
        calibration_tool_solid_name="body",
        calibration_tool_anchor="tcp",
        calibration_tool_offset=[0, 0, 0, 0, 0, 0],
    )

    def __init__(self, workspace, core, component, **kwargs):
        # prm
        prm = deepcopy(Recipe.DEFAULTS) # default
        merge(prm, self.DEFAULTS) # self
        merge(prm, kwargs) # kwargs

        super().__init__(
            workspace=workspace,
            core=core,
            component=component,
            **prm
        )
        
    
    def pick_from(self, level=0, solid_name="body", approach=True, exit=True, attachment=True, trigger_io=True, padding=10, gap=2, **kwargs):
        # anchor
        anchor = f"place_{level}"

        # pick parameters
        motion_prm = self.pick_setting(anchor=anchor, solid_name=solid_name, approach=approach, exit=exit, attachment=attachment, trigger_io=trigger_io, padding=padding, gap=gap, **kwargs)
        if not motion_prm:
            return False

        # update approach
        motion_prm["approach_path"] = [[self.component.size[0] + padding, 0, motion_prm["height_load"] + motion_prm["height_tool"]+ gap, 0, 0, 0], 
                        [padding, 0, motion_prm["height_load"] + motion_prm["height_tool"] + gap, 0, 0, 0],
                        [padding, 0, motion_prm["height_load"], 0, 0, 0]]

        # update exit
        motion_prm["exit_path"] = [[0, 0, motion_prm["height_container"] + gap, 0, 0, 0], 
                    [0, 0, motion_prm["height_container"] + padding, 0, 0, 0], 
                    [self.component.size[0] + padding, 0, motion_prm["height_container"] + padding, 0, 0, 0]]

        # run touch
        return self.touch(**motion_prm)


    def place_in(self, level=0, solid_name="body", approach=True, exit=True, attachment=True, trigger_io=True, padding=10, gap=2, load_anchor="center", **kwargs):
            # anchor
            anchor = f"place_{level}"

            # place parameters
            motion_prm = self.place_setting(anchor=anchor, solid_name=solid_name, approach=approach, exit=exit, attachment=attachment, trigger_io=trigger_io, padding=padding, gap=gap, load_anchor=load_anchor, **kwargs)
            if not motion_prm:
                return False

            # update approach
            motion_prm["approach_path"] = [[self.component.size[0] + padding, 0, motion_prm["height_container"] + padding, 0, 0, 0], 
                            [0, 0, motion_prm["height_container"] + padding, 0, 0, 0],
                            [0, 0, motion_prm["height_container"] + gap, 0, 0, 0]]

            # update exit
            motion_prm["exit_path"] = [[padding, 0, max(motion_prm["height_load"], motion_prm["height_container"]) + motion_prm["height_tool"] + gap, 0, 0, 0], 
                        [self.component.size[0] + padding, 0, max(motion_prm["height_load"], motion_prm["height_container"]) + motion_prm["height_tool"] + gap, 0, 0, 0]]

            # run touch
            return self.touch(**motion_prm)