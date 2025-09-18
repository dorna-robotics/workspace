# workspace/workspace.py
from pathlib import Path
import yaml
import numpy as np

from workspace.display import Display
from workspace.components import factory as comp_factory
from dorna2.pose import T_to_xyzabc


class Workspace:
    def __init__(self, config_path="config/config.yaml"):
        comp_cfgs = yaml.safe_load(Path(config_path).read_text())
        if "core" not in comp_cfgs:
            raise ValueError("config must include a top-level 'core' component.")

        # 1) build components
        self.components = {}
        for name, ccfg in comp_cfgs.items():
            self.components[name] = comp_factory.create_component(name, ccfg)

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
        """
        # first update the pose of all driving components including core (robot and rail)
        for comp in self.components.values():
            if hasattr(comp, "update_pose"):
                comp.update_pose()

        # find all roots
        roots = []
        seen = set()
        for comp in self.components.values():
            for solid in comp.assembly.values():
                if solid.parent is None and id(solid) not in seen:
                    seen.add(id(solid))
                    roots.append(solid)

        # DFS to compute world transforms
        world_T = {}
        stack = [(root, np.eye(4)) for root in roots]
        while stack:
            node, T_parent = stack.pop()
            T_world = T_parent @ node.local["T"]
            world_T[id(node)] = T_world
            for child in node.children:
                stack.append((child, T_world))

        # build name->pose dict
        poses = {}
        for comp_name, comp in self.components.items():
            for solid_name, solid in comp.assembly.items():
                key = f"{comp_name}_{solid_name}"
                T = world_T.get(id(solid), solid.local["T"])  # fallback if orphan
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
