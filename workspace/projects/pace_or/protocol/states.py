# pace_or/states.py
# All state handlers for the PACE protocol as a class.
# rcp, rt, n are set once in __init__ — methods are flat, no nesting.

# ── Run parameters ────────────────────────────────────────────────────────────

INSPECTION_FRQ      = 4
INSPECTION_ROT      = 90
IMMERSE_40ML_DIST   = 90
RETRACT_40ML_DIST   = 10
IMMERSE_2ML_DIST    = 25
RETRACT_2ML_DIST    = 10

# ── Position mappings ─────────────────────────────────────────────────────────

SOURCE = [
    ("source_rack", "A1"),
    ("source_rack", "A2"),
    ("source_rack", "A3"),
    ("source_rack", "A4"),
]

WORKING = [
    ("working_rack", "B1"),
    ("working_rack", "B2"),
    ("working_rack", "B3"),
    ("working_rack", "B4"),
]

CAP_HOLDER = [
    "decapper_1",
    "decapper_2",
    "decapper_3",
    "decapper_4",
]

SHAKER_SLOTS = [
    ("shaker_1", "A1"),
    ("shaker_1", "A2"),
    ("shaker_2", "A1"),
    ("shaker_2", "A2"),
]

CAP_FEEDER = [
    ("cap_holder", "A1"),
    ("cap_holder", "A2"),
    ("cap_holder", "A3"),
    ("cap_holder", "A4"),
]

DOSING_40ML = [
    ("doser_40ml", "B1"),
    ("doser_40ml", "B2"),
    ("doser_40ml", "B3"),
    ("doser_40ml", "B4"),
]

DOSING_CLEAN = [
    ("doser_40ml", "A1"),
    ("doser_40ml", "A2"),
    ("doser_40ml", "A3"),
]

DOSING_WASTE = [
    ("doser_40ml", "A4"),
]

DOSING_2ML_END = [
    ("doser_2ml_end", "A1"),
    ("doser_2ml_end", "A2"),
    ("doser_2ml_end", "A3"),
    ("doser_2ml_end", "A4"),
]

DOSING_2ML_MIDDLE = [
    ("doser_2ml_middle", "A1"),
    ("doser_2ml_middle", "A2"),
    ("doser_2ml_middle", "A3"),
    ("doser_2ml_middle", "A4"),
]

RACK_2ML_END = [
    ("rack_2ml_end", "A1"),
    ("rack_2ml_end", "A2"),
    ("rack_2ml_end", "A3"),
    ("rack_2ml_end", "A4"),
]


# ── State handlers ────────────────────────────────────────────────────────────

