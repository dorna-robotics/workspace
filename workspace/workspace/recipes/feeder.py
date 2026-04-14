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
        pick_offset=0,
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
        self.pick_offset = prm["pick_offset"]
        self.shift_steps = prm["shift_steps"]

    # mix: mix the feeder for certain turns and shift the slots
    def mix(self, **kwargs):
        # current joint
        current_joint = self.rt.joint()

        # new_joint
        new_joint = current_joint[:]

        # change the direction if necessary
        if abs(new_joint[self.component.axis_cfg["axis"]]) > self.thr_dir:
            self.mix_dir = -1 * self.mix_dir

        return self.rotate_in_step(step=self.mix_dir * self.shift_steps, **kwargs)

    # rotate the feeder to move to the nth slot from the current
    def rotate_in_step(self, step=1, **kwargs):
        rt = self.rt

        # current joint
        current_joint = rt.joint()

        # new_joint
        new_joint = current_joint[:]  # init
        axis = self.component.axis_cfg["axis"]

        current_steps = round(
            (new_joint[axis] - self.pick_offset) * (self.component.num_slots / 360)
        )
        new_joint[axis] = (step + current_steps) * (360 / self.component.num_slots) + self.pick_offset

        # motion
        rt.checkpoint()
        return rt.jmove(
            joint=new_joint,
            vel=self.vaj_mix[0],
            accel=self.vaj_mix[1],
            jerk=self.vaj_mix[2],
        )

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
        return super().above(
            anchor=anchor,
            solid_name=solid_name,
            component=component,
            padding=padding,
            tool_tcp_z_offset=tool_tcp_z_offset,
            tool_tip_z_offset=tool_tip_z_offset,
            **kwargs
        )

    """
    use inspector to check if cap is present
    if not mix and run again
    if yes, present that position to the pick position of the feeder
    index_list contains a list of indices to check, each element is a (step, preset)
    """
    def present_cap(self, inspector, **kwargs):
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
