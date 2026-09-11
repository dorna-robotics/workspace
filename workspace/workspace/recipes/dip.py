"""DipSite — the ONE way a carried tool goes into a vessel and out.

A needle over a rack of vials, a pH probe over the same rack, a needle
into the waste container: every "dip" is the same three verbs with the
same references, and they live here once. ``DosingSite`` and
``PhMeterSite`` differ only in WHAT the verbs target (a plate sitting on
an adapter, or the rack itself) and in their defaults — nothing about
how the motion is made.

    above(anchor, padding)   planned travel to a hover ``padding`` mm above
                             the payload top, tip-referenced, fused
    immerse(dist, anchor)    ONE straight leg: the tip ``dist`` mm below
                             the payload top (from the hover / the gap)
    retract(dist, anchor)    the mirror: ONE straight leg up to ``dist``
                             above the payload top (deferrable under fusion)

Every verb:
  * is tip-referenced — ``padding`` and ``dist`` are measured from the
    payload top to the TOOL TIP; a held load (a pipette's tip) is
    folded in so the reference is the same for all three
  * keeps a ``lock_j5`` tool's wrist pinned — the hover included
    (``Recipe.above``), so the dive and the lift never carry a wrist
    roll; ``approach_j5=`` per call wins
  * wires itself for attribution (``_wire_verb``): every robot command
    of every dip verb is stamped with its recipe and verb
  * runs at the recipe's ``lmove_vaj`` unless the call names ``vaj``
    (``Recipe.immerse`` / ``retract``)

A subclass sets the defaults it wants (``DIP_ANCHOR`` … ``DIP_OUT``) and,
when its component is a holder, overrides ``_dip_target`` to resolve
what actually sits on it.
"""

from __future__ import annotations

from workspace.recipes.recipe import Recipe


class DipSite(Recipe):
    #: Defaults a subclass tunes; every caller may pass its own.
    DIP_ANCHOR = "place"    # anchor the verbs target when none is given
    DIP_PADDING = 50        # above(): hover height over the payload top (mm)
    DIP_IN = 0              # immerse(): tip depth below the payload top (mm)
    DIP_OUT = 0             # retract(): tip height above the payload top (mm)

    def _dip_target(self):
        """``(component, solid_name)`` the verbs act on. Default: this
        recipe's own component — a rack whose anchors ARE the vessels.
        A site over a holder resolves what sits on it instead."""
        return self.component, "body"

    # ── the three verbs ───────────────────────────────────────────────
    def above(self, anchor=None, padding=None, **kwargs):
        anchor = self.DIP_ANCHOR if anchor is None else anchor
        padding = self.DIP_PADDING if padding is None else padding
        self._wire_verb("above", anchor)
        component, solid_name = self._dip_target()
        # Tip-referenced: the hover is ``padding`` from the payload top
        # to the TOOL TIP — the same reference immerse and retract use —
        # so a held load (a pipette's tip) lifts the hover by its height.
        _, _, height_load = self._get_tool_and_load_height()
        return super().above(
            anchor=anchor, solid_name=solid_name, component=component,
            padding=padding, tool_tcp_z_offset=height_load,
            tool_tip_z_offset=height_load, **kwargs,
        )

    def immerse(self, dist=None, anchor=None, **kwargs):
        anchor = self.DIP_ANCHOR if anchor is None else anchor
        dist = self.DIP_IN if dist is None else dist
        self._wire_verb("immerse", anchor)
        component, solid_name = self._dip_target()
        return super().immerse(dist=dist, anchor=anchor, solid_name=solid_name,
                               component=component, **kwargs)

    def retract(self, dist=None, anchor=None, **kwargs):
        anchor = self.DIP_ANCHOR if anchor is None else anchor
        dist = self.DIP_OUT if dist is None else dist
        self._wire_verb("retract", anchor)
        component, solid_name = self._dip_target()
        return super().retract(dist=dist, anchor=anchor, solid_name=solid_name,
                               component=component, **kwargs)
