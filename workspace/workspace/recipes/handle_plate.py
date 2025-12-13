import numpy as np
import workspace.recipes.util as util

class HandlePlate:
    def __init__(self, workspace, core, 
        container, # component
        solid_name = None,
        anchor = None,
        rotation = 340,
        clearance = 20,
        clearance_offset = [0, 0, 180, 180, 0, 0],
        ref_joints = [0, 0, 0, 0, 0, 0, 0, 0],
        speed_factor=0.5,
        left_approach=True,
        base_distance=350,
        rail_step=5.0,
        rail_span=2,
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
        
        # rotation
        self.rotation = rotation

        # clearance
        self.clearance = clearance
        
        # offset
        self.clearance_offset = clearance_offset

        # the reference joints will be on top of the microplate center at 150mm height
        J,C = core.IK(
                    target_solid=self.container.assembly[self.solid_name], 
                    target_anchor=self.anchor, 
                    target_offset=self.clearance_offset, 
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
        
    
    def pick_from(self, index=None, container=None, offset=None, approach=True, exit=True, **kwargs):
        # index
        index = index or self.anchor
        container = container or self.container

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
            print("No tool attached to the robot")
            return False
        
        # we check if there is an item in the index
        solid_parent = util.solid_attached_to_anchor(container.assembly[self.solid_name], index)
        if solid_parent is None:
            print(f"No item found in position {index}")
            return False
        
        # parent component
        component_parent = self.ws.components[solid_parent.component]

        #check if two solids or not
        solid_child = util.solid_attached_to_anchor(solid_parent, "place")
        if solid_child is not None:
            height = np.linalg.norm(np.array(solid_parent.pose("center")[0:3]) - np.array(solid_child.pose("top")[0:3]))
        else:
            height = np.linalg.norm(np.array(solid_parent.pose("center")[0:3]) - np.array(solid_parent.pose("top")[0:3]))
        
        # approach
        approach_path = []
        if approach:
            approach_path = [[0, 0, height+self.clearance, 180, 0, 0]]

        # exit
        exit_path = []
        if exit:
            exit_path = [[0, 0, 2*height+self.clearance, 180, 0, 0]]
        
        # disable and enable
        if hasattr(container, "enable"):
            enable = container.enable[:]
        else:
            enable = []
        
        if hasattr(container, "disable"):
            disable = container.disable[:]
        else:
            disable = []

        # touch
        return util.touch(
            self.core,
            target_solid = container.assembly[self.solid_name],
            target_anchor = index, 
            target_offset = offset or [0, 0, height, 180, 0, 0],
            output_init= tool.disable + enable,
            approach_tool = {"solid": tool.assembly[next(iter(tool.assembly))], "anchor": "tcp", "offset":[0, 0, 0, 0, 0, 0]},
            approach_path = approach_path,
            output_config = tool.enable + disable,
            actions= [],
            sleep= 0.1,
            attach = [component_parent.assembly[next(iter(component_parent.assembly))], {"parent": tool.assembly[next(iter(tool.assembly))], "parent_anchor":"tcp", "child_anchor":"center", "offset": [0, 0, height, 180, 0, 0], "offset_frame": "parent"}],
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


    def place_in(self, index=None, container=None, offset=None, approach=True, exit=True, **kwargs):
        # index
        index = index or self.anchor
        container = container or self.container
        
        # assign kwargs
        for k, v in kwargs.items():
            setattr(self, k, v)
        
        # ref joints
        if self.ref_joints is None:
            print("No reference joints defined")
            return False

        # we check if there is an item in the index
        if util.solid_attached_to_anchor(container.assembly[self.solid_name], index) is not None:
            print(f"There is already an item in position {index}")
            return False

        # tool
        tool = util.tool(self.ws, self.core)
        if tool is None:
            print("No tool attached to the robot")
            return False

        # item in tool
        solid_parent = util.solid_attached_to_tool(tool)
        if solid_parent is None:
            print("No item in the gripper")
            return False
        component_parent = self.ws.components[solid_parent.component]

        # cehck if two solids or not
        solid_child = util.solid_attached_to_anchor(solid_parent, "place")
        if solid_child is not None:
            height = np.linalg.norm(np.array(solid_parent.pose("center")[0:3]) - np.array(solid_child.pose("top")[0:3]))
        else:
            height = np.linalg.norm(np.array(solid_parent.pose("center")[0:3]) - np.array(solid_parent.pose("top")[0:3]))

        # approach
        approach_path = []
        if approach:
            approach_path = [[0, 0, height+self.clearance, 180, 0, 0]]

        # exit
        exit_path = []
        if exit:
            exit_path = [[0, 0, height+self.clearance, 180, 0, 0]]

        # disable and enable
        if hasattr(container, "enable"):
            enable = container.enable[:]
        else:
            enable = []
        
        if hasattr(container, "disable"):
            disable = container.disable[:]
        else:
            disable = []
        
        # touch
        return util.touch(
            self.core,
            target_solid = container.assembly[self.solid_name],
            target_anchor = index, 
            target_offset = offset or [0, 0, height, 180, 0, 0],
            output_init= tool.enable+disable,
            approach_tool = {"solid": tool.assembly[next(iter(tool.assembly))], "anchor": "tcp", "offset":[0, 0, 0, 0, 0, 0]},
            approach_path = approach_path,
            output_config = enable+tool.disable,
            actions= [],
            sleep= 0.1,
            attach = [component_parent.assembly[next(iter(component_parent.assembly))], {"parent": container.assembly[self.solid_name], "parent_anchor":index, "child_anchor":"center", "offset": [0, 0, 0, 0, 0, 0], "offset_frame": "child"}],
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