from copy import deepcopy
from mergedeep import merge
from dorna2 import pose as dorna_pose
from workspace.recipes.recipe import Recipe, RecipeError


class Hotel(Recipe):
    DEFAULTS = dict(
        # ref joints
        target_anchor="clb_0",
        target_offset=[0, 0, 10, 0, 180, 0],
        # motion
        jmove_vaj=[150, 750, 4500],
        lmove_vaj=[300, 700, 3000],
        # IK
        left_approach=True,
        base_distance=150,
        rail_step=20, #10
        rail_span=5, # 5    
        # calibration
        calibrate_abc = True, # True
    )

    def __init__(self, workspace, core, component, **kwargs):
        # prm
        prm = deepcopy(Recipe.DEFAULTS) # default
        merge(prm, self.DEFAULTS) # self
        merge(prm, kwargs) # kwargs

        # super init
        super().__init__(
            workspace=workspace,
            core=core,
            component=component,
            **prm
        )
        
    
    def _shelf_height(self, motion_prm, level, solid_name):
        """For a hotel that asks for it (``lift_from_shelf``), the rim to
        clear is this shelf's (``top_{level}``), not the hotel's base
        ``top``: measured from the base, the lift before the slide-out
        grows a pitch per level (300 mm on a 4-level hotel's top shelf) and
        drives the plate into the shelf above. Other hotels: unchanged."""
        if not getattr(self.component, "lift_from_shelf", False):
            return
        solid = self.component.assembly[solid_name]
        try:
            top = solid.pose(f"top_{level}")
        except Exception:
            return
        motion_prm["height_container"] = abs(dorna_pose.transform_pose(
            [0, 0, 0, 0, 0, 0], from_frame=top, to_frame=solid.pose(f"place_{level}"))[2])

    def pick(self, level=0, solid_name="body", approach=True, exit=True, attachment=True, trigger_io=True, padding=15, gap=2, motion_plan_kwargs={"gravity_vec":[0, 0, 1], "gravity_thr": 5}, **kwargs):
        """Pick a plate from the hotel shelf at ``level`` using a lateral slide-in approach.

        Hotels have side-loaded shelves, so the approach enters from the
        side (``size[0] + padding`` out in X), slides in, descends, picks.
        Exit mirrors that path.

        Args:
            level: Integer shelf level — builds anchor ``place_{level}``.
            padding, gap: Side offset and clearance (mm).
        """
        # anchor
        anchor = f"place_{level}"

        # pick parameters
        motion_prm = self.pick_setting(anchor=anchor, solid_name=solid_name, approach=approach, exit=exit, attachment=attachment, trigger_io=trigger_io, padding=padding, gap=gap, **kwargs)
        if not motion_prm:
            raise RecipeError("pick_setting failed — could not compute pick parameters")
        self._shelf_height(motion_prm, level, solid_name)

        # update approach
        motion_prm["approach"] = [
                            [
                                [self.component.size[0] + padding, 0, motion_prm["height_load"] + motion_prm["height_tool"]+ gap, 0, 0, 0],
                                [padding, 0, motion_prm["height_load"] + motion_prm["height_tool"] + gap, 0, 0, 0],
                                [padding, 0, motion_prm["height_load"], 0, 0, 0]
                            ],
                            [
                                motion_prm["contact"]
                            ]
                        ]

        # update exit
        motion_prm["exit"] = [[[0, 0, motion_prm["height_container"] + gap, 0, 0, 0],
                    [0, 0, motion_prm["height_container"] + padding, 0, 0, 0],
                    [self.component.size[0] + padding, 0, motion_prm["height_container"] + padding, 0, 0, 0]]]

        # run touch
        return self.touch(**motion_prm, motion_plan_kwargs=motion_plan_kwargs)


    def place(self, level=0, solid_name="body", approach=True, exit=True, attachment=True, trigger_io=True, padding=15, gap=2, load_anchor="center", motion_plan_kwargs={"gravity_vec":[0, 0, 1], "gravity_thr": 5}, **kwargs):
            """Place a plate into the hotel shelf at ``level`` via a lateral slide-in.

            Mirror of ``pick`` — approaches from the side, slides in, lowers,
            releases, then exits laterally. See ``pick`` for args.
            """
            # anchor
            anchor = f"place_{level}"

            # place parameters
            motion_prm = self.place_setting(anchor=anchor, solid_name=solid_name, approach=approach, exit=exit, attachment=attachment, trigger_io=trigger_io, padding=padding, gap=gap, load_anchor=load_anchor, **kwargs)
            if not motion_prm:
                raise RecipeError("place_setting failed — could not compute place parameters")
            self._shelf_height(motion_prm, level, solid_name)

            # update approach
            motion_prm["approach"] = [
                            [[self.component.size[0] + padding, 0, motion_prm["height_container"] + padding, 0, 0, 0],
                             [0, 0, motion_prm["height_container"] + padding, 0, 0, 0],
                             [0, 0, motion_prm["height_container"] + gap, 0, 0, 0]],
                            [motion_prm["contact"]]]

            # update exit
            motion_prm["exit"] = [[[padding, 0, max(motion_prm["height_load"], motion_prm["height_container"]) + motion_prm["height_tool"] + gap, 0, 0, 0],
                        [self.component.size[0] + padding, 0, max(motion_prm["height_load"], motion_prm["height_container"]) + motion_prm["height_tool"] + gap, 0, 0, 0]]]

            # run touch
            return self.touch(**motion_prm, motion_plan_kwargs=motion_plan_kwargs)