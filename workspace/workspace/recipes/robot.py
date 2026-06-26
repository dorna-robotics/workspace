from copy import deepcopy
from mergedeep import merge
from workspace.recipes.recipe import Recipe


class Robot(Recipe):
    """Generic robot-motion recipe — no tool, no rack, no pick target.

    Use this when a project needs robot motions that don't belong to a
    component/tool — most commonly ``park()`` (move to a safe joint
    config) from a Start/Park action, on a bench with no tool changer or
    where the gripper is permanently mounted.

    It subclasses :class:`Recipe` to get the motion methods (``park``,
    ``rotate``, ``jmove``-style moves) but **deliberately skips
    ``Recipe.__init__``'s IK / reference-joint computation** — that step
    validates a pick target against the component's anchors and would
    fail here (there is no target). Mirrors how :class:`MultiMeter` skips
    the motion-IK init; the difference is this recipe still needs the
    motion settings (speed_factor / vaj) that ``park`` reads, so it sets
    those explicitly.

    Wire it in recipes.j2 as, e.g.::

        robot:
          class: workspace.recipes.robot.Robot
          kwargs: {speed_factor: 20}

    and call ``rcp["robot"].park(joint=PARK_JOINTS, has_motion_plan=True)``.
    Works on any project — it depends only on ``core`` + the runtime, not
    on any component.
    """

    DEFAULTS = dict(
        motion_type="lmove",
        speed_factor=0.5,
        jmove_vaj=[200, 500, 3000],
        lmove_vaj=[600, 1400, 6000],
    )

    def __init__(self, workspace, core, component=None, **kwargs):
        # Merge defaults (caller wins). ``component`` is accepted but
        # unused — there's no component to operate on.
        prm = deepcopy(self.DEFAULTS)
        merge(prm, kwargs)

        self.workspace = workspace
        self.core = core
        self.component = component

        # Only the fields ``park`` / the joint-motion path read — NO IK,
        # NO reference joint (the part Recipe.__init__ would compute).
        self.motion_type = prm["motion_type"]
        self.speed_factor = prm["speed_factor"]
        self.jmove_vaj = prm["jmove_vaj"]
        self.lmove_vaj = prm["lmove_vaj"]
