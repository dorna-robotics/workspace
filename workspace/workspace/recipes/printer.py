from copy import deepcopy
from mergedeep import merge
from workspace.recipes.recipe import Recipe
from dorna2 import pose as dorna_pose


class Printer(Recipe):
    DEFAULTS = dict(
        # ref joint
        target_anchor="place",
        base_distance = 100,
        rail_step=20, #10
        rail_span=5, # 5 
        # calibration
        calibration_targets={"body": ["clb_0"]},
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
        

    def pick(self, anchor="place", solid_name="body", approach=True, exit=True, attachment=True, trigger_io=True, padding=30, gap=2, **kwargs):
        return super().pick(anchor=anchor, solid_name=solid_name, approach=approach, exit=exit, attachment=attachment, trigger_io=trigger_io, padding=padding, gap=gap, **kwargs)



    def place(self, anchor="place", solid_name="body", approach=True, exit=True, attachment=True, trigger_io=True, padding=30, gap=2, load_anchor="center", gravity_offset=2, **kwargs):
        # tool
        tool = self.tool_attached_to_the_robot()

        # item in tool
        solid_in_tool = self.solid_attached_to_tool(tool)

        # place offset based on the radius of the tube
        offset= self.component._place_offset(
            self.workspace.components[solid_in_tool.component].size[0]/2
        )

        # place
        return super().place(anchor=anchor, solid_name=solid_name, offset=offset, approach=approach, exit=exit, attachment=attachment, trigger_io=trigger_io, padding=padding, gap=gap, load_anchor=load_anchor, gravity_offset=gravity_offset, **kwargs)
    

    # dry run spin
    def dry_run_spin(self, count=1):
        if not self.component._simulation_mode:
            return self.component.device.dry_run_spin(count=count)
        return True

    # real print
    def print_label(self, data, code_type="code128", autorun=True, verify=True):
        if not self.component._simulation_mode:
            from workspace.components.printer.cab_wrapper import CodeType
            return self.component.device.print_one(CodeType(code_type), data, autorun=autorun, verify=verify)
        return True
