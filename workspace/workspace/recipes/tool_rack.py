from copy import deepcopy
from mergedeep import merge
from dorna2 import pose as dorna_pose
from workspace.recipes.recipe import Recipe, RecipeError

class ToolRack(Recipe):
    DEFAULTS = dict(
        # ref joints
        target_solid_name="body",
        target_anchor="clb_0",
        target_offset=[0, 0, 0, 0, 180, 0],
        # motion
        lmove_vaj=[300, 450, 1500], # [150, 350, 1500],
        # calibration
        calibrate_abc=True,
        # The tool changer is a LATCH: every open/close must happen
        # with the robot stopped at its classic moment — never
        # overlapped with motion (bench: the latch visibly opened as
        # the next motion started). Discrete IO for every verb here.
        io_overlap=False,
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


    def pick(self, anchor="place", solid_name="body", padding=60, gap=2, **kwargs):
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
            "output_approach": output_approach,
            "approach_tool": {"solid": self.core.tool_changer_robot_side, "anchor": "tool_changer_connection", "offset":[0, 0, 0, 0, 0, 0]},
            # Groups: the entry corridor flows as one motion; mating
            # the changer plates is the contact group after the
            # stop+verify boundary.
            "approach": [
                            [
                                [-2*padding, 0, -2*padding-height_offset, 0, 0, 0],
                                [0, 0, -2*padding-height_offset, 0, 0, 0],
                                [0, 0, -12-gap-height_offset, 0, 0, 0],
                            ],
                            [
                                [0, 0, -height_offset, 0, 0, 0],
                            ],
                        ],
            "output_touch": output_touch,
            "actions": [],
            "sleep": 0.1,
            "attach": [tool, {"parent": self.core.tool_changer_robot_side, "parent_anchor":"tool_changer_connection", "child_anchor":"tool_changer_connection"}],
            "exit_tool": {"solid": self.core.tool_changer_robot_side, "anchor": "tool_changer_connection", "offset":[0, 0, 0, 0, 0, 0]},
            "exit": [[
                        [0, 0, -gap-height_offset, 0, 0, 0]
                    ],
                    [
                        [-padding,0,-gap-height_offset,0,0,0],
                        [-padding,0,-padding-height_offset,0,0,0],
                        [-2*padding,0,-2*padding-height_offset,0,0,0]
                    ],
                ],
            "fuse": False,
        }

        # motion
        return self.touch(**motion_prm)


    def place(self, anchor="place", solid_name="body", padding=60, gap=2, motion_plan_kwargs={"gravity_vec":[0, 0, 1], "gravity_thr": 45}, **kwargs):
        """Put the currently-held tool back into the rack slot at ``anchor``.

        Inverse of ``pick``. Verifies that the rack slot is free and that the
        robot is actually holding a tool, then lowers onto the rack,
        deactivates the changer, transfers the tool solid to the rack, and
        retracts.

        NEVER fuses its exit: the exit IO is the changer's RE-ARM
        (output_attach) — deferred onto a fused exit it fired while
        the flange was still lifting off the seated tool, the changer
        re-engaged, and the tool never separated (real bench — pins
        verified, sim cannot catch the mechanics). The re-arm must
        run with the corridor fully cleared, i.e. after a classic
        exit. PICK exits keep fusing.

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
            "approach_tool": {"solid": tool, "anchor": "tool_changer_connection", "offset":[0, 0, 0, 0, 0, 0]},
            # Groups: entry corridor as one motion; seating the tool
            # into the rack slot is the contact group after the stop.
            "approach": [
                            [
                                [-2*padding,0,-2*padding-height_offset,0,0,0],
                                [-padding,0,-2*padding-height_offset,0,0,0],
                            ],
                            [
                                [-padding,0,-gap-height_offset,0,0,0],
                                [0, 0, -gap-height_offset, 0, 0, 0],
                            ],
                            [
                                [0, 0, -height_offset, 0, 0, 0],
                            ],
                        ],
            "output_touch": output_touch,
            "actions": [],
            "sleep": 0.1,
            "attach": [tool, {"parent": self.component.assembly[solid_name], "parent_anchor": anchor, "child_anchor":"tool_rack_connection"}],
            "exit_tool": {"solid": self.core.tool_changer_robot_side, "anchor": "tool_changer_connection", "offset":[0, 0, 0, 0, 0, 0]},
            "exit": [[
                            [0, 0, -12-gap-height_offset, 0, 0, 0],
                            [0, 0, -padding-height_offset, 0, 0, 0],
                            [-2*padding, 0, -2*padding-height_offset, 0, 0, 0],
                        ]],
            "output_exit": output_exit,
            "fuse": False,   # see docstring — the re-arm needs a cleared corridor
        }

        # motion
        return self.touch(**motion_prm, motion_plan_kwargs=motion_plan_kwargs)
