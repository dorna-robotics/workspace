"""Measuring a part's 3D model — the component assistant's tools (component_ai.py).

The component editor sends the model the way it shows it: a triangle soup in
the part's frame (millimetres, Z up, after the file's units and up axis are
applied — the frame anchors are written in). It's kept on this unit under a
content hash, and the assistant measures it with:

    overview()                     size, triangle count, the surface levels seen from each side
    section(axis, at)              the outline loops where a plane cuts the part: centre, size,
                                   area, circle fit, hole or material, grids of like holes
    features(view, …)              holes and pegs (protrusions) seen from one side, the
                                   surface levels from that side, the grids they form
    probe(points, view)            every surface a straight line meets, through the part
                                   (exact: the line against each triangle)

All in plain numpy / scipy (no rtree, no shapely — the server has neither).
"""
from __future__ import annotations

import base64
import hashlib
import re
from pathlib import Path

import numpy as np

from gui.data_home import data_home
from .parts import PartError

DIR = Path(data_home()) / "component_meshes"
MAX_TRIS = 2_000_000
KEEP = 16                      # meshes kept (the newest)

# a view: looking at the part from that side. u, v span the picture, w points at the viewer
# (the height seen); each as (part axis index, sign).
VIEWS = {
    "top":    ((0, 1), (1, 1), (2, 1)),
    "bottom": ((0, 1), (1, -1), (2, -1)),
    "+x":     ((1, 1), (2, 1), (0, 1)),
    "-x":     ((1, -1), (2, 1), (0, -1)),
    "+y":     ((0, -1), (2, 1), (1, 1)),
    "-y":     ((0, 1), (2, 1), (1, -1)),
}
AX = "xyz"


def _r(v, n=2):
    return round(float(v), n)


# ── storing the editor's mesh ───────────────────────────────────────────
def store(b64: str) -> dict:
    raw = base64.b64decode(str(b64).split(",")[-1])
    if len(raw) % 36:
        raise PartError("the mesh isn't whole triangles")
    tris = np.frombuffer(raw, dtype="<f4").reshape(-1, 3, 3)
    if not len(tris):
        raise PartError("the mesh is empty")
    if len(tris) > MAX_TRIS:
        raise PartError(f"the model has {len(tris):,} triangles — over {MAX_TRIS:,}; simplify it to measure it")
    if not np.isfinite(tris).all():
        raise PartError("the mesh has invalid coordinates")
    mid = hashlib.sha1(raw).hexdigest()[:20]
    DIR.mkdir(parents=True, exist_ok=True)
    path = DIR / f"{mid}.npy"
    if not path.is_file():
        np.save(path, tris)
    for old in sorted(DIR.glob("*.npy"), key=lambda p: p.stat().st_mtime, reverse=True)[KEEP:]:
        old.unlink(missing_ok=True)
    return {"id": mid, "triangles": int(len(tris))}


def load(mid: str) -> np.ndarray:
    if not re.fullmatch(r"[0-9a-f]{20}", str(mid or "")):
        raise PartError("no measured model — upload the part's model in the editor first")
    path = DIR / f"{mid}.npy"
    if not path.is_file():
        raise PartError("the measured model expired — reload the part in the editor")
    path.touch()
    return np.load(path).astype(np.float64)


# ── geometry helpers ────────────────────────────────────────────────────
def _view(tris, view):
    """The triangles in a view's frame: (u, v, w) with w towards the viewer."""
    if view not in VIEWS:
        raise PartError(f"view is one of {', '.join(VIEWS)}")
    out = np.empty_like(tris)
    for k, (a, s) in enumerate(VIEWS[view]):
        out[..., k] = tris[..., a] * s
    return out


def _to_part(view, u, v, w):
    """A point given in a view's frame, back in the part's frame."""
    p = [0.0, 0.0, 0.0]
    for val, (a, s) in zip((u, v, w), VIEWS[view]):
        p[a] = val * s
    return [_r(c) for c in p]


def _axes_note(view):
    names = [("+" if s > 0 else "−") + AX[a] for a, s in VIEWS[view]]
    return f"seen from {view}: u = part {names[0]}, v = part {names[1]}, height = part {names[2]}"


def _sample(tris, n):
    """n points on the surface, spread by area (deterministic)."""
    e1, e2 = tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0]
    area = 0.5 * np.linalg.norm(np.cross(e1, e2), axis=1)
    tot = area.sum()
    if tot <= 0:
        return tris.reshape(-1, 3)
    rng = np.random.default_rng(7)
    idx = rng.choice(len(tris), size=n, p=area / tot)
    a, b = rng.random(n), rng.random(n)
    flip = a + b > 1
    a[flip], b[flip] = 1 - a[flip], 1 - b[flip]
    return tris[idx, 0] + a[:, None] * e1[idx] + b[:, None] * e2[idx]


