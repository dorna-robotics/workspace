from copy import deepcopy
from mergedeep import merge
from workspace.recipes.recipe import Recipe, RecipeError


class Adapter(Recipe):
    DEFAULTS = dict(
        # motion
        jmove_vaj=[150, 750, 4500],
        lmove_vaj=[300, 700, 3000],
        # IK
        left_approach=True,
        base_distance=200,
        rail_step=0, # 5
        rail_span=0, # 10        
        # calibration
        calibration_abc=True,
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
        

    def pick(self, anchor="place", solid_name="body", approach=True, exit=True, attachment=True, trigger_io=True, padding=75, gap=2, motion_plan_kwargs={"gravity_vec":[0, 0, 1], "gravity_thr": 5}, **kwargs):
        """Pick from adapter using a 3-waypoint approach with +10mm X bias.

        Overrides the base pick so the gripper comes in slightly to the side
        (X=10) to clear the adapter wall, steps down, then descends to the load.
        Same args as ``Recipe.pick``. Default padding=75 for the larger clearance.
        """
        # pick parameters
        motion_prm = self.pick_setting(anchor=anchor, solid_name=solid_name, approach=approach, exit=exit, attachment=attachment, trigger_io=trigger_io, padding=padding, gap=gap, **kwargs)
        if not motion_prm:
            raise RecipeError("pick_setting failed — could not compute pick parameters")

        # update approach
        motion_prm["approach"] = [
                                    [[10, 0, max(motion_prm["height_load"], motion_prm["height_container"]) + padding, 0, 0, 0],
                                     [10, 0, motion_prm["height_load"] + motion_prm["height_tool"] + gap, 0, 0, 0],
                                     [10, 0, motion_prm["height_load"], 0, 0, 0]],
                                    [motion_prm["contact"]]]

        # run touch
        return self.touch(**motion_prm, motion_plan_kwargs=motion_plan_kwargs)


    def place(self, anchor="place", solid_name="body", approach=True, exit=True, attachment=True, trigger_io=True, padding=75, gap=2, load_anchor="center", motion_plan_kwargs={"gravity_vec":[0, 0, 1], "gravity_thr": 5}, **kwargs):
            """Place into adapter with a 2-waypoint angled exit.

            Uses ``soft_approach=True`` and an exit path that lifts straight
            then shifts out to the side, so the gripper clears the adapter wall.
            Same args as ``Recipe.place``. Default padding=75.
            """
            # place parameters
            motion_prm = self.place_setting(anchor=anchor, solid_name=solid_name, approach=approach, exit=exit, attachment=attachment, trigger_io=trigger_io, padding=padding, gap=gap, load_anchor=load_anchor, soft_approach=True, **kwargs)
            if not motion_prm:
                raise RecipeError("place_setting failed — could not compute place parameters")

            # update exit
            motion_prm["exit"] = [[[10, 0, motion_prm["height_load"], 0, 0, 0],
                        [0, 0, max(motion_prm["height_load"], motion_prm["height_container"]) + padding, 0, 0, 0]]]

            # run touch
            return self.touch(**motion_prm, motion_plan_kwargs=motion_plan_kwargs)