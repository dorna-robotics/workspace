from copy import deepcopy
from mergedeep import merge
from workspace.components.factory import register
from workspace.components.gripper.gripper import Gripper


@register("gripper_suction")
class GripperSuction(Gripper):
    """A venturi suction cup. Three IO states, each a config list of
    ``[output, value, wait_s]`` rows (scene yaml, written out):

        output_enable   suck — the pick holds
        output_disable  off  — the release
        output_blow     blow-off — air pushed out of the cup

    ``blow(duration)`` is the one timed op: blow-off for ``duration``
    seconds, then the off state. A place never blows: its release is
    ``output_disable``; blow is an explicit step of an action
    (``ws.components["gripper_suction_1"].blow()``) or the operator's
    button."""
    DEFAULTS = dict(
        anchors={"body": {"center": [0, 0, 0, 0, 0, 0], "tcp":[0, 0, 139+2.5, 0, 0, 0],  "tip":[0, 0, 140+2.5, 0, 0, 0]}},
        collision_box = 
            {"body":[
                {"pose":[0.0, 0.0, 43.5/2, 0.0, 0.0, 0.0], "scale":[43.0, 43.0, 43.50]},#[xyzabc] , [lx,ly,lz]
                {"pose":[0.0, 0.0, (140+2.5)/2, 0.0, 0.0, 0.0], "scale":[12, 12, 140+2.5]},#[xyzabc] , [lx,ly,lz]

        ]},
        #cfg
        has_tool_changer = False,
        output_enable=[[0, 1, 0], [1, 1, 0.75]], # output_enable=[[1, 1, 0.5], [0, 1, 0.75]]
        output_disable=[[0, 1, 0], [1, 0, 0.25]],
        # The blow-off output. UNSET here — it is bench wiring, so the
        # scene yaml writes it (``output_blow: [[2, 1, 0]]``); blow()
        # refuses to run on the unset row rather than fire a guess.
        output_blow=[[0, 1, 0], [1, 0, 0.25], [0, 0, 0]],
    )

    def __init__(self, name: str, cfg: dict, workspace, **kwargs):
        # prm
        prm = deepcopy(Gripper.DEFAULTS) # default
        merge(prm, self.DEFAULTS) # self
        merge(prm, cfg) # cfg
        merge(prm, kwargs) # kwargs
        
        # update type
        prm.setdefault("type", getattr(self.__class__, "_registered_type", cfg.get("type")))
        
        self.output_blow = prm.pop("output_blow")

        super().__init__(
            name=name,
            workspace=workspace,
            **prm
        )

    def blow(self, duration=5):
        """Blow-off for ``duration`` seconds, then the off state — ONE
        atomic op: the air never stays on, so the wait is a plain
        sleep, not a checkpoint (a pause takes effect after it, like
        the waits inside the IO rows themselves)."""
        if not any(r and r[0] is not None for r in self.output_blow):
            raise RuntimeError(
                f"{self.name}: output_blow is not set — write the blow-off output in the "
                f"scene yaml, e.g. output_blow: [[2, 1, 0]]")
        rt = self.workspace.rt
        rt.output(config=self.output_blow)
        self.output_state(2)
        rt.sleep(float(duration), checkpoint=False)
        self.disable()

    def operator_actions(self) -> list[dict]:
        return [
            {"label": "Enable",  "method": "enable",  "icon": "power",     "group": "io"},
            {"label": "Disable", "method": "disable", "icon": "power-off", "group": "io"},
            {"label": "Blow",    "method": "blow",    "icon": "zap"},
        ]
