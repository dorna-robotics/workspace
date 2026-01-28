from copy import deepcopy
from mergedeep import merge
from workspace.recipes.recipe import Recipe


class Adapter(Recipe):
    DEFAULTS = dict(
        # IK
        left_approach=True,
        base_distance=200,
        rail_step=0, # 5
        rail_span=0, # 10        
        # calibration
        calibration_abc=True,
        calibration_targets={"body": ["clb_0", "clb_1", "clb_2", "clb_3"]}, # {solid_name: {anchor_1:..., anchor_2:...},...}
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
        

    def pick_from(self, anchor="place", solid_name="body", approach=True, exit=True, attachment=True, trigger_io=True, padding=75, gap=2, **kwargs):
        # pick parameters
        motion_prm = self.pick_setting(anchor=anchor, solid_name=solid_name, approach=approach, exit=exit, attachment=attachment, trigger_io=trigger_io, padding=padding, gap=gap, **kwargs)
        if not motion_prm:
            return False

        # update approach
        motion_prm["approach_path"] = [[10, 0, max(motion_prm["height_load"], motion_prm["height_container"]) + padding, 0, 0, 0], 
                                    [10, 0, motion_prm["height_load"] + motion_prm["height_tool"] + gap, 0, 0, 0],
                                    [10, 0, motion_prm["height_load"], 0, 0, 0]]

        # run touch
        return self.touch(**motion_prm)


    def place_in(self, anchor="place", solid_name="body", approach=True, exit=True, attachment=True, trigger_io=True, padding=75, gap=2, load_anchor="center", **kwargs):
            # place parameters
            motion_prm = self.place_setting(anchor=anchor, solid_name=solid_name, approach=approach, exit=exit, attachment=attachment, trigger_io=trigger_io, padding=padding, gap=gap, load_anchor=load_anchor, soft_approach=True, **kwargs)
            if not motion_prm:
                return False

            # update exit
            motion_prm["exit_path"] = [[10, 0, motion_prm["height_load"], 0, 0, 0], 
                        [0, 0, max(motion_prm["height_load"], motion_prm["height_container"]) + padding, 0, 0, 0]]

            # run touch
            return self.touch(**motion_prm)