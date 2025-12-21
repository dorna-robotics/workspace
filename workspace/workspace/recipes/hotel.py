import numpy as np
from dorna2 import pose as dorna_pose
from workspace.recipes.plate import Plate
import workspace.recipes.util as util


class Hotel(Plate):
    def __init__(self, workspace, core, 
        container, # component
        solid_name = None,
        anchor = "center",
        padding = 10,
        gap = 2, # mm
        ref_joints = [0, 0, 0, 0, 0, 0, 0, 0],
        speed_factor=0.5,
        left_approach=True,
        base_distance=350,
        rail_step=10,
        rail_span=20,
        jmove_vaj=[200, 5000, 50000],
        lmove_vaj=[200, 5000, 50000],
        motion="lmove",
        pose_ref=[0, 0, 100, 0, 180, 0],
        **kwargs
        ):

        # super
        super().__init__(
            workspace=workspace,
            core=core,
            container=container,
            solid_name=solid_name,
            anchor=anchor,
            padding=padding,
            gap=gap,
            ref_joints=ref_joints,
            speed_factor=speed_factor,
            left_approach=left_approach,
            base_distance=base_distance,
            rail_step=rail_step,
            rail_span=rail_span,
            jmove_vaj=jmove_vaj,
            lmove_vaj=lmove_vaj,
            motion=motion,
            pose_ref=pose_ref,
            **kwargs
        )
        
    
    def pick_from(self, level=0, container=None, offset=None, approach=True, exit=True, output=True, **kwargs):
        # index
        index = f"place_{level}"
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


        # height load
        height_load = abs(dorna_pose.transform_pose([0, 0, 0, 0, 0, 0], 
                                from_frame=load_list[0].pose("center"),
                                to_frame=load_list[-1].pose("top"))[2])

        # height_container
        height_container = abs(dorna_pose.transform_pose([0, 0, 0, 0, 0, 0], 
                                from_frame=container.assembly[self.solid_name].pose(f"top_{level}"),
                                to_frame=container.assembly[self.solid_name].pose(f"place_{level}"))[2])

        # approach
        approach_path = []
        if approach:
            height_tool = abs(dorna_pose.transform_pose([0, 0, 0, 0, 0, 0], 
                                    from_frame=tool.assembly[next(iter(tool.assembly))].pose("tcp"),
                                    to_frame=tool.assembly[next(iter(tool.assembly))].pose("top"))[2])
            
            approach_path = [[self.container.shape[0]+self.padding, 0, height_load + height_tool+ self.gap, 0, 0, 0], 
                            [self.padding, 0, height_load+height_tool+self.gap, 0, 0, 0],
                            [self.padding, 0, height_load, 0, 0, 0]]
        # exit
        exit_path = []
        if exit:
            exit_path = [[0, 0, height_container+self.gap, 0, 0, 0], 
                        [0, 0, height_container+self.padding, 0, 0, 0], 
                         [self.container.shape[0]+self.padding, 0, height_container+self.padding, 0, 0, 0]]

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
            target_offset = offset or [0, 0, height_load, 0, 0, 0],
            output_init= output_init,
            approach_tool = {"solid": tool.assembly[next(iter(tool.assembly))], "anchor": "tcp", "offset":[0, 0, 0, 0, 180, 0]},
            approach_path = approach_path,
            output_config = output_config,
            actions= [],
            sleep= 0.1,
            attach = [load_list[0], {"parent": tool.assembly[next(iter(tool.assembly))], "parent_anchor":"tcp", "child_anchor":"center", "offset": [0, 0, height_load, 0, 180, 0], "offset_frame": "parent"}],
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


    def place_in(self, level=0, container=None, offset=None, approach=True, exit=True, output=True, load_anchor="center", **kwargs):
        # index
        index = f"place_{level}"
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

        # height_tool
        height_tool = abs(dorna_pose.transform_pose([0, 0, 0, 0, 0, 0], 
                                from_frame=tool.assembly[next(iter(tool.assembly))].pose("tcp"),
                                to_frame=tool.assembly[next(iter(tool.assembly))].pose("top"))[2])

        # item in tool
        solid = util.solid_attached_to_tool(tool)
        if solid is None:
            print("no item in the gripper")
            return False
        
        # load_list
        load_list = [solid]

        # height load
        height_load = abs(dorna_pose.transform_pose([0, 0, 0, 0, 0, 0], 
                                from_frame=load_list[0].pose(load_anchor),
                                to_frame=load_list[-1].pose("top"))[2])

        # height_container
        height_container = abs(dorna_pose.transform_pose([0, 0, 0, 0, 0, 0], 
                                from_frame=container.assembly[self.solid_name].pose(f"top_{level}"),
                                to_frame=container.assembly[self.solid_name].pose(f"place_{level}"))[2])

        # approach
        approach_path = []
        if approach:
            approach_path = [[self.container.shape[0]+self.padding, 0, height_container+self.padding, 0, 0, 0],
                             [0, 0, height_container+self.padding, 0, 0, 0], 
                             [0, 0, height_container+self.gap, 0, 0, 0]]
        # exit
        exit_path = []
        if exit:
            exit_path = [[self.padding, 0, max(height_load, height_container)+ height_tool + self.gap, 0, 0, 0], 
                         [self.container.shape[0]+self.padding, 0, max(height_load, height_container)+ height_tool + self.gap, 0, 0, 0]]

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
            attach = [load_list[0], {"parent": container.assembly[self.solid_name], "parent_anchor":f"place_{level}", "child_anchor":"center", "offset": [0, 0, 0, 0, 0, 0], "offset_frame": "child"}]
            exit_tool = {"solid": tool.assembly[next(iter(tool.assembly))], "anchor": "tcp", "offset":[0, 0, 0, 0, 180, 0]}
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
