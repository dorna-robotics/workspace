# workspace/recipes/recipe.py

from copy import deepcopy
from mergedeep import merge
from dorna2 import pose as dorna_pose
from dorna2 import Pose


class RecipeError(Exception):
    """Raised when a recipe step fails (bad IK, missing tool, etc.)."""
    pass


class Recipe:
    DEFAULTS = dict(
        # ref joints
        target_solid_name="body",
        target_anchor="center",
        target_offset=[0, 0, 50, 0, 180, 0],
        initial_joints=[0, 0, 0, 0, 0, 0, 0, 0],
        # IK
        left_approach=True,
        base_distance=350,
        rail_step=0,  # step size
        rail_span=0,  # number of tries around that point positive and negative directions
        # motion
        motion_type="lmove",
        speed_factor=0.5,
        jmove_vaj=[200, 500, 3000],  # [200, 1200, 6000],
        lmove_vaj=[600, 1400, 6000],
        # calibration
        calibration_name=None,
        calibration=True,
        calibrate_abc=False,
        calibration_targets=None,  # auto-discovers clb_ anchors if None
        calibration_target_offset=[0, 0, 8, 0, 0, 0],
        calibration_tool_solid_name="body",
        calibration_tool_anchor="tcp",
        calibration_tool_offset=[0, 0, 0, 0, 0, 0],
    )

    def __init__(self, workspace, core, component, **kwargs):
        """Construct the recipe + IK-validate the scene at boot.

        Merges ``self.DEFAULTS`` with ``kwargs`` (caller wins), wires
        the workspace / core / component references, and **runs IK
        against the component's reference anchor immediately** so a
        misconfigured scene (bad anchors, unreachable poses, missing
        ``clb_*`` calibration targets) raises ``RecipeError`` at
        workspace boot — never silently during a workflow.

        Args:
            workspace: The :class:`Workspace` this recipe lives in.
            core: The :class:`Core` component (robot + kinematics).
            component: The component this recipe drives.
            **kwargs: Any ``DEFAULTS`` key — overrides for IK params
                (``left_approach``, ``base_distance``, …), motion
                (``speed_factor``, ``motion_type``, ``*_vaj``), or
                calibration (``calibration_name``, ``calibrate_abc``,
                …). Unknown keys are merged into the DEFAULTS dict
                but only used if ``DEFAULTS`` declares them.

        Raises:
            RecipeError: If no valid reference joints could be found
                for the component's ``target_solid_name`` /
                ``target_anchor`` / ``target_offset`` at the given
                ``base_distance`` / rail range. Fix the scene yaml or
                widen ``base_distance`` / ``rail_span`` and relaunch.
        """
        # prm
        prm = deepcopy(self.DEFAULTS)  # default
        merge(prm, kwargs)  # self

        # init
        self.workspace = workspace
        self.core = core
        self.component = component

        # IK
        self.left_approach = prm["left_approach"]
        self.base_distance = prm["base_distance"]
        self.rail_step = prm["rail_step"]
        self.rail_span = prm["rail_span"]

        # motion
        self.motion_type = prm["motion_type"]
        self.speed_factor = prm["speed_factor"]
        self.jmove_vaj = prm["jmove_vaj"]
        self.lmove_vaj = prm["lmove_vaj"]

        # calibration
        self.calibration = prm["calibration"]
        self.calibrate_abc = prm["calibrate_abc"]
        if prm["calibration_name"] is None:
            self.calibration_name = f"{self.component.name}_{self.left_approach}_{self.base_distance}_{self.rail_step}_{self.rail_span}"
        else:
            self.calibration_name = prm["calibration_name"]
        if prm["calibration_targets"] is None:
            self.calibration_targets = {
                k: [a for a in component.assembly[k].anchors if a.startswith("clb_")]
                for k in component.assembly
            }
        else:
            self.calibration_targets = prm["calibration_targets"]
        self.calibration_target_offset = prm["calibration_target_offset"]
        self.calibration_tool_solid_name = prm["calibration_tool_solid_name"]
        self.calibration_tool_anchor = prm["calibration_tool_anchor"]
        self.calibration_tool_offset = prm["calibration_tool_offset"]

        # find the reference joints used later for every IK
        J, C = self.core.IK(
            target_solid=self.component.assembly[prm["target_solid_name"]],
            target_anchor=prm["target_anchor"],
            target_offset=prm["target_offset"],
            base_distance=self.base_distance,
            rail_step=self.rail_step,
            rail_span=self.rail_span,
            ref_joints=prm["initial_joints"],
            left_approach=self.left_approach,
        )
        if C != 2:
            raise RecipeError(f"could not find a valid reference joint for {self.component.name}")
        self.ref_joints = J

    @property
    def rt(self):
        # Workspace Runtime (pause/stop/resume aware + robot_api proxy + lock)
        return self.workspace.rt

    # ── Axis-init helpers ───────────────────────────────────────────────────
    # Bundle the 3-step startup sequence (set_axis + set_pid + a homing
    # call) into a single recipe method. ``axis_cfg`` is the same dict
    # held in ``core.rail_cfg`` / ``feeder.axis_cfg`` and must contain:
    # axis, offset, usem, usee, pprm, tprm, ppre, tpre, p, i, d,
    # duration, threshold. The 1 s settle between SDK calls is via
    # ``rt.delay`` so an operator pause mid-startup is honoured.
    # SimulationAPI stubs each underlying SDK call to return 2
    # immediately, so these helpers work transparently in sim mode
    # too — no special-casing needed here.
    def set_axis_with_stop(self, axis_cfg, dir=-1):
        """Init an axis and home it against a hard stop (rail-style)."""
        api = self.core.robot_api
        rt = self.rt
        api.set_axis(
            index=axis_cfg["axis"],
            usem=axis_cfg["usem"], usee=axis_cfg["usee"],
            pprm=axis_cfg["pprm"], tprm=axis_cfg["tprm"],
            ppre=axis_cfg["ppre"], tpre=axis_cfg["tpre"],
        )
        rt.delay(1)
        api.set_pid(
            index=axis_cfg["axis"],
            p=axis_cfg["p"], i=axis_cfg["i"], d=axis_cfg["d"],
            duration=axis_cfg["duration"], threshold=axis_cfg["threshold"],
        )
        rt.delay(1)
        return api.home_with_stop(
            index=axis_cfg["axis"], val=axis_cfg["offset"], dir=dir,
        )

    def set_axis_with_encoder(self, axis_cfg):
        """Init an axis and home it against an encoder index (feeder-style)."""
        api = self.core.robot_api
        rt = self.rt
        api.set_axis(
            index=axis_cfg["axis"],
            usem=axis_cfg["usem"], usee=axis_cfg["usee"],
            pprm=axis_cfg["pprm"], tprm=axis_cfg["tprm"],
            ppre=axis_cfg["ppre"], tpre=axis_cfg["tpre"],
        )
        rt.delay(1)
        api.set_pid(
            index=axis_cfg["axis"],
            p=axis_cfg["p"], i=axis_cfg["i"], d=axis_cfg["d"],
            duration=axis_cfg["duration"], threshold=axis_cfg["threshold"],
        )
        rt.delay(1)
        return api.home_with_encoder_index(
            index=axis_cfg["axis"], val=axis_cfg["offset"],
        )

    # ── Solid / tool queries ────────────────────────────────────────────────

    def solid_attached_to_tool(self, tool):
        """Return the solid currently gripped by ``tool`` (at its ``tcp`` anchor), or None."""
        for child in tool.assembly[next(iter(tool.assembly))].children["tcp"]:
            return child["child_solid"]
        return None

    def solid_attached_to_anchor(self, solid, anchor):
        """Return the first child solid attached to ``anchor`` on ``solid``, or None.

        Used to check whether a slot/anchor is occupied (e.g. "is there a tube
        at rack slot A1?").
        """
        try:
            for child in solid.children[anchor]:
                return child["child_solid"]
        except Exception:
            pass
        return None

    def solid_hierarchy(self, parent_solid, parent_anchor, connection_anchor="place"):
        """Return the stack of solids sitting at ``parent_anchor`` of ``parent_solid``.

        Walks down through ``connection_anchor`` (default "place") repeatedly so
        that stacked items (e.g. a tube sitting on a cap sitting in a rack)
        are returned in bottom-up order.

        Returns:
            List of solids, bottom first. Empty if nothing is attached.
        """
        load_list = []
        first_child = self.solid_attached_to_anchor(parent_solid, parent_anchor)
        if first_child is None:
            return load_list
        load_list = [first_child]
        while True:
            child = self.solid_attached_to_anchor(load_list[-1], connection_anchor)
            if child is not None:
                load_list.append(child)
            else:
                return load_list

    # ── Shared helpers ──────────────────────────────────────────────────────

    def _apply_output_config(self, rt, output_list):
        """Apply an IO output list: [[config, get_call, set_call], ...]."""
        _output_config = []
        for _config, get_call, set_call in output_list:
            if set_call is not None:
                current_state = get_call[0](*get_call[1])
                new_state = set_call[1][0]
                if current_state != new_state:
                    _output_config += _config
                    set_call[0](*set_call[1])
            else:
                _output_config += _config
        rt.checkpoint()
        rt.output(config=_output_config)

    def _calibrate_offset(self, target_solid, target_anchor, offset):
        """Apply calibration correction to a single offset, return corrected offset."""
        pose_in_world = target_solid.pose(anchor=target_anchor, offset=offset)
        corrected_pose_frame = Pose(
            pose=self.core.calibration.interpolate(
                pose_in_world,
                dict_name=self.calibration_name,
                calibrate_abc=self.calibrate_abc,
            )
        )
        anchor_frame = Pose(pose=target_solid.pose(anchor=target_anchor))
        return corrected_pose_frame.pose(in_frame=anchor_frame)

    def _solve_ik(self, target_solid, target_anchor, offset, tool_dict, j5_override=None):
        """Solve IK for a single offset with optional calibration. Returns joint values."""
        if self.calibration:
            offset = self._calibrate_offset(target_solid, target_anchor, offset)

        J, C = self.core.IK(
            target_solid=target_solid,
            target_anchor=target_anchor,
            target_offset=offset,
            tool_solid=tool_dict["solid"],
            tool_anchor=tool_dict["anchor"],
            tool_offset=tool_dict["offset"],
            base_distance=self.base_distance,
            rail_step=self.rail_step,
            rail_span=self.rail_span,
            ref_joints=self.ref_joints,
            left_approach=self.left_approach,
        )

        if j5_override is not None:
            J[5] = j5_override

        if C != 2:
            raise RecipeError("could not find a valid pose")

        return J

    def _execute_motion_planned(self, rt, J, vaj_map, use_planning=False, motion_plan_kwargs={}):
        """Execute a single motion — with optional motion planning for collision avoidance."""
        if use_planning:
            points = self.core.motion_plan(joint=J, **motion_plan_kwargs)
            if len(points) == 0:
                raise RecipeError("no proper path was found")
            rt.smove(
                points,
                vel=vaj_map["jmove"][0] * self.speed_factor,
                accel=vaj_map["jmove"][1] * self.speed_factor,
                jerk=vaj_map["jmove"][2] * self.speed_factor,
            )
        else:
            rt.jmove(
                joint=J,
                vel=vaj_map["jmove"][0] * self.speed_factor,
                accel=vaj_map["jmove"][1] * self.speed_factor,
                jerk=vaj_map["jmove"][2] * self.speed_factor,
            )

    def _move_along_path(self, rt, path, target_solid, target_anchor, tool_dict, j5_override, vaj_map, has_motion_plan=False, first_approach=False, motion_plan_kwargs={}):
        """Execute a sequence of IK-solved motions along path offsets.

        has_motion_plan / first_approach: only the first step of an approach
        may use path planning.
        motion_plan_kwargs: extra args passed to core.motion_plan() (padding, gravity_vec, etc.)
        """
        for i, offset in enumerate(path):
            J = self._solve_ik(target_solid, target_anchor, offset, tool_dict, j5_override)
            rt.checkpoint()
            if i == 0 and first_approach:
                self._execute_motion_planned(rt, J, vaj_map, use_planning=has_motion_plan, motion_plan_kwargs=motion_plan_kwargs)
            else:
                self._do_motion(rt, J, tool_dict, vaj_map)

    def _do_motion(self, rt, J, tool_dict, vaj_map):
        """Dispatch a single motion step based on self.motion_type."""
        if self.motion_type == "lmove":
            tool_pose = [0, 0, 0, 0, 0, 0]
            if tool_dict["solid"] and tool_dict["anchor"]:
                tool_pose = tool_dict["solid"].pose(
                    anchor=tool_dict["anchor"],
                    in_frame=self.core.robot_flange,
                    offset=tool_dict["offset"],
                )
            rt.lmove(
                joint=J,
                vel=vaj_map["lmove"][0] * self.speed_factor,
                accel=vaj_map["lmove"][1] * self.speed_factor,
                jerk=vaj_map["lmove"][2] * self.speed_factor,
                tool_pose=tool_pose,
            )
        elif self.motion_type == "jmove":
            rt.jmove(
                joint=J,
                vel=vaj_map["jmove"][0] * self.speed_factor,
                accel=vaj_map["jmove"][1] * self.speed_factor,
                jerk=vaj_map["jmove"][2] * self.speed_factor,
            )
        else:
            getattr(rt, self.motion_type)(
                joint=J,
                vel=vaj_map["jmove"][0] * self.speed_factor,
                accel=vaj_map["jmove"][1] * self.speed_factor,
                jerk=vaj_map["jmove"][2] * self.speed_factor,
            )

    def _build_io_config(self, tool, component, trigger_io):
        """Build the four IO building-block lists for pick / place.

        Returns:
            Tuple ``(tool_enable, tool_disable, component_enable,
            component_disable)``. Each element is a list of
            ``[config, get_call, set_call]`` triples ready for
            ``_apply_output_config``. The caller composes them into
            ``output_approach`` / ``output_touch`` per the operation:

            - **pick**: approach = ``tool_disable + component_enable``,
              touch = ``tool_enable + component_disable``.
            - **place**: approach = ``component_disable + tool_enable``,
              touch = ``component_enable + tool_disable``.

            If ``trigger_io`` is False, returns four empty lists.
        """
        if not trigger_io:
            return [], [], [], []

        component_enable = []
        component_disable = []
        if getattr(component, "output_state", False):
            component_enable = [[
                component.output_enable,
                (component.output_state, ()),
                (component.output_state, (1,)),
            ]]
            component_disable = [[
                component.output_disable,
                (component.output_state, ()),
                (component.output_state, (0,)),
            ]]

        tool_enable = []
        tool_disable = []
        if getattr(tool, "output_state", False):
            tool_enable = [[
                tool.output_enable,
                (tool.output_state, ()),
                (tool.output_state, (1,)),
            ]]
            tool_disable = [[
                tool.output_disable,
                (tool.output_state, ()),
                (tool.output_state, (0,)),
            ]]

        return tool_enable, tool_disable, component_enable, component_disable

    def _resolve_attached_component(self, anchor="place", solid_name="body"):
        """Return ``(component, solid_name)`` for the item attached to this
        recipe's ``self.component.assembly[solid_name]`` at ``anchor``.

        Common indirection pattern for recipes whose component is a holder
        (pipetting site, dosing site, rack adapter) and the actual motion
        target is whatever's sitting on it.

        Raises:
            RecipeError: If nothing is attached at the anchor.
        """
        attached = self.solid_attached_to_anchor(self.component.assembly[solid_name], anchor)
        if attached is None:
            raise RecipeError(f"no component attached at {solid_name}/{anchor}")
        component = self.workspace.components[attached.component]
        resolved_solid_name = next(k for k, v in component.assembly.items() if v is attached)
        return component, resolved_solid_name

    def _get_tool_and_load_height(self):
        """Get the current tool, load_list attached to it, and height_load.

        Returns (tool, load_list, height_load).
        """
        tool = self.core.current_tool()
        if tool is None:
            raise RecipeError("no tool attached to the robot")
        load_list = [self.solid_attached_to_tool(tool)]
        if load_list[-1] is not None:
            load_list += self.solid_hierarchy(parent_solid=load_list[0], parent_anchor="place", connection_anchor="place")
            height_load = abs(
                dorna_pose.transform_pose(
                    [0, 0, 0, 0, 0, 0],
                    from_frame=load_list[0].pose("center"),
                    to_frame=load_list[-1].pose("top"),
                )[2]
            )
        else:
            height_load = 0
        return tool, load_list, height_load

    def _compute_pick_heights(self, component, solid_name, anchor, tool, load_list, tool_tcp_z_offset=0, tool_tip_z_offset=0):
        """Compute heights for pick operations. Returns (height_load, height_container, height_tool, pose_offset, tool_body)."""
        height_load = 0
        pose_offset = dorna_pose.Pose(pose=[0, 0, 0, 0, 0, 0])
        if load_list:
            height_load = abs(
                dorna_pose.transform_pose(
                    [0, 0, 0, 0, 0, 0],
                    from_frame=load_list[0].pose("center"),
                    to_frame=load_list[-1].pose("top"),
                )[2]
            )
            pose_offset = dorna_pose.Pose(
                pose=dorna_pose.transform_pose(
                    [0, 0, 0, 0, 0, 0],
                    from_frame=load_list[0].pose("center"),
                    to_frame=component.assembly[solid_name].pose(anchor),
                )
            )

        height_container = abs(
            dorna_pose.transform_pose(
                [0, 0, 0, 0, 0, 0],
                from_frame=component.assembly[solid_name].pose("top"),
                to_frame=component.assembly[solid_name].pose("place"),
            )[2]
        )

        tool_body = tool.assembly[next(iter(tool.assembly))]
        height_tool = abs(
            dorna_pose.transform_pose(
                [0, 0, tool_tip_z_offset - tool_tcp_z_offset, 0, 0, 0],
                from_frame=tool_body.pose("tip"),
                to_frame=tool_body.pose("tcp"),
            )[2]
        )

        return height_load, height_container, height_tool, pose_offset, tool_body

    def _screw_motion(self, tool, pitch, total_twist, max_rotation, direction,
                      lmove_vaj, jmove_vaj, j5_start):
        """Chunked screw/unscrew motion around the tool TCP's Z-axis.

        Each chunk rotates j5 by ``direction * chunk`` degrees while z
        advances by ``direction * pitch * chunk / max_rotation``. Between
        chunks, j5 rewinds to ``j5_start`` so the gripper can re-bite.
        The gripper is engaged during each screw lmove and released during
        the rewind jmove. After the final chunk no rewind happens — the
        caller is responsible for the exit motion and the final gripper state.

        Chunk order: for ``direction = -1`` (unscrew) the small remainder
        chunk runs first (small initial nudge, then full chunks); for
        ``direction = +1`` (screw in) the order is reversed (full chunks
        first, small finish last). Adjust by passing chunks yourself if
        you need a different pattern.

        Args:
            tool: Tool component on the robot — must expose
                ``output_enable``, ``output_disable``, ``output_state``.
            pitch: Thread pitch (mm per 360°).
            total_twist: Total rotation to apply (degrees, positive).
            max_rotation: Maximum j5 swing per chunk (degrees).
            direction: +1 (screw in) or -1 (unscrew).
            lmove_vaj: [vel, accel, jerk] for the screw lmoves.
            jmove_vaj: [vel, accel, jerk] for the rewind jmoves.
            j5_start: j5 value the robot rewinds to between chunks.

        Returns:
            The final joint vector reached (copy), or None if no chunks ran.

        Raises:
            RecipeError: If IK can't find a valid configuration for a chunk.
        """
        rt = self.rt
        tool_body = tool.assembly[next(iter(tool.assembly))]
        total_twist = int(total_twist)

        # build chunk list: [remainder, max, max, ...]
        chunks = ([total_twist % max_rotation] if total_twist % max_rotation else []) + \
                 [max_rotation] * (total_twist // max_rotation)
        if direction > 0:
            chunks = chunks[::-1]

        # pre-solve joint list so any IK failure aborts before motion
        joint_list = []
        z_offset = 0
        for chunk in chunks:
            z_offset += direction * pitch * chunk / max_rotation
            J, C = self.core.IK(
                target_solid=tool_body,
                target_anchor="tcp",
                target_offset=[0, 0, z_offset, 0, 0, 0],
                tool_solid=tool_body,
                tool_anchor="tcp",
                tool_offset=[0, 0, 0, 0, 0, 0],
                base_distance=self.base_distance,
                rail_step=self.rail_step,
                rail_span=self.rail_span,
                ref_joints=self.ref_joints,
                left_approach=self.left_approach,
            )
            if C != 2:
                raise RecipeError("could not find valid joints for screw motion")
            J[5] = j5_start + direction * chunk
            joint_list.append(J[:])

        # execute: gripper on during screw, off during rewind
        for i in range(len(joint_list)):
            if tool.output_state() != 1:
                rt.checkpoint()
                rt.output(config=tool.output_enable)
                tool.output_state(1)

            rt.checkpoint()
            rt.lmove(joint=joint_list[i], vel=lmove_vaj[0], accel=lmove_vaj[1], jerk=lmove_vaj[2])

            if i < len(joint_list) - 1:
                if tool.output_state() != 0:
                    rt.checkpoint()
                    rt.output(config=tool.output_disable)
                    tool.output_state(0)

                J_start = joint_list[i][:]
                J_start[5] = j5_start
                rt.checkpoint()
                rt.jmove(joint=J_start, vel=jmove_vaj[0], accel=jmove_vaj[1], jerk=jmove_vaj[2])

        return joint_list[-1][:] if joint_list else None

    # ── Core motion ─────────────────────────────────────────────────────────

    def touch(
        self,
        target_solid,
        target_anchor,
        target_offset=[0, 0, 0, 0, 0, 0],
        output_approach=[],
        approach_tool={"solid": None, "anchor": None, "offset": [0, 0, 0, 0, 0, 0]},
        approach_path=[],
        approach_j5=None,
        output_touch=[],
        actions=[],
        sleep=0,
        attach=[
            None,
            {
                "parent": None,
                "parent_anchor": None,
                "child_anchor": None,
                "offset": [0, 0, 0, 0, 0, 0],
                "offset_frame": "parent",
            },
        ],
        exit_tool={"solid": None, "anchor": None, "offset": [0, 0, 0, 0, 0, 0]},
        exit_path=[],
        exit_j5=None,
        output_exit=[],
        has_motion_plan=None,
        motion_plan_kwargs={},
        **kwargs,
    ):
        """Universal motion primitive used by pick/place/above/stand/immerse/retract.

        Flow:
            1. Apply ``output_approach`` IO.
            2. Move through ``approach_path`` waypoints, then to ``target_offset``.
               The very first hop can be path-planned if ``has_motion_plan`` is True.
            3. Apply ``output_touch`` IO, run ``actions``, sleep.
            4. Attach solids per ``attach`` spec (e.g. gripped item → tool).
            5. Retract along ``exit_path`` with ``exit_tool`` active.
            6. Apply ``output_exit`` IO.

        Normally you won't call ``touch`` directly — call ``pick``/``place``/``above``/
        ``stand`` which build the parameter dict via ``pick_setting`` / ``place_setting``.

        Args:
            target_solid: Solid that owns the target anchor.
            target_anchor: Name of the anchor on ``target_solid``.
            target_offset: [x, y, z, a, b, c] offset applied at touch-down.
                Set to None to skip the final touch step (used by ``above``/``stand``).
            approach_path: List of pre-positioning offsets before touch-down.
            exit_path: List of offsets after touch-down / attach.
            has_motion_plan: If True, use ``core.motion_plan`` for the first hop.
                Defaults to ``self.core.has_motion_plan``.
            motion_plan_kwargs: Dict forwarded to ``core.motion_plan``
                (e.g. padding, gravity_vec) when planning is on.
            **kwargs: Absorbs unused keys from pick_setting/place_setting output dicts.

        Returns:
            True on success.
        """
        rt = self.rt
        has_motion_plan = self.core.has_motion_plan if has_motion_plan is None else has_motion_plan
        vaj_map = {
            "jmove": self.jmove_vaj,
            "lmove": self.lmove_vaj,
        }

        # output approach
        self._apply_output_config(rt, output_approach)

        # approach path
        if target_offset is None:
            path = approach_path[:]
        else:
            path = approach_path[:] + [target_offset]

        self._move_along_path(
            rt, path, target_solid, target_anchor,
            tool_dict=approach_tool,
            j5_override=approach_j5,
            vaj_map=vaj_map,
            has_motion_plan=has_motion_plan,
            first_approach=bool(approach_path),
            motion_plan_kwargs=motion_plan_kwargs,
        )

        # output touch
        self._apply_output_config(rt, output_touch)

        # sleep + actions
        rt.checkpoint()
        rt.delay(sleep)
        for func, args, kwargs in actions:
            rt.checkpoint()
            func(*args, **kwargs)

        # attach
        if isinstance(attach, (list, tuple)) and len(attach) == 2 and attach[0] is not None:
            attach[0].attach_to(**attach[1])

        # exit path
        self._move_along_path(
            rt, list(exit_path), target_solid, target_anchor,
            tool_dict=exit_tool,
            j5_override=exit_j5,
            vaj_map=vaj_map,
        )

        # output exit
        self._apply_output_config(rt, output_exit)

        return True

    # ── Pick / Place settings ───────────────────────────────────────────────

    def pick_setting(
        self,
        anchor,
        solid_name="body",
        component=None,
        approach=True,
        actions=[],
        exit=True,
        attachment=True,
        trigger_io=True,
        padding=50,
        gap=2,
        tool_tcp_z_offset=0,
        tool_tip_z_offset=0,
        soft_approach=False,
        **kwargs,
    ):
        """Compute the motion-parameter dict that ``pick`` (and friends) feed to ``touch``.

        Call this directly when you need to tweak the returned fields before
        running the motion (see subclass overrides in ``adapter.py``, ``hotel.py``).
        Otherwise use ``pick``.

        Args:
            anchor: Target anchor name on the component (e.g. "place", "A1").
            solid_name: Which assembly solid owns the anchor (default "body").
            component: Component to target. Defaults to ``self.component``.
            approach: If True, build approach waypoints from padding/gap.
                Set False for a direct one-shot motion (no planning, no hover).
            actions: List of ``(fn, args, kwargs)`` called during the touch phase.
            exit: If True, build an exit path retracting to ``padding`` height.
            attachment: If True, attach the picked solid to the tool at touch-down.
            trigger_io: If True, build tool/component enable-disable IO lists
                (``output_approach`` / ``output_touch``).
            padding: Safe-height above the target (mm) for approach and exit.
            gap: Clearance above the load used as the soft-approach waypoint (mm).
            tool_tcp_z_offset: Shift TCP by this Z (mm). Negative = drive deeper;
                e.g. ``-5`` for suction cups, ``-2`` for decappers.
            tool_tip_z_offset: Shift tool tip (tip-to-TCP length) by this Z (mm).
            soft_approach: If True, insert a second approach waypoint just above
                the load for a vertical final descent (recommended for racks).
            **kwargs: Any attribute on ``self`` named here is overwritten (e.g.
                ``speed_factor``, ``motion_type``).

        Returns:
            Dict with keys: target_solid, target_anchor, target_offset,
            output_approach, approach_tool, approach_path, output_touch, actions,
            sleep, attach, exit_tool, exit_path, output_exit, height_tool,
            height_load, height_container, load_list, tool, pose_offset.
        """
        for k, v in kwargs.items():
            if hasattr(self, k):
                setattr(self, k, v)

        component = component or self.component

        if self.ref_joints is None:
            raise RecipeError("no reference joints defined")

        tool = self.core.current_tool()
        if tool is None:
            raise RecipeError("no tool attached to the robot")

        # items stacked at this anchor
        load_list = self.solid_hierarchy(
            parent_solid=component.assembly[solid_name], parent_anchor=anchor, connection_anchor="place"
        )

        # heights
        height_load, height_container, height_tool, pose_offset, tool_body = self._compute_pick_heights(
            component, solid_name, anchor, tool, load_list,
            tool_tcp_z_offset=tool_tcp_z_offset, tool_tip_z_offset=tool_tip_z_offset,
        )

        # target offset
        target_offset = pose_offset.pose(offset=[0, 0, height_load, 0, 0, 0])

        # approach path
        approach_path = []
        if approach:
            _approach_path = [
                [0, 0, max(height_load, height_container) + padding, 0, 0, 0],
                [0, 0, height_load + height_tool + gap, 0, 0, 0],
            ]
            if not soft_approach:
                _approach_path = _approach_path[0:1]
            approach_path = [pose_offset.pose(offset=p) for p in _approach_path]

        # exit path
        exit_path = []
        if exit:
            _exit_path = [[0, 0, max(height_load, height_container) + padding, 0, 0, 0]]
            exit_path = [pose_offset.pose(offset=p) for p in _exit_path]

        # IO config
        output_approach = []
        output_touch = []
        output_exit = []
        if trigger_io:
            tool_enable, tool_disable, component_enable, component_disable = self._build_io_config(tool, component, trigger_io)
            output_approach = tool_disable + component_enable
            output_touch = tool_enable + component_disable

        # attachment
        attach = [
            None,
            {"parent": None, "parent_anchor": None, "child_anchor": None, "offset": [0, 0, 0, 0, 0, 0], "offset_frame": "parent"},
        ]
        exit_tool = {
            "solid": tool_body,
            "anchor": "tcp",
            "offset": [0, 0, tool_tcp_z_offset, 0, 180, 0],
        }
        if attachment:
            attach = [
                load_list[0],
                {
                    "parent": tool_body,
                    "parent_anchor": "tcp",
                    "child_anchor": "center",
                    "offset": [0, 0, height_load + tool_tcp_z_offset, 0, 180, 0],
                    "offset_frame": "parent",
                },
            ]
            exit_tool = {"solid": load_list[0], "anchor": "center", "offset": [0, 0, 0, 0, 0, 0]}

        return {
            "target_solid": component.assembly[solid_name],
            "target_anchor": anchor,
            "target_offset": target_offset,
            "output_approach": output_approach,
            "approach_tool": {
                "solid": tool_body,
                "anchor": "tcp",
                "offset": [0, 0, tool_tcp_z_offset, 0, 180, 0],
            },
            "approach_path": approach_path,
            "output_touch": output_touch,
            "actions": actions,
            "sleep": 0,
            "attach": attach,
            "exit_tool": exit_tool,
            "exit_path": exit_path,
            "output_exit": output_exit,
            "height_tool": height_tool,
            "height_load": height_load,
            "height_container": height_container,
            "load_list": load_list,
            "tool": tool,
            "pose_offset": pose_offset,
        }

    def pick(
        self,
        anchor,
        solid_name="body",
        component=None,
        approach=True,
        actions=[],
        exit=True,
        attachment=True,
        trigger_io=True,
        padding=50,
        gap=2,
        tool_tcp_z_offset=0,
        tool_tip_z_offset=0,
        soft_approach=False,
        **kwargs,
    ):
        """Pick the item at ``anchor``: approach, close gripper, attach, exit.

        Wraps ``pick_setting(...)`` → ``touch(...)``. See ``pick_setting`` for
        parameter meanings and tool-specific tips.

        Example:
            >>> rcp["tube_rack"].pick(anchor="A1")
            >>> rcp["tube_rack"].pick(anchor="A1", tool_tcp_z_offset=-5)  # suction cup
        """
        pick_prm = self.pick_setting(
            anchor, solid_name,
            component=component, approach=approach, actions=actions,
            exit=exit, attachment=attachment, trigger_io=trigger_io,
            padding=padding, gap=gap,
            tool_tcp_z_offset=tool_tcp_z_offset, tool_tip_z_offset=tool_tip_z_offset,
            soft_approach=soft_approach, **kwargs,
        )
        if not pick_prm:
            raise RecipeError("pick_setting failed — could not compute pick parameters")
        return self.touch(**pick_prm, motion_plan_kwargs=kwargs.get("motion_plan_kwargs", {}))

    def place_setting(
        self,
        anchor,
        solid_name="body",
        component=None,
        offset=[0, 0, 0, 0, 0, 0],
        approach=True,
        actions=[],
        exit=True,
        attachment=True,
        trigger_io=True,
        padding=50,
        gap=2,
        load_anchor="center",
        gravity_offset=1,
        soft_approach=False,
        **kwargs,
    ):
        """Compute the motion-parameter dict for ``place`` / friends.

        Mirror of ``pick_setting`` for the reverse direction: assumes the
        gripper already holds the load (``solid_attached_to_tool``) and plans
        waypoints to release it at ``anchor``.

        Args:
            anchor: Destination anchor on the component.
            solid_name: Assembly solid owning the anchor (default "body").
            component: Component to target. Defaults to ``self.component``.
            offset: [x, y, z, a, b, c] applied to the target pose.
            approach: If True, build approach waypoints from padding/gap.
            actions: ``(fn, args, kwargs)`` list run during the touch phase.
            exit: If True, build an exit path retracting to padding height.
            attachment: If True, transfer the held solid to the destination
                anchor on touch-down (so it "lives" there afterwards).
            trigger_io: If True, build tool/component enable-disable IO lists.
            padding: Safe-height above the target (mm).
            gap: Clearance used as the soft-approach waypoint (mm).
            load_anchor: Anchor on the held solid used as its reference point
                (default "center").
            gravity_offset: Z-offset (mm) at touch-down. Positive = release
                slightly above target (typical for 2/4-finger grippers).
                Negative = drive deeper (typical for suction cups with leveler).
                See ``docs/parameter-guidelines.md`` for guidance.
            soft_approach: If True, insert a second approach waypoint just
                above the target for a vertical final descent. Recommended
                for racks.
            **kwargs: Any attribute on ``self`` named here is overwritten.

        Returns:
            Dict consumed by ``touch`` — same shape as ``pick_setting`` output.
        """
        for k, v in kwargs.items():
            if hasattr(self, k):
                setattr(self, k, v)

        component = component or self.component

        if self.ref_joints is None:
            raise RecipeError("no reference joints defined")

        tool = self.core.current_tool()
        if tool is None:
            raise RecipeError("no tool attached to the robot")

        # hierarchy of items attached to the tool
        load_list = [self.solid_attached_to_tool(tool)]
        if load_list[-1] is None:
            raise RecipeError("no item in the gripper")
        load_list += self.solid_hierarchy(parent_solid=load_list[0], parent_anchor="place", connection_anchor="place")

        # height load
        height_load = abs(
            dorna_pose.transform_pose(
                [0, 0, 0, 0, 0, 0],
                from_frame=load_list[0].pose(load_anchor),
                to_frame=load_list[-1].pose("top"),
            )[2]
        )

        # height container
        height_container = max(
            -dorna_pose.transform_pose(
                offset,
                from_frame=component.assembly[solid_name].pose(anchor),
                to_frame=component.assembly[solid_name].pose("top"),
            )[2],
            0,
        )

        # height tool
        tool_body = tool.assembly[next(iter(tool.assembly))]
        height_tool = abs(
            dorna_pose.transform_pose(
                [0, 0, 0, 0, 0, 0],
                from_frame=tool_body.pose("tcp"),
                to_frame=tool_body.pose("tip"),
            )[2]
        )

        # approach path
        approach_path = []
        if approach:
            _approach_path = [
                [0, 0, max(height_load, height_container) + padding, 0, 0, 0],
                [0, 0, height_container + gap, 0, 0, 0],
            ]
            if not soft_approach:
                _approach_path = _approach_path[0:1]
            approach_path = [dorna_pose.transform_pose(p, from_frame=offset, to_frame=[0, 0, 0, 0, 0, 0]) for p in _approach_path]

        # exit path
        exit_path = []
        if exit:
            _exit_path = [[0, 0, max(height_load, height_container) + padding, 0, 0, 0]]
            exit_path = [dorna_pose.transform_pose(p, from_frame=offset, to_frame=[0, 0, 0, 0, 0, 0]) for p in _exit_path]

        # IO config
        output_approach = []
        output_touch = []
        output_exit = []
        if trigger_io:
            tool_enable, tool_disable, component_enable, component_disable = self._build_io_config(tool, component, trigger_io)
            output_approach = component_disable + tool_enable
            output_touch = component_enable + tool_disable

        # attachment
        attach = [
            None,
            {"parent": None, "parent_anchor": None, "child_anchor": None, "offset": [0, 0, 0, 0, 0, 0], "offset_frame": "parent"},
        ]
        exit_tool = {"solid": load_list[0], "anchor": load_anchor, "offset": [0, 0, 0, 0, 0, 0]}
        if attachment:
            attach = [
                load_list[0],
                {
                    "parent": component.assembly[solid_name],
                    "parent_anchor": anchor,
                    "child_anchor": load_anchor,
                    "offset": offset,
                    "offset_frame": "parent",
                },
            ]
            exit_tool = {
                "solid": tool_body,
                "anchor": "tcp",
                "offset": [0, 0, 0, 0, 180, 0],
            }

        # gravity compensation
        target_offset = offset[:]
        target_offset[2] += gravity_offset

        return {
            "target_solid": component.assembly[solid_name],
            "target_anchor": anchor,
            "target_offset": target_offset,
            "output_approach": output_approach,
            "approach_tool": {"solid": load_list[0], "anchor": load_anchor, "offset": [0, 0, 0, 0, 0, 0]},
            "approach_path": approach_path,
            "output_touch": output_touch,
            "actions": actions,
            "sleep": 0,
            "attach": attach,
            "exit_tool": exit_tool,
            "exit_path": exit_path,
            "output_exit": output_exit,
            "height_tool": height_tool,
            "height_load": height_load,
            "height_container": height_container,
            "load_list": load_list,
            "tool": tool,
        }

    def place(
        self,
        anchor,
        solid_name="body",
        component=None,
        offset=[0, 0, 0, 0, 0, 0],
        approach=True,
        actions=[],
        exit=True,
        attachment=True,
        trigger_io=True,
        padding=50,
        gap=2,
        load_anchor="center",
        gravity_offset=1,
        soft_approach=False,
        **kwargs,
    ):
        """Place the held item at ``anchor``: approach, release, detach, exit.

        Wraps ``place_setting(...)`` → ``touch(...)``. See ``place_setting`` for
        parameter meanings and gripper-specific tips.

        Example:
            >>> rcp["tube_rack"].place(anchor="A1")
            >>> rcp["tube_rack"].place(anchor="A1", soft_approach=True)  # racks
            >>> rcp["tube_rack"].place(anchor="A1", gravity_offset=-10)  # suction elbow
        """
        place_prm = self.place_setting(
            anchor=anchor, solid_name=solid_name,
            component=component, offset=offset, approach=approach,
            actions=actions, exit=exit, attachment=attachment,
            trigger_io=trigger_io, padding=padding, gap=gap,
            load_anchor=load_anchor, gravity_offset=gravity_offset,
            soft_approach=soft_approach, **kwargs,
        )
        if not place_prm:
            raise RecipeError("place_setting failed — could not compute place parameters")
        return self.touch(**place_prm, motion_plan_kwargs=kwargs.get("motion_plan_kwargs", {}))

    # ── High-level motions ──────────────────────────────────────────────────
    def above(self, anchor, solid_name="body", component=None, padding=50, tool_tcp_z_offset=0, tool_tip_z_offset=0, **kwargs):
        """Hover ``padding`` mm above ``anchor`` — no touch, no attach, no IO.

        Uses ``pick_setting`` to compute the safe-above waypoint and stops
        there. Useful as a pre-positioning step before inspection or manual
        work. Runs a planned ``smove`` if ``core.has_motion_plan`` is on,
        otherwise a plain ``jmove``.

        Args:
            anchor: Target anchor on the component.
            padding: Height above the container/load top (mm).
            tool_tcp_z_offset, tool_tip_z_offset: Tool Z shifts — see pick_setting.
            **kwargs: Forwarded to pick_setting / touch.

        Example:
            >>> rcp["inspector_1"].above("place", padding=80)
        """
        pick_prm = self.pick_setting(
            anchor, solid_name,
            component=component, actions=[], exit=False,
            attachment=False, trigger_io=False, padding=padding,
            tool_tcp_z_offset=tool_tcp_z_offset, tool_tip_z_offset=tool_tip_z_offset,
            **kwargs,
        )
        if not pick_prm:
            raise RecipeError("above failed — could not compute pick parameters")
        pick_prm.pop("pose_offset", None)
        pick_prm["target_offset"] = None
        pick_prm["approach_path"] = pick_prm["approach_path"][0:1]
        return self.touch(**pick_prm, **kwargs)

    def stand(self, anchor, offset=[0, 0, 0, 0, 0, 0], solid_name="body", component=None, tool_tcp_z_offset=0, tool_tip_z_offset=0, **kwargs):
        """Move to a single pose at ``offset`` relative to ``anchor``'s frame.

        Pure positioning primitive — no approach waypoints, no touch-down,
        no attach, no IO. The offset is interpreted in the anchor's local
        frame (same convention as ``pick_setting``'s internal waypoints).

        Args:
            anchor: Target anchor on the component.
            offset: [x, y, z, a, b, c] in mm + Euler degrees, in the anchor frame.
                Default = stand exactly at the anchor.
            tool_tcp_z_offset, tool_tip_z_offset: Tool Z shifts.
            **kwargs: Forwarded to pick_setting / touch.

        Example:
            >>> rcp["inspector_1"].stand("place", offset=[0, 0, 30, 0, 0, 0])
            >>> rcp["inspector_1"].stand("place", offset=[10, 0, 50, 0, 0, 45])
        """
        pick_prm = self.pick_setting(
            anchor, solid_name,
            component=component, actions=[], exit=False,
            attachment=False, trigger_io=False,
            tool_tcp_z_offset=tool_tcp_z_offset, tool_tip_z_offset=tool_tip_z_offset,
            **kwargs,
        )
        if not pick_prm:
            raise RecipeError("stand failed — could not compute parameters")
        pose_offset = pick_prm.pop("pose_offset")
        pick_prm["target_offset"] = None
        pick_prm["approach_path"] = [pose_offset.pose(offset=offset)]
        return self.touch(**pick_prm, **kwargs)

    def rotate(self, rotation=90, joint="j5", limit=[-175, 175], vaj=[500, 3000, 15000], **kwargs):
        """Rotate a single joint by ``rotation`` degrees (default j5 ±175°).

        Wraps around ``limit`` so the resulting joint stays in range.

        Args:
            rotation: Degrees to add (can be negative).
            joint: Joint name, e.g. "j0" .. "j5".
            limit: [min, max] joint range used for wrap-around.
            vaj: [velocity, accel, jerk] for the jmove.
        """
        rt = self.rt

        current_joint = rt.joint()
        joint_index = int(joint[1:])
        new_joint = current_joint[:]
        new_joint[joint_index] = (new_joint[joint_index] + rotation + limit[1]) % abs(limit[1] - limit[0]) + limit[0]

        rt.checkpoint()
        rt.jmove(joint=new_joint, vel=vaj[0], accel=vaj[1], jerk=vaj[2])
        rt.delay(0.1)
        return True

    def vibrate(self, pattern=[[2.5, 0, 0], [-2.5, 0, 0]], cnt=5, vaj=[300, 10000, 20000], **kwargs):
        """Oscillate the robot flange through a small Cartesian pattern.

        Useful for shaking a tip free, loosening a seal, or mixing.

        Args:
            pattern: List of [x, y, z] offsets in the flange's output frame.
                The robot sweeps through them in order.
            cnt: Repeat count.
            vaj: [velocity, accel, jerk] for each jmove.
        """
        rt = self.rt

        current_joint = rt.joint()

        pattern = [
            dorna_pose.transform_pose(
                p + self.core.assembly["robot_flange"].pose("output")[3:],
                from_frame=[0, 0, 0, 0, 0, 0],
                to_frame=[0, 0, 0] + self.core.assembly["robot_flange"].pose("output")[3:],
            )
            for p in pattern
        ]

        joint_list = []
        for p in pattern:
            J, C = self.core.IK(
                target_solid=self.core.assembly["robot_flange"],
                target_anchor="output",
                target_offset=p,
                tool_solid=None,
                tool_anchor=None,
                tool_offset=[0, 0, 0, 0, 0, 0],
                base_distance=self.base_distance,
                rail_step=self.rail_step,
                rail_span=self.rail_span,
                left_approach=self.left_approach,
                ref_joints=self.ref_joints,
            )
            if C == 2:
                joint_list.append(J)
            else:
                raise RecipeError("could not find a valid approach")

        joint_list = cnt * joint_list
        joint_list.append(current_joint)

        for J in joint_list:
            rt.checkpoint()
            rt.jmove(
                joint=J,
                vel=vaj[0] * self.speed_factor,
                accel=vaj[1] * self.speed_factor,
                jerk=vaj[2] * self.speed_factor,
            )
        return True

    def park(self, joint, has_motion_plan=None, motion_plan_kwargs={}, **kwargs):
        """Move the robot to a known ``joint`` configuration — typically the
        safe parking pose, invoked from a ``trigger="park"`` Action.

        Uses :py:meth:`_execute_motion_planned` so the move honors
        :pyattr:`speed_factor`, dispatches to ``core.motion_plan`` +
        ``rt.smove`` when planning is on (collision-aware), and falls
        back to a plain ``rt.jmove`` otherwise. A ``rt.checkpoint()`` is
        issued first so the operator can still Pause / Resume on the
        way to the park pose. The motion uses ``self.jmove_vaj`` scaled
        by ``self.speed_factor``.

        Args:
            joint: Target joint vector (degrees). May be shorter than
                the robot's full joint vector — the missing trailing
                entries are filled from ``rt.joint()`` so the auxiliary
                axes (rail, second rail, …) stay put. Same pattern as
                :py:meth:`rotate`, which reads the live joints and
                overrides a single index.
            has_motion_plan: If ``True``, plan a collision-free path
                via ``core.motion_plan``; if ``False``, a single
                ``jmove``. If ``None`` (default), follows
                ``core.has_motion_plan``.
            motion_plan_kwargs: Forwarded to ``core.motion_plan``
                (padding, gravity_vec, etc.) when planning is on.

        Example:
            >>> rcp["robot"].park(joint=[0, 0, 90, 0, 90, 0])     # 6 joints — aux axes unchanged
            >>> rcp["robot"].park(joint=PARK_JOINTS, has_motion_plan=True,
            ...                   motion_plan_kwargs={"padding": 30})
        """
        rt = self.rt

        has_motion_plan = self.core.has_motion_plan if has_motion_plan is None else has_motion_plan

        # Overlay the caller's target onto the live joints so a partial
        # vector (e.g. just the 6 robot joints) leaves the auxiliary
        # axes — rail position, second rail, etc — where they are. Same
        # idea as ``rotate`` reading rt.joint() before overriding a
        # single index.
        target = list(rt.joint())
        target[:len(joint)] = list(joint)

        vaj_map = {
            "jmove": self.jmove_vaj,
            "lmove": self.lmove_vaj,
        }

        rt.checkpoint()
        self._execute_motion_planned(
            rt, target, vaj_map,
            use_planning=has_motion_plan,
            motion_plan_kwargs=motion_plan_kwargs,
        )
        return True

    def immerse(self, dist=0, anchor="place", solid_name="body", component=None, approach=False, exit=False, attachment=False, trigger_io=False, padding=10, **kwargs):
        """Dip the held load ``dist`` mm into ``anchor`` (tip goes below the anchor surface).

        Two patterns selectable via ``approach``:

        - ``approach=False`` (default): two-phase motion. First hovers at the
          container top via ``above`` (depth-independent), then dives straight
          down with ``pick(approach=False)``. Safer when ``dist`` is large
          because the hover pose ignores depth.
        - ``approach=True``: single-phase motion via ``pick(approach=True)``,
          so the full approach corridor (padding/gap waypoints) is used and
          the depth offset is applied throughout. More efficient when ``dist``
          is small; requires that ``padding`` comfortably exceeds the load height.

        No attach / IO — used for aspirating, dipping, etc.

        Args:
            dist: Depth below the anchor surface (mm). 0 = tip touches surface.
            anchor: Target anchor (default "place").
            approach: Pattern selector (see above).
            padding: Safe height above the target (mm).
            exit/attachment/trigger_io: All False by default.

        Example:
            >>> rcp["doser"].immerse(dist=10)                  # hover+dive
            >>> rcp["pipetting_site"].immerse(dist=5, approach=True)  # single motion
        """
        _, _, height_load = self._get_tool_and_load_height()

        tool_tcp_z_offset = height_load - dist
        tool_tip_z_offset = height_load - dist

        if approach:
            return self.pick(
                anchor=anchor, solid_name=solid_name, component=component,
                approach=True, exit=exit, attachment=attachment, trigger_io=trigger_io,
                padding=padding,
                tool_tcp_z_offset=tool_tcp_z_offset,
                tool_tip_z_offset=tool_tip_z_offset,
                **kwargs,
            )

        if self.above(anchor=anchor, solid_name=solid_name, component=component, padding=padding, tool_tcp_z_offset=height_load, tool_tip_z_offset=height_load, **kwargs):
            return self.pick(anchor=anchor, solid_name=solid_name, component=component, approach=False, exit=exit, attachment=attachment, trigger_io=trigger_io, padding=padding, tool_tcp_z_offset=tool_tcp_z_offset, tool_tip_z_offset=tool_tip_z_offset, **kwargs)
        raise RecipeError("immerse failed — could not move above target")

    def retract(self, dist=0, anchor="place", solid_name="body", component=None, padding=0, has_motion_plan=False, **kwargs):
        """Inverse of ``immerse`` — lift the held load ``dist`` mm above ``anchor``.

        Under the hood, calls ``above`` with tool Z offsets shifted so the tip
        ends up (anchor + load-height + dist) above the surface. No planning
        by default (has_motion_plan=False).

        Args:
            dist: Extra lift above the natural load-height clearance (mm).
            anchor: Reference anchor (default "place").
            padding: Extra padding applied by ``above`` (mm, default 0).

        Example:
            >>> rcp["doser"].retract(dist=20)   # lift tip 20mm above surface
        """
        _, _, height_load = self._get_tool_and_load_height()

        tool_tcp_z_offset = height_load + dist
        tool_tip_z_offset = height_load + dist
        return self.above(anchor=anchor, solid_name=solid_name, component=component, padding=padding, tool_tcp_z_offset=tool_tcp_z_offset, tool_tip_z_offset=tool_tip_z_offset, has_motion_plan=has_motion_plan, **kwargs)

    # ── Calibration ─────────────────────────────────────────────────────────

    def calibrate_anchor(self, target_solid, target_anchor, target_offset, tool_solid, tool_anchor, tool_offset):
        """Guided single-point calibration for one anchor. Interactive — prompts operator.

        Flow:
            1. Moves the robot to the computed pose (IK-solved).
            2. Prompts operator to nudge the robot onto the real calibration point.
            3. Records the corrected joint values and stores the offset in
               ``core.calibration`` under ``self.calibration_name``.

        Normally called by ``calibrate()`` rather than directly.
        """
        rt = self.rt

        J, C = self.core.IK(
            target_solid=target_solid,
            target_anchor=target_anchor,
            target_offset=target_offset,
            tool_solid=tool_solid,
            tool_anchor=tool_anchor,
            tool_offset=tool_offset,
            base_distance=self.base_distance,
            rail_step=self.rail_step,
            rail_span=self.rail_span,
            left_approach=self.left_approach,
            ref_joints=self.ref_joints,
        )
        if C == 2:
            rt.checkpoint()
            rt.jmove(
                joint=J,
                vel=self.jmove_vaj[0] * self.speed_factor,
                accel=self.jmove_vaj[1] * self.speed_factor,
                jerk=self.jmove_vaj[2] * self.speed_factor,
            )
        else:
            raise RecipeError("could not find a valid approach to the calibration point")

        rt.checkpoint()
        input("2- 🎯 take the robot to the calibration point...")

        corrected_joint_values = rt.joint()
        corrected_xyz_values = tool_solid.pose(anchor=tool_anchor, offset=tool_offset)
        raw_xyz_values = target_solid.pose(anchor=target_anchor, offset=[0, 0, 0, 0, 0, 0])

        rt.checkpoint()
        input("3- ⬆️ take the robot out of the calibration point...")

        self.core.calibration.add_point(raw_xyz_values, corrected_xyz_values, threshold=1e-3, dict_name=self.calibration_name)
        return True

    def calibrate(self, calibration_targets={}):
        """Run guided calibration on every anchor in ``calibration_targets``.

        ``calibration_targets`` is ``{solid_name: [anchor_name, ...]}``. If not
        provided, ``self.calibration_targets`` is used (usually auto-discovered
        from ``clb_*`` anchors on the component). Each anchor triggers a
        ``calibrate_anchor`` prompt — interactive.
        """
        tool = self.core.current_tool()
        tool_solid = tool.assembly[self.calibration_tool_solid_name]

        _calibration_targets = calibration_targets or self.calibration_targets
        for solid in _calibration_targets:
            calibration_target_solid = self.component.assembly[solid]
            for anchor in _calibration_targets[solid]:
                self.calibrate_anchor(
                    target_solid=calibration_target_solid,
                    target_anchor=anchor,
                    target_offset=self.calibration_target_offset,
                    tool_solid=tool_solid,
                    tool_anchor=self.calibration_tool_anchor,
                    tool_offset=self.calibration_tool_offset,
                )
