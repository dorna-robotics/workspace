# workspace/recipes/change_tool.py
from workspace.components.core import Core
from workspace.components.tool_rack import ToolRack
from workspace import Workspace

def change_tool(core: Core, tool_rack: ToolRack, workspace: Workspace):
    """
    Approaches the tool rack and activates the tool changer
    to pick up a new tool.
    """
    # first we find the position of the tool rack picking place
    safe_pose = tool_rack.assembly["tool_rack"].pose(anchor="tool_connection", in_frame=core.rail_base,  offset=[0, 0, 20, 0, 0, 0])
    # this function needs to know all of the solids in the scene
    core.plan_motion(safe_pose)
    core.robot_api.jmove(safe_pose, speed=100, accel=100)


    inside Core, we should have a function for simple moves
    another function for planning path

    the path planning function will need the scene and plan the motion from current joint, to the target safe_pose
    the simple move will call jmove, with 