class States:

    def __init__(self, rcp, rt, batch_size):
        self.rcp = rcp
        self.rt  = rt
        self.batch_size = batch_size

    # ── Shared helpers ────────────────────────────────────────────────────────

    def _inspect_tube(self):
        self.rcp["inspector"].present(approach=True)
        for _ in range(INSPECTION_FRQ):
            self.rcp["inspector"].rotate(rotation=INSPECTION_ROT)

    def _rinse_needle(self):
        for j in range(len(DOSING_CLEAN)):
            self.rcp[DOSING_CLEAN[j][0]].immerse(dist=IMMERSE_40ML_DIST, anchor=DOSING_CLEAN[j][1])
            self.rcp[DOSING_CLEAN[j][0]].dispense(vol=10)
            self.rcp[DOSING_CLEAN[j][0]].retract(dist=RETRACT_40ML_DIST, anchor=DOSING_CLEAN[j][1])
            self.rcp[DOSING_WASTE[0][0]].immerse(dist=IMMERSE_40ML_DIST, anchor=DOSING_WASTE[0][1], padding=10)
            self.rcp[DOSING_WASTE[0][0]].dispense(vol=10)
            self.rcp[DOSING_WASTE[0][0]].retract(dist=RETRACT_40ML_DIST, anchor=DOSING_WASTE[0][1])

    # ── States ────────────────────────────────────────────────────────────────

    def inspected(self, i):
        self.rcp[SOURCE[i][0]].pick(SOURCE[i][1])
        self._inspect_tube()
        self.rcp["scale"].place("place")
        self.rcp["scale"].weight()
        self.rcp["scale"].pick("place")
        self.rcp["decapper_5"].place(exit=False)
        self.rcp["decapper_5"].decap(approach=False)
        self.rcp[CAP_HOLDER[i]].place()
        self.rcp["decapper_5"].pick()
        self.rcp[WORKING[i][0]].place(WORKING[i][1])

    def dosed_40ml(self, i):
        self.rcp[DOSING_40ML[i][0]].immerse(dist=IMMERSE_40ML_DIST, anchor=DOSING_40ML[i][1])
        self.rcp[DOSING_40ML[i][0]].dispense(vol=10)
        self.rcp[DOSING_40ML[i][0]].retract(dist=RETRACT_40ML_DIST, anchor=DOSING_40ML[i][1])
        self._rinse_needle()

    def loaded_shaker(self, i):
        self.rcp[WORKING[i][0]].pick(WORKING[i][1])
        self.rcp["decapper_5"].place()
        self.rcp[CAP_HOLDER[i]].pick()
        self.rcp["decapper_5"].cap(exit=False)
        self.rcp["decapper_5"].pick(approach=False)
        self.rcp[SHAKER_SLOTS[i][0]].place(SHAKER_SLOTS[i][1])

    def shaken(self, _):
        for i in range(self.batch_size):
            self.rcp[SHAKER_SLOTS[i][0]].shake(duration=120)

    def stop_shaken(self):
        for i in range(self.batch_size):
            self.rcp[SHAKER_SLOTS[i][0]].stop_shaking()

    def cap_fed(self, i):
        self.rt.step(f"Feeding cap {i+1}/{self.batch_size} from autosampler")
        self.rcp["autosampler"].above(anchor="plate_center")
        self.rcp["autosampler"].present_cap(self.rcp["inspector"])
        self.rcp["autosampler"].pick(approach=False)
        self.rcp[CAP_FEEDER[i][0]].place(CAP_FEEDER[i][1])

    def retrieved(self, i):
        self.rcp[SHAKER_SLOTS[i][0]].pick(SHAKER_SLOTS[i][1])
        self._inspect_tube()
        self.rcp["decapper_5"].place(exit=False)
        self.rcp["decapper_5"].decap(approach=False)
        self.rcp[CAP_HOLDER[i]].place()
        self.rcp["decapper_5"].pick()
        self.rcp[WORKING[i][0]].place(WORKING[i][1])

    def dosed_2ml(self, i):
        self.rcp[DOSING_40ML[i][0]].immerse(dist=IMMERSE_40ML_DIST, anchor=DOSING_40ML[i][1], padding=150)
        self.rcp[DOSING_40ML[i][0]].dispense(vol=10)
        self.rcp[DOSING_40ML[i][0]].retract(dist=RETRACT_40ML_DIST, anchor=DOSING_40ML[i][1])
        self.rcp[DOSING_2ML_MIDDLE[i][0]].immerse(dist=IMMERSE_2ML_DIST, anchor=DOSING_2ML_MIDDLE[i][1])
        self.rcp[DOSING_2ML_MIDDLE[i][0]].dispense(vol=10)
        self.rcp[DOSING_2ML_MIDDLE[i][0]].retract(dist=RETRACT_2ML_DIST, anchor=DOSING_2ML_MIDDLE[i][1])
        self.rcp[DOSING_2ML_END[i][0]].immerse(dist=IMMERSE_2ML_DIST, anchor=DOSING_2ML_END[i][1])
        self.rcp[DOSING_2ML_END[i][0]].dispense(vol=10)
        self.rcp[DOSING_2ML_END[i][0]].retract(dist=RETRACT_2ML_DIST, anchor=DOSING_2ML_END[i][1])
        self._rinse_needle()

    def recapped_final(self, i):
        self.rcp[WORKING[i][0]].pick(WORKING[i][1])
        self.rcp["decapper_5"].place()
        self.rcp[CAP_HOLDER[i]].pick()
        self.rcp["decapper_5"].cap(exit=False)
        self.rcp["decapper_5"].pick(approach=False)
        self.rcp[SOURCE[i][0]].place(SOURCE[i][1])

    def capped_2ml(self, i):
        self.rcp[RACK_2ML_END[i][0]].pick(RACK_2ML_END[i][1])
        self.rcp["decapper_5"].place()
        self.rcp[CAP_FEEDER[i][0]].pick(CAP_FEEDER[i][1])
        self.rcp["decapper_5"].cap(exit=False)
        self.rcp["decapper_5"].pick(approach=False)
        self.rcp["decapper_5"].vibrate()
        self._inspect_tube()
        self.rcp[RACK_2ML_END[i][0]].place(RACK_2ML_END[i][1])

    def make(self):
        return {
            "inspected":      self.inspected,
            "dosed_40ml":     self.dosed_40ml,
            "loaded_shaker":  self.loaded_shaker,
            "shaken":         self.shaken,
            "stop_shaken":    self.stop_shaken,
            "cap_fed":        self.cap_fed,
            "retrieved":      self.retrieved,
            "dosed_2ml":      self.dosed_2ml,
            "recapped_final": self.recapped_final,
            "capped_2ml":     self.capped_2ml,
        }
