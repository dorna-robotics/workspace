# workspace/components/core.py
from copy import deepcopy
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
            "stream": {"width":1280, "height":720, "fps":30},
            "K": [[645.6025, 0, 629.7725],
                   [0, 644.7921, 361.7835],
                   [0, 0, 1]],
            "D": [-0.055158, 0.060188, 0.001047, 0.000623, -0.019884],
            "mode": "bgrd",
            "filter": {},
            "exposure": None,
            "native_res": None,
            "mount": {
                "type": "dorna_ta_j4_1",
                "T": [46.5174596, 32.0776662, -4.24772615, -0.27547989, 0.27691881, 89.6939516],
                "ej": [0, 0, 0, 0, 0, 0, 0, 0]
            },
        },
        has_tool_changer = True,
        # I/O signals fired on attach/detach. Each list-of-lists is a
        # sequence of [output_port, value, delay_s] rows played in order.
        tool_changer_cfg = {
            "output_attach": [[1, 0, 0], [0, 1, 0], [2, 0, 0.25]],
            "output_detach": [[1, 0, 0], [0, 1, 0], [2, 1, 0.25]],
        },
        has_motion_plan = False, # enable or disable path planing
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

        # -------- rail
        self.has_rail = prm["has_rail"]
        self.rail_cfg = prm["rail_cfg"]
        if self.rail_cfg["type"] == "rail_hd_500mm":
            self.rail_min = -159
            self.rail_max = 316.0
        elif self.rail_cfg["type"] == "rail_hd_1000mm":
            self.rail_min = -199.0
            self.rail_max = 801.0
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
                    meta={"ip": self.robot_ip, "model": prm.get("model", "dorna_ta")},
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
        if not self._simulation_mode:
            print(f"🟡 {self.name} simulation api disabled")
            self.robot_api = self.dorna
        else:
            self.robot_api = SimulationAPI()
            print(f"🔵 {self.name} simulation api enabled")


        # Robot-mounted camera. Like Inspection, the actual Camera lives on
        # the vision server; we just hold a VisionClient to it via the
        # shared VisionStation helper. has_camera=False keeps it in
        # simulation regardless of ip/serial.
        from workspace.components.inspection.vision_station import VisionStation
        self.vision = VisionStation(
            ip=self.camera_cfg.get("ip", "127.0.0.1"),
            port=int(self.camera_cfg.get("port", 80)),
            serial_number=self.camera_cfg.get("serial_number", ""),
            camera_cfg=self.camera_cfg,
            simulation=(not self.has_camera) or bool(prm["simulation"]),
            label=f"{self.name} camera",
        )
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
                rail_collision_boxes = {"rail_base": [
                    {"pose": [170.0-84, 33.63, 40.0, 0.0, 0.0, 0.0], "scale": [761.0, 183.5, 82]},
                    {"pose": [-176.105-84, 116.3, 40.0, 0.0, 0.0, 0.0], "scale": [68.6, 120.5, 82]},
                    {"pose": [170.0-84, 92.5, 41.0, 0.0, 0.0, 0.0], "scale": [761.0, 66, 82]}
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
                rail_collision_boxes = {"rail_base": [
                    {"pose": [420.0-119, 33.63, 40.0, 0.0, 0.0, 0.0], "scale": [1261.0, 183.5, 82]},
                    {"pose": [-176.105-119, 116.3, 40.0, 0.0, 0.0, 0.0], "scale": [68.6, 120.5, 82]},
                    {"pose": [420.0-119, 92.5, 41.0, 0.0, 0.0, 0.0], "scale": [1261.0, 66, 82]}
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
                rail_collision_boxes = {"rail_base": [
                    {"pose": [920.0-340, 33.63, 40.0, 0.0, 0.0, 0.0], "scale": [2261.0, 183.5, 82]},
                    {"pose": [-176.105-340, 120, 40.0, 0.0, 0.0, 0.0], "scale": [68.6, 120.5, 82]},
                    {"pose": [920.0-340, 92.5, 41.0, 0.0, 0.0, 0.0], "scale": [2261.0, 66, 82]}
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
            self.robot_api = self.dorna
            print(f"🟡 {self.name} simulation api disabled")
        else:
            # real → sim: route recipes through SimulationAPI
            self._simulation_mode = True
            self.robot_api = SimulationAPI(joints=self.robot_api.joint())
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
            tmp.write_text("".join(
                json.dumps({"k": k, "v": v}, separators=(",", ":")) + "\n"
                for k, v in self._ik_cache.items()
            ))
            os.replace(tmp, self._ik_cache_path)
        except Exception:
            pass

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
            with open(self._ik_cache_path, "a") as f:
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
    # Only slow solves (>= PATH_CACHE_MIN_SEC) are stored — direct-
    # shortcut hops replan faster than a lookup.

    PATH_CACHE_MIN_SEC = 1.0
    PATH_CACHE_START_TOL = 1.0   # deg (arm) / mm (rail) per joint
    PATH_CACHE_GOAL_TOL = 0.5    # deg / mm per joint
    PATH_CACHE_TOOL_TOL = 1.0    # mm / deg per tool-box element
    PATH_CACHE_MAX_ROWS = 500    # oldest evicted first
    PATH_DECIMATE_EPS = 2.0      # deg/mm max deviation for waypoint decimation
    PATH_CHECK_PADDING_MARGIN = 2.0  # mm — check envelope hysteresis vs the plan envelope

    @staticmethod
    def _decimate_path(path, eps):
        """Corner-aware decimation (Ramer-Douglas-Peucker in joint
        space): drop waypoints where the path runs straight, keep them
        where it bends. smove's spline then flows through sparse points
        in free space while staying pinned exactly where the path
        hugs obstacles — the bends are where the kept points cluster.
        The caller re-checks the decimated polyline before executing."""
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
            if best_d > eps:
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
                tmp.write_text("".join(
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
            with open(self._path_cache_path, "a") as f:
                f.write(json.dumps({"v": row}, separators=(",", ":")) + "\n")
        except Exception:
            pass

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
                return (_hit, 2)

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
                return (best[1], 2)

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
                return (best[1], 2)

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

    def capture(self, name: str, data=None) -> dict:
        """Capture a fresh atomic snapshot (camera frames + robot joints)
        and cache it server-side. Pair with ``detect(name, use_last=True)``
        so detection runs only on a confirmed-fresh frame. See
        VisionStation.capture for the reply shape and ``data`` modes.
        """
        return self.vision.capture(name, data=data)

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

        Includes the robot itself (Core wraps dorna2.Dorna in a
        RobotStation that publishes connection + alarm state to the
        device bus) and the robot-mounted camera, when present. Both
        appear in the Devices panel for any project that uses Core.
        """
        ids: list[str] = []
        if self.robot_ip:
            ids.append(f"dorna:{self.robot_ip}")
        sn = self.vision.serial_number
        if sn:
            ids.append(f"camera:{sn}")
        return ids

    def device_claim(self, device_id: str) -> str:
        """Project-level sim/real claim for ``device_id``.

        For the robot, Core IS the bus publisher and the bus already
        carries the sim flag; this method just mirrors that for any
        consumer that prefers the workspace-side surface. For the
        robot-mounted camera, the vision server owns the bus entry —
        this method is the only place workspace sim intent surfaces.
        """
        if self.robot_ip and device_id == f"dorna:{self.robot_ip}":
            return "sim" if self._simulation_mode else "real"
        sn = self.vision.serial_number
        if sn and device_id == f"camera:{sn}":
            return "sim" if self.vision.simulation else "real"
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

        def _cubes(boxes):
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

        scene, tool, tool_boxes = [], [], []
        if hasattr(self.workspace, "compute_collision_boxes"):
            world_boxes, tool_boxes = self.workspace.compute_collision_boxes(padding)
            scene = _cubes(world_boxes)
            tool = _cubes(tool_boxes)

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

        # -------------------------
        # Path cache (core_path.json) — hit = revalidate + return
        # -------------------------
        if self._path_cache is None:
            self._path_cache_init()
        path_sig = self._path_tool_sig(tool_boxes)
        if path_sig is not None:
            cached = self._path_cache_get(start, goal, path_sig)
            if cached is not None:
                print(f"[plan] cache hit: {len(cached)} wps")
                return cached

        start_time = time.perf_counter()

        # pp branch: AIT* @ 10s is the platform default for every planned
        # hop (requires the pp path_planning build — planner selection +
        # honored time budget + GIL release). rail_weight=0.004 makes
        # rail travel ~2.5x cheaper than stock in the path-length metric
        # so paths slide the bench instead of contorting the arm.
        self.planner.update(scene=scene, gripper=tool, base_in_world=list(base_in_world))
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
            # The gate checks against a slightly slimmer envelope
            # (padding - margin): OMPL validates sampled points along
            # segments, so the solved path may graze the full envelope
            # within one resolution step — the margin absorbs that;
            # anything deeper keeps the dense path. This is the ONE
            # validation a stored row ever gets (creation-time).
            if len(res) > 2:
                sparse = self._decimate_path(res, self.PATH_DECIMATE_EPS)
                if len(sparse) < len(res):
                    try:
                        check_pad = max(0.0, padding - self.PATH_CHECK_PADDING_MARGIN)
                        cw, ct = self.workspace.compute_collision_boxes(check_pad)
                        self.planner.update(scene=_cubes(cw), gripper=_cubes(ct), base_in_world=list(base_in_world))
                        if self.planner.check(sparse, gravity=gravity,
                                              gravity_vec=(gravity_vec if gravity_vec is not None else [0, 0, 1]),
                                              gravity_thr=gravity_thr, rail_weight=rail_weight):
                            res = sparse
                    except Exception:
                        pass  # keep the dense path
            self._motion_plan_log(start, goal, res, planner, time_limit_sec, execution_time)
            if path_sig is not None and execution_time >= self.PATH_CACHE_MIN_SEC:
                self._path_cache_put(start, goal, path_sig, res)
        else:
            print(f"[plan] {planner}@{time_limit_sec:g}s: NO PATH in {execution_time:.1f}s "
                  f"start={[round(v, 1) for v in start]} goal={[round(v, 1) for v in goal]}")

        return res

    def _motion_plan_log(self, start, goal, path, planner, time_limit_sec, execution_time):
        """One pasteable [plan] line per planned hop.

        detour  — joint-space path length / straight start→goal distance
                  (x1.00 = the direct segment; big = the hop went around
                  something or the planner left slack in the path).
        overshoot — joints that leave the [start, goal] interval and come
                  back (deg/mm beyond the envelope). A planner-detour
                  signature: the visible "swing" mid-carry.
        """
        try:
            dof = len(goal)

            def dist(a, b):
                return math.sqrt(sum((x - y) ** 2 for x, y in zip(a[:dof], b[:dof])))

            direct = dist(start, goal)
            length = sum(dist(path[i], path[i + 1]) for i in range(len(path) - 1))
            detour = (length / direct) if direct > 1e-6 else 1.0
            over = []
            for j in range(dof):
                lo, hi = min(start[j], goal[j]), max(start[j], goal[j])
                exc = max(max(p[j] - hi, lo - p[j]) for p in path)
                if exc > 2.0:
                    over.append(f"j{j}+{exc:.1f}")
            print(f"[plan] {planner}@{time_limit_sec:g}s: {len(path)} wps in {execution_time:.1f}s, "
                  f"detour x{detour:.2f}"
                  + (f", overshoot {' '.join(over)}" if over else "")
                  + f", dJ={[round(g - s, 1) for s, g in zip(start, goal)]}")
        except Exception:
            pass



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
    



    def jmove(self, joint, vel=100, accel=1000, jerk=4000):
        """
        Move from current joint vector to `joint` using an S-curve distance profile.
        Interpolates joint updates at `interp_freq` Hz (default 120).

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
        t0 = time.perf_counter()
        step = 0
        while True:
            now = time.perf_counter()
            elapsed = now - t0
            if elapsed >= t_total:
                break

            # scalar motion state at this time
            q, v, a = self.traverse(jerks, ticks, q0=0.0, v0=0.0, a0=0.0, t=elapsed)
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
        t0 = time.perf_counter()
        step = 0

        while True:
            now = time.perf_counter()
            elapsed = now - t0
            if elapsed >= t_total:
                break

            # Distance traveled at this time
            q, v, a = self.traverse(jerks, ticks, q0=0.0, v0=0.0, a0=0.0, t=elapsed)
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
            now = time.perf_counter()
            elapsed = now - t0
            if elapsed >= t_total:
                break

            # scalar motion state at this time
            q, v, a = self.traverse(jerks, ticks, q0=0.0, v0=0.0, a0=0.0, t=elapsed)
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