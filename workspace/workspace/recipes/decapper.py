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

    def _keep_wrist(self, kwargs):
        """On the infinite wrist, everything this station grips is round
        (tubes, caps), so no verb here has a reason to roll the wrist:
        default approach_j5/exit_j5 to "keep" (the current angle),
        letting an explicit caller value win."""
        if bool(getattr(self.core, "j5_infinite", False)):
            kwargs.setdefault("approach_j5", "keep")
            kwargs.setdefault("exit_j5", "keep")
        return kwargs

    def place(self, approach=True, exit=True, padding=None, **kwargs):
        """Place a tube into the decapper's ``place`` anchor.

        Thin override of ``Recipe.place`` with ``gravity_offset=0`` (the
        decapper holds the tube directly — no lift compensation) and a
        shorter default padding of 30 mm.
        """
        return super().place(anchor="place", approach=approach, exit=exit, padding=padding, gravity_offset=0,
                             **self._keep_wrist(kwargs))

    def pick(self, approach=True, exit=True, padding=None, compliant=False,
             soft_exit=False, **kwargs):
        """Pick a tube from the decapper's ``place`` anchor. Padding defaults to 30 mm.

        ``compliant`` defaults to False here: the decapper is a rigid jaw
        grip on a screwed-on cap, so a ``tool_tcp_z_offset`` over-drive really
        moves the tube on the tool and must fold into the attach offset (the
        base Recipe defaults compliant=True for suction/soft tools).

        ``soft_exit`` defaults False here: the chuck is released when
        the tube lifts, so the exit needs no staged pull-off — one
        continuous lift out."""
        return super().pick(anchor="place", approach=approach, exit=exit, padding=padding,
                            compliant=compliant, soft_exit=soft_exit,
                            **self._keep_wrist(kwargs))

    def decap(
        self,
        anchor="place",
        solid_name="body",
        approach=True,
        exit=True,
        padding=None,
        gap=2,
        lmove_vaj=[600, 2000, 8000],
        jmove_vaj=[500, 1000, 8000],
        max_rotation=500,
        twist=None,
        **kwargs,
    ):
        """Unscrew the cap off a tube sitting at ``anchor``.

        Twists the cap loose in chunks of ``max_rotation`` degrees, ascending by
        ``pitch/max_rotation`` per degree. Toggles the gripper between chunks
        to re-bite the cap. On success, the cap is attached to the tool.

        Args:
            anchor: Anchor holding the capped tube (default "place").
            padding, gap: Safe-height and near-gap (mm). ``padding``
                governs the approach AND the exit lift — the carried
                cap ends ``padding + gap`` above the tube.
            lmove_vaj, jmove_vaj: [vel, accel, jerk] for linear / joint moves.
            max_rotation: Maximum j5 swing per chunk (degrees).
            twist: Total rotation to unscrew (degrees). None (default)
                uses the cap component's declared ``twist``.
        """
        self._wire_verb("decap", anchor)
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

        # j5 staging: a LIMITED wrist pre-rotates to +max_rotation/2 so
        # every chunk has winding room within ±180°. The infinite wrist
        # (core.j5_infinite) needs none of that — the screw runs
        # RELATIVE from wherever the approach leaves the wrist, so no
        # staging rotation happens at all.
        one_shot = bool(getattr(self.core, "j5_infinite", False))
        j5_start = None if one_shot else max_rotation / 2
        motion_prm["approach_j5"] = "keep" if one_shot else j5_start

        # run the motion (touch already uses runtime internally in your updated Recipe)
        if not self.touch(**motion_prm):
            raise RecipeError("decap failed — touch motion failed")

        # unscrew (j5 rotates backward while z rises). One shot on the
        # infinite wrist — no re-bites, screwing relative from the
        # current wrist angle; chunk-and-rebite on a limited one.
        last_J = self._screw_motion(
            tool=tool,
            pitch=component_cap.pitch,
            total_twist=twist or component_cap.twist,
            max_rotation=max_rotation,
            direction=-1,
            lmove_vaj=lmove_vaj,
            jmove_vaj=jmove_vaj,
            j5_start=j5_start,
            rebite=not one_shot,
        )

        # exit (gripper stays ON — we're carrying the cap out). The
        # lift height is the SAME resolved padding the approach used
        # (per-call > recipe > default) — one knob, one meaning. A
        # fixed 20 here parked the cap inside the decapper's inflated
        # collision box and poisoned the next plan's start.
        if exit and last_J is not None:
            pad = self._padding(padding)
            J, C = self.core.IK(
                target_solid=tool.assembly[next(iter(tool.assembly))],
                target_anchor="tcp",
                target_offset=[0, 0, -pad - gap - height_cap, 0, 0, 0],
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

            # WALLS-CLEARED SPLIT (same cure as the rack soft exit):
            # the carried cap starts between the chuck jaws, and a
            # deposited lift fused into the next fold's spline+blend
            # can bend the path while still inside them (bench: the
            # rack version rubbed tubes on the way out). First leg —
            # up by gap + height_cap, the cap's bottom just clear of
            # the jaws — is a discrete TRUE lmove every run; only the
            # remaining free-air lift may deposit.
            J_gap, C = self.core.IK(
                target_solid=tool.assembly[next(iter(tool.assembly))],
                target_anchor="tcp",
                target_offset=[0, 0, -gap - height_cap, 0, 0, 0],
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
                raise RecipeError("could not find valid joints for the pull-off")
            J_gap[5] = last_J[5]

            rt.checkpoint()
            vaj = self.scaled_vaj(self.lmove_vaj)
            rt.lmove(joint=J_gap, vel=vaj[0], accel=vaj[1], jerk=vaj[2])
            if one_shot and self.fuse:
                # The remaining lift fuses with the next verb's travel
                # (the ride to the cap rack) — held, not executed.
                # Chunked decaps must execute it (the unwind below
                # needs the lift done).
                self._tail_deposit_lift(rt, J, vaj, owner="Decapper.decap lift")
            else:
                rt.lmove(joint=J, vel=vaj[0], accel=vaj[1], jerk=vaj[2])

            # Limited wrist: unwind the screw turns — normalize j5 into
            # ±180 (same tool pose mod 360, the free cap just spins in
            # place) so the outgoing carry plans from a centered wrist
            # and the next screw op has full winding room. The
            # infinite wrist skips the unwind entirely: j5 stays where
            # the screw left it and every later target is unwrapped to
            # its nearest equivalent (core.unwrap_j5), so nothing ever
            # rotates extra.
            if not one_shot:
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
        lmove_vaj=[500, 1000, 8000],
        jmove_vaj=[500, 1000, 8000],
        max_rotation=500,
        release=True,
        **kwargs,
    ):
        """Screw the currently-held cap onto the tube at ``anchor``.

        Inverse of ``decap``: lowers while rotating j5 forward in chunks of
        ``max_rotation`` degrees. Requires the cap to be gripped already and
        the tube to be present at ``anchor``. Attaches cap → tube on success.

        ``release=False``: the tighten's end state IS the pick — the
        gripper never opens. The capped tube is re-rooted on the tool
        at its CURRENT geometry (the screw's true end pose, no
        nominal-height re-grab), the chuck jaws open, and the exit
        lift carries it out (held as a fusable tail like decap's).
        Use instead of a follow-up ``pick()``: a closed gripper
        commanding approach/touch motion at the seat drags the sealed
        tube. """
        self._wire_verb("cap", anchor)
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

        # j5 staging — same rule as decap: explicit -max_rotation/2 only
        # for the limited wrist; the infinite wrist tightens relative
        # from wherever the place approach leaves j5.
        one_shot = bool(getattr(self.core, "j5_infinite", False))
        j5_start = None if one_shot else -max_rotation / 2

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

        # adjust j5 in the approach — the staging angle on a limited
        # wrist, the current angle (no roll at all) on the infinite one
        place_prm["approach_j5"] = "keep" if one_shot else j5_start
        if not self.touch(**place_prm):
            raise RecipeError("cap failed — touch motion failed")

        # tighten (j5 rotates forward while z descends) — one shot on
        # the infinite wrist, chunk-and-rebite on a limited one.
        last_J = self._screw_motion(
            tool=tool,
            pitch=component_cap.pitch,
            total_twist=component_cap.twist,
            max_rotation=max_rotation,
            direction=+1,
            lmove_vaj=lmove_vaj,
            jmove_vaj=jmove_vaj,
            j5_start=j5_start,
            rebite=not one_shot,
        )

        # attach cap to body — the screw seated it; the model follows
        # BEFORE any exit so a carried lift moves the whole stack.
        solid_cap.attach_to(parent=solid_tube, parent_anchor="place", child_anchor="center")

        if release:
            # gripper OFF so the empty exit clears without dragging the cap
            if tool.output_state() != 0:
                rt.checkpoint()
                rt.output(config=tool.output_disable)
                tool.output_state(0)
        else:
            # Carry ending (mirror of decap): re-root the capped tube on
            # the tool at the CURRENT relative pose, then open the chuck
            # jaws — station side only, the gripper is never touched.
            tool_body = tool.assembly[next(iter(tool.assembly))]
            attach_z = abs(dorna_pose.transform_pose(
                [0, 0, 0, 0, 0, 0],
                from_frame=solid_tube.pose("center"),
                to_frame=tool_body.pose("tcp"),
            )[2])
            solid_tube.attach_to(
                parent=tool_body,
                parent_anchor="tcp",
                child_anchor="center",
                offset=[0, 0, attach_z, 0, 180, 0],
                offset_frame="parent",
            )
            _, _, _, component_disable = self._build_io_config(
                tool, self.component, "component")
            rt.checkpoint()
            self._apply_output_config(rt, component_disable)

        # exit — the empty tool lifts to the SAME resolved padding the
        # approach used (tip ends padding + gap above the seated cap),
        # consistent with decap and every pick/place exit. The old
        # full-stack lift was generous for tall tubes but would park
        # the tool inside the inflated box after capping short vials.
        # Carrying (release=False) DOES need the full-stack term: the
        # capped tube hangs height_total below the grip, so the lift
        # must raise its BOTTOM clear of the chuck's padded box — a
        # tip-only lift leaves the next travel start-invalid (bench).
        if exit and last_J is not None:
            pad = self._padding(padding)
            lift = pad + gap + (0 if release else height_total)
            J, C = self.core.IK(
                target_solid=tool.assembly[next(iter(tool.assembly))],
                target_anchor="tcp",
                target_offset=[0, 0, -lift, 0, 0, 0],
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

            if one_shot:
                # Keep the j5 the tighten ended on — the gripper is
                # empty and the lift is pure z, so the infinite wrist
                # has no reason to reorient on the way out (IK's
                # preferred roll could be up to ~180° away). Mirrors
                # decap's exit.
                J[5] = last_J[5]

            rt.checkpoint()
            vaj = self.scaled_vaj(self.lmove_vaj)
            if not release:
                # WALLS-CLEARED SPLIT (same cure as decap): carrying
                # the capped tube out, its bottom starts between the
                # chuck jaws — the first gap + height_total of the
                # lift is a discrete TRUE lmove every run, and only
                # the free-air remainder may deposit. The empty-tool
                # ending (release=True) stays a single fusible lift,
                # like every place exit.
                J_gap, C = self.core.IK(
                    target_solid=tool.assembly[next(iter(tool.assembly))],
                    target_anchor="tcp",
                    target_offset=[0, 0, -gap - height_total, 0, 0, 0],
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
                    raise RecipeError("could not find valid joints for the pull-off")
                if one_shot:
                    J_gap[5] = last_J[5]
                rt.lmove(joint=J_gap, vel=vaj[0], accel=vaj[1], jerk=vaj[2])
            if one_shot and self.fuse:
                # Same as decap: the post-tighten lift rides into the
                # next verb's travel as a held tail.
                self._tail_deposit_lift(rt, J, vaj, owner="Decapper.cap lift")
            else:
                rt.lmove(joint=J, vel=vaj[0], accel=vaj[1], jerk=vaj[2])

            # Limited wrist: unwind the screw turns — normalize j5 into
            # ±180 (same tool pose mod 360, the gripper is already
            # empty here) so the outgoing carry plans from a centered
            # wrist. The infinite wrist skips it: later targets unwrap
            # to the nearest equivalent instead (core.unwrap_j5).
            if not one_shot:
                self.rotate(rotation=0, joint="j5", limit=[-180, 180], vaj=jmove_vaj)

        return True
