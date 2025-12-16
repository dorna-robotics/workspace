# workspace/workspace.py
from jinja2 import Template
from pathlib import Path
import yaml
import numpy as np

from workspace.display import Display
from workspace.components import factory as comp_factory
from dorna2.pose import T_to_xyzabc


class Workspace:
    def __init__(self, config_path="config/config.yaml"):
        #comp_cfgs = yaml.safe_load(Path(config_path).read_text())
        text = Path(config_path).read_text()

        # If extension is .j2, or if Jinja syntax appears inside:
        if config_path.endswith(".j2") or "{%" in text or "{{" in text:
            text = Template(text).render()

        comp_cfgs = yaml.safe_load(text)
        # if "core" not in comp_cfgs:
        #     raise ValueError("config must include a top-level 'core' component.")

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