"""The needle a nozzle wears: gauge and length.

A needle is consumable and interchangeable — the same dispense head
takes a 22G x 2" one day and a 16G x 4" the next — so it belongs in the
scene next to the component that wears it, not baked into a CAD model.

The two numbers do very different jobs, and it matters which:

* ``needle_length`` is GEOMETRY. It moves the tool's working point:
  a 50 mm needle puts the tip 50 mm further from the flange, and every
  ``immerse`` / ``above`` / IK solve follows it. Get this wrong and the
  arm dives the difference — into the bottom of a vial, or short of the
  liquid.
* ``needle_gauge`` is a PROPERTY. Its outer diameter is a fraction of a
  millimetre, far under any collision padding, so it changes no motion.
  It is carried for the fluid side (flow, dead volume, what the
  operator is looking at in the panel) and for provenance.

Reference plane: ``needle_length`` is measured from the nozzle face the
component's CAD already ends at — its existing ``tip`` plane. The
default is therefore ``0.0``: the component as its GLB models it, with
nothing added. Declare the real needle to extend it.
"""

from __future__ import annotations

from typing import Optional


# Birmingham gauge → outer diameter in mm (ISO 9626). Dispensing and
# lab needles are specified by gauge; nothing on the bench measures the
# steel, so this is the only place the number can come from.
GAUGE_OD_MM = {
    12: 2.769, 13: 2.413, 14: 2.108, 15: 1.829, 16: 1.651, 17: 1.473,
    18: 1.270, 19: 1.067, 20: 0.908, 21: 0.819, 22: 0.718, 23: 0.641,
    24: 0.565, 25: 0.514, 26: 0.464, 27: 0.413, 28: 0.362, 30: 0.311,
}


def gauge_od_mm(gauge) -> Optional[float]:
    """Outer diameter for a gauge, or ``None`` when unknown/undeclared.

    Unknown gauges return ``None`` rather than guessing: a made-up
    diameter reads as a measurement, and nothing here needs it badly
    enough to invent one.
    """
    if gauge is None:
        return None
    try:
        return GAUGE_OD_MM.get(int(gauge))
    except (TypeError, ValueError):
        return None


class Needle:
    """What is fitted to a nozzle right now.

    Attached to any component that dispenses through a needle, so the
    same two questions have the same two answers everywhere::

        tool.needle.length      # mm past the component's own tip plane
        tool.needle.gauge       # 22
        tool.needle.od          # 0.718 mm, or None if the gauge is unknown
        tool.needle.fitted      # False when length is 0 and no gauge given
    """

    def __init__(self, gauge=None, length: float = 0.0):
        self.gauge = gauge
        self.length = float(length or 0.0)

    @property
    def od(self) -> Optional[float]:
        return gauge_od_mm(self.gauge)

    @property
    def fitted(self) -> bool:
        return bool(self.length) or self.gauge is not None

    def __str__(self) -> str:
        if not self.fitted:
            return "no needle"
        g = f"{self.gauge}G" if self.gauge is not None else "?G"
        od = self.od
        od_s = f" ({od:.3f} mm od)" if od is not None else ""
        return f"{g} x {self.length:.1f} mm{od_s}"

    # ── Geometry helpers ─────────────────────────────────────────────

    def extend_anchor(self, anchor, axis: int = 2):
        """Push an ``[x, y, z, a, b, c]`` anchor out by the needle's
        length along ``axis`` (z by default). Returns a new list; the
        caller decides which anchors move (``tcp`` / ``tip``) and which
        stay (mounting holes, ``center``)."""
        out = list(anchor)
        out[axis] = out[axis] + self.length
        return out

    def collision_box(self, base_z: float, axis_scale=None) -> Optional[dict]:
        """A box for the needle itself, starting at ``base_z`` and
        running its length. ``None`` when no needle is fitted, so a
        caller can splice the result in unconditionally.

        The box is at least 1 mm wide even for a 27G: sub-millimetre
        obstacles are noise to the motion planner, and a hair-thin box
        is more likely to slip between voxels than to protect anything.
        """
        if not self.length:
            return None
        w = max(1.0, (self.od or 1.0))
        return {
            "pose": [0.0, 0.0, base_z + self.length / 2.0, 0.0, 0.0, 0.0],
            "scale": [w if axis_scale is None else axis_scale, w, self.length],
        }
