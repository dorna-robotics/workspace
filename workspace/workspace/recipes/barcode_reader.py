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
                    timeout: float = 2.5, sim_return=None):
        """Rotate-until-read: the label can face away from the window,
        so try up to ``angles`` presentations, turning the held item by
        ``rotation`` degrees (j5) between misses. Call it with the item
        already presented (``present()``); returns the decoded string,
        or ``None`` after every angle missed — the caller decides how
        loud that is. On an infinite wrist the turns accumulate (no
        unwind); in simulation the first read returns the canned code,
        so sim runs stay single-pass."""
        for attempt in range(max(1, int(angles))):
            if attempt:
                self.rotate(rotation)
            code = self.code(allowed=allowed, timeout=timeout,
                             sim_return=sim_return)
            if code:
                return code
        return None
