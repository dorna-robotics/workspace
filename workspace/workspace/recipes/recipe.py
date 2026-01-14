from copy import deepcopy
from mergedeep import merge
from collections import deque
from dorna2 import pose as dorna_pose

class Recipe:
    DEFAULTS = dict(
        # ref joints
        target_solid_name="body",
        target_anchor="center",
        target_offset=[0, 0, 50, 0, 180, 0],
        initial_joints=[0, 0, 0, 0, 0, 0, 0, 0],
        # IK
        left_approach=True,
        base_distance=350,
        rail_step=0,
        rail_span=0,        
        # motion
        motion_type="lmove",
        speed_factor=0.5,
        jmove_vaj=[100, 600, 3000],
        lmove_vaj=[300, 1000, 5000],
        # calibration
        calibration_name=None,
        calibration=True,
        calibration_targets={}, # {solid_name: {anchor_1:..., anchor_2:...},...}
        calibration_target_offset=[0, 0, 20, 0, 0, 0],
        calibration_tool_solid_name="body",
        calibration_tool_anchor="tcp",
        calibration_tool_offset=[0, 0, 0, 0, 0, 0],
    )

    def __init__(self, workspace, core, component, **kwargs):
        # prm
        prm = deepcopy(self.DEFAULTS) # default
        merge(prm, kwargs) # self

        # init
        self.workspace = workspace
        self.core = core
        self.component = component
        
        # IK
        self.left_approach = prm["left_approach"]
        self.base_distance = prm["base_distance"]
        self.rail_step = prm["rail_step"]
        self.rail_span = prm["rail_span"]     

        # motion
        self.motion_type = prm["motion_type"]
        self.speed_factor = prm["speed_factor"]
        self.jmove_vaj = prm["jmove_vaj"]
        self.lmove_vaj = prm["lmove_vaj"] 

        # calibration
        self.calibration = prm["calibration"]
        self.calibration_targets = prm["calibration_targets"]
        self.calibration_target_offset = prm["calibration_target_offset"]
        self.calibration_tool_solid_name = prm["calibration_tool_solid_name"]
        self.calibration_tool_anchor = prm["calibration_tool_anchor"]
        self.calibration_tool_offset = prm["calibration_tool_offset"]
        # calibration name initi
        if prm["calibration_name"] is None:
            self.calibration_name = f"{self.component.name}_{self.left_approach}_{self.base_distance}_{self.rail_step}_{self.rail_span}"


        # find the reference joints used later for every IK
        J,C = self.core.IK(
                    target_solid=self.component.assembly[prm["target_solid_name"]], 
                    target_anchor=prm["target_anchor"],
                    target_offset=prm["target_offset"],
                    base_distance=self.base_distance,
                    rail_step=self.rail_step, 
                    rail_span=self.rail_span,
                    ref_joints=prm["initial_joints"],
                    left_approach=self.left_approach)
        if C == 2:
            self.ref_joints = J
        else:
            print("🔴 could not find a valid reference joint to approach the container")
            return


    # return the current tool
    def tool_attached_to_the_robot(self):
        tool = None
        if self.core.has_tool_changer:
            for child in self.core.tool_changer_robot_side.children["tool_changer_connection"]:
                solid = child["child_solid"]
                tool = self.workspace.components[solid.component]
                continue

        else:
            for child in self.core.robot_flange.children["output"]:
                solid = child["child_solid"]
                tool = self.workspace.components[solid.component]
                continue
        
        return tool


    # return the first solid attached to the tool given
    def solid_attached_to_tool(self, tool):        
        # we check if there is component in the gripper already
        for child in tool.assembly[next(iter(tool.assembly))].children["tcp"]:
            return child["child_solid"]
        return None


    # return the solid attached to an specific anchor
    def solid_attached_to_anchor(self, solid, anchor):        
        try:
            for child in solid.children[anchor]:
                return child["child_solid"]
        except:
            pass
        return None

    # run bfs to return the fist solid with the given anchor name, including the initial solid
    def solid_with_anchor(self, initial_solid, anchor):
        queue = deque([initial_solid])
        visited = set()

        while queue:
            solid = queue.popleft()

            if solid in visited:
                continue
            visited.add(solid)

            # check current layer node
            if anchor in solid.anchors:
                return solid

            # enqueue next layer
            for links in solid.children.values():
                for link in links:
                    queue.append(link["child_solid"])

        return None

    # find the chchilderen of the parent attached to its anchor, and going down
    def solid_hierarchy(self, parent_solid, parent_anchor, connection_anchor="place"):
        # initialize load list
        load_list = []

        # we check if there is an item in the index
        first_child = self.solid_attached_to_anchor(parent_solid, parent_anchor)
        if first_child is None:
            return load_list

        # find all the items attached to the tool
        load_list = [first_child]
        while True:
            child = self.solid_attached_to_anchor(load_list[-1], connection_anchor)
            if child is not None:
                load_list.append(child)
            else:
                return load_list


    # touch a point
    def touch(self,
            target_solid, target_anchor, target_offset=[0, 0, 0, 0, 0, 0],
            output_approach=[],
            approach_tool={"solid": None, "anchor": None, "offset":[0, 0, 0, 0, 0, 0]},
            approach_path = [],
            output_touch=[],
            actions=[],
            sleep=0,
            attach=[None, {"parent":None, "parent_anchor":None, "child_anchor":None, "offset":[0, 0, 0, 0, 0, 0], "offset_frame":"parent"}],
            exit_tool={"solid": None, "anchor": None, "offset":[0, 0, 0, 0, 0, 0]},
            exit_path = [],
            output_exit=[],
            **kwargs,
            ):
        # vaj_map
        vaj_map = {
            "jmove": self.jmove_vaj,
            "lmove": self.lmove_vaj 
        }

        """
        output_approach
        """
        self.core.robot_api.output(config=output_approach)


        """
        approach
        """
        path = list(approach_path+[target_offset])
        for i in range(len(path)):
            J,C = self.core.IK(target_solid=target_solid, target_anchor=target_anchor, target_offset=path[i],
                                tool_solid=approach_tool["solid"], tool_anchor=approach_tool["anchor"], tool_offset=approach_tool["offset"],
                                base_distance=self.base_distance, rail_step=self.rail_step, rail_span=self.rail_span, ref_joints=self.ref_joints, left_approach=self.left_approach)
            if C == 2:
                # calibration
                if self.calibration:
                    print("no clb: ", J[:])
                    J = self.core.calibration.interpolate(J[:], dict_name=self.calibration_name)
                    print("ye clb:", J[:])
                    print("################")
                if i == 0 and approach_path: # first motion of the approach
                    if self.core.has_motion_plan: # run path planing 
                        #create the path
                        points = self.core.motion_plan(joint=J)
                        if len(points)==0:
                            print("🔴 [error] no proper path was found")
                            return False
                        
                        #run the path
                        self.core.robot_api.jmove_multi_point(points, vel=vaj_map["jmove"][0]*self.speed_factor, accel=vaj_map["jmove"][1]*self.speed_factor, jerk=vaj_map["jmove"][2]*self.speed_factor)
                    else: # no path planing
                        self.core.robot_api.jmove(joint=J, vel=vaj_map["jmove"][0]*self.speed_factor, accel=vaj_map["jmove"][1]*self.speed_factor, jerk=vaj_map["jmove"][2]*self.speed_factor)

                else: # rest are all based on the user motion command
                    if self.motion_type == "lmove": # run lmove
                        # set the tool
                        path_tool = dict(approach_tool)
                        tool_pose = [0,0,0,0,0,0]
                        if path_tool["solid"] and path_tool["anchor"]:
                            tool_pose = path_tool["solid"].pose(anchor=path_tool["anchor"], in_frame=self.core.robot_flange, offset=path_tool["offset"])

                        # lmove with tool_pose
                        getattr(self.core.robot_api, self.motion_type)(joint=J, vel=vaj_map[self.motion_type][0]*self.speed_factor, accel=vaj_map[self.motion_type][1]*self.speed_factor, jerk=vaj_map[self.motion_type][2]*self.speed_factor, tool_pose=tool_pose)   

                    elif self.motion_type == "jmove": # run jmove
                        getattr(self.core.robot_api, self.motion_type)(joint=J, vel=vaj_map[self.motion_type][0]*self.speed_factor, accel=vaj_map[self.motion_type][1]*self.speed_factor, jerk=vaj_map[self.motion_type][2]*self.speed_factor)   


            else:
                print("🔴 could not find a valid pose to approach")
                return False
        
        """
        output_config
        """
        self.core.robot_api.output(config=output_touch)

        """
        sleep, actions
        """
        self.core.robot_api.sleep(sleep)
        for func, args, kwargs in actions:
            func(*args, **kwargs)

        """
        attach
        """
        if attach[0] is not None:
            attach[0].attach_to(**attach[1])

        """
        exit
        """
        path = list(exit_path)
        for i in range(len(path)):
            J,C = self.core.IK(target_solid=target_solid, target_anchor=target_anchor, target_offset=path[i],
                                tool_solid=exit_tool["solid"], tool_anchor=exit_tool["anchor"], tool_offset=exit_tool["offset"],
                                base_distance=self.base_distance, rail_step=self.rail_step, rail_span=self.rail_span, ref_joints=self.ref_joints, left_approach=self.left_approach)        
            if C == 2:
                # calibration
                if self.calibration:
                    print("no clb: ", J[:])
                    J = self.core.calibration.interpolate(J[:], dict_name=self.calibration_name)
                    print("ye clb:", J[:])
                    print("################")
                # motion
                if self.motion_type == "lmove": # run lmove
                    # set the tool
                    path_tool = dict(exit_tool)
                    tool_pose = [0,0,0,0,0,0]
                    if path_tool["solid"] and path_tool["anchor"]:
                        tool_pose = path_tool["solid"].pose(anchor=path_tool["anchor"], in_frame=self.core.robot_flange, offset=path_tool["offset"])

                    # lmove with tool_pose
                    getattr(self.core.robot_api, self.motion_type)(joint=J, vel=vaj_map[self.motion_type][0]*self.speed_factor, accel=vaj_map[self.motion_type][1]*self.speed_factor, jerk=vaj_map[self.motion_type][2]*self.speed_factor, tool_pose=tool_pose)   

                elif self.motion_type == "jmove": # run jmove
                    getattr(self.core.robot_api, self.motion_type)(joint=J, vel=vaj_map[self.motion_type][0]*self.speed_factor, accel=vaj_map[self.motion_type][1]*self.speed_factor, jerk=vaj_map[self.motion_type][2]*self.speed_factor)   
            
            else:
                print("🔴 could not find a valid pose to approach")
                return False

        """
        output_exit
        """
        self.core.robot_api.output(config=output_exit)

        return True

    
    # pick from specific anchor in the given solid and component
    # always locate the object in the anchor, and go for that
    def pick_setting(self, anchor, solid_name="body", component=None, approach=True, actions=[], exit=True, attachment=True, trigger_io=True, padding=50, gap=2, tool_tcp_z_offset=0, tool_tip_z_offset=0, **kwargs):
        """
        assign kwargs
        """
        for k, v in kwargs.items():
            setattr(self, k, v)

        """
        component
        """
        component = component or self.component
        
        """
        ref joints
        """
        if self.ref_joints is None:
            print("🔴 no reference joints defined")
            return False
        
        """
        tool
        """
        tool = self.tool_attached_to_the_robot()
        if tool is None:
            print("🔴 no tool attached to the robot")
            return False
        
        """
        find the hierarchy of the items attached to the anchor
        """
        load_list = self.solid_hierarchy(parent_solid=component.assembly[solid_name], parent_anchor=anchor, connection_anchor="place")
        if not load_list:
            print(f"🔴 no item found in position {anchor}")
            return False

        """
        height load
        """
        height_load = abs(dorna_pose.transform_pose([0, 0, 0, 0, 0, 0], 
                                from_frame=load_list[0].pose("center"),
                                to_frame=load_list[-1].pose("top"))[2])

        """
        height_container
        """
        height_container = abs(dorna_pose.transform_pose([0, 0, 0, 0, 0, 0], 
                                from_frame=component.assembly[solid_name].pose("top"),
                                to_frame=component.assembly[solid_name].pose("place"))[2])

        """
        height tool
        """
        height_tool = abs(dorna_pose.transform_pose([0, 0, tool_tip_z_offset - tool_tcp_z_offset, 0, 0, 0], 
                                from_frame=tool.assembly[next(iter(tool.assembly))].pose("tip"),
                                to_frame=tool.assembly[next(iter(tool.assembly))].pose("tcp"))[2])


        """
        pose_offset: anchor center in load_list[0] with respect to the anchor in component.assembly[solid_name]
        """
        pose_offset = dorna_pose.Pose(
                        dorna_pose.transform_pose(
                            [0, 0, 0, 0, 0, 0], 
                            from_frame=load_list[0].pose("center"),
                            to_frame=component.assembly[solid_name].pose(anchor)
        ))



        """
        target_offset
        """
        _target_offset = [0, 0, height_load, 0, 0, 0]
        target_offset = pose_offset.pose(offset=_target_offset)

        """
        approach path
        """
        approach_path = []
        if approach:
            _approach_path = [[0, 0, max(height_load,height_container) + padding, 0, 0, 0], 
                            [0, 0, height_load+height_tool+gap, 0, 0, 0]]
            approach_path = [pose_offset.pose(offset=p) for p in _approach_path]
        
        """
        exit path
        """
        exit_path = []
        if exit:
            _exit_path = [[0, 0, height_container+gap, 0, 0, 0], 
                        [0, 0, max(height_load,height_container)+padding, 0, 0, 0]]
            exit_path = [pose_offset.pose(offset=p) for p in _exit_path]

            
        """
        output config
        """
        output_approach = []
        output_touch = []
        output_exit = []
        if trigger_io:
            # enable component setting
            enable = list(getattr(component, "enable", []))
            
            # disable component setting
            disable = list(getattr(component, "disable", []))
            
            # output config
            output_approach = tool.disable + enable
            output_touch = tool.enable + disable

        """
        run attachment
        """
        attach = [None, {"parent":None, "parent_anchor":None, "child_anchor":None, "offset":[0, 0, 0, 0, 0, 0], "offset_frame":"parent"}]
        exit_tool = {"solid": tool.assembly[next(iter(tool.assembly))], "anchor": "tcp", "offset":[0, 0, tool_tcp_z_offset, 0, 180, 0]}
        if attachment:
            attach = [load_list[0], {"parent": tool.assembly[next(iter(tool.assembly))], "parent_anchor":"tcp", "child_anchor":"center", "offset": [0, 0, height_load+tool_tcp_z_offset, 0, 180, 0], "offset_frame": "parent"}]
            exit_tool = {"solid": load_list[0], "anchor": "center", "offset":[0, 0, 0, 0, 0, 0]}

        """
        return
        """
        return {
            "target_solid": component.assembly[solid_name],
            "target_anchor": anchor, 
            "target_offset": target_offset,
            "output_approach": output_approach,
            "approach_tool": {"solid": tool.assembly[next(iter(tool.assembly))], "anchor": "tcp", "offset":[0, 0, tool_tcp_z_offset, 0, 180, 0]},
            "approach_path": approach_path,
            "output_touch": output_touch,
            "actions": actions,
            "sleep": 0,
            "attach": attach,
            "exit_tool": exit_tool,
            "exit_path": exit_path,
            "output_exit": output_exit,
            "height_tool": height_tool,
            "height_load": height_load,
            "height_container": height_container,
            "load_list": load_list,
            "tool": tool,
        }


    # run pick with motion
    def pick_from(self, anchor, solid_name="body", component=None, approach=True, actions=[], exit=True, attachment=True, trigger_io=True, padding=50, gap=2, tool_tcp_z_offset=0, tool_tip_z_offset=0, **kwargs):
        # pick parameters
        pick_prm = self.pick_setting(anchor, solid_name, component=component, approach=approach, actions=actions, exit=exit, attachment=attachment, trigger_io=trigger_io, padding=padding, gap=gap, tool_tcp_z_offset=tool_tcp_z_offset, tool_tip_z_offset=tool_tip_z_offset, **kwargs)
        if not pick_prm:
            return False
        # touch
        return self.touch(**pick_prm)


    # place the load in an specific anchor of the given solid and component, with the given offset
    def place_setting(self, anchor, solid_name="body", component=None, offset=[0, 0, 0, 0, 0, 0], approach=True, actions=[], exit=True, attachment=True, trigger_io=True, padding=50, gap=2, load_anchor="center", gravity_offset=1, **kwargs):
        """
        assign kwargs
        """
        for k, v in kwargs.items():
            setattr(self, k, v)

        """
        component
        """
        component = component or self.component

        """
        ref joints
        """
        if self.ref_joints is None:
            print("🔴 no reference joints defined")
            return False

        """
        tool
        """
        tool = self.tool_attached_to_the_robot()
        if tool is None:
            print("🔴 no tool attached to the robot")
            return False

        """
        find the hierarchy of the items attached to the tool
        """
        # item in tool
        load_list = [self.solid_attached_to_tool(tool)]
        if load_list[-1] is None:
            print("🔴 no item in the gripper")
            return False
        
        # find all the items attached to the tool
        load_list += self.solid_hierarchy(parent_solid=load_list[0], parent_anchor="place", connection_anchor="place")

        """
        height load
        """
        height_load = abs(dorna_pose.transform_pose([0, 0, 0, 0, 0, 0], 
                                from_frame=load_list[0].pose(load_anchor),
                                to_frame=load_list[-1].pose("top"))[2])

        """height_container"""
        height_container = max(-dorna_pose.transform_pose(offset, 
                                from_frame= component.assembly[solid_name].pose(anchor),
                                to_frame=component.assembly[solid_name].pose("top"))[2], 0)

        """
        height tool
        """
        height_tool = abs(dorna_pose.transform_pose([0, 0, 0, 0, 0, 0], 
                                from_frame=tool.assembly[next(iter(tool.assembly))].pose("tcp"),
                                to_frame=tool.assembly[next(iter(tool.assembly))].pose("tip"))[2])

        """
        pose_offset
        """
        pose_offset = dorna_pose.Pose(pose=offset)

        """approach path"""
        approach_path = []
        if approach:
            _approach_path = [[0, 0, max(height_load, height_container)+padding, 0, 0, 0], 
                            [0, 0, height_container+gap, 0, 0, 0]]
            approach_path = [pose_offset.pose(offset=p) for p in _approach_path]

        """exit path"""
        exit_path = []
        if exit:
            _exit_path = [[0, 0, max(height_load, height_container)+padding, 0, 0, 0]]
            exit_path = [pose_offset.pose(offset=p) for p in _exit_path]

        """
        output config
        """
        # output init
        output_approach = []
        output_touch = []
        output_exit = []
        if trigger_io:
            # enable component setting
            enable = list(getattr(component, "enable", []))
            
            # disable component setting
            disable = list(getattr(component, "disable", []))
            
            # output config
            output_approach = disable + tool.enable
            output_touch = enable + tool.disable

        """
        run attachment
        """
        attach = [None, {"parent":None, "parent_anchor":None, "child_anchor":None, "offset":[0, 0, 0, 0, 0, 0], "offset_frame":"parent"}]
        exit_tool = {"solid": load_list[0], "anchor": load_anchor, "offset":[0, 0, 0, 0, 0, 0]}
        if attachment:
            attach = [load_list[0], {"parent": component.assembly[solid_name], "parent_anchor":anchor, "child_anchor":load_anchor, "offset": offset, "offset_frame": "parent"}]
            exit_tool = {"solid": tool.assembly[next(iter(tool.assembly))], "anchor": "tcp", "offset":[0, 0, 0, 0, 180, 0]}

        # add gravity compensation
        target_offset = offset[:]
        if gravity_offset > 0:
            target_offset[2] += gravity_offset

        """
        return
        """
        return {
            "target_solid": component.assembly[solid_name],
            "target_anchor": anchor, 
            "target_offset": target_offset,
            "output_approach": output_approach,
            "approach_tool": {"solid": load_list[0], "anchor": load_anchor, "offset":[0, 0, 0, 0, 0, 0]},
            "approach_path": approach_path,
            "output_touch": output_touch,
            "actions": actions,
            "sleep": 0,
            "attach": attach,
            "exit_tool": exit_tool,
            "exit_path": exit_path,
            "output_exit": output_exit,
            "height_tool": height_tool,
            "height_load": height_load,
            "height_container": height_container,
            "load_list": load_list,
            "tool": tool,
        }
    

    def place_in(self, anchor, solid_name="body", component=None, offset=[0, 0, 0, 0, 0, 0], approach=True, actions=[], exit=True, attachment=True, trigger_io=True, padding=50, gap=2, load_anchor="center", gravity_offset=1, **kwargs):
        # place parameters
        place_prm = self.place_setting(anchor=anchor, solid_name=solid_name, component=component, offset=offset, approach=approach, actions=actions, exit=exit, attachment=attachment, trigger_io=trigger_io, padding=padding, gap=gap, load_anchor=load_anchor, gravity_offset=gravity_offset,**kwargs)
        if not place_prm:
            return False
        
        # touch
        return self.touch(**place_prm)


    # this method moves the robot close to the anchor point and then turns the motor off and asks user to move the robot 
    # to the target anchor and offset using tool attached to the robot. Then the user click on a button to approve the calibration point
    # then the user moves the robot out and then the robot motors are turned on
    # the tool anchor and offset will match target anchor by the user. 
    # the robot will move to target_offset in the beginning
    def calibrate_anchor(self, target_solid, target_anchor, target_offset, tool_solid, tool_anchor, tool_offset):
        # initialize
        success = False

        # first we find the solutions for the target:
        J,C = self.core.IK(target_solid=target_solid, target_anchor=target_anchor, target_offset=target_offset, tool_solid=tool_solid, tool_anchor=tool_anchor, tool_offset=tool_offset,
            base_distance=self.base_distance, rail_step=self.rail_step, rail_span=self.rail_span, left_approach=self.left_approach,ref_joints=self.ref_joints)
        if C == 2:
            self.core.robot_api.jmove(joint=J, vel=self.jmove_vaj[0]*self.speed_factor,accel=self.jmove_vaj[1]*self.speed_factor,jerk=self.jmove_vaj[2]*self.speed_factor)
        else:
            print("🔴 could not find a valid approach to the calibration point")
            return False
        
        # now we are at the point. We show a message to the user and ask him to hold the robot to release the motor.
        input("1- ✋ hold the robot by hand...")

        # next we release the motor
        self.core.robot_api.motor(0)

        # now ask user to align the robot to the calibration point
        input("2- 🎯 take the robot to the calibration point...")
        
        # the joint recording from the user
        corrected_joint_values = self.core.robot_api.joint()
        
        # now we need to convert the corrected values to corrected xyz in world frame
        corrected_xyz_values = tool_solid.pose(anchor=tool_anchor, offset=tool_offset)

        raw_xyz_values = target_solid.pose(anchor=target_anchor, offset=[0,0,0,0,0,0])


        """

        # now we find the raw values by solving IK
        # note target_offset is all zeros because we want to exactly match the anchor point
        raw_values,C = self.core.IK(target_solid=target_solid, target_anchor=target_anchor, target_offset=[0,0,0,0,0,0], tool_solid=tool_solid, tool_anchor=tool_anchor, tool_offset=tool_offset,
                base_distance=self.base_distance, rail_step=self.rail_step, rail_span=self.rail_span, left_approach=self.left_approach,ref_joints=self.ref_joints)
        if C == 2:
            # we found a solution now we need to save it.
            # first we check if the error between calibrated point and the raw point is not too large only for robot joints.
            for i in range(6):        # compare only robot joints j0..j5
                if abs(corrected_values[i] - raw_values[i]) > 5: # if the error is more than 10 degrees we stop the calibration
                    input("🟡 calibration error is too large. Please try again. Move the robot out of the calibration point and when done press enter...")
                    self.core.robot_api.motor(1)
                    return False
                
            # if the error is small we save the calibration point
            success = True
            # we also print the raw and corrected values for the user to see
            print("raw values:", raw_values)
            print("corrected values:", corrected_values)
            # now we turn the motors on again

        else:
            input("🔴 could not find a valid solution for the calibration point. Move the robot out of the calibration point and when done press enter...")
            self.core.robot_api.motor(1)            
            return False     
        """
        # now we ask the user to move the robot out of the calibration point
        input("3- ⬆️ take the robot out of the calibration point...")
        # now we turn the motors on again
        self.core.robot_api.motor(1)

        self.core.calibration.add_point(raw_xyz_values, corrected_xyz_values, threshold=1e-3, dict_name=self.calibration_name)

        """

        # success
        if success:
            val = input("🔵 press enter to save the calibration point...")
            if val == "":
                self.core.calibration.add_point(raw_values, corrected_values, threshold=1e-3, dict_name=self.calibration_name)
        """

        return True


    # this method goes over all calibration anchors and calibrates them
    def calibrate(self, calibration_targets={}):
        # first we find the tool attached to the robot
        tool = self.tool_attached_to_the_robot()

        # now we find the solid that will be used for calibration
        tool_solid = tool.assembly[self.calibration_tool_solid_name]

        # calibration targets
        _calibration_targets = calibration_targets or self.calibration_targets
        # now we loop over the solids that will be used for calibration
        for solid in _calibration_targets:
            calibration_target_solid = self.component.assembly[solid]

            # now we go over all the calibration anchors and calibrate them
            for anchor in _calibration_targets[solid]:
                self.calibrate_anchor(target_solid=calibration_target_solid, target_anchor=anchor, target_offset=self.calibration_target_offset, tool_solid=tool_solid, tool_anchor=self.calibration_tool_anchor, tool_offset=self.calibration_tool_offset)
