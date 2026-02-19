from copy import deepcopy
from mergedeep import merge
from dorna2 import pose as dorna_pose
from workspace.recipes.recipe import Recipe


class Decapper(Recipe):
    DEFAULTS = dict(
        # IK
        base_distance=50,
        # calibration
        calibration_targets={"body": ["clb_0", "clb_1"]}, # {solid_name: {anchor_1:..., anchor_2:...},...}
    )

    def __init__(self, workspace, core, component, **kwargs):
        # prm
        prm = deepcopy(Recipe.DEFAULTS) # default
        merge(prm, self.DEFAULTS) # self
        merge(prm, kwargs) # kwargs

        super().__init__(
            workspace=workspace,
            core=core,
            component=component,
            **prm
        )


    def place(self, approach=True, exit=True, padding=30, **kwargs):
        return self.place_in(anchor="place", approach=approach, exit=exit, padding=padding, gravity_offset=0, **kwargs)


    def pick(self, approach=True, exit=True, padding=30, **kwargs):
        return self.pick_from(anchor="place", approach=approach, exit=exit, padding=padding, **kwargs)


    def decap(self, anchor="place", solid_name="body", approach=True, exit=True, padding=30, gap=2, lmove_vaj=[1000, 3000, 15000], jmove_vaj=[500, 3000, 15000], max_rotation=500, twist=500, **kwargs): # twist=400        
        # pick parameters
        motion_prm = self.pick_setting(anchor=anchor, solid_name=solid_name, approach=approach, trigger_io=False, exit=False, attachment=False, padding=padding, gap=gap, **kwargs)
        if not motion_prm:
            return False

        # tube and cap
        if len(motion_prm["load_list"]) != 2:
            print(f"No tube and cap found in position {anchor}")
        
        # cap
        solid_cap = motion_prm["load_list"][1]
        component_cap = self.workspace.components[solid_cap.component]

        # cap type
        if component_cap.cap_type != "screw":
            return False
        
        # height cap
        height_cap = abs(dorna_pose.transform_pose([0, 0, 0, 0, 0, 0], 
                                from_frame=solid_cap.pose("center"),
                                to_frame=solid_cap.pose("top"))[2])

        # tool
        tool = motion_prm["tool"]

        # j5_start
        j5_start = max_rotation/2
        
        # adjust j5 in the approach
        motion_prm["approach_j5"] = j5_start
        
        # run the motion
        if not self.touch(**motion_prm):
            return False
        
        # chunks
        twist_chunks = lambda t: ([t % max_rotation] if t % max_rotation else []) + [max_rotation] * (t // max_rotation)
        chunks = twist_chunks(twist or component_cap.twist) # rewrite twist, with the given twist
        joint_list = []
        z_offset = 0
        for chunk in chunks:
            z_offset += - component_cap.pitch * chunk / max_rotation
            # inverse kinematic
            J,C = self.core.IK(target_solid=tool.assembly[next(iter(tool.assembly))], target_anchor="tcp", target_offset=[0, 0, z_offset, 0, 0, 0],
                                tool_solid=tool.assembly[next(iter(tool.assembly))], tool_anchor="tcp", tool_offset=[0, 0, 0, 0, 0, 0],
                                base_distance=self.base_distance, rail_step=self.rail_step, rail_span=self.rail_span, ref_joints=self.ref_joints, left_approach=self.left_approach)        
            if C != 2:
                print("Could not find valid joints to decap")
                return False

            # end joint
            J[5] = j5_start - chunk
            joint_list.append(J[:])
        
        # move, startting from max_rotation/2
        for i in range(len(joint_list)):
            # enable gripper
            if tool.output_state() != 1:
                self.core.robot_api.output(config=tool.output_enable)
                tool.output_state(1)

            # uncap
            self.core.robot_api.lmove(joint=joint_list[i], vel=lmove_vaj[0], accel=lmove_vaj[1], jerk=lmove_vaj[2])

            if i < len(joint_list)-1:
                # disable gripper
                if tool.output_state() != 0:
                    self.core.robot_api.output(config=tool.output_disable)
                    tool.output_state(0)
                
                # go to start
                J_start = joint_list[i][:]
                J_start[5] = j5_start
                self.core.robot_api.jmove(joint=J_start, vel=jmove_vaj[0], accel=jmove_vaj[1], jerk=jmove_vaj[2])

            elif exit:
                # IK go up
                J,C = self.core.IK(target_solid=tool.assembly[next(iter(tool.assembly))], target_anchor="tcp", target_offset=[0, 0, -gap-height_cap, 0, 0, 0],
                                    tool_solid=tool.assembly[next(iter(tool.assembly))], tool_anchor="tcp", tool_offset=[0, 0, 0, 0, 0, 0],
                                    base_distance=self.base_distance, rail_step=self.rail_step, rail_span=self.rail_span, ref_joints=self.ref_joints, left_approach=self.left_approach)        
                if C != 2:
                    print("Could not find valid joints to decap")
                    return False

                # adjust j5
                J[5] = joint_list[i][5]
                # go up
                self.core.robot_api.lmove(joint=J, vel=self.lmove_vaj[0]*self.speed_factor, accel=self.lmove_vaj[1]*self.speed_factor, jerk=self.lmove_vaj[2]*self.speed_factor)
        
        # attach
        solid_cap.attach_to(parent=tool.assembly[next(iter(tool.assembly))],
                            parent_anchor="tcp",
                            child_anchor="top",
                            offset_frame="parent",
                            offset=[0, 0, 0, 0, 180, 0])
    
        return True



    def cap(self, anchor="place", solid_name="body", approach=True, exit=True, padding=30, gap=2, lmove_vaj=[1000, 3000, 15000], jmove_vaj=[500, 3000, 15000], max_rotation=500, **kwargs):             
        # ref joints
        if self.ref_joints is None:
            print("No reference joints defined")
            return False
        
        # tool
        tool = self.tool_attached_to_the_robot()
        if tool is None:
            print("No tool attached to the robot")
            return False

        # cap in tool
        solid_cap = self.solid_attached_to_tool(tool)
        if solid_cap is None:
            print("No item in the gripper")
            return False
        component_cap = self.workspace.components[solid_cap.component]

        # cap type
        if component_cap.cap_type != "screw":
            return False
        
        # tube in the anchor
        solid_tube = self.solid_attached_to_anchor(self.component.assembly[solid_name], anchor)
        if solid_tube is None:
            print(f"No item found in position {anchor}")
            return False
        component_tube = self.workspace.components[solid_tube.component]

        # height_cap
        height_cap = abs(dorna_pose.transform_pose([0, 0, 0, 0, 0, 0], 
                                from_frame=solid_cap.pose("center"),
                                to_frame=solid_cap.pose("top"))[2])

        # height tube
        height_tube = abs(dorna_pose.transform_pose([0, 0, 0, 0, 0, 0], 
                                from_frame=solid_tube.pose("center"),
                                to_frame=solid_tube.pose("top"))[2])

        # total height
        height_total = height_tube + height_cap

        # j5_start
        j5_start = -max_rotation/2

        # place setting
        place_prm = self.place_setting(anchor="place_cap", solid_name="body", component=component_tube, offset=[0, 0, 0, 0, 0, 0], approach=approach, exit=False, attachment=False, trigger_io=False, padding=padding, gap=gap, gravity_offset=0, soft_approach=True, **kwargs)
        if not place_prm: 
            print("Not able to place")
            return False
        # adjust j5 in the approach
        place_prm["approach_j5"] = j5_start
        if not self.touch(**place_prm):
            print("Not able to place")
            return False            

        # run chunks
        twist_chunks = lambda t: ([t % max_rotation] if t % max_rotation else []) + [max_rotation] * (t // max_rotation)
        chunks = twist_chunks(int(component_cap.twist))[::-1]
        joint_list = []
        z_offset = 0
        for chunk in chunks:
            z_offset += component_cap.pitch * chunk / max_rotation
            # inverse kinematic
            J,C = self.core.IK(target_solid=tool.assembly[next(iter(tool.assembly))], target_anchor="tcp", target_offset=[0, 0, z_offset, 0, 0, 0],
                                tool_solid=tool.assembly[next(iter(tool.assembly))], tool_anchor="tcp", tool_offset=[0, 0, 0, 0, 0, 0],
                                base_distance=self.base_distance, rail_step=self.rail_step, rail_span=self.rail_span, ref_joints=self.ref_joints, left_approach=self.left_approach)        
            if C != 2:
                print("Could not find valid joints to cap")
                return False

            # end joint
            J[5] = j5_start + chunk
            joint_list.append(J[:])

        # move, startting from -rotation/2
        for i in range(len(joint_list)):
            # tighten cap
            self.core.robot_api.lmove(joint=joint_list[i], vel=lmove_vaj[0], accel=lmove_vaj[1], jerk=lmove_vaj[2])

            # disable gripper
            if tool.output_state() != 0:
                self.core.robot_api.output(config=tool.output_disable)
                tool.output_state(0)

            if i < len(joint_list)-1:
                # go to start
                #J_start = joint_list[i+1][:]
                J_start = joint_list[i][:]
                J_start[5] = j5_start
                self.core.robot_api.jmove(joint=J_start, vel=jmove_vaj[0], accel=jmove_vaj[1], jerk=jmove_vaj[2])

                # enable gripper
                if tool.output_state() != 1:
                    self.core.robot_api.output(config=tool.output_enable)
                    tool.output_state(1)

            elif exit:
                # IK go up
                J,C = self.core.IK(target_solid=tool.assembly[next(iter(tool.assembly))], target_anchor="tcp", target_offset=[0, 0, -height_total-gap, 0, 0, 0],
                                    tool_solid=tool.assembly[next(iter(tool.assembly))], tool_anchor="tcp", tool_offset=[0, 0, 0, 0, 0, 0],
                                    base_distance=self.base_distance, rail_step=self.rail_step, rail_span=self.rail_span, ref_joints=self.ref_joints, left_approach=self.left_approach)        
                if C != 2:
                    print("Could not find valid joints to decap")
                    return False

                # go up
                self.core.robot_api.lmove(joint=J, vel=self.lmove_vaj[0]*self.speed_factor, accel=self.lmove_vaj[1]*self.speed_factor, jerk=self.lmove_vaj[2]*self.speed_factor)

        # attach cap to body
        solid_cap.attach_to(parent=solid_tube,
                            parent_anchor="place",
                            child_anchor="center")
    
        return True
        
                
