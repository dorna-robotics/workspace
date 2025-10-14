# workspace/recipes/handle_microtube.py

class ChangeTool:
    def __init__(self, workspace, core, microplate, speed_factor=0.5):
        self.ws = workspace
        self.core = core
        self.microplate = microplate
        self.ref_joints = None
        self.speed_factor = speed_factor

        # first we check if the robot has a tool changer
        if core.has_toolchanger is False:
            print("Robot does not have a tool changer, cannot change tool")
            return


        # first we assign the reference joints
        J,C = core.IK(target_solid=tool_rack.assembly["tool_rack"], target_anchor="tool_connection", target_offset=[0,0,-50,0,0,0], base_distance=self.base_distance,
        rail_step=5.0, rail_span=2,tool_solid=core.toolchanger_robot_side, tool_anchor="toolchanger_connection", tool_offset=[0,0,0,0,0,0],ref_joints=[0,0,0,0,0,0,0,0])
        if C == 2:
            self.ref_joints = J
        else:
            print("Could not find a valid reference pose to approach the tool rack")
            return

        # next we check if there is a tool already attached
        for child in tool_rack.assembly["tool_rack"].children["tool_connection"]:
            solid = child["child_solid"]
            self.tool = self.ws.components[solid.component]
            continue



    """
    Approaches the tool rack and activates the tool changer
    to pick up a new tool.
    """
    def pick_tool(self):
        # we go to the reference joint first
        if self.ref_joints is None:
            print("No reference joints defined, cannot pick tool")
            return False

        #self.core.robot_api.jmove(self.ref_joints, vel=200*self.speed_factor, accel=5000*self.speed_factor, jerk=50000*self.speed_factor)
        # next we verify there is a tool to pick
        
        if self.tool is None:
            print("No tool to pick, aborting")
            return False
        # next we approach the tool

        tool = self.tool

        J,C = self.core.IK(target_solid=self.tool_rack.assembly["tool_rack"], target_anchor="tool_connection", target_offset=[0,0,-self.retract_height_without_tool,0,0,0], tool_solid=self.core.toolchanger_robot_side, tool_anchor="toolchanger_connection", tool_offset=[0,0,0,0,0,0],base_distance=self.base_distance,
        rail_step=5.0, rail_span=2,ref_joints=self.ref_joints)

        if C == 2:
            self.core.robot_api.lmove(J, vel=200*self.speed_factor,accel=5000*self.speed_factor,jerk=50000*self.speed_factor)
        else:
            print("Could not find a valid approach pose to the tool")
            return False
        
        # next we go down to the tool
        J,C = self.core.IK(target_solid=tool.assembly["toolchanger_tool_side"], target_anchor="toolchanger_connection", target_offset=[0,0,0,0,0,0], tool_solid=self.core.toolchanger_robot_side, tool_anchor="toolchanger_connection", tool_offset=[0,0,0,0,0,0],base_distance=self.base_distance,rail_step=5.0, rail_span=2,ref_joints=self.ref_joints)

        if C == 2:
            self.core.robot_api.lmove(J, vel=200*self.speed_factor,accel=5000*self.speed_factor,jerk=50000*self.speed_factor)
        else:
            print("Could not find a valid pose to go down to the tool")
            return False

        # next we attach the tool
        self.tool.assembly["toolchanger_tool_side"].attach_to(parent=self.core.toolchanger_robot_side, parent_anchor="toolchanger_connection", child_anchor="toolchanger_connection")

        self.tool = None

        # next we go up a little
        J,C = self.core.IK(target_solid=self.tool_rack.assembly["tool_rack"], target_anchor="tool_connection", target_offset=[0,0,-self.release_height,0,0,0], tool_solid=tool.assembly["toolchanger_tool_side"], tool_anchor="tool_rack_connection", tool_offset=[0,0,0,0,0,0],
                            ref_joints=self.ref_joints, rail_step=5.0, rail_span=2, base_distance=self.base_distance)



        if C == 2:
            self.core.robot_api.lmove(J, vel=200*self.speed_factor,accel=5000*self.speed_factor,jerk=50000*self.speed_factor)
        else:
            print("Could not find a valid pose to go up")
            return False

        # next we go back a little
        J,C = self.core.IK(target_solid=self.tool_rack.assembly["tool_rack"], target_anchor="tool_connection", target_offset=[-self.retract_distance,0,-self.release_height,0,0,0], tool_solid=tool.assembly["toolchanger_tool_side"], tool_anchor="tool_rack_connection", tool_offset=[0,0,0,0,0,0],
                            ref_joints=self.ref_joints, rail_step=5.0, rail_span=2, base_distance=self.base_distance)


        if C == 2:
            self.core.robot_api.lmove(J, vel=200*self.speed_factor,accel=5000*self.speed_factor,jerk=50000*self.speed_factor)
        else:
            print("Could not find a valid pose to go back")
            return False

        # now we go up
        J,C = self.core.IK(target_solid=self.tool_rack.assembly["tool_rack"], target_anchor="tool_connection", target_offset=[-self.retract_distance,0,-self.retract_height_with_tool,0,0,0], tool_solid=tool.assembly["toolchanger_tool_side"], tool_anchor="tool_rack_connection", tool_offset=[0,0,0,0,0,0],
                            ref_joints=self.ref_joints, rail_step=5.0, rail_span=2, base_distance=self.base_distance)

        if C == 2:
            self.core.robot_api.lmove(J, vel=200*self.speed_factor,accel=5000*self.speed_factor,jerk=50000*self.speed_factor)
        else:
            print("Could not find a valid pose to go up")
            return False
        
        return True


    def place_tool(self):
        if self.tool is not None:
            print("There is already a tool attached, cannot place tool")
            return False
        
        if self.ref_joints is None:
            print("No reference joints defined, cannot pick tool")
            return False
        
        # next we find the tool attached to the robot
        tool = None
        for child in self.core.toolchanger_robot_side.children["toolchanger_connection"]:
            solid = child["child_solid"]
            tool = self.ws.components[solid.component]
            continue

        if tool is None:
            print("Could not find tool attached to robot")
            return False
        
        # we start from back and up
        J,C = self.core.IK(target_solid=self.tool_rack.assembly["tool_rack"], target_anchor="tool_connection", target_offset=[-self.retract_distance,0,-self.retract_height_with_tool,0,0,0], tool_solid=tool.assembly["toolchanger_tool_side"], tool_anchor="tool_rack_connection", tool_offset=[0,0,0,0,0,0],
                            ref_joints=self.ref_joints, rail_step=5.0, rail_span=2, base_distance=self.base_distance)
        if C == 2:
            self.core.robot_api.lmove(J, vel=200*self.speed_factor,accel=5000*self.speed_factor,jerk=50000*self.speed_factor)
        else:   
            print("Could not find a valid pose to start placing the tool")
            return False
        

        # next we go down to the release height
        J,C = self.core.IK(target_solid=self.tool_rack.assembly["tool_rack"], target_anchor="tool_connection", target_offset=[-self.retract_distance,0,-self.release_height,0,0,0], tool_solid=tool.assembly["toolchanger_tool_side"], tool_anchor="tool_rack_connection", tool_offset=[0,0,0,0,0,0],
                            ref_joints=self.ref_joints, rail_step=5.0, rail_span=2, base_distance=self.base_distance)

        if C == 2:
            self.core.robot_api.lmove(J, vel=200*self.speed_factor,accel=5000*self.speed_factor,jerk=50000*self.speed_factor)
        else:
            print("Could not find a valid pose to go down")
            return False
        
        # now we go to the tool rack
        J,C = self.core.IK(target_solid=self.tool_rack.assembly["tool_rack"], target_anchor="tool_connection", target_offset=[0,0,-self.release_height,0,0,0], tool_solid=tool.assembly["toolchanger_tool_side"], tool_anchor="tool_rack_connection", tool_offset=[0,0,0,0,0,0],
                            ref_joints=self.ref_joints, rail_step=5.0, rail_span=2, base_distance=self.base_distance)
        if C == 2:
            self.core.robot_api.lmove(J, vel=200*self.speed_factor,accel=5000*self.speed_factor,jerk=50000*self.speed_factor)
        else:
            print("Could not find a valid pose to go to the tool rack")
            return False


        # now we place the tool into the rack        
        J,C = self.core.IK(target_solid=tool.assembly["toolchanger_tool_side"], target_anchor="toolchanger_connection", target_offset=[0,0,0,0,0,0], tool_solid=self.core.toolchanger_robot_side, tool_anchor="toolchanger_connection", tool_offset=[0,0,0,0,0,0],base_distance=self.base_distance,rail_step=5.0, rail_span=2,ref_joints=self.ref_joints)
        if C == 2:
            self.core.robot_api.lmove(J, vel=200*self.speed_factor,accel=5000*self.speed_factor,jerk=50000*self.speed_factor)   
        else:
            print("Could not find a valid pose to place the tool")
            return False

        # now we attach the tool to the tool rack
        tool.assembly["toolchanger_tool_side"].attach_to(parent=self.tool_rack.assembly["tool_rack"], parent_anchor="tool_connection", child_anchor="tool_rack_connection")
        self.tool = tool


        J,C = self.core.IK(target_solid=self.tool_rack.assembly["tool_rack"], target_anchor="tool_connection", target_offset=[0,0,-self.retract_height_without_tool,0,0,0], tool_solid=self.core.toolchanger_robot_side, tool_anchor="toolchanger_connection", tool_offset=[0,0,0,0,0,0],base_distance=self.base_distance,
        rail_step=5.0, rail_span=2,ref_joints=self.ref_joints)


        if C == 2:
            self.core.robot_api.lmove(J, vel=200*self.speed_factor,accel=5000*self.speed_factor,jerk=50000*self.speed_factor)
        else:
            print("Could not find a valid pose to go up")
            return False

        return True



