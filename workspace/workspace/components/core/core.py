# workspace/components/core.py
from copy import deepcopy
import logging
from mergedeep import merge
import json
import math
import os
from pathlib import Path
import numpy as np
import yaml

from dorna2 import Solid, Dorna
import dorna2.pose
from workspace.components.factory import register
from path_planning import Planner

log = logging.getLogger(__name__)
from workspace.components.calibration import Calibration
from workspace.components.core.robot_station import RobotStation
from workspace.devices import AutoRecover, attach_device


import time


class SplinePath:
    """Natural cubic spline through a list of points in joint space."""
    def __init__(self, points):
        """
        points : list of lists/arrays, each of length d (joint space dimension)
        """
        if len(points) < 2:
            raise ValueError("Need at least two points to build a spline.")
        self.points = points
        self.n = len(points)
        self.d = len(points[0])

        # 1. Chord‑length parameter s (cumulative Euclidean distance)
        self.s = [0.0]
        for i in range(1, self.n):
            diff = [points[i][j] - points[i-1][j] for j in range(self.d)]
            dist = math.sqrt(sum(dj * dj for dj in diff))
            self.s.append(self.s[-1] + dist)
        self.total_len = self.s[-1]

        # Number of segments = n-1
        n_seg = self.n - 1

        # Pre‑allocate coefficient storage: one list per segment, each of length d
        self.coeffs = [ [None] * self.d for _ in range(n_seg) ]

        # Work arrays (tridiagonal solve)
        h = [self.s[i+1] - self.s[i] for i in range(n_seg)]

        for dim in range(self.d):
            y = [p[dim] for p in points]

            # Tridiagonal system for c (natural spline: c[0]=c[n-1]=0)
            alpha = [0.0] * self.n
            l = [0.0] * self.n
            mu = [0.0] * self.n
            z = [0.0] * self.n
            c_arr = [0.0] * self.n   # c at knots

            l[0] = 1.0
            mu[0] = 0.0
            z[0] = 0.0

            for i in range(1, self.n-1):
                alpha[i] = (3.0/h[i])*(y[i+1]-y[i]) - (3.0/h[i-1])*(y[i]-y[i-1])
                l[i] = 2.0*(self.s[i+1] - self.s[i-1]) - h[i-1]*mu[i-1]
                mu[i] = h[i]/l[i]
                z[i] = (alpha[i] - h[i-1]*z[i-1])/l[i]

            l[self.n-1] = 1.0
            z[self.n-1] = 0.0
            c_arr[self.n-1] = 0.0

            # Back substitution and coefficient computation
            for i in range(self.n-2, -1, -1):
                c_arr[i] = z[i] - mu[i]*c_arr[i+1]

                a = y[i]
                b = (y[i+1]-y[i])/h[i] - h[i]*(c_arr[i+1] + 2.0*c_arr[i])/3.0
                c = c_arr[i]
                if h[i] < 1e-12:
                    d_coeff = 0
                else:
                    d_coeff = (c_arr[i+1] - c_arr[i])/(3.0*h[i])
                # Store at the correct segment index i
                self.coeffs[i][dim] = (a, b, c, d_coeff)

    def get_curve_data(self, s):
        """
        Return (position, velocity, acceleration) at arc length s.
        Each is a list of length self.d.
        """
        if s <= 0.0:
            s = 0.0
        # Locate segment
        if s >= self.total_len:
            i = self.n - 2
            ds = self.total_len - self.s[i]
        else:
            low, high = 0, self.n - 2
            while low <= high:
                mid = (low + high) // 2
                if s < self.s[mid]:
                    high = mid - 1
                elif s >= self.s[mid+1]:
                    low = mid + 1
                else:
                    i = mid
                    break
            ds = s - self.s[i]

        pos = [0.0] * self.d
        vel = [0.0] * self.d
        acc = [0.0] * self.d
        for j in range(self.d):
            a, b, c, d_coeff = self.coeffs[i][j]
            # position
            pos[j] = ((d_coeff * ds + c) * ds + b) * ds + a
            # velocity (first derivative w.r.t. s)
            vel[j] = (3.0 * d_coeff * ds + 2.0 * c) * ds + b
            # acceleration (second derivative w.r.t. s)
            acc[j] = 6.0 * d_coeff * ds + 2.0 * c
        return pos, vel, acc

    def get_curve_point(self, s):
        """Return only position at arc length s."""
        pos, _, _ = self.get_curve_data(s)
        return pos

