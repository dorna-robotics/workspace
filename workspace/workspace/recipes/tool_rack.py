from copy import deepcopy
from mergedeep import merge
from dorna2 import pose as dorna_pose
from workspace.recipes.recipe import Recipe, RecipeError

class ToolRack(Recipe):
    DEFAULTS = dict(
        # motion
        lmove_vaj=[150, 350, 1500],
        # calibration
        calibrate_abc=True,
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


    def pick(self, anchor="place", solid_name="body", padding=80, gap=2, **kwargs):
        """Pick a tool from the rack via the tool-changer interface.

        Requires ``core.has_tool_changer`` and that ``anchor`` currently holds
        a tool. Approaches from above, drops onto the changer's
        ``tool_changer_connection``, actuates the changer, attaches the tool
        to the robot side, and retracts.

        Raises:
            RecipeError: If no tool changer, no tool at anchor, or
                ``ref_joints`` undefined.
        """
        # ref joints
        if self.ref_joints is None:
            raise RecipeError("no reference joints defined")

        # check if the robot has a tool changer
        if not self.core.has_tool_changer:
            raise RecipeError("no tool changer attached to the robot")

        # next we verify there is a tool to pick
        tool = self.solid_attached_to_anchor(self.component.assembly[solid_name], anchor)
        if tool is None:
            raise RecipeError(f"no item found in position {anchor}")
        
        # offset height
        height_offset = abs(dorna_pose.transform_pose([0, 0, 0, 0, 0, 0], 
                                                        from_frame=tool.pose("tool_changer_connection"),
                                                        to_frame=tool.pose("tool_rack_connection"))[2])
        
        # output approach
        output_approach = [[self.core.tool_changer_cfg["output_detach"][:], None, None]]

        # output touch
        output_touch = [[self.core.tool_changer_cfg["output_attach"][:], None, None]]

        # motion prm
        motion_prm ={
            "target_solid": self.component.assembly[solid_name],
            "target_anchor": anchor, 
            "target_offset": [0, 0, -height_offset, 0, 0, 0],
            "output_approach": output_approach,
            "approach_tool": {"solid": self.core.tool_changer_robot_side, "anchor": "tool_changer_connection", "offset":[0, 0, 0, 0, 0, 0]},
            "approach_path": [
                                [-padding, 0, -padding-height_offset, 0, 0, 0],
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
                    [-padding,0,-padding-height_offset,0,0,0],
                ],
        }

        # motion
        return self.touch(**motion_prm)


    def place(self, anchor="place", solid_name="body", padding=80, gap=2, **kwargs):
        """Put the currently-held tool back into the rack slot at ``anchor``.

        Inverse of ``pick``. Verifies that the rack slot is free and that the
        robot is actually holding a tool, then lowers onto the rack,
        deactivates the changer, transfers the tool solid to the rack, and
        retracts.

        Raises:
            RecipeError: If the slot is already occupied, no tool on robot,
                or no tool changer.
        """
        # ref joints
        if self.ref_joints is None:
            raise RecipeError("no reference joints defined")

        # check if the robot has a tool changer
        if not self.core.has_tool_changer:
            raise RecipeError("no tool changer attached to the robot")

        # check if there is a tool in tool change ror not
        if self.solid_attached_to_anchor(self.component.assembly[solid_name], anchor) is not None:
            raise RecipeError(f"position {anchor} is already occupied")

        # next we find the tool attached to the robot
        tool = None
        for child in self.core.tool_changer_robot_side.children["tool_changer_connection"]:
            tool = child["child_solid"]
            break
        if tool is None:
            raise RecipeError("no tool attached to robot")
        
        # offset height
        height_offset = abs(dorna_pose.transform_pose([0, 0, 0, 0, 0, 0], 
                                                        from_frame=tool.pose("tool_changer_connection"),
                                                        to_frame=tool.pose("tool_rack_connection"))[2])


        # output touch
        output_touch = [[self.core.tool_changer_cfg["output_detach"][:], None, None]]

        # output exit
        output_exit = [[self.core.tool_changer_cfg["output_attach"][:], None, None]]

        # motion prm
        motion_prm ={
            "target_solid": self.component.assembly[solid_name],
            "target_anchor": anchor, 
            "target_offset": [0, 0, -height_offset, 0, 0, 0],
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
                            [-padding, 0, -padding-height_offset, 0, 0, 0],
                        ],
            "output_exit": output_exit
        }

        # motion
        return self.touch(**motion_prm)