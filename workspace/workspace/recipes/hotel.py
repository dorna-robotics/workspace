"""
place, use the load in the gripper and place it in the container
pick, use the raw tool
"""
import numpy as np
import workspace.recipes.util as util

class Hotel:
    def __init__(self, workspace, core, 
        container, # component
        solid_name = None,
        anchor = None,
        padding = 10,
        height = 100,
        ref_joints = [0, 0, 0, 0, 0, 0, 0, 0],
        speed_factor=0.5,
        left_approach=True,
        base_distance=300,
        rail_step=5.0,
        rail_span=6,
        jmove_vaj=[200, 5000, 50000],
        lmove_vaj=[200, 5000, 50000],
        motion="lmove",
        **kwargs
        ):
        # kwargs
        for k, v in kwargs.items():
            setattr(self, k, v)
        
        # init
        self.ws = workspace
        self.core = core
        self.container = container
        self.ref_joints = ref_joints
        self.base_distance = base_distance
        self.rail_step = rail_step
        self.rail_span = rail_span
        self.left_approach = left_approach
        self.speed_factor = speed_factor
        self.jmove_vaj = jmove_vaj
        self.lmove_vaj = lmove_vaj
        self.motion = motion  


        # container
        self.solid_name =  solid_name or next(iter(container.assembly))
        self.anchor = anchor or "center"
        

        # clearance
        self.padding = padding
        self.height = height
        
        # the reference joints will be on top of the microplate center at 150mm height
        J,C = core.IK(
                    target_solid=self.container.assembly[self.solid_name], 
                    target_anchor=self.anchor, 
                    target_offset=[0, 0, self.height, 0, 180, 0], 
                    base_distance=self.base_distance,
                    rail_step=self.rail_step, 
                    rail_span=self.rail_span,
                    ref_joints=self.ref_joints,
                    left_approach=self.left_approach)
        if C == 2:
            self.ref_joints = J
        else:
            print("Could not find a valid reference joint to approach the container")
            return
        
    
    def pick_from(self, level=0, approach=True, exit=True, **kwargs):
        # index
        index = f"place_{level}"

        # assign kwargs
        for k, v in kwargs.items():
            setattr(self, k, v)
        
        # ref joints
        if self.ref_joints is None:
            print("No reference joints defined")
            return False
        
        # tool
        tool = util.tool(self.ws, self.core)
        if tool is None:
            print("No tool attached")
            return False
        
        # we check if there is an item in the index
        solid_child = util.solid_attached_to_anchor(self.container.assembly[self.solid_name], f"place_{level}")
        if solid_child is None:
            print(f"No item found")
            return False
        
        # parent component
        component_child = self.ws.components[solid_child.component]

        # height
        height = np.linalg.norm(np.array(self.container.assembly[self.solid_name].pose(f"place_{level}")[0:3]) - np.array(self.container.assembly[self.solid_name].pose(f"top_{level}")[0:3]))
        
        # approach
        approach_path = []
        if approach:
            approach_path = [[self.container.shape[0]+self.padding, 0, height+self.padding, 0, 180, 0], [self.padding, 0, height+self.padding, 0, 180, 0]]

        # exit
        exit_path = []
        if exit:
            exit_path = [[0, 0, height+self.padding, 0, 180, 0], [self.container.shape[0]+self.padding, 0, height+self.padding, 0, 180, 0]]

        # touch
        return util.touch(
            self.core,
            target_solid = self.container.assembly[self.solid_name],
            target_anchor = index, 
            target_offset = [0, 0, 0, 0, 180, 0],
            output_init= tool.disable,
            approach_tool = {"solid": tool.assembly[next(iter(tool.assembly))], "anchor": "tcp", "offset":[0, 0, 0, 0, 0, 0]},
            approach_path = approach_path,
            output_config = tool.enable,
            actions= [],
            sleep= 0.1,
            attach = [component_child.assembly[next(iter(component_child.assembly))], {"parent": tool.assembly[next(iter(tool.assembly))], "parent_anchor":"tcp", "child_anchor":"center", "offset": [0, 0, 0, 0, 180, 0], "offset_frame": "parent"}],
            exit_tool = {"solid": tool.assembly[next(iter(tool.assembly))], "anchor": "tcp", "offset":[0, 0, 0, 0, 0, 0]},
            exit_path = exit_path,
            motion = self.motion,
            base_distance = self.base_distance, 
            rail_step = self.rail_step,
            rail_span = self.rail_span,
            left_approach = self.left_approach,
            ref_joints = self.ref_joints,
            jmove_vaj = self.jmove_vaj,
            lmove_vaj = self.lmove_vaj,
            speed_factor = self.speed_factor,
        )


    def place_in(self, level=0, approach=True, exit=True, **kwargs):
        # index
        index = f"place_{level}"
        
        # assign kwargs
        for k, v in kwargs.items():
            setattr(self, k, v)
        
        # ref joints
        if self.ref_joints is None:
            print("No reference joints defined")
            return False

        # we check if there is an item in the index
        if util.solid_attached_to_anchor(self.container.assembly[self.solid_name], "place") is not None:
            print(f"There is already an item in the container")
            return False

        # tool
        tool = util.tool(self.ws, self.core)
        if tool is None:
            print("No tool attached to the robot")
            return False

        # item in tool
        solid_load = util.solid_attached_to_tool(tool)
        if solid_load is None:
            print("No item in the gripper")
            return False
        component_load = self.ws.components[solid_load.component]

        # height
        height = np.linalg.norm(np.array(self.container.assembly[self.solid_name].pose(f"place_{level}")[0:3]) - np.array(self.container.assembly[self.solid_name].pose(f"top_{level}")[0:3]))
        
        # approach
        approach_path = []
        if approach:
            approach_path = [[self.container.shape[0]+self.padding, 0, height+self.padding, 0, 0, 0], [0, 0, height+self.padding, 0, 0, 0]]

        # exit
        exit_path = []
        if exit:
            exit_path = [[self.padding, 0, height+self.padding, 0, 0, 0], [self.container.shape[0]+self.padding, 0, height+self.padding, 0, 0, 0]]
        
        # touch
        return util.touch(
            self.core,
            target_solid = self.container.assembly[self.solid_name],
            target_anchor = index, 
            target_offset = [0, 0, 0, 0, 0, 0],
            output_init= tool.enable,
            approach_tool = {"solid": solid_load, "anchor": "center", "offset":[0, 0, 0, 0, 0, 0]},
            approach_path = approach_path,
            output_config = tool.disable,
            actions= [],
            sleep= 0.1,
            attach = [component_load.assembly[next(iter(component_load.assembly))], {"parent": self.container.assembly[self.solid_name], "parent_anchor":f"place_{level}", "child_anchor":"center", "offset": [0, 0, 0, 0, 0, 0], "offset_frame": "child"}],
            exit_tool = {"solid": tool.assembly[next(iter(tool.assembly))], "anchor": "tcp", "offset":[0, 0, 0, 0, 180, 0]},
            exit_path = exit_path,
            motion = self.motion,
            base_distance = self.base_distance, 
            rail_step = self.rail_step,
            rail_span = self.rail_span,
            left_approach = self.left_approach,
            ref_joints = self.ref_joints,
            jmove_vaj = self.jmove_vaj,
            lmove_vaj = self.lmove_vaj,
            speed_factor = self.speed_factor,   
        )