@register("core")
class Core:
    """
    Core component: robot (A0..A5), rail (base + carriage), and plates (plate_0..plate_5).
    Internal attachments are determined by the preset (e.g., 'core500').
    """
    DEFAULTS = dict(
        simulation = True,
        ip = "",
        has_rail = True,
        rail_cfg = {"type": "rail_hd_500mm", "axis": 6, "offset": 0, "usem":1, "pprm":4000, "tprm":75, "usee":1, "ppre":4000, "tpre":75, "p":0.01, "i":0.0001, "d":0, "duration":10000 , "threshold":200},
        has_camera = False,
        camera_cfg = {
            "serial_number": "",
            "ip": "127.0.0.1",
            "port": 80,
            # Camera driver on the vision server: "d405" (RealSense,
            # depth+color) or "ueye_xs" (IDS uEye XS, color + autofocus).
            "type": "d405",
            "stream": {"width":1280, "height":720, "fps":30},
            "mode": "bgrd",
            "filter": {},
            "exposure": None,
            "native_res": None,
        },
        has_tool_changer = True,
        # I/O signals fired on attach/detach. Each list-of-lists is a
        # sequence of [output_port, value, delay_s] rows played in order.
        tool_changer_cfg = {
            "output_attach": [[1, 0, 0], [0, 1, 0], [2, 0, 0.25]],
            "output_detach": [[1, 0, 0], [0, 1, 0], [2, 1, 0.25]],
        },
        has_motion_plan = False, # enable or disable path planing
        # Whether this robot's j5 rotates without the ±180° travel
        # limit (the infinite-wrist variant). When True, every j5
        # target the workspace commands is unwrapped to the nearest
        # 360°-equivalent of the live j5 (see ``unwrap_j5``), so the
        # wrist never winds back to reach a canonical angle, and the
        # decapper runs its screw in ONE shot with no gripper re-bites.
        # The firmware speaks absolute counter values and is never
        # touched — no set_joint, ever.
        j5_infinite = False,
    )


    def __init__(self, name: str, cfg: dict, workspace, **kwargs):
        # License check — must pass before anything else
        from workspace.license import verify
        verify()

        # prm
        prm = deepcopy(self.DEFAULTS) # default
        merge(prm, cfg) # config
        merge(prm, kwargs) # kwargs
        
        # type
        prm.setdefault("type", getattr(self.__class__, "_registered_type", prm.get("type")))

        # init
        self.name = name
        self.workspace = workspace
        self.type = prm["type"]

        # assembly
        self.assembly = {}

        # -------- j5 travel
        self.j5_infinite = bool(prm["j5_infinite"])

        # -------- rail
        self.has_rail = prm["has_rail"]
        self.rail_cfg = prm["rail_cfg"]
        if self.rail_cfg["type"] == "rail_hd_500mm":
            self.rail_min = -159
            self.rail_max = 316.0
        elif self.rail_cfg["type"] == "rail_hd_1000mm":
            self.rail_min = -215.0
            self.rail_max = 775.0
        elif self.rail_cfg["type"] == "rail_hd_2000mm":
            self.rail_min = -280.0
            self.rail_max = 1720.0
        else:
            raise ValueError(f"Unsupported rail type: {self.rail_cfg['type']}")
        
        # -------- robot
        self.robot_ip = prm["ip"]

        # -------- calibration
        core_dir = self._core_dir()
        self.calibration = Calibration(
            self.name,
            file_path=(core_dir / "calibrate.json") if core_dir else None,
        )

        # -------- tool_changer
        self.has_tool_changer = prm["has_tool_changer"]
        self.tool_changer_cfg = prm["tool_changer_cfg"]

        # ------- camera
        self.has_camera = prm["has_camera"]
        self.camera_cfg = prm["camera_cfg"]
        
        # planner
        self.planner = Planner()

        self.planner.update(
            aux_dir=[[1, 0, 0], [0, 0, 0]],
            aux_limit=[[self.rail_min, self.rail_max], [-1,1]],
            has_camera=self.has_camera
        )

        # --- scene dirty tracking & last joints (for Workspace optimization)
        self._last_joints = None
        # Workspace will look at this flag; initialize as dirty so first frame recomputes
        if hasattr(self.workspace, "_scene_dirty"):
            self.workspace._scene_dirty = True

        # Always create a RobotStation — its underlying dorna2.Dorna is
        # needed for offline kinematic math (``self.dorna.kinematic.inv``)
        # whether or not we're driving real hardware. Empty IP / sim mode
        # just skip the connect, so the kinematic side keeps working.
        # In real mode RobotStation also implements the Device protocol,
        # so the robot shows up in the Devices panel alongside the camera
        # and self-heals connection drops via AutoRecover.
        # Authored simulation intent. Failures must NOT flip this — the
        # operator either authored sim mode or didn't, and an unreachable
        # robot is a fault to surface (red dot + auto-pause), not a
        # reason to silently switch to a fake api.
        self._simulation_mode = prm["simulation"]
        # RobotStation gets the authored sim flag but always attempts
        # the real connect — device-guide §16: bus state reflects
        # hardware reachability regardless of sim. The flag is used
        # by ``set_simulation`` for runtime toggle and by Core for api
        # routing; AutoRecover is suspended in sim via the
        # attachment's ``set_sim`` so a fake/unreachable IP in sim
        # shows red dot + SIM pill without retry storms.
        self.dorna = RobotStation(
            ip=self.robot_ip or "",
            label=self.name,
            simulation=self._simulation_mode,
        )
        self._robot_attachment = None

        # Always attach the robot to the device bus when an IP exists,
        # regardless of sim mode and regardless of whether the initial
        # connect succeeded. ``attach_device`` publishes the robot's
        # info+state retained, and only wires AutoRecover in real mode.
        # Sim → green dot + SIM badge. Real + unreachable → red dot,
        # AutoRecover retries, runtime auto-pauses.
        if self.robot_ip:
            def _make_recover() -> AutoRecover:
                rec = AutoRecover(
                    recover_fn=self.dorna.recover,
                    set_status=self.dorna._set_state,
                    log_label=f"dorna:{self.robot_ip}",
                )
                # Trigger AutoRecover ONLY on connection-lost events
                # (TCP drop / host unreachable). Alarms ALSO fire
                # state→down, but AutoRecover can't fix alarms — they
                # need physical operator clearance. Wiring the generic
                # state-down edge here would auto-"recover" alarms by
                # reconnecting (which works, since TCP is fine), then
                # incorrectly flip the dot back to green ms after it
                # turned red. Use the connection-specific event instead.
                self.dorna.on_connection_lost(rec.trigger)
                return rec

            try:
                self._robot_attachment = attach_device(
                    self.dorna,
                    kind="dorna",
                    sim=self._simulation_mode,
                    critical=True,
                    meta={"ip": self.robot_ip, "model": prm.get("model", "dorna_ta"),
                          "j5_infinite": self.j5_infinite},
                    recover_factory=_make_recover,
                )
            except Exception:
                # Adapter / recovery wiring must NOT take down Core —
                # the robot is still usable for kinematic math.
                import logging
                logging.getLogger(__name__).exception(
                    "Core[%s]: attach_device failed for robot",
                    self.name,
                )

        # Robot api selection follows the authored sim flag verbatim.
        # On an infinite wrist the api is wrapped in J5WindingGuard —
        # a non-blocking tripwire for the turn-carry invariant (no
        # commanded j5 more than one turn from live).
        if not self._simulation_mode:
            print(f"🟡 {self.name} simulation api disabled")
            self.robot_api = self._guard_api(self.dorna)
        else:
            self.robot_api = self._guard_api(SimulationAPI())
            print(f"🔵 {self.name} simulation api enabled")


        # Robot-mounted camera: wired at the END of __init__ (the
        # camera is a real component bolted to robot_A5, so the robot
        # solids must exist first). See the block after the rail attach.
        self.camera = None
        # Detection the operator "Detect" button runs (last registered;
        # mirrors Inspection). Only surfaced when has_camera (see
        # operator_actions).
        self._default_detection = "default"

        # --------- motion_planning
        self.has_motion_plan = prm["has_motion_plan"]

        # --------- IK solution cache (core_ik.json in the project folder)
        # Lazy-loaded on the first IK() call; see _ik_cache_init. Every
        # failure mode (no project folder, missing/corrupt file,
        # read-only filesystem) degrades to in-memory-only or straight
        # solving — the cache must never take down a run.
        self._ik_cache = None          # {key: {"arm": [j0..j5], "rail": r|None}}
        self._ik_cache_path = None

        # --------- planned-path cache (core/path.json in the project folder)
        # Same lifecycle as the IK cache: lazy on the first motion_plan(),
        # every failure mode degrades to planning from scratch.
        self._path_cache = None        # {key: [[j0..jN], ...]}
        self._path_cache_path = None

        # --------- rail carriage (shared across all rail sizes)
        rail_hd_carriage_anchors = {
            "center": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
            "hole_0": [-50.0, 50.0, 0.0, 0.0, 0.0, 0.0],
            "hole_1": [50.0, 50.0, 0.0, 0.0, 0.0, 0.0],
            "hole_2": [50.0, -50.0, 0.0, 0.0, 0.0, 0.0],
            "hole_3": [-50.0, -50.0, 0.0, 0.0, 0.0, 0.0],
        }

        # next we add the rail base depending on the type of the rail
        if self.has_rail:
            if self.rail_cfg["type"] == "rail_hd_500mm":
                rail_base_anchors = {
                    "center": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
                    "carriage": [0.0, 0.0, 82.0, 0.0, 0.0, 0.0],
                    "hole_0": [0.0, 25/2, 0.0, 0.0, 0.0, 0.0],
                    "hole_1": [400.0, 25/2, 0.0, 0.0, 0.0, 0.0],
                    "hole_2": [400.0, -25/2, 0.0, 0.0, 0.0, 0.0],
                    "hole_3": [0.0, -25/2, 0.0, 0.0, 0.0, 0.0],
                }
                # Same treatment as the 1000mm below. Measured from
                # rail_hd_500mm_base.glb: the beam spans x -295..466 and
                # centres at 85.5 (offset was -84), and the motor reaches
                # y=168.4 — shallower than the 1000mm's 179.4, so it gets
                # its own depth rather than a shared constant. Both long
                # boxes stop at y=125, the fixture plate edge.
                rail_collision_boxes = {"rail_base": [
                    {"pose": [170.0-84.5, 36.125, 40.0, 0.0, 0.0, 0.0], "scale": [761.0, 177.75, 82]},
                    {"pose": [-176.105-84.5, 111.3, 40.0, 0.0, 0.0, 0.0], "scale": [68.6, 115.0, 82]},
                    {"pose": [170.0-84.5, 92.25, 41.0, 0.0, 0.0, 0.0], "scale": [761.0, 65.5, 82]}
                ]}
                self.rail_base = Solid(name="rail_base", type="rail_hd_500mm_base", anchors=rail_base_anchors, component=self.name, collision_box=rail_collision_boxes)

            elif self.rail_cfg["type"] == "rail_hd_1000mm":
                rail_base_anchors = {
                    "center": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
                    "carriage": [0.0, 0.0, 82.0, 0.0, 0.0, 0.0],
                    "hole_0": [0.0, 12.5, 0.0, 0.0, 0.0, 0.0],
                    "hole_1": [800.0, 12.5, 0.0, 0.0, 0.0, 0.0],
                    "hole_2": [800.0, -12.5, 0.0, 0.0, 0.0, 0.0],
                    "hole_3": [0.0, -12.5, 0.0, 0.0, 0.0, 0.0],
                }
                # x offset was -119; the mesh centres at 280.5, not 301
                # (rail_hd_1000mm_base.glb spans x -350..911), so the
                # boxes sat 20.5 down the rail from the rail itself.
                # Both long boxes stop at y=125, the fixture plate edge —
                # past that they claim air over the next plate's row.
                rail_collision_boxes = {"rail_base": [
                    {"pose": [420.0-139.5, 36.125, 40.0, 0.0, 0.0, 0.0], "scale": [1261.0, 177.75, 82]},
                    {"pose": [-176.105-139.5, 116.7, 40.0, 0.0, 0.0, 0.0], "scale": [68.6, 126.0, 82]},
                    {"pose": [420.0-139.5, 92.25, 41.0, 0.0, 0.0, 0.0], "scale": [1261.0, 65.5, 82]}
                ]}
                self.rail_base = Solid(name="rail_base", type="rail_hd_1000mm_base", anchors=rail_base_anchors, component=self.name, collision_box=rail_collision_boxes)

            elif self.rail_cfg["type"] == "rail_hd_2000mm":
                rail_base_anchors = {
                    "center": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
                    "carriage": [0.0, 0.0, 82.0, 0.0, 0.0, 0.0],
                    "hole_0": [0.0, 12.5, 0.0, 0.0, 0.0, 0.0],
                    "hole_1": [1600.0, 12.5, 0.0, 0.0, 0.0, 0.0],
                    "hole_2": [1600.0, -12.5, 0.0, 0.0, 0.0, 0.0],
                    "hole_3": [0.0, -12.5, 0.0, 0.0, 0.0, 0.0],
                }
                # Same treatment again. rail_hd_2000mm_base.glb: beam
                # x -550..1711 centres at 580.5, and the motor matches the
                # 1000mm's (y 54.1..179.4). Both long boxes stop at y=125,
                # the fixture plate edge.
                rail_collision_boxes = {"rail_base": [
                    {"pose": [920.0-339.5, 36.125, 40.0, 0.0, 0.0, 0.0], "scale": [2261.0, 177.75, 82]},
                    {"pose": [-176.105-339.5, 116.7, 40.0, 0.0, 0.0, 0.0], "scale": [68.6, 126.0, 82]},
                    {"pose": [920.0-339.5, 92.25, 41.0, 0.0, 0.0, 0.0], "scale": [2261.0, 65.5, 82]}
                ]}
                self.rail_base = Solid(name="rail_base", type="rail_hd_2000mm_base", anchors=rail_base_anchors, component=self.name, collision_box=rail_collision_boxes)

            else:
                raise ValueError(f"Unsupported rail type: {self.rail_cfg['type']}")

            self.assembly["rail_base"] = self.rail_base
            self.rail_carriage = Solid(name="rail_carriage", type="rail_hd_carriage", anchors=rail_hd_carriage_anchors, component=self.name)
            self.assembly["rail_carriage"] = self.rail_carriage


        robot_A0_anchors = {
            "input": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
            "output": [0.0, 0.0, 131.0, 0.0, 0.0, 90.0],
            "hole_0": [35.0, 50.0, 0.0, 0.0, 0.0, 0.0],
            "hole_1": [35.0, -50.0, 0.0, 0.0, 0.0, 0.0],
            "hole_2": [-15.0, 50.0, 0.0, 0.0, 0.0, 0.0],
            "hole_3": [-15.0, -50.0, 0.0, 0.0, 0.0, 0.0],
            "hole_4": [-65.0, 50.0, 0.0, 0.0, 0.0, 0.0],
            "hole_5": [-65.0, -50.0, 0.0, 0.0, 0.0, 0.0],
            "hole_6": [-115.0, 50.0, 0.0, 0.0, 0.0, 0.0],
            "hole_7": [-115.0, -50.0, 0.0, 0.0, 0.0, 0.0],
        }
        robot_A1_anchors = {
            "input": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
            "output": [36.0, -80.0, 99.01829,0, -90.0, 0.0],
        }
        robot_A2_anchors = {
            "input": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
            "output": [0, -210, 4.8, 0.0, 0.0, 0.0],
        }
        robot_A3_anchors = {
            "input": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
            "output": [0.0, -29, 73.0, 90.0, 0, 0.0],
        }
        robot_A4_anchors = {
            "input": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
            "output": [0, -29, 146.0, 90.0, 0, 0.0],
        }
        robot_A5_anchors = {
            "input": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
            "output": [0, 29, 60.0, -90, 0, 0.0],
            "hole_0": [10, -24, 21, -69.282032, 69.282032, 69.282032],
            "hole_1": [-10, -24, 21, -69.282032, 69.282032, 69.282032],
        }
        robot_flange_anchors = {
            "input": [0.0, 0.0, -6.0, 0.0, 0.0, 0.0],
            "output": [0, 0.0, 0.0, 0.0, 0, 0.0],
        }


        self.robot_A0 = Solid(name="robot_A0", type="robot_A0", anchors=robot_A0_anchors, component=self.name)
        self.robot_A1 = Solid(name="robot_A1", type="robot_A1", anchors=robot_A1_anchors, component=self.name)
        self.robot_A2 = Solid(name="robot_A2", type="robot_A2", anchors=robot_A2_anchors, component=self.name)
        self.robot_A3 = Solid(name="robot_A3", type="robot_A3", anchors=robot_A3_anchors, component=self.name)
        self.robot_A4 = Solid(name="robot_A4", type="robot_A4", anchors=robot_A4_anchors, component=self.name)
        self.robot_A5 = Solid(name="robot_A5", type="robot_A5", anchors=robot_A5_anchors, component=self.name)
        self.robot_flange = Solid(name="robot_flange", type="robot_flange", anchors=robot_flange_anchors, component=self.name)


        self.assembly["robot_A0"] = self.robot_A0
        self.assembly["robot_A1"] = self.robot_A1
        self.assembly["robot_A2"] = self.robot_A2
        self.assembly["robot_A3"] = self.robot_A3
        self.assembly["robot_A4"] = self.robot_A4
        self.assembly["robot_A5"] = self.robot_A5
        self.assembly["robot_flange"] = self.robot_flange

        # we check if there is tool changer
        if self.has_tool_changer:
            tool_changer_robot_side_anchors = {
                "input": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
                "output": [0.0, 0.0, 22.0, 0.0, 0.0, 0.0],
                "tool_changer_connection": [0.0, 0.0, 22.0, 0.0, 0.0, 0.0],
                "top": [0.0, 0.0, 34.0, 0.0, 0.0, 0.0]
            }
            tool_changer_collision_boxes = {"tool_changer_robot_side": [
                {"pose": [0.0, 0.0, 18.5-3, 0.0, 0.0, 0.0], "scale": [48, 48, 37]}
            ]}
            self.tool_changer_robot_side = Solid(name="tool_changer_robot_side", type="tool_changer_robot_side", anchors=tool_changer_robot_side_anchors, component=self.name, collision_box=tool_changer_collision_boxes)
            self.assembly["tool_changer_robot_side"] = self.tool_changer_robot_side
            self.tool_changer_robot_side.attach_to(parent=self.robot_flange, parent_anchor="output", child_anchor="input", offset=[0, 0, 0, 0, 0, 0])


        # now we just need to attach robot_A0 to the rail carriage
        if self.has_rail:
            att = cfg.get("robot_attach")
            if att:
                self.robot_A0.attach_to(parent=self.rail_carriage, parent_anchor=att.get("rail_carriage_anchor","hole_1"), child_anchor=att.get("robot_A0_anchor","hole_0"), offset=att.get("offset",[0, 0, 0, 0, 0, 0]))
            else:
                self.robot_A0.attach_to(parent=self.rail_carriage, parent_anchor="hole_1", child_anchor="hole_0", offset=[0, 0, 0, 0, 0, 0])

        # ------- robot-mounted camera: a real component, not core math.
        # has_camera=true auto-adds an inspection_d405_robot component
        # ("<core>_camera") bolted to robot_A5's camholder holes. ITS
        # ``lens`` anchor states the camera frame (the scene tree is the
        # kinematic truth — see lens_pose) and IT owns the VisionStation;
        # core proxies capture/detect through it so recipes keep pointing
        # at the core. has_camera=false keeps a detached sim station so
        # the surface stays callable.
        from workspace.components.inspection.vision_station import VisionStation
        if self.has_camera:
            from workspace.components import factory as comp_factory
            cam_name = f"{self.name}_camera"
            self.camera = comp_factory.create_component(cam_name, {
                "type": "inspection_d405_robot",
                "simulation": bool(prm["simulation"]),
                "camera_cfg": deepcopy(self.camera_cfg),
            }, workspace)
            workspace.components[cam_name] = self.camera
            self.camera.assembly["body"].attach_to(
                parent=self.robot_A5,
                parent_anchor="hole_0",
                child_anchor="hole_0",
                offset=[0, 0, 0, 0, 0, 0],
            )
            self.vision = self.camera.vision
        else:
            self.vision = VisionStation(
                ip=self.camera_cfg.get("ip", "127.0.0.1"),
                port=int(self.camera_cfg.get("port", 80)),
                serial_number=self.camera_cfg.get("serial_number", ""),
                camera_cfg=self.camera_cfg,
                simulation=True,
                label=f"{self.name} camera",
            )


    # -------------------------------------------------------------------------
    # live joint update
    # -------------------------------------------------------------------------
    # -------------------------------------------------------------------------
    # live joint update (event-driven / dirty-aware)
    # -------------------------------------------------------------------------
    
    def update_pose(self):
        """
        If a robot API connection exists, update link poses ONLY when joints change.
        When joints change, mark the Workspace scene as dirty so compute_world_poses()
        knows it must recompute the world transforms.
        """

        if self.robot_api is None:
            return

        # Read joints (expect 8 floats, but we just treat as sequence)
        joints_raw = self.robot_api.joint()
        try:
            joints = list(joints_raw)
        except TypeError:
            joints = joints_raw

        if not joints:
            return

        # --- Detect if anything actually moved ---
        moved = False
        if self._last_joints is None:
            moved = True
        else:
            # you can tighten epsilon if you want
            eps = 1e-4
            for a, b in zip(joints, self._last_joints):
                if abs(a - b) > eps:
                    moved = True
                    break

        if not moved:
            # Joints unchanged -> no geometry change -> don't touch scene
            return

        # Update cached joints
        self._last_joints = joints

        # Mark scene dirty so Workspace knows transforms must be recomputed
        if hasattr(self.workspace, "_scene_dirty"):
            self.workspace._scene_dirty = True

        # --- Apply new joint values to kinematic chain as before ---
        if self.has_rail:
            self.rail_carriage.attach_to(
                parent=self.rail_base,
                parent_anchor="carriage",
                child_anchor="center",
                offset=[joints[self.rail_cfg["axis"]], 0, 0, 0, 0, 0],
            )

        self.robot_A1.attach_to(
            parent=self.robot_A0,
            parent_anchor="output",
            child_anchor="input",
            offset=[0, 0, 0, 0, 0, joints[0]],
        )
        self.robot_A2.attach_to(
            parent=self.robot_A1,
            parent_anchor="output",
            child_anchor="input",
            offset=[0, 0, 0, 0, 0, joints[1]],
        )
        self.robot_A3.attach_to(
            parent=self.robot_A2,
            parent_anchor="output",
            child_anchor="input",
            offset=[0, 0, 0, 0, 0, joints[2]],
        )
        self.robot_A4.attach_to(
            parent=self.robot_A3,
            parent_anchor="output",
            child_anchor="input",
            offset=[0, 0, 0, 0, 0, joints[3]],
        )
        self.robot_A5.attach_to(
            parent=self.robot_A4,
            parent_anchor="output",
            child_anchor="input",
            offset=[0, 0, 0, 0, 0, -joints[4]],
        )
        self.robot_flange.attach_to(
            parent=self.robot_A5,
            parent_anchor="output",
            child_anchor="input",
            offset=[0, 0, 0, 0, 0, joints[5]],
        )



    # ── Operator-callable primitives ──────────────────────────────────
    # Atomic ops on the robot itself. Designed for both recipe use and
    # operator-button use via ``operator_actions``. All go through
    # ``workspace.rt`` so sim mode is respected.

    def current_tool(self):
        """Return the tool component currently mounted on the robot, or
        None if nothing is attached.

        Walks the dorna2 Solid kinematic chain — either the tool
        changer's ``tool_changer_connection`` anchor (when present) or
        the flange's ``output`` anchor — and resolves to the workspace
        component. Single source of truth for "what tool is on the
        robot right now"; recipes delegate to this instead of
        duplicating the walk.
        """
        try:
            if self.has_tool_changer:
                for child in self.tool_changer_robot_side.children["tool_changer_connection"]:
                    return self.workspace.components[child["child_solid"].component]
            else:
                for child in self.robot_flange.children["output"]:
                    return self.workspace.components[child["child_solid"].component]
        except (KeyError, AttributeError):
            return None
        return None

    def tool_holds_load(self) -> bool:
        """True if the mounted tool is currently holding a picked item —
        a solid attached at its ``tcp`` anchor (e.g. a tube in the
        gripper). Used to defer a graceful Park until the robot's hand
        is empty, so a held item is never stranded mid-air. Returns
        False when nothing is mounted or the hand is empty."""
        tool = self.current_tool()
        if tool is None:
            return False
        try:
            body = tool.assembly[next(iter(tool.assembly))]
            for _ in body.children["tcp"]:
                return True
        except (KeyError, AttributeError, StopIteration, TypeError):
            pass
        return False

    def motor_enable(self):
        self.workspace.rt.motor(1)

    def motor_disable(self):
        self.workspace.rt.motor(0)

    def tool_attach(self):
        """Fire the tool-changer engage IO. Operator caution: safe only
        when the robot is at a tool-rack position; firing elsewhere can
        mis-grip or do nothing useful."""
        if not self.has_tool_changer:
            raise RuntimeError("This robot has no tool changer")
        self.workspace.rt.output(config=self.tool_changer_cfg["output_attach"])

    def tool_detach(self):
        """Fire the tool-changer release IO. Operator caution: with a
        tool held away from a rack position, this drops it on the
        floor."""
        if not self.has_tool_changer:
            raise RuntimeError("This robot has no tool changer")
        self.workspace.rt.output(config=self.tool_changer_cfg["output_detach"])

    def tool_enable(self):
        """Enable whatever tool is currently on the flange.

        Looks up the mounted tool via ``current_tool`` and calls its
        ``enable`` method. Raises with a clear, operator-friendly
        message if no tool is mounted or the mounted tool doesn't
        implement enable — the orchestrator surfaces the message as a
        toast on the failed button click.
        """
        tool = self.current_tool()
        if tool is None:
            raise RuntimeError("No tool currently attached to the robot")
        fn = getattr(tool, "enable", None)
        if not callable(fn):
            raise RuntimeError(f"Tool '{getattr(tool, 'name', tool)}' has no enable() method")
        return fn()

    def tool_disable(self):
        """Disable whatever tool is currently on the flange. See
        ``tool_enable`` for the failure-mode contract."""
        tool = self.current_tool()
        if tool is None:
            raise RuntimeError("No tool currently attached to the robot")
        fn = getattr(tool, "disable", None)
        if not callable(fn):
            raise RuntimeError(f"Tool '{getattr(tool, 'name', tool)}' has no disable() method")
        return fn()

    def operator_actions(self) -> list[dict]:
        actions = [
            {"label": "Enable Motors",  "method": "motor_enable",  "icon": "power",     "group": "motors"},
            {"label": "Disable Motors", "method": "motor_disable", "icon": "power-off", "group": "motors"},
            {"label": "Enable Tool",    "method": "tool_enable",   "icon": "zap",       "group": "tool"},
            {"label": "Disable Tool",   "method": "tool_disable",  "icon": "zap-off",   "group": "tool"},
        ]
        if self.has_tool_changer:
            actions += [
                {"label": "Attach Tool", "method": "tool_attach", "icon": "link",     "group": "tool_changer"},
                {"label": "Detach Tool", "method": "tool_detach", "icon": "link-off", "group": "tool_changer"},
            ]
        if self.has_camera:
            actions += [{"label": "Detect", "method": "operator_detect", "icon": "eye"}]
        return actions

    def _guard_api(self, api):
        """Wrap a robot api in the j5 winding tripwire on an infinite
        wrist (see ``J5WindingGuard``); pass-through on a limited one."""
        return J5WindingGuard(api) if self.j5_infinite else api

    def simulation(self, on: bool = True):
        """Live sim/real flip — parity with ``MultiMeter.simulation``
        (see device-guide.md §16).

        Sim is orthogonal to connection state. Three layers flipped:

          1. Robot api selection — ``self.robot_api`` switches
             between ``SimulationAPI`` (sim) and ``self.dorna``
             (real). This is what makes recipes run on canned data
             without touching hardware.
          2. RobotStation flag — ``set_simulation`` flips
             ``self.dorna.simulation`` (no connection change). The
             TCP stays open; bus state keeps reflecting
             reachability.
          3. Bus attachment — ``set_sim`` republishes ``info.sim``
             so the panel SIM pill flips live and AutoRecover
             suspends or re-arms.
        """
        if self._simulation_mode == bool(on):
            return  # idempotent
        if self._simulation_mode and not on:
            # sim → real: route recipes through the real client
            self._simulation_mode = False
            self.robot_api = self._guard_api(self.dorna)
            print(f"🟡 {self.name} simulation api disabled")
        else:
            # real → sim: route recipes through SimulationAPI
            self._simulation_mode = True
            self.robot_api = self._guard_api(SimulationAPI(joints=self.robot_api.joint()))
            print(f"🔵 {self.name} simulation api enabled")

        self.dorna.set_simulation(self._simulation_mode)

        # Republish the bus sim flag. set_sim on the attachment is
        # idempotent + suspends/re-arms AutoRecover.
        if self._robot_attachment is not None:
            self._robot_attachment.set_sim(self._simulation_mode)



    # ── Per-station core folder ─────────────────────────────────────────
    # ALL station-local state lives in one folder: <project>/core/ by
    # default, overridable per project with ``core_dir:`` in launch.yaml
    # (absolute, or relative to the project folder). Files:
    #   calibrate.json  — station calibration (Calibration)
    #   ik.json         — IK solution cache
    #   path.json       — planned-path cache
    # Legacy root-level core.json / core_ik.json / core_path.json are
    # MOVED in the first time the folder resolves (one-time migration).
    # The folder is per-station truth — gitignored, never synced.

    _CORE_DIR_LEGACY = {
        "core.json": "calibrate.json",
        "core_ik.json": "ik.json",
        "core_path.json": "path.json",
    }

    def _core_dir(self):
        """Resolve (and create) the station's core folder. None when no
        project folder is resolvable (bare harness). Never raises."""
        if hasattr(self, "_core_dir_cache"):
            return self._core_dir_cache
        d = None
        try:
            paths = getattr(self.workspace, "config_paths", None) or []
            if paths:
                proj = Path(paths[0]).resolve().parent
                if proj.name == "scene":
                    proj = proj.parent
                if proj.is_dir():
                    d = proj / "core"
                    try:
                        launch = proj / "launch.yaml"
                        if launch.is_file():
                            override = (yaml.safe_load(launch.read_text()) or {}).get("core_dir")
                            if override:
                                d = Path(override)
                                if not d.is_absolute():
                                    d = proj / d
                    except Exception:
                        pass  # unreadable launch.yaml → default location
                    d.mkdir(parents=True, exist_ok=True)
                    for old, new in self._CORE_DIR_LEGACY.items():
                        src, dst = proj / old, d / new
                        if src.is_file() and not dst.exists():
                            os.replace(src, dst)
        except Exception:
            d = None
        self._core_dir_cache = d
        return d

    # ── IK solution cache ───────────────────────────────────────────────
    # core/ik.json (see the core-folder block above).
    # Rows are keyed by the full rounded numeric inputs
    # of a solve — target pose in world, tool pose in flange, robot/rail
    # base pose in world, sweep params, ref_joints — so moving a
    # component, recalibrating, or changing recipe params changes the
    # key and simply misses (stale rows can never match; no invalidation
    # logic exists or is needed). On a hit the stored solution is
    # re-validated against the LIVE collision scene before use; if the
    # world changed and it now collides, we fall through to a full solve
    # and the row is overwritten.

    @staticmethod
    def _ik_row_valid(v):
        return (isinstance(v, dict) and isinstance(v.get("arm"), list)
                and len(v["arm"]) == 6)

    def _ik_cache_init(self):
        """Resolve the cache path and load it. Never raises.

        On-disk format is JSONL — one ``{"k": key, "v": row}`` per
        line, APPENDED per solve. A full-file rewrite per miss would be
        O(N²) bytes to the SD card while a big cache builds (and ~0.3s
        per miss at tens of thousands of rows); an append is a few
        hundred bytes flat. Duplicate keys are last-wins at load; a
        torn tail line (power cut mid-append) is dropped. Legacy
        whole-dict files migrate transparently, and the file compacts
        at load when overwritten rows pile up."""
        self._ik_cache = {}
        try:
            d = self._core_dir()
            if d is not None:
                self._ik_cache_path = d / "ik.json"
        except Exception:
            self._ik_cache_path = None
        if self._ik_cache_path is None or not self._ik_cache_path.is_file():
            return
        if not self._cache_scene_ok(self._ik_cache_path):
            self._cache_discard_stale(self._ik_cache_path, "ik.json")
            return
        try:
            text = self._ik_cache_path.read_text()
            # Format detection must be parse-based — JSONL lines also
            # start with "{". If the WHOLE text parses as one dict it's
            # either a single JSONL record ({"k":..,"v":..}) or the
            # legacy whole-dict format (migrate it); otherwise parse
            # line-by-line as JSONL.
            try:
                whole = json.loads(text)
            except Exception:
                whole = None
            if isinstance(whole, dict):
                if set(whole.keys()) == {"k", "v"}:
                    if self._ik_row_valid(whole.get("v")):
                        self._ik_cache[whole["k"]] = whole["v"]
                    return
                self._ik_cache = {
                    k: v for k, v in whole.items() if self._ik_row_valid(v)
                }
                self._ik_cache_rewrite()  # migrate legacy → JSONL
                return
            lines = 0
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                lines += 1
                try:
                    rec = json.loads(line)
                    if self._ik_row_valid(rec.get("v")):
                        self._ik_cache[rec["k"]] = rec["v"]
                except Exception:
                    continue  # torn/corrupt line — drop it
            # Compact when overwritten/dropped rows dominate the file.
            if lines > 100 and lines > 2 * len(self._ik_cache):
                self._ik_cache_rewrite()
        except Exception:
            self._ik_cache = {}  # unreadable file → start empty

    def _ik_cache_rewrite(self):
        """Atomically rewrite the whole file as compact JSONL."""
        try:
            tmp = self._ik_cache_path.with_suffix(".json.tmp")
            fp = self._scene_fp()
            stamp = (json.dumps({"__scene__": fp}) + "\n") if fp else ""
            tmp.write_text(stamp + "".join(
                json.dumps({"k": k, "v": v}, separators=(",", ":")) + "\n"
                for k, v in self._ik_cache.items()
            ))
            os.replace(tmp, self._ik_cache_path)
        except Exception:
            pass

    # ── scene-fingerprint cache guard ─────────────────────────────────
    # Both caches store solves keyed on world poses; ANY scene-geometry
    # change silently invalidates them. The first JSONL line stamps the
    # workspace's scene fingerprint; a mismatch (or a legacy unstamped
    # file) discards the cache instead of serving stale rows. Old
    # loaders skip the stamp line naturally (no "v" field).

    def _scene_fp(self):
        try:
            return getattr(self.workspace, "scene_fingerprint", None)
        except Exception:
            return None

    def _cache_scene_ok(self, path):
        """True when ``path`` is stamped with THIS scene's fingerprint.
        Unstamped or foreign-stamped files are stale."""
        fp = self._scene_fp()
        if fp is None:
            return True  # no fingerprint available — never discard on doubt
        try:
            for line in path.read_text().splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    return False
                return rec.get("__scene__") == fp
        except Exception:
            return False
        return False  # empty file — restamp

    def _cache_discard_stale(self, path, label):
        try:
            path.unlink()
            print(f"[cache] {label} discarded — scene changed since it was built")
        except Exception:
            pass

    def _cache_stamp(self, f):
        """Write the stamp line into a fresh cache file handle."""
        fp = self._scene_fp()
        if fp is not None:
            f.write(json.dumps({"__scene__": fp}) + "\n")

    def _ik_key(self, target_solid, target_anchor, target_offset, tool_solid,
                tool_anchor, tool_offset, base_distance, rail_step, rail_span,
                ref_joints, left_approach):
        """Rounded numeric key for one solve, or None (= don't cache).
        Only ref_joints-seeded solves are cacheable — without a fixed
        reference the answer depends on live joints."""
        if ref_joints is None:
            return None
        try:
            obj_w = target_solid.pose(anchor=target_anchor, offset=target_offset)
            tool_w = [0, 0, 0, 0, 0, 0]
            if tool_solid and tool_anchor:
                tool_w = tool_solid.pose(anchor=tool_anchor, in_frame=self.robot_flange, offset=tool_offset)
            base_w = (self.rail_base.pose(anchor="carriage") if self.has_rail
                      else self.robot_A0.pose())
            def r3(v):
                # +0.0 collapses -0.0 → 0.0; without it, json renders
                # "-0.0" vs "0.0" and physically identical poses mint
                # duplicate rows.
                return round(float(v), 3) + 0.0
            parts = (
                [r3(v) for v in obj_w]
                + [r3(v) for v in tool_w]
                + [r3(v) for v in base_w]
                + [r3(v) for v in list(ref_joints)[:6]]
                + [None if base_distance is None else r3(base_distance),
                   r3(rail_step), int(rail_span),
                   bool(left_approach), bool(self.has_rail)]
            )
            return json.dumps(parts, separators=(",", ":"))
        except Exception:
            return None

    def _ik_cache_get(self, key, cur, aux):
        """Return a full joint list for a validated hit, else None.
        The cached row stores arm (j0..j5) + rail only; all passthrough
        joints come from the LIVE ``cur`` — identical to what a fresh
        solve does. Validation re-checks collision against the live
        scene (stricter than the solver's own last-pushed-scene check)."""
        entry = self._ik_cache.get(key) if self._ik_cache else None
        if not entry:
            return None
        try:
            J = list(cur)
            for i in range(6):
                J[i] = float(entry["arm"][i])
            if entry.get("rail") is not None:
                J[aux] = float(entry["rail"])
            if len(self.check_collision([float(v) for v in J[:6]])) > 0:
                return None  # world changed — fall through to a full solve
            return J
        except Exception:
            return None

    def _ik_cache_put(self, key, J, aux, with_rail):
        """Store a solved row and append it to disk. Never raises.

        One JSONL line per solve — constant cost regardless of cache
        size, and the row is on disk the moment it solved (a kill
        loses nothing). A torn line from a power cut is dropped at the
        next load."""
        try:
            row = {
                "arm": [float(J[i]) for i in range(6)],
                "rail": float(J[aux]) if with_rail else None,
            }
            self._ik_cache[key] = row
            if self._ik_cache_path is None:
                return  # no resolvable project folder — in-memory only
            _fresh = not self._ik_cache_path.is_file()
            with open(self._ik_cache_path, "a") as f:
                if _fresh:
                    self._cache_stamp(f)
                f.write(json.dumps({"k": key, "v": row}, separators=(",", ":")) + "\n")
        except Exception:
            pass  # read-only fs / race — keep the in-memory row, move on

    # ── Planned-path cache ──────────────────────────────────────────────
    # core/path.json (see the core-folder block above). NO exact keys
    # anywhere: every numeric input of a hop wobbles run-to-run — starts
    # by encoder flutter (~0.2°), goals by IK seeded from live joints,
    # tool boxes by attach offsets derived from live poses at pick — and
    # any quantization has bucket edges a wobbling value flaps across,
    # minting fresh entries forever. Rows hold GEOMETRY only — start,
    # goal, attached boxes, waypoints — matched by DISTANCE with
    # per-element tolerances, newest row first. A hit REPLAYS verbatim
    # (endpoints snapped to the live start / requested goal) with no
    # re-check: the scene's fixed collision boxes are the project's
    # contract — a path validated once at creation stays truthful as
    # long as the boxes do. Changed the boxes (or the bench)? Delete
    # core/path.json. Validation happens ONCE, at creation: the solve
    # itself, plus the decimation gate on the sparse polyline. The row
    # format is simple on purpose — hand-authored rows (teach a hop its
    # waypoints) are first-class, but note they replay unvalidated.
    # EVERY solved hop is stored — direct-connection segments and OMPL
    # detours alike: one solve pipeline, one record, one replay rule.

    PATH_CACHE_START_TOL = 1.0   # deg (arm) / mm (rail) per joint
    PATH_CACHE_GOAL_TOL = 0.5    # deg / mm per joint
    PATH_CACHE_TOOL_TOL = 1.0    # mm / deg per tool-box element
    PATH_CACHE_MAX_ROWS = 500    # oldest evicted first
    PATH_DECIMATE_EPS = 4.0      # deg/mm max deviation for waypoint decimation
                                 # (4: spreads travel knots so cont corners
                                 # grow — 2 clustered knots 11-15 apart and
                                 # corner-bound whole chains to ~55; every
                                 # straightened segment is still validated
                                 # against the collision envelope)
    PATH_CHECK_PADDING_MARGIN = 2.0  # mm — check envelope hysteresis vs the plan envelope

    @staticmethod
    def _decimate_path(path, eps, check=None):
        """Corner-aware decimation (Ramer-Douglas-Peucker in joint
        space): drop waypoints where the path runs straight, keep them
        where it bends. smove's spline then flows through sparse points
        in free space while staying pinned exactly where the path
        hugs obstacles — the bends are where the kept points cluster.

        ``check(two_point_segment) -> bool`` (the collision gate) is
        enforced PER SEGMENT: a straightened span must pass on its own,
        and one that fails splits at its max-deviation point so only
        its halves retry. Free-space travel collapses to its endpoints
        while spans that actually hug an obstacle keep dense samples —
        unlike a wholesale gate, one tight span near a station no
        longer condemns the entire hop to the dense polyline. Adjacent
        original points are never checked: they are the planner's own
        validated steps, the worst-case output equals the input."""
        if len(path) <= 2:
            return path
        pts = np.array(path, dtype=float)
        keep = np.zeros(len(pts), dtype=bool)
        keep[0] = keep[-1] = True
        stack = [(0, len(pts) - 1)]
        while stack:
            a, b = stack.pop()
            if b - a < 2:
                continue
            seg = pts[b] - pts[a]
            L2 = float(seg @ seg)
            best_d, best_i = -1.0, -1
            for i in range(a + 1, b):
                v = pts[i] - pts[a]
                t = max(0.0, min(1.0, (float(v @ seg) / L2) if L2 > 0 else 0.0))
                d = float(np.linalg.norm(v - t * seg))
                if d > best_d:
                    best_d, best_i = d, i
            if best_d > eps or (check is not None
                                and not check([[float(v) for v in pts[a]],
                                               [float(v) for v in pts[b]]])):
                keep[best_i] = True
                stack.append((a, best_i))
                stack.append((best_i, b))
        return [[float(v) for v in p] for p in pts[keep]]

    @staticmethod
    def _path_row_valid(v):
        return (isinstance(v, dict)
                and isinstance(v.get("s"), list) and isinstance(v.get("g"), list)
                and isinstance(v.get("t"), list)
                and isinstance(v.get("p"), list) and len(v["p"]) >= 2
                and all(isinstance(p, list) for p in v["p"]))

    @staticmethod
    def _path_near(a, b, tol):
        return (len(a) == len(b)
                and all(abs(float(x) - float(y)) <= tol for x, y in zip(a, b)))

    @staticmethod
    def _path_tool_sig(tool_boxes):
        """Flat numeric signature of the attached geometry, or None
        (= don't cache)."""
        try:
            return [
                [float(v) for v in list(box["pose"])] + [float(v) for v in list(box["scale"])]
                for box in tool_boxes
            ]
        except Exception:
            return None

    def _path_row_match(self, row, start, goal, sig):
        """Geometry-only matching: start, goal, attached boxes. Planner
        style, gravity, rail weight are NOT matched — revalidation runs
        under the QUERY's constraints, so a row that violates them fails
        the check and falls through to a fresh solve. (Consequence:
        after retuning planner knobs, delete core/path.json to stop
        old-style paths from serving.)"""
        try:
            if len(row["t"]) != len(sig) or not all(
                    self._path_near(a, b, self.PATH_CACHE_TOOL_TOL)
                    for a, b in zip(row["t"], sig)):
                return False
            return (self._path_near(row["s"], start, self.PATH_CACHE_START_TOL)
                    and self._path_near(row["g"], goal, self.PATH_CACHE_GOAL_TOL))
        except Exception:
            return False

    def _path_rows_add(self, row):
        """Insert a row: replace the row whose full context it matches
        (within tolerances), else append. Bounded — oldest drops first."""
        rows = self._path_cache
        for i, r in enumerate(rows):
            if self._path_row_match(r, row["s"], row["g"], row["t"]):
                rows[i] = row
                return
        rows.append(row)
        if len(rows) > self.PATH_CACHE_MAX_ROWS:
            rows.pop(0)

    def _path_cache_init(self):
        """Resolve + load core/path.json (JSONL). Never raises."""
        self._path_cache = []
        try:
            d = self._core_dir()
            if d is not None:
                self._path_cache_path = d / "path.json"
        except Exception:
            self._path_cache_path = None
        if self._path_cache_path is None or not self._path_cache_path.is_file():
            return
        if not self._cache_scene_ok(self._path_cache_path):
            self._cache_discard_stale(self._path_cache_path, "path.json")
            return
        try:
            lines = 0
            for line in self._path_cache_path.read_text().splitlines():
                line = line.strip()
                if not line:
                    continue
                lines += 1
                try:
                    rec = json.loads(line)
                    if self._path_row_valid(rec.get("v")):
                        self._path_rows_add(rec["v"])
                except Exception:
                    continue  # torn/corrupt/legacy line — drop it
            # Compact when replaced/dropped rows dominate the file.
            if lines > 50 and lines > 2 * len(self._path_cache):
                tmp = self._path_cache_path.with_suffix(".json.tmp")
                fp = self._scene_fp()
                stamp = (json.dumps({"__scene__": fp}) + "\n") if fp else ""
                tmp.write_text(stamp + "".join(
                    json.dumps({"v": row}, separators=(",", ":")) + "\n"
                    for row in self._path_cache
                ))
                os.replace(tmp, self._path_cache_path)
        except Exception:
            self._path_cache = []  # unreadable file → start empty

    def _path_cache_get(self, start, goal, sig):
        """Return a cached path (endpoints snapped to the live start /
        requested goal), else None. Newest rows win. No re-check — see
        the block comment: validation is a creation-time event."""
        for row in reversed(self._path_cache or []):
            try:
                if not self._path_row_match(row, start, goal, sig):
                    continue
                p = [[float(v) for v in w] for w in row["p"]]
                p[0] = [float(v) for v in start]
                p[-1] = [float(v) for v in goal]
                return p
            except Exception:
                continue
        return None

    def _path_cache_put(self, start, goal, sig, path):
        """Store a solved hop and append it to disk. Never raises."""
        try:
            row = {
                "s": [round(float(v), 3) for v in start],
                "g": [round(float(v), 3) for v in goal],
                "t": [[round(float(v), 3) for v in b] for b in sig],
                "p": [[round(float(v), 3) for v in p] for p in path],
            }
            self._path_rows_add(row)
            if self._path_cache_path is None:
                return  # no resolvable project folder — in-memory only
            _fresh = not self._path_cache_path.is_file()
            with open(self._path_cache_path, "a") as f:
                if _fresh:
                    self._cache_stamp(f)
                f.write(json.dumps({"v": row}, separators=(",", ":")) + "\n")
        except Exception:
            pass

    @staticmethod
    def _wrap180(x):
        """Canonical name of a shaft angle: (-180, 180]."""
        return -((-float(x) + 180.0) % 360.0 - 180.0)

    def unwrap_j5(self, target, ref=None):
        """Re-base a CANONICAL j5 target onto the wound counter with
        LIMITED-WRIST semantics. Identity unless ``j5_infinite``.

        The firmware takes ABSOLUTE counter values, and on the infinite
        wrist the counter accumulates turns (a 1000° tighten leaves it
        near 980). Motion, however, must behave exactly as it would on
        a limited wrist: 170 → -170 travels the long -340° through 0 —
        NEVER the 20° shortcut across the ±180 seam, which would sweep
        the tool through a region no bench-validated path ever crosses.

        So: keep the counter's accumulated FULL TURNS, express the
        target in canonical (-180, 180] terms, and command
        ``canonical(target) + 360 x turns``. The signed delta is then
        identical to what a limited wrist would execute from the same
        canonical position; the turns ride along untouched (only the
        screw ops change them, deliberately, with raw targets that
        bypass this).
        """
        if not self.j5_infinite or target is None:
            return target
        if ref is None:
            ref = float(self.robot_api.joint()[5])
        turns = round((float(ref) - self._wrap180(ref)) / 360.0)
        return self._wrap180(target) + 360.0 * turns

    def _ik_finish(self, J, cur):
        """Re-base an IK result's j5 onto the live counter's turn count
        (limited-wrist semantics — see ``unwrap_j5``). Copy: cached
        entries stay canonical so a later call re-bases them against
        ITS live j5."""
        if J is None or not self.j5_infinite:
            return J
        J = list(J)
        turns = round((cur[5] - self._wrap180(cur[5])) / 360.0)
        J[5] = self._wrap180(J[5]) + 360.0 * turns
        return J

    def IK(self, target_solid, target_anchor, target_offset=[0,0,0,0,0,0], tool_solid=None, tool_anchor=None, tool_offset=[0,0,0,0,0,0], base_distance=None,
         rail_step=10.0, rail_span=0, ref_joints=None, left_approach=True):



        """
        Returns: (full_joints_or_none, status_code)
        - full_joints_or_none : list[float] length 8 on success; None on failure
            (copy of current joints with j0..j5 from IK and rail updated)
        - status_code :
                2  -> success
            -1  -> rail failure (no rail satisfies the distance within [self.rmin, self.rmax])
            -2  -> IK solver raised errors on all attempts
            -3  -> IK ran but returned no solutions for all attempts
        """
        # we find the rail value so the base of the robot and the desired pose in the space are at base_distance.
        # we find the inverse kinematics based on the pose with respect to the base of the robot.
        # we allow for a little movement of the rail to find better solution.
        # rail frame is at its center of the rail_base
        # assumptions
        # rail has a frame (normally middle of the back bracket.)
        # rail base has an anchor, which is called carriage anchor. 
        # rail carriage frame (located at its center), in the frame of the base is located carriage anchor + offset = [joints[self.aux_axis],0,0,0,0,0]
        # the user input will be a solid, an anchor point in that solide, and offset with respect to that anchor.
        # the function, first finds the rail options with that provide the base distance.
        # it picks the value of the rail which is closer to the current rail value, which is determined from cur_joint 
        # then it solves the inverse kinematics and find only acceptable solutions, and within those acceptable solutions the closest to the cur_joint
        # we do this process for few other values of the rails + and - of the current rail values and within all found solutions, we find the one with minimum joint distance of the current joint
        # this is to avoid singularities and odd solutions of the current base position.
        # for this function, we use         all_sol = kinematic.inv(pose_in_robot, init_joint, True, freedom=None)
        # 
        # 
        # If base distance is not given (None), the rail value will be set to the current rail value.
        # It is very helpful for calibration methods        
        # Refresh all poses/frames
        self.update_pose()
        # Live joints & indices
        cur = list(self.robot_api.joint())   # expect length 8
        aux = self.rail_cfg["axis"]
        r_cur = cur[aux]

        # Cache lookup — see _ik_cache_init above. Key is None (never
        # cached) when ref_joints wasn't given, since that solve depends
        # on live joints.
        if self._ik_cache is None:
            self._ik_cache_init()
        _ck = self._ik_key(target_solid, target_anchor, target_offset,
                           tool_solid, tool_anchor, tool_offset,
                           base_distance, rail_step, rail_span,
                           ref_joints, left_approach)
        if _ck is not None:
            _hit = self._ik_cache_get(_ck, cur, aux)
            if _hit is not None:
                return (self._ik_finish(_hit, cur), 2)

        if ref_joints is None:
            ref_joints = list(cur)

        # --- helper: rails r where |p - (C0 + [r,0,0])| = base_distance and r ∈ [rmin, rmax]
        def rail_solutions(px, py, pz, c0x, c0y, c0z, d, rmin, rmax):
            dx, dy, dz = px, py - c0y, pz - c0z
            
            # we do not condider z difference only x and y
            dz = 0
            #rhs = d*d - (dy*dy + dz*dz) #???
            rhs = d*d - (dz*dz)
            if rhs < 0.0:
                return []
            root = (rhs ** 0.5)
            R = [] 
            # we start by the smaller rail value. If it is out of range then we consider the larger rail value
            
            if left_approach:
                for k in range(-rail_span, rail_span + 1):
                    r = dx - root + k * rail_step
                    if r >= rmin and r <= rmax:
                        R.append(r)
            
            else:
                for k in range(-rail_span, rail_span + 1):
                    r = dx + root + k * rail_step
                    if r >= rmin and r <= rmax:
                        R.append(r)
            return R    
                    

        def joint_distance(q):
            W = np.array([1,1,1,4,1,0.25])
            dq = (np.array(q[:6]) - ref_joints[:6])
            if self.j5_infinite:
                # Candidates are canonical; a wound reference must be
                # compared by its CANONICAL name (limited-wrist
                # semantics — plain difference, no shortest-path wrap),
                # so branch choice matches what a limited wrist picks.
                dq[5] = q[5] - self._wrap180(ref_joints[5])
            return np.linalg.norm(W * dq)

        
        # now there are two cases, with rail and without rail
        if not self.has_rail:

            # we find the pose of the target in the robot frame
            pose_in_robot = target_solid.pose(anchor=target_anchor, in_frame=self.robot_A0, offset=target_offset)
            # Seed: arm-only initial joints (j0..j5)
            init_arm = [cur[i] for i in range(6)]

            tool_pose = [0,0,0,0,0,0]
            if tool_solid and tool_anchor:
                tool_pose = tool_solid.pose(anchor=tool_anchor, in_frame=self.robot_flange, offset=tool_offset)

            self.dorna.kinematic.set_tcp_xyzabc(tool_pose)
            sols = self.dorna.kinematic.inv(pose_in_robot, init_arm, True, freedom=None)
            if sols is None or len(sols) == 0:
                return (None, -2)
            
            best = None                         
            for arm_sol in sols:  # each is a NumPy vector of length 6
                joint_sol = list(cur)     # start from live joints
                for i in range(6):        # overwrite j0..j5
                    joint_sol[i] = float(arm_sol[i])
                
                col_res = self.planner.check_collision(arm_sol)
                if len(col_res) > 0:
                    # collision detected, skip
                    continue
                jd = joint_distance(joint_sol)
                if (best is None) or (jd < best[0]):
                    best = (jd, joint_sol)
            if best:
                if _ck is not None:
                    self._ik_cache_put(_ck, best[1], aux, with_rail=False)
                return (self._ik_finish(best[1], cur), 2)

            else:
                return (None, -2)


        # --- with rail: find rail candidates
        else:

            if base_distance is not None:
                rmin, rmax = self.rail_min, self.rail_max

                # Target pose in rail_base (used only to compute rail candidates)
                px, py, pz, rx, ry, rz = target_solid.pose(anchor=target_anchor, in_frame=self.rail_base, offset=target_offset)

                # r=0 origin on rail_base (carriage anchor)
                c0x, c0y, c0z, _, _, _ = self.rail_base.pose(anchor="carriage")

                # Exact-distance rails; if none, return rail failure
                R = rail_solutions(px, py, pz, c0x, c0y, c0z, base_distance, rmin, rmax)

            
            else:
                # if rail is given, the candidate rail will be that value
                R = [r_cur]


            if not R:
                # SAY WHICH RAIL POSITION WAS WANTED. Status -1 alone
                # ("no rail satisfies the distance") does not say whether
                # the station is 5 mm past the end of travel or 500, and
                # that is the whole difference between nudging it and
                # rebuilding the bench. Note dz is forced to 0 and dy is
                # unused above, so root == base_distance and the only way
                # to land here is the [rmin, rmax] filter.
                if base_distance is not None:
                    sign = -1.0 if left_approach else 1.0
                    want = [px + sign * base_distance + k * rail_step
                            for k in range(-rail_span, rail_span + 1)]
                    log.warning(
                        "IK: no rail position in range — wanted carriage at %s "
                        "mm (target x=%.1f, base_distance=%s, rail_step=%s, "
                        "rail_span=%s, left_approach=%s) but rail travel is "
                        "[%.1f, %.1f]. Move the station along the rail, or "
                        "reach it from the other side.",
                        [round(v, 1) for v in want], px, base_distance,
                        rail_step, rail_span, left_approach, rmin, rmax,
                    )
                return (None, -1)

            # # r0: exact-distance candidate closest to current rail
            # r0 = min(candidates, key=lambda r: abs(r - r_cur))

            # # Neighborhood ONLY around r0 (clamped & deduped)
            # R = {r0 + k * rail_step for k in range(-rail_span, rail_span + 1)}
            # R = {min(max(r, rmin), rmax) for r in R}



            best = None  # (dist, full_q)



            # first we find the pose of the robot base frame in the rail base
            robot_pose_in_rail_base = self.robot_A0.pose(in_frame=self.rail_base)

            #for r in sorted(R, key=lambda rr: abs(rr - r0)):

            # now we find the pose of the object in the world frame
            object_pose_in_world = target_solid.pose(anchor=target_anchor, offset=target_offset)
            T_object = np.array(dorna2.pose.xyzabc_to_T(object_pose_in_world))

            # set tcp
            tool_pose = [0,0,0,0,0,0]
            if tool_solid and tool_anchor:
                tool_pose = tool_solid.pose(anchor=tool_anchor, in_frame=self.robot_flange, offset=tool_offset)

            self.dorna.kinematic.set_tcp_xyzabc(tool_pose)
            
            for r in R:
                # Pose relative to ROBOT BASE
                # now we update robot pose in rail base
                updated_robot_pose_in_rail_base = list(robot_pose_in_rail_base)
                updated_robot_pose_in_rail_base[0] = r-r_cur + robot_pose_in_rail_base[0]

                # now we find update robot pose in world base
                updated_robot_pose_in_world_base = self.rail_base.pose(pose=updated_robot_pose_in_rail_base)

                # now we calculate the transfer matrix for this pose
                T_robot = np.array(dorna2.pose.xyzabc_to_T(updated_robot_pose_in_world_base))
                inv_T_robot = dorna2.pose.inv_T(T_robot)
                
                # now we find the pose of the object in the robot frame
                T_object_in_robot = inv_T_robot @ T_object
                pose_in_robot = dorna2.pose.T_to_xyzabc(T_object_in_robot)

                # Seed: arm-only initial joints (j0..j5)
                init_arm = [ref_joints[i] for i in range(6)]


                """
                tool_pose = [0,0,0,0,0,0]
                if tool_solid and tool_anchor:
                    tool_pose = tool_solid.pose(anchor=tool_anchor, in_frame=self.robot_flange, offset=tool_offset)

                self.dorna.kinematic.set_tcp_xyzabc(tool_pose)
                """
                # pose_in_robot[3] += 0.01  # to avoid singularity
                # pose_in_robot[4] += 0.01  # to avoid singularity
                # pose_in_robot[5] += 0.01  # to avoid singularity
                sols = self.dorna.kinematic.inv(pose_in_robot, init_arm, True, freedom=None)
 
                if sols is None or len(sols) == 0:
                    continue

                for arm_sol in sols:  # each is a NumPy vector of length 6
                    joint_sol = list(cur)     # start from live joints
                    for i in range(6):        # overwrite j0..j5
                        joint_sol[i] = float(arm_sol[i])
                    joint_sol[aux] = r               # set rail
                    col_res = self.planner.check_collision(arm_sol)
                    if len(col_res) > 0:
                        # collision detected, skip
                        continue
                    jd = joint_distance(joint_sol)
                    if (best is None) or (jd < best[0]):
                        best = (jd, joint_sol)

            if best:
                if _ck is not None:
                    self._ik_cache_put(_ck, best[1], aux, with_rail=True)
                return (self._ik_finish(best[1], cur), 2)

            else:
                return(None, -2)



    def stop(self):
        # robot — close the bus attachment first (stops AutoRecover and
        # publishes online: false via the clean-shutdown path), then
        # close the underlying dorna client.
        if self._robot_attachment is not None:
            try:
                self._robot_attachment.close()
            except Exception:
                pass
        if self.dorna:
            try:
                self.dorna.close()
            except Exception:
                pass

        # camera (vision-server client)
        try:
            self.vision.close()
        except Exception:
            pass

    # ── Robot-mounted camera detection API ────────────────────────────
    # Thin wrappers around the shared VisionStation helper (see
    # workspace/components/inspection/vision_station.py). Mirrors the
    # Inspection component's surface so recipes that touch either look
    # identical.

    def add_detection(self, name: str, **detection_preset) -> bool:
        # Auto-attach the robot host so the server can wire this Detection
        # to the matching dorna2.Dorna instance for hand-eye geometry.
        if "robot_host" not in detection_preset and getattr(self, "ip", None):
            detection_preset["robot_host"] = self.ip
        self._default_detection = name
        return self.vision.add_detection(name, **detection_preset)

    def operator_detect(self):
        """No-arg ``detect`` for the Operator Actions UI — runs the
        default detection. Surfaced only when ``has_camera`` (see
        ``operator_actions``). Mirrors ``Inspection.operator_detect``."""
        return self.detect(self._default_detection)

    def lens_pose(self) -> list:
        """The robot-mounted lens's CURRENT world pose — the per-capture
        frame the Inspector passes (camera_in_world), putting the robot
        camera on the same contract as fixed stations (vision-guide §5).

        Read straight from the auto-added camera component's ``lens``
        anchor: the component is bolted to robot_A5's camholder holes,
        so the scene tree — not kinematic math — is the single source
        of the lens frame."""
        if self.camera is None:
            raise RuntimeError(
                f"{self.name} has no camera component (has_camera is false) — no lens pose")
        return self.camera.lens_pose()

    def capture(self, name: str, data=None, camera_in_world=None) -> dict:
        """Capture a fresh atomic snapshot (camera frames + robot joints)
        and cache it server-side. Pair with ``detect(name, use_last=True)``
        so detection runs only on a confirmed-fresh frame. See
        VisionStation.capture for the reply shape and ``data`` modes.
        """
        return self.vision.capture(name, data=data, camera_in_world=camera_in_world)

    def detect(self, name: str, sim_return=[], use_last: bool = False, data=None, **kwargs):
        """Run the named detection. By default, captures a fresh frame
        first and runs on it (raises ``CameraUnavailableError`` on
        capture failure). Pass ``use_last=True`` to skip capture and
        run on the previously cached frame. See VisionStation.detect.

        ``sim_return`` (device-guide §17) — the detection result returned
        in sim (default ``[]``); pass detections to inject them.
        """
        return self.vision.detect(name, sim_return=sim_return, use_last=use_last, data=data, **kwargs)

    # ── DeviceComponent contract (workspace.devices.DeviceComponent) ───

    @property
    def device_ids(self) -> list[str]:
        """Device ids this component depends on. See docs/device-guide.md §9.

        The robot only — the robot-mounted camera is its own component
        (``<core>_camera``, auto-added when has_camera) and reports the
        camera device itself.
        """
        ids: list[str] = []
        if self.robot_ip:
            ids.append(f"dorna:{self.robot_ip}")
        return ids

    def device_claim(self, device_id: str) -> str:
        """Project-level sim/real claim for ``device_id``.

        For the robot, Core IS the bus publisher and the bus already
        carries the sim flag; this method just mirrors that for any
        consumer that prefers the workspace-side surface. The robot-
        mounted camera claims through its own component
        (``<core>_camera`` — see Inspection.device_claim).
        """
        if self.robot_ip and device_id == f"dorna:{self.robot_ip}":
            return "sim" if self._simulation_mode else "real"
        return "real"

    def lmove_points(self, joint_from, joint_to, tool_pose=[0, 0, 0, 0, 0, 0], step=5.0):
        """Sample the lmove path joint_from → joint_to as smove-ready
        waypoints, one every ``step`` mm — the same tested interpolation
        the sim lmove executes (straight TCP line, nearest-branch IK).
        Returns a list of joint lists ending exactly at joint_to, or
        None on IK failure."""
        try:
            return lmove_path_points(self.dorna.kinematic, joint_from, joint_to, tool_pose, step)
        except Exception:
            return None

    def blend_points(self, points, radius, tool_pose=[0, 0, 0, 0, 0, 0], from_idx=1, step=5.0, padding=None, rail_weight=0.004):
        """Fillet EVERY sharp corner from ``from_idx`` on, auto-detected
        by TCP direction change — see blend_sharp_corners. When
        ``padding`` is given, each fillet is validated ONCE at creation
        against the same slimmed envelope the decimation gate uses
        (padding - margin): an arc may not introduce a collision the
        sharp corner didn't have — travel arcs that would cut toward
        an obstacle keep their corner, approach arcs inside station
        envelopes keep the fixed-boxes contract. Returns the blended
        list (unchanged when no corners), or None on failure (keep the
        sharp path)."""
        try:
            check = None
            if padding is not None:
                check_pad = max(0.0, float(padding) - self.PATH_CHECK_PADDING_MARGIN)
                cw, ct = self.workspace.compute_collision_boxes(check_pad)
                base_in_world = list(self.rail_base.pose(anchor="carriage"))
                self.planner.update(scene=self._boxes_to_cubes(cw), gripper=self._boxes_to_cubes(ct), base_in_world=base_in_world)
                check = lambda seg: self.planner.check([list(p) for p in seg], rail_weight=rail_weight)
            return blend_sharp_corners(self.dorna.kinematic, points, radius, tool_pose, step, from_idx, check=check)
        except Exception:
            return None

    def traj_points(self, points, vel, accel, dt=0.01):
        """Time-parameterize a waypoint path with TOPP-RA under
        per-joint velocity / acceleration caps. Returns tmove samples
        ``[[t, j0..j7], ...]`` every ``dt`` seconds, t starting at 0.

        Curvature-aware where smove's single global S-curve is flat:
        the profile slows exactly where the path bends (the fillets)
        and cruises on straights, so corners are taken within the
        per-joint accel bound instead of whipped through at cruise
        speed. Deterministic: same path + caps -> same trajectory.
        toppra is required when this runs — no silent fallback."""
        try:
            import toppra as ta
            import toppra.constraint as tc
        except ImportError as ex:
            raise RuntimeError(
                "pvt motion requires toppra (sudo pip3 install toppra)") from ex
        pts = [[float(v) for v in p] for p in points]
        # Physical knot merge: knots closer than MIN_LEG are the SAME
        # waypoint (encoder noise makes a robot that is already at the
        # chain's first knot produce a ~0.3-unit ghost leg, whose
        # 0.4*leg corner then certifies the whole hop to a crawl —
        # seen on the bench as a 12 s scale pick-off). The final
        # target always survives exactly: it replaces its
        # too-close predecessor instead of being dropped.
        MIN_LEG = 1.0
        merged = [pts[0]]
        for i, q in enumerate(pts[1:], 1):
            if max(abs(a - b) for a, b in zip(q, merged[-1])) < MIN_LEG:
                if i == len(pts) - 1 and len(merged) > 1:
                    merged[-1] = q
                elif i == len(pts) - 1:
                    merged.append(q)
                continue
            merged.append(q)
        pts = [p for i, p in enumerate(merged)
               if i == 0 or any(abs(a - b) > 1e-9 for a, b in zip(p, merged[i - 1]))]
        if len(pts) < 2:
            return [[0.0] + pts[0]] if pts else []
        arr = np.array(pts)
        s = np.zeros(len(arr))
        for i in range(1, len(arr)):
            s[i] = s[i - 1] + float(np.linalg.norm(arr[i] - arr[i - 1]))
        if s[-1] <= 0.0:
            return [[0.0] + pts[0]]
        s /= s[-1]
        ndof = arr.shape[1]
        t0 = time.perf_counter()
        inst = ta.algorithm.TOPPRA(
            [tc.JointVelocityConstraint(np.array([[-vel, vel]] * ndof)),
             tc.JointAccelerationConstraint(np.array([[-accel, accel]] * ndof))],
            ta.SplineInterpolator(s, arr),
            parametrizer="ParametrizeConstAccel",
        )
        traj = inst.compute_trajectory()
        if traj is None:
            raise RuntimeError("TOPP-RA could not parameterize the path")
        # Uniform time grid (the tmove wire contract: t_i = i*dt, no
        # off-grid tail) — the final grid point clamps to the exact
        # trajectory end, so the last sample IS the goal pose.
        n = int(math.ceil(traj.duration / dt)) + 1
        samples = [[round(i * dt, 6)] + [float(v) for v in traj(min(i * dt, float(traj.duration)))]
                   for i in range(n)]
        print(f"[traj] {len(pts)} pts -> {len(samples)} samples, "
              f"{traj.duration:.2f}s motion, solved in {(time.perf_counter() - t0) * 1000:.0f} ms")
        return samples

    def chain_prm(self, points, vel, accel, jerk, corner_cap, padding=None, rail_weight=0.004, dt=0.01, sample=5.0, label="cjmove"):
        """Chain parameters for cjmove tuned for MAXIMUM smoothness
        under the user's caps — derived from the firmware's actual
        execution model (see the _fw_* ports of server/motion.cpp):

        1. Per-corner radius: min(corner_cap, 0.4 * min(adjacent leg
           lengths)) — the firmware's own clamp, computed host-side so
           the executed geometry is KNOWN, not guessed.
        2. When ``padding`` is given, each corner's exact firmware
           curve is validated once at creation against the slimmed
           envelope under the fillet contract: the cut may not
           introduce a collision the sharp corner didn't have. A
           failing corner drops to 0 (the chain passes THROUGH that
           knot, still continuous — geometrically safe, and step 3
           slows for the kink).
        3. TOPP-RA over the TRUE executed path — lines + corner curves
           sampled every ``sample`` units — so per-section (vel,
           accel) reflect real arc curvature: bigger corners earn
           higher corner speeds (v ~ sqrt(accel * r)).

        Returns ``(pts, vajs, corners)`` ready for cjmove: deduped
        waypoints (pts[0] = current pose), one [vel, accel, jerk] and
        one corner per section."""
        try:
            import toppra as ta
            import toppra.constraint as tc
        except ImportError as ex:
            raise RuntimeError(
                "cjmove chain_prm requires toppra (sudo pip3 install toppra)") from ex
        pts = [[float(v) for v in p] for p in points]
        # Physical knot merge — see section_vels: ghost legs from
        # encoder noise must not mint crawl-speed micro corners.
        MIN_LEG = 1.0
        merged = [pts[0]]
        for i, q in enumerate(pts[1:], 1):
            if max(abs(a - b) for a, b in zip(q, merged[-1])) < MIN_LEG:
                if i == len(pts) - 1 and len(merged) > 1:
                    merged[-1] = q
                elif i == len(pts) - 1:
                    merged.append(q)
                continue
            merged.append(q)
        pts = [p for i, p in enumerate(merged)
               if i == 0 or any(abs(a - b) > 1e-9 for a, b in zip(p, merged[i - 1]))]
        n_sec = len(pts) - 1
        if n_sec < 1:
            return pts, [], [], []
        t0 = time.perf_counter()
        a_budget_est = float(accel) / 2.0

        def _turn_dot(A, B, C):
            BA = _fw_unit(_fw_vec(A, B, 1.0, -1.0))
            BC = _fw_unit(_fw_vec(C, B, 1.0, -1.0))
            return _fw_inner(BA, BC)   # ~ -1 = straight through, ~ +1 = reversal

        # 0. CRUISE/BRAKE SPLIT: the firmware runs ONE speed plateau
        # per section, so a long leg ending in a small corner would
        # crawl its whole length at corner speed (bench: 204-unit
        # travel at 75 because of a 9-unit exit corner). Insert a
        # collinear knot at the braking point so the leg becomes
        # cruise-section + brake-section. The knot lies ON the
        # validated segment — geometry unchanged.
        out = [pts[0]]
        for k in range(len(pts) - 1):
            A, B = pts[k], pts[k + 1]
            L = _fw_norm(_fw_vec(B, A, 1.0, -1.0))
            if k + 2 < len(pts):
                leg2 = _fw_norm(_fw_vec(pts[k + 2], B, 1.0, -1.0))
                c_est = min(float(corner_cap), 0.4 * min(L, leg2))
                v_exit_est = math.sqrt(max(a_budget_est * c_est, 1.0))
                brake = max(0.0, (float(vel) ** 2 - v_exit_est ** 2) / (2.0 * a_budget_est))
                tail = brake + max(2.5 * c_est, 10.0)
                if L > tail + 20.0:
                    f = (L - tail) / L
                    out.append(_fw_vec(A, B, 1.0 - f, f))
            out.append(list(B))
        pts = out
        n_sec = len(pts) - 1

        # 1. corners: firmware clamp, host-side; straight-through
        # knots (incl. the synthetic split knots) get NO corner — a
        # collinear pass at speed has no curvature to bound, and the
        # blend curve is degenerate there anyway
        legs = [_fw_norm(_fw_vec(pts[k + 1], pts[k], 1.0, -1.0)) for k in range(n_sec)]
        straight = [_turn_dot(pts[k], pts[k + 1], pts[k + 2]) < -0.999
                    for k in range(n_sec - 1)]
        corners = [0.0 if straight[k] else min(float(corner_cap), 0.4 * min(legs[k], legs[k + 1]))
                   for k in range(n_sec - 1)] + [0.0]

        # 2. creation-time validation of the exact corner cuts
        curves = [None] * n_sec
        check = None
        if padding is not None:
            check_pad = max(0.0, float(padding) - self.PATH_CHECK_PADDING_MARGIN)
            cw, ct = self.workspace.compute_collision_boxes(check_pad)
            base_in_world = list(self.rail_base.pose(anchor="carriage"))
            self.planner.update(scene=self._boxes_to_cubes(cw), gripper=self._boxes_to_cubes(ct), base_in_world=base_in_world)
            check = lambda seg: self.planner.check([list(q) for q in seg], rail_weight=rail_weight)
        for k in range(n_sec - 1):
            if corners[k] <= 0:
                continue
            # Largest SAFE arc, not accept-or-reject: a rejected corner
            # forces a sharp pass-through, which cannot be taken at
            # speed — the section then falls to the crawl floor and the
            # whole hop stalls (bench: two sections at vel 1, 45 s).
            # Shrinking keeps most of the benefit, because a smaller
            # arc hugs the sharp path it is replacing and is far more
            # likely to clear whatever the full-size cut hit.
            r_try = corners[k]
            accepted = None
            for _ in range(4):            # r, r/2, r/4, r/8
                c = _fw_create_curve(pts[k], pts[k + 1], pts[k + 2], r_try)
                if check is None or c["length"] <= 0:
                    accepted = c
                    break
                m = max(2, int(math.ceil(c["length"] / sample)))
                arc = ([_fw_curve_point(c, i * (c["length"] / 2) / m, True) for i in range(m + 1)]
                       + [_fw_curve_point(c, i * (c["length"] / 2) / m, False) for i in range(1, m + 1)])
                if check(arc):
                    accepted = c
                    break
                sharp = [c["curveInitial"], list(pts[k + 1]), c["curveFinal"]]
                if not check(sharp):
                    # the sharp corner is no safer — keep the fillet
                    # (same contract as the approach-leg blends)
                    accepted = c
                    break
                r_try *= 0.5
            if accepted is None:
                corners[k] = 0.0          # nothing fits: sharp pass-through
            else:
                corners[k] = accepted["r"]
                curves[k] = accepted

        # 3. Section speeds from the FIRMWARE's own execution model —
        # no profile compression, no iteration. cont() runs ONE speed
        # plateau per section (ramp from the carried speed toward this
        # section's vel, then hold), so the chain's speed sequence is
        # fully determined by per-section target speeds. Those are
        # computed exactly:
        #   - corner limit: per-joint peak curvature of the REAL curve
        #     (sampled from the motion.cpp port) -> v = sqrt(accel/k)
        #   - velocity limit: per-joint tangent components -> chord cap
        #   - backward pass: braking feasibility at the full accel cap
        #   - forward pass: reachability from the previous speed
        # THE ACCEL BUDGET IS SPLIT — BUT ONLY WHERE ARCS EXIST.
        # Tangential ramps and corner curvature superimpose on the same
        # joints (short sections ramp all the way through their
        # corners), so on any section whose traversal crosses an arc
        # each source gets half the cap — their sum provably never
        # exceeds it. A section with NO arc on either side (both
        # adjacent knots collinear, stopped, or chain ends) has zero
        # centripetal demand over its entire length, so its tangential
        # ramps get the FULL cap: the flat 50/50 split was leaving
        # straight legs to brake at half their real capability
        # (achieved acc ~350/600 on every straight-dominated bench
        # chain). Per-section budgets are assigned after the corner
        # geometry is known (see ``a_tan`` below); the centripetal
        # share for the corner bounds stays accel/2.
        a_budget = float(accel) / 2.0
        sec_lens = []
        sec_line_lens = []
        sec_vel_caps = []
        last_c = None
        for k in range(n_sec):
            paths, d_total, cv = _fw_build_section(pts, corners, k, last_c)
            sec_lens.append(d_total)
            sec_line_lens.append(sum(pd for kind, _, pd in paths if kind == "line"))
            v_cap_sec = float("inf")
            for kind, data, pd in paths:
                m = max(2, int(math.ceil(pd / max(sample, 1e-9))) * 2)
                for i in range(m):
                    q0p = _fw_path_pose(paths, min(i * d_total / m, d_total))
                    q1p = _fw_path_pose(paths, min((i + 1) * d_total / m, d_total))
                    ds = d_total / m
                    comp = max(abs(b - a) / ds for a, b in zip(q0p, q1p)) if ds > 0 else 1.0
                    if comp > 1e-9:
                        v_cap_sec = min(v_cap_sec, float(vel) / comp)
                break  # tangent sampled once over the whole section
            sec_vel_caps.append(v_cap_sec if v_cap_sec < float("inf") else float(vel))
            last_c = cv

        # A knot with no arc is one of two things, never a "slow
        # pass-through": either a COLLINEAR knot (zero turn — the
        # cruise/brake split points), which needs no bound at all, or a
        # real direction change that could not be blended, which is a
        # velocity DISCONTINUITY — no speed makes it valid, so the
        # chain STOPS there and a new one starts. That removes both
        # arbitrary constants the old model needed (a 2.5-degree
        # straight/not-straight cliff and a vel=1 crawl floor) and
        # replaces them with the firmware's own cont=0 semantics.
        stops = [False] * n_sec
        stops[-1] = True                       # a chain always ends stopped
        for k in range(n_sec - 1):
            if curves[k] is None and not straight[k]:
                stops[k] = True

        # Per-section tangential accel budget (see the split comment
        # above): full cap on arc-free sections, half where the
        # traversal crosses an entry or exit arc. Certification below
        # remains authoritative either way — it plays the exact
        # firmware profiles and reduces whatever it measures over cap.
        def _has_arc(k):
            ent = k > 0 and curves[k - 1] is not None and curves[k - 1]["length"] > 0
            exi = k < n_sec - 1 and curves[k] is not None and curves[k]["length"] > 0
            return ent or exi
        a_tan = [a_budget if _has_arc(k) else float(accel) for k in range(n_sec)]

        v_corner = []
        for k in range(n_sec - 1):
            c = curves[k]
            if c is None or c["length"] <= 0:
                # collinear (no turn) or a full stop — either way the
                # knot imposes no curvature bound; the stop constraint
                # in the passes below governs.
                v_corner.append((float("inf"), float("inf")))
                continue
            m = max(8, int(math.ceil(c["length"] / max(c["r"] / 8.0, 0.25))))
            arc = ([_fw_curve_point(c, i * (c["length"] / 2) / m, True) for i in range(m + 1)]
                   + [_fw_curve_point(c, i * (c["length"] / 2) / m, False) for i in range(1, m + 1)])
            ds = c["length"] / (2 * m)
            # Pointwise centripetal bounds, one per corner HALF: the
            # first half is crossed at THIS section's plateau, the
            # second half at the NEXT section's plateau (the ramp
            # between them stays under max(v_k, v_{k+1}), which the
            # two bounds cover region-wise; certification guards the
            # residual).
            b1_sq = b2_sq = float("inf")
            for i in range(1, len(arc) - 1):
                a_, b_, c_ = arc[i - 1], arc[i], arc[i + 1]
                kappa = max(abs(x - 2 * y + z) for x, y, z in zip(a_, b_, c_)) / (ds * ds)
                if kappa <= 1e-12:
                    continue
                if i <= m:
                    b1_sq = min(b1_sq, a_budget / kappa)
                else:
                    b2_sq = min(b2_sq, a_budget / kappa)
            v_corner.append((math.sqrt(max(b1_sq, 1.0)) if b1_sq < float("inf") else float(vel),
                             math.sqrt(max(b2_sq, 1.0)) if b2_sq < float("inf") else float(vel)))

        # target per-section speeds: capped by geometry, the exit
        # corner's first half, and the entry corner's second half
        v_t = []
        for k in range(n_sec):
            v_k = sec_vel_caps[k]
            if k < n_sec - 1:
                v_k = min(v_k, v_corner[k][0])
            if k > 0:
                v_k = min(v_k, v_corner[k - 1][1])
            v_t.append(max(1.0, v_k))
        # Braking room is a section's straight-line portion (bench
        # certification caught braking spilling into the arcs); a
        # section that is all curve falls back to its full length.
        def _room(k):
            r = sec_line_lens[k]
            return r if r > 1e-9 else max(sec_lens[k], 1e-9)
        # backward pass: a section that ENDS AT A STOP must brake to 0
        # within its own room (the firmware gives it a move-stop
        # profile); otherwise braking to the next plateau happens in
        # the next section, before its corner arc begins.
        for k in range(n_sec - 1, -1, -1):
            if stops[k]:
                v_t[k] = min(v_t[k], math.sqrt(2.0 * a_tan[k] * _room(k)))
            else:
                v_t[k] = min(v_t[k], math.sqrt(v_t[k + 1] ** 2 + 2.0 * a_tan[k + 1] * _room(k + 1)))
        # forward pass: reachability from the carried speed — a stop
        # resets the carry to zero.
        v_prev = 0.0
        for k in range(n_sec):
            v_t[k] = min(v_t[k], math.sqrt(v_prev ** 2 + 2.0 * a_tan[k] * _room(k)))
            v_prev = 0.0 if stops[k] else v_t[k]

        # binding-constraint tag per section (log diagnosis):
        #   v = geometry vel cap, c = own exit corner, e = entry
        #   corner (previous knot), b = braking for a slower section
        #   ahead, r = reachability ramp from the carried speed
        bind = []
        for k in range(n_sec):
            v = v_t[k]
            tol = 1e-6
            if abs(v - sec_vel_caps[k]) < tol:
                bind.append("v")
            elif k < n_sec - 1 and abs(v - v_corner[k][0]) < tol:
                bind.append("c")
            elif k > 0 and abs(v - v_corner[k - 1][1]) < tol:
                bind.append("e")
            else:
                bind.append("b" if k < n_sec - 1 and v_t[k] > v_t[k + 1] else "r")
        legs = [round(_fw_norm(_fw_vec(pts[k + 1], pts[k], 1.0, -1.0))) for k in range(n_sec)]

        vajs = [[v_t[k], a_tan[k], float(jerk)] for k in range(n_sec)]

        # CERTIFICATION IS AUTHORITATIVE. The analytic model above is a
        # fast estimate; this plays the exact firmware profiles over the
        # exact firmware geometry and measures what the robot would
        # actually command. Where the two disagree the measurement wins:
        # offending sections are reduced (deterministically, by the
        # measured violation ratio) and re-measured. The system must not
        # be able to emit a chain it knows exceeds the caps — a warning
        # was never a guarantee.
        TOL = 1.05
        report = []
        for _ in range(8):
            report = self._fw_verify_chain(pts, vajs, corners, stops)
            worst = 1.0
            for k, (jv, ja_entry, ja_body) in enumerate(report):
                rv = jv / float(vel)
                ra_e = ja_entry / float(accel)
                ra_b = ja_body / float(accel)
                if rv > TOL:
                    vajs[k][0] = max(1.0, vajs[k][0] / rv)
                    worst = max(worst, rv)
                if ra_e > TOL and k > 0:
                    # Entry-half acceleration is the VECTOR SUM of two
                    # sources: centripetal, set by the speed carried in
                    # from the previous section, and tangential, set by
                    # THIS section's accel param when its ramp runs
                    # through the entry curve. Reducing only the former
                    # cannot converge when the latter dominates — the
                    # loop then drives speed to the floor and still
                    # fails (bench: vels [.., 1, ..] then DEGRADED).
                    vajs[k - 1][0] = max(1.0, vajs[k - 1][0] / math.sqrt(min(ra_e, 4.0)))
                    vajs[k][1] = max(1.0, vajs[k][1] / ra_e)
                    worst = max(worst, ra_e)
                if ra_b > TOL:
                    # body: TWO regimes, opposite fixes. When the speed
                    # carried in exceeds this section's own target, the
                    # section is BRAKING and the violation is overshoot
                    # into its exit corner — the entry speed is the
                    # driver, and cutting this section's accel would cut
                    # the very braking authority that resolves it (the
                    # loop then diverges: measured accel RISES as params
                    # fall, runs to the floor, DEGRADED — bench: the
                    # tool-rack -> source-rack travel). Reduce the
                    # upstream speed instead and keep the accel.
                    # Otherwise the violation is this section's own
                    # doing (tangential ramp + own-corner centripetal):
                    # shrink its params by the measured ratio.
                    v_in = vajs[k - 1][0] if k > 0 and not stops[k - 1] else 0.0
                    if v_in > vajs[k][0]:
                        vajs[k - 1][0] = max(1.0, vajs[k - 1][0] / math.sqrt(min(ra_b, 4.0)))
                    else:
                        vajs[k][1] = max(1.0, vajs[k][1] / ra_b)
                        vajs[k][0] = max(1.0, vajs[k][0] / math.sqrt(min(ra_b, 4.0)))
                    worst = max(worst, ra_b)
            if worst <= TOL:
                break
        else:
            # Did not converge: DEGRADE to the always-valid form —
            # stop at every knot, no blends. A stop-to-stop section is
            # within caps by construction, so the guarantee holds even
            # when the model cannot. Loud, because it means the
            # estimate is wrong somewhere worth fixing.
            corners = [0.0] * n_sec
            stops = [True] * n_sec
            # Recompute the speeds for THIS geometry — do not inherit
            # what the failed search left behind. Every section is now
            # a clean stop-to-stop straight line, so its speed is the
            # geometry cap limited by the room it has to accelerate and
            # brake in; carrying the crushed values forward would leave
            # the fallback crawling (bench: 94 s at vels [223, 1, 15]).
            # Stop-to-stop straight lines have no arcs at all, so the
            # tangential budget is the FULL cap here.
            vajs = []
            for k in range(n_sec):
                leg = max(legs[k], 1e-9)
                v_k = min(sec_vel_caps[k], math.sqrt(float(accel) * leg))
                vajs.append([max(1.0, v_k), float(accel), float(jerk)])
            report = self._fw_verify_chain(pts, vajs, corners, stops)
            print(f"[traj] DEGRADED to stop-at-every-knot: certification would not "
                  f"converge within caps (vel {vel:.0f}, acc {accel:.0f}) — model gap, report it")
        jv_max = max(r[0] for r in report) if report else 0.0
        ja_max = max(max(r[1], r[2]) for r in report) if report else 0.0
        n_stop = sum(1 for i, s in enumerate(stops) if s and i < n_sec - 1)
        print(f"[traj] {len(pts)} pts -> {n_sec} {label} sections"
              f"{f' ({n_stop} internal stop)' if n_stop else ''}, "
              f"vels {[round(v[0]) for v in vajs]}, corners {[round(c) for c in corners]}, "
              f"legs {legs}, bind {bind}, "
              f"certified: joint vel {jv_max:.0f}/{vel:.0f}, acc {ja_max:.0f}/{accel:.0f}, "
              f"solved in {(time.perf_counter() - t0) * 1000:.0f} ms")
        return pts, vajs, corners, stops

    def _fw_verify_chain(self, pts, vajs, corners, stops=None, dt=0.004):
        """Evaluate the chain with the FIRMWARE's exact math — the
        ported cont()/createProfile section profiles with carried
        velocity over the ported corner geometry — and return, per
        section, the maximum per-joint |velocity| and |acceleration|
        the robot would actually command. No approximation: this IS
        the execution model, sampled at ``dt``."""
        if getattr(self, "_fw_eval", None) is None:
            self._fw_eval = SimulationAPI()
        sim = self._fw_eval
        v_tick, a_tick = 0.0, 0.0
        last_curve = None
        report = []
        n_sec = len(pts) - 1
        for k in range(n_sec):
            paths, d_total, curve = _fw_build_section(pts, corners, k, last_curve)
            to_stop = stops[k] if stops is not None else (k == n_sec - 1)
            prof = sim._fw_profile_cont(vajs[k][2], vajs[k][1], vajs[k][0],
                                        d_total, v_tick, a_tick,
                                        to_stop=to_stop)
            dur = sum(prof["ticks"]) / float(sim.FREQ)
            m = max(2, int(math.ceil(dur / dt)))
            poses = []
            for i in range(m + 1):
                q, _, _ = sim.traverse(prof["jerks"], prof["ticks"],
                                       q0=0.0, v0=v_tick, a0=a_tick,
                                       t=min(i * dur / m, dur))
                poses.append(_fw_path_pose(paths, min(q, d_total)))
            step_t = dur / m if m else dt
            entry_len = paths[0][2] if paths and paths[0][0] == "curve2" else 0.0
            jv = ja_entry = ja_body = 0.0
            prev_v = None
            q_marks = []
            for i in range(m + 1):
                qi, _, _ = sim.traverse(prof["jerks"], prof["ticks"],
                                        q0=0.0, v0=v_tick, a0=a_tick,
                                        t=min(i * dur / m, dur))
                q_marks.append(min(qi, d_total))
            for i, (a, b) in enumerate(zip(poses, poses[1:])):
                v = [(y - x) / step_t for x, y in zip(a, b)]
                jv = max(jv, max(abs(x) for x in v))
                if prev_v is not None:
                    acc_j = max(abs(y - x) / step_t for x, y in zip(prev_v, v))
                    if q_marks[i] <= entry_len:
                        ja_entry = max(ja_entry, acc_j)
                    else:
                        ja_body = max(ja_body, acc_j)
                prev_v = v
            report.append((jv, ja_entry, ja_body))
            v_tick, a_tick = (0.0, 0.0) if to_stop else (prof["vFinal"], 0.0)
            last_curve = curve
        return report

    @staticmethod
    def _boxes_to_cubes(boxes):
        out = []
        for box in boxes:
            try:
                pose = box["pose"]
                scale = box["scale"]
                out.append(
                    Planner.create_cube(list(pose), [scale[0], scale[1], scale[2]])
                )
            except Exception:
                # If a malformed box slips through, skip it rather than failing the whole move
                continue
        return out

    def motion_plan(self, joint, seed=1234, padding=10, gravity_vec=None, gravity_thr=5.0, planner="aitstar", time_limit_sec=10.0, rail_weight=0.004):

        """
        Collision-aware joint move:
        - Build collision scene from workspace boxes
        - Update planner with scene/base_in_world/aux_dir/aux_limit
        - Plan from current joints -> target `joint`
        - Execute the returned waypoint list via repeated jmove()

        Returns:
            2  success
            -1 planning failure / empty path
            otherwise: whatever robot_api.jmove returns if it fails
        """

        gravity = False
        if gravity_vec is not None:
            gravity = True

        # -------------------------
        # Build collision scene
        # -------------------------

        scene, tool, tool_boxes = [], [], []
        if hasattr(self.workspace, "compute_collision_boxes"):
            world_boxes, tool_boxes = self.workspace.compute_collision_boxes(padding)
            scene = self._boxes_to_cubes(world_boxes)
            tool = self._boxes_to_cubes(tool_boxes)

        # -------------------------
        # Planner update args
        # -------------------------
        # base_in_world is derived from the rail_base pose in world coordinates.
        # If no rail exists, fall back to robot_A0 (robot base link).
        base_solid = self.rail_base

        base_in_world = list( self.rail_base.pose(anchor="carriage"))


        # -------------------------
        # Plan and execute
        # -------------------------
        start_full = list(self.robot_api.joint())
        goal = list(joint)

        # planner.plan(start, goal): start should match goal dimensionality
        start = start_full[:len(goal)]

        # j5 lives on a circle; the planner's joint space is the
        # canonical (-180, 180]. A wound wrist (j5_infinite, after
        # capping) sits outside it — and OMPL silently CLAMPS an
        # out-of-bounds start to the limit, so every waypoint comes
        # back at ±179 and executing the path unwinds the wrist by
        # whole turns. The wrist roll is mod-360 symmetric, so the
        # geometry of the canonical query is EXACTLY the wound one:
        # plan canonical, then re-carry the turns onto the result.
        # (The goal's carry equals the start's by construction — every
        # target is unwrapped against the live joints, delta ≤ 360 —
        # so subtracting the same carry lands it in canonical range;
        # a mismatched carry fails the plan loudly instead of moving
        # wrong.) Identity on a limited wrist (carry = 0).
        j5_turns = 0
        if len(goal) > 5:
            j5_turns = round((float(start[5]) - self._wrap180(start[5])) / 360.0)
            if j5_turns:
                start = list(start)
                goal = list(goal)
                start[5] = self._wrap180(start[5])
                goal[5] = float(goal[5]) - 360.0 * j5_turns

        def _recarry(path):
            """Wound frame back onto a canonical-space path."""
            if not j5_turns or path is None or not len(path):
                return path
            out = [list(p) for p in path]
            for p in out:
                if len(p) > 5:
                    p[5] = float(p[5]) + 360.0 * j5_turns
            return out

        # -------------------------
        # Path cache (core_path.json) — hit = return (validated at creation)
        # -------------------------
        if self._path_cache is None:
            self._path_cache_init()
        path_sig = self._path_tool_sig(tool_boxes)
        if path_sig is not None:
            cached = self._path_cache_get(start, goal, path_sig)
            if cached is not None:
                return _recarry(cached)

        start_time = time.perf_counter()

        self.planner.update(scene=scene, gripper=tool, base_in_world=list(base_in_world))
        # Loud diagnosis for a doomed solve: a start inside the padded
        # envelope means the PREVIOUS motion ended inside a box (its
        # exit should have auto-lifted) — the planner would silently
        # burn its whole budget and report NO PATH.
        try:
            if not self.planner.check([list(start), list(start)], rail_weight=rail_weight):
                print("[plan] START is inside the collision envelope — the previous "
                      "motion ended inside a box (exit not lifted?)")
        except Exception:
            pass

        # -------------------------
        # ONE solve pipeline: direct connection, else OMPL. Either way
        # the result is recorded below and replays from the cache.
        # -------------------------
        # Direct: if the straight joint segment start→goal clears the
        # padded envelope, it IS the path — the same segment a bare
        # jmove would drive, now collision-certified. OMPL's informed
        # planners only SOMETIMES converge to it within budget; this
        # makes it a guarantee.
        # pp branch: AIT* @ 10s is the platform default for the OMPL
        # fallback (requires the pp path_planning build — planner
        # selection + honored time budget + GIL release).
        # rail_weight=0.004 makes rail travel ~2.5x cheaper than stock
        # in the path-length metric so paths slide the bench instead of
        # contorting the arm.
        gv = gravity_vec if gravity_vec is not None else [0, 0, 1]
        res = None
        try:
            if self.planner.check([list(start), list(goal)], gravity=gravity,
                                  gravity_vec=gv, gravity_thr=gravity_thr,
                                  rail_weight=rail_weight):
                res = [list(start), list(goal)]
        except Exception:
            res = None
        if res is None:
            res = self.planner.plan(start, goal, seed=seed, gravity=gravity, gravity_vec=gravity_vec, gravity_thr=gravity_thr, planner=planner, time_limit_sec=time_limit_sec, rail_weight=rail_weight)

        end_time = time.perf_counter()
        execution_time = end_time - start_time

        # The planner returns numpy waypoints. Convert to plain Python
        # floats at the boundary — motion_plan's contract is a list of
        # joint lists. Leaking ndarrays downstream breaks the real SDK:
        # dorna2's play() does ``_msg[key] == None`` on the command dict,
        # which is an ambiguous elementwise comparison on an array (and
        # the command must be JSON-serializable anyway).
        if res is not None and len(res):
            res = [[float(v) for v in p] for p in res]
            # Sparse execution: decimate to the essential corners so the
            # smove spline flows instead of hugging the dense polyline.
            # The collision gate runs PER SEGMENT inside the decimation
            # (see _decimate_path): a failing span splits and its halves
            # retry, so free-space travel goes sparse while only the
            # spans that hug an obstacle stay dense. The gate checks a
            # slightly slimmer envelope (padding - margin): OMPL
            # validates sampled points along segments, so the solved
            # path may graze the full envelope within one resolution
            # step — the margin absorbs that. This is the ONE
            # validation a stored row ever gets (creation-time).
            if len(res) > 2:
                try:
                    check_pad = max(0.0, padding - self.PATH_CHECK_PADDING_MARGIN)
                    cw, ct = self.workspace.compute_collision_boxes(check_pad)
                    self.planner.update(scene=self._boxes_to_cubes(cw), gripper=self._boxes_to_cubes(ct), base_in_world=list(base_in_world))
                    seg_ok = lambda seg: self.planner.check(seg, gravity=gravity, gravity_vec=gv,
                                                            gravity_thr=gravity_thr, rail_weight=rail_weight)
                    res = self._decimate_path(res, self.PATH_DECIMATE_EPS, check=seg_ok)
                except Exception:
                    pass  # keep the dense path
            # Record EVERY solved hop — direct and OMPL alike. One
            # pipeline, one record: the next call replays from the
            # cache instead of re-deriving.
            if path_sig is not None:
                self._path_cache_put(start, goal, path_sig, res)
        else:
            print(f"[plan] {planner}@{time_limit_sec:g}s: NO PATH in {execution_time:.1f}s "
                  f"start={[round(v, 1) for v in start]} goal={[round(v, 1) for v in goal]}")

        return _recarry(res)

    def check_collision(self, j, internal=True):
        scene = []
        tool = []
        padding = 0
        if hasattr(self.workspace, "compute_collision_boxes"):
            world_boxes, tool_boxes = self.workspace.compute_collision_boxes(padding) 
            for box in world_boxes:
                try:
                    pose = box["pose"]
                    scale = box["scale"]
                    scene.append(
                        Planner.create_cube(pose, [scale[0], scale[1], scale[2]])
                    )
                except Exception:
                    # If a malformed box slips through, skip it rather than failing the whole move
                    continue

            for box in tool_boxes:
                try:
                    pose = box["pose"]
                    scale = box["scale"]
                    tool.append(
                        Planner.create_cube(pose, [scale[0], scale[1], scale[2]])
                    )
                except Exception:
                    # If a malformed box slips through, skip it rather than failing the whole move
                    continue

        #print("world box: ", world_boxes)
        #print("tool box: ", tool_boxes)
        # -------------------------
        # Planner update args
        # -------------------------
        # base_in_world is derived from the rail_base pose in world coordinates.
        # If no rail exists, fall back to robot_A0 (robot base link).
        base_solid = self.rail_base

        base_in_world = list( self.rail_base.pose(anchor="carriage"))

        self.planner.update(
            scene=scene,
            gripper=tool,
            base_in_world=list(base_in_world)
        )

        # State-space queries speak canonical j5 (same rule as IK
        # candidates and motion_plan): a wound wrist (j5_infinite)
        # would trip the planner's ±179 limit check as a phantom
        # "j5_limit" hit even though the geometry — roll is mod-360
        # symmetric — is identical. Identity on a limited wrist.
        j = list(j)
        if len(j) > 5:
            j[5] = self._wrap180(j[5])

        return self.planner.check_collision(j,internal)



