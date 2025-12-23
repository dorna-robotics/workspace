# workspace/recipes/recipe.py
# the super class for all recipes
# includes handy functions that are frequently used in recipes

class Recipe:
    def __init__(self, workspace, core, speed_factor=0.5,left_approach=True,base_distance=350,rail_step=5.0,rail_span=2):
        self.ws = workspace
        self.core = core
        self.tool = None
        self.ref_joints = None
        self.speed_factor = speed_factor
        self.left_approach = left_approach
        self.base_distance = base_distance
        self.rail_step = rail_step
        self.rail_span = rail_span

        # first we assign the reference joints
        J,C = core.IK(target_solid=self.tool_rack.assembly["tool_rack"], target_anchor="tool_connection", target_offset=[0,0,-50,0,0,0], base_distance=self.base_distance,
        rail_step=self.rail_step, rail_span=self.rail_span, left_approach=self.left_approach,tool_solid=self.core.toolchanger_robot_side, tool_anchor="toolchanger_connection", tool_offset=[0,0,0,0,0,0],ref_joints=[0,0,0,0,0,0,0,0])
        if C == 2:
            self.ref_joints = J
        else:
            print("Could not find a valid reference pose to approach the tool rack")
            print("C=",C)
            return
        

    def calibrate(self, solid, ):
        # when the use