def height_map(tris, view, cell=None, max_cells=360):
    """What's seen from a side: the nearest surface's height per grid cell
    (NaN where the line of sight misses the part). From points sampled on
    the surface, ~12 per cell, so flat faces read exact and slopes close."""
    t = _view(tris, view)
    lo, hi = t.reshape(-1, 3).min(0), t.reshape(-1, 3).max(0)
    span = np.maximum(hi - lo, 1e-6)
    if cell is None:
        cell = max(span[0], span[1]) / max_cells
    cell = max(float(cell), 0.05)
    nu, nv = int(np.ceil(span[0] / cell)) + 1, int(np.ceil(span[1] / cell)) + 1
    e1, e2 = t[:, 1] - t[:, 0], t[:, 2] - t[:, 0]
    area = 0.5 * np.linalg.norm(np.cross(e1, e2), axis=1).sum()
    n = int(min(3_000_000, max(200_000, 14 * area / (cell * cell))))
    p = _sample(t, n)
    iu = np.clip(((p[:, 0] - lo[0]) / cell).astype(int), 0, nu - 1)
    iv = np.clip(((p[:, 1] - lo[1]) / cell).astype(int), 0, nv - 1)
    hm = np.full(nu * nv, -np.inf)
    np.maximum.at(hm, iv * nu + iu, p[:, 2])
    hm = hm.reshape(nv, nu)
    hm[np.isinf(hm)] = np.nan
    return hm, lo, cell


def _levels(hm, step=0.25, top=5):
    z = hm[~np.isnan(hm)]
    if not len(z):
        return []
    q = np.round(z / step) * step
    vals, cnt = np.unique(q, return_counts=True)
    order = np.argsort(-cnt)[:top]
    return [{"height": _r(vals[i]), "area_pct": _r(100 * cnt[i] / len(z), 1)} for i in order if 100 * cnt[i] / len(z) >= 1]


def _fit(points):
    """Rows / columns / pitch of like features (component_kinds.fit_grid)."""
    from . import component_kinds as CK
    g = CK.fit_grid([{"x": p[0], "y": p[1], "d": p[2], "bottom": p[3]} for p in points])
    if not g:
        return None
    return {"rows": g["rows"], "cols": g["cols"], "first_uv": g["first"][:2], "pitch_uv": g["pitch"],
            "d": g["d"], "count": g["rows"] * g["cols"], "other_size": g["extra"]}


# ── the tools ───────────────────────────────────────────────────────────
def overview(tris) -> dict:
    pts = tris.reshape(-1, 3)
    lo, hi = pts.min(0), pts.max(0)
    sides = {}
    for view in VIEWS:
        hm, _, _ = height_map(tris, view, max_cells=160)
        sides[view] = _levels(hm, top=4)
    return {"triangles": int(len(tris)), "bbox_min": [_r(v) for v in lo], "bbox_max": [_r(v) for v in hi],
            "size": [_r(v) for v in hi - lo],
            "levels_by_side": sides,
            "note": "frame: mm, Z up, the part's frame (anchors use it). A level is a surface height seen from that "
                    "side (along the side's axis, signed as in that axis) and the share of the view it covers."}


