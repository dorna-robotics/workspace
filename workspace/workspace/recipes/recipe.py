# workspace/recipes/recipe.py

from copy import deepcopy
from mergedeep import merge
from collections import deque
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
        calibration_targets={},  # {solid_name: {anchor_1:..., anchor_2:...},...}
        calibration_target_offset=[0, 0, 8, 0, 0, 0],
        calibration_tool_solid_name="body",
        calibration_tool_anchor="tcp",
        calibration_tool_offset=[0, 0, 0, 0, 0, 0],
    )

    def __init__(self, workspace, core, component, **kwargs):
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

    # ── Solid / tool queries ────────────────────────────────────────────────

    def tool_attached_to_the_robot(self):
        tool = None
        if self.core.has_tool_changer:
            for child in self.core.tool_changer_robot_side.children["tool_changer_connection"]:
                solid = child["child_solid"]
                tool = self.workspace.components[solid.component]
                continue
        else:
            for child in self.core.robot_flange.children["output"]:
                solid = child["child_solid"]
                tool = self.workspace.components[solid.component]
                continue
        return tool

    def solid_attached_to_tool(self, tool):
        for child in tool.assembly[next(iter(tool.assembly))].children["tcp"]:
            return child["child_solid"]
        return None

    def solid_attached_to_anchor(self, solid, anchor):
        try:
            for child in solid.children[anchor]:
                return child["child_solid"]
        except Exception:
            pass
        return None

    def solid_with_anchor(self, initial_solid, anchor):
        queue = deque([initial_solid])
        visited = set()
        while queue:
            solid = queue.popleft()
            if solid in visited:
                continue
            visited.add(solid)
            if anchor in solid.anchors:
                return solid
            for links in solid.children.values():
                for link in links:
                    queue.append(link["child_solid"])
        return None

    def solid_hierarchy(self, parent_solid, parent_anchor, connection_anchor="place"):
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

    def _move_along_path(self, rt, path, target_solid, target_anchor, tool_dict, j5_override, vaj_map, has_motion_plan=False, first_approach=False):
        """Execute a sequence of IK-solved motions along path offsets.

        has_motion_plan / first_approach: only the first step of an approach
        may use path planning.
        """
        for i in range(len(path)):
            # calibration correction
            if self.calibration:
                path[i] = self._calibrate_offset(target_solid, target_anchor, path[i])

            # IK
            J, C = self.core.IK(
                target_solid=target_solid,
                target_anchor=target_anchor,
                target_offset=path[i],
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
                raise RecipeError("could not find a valid pose to approach")

            rt.checkpoint()
            # first approach step may use path planning
            if i == 0 and first_approach:
                if has_motion_plan:
                    points = self.core.motion_plan(joint=J)
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
        """Build output_approach, output_touch, output_exit lists for pick or place.

        Returns (output_approach, output_touch, output_exit) for pick semantics.
        Caller swaps approach/touch order for place semantics.
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

    def _get_tool_and_load_height(self):
        """Get the current tool, load_list attached to it, and height_load.

        Returns (tool, load_list, height_load).
        """
        tool = self.tool_attached_to_the_robot()
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
        **kwargs,
    ):
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
        for k, v in kwargs.items():
            if hasattr(self, k):
                setattr(self, k, v)

        component = component or self.component

        if self.ref_joints is None:
            raise RecipeError("no reference joints defined")

        tool = self.tool_attached_to_the_robot()
        if tool is None:
            raise RecipeError("no tool attached to the robot")

        # hierarchy of items attached to the anchor
        height_load = 0
        pose_offset = dorna_pose.Pose(pose=[0, 0, 0, 0, 0, 0])
        load_list = self.solid_hierarchy(
            parent_solid=component.assembly[solid_name], parent_anchor=anchor, connection_anchor="place"
        )
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

        # height container
        height_container = abs(
            dorna_pose.transform_pose(
                [0, 0, 0, 0, 0, 0],
                from_frame=component.assembly[solid_name].pose("top"),
                to_frame=component.assembly[solid_name].pose("place"),
            )[2]
        )

        # height tool
        tool_body = tool.assembly[next(iter(tool.assembly))]
        height_tool = abs(
            dorna_pose.transform_pose(
                [0, 0, tool_tip_z_offset - tool_tcp_z_offset, 0, 0, 0],
                from_frame=tool_body.pose("tip"),
                to_frame=tool_body.pose("tcp"),
            )[2]
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
        return self.touch(**pick_prm)

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
        for k, v in kwargs.items():
            if hasattr(self, k):
                setattr(self, k, v)

        component = component or self.component

        if self.ref_joints is None:
            raise RecipeError("no reference joints defined")

        tool = self.tool_attached_to_the_robot()
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
        return self.touch(**place_prm)

    # ── High-level motions ──────────────────────────────────────────────────

    def above(self, anchor, solid_name="body", component=None, padding=50, tool_tcp_z_offset=0, tool_tip_z_offset=0, **kwargs):
        pick_prm = self.pick_setting(
            anchor, solid_name,
            component=component, actions=[], exit=False,
            attachment=False, trigger_io=False, padding=padding,
            tool_tcp_z_offset=tool_tcp_z_offset, tool_tip_z_offset=tool_tip_z_offset,
            **kwargs,
        )
        if not pick_prm:
            raise RecipeError("above failed — could not compute pick parameters")
        pick_prm["target_offset"] = None
        pick_prm["approach_path"] = pick_prm["approach_path"][0:1]
        return self.touch(**pick_prm, **kwargs)

    def rotate(self, rotation=90, joint="j5", limit=[-175, 175], vaj=[500, 3000, 15000], **kwargs):
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

    def immerse(self, dist=0, anchor="place", solid_name="body", component=None, exit=False, attachment=False, trigger_io=False, padding=10, **kwargs):
        _tool, _load_list, height_load = self._get_tool_and_load_height()

        tool_tcp_z_offset = height_load - dist
        tool_tip_z_offset = height_load - dist
        if self.above(anchor=anchor, solid_name=solid_name, component=component, padding=padding, tool_tcp_z_offset=height_load, tool_tip_z_offset=height_load, **kwargs):
            return self.pick(anchor=anchor, solid_name=solid_name, component=component, approach=False, exit=exit, attachment=attachment, trigger_io=trigger_io, padding=padding, tool_tcp_z_offset=tool_tcp_z_offset, tool_tip_z_offset=tool_tip_z_offset, **kwargs)
        raise RecipeError("immerse failed — could not move above target")

    def retract(self, dist=0, anchor="place", solid_name="body", component=None, padding=0, has_motion_plan=False, **kwargs):
        _tool, _load_list, height_load = self._get_tool_and_load_height()

        tool_tcp_z_offset = height_load + dist
        tool_tip_z_offset = height_load + dist
        return self.above(anchor=anchor, solid_name=solid_name, component=component, padding=padding, tool_tcp_z_offset=tool_tcp_z_offset, tool_tip_z_offset=tool_tip_z_offset, has_motion_plan=has_motion_plan, **kwargs)

    # ── Calibration ─────────────────────────────────────────────────────────

    def calibrate_anchor(self, target_solid, target_anchor, target_offset, tool_solid, tool_anchor, tool_offset):
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
        tool = self.tool_attached_to_the_robot()
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
