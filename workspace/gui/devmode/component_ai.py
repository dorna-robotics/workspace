"""The component editor's assistant: describe a part, get its anchors and
collision boxes — the editor applies them and the user can still edit by hand.

One small model call with one tool (``set_part``). What the model knows of
the part is what the editor measured on the mesh (compbuilder.js, a height map
seen from above): its size, the surface levels, its holes (centre, diameter,
floor) and its raised pegs (centre, diameter, top, the surface under them),
with the grid each forms already fitted (component_kinds.fit_grid). A grid of slots comes back as a ``grid`` the
server expands into named anchors (A1…), the way Workspace's racks name
them, so "a place anchor at the bottom of each of the 24 holes" doesn't
mean the model typing 24 poses.

The part's kind (component_kinds.py — tube, cap, rack, cap holder, adapter,
tool rack, device) brings Workspace's convention for it and real library
examples into the prompt, so the model starts from what that kind of part
always has and adjusts to this one.
"""
from __future__ import annotations

import asyncio
import json

from gui.ai import agent
from . import component_kinds as CK
from . import mesh_measure as MM
from .parts import PartError

MAX_TURNS = 12                 # measuring calls + set_part, per message
TOOL_TEXT_MAX = 24000          # a measurement's JSON, at most (long lists are cut)

SYSTEM = """You set up a lab part for Dorna Workspace: its anchors and collision boxes.

Frame: millimetres, Z up, origin at the part's base centre. An anchor is
[x, y, z, rx, ry, rz] (degrees). Workspace's conventions:
- "center" [0,0,0,0,0,0] always exists; "top" sits on the top face; "place"
  is where a thing set on the part lands.
- Slots in a grid are named row letter(s) + column number (after row Z come
  AA, AB…): A1 is the −x, −y corner, columns run along +x, rows (A, B, …) along +y. A slot anchor sits
  at the slot's FLOOR (the hole's bottom), rz 45 like Workspace's racks unless
  the user says otherwise.
- Mounting holes for fixture plates are hole_0, hole_1, … at z 0.
- Collision boxes: [x, y, z] centre + [lx, ly, lz] size. One box around the
  whole part is the safe default; add or split boxes only when the shape
  needs it (a tall post on a low base).

THE PART IS MEASURED FOR YOU. The editor measures the 3D model itself and
gives you the result as "measured" (never say you can't measure the model —
this IS its measurement): its size; "levels", the surface heights seen from
above with the share of the footprint each covers (the first is the main
surface); "holes", recessed pockets (centre, diameter, floor z, through or
not); "pegs", raised pins / protrusions (centre, diameter, top z, and the
surface z they stand on); and "grid_from_holes" / "grid_from_pegs", the rows,
columns, A1 centre and pitch those features form, already fitted. Positions
are exact to about one map cell ("resolution", mm).
- Slots in a rack are at its holes' floors. A cap holder holds caps either in
  pockets (holes: slot at the pocket floor) or over pegs (the cap's rim rests
  on the surface the pegs stand on: slot at that surface z, centred on the
  peg). Use the fitted grid — pass it as a set_part grid (rows, cols, first,
  pitch) — and only adjust z if the user says where the cap sits.
- When the user's count differs from what was measured, use the measured grid
  and say what you found (e.g. "96 pegs, 8 × 12 at 18 mm"). Only when nothing
  was measured that fits, ask for the one number you need.
The part's kind comes with Workspace's convention for it and library examples
— follow them unless this part is different, and change the kind (set_part's
kind) if it was guessed wrong. Call set_part with your result and say in one
or two short sentences what you set."""

MEASURING = """MEASURING TOOLS: you can measure the model yourself — measure_overview,
measure_features (holes and pegs from any side: top, bottom, ±x, ±y),
measure_section (exact outline loops where a plane cuts it) and measure_probe
(every surface a line meets: floors, thicknesses, peg tops). Use them whenever
the summary above doesn't settle something: features on a side or underneath,
an unusual shape, an exact diameter or height, or the user asking you to
measure a particular thing. Never ask the user to measure the model and never
guess a position — measure it, then set_part. Say briefly what you measured."""

