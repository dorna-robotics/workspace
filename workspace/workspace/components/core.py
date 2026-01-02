# workspace/components/core.py
from copy import deepcopy
from mergedeep import merge
import math
import numpy as np

from camera import Camera
from dorna2 import Solid, Dorna
import dorna2.pose
from workspace.components.factory import register
from path_planning import Planner
from workspace.components.calibration import Calibration


import time

@register("core")
class Core:
    """
    Core component: robot (A0..A5), rail (base + carriage), and plates (plate_0..plate_5).
    Internal attachments are determined by the preset (e.g., 'core500').
    """
    DEFAULTS = dict(
        simulation = True,
        ip = "127.0.0.1",
        has_rail = True,
        rail_cfg = {"type": "rail_hd_500mm", "axis": 6, "offset": 0},
        has_camera = True,
        camera_serial_number = "",
        camera_cfg = {
            "stream": {"width":848, "height":480, "fps":15},
            "K": None,
            "D": None,
            "mode": "bgrd", 
            "filter": {}, 
            "exposure": None,
            "native_res": None,
        },
        has_tool_changer = True,
        tool_changer_output = [[None, None, 0]], # attach signal
    )


    def __init__(self, name: str, cfg: dict, workspace, **kwargs):
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
            self.rail_min = -80.0
            self.rail_max = 420.0
        elif self.rail_cfg["type"] == "rail_hd_1000mm":
            self.rail_min = -80.0
            self.rail_max = 920.0
        elif self.rail_cfg["type"] == "rail_hd_2000mm":
            self.rail_min = -80.0
            self.rail_max = 1920.0
        else:
            raise ValueError(f"Unsupported rail type: {self.rail_cfg['type']}")
        
        # -------- robot
        self.robot_ip = prm["ip"]

        # -------- calibration
        axis_mask = [1,1,1,1,1,1,0,0]
        axis_mask[self.rail_cfg["axis"]] = 1
        self.calibration = Calibration(self.name, axis_mask)


        # -------- tool_changer
        self.has_tool_changer = prm["has_tool_changer"]
        self.tool_changer_output = prm["tool_changer_output"]

        # planner
        self.planner = Planner()

        self.planner.update(
            aux_dir=[[1, 0, 0], [0, 0, 0]],
            aux_limit=[[self.rail_min, self.rail_max], [-1,1]],
        )

        # --- scene dirty tracking & last joints (for Workspace optimization)
        self._last_joints = None
        # Workspace will look at this flag; initialize as dirty so first frame recomputes
        if hasattr(self.workspace, "_scene_dirty"):
            self.workspace._scene_dirty = True


        # optional robot API hookup
        self._simulation_mode = prm["simulation"]
        self.dorna = Dorna()
        if not self._simulation_mode and self.dorna.connect(self.robot_ip):
                self.robot_api = self.dorna
        else:
                self.robot_api = SimulationAPI()
                self._simulation_mode = True

        # ------- camera
        self.has_camera = prm["has_camera"]
        self.camera_serial_number = prm["camera_serial_number"]
        self.camera_cfg = prm["camera_cfg"]
        
        # camera api
        self.camera = None
        if not self._simulation_mode and self.has_camera:
            # init camera
            self.camera = Camera()
            self.camera.connect(serial_number=self.camera_serial_number, **self.camera_cfg)


        # --------- rail base
        rail_hd_500mm_base_anchors = {
        "center": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        "carriage": [0.0, 0.0, 82.0, 0.0, 0.0, 0.0],
        "hole_0": [0.0, 37.5, 0.0, 0.0, 0.0, 0.0],
        "hole_1": [400, 37.5, 0.0, 0.0, 0.0, 0.0],
        "hole_2": [400.0, -37.5, 0.0, 0.0, 0.0, 0.0],
        "hole_3": [0, -37.5, 0.0, 0.0, 0.0, 0.0],
        }
        # we can later add 1000mm and 2000mm rail bases


        # --------- rail carriage
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
                self.rail_base = self.assembly["rail_base"] = Solid(name="rail_base", type="rail_hd_500mm_base", anchors=rail_hd_500mm_base_anchors, component = self.name)
                self.assembly["rail_base"] = self.rail_base
                self.rail_carriage = Solid(name="rail_carriage", type="rail_hd_carriage", anchors=rail_hd_carriage_anchors, component = self.name)
                self.assembly["rail_carriage"] =  self.rail_carriage
             
            else:
                # rail type is not supported
                raise ValueError(f"Unsupported rail type: {self.rail_cfg['type']}")


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
            self.tool_changer_robot_side = Solid(name="tool_changer_robot_side", type="tool_changer_robot_side", anchors=tool_changer_robot_side_anchors, component=self.name)
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
    
    def simulation(self, on: bool = True):
        """
        Switch between simulation and real robot API.
        user will call robot api calls through self.robot_api
        """
        if self._simulation_mode and on:
            # already in simulation
            return
        elif not self._simulation_mode and not on:
            # already in normal mode
            return
        elif self._simulation_mode and not on:
            # switch to real robot
            self._simulation_mode = False
            self.robot_api = self.dorna
            print("Switched to real robot API")
        elif not self._simulation_mode and on:
            # switch to simulation
            self._simulation_mode = True
            self.robot_api = SimulationAPI(joints=self.robot_api.joint())
            print("Switched to simulation API")



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

        if ref_joints is None:
            ref_joints = list(cur)


        # --- helper: rails r where |p - (C0 + [r,0,0])| = base_distance and r ∈ [rmin, rmax]
        def rail_solutions(px, py, pz, c0x, c0y, c0z, d, rmin, rmax):
            dx, dy, dz = px, py - c0y, pz - c0z
            
            # we do not condider z difference only x and y
            dz = 0
            #rhs = d*d - (dy*dy + dz*dz)
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
            weight = [1, 1, 1, 1, 1, 0.25]
            s = 0.0
            for i in (0, 1, 2, 3, 4, 5):
                d = weight[i] * (q[i] - ref_joints[i])
                s += d * d
            return s ** 0.5



        
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
            for r in R:
                # Pose relative to ROBOT BASE
                # now we update robot pose in rail base
                updated_robot_pose_in_rail_base = list(robot_pose_in_rail_base)
                updated_robot_pose_in_rail_base[0] = r-r_cur + robot_pose_in_rail_base[0]

                # now we find update robot pose in world base
                updated_robot_pose_in_world_base = self.rail_base.pose(pose=updated_robot_pose_in_rail_base)

                # now we calculate the transfer matrix for this pose
                T_robot = np.array(dorna2.pose.xyzabc_to_T(updated_robot_pose_in_world_base))
                inv_T_robot = np.linalg.inv(T_robot)

                # now we find the pose of the object in the world frame
                object_pose_in_world = target_solid.pose(anchor=target_anchor, offset=target_offset)
                T_object = np.array(dorna2.pose.xyzabc_to_T(object_pose_in_world))

                # now we find the pose of the object in the robot frame
                T_object_in_robot = inv_T_robot @ T_object
                pose_in_robot = dorna2.pose.T_to_xyzabc(T_object_in_robot)

                # Seed: arm-only initial joints (j0..j5)
                init_arm = [ref_joints[i] for i in range(6)]



                tool_pose = [0,0,0,0,0,0]
                if tool_solid and tool_anchor:
                    tool_pose = tool_solid.pose(anchor=tool_anchor, in_frame=self.robot_flange, offset=tool_offset)

                self.dorna.kinematic.set_tcp_xyzabc(tool_pose)
                # pose_in_robot[3] += 0.01  # to avoid singularity
                # pose_in_robot[4] += 0.01  # to avoid singularity
                # pose_in_robot[5] += 0.01  # to avoid singularity
                sols = self.dorna.kinematic.inv(pose_in_robot, init_arm, True, freedom=None)

 
                if sols is None or len(sols) == 0:
                    continue

                for arm_sol in sols:  # each is a NumPy vector of length 6
                    joint_sol = list(ref_joints)     # start from live joints
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

                return (best[1], 2)
            
            else:
                return(None, -2)



    def stop(self):
        # robot
        if self.dorna:
            self.dorna.close()
        
        # camera
        if self.camera:
            self.camera.close()

    def jmove_no_collision(self, joint, vel=100, accel=1000, jerk=4000):
        print("jmove_no_collision called")
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

        # -------------------------
        # Build collision scene
        # -------------------------
        scene = []
        if hasattr(self.workspace, "compute_collision_boxes"):
            col_boxes = self.workspace.compute_collision_boxes() or []
            for box in col_boxes:
                try:
                    pose = box["pose"]
                    scale = box["scale"]
                    scene.append(
                        Planner.create_cube(pose, [scale[0], scale[1], scale[2]])
                    )
                except Exception:
                    # If a malformed box slips through, skip it rather than failing the whole move
                    continue

        # -------------------------
        # Planner update args
        # -------------------------
        # base_in_world is derived from the rail_base pose in world coordinates.
        # If no rail exists, fall back to robot_A0 (robot base link).
        base_solid = self.rail_base

        base_in_world = list( self.rail_base.pose(anchor="carriage"))

        self.planner.update(
            scene=scene,
            base_in_world=list(base_in_world)
        )

        # -------------------------
        # Plan and execute
        # -------------------------
        start_full = list(self.robot_api.joint())
        goal = list(joint)

        # planner.plan(start, goal): start should match goal dimensionality
        start = start_full[:len(goal)]

        start_time = time.perf_counter()
        
        res = self.planner.plan(start, goal)

        end_time = time.perf_counter()
        execution_time = end_time - start_time
        print(f"Operation finished in {execution_time:.4f} seconds")
        
        #print(res)

        if res is None or len(res) == 0:
            return -1

        # Execute waypoint list using jmove() calls
        cur_full = start_full
        for wp in res:
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
                out = self.robot_api.jmove(wp_full, vel=vel, accel=accel, jerk=jerk)
            except TypeError:
                out = self.robot_api.jmove(wp_full)

            if out not in (2, True, None):
                return out

            cur_full = wp_full

        return 2

    def check_collision(self, j):
        scene = []
        if hasattr(self.workspace, "compute_collision_boxes"):
            col_boxes = self.workspace.compute_collision_boxes() or []
            for box in col_boxes:
                try:
                    pose = box["pose"]
                    scale = box["scale"]
                    scene.append(
                        Planner.create_cube(pose, [scale[0], scale[1], scale[2]])
                    )
                except Exception:
                    # If a malformed box slips through, skip it rather than failing the whole move
                    continue

        # -------------------------
        # Planner update args
        # -------------------------
        # base_in_world is derived from the rail_base pose in world coordinates.
        # If no rail exists, fall back to robot_A0 (robot base link).
        base_solid = self.rail_base

        base_in_world = list( self.rail_base.pose(anchor="carriage"))

        self.planner.update(
            scene=scene,
            base_in_world=list(base_in_world)
        )

        return self.planner.check_collision(j)



