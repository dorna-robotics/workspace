from copy import deepcopy
from mergedeep import merge
from workspace.recipes.recipe import Recipe
from dorna2 import pose as dorna_pose


class Printer(Recipe):
    DEFAULTS = dict(
        # ref joints
        target_solid_name="body",
        target_anchor="place",
        target_offset=[0, 0, 50, 0, 180, 0],
        initial_joints = [0, 0, 0, 0, 0, 0, 0, 0],
        # IK
        left_approach=True,
        base_distance=350,
        rail_step=5.0,
        rail_span=10,        
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
        

    def pick(self, anchor="place", solid_name="body", approach=True, exit=True, attachment=True, trigger_io=True, padding=50, gap=2, **kwargs):
        # pick parameters
        motion_prm = self.pick_setting(anchor=anchor, solid_name=solid_name, approach=approach, exit=exit, attachment=attachment, trigger_io=trigger_io, padding=padding, gap=gap, **kwargs)
        if not motion_prm:
            return False

        # place offset based on the radius of the tube
        offset= self.component._place_offset(
            self.workspace.components[motion_prm["load_list"][0].component].size[0]/2
        )
        pose_offset = dorna_pose.Pose(pose=offset)

        # update target offset
        motion_prm["target_offset"] = pose_offset.pose(offset=motion_prm["target_offset"])


        # update approach
        for i in range(len(motion_prm["approach_path"])):
            motion_prm["approach_path"][i] = pose_offset.pose(offset=motion_prm["approach_path"][i])
            
        # update exit
        for i in range(len(motion_prm["exit_path"])):
            motion_prm["exit_path"][i] = pose_offset.pose(offset=motion_prm["exit_path"][i])

        # run touch
        return self.touch(**motion_prm)



    def place(self, anchor="place", solid_name="body", approach=True, exit=True, attachment=True, trigger_io=True, padding=50, gap=2, load_anchor="center", **kwargs):
        # place parameters
        motion_prm = self.place_setting(anchor=anchor, solid_name=solid_name, approach=approach, exit=exit, attachment=attachment, trigger_io=trigger_io, padding=padding, gap=gap, load_anchor=load_anchor, **kwargs)
        if not motion_prm:
            return False

        # place offset based on the radius of the tube
        offset= self.component._place_offset(
            self.workspace.components[motion_prm["load_list"][0].component].size[0]/2
        )
        pose_offset = dorna_pose.Pose(pose=offset)


        # update target offset
        motion_prm["target_offset"] = pose_offset.pose(offset=motion_prm["target_offset"])


        # update approach
        for i in range(len(motion_prm["approach_path"])):
            motion_prm["approach_path"][i] = pose_offset.pose(offset=motion_prm["approach_path"][i])
            
        # update exit
        for i in range(len(motion_prm["exit_path"])):
            motion_prm["exit_path"][i] = pose_offset.pose(offset=motion_prm["exit_path"][i])

        # update attach
        motion_prm["attach"][1]["offset"] = motion_prm["target_offset"]

        # run touch
        return self.touch(**motion_prm)

