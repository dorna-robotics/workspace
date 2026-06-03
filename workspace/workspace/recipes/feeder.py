from copy import deepcopy
from mergedeep import merge
from workspace.recipes.recipe import Recipe, RecipeError


class Feeder(Recipe):
    DEFAULTS = dict(
        # IK
        base_distance=100,
        # calibration
        calibration_targets={"body": ["clb_0"]},  # {solid_name: {anchor_1:..., anchor_2:...},...}
        # index_list
        index_list=[],
        # mix
        vaj_mix=[200, 600, 3000],
        thr_dir=10000,
        shift_steps=21,
    )

    def __init__(self, workspace, core, component, **kwargs):
        prm = deepcopy(Recipe.DEFAULTS)
        merge(prm, self.DEFAULTS)
        merge(prm, kwargs)

        super().__init__(
            workspace=workspace,
            core=core,
            component=component,
            **prm
        )

        # mix sign
        self.mix_dir = 1

        # index list
        self.index_list = prm["index_list"]

        # mix
        self.vaj_mix = prm["vaj_mix"]
        self.thr_dir = prm["thr_dir"]
        self.shift_steps = prm["shift_steps"]

    def mix(self, **kwargs):
        """Agitate the feeder by rotating ``shift_steps`` in the current direction.

        Reverses direction when the feeder's axis exceeds ``thr_dir`` to keep
        it within its joint limits. Delegates to ``rotate_in_step``.
        """
        # current joint
        current_joint = self.rt.joint()

        # new_joint
        new_joint = current_joint[:]

        # change the direction if necessary
        if abs(new_joint[self.component.axis_cfg["axis"]]) > self.thr_dir:
            self.mix_dir = -1 * self.mix_dir

        return self.rotate_in_step(step=self.mix_dir * self.shift_steps, **kwargs)

    def rotate_in_step(self, step=1, **kwargs):
        """Rotate the feeder by ``step`` slots at workflow (mix) speed.

        Thin delegate to ``self.component.rotate`` — the grid-snap math
        lives on the component now. This wrapper just overrides the
        speed to ``vaj_mix`` so workflow agitation stays slower than
        the operator-facing default.
        """
        return self.component.rotate(step=step, vaj=self.vaj_mix)

    def pick(
        self,
        anchor="place",
        solid_name="body",
        component=None,
        approach=True,
        actions=[],
        exit=True,
        attachment=True,
        trigger_io=True,
        padding=25,
        gap=2,
        tool_tcp_z_offset=0,
        tool_tip_z_offset=0,
        **kwargs
    ):
        """Pick from the feeder's current slot. Thin override, padding defaults to 25 mm."""
        return super().pick(
            anchor=anchor,
            solid_name=solid_name,
            component=component,
            approach=approach,
            actions=actions,
            exit=exit,
            attachment=attachment,
            trigger_io=trigger_io,
            padding=padding,
            gap=gap,
            tool_tcp_z_offset=tool_tcp_z_offset,
            tool_tip_z_offset=tool_tip_z_offset,
            **kwargs
        )

    def above(self, anchor="place", solid_name="body", component=None, padding=25, tool_tcp_z_offset=0, tool_tip_z_offset=0, **kwargs):
        """Hover above the feeder pick point. Thin override, padding defaults to 25 mm."""
        return super().above(
            anchor=anchor,
            solid_name=solid_name,
            component=component,
            padding=padding,
            tool_tcp_z_offset=tool_tcp_z_offset,
            tool_tip_z_offset=tool_tip_z_offset,
            **kwargs
        )

    def present_cap(self, inspector, **kwargs):
        """Rotate a slot that contains a cap into the pick position, mixing if needed.

        Iterates through ``self.index_list`` — each entry is ``(step, preset)``
        where ``preset`` is a kwargs dict for ``inspector.detect``. If detection
        succeeds at a position, rotates the feeder to ``step`` and returns True.
        If no slot has a cap, runs ``mix()`` and recurses.

        Args:
            inspector: An Inspector recipe whose ``detect(**preset)`` returns
                True when the current view has the desired feature.

        Returns:
            True once a cap is positioned, False if ``index_list`` is empty.
        """
        rt = self.rt

        # empty index list
        if not self.index_list:
            return False

        # loop over index list
        for step, preset in self.index_list:
            # object exists
            if inspector.detect(**preset, **kwargs):
                # move the feeder to that position
                self.rotate_in_step(step=step)
                rt.checkpoint()
                rt.delay(0.5)   # pause-aware
                return True

        # mix
        self.mix()

        # run recursively
        return self.present_cap(inspector=inspector, **kwargs)
