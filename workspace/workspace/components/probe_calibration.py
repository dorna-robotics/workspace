"""Probe-tool calibration — recipe-driven.

Driven by `Recipe.calibrate()`: `ProbeCalibration(recipe)` reads
core / component, the IK params, the frozen `ref_joints` and the
`calibration_name` off the recipe — nothing is passed or re-derived
separately.

The tool->pocket orientation is intrinsic to the mounted probe tool: it
lives in the probe component's `tcp` anchor (its abc encodes how the rod
meets the pocket), so IK solves straight to tcp and a new probe geometry
needs no edit here.

For each `clb_*` anchor on the component it probes three fixed pockets:
`place1` / `place2` (the two side pockets) give a rough XY plus Z and the
floor tilt (ABC); `place3` (the small centre pocket) gives the precise XY.
The three are fused into one measured pose, mapped back to the clb anchor
(the jig sits flat on it; the pocket floor is 5 mm above), and stored as a
single raw/corrected calibration point under the recipe's
`calibration_name`. Production's `interpolate()` corrects pick/place poses
from those points.

Motion split:
  * gross travel above each clb anchor is the recipe's job (`recipe.above`).
  * in-pocket fine motions + probing + the math live here.
Every IK uses the recipe's frozen `ref_joints` and IK params, so the wrist
branch we calibrate is the one production reaches at pick/place time.

Simulation is owned by the robot controller and the probe-tool component
(`Probe.probe`); the only sim awareness here is reading
`core._simulation_mode` to pick the animated (no-IO) path.
"""

import math
import time

import numpy as np

import dorna2.pose as dorna_pose


