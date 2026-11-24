"""Just enough glTF to turn a user's model into a project component's mesh:
read a .glb / .gltf (JSON + one binary buffer), measure its bounding box
from the POSITION accessors' min/max (required by the spec, so this works
on Draco-compressed files too, without decoding a triangle), wrap the
scene in one root node (scale / turn / move), and write a .glb.
"""
import base64
import json
import math
import struct
from pathlib import Path

MAGIC = 0x46546C67
CH_JSON, CH_BIN = 0x4E4F534A, 0x004E4942


class GltfError(Exception):
    pass


def load(path, sidecar=None):
    """(gltf json, bin bytes or None). ``sidecar`` resolves an external
    buffer uri of a .gltf (a function name -> bytes or None)."""
    raw = Path(path).read_bytes()
    if len(raw) >= 12 and struct.unpack_from("<I", raw, 0)[0] == MAGIC:
        _, _, total = struct.unpack_from("<III", raw, 0)
        off, js, binb = 12, None, None
        while off + 8 <= min(total, len(raw)):
            n, typ = struct.unpack_from("<II", raw, off)
            chunk = raw[off + 8: off + 8 + n]
            if typ == CH_JSON:
                js = json.loads(chunk.decode("utf-8"))
            elif typ == CH_BIN and binb is None:
                binb = bytes(chunk)
            off += 8 + n
        if js is None:
            raise GltfError("a .glb without its JSON chunk")
        return js, binb
    try:
        js = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise GltfError("neither a binary .glb nor a JSON .gltf")
    bufs = js.get("buffers") or []
    if not bufs:
        return js, None
    blobs = []
    for b in bufs:
        uri = b.get("uri")
        if uri is None:
            raise GltfError("a .gltf buffer with no uri")
        if uri.startswith("data:"):
            blobs.append(base64.b64decode(uri.split(",", 1)[1]))
        else:
            data = sidecar(uri) if sidecar else None
            if data is None:
                raise GltfError(f"the .gltf needs its buffer file {uri!r} — upload it too, or export a .glb")
            blobs.append(data)
    # one buffer: concatenate (4-byte aligned) and move the views along
    starts, joined = [], b""
    for blob in blobs:
        joined += b"\0" * ((-len(joined)) % 4)
        starts.append(len(joined))
        joined += blob
    for v in js.get("bufferViews") or []:
        i = v.get("buffer", 0)
        v["byteOffset"] = v.get("byteOffset", 0) + starts[i]
        v["buffer"] = 0
    js["buffers"] = [{"byteLength": len(joined)}]
    return js, joined


def save_glb(js, binb, path):
    js = dict(js)
    if binb is not None:
        js["buffers"] = [{"byteLength": len(binb)}] + [b for b in (js.get("buffers") or [])[1:]]
    jb = json.dumps(js, separators=(",", ":")).encode("utf-8")
    jb += b" " * ((-len(jb)) % 4)
    out = struct.pack("<II", len(jb), CH_JSON) + jb
    if binb is not None:
        bb = binb + b"\0" * ((-len(binb)) % 4)
        out += struct.pack("<II", len(bb), CH_BIN) + bb
    Path(path).write_bytes(struct.pack("<III", MAGIC, 2, 12 + len(out)) + out)


# ── transforms ───────────────────────────────────────────────────────

def _mul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


def _node_matrix(n):
    if "matrix" in n:
        m = n["matrix"]                          # column-major
        return [[m[c * 4 + r] for c in range(4)] for r in range(4)]
    tx, ty, tz = n.get("translation", [0, 0, 0])
    x, y, z, w = n.get("rotation", [0, 0, 0, 1])
    sx, sy, sz = n.get("scale", [1, 1, 1])
    r = [[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
         [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
         [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]]
    return [[r[0][0] * sx, r[0][1] * sy, r[0][2] * sz, tx],
            [r[1][0] * sx, r[1][1] * sy, r[1][2] * sz, ty],
            [r[2][0] * sx, r[2][1] * sy, r[2][2] * sz, tz],
            [0, 0, 0, 1]]


_I = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]


def bbox(js):
    """[[xmin, ymin, zmin], [xmax, ymax, zmax]] of the default scene, in
    the file's own units, or None when no mesh says its extent."""
    nodes, meshes, acc = js.get("nodes") or [], js.get("meshes") or [], js.get("accessors") or []
    scenes = js.get("scenes") or []
    roots = (scenes[js.get("scene", 0)].get("nodes") if scenes else None) or list(range(len(nodes)))
    lo, hi = [math.inf] * 3, [-math.inf] * 3

    def walk(i, parent, depth=0):
        if depth > 64 or i >= len(nodes):
            return
        n = nodes[i]
        m = _mul(parent, _node_matrix(n))
        if "mesh" in n and n["mesh"] < len(meshes):
            for prim in meshes[n["mesh"]].get("primitives") or []:
                a = (prim.get("attributes") or {}).get("POSITION")
                if a is None or a >= len(acc) or "min" not in acc[a] or "max" not in acc[a]:
                    continue
                mn, mx = acc[a]["min"], acc[a]["max"]
                for cx in (mn[0], mx[0]):
                    for cy in (mn[1], mx[1]):
                        for cz in (mn[2], mx[2]):
                            p = [m[r][0] * cx + m[r][1] * cy + m[r][2] * cz + m[r][3] for r in range(3)]
                            for k in range(3):
                                lo[k] = min(lo[k], p[k])
                                hi[k] = max(hi[k], p[k])
        for c in n.get("children") or []:
            walk(c, m, depth + 1)

    for r in roots:
        walk(r, _I)
    if lo[0] == math.inf:
        return None
    return [lo, hi]


def wrap(js, scale=1.0, y_up=False, offset=(0.0, 0.0, 0.0)):
    """Put the default scene's roots under one new node: first scaled
    (``scale``), turned Y-up → Z-up when ``y_up``, then moved by
    ``offset`` (in the result's units)."""
    nodes = js.setdefault("nodes", [])
    scenes = js.setdefault("scenes", [{"nodes": list(range(len(nodes)))}])
    si = js.get("scene", 0)
    roots = scenes[si].get("nodes") or []
    s = math.sqrt(0.5)
    node = {"name": "workspace_root", "children": list(roots), "translation": [float(o) for o in offset],
            "scale": [scale] * 3}
    if y_up:
        node["rotation"] = [s, 0.0, 0.0, s]      # +90° about X: +Y → +Z
    nodes.append(node)
    scenes[si]["nodes"] = [len(nodes) - 1]
    js["scene"] = si
    return js