# ── lmove path math ─────────────────────────────────────────────────────
# The tested lmove interpolation, shared by SimulationAPI.lmove (which
# executes it tick by tick) and Core.lmove_points (which samples it as
# smove waypoints): straight TCP line in Cartesian space, linear wrist
# (j3..j5) + rail interpolation, nearest-branch closed-form IK chained
# point to point.

_PI = np.pi
_PI2 = np.pi / 2.0

_DORNA_TA_DH = {
    "a":     np.array([0.0, 80.0, 210.0, 0.0, 0.0, 0.0, 0.0], dtype=float),
    "d":     np.array([230.018, 0.0, 0.0, 41.80, 175.0, -89.0, 35.0], dtype=float),
    "alpha": np.array([0.0, _PI2, 0.0, _PI2, _PI2, _PI2, 0.0], dtype=float),
    "delta": np.array([0.0, 0.0, 0.0, _PI2, _PI, _PI, 0.0], dtype=float),
    "limit_n": np.array([-185.0, -150.0, -160.0, -175.0, -185.0, -180.0], dtype=float),
    "limit_p": np.array([ 175.0,  210.0,  200.0,  185.0,  175.0,  180.0], dtype=float),
}


def _dh_T_i(joint, i):
    delta = _DORNA_TA_DH["delta"][i]
    alpha = _DORNA_TA_DH["alpha"][i]
    ai    = _DORNA_TA_DH["a"][i]
    di    = _DORNA_TA_DH["d"][i]

    ct = np.cos(joint + delta)
    st = np.sin(joint + delta)
    ca = np.cos(alpha)
    sa = np.sin(alpha)

    res = np.array([
        [ ct,     -st * ca,  st * sa,  ai * ct],
        [ st,      ct * ca, -ct * sa,  ai * st],
        [ 0.0,          sa,       ca,      di ],
        [ 0.0,         0.0,      0.0,     1.0 ]
    ], dtype=float)

    return res


