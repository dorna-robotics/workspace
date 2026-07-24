from copy import deepcopy
from mergedeep import merge
from dorna2 import pose as dorna_pose
from workspace.recipes.recipe import Recipe, RecipeError


class Decapper(Recipe):
    DEFAULTS = dict(
        # IK
        base_distance=50,
    )

    def __init__(self, workspace, core, component, **kwargs):
        prm = deepcopy(Recipe.DEFAULTS)
        merge(prm, self.DEFAULTS)
        merge(prm, kwargs)

        super().__init__(
            workspace=workspace,
            core=core,
            component=component,
            **prm,
        )

    def place(self, approach=True, exit=True, padding=None, **kwargs):
        """Place a tube into the decapper's ``place`` anchor.

        Thin override of ``Recipe.place`` with ``gravity_offset=0`` (the
        decapper holds the tube directly — no lift compensation) and a
        shorter default padding of 30 mm.
        """
        return super().place(anchor="place", approach=approach, exit=exit, padding=padding, gravity_offset=0, **kwargs)

    def pick(self, approach=True, exit=True, padding=None, compliant=False, **kwargs):
        """Pick a tube from the decapper's ``place`` anchor. Padding defaults to 30 mm.

        ``compliant`` defaults to False here: the decapper is a rigid jaw
        grip on a screwed-on cap, so a ``tool_tcp_z_offset`` over-drive really
        moves the tube on the tool and must fold into the attach offset (the
        base Recipe defaults compliant=True for suction/soft tools)."""
        return super().pick(anchor="place", approach=approach, exit=exit, padding=padding,
                            compliant=compliant, **kwargs)

    def decap(
        self,
        anchor="place",
        solid_name="body",
        approach=True,
        exit=True,
        padding=None,
        gap=2,
        lmove_vaj=[1000, 3000, 15000],
        jmove_vaj=[500, 3000, 15000],
        max_rotation=500,
        twist=500,
        **kwargs,
    ):
        """Unscrew the cap off a tube sitting at ``anchor``.

        Twists the cap loose in chunks of ``max_rotation`` degrees, ascending by
        ``pitch/max_rotation`` per degree. Toggles the gripper between chunks
        to re-bite the cap. On success, the cap is attached to the tool.

        Args:
            anchor: Anchor holding the capped tube (default "place").
            padding, gap: Safe-height and near-gap (mm).
            lmove_vaj, jmove_vaj: [vel, accel, jerk] for linear / joint moves.
            max_rotation: Maximum j5 swing per chunk (degrees).
            twist: Total rotation to unscrew (degrees). Defaults to component's.
        """
        rt = self.rt

        # pick parameters
        motion_prm = self.pick_setting(
            anchor=anchor,
            solid_name=solid_name,
            approach=approach,
            trigger_io=False,
            exit=False,
            attachment=False,
            padding=padding,
            gap=gap,
            **kwargs,
        )
        if not motion_prm:
            raise RecipeError("decap failed — could not compute pick parameters")

        # tube and cap
        if len(motion_prm["load_list"]) != 2:
            print(f"No tube and cap found in position {anchor}")

        # cap
        solid_cap = motion_prm["load_list"][1]
        component_cap = self.workspace.components[solid_cap.component]

        # cap type
        if component_cap.cap_type != "screw":
            raise RecipeError(f"unsupported cap type: {component_cap.cap_type}")

        # height cap
        height_cap = abs(
            dorna_pose.transform_pose(
                [0, 0, 0, 0, 0, 0],
                from_frame=solid_cap.pose("center"),
                to_frame=solid_cap.pose("top"),
            )[2]
        )

        # tool
        tool = motion_prm["tool"]

        # j5_start
        j5_start = max_rotation / 2

        # adjust j5 in the approach
        motion_prm["approach_j5"] = j5_start

        # run the motion (touch already uses runtime internally in your updated Recipe)
        if not self.touch(**motion_prm):
            raise RecipeError("decap failed — touch motion failed")

        # chunked unscrew (j5 rotates backward while z rises)
        last_J = self._screw_motion(
            tool=tool,
            pitch=component_cap.pitch,
            total_twist=twist or component_cap.twist,
            max_rotation=max_rotation,
            direction=-1,
            lmove_vaj=lmove_vaj,
            jmove_vaj=jmove_vaj,
            j5_start=j5_start,
        )

        # exit (gripper stays ON — we're carrying the cap out)
        if exit and last_J is not None:
            J, C = self.core.IK(
                target_solid=tool.assembly[next(iter(tool.assembly))],
                target_anchor="tcp",
                target_offset=[0, 0, -20 -gap - height_cap, 0, 0, 0],
                tool_solid=tool.assembly[next(iter(tool.assembly))],
                tool_anchor="tcp",
                tool_offset=[0, 0, 0, 0, 0, 0],
                base_distance=self.base_distance,
                rail_step=self.rail_step,
                rail_span=self.rail_span,
                ref_joints=self.ref_joints,
                left_approach=self.left_approach,
            )
            if C != 2:
                raise RecipeError("could not find valid joints for exit")

            # keep the j5 we ended the screw on
            J[5] = last_J[5]

            rt.checkpoint()
            vel, accel, jerk = self.scaled_vaj(self.lmove_vaj)
            rt.lmove(joint=J, vel=vel, accel=accel, jerk=jerk)

            # unwind the screw turns: normalize j5 into ±180 (same tool
            # pose mod 360 — the free cap just spins in place) so the
            # outgoing carry plans from a centered wrist and the next
            # screw op has full winding room.
            self.rotate(rotation=0, joint="j5", limit=[-180, 180], vaj=jmove_vaj)

        # attach
        solid_cap.attach_to(
            parent=tool.assembly[next(iter(tool.assembly))],
            parent_anchor="tcp",
            child_anchor="top",
            offset_frame="parent",
            offset=[0, 0, 0, 0, 180, 0],
        )

        return True

    def cap(
        self,
        anchor="place",
        solid_name="body",
        approach=True,
        exit=True,
        padding=None,
        gap=2,
        lmove_vaj=[1000, 3000, 15000],
        jmove_vaj=[500, 3000, 15000],
        max_rotation=500,
        **kwargs,
    ):
        """Screw the currently-held cap onto the tube at ``anchor``.

        Inverse of ``decap``: lowers while rotating j5 forward in chunks of
        ``max_rotation`` degrees. Requires the cap to be gripped already and
        the tube to be present at ``anchor``. Attaches cap → tube on success.
        """
        rt = self.rt

        # ref joints
        if self.ref_joints is None:
            raise RecipeError("no reference joints defined")

        # tool
        tool = self.core.current_tool()
        if tool is None:
            raise RecipeError("no tool attached to the robot")

        # cap in tool
        solid_cap = self.solid_attached_to_tool(tool)
        if solid_cap is None:
            raise RecipeError("no item in the gripper")
        component_cap = self.workspace.components[solid_cap.component]

        # cap type
        if component_cap.cap_type != "screw":
            raise RecipeError(f"unsupported cap type: {component_cap.cap_type}")

        # tube in the anchor
        solid_tube = self.solid_attached_to_anchor(self.component.assembly[solid_name], anchor)
        if solid_tube is None:
            raise RecipeError(f"no item found in position {anchor}")
        component_tube = self.workspace.components[solid_tube.component]

        # height_cap
        height_cap = abs(
            dorna_pose.transform_pose(
                [0, 0, 0, 0, 0, 0],
                from_frame=solid_cap.pose("center"),
                to_frame=solid_cap.pose("top"),
            )[2]
        )

        # height tube
        height_tube = abs(
            dorna_pose.transform_pose(
                [0, 0, 0, 0, 0, 0],
                from_frame=solid_tube.pose("center"),
                to_frame=solid_tube.pose("top"),
            )[2]
        )

        # total height
        height_total = height_tube + height_cap

        # j5_start
        j5_start = -max_rotation / 2

        # place setting
        place_prm = self.place_setting(
            anchor="cap_seat",
            solid_name="body",
            component=component_tube,
            offset=[0, 0, 0, 0, 0, 0],
            approach=approach,
            exit=False,
            attachment=False,
            trigger_io=False,
            padding=padding,
            gap=gap,
            gravity_offset=0,
            soft_approach=True,
            **kwargs,
        )
        if not place_prm:
            raise RecipeError("cap failed — could not compute place parameters")

        # adjust j5 in the approach
        place_prm["approach_j5"] = j5_start
        if not self.touch(**place_prm):
            raise RecipeError("cap failed — touch motion failed")

        # chunked tighten (j5 rotates forward while z descends)
        last_J = self._screw_motion(
            tool=tool,
            pitch=component_cap.pitch,
            total_twist=component_cap.twist,
            max_rotation=max_rotation,
            direction=+1,
            lmove_vaj=lmove_vaj,
            jmove_vaj=jmove_vaj,
            j5_start=j5_start,
        )

        # release the cap (gripper OFF) so the exit clears without dragging it
        if tool.output_state() != 0:
            rt.checkpoint()
            rt.output(config=tool.output_disable)
            tool.output_state(0)

        # exit
        if exit and last_J is not None:
            J, C = self.core.IK(
                target_solid=tool.assembly[next(iter(tool.assembly))],
                target_anchor="tcp",
                target_offset=[0, 0, -height_total - gap, 0, 0, 0],
                tool_solid=tool.assembly[next(iter(tool.assembly))],
                tool_anchor="tcp",
                tool_offset=[0, 0, 0, 0, 0, 0],
                base_distance=self.base_distance,
                rail_step=self.rail_step,
                rail_span=self.rail_span,
                ref_joints=self.ref_joints,
                left_approach=self.left_approach,
            )
            if C != 2:
                raise RecipeError("could not find valid joints for exit")

            rt.checkpoint()
            vel, accel, jerk = self.scaled_vaj(self.lmove_vaj)
            rt.lmove(joint=J, vel=vel, accel=accel, jerk=jerk)

            # unwind the screw turns: normalize j5 into ±180 (same tool
            # pose mod 360 — the gripper is already empty here) so the
            # outgoing carry plans from a centered wrist.
            self.rotate(rotation=0, joint="j5", limit=[-180, 180], vaj=jmove_vaj)

        # attach cap to body
        solid_cap.attach_to(parent=solid_tube, parent_anchor="place", child_anchor="center")

        return True