class ProbeCalibration:
    # ── pocket geometry ────────────────────────────────────────────────
    POCKET_DIAMETER        = 20.0   # mm
    APPROACH_CLEARANCE     = 50.0   # mm above pocket
    GROSS_PADDING          = 50.0   # mm above the part's top for the gross staging approach
    POCKET_ENTRY_CLEARANCE = 10.0   # mm above pocket opening for initial floor find
    WALL_PROBE_DEPTH       = 3.0    # mm above floor for wall probes
    NUM_FLOOR_PROBES       = 6      # floor touches around fitted XY centre
    BOTTOM_PROBE_RADIUS    = 7.5    # mm from pocket centre for floor probes

    # Fixed jig pockets, as constant offsets in the clb-anchor frame. The
    # jig sits flat on the anchor (zero gap), so the anchor is at z=0 and
    # each pocket floor is 5 mm above it. ±12.5 mm in X from the jig
    # geometry — edit here if the jig changes.
    POCKET_OFFSETS = {
        "place1": [ 12.5, 0.0, 5.0, 0, 0, 0],
        "place2": [-12.5, 0.0, 5.0, 0, 0, 0],
        "place3": [  0.0, 0.0, 5.0, 0, 0, 0],
    }

    # ── two-step probe ─────────────────────────────────────────────────
    # Step 1 finds the wall from far out; step 2 re-probes after a small
    # back-off so the trigger point isn't biased by step-1 overshoot.
    SEARCH_STEP_1      = 21.0   # mm — initial coarse search distance
    STEP_1_BACKOFF     = 3.0    # mm — back off after step-1 trigger
    SEARCH_STEP_2      = 5.0    # mm — second-pass search distance
    PROBE_VEL          = 2.0    # mm/s — same vel for both steps
    PROBE_RETRACT_DIST = 5.0    # mm between wall probes

    # ── inter-probe motion ─────────────────────────────────────────────
    # vel is chosen per move; accel/jerk come in two fixed profiles.
    VEL_FAST     = 200
    VEL_MEDIUM   = 50
    VEL_APPROACH = 5
    VEL_RETRACT  = 3                    # default for short relative retracts/backoffs
    MOVE_ACCEL, MOVE_JERK = 500, 3000   # normal point-to-point jmoves
    FINE_ACCEL, FINE_JERK = 50, 500     # short retract / backoff / probe contact

    # ── tool ───────────────────────────────────────────────────────────
    # IK solves to the probe's tcp; the tool->pocket orientation is baked
    # into that tcp anchor's abc on the probe component.
    TOOL_ANCHOR = "tcp"

    def __init__(self, recipe):
        # Everything comes off the recipe — no separate core / component /
        # IK args. The recipe already froze ref_joints once in
        # Recipe.__init__, and its calibration_name embeds the IK config.
        self.recipe            = recipe
        self.core              = recipe.core
        self.component         = recipe.component
        self.calibration_name  = recipe.calibration_name

        # IK params (single set, used for every IK in this run).
        self.base_distance = recipe.base_distance
        self.rail_step     = recipe.rail_step
        self.rail_span     = recipe.rail_span
        self.left_approach = recipe.left_approach
        self.ref_joints    = recipe.ref_joints
        # how high the rod parks above each pocket (recipe-configurable).
        self.approach_clearance = getattr(recipe, "approach_clearance", self.APPROACH_CLEARANCE)

    # ── helpers ───────────────────────────────────────────────────────
    @property
    def rt(self):
        return self.recipe.rt

    @property
    def _sim_mode(self):
        return bool(getattr(self.core, "_simulation_mode", False))

    def _tool(self):
        """The probe tool currently mounted (resolved via the recipe)."""
        return self.core.current_tool()
    
    def _body(self):
        return self.component.assembly["body"]

    def _part_clearance(self):
        """Vertical extent of the part (top anchor above its place plane).
        Mirrors Recipe.above()'s height_container so the gross staging
        approach clears the part. 0 if the part has no top/place anchors."""
        body = self._body()
        if "top" in body.anchors and "place" in body.anchors:
            return abs(dorna_pose.transform_pose(
                [0, 0, 0, 0, 0, 0],
                from_frame=body.pose("top"), to_frame=body.pose("place"),
            )[2])
        return 0.0

    def _gross_approach(self, clb_anchor):
        """Stage the rod clear of the part, above the clb anchor, ALREADY
        oriented for the probe tool. Replaces Recipe.above() for
        calibration: above() bakes in a tool-down orientation that is
        correct for the vertical rod but wrong for the sideways one —
        this uses the same tcp-anchor orientation as the probing motions,
        so gross approach and probe agree for every tool.

        The staging point is `_part_clearance + GROSS_PADDING` out along the
        anchor's approach axis (up for a top-facing part, sideways for a
        shelf), matching above()'s height_container + padding."""
        center_w = self._body().pose(anchor=clb_anchor)
        entry_dir = -np.array(dorna_pose.abc_to_rmat(center_w[3:6]))[:, 2]
        clearance = self._part_clearance() + self.GROSS_PADDING
        gross_pt = np.array(center_w[:3]) - clearance * entry_dir
        return self._jmove_to(gross_pt, clb_anchor, vel=self.VEL_FAST)

    def _frames(self, clb_anchor, pocket):
        """Return (center_w, place_w, entry_dir, wall_1, wall_2) for a
        pocket at the given clb anchor.

        center_w is the clb anchor's world pose (the reference for the
        rod-down axis); place_w is the pocket floor pose (anchor + the
        fixed POCKET_OFFSETS). entry_dir = -R[:,2] (rod descends down the
        anchor's local -Z); wall_1 / wall_2 = R[:,0] / R[:,1]. All axes
        derive from the anchor's pose, so a tilted workpiece gives tilted
        probe directions automatically."""
        body = self._body()
        center_w = body.pose(anchor=clb_anchor)
        place_w  = body.pose(anchor=clb_anchor, offset=self.POCKET_OFFSETS[pocket])
        R = np.array(dorna_pose.abc_to_rmat(center_w[3:6]))
        return center_w, place_w, -R[:, 2], R[:, 0], R[:, 1]

    # ── IK + jmoves ────────────────────────────────────────────────────
    def _ik_to_world(self, world_xyz, clb_anchor):
        """IK so the tool's TOOL_ANCHOR lands at `world_xyz` with the rod
        oriented into the pocket. Always seeds with the recipe's frozen
        ref_joints, so every motion (approach, descend, probe, backoff,
        retract) lands on the same wrist branch — matching production."""
        tool = self._tool()
        if tool is None:
            return None, 0
        tool_solid = tool.assembly[next(iter(tool.assembly))]

        body = self._body()
        center_w = body.pose(anchor=clb_anchor)
        R_c = np.array(dorna_pose.abc_to_rmat(center_w[3:6]))
        delta_local = R_c.T @ (np.array(world_xyz) - np.array(center_w[:3]))
        # Orientation is baked into the tool's tcp anchor (its abc encodes the
        # rod->pocket approach), so the target offset is position-only.
        offset = [float(delta_local[0]), float(delta_local[1]),
                  float(delta_local[2]), 0.0, 0.0, 0.0]

        return self.core.IK(
            target_solid  = body,
            target_anchor = clb_anchor,
            target_offset = offset,
            tool_solid    = tool_solid,
            tool_anchor   = self.TOOL_ANCHOR,
            tool_offset   = [0, 0, 0, 0, 0, 0],   # no J5 rotation, ever
            base_distance = self.base_distance,
            rail_step     = self.rail_step,
            rail_span     = self.rail_span,
            ref_joints    = self.ref_joints,
            left_approach = self.left_approach,
        )

    def _jmove_to(self, world_xyz, clb_anchor, vel=None):
        """IK + jmove to a world XYZ via the runtime (pause/stop aware)."""
        if vel is None:
            vel = self.VEL_APPROACH
        J, C = self._ik_to_world(world_xyz, clb_anchor)
        if C != 2 or J is None:
            print(f"    IK failed (C={C}) for jmove to {[round(v, 3) for v in world_xyz]}")
            return False
        self.rt.jmove(joint=J, vel=vel, accel=self.MOVE_ACCEL, jerk=self.MOVE_JERK)
        return True

    def _relative_move(self, world_delta, clb_anchor, vel=None):
        """Relative move by world_delta (3-vector). Reads the current TCP,
        IKs current+delta through our solver (recipe ref_joints + IK
        params), then jmoves to the resolved joints — never a controller
        cartesian rel-move, which would re-IK on the firmware with no
        knowledge of our seed and could pick a different wrist branch."""
        if vel is None:
            vel = self.VEL_RETRACT
        tool = self._tool()
        tool_solid = tool.assembly[next(iter(tool.assembly))]

        if not self._sim_mode:
            # dorna.halt() returns before the controller's reported joint()
            # catches up; settle before reading the TCP or update_pose()
            # IKs against a stale pose.
            time.sleep(0.1)

        self.core._last_joints = None
        self.core.update_pose()
        current = tool_solid.pose(anchor=self.TOOL_ANCHOR, offset=[0, 0, 0, 0, 0, 0])
        target = np.array([float(current[i]) + float(world_delta[i]) for i in range(3)])

        J, C = self._ik_to_world(target, clb_anchor)
        if C != 2 or J is None:
            print(f"    IK failed (C={C}) for relative move "
                  f"by {[round(float(v), 3) for v in world_delta]}")
            return False
        # the FINE profile keeps the short retract/backoff smooth.
        self.rt.jmove(joint=J, vel=vel, accel=self.FINE_ACCEL, jerk=self.FINE_JERK)
        return True

    def _tcp_world(self):
        """Tool TOOL_ANCHOR world pose from the live joints. After a real
        probe + halt + settle the live joints are the trigger joints, so
        this reads back the contact pose."""
        self.core._last_joints = None
        self.core.update_pose()
        tool = self._tool()
        tool_solid = tool.assembly[next(iter(tool.assembly))]
        return list(tool_solid.pose(anchor=self.TOOL_ANCHOR, offset=[0, 0, 0, 0, 0, 0]))

    # ── probe ──────────────────────────────────────────────────────────
    def _probe_step(self, direction_unit, distance, vel, clb_anchor,
                    expected_trigger=None, step_label="", timeout=None):
        """One IO-monitored probe move. Returns the trigger world XYZ, or
        None on timeout.

        Real: pre-IK the destination so the lmove endpoint pins the wrist
        branch + rail, then delegate the contact move + IO + halt to the
        probe-tool component (Probe.probe). Sim: no IO, so we
        animate toward `expected_trigger` (capped at `distance`) with the
        same relative-move primitive and report it as the touch."""
        tool = self._tool()
        tool_solid = tool.assembly[next(iter(tool.assembly))]

        if not tool.probe_ready():
            print(f"    probe not ready (input not at safe value) — skipping {step_label}")
            return None

        if self._sim_mode:
            if expected_trigger is None:
                return None
            expected_trigger = np.asarray(expected_trigger, dtype=float)
            self.core._last_joints = None
            self.core.update_pose()
            current = np.array(
                tool_solid.pose(anchor=self.TOOL_ANCHOR, offset=[0, 0, 0, 0, 0, 0])[:3],
                dtype=float,
            )
            dirv = np.array(direction_unit[:3], dtype=float)
            dist_to_expected = float(np.dot(expected_trigger - current, dirv))
            if 0 < dist_to_expected <= distance:
                delta = dirv * dist_to_expected
                triggered = True
            else:
                delta = dirv * float(distance)
                triggered = False
            self._relative_move(delta, clb_anchor, vel=vel)
            if not triggered:
                return None
            return expected_trigger

        # real — pre-IK then hand off the contact move to the tool
        self.core._last_joints = None
        self.core.update_pose()
        current = tool_solid.pose(anchor=self.TOOL_ANCHOR, offset=[0, 0, 0, 0, 0, 0])
        target_xyz = np.array([float(current[i]) + float(direction_unit[i]) * float(distance)
                               for i in range(3)])
        J, C = self._ik_to_world(target_xyz, clb_anchor)
        if C != 2 or J is None:
            print(f"    IK failed (C={C}) for probe step "
                  f"toward {[round(float(v), 3) for v in target_xyz]}")
            return None

        trigger = tool.probe(joint_target=J, expected_world_xyz=target_xyz,
                             vel=vel, accel=self.FINE_ACCEL, jerk=self.FINE_JERK, timeout=timeout)
        if trigger is None:
            print("    probe did not trigger within timeout")
            return None
        time.sleep(0.1)   # settle before reading the trigger pose back
        return np.array(self._tcp_world()[:3], dtype=float)

    def _probe_two_step(self, direction_unit, clb_anchor, expected_trigger=None):
        """step 1 (coarse) → back off → step 2 (fine). Step 2 is recorded."""
        tp1 = self._probe_step(direction_unit, self.SEARCH_STEP_1, self.PROBE_VEL,
                               clb_anchor, expected_trigger=expected_trigger, step_label="step1")
        if tp1 is None:
            return None
        back = -np.array(direction_unit[:3]) * self.STEP_1_BACKOFF
        self._relative_move(back, clb_anchor, vel=self.VEL_MEDIUM)
        tp2 = self._probe_step(direction_unit, self.SEARCH_STEP_2, self.PROBE_VEL,
                               clb_anchor, expected_trigger=expected_trigger, step_label="step2")
        return tp2

    def _probe_wall(self, wall_probe_pt, direction_unit, clb_anchor):
        """Wall probe: expected hit is the pocket wall at the pocket radius
        along `direction_unit`; run the two-step probe."""
        radius = self.POCKET_DIAMETER / 2.0
        expected = np.array(wall_probe_pt) + radius * np.array(direction_unit)
        return self._probe_two_step(direction_unit, clb_anchor, expected_trigger=expected)

    # ── math ───────────────────────────────────────────────────────────
    @staticmethod
    def _fit_plane(pts, frame):
        """Fit a floor plane in the local `frame` = (origin, x, y, z),
        returning (a, b, c, n_world). Passing the pocket's own axes keeps
        the lstsq well-conditioned for any pocket orientation."""
        pts = np.asarray(pts, dtype=float)
        origin = np.asarray(frame[0], dtype=float)
        x_axis = np.asarray(frame[1], dtype=float)
        y_axis = np.asarray(frame[2], dtype=float)
        z_axis = np.asarray(frame[3], dtype=float)
        rel = pts - origin
        A = np.column_stack([rel @ x_axis, rel @ y_axis, np.ones(len(pts))])
        coeff, *_ = np.linalg.lstsq(A, rel @ z_axis, rcond=None)
        a, b, c = float(coeff[0]), float(coeff[1]), float(coeff[2])
        n_local = np.array([-a, -b, 1.0])
        n_local = n_local / np.linalg.norm(n_local)
        n_world = n_local[0] * x_axis + n_local[1] * y_axis + n_local[2] * z_axis
        return a, b, c, n_world / np.linalg.norm(n_world)

    @staticmethod
    def _slerp_abc(abc1, abc2, t=0.5):
        """Blend two ABC orientations on the unit quaternion (shortest arc)."""
        q1 = dorna_pose.rmat_to_quat(dorna_pose.abc_to_rmat(abc1))
        q2 = dorna_pose.rmat_to_quat(dorna_pose.abc_to_rmat(abc2))
        if (q1[0] * q2[0] + q1[1] * q2[1] + q1[2] * q2[2] + q1[3] * q2[3]) < 0:
            q2 = [-q2[0], -q2[1], -q2[2], -q2[3]]
        q = dorna_pose.quat_slerp(q1, q2, t)
        n = (q[0] ** 2 + q[1] ** 2 + q[2] ** 2 + q[3] ** 2) ** 0.5
        q = [v / n for v in q]
        return list(dorna_pose.rmat_to_abc(dorna_pose.quat_to_rmat(q)))

    @staticmethod
    def _abc_from_normal(plane_n, nominal_x):
        """Measured orientation from the floor normal (Z) and the anchor's
        nominal in-plane X. Captures the tilt; yaw stays at nominal."""
        MZ = np.asarray(plane_n, dtype=float)
        MZ = MZ / np.linalg.norm(MZ)
        MX = np.asarray(nominal_x, dtype=float)
        MX = MX - np.dot(MX, MZ) * MZ
        MX = MX / np.linalg.norm(MX)
        MY = np.cross(MZ, MX)
        MY = MY / np.linalg.norm(MY)
        R = np.column_stack([MX, MY, MZ])
        return [float(a) for a in dorna_pose.rmat_to_abc(R)]

    # ── per-pocket measurement (XY walls + A/B/Z floor plane) ──────────
    def _calibrate_pocket(self, clb_anchor, pocket):
        """Probe one side pocket: 4-pair wall touches → XY centre; 6-point
        floor pattern → Z + tilt. Returns the measured world floor point,
        Z and ABC. In-memory only; nothing is written to core.json here."""
        center_w, place_w, entry_dir, wall_1, wall_2 = self._frames(clb_anchor, pocket)
        place_xyz   = np.array(place_w[:3])
        approach_pt = place_xyz - self.approach_clearance * entry_dir
        entry_pt    = place_xyz - self.POCKET_ENTRY_CLEARANCE * entry_dir

        print(f"\n  -- {clb_anchor}.{pocket} --")
        wall_touches, floor_touches = [], []

        # XY phase
        assert self._jmove_to(approach_pt, clb_anchor, vel=self.VEL_FAST),  "approach failed"
        assert self._jmove_to(entry_pt,    clb_anchor, vel=self.VEL_MEDIUM), "entry failed"

        if self._sim_mode:
            floor_pt = np.array(place_xyz)
        else:
            floor_pt = self._probe_two_step(entry_dir, clb_anchor)
        assert floor_pt is not None, "initial floor find failed"

        wall_probe_pt   = np.array(floor_pt) - self.WALL_PROBE_DEPTH * entry_dir
        # Lift measured along -entry_dir so a tilted pocket lifts up the
        # pocket axis, not up world Z.
        lift_along_axis = self.POCKET_ENTRY_CLEARANCE - self.WALL_PROBE_DEPTH

        def _touch(start_3d, direction, label):
            start_3d = np.asarray(start_3d, dtype=float)
            above_pt = start_3d - lift_along_axis * entry_dir
            assert self._jmove_to(above_pt, clb_anchor, vel=self.VEL_MEDIUM), f"{label} lift failed"
            assert self._jmove_to(start_3d, clb_anchor, vel=self.VEL_MEDIUM), f"{label} descend failed"
            tp = self._probe_wall(start_3d, direction, clb_anchor)
            assert tp is not None, f"{label} probe failed"
            tp = np.array(tp)
            wall_touches.append(tp)
            self._relative_move(-np.array(direction) * self.PROBE_RETRACT_DIST, clb_anchor)
            return tp

        def _pair(start_3d, direction, label):
            tp_pos = _touch(start_3d, +np.array(direction), f"+{label}")
            tp_neg = _touch(start_3d, -np.array(direction), f"-{label}")
            return (tp_pos + tp_neg) / 2.0

        M1 = _pair(wall_probe_pt, wall_1, "wall_1")
        M2 = _pair(M1,            wall_2, "wall_2")
        diag_1 = math.cos(math.radians( 45.0)) * wall_1 + math.sin(math.radians( 45.0)) * wall_2
        diag_2 = math.cos(math.radians(-45.0)) * wall_1 + math.sin(math.radians(-45.0)) * wall_2
        M3 = _pair(M2, diag_1, "diag_1")
        M4 = _pair(M3, diag_2, "diag_2")
        combined_xy = (M2 + M4) / 2.0

        # A/B/Z floor pattern
        angles = np.linspace(0.0, 2 * math.pi, self.NUM_FLOOR_PROBES, endpoint=False)
        for i, theta in enumerate(angles):
            offset_3d = self.BOTTOM_PROBE_RADIUS * (math.cos(theta) * wall_1 + math.sin(theta) * wall_2)
            target_3d = combined_xy + offset_3d
            above_pt  = target_3d - lift_along_axis * entry_dir
            assert self._jmove_to(above_pt,  clb_anchor, vel=self.VEL_MEDIUM), f"floor[{i}] lift failed"
            assert self._jmove_to(target_3d, clb_anchor, vel=self.VEL_MEDIUM), f"floor[{i}] descend failed"
            expected_floor = target_3d + self.WALL_PROBE_DEPTH * entry_dir
            fp = self._probe_two_step(entry_dir, clb_anchor, expected_trigger=expected_floor)
            assert fp is not None, f"floor[{i}] probe failed"
            floor_touches.append(fp)

        a_c, b_c, c_c, plane_n = self._fit_plane(
            floor_touches, frame=(place_xyz, wall_1, wall_2, -entry_dir),
        )
        # Evaluate the fitted floor at combined_xy's in-plane coords, then
        # rebuild a world 3D point (works for any pocket orientation).
        rel   = np.asarray(combined_xy)[:3] - np.asarray(place_xyz)
        lx_at = float(rel @ wall_1)
        ly_at = float(rel @ wall_2)
        lz_at = a_c * lx_at + b_c * ly_at + c_c
        world_floor_pt = (np.asarray(place_xyz)
                          + lx_at * np.asarray(wall_1)
                          + ly_at * np.asarray(wall_2)
                          + lz_at * (-np.asarray(entry_dir)))

        self._jmove_to(approach_pt, clb_anchor, vel=self.VEL_FAST)

        measured_xyz = [float(v) for v in world_floor_pt]
        abc = self._abc_from_normal(plane_n, wall_1)
        print(f"     measured floor: {[round(v, 4) for v in measured_xyz]}")
        return dict(
            pocket=pocket,
            measured_xyz=measured_xyz,
            measured_z=measured_xyz[2],
            abc=abc,
            plane_normal=[float(n) for n in plane_n],
        )

    # ── small centre pocket (place3) refinement ────────────────────────
    def _calibrate_small_pocket(self, clb_anchor, rough_place3,
                                search_dist=5.0, retract_dist=1.5,
                                mid_hole_offset=3.0, pre_descent_clearance=5.0):
        """4-touch refinement of the tight centre hole, using the rough
        place3 estimate (fused from the two side pockets) so the rod
        descends into the hole instead of onto the surface. Returns the
        precise centre XYZ.

        `rough_place3` is a corrected 6-pose [x,y,z,a,b,c]."""
        R = np.array(dorna_pose.abc_to_rmat(rough_place3[3:6]))
        entry_dir = -R[:, 2]
        wall_1    =  R[:, 0]
        wall_2    =  R[:, 1]
        place_xyz = np.array(rough_place3[:3])
        # jig top is 6 mm above the pocket floor (place3 z=5, top z=11).
        top_xyz   = place_xyz - 6.0 * entry_dir

        approach_pt  = place_xyz - self.approach_clearance * entry_dir
        above_top_pt = top_xyz   - pre_descent_clearance * entry_dir
        entry_pt     = place_xyz - mid_hole_offset * entry_dir
        descent_dist = float(np.linalg.norm(entry_pt - above_top_pt))

        print(f"\n  -- {clb_anchor}.place3 (centre hole) --")
        assert self._jmove_to(approach_pt,  clb_anchor, vel=self.VEL_FAST),  "approach failed"
        assert self._jmove_to(above_top_pt, clb_anchor, vel=self.VEL_MEDIUM), "pre-descent failed"

        if self._sim_mode:
            assert self._jmove_to(entry_pt, clb_anchor, vel=self.PROBE_VEL), "sim descent failed"
        else:
            # IO-monitored descent — a trigger here means the rod hit the
            # tool surface instead of entering the hole (rough estimate off).
            descent_timeout = descent_dist / self.PROBE_VEL + 2.0
            trigger = self._probe_step(entry_dir, descent_dist, self.PROBE_VEL, clb_anchor,
                                       step_label="centre_descent", timeout=descent_timeout)
            if trigger is not None:
                raise RuntimeError(
                    f"probe triggered during descent into the place3 hole at "
                    f"{[round(float(v), 4) for v in trigger]} — rod hit the tool "
                    f"surface instead of the centre hole; the side-pocket estimate "
                    f"is off by more than the hole clearance."
                )

        touches = []
        for label, direction in [
            ("+wall_1", wall_1), ("-wall_1", -wall_1),
            ("+wall_2", wall_2), ("-wall_2", -wall_2),
        ]:
            d = np.asarray(direction, dtype=float)
            expected_wall = entry_pt + d * 2.0   # hole radius = 2 mm
            tp = self._probe_step(d, search_dist, self.PROBE_VEL, clb_anchor,
                                  expected_trigger=expected_wall, step_label=f"centre_{label}")
            assert tp is not None, f"{label} probe failed"
            touches.append(np.array(tp, dtype=float))
            self._relative_move(-d * retract_dist, clb_anchor, vel=self.PROBE_VEL)

        center_xyz = ((touches[0] + touches[1]) / 2.0 + (touches[2] + touches[3]) / 2.0) / 2.0
        self._jmove_to(approach_pt, clb_anchor, vel=self.VEL_FAST)
        print(f"     centre XY: {[round(v, 4) for v in center_xyz.tolist()]}")
        return dict(pocket="place3", measured_xyz=center_xyz.tolist())

    # ── per-anchor entry point ─────────────────────────────────────────
    def calibrate_clb_anchor(self, clb_anchor):
        """Calibrate one clb anchor: probe its 3 pockets, fuse into one
        measured pose, map it back to the anchor, and store a single
        raw/corrected point under the recipe's calibration_name."""
        print(f"\n{'=' * 70}")
        print(f"=== {self.component.name}.{clb_anchor}  ->  {self.calibration_name} ===")
        print(f"{'=' * 70}")

        # gross staging approach — clear of the part, oriented for the tool
        self._gross_approach(clb_anchor)

        # side pockets → rough XY + Z + tilt
        p1 = self._calibrate_pocket(clb_anchor, "place1")
        p2 = self._calibrate_pocket(clb_anchor, "place2")

        rough_xy  = (np.array(p1["measured_xyz"][:2]) + np.array(p2["measured_xyz"][:2])) / 2.0
        rough_z   = float(np.mean([p1["measured_z"], p2["measured_z"]]))
        rough_abc = self._slerp_abc(p1["abc"], p2["abc"])
        rough_place3 = [float(rough_xy[0]), float(rough_xy[1]), rough_z, *rough_abc]

        # centre pocket → precise XY
        p3 = self._calibrate_small_pocket(clb_anchor, rough_place3)

        # fuse: precise XY from place3, Z + tilt from the side pockets
        measured_place3 = [float(p3["measured_xyz"][0]), float(p3["measured_xyz"][1]),
                           rough_z, *rough_abc]

        # back-map place3 (offset [0,0,5] from the anchor) to the clb anchor
        oz = self.POCKET_OFFSETS["place3"][2]
        corrected = list(dorna_pose.transform_pose(
            [0, 0, -oz, 0, 0, 0], from_frame=measured_place3, to_frame=[0, 0, 0, 0, 0, 0],
        ))
        raw = list(self._body().pose(anchor=clb_anchor))

        self.core.calibration.add_point(raw, corrected, threshold=1e-3,
                                        dict_name=self.calibration_name)
        print(f"  [{clb_anchor}] raw       = {[round(v, 4) for v in raw]}")
        print(f"  [{clb_anchor}] corrected = {[round(v, 4) for v in corrected]}")
        return dict(clb_anchor=clb_anchor, raw=raw, corrected=corrected)