def _solve_cs_equation(aa, bb, cc, i):
    # solving equation: aa + bb*cos(theta) + cc*sin(theta) = 0
    delta = cc * cc * (-aa * aa + bb * bb + cc * cc)

    if delta < 0:
        return None
    if bb == 0.0 and cc == 0.0:
        return None
    if bb == 0.0:
        s1 = -aa / cc
        if abs(s1) > 1.0:
            return None
        c1 = np.sqrt(1.0 - s1 * s1)
        if i == 1:
            c1 = -c1
        return c1, s1
    if cc == 0.0:
        c1 = -aa / bb
        if abs(c1) > 1.0:
            return None
        s1 = np.sqrt(1.0 - c1 * c1)
        if i == 1:
            s1 = -s1
        return c1, s1

    if i == 0:
        c1 = (-aa * bb + np.sqrt(delta)) / (bb * bb + cc * cc)
    else:
        c1 = (-aa * bb - np.sqrt(delta)) / (bb * bb + cc * cc)

    s1 = -(aa + bb * c1) / cc

    return c1, s1


def _clamp(x, lo, hi):
    return lo if x < lo else hi if x > hi else x


def _wrap_to_limits(q, qmin, qmax):
    # shift by 2π until within [qmin, qmax)
    two_pi = 2.0 * np.pi
    while q >= qmax:
        q -= two_pi
    while q < qmin:
        q += two_pi
    return q