_FROM_SUMMARY = """The "measured" summary above IS the model's measurement, taken by
the editor from the 3D model itself: "pegs" (raised pins / protrusions — a cap
holder often has these instead of pockets) and "holes" each list every feature's
centre, diameter and heights, and "grid_from_pegs" / "grid_from_holes" give the
rows, columns, A1 centre and pitch already fitted. Place slots from them (a
set_part grid with that first and pitch). Never ask the user for the pitch, the
A1 position, the row / column count or a diameter, never ask them to measure
the model, and never say you can't measure it. Only if the summary has no
features that fit (nothing found from above), say what was found and ask for
the one thing that is missing."""

NO_MESH = {
    # the upload hasn't finished yet: the tools come with the next message
    "uploading": "(The model is still being prepared for your measuring tools; they will be there "
                 "on the next message.) " + _FROM_SUMMARY,
    # this server can't take the model for measuring (or it refused it): the summary is all there is
    "unavailable": "(Your measuring tools aren't available for this model, so work from the summary "
                   "alone — don't mention uploading or waiting.) " + _FROM_SUMMARY,
}

TOOL = {
    "name": "set_part",
    "description": "Set the part's anchors and collision boxes. Anchors not given are kept unless replace_anchors.",
    "input_schema": {
        "type": "object",
        "properties": {
            "anchors": {"type": "array", "items": {"type": "object", "properties": {
                "name": {"type": "string"},
                "pose": {"type": "array", "items": {"type": "number"}, "minItems": 6, "maxItems": 6}},
                "required": ["name", "pose"]}},
            "grids": {"type": "array", "description": "slots named row letter(s) + column (A1 at −x, −y; columns +x, rows +y)",
                      "items": {"type": "object", "properties": {
                          "rows": {"type": "integer"}, "cols": {"type": "integer"},
                          "first": {"type": "array", "items": {"type": "number"}, "description": "A1's [x, y, z]"},
                          "pitch": {"type": "array", "items": {"type": "number"}, "description": "[along x between columns, along y between rows]"},
                          "rz": {"type": "number"}, "prefix": {"type": "string", "description": "before the slot name, e.g. 'S' → SA1; usually empty"}},
                          "required": ["rows", "cols", "first", "pitch"]}},
            "collision_boxes": {"type": "array", "items": {"type": "object", "properties": {
                "center": {"type": "array", "items": {"type": "number"}}, "size": {"type": "array", "items": {"type": "number"}}},
                "required": ["center", "size"]}},
            "kind": {"type": "string", "enum": list(CK.KINDS), "description": "only to change the part's kind"},
            "settings": {"type": "object", "description": "kind settings: a cap's cap_type ('screw'/'snap'), twist (deg), pitch (mm a turn); a tube's size [dx, dy, dz]",
                         "properties": {"cap_type": {"type": "string"}, "twist": {"type": "number"},
                                        "pitch": {"type": "number"}, "size": {"type": "array", "items": {"type": "number"}}}},
            "replace_anchors": {"type": "boolean", "description": "drop the anchors not listed (center stays)"},
            "remove_anchors": {"type": "array", "items": {"type": "string"}},
        },
    },
}


def expand_grid(g):
    rows, cols = int(g["rows"]), int(g["cols"])
    if not (0 < rows and 0 < cols and rows * cols <= 4000):
        raise ValueError(f"a grid of {rows} × {cols} slots — at most 4000, at least 1 × 1")
    x0, y0, z0 = (list(g["first"]) + [0, 0, 0])[:3]
    dx, dy = (list(g["pitch"]) + [0, 0])[:2]
    rz, pre = float(g.get("rz", 45)), str(g.get("prefix") or "")
    return [{"name": f"{pre}{CK.row_name(r)}{c + 1}", "pose": [round(x0 + c * dx, 2), round(y0 + r * dy, 2), round(z0, 2), 0, 0, rz]}
            for r in range(rows) for c in range(cols)]


def apply(part, anchors, boxes):
    """The editor's anchors / boxes after a set_part — what the page shows."""
    out = {a["name"]: list(a["pose"]) for a in anchors}
    if part.get("replace_anchors"):
        out = {k: v for k, v in out.items() if k == "center"}
    for n in part.get("remove_anchors") or []:
        if n != "center":
            out.pop(n, None)
    for g in part.get("grids") or []:
        for a in expand_grid(g):
            out[a["name"]] = a["pose"]
    def nums(v, n, what):
        v = [float(x) for x in v]
        if len(v) != n:
            raise ValueError(f"{what} needs {n} numbers")
        return v
    for a in part.get("anchors") or []:
        out[str(a["name"])] = nums(a["pose"], 6, f"anchor {a['name']}")
    out.setdefault("center", [0, 0, 0, 0, 0, 0])
    new_boxes = [{"center": nums(b["center"], 3, "a box centre"), "size": nums(b["size"], 3, "a box size")}
                 for b in part["collision_boxes"]] if part.get("collision_boxes") else boxes
    return [{"name": k, "pose": v} for k, v in out.items()], new_boxes


