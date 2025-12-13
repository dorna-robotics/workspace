import numpy as np
import workspace.recipes.util as util
from workspace.recipes.handle_plate import HandlePlate


class HandleDecapper(HandlePlate):
    def __init__(self, workspace, core, 
        container, # component
        solid_name = None,
        anchor = "place",
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

        super().__init__(
            workspace=workspace,
            core=core,
            container=container,
            solid_name=solid_name,
            anchor=anchor,
            rotation=rotation,
            clearance=clearance,
            clearance_offset=clearance_offset,
            ref_joints=ref_joints,
            speed_factor=speed_factor,
            left_approach=left_approach,
            base_distance=base_distance,
            rail_step=rail_step,
            rail_span=rail_span,
            jmove_vaj=jmove_vaj,
            lmove_vaj=lmove_vaj,
            motion=motion,
            **kwargs
        )


    def decap(self, index=None, approach=True, exit=True, **kwargs):
        # index
        index = index or self.anchor

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
        solid_parent = util.solid_attached_to_anchor(self.container.assembly[self.solid_name], index)
        if solid_parent is None:
            print(f"No item found in position {index}")
            return False
        component_parent = self.ws.components[solid_parent.component]

        #cehck if two solids or not
        solid_child = util.solid_attached_to_anchor(solid_parent, "place")
        if solid_child is None:
            print(f"No item found in position {index}")
            return False
        component_child = self.ws.components[solid_child.component]
        
        # pick
        if not self.pick_from(index="place", container=component_parent, approach=approach, exit=False, **kwargs):
            print("Not able to pick")
            return False

        # decapping
        if component_child.cap_type == "screw":
            # chunks
            twist_chunks = lambda t: ([t % self.rotation] if t % self.rotation else []) + [self.rotation] * (t // self.rotation)
            chunks = [0] + twist_chunks(component_child.twist)
            joint_list = []
            z_offset = 0
            for chunk in chunks:
                z_offset += - component_child.pitch * chunk / self.rotation
                # inverse kinematic
                J,C = self.core.IK(target_solid=tool.assembly[next(iter(tool.assembly))], target_anchor="tcp", target_offset=[0, 0, z_offset, 0, 0, 0],
                                    tool_solid=tool.assembly[next(iter(tool.assembly))], tool_anchor="tcp", tool_offset=[0, 0, 0, 0, 0, 0],
                                    base_distance=self.base_distance, rail_step=self.rail_step, rail_span=self.rail_span, ref_joints=self.ref_joints, left_approach=self.left_approach)        
                if C != 2:
                    print("Could not find valid joints to decap")
                    return False
                
                # end joint
                J[5] = self.rotation/2 - chunk
                joint_list.append(J[:])
            
            # move, startting from rotation/2
            for i in range(len(joint_list)):
                if i < len(joint_list)-1:
                    # disable gripper
                    self.core.robot_api.output(config=tool.disable)
                    
                    # go to start
                    J_start = joint_list[i][:]
                    J_start[5] = -self.rotation/2
                    self.core.robot_api.jmove(J_start, vel=300*self.speed_factor, accel=4000*self.speed_factor, jerk=10000*self.speed_factor)

                    # enable gripper
                    self.core.robot_api.output(config=tool.enable)

                    # uncap
                    self.core.robot_api.lmove(joint_list[i+1], vel=300*self.speed_factor, accel=4000*self.speed_factor, jerk=10000*self.speed_factor)
                else:
                    if exit:
                        # IK go up
                        J,C = self.core.IK(target_solid=tool.assembly[next(iter(tool.assembly))], target_anchor="tcp", target_offset=[0, 0, -self.clearance, 0, 0, 0],
                                            tool_solid=tool.assembly[next(iter(tool.assembly))], tool_anchor="tcp", tool_offset=[0, 0, 0, 0, 0, 0],
                                            base_distance=self.base_distance, rail_step=self.rail_step, rail_span=self.rail_span, ref_joints=self.ref_joints, left_approach=self.left_approach)        
                        if C != 2:
                            print("Could not find valid joints to decap")
                            return False
                        
                        # go up
                        self.core.robot_api.lmove(J, vel=self.lmove_vaj[0]*self.speed_factor, accel=self.lmove_vaj[1]*self.speed_factor, jerk=self.lmove_vaj[2]*self.speed_factor)
        
        # attach
        solid_child.attach_to(parent=tool.assembly[next(iter(tool.assembly))],
                            parent_anchor="tcp",
                            child_anchor="top",
                            offset_frame="parent",
                            offset=[0, 0, 0, 180, 0, 0])
    
        return True


    
    def cap(self, index=None, approach=True, exit=True, **kwargs):
        # index
        index = index or self.anchor
        
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

        # item in tool
        solid_load = util.solid_attached_to_tool(tool)
        if solid_load is None:
            print("No item in the gripper")
            return False
        component_load = self.ws.components[solid_load.component]
        
        # item in the index
        solid_index = util.solid_attached_to_anchor(self.container.assembly[self.solid_name], index)
        if solid_index is None:
            print(f"No item found in position {index}")
            return False
        component_index = self.ws.components[solid_index.component]

        # place
        height_init = component_load.twist * component_load.pitch / 360
        height_cap = np.linalg.norm(np.array(solid_load.pose("center")[0:3]) - np.array(solid_load.pose("top")[0:3]))

        if not self.place_in(index=index, container=component_index, offset=[0, 0, height_cap+height_init, 180, 0, 0], approach=approach, exit=False, **kwargs):
            print("Not able to place")
            return False
            
        # capping
        if component_load.cap_type == "screw":
            # chunks
            twist_chunks = lambda t: ([t % self.rotation] if t % self.rotation else []) + [self.rotation] * (t // self.rotation)
            chunks = [0] + twist_chunks(component_load.twist)[::-1]
            joint_list = []
            z_offset = 0
            for chunk in chunks:
                z_offset += component_load.pitch * chunk / self.rotation
                # inverse kinematic
                J,C = self.core.IK(target_solid=tool.assembly[next(iter(tool.assembly))], target_anchor="tcp", target_offset=[0, 0, z_offset, 0, 0, 0],
                                    tool_solid=tool.assembly[next(iter(tool.assembly))], tool_anchor="tcp", tool_offset=[0, 0, 0, 0, 0, 0],
                                    base_distance=self.base_distance, rail_step=self.rail_step, rail_span=self.rail_span, ref_joints=self.ref_joints, left_approach=self.left_approach)        
                if C != 2:
                    print("Could not find valid joints to cap")
                    return False
                
                # end joint
                J[5] = -self.rotation/2 + chunk
                joint_list.append(J[:])
            
            # move, startting from -rotation/2
            for i in range(len(joint_list)):
                if i < len(joint_list)-1:
                    # disable gripper
                    self.core.robot_api.output(config=tool.disable)
                    
                    # go to start
                    J_start = joint_list[i][:]
                    J_start[5] = -self.rotation/2
                    self.core.robot_api.jmove(J_start, vel=300*self.speed_factor, accel=4000*self.speed_factor, jerk=10000*self.speed_factor)

                    # enable gripper
                    self.core.robot_api.output(config=tool.enable)

                    # cap
                    self.core.robot_api.lmove(joint_list[i+1], vel=300*self.speed_factor, accel=4000*self.speed_factor, jerk=10000*self.speed_factor)
                else:
                    if exit:
                        # IK go up
                        J,C = self.core.IK(target_solid=tool.assembly[next(iter(tool.assembly))], target_anchor="tcp", target_offset=[0, 0, -self.clearance, 0, 0, 0],
                                            tool_solid=tool.assembly[next(iter(tool.assembly))], tool_anchor="tcp", tool_offset=[0, 0, 0, 0, 0, 0],
                                            base_distance=self.base_distance, rail_step=self.rail_step, rail_span=self.rail_span, ref_joints=self.ref_joints, left_approach=self.left_approach)        
                        if C != 2:
                            print("Could not find valid joints to decap")
                            return False
                        
                        # go up
                        self.core.robot_api.lmove(J, vel=self.lmove_vaj[0]*self.speed_factor, accel=self.lmove_vaj[1]*self.speed_factor, jerk=self.lmove_vaj[2]*self.speed_factor)

        # attach cap to body
        solid_load.attach_to(parent=solid_index,
                            parent_anchor="place",
                            child_anchor="center",)
    
        return True


