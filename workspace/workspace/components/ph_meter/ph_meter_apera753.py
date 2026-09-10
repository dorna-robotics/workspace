"""Apera LabSen 753 pH probe — geometry for the shared ``PhMeter`` family
base.

Mechanically a different envelope from ``ph_meter_atlas``, electrically
the same thing: a BNC glass electrode read by an EZO-pH circuit over
UART. So the device link, bus attachment, atomic ops and operator buttons
all come from ``ph_meter.PhMeter`` unchanged — this module contributes
the mechanical envelope and nothing else. See that module's docstring for
the scene-yaml shape and the port/simulation contract.

Hardware notes (Apera LabSen 753, aperainst.com/labsen-753)
===========================================================
* **Spear food electrode**, not a general immersion probe — titanium
  body, S-type spear glass, designed to be pushed into cheese / dough /
  meat. It reads liquids fine; the geometry is just built for solids.
* **"Ceramic + Open" double junction**, polymer electrolyte, long-life
  AgCl reference. Vendor soaking solution is **3M KCl** — not DI water,
  which leaches the junction.
* **Connector is BNC + RCA.** The EZO takes the BNC (pH signal) only.
  The RCA carries the probe's built-in **NTC 30 kΩ** temperature element
  and the EZO has no input for it, so that lead is unused here and ATC
  is NOT automatic — the chip compensates with whatever
  ``set_temperature_compensation`` last stored. Reading the NTC would
  need its own ADC; an EZO-RTD will not take it (that circuit expects a
  PT-series RTD, not a 30 kΩ NTC).
* Membrane impedance <250 MΩ; vendor response spec 15–30 s to a fully
  stabilised reading. Measured on the bench, buffer-to-buffer transfers
  settled in **20–45 s** — so ``read_stable``'s default 20-reading budget
  (~18 s) is too short for a transfer and will return a not-yet-settled
  value.
* Range 0–14 pH, 0–80 °C. Six-month warranty.

**A BNC is a bayonet** — push and quarter-turn. There is nothing to
tighten. Torquing one crushes the dielectric into a short, which presents
as 0 mV in every solution (reads a steady pH 7.00) and, before it fails
outright, as a large offset plus wandering readings that look exactly
like a worn-out electrode.

IMMERSION DEPTH
===============
Both the glass AND the reference junction must be under the liquid.
Wetting the glass while the junction sits in air gives a live, stable,
completely wrong reading.

On this probe both sit near the end of the spear: the pH glass is only
the last few mm of the point, and the junction is a short way behind it —
it has to be, or the probe could not work stabbed into a block of cheese.
Do NOT read the spec's "(Φ6 × 50) mm measuring tip" as the depth needed;
that is the dimension of the whole spear section, not the distance back
to the junction. Find the junction visually on the actual probe and make
sure the liquid covers it with margin. A bubble trapped on the spear
point does the same damage as air.

Geometry, measured off ph_meter_apera753.glb (mm, z from the flange
face):
      0.0 -  10.0   mount flange       43.0 dia
     10.0 - 100.0   slotted guard cage 38.5 dia over the ribs
    100.0 - 120.0   electrode body     44.5 dia at its widest
    120.0 - 173.5   spear shaft, tapering to the tip at 173.5
                    (the Φ6x50 measuring tip; junction near its top)
"""

from __future__ import annotations

from copy import deepcopy

from mergedeep import merge

from workspace.components.factory import register
from workspace.components.gripper.gripper import Gripper
from workspace.components.ph_meter.ph_meter import PhMeter


@register("ph_meter_apera753")
class PhMeterApera753(PhMeter):
    DEFAULTS = dict(
        anchors={"body": {"center": [0, 0, 0, 0, 0, 0], "tcp": [0, 0, 188.5, 0, 0, 0], "tip": [0, 0, 188.5, 0, 0, 0]}},
        # Two boxes: everything wide (flange + cage + electrode body) in
        # one block, the slim tapering spear in the other, split at the
        # z=120 shoulder.
        collision_box =
            {"body":[
                {"pose":[0.0, 0.0, 110.0/2, 0.0, 0.0, 0.0], "scale":[44.5, 44.5, 110.0]},
                {"pose":[0.0, 0.0, (110.0+188.5)/2, 0.0, 0.0, 0.0], "scale":[12.0, 12.0, 188.5-110.0]},
        ]},
    )

    def __init__(self, name: str, cfg: dict, workspace, **kwargs):
        # prm
        prm = deepcopy(Gripper.DEFAULTS) # base
        merge(prm, PhMeter.DEFAULTS) # family
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