async def design(data):
    """{message, history, dims, top, holes, pegs, levels, mesh_id, anchors, boxes,
    description, kind, settings} → {reply, anchors, boxes, kind, settings, history}.
    With a mesh_id (the editor uploaded the model: mesh_measure.store) the model
    can measure it itself, in a loop of tool calls, before set_part."""
    anchors = data.get("anchors") or []
    boxes = data.get("boxes") or []
    kind = data.get("kind") if data.get("kind") in CK.KINDS else CK.detect(data.get("type"), data.get("description"))
    settings = dict(data.get("settings") or {})
    holes, pegs = data.get("holes") or [], data.get("pegs") or []
    measured = {"size_mm": data.get("dims"), "top_z": data.get("top"), "levels": data.get("levels") or [],
                "holes": holes, "pegs": pegs, "resolution": data.get("resolution"),
                "grid_from_holes": CK.fit_grid(holes), "grid_from_pegs": CK.fit_grid(pegs)}
    part = {"kind": kind, "settings": settings, "description": data.get("description") or "",
            "measured": measured, "anchors_now": anchors, "collision_boxes_now": boxes}
    history = list(data.get("history") or [])
    history.append({"role": "user", "content": f"The part as it stands:\n{json.dumps(part)}\n\n{data.get('message', '')}"})
    tris = None
    if data.get("mesh_id"):
        try:
            tris = MM.load(data["mesh_id"])
        except PartError:
            tris = None
    tools = (MM.TOOLS if tris is not None else []) + [TOOL]
    no_mesh = NO_MESH["uploading" if data.get("mesh_state") == "busy" else "unavailable"]
    system = SYSTEM + "\n\n" + (MEASURING if tris is not None else no_mesh) + "\n\n" + CK.guidance(kind)
    msgs = list(history)
    reply, used = [], None
    loop = asyncio.get_running_loop()
    for _ in range(MAX_TURNS):
        resp = await agent.client().messages.create(model=agent.MODEL, max_tokens=4000, system=system,
                                                    tools=tools, messages=msgs)
        reply = [b.text for b in resp.content if b.type == "text"]
        calls = [b for b in resp.content if b.type == "tool_use"]
        if not calls:
            break
        msgs.append({"role": "assistant", "content": resp.content})
        results = []
        for c in calls:
            if c.name == "set_part":
                try:                                      # a malformed call goes back to the model, not a crash
                    anchors, boxes = apply(dict(c.input), anchors, boxes)
                except (KeyError, TypeError, ValueError, IndexError) as ex:
                    out = {"error": f"set_part not applied: {type(ex).__name__}: {ex}"}
                else:
                    used = dict(c.input)
                    if used.get("kind") in CK.KINDS:
                        kind = used["kind"]
                    settings.update({k: v for k, v in (used.get("settings") or {}).items() if v is not None})
                    out = {"ok": True, "anchors": len(anchors), "collision_boxes": len(boxes)}
            else:
                try:                                      # numpy work off the server's event loop
                    out = await loop.run_in_executor(None, MM.run, c.name, c.input, tris)
                except PartError as ex:
                    out = {"error": str(ex)}
                except Exception as ex:                   # a measurement that fails is reported, not fatal
                    out = {"error": f"{type(ex).__name__}: {ex}"}
            text = json.dumps(out)
            if len(text) > TOOL_TEXT_MAX:
                text = text[:TOOL_TEXT_MAX] + ' … (cut: ask for a narrower view, min_d / max_d, or fewer points)'
            results.append({"type": "tool_result", "tool_use_id": c.id, "content": text})
        msgs.append({"role": "user", "content": results})
        if resp.stop_reason != "tool_use":
            break
    # the history keeps plain text turns (the part state is re-sent each time)
    history[-1] = {"role": "user", "content": data.get("message", "")}
    history.append({"role": "assistant", "content": " ".join(reply).strip() or "Done."})
    return {"reply": " ".join(reply).strip() or ("Set." if used else ""), "anchors": anchors, "boxes": boxes,
            "kind": kind, "settings": settings, "changed": used is not None, "history": history[-12:]}
