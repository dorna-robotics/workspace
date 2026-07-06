from copy import deepcopy
from mergedeep import merge
from dorna2 import Solid
import time

from workspace.components.factory import register


# Stationary pneumatic probe — self-contained (handles its own pneumatic IO,
# no Arm base). Exposes the same enable()/disable() interface as a gripper, so
# it's drop-in compatible and operator-callable. enable() drives the plunger
# all the way down (extended); disable() retracts it all the way up (rest) —
# each fires the matching pneumatic IO and plays a time-based animation modeled
# on the shaker: the component owns the interpolation and update_pose() (called
# by the render loop each tick), so the motion shows live while the call runs
# on a worker thread (operator-action handler or recipe).
@register("rotating_cylinder_mkb1630")
class RotatingCylinderMKB1630:
    DEFAULTS = dict(
        anchors={
            "housing": {
                "center": [0, 0, 0, 0, 0, 0],
                "place": [0, 0, 54, 0, 0, 0],
                "hole_0": [ 25,  25, 0, 0, 0, 0],
                "hole_1": [-25,  25, 0, 0, 0, 0],
                "hole_2": [-25, -25, 0, 0, 0, 0],
                "hole_3": [ 25, -25, 0, 0, 0, 0],
            },
            "plunger": {
                "center": [0, 0, 0, 0, 0, 0],
                "place":    [0, 0, 86.5, 0, 0, 0],
            },
        },
        collision_box={
            "housing": [
                {"pose": [0, 0, (65 + 6.5) / 2, 0.0, 0.0, 0.0], "scale": [65, 65, 65 + 6.5], "padding_enabled": True},
            ],
            "plunger": [
                {"pose": [0, 0, 86.5/2, 0.0, 0.0, 0.0], "scale": [14, 14, 86.5], "padding_enabled": True},
            ],
        },
        # limit — [start, end] plunger poses; each pose is [rotation (deg about
        # z), extension (mm along z)]; extension negative = plunger driven down
        # out of the housing. start = rest, end = extended.
        toggle_range = [[90.0, 0.0], [0.0, -37.0]],
        toggle_period = 1,  # second, time to toggle from one state to another
        # pneumatic IO  [[pin, index, time]]
        output_enable=[[None, None, 0]],
        output_disable=[[None, None, 0]],
    )

    def __init__(self, name: str, cfg: dict, workspace, **kwargs):
        prm = deepcopy(self.DEFAULTS)
        merge(prm, cfg)
        merge(prm, kwargs)
        prm.setdefault("type", getattr(self.__class__, "_registered_type", cfg.get("type")))

        self.name = name
        self.workspace = workspace
        self.type = prm["type"]

        self.assembly = {
            k: Solid(
                type=f"rotating_cylinder_mkb1630_{k}",
                anchors=prm["anchors"][k],
                component=self.name,
                **({"collision_box": cb[k]} if (cb := prm.get("collision_box")) and k in cb else {}),
            )
            for k in prm["anchors"]
        }

        # pneumatic IO (handled here instead of via the Arm base)
        self.output_enable = prm["output_enable"]
        self.output_disable = prm["output_disable"]
        self._output_state = None

        # motion (mirrors Shaker: range + period live on the component)
        self.toggle_range = prm["toggle_range"]
        self.toggle_period = prm["toggle_period"]
        self.rotation, self.extension = prm["toggle_range"][0]  # current pose
        self._toggle_state = "start"  # start (rest) or end (extended)

        self.update_pose()

    # set or get pneumatic output state
    def output_state(self, state=None):
        if state is None:
            return self._output_state
        self._output_state = state
        return self._output_state

    # set or get toggle state ("start" = retracted/up, "end" = extended/down)
    def toggle_state(self, state=None):
        if state is None:
            return self._toggle_state
        self._toggle_state = state
        return self._toggle_state

    def enable(self):
        """Drive the plunger all the way down (extended) with animation
        and fire the enable IO. Same interface as ``Gripper.enable``;
        exposed to operator control. Blocks for ``toggle_period`` while
        the render loop animates the motion — call from a worker thread."""
        self.workspace.rt.output(config=self.output_enable)
        self._animate(*self.toggle_range[1])   # end = extended / down
        self._toggle_state = "end"
        self.output_state(1)

    def disable(self):
        """Retract the plunger all the way up (rest) with animation and
        fire the disable IO. Same interface as ``Gripper.disable``;
        exposed to operator control. Blocks for ``toggle_period`` while
        the render loop animates the motion — call from a worker thread."""
        self.workspace.rt.output(config=self.output_disable)
        self._animate(*self.toggle_range[0])   # start = rest / up
        self._toggle_state = "start"
        self.output_state(0)

    def _animate(self, target_rotation, target_extension, stop_event=None):
        """Interpolate the plunger from its current pose to the target
        over ``toggle_period`` seconds. ``update_pose`` reads
        ``rotation``/``extension`` each render tick, so the motion shows
        live. A set ``stop_event`` aborts mid-move (caller decides where
        to settle)."""
        r0, e0 = self.rotation, self.extension
        r1, e1 = target_rotation, target_extension
        start = time.time()
        while True:
            current = time.time()
            if current - start >= self.toggle_period:
                break
            if stop_event is not None and stop_event.is_set():
                return
            ratio = (current - start) / self.toggle_period
            self.rotation  = r0 + (r1 - r0) * ratio
            self.extension = e0 + (e1 - e0) * ratio
            time.sleep(0.015)  # ~60fps
        self.rotation, self.extension = r1, e1

    def operator_actions(self) -> list[dict]:
        return [
            {"label": "Enable",  "method": "enable"},
            {"label": "Disable", "method": "disable"},
        ]

    def update_pose(self):
        self.assembly["plunger"].attach_to(
            parent=self.assembly["housing"],
            parent_anchor="place",
            child_anchor="center",
            offset=[0, 0, self.extension, 0, 0, self.rotation],
            offset_frame="parent",
        )
