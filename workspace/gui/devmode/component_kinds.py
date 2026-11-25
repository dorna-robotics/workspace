"""What kind of part a custom component is — tube, cap, rack, cap holder,
adapter plate, tool rack, device — and what that kind needs.

Workspace's library already says it: every tube has ``top`` / ``cap_seat`` /
``place`` and subclasses ``Tube`` (its ``cap_seat`` slot and ``size`` are
what the recipes use); every cap subclasses ``Cap`` with ``cap_type`` /
``twist`` / ``pitch`` (the decapper recipe screws by them); racks and cap
holders subclass ``Rack``, whose grid (offset, pitch, rows, cols) makes the
A1… slot anchors and the ``slot`` list the solver fills; adapters and tool
racks have their own bases. A part made in the editor gets the same: the
kind is guessed from its id / file name / description, the template below
gives the kind's anchors from the measured model (its size, top and the
holes found from above), and the part is saved on the kind's base class.
The assistant gets the kind's convention and real library examples, and
adjusts from there.
"""
from __future__ import annotations

import functools
import re
import string

# order matters: the first kind whose words match wins ("cap holder" before
# "cap", "tube rack" is a rack, "vial cap" a cap)
KINDS = {
    "tool_rack": dict(
        label="Tool rack", folder="tool_rack", base=("workspace.components.tool_rack.tool_rack", "ToolRack"),
        words=r"tool ?rack|tool ?holder|tool ?dock|tool ?changer",
        rule="A tool rack stands on the fixture plate on 4 holes (hole_0..3 at ±25 mm, z 0) and holds a robot "
             "tool: 'place' is where the tool's flange docks — [0, 46, top − 2] tilted [127.28, −127.28, 0] like "
             "the library's — and clb_0 a calibration point just below it. Its one slot is 'place'."),
    "capholder": dict(
        label="Cap holder", folder="capholder", base=("workspace.components.rack.rack", "Rack"),
        words=r"cap ?holder|cap ?rack|cap ?park|cap ?tray",
        rule="A cap holder is a rack for caps: one slot per pocket named A1… (A1 at −x, −y; columns +x, rows +y), "
             "each slot anchor at the pocket's floor, rz 45; 'place' at the pocket floor height, 'top' on the top "
             "face. Saved as a Rack: a regular grid becomes offset / pitch / rows / cols."),
    "adapter": dict(
        label="Adapter plate", folder="adapter", base=("workspace.components.adapter.adapter", "Adapter"),
        words=r"adapter|adaptor|base ?plate|mount(ing)? ?plate|riser",
        rule="An adapter plate sits on the fixture plate under a rack: mounting holes hole_0, hole_1… at z 0 on "
             "the plate's 25 mm grid; 'place' is where the rack it carries lands (its top face). Its one slot is "
             "'place'. Calibration points clb_0… must land on fixture-plate holes wherever the adapter is mounted "
             "by hole_0 or hole_1: their x and y offsets from those holes are multiples of 25 mm. Use the 4 corners "
             "of the widest such grid rectangle inside the footprint, plus 2 near the centre clear of the holes."),
    "rack": dict(
        label="Rack", folder="rack", base=("workspace.components.rack.rack", "Rack"),
        words=r"rack|tray|well ?plate|microplate|block|stand|holder",
        rule="A rack holds vessels: one slot per hole named A1… (A1 at −x, −y; columns +x, rows +y), each slot "
             "anchor at the hole's FLOOR — where the tube's bottom rests — rz 45; 'place' at the floor height, "
             "'top' on the top face. Saved as a Rack: a regular grid becomes offset / pitch / rows / cols."),
    "cap": dict(
        label="Cap", folder="cap", base=("workspace.components.cap.cap", "Cap"),
        words=r"cap|lid|closure|septum|stopper",
        rule="A cap has 'center' at its base and 'top' on its top face (where the gripper reaches it). Saved as "
             "a Cap: cap_type 'screw' or 'snap', and for a screw cap twist (degrees to screw it on) and pitch "
             "(mm a turn) — the decapper recipe uses them. Without a datasheet they follow bna's amber cap: "
             "rise 0.79 × cap height, pitch 3.33 × diameter/26, twist = 360 × rise / pitch."),
    "tube": dict(
        label="Tube / vial", folder="tube", base=("workspace.components.tube.tube", "Tube"),
        words=r"tube|vial|falcon|eppendorf|cuvette|bottle|flask|ampoule|syringe ?barrel",
        rule="A tube has 'center' at its bottom, 'top' at the rim (or the top of its cap if the model has one), "
             "'cap_seat' where a cap's base sits on the threads (a few mm under the rim: 0.89–0.93 × height in "
             "the library) and 'place' just under cap_seat — where it is gripped and set down. Saved as a Tube "
             "with its size [dx, dy, dz]: cap_seat is its slot (where its cap goes)."),
    "device": dict(
        label="Device / station", folder=None, base=None,
        words=r"scale|balance|shaker|vortex|mixer|stirrer|heater|decapper|capper|camera|inspection|reader|"
              r"scanner|printer|meter|pump|centrifuge|hotel|feeder|station",
        rule="A bench device stands on the fixture plate on 4 holes (hole_0..3 at ±25 mm, z 0 — the library's "
             "50 mm foot); 'place' is where a vessel goes on or in it, 'top' its top face. A camera or reader "
             "also has 'lens' at its optical centre, pointing where it looks."),
    "other": dict(
        label="Other part", folder=None, base=None, words=None,
        rule="'center' at the base centre, 'top' on the top face, 'place' where a thing set on it lands."),
}

