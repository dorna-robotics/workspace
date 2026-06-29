from workspace.components.gripper.gripper import Gripper


class ProbeBase(Gripper):
    """Shared base for the calibration probe tools (probe_vertical,
    probe_vertical_100, probe_horizontal).

    The tool->pocket orientation is intrinsic too: it lives in each
    concrete probe's ``tcp`` anchor (its abc), so the calibrator solves IK
    straight to that anchor with no extra rotation.

    Adds the contact-probe IO on top of the standard Gripper. Like every
    other gripper, the IO *config* lives on the component; unlike the
    output grippers (which the recipe drives via rt.output), probing is a
    blocking input-watch + halt, so the action lives here as probe().
    Only probe tools have it — it is independent to this family.

    IO polarity: probe input reads probe_safe_value (0) when the rod is
    not touching anything, and probe_trigger_value (1) on contact.

    Simulation is owned by the robot controller (the probe sensor is wired
    to it), so we branch on core._simulation_mode — never special-cased in
    the recipe. In sim there is no IO, so probe() echoes the joint target
    the caller already IK-solved for the expected contact point.
    """

    # Probe IO config (mirrors how output_enable/disable live on the
    # component). Override per tool via cfg if a rig is wired differently.
    PROBE_DEFAULTS = dict(
        probe_input_index=7,
        probe_trigger_value=1,   # input value when the rod touches a surface
        probe_safe_value=0,      # input value when not in contact
        probe_timeout=30,        # s — dorna.probe() block timeout
        halt_accel=1000,         # tight decel applied the moment contact triggers
    )

    def __init__(self, name: str, workspace, type=None, **kwargs):
        # Pull probe config out of kwargs so it doesn't get passed down to
        # the Solid anchors machinery; fall back to PROBE_DEFAULTS.
        self.probe_input_index = kwargs.pop("probe_input_index", self.PROBE_DEFAULTS["probe_input_index"])
        self.probe_trigger_value = kwargs.pop("probe_trigger_value", self.PROBE_DEFAULTS["probe_trigger_value"])
        self.probe_safe_value = kwargs.pop("probe_safe_value", self.PROBE_DEFAULTS["probe_safe_value"])
        self.probe_timeout = kwargs.pop("probe_timeout", self.PROBE_DEFAULTS["probe_timeout"])
        self.halt_accel = kwargs.pop("halt_accel", self.PROBE_DEFAULTS["halt_accel"])

        super().__init__(name=name, workspace=workspace, type=type, **kwargs)

    @property
    def _core(self):
        # Resolve at call time — the tool may be built before core is wired.
        return self.workspace.components["core"]

    def probe_ready(self):
        """Real mode: read the input and confirm it is at the safe value
        before any motion. Returns True if safe to probe. In sim there is
        no IO, so it is always ready."""
        core = self._core
        if core._simulation_mode:
            return True
        return core.dorna.input(index=self.probe_input_index) == self.probe_safe_value

    def probe(self, joint_target, expected_world_xyz=None, vel=2.0, accel=50, jerk=500, timeout=None):
        """Drive the rod to a pre-IK'd joint target while watching the
        contact IO, halting the instant it triggers.

        joint_target       : 8-vector, already IK-solved by the caller so
                             the wrist branch + rail offset are pinned.
        expected_world_xyz : where contact is expected; used only to keep
                             the call shape symmetric (sim echoes joints).
        Returns the trigger joint vector on contact, or None on timeout.
        """
        core = self._core
        if not core._simulation_mode:
            dorna = core.dorna
            dorna.lmove(joint=list(joint_target), vel=vel, accel=accel, jerk=jerk, timeout=0)
            trig = dorna.probe(
                index=self.probe_input_index,
                val=self.probe_trigger_value,
                timeout=self.probe_timeout if timeout is None else timeout,
            )
            dorna.halt(accel=self.halt_accel)
            if trig is False or trig is None:
                return None
            return list(trig)

        # sim: no contact sensor. The caller IK-solved joint_target for the
        # expected contact point, so the rod "stops" exactly there.
        return list(joint_target)
