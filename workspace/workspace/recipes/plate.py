import numpy as np
from dorna2 import pose as dorna_pose
import workspace.recipes.util as util

class Plate:
    def __init__(self, workspace, core, 
        container, # component
        solid_name = None,
        anchor = None,
        padding = 50,
        gap = 2, # mm
        ref_joints = [0, 0, 0, 0, 0, 0, 0, 0],
        speed_factor=0.5,
        left_approach=True,
        base_distance=350,
        rail_step=5.0,
        rail_span=10,
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

        # padding
        self.padding = padding
        self.gap = gap
        target_offset = [0, 0, self.padding, 180, 0, 0]

        # assign kwargs
        for k, v in kwargs.items():
            setattr(self, k, v)

        # the reference joints will be on top of the microplate center at 150mm height
        J,C = self.core.IK(
                    target_solid=self.container.assembly[self.solid_name], 
                    target_anchor=self.anchor, 
                    target_offset=target_offset,
                    base_distance=self.base_distance,
                    rail_step=self.rail_step, 
                    rail_span=self.rail_span,
                    ref_joints=self.ref_joints,
                    left_approach=self.left_approach)
        if C == 2:
            self.ref_joints = J
        else:
            print("could not find a valid reference joint to approach the container")
            return
    

    def pick_from(self, index=None, container=None, offset=None, approach=True, exit=True, output=True, **kwargs):
        # index
        index = index or self.anchor
        container = container or self.container

        # assign kwargs
        for k, v in kwargs.items():
            setattr(self, k, v)
        
        # ref joints
        if self.ref_joints is None:
            print("no reference joints defined")
            return False
        
        # tool
        tool = util.tool(self.ws, self.core)
        if tool is None:
            print("no tool attached to the robot")
            return False
        
        # we check if there is an item in the index
        solid = util.solid_attached_to_anchor(container.assembly[self.solid_name], index)
        if solid is None:
            print(f"no item found in position {index}")
            return False
        
        # load_list
        load_list = [solid]

        # find all the items attached to the tool
        while True:
            child = util.solid_attached_to_anchor(load_list[-1], "place")
            if child is not None:
                load_list.append(child)
            else:
                break

        # height
        height = np.linalg.norm(np.array(load_list[0].pose("center")[0:3]) - np.array(load_list[-1].pose("top")[0:3]))

        # height_container
        height_container = np.linalg.norm(np.array(container.assembly[self.solid_name].pose("top")[0:3]) - np.array(container.assembly[self.solid_name].pose("place")[0:3]))

        # approach
        approach_path = []
        if approach:
            height_tool = abs(dorna_pose.transform_pose([0, 0, 0, 0, 0, 0], 
                                    from_frame=tool.assembly[next(iter(tool.assembly))].pose("tcp"),
                                    to_frame=tool.assembly[next(iter(tool.assembly))].pose("top"))[2])

            approach_path = [[0, 0, max(height,height_container) + self.padding, 0, 0, 0], 
                            [0, 0, height+height_tool+self.gap, 0, 0, 0]]
        # exit
        exit_path = []
        if exit:
            exit_path = [[0, 0, height_container+self.gap, 0, 0, 0], 
                        [0, 0, max(height,height_container)+self.padding, 0, 0, 0]]
            
        # disable and enable
        if hasattr(container, "enable"):
            enable = container.enable[:]
        else:
            enable = []
        
        if hasattr(container, "disable"):
            disable = container.disable[:]
        else:
            disable = []

        # output
        output_init = enable[:]
        output_config = disable[:]
        if output:
            output_init = tool.disable + enable
            output_config = tool.enable + disable
        
        # touch
        return util.touch(
            self.core,
            target_solid = container.assembly[self.solid_name],
            target_anchor = index, 
            target_offset = offset or [0, 0, height, 0, 0, 0],
            output_init= output_init,
            approach_tool = {"solid": tool.assembly[next(iter(tool.assembly))], "anchor": "tcp", "offset":[0, 0, 0, 180, 0, 0]},
            approach_path = approach_path,
            output_config = output_config,
            actions= [],
            sleep= 0.1,
            attach = [load_list[0], {"parent": tool.assembly[next(iter(tool.assembly))], "parent_anchor":"tcp", "child_anchor":"center", "offset": [0, 0, height, 180, 0, 0], "offset_frame": "parent"}],
            exit_tool = {"solid": load_list[0], "anchor": "center", "offset":[0, 0, 0, 0, 0, 0]},
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


    def place_in(self, index=None, container=None, offset=None, approach=True, exit=True, output=True, load_anchor="center", **kwargs):
        # index
        index = index or self.anchor
        container = container or self.container
        
        # assign kwargs
        for k, v in kwargs.items():
            setattr(self, k, v)
        
        # ref joints
        if self.ref_joints is None:
            print("no reference joints defined")
            return False

        # we check if there is an item in the index
        if util.solid_attached_to_anchor(container.assembly[self.solid_name], index) is not None:
            print(f"there is already an item in position {index}")
            return False

        # tool
        tool = util.tool(self.ws, self.core)
        if tool is None:
            print("no tool attached to the robot")
            return False

        # item in tool
        solid = util.solid_attached_to_tool(tool)
        if solid is None:
            print("no item in the gripper")
            return False
        
        # load_list
        load_list = [solid]

        # find all the items attached to the tool
        while True:
            child = util.solid_attached_to_anchor(load_list[-1], "place")
            if child is not None:
                load_list.append(child)
            else:
                break

        # height
        height = np.linalg.norm(np.array(load_list[0].pose(load_anchor)[0:3]) - np.array(load_list[-1].pose("top")[0:3]))

        # height_container
        height_container = abs(dorna_pose.transform_pose([0, 0, 0, 0, 0, 0], 
                                from_frame=container.assembly[self.solid_name].pose("top"),
                                to_frame=container.assembly[self.solid_name].pose("place"))[2])

        # approach
        approach_path = []
        if approach:
            approach_path = [[0, 0, max(height, height_container)+self.padding, 0, 0, 0], 
                            [0, 0, height_container+self.gap, 0, 0, 0]]

        # exit
        exit_path = []
        if exit:
            exit_path = [[0, 0, max(height, height_container)+self.padding, 0, 0, 0]]

        # disable and enable
        if hasattr(container, "enable"):
            enable = container.enable[:]
        else:
            enable = []
        
        if hasattr(container, "disable"):
            disable = container.disable[:]
        else:
            disable = []

        # approach, attach and exit
        approach_tool = {"solid": load_list[0], "anchor": load_anchor, "offset":[0, 0, 0, 0, 0, 0]}
        attach = [None, {"parent":None, "parent_anchor":None, "child_anchor":None, "offset":[0, 0, 0, 0, 0, 0], "offset_frame":"parent"}]
        exit_tool = {"solid": load_list[0], "anchor": load_anchor, "offset":[0, 0, 0, 0, 0, 0]}
        # output
        output_init = enable[:]
        output_config = disable[:]
        if output:
            output_init = tool.disable + enable
            output_config = tool.enable + disable
            
            # attach and exit tool
            attach = [load_list[0], {"parent": container.assembly[self.solid_name], "parent_anchor":index, "child_anchor":load_anchor, "offset": [0, 0, 0, 0, 0, 0], "offset_frame": "child"}]
            exit_tool = {"solid": tool.assembly[next(iter(tool.assembly))], "anchor": "tcp", "offset":[0, 0, 0, 180, 0, 0]}
        # touch
        return util.touch(
            self.core,
            target_solid = container.assembly[self.solid_name],
            target_anchor = index, 
            target_offset = offset or [0, 0, 0, 0, 0, 0],
            output_init= output_init,
            approach_tool = approach_tool,
            approach_path = approach_path,
            output_config = output_config,
            actions= [],
            sleep= 0.1,
            attach = attach,
            exit_tool = exit_tool,
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
    


    """
    bring and align the tool anchor with the given index (anchor) in the given container (component), with the given offset
    """    
    def tool_to(self, index=None, container=None, offset=None, tool_anchor="center", **kwargs):
        # index
        index = index or self.anchor
        container = container or self.container

        # assign kwargs
        for k, v in kwargs.items():
            setattr(self, k, v)
        
        # ref joints
        if self.ref_joints is None:
            print("no reference joints defined")
            return False
        
        # tool
        approach_tool = {"solid": None, "anchor": None, "offset":None}
        height_tool = 0
        tool = util.tool(self.ws, self.core)
        if tool is not None:
            approach_tool = {"solid": tool.assembly[next(iter(tool.assembly))], "anchor": tool_anchor, "offset":[0, 0, 0, 0, 0, 0]},
            height_tool = abs(dorna_pose.transform_pose([0, 0, 0, 0, 0, 0], 
                                    from_frame=tool.assembly[next(iter(tool.assembly))].pose(tool_anchor),
                                    to_frame=tool.assembly[next(iter(tool.assembly))].pose("top"))[2])

        # height_container
        height_container = abs(dorna_pose.transform_pose([0, 0, 0, 0, 0, 0], 
                                from_frame=container.assembly[self.solid_name].pose("top"),
                                to_frame=container.assembly[self.solid_name].pose("place"))[2])

        # approach
        approach_path = [[0, 0, height_container+ self.padding, 0, 0, 0], 
                        [0, 0, height_tool+self.gap, 0, 0, 0]]
        
        # touch
        return util.touch(
            self.core,
            target_solid = container.assembly[self.solid_name],
            target_anchor = index, 
            target_offset = offset or [0, 0, 0, 0, 0, 0],
            approach_tool = approach_tool,
            approach_path = approach_path,
            actions= [],
            sleep= 0,
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


    """
    use go to bring the tcp at specific offset of the anchor
    """
    def calibrate(self, index=None, container=None, offset=[0, 0, -20, 0, 0, 0], **kwargs):
        # tool
        tool = util.tool(self.ws, self.core)
        if tool is None:
            print("no tool attached to the robot")
            return False

        # the reference joints
        J,C = self.core.IK(
                    target_solid=self.container.assembly[self.solid_name], 
                    target_anchor=index,
                    tool_solid=tool.assembly[next(iter(tool.assembly))],
                    tool_anchor="tcp",
                    base_distance=self.base_distance,
                    rail_step=self.rail_step, 
                    rail_span=self.rail_span,
                    ref_joints=self.ref_joints,
                    left_approach=self.left_approach)
        if C != 2:
            print("could not find a valid joint for calibration")
            return False

        # go
        if not self.tool_to(index=index, container=container, offset=offset, **kwargs):
            print("can not go to the calibration anchor")
            return False

        # disable motor
        if input("press Enter to disable the motors..."):
            print("calibration processed canceled")
            return False
        self.core.robot_api.motor(0)

        # record joints
        if input("press Enter to record the position..."):
            print("calibration processed canceled")
            return False
        encoder = self.core.robot_api.joint()

        # enable motor
        if input("press Enter to enable the motors and complete the calibration..."):
            print("calibration processed canceled")
            return False
        self.core.robot_api.motor(0)

        # how to store data
        #???