def _angle_distance(a, b):
    # shortest angular distance between angles a and b (radians)
    d = a - b
    d = (d + np.pi) % (2.0 * np.pi) - np.pi
    return d


def _joint_space_distance(q, qref):
    # simple Euclidean norm of wrapped angular differences over 6 joints
    diffs = [_angle_distance(q[k], qref[k]) for k in range(6)]
    return float(np.linalg.norm(diffs))


def _xyzj_to_joints(xyzj, curJoints, tool_pose, kinematic):

    T_tool = dorna2.pose.xyzabc_to_T(tool_pose)
    T345 = np.eye(4)
    temp1 = None
    temp2 = None

    xyz = np.array([xyzj[0], xyzj[1], xyzj[2]], dtype=float)
    j345 = np.deg2rad([xyzj[3], xyzj[4], xyzj[5]])

    for i in range(4, 8):
        if i < 7:
            temp1 = _dh_T_i(j345[i - 4], i)
        else:
            temp1 = T_tool
        temp2 = T345.copy()
        T345 = temp2 @ temp1

    lx = T345[0, 3]
    ly = T345[2, 3]
    lz = T345[1, 3]
    lxy = np.sqrt(lx * lx + ly * ly)

    j0 = 0.0
    j1 = 0.0
    j2 = 0.0
    j3 = j345[0]
    j4 = j345[1]
    j5 = j345[2]

    a2 = _DORNA_TA_DH["a"][1]
    a3 = _DORNA_TA_DH["a"][2]
    d1 = _DORNA_TA_DH["d"][0]
    d4 = _DORNA_TA_DH["d"][3]
    d5 = _DORNA_TA_DH["d"][4]
    d6 = _DORNA_TA_DH["d"][5]
    d7 = _DORNA_TA_DH["d"][6]

    jointMin_ = np.deg2rad(_DORNA_TA_DH["limit_n"][:6])
    jointMax_ = np.deg2rad(_DORNA_TA_DH["limit_p"][:6])

    num_res = 0
    res = []

    rhoxyz = float(np.hypot(xyz[0], xyz[1]))
    nz = xyz[2] - d1
    lz = T345[1, 3] + d4

    T00, T01, T02, T03 = T_tool[0][0], T_tool[0][1], T_tool[0][2], T_tool[0][3]
    T10, T11, T12, T13 = T_tool[1][0], T_tool[1][1], T_tool[1][2], T_tool[1][3]
    T20, T21, T22, T23 = T_tool[2][0], T_tool[2][1], T_tool[2][2], T_tool[2][3]

    for idx_j0 in range(2):
        for idx_j2 in range(2):
            j0 = np.arctan2(xyz[1], xyz[0])

            if abs(rhoxyz) < abs(lz):
                continue
            nxy = np.sqrt(rhoxyz * rhoxyz - lz * lz)

            dj0 = np.arctan2(lz, nxy)

            if (idx_j0 % 2) == 0:
                j0 += dj0
            else:
                j0 += -dj0 + np.pi

            j0 = _wrap_to_limits(j0, jointMin_[0], jointMax_[0])

            if idx_j0 != 0:
                nxy = -nxy

            nxy += -a2
            dis = float(np.hypot(nxy, nz))

            if dis > a3 + lxy + 1e-5:
                continue

            j1 = np.arctan2(nz, nxy)
            arg = (a3 * a3 + dis * dis - lxy * lxy) / (2.0 * a3 * dis)
            phi = np.arccos(_clamp(arg, -1.0, 1.0))

            if idx_j2 == 0:
                j1 += phi
            else:
                j1 += -phi

            j1 = _wrap_to_limits(j1, jointMin_[1], jointMax_[1])

            for idx_j3 in range(2):
                cj2_sj2 = _solve_cs_equation(
                    d1 + a3 * np.sin(j1) - xyz[2],
                    np.sin(j1) * (d5 + (d7 + T23) * np.cos(j4) - T03 * np.cos(j5) * np.sin(j4) + T13 * np.sin(j4) * np.sin(j5))
                    + np.cos(j1) * (-np.sin(j3) * (d6 + T13 * np.cos(j5) + T03 * np.sin(j5))
                    + np.cos(j3) * (T03 * np.cos(j4) * np.cos(j5) + (d7 + T23) * np.sin(j4) - T13 * np.cos(j4) * np.sin(j5))),
                    np.cos(j1) * (d5 + (d7 + T23) * np.cos(j4) - T03 * np.cos(j5) * np.sin(j4) + T13 * np.sin(j4) * np.sin(j5))
                    + np.sin(j1) * (np.sin(j3) * (d6 + T13 * np.cos(j5) + T03 * np.sin(j5))
                    - np.cos(j3) * (T03 * np.cos(j4) * np.cos(j5) + (d7 + T23) * np.sin(j4) - T13 * np.cos(j4) * np.sin(j5))),
                    idx_j3
                )

                if cj2_sj2 is None:
                    continue

                cj2, sj2 = cj2_sj2
                j2 = np.arctan2(sj2, cj2)
                j2 = _wrap_to_limits(j2, jointMin_[2], jointMax_[2])

                res.append([j0, j1, j2, j3, j4, j5])
                num_res += 1

    best_ans_idx = -1
    best_ans_dis = 1e9

    current_joint = np.deg2rad([
        curJoints[0], curJoints[1], curJoints[2],
        curJoints[3], curJoints[4], curJoints[5]
    ])

    for i in range(num_res):

        kinematic.set_tcp_xyzabc(tool_pose)
        xyz_tmp = kinematic.fw(np.rad2deg(res[i]))

        res_xyz = np.array([xyz_tmp[0] - xyz[0], xyz_tmp[1] - xyz[1], xyz_tmp[2] - xyz[2]], dtype=float)
        l = float(res_xyz @ res_xyz)
        if l > 1e-4:
            continue

        dis_to_current = _joint_space_distance(res[i], current_joint)

        if dis_to_current < best_ans_dis and (current_joint[2] - 0.03) * res[i][2] > 0.0:
            best_ans_idx = i
            best_ans_dis = dis_to_current

    if best_ans_idx == -1:
        return None

    out = np.zeros(8, dtype=float)
    out[:6] = np.rad2deg(res[best_ans_idx][:6])
    out[6] = xyzj[6]
    out[7] = xyzj[7]

    # we do a sanity check on the ouput.
    # first we find the x,y,z of the output joints
    kinematic.set_tcp_xyzabc(tool_pose)
    fk = kinematic.fw(out[:6])
    dx = fk[0] - xyzj[0]
    dy = fk[1] - xyzj[1]
    dz = fk[2] - xyzj[2]
    err = math.sqrt(dx*dx + dy*dy + dz*dz)
    return out