SLOT = re.compile(r"^([A-Z]+)(\d+)$")


def row_name(r):
    """Row r's letters: A…Z, then AA, AB… (a 1536-well plate's 32 rows end at AF)."""
    s = ""
    r += 1
    while r:
        r, m = divmod(r - 1, 26)
        s = string.ascii_uppercase[m] + s
    return s


def row_index(name):
    n = 0
    for ch in name:
        n = n * 26 + ord(ch) - 64
    return n - 1


def detect(*texts):
    """The kind a part's id / file name / description names, else 'other'."""
    s = " ".join(str(t or "") for t in texts).lower()
    s = re.sub(r"[_\-./]+", " ", s)
    s = re.sub(r"(\d)\s*(ml|ul|µl)\b", r"\1\2", s)
    for k, v in KINDS.items():
        if v["words"] and re.search(rf"(?<![a-z])({v['words']})s?(?![a-z])", s):
            return k
    return "other"


def _r(v):
    return round(float(v), 1)


def _clusters(vals, tol):
    out = []
    for v in sorted(vals):
        if out and v - out[-1][-1] <= tol:
            out[-1].append(v)
        else:
            out.append([v])
    return [sum(c) / len(c) for c in out]


def fit_grid(holes):
    """The holes found from above as a rack grid — {rows, cols, first,
    pitch, floor, d, extra} — from the most common hole size; None when
    they don't line up into rows and columns."""
    holes = [h for h in holes or [] if h.get("d")]
    if len(holes) < 1:
        return None
    # the vessel holes: the most common diameter (mounting holes, drain
    # holes and the like are other sizes)
    groups = []
    for h in sorted(holes, key=lambda h: h["d"]):
        if groups and h["d"] <= groups[-1][0]["d"] * 1.15:
            groups[-1].append(h)
        else:
            groups.append([h])
    main = max(groups, key=len)
    d = sum(h["d"] for h in main) / len(main)
    tol = max(1.5, d * 0.3)
    xs, ys = _clusters([h["x"] for h in main], tol), _clusters([h["y"] for h in main], tol)
    cols, rows = len(xs), len(ys)
    if rows * cols < len(main) or rows * cols > len(main) * 1.25:
        return None
    px = (xs[-1] - xs[0]) / (cols - 1) if cols > 1 else 0
    py = (ys[-1] - ys[0]) / (rows - 1) if rows > 1 else 0
    # evenly spaced? (a grid, not a scatter)
    if any(abs(x - (xs[0] + i * px)) > tol for i, x in enumerate(xs)) or \
       any(abs(y - (ys[0] + i * py)) > tol for i, y in enumerate(ys)):
        return None
    floors = sorted(h["bottom"] for h in main if h.get("bottom") is not None)
    floor = floors[len(floors) // 2] if floors else 0
    px, py = _r(px), _r(py)      # centred on the holes, so the far corner lands too
    x0 = (xs[0] + xs[-1]) / 2 - (cols - 1) * px / 2
    y0 = (ys[0] + ys[-1]) / 2 - (rows - 1) * py / 2
    return {"rows": rows, "cols": cols, "first": [_r(x0), _r(y0), _r(floor)], "pitch": [px, py],
            "floor": _r(floor), "d": _r(d), "extra": len(holes) - len(main)}


def grid_anchors(g, rz=45):
    x0, y0, z0 = g["first"]
    return {f"{row_name(r)}{c + 1}": [_r(x0 + c * g["pitch"][0]), _r(y0 + r * g["pitch"][1]), _r(z0), 0, 0, rz]
            for r in range(g["rows"]) for c in range(g["cols"])}


# bna's amber cap: 14 mm tall, 26 mm across, screwed on 1200° at 3.33 mm a turn
BNA_CAP = {"height": 14.0, "diameter": 26.0, "twist": 1200.0, "pitch": 3.33}
CAP_RISE_PER_HEIGHT = BNA_CAP["twist"] / 360.0 * BNA_CAP["pitch"] / BNA_CAP["height"]   # ≈ 0.79


def cap_settings(height, diameter):
    """bna's amber cap scaled to this one."""
    h = float(height or BNA_CAP["height"])
    d = float(diameter or BNA_CAP["diameter"])
    rise = round(CAP_RISE_PER_HEIGHT * h, 1)
    pitch = round(BNA_CAP["pitch"] * d / BNA_CAP["diameter"], 2)
    return {"cap_type": "screw", "twist": round(360 * rise / pitch), "pitch": pitch}


FOOT = {"hole_0": [25, 25, 0, 0, 0, 0], "hole_1": [-25, 25, 0, 0, 0, 0],
        "hole_2": [-25, -25, 0, 0, 0, 0], "hole_3": [25, -25, 0, 0, 0, 0]}


def template(kind, dims=None, top=None, holes=None, xy=None, pegs=None):
    """{kind, anchors, settings, notes} — the kind's anchors for this model.
    ``xy`` is the model's footprint centre when its origin isn't there: the
    anchors taken from the outline (top, place, the foot…) move with it;
    the ones from the holes are where the holes are."""
    dims = [float(v) for v in (dims or [0, 0, 0])]
    H = _r(top if top is not None else dims[2])
    a = {"center": [0, 0, 0, 0, 0, 0]}
    settings, notes = {}, []
    if kind == "tube":
        seat = _r(H * 0.91)
        a.update(top=[0, 0, H, 0, 0, 0], place=[0, 0, _r(seat - (1 if H > 50 else 0.5)), 0, 0, 0],
                 cap_seat=[0, 0, seat, 0, 0, 0])
        settings["size"] = [_r(dims[0]), _r(dims[1]), H]
        notes.append(f"cap_seat at 0.91 × {H:g} mm = {seat:g} mm — move it to where the cap's base really sits")
    elif kind == "cap":
        a["top"] = [0, 0, H, 0, 0, 0]
        settings = cap_settings(H, max(dims[0], dims[1]) or None)
        notes.append(f"screw cap {settings['twist']}° at {settings['pitch']} mm a turn (bna's amber cap scaled) "
                     "— set your cap's own if you know them")
    elif kind in ("rack", "capholder"):
        # slots from the holes (a rack's pockets) — or, when they don't make a
        # grid, from raised pegs (a cap holder's pins: the cap sits over the pin,
        # its rim on the surface the pins stand on, so the slot is at that surface)
        # the bigger grid wins: a cap holder's 96 pegs, not its 2 mounting holes
        fits = [(gg, nm) for gg, nm in ((fit_grid(holes), "holes"), (fit_grid(pegs), "pegs")) if gg]
        g, on = max(fits, key=lambda f: f[0]["rows"] * f[0]["cols"]) if fits else (None, "holes")
        floor = g["floor"] if g else 0
        a.update(place=[0, 0, floor, 0, 0, 0], top=[0, 0, H, 0, 0, 0])
        if g:
            a.update(grid_anchors(g))
            where = f"floor z {floor:g}" if on == "holes" else f"at the surface the pegs stand on, z {floor:g}"
            notes.append(f"{g['rows'] * g['cols']} slots A1–{row_name(g['rows'] - 1)}{g['cols']} from the {on} found "
                         f"(Ø {g['d']:g} mm, {g['pitch'][0]:g} × {g['pitch'][1]:g} mm pitch, {where})")
            if g["extra"]:
                notes.append(f"{g['extra']} other {on[:-1]}(s) of another size left out — say if they are slots too")
        else:
            notes.append("no regular grid of holes or pegs found from above — describe the slots to the assistant")
    elif kind == "adapter":
        a.update(place=[0, 0, H, 0, 0, 0], top=[0, 0, H, 0, 0, 0])
        mount = [h for h in holes or [] if h.get("d", 99) <= 9 and (h.get("bottom") or 0) <= 1.5]
        for i, h in enumerate(mount[:8]):
            a[f"hole_{i}"] = [_r(h["x"]), _r(h["y"]), 0, 0, 0, 0]
        notes.append(f"{len(mount[:8])} mounting hole(s) found" if mount else
                     "no through mounting holes found from above — add hole_0… where the screws go")
        if mount:
            # calibration points on fixture-plate holes: the 25 mm grid from hole_0, the
            # widest rectangle of it inside the footprint
            hx, hy = a["hole_0"][0], a["hole_0"][1]
            gx = [hx + 25 * k for k in range(-20, 21) if abs(hx + 25 * k) <= dims[0] / 2 - 2]
            gy = [hy + 25 * k for k in range(-20, 21) if abs(hy + 25 * k) <= dims[1] / 2 - 2]
            if gx and gy:
                for i, (x, y) in enumerate([(max(gx), max(gy)), (min(gx), max(gy)), (min(gx), min(gy)), (max(gx), min(gy))]):
                    a[f"clb_{i}"] = [_r(x), _r(y), 0, 0, 0, 0]
                notes.append("calibration points clb_0–3 on the plate's 25 mm grid from hole_0, at the corners "
                             "inside the footprint")
    elif kind == "tool_rack":
        a.update(place=[0, 46, _r(H - 2), 127.28, -127.28, 0], **FOOT, clb_0=[0, 46, _r(H - 7), 0, 0, -45])
        notes.append("the dock pose follows the library's tool racks — check it against the tool's flange")
    elif kind == "device":
        a.update(place=[0, 0, H, 0, 0, 0], top=[0, 0, H, 0, 0, 0], **FOOT)
        notes.append("the 50 mm foot (hole_0..3) — move the holes if this device mounts differently")
    else:
        a.update(top=[0, 0, H, 0, 0, 0], place=[0, 0, H, 0, 0, 0])
    cx, cy = (float(v) for v in (list(xy or []) + [0, 0])[:2])
    if cx or cy:
        found = {f"hole_{i}" for i in range(8)} if kind == "adapter" else set()
        for n, v in a.items():
            if n != "center" and not SLOT.match(n) and n not in found:
                a[n] = [_r(v[0] + cx), _r(v[1] + cy), *v[2:]]
    return {"kind": kind, "anchors": a, "settings": settings, "notes": notes}


@functools.lru_cache(maxsize=None)
def _library(folder):
    """Library parts of a folder with their anchors (slot grids summarised)."""
    import gui.scene_builder.server as sb
    out = []
    for t, f in sorted(sb.COMPONENT_MAP.items()):
        if f"/components/{folder}/" not in str(f).replace("\\", "/"):
            continue
        try:
            bp = sb.instantiate_component_blueprint(t, {})
        except Exception:
            continue
        sol = (bp.get("solids") or [{}])[0]
        anc = sol.get("anchors") or {}
        slots = [k for k in anc if SLOT.match(k)]
        keep = {k: [_r(x) for x in list(v)[:6]] for k, v in anc.items() if k not in slots}
        for k in ("A1", "A2", "B1"):
            if k in anc:
                keep[k] = [_r(x) for x in list(anc[k])[:6]]
        out.append({"type": t, "anchors": keep, "slots": len(slots)})
    return out


def examples(kind, n=2):
    folder = KINDS.get(kind, {}).get("folder")
    return _library(folder)[:n] if folder else []


def guidance(kind):
    """What the assistant is told about the kind: its rule and library examples."""
    k = KINDS.get(kind) or KINDS["other"]
    ex = examples(kind)
    s = f"This part is a {k['label'].lower()} ({kind}). Workspace's convention: {k['rule']}"
    if ex:
        s += "\nLibrary examples (anchors; slot grids shown by A1, A2, B1 and their count):\n" + \
             "\n".join(f"- {e['type']}: {e['anchors']}" + (f" ({e['slots']} slots)" if e["slots"] else "") for e in ex)
    return s


def kinds():
    return [{"key": k, "label": v["label"]} for k, v in KINDS.items()]


# ── saving: the kind's base class ─────────────────────────────────────────

def slot_grid(anchors):
    """A1… slot anchors as a Rack grid {offset, pitch, rows, cols, rvec_safe
    [, transpose]} when they make a full regular grid; else None. Rows run
    along y and columns along x — or, ``transpose`` (Workspace's Rack flag, as
    the library's vortex tops), rows along x and columns along y."""
    pos = {}
    for k, v in anchors.items():
        m = SLOT.match(k)
        if m:
            pos[(row_index(m.group(1)), int(m.group(2)) - 1)] = v
    if not pos:
        return None
    R = max(r for r, _ in pos) + 1
    C = max(c for _, c in pos) + 1
    if len(pos) != R * C:
        return None
    a1 = pos[(0, 0)]
    rv = list(a1[3:6])
    for transpose in (False, True):
        # the axis each index steps along: columns along x (rows along y), or the other way round
        ic, ir = (1, 0) if transpose else (0, 1)
        pc = (pos[(0, C - 1)][ic] - a1[ic]) / (C - 1) if C > 1 else 0
        pr = (pos[(R - 1, 0)][ir] - a1[ir]) / (R - 1) if R > 1 else 0
        if all(abs(v[ic] - (a1[ic] + c * pc)) <= 0.2 and abs(v[ir] - (a1[ir] + r * pr)) <= 0.2
               and abs(v[2] - a1[2]) <= 0.2 and all(abs(x - y) <= 0.01 for x, y in zip(v[3:6], rv))
               for (r, c), v in pos.items()):
            px, py = (pr, pc) if transpose else (pc, pr)
            g = {"offset": [round(a1[0], 3), round(a1[1], 3), round(a1[2], 3)], "pitch": [round(px, 3), round(py, 3), 0],
                 "rows": [row_name(r) for r in range(R)], "cols": list(range(1, C + 1)), "rvec_safe": rv}
            if transpose:
                g["transpose"] = True
            return g
    return None
