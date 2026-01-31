# workspace/workspace.py
from jinja2 import Template
from pathlib import Path
import yaml
import numpy as np

from workspace.display import Display
from workspace.components import factory as comp_factory
from dorna2.pose import T_to_xyzabc, xyzabc_to_T, inv_T

class Workspace:
    def __init__(self, config_path="config/config.yaml"):

        # --- normalize to list ---
        if isinstance(config_path, (str, Path)):
            paths = [config_path]
        elif isinstance(config_path, (list, tuple)):
            paths = config_path
        else:
            raise TypeError("config_path must be a str, Path, or list of them")

        comp_cfgs = {}

        # --- load + merge configs in order ---
        for path in paths:
            text = Path(path).read_text()

            if str(path).endswith(".j2") or "{%" in text or "{{" in text:
                text = Template(text).render()

            cfg = yaml.safe_load(text) or {}
            comp_cfgs.update(cfg)   # later files override earlier ones


        # 1) build components
        self.components = {}
        for name, ccfg in comp_cfgs.items():
            self.components[name] = comp_factory.create_component(name, ccfg, self)

        # 2) perform attachments (child-side offset)
        for child_name, ccfg in comp_cfgs.items():
            att = ccfg.get("attach")
            if not att:
                continue
            parent_comp = self.components[att["parent_name"]]
            child_comp  = self.components[child_name]
            parent_solid = parent_comp.assembly[att["parent_solid"]]
            child_solid  = child_comp.assembly[att["child_solid"]]
            child_solid.attach_to(
                parent=parent_solid,
                parent_anchor=att["parent_anchor"],
                child_anchor=att["child_anchor"],
                offset=att.get("offset", [0, 0, 0, 0, 0, 0]),
            )

        # 3) start Display (it will pull poses from compute_world_poses())
        self.display = Display(self)
        self.display.start()


    def compute_collision_boxes(self, padding=0.0):
        """
        Returns two lists:
        1) collision_world: boxes in WORLD frame for everything not downstream of robot_flange
        2) collision_flange: boxes in FLANGE frame for anything downstream of robot_flange
        """
        collision_world = []
        collision_flange = []

        # ======================================================================
        # PHASE 1: FULL KINEMATIC UPDATE (same as before): compute solid._world_T
        # ======================================================================
        for comp in self.components.values():
            if hasattr(comp, "update_pose"):
                comp.update_pose()

        roots = []
        seen = set()
        for comp in self.components.values():
            for solid in comp.assembly.values():
                if solid.parent["parent_solid"] is None and id(solid) not in seen:
                    seen.add(id(solid))
                    roots.append(solid)

        stack = []
        for root in roots:
            stack.append((root, np.eye(4)))

        while stack:
            node, T_parent = stack.pop()
            T_world = T_parent @ node.local["T"]
            node._world_T = T_world
            for child_list in node.children.values():
                for entry in child_list:
                    stack.append((entry["child_solid"], T_world))

        # ======================================================================
        # Helpers for flange filtering / relative poses
        # ======================================================================
        def _parent_solid(s):
            if s is None:
                return None
            if hasattr(s, "parent") and isinstance(s.parent, dict):
                return s.parent.get("parent_solid", None)
            return None

        core_comp = self.components.get("core", None)
        robot_flange = None
        if core_comp is not None and hasattr(core_comp, "assembly") and isinstance(core_comp.assembly, dict):
            # Expecting the solid name used in your debug output: "robot_flange"
            robot_flange = core_comp.assembly.get("robot_flange", None)

        T_flange_world = getattr(robot_flange, "_world_T", None) if robot_flange is not None else None
        T_world_flange = inv_T(T_flange_world) if T_flange_world is not None else None

        def _is_downstream_of_flange(solid, max_hops=200):
            """
            True iff walking parents reaches robot_flange.
            NOTE: starting from parent means the flange itself is NOT counted as downstream.
            """
            if robot_flange is None:
                return False

            cur = _parent_solid(solid)
            seen_ids = set()
            for _ in range(max_hops):
                if cur is None:
                    return False
                if cur is robot_flange:
                    return True
                cid = id(cur)
                if cid in seen_ids:
                    return False
                seen_ids.add(cid)
                cur = _parent_solid(cur)
            return False

        def _is_descendant_of(solid, ancestor, max_hops=200):
            if solid is None or ancestor is None:
                return False
            cur = _parent_solid(solid)
            seen_ids = set()
            for _ in range(max_hops):
                if cur is None:
                    return False
                if cur is ancestor:
                    return True
                cid = id(cur)
                if cid in seen_ids:
                    return False
                seen_ids.add(cid)
                cur = _parent_solid(cur)
            return False

        def pad(scale):
            return [scale[0] + padding*2.0, scale[1] + padding*2.0, scale[2] + padding*2.0]
        
        # ======================================================================
        # Find tool + load (used for box_for_grip filtering)
        # ======================================================================
        def _tool_attached_to_robot():
            if core_comp is None:
                return None
            if getattr(core_comp, "has_tool_changer", False) and hasattr(core_comp, "tool_changer_robot_side"):
                children = core_comp.tool_changer_robot_side.children.get("tool_changer_connection", [])
            else:
                robot_flange_local = getattr(core_comp, "robot_flange", None)
                children = robot_flange_local.children.get("output", []) if robot_flange_local is not None else []

            for child in children:
                solid = child.get("child_solid")
                if solid is None:
                    continue
                return self.components.get(solid.component)
            return None

        def _solid_attached_to_tool(tool):
            if tool is None or not hasattr(tool, "assembly") or not tool.assembly:
                return None
            tool_root = tool.assembly[next(iter(tool.assembly))]
            for child in tool_root.children.get("tcp", []):
                return child.get("child_solid")
            return None

        tool_comp = _tool_attached_to_robot()
        tool_load_solid = _solid_attached_to_tool(tool_comp)

        # ======================================================================
        # PHASE 2: EXTRACT COLLISION DATA (from solids)
        # ======================================================================
        def _solid_boxes(solid, solid_name):
            c_box_data = getattr(solid, "collision_box", None)
            if not c_box_data:
                return []
            if isinstance(c_box_data, dict):
                if solid_name in c_box_data:
                    return c_box_data.get(solid_name) or []
                if "boxes" in c_box_data:
                    return c_box_data.get("boxes") or []
                if len(c_box_data) == 1:
                    return next(iter(c_box_data.values())) or []
                return []
            return c_box_data

        for comp in self.components.values():
            comp_name = getattr(comp, "name", None)
            for solid_name, solid in comp.assembly.items():
                safe_solid_name = str(solid_name) if solid_name else ""
                if safe_solid_name.lower().startswith("robot_"):
                    continue

                boxes = _solid_boxes(solid, solid_name)
                if not boxes:
                    continue

                T_solid_world = getattr(solid, "_world_T", None)
                if T_solid_world is None:
                    continue

                downstream = _is_downstream_of_flange(solid)

                if downstream and tool_load_solid is not None:
                    if _is_descendant_of(solid, tool_load_solid):
                        continue

                for box in boxes:
                    T_box_local = xyzabc_to_T(box["pose"])
                    T_box_world = T_solid_world @ T_box_local

                    if downstream and getattr(solid, "box_for_grip", False):
                        if tool_load_solid is None or solid is not tool_load_solid:
                            continue

                    if (not downstream) and getattr(solid, "box_for_grip", False):
                        continue

                    if downstream and (T_world_flange is not None):
                        # Pose in flange frame: T_flange^-1 * T_box_world
                        T_box_flange = T_world_flange @ T_box_world
                        pose_out = T_to_xyzabc(T_box_flange)
                        collision_flange.append({
                            "pose": pose_out,
                            "scale": pad(box["scale"]),
                            "componentName": comp_name,
                            "solidName": solid_name,
                            "frame": "flange",
                        })
                    else:
                        # Pose in world frame (old behavior)
                        pose_out = T_to_xyzabc(T_box_world)
                        entry = {
                            "pose": pose_out,
                            "scale": pad(box["scale"]),
                            "componentName": comp_name,
                            "solidName": solid_name,
                            "frame": "world",
                        }
                        if isinstance(box.get("pose"), (list, tuple)):
                            entry["poseLocal"] = list(box["pose"])
                        collision_world.append(entry)

        return collision_world, collision_flange

    # ---------- pose calculation (the only thing Display needs) ----------
    def compute_world_poses(self):
        """
        Returns a dict mapping "component_solid" -> [x,y,z,a,b,c] in WORLD frame.

        Fast: single DFS over the pose graph using cached local["T"] matrices.
        Always calls core.update_pose() first (if present) to refresh joint locals.

        Optimization: per-solid flags and cached world transforms so that we only
        recompute poses for solids whose parents (or themselves) have moved.
        """

        # ----------------------------------------------------------------------
        # 0) Ensure runtime fields on all solids; clear flags for this pass
        # ----------------------------------------------------------------------
        for comp in self.components.values():
            for solid in comp.assembly.values():
                if not hasattr(solid, "_pose_flag"):
                    solid._pose_flag = False
                if not hasattr(solid, "_world_T"):
                    solid._world_T = None
                # For this pass, flags start cleared; we will re-set as needed
                solid._pose_flag = False

        # ----------------------------------------------------------------------
        # 1) Update pose of all driving components (robot, rail, etc.)
        #    and flag ALL their solids.
        # ----------------------------------------------------------------------
        for comp in self.components.values():
            if hasattr(comp, "update_pose"):
                comp.update_pose()
                # Mark all solids of this component as "changed"
                for solid in comp.assembly.values():
                    solid._pose_flag = True

        # ----------------------------------------------------------------------
        # 2) Find all roots (same logic as your original code)
        # ----------------------------------------------------------------------
        roots = []
        seen = set()
        for comp in self.components.values():
            for solid in comp.assembly.values():
                if solid.parent["parent_solid"] is None and id(solid) not in seen:
                    seen.add(id(solid))
                    roots.append(solid)

        # ----------------------------------------------------------------------
        # 3) DFS to compute / reuse world transforms.
        #
        # Stack entries: (node_solid, T_parent, parent_updated_flag)
        # - parent_updated_flag is True if the parent was recomputed this pass.
        # ----------------------------------------------------------------------
        stack = []

        for root in roots:
            node_flag = bool(getattr(root, "_pose_flag", False))
            has_cached = getattr(root, "_world_T", None) is not None

            # For roots, we must recompute if:
            #   - root was flagged (its component updated), OR
            #   - it has no cached world transform yet.
            parent_updated = node_flag or (not has_cached)

            stack.append((root, np.eye(4), parent_updated))

        while stack:
            node, T_parent, parent_updated = stack.pop()

            node_flag = bool(getattr(node, "_pose_flag", False))
            has_cached = getattr(node, "_world_T", None) is not None

            # Decide whether to recompute this node's world pose.
            # Your rule:
            #   - recompute if parent has flag
            # Extended to be robust:
            #   - OR node itself is flagged
            #   - OR no cached world transform yet (first call / new solid)
            recompute = parent_updated or node_flag or (not has_cached)

            if recompute:
                T_world = T_parent @ node.local["T"]
                node._world_T = T_world
                node._pose_flag = True   # we updated this solid in this pass
            else:
                # Reuse cached world pose; solid did not move this pass
                T_world = node._world_T
                node._pose_flag = False  # not a source of updates for children

            # Push children; they recompute only if THIS node was updated
            for child_list in node.children.values():
                for entry in child_list:
                    child_solid = entry["child_solid"]
                    stack.append((child_solid, T_world, node._pose_flag))
        # ----------------------------------------------------------------------
        # 4) Build name->pose dict (same as your original behavior)
        # ----------------------------------------------------------------------
        poses = {}
        for comp_name, comp in self.components.items():
            for solid_name, solid in comp.assembly.items():
                key = f"{comp_name}_{solid_name}"
                T = getattr(solid, "_world_T", None)

                # Same fallback as your original code for orphans:
                if T is None:
                    T = solid.local["T"]

                poses[key] = T_to_xyzabc(T)

        return poses


    def stop(self):
        """Cleanly stop background threads and close any resources."""
        # stop display loop (if it was started)
        try:
            self.display.stop()
        except Exception:
            pass

        # give each component a chance to cleanup 
        for comp in self.components.values():
            if hasattr(comp, "stop"):
                try:
                    comp.stop()
                except Exception:
                    pass
            if hasattr(comp, "close"):
                try:
                    comp.close()
                except Exception:
                    pass