class SimulationAPI:
    def __init__(self, joints=[0,0,0,0,0,0,0,0]):
        self.joints = joints 
        self.FREQ = 100000
        self.INTERP_FREQ=120
        self.dorna = Dorna()

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





    def lmove(self, joint, vel=100, accel=1000, jerk=4000, tool_solid=None, tool_anchor=None, tool_offset=[0,0,0,0,0,0]):
        """
        Move from current joint vector to `joint` using an S-curve distance profile.
        Interpolates joint updates at `interp_freq` Hz (default 120).

        Returns:
            -1 : if any error happens
            2 : if successful
        """
        PI  = np.pi
        PI2 = np.pi / 2.0

        DotnaTA_DH = {
            "a":     np.array([0.0, 80.0, 210.0, 0.0, 0.0, 0.0, 0.0], dtype=float),
            "d":     np.array([230.018, 0.0, 0.0, 41.80, 175.0, -89.0, 35.0], dtype=float),
            "alpha": np.array([0.0, PI2, 0.0, PI2, PI2, PI2, 0.0], dtype=float),
            "delta": np.array([0.0, 0.0, 0.0, PI2, PI, PI, 0.0], dtype=float),
            "limit_n": np.array([-185.0, -150.0, -160.0, -175.0, -185.0, -180.0], dtype=float),
            "limit_p": np.array([ 175.0,  210.0,  200.0,  185.0,  175.0,  180.0], dtype=float),
        }

        def T_i(joint, i):
            delta = DotnaTA_DH["delta"][i]
            alpha = DotnaTA_DH["alpha"][i]
            ai    = DotnaTA_DH["a"][i]
            di    = DotnaTA_DH["d"][i]

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

        def solve_cs_equation(aa, bb, cc, i):
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


        def clamp(x, lo, hi):
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



        def xyzj_to_joints(xyzj, curJoints, tool_pose):
            
            T_tool = dorna2.pose.xyzabc_to_T(tool_pose)
            T345 = np.eye(4)
            temp1 = None
            temp2 = None

            xyz = np.array([xyzj[0], xyzj[1], xyzj[2]], dtype=float)
            j345 = np.deg2rad([xyzj[3], xyzj[4], xyzj[5]])

            for i in range(4, 8):
                if i < 7:
                    temp1 = T_i(j345[i - 4], i)
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

            a2 = DotnaTA_DH["a"][1]
            a3 = DotnaTA_DH["a"][2]
            d1 = DotnaTA_DH["d"][0]
            d4 = DotnaTA_DH["d"][3]
            d5 = DotnaTA_DH["d"][4]
            d6 = DotnaTA_DH["d"][5]
            d7 = DotnaTA_DH["d"][6]

            jointMin_ = np.deg2rad(DotnaTA_DH["limit_n"][:6])
            jointMax_ = np.deg2rad(DotnaTA_DH["limit_p"][:6])

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
                    phi = np.arccos(clamp(arg, -1.0, 1.0))

                    if idx_j2 == 0:
                        j1 += phi
                    else:
                        j1 += -phi

                    j1 = _wrap_to_limits(j1, jointMin_[1], jointMax_[1])

                    for idx_j3 in range(2):
                        cj2_sj2 = solve_cs_equation(
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

                self.dorna.kinematic.set_tcp_xyzabc(tool_pose)
                xyz_tmp = self.dorna.kinematic.fw(np.rad2deg(res[i]))

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
            self.dorna.kinematic.set_tcp_xyzabc(tool_pose)
            fk = self.dorna.kinematic.fw(out[:6])
            dx = fk[0] - xyzj[0]
            dy = fk[1] - xyzj[1]
            dz = fk[2] - xyzj[2]
            err = math.sqrt(dx*dx + dy*dy + dz*dz)


            return out




        # --- Setup start/goal
        cur_joints = list(self.joints[:])
        tgt_joints = list(joint)

        # first we set the tool
        tool_pose = [0,0,0,0,0,0]
        if tool_solid and tool_anchor:
            tool_pose = tool_solid.pose(anchor=tool_anchor, in_frame=self.robot_flange, offset=tool_offset)


        #self.dorna.kinematic.set_tcp_xyzabc(tool_pose)
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
            J = xyzj_to_joints(xyz_joints, self.joints, tool_pose)

            
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
    
    # output
    def output(self, index=None, val=None, config=None):
        if config is not None:
            for c in config:
                if len(c) > 2 and c[2] > 0:
                    self.sleep(c[2])
        return True