def lmove_path_points(kinematic, joint_from, joint_to, tool_pose=[0, 0, 0, 0, 0, 0], step=5.0):
    """Sample the lmove path joint_from → joint_to, one point every
    ``step`` (mm in the xyzj metric) — the SAME tested interpolation
    the sim lmove executes, chained sample to sample for branch
    continuity. Returns a list of 8-dof joint lists ending exactly at
    joint_to, or None on IK failure."""
    cur = list(joint_from)
    tgt = list(joint_to)
    kinematic.set_tcp_xyzabc(tool_pose)
    cur_xyz = kinematic.fw(cur[0:6])
    tgt_xyz = kinematic.fw(tgt[0:6])

    cur_xyzj = [cur_xyz[0], cur_xyz[1], cur_xyz[2], cur[3], cur[4], cur[5], cur[6], cur[7]]
    tgt_xyzj = [tgt_xyz[0], tgt_xyz[1], tgt_xyz[2], tgt[3], tgt[4], tgt[5], tgt[6], tgt[7]]

    delta = [t - c for c, t in zip(cur_xyzj, tgt_xyzj)]
    d = math.sqrt(sum(di * di for di in delta))
    if d <= 0.0:
        return [tgt]

    n = max(1, math.ceil(d / step))
    points = []
    prev = cur
    for k in range(1, n + 1):
        if k == n:
            points.append([float(v) for v in tgt])  # exact final value
            break
        xyzj = [c + di * k / n for c, di in zip(cur_xyzj, delta)]
        J = _xyzj_to_joints(xyzj, prev, tool_pose, kinematic)
        if J is None:
            return None
        J = [float(v) for v in J]
        points.append(J)
        prev = J
    return points


def blend_path_points(kinematic, points, junctions, radius, tool_pose=[0, 0, 0, 0, 0, 0], step=5.0, check=None):
    """Round the corners of a fused waypoint path with quadratic-Bezier
    fillets, blended directly in JOINT space.

    ``check(waypoints) -> bool`` (a collision gate, run ONCE at
    creation) enforces one rule per fillet: THE ARC MAY NOT INTRODUCE
    A COLLISION THE SHARP CORNER DID NOT HAVE. An arc that fails the
    gate is compared against the sharp span it replaces — if the sharp
    span passes, the corner stays sharp (the fillet would cut into an
    obstacle, e.g. a travel corner the planner routed around); if the
    sharp span fails too, the fillet goes in (approach corners
    legitimately live inside station envelopes — the fixed collision
    boxes are the project's contract there, same as the unchecked
    behavior). check=None blends unconditionally.

    At each junction index C: walk back ``r_in`` mm (Cartesian arc
    length, measured by FK — the radius is spatial) along the incoming
    polyline to A, forward ``r_out`` to B, and replace the in-between
    waypoints with samples of
    Q(t) = (1-t)^2 A + 2t(1-t) C + t^2 B over the JOINT vectors.
    The arms are ASYMMETRIC and greedy: each side takes up to the full
    available run (to the neighbouring junction or path end, minus one
    step) — a 100/60 corner sweeps 75/55 instead of 30/30. When two
    corners share a leg their claims scale down proportionally, so
    blends never overlap and endpoints always survive. The Bezier is
    tangent at A and B regardless of arm lengths (G1 either way).
    FK is smooth, so joint-space G1 is Cartesian-smooth too; over a
    ~20 mm fillet the deviation from the ideal Cartesian arc is
    second-order. No IK anywhere — joint interpolation cannot branch-
    flip and has no failure mode. The fillet stays within r of its
    corner and cuts INSIDE the turn, so with entries auto-lifted
    clear of the station envelope the arc inherits that clearance —
    the fixed collision boxes are the project's contract, no re-check.
    """
    if radius <= 0 or not junctions or len(points) < 3:
        return None

    kinematic.set_tcp_xyzabc(tool_pose)
    pts = [[float(v) for v in p] for p in points]

    # Geometry lives in arm-FK xyz PLUS the rail dims (mm) — the same
    # metric the lmove engine samples in. fw(arm) alone is blind to
    # rail-carried motion (a rail-dominant approach reads as
    # zero-length segments and no corners).
    def fk_xyz(J):
        f = kinematic.fw(J[:6])
        return [f[0], f[1], f[2]] + [float(v) for v in J[6:]]

    def xyz_dist(a, b):
        return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))

    X = [fk_xyz(p) for p in pts]
    js = sorted({int(j) for j in junctions if 0 < int(j) < len(pts) - 1})

    def locate(lengths, r):
        # index + fraction along a run of segment lengths at arc r
        acc = 0.0
        for n, L in enumerate(lengths):
            if L > 0 and acc + L >= r:
                return n, (r - acc) / L
            acc += L
        return len(lengths) - 1, 1.0

    # Pass 1 — greedy per-side radii: the full run to the neighbouring
    # junction / path end, minus one step (endpoints + neighbours are
    # never consumed).
    r_in, r_out = {}, {}
    for k, j in enumerate(js):
        lo = js[k - 1] if k > 0 else 0
        hi = js[k + 1] if k + 1 < len(js) else len(pts) - 1
        avail_in = sum(xyz_dist(X[i], X[i - 1]) for i in range(j, lo, -1)) - step
        avail_out = sum(xyz_dist(X[i], X[i + 1]) for i in range(j, hi)) - step
        r_in[j] = min(float(radius), max(0.0, avail_in))
        r_out[j] = min(float(radius), max(0.0, avail_out))

    # Pass 2 — shared-leg conflicts: two corners claiming one leg scale
    # down proportionally instead of everyone paying a half-tax.
    for k in range(len(js) - 1):
        a, b = js[k], js[k + 1]
        leg = sum(xyz_dist(X[i], X[i + 1]) for i in range(a, b))
        want = r_out[a] + r_in[b]
        if want > leg - step and want > 0:
            f = max(0.0, (leg - step)) / want
            r_out[a] *= f
            r_in[b] *= f

    # Pass 3 — splice, descending: each splice touches only indices
    # at/after its own window, so earlier junction indices stay valid.
    for k in range(len(js) - 1, -1, -1):
        j = js[k]
        lo = js[k - 1] if k > 0 else 0
        hi = js[k + 1] if k + 1 < len(js) else len(pts) - 1
        hi = min(hi, len(pts) - 1)   # a higher splice may have shrunk pts
        ri, ro = r_in[j], r_out[j]
        if ri < step or ro < step:
            continue  # too tight to blend — keep the sharp corner

        Lin = [xyz_dist(X[i], X[i - 1]) for i in range(j, lo, -1)]
        Lout = [xyz_dist(X[i], X[i + 1]) for i in range(j, hi)]
        na, ta = locate(Lin, ri)   # A between pts[j-na] and pts[j-na-1]
        nb, tb = locate(Lout, ro)  # B between pts[j+nb] and pts[j+nb+1]
        ia, ib = j - na, j + nb
        A = [u + (v - u) * ta for u, v in zip(pts[ia], pts[ia - 1])]
        B = [u + (v - u) * tb for u, v in zip(pts[ib], pts[ib + 1])]
        C = pts[j]

        n = max(2, math.ceil((ri + ro) / step))
        blend_pts = []
        for s in range(n + 1):
            t = s / n
            blend_pts.append([
                (1 - t) ** 2 * a + 2 * t * (1 - t) * c + t ** 2 * b
                for a, c, b in zip(A, C, B)
            ])

        if check is not None and not check(blend_pts):
            # the fillet may not introduce a collision the sharp
            # corner didn't have (see docstring)
            sharp_span = [A] + [list(p) for p in pts[ia:ib + 1]] + [B]
            if check(sharp_span):
                continue  # arc cuts into an obstacle — keep the corner

        pts = pts[:ia] + blend_pts + pts[ib + 1:]
        X = [fk_xyz(p) for p in pts]

    return pts


def blend_sharp_corners(kinematic, points, radius, tool_pose=[0, 0, 0, 0, 0, 0], step=5.0, from_idx=1, angle_deg=20.0, check=None):
    """Auto-detect sharp corners and fillet them all — dumb on purpose.

    A corner is any waypoint (index >= from_idx) where the TCP
    direction changes by more than ``angle_deg`` between the incoming
    and outgoing segments. ALL corners of a fused path qualify —
    travel and approach alike: since per-segment decimation the
    planner's portion is a sparse polyline whose corners are as sharp
    as anyone's, and the smove spline carries full speed through
    anything unfilleted. Corners are found by GEOMETRY, not by
    bookkeeping — new motion types are covered automatically.
    ``check`` gates each fillet at creation (see blend_path_points:
    an arc may not introduce a collision the sharp corner didn't
    have).

    Returns the blended list (unchanged when nothing qualifies), or
    None on failure (caller keeps the sharp path)."""
    if radius <= 0 or len(points) < 3:
        return None

    kinematic.set_tcp_xyzabc(tool_pose)

    # arm-FK xyz + rail dims — see blend_path_points: fw(arm) alone is
    # blind to rail-carried motion.
    def fk_xyz(J):
        f = kinematic.fw(J[:6])
        return [f[0], f[1], f[2]] + [float(v) for v in J[6:]]

    X = [fk_xyz(p) for p in points]
    thr = math.cos(math.radians(angle_deg))
    corners = []
    for i in range(max(1, int(from_idx)), len(points) - 1):
        v1 = [b - a for a, b in zip(X[i - 1], X[i])]
        v2 = [b - a for a, b in zip(X[i], X[i + 1])]
        n1 = math.sqrt(sum(v * v for v in v1))
        n2 = math.sqrt(sum(v * v for v in v2))
        if n1 < 1.0 or n2 < 1.0:
            continue  # degenerate/tiny segments — nothing to blend
        cosang = sum(a * b for a, b in zip(v1, v2)) / (n1 * n2)
        if cosang < thr:
            corners.append(i)
    if not corners:
        return [list(map(float, p)) for p in points]
    return blend_path_points(kinematic, points, corners, radius, tool_pose, step, check=check)


# ── Firmware-fidelity cont-chain engine ─────────────────────────────
# Direct Python ports from the controller firmware (server/motion.cpp)
# so the sim executes cjmove/clmove chains EXACTLY like the robot:
#  * corner curves (Motion::createCurve + traverse pathType 2): the
#    commanded midpoint B is NEVER touched — the path leaves the
#    incoming line r before B and rejoins r after, r = min(corner,
#    0.4*min(leg lengths));
#  * tick-quantized carried-velocity S-curve profiles (cont()/slice(),
#    1 ms ticks — resolutionFactor 1000);
#  * the same q-over-concatenated-paths playback as Motion::addJMove
#    (each chain section = [second half of previous corner] + line +
#    [first half of own corner], one profile per section, velocity
#    carried across sections).
# Keep in lockstep with motion.cpp — quirks are ported deliberately
# (e.g. slice()'s pow(x, 1/3) integer-division bug).

def _fw_q_n(t, j, a0, v0, q0=0.0):
    return q0 + t * v0 + t * (t - 1) / 2 * a0 + t * (t - 1) * (t - 2) / 6 * j

def _fw_q_n_prime(t, j, a0, v0):
    return v0 - a0 / 2 + t * a0 + j * (3 * t * t - 6 * t + 2) / 6

def _fw_v_n(t, j, a0, v0):
    return v0 + t * a0 + t * (t - 1) / 2 * j

def _fw_a_n(t, j, a0):
    return a0 + t * j

