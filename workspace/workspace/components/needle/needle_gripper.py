"""Needle on the tool changer — the CARRIED end of a fluid
path.

Geometry comes from ``Gripper``; the fluidics come from ``PumpedTool``,
which binds this needle to one pump component and one valve port
(scene keys ``pump`` / ``pump_port``). The needle owns no device and
takes no bus row — the pump component is the sole owner of the device
id (device-guide §4). See ``components/pump/pump_link.py``.

The real tool carries a stripper-weight-and-rod assembly around the
needle: on the way out of a septum vial the weight holds the vial
down so the needle strips clean. The rods clash with j4 / the robot
body unless the wrist roll (j5) is held at 0 during the vertical
entry and exit — ``lock_j5`` declares that constraint, and
``immerse`` / ``retract`` read it off the mounted tool and pin every
joint target they execute (``recipe.py _tool_lock_j5``). Aspirate /
dispense involve no motion, so nothing else is needed.

Scene yaml::

    needle_gripper_1:
      type: "needle_gripper"
      has_tool_changer: true
      pump: "pump_1"     # "" -> unplumbed, geometry only
      pump_port: 3               # valve port this tube lands on
      lock_j5: 0                 # wrist roll during entry/exit; null = free

Because the fluid method names match the pipettor's, a project can
drive this needle with the existing ``DosingSite`` recipe — motion
targets the vessel, ``aspirate`` / ``dispense`` resolve through
``core.current_tool()`` — instead of a parallel recipe family.
"""

from copy import deepcopy
from mergedeep import merge
from workspace.components.factory import register
from workspace.components.gripper.gripper import Gripper
from workspace.components.needle.needle import Needle
from workspace.components.pump.pump_link import PumpedTool


@register("needle_gripper")
class NeedleGripper(PumpedTool, Gripper):
    # Geometry measured off needle_gripper.glb (solid cross-sections
    # every 0.25 mm; needle bore by least-squares circle fit), z = 0 at
    # the tool-changer flange, +z toward the tip:
    #   0     -  41.50  tool changer + body (43 x 43 at the flange,
    #                   tapering to 33.56 x 32.28 — boxed at flange width)
    #   41.50 -  55.75  upper plate      40.00 x 66.00
    #   55.75 -  67.50  collar           23.21 x 62.35
    #   67.50 - 154.00  column bar        6.35 x 62.35
    #   154.00- 168.00  bottom plate     40.00 x 66.00
    #   168.00- 169.10  needle, exposed  d 0.711 (22 ga: OD 0.711 / ID 0.406)
    # The needle runs on the centreline (axis fitted to (0.0, 0.0)) and
    # stands 1.10 mm proud of the bottom plate, so tcp/tip is z = 169.10
    # — 11.35 mm below the previous model's flush tip at 157.751.
    DEFAULTS = dict(
        anchors={"body": {"center": [0, 0, 0, 0, 0, 0], "tcp":[0, 0, 169.10, 0, 0, 0],  "tip":[0, 0, 169.10, 0, 0, 0]}},
        collision_box =
            {"body":[
                {"pose":[0.0, 0.0, 41.50/2, 0.0, 0.0, 0.0], "scale":[43.0, 43.0, 41.50]},
                {"pose":[0.0, 0.0, 41.50+(14.25/2), 0.0, 0.0, 0.0], "scale":[66.0, 40.0, 14.25]},
                {"pose":[0.0, 0.0, 55.75+(11.75/2), 0.0, 0.0, 0.0], "scale":[62.35, 23.22, 11.75]},
                {"pose":[0.0, 0.0, 67.50+(86.50/2), 0.0, 0.0, 0.0], "scale":[62.35, 6.35, 86.50]},
                {"pose":[0.0, 0.0, 154.00+(14.0/2), 0.0, 0.0, 0.0], "scale":[66.0, 40.0, 14.0]},
                {"pose":[0.0, 0.0, 168.00+(1.10/2), 0.0, 0.0, 0.0], "scale":[0.72, 0.72, 1.10]},

        ]},
        #cfg
        has_tool_changer = False,
        output_enable=[[None, None, 0], [None, None, 0]],
        output_disable=[[None, None, 0], [None, None, 0]],
        # ── fluid path ───────────────────────────────────────────────
        # "" -> unplumbed: the needle still mounts and moves, it just
        # has no pump behind it (and every fluid call says so plainly).
        pump="",
        pump_port=None,
        # ── the fitted needle ────────────────────────────────────────
        # Declarative: what is on the tool right now, for the fluid
        # side and the panel. Geometry stays the CAD's business — these
        # move no anchors (components/needle/needle.py).
        needle_gauge=None,     # e.g. 22
        needle_length=0.0,     # mm, e.g. 50.8 for 2"
        # ── motion constraint ────────────────────────────────────────
        # Wrist roll (j5, degrees) pinned during immerse/retract. The
        # stripper rods hit j4 / the robot body at any other roll, so
        # 0 is the hardware's default; null frees the wrist (a rod-less
        # variant). immerse/retract read this off the mounted tool.
        lock_j5=0.0,
    )

    def __init__(self, name: str, cfg: dict, workspace, **kwargs):
        # prm
        prm = deepcopy(Gripper.DEFAULTS) # default
        merge(prm, self.DEFAULTS) # self
        merge(prm, cfg) # cfg
        merge(prm, kwargs) # kwargs

        # update type
        prm.setdefault("type", getattr(self.__class__, "_registered_type", cfg.get("type")))

        super().__init__(
            name=name,
            workspace=workspace,
            **prm
        )

        # Bind the fluid path (lazy: the pump may be built after us).
        self._init_pump_link(workspace, prm)
        self.needle = Needle(gauge=prm.get("needle_gauge"),
                             length=prm.get("needle_length", 0.0))
        # Wrist-roll pin for immerse/retract (None = unconstrained).
        v = prm.get("lock_j5")
        self.lock_j5 = None if v is None else float(v)
