# workspace/components/core.py
from dorna2 import Solid, Dorna
from workspace.components.factory import register


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

        # optional robot API hookup
        self.robot_api = None
        if self.robot_ip:
            try:
                self.robot_api = Dorna()
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
        # self.has_tool_changer = cfg.get("has_tool_changer", False)
        # if self.has_tool_changer:
        #     tool_changer_anchors = {
        #         "input": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        #         "output": [0.0, 0.0, 60.0, 0.0, 0.0, 0.0],
        #     }
        #     self.tool_changer = Solid(name="tool_changer", type="tool_changer", anchors=tool_changer_anchors, component=self.name)
        #     self.assembly["tool_changer"] = self.tool_changer
        #     self.tool_changer.attach_to(parent=self.robot_flange, parent_anchor="output", child_anchor="input", offset=[0, 0, 0, 0, 0, 0])




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
            joints = self.robot_api.joint()  # expect list/tuple of 5 floats
        except Exception:
            return


        self.rail_carriage.attach_to(parent =self.rail_base, parent_anchor="center", child_anchor="center", offset =[joints[self.aux_axis],0,82,0,0,0])

        self.robot_A1.attach_to(parent=self.robot_A0, parent_anchor="output", child_anchor="input", offset=[0, 0, 0, 0, 0, joints[0]])
        self.robot_A2.attach_to(parent=self.robot_A1, parent_anchor="output", child_anchor="input", offset=[0, 0, 0, 0, 0, joints[1]])
        self.robot_A3.attach_to(parent=self.robot_A2, parent_anchor="output", child_anchor="input", offset=[0, 0, 0, 0, 0, joints[2]])
        self.robot_A4.attach_to(parent=self.robot_A3, parent_anchor="output", child_anchor="input", offset=[0, 0, 0, 0, 0, joints[3]])
        self.robot_A5.attach_to(parent=self.robot_A4, parent_anchor="output", child_anchor="input", offset=[0, 0, 0, 0, 0, -joints[4]])
        self.robot_flange.attach_to(parent=self.robot_A5, parent_anchor="output", child_anchor="input", offset=[0, 0, 0, 0, 0, joints[5]])





    def stop(self):

        if self.robot_api:
            self.robot_api.close()