def features(tris, view="top", min_step=None, min_d=None, max_d=None, cell=None) -> dict:
    hm, lo, cell = height_map(tris, view, cell=cell)
    nv, nu = hm.shape
    lev = _levels(hm)
    if not lev:
        return {"view": view, "axes": _axes_note(view), "holes": [], "pegs": [], "levels": []}
    surface = lev[0]["height"]
    finite = hm[~np.isnan(hm)]
    depth = float(finite.max() - finite.min()) if len(finite) else 0.0
    step = float(min_step) if min_step else max(0.4, 0.04 * depth)
    from scipy import ndimage
    solid = ~np.isnan(hm)
    filled = ndimage.binary_fill_holes(solid)                      # the outline, through-holes inside it
    low = (np.nan_to_num(hm, nan=-1e9) < surface - step) & filled   # recessed, or a through-hole inside the outline
    high = np.nan_to_num(hm, nan=-1e9) > surface + step

    def comps(mask, kind):
        lab, k = ndimage.label(mask)
        if not k:
            return []
        out = []
        edge = set(np.unique(np.r_[lab[0], lab[-1], lab[:, 0], lab[:, -1]])) - {0}
        sizes = ndimage.sum(np.ones_like(lab), lab, index=range(1, k + 1))
        for i in range(1, k + 1):
            if i in edge or sizes[i - 1] < 3 or sizes[i - 1] > 0.25 * nu * nv:
                continue
            vv, uu = np.nonzero(lab == i)
            u0, v0 = lo[0] + (uu.mean() + 0.5) * cell, lo[1] + (vv.mean() + 0.5) * cell
            # a cell reads high if any of it is on a peg, low only if all of it is in a hole:
            # pegs count the cells along their edge, holes miss them — a cell either way
            d = max(cell, 2 * np.sqrt(sizes[i - 1] * cell * cell / np.pi) + (-cell if kind == "peg" else cell))
            if (min_d and d < float(min_d)) or (max_d and d > float(max_d)):
                continue
            hv = hm[vv, uu]
            fin = hv[~np.isnan(hv)]
            f = {"center_uv": [_r(u0), _r(v0)], "d": _r(d),
                 "size_uv": [_r((uu.max() - uu.min() + 1) * cell), _r((vv.max() - vv.min() + 1) * cell)]}
            if kind == "hole":
                through = len(fin) < 0.5 * len(hv)
                floor = float(fin.min()) if len(fin) else None
                f.update(floor=_r(floor) if floor is not None else None, through=bool(through),
                         depth=_r(surface - floor) if floor is not None else None,
                         center=_to_part(view, u0, v0, floor if floor is not None else surface))
            else:
                top = float(fin.max())
                f.update(top=_r(top), rises=_r(top - surface), stands_on=_r(surface), center=_to_part(view, u0, v0, top))
            out.append(f)
        out.sort(key=lambda f: (round(f["center_uv"][1] / max(cell * 2, 0.5)), f["center_uv"][0]))
        return out

    holes, pegs = comps(low, "hole"), comps(high, "peg")
    res = {"view": view, "axes": _axes_note(view), "cell_mm": _r(cell, 3), "main_surface": surface, "step_mm": _r(step),
           "levels": lev, "holes": holes[:300], "pegs": pegs[:300],
           "hole_grid": _fit([(h["center_uv"][0], h["center_uv"][1], h["d"], h["floor"] or 0) for h in holes]),
           "peg_grid": _fit([(p["center_uv"][0], p["center_uv"][1], p["d"], surface) for p in pegs])}
    if len(holes) > 300 or len(pegs) > 300:
        res["truncated"] = f"{len(holes)} holes, {len(pegs)} pegs — first 300 of each listed"
    return res


def _mesh(tris):
    import trimesh
    return trimesh.Trimesh(vertices=tris.reshape(-1, 3), faces=np.arange(len(tris) * 3).reshape(-1, 3), process=True)


def _inside(pt, poly):
    """Point in polygon (even-odd ray cast)."""
    x, y = pt
    xs, ys = poly[:, 0], poly[:, 1]
    xj, yj = np.roll(xs, 1), np.roll(ys, 1)
    cross = ((ys > y) != (yj > y)) & (x < (xj - xs) * (y - ys) / np.where(yj - ys == 0, 1e-12, yj - ys) + xs)
    return bool(np.count_nonzero(cross) % 2)


def section(tris, axis="z", at=0.0) -> dict:
    a = AX.index(str(axis).lower()) if str(axis).lower() in AX else None
    if a is None:
        raise PartError("axis is x, y or z")
    normal = np.zeros(3); normal[a] = 1
    origin = np.zeros(3); origin[a] = float(at)
    sec = _mesh(tris).section(plane_origin=origin, plane_normal=normal)
    plane = [i for i in range(3) if i != a]
    if sec is None:
        return {"axis": AX[a], "at": _r(at), "loops": [], "note": "the plane misses the part"}
    loops = []
    for L in sec.discrete:
        p = np.asarray(L)[:, plane]
        if len(p) < 3:
            continue
        x, y = p[:, 0], p[:, 1]
        area = 0.5 * abs(np.dot(x, np.roll(y, 1)) - np.dot(y, np.roll(x, 1)))
        c = p.mean(0)
        r = np.linalg.norm(p - c, axis=1)
        loops.append({"p": p, "center": c, "area": area, "r": r})
    loops.sort(key=lambda l: -l["area"])
    out = []
    for i, l in enumerate(loops):
        depth = sum(1 for j, o in enumerate(loops) if j != i and o["area"] > l["area"] and _inside(l["center"], o["p"]))
        round_ = float(l["r"].std() / l["r"].mean()) if l["r"].mean() > 0 else 1
        lo, hi = l["p"].min(0), l["p"].max(0)
        out.append({"center": [_r(v) for v in l["center"]], "size": [_r(v) for v in hi - lo], "area": _r(l["area"]),
                    "is_hole": depth % 2 == 1, "round": round_ < 0.05,
                    "d": _r(2 * l["r"].mean()) if round_ < 0.05 else None})
    holes = [o for o in out if o["is_hole"] and o["round"]]
    note = f"loops in the {AX[plane[0]]}{AX[plane[1]]} plane at {AX[a]} = {_r(at)}; center/size are ({AX[plane[0]]}, {AX[plane[1]]})"
    return {"axis": AX[a], "at": _r(at), "plane": AX[plane[0]] + AX[plane[1]], "loops": out[:200], "count": len(out),
            "round_hole_grid": _fit([(h["center"][0], h["center"][1], h["d"], 0) for h in holes]), "note": note}


