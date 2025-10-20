# workspace/recipes/handle_microtube.py

class HandleMicrotube:
    def __init__(self, workspace, core, microplate, speed_factor=0.5,left_approach=True,base_distance=350,rail_step=5.0,rail_span=2):
        self.ws = workspace
        self.core = core
        self.microplate = microplate
        self.ref_joints = None
        self.speed_factor = speed_factor
        self.left_approach = left_approach
        self.base_distance = base_distance
        self.rail_step = rail_step
        self.rail_span = rail_span
        self.clearance_height = 180  # mm above the microplate
       

        # first we assign the reference joints
        # the reference joints will be on top of the microplate center at 150mm height
        J,C = core.IK(target_solid=self.microplate.assembly["microplate"], target_anchor="center", target_offset=[0,0,self.clearance_height,180,0,0], base_distance=self.base_distance,
        rail_step=self.rail_step, rail_span=self.rail_span,ref_joints=[0,0,0,0,0,0,0,0],left_approach=self.left_approach)
        if C == 2:
            self.ref_joints = J
        else:
            print("Could not find a valid reference pose to approach the microplate")
            return
        


    def tool(self):
        tool = None
        if self.core.has_toolchanger:
            for child in self.core.toolchanger_robot_side.children["toolchanger_connection"]:
                solid = child["child_solid"]
                tool = self.ws.components[solid.component]
                continue

        else:
            for child in self.core.robot_flange.children["output"]:
                solid = child["child_solid"]
                tool = self.ws.components[solid.component]
                continue
        
        return tool

    def pick_tube(self,index):

        # index is A1 to H12
        # each index corresponds to an anchor on the microplate


        # we go to the reference joint first
        if self.ref_joints is None:
            print("No reference joints defined, cannot pick tool")
            return False
        
        tool = self.tool()
        if tool is None:
            print("No tool attached to robot, cannot pick tool")
            return False
        
        
        # we check if there is a tube in the index
        tube = None
        for child in self.microplate.assembly["microplate"].children[index]:
            solid = child["child_solid"]
            tube = self.ws.components[solid.component]
            continue
        if tube is None:
            print(f"No tube found in position {index}, cannot pick tube")
            return False
        
        
        # we check if there is tube in the gripper already
        existing_tube = None
        for child in tool.assembly["microtube_gripper"].children["gripping_point"]:
            solid = child["child_solid"]
            existing_tube = self.ws.components[solid.component]
            continue
        if existing_tube is not None:
            print("There is already a tube in the gripper, cannot pick another tube")
            return False
            


        # we go to clearance height on top of the tube
        J,C = self.core.IK(target_solid=self.microplate.assembly["microplate"], target_anchor=index, target_offset=[0,0,self.clearance_height,180,0,0], base_distance=self.base_distance,
                            ref_joints=self.ref_joints, rail_step=self.rail_step, rail_span=self.rail_span, left_approach=self.left_approach)

        if C == 2:
            self.core.robot_api.jmove(J, vel=200*self.speed_factor, accel=5000*self.speed_factor, jerk=50000*self.speed_factor)
        else:
            print("Could not find a valid pose to go to clearance height")
            return False
        

        # open the gripper
        # we go down to the tube
        # this move will be to the tube and using the tool
        J,C = self.core.IK(target_solid=tube.assembly["microtube"], target_anchor="gripping_point", target_offset=[0,0,0,0,0,0], tool_solid=tool.assembly["microtube_gripper"], tool_anchor="gripping_point", tool_offset=[0,0,0,0,0,0], base_distance=self.base_distance,
                            ref_joints=self.ref_joints, rail_step=self.rail_step, rail_span=self.rail_span, left_approach=self.left_approach)

        if C == 2:
            self.core.robot_api.lmove(J, vel=200*self.speed_factor, accel=5000*self.speed_factor, jerk=50000*self.speed_factor)
        else:
            print("Could not find a valid pose to go down to the tube")
            return False


            
        #self.core.robot_api.jmove(self.ref_joints, vel=200*self.speed_factor, accel=5000*self.speed_factor, jerk=50000*self.speed_factor)
        # next we verify there is a tool to pick
        # close the gripper

        # attach the tube to the gripper
        tube.assembly["microtube"].attach_to(parent=tool.assembly["microtube_gripper"], parent_anchor="gripping_point", child_anchor="gripping_point")

        # # we go back to clearance height
        J,C = self.core.IK(target_solid=self.microplate.assembly["microplate"], target_anchor=index, target_offset=[0,0,self.clearance_height,180,0,0], base_distance=self.base_distance,
                            ref_joints=self.ref_joints, rail_step=self.rail_step, rail_span=self.rail_span, left_approach=self.left_approach)

        if C == 2:
            self.core.robot_api.lmove(J, vel=200*self.speed_factor, accel=5000*self.speed_factor, jerk=50000*self.speed_factor)
        else:
            print("Could not find a valid pose to go back to clearance height")
            return False

        return True
    def place_tube(self,index):

        # index is A1 to H12
        # each index corresponds to an anchor on the microplate


        # we go to the reference joint first
        if self.ref_joints is None:
            print("No reference joints defined, cannot place tool")
            return False
        
        tool = self.tool()
        if tool is None:
            print("No tool attached to robot, cannot place tool")
            return False
        

        # we check if there is tube in the gripper already
        existing_tube = None
        for child in tool.assembly["microtube_gripper"].children["gripping_point"]:
            solid = child["child_solid"]
            existing_tube = self.ws.components[solid.component]
            continue
        if existing_tube is None:
            print("There is no tube in the gripper, cannot place tube")
            return False
        
        # now we check if there is a tube in the target position
        target_tube = None
        for child in self.microplate.assembly["microplate"].children[index]:
            solid = child["child_solid"]
            target_tube = self.ws.components[solid.component]
            continue
        if target_tube is not None:
            print(f"There is already a tube in position {index}, cannot place another tube")
            return False
        


        # we go to clearance height on top of the tube
        J,C = self.core.IK(target_solid=self.microplate.assembly["microplate"], target_anchor=index, target_offset=[0,0,self.clearance_height,180,0,0], base_distance=self.base_distance,
                            ref_joints=self.ref_joints, rail_step=self.rail_step, rail_span=self.rail_span, left_approach=self.left_approach)
        if C == 2:
            self.core.robot_api.jmove(J, vel=200*self.speed_factor, accel=5000*self.speed_factor, jerk=50000*self.speed_factor)
        else:
            print("Could not find a valid pose to go to clearance height")
            return False
        
        # we go down to the position to place the tube
        # at this position, the center of the tube will be at the index anchor
        J,C = self.core.IK(target_solid=self.microplate.assembly["microplate"], target_anchor=index, target_offset=[0,0,0,0,0,0], base_distance=self.base_distance,
                            tool_solid=existing_tube.assembly["microtube"], tool_anchor="center", tool_offset=[0,0,0,0,0,0],
                            ref_joints=self.ref_joints, rail_step=self.rail_step, rail_span=self.rail_span, left_approach=self.left_approach)
        if C == 2:
            self.core.robot_api.lmove(J, vel=200*self.speed_factor, accel=5000*self.speed_factor, jerk=50000*self.speed_factor)
        else:
            print("Could not find a valid pose to go down to place the tube")
            return False
        
        # open the gripper
        # detach the tube from the gripper
        existing_tube.assembly["microtube"].attach_to(parent=self.microplate.assembly["microplate"], parent_anchor=index, child_anchor="center")

        # now we go back to clearance height
        J,C = self.core.IK(target_solid=self.microplate.assembly["microplate"], target_anchor=index, target_offset=[0,0,self.clearance_height,180,0,0], base_distance=self.base_distance,
                            ref_joints=self.ref_joints, rail_step=self.rail_step, rail_span=self.rail_span, left_approach=self.left_approach)
        if C == 2:
            self.core.robot_api.lmove(J, vel=200*self.speed_factor, accel=5000*self.speed_factor, jerk=50000*self.speed_factor)
        else:
            print("Could not find a valid pose to go back to clearance height")
            return False
        return True
    
    def calibrate():

        # # here we move to different key points and record the calibration
        # J1 = 
        # core.calibration.record_point(core.name, J1, msg="Move to position 1 and press Enter to record calibration point.")

        # J2 =
        # core.calibration.record_point(core.name, J2, msg="Move to position 2 and press Enter to record calibration point.")

        # J3 =
        # core.calibration.record_point(core.name, J3, msg="Move to position 3 and press Enter to record calibration point.")
        
        # we go on top of four corners with the gripper first to the clearance height and then down to the tube position while the tube is open
        # then the user will adjust the tube position manually and press enter to record the calibration point
        # the 4 indices are A1, A12, H1, H12
        
        pass

