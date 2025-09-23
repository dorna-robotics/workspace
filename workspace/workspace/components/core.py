# workspace/components/core.py
from dorna2 import Solid, Dorna
from workspace.components.factory import register
import math
import time

@register("core")
class Core:
    """
    Core component: robot (A0..A5), rail (base + carriage), and plates (plate_0..plate_5).
    Internal attachments are determined by the preset (e.g., 'core500').
    """

    def __init__(self, name: str, cfg: dict):
        self.name = name
        self.type = "core"
        self.assembly = {}

        # ---- read config ----
        self.preset = cfg.get("preset", "core500")
        self.robot_ip = cfg.get("ip")
        self.aux_axis = cfg.get("aux_axis", 6)
        self.rail_offset = cfg.get("rail_offset", 0)
        self.toolchanger_output = cfg.get("toolchanger_output", 0)

        # optional robot API hookup

        
        self._simulation_mode = False
        self.robot_api = None
        if self.robot_ip:
            try:
                self.dorna = Dorna()
                self.robot_api = self.dorna
                self.robot_api.connect(self.robot_ip)
            except Exception:
                # keep going without a live robot
                self.robot_api = None


        # now we buiild all anchors for the following items:
        # --------- plate
        plate_anchors = {}
        # 10 x 20 grid (A..J, 1..20), 25mm pitch, + convenience anchors
        plate_x_start = -237.5
        plate_y_start = 112.5
        plate_pitch = 25.0
        rows = [chr(c) for c in range(ord("A"), ord("J") + 1)]  # A..J
        cols = range(1, 21)  # 1..20
        for r_idx, r in enumerate(rows):
            y = plate_y_start - r_idx * plate_pitch
            for c in cols:
                x = plate_x_start + (c - 1) * plate_pitch
                plate_anchors[f"{r}{c}"] = [x, y, 7.0, 0.0, 0.0, 0.0]
        plate_anchors["corner_0"] = [-250.0, 125.0, 7.0, 0.0, 0.0, 0.0]
        plate_anchors["corner_1"] = [250.0, 125.0, 7.0, 0.0, 0.0, 0.0]
        plate_anchors["corner_2"] = [250.0, -125.0, 7.0, 0.0, 0.0, 0.0]
        plate_anchors["corner_3"] = [-250.0, -125.0, 7.0, 0.0, 0.0, 0.0]
        plate_anchors["center"] = [0.0, 0.0, 7.0, 0.0, 0.0, 0.0]


        # --------- rail base
        rail_base_500mm_anchors = {
        "center": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        "hole_0": [0.0, 37.5, 0.0, 0.0, 0.0, 0.0],
        "hole_1": [400, 37.5, 0.0, 0.0, 0.0, 0.0],
        "hole_2": [400.0, -37.5, 0.0, 0.0, 0.0, 0.0],
        "hole_3": [0, -37.5, 0.0, 0.0, 0.0, 0.0],
        }


        # --------- rail carriage
        rail_carriage_anchors = {
            "center": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
            "hole_0": [-50.0, 50.0, 0.0, 0.0, 0.0, 0.0],
            "hole_1": [50.0, 50.0, 0.0, 0.0, 0.0, 0.0],
            "hole_2": [50.0, -50.0, 0.0, 0.0, 0.0, 0.0],
            "hole_3": [-50.0, -50.0, 0.0, 0.0, 0.0, 0.0],
        }


        if self.preset == "core500":
            # we add 6 plates
            # world-space placement centers to keep the 3x2 array centered
            
            PLATE_W = 500.0  # along X
            PLATE_H = 250.0  # along Y
            world_centers = {
                "plate_0": (-PLATE_W / 2, +PLATE_H),   # top-left
                "plate_1": (-PLATE_W / 2, 0.0),        # mid-left
                "plate_2": (-PLATE_W / 2, -PLATE_H),   # bot-left
                "plate_3": (PLATE_W / 2, +PLATE_H),    # top-right
                "plate_4": (PLATE_W / 2, 0.0),         # mid-right
                "plate_5": (PLATE_W / 2, -PLATE_H),    # bot-right
            }

            for name, (x, y) in world_centers.items():
                self.assembly[name] = Solid(
                    name=name,
                    type="fixture_plate",           # matches static/CAD/fixture_plate.glb
                    anchors=plate_anchors,
                    parent=None,                    # world-relative
                    component = self.name,
                    pose=[x, y, 0.0, 0.0, 0.0, 0.0] # place in world
                )
            self.plate_0 = self.assembly["plate_0"]
            self.plate_1 = self.assembly["plate_1"]
            self.plate_2 = self.assembly["plate_2"]
            self.plate_3 = self.assembly["plate_3"]
            self.plate_4 = self.assembly["plate_4"]
            self.plate_5 = self.assembly["plate_5"]


            # next we add 500mm rail base
            self.rail_base = self.assembly["rail_base"] = Solid(name="rail_base", type="rail_base_500mm", anchors=rail_base_500mm_anchors, component = self.name)
            self.assembly["rail_base"] = self.rail_base
        else:
            raise ValueError(f"Unsupported core preset: {self.preset}")

        

        self.rail_base.attach_to(parent=self.plate_1, parent_anchor='D10', child_anchor= 'hole_0', offset=[0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
        
        self.rail_carriage = Solid(name="rail_carriage", type="rail_carriage", anchors=rail_carriage_anchors, component = self.name)
        self.assembly["rail_carriage"] =  self.rail_carriage
        self.rail_carriage.attach_to(parent =self.rail_base, parent_anchor="center", child_anchor="center", offset =[0,0,82,0,0,0])

        robot_A0_anchors = {
            "input": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
            "output": [0.0, 0.0, 131.0, 0.0, 0.0, 0.0],
            "0": [35.0, 50.0, 0.0, 0.0, 0.0, 0.0],
            "1": [35.0, -50.0, 0.0, 0.0, 0.0, 0.0],
            "2": [-15.0, 50.0, 0.0, 0.0, 0.0, 0.0],
            "3": [-15.0, -50.0, 0.0, 0.0, 0.0, 0.0],
            "4": [-65.0, 50.0, 0.0, 0.0, 0.0, 0.0],
            "5": [-65.0, -50.0, 0.0, 0.0, 0.0, 0.0],
            "6": [-115.0, 50.0, 0.0, 0.0, 0.0, 0.0],
            "7": [-115.0, -50.0, 0.0, 0.0, 0.0, 0.0],
        }
        robot_A1_anchors = {
            "input": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
            "output": [80.0, 36.0, 99.01829, 90.0, 0.0, 0.0],
        }
        robot_A2_anchors = {
            "input": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
            "output": [210.0, 0.0, 4.8, 0.0, 0.0, 0.0],
        }
        robot_A3_anchors = {
            "input": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
            "output": [29.0, 0.0, 72.0, 0.0, 90.0, 0.0],
        }
        robot_A4_anchors = {
            "input": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
            "output": [29.0, 0.0, 146.0, 0.0, 89.9999, 0.0],
        }
        robot_A5_anchors = {
            "input": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
            "output": [-29.0, 0.0, 60.0, 0.0, -90.0, 0.0],
        }
        robot_flange_anchors = {
            "input": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
            "output": [0, 0.0, 6.0, 0.0, 0, 0.0],
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

        # chain robot links via anchors (static zero joints to start)
        
        self.robot_A0.attach_to(parent=self.rail_carriage, parent_anchor="hole_1", child_anchor="0", offset=[0, 0, 0, 0, 0, 0])
        self.robot_A1.attach_to(parent=self.robot_A0, parent_anchor="output", child_anchor="input", offset=[0, 0, 0, 0, 0, 0])
        self.robot_A2.attach_to(parent=self.robot_A1, parent_anchor="output", child_anchor="input", offset=[0, 0, 0, 0, 0, 0])
        self.robot_A3.attach_to(parent=self.robot_A2, parent_anchor="output", child_anchor="input", offset=[0, 0, 0, 0, 0, 0])
        self.robot_A4.attach_to(parent=self.robot_A3, parent_anchor="output", child_anchor="input", offset=[0, 0, 0, 0, 0, 0])
        self.robot_A5.attach_to(parent=self.robot_A4, parent_anchor="output", child_anchor="input", offset=[0, 0, 0, 0, 0, 0])
        self.robot_flange.attach_to(parent=self.robot_A5, parent_anchor="output", child_anchor="input", offset=[0, 0, 0, 0, 0, 0])
        # done

        # we check if there is tool changer
        self.has_toolchanger = cfg.get("has_toolchanger", False)
        if self.has_toolchanger:
            toolchanger_robot_side_anchors = {
            "input": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
            "output": [0.0, 0.0, 22.0, 0.0, 0.0, 0.0],
            }
            self.toolchanger_robot_side = Solid(name="toolchanger_robot_side", type="toolchanger_robot_side", anchors=toolchanger_robot_side_anchors, component=self.name)
            self.assembly["toolchanger_robot_side"] = self.toolchanger_robot_side
            self.toolchanger_robot_side.attach_to(parent=self.robot_flange, parent_anchor="output", child_anchor="input", offset=[0, 0, 0, 0, 0, 0])



    # -------------------------------------------------------------------------
    # live joint update
    # -------------------------------------------------------------------------

    def update_pose(self):
        """
        If a Dorna robot connection exists, update A1..A5 relative rotations
        by attaching each link with a Z-rotation equal to the corresponding joint angle.
        """
        if self.robot_api is None:
            return

        try:
            joints = self.robot_api.joint()  # expect list/tuple of 8 floats
        except Exception:
            return


        self.rail_carriage.attach_to(parent =self.rail_base, parent_anchor="center", child_anchor="center", offset =[joints[self.aux_axis],0,82,0,0,0])

        self.robot_A1.attach_to(parent=self.robot_A0, parent_anchor="output", child_anchor="input", offset=[0, 0, 0, 0, 0, joints[0]])
        self.robot_A2.attach_to(parent=self.robot_A1, parent_anchor="output", child_anchor="input", offset=[0, 0, 0, 0, 0, joints[1]])
        self.robot_A3.attach_to(parent=self.robot_A2, parent_anchor="output", child_anchor="input", offset=[0, 0, 0, 0, 0, joints[2]])
        self.robot_A4.attach_to(parent=self.robot_A3, parent_anchor="output", child_anchor="input", offset=[0, 0, 0, 0, 0, joints[3]])
        self.robot_A5.attach_to(parent=self.robot_A4, parent_anchor="output", child_anchor="input", offset=[0, 0, 0, 0, 0, -joints[4]])
        self.robot_flange.attach_to(parent=self.robot_A5, parent_anchor="output", child_anchor="input", offset=[0, 0, 0, 0, 0, joints[5]])

    def simulation(self, on: bool = True):
        """
        user will call robot api calls through core.robot_api()
        if simulation mode is on, the robot api will exctue certaion functions in simulation mode
        """
        if self._simulation_mode == True and on == True:
            pass
        elif self._simulation_mode == False and on == False:
            pass
        elif self._simulation_mode == True and on == False:
            self._simulation_mode = False
            self.robot_api = self.dorna
        elif self._simulation_mode == False and on == True:
            simulation_api = SimulationAPI(joints = self.robot_api.joint())
            self.robot_api = simulation_api

        


    def stop(self):

        if self.dorna:
            self.dorna.close()


class SimulationAPI:
    def __init__(self, joints=[0,0,0,0,0,0,0,0]):
        self.joints = joints 
        self.FREQ = 100000
        self.INTERP_FREQ=120

    def joint(self):
        return self.joints
    
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

        try:
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

            try:
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

            finally:
                # ensure exact final value
                self.joints = tgt[:]

            return 2  # success

        except Exception:
            return -1  # any unexpected error



