from copy import deepcopy
from mergedeep import merge
from workspace.recipes.recipe import Recipe, RecipeError
import time

class PipettingSite(Recipe):
    DEFAULTS = dict(
        # ref joints
        target_offset=[0, 0, 150, 0, 180, 0],
        # IK
        rail_step=20, # 5
        rail_span=5, # 10        
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


    def pick_tip(self, anchor="place", padding=70, **kwargs):
        """Pick a disposable tip from the tip-box at ``anchor``.

        Resolves the tip-rack sitting on this pipetting site, delegates to
        ``pick`` with ``soft_approach=True`` and no IO trigger, then
        verifies tip presence via the component's sim-agnostic
        ``has_tip()`` (canned True in sim; sensor read on hardware;
        None → False when the pump is unreachable, so a BT action can
        ``return False`` and let the planner re-select — no retry loop).
        """
        component, solid_name = self._resolve_attached_component()

        # motion
        if not self.pick(anchor=anchor, solid_name=solid_name, component=component, padding=padding, trigger_io=False, soft_approach=True, **kwargs):
            raise RecipeError("pick_tip failed — could not pick from anchor")

        # verify the tip is actually on
        pipette = self.core.current_tool()
        if pipette is None:
            raise RecipeError("no pipette attached to the robot")
        return bool(pipette.has_tip(sim_return=True))


    def eject_tip(self, anchor="A1", shake_travel=5, **kwargs):
        """Eject the tip into a waste anchor, shaking laterally to dislodge.

        Runs ``place_setting`` with the pipette's ``eject_tip`` device call
        queued as an action. Prepends a left/right shake (``±shake_travel``)
        to the exit path. On hardware, verifies tip is gone via ``has_tip()``.

        Returns:
            True if tip ejected (or motion_result in simulation).
        """
        component, solid_name = self._resolve_attached_component()

        # find the pipette
        pipette = self.core.current_tool()
        if pipette is None:
            raise RecipeError("no pipette attached to the robot")

        # The pump's eject runs as a touch-down action — sim-agnostic
        # (the component's station returns canned True in sim without
        # touching hardware).
        actions = [[pipette.eject_tip, [], {}]]

        # motion prm
        motion_prm = self.place_setting(anchor=anchor, solid_name=solid_name, component=component, actions=actions, trigger_io=False, gravity_offset=0, **kwargs)

        # adjust exit_path
        motion_prm["exit_path"] = [[shake_travel, 0, motion_prm["height_load"], 0, 0, 0], [-shake_travel, 0, motion_prm["height_load"], 0, 0, 0], [shake_travel, 0, motion_prm["height_load"], 0, 0, 0]] + motion_prm["exit_path"]

        # run the motion
        self.touch(**motion_prm)

        # settle, then verify the tip is gone (canned False in sim —
        # the tip IS ejected there; None → treated as "still on" when
        # the pump is unreachable, so the caller fails before moving on)
        time.sleep(0.25)
        left = pipette.has_tip(sim_return=False)
        if left is None:
            return False
        return not left


    def immerse(self, anchor="place", depth=0, approach=True, padding=50, **kwargs):
        """Dip the tip ``depth`` mm into the well at ``anchor`` of the plate on this site.

        Thin wrapper: resolves the plate attached to the site and delegates
        to ``Recipe.immerse`` with the pipetting pattern (``approach=True``,
        single-motion with depth-adjusted approach corridor).
        """
        component, solid_name = self._resolve_attached_component()
        return super().immerse(
            dist=depth, anchor=anchor, solid_name=solid_name, component=component,
            approach=approach, padding=padding, **kwargs,
        )


    def retract(self, anchor="place", padding=50, **kwargs):
        """Lift the tip out of the well — inverse of ``immerse``.

        Thin wrapper: resolves the plate attached to the site and delegates
        to ``Recipe.retract`` with ``dist=0`` (tip rises exactly by its own length).
        """
        component, solid_name = self._resolve_attached_component()
        return super().retract(
            dist=0, anchor=anchor, solid_name=solid_name, component=component,
            padding=padding, **kwargs,
        )


    
    def aspirate(self, vol, speed=200):
        """Aspirate ``vol`` µL at ``speed`` µL/s.

        Sim-agnostic: the component branches internally (canned True in
        sim). Returns False when the pump refused or is unreachable —
        the BT action should ``return False`` and let the planner
        re-select (declarative retry, project-guide §8)."""
        pipette = self.core.current_tool()
        if pipette is None:
            raise RecipeError("no pipette attached to the robot")
        return bool(pipette.aspirate(vol, speed=speed, sim_return=True))


    def dispense(self, vol, speed=500, blowout=False):
        """Dispense ``vol`` µL at ``speed`` µL/s; ``blowout=True`` expels
        residual volume. Same contract as :meth:`aspirate`."""
        pipette = self.core.current_tool()
        if pipette is None:
            raise RecipeError("no pipette attached to the robot")
        return bool(pipette.dispense(vol, speed=speed, blowout=blowout, sim_return=True))