def probe(tris, points, view="top") -> dict:
    """Every surface a line meets — from the side ``view`` (the line runs along
    that side's axis) at each (u, v) point given in that view's frame."""
    t = _view(tris, view)
    v0, e1, e2 = t[:, 0], t[:, 1] - t[:, 0], t[:, 2] - t[:, 0]
    out = []
    for pt in list(points or [])[:60]:
        u, v = float(pt[0]), float(pt[1])
        # the line (u, v, w) for every w: a vertical ray in the view frame — 2D barycentric test
        d = e1[:, 0] * e2[:, 1] - e1[:, 1] * e2[:, 0]
        ok = np.abs(d) > 1e-12
        du, dv = u - v0[:, 0], v - v0[:, 1]
        with np.errstate(divide="ignore", invalid="ignore"):
            b1 = (du * e2[:, 1] - dv * e2[:, 0]) / d
            b2 = (e1[:, 0] * dv - e1[:, 1] * du) / d
            hit = ok & (b1 >= -1e-9) & (b2 >= -1e-9) & (b1 + b2 <= 1 + 1e-9)
        w = v0[hit, 2] + b1[hit] * e1[hit, 2] + b2[hit] * e2[hit, 2]
        ws = sorted({_r(x) for x in w}, reverse=True)
        out.append({"uv": [_r(u), _r(v)], "surfaces": ws, "first": ws[0] if ws else None,
                    "thickness_pairs": [[ws[i], ws[i + 1]] for i in range(0, len(ws) - 1, 2)][:10]})
    return {"view": view, "axes": _axes_note(view), "probes": out,
            "note": "surfaces: every height (along the view's axis) where the line crosses the part's skin, nearest "
                    "first; consecutive pairs are usually material (enter, exit)."}


TOOLS = [
    {"name": "measure_overview",
     "description": "The model's size, bounding box, triangle count and the surface levels seen from each side (top, bottom, ±x, ±y). Start here.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "measure_features",
     "description": "Holes (recesses, through-holes) and pegs (protrusions, pins) seen from one side (diameters to about one cell — measure_section across a feature gives it exactly), with centres (in the view's u,v and in the part frame), diameters, floors/tops, the surface they're in or on, and the grid each set forms (rows, cols, A1 centre, pitch). Use another view for features on a side or the underside.",
     "input_schema": {"type": "object", "properties": {
         "view": {"type": "string", "enum": list(VIEWS)},
         "min_step": {"type": "number", "description": "how far (mm) a surface must be below/above the main surface to count (default: 4% of the depth, at least 0.4)"},
         "min_d": {"type": "number", "description": "ignore features narrower than this (mm)"},
         "max_d": {"type": "number", "description": "ignore features wider than this (mm)"},
         "cell": {"type": "number", "description": "grid cell in mm (default: the longest side / 360) — smaller to resolve small features"}},
         "required": ["view"]}},
    {"name": "measure_section",
     "description": "Cut the model with a plane (x, y or z = at) and get the outline loops there: centre, size, area, whether each is a hole in the material or material, round loops' diameters, and the grid round holes form. Exact (from the mesh).",
     "input_schema": {"type": "object", "properties": {
         "axis": {"type": "string", "enum": ["x", "y", "z"]}, "at": {"type": "number"}}, "required": ["axis", "at"]}},
    {"name": "measure_probe",
     "description": "Shoot straight lines through the model from one side at given points and get every surface each line meets (exact): e.g. the floor of a pocket, the thickness of a plate, the top of a peg. Points are (u, v) in that view's frame (top: u = x, v = y).",
     "input_schema": {"type": "object", "properties": {
         "view": {"type": "string", "enum": list(VIEWS)},
         "points": {"type": "array", "items": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2}, "maxItems": 60}},
         "required": ["points"]}},
]


def run(name, args, tris) -> dict:
    args = args or {}
    if name == "measure_overview":
        return overview(tris)
    if name == "measure_features":
        return features(tris, args.get("view", "top"), args.get("min_step"), args.get("min_d"), args.get("max_d"), args.get("cell"))
    if name == "measure_section":
        return section(tris, args.get("axis", "z"), args.get("at", 0))
    if name == "measure_probe":
        return probe(tris, args.get("points") or [], args.get("view", "top"))
    raise PartError(f"no measuring tool {name}")
