import workspace.recipes.util as util

class HandleFeeder:
    def __init__(self, workspace, core, 
        container,
        solid_name = None,
        anchor = "pick",
        clearance_offset = [0, 0, 0, 180, 0, 0],
        ref_joints = [0, 0, 0, 0, 0, 0, 0, 0],
        speed_factor=0.5,
        left_approach=True,
        base_distance=250,
        rail_step=5.0,
        rail_span=2,
        jmove_vaj=[200, 5000, 50000],
        lmove_vaj=[200, 5000, 50000],
        motion="lmove"
        ):
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
        self.anchor = anchor
        
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
        

    def pick_from(self, index, **kwargs):
        # assign kwargs
        for k, v in kwargs.items():
            setattr(self, k, v)
        
        # ref joints
        if self.ref_joints is None:
            print("No reference joints defined, cannot pick component")
            return False
        
        # tool
        tool = util.tool(self.ws, self.core)
        if tool is None:
            print("No tool attached to the robot")
            return False
        
        # we check if there is an item in the index
        solid_bottom = util.solid_attached_to_anchor(self.container.assembly[self.solid_name], index)
        if solid_bottom is None:
            print(f"No item found in position {index}, cannot pick")
            return False
        component_bottom = self.ws.components[solid_bottom.component]
        height = component_bottom.height
        
        # touch
        return util.touch(
            self.core,
            target_solid = self.container.assembly[self.solid_name],
            target_anchor = index, 
            target_offset = [0, 0, height, 180, 0, 0],
            approach_tool = {"solid": tool.assembly[next(iter(tool.assembly))], "anchor": "tcp", "offset":[0, 0, 0, 0, 0, 0]},
            approach_path = [[0, 0, max(self.container.height, height)+20, 180, 0, 0]],
            output_config = tool.enable,
            actions= [],
            sleep= 0.1,
            attach = [component_bottom.assembly[next(iter(component_bottom.assembly))], {"parent": tool.assembly[next(iter(tool.assembly))], "parent_anchor":"tcp", "child_anchor":"center", "offset": [0, 0, height, 180, 0, 0], "offset_frame": "parent"}],
            exit_tool = {"solid": component_bottom.assembly[next(iter(component_bottom.assembly))], "anchor": "center", "offset":[0, 0, 0, 0, 0, 0]},
            exit_path = [[0, 0, max(self.container.height, height)+20, 0, 0, 0]],
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

