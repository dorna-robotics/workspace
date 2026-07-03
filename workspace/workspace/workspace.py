# workspace/workspace.py
import threading
from jinja2 import Template
from pathlib import Path
import yaml
import numpy as np

from workspace.display import Display
from workspace.components import factory as comp_factory
from workspace.runtime import Runtime
from workspace.devices import MQTTOrchestrator, component_device_ids
from workspace.devices.component_contract import component_device_claim
from dorna2.pose import T_to_xyzabc, xyzabc_to_T, inv_T


class Workspace:
    def __init__(self, config_path="config/config.yaml", port: int = 8000):

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
            comp_cfgs.update(cfg)  # later files override earlier ones

        # Scene-mutation lock — protects ``self.components`` and any
        # kinematic-tree edits from runtime add/remove calls. Re-entrant
        # so the BT thread can hold it briefly across nested ops.
        self._scene_lock = threading.RLock()

        # Active BT context — registered by ``workspace.bt.launcher`` at
        # run start, cleared on tear-down. ``add_fact`` / ``remove_fact``
        # operate on its ``state["facts"]`` set so observations propagate
        # to the planner and BT in one place.
        self._active_ctx = None

        # Names of runtime-added components declared ``transient=True`` in
        # ``add_component``. Transient components are per-run scene objects
        # (a spawned disc, a virtual sample): ``reset_scene`` — which the
        # launcher runs before every workflow start — REMOVES any still
        # present, so a run killed between a physical effect and its
        # compensating ``remove_component`` can never leak objects into the
        # next run. Explicit opt-in only: the framework never guesses which
        # components are transient (a stranded REAL object must stay in the
        # model, because it stays on the bench).
        self._transient_names: set = set()

        # 1) build components
        self.components = {}
        for name, ccfg in comp_cfgs.items():
            self.components[name] = comp_factory.create_component(name, ccfg, self)

        # 1.5) runtime controller (pause/stop/resume/start)
        core = self.components.get("core")
        if core is None:
            raise RuntimeError("Workspace requires a 'core' component for Runtime")
        self.rt = Runtime(core)

        # 1.6) device health bus (MQTT). Best-effort: a missing/unreachable
        # broker must not block startup — devices will appear once it's up.
        # ``claim_resolver`` walks components live so a runtime
        # ``core.simulation(True)`` toggle takes effect on the next bus
        # event without explicit refresh. Strictest-claim-wins: any
        # ``"real"`` claim for a given id beats any ``"sim"`` claim.
        def _claim_resolver(device_id: str) -> str:
            comps = (self.components or {}).values()
            net = None
            for comp in comps:
                if device_id not in component_device_ids(comp):
                    continue
                claim = component_device_claim(comp, device_id) or "real"
                if claim == "real":
                    return "real"
                net = claim
            return net or "real"

        try:
            self.devices = MQTTOrchestrator(
                runtime=self.rt,
                claim_resolver=_claim_resolver,
            )
        except Exception as ex:
            import logging
            logging.getLogger(__name__).warning(
                "MQTTOrchestrator unavailable: %s. Device monitoring disabled.", ex,
            )
            self.devices = None

        # 2) perform attachments (child-side offset). Snapshot the
        # config-declared list so we can re-apply it later via
        # ``reset_scene()`` — used between workflow runs to put every
        # tube / cap / tool back in its launch-time position.
        self._initial_attachments = [
            (child_name, ccfg["attach"])
            for child_name, ccfg in comp_cfgs.items()
            if ccfg.get("attach")
        ]
        self._apply_initial_attachments()

        # 3) start Display (it will pull poses from compute_world_poses())
        self.port = int(port)
        self.display = Display(self, port=self.port)
        self.display.start()


    def _apply_initial_attachments(self):
        """Apply every attach: clause from the loaded scene config.

        The attach graph (Solid.parent / Solid.children) IS the world
        state — recipes read it to decide what's where. So re-running
        this method fully restores the scene to its launch-time layout.
        Safe to call repeatedly: ``Solid.attach_to`` detaches from any
        current parent before wiring the new one.
        """
        for child_name, att in self._initial_attachments:
            self._apply_one_attachment(child_name, att)

    def _apply_one_attachment(self, child_name: str, att: dict) -> bool:
        """Apply a single ``attach:`` clause. Returns True on success,
        False if either side of the attachment isn't resolvable yet
        (silent skip mirrors the original loop's behaviour). Factored
        out of ``_apply_initial_attachments`` so ``add_component`` can
        reuse it without duplicating the resolution logic.
        """
        parent_comp = self.components.get(att["parent_name"])
        child_comp = self.components.get(child_name)
        if parent_comp is None or child_comp is None:
            return False
        try:
            parent_solid = parent_comp.assembly[att["parent_solid"]]
            child_solid = child_comp.assembly[att["child_solid"]]
        except KeyError:
            return False
        child_solid.attach_to(
            parent=parent_solid,
            parent_anchor=att["parent_anchor"],
            child_anchor=att["child_anchor"],
            offset=att.get("offset", [0, 0, 0, 0, 0, 0]),
        )
        return True

    @staticmethod
    def _detach_solid(solid) -> None:
        """Remove ``solid`` from its current parent's children list and
        reset its parent record. ``dorna2.Solid`` doesn't ship a
        ``detach()`` method, so we walk the structure directly.
        Idempotent: safe to call on an already-orphaned solid.
        """
        parent_solid = solid.parent.get("parent_solid")
        parent_anchor = solid.parent.get("parent_anchor")
        if parent_solid is not None and parent_anchor is not None:
            children_at_anchor = parent_solid.children.get(parent_anchor, [])
            parent_solid.children[parent_anchor] = [
                c for c in children_at_anchor if c.get("child_solid") is not solid
            ]
        solid.parent = {
            "parent_solid": None,
            "parent_anchor": None,
            "child_anchor": None,
            "offset": None,
        }

    # ── Runtime scene mutation ───────────────────────────────────────────
    # Scene topology and PDDL state are explicitly separate — see
    # ``docs/component-guide.md`` (scene-side) and
    # ``docs/bt-framework-guide.md`` (state-side). A caller that
    # mutates one is responsible for mutating the other. The framework
    # never infers.

    def add_component(self, name: str, cfg: dict, transient: bool = False):
        """Add a component to the workspace at runtime.

        ``cfg`` is the same dict shape as a scene yaml entry — must
        include ``type`` (the registered component type) and any
        component-specific config. If ``cfg["attach"]`` is present, the
        kinematic attachment is applied immediately.

        ``transient=True`` declares the component a per-run scene object
        (a spawned disc, a virtual sample). ``reset_scene`` — run by the
        launcher before every workflow start — removes any transient
        component still present, so a run that died between creating the
        object and its balancing ``remove_component`` can never leak it
        into the next run. Leave False (default) for anything that models
        a persistent physical object.

        Refuses during a run for:
          * device-backed components (MQTT lifecycle isn't safe mid-run)
        Always refuses:
          * a name already in ``self.components``

        Returns the new component instance. The caller is responsible
        for adding any PDDL facts the change implies (see ``add_fact``).
        """
        if not isinstance(cfg, dict):
            raise TypeError("cfg must be a dict (same shape as a scene yaml entry)")
        if "type" not in cfg:
            raise ValueError(f"component '{name}' cfg missing 'type'")
        with self._scene_lock:
            if name in self.components:
                raise ValueError(f"component '{name}' already exists")
            comp = comp_factory.create_component(name, cfg, self)
            # Device-backed components carry MQTT subscriptions whose
            # lifecycle isn't safe to start mid-run. Reject here rather
            # than half-attach.
            if component_device_ids(comp) and self._is_running():
                raise RuntimeError(
                    f"cannot add device-backed component '{name}' during a run; "
                    "launch the workspace with it from the start"
                )
            self.components[name] = comp
            if transient:
                self._transient_names.add(name)
            if cfg.get("attach"):
                self._initial_attachments.append((name, cfg["attach"]))
                self._apply_one_attachment(name, cfg["attach"])
        self._notify_scene_changed()
        return comp

    def remove_component(self, name: str):
        """Remove a component from the workspace at runtime.

        Detaches every solid in the component's assembly from its
        current parent and drops the component from ``self.components``.

        Refuses for:
          * ``core`` (runtime-critical)
          * a tool currently mounted on the robot flange (would yank
            the kinematic chain out from under live motion)
          * device-backed components during a run (MQTT lifecycle)
          * a missing name

        Returns nothing. Caller updates PDDL facts to match (see
        ``remove_fact``).
        """
        with self._scene_lock:
            if name not in self.components:
                raise KeyError(f"component '{name}' does not exist")
            if name == "core":
                raise ValueError("'core' cannot be removed at runtime")
            comp = self.components[name]
            if self._is_running() and component_device_ids(comp):
                raise RuntimeError(
                    f"cannot remove device-backed component '{name}' during a run"
                )
            if self._is_mounted_on_robot(comp):
                raise ValueError(
                    f"'{name}' is currently mounted on the robot — detach first"
                )
            # Detach every solid from its parent. Drops it cleanly out of
            # the kinematic tree so subsequent pose computations skip it.
            assembly = getattr(comp, "assembly", {}) or {}
            for solid in assembly.values():
                self._detach_solid(solid)
            # Drop attachment record so ``reset_scene`` doesn't try to
            # re-attach a now-missing component.
            self._initial_attachments = [
                (n, a) for n, a in self._initial_attachments if n != name
            ]
            self._transient_names.discard(name)
            del self.components[name]
        self._notify_scene_changed()

    def _is_running(self) -> bool:
        """True if the runtime is actively executing a workflow.
        PAUSED / IDLE / ERROR are all 'not running'.
        """
        rt = getattr(self, "rt", None)
        state = (getattr(rt, "state", "") or "").upper() if rt is not None else ""
        return state in ("RUNNING", "ACTIVE")

    def _is_mounted_on_robot(self, comp) -> bool:
        """Whether ``comp`` is the tool currently mounted on Core's
        flange. Uses ``core.current_tool()`` — the single source of
        truth for what's on the robot right now.
        """
        core = self.components.get("core")
        if core is None:
            return False
        getter = getattr(core, "current_tool", None)
        if not callable(getter):
            return False
        try:
            current = getter()
        except Exception:
            return False
        return current is comp

    def _notify_scene_changed(self) -> None:
        """Fan out a 'scene changed' signal:
          * 3D viewer snapshot (Display)
          * /ws/devices broadcast of fresh snapshot
          * /ws/operator_actions broadcast of fresh snapshot
        Best-effort: a missing display / WS broker doesn't block the
        mutation itself.
        """
        # Display — Display.send_snapshot is thread-safe.
        try:
            disp = getattr(self, "display", None)
            if disp is not None and hasattr(disp, "send_snapshot"):
                disp.send_snapshot()
        except Exception:
            pass
        # WS fan-out — imported lazily to avoid a workspace ↔
        # runtime_server circular import at module load.
        try:
            from workspace.runtime_server import _broadcast_scene_changed
            _broadcast_scene_changed(self)
        except Exception:
            pass

    # ── Runtime PDDL state mutation ──────────────────────────────────────
    # See ``docs/bt-framework-guide.md`` for the rule that scene and
    # state are separate concerns; mutating one never auto-mutates the
    # other.

    def set_active_ctx(self, ctx) -> None:
        """Register the BT ``WorkspaceContext`` so ``add_fact`` /
        ``remove_fact`` can reach its ``state["facts"]`` set. Called by
        ``workspace.bt.launcher`` at run start; ``clear_active_ctx`` at
        teardown.
        """
        self._active_ctx = ctx

    def clear_active_ctx(self) -> None:
        self._active_ctx = None

    def add_fact(self, *args) -> None:
        """Add a PDDL fact to the active run's state.

        Args form the predicate tuple, e.g.
        ``workspace.add_fact("capped", "tube_5")``.

        Idempotent — adding a fact already present is a no-op. Raises
        if there is no active run (facts only exist inside a run).
        """
        if self._active_ctx is None:
            raise RuntimeError("no active run; facts can only be set during a run")
        if not args:
            raise ValueError("add_fact requires a non-empty predicate tuple")
        facts = self._active_ctx.state.setdefault("facts", set())
        facts.add(tuple(args))

    def remove_fact(self, *args) -> None:
        """Remove a PDDL fact from the active run's state.

        Silent no-op if the fact isn't present. Raises if there is no
        active run.
        """
        if self._active_ctx is None:
            raise RuntimeError("no active run; facts can only be cleared during a run")
        if not args:
            raise ValueError("remove_fact requires a non-empty predicate tuple")
        facts = self._active_ctx.state.get("facts")
        if facts is None:
            return
        facts.discard(tuple(args))

    def facts(self) -> set:
        """Snapshot of current PDDL facts (a plain set of tuples). Empty
        set when there is no active run, so the caller can use it in
        ``len(workspace.facts()) > 0``-style checks without guarding.
        """
        if self._active_ctx is None:
            return set()
        return set(self._active_ctx.state.get("facts") or set())

    def reset_scene(self):
        """Reset the scene to its launch-time layout.

        First REMOVES every component added with ``transient=True`` that
        is still present — a run killed between a physical effect and its
        balancing ``remove_component`` strands such objects, and they must
        not leak into the next run. Then re-runs every ``attach:`` clause
        from the loaded config, which snaps every tube, cap, and tool back
        to where the .j2 scene files put it at startup. Intended for
        between-run resets so the operator can click Start again on a
        workspace that has already finished one workflow — without needing
        to Kill+Launch first.
        """
        with self._scene_lock:
            stranded = [n for n in self._transient_names if n in self.components]
        for name in stranded:
            try:
                self.remove_component(name)
                print(f"reset_scene: swept stranded transient '{name}'")
            except Exception:
                logging.getLogger(__name__).exception(
                    "reset_scene: could not sweep transient %r", name
                )
        self._transient_names.clear()
        self._apply_initial_attachments()

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
        # stop any running job first
        try:
            if hasattr(self, "rt") and self.rt is not None:
                self.rt.stop()
        except Exception:
            pass

        # close device bus
        try:
            if getattr(self, "devices", None) is not None:
                self.devices.close()
        except Exception:
            pass

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