def _fw_solver_quadratic(a, b, c):
    delta = b * b - 4 * a * c
    if delta < 0:
        return None
    delta = math.sqrt(delta)
    return ((-b - delta) / (2 * a), (-b + delta) / (2 * a))

def _fw_sign3(x):
    return 1 if x >= 0 else -1

def _fw_slice(t, j0, a0, v0, d):
    """Port of slice() — one jerk segment of at most t ticks, capped so
    the covered distance never exceeds d. finish=1 means the full t
    ticks fit inside d."""
    q_t = _fw_q_n(t, j0, a0, v0)
    if q_t <= d:
        return {"ticks": [t], "jerks": [j0], "vFinal": _fw_v_n(t, j0, a0, v0),
                "aFinal": _fw_a_n(t, j0, a0), "d": q_t, "finish": 1}
    if j0 == 0:
        roots = _fw_solver_quadratic(a0 / 2, v0 - a0 / 2, -d)
        if roots is not None:
            x = math.floor(min(roots))
            if x >= 0:
                return {"ticks": [x], "jerks": [j0], "vFinal": _fw_v_n(x, j0, a0, v0),
                        "aFinal": _fw_a_n(x, j0, a0), "d": _fw_q_n(x, j0, a0, v0), "finish": 0}
        else:
            return {"ticks": [0], "jerks": [j0], "vFinal": _fw_v_n(0, j0, a0, v0),
                    "aFinal": _fw_a_n(0, j0, a0), "d": _fw_q_n(0, j0, a0, v0), "finish": 0}
    # Newton search (C++ quirk kept: pow(x, 1/3) with integer division
    # is pow(x, 0) == 1.0)
    x_u, q_u = t, q_t
    x_l, q_l = 0.0, 0.0
    x = max(0.0, min(1.0, t)) if j0 > 0 else t
    qq_n = _fw_q_n(x, j0, a0, v0, 0)
    i = 0
    while i < 100 and math.floor(x_u) > math.ceil(x_l):
        denom = _fw_q_n_prime(x, j0, a0, v0)
        if denom == 0:
            break
        x = x - (qq_n - d) / denom
        qq_n = _fw_q_n(x, j0, a0, v0, 0)
        if x_l <= x <= x_u:
            if qq_n > d and qq_n < q_u:
                x_u, q_u = x, qq_n
            elif qq_n < d and qq_n > q_l:
                x_l, q_l = x, qq_n
            else:
                x_u, q_u = x, qq_n
                x_l, q_l = x, qq_n
        i += 1
    x = math.floor(x_u)
    return {"ticks": [x], "jerks": [j0], "vFinal": _fw_v_n(x, j0, a0, v0),
            "aFinal": _fw_a_n(x, j0, a0), "d": _fw_q_n(x, j0, a0, v0), "finish": 0}

def _fw_append(rtn, slc):
    rtn["ticks"] += slc["ticks"]
    rtn["jerks"] += slc["jerks"]
    rtn["aFinal"] = slc["aFinal"]
    rtn["vFinal"] = slc["vFinal"]
    rtn["d"] += slc["d"]

def _fw_cont(a0, v0, jm, am, vm, d):
    """Port of cont() — carried-velocity S-curve profile toward target
    speed vm over distance d, tick-quantized."""
    rtn = {"ticks": [], "jerks": [], "aFinal": a0, "vFinal": v0, "d": 0.0}
    if d < v0:
        return rtn
    if vm > 0:
        if a0 == 0:
            if v0 == vm:
                t4 = math.floor(d / vm)
                rtn["ticks"], rtn["jerks"] = [t4], [0]
                rtn["d"] += _fw_q_n(t4, 0, 0, v0)
                return rtn
            t1 = math.floor(am / jm) + 1
            if t1 * am >= abs(vm - v0):
                t1 = math.floor(math.sqrt(abs(vm - v0) / jm)) + 1
                j = (vm - v0) / (t1 * t1)
                a = j * t1
                t2 = 0
            else:
                t2 = math.floor(abs(vm - v0) / am) - t1 + 1
                a = (vm - v0) / (t1 + t2)
                j = a / t1
            slc = _fw_slice(t1, j, a0, v0, d)
            _fw_append(rtn, slc)
            if slc["finish"]:
                slc = _fw_slice(t2, 0, a, rtn["vFinal"], d - rtn["d"])
                _fw_append(rtn, slc)
                if slc["finish"]:
                    slc = _fw_slice(t1, -j, a, rtn["vFinal"], d - rtn["d"])
                    _fw_append(rtn, slc)
                    if slc["finish"]:
                        _fw_append(rtn, _fw_cont(0, rtn["vFinal"], jm, am, vm, d - rtn["d"]))
            return rtn
        # a0 != 0
        roots = _fw_solver_quadratic(_fw_sign3(a0) * jm, 2 * a0,
                                     v0 - a0 * a0 / (2 * jm) + a0 / 2 - vm)
        if roots is not None:
            t1 = min(roots) if a0 >= 0 else max(roots)
            if t1 >= 1:
                a1 = _fw_a_n(t1, _fw_sign3(a0) * jm, a0)
                v1 = _fw_v_n(t1, _fw_sign3(a0) * jm, a0, v0)
                t1_adj = math.floor(abs(min(abs(a1), am) - abs(a0)) / jm)
                v1_adj = _fw_v_n(t1_adj, _fw_sign3(a0) * _fw_sign3(am - abs(a0)) * jm, a0, v0)
                a1_adj = _fw_a_n(t1_adj, _fw_sign3(a0) * _fw_sign3(am - abs(a0)) * jm, a0)
                t_tmp = abs(a1 - a1_adj) / jm
                v_tmp = _fw_v_n(t_tmp, -_fw_sign3(a0) * jm, a1, v1)
                t2_adj = math.floor((v_tmp - v1_adj) / (_fw_sign3(a0) * am))
                v2_adj = _fw_v_n(t2_adj, 0, a1_adj, v1_adj)
                t3_adj = math.floor(abs(a1_adj) / jm)
                j3 = -a1_adj / t3_adj if t3_adj else 0.0
                slc = _fw_slice(t1_adj, _fw_sign3(a0) * _fw_sign3(am - abs(a0)) * jm, a0, v0, d)
                _fw_append(rtn, slc)
                if slc["finish"]:
                    slc = _fw_slice(t2_adj, 0, a1_adj, v1_adj, d - rtn["d"])
                    _fw_append(rtn, slc)
                    if slc["finish"]:
                        slc = _fw_slice(t3_adj, j3, a1_adj, v2_adj, d - rtn["d"])
                        _fw_append(rtn, slc)
                        if slc["finish"]:
                            _fw_append(rtn, _fw_cont(0, rtn["vFinal"], jm, am, vm, d - rtn["d"]))
                return rtn
        # go toward a = 0 as fast as possible
        if a0 < -v0:
            a0 = -v0
        t3 = math.floor(abs(a0) / jm) + 1
        j3 = -a0 / t3
        v3 = _fw_v_n(t3, j3, a0, v0)
        if v3 < 0:
            t3 = math.floor(2 * v0 / abs(a0)) - 1
            j3 = -a0 / t3 if t3 else 0.0
        slc = _fw_slice(t3, j3, a0, v0, d)
        _fw_append(rtn, slc)
        if slc["finish"]:
            _fw_append(rtn, _fw_cont(0, rtn["vFinal"], jm, am, vm, d - rtn["d"]))
        return rtn
    # vm <= 0: decelerate to stop within d
    if a0 == 0:
        t1_a = math.floor(am / jm)
        t1_v = math.floor(math.sqrt(v0 / jm)) if v0 > 0 else 0
        t1_d = math.floor((d / v0) - 1.0) if v0 > 0 else 0
        t1 = max(0, min(t1_a, t1_v, t1_d))
        t2 = 0
        if t1 == 0:
            t4 = max(0, math.ceil(d / v0) - 1.0) if v0 > 0 else 0
            j_t = 0
        else:
            if t1_a <= min(t1_v, t1_d):
                t2_v = math.floor((v0 / (jm * t1)) - t1)
                t2_d = 2.0 * (t1_d - t1)
                t2 = max(0.0, min(t2_v, t2_d))
            t4 = max(0.0, math.ceil((d / v0) - t1 - t2 / 2.0 - 1.0))
            j_t = v0 / (t1 * (t1 + t2))
        rtn["ticks"] += [t4, t1, t2, t1]
        rtn["jerks"] += [0, -j_t, 0, j_t]
        rtn["aFinal"] = 0.0
        rtn["vFinal"] = 0.0
        rtn["d"] = j_t * t1 * (t1 + t2) * (t1 + t2 / 2.0 + t4 + 1.0)
        return rtn
    d1 = d / 2
    roots = _fw_solver_quadratic(a0 / 3, v0, -a0 / 3 - d1)
    if roots is not None:
        t1 = math.floor(min(roots))
        if t1 < 0:
            t1 = math.floor(max(roots))
        if a0 < 0 and t1 > -2 * (v0 / a0) - 1:
            t1 = math.floor(-(v0 / a0) - 0.5)
        j1 = -a0 / t1 if t1 else 0.0
        v1 = _fw_v_n(t1, j1, a0, v0)
        d1 = _fw_q_n(t1, j1, a0, v0)
        rtn["ticks"].append(t1)
        rtn["jerks"].append(j1)
        rtn["d"] += d1
        _fw_append(rtn, _fw_cont(0, v1, jm, am, vm, d - d1))
    return rtn

def _fw_profile(jerk, accel, vel, d, v_init, a_init, to_stop):
    """Port of Motion::createProfile types 1/2 — the x1000 tick
    scaling wrapper around cont(). Quirk kept: an empty profile is
    replaced by a single zero segment with vFinal forced to 0."""
    F = 1000.0
    prof = _fw_cont(a_init * F, v_init * F, jerk * F, accel * F,
                    (0.0 if to_stop else vel * F), d * F + 1.0e-6)
    if to_stop:
        prof["vFinal"] = 0.0
        prof["aFinal"] = 0.0
    if not prof["ticks"]:
        prof = {"ticks": [0], "jerks": [0], "vFinal": 0.0, "aFinal": 0.0, "d": prof["d"]}
    return {"ticks": [int(round(t)) for t in prof["ticks"]],
            "jerks": [j / F for j in prof["jerks"]],
            "vInitial": v_init, "aInitial": a_init,
            "vFinal": prof["vFinal"] / F, "aFinal": prof["aFinal"] / F,
            "d": prof["d"] / F}

def _fw_vec(a, b, ca, cb):
    return [ca * x + cb * y for x, y in zip(a, b)]

def _fw_norm(v):
    return math.sqrt(sum(x * x for x in v))

def _fw_unit(v):
    n = _fw_norm(v)
    return [x / n for x in v] if n > 0 else list(v)

def _fw_inner(a, b):
    return sum(x * y for x, y in zip(a, b))

def _fw_create_curve(A, B, C, corner):
    """Port of Motion::createCurve — the cont corner blend between the
    incoming leg A->B and outgoing leg B->C. B is never touched."""
    BA = _fw_vec(A, B, 1.0, -1.0)
    BC = _fw_vec(C, B, 1.0, -1.0)
    LBA, LBC = _fw_norm(BA), _fw_norm(BC)
    BA, BC = _fw_unit(BA), _fw_unit(BC)
    y = _fw_unit(_fw_vec(BA, BC, 1.0, 1.0))
    x = _fw_unit(_fw_vec(BC, y, 1.0, -_fw_inner(BC, y)))
    r = min(corner, 0.4 * min(LBA, LBC))
    a_abs = _fw_vec(B, BA, 1.0, r)
    c_abs = _fw_vec(B, BC, 1.0, r)
    return {"r": r, "b": list(B), "x": x, "y": y,
            "a": _fw_vec(a_abs, B, 1.0, -1.0),
            "c": _fw_vec(c_abs, B, 1.0, -1.0),
            "curveInitial": a_abs, "curveFinal": c_abs,
            "length": 2.0 * r}

def _fw_curve_point(c, q, first):
    """Port of Motion::traverse pathType 2 — evaluate the corner curve
    at arc position q of its half (first / second)."""
    z = q if first else q + c["length"] / 2
    z = z / c["length"] if c["length"] > 0 else 0.0
    zp = 1.0 - z
    f = 0.5 * (1.0 - math.cos(math.pi * 0.5 * (1.0 - math.cos(math.pi * z))))
    lam = -3.48
    x = _fw_inner(c["a"], c["x"]) * zp + _fw_inner(c["c"], c["x"]) * z
    y = (_fw_inner(c["a"], c["y"]) * (1.0 - 2.0 * z - lam * z ** 3) * (1.0 - f)
         + _fw_inner(c["c"], c["y"]) * (1.0 - 2.0 * zp - lam * zp ** 3) * f)
    v = list(c["b"])
    v = _fw_vec(v, c["x"], 1.0, x)
    v = _fw_vec(v, c["y"], 1.0, y)
    return v


def _fw_build_section(pts, corners, k, last_curve):
    """One chain section's path list, exactly as Motion::addJMove builds
    it: [second half of previous corner] + line + [first half of own
    corner]. Shared by the sim executor and the host-side verifier so
    both evaluate the SAME geometry. Returns (paths, d_total, curve)."""
    A, B = pts[k], pts[k + 1]
    has_next = k + 1 < len(pts) - 1
    paths = []
    if last_curve is not None and last_curve["length"] > 0:
        paths.append(("curve2", last_curve, last_curve["length"] / 2))
    line_start = last_curve["curveFinal"] if last_curve is not None else A
    curve = None
    if has_next:
        corner = float(corners[k]) if corners[k] else 0.0
        curve = _fw_create_curve(A, B, pts[k + 2], corner)
        line_end = curve["curveInitial"]
    else:
        line_end = B
    d_line = _fw_norm(_fw_vec(line_end, line_start, 1.0, -1.0))
    paths.append(("line", (line_start, line_end, d_line), d_line))
    if curve is not None and curve["length"] > 0:
        paths.append(("curve1", curve, curve["length"] / 2))
    return paths, sum(pd for _, _, pd in paths), curve

def _fw_path_pose(paths, qq):
    """Pose at arc position qq over a section's concatenated paths —
    the firmware's q mapping (traverse dispatch)."""
    acc = 0.0
    for idx, (kind, data, pd) in enumerate(paths):
        if qq <= acc + pd or idx == len(paths) - 1:
            qe = min(max(qq - acc, 0.0), pd)
            if kind == "line":
                s0, s1, dl = data
                f = qe / dl if dl > 0 else 1.0
                return _fw_vec(s0, s1, 1.0 - f, f)
            return _fw_curve_point(data, qe, kind == "curve1")
        acc += pd
    return list(paths[-1][1][1]) if paths[-1][0] == "line" else _fw_curve_point(paths[-1][1], paths[-1][2], False)


class J5WindingGuard:
    """Transparent proxy over the robot api on an infinite wrist.

    The turn-carry invariant: every joint target the workspace executes
    is unwrapped against the live joints first (``core.unwrap_j5``), so
    no commanded j5 may ever differ from the live j5 by more than one
    turn (360°). A larger delta means some layer leaked a canonical /
    raw j5 to the firmware — the multi-turn unwind. The guard never
    blocks motion (a refused move mid-protocol strands the bench); it
    prints the offending command WITH the call stack, so a single
    reproduction names the leaking layer.
    """

    def __init__(self, api):
        object.__setattr__(self, "_api", api)

    def __getattr__(self, name):
        attr = getattr(self._api, name)
        if callable(attr) and name in ("jmove", "cjmove", "smove", "tmove"):
            def _watched(*a, **k):
                self._audit(name, a, k)
                return attr(*a, **k)
            _watched.__name__ = name
            return _watched
        return attr

    def _audit(self, name, a, k):
        try:
            live = float(self._api.joint()[5])
            t5s = []
            if name == "jmove":
                if k.get("rel"):
                    return
                j = k.get("joint", a[0] if a else None)
                if j is not None and len(j) > 5 and j[5] is not None:
                    t5s = [float(j[5])]
                elif k.get("j5") is not None:
                    t5s = [float(k["j5"])]
            elif name in ("cjmove", "smove"):
                pts = a[0] if a else k.get("joints", k.get("points", []))
                t5s = [float(p[5]) for p in pts if len(p) > 5 and p[5] is not None]
            elif name == "tmove":
                pts = a[0] if a else k.get("samples", [])
                t5s = [float(p[6]) for p in pts if len(p) > 6 and p[6] is not None]
            worst = max(t5s, key=lambda t: abs(t - live), default=None)
            if worst is not None and abs(worst - live) > 360.5:
                import traceback
                print(f"[j5-guard] {name} commands j5={worst:.1f} while live j5={live:.1f} "
                      f"(Δ{worst - live:+.1f}° > one turn) — turn-carry invariant violated at:\n"
                      + "".join(traceback.format_stack(limit=14)))
        except Exception:
            pass


