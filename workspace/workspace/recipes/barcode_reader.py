import time as _time

from copy import deepcopy
from mergedeep import merge
from workspace.recipes.recipe import Recipe
from workspace.components.barcode_reader.ds457_driver import ALL_SYMBOLOGIES


"""Barcode-reader recipe — present the held item to the scanner, then read.

Same shape as ``Inspector`` (workspace/recipes/inspector.py): a thin
``Recipe`` over a fixed bench station the robot presents a tube to. The
robot motion (``present()``) always runs; only the device read
(``scan()`` / ``code()``) returns canned values in simulation, so workflow
timing is identical with or without hardware.

The component is the ``BarcodeReaderZebraVertical144mm`` device component;
the read is sim-agnostic (the station hides sim vs real). Wire it as
``rcp["barcode_reader"]`` in recipes.j2.
"""


class BarcodeReader(Recipe):
    DEFAULTS = dict(
        base_distance=200,
        # ref joint
        target_anchor="place",
    )

    def __init__(self, workspace, core, component, **kwargs):
        # prm
        prm = deepcopy(Recipe.DEFAULTS)
        merge(prm, self.DEFAULTS)
        merge(prm, kwargs)

        super().__init__(
            workspace=workspace,
            core=core,
            component=component,
            **prm,
        )

    def present(self, approach=True, padding=50, soft_approach=False, load_anchor="center", **kwargs):
        """Position the held item in front of the scanner's window
        ("place" anchor). Robot motion runs whether or not we're in
        simulation — only ``detect()`` / ``code()`` returns canned values
        when the reader is offline."""
        self._wire_verb("present")
        return self.place(
            anchor="place",
            solid_name="body",
            approach=approach,
            exit=False,
            attachment=False,
            trigger_io=False,
            padding=padding,
            gap=2,
            soft_approach=soft_approach,
            load_anchor=load_anchor,
            gravity_offset=0,
            **kwargs,
        )

    def detect(self, allowed=ALL_SYMBOLOGIES, timeout: float = 10.0, sim_return=None):
        """Trigger one on-demand detect via the component's device link —
        returns a ``Scan`` (status + data + symbology), or ``None`` when
        disconnected and not in sim. The scanner stays quiet until this is
        called. ``allowed`` restricts which symbologies count (default:
        all). ``sim_return`` (device-guide §17): pass a ``Scan`` to inject
        the sim reading; omit it to use the component's canned default."""
        kw = {} if sim_return is None else {"sim_return": sim_return}
        return self.component.detect(allowed=allowed, timeout=timeout, **kw)

    def code(self, allowed=ALL_SYMBOLOGIES, timeout: float = 10.0, sim_return=None):
        """Convenience: trigger a detect and return just the decoded
        barcode string (or None). ``allowed`` restricts symbologies.
        ``sim_return`` (device-guide §17): pass a ``str`` to inject the sim
        barcode; omit it to use the component's canned default."""
        kw = {} if sim_return is None else {"sim_return": sim_return}
        return self.component.code(allowed=allowed, timeout=timeout, **kw)

    def rotate(self, rotation=90, **kwargs):
        """Rotate j5 — used to flip the presentation angle."""
        return super().rotate(rotation=rotation, joint="j5", **kwargs)

    def code_rotate(self, angles=4, rotation=90, allowed=ALL_SYMBOLOGIES,
                    timeout: float = 2.5, sim_return=None,
                    continuous=True, sweep=360,
                    sweep_vaj=[90, 500, 3000], trigger_timeout: float = 0.6):
        """Rotate-until-read. Call it with the item already presented
        (``present()``); returns the decoded string, or ``None`` when
        nothing read — the caller decides how loud that is.

        ``continuous=True`` (default, needs the infinite wrist): ONE
        non-blocking j5 turn of ``sweep`` degrees (``timeout=0`` — the
        command returns while the robot is still moving) while the
        scanner live-triggers in short ``trigger_timeout`` windows; the
        FIRST read halts the robot on the spot. Typically sub-second on
        hardware instead of stop-and-shoot. ``sweep_vaj`` paces the
        turn (default 90 deg/s: ~8 trigger windows per revolution).
        In simulation the turn runs to completion first (the sim
        executes motions serially), then the canned code returns —
        same call sequence, deterministic timing.

        ``continuous=False`` — or a limited wrist, automatically:
        stepped mode — up to ``angles`` presentations, ``rotation``
        degrees between misses, one ``timeout`` read each."""
        if continuous and getattr(self.core, "j5_infinite", False):
            rt = self.rt
            rt.checkpoint()
            if getattr(self.core, "_simulation_mode", False):
                # Sim: full blocking turn, then the canned read — the
                # halt below is the SimulationAPI's acknowledged no-op.
                self.rotate(sweep, vaj=sweep_vaj)
                code = self.code(allowed=allowed, timeout=timeout,
                                 sim_return=sim_return)
                rt.halt()
                return code
            cur = [float(v) for v in rt.joint()]
            tgt = cur[:]
            tgt[5] += float(sweep)
            # Launch the turn and return immediately (dorna2 play
            # timeout=0); the read loop below owns the clock.
            rt.jmove(joint=tgt, vel=sweep_vaj[0], accel=sweep_vaj[1],
                     jerk=sweep_vaj[2], timeout=0)
            deadline = _time.time() + abs(sweep) / max(sweep_vaj[0], 1) + 3.0
            code = None
            try:
                while _time.time() < deadline:
                    code = self.code(allowed=allowed,
                                     timeout=trigger_timeout,
                                     sim_return=sim_return)
                    if code:
                        break
            finally:
                # Stop where we are (first read), or acknowledge the
                # already-finished turn — either way the queue is clean
                # and the next verb starts from live joints.
                rt.halt()
                rt.delay(0.1)
            return code
        for attempt in range(max(1, int(angles))):
            if attempt:
                self.rotate(rotation)
            code = self.code(allowed=allowed, timeout=timeout,
                             sim_return=sim_return)
            if code:
                return code
        return None
