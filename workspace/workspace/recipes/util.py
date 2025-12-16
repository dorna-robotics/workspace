# workspace/recipes/handle_microtube.py
import numpy as np


def tool(ws, core):
    tool = None
    if core.has_toolchanger:
        for child in core.toolchanger_robot_side.children["toolchanger_connection"]:
            solid = child["child_solid"]
            tool = ws.components[solid.component]
            continue

    else:
        for child in core.robot_flange.children["output"]:
            solid = child["child_solid"]
            tool = ws.components[solid.component]
            continue
    
    return tool

def solid_attached_to_tool(tool):        
    # we check if there is component in the gripper already
    for child in tool.assembly[next(iter(tool.assembly))].children["tcp"]:
        return child["child_solid"]
    return None


def solid_attached_to_anchor(base_solid, anchor):        
    try:
        for child in base_solid.children[anchor]:
            return child["child_solid"]
    except:
        pass
    return None


def touch(core,
        target_solid, target_anchor, target_offset=[0, 0, 0, 0, 0, 0],
        output_init=[],
        approach_tool={"solid": None, "anchor": None, "offset":[0, 0, 0, 0, 0, 0]},
        approach_path = [],
        output_config=[],
        actions=[],
        sleep=0,
        attach=[None, {"parent":None, "parent_anchor":None, "child_anchor":None, "offset":[0, 0, 0, 0, 0, 0], "offset_frame":"parent"}],
        exit_tool={"solid": None, "anchor": None, "offset":[0, 0, 0, 0, 0, 0]},
        exit_path = [],
        motion="lmove",
        base_distance=250, 
        rail_step=10,
        rail_span=0,
        left_approach=True,
        ref_joints=None,
        jmove_vaj=[200, 5000, 50000],
        lmove_vaj=[200, 5000, 50000],
        speed_factor=0.1,
        ):
    # vaj_map
    vaj_map = {
        "jmove": jmove_vaj,
        "lmove": lmove_vaj 
    }

    """
    output_init
    """
    core.robot_api.output(config=output_init)


    """
    approach
    """
    path = list(approach_path+[target_offset])
    for i in range(len(path)):
        J,C = core.IK(target_solid=target_solid, target_anchor=target_anchor, target_offset=path[i],
                            tool_solid=approach_tool["solid"], tool_anchor=approach_tool["anchor"], tool_offset=approach_tool["offset"],
                            base_distance=base_distance, rail_step=rail_step, rail_span=rail_span, ref_joints=ref_joints, left_approach=left_approach)
        if C == 2:
            if i == 0: # first motion jmove
                core.robot_api.jmove(J, vel=vaj_map["jmove"][0]*speed_factor, accel=vaj_map["jmove"][1]*speed_factor, jerk=vaj_map["jmove"][2]*speed_factor)
            else: # rest are all based on the user motion command  
                getattr(core.robot_api, motion)(J, vel=vaj_map[motion][0]*speed_factor, accel=vaj_map[motion][1]*speed_factor, jerk=vaj_map[motion][2]*speed_factor)   
        else:
            print("Could not find a valid pose to approach")
            return False
    
    """
    output_config
    """
    core.robot_api.output(config=output_config)

    """
    actions, sleep
    """
    for func, args, kwargs in actions:
        func(*args, **kwargs)
    core.robot_api.sleep(sleep)

    """
    attach
    """
    attach[0].attach_to(**attach[1])

    """
    exit
    """
    path = list(exit_path)
    for i in range(len(path)):
        J,C = core.IK(target_solid=target_solid, target_anchor=target_anchor, target_offset=path[i],
                            tool_solid=exit_tool["solid"], tool_anchor=exit_tool["anchor"], tool_offset=exit_tool["offset"],
                            base_distance=base_distance, rail_step=rail_step, rail_span=rail_span, ref_joints=ref_joints, left_approach=left_approach)        
        if C == 2:
            getattr(core.robot_api, motion)(J, vel=vaj_map[motion][0]*speed_factor, accel=vaj_map[motion][1]*speed_factor, jerk=vaj_map[motion][2]*speed_factor)    
        else:
            print("Could not find a valid pose to approach")
            return False

    
    return True