class SimulationAPI:
    def __init__(self, joints=[0,0,0,0,0,0,0,0]):
        self.joints = joints
        self.FREQ = 100000
        self.INTERP_FREQ=120
        self.dorna = Dorna()
        # Digital output states — recorded so the approach-IO barrier's
        # pin verification runs identically in sim and real.
        self._outputs = [0] * 16

    def joint(self):
        return self.joints[:]
    
    def solve_third_degree(self,a, b, c, d):
        """
        Solve a cubic a*t^3 + b*t^2 + c*t + d = 0
        Return sorted list of real roots (like C++ version).
        """
        results = []
        if abs(a) < 1e-15:  # quadratic or linear
            delta = c * c - 4.0 * b * d
            if delta < 0:
                return []
            root1 = (-c + math.sqrt(delta)) / (2.0 * b)
            root2 = (-c - math.sqrt(delta)) / (2.0 * b)
            results = [root1, root2] if root1 <= root2 else [root2, root1]
            return results

        PI = math.pi
        p = (b * b - 3.0 * a * c) / (9.0 * a * a)
        q = (9.0 * a * b * c - 27.0 * a * a * d - 2.0 * b * b * b) / (54.0 * a * a * a)
        offset = b / (3.0 * a)
        discriminant = p * p * p - q * q

        if discriminant > 0:  # three real roots
            theta = math.acos(q / (p * math.sqrt(p)))
            r = 2.0 * math.sqrt(p)
            for i in range(3):
                results.append(r * math.cos((theta + 2.0 * i * PI) / 3.0) - offset)
            results.sort()
            return results
        else:  # one real root
            gamma1 = math.copysign(abs(q + math.sqrt(-discriminant)) ** (1.0 / 3.0), q + math.sqrt(-discriminant))
            gamma2 = math.copysign(abs(q - math.sqrt(-discriminant)) ** (1.0 / 3.0), q - math.sqrt(-discriminant))
            root = gamma1 + gamma2 - offset
            return [root]

    def sign(self,x):
        return -1.0 if x < 0 else (1.0 if x > 0 else 0.0)

    def create_profile(self,jerk, accel, vel, d):
        """
        Stop-stop motion profile (S-curve) generator.
        Inputs:
            jerk, accel, vel : max jerk/accel/vel (user-specified)
            d                : target displacement

        Returns dict with:
            ticks : list of integer ticks per segment
            jerks : list of jerk values per segment (same length as ticks)
            j_peak, a_peak, v_peak, d_total
        """

        jerk /= (self.FREQ * self.FREQ * self.FREQ)
        accel /= (self.FREQ * self.FREQ)
        vel   /= self.FREQ

        resolutionFactor = 1000.0

        jerk  *= resolutionFactor
        accel *= resolutionFactor
        vel   *= resolutionFactor
        d     *= resolutionFactor 

        vInitial = 0.0
        aInitial = 0.0

        # Step 1: t1 candidates
        t1_a = math.floor(accel / jerk) if jerk > 0 else 0
        t1_v = math.floor(math.sqrt(abs(vel - vInitial) / jerk)) if jerk > 0 else 0

        roots = self.solve_third_degree(self.sign(vel - vInitial) * 2.0 * jerk, 0.0, 4.0 * vInitial, -d)
        if vel >= vInitial:
            t1_d = math.floor(max(roots) if roots else 0)
        else:
            if len(roots) <= 1:
                t1_d = math.floor(math.sqrt((2.0 * vInitial) / (3.0 * jerk)))
            else:
                t1_d = math.floor(roots[1])

        t1 = max(min(t1_a, t1_v, t1_d), 0)

        # Step 2: handle t1 == 0
        if t1 == 0:
            return {
                "ticks": [],
                "jerks": [],
                "j_peak": 0.0,
                "a_peak": 0.0,
                "v_peak": 0.0,
                "d_total": 0.0
            }

        # Step 3: t2
        t2 = 0
        if t1_a <= min(t1_v, t1_d):
            t2_v = math.floor((abs(vel - vInitial) / (jerk * t1)) - t1)

            roots = self.solve_third_degree(
                0.0,
                self.sign(vel - vInitial) * jerk * t1,
                self.sign(vel - vInitial) * 3.0 * jerk * t1 * t1 + 2.0 * vInitial,
                self.sign(vel - vInitial) * 2.0 * jerk * t1 * t1 * t1 + 4.0 * vInitial * t1 - d
            )
            if vel >= vInitial:
                t2_d = math.floor(max(roots) if roots else 0)
            else:
                if len(roots) <= 1:
                    t2_d = math.floor(math.sqrt((-3.0 * jerk * t1 * t1 + 2.0 * vInitial) / (2.0 * jerk * t1)))
                else:
                    t2_d = math.floor(roots[0])

            t2 = max(min(t2_v, t2_d), 0)

        # Step 4: t4 and j_m
        denom = self.sign(vel - vInitial) * jerk * t1 * (t1 + t2) + vInitial
        t4 = math.ceil((d - (2.0 * t1 + t2) * (2.0 * vInitial + self.sign(vel - vInitial) * jerk * t1 * (t1 + t2))) / denom) if denom != 0 else 0
        t4 = max(t4, 0)

        denom_j = t1 * (t1 + t2) * (2.0 * t1 + t2 + t4)
        j_m = (d - vInitial * (4.0 * t1 + 2.0 * t2 + t4)) / denom_j if denom_j != 0 else 0.0

        a_m = j_m * t1
        v_m = a_m * (t1 + t2) + vInitial
        d_m = (v_m - vInitial) * (2.0 * t1 + t2 + t4) + vInitial * (4.0 * t1 + 2.0 * t2 + t4)

        # Step 5: assemble profile
        ticks = []
        jerks = []

        def push(seg_ticks, seg_jerk):
            n = int(round(seg_ticks))
            if n > 0:
                ticks.append(n)
                jerks.append(seg_jerk)

        push(t1,  j_m)
        push(t2,  0.0)
        push(t1, -j_m)
        push(t4,  0.0)
        push(t1, -j_m)
        push(t2,  0.0)
        push(t1,  j_m)

        # Scale back to user units
        j_scale = 1.0 / resolutionFactor
        a_scale = 1.0 / resolutionFactor
        v_scale = 1.0 / resolutionFactor
        d_scale = 1.0 / resolutionFactor

        return {
            "ticks": ticks,
            "jerks": [j * j_scale for j in jerks],
            "j_peak": j_m * j_scale,
            "a_peak": a_m * a_scale,
            "v_peak": v_m * v_scale,
            "d_total": d_m * d_scale,
            "t_total": sum(ticks) / self.FREQ   
        }


    def traverse(self,jerks, ticks, q0=0.0, v0=0.0, a0=0.0, t=0):
        """
        Closed-form state (q, v, a) at tick n (integer) 
        for a piecewise-constant jerk profile.

        Args:
            J: list of jerks [j0, j1, ..., j_{K-1}]
            N: list of durations [n0, n1, ..., n_{K-1}]
            q0, v0, a0: initial position, velocity, acceleration
            n: tick index (integer)

        Returns:
            (q, v, a) at tick n
        """

        n = int(round(t * self.FREQ))

        # convert to floats/ints
        jerks = [float(j) for j in jerks]
        ticks = [int(x) for x in ticks]
        q0, v0, a0 = float(q0), float(v0), float(a0)

        # cumulative tick starts
        T = [0]
        for nn in ticks:
            T.append(T[-1] + nn)
        total = T[-1]

        # prefix states at segment starts
        A = [a0]
        V = [v0]
        Q = [q0]
        for j, nn in zip(jerks, ticks):
            A_s, V_s, Q_s = A[-1], V[-1], Q[-1]
            A.append(A_s + j * nn)
            V.append(V_s + nn * A_s + 0.5 * j * nn * (nn - 1))
            Q.append(Q_s + nn * V_s + 0.5 * A_s * nn * (nn - 1) + (j/6.0) * nn * (nn - 1) * (nn - 2))

        # clamp n to valid range
        if n <= 0:
            return (q0, v0, a0)
        if n >= total:
            return (Q[-1], V[-1], A[-1])

        # find segment s with T[s] <= n < T[s+1]
        s = 0
        while not (T[s] <= n < T[s+1]):
            s += 1

        # ticks into segment
        m = n - T[s]
        j = jerks[s]
        A_s, V_s, Q_s = A[s], V[s], Q[s]

        # closed-form updates
        a = A_s + m * j
        v = V_s + m * A_s + 0.5 * j * m * (m - 1)
        q = Q_s + m * V_s + 0.5 * A_s * m * (m - 1) + (j/6.0) * m * (m - 1) * (m - 2)
        return (q, v, a)
    



    def jmove(self, joint, vel=100, accel=1000, jerk=4000, **kwargs):
        """
        Move from current joint vector to `joint` using an S-curve distance profile.
        Interpolates joint updates at `interp_freq` Hz (default 120).

        Extra kwargs (``cont``, ``timeout``, ``rel`` …) are accepted for
        real-SDK call compatibility and ignored: the sim executes every
        jmove to completion serially — a cont chain runs stop-start
        here (velocity shape preserved, blending is firmware behavior).

        Returns:
            -1 : if any error happens
            2 : if successful
        """

        # --- Setup start/goal
        cur = list(self.joints[:])
        tgt = list(joint)
        delta = [t - c for c, t in zip(cur, tgt)]
        d = math.sqrt(sum(di * di for di in delta))
        if d <= 0.0:
            return 2  # nothing to do


        # --- Build profile
        prof = self.create_profile(jerk=jerk, accel=accel, vel=vel, d=d)
        jerks = prof.get("jerks", [])
        ticks = prof.get("ticks", [])
        t_total = prof.get("t_total", 0.0)

        if t_total <= 0.0 or not ticks:
            return 2

        # --- Interpolation timing
        dt = 1.0 / float(self.INTERP_FREQ)
        # Playback advances SIMULATED time one tick per frame — never
        # wall-clock catch-up: a Python thread stall must slow the sim
        # down, not teleport the pose along the path (the firmware
        # ticks at a fixed rate and can never skip).
        t0 = time.perf_counter()
        step = 0
        while True:
            t_sim = step * dt
            if t_sim >= t_total:
                break

            # scalar motion state at this time
            q, v, a = self.traverse(jerks, ticks, q0=0.0, v0=0.0, a0=0.0, t=t_sim)
            s = max(0.0, min(q / d, 1.0))

            # update joints
            self.joints = [c + s * di for c, di in zip(cur, delta)]

            # sleep until next interpolation tick
            step += 1
            next_tick_time = t0 + step * dt
            sleep_for = next_tick_time - time.perf_counter()
            if sleep_for > 0:
                time.sleep(sleep_for)

        self.joints = tgt[:]

        return 2  # success


    def smove(self, points, vel=100, accel=1000, jerk=4000):
        """
        Move along a cubic spline path defined by a list of joint vectors.
        The current joint position is prepended as the first waypoint, so
        callers pass only the points to move THROUGH (the recipe strips the
        planner's leading current-pose point).
        Returns:
            -1 : error
            2 : success
        """
        # Prepend the current joints as ONE waypoint (a flat concat would
        # mix scalars with waypoint lists and blow up SplinePath).
        points = [list(self.joint())] + [list(p) for p in points]
        # Drop consecutive duplicates — a plan to (or through) the
        # current pose yields zero-length segments that divide by zero
        # inside SplinePath.
        points = [p for i, p in enumerate(points)
                  if i == 0 or any(abs(a - b) > 1e-9 for a, b in zip(p, points[i - 1]))]
        if len(points) < 2:
            return 2   # already there — nothing to do

        # Build the spline path
        path = SplinePath(points)
        d = path.total_len
        if d <= 0.0:
            return 2

        # Create S‑curve profile using the total path length
        prof = self.create_profile(jerk=jerk, accel=accel, vel=vel, d=d)
        jerks = prof.get('jerks', [])
        ticks = prof.get('ticks', [])
        t_total = prof.get('t_total', 0.0)

        if t_total <= 0.0 or not ticks:
            return 2

        dt = 1.0 / float(self.INTERP_FREQ)
        # Simulated-time stepping — see jmove: stalls slow the sim,
        # they never skip the pose along the path.
        t0 = time.perf_counter()
        step = 0

        while True:
            t_sim = step * dt
            if t_sim >= t_total:
                break

            # Distance traveled at this time
            q, v, a = self.traverse(jerks, ticks, q0=0.0, v0=0.0, a0=0.0, t=t_sim)
            # q is in [0, d]

            # Get joint positions at that arc length
            pos, _, _ = path.get_curve_data(q)
            self.joints = pos

            step += 1
            next_tick = t0 + step * dt
            sleep_for = next_tick - time.perf_counter()
            if sleep_for > 0:
                time.sleep(sleep_for)

        # Ensure exact final position
        self.joints = points[-1][:]
        return 2

    def jmove_multi_point(self, points, vel=100, accel=1000, jerk=4000):


        if points is None or len(points) == 0:
            return -1

        # Execute waypoint list using jmove() calls
        cur_full = list(self.joint())
        for wp in points:
            wp = list(wp)

            # Keep any extra axes (e.g., rail/other) unchanged unless planner provided them
            wp_full = list(cur_full)
            for i, v in enumerate(wp):
                if i < len(wp_full):
                    wp_full[i] = float(v)
                else:
                    wp_full.append(float(v))

            # Call robot_api.jmove with motion params when supported
            try:
                out = self.jmove(wp_full, vel=vel, accel=accel, jerk=jerk)
            except TypeError:
                out = self.jmove(wp_full)

            if out not in (2, True, None):
                return out

            cur_full = wp_full

        return 2


    def lmove(self, joint, vel=100, accel=1000, jerk=4000, tool_pose=[0, 0, 0, 0, 0, 0], **kwargs):
        """
        Move from current joint vector to `joint` using an S-curve distance profile.
        Interpolates joint updates at `interp_freq` Hz (default 120).

        The path math (straight TCP line + nearest-branch closed-form
        IK) lives at module level — see ``_xyzj_to_joints`` — shared
        with ``Core.lmove_points``.

        Returns:
            -1 : if any error happens
            2 : if successful
        """
        # --- Setup start/goal
        cur_joints = list(self.joints[:])
        tgt_joints = list(joint)

        # first we set the tool
        self.dorna.kinematic.set_tcp_xyzabc(tool_pose)
        cur_xyz = self.dorna.kinematic.fw(cur_joints[0:6])

        tgt_xyz = self.dorna.kinematic.fw(tgt_joints[0:6])



        # now we form xyz joint vectors
        cur_xyz_joints = [cur_xyz[0], cur_xyz[1], cur_xyz[2], cur_joints[3], cur_joints[4], cur_joints[5], cur_joints[6], cur_joints[7]]
        tgt_xyz_joints = [tgt_xyz[0], tgt_xyz[1], tgt_xyz[2], tgt_joints[3], tgt_joints[4], tgt_joints[5], tgt_joints[6], tgt_joints[7]]

        delta = [t - c for c, t in zip(cur_xyz_joints, tgt_xyz_joints)]
        d = math.sqrt(sum(di * di for di in delta))
        if d <= 0.0:
            return 2  # nothing to do


        # --- Build profile
        prof = self.create_profile(jerk=jerk, accel=accel, vel=vel, d=d)
        jerks = prof.get("jerks", [])
        ticks = prof.get("ticks", [])
        t_total = prof.get("t_total", 0.0)

        if t_total <= 0.0 or not ticks:
            return 2

        # --- Interpolation timing
        dt = 1.0 / float(self.INTERP_FREQ)
        t0 = time.perf_counter()
        step = 0


        while True:
            t_sim = step * dt   # simulated time — stalls slow, never skip
            if t_sim >= t_total:
                break

            # scalar motion state at this time
            q, v, a = self.traverse(jerks, ticks, q0=0.0, v0=0.0, a0=0.0, t=t_sim)
            s = max(0.0, min(q / d, 1.0))

            # update joints
            xyz_joints = [c + s * di for c, di in zip(cur_xyz_joints, delta)]
            J = _xyzj_to_joints(xyz_joints, self.joints, tool_pose, self.dorna.kinematic)

            
            if J is None:
                
                return -1  # inverse kinematics failure
            else:
                self.joints = J



            # sleep until next interpolation tick
            step += 1
            next_tick_time = t0 + step * dt
            sleep_for = next_tick_time - time.perf_counter()
            if sleep_for > 0:
                time.sleep(sleep_for)


        # ensure exact final value
        self.joints = tgt_joints
        return 2  # success
    

    # sleep
    def sleep(self, val=0):
        time.sleep(val)
        return 2

    # output — records pin states and honours the sequencing sleeps,
    # mirroring the real controller chain played by dorna2.output(config=).
    def output(self, index=None, val=None, config=None):
        if config is not None:
            for c in config:
                if len(c) > 1 and c[0] is not None and 0 <= c[0] < 16:
                    self._outputs[int(c[0])] = int(c[1])
                if len(c) > 2 and c[2] > 0:
                    self.sleep(c[2])
            return True
        if index is not None and val is not None:
            if 0 <= int(index) < 16:
                self._outputs[int(index)] = int(val)
            return True
        return self._outputs[:]

    def tmove(self, samples, **kwargs):
        """Follow a time-parameterized joint trajectory (PVT): samples
        = [[t, j0..j7], ...], t strictly increasing from 0. The sim
        twin of the future firmware tmove — linear interpolation
        between timed samples, executed wall-clock so the viewer (and
        the motion trail) shows the true speed profile.
        Returns 2 on success."""
        if not samples:
            return 2
        if len(samples) == 1:
            self.joints = [float(v) for v in samples[0][1:]]
            return 2
        t0 = time.perf_counter()
        dt = 1.0 / float(self.INTERP_FREQ)
        T = float(samples[-1][0])
        k, step = 0, 0
        while True:
            el = step * dt   # simulated time — stalls slow, never skip
            if el >= T:
                break
            while k + 1 < len(samples) and samples[k + 1][0] <= el:
                k += 1
            a = samples[k]
            b = samples[k + 1] if k + 1 < len(samples) else samples[k]
            span = float(b[0]) - float(a[0])
            f = 0.0 if span <= 0 else (el - float(a[0])) / span
            self.joints = [pa + (pb - pa) * f for pa, pb in zip(a[1:], b[1:])]
            step += 1
            sleep_for = t0 + step * dt - time.perf_counter()
            if sleep_for > 0:
                time.sleep(sleep_for)
        self.joints = [float(v) for v in samples[-1][1:]]
        return 2

    def _fw_profile_cont(self, jerk, accel, vel, d, v0_tick, a0_tick, to_stop):
        """Section profile with carried velocity — Motion::createProfile
        types 1/2 (the cont() wrapper) in the sim's per-tick unit
        convention (see create_profile: user units / FREQ powers, then
        the x1000 resolution scale). vFinal/aFinal come back in
        per-tick units, ready to carry into the next section."""
        F = float(self.FREQ)
        R = 1000.0
        if to_stop and v0_tick == 0.0 and a0_tick == 0.0:
            # Stop-to-stop section: cont() is the carried-velocity
            # engine and returns EMPTY for v0=0/vm=0 — the firmware
            # routes this case to createProfile type 0
            # (Motion::addJMove, combined_profile_type 0). Mirror it.
            prof0 = self.create_profile(jerk=jerk, accel=accel, vel=vel, d=d)
            return {"ticks": list(prof0["ticks"]), "jerks": list(prof0["jerks"]),
                    "vFinal": 0.0, "aFinal": 0.0}
        prof = _fw_cont(a0_tick * R, v0_tick * R,
                        jerk / (F * F * F) * R, accel / (F * F) * R,
                        (0.0 if to_stop else vel / F * R), d * R + 1.0e-6)
        ticks = [int(round(x)) for x in prof["ticks"] if int(round(x)) > 0]
        jerks = [j / R for j, x in zip(prof["jerks"], prof["ticks"]) if int(round(x)) > 0]
        v_final, a_final = prof["vFinal"] / R, prof["aFinal"] / R
        if to_stop or not ticks:   # firmware quirk: empty profile forces vFinal 0
            v_final, a_final = 0.0, 0.0
        return {"ticks": ticks, "jerks": jerks, "vFinal": v_final, "aFinal": a_final}

    def cjmove(self, joints, vajs, corners, **kwargs):
        """Sim twin of Dorna.cjmove with FIRMWARE fidelity (ports of
        server/motion.cpp): corner curves cut inside every intermediate
        target — the commanded midpoint is never touched — one
        tick-quantized S-curve per section with velocity carried
        across sections, full stop only at the chain end."""
        if not joints:
            return None
        if len(vajs) != len(joints) or len(corners) != len(joints):
            raise ValueError("cjmove: joints, vajs and corners must have the same length")
        targets = [[float(v) for v in p] for p in joints]
        return self._fw_chain_exec(list(self.joints), targets, vajs, corners,
                                   lambda pose, cur: [float(v) for v in pose])

    def clmove(self, joints, vajs, corners, tool_pose=[0, 0, 0, 0, 0, 0], **kwargs):
        """Sim twin of Dorna.clmove with FIRMWARE fidelity: the chain
        runs in xyzj space ([x, y, z, j3, j4, j5, aux…] — the
        controller's lmove space, straight TCP lines) with corner
        curves in that same space; ONE tool_pose for the whole chain
        (the controller sets the tool once and it persists)."""
        if not joints:
            return None
        if len(vajs) != len(joints) or len(corners) != len(joints):
            raise ValueError("clmove: joints, vajs and corners must have the same length")
        kin = self.dorna.kinematic
        kin.set_tcp_xyzabc(tool_pose)

        def to_xyzj(J):
            f = kin.fw(J[:6])
            return [float(f[0]), float(f[1]), float(f[2]),
                    float(J[3]), float(J[4]), float(J[5])] + [float(v) for v in J[6:]]

        def to_joints(pose, cur):
            return _xyzj_to_joints(pose, cur, tool_pose, kin)

        start = to_xyzj(list(self.joints))
        targets = [to_xyzj([float(v) for v in p]) for p in joints]
        return self._fw_chain_exec(start, targets, vajs, corners, to_joints)

    def _fw_chain_exec(self, start, targets, vajs, corners, to_joints):
        """Firmware-faithful chain playback (Motion::addJMove): section
        k drives [second half of previous corner] + line + [first half
        of own corner] under ONE carried-velocity profile; q maps
        across the concatenated paths exactly like the tick loop in
        the firmware. The sim assumes the whole chain is queued ahead
        (nextReady always true) — on the real robot a starved command
        queue breaks the chain into a stop, which the sim cannot
        show."""
        pts = [start] + [list(p) for p in targets]
        n = len(targets)
        dt = 1.0 / float(self.INTERP_FREQ)
        v_tick, a_tick = 0.0, 0.0
        last_curve = None
        cur_joints = list(self.joints)
        for k in range(n):
            vel, accel, jerk = (float(vajs[k][0]), float(vajs[k][1]), float(vajs[k][2]))
            has_next = k + 1 < n
            paths, d_total, curve = _fw_build_section(pts, corners, k, last_curve)

            prof = self._fw_profile_cont(jerk, accel, vel, d_total, v_tick, a_tick,
                                         to_stop=not has_next)
            duration = sum(prof["ticks"]) / float(self.FREQ)
            t0 = time.perf_counter()
            step = 0
            while True:
                t_sim = step * dt   # simulated time — stalls slow, never skip
                final = t_sim >= duration
                q, _, _ = self.traverse(prof["jerks"], prof["ticks"],
                                        q0=0.0, v0=v_tick, a0=a_tick,
                                        t=min(t_sim, duration))
                pose = _fw_path_pose(paths, min(max(q, 0.0), d_total))
                J = to_joints(pose, cur_joints)
                if J is None:
                    return -1  # IK failure mid-line (firmware error -110)
                cur_joints = [float(x) for x in J]
                self.joints = list(cur_joints)
                if final:
                    break
                step += 1
                sleep_for = t0 + step * dt - time.perf_counter()
                if sleep_for > 0:
                    time.sleep(sleep_for)
            v_tick, a_tick = prof["vFinal"], 0.0
            last_curve = curve
        # land exactly on the final target
        J = to_joints(pts[-1], cur_joints)
        if J is None:
            return -1
        self.joints = [float(v) for v in J]
        return 2

    def raw_output(self, index, val):
        """Sim twin of RobotStation.raw_output — record only, no sleep
        (the caller's IO thread owns the sequencing delays)."""
        if index is not None and 0 <= int(index) < 16:
            self._outputs[int(index)] = int(val)
        return True

    def get_all_output(self, **kwargs):
        return self._outputs[:]

    # motor
    def motor(self, val=None):
        return val

    # ── Axis / homing stubs ────────────────────────────────────────────
    # SDK methods that touch the motor controller. No hardware in sim,
    # so they all return 2 (the SDK's success sentinel) and let
    # higher-level helpers (Recipe.set_axis_with_stop / _encoder, the
    # project startup notebooks, etc.) call them unconditionally.
    def set_axis(self, **kwargs):
        return 2

    def set_pid(self, **kwargs):
        return 2

    def home_with_stop(self, **kwargs):
        return True

    def home_with_encoder_index(self, **kwargs):
        return True
    
    def is_homed(self, index=6):
        return True