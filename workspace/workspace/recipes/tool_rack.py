from copy import deepcopy
from mergedeep import merge
from dorna2 import pose as dorna_pose
from workspace.recipes.recipe import Recipe

class ToolRack(Recipe):
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
        rail_span=2,        
        # motion
        motion_type="lmove",
        speed_factor=0.5,
        jmove_vaj=[200, 1000, 5000],
        lmove_vaj=[200, 1000, 5000],
        # calibration
        calibration=True,
        calibration_targets={"body":["clb_0"]}, # {solid_name: {anchor_1:..., anchor_2:...},...}
        calibration_target_offset=[0, 0, 20, 0, 0, 0],
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


    """
    Approaches the tool rack and activates the tool changer
    to pick up a new tool.
    """
    def pick(self, anchor="place", solid_name="body", padding=80, gap=2, **kwargs):
        # ref joints
        if self.ref_joints is None:
            print("no reference joints defined")
            return False
        
        # check if the robot has a tool changer
        if not self.core.has_tool_changer:
            print("no tool changer attached to the robot")
            return False
        
        # next we verify there is a tool to pick
        tool = self.solid_attached_to_anchor(self.component.assembly[solid_name], anchor)
        if tool is None:
            print(f"no item found in position {anchor}")
            return False
        
        # offset height
        height_offset = abs(dorna_pose.transform_pose([0, 0, 0, 0, 0, 0], 
                                                        from_frame=tool.pose("tool_changer_connection"),
                                                        to_frame=tool.pose("tool_rack_connection"))[2])
        
        # output approach
        output_approach = self.core.tool_changer_output[:]
        output_approach[0][1] = int(not output_approach[0][1]) # change it for detach

        # output touch
        output_touch = self.core.tool_changer_output[:]
        
        # motion prm
        motion_prm ={
            "target_solid": self.component.assembly[solid_name],
            "target_anchor": anchor, 
            "target_offset": [0, 0, -height_offset, 0, 0, 0],
            "output_approach": output_approach,
            "approach_tool": {"solid": self.core.tool_changer_robot_side, "anchor": "tool_changer_connection", "offset":[0, 0, 0, 0, 0, 0]},
            "approach_path": [
                                [0, 0, -padding-height_offset, 0, 0, 0],
                                [0, 0, -12-gap-height_offset, 0, 0, 0]
                            ],
            "output_touch": output_touch,
            "actions": [],
            "sleep": 0.1,
            "attach": [tool, {"parent": self.core.tool_changer_robot_side, "parent_anchor":"tool_changer_connection", "child_anchor":"tool_changer_connection"}],
            "exit_tool": {"solid": self.core.tool_changer_robot_side, "anchor": "tool_changer_connection", "offset":[0, 0, 0, 0, 0, 0]},
            "exit_path": [
                    [0, 0, -gap-height_offset, 0, 0, 0],
                    [-padding,0,-gap-height_offset,0,0,0],
                    [-padding,0,-padding-height_offset,0,0,0]
                ],
        }

        # motion
        return self.touch(**motion_prm)


    def place(self, anchor="place", solid_name="body", padding=80, gap=2, **kwargs):
        # ref joints
        if self.ref_joints is None:
            print("no reference joints defined")
            return False

        # check if the robot has a tool changer
        if not self.core.has_tool_changer:
            print("no tool changer attached to the robot")
            return False
        
        # check if there is a tool in tool change ror not        
        if self.solid_attached_to_anchor(self.component.assembly[solid_name], anchor) is not None:
            print(f"Item found in position {anchor}")
            return False
        
        # next we find the tool attached to the robot
        tool = None
        for child in self.core.tool_changer_robot_side.children["tool_changer_connection"]:
            tool = child["child_solid"]
            break
        if tool is None:
            print("Could not find tool attached to robot")
            return False
        
        # offset height
        height_offset = abs(dorna_pose.transform_pose([0, 0, 0, 0, 0, 0], 
                                                        from_frame=tool.pose("tool_changer_connection"),
                                                        to_frame=tool.pose("tool_rack_connection"))[2])

        # output approach
        output_approach = self.core.tool_changer_output[:]

        # output touch
        output_touch = self.core.tool_changer_output[:]
        output_touch[0][1] = int(not output_touch[0][1]) # change it for detach

        # output exit
        output_exit = self.core.tool_changer_output[:]

        # motion prm
        motion_prm ={
            "target_solid": self.component.assembly[solid_name],
            "target_anchor": anchor, 
            "target_offset": [0, 0, -height_offset, 0, 0, 0],
            "output_approach": output_approach,
            "approach_tool": {"solid": tool, "anchor": "tool_changer_connection", "offset":[0, 0, 0, 0, 0, 0]},
            "approach_path":[
                                [-padding,0,-padding-height_offset,0,0,0],
                                [-padding,0,-gap-height_offset,0,0,0],
                                [0, 0, -gap-height_offset, 0, 0, 0],
                        ],
            "output_touch": output_touch,
            "actions": [],
            "sleep": 0.1,
            "attach": [tool, {"parent": self.component.assembly[solid_name], "parent_anchor": anchor, "child_anchor":"tool_rack_connection"}],
            "exit_tool": {"solid": self.core.tool_changer_robot_side, "anchor": "tool_changer_connection", "offset":[0, 0, 0, 0, 0, 0]},
            "exit_path": [
                            [0, 0, -12-gap-height_offset, 0, 0, 0],
                            [0, 0, -padding-height_offset, 0, 0, 0],
                        ],
            "output_exit": output_exit
        }

        # motion
        return self.touch(**motion_prm)