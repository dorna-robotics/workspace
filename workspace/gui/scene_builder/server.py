import os, json, time
import re, ast, sys, importlib.util
import weakref
import tornado.web
import tornado.ioloop
import socketio
from tornado import autoreload
import yaml
try:
    import numpy as np
except Exception:
    np = None

# --- Builder anchor capture (robust to dorna2 internal changes) ---
_SOLID_ANCHORS_WEAK = weakref.WeakKeyDictionary()
_SOLID_ANCHORS_BY_ID = {}  # fallback if solids are not weakref-able

def _cache_solid_anchors(obj, anchors):
    if not isinstance(anchors, dict) or not anchors:
        return
    try:
        _SOLID_ANCHORS_WEAK[obj] = anchors
        return
    except Exception:
        pass
    try:
        _SOLID_ANCHORS_BY_ID[id(obj)] = anchors
    except Exception:
        pass


# Builder must mirror simulation's component instantiation to obtain anchors and
# solids. Components are the source of truth.

# optional: pose conversion for internal solid local transforms
try:
    from dorna2.pose import T_to_xyzabc, xyzabc_to_T, inv_T
except Exception:
    T_to_xyzabc = None
    xyzabc_to_T = None
    inv_T = None

# Fallback minimal pose helpers if dorna2.pose doesn't expose them.
def _rodrigues_deg_to_R(rx, ry, rz):
    if np is None:
        return None
    ang = float((rx*rx + ry*ry + rz*rz) ** 0.5)
    if ang == 0.0:
        return np.eye(3)
    ax, ay, az = rx/ang, ry/ang, rz/ang
    th = ang * (np.pi/180.0)
    K = np.array([[0, -az, ay],
                  [az, 0, -ax],
                  [-ay, ax, 0]], dtype=float)
    I = np.eye(3)
    return I + np.sin(th)*K + (1-np.cos(th))*(K@K)


def _xyzabc_to_T_fallback(pose):
    if np is None:
        return None
    if not isinstance(pose, (list, tuple)) or len(pose) != 6:
        return None
    x,y,z,a,b,c = [float(v) for v in pose]
    R = _rodrigues_deg_to_R(a,b,c)
    if R is None:
        return None
    T = np.eye(4)
    T[:3,:3] = R
    T[:3,3] = [x,y,z]
    return T


def _inv_T_fallback(T):
    if np is None or T is None:
        return None
    R = T[:3,:3]
    t = T[:3,3]
    Ti = np.eye(4)
    Ti[:3,:3] = R.T
    Ti[:3,3] = -(R.T @ t)
    return Ti


def _xyzabc_to_T(pose):
    if xyzabc_to_T is not None:
        try:
            return xyzabc_to_T(pose)
        except Exception:
            pass
    return _xyzabc_to_T_fallback(pose)


def _inv_T(T):
    if inv_T is not None:
        try:
            return inv_T(T)
        except Exception:
            pass
    return _inv_T_fallback(T)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PARENT_DIR = os.path.dirname(os.path.dirname(BASE_DIR))  # workspace/ root (up from gui/scene_builder/)

WS_PKG_DIR = os.path.join(PARENT_DIR, "workspace")
COMPONENTS_DIR = os.path.join(WS_PKG_DIR, "components")
CAD_DIR = os.path.join(PARENT_DIR, "static", "CAD")
print(f"[builder] server.py loaded from: {__file__}")
print(f"[builder] sys.path[0:3]: {sys.path[0:3]}")
# Ensure project root is on sys.path so `import workspace` resolves to the package, not workspace/workspace.py
if PARENT_DIR not in sys.path:
    sys.path.insert(0, PARENT_DIR)
# --- Builder-only stubs for newer Core imports ---
# Newer versions of Core may import top-level modules that aren't present in the builder repo
# (e.g. `from camera import Camera`, `from path_planning import Planner`).
# When users "copy over the builder folder" into a fresh workspace checkout, we must still
# be able to instantiate Core without requiring any new files outside /builder.
from types import ModuleType

def _ensure_builder_stubs():
    # camera.py stub
    if "camera" not in sys.modules:
        cam_mod = ModuleType("camera")
        class Camera:  # minimal interface used by core; safe no-op
            def __init__(self, *a, **k):
                pass
        cam_mod.Camera = Camera
        sys.modules["camera"] = cam_mod

    # path_planning.py stub
    if "path_planning" not in sys.modules:
        pp_mod = ModuleType("path_planning")
        class Planner:  # minimal interface used by core; safe no-op
            def __init__(self, *a, **k):
                pass
            def update(self, *a, **k):
                # Core calls planner.update(...) during init.
                return None
            def check_collision(self, *a, **k):
                return []
        pp_mod.Planner = Planner
        sys.modules["path_planning"] = pp_mod

_ensure_builder_stubs()


# Patch dorna2 early (BEFORE importing workspace.components, which auto-imports all modules).
# This ensures any `from dorna2 import Solid` inside components gets the patched Solid.
_ORIG_DORNA = None
_ORIG_SOLID = None

def _patch_dorna2_once():
    global _ORIG_DORNA, _ORIG_SOLID
    try:
        import dorna2
    except Exception:
        return

    if _ORIG_DORNA is None:
        _ORIG_DORNA = getattr(dorna2, "Dorna", None)
    if _ORIG_SOLID is None:
        _ORIG_SOLID = getattr(dorna2, "Solid", None)

    class _DummyDorna:
        def __init__(self, *a, **k):
            pass
        def connect(self, *a, **k):
            return False
        def joint(self, *a, **k):
            return [0.0] * 8

    if getattr(dorna2, "Dorna", None) is not _DummyDorna:
        try:
            dorna2.Dorna = _DummyDorna
        except Exception:
            pass

    orig_solid = _ORIG_SOLID
    if orig_solid is None:
        return

    # If Solid is a Python class and subclassing works, use a subclass shim.
    try:
        class _BuilderSolid(orig_solid):
            def __init__(self, *a, **k):
                passed = k.get("anchors", None)
                passed_cb = k.get("collision_box", None)
                super().__init__(*a, **k)
                _cache_solid_anchors(self, passed)
                # Also mirror to a conventional attribute if allowed.
                try:
                    cur = getattr(self, "anchors", None)
                    if (not isinstance(cur, dict) or not cur) and isinstance(passed, dict):
                        setattr(self, "anchors", passed)
                except Exception:
                    pass
                # Store collision_box so Builder can read it back.
                try:
                    if passed_cb is not None and not hasattr(self, "collision_box"):
                        setattr(self, "collision_box", passed_cb)
                except Exception:
                    pass

        dorna2.Solid = _BuilderSolid
    except Exception:
        # Fallback: Solid might be non-subclassable (C-extension). Wrap it.
        def _SolidFactory(*a, **k):
            passed = k.get("anchors", None)
            passed_cb = k.get("collision_box", None)
            obj = orig_solid(*a, **k)
            _cache_solid_anchors(obj, passed)
            try:
                cur = getattr(obj, "anchors", None)
                if (not isinstance(cur, dict) or not cur) and isinstance(passed, dict):
                    setattr(obj, "anchors", passed)
            except Exception:
                pass
            try:
                if passed_cb is not None and not hasattr(obj, "collision_box"):
                    setattr(obj, "collision_box", passed_cb)
            except Exception:
                pass
            return obj
        dorna2.Solid = _SolidFactory

_patch_dorna2_once()

# Now that dorna2 is patched, importing components is safe.
from workspace.components import factory as comp_factory


REGISTER_RE = re.compile(r'@register\([\'"]([^\'"]+)[\'"]\)')

def scan_registered_components():
    out = {}
    for root, _, files in os.walk(COMPONENTS_DIR):
        for fn in files:
            if not fn.endswith(".py") or fn.startswith("_"):
                continue
            fp = os.path.join(root, fn)
            try:
                src = open(fp, "r", encoding="utf-8", errors="ignore").read()
            except Exception:
                continue
            for mm in REGISTER_RE.finditer(src):
                out[mm.group(1)] = fp
    return out

def ast_extract_cfg_get_options(src: str):
    try:
        tree = ast.parse(src)
    except Exception:
        return []
    opts = {}

    def _store_opt(key: str, default):
        """Store option with inferred kind/default."""
        if not isinstance(key, str) or not key:
            return
        # Don't expose the internal simulation toggle in Builder UI.
        if key == "simulation":
            return

        # infer kind
        boolish = key.startswith(("has_", "enable_", "use_", "is_")) or key.endswith(("_enabled", "_enable"))
        if isinstance(default, bool) or boolish:
            kind = "bool"
            if not isinstance(default, bool):
                default = False
        elif isinstance(default, int):
            kind = "int"
        elif isinstance(default, float):
            kind = "float"
        elif isinstance(default, (list, dict)) or default is None:
            kind = "json"
        else:
            kind = "text"

        # prefer existing entry (cfg.get might provide a better default)
        if key not in opts:
            opts[key] = {"name": key, "kind": kind, "default": default}

    # 1) Extract cfg.get("key", default)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not isinstance(fn, ast.Attribute) or fn.attr != "get":
            continue
        if not node.args:
            continue
        k0 = node.args[0]
        if not isinstance(k0, ast.Constant) or not isinstance(k0.value, str):
            continue
        key = k0.value
        default = None
        if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
            default = node.args[1].value
        _store_opt(key, default)

    # 2) Extract class DEFAULTS = dict(...) or DEFAULTS = {...}
    # Newer components (notably Core) use DEFAULTS + mergedeep.merge(prm, cfg)
    # instead of cfg.get(...). Builder still needs to surface has_* toggles.
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        # target named DEFAULTS
        if not any(isinstance(t, ast.Name) and t.id == "DEFAULTS" for t in node.targets):
            continue

        v = node.value
        # DEFAULTS = dict(a=1, b=True, ...)
        if isinstance(v, ast.Call) and isinstance(v.func, ast.Name) and v.func.id == "dict":
            for kw in v.keywords or []:
                if not kw.arg:
                    continue
                if isinstance(kw.value, ast.Constant):
                    _store_opt(kw.arg, kw.value.value)
                else:
                    # non-constant default; still expose boolish toggles
                    _store_opt(kw.arg, None)
        # DEFAULTS = {"a": 1, "b": True}
        elif isinstance(v, ast.Dict):
            for kk, vv in zip(v.keys or [], v.values or []):
                if isinstance(kk, ast.Constant) and isinstance(kk.value, str):
                    key = kk.value
                    if isinstance(vv, ast.Constant):
                        _store_opt(key, vv.value)
                    else:
                        _store_opt(key, None)

    return list(opts.values())

def load_module_from_path(fp: str, unique_name: str):
    spec = importlib.util.spec_from_file_location(unique_name, fp)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

def extract_anchors_from_instance(obj):
    anchors_by_solid = {}
    if hasattr(obj, "assembly") and isinstance(obj.assembly, dict):
        for solid_name, solid in obj.assembly.items():
            anchors = extract_solid_anchors(solid)
            if anchors:
                anchors_by_solid[solid_name] = anchors
    return anchors_by_solid


def _normalize_anchors(anchor_src):
    """Normalize anchors to a JSON-serializable dict[str, list[float]]."""
    if not isinstance(anchor_src, dict) or not anchor_src:
        return {}
    out = {}
    for k, v in anchor_src.items():
        if isinstance(v, (list, tuple)) and len(v) == 6:
            try:
                out[str(k)] = [float(v[i]) for i in range(6)]
            except Exception:
                continue
    return out


def extract_solid_anchors(solid):
    """Best-effort anchor extraction from a dorna2.Solid-like object.

    Priority (most reliable -> least):
      1) Anchors captured at Solid construction time by the Builder shim.
      2) Common attributes on Solid (anchors/anchor_dict/anchor).
      3) Common attributes on Solid.pose (if present).

    This is intentionally flexible so new components/new dorna2 versions keep working.
    """
    # 1) Builder-captured anchors (works even if dorna2 does not store them)
    try:
        val = _SOLID_ANCHORS_WEAK.get(solid)
    except Exception:
        val = None
    if val is None:
        try:
            val = _SOLID_ANCHORS_BY_ID.get(id(solid))
        except Exception:
            val = None
    norm = _normalize_anchors(val)
    if norm:
        return norm

    # 2) Captured by attribute (some shims may set this)
    for attr in ("_builder_anchors", "anchors", "anchor_dict", "anchor"):
        try:
            val = getattr(solid, attr, None)
        except Exception:
            val = None
        norm = _normalize_anchors(val)
        if norm:
            return norm

    # 3) Some implementations nest anchors under pose
    try:
        pose = getattr(solid, "pose", None)
    except Exception:
        pose = None
    if pose is not None:
        for attr in ("anchors", "anchor_dict", "anchor"):
            try:
                val = getattr(pose, attr, None)
            except Exception:
                val = None
            norm = _normalize_anchors(val)
            if norm:
                return norm

    return {}

def extract_anchors_from_module_globals(mod):
    anchors_by_solid = {}
    for k, v in mod.__dict__.items():
        if k.endswith("_anchors") and isinstance(v, dict) and v:
            solid = k[:-8]
            anchors_by_solid[solid] = v
    return anchors_by_solid


def _patch_dorna_for_builder():
    """Prevent components like core from attempting real robot connections."""
    try:
        import dorna2
    except Exception:
        return None

    # We patch both Dorna (to prevent real connections) and Solid (to reliably
    # capture anchors passed in at construction time, regardless of dorna2
    # internal attribute naming).
    orig_dorna = getattr(dorna2, "Dorna", None)
    orig_solid = getattr(dorna2, "Solid", None)

    class _DummyDorna:
        def __init__(self, *a, **k):
            pass

        def connect(self, *a, **k):
            return False

        def joint(self, *a, **k):
            return [0.0] * 8

    if orig_dorna is not None:
        dorna2.Dorna = _DummyDorna

    # --- Solid anchor capture shim ---
    # Some dorna2 versions do not expose anchors on `solid.anchors` (or use a
    # different internal structure). Builder must *always* be able to recover
    # the anchors dictionary that components pass to Solid(, anchors=).
    if orig_solid is not None:
        class _BuilderSolid(orig_solid):
            def __init__(self, *a, **k):
                passed_anchors = k.get("anchors", None)
                passed_cb = k.get("collision_box", None)
                super().__init__(*a, **k)
                # Persist the raw anchors as provided by the component.
                try:
                    self._builder_anchors = passed_anchors
                except Exception:
                    pass
                # Best-effort: if dorna2 doesn't keep them anywhere obvious,
                # also mirror to a conventional attribute.
                try:
                    cur = getattr(self, "anchors", None)
                    if (not isinstance(cur, dict) or not cur) and isinstance(passed_anchors, dict):
                        setattr(self, "anchors", passed_anchors)
                except Exception:
                    pass
                # Store collision_box so Builder can read it back.
                try:
                    if passed_cb is not None and not hasattr(self, "collision_box"):
                        setattr(self, "collision_box", passed_cb)
                except Exception:
                    pass

        dorna2.Solid = _BuilderSolid

    # Some components do: `from dorna2 import Dorna` at import time.
    # Patch any already-imported modules that captured the symbol.
    for mname, mod in list(sys.modules.items()):
        if not mname or not mod:
            continue
        if mname.startswith("workspace.components") and hasattr(mod, "Dorna"):
            try:
                setattr(mod, "Dorna", _DummyDorna)
            except Exception:
                pass

        # Some components do: `from dorna2 import Solid` at import time.
        # Patch any already-imported modules that captured the symbol.
        if mname.startswith("workspace.components") and hasattr(mod, "Solid") and orig_solid is not None:
            try:
                setattr(mod, "Solid", dorna2.Solid)
            except Exception:
                pass

    return (orig_dorna, orig_solid)


def _unpatch_dorna(orig):
    try:
        import dorna2
    except Exception:
        return
    orig_dorna, orig_solid = (orig or (None, None))
    if orig_dorna is not None:
        dorna2.Dorna = orig_dorna
    if orig_solid is not None:
        dorna2.Solid = orig_solid

    for mname, mod in list(sys.modules.items()):
        if not mname or not mod:
            continue
        if mname.startswith("workspace.components") and hasattr(mod, "Dorna"):
            try:
                setattr(mod, "Dorna", orig_dorna)
            except Exception:
                pass

        if mname.startswith("workspace.components") and hasattr(mod, "Solid") and orig_solid is not None:
            try:
                setattr(mod, "Solid", orig_solid)
            except Exception:
                pass


def _compute_world_Ts_for_solids(solids: dict):
    """Compute world transforms for a set of dorna2.Solid objects.

    Mirrors Workspace.compute_world_poses(), but scoped to an in-memory dict of
    solids (single component instantiation).

    Returns: dict solid_name -> 4x4 numpy array
    """
    if np is None:
        return {}

    # Roots are solids with no parent_solid
    roots = []
    for s in solids.values():
        try:
            if s.parent.get("parent_solid") is None:
                roots.append(s)
        except Exception:
            roots.append(s)

    world = {}
    stack = [(r, np.eye(4)) for r in roots]

    while stack:
        node, T_parent = stack.pop()
        try:
            T_local = node.local.get("T")
        except Exception:
            T_local = None
        if T_local is None:
            T_local = np.eye(4)

        try:
            T_world = T_parent @ T_local
        except Exception:
            T_world = np.array(T_parent)
        world[getattr(node, "name", str(id(node)))] = T_world

        # children structure: dict[key] -> list[{"child_solid": Solid, }]
        try:
            for child_list in getattr(node, "children", {}).values():
                for entry in child_list:
                    ch = entry.get("child_solid")
                    if ch is None:
                        continue
                    stack.append((ch, T_world))
        except Exception:
            pass

    return world


def instantiate_component_blueprint(type_name: str, options: dict, joints=None):
    """
    Create the component exactly like simulation does (factory.create_component)
    and return a blueprint that the Builder UI can render.

    Returns:
      {
        "solids": [
          {
            "solid": "tool_rack",
            "glb": "/static/CAD/tool_rack.glb",
            "pose": [x,y,z,a,b,c],
            "anchors": {}
          },
          
        ]
      }
    """
    cfg = {"type": type_name}
    if isinstance(options, dict):
        cfg.update(options)
    # Builder previews are GEOMETRY-ONLY: force simulation and blank every
    # connection field (ip / port / serial_number, top-level or inside
    # camera_cfg) so no component ever dials hardware or waits on a
    # timeout from a preview. Display truth = the scene; connection truth
    # stays untouched in the authored yaml.
    cfg["simulation"] = True
    for k in ("ip", "port", "serial_number"):
        cfg.pop(k, None)
    if isinstance(cfg.get("camera_cfg"), dict):
        cfg["camera_cfg"] = {k: v for k, v in cfg["camera_cfg"].items()
                             if k not in ("ip", "port", "serial_number")}

    dummy_ws = type("BuilderWS", (), {})()
    dummy_ws.components = {}
    # some components check this
    dummy_ws._scene_dirty = True



    # dorna2 is patched once at startup for Builder (see _patch_dorna2_once).
    comp = comp_factory.create_component(f"{type_name}_preview", cfg, dummy_ws)

    # If component exposes kinematic/attachment update logic (e.g., core),
    # ensure it is initialized in simulation mode so solids end up in the correct poses.
    try:
        if hasattr(comp, "update_pose"):
            # Some components (core) only update link poses when robot_api exists.
            J = ([float(v) for v in joints] + [0.0] * 8)[:8] if joints else [0.0] * 8
            if getattr(comp, "robot_api", None) is None:
                mod = sys.modules.get(comp.__class__.__module__)
                SimAPI = getattr(mod, "SimulationAPI", None) if mod else None
                if SimAPI is not None:
                    comp._simulation_mode = True
                    comp.robot_api = SimAPI(joints=J)
            elif joints:
                try:
                    comp.robot_api.joints = J
                except Exception:
                    pass
            # run once to build the attachment chain at the requested joints
            comp.update_pose()
    except Exception:
        pass

    solids = []
    world_Ts = {}
    if hasattr(comp, "assembly") and isinstance(comp.assembly, dict):
        world_Ts = _compute_world_Ts_for_solids(comp.assembly)
        for solid_name, solid in comp.assembly.items():
            # solid.type is the CAD name. Resolve the glb from the active
            # project's CAD/ folder first, then the library — same
            # project-first order the runtime server uses.
            stype = getattr(solid, "type", None) or solid_name
            glb_url = _resolve_cad_glb_url(stype)

            pose = [0, 0, 0, 0, 0, 0]
            try:
                if T_to_xyzabc is not None:
                    # Prefer world pose (simulation-style). Fall back to local.
                    T = world_Ts.get(solid_name)
                    if T is None and hasattr(solid, "local") and isinstance(solid.local, dict):
                        T = solid.local.get("T")
                    if T is not None:
                        pose = T_to_xyzabc(T)
            except Exception:
                pass

            # Robust anchor extraction (works across dorna2 versions and our shim)
            anchors = extract_solid_anchors(solid)

            # Collision boxes (local to solid frame; UI parents them so they follow edits)
            collision_local = []
            try:
                c_box_data = getattr(solid, "collision_box", None)
                boxes = []
                if c_box_data:
                    if isinstance(c_box_data, dict):
                        if solid_name in c_box_data:
                            boxes = c_box_data.get(solid_name) or []
                        elif "boxes" in c_box_data:
                            boxes = c_box_data.get("boxes") or []
                        elif len(c_box_data) == 1:
                            boxes = next(iter(c_box_data.values())) or []
                    elif isinstance(c_box_data, list):
                        boxes = c_box_data

                for box in boxes or []:
                    if not isinstance(box, dict):
                        continue
                    bp = box.get("pose")
                    bs = box.get("scale")
                    if not (isinstance(bp, (list, tuple)) and len(bp) == 6 and isinstance(bs, (list, tuple)) and len(bs) == 3):
                        continue
                    collision_local.append({
                        "pose": [float(bp[0]), float(bp[1]), float(bp[2]), float(bp[3]), float(bp[4]), float(bp[5])],
                        "scale": [float(bs[0]), float(bs[1]), float(bs[2])],
                    })
            except Exception:
                collision_local = []

            solids.append({
                "solid": solid_name,
                "solid_type": stype,
                "glb": glb_url,
                "pose": pose,
                "anchors": anchors,
                "collisionLocal": collision_local,
                "boxForGrip": bool(getattr(solid, "box_for_grip", False)),
            })

    return {"solids": solids}

COMPONENT_MAP = scan_registered_components()

# Project-local components (set via /api/set_project)
_project_path = None
_project_component_map = {}


def _project_cad_dir():
    """The active project's CAD/ folder, or None if no project is set."""
    return os.path.join(_project_path, "CAD") if _project_path else None


def _resolve_cad_glb_url(stype):
    """URL for a component type's glb, project CAD first then library.

    Returns ``/static/CAD/<type>.glb`` if the file exists in the project's
    CAD/ folder or the library static/CAD/, else None. The CAD static
    handler resolves which folder actually serves it (project-first too),
    so the URL is the same either way.
    """
    name = f"{stype}.glb"
    proj = _project_cad_dir()
    if proj and os.path.exists(os.path.join(proj, name)):
        return f"/static/CAD/{name}"
    if os.path.exists(os.path.join(CAD_DIR, name)):
        return f"/static/CAD/{name}"
    return None


class CADStaticHandler(tornado.web.StaticFileHandler):
    """Serve ``/static/CAD/*`` from the active project's CAD/ folder first,
    falling back to the library ``workspace/static/CAD/``. Mirrors the
    runtime server's project-first asset resolution so project-local
    meshes (+ their .bin buffers / textures) render in the builder."""

    def get_absolute_path(self, root, path):
        proj = _project_cad_dir()
        if proj:
            cand = os.path.abspath(os.path.join(proj, path))
            if os.path.isfile(cand):
                return cand
        return os.path.abspath(os.path.join(root, path))

    def validate_absolute_path(self, root, absolute_path):
        # Allow files under either the library CAD root or the project one.
        roots = [os.path.abspath(root)]
        proj = _project_cad_dir()
        if proj:
            roots.append(os.path.abspath(proj))
        if not any(absolute_path == r or absolute_path.startswith(r + os.sep) for r in roots):
            raise tornado.web.HTTPError(403)
        if not os.path.exists(absolute_path):
            raise tornado.web.HTTPError(404)
        if not os.path.isfile(absolute_path):
            raise tornado.web.HTTPError(403)
        return absolute_path

def scan_project_components(project_dir):
    """Scan a project's components/ folder for @register types AND import
    each module so its ``@register`` decorator actually runs.

    Regex-scanning the source only gives us the type→file map for the
    catalog; without importing the module, the factory registry never
    learns the type and ``create_component`` raises "Unknown component
    type" (→ instantiate 500, no anchors/collision in the builder). So
    we import each file here, registering it with the factory the same
    way the library components are at startup.
    """
    out = {}
    comp_dir = os.path.join(project_dir, "components")
    if not os.path.isdir(comp_dir):
        return out
    for root, _, files in os.walk(comp_dir):
        for fn in files:
            if not fn.endswith(".py") or fn.startswith("_"):
                continue
            fp = os.path.join(root, fn)
            try:
                src = open(fp, "r", encoding="utf-8", errors="ignore").read()
            except Exception:
                continue
            types = [mm.group(1) for mm in REGISTER_RE.finditer(src)]
            if not types:
                continue
            # Import the module so its @register decorator runs and the
            # factory can actually instantiate the type.
            try:
                load_module_from_path(fp, f"_project_component_{os.path.splitext(fn)[0]}")
            except Exception as e:
                print(f"[builder] failed to import project component {fp}: {e}")
                continue
            for t in types:
                out[t] = fp
    return out

STATIC_DIR = os.path.join(PARENT_DIR, "static")
WEB_DIR = os.path.join(BASE_DIR, "web")

OUT_DIR = os.path.join(PARENT_DIR, "projects", "builder")
OUT_PATH = os.path.join(OUT_DIR, "config.j2")


class IndexHandler(tornado.web.RequestHandler):
    def get(self):
        self.render('index.html')

PORT = int(os.environ.get("PORT", "5000"))
DEV_NOCACHE = os.environ.get("DEV_NOCACHE", "1") == "1"

sio = socketio.AsyncServer(async_mode="tornado", cors_allowed_origins="*")
app = tornado.web.Application([
    # CAD assets: project CAD/ first, then library static/CAD/ (must be
    # registered before the generic /static handler so it wins).
    (r"/static/CAD/(.*)", CADStaticHandler, {"path": CAD_DIR}),
    (r"/static/(.*)", tornado.web.StaticFileHandler, {"path": STATIC_DIR}),
    (r"/socket.io/(.*)", socketio.get_tornado_handler(sio)),
    # /save_config is added below after SaveConfigHandler definition
    (r"/", IndexHandler),
    (r"/(.*)", tornado.web.StaticFileHandler, {"path": WEB_DIR, "default_filename": "index.html"}),
], debug=True, template_path=WEB_DIR)


class CatalogHandler(tornado.web.RequestHandler):
    """Return list of registered component types (library + project)."""
    def get(self):
        all_types = set(COMPONENT_MAP.keys()) | set(_project_component_map.keys())
        self.write({"ok": True, "items": sorted(all_types)})


class CategoriesHandler(tornado.web.RequestHandler):
    """Return components grouped by folder category."""
    def get(self):
        categories = []
        if os.path.isdir(COMPONENTS_DIR):
            for folder in sorted(os.listdir(COMPONENTS_DIR)):
                folder_path = os.path.join(COMPONENTS_DIR, folder)
                if not os.path.isdir(folder_path) or folder.startswith("_") or folder.startswith("."):
                    continue
                cat_name = folder.replace("_", " ").lower()
                items = []
                for fn in sorted(os.listdir(folder_path)):
                    if not fn.endswith(".py") or fn.startswith("_"):
                        continue
                    fp = os.path.join(folder_path, fn)
                    try:
                        src = open(fp, "r", encoding="utf-8", errors="ignore").read()
                    except Exception:
                        continue
                    for m in REGISTER_RE.finditer(src):
                        items.append(m.group(1))
                if items:
                    categories.append({"name": cat_name, "items": items})
        # Add project-local components as a category
        if _project_component_map:
            categories.append({"name": "project", "items": sorted(_project_component_map.keys())})
        self.write({"ok": True, "categories": categories})

class TypeMetaHandler(tornado.web.RequestHandler):
    """Return anchors + options for a component type."""
    def get(self):
        t = self.get_argument("type", "")
        fp = COMPONENT_MAP.get(t)
        if not fp or not os.path.exists(fp):
            self.set_status(404)
            self.write({"ok": False, "error": f"unknown type: {t}"})
            return

        try:
            src = open(fp, "r", encoding="utf-8", errors="ignore").read()
        except Exception as e:
            self.set_status(500)
            self.write({"ok": False, "error": str(e)})
            return

        options = ast_extract_cfg_get_options(src)

        # Builder UI auto-generates checkboxes/fields from discovered options.
        # Hide internal toggles that exist in some components but are not
        # useful in Builder. IMPORTANT: do not hardcode has_* flags; only
        # filter known-noisy ones.
        #
        # Pneumatic IO config (``output_enable`` / ``output_disable`` =
        # [[pin, index, time]]) is never builder-relevant — it's actuator
        # wiring, set in the scene yaml, and the sim is always initialized
        # to match the real device. Hide it for every component.
        _HIDDEN_OPTS = {"output_enable", "output_disable"}
        options = [o for o in options if o.get("name") not in _HIDDEN_OPTS]

        # Instantiate component with defaults (no options) to mirror simulation.
        anchors_by_solid = {}
        glb = None
        try:
            bp = instantiate_component_blueprint(t, {})
            solids = bp.get("solids") or []
            for s in solids:
                a = s.get("anchors")
                if isinstance(a, dict) and a:
                    anchors_by_solid[s.get("solid")] = a
            # choose a good preview glb
            for s in solids:
                if s.get("glb"):
                    glb = s.get("glb")
                    break
        except Exception:
            anchors_by_solid = {}

        self.write({"ok": True, "meta": {"type": t, "options": options, "anchors": anchors_by_solid, "glb": glb}})


class InstantiateHandler(tornado.web.RequestHandler):
    """Instantiate component (simulation-style) and return full solids blueprint."""
    def post(self):
        try:
            data = json.loads(self.request.body.decode("utf-8") or "{}")
        except Exception:
            self.set_status(400)
            self.write({"ok": False, "error": "invalid json"})
            return

        t = data.get("type") or ""
        if t not in COMPONENT_MAP:
            self.set_status(404)
            self.write({"ok": False, "error": f"unknown type: {t}"})
            return
        opts = data.get("options") or {}
        if not isinstance(opts, dict):
            opts = {}

        # Never allow builder UI/state to force non-simulation behavior.
        # Some components (notably Core) interpret this flag as "connect to real robot".
        opts.pop("simulation", None)
        # ``joints`` is a VIEW parameter, not component config: pose the
        # kinematic chain (core) at these joints instead of zeros — used
        # by the recipes panel to show a solved reference pose.
        joints = opts.pop("joints", None)

        try:
            bp = instantiate_component_blueprint(t, opts, joints=joints)
            self.write({"ok": True, "blueprint": bp})
        except Exception as e:
            print(f"[builder] instantiate {t} failed: {type(e).__name__}: {e}")
            self.set_status(500)
            self.write({"ok": False, "error": str(e)})

class RailsHandler(tornado.web.RequestHandler):
    """Detect available rail types by scanning core.py for rail_hd_* references
       and verifying that a matching GLB model exists."""
    def get(self):
        import re
        rails = []
        # Scan core component source for rail type strings
        core_fp = COMPONENT_MAP.get("core")
        if core_fp and os.path.exists(core_fp):
            try:
                src = open(core_fp, "r", encoding="utf-8", errors="ignore").read()
                # Find all rail_hd_*mm type references
                found = set(re.findall(r'rail_hd_\d+mm', src))
                for rail_type in sorted(found, key=lambda r: int(re.search(r'\d+', r).group())):
                    # Check that the base GLB exists
                    glb_path = os.path.join(STATIC_DIR, "CAD", f"{rail_type}_base.glb")
                    if not os.path.exists(glb_path):
                        continue
                    # Verify component can instantiate with this rail (has anchors)
                    try:
                        bp = instantiate_component_blueprint("core", {
                            "has_rail": True,
                            "rail_cfg": {"type": rail_type, "axis": 6, "offset": 0,
                                         "usem": 1, "pprm": 4000, "tprm": 75,
                                         "usee": 1, "ppre": 4000, "tpre": 75,
                                         "p": 0.01, "i": 0.0001, "d": 0,
                                         "duration": 100, "threshold": 100}
                        })
                        has_rail_solid = any(
                            s.get("solid") == "rail_base" and s.get("anchors")
                            for s in (bp.get("solids") or [])
                        )
                        if not has_rail_solid:
                            continue
                    except Exception:
                        continue
                    # Extract size label (e.g. "500mm" from "rail_hd_500mm")
                    size = re.search(r'(\d+mm)', rail_type)
                    label = size.group(1) if size else rail_type
                    rails.append({"type": rail_type, "label": label})
            except Exception as e:
                self.write({"ok": False, "error": str(e)})
                return
        self.write({"ok": True, "rails": rails})


class SaveConfigHandler(tornado.web.RequestHandler):
    def set_default_headers(self):
        self.set_header("Access-Control-Allow-Origin", "*")
        self.set_header("Access-Control-Allow-Headers", "content-type")
        self.set_header("Access-Control-Allow-Methods", "POST, OPTIONS")

    def options(self):
        self.set_status(204)
        self.finish()

    def post(self):
        try:
            data = json.loads(self.request.body.decode("utf-8") or "{}")
        except Exception:
            self.set_status(400)
            self.finish({"ok": False, "error": "Invalid JSON"})
            return

        components = data.get("components") or {}
        if not isinstance(components, dict):
            self.set_status(400)
            self.finish({"ok": False, "error": "components must be an object"})
            return

        os.makedirs(OUT_DIR, exist_ok=True)

        # Write YAML similar to other project configs
        # Keep key order stable-ish: disable sort_keys
        with open(OUT_PATH, "w", encoding="utf-8") as f:
            yaml.safe_dump(components, f, sort_keys=False, default_flow_style=False)

        self.finish({"ok": True, "path": OUT_PATH})

class SetProjectHandler(tornado.web.RequestHandler):
    """Set the project path — scans components/ and CAD/ folders."""
    def set_default_headers(self):
        self.set_header("Access-Control-Allow-Origin", "*")
        self.set_header("Access-Control-Allow-Headers", "content-type")
        self.set_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")

    def options(self):
        self.set_status(204)
        self.finish()

    def get(self):
        """Return current project path."""
        global _project_path
        self.write({"ok": True, "path": _project_path})

    def post(self):
        global _project_path, _project_component_map
        try:
            data = json.loads(self.request.body.decode("utf-8") or "{}")
        except Exception:
            self.set_status(400)
            self.finish({"ok": False, "error": "Invalid JSON"})
            return

        path = (data.get("path") or "").strip()
        if path and not os.path.isdir(path):
            self.set_status(400)
            self.finish({"ok": False, "error": f"Directory not found: {path}"})
            return

        _project_path = path or None
        _project_component_map = scan_project_components(path) if path else {}

        # Merge project components into the global map so instantiate/type_meta work
        for k, v in _project_component_map.items():
            COMPONENT_MAP[k] = v

        self.finish({
            "ok": True,
            "path": _project_path,
            "components": sorted(_project_component_map.keys()),
        })

class ProjectBundleHandler(tornado.web.RequestHandler):
    """Everything the builder can import from the active project, resolved
    the way ``main.py`` resolves it: ``launch.yaml`` names the scene files
    (served with contents, in merge order) and the recipes file (served as
    a parsed name/class/component listing). No platform imports — recipes
    are rendered as text (jinja2) + yaml only; the SOLVE is a separate,
    subprocess-backed endpoint (/api/solve_ref)."""

    def set_default_headers(self):
        self.set_header("Access-Control-Allow-Origin", "*")

    def get(self):
        if not _project_path:
            self.write({"ok": True, "project": None, "scenes": [], "recipes": []})
            return
        out = {"ok": True, "project": _project_path, "scenes": [], "recipes": [],
               "components": sorted(_project_component_map.keys())}
        launch = {}
        try:
            with open(os.path.join(_project_path, "launch.yaml")) as f:
                launch = yaml.safe_load(f) or {}
        except Exception as ex:
            out["launch_error"] = str(ex)
        out["project_name"] = launch.get("project_name")
        scene = launch.get("scene") or []
        if isinstance(scene, str):
            scene = [scene]
        for rel in scene:
            p = os.path.join(_project_path, rel)
            try:
                text = open(p, encoding="utf-8").read()
                row = {"name": os.path.basename(rel), "path": rel, "text": text}
                # Real Jinja + YAML, server-side: the client's simple
                # parser only handles two nesting levels and mangles
                # deeper blocks (anchors: body: cap_seat:). cfg is the
                # authoritative structure; text stays for display.
                try:
                    from jinja2 import Template as _T
                    row["cfg"] = yaml.safe_load(_T(text).render()) or {}
                except Exception as ex:
                    row["cfg_error"] = str(ex)
                out["scenes"].append(row)
            except Exception as ex:
                out["scenes"].append({"name": os.path.basename(rel), "path": rel,
                                      "error": str(ex)})
        rel = launch.get("recipes", "recipes.j2")
        rp = os.path.join(_project_path, rel)
        if os.path.isfile(rp):
            out["recipes_file"] = rel
            try:
                from jinja2 import Template as _T
                defs = yaml.safe_load(_T(open(rp, encoding="utf-8").read()).render()) or {}
                for name, spec in defs.items():
                    kw = (spec or {}).get("kwargs") or {}
                    out["recipes"].append({
                        "name": name,
                        "class": (spec or {}).get("class", ""),
                        "component": kw.get("component"),
                        "pinned": kw.get("ref_joints") is not None,
                    })
            except Exception as ex:
                out["recipes_error"] = str(ex)
        self.write(out)


# /api/solve_ref result cache: {project_path: (sig, result_dict)} where sig
# is the mtime fingerprint of launch.yaml + scene files + recipes file —
# any edit re-solves, an unchanged project answers instantly.
_ref_solve_cache = {}


def _project_solve_sig(project_dir):
    files = [os.path.join(project_dir, "launch.yaml")]
    try:
        launch = yaml.safe_load(open(files[0])) or {}
    except Exception:
        launch = {}
    scene = launch.get("scene") or []
    if isinstance(scene, str):
        scene = [scene]
    files += [os.path.join(project_dir, p) for p in scene]
    files.append(os.path.join(project_dir, launch.get("recipes", "recipes.j2")))
    sig = []
    for p in files:
        try:
            sig.append((p, os.path.getmtime(p)))
        except OSError:
            sig.append((p, None))
    return tuple(sig)


class SolveRefHandler(tornado.web.RequestHandler):
    """Solve every recipe's reference joints for the active project.

    Runs ``ref_solve.py`` in a SUBPROCESS: the builder patches dorna2 and
    friends with preview stubs, and the solve must run the real platform
    code (Workspace in simulation + each recipe's own __init__). First
    call costs seconds; repeats are served from the mtime cache."""

    def set_default_headers(self):
        self.set_header("Access-Control-Allow-Origin", "*")
        self.set_header("Access-Control-Allow-Headers", "content-type")
        self.set_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")

    def options(self):
        self.set_status(204)
        self.finish()

    async def post(self):
        if not _project_path:
            self.write({"ok": False, "error": "no project path set"})
            return
        sig = _project_solve_sig(_project_path)
        cached = _ref_solve_cache.get(_project_path)
        if cached and cached[0] == sig:
            self.write(cached[1])
            return
        script = os.path.join(BASE_DIR, "ref_solve.py")

        def _run():
            import subprocess
            r = subprocess.run([sys.executable, script, _project_path],
                               capture_output=True, text=True, timeout=600,
                               cwd=PARENT_DIR)
            line = (r.stdout or "").strip().splitlines()
            try:
                return json.loads(line[-1]) if line else {"ok": False, "error": "no output"}
            except Exception:
                return {"ok": False,
                        "error": (r.stderr or r.stdout or "solve failed")[-800:]}

        result = await tornado.ioloop.IOLoop.current().run_in_executor(None, _run)
        if result.get("ok"):
            _ref_solve_cache[_project_path] = (sig, result)
        self.write(result)


# patch handler into app
app.add_handlers(r".*$", [(r"/save_config", SaveConfigHandler)])
app.add_handlers(r".*$", [(r"/api/set_project", SetProjectHandler)])
app.add_handlers(r".*$", [(r"/api/project_bundle", ProjectBundleHandler)])
app.add_handlers(r".*$", [(r"/api/solve_ref", SolveRefHandler)])

# catalog endpoint (CAD/*.glb)
app.add_handlers(r".*$", [(r"/api/catalog", CatalogHandler)])
app.add_handlers(r".*$", [(r"/api/categories", CategoriesHandler)])
app.add_handlers(r".*$", [(r"/api/type_meta", TypeMetaHandler)])
app.add_handlers(r".*$", [(r"/api/instantiate", InstantiateHandler)])
app.add_handlers(r".*$", [(r"/api/rails", RailsHandler)])

world_state = {}

def merge_into_state(state: dict, patch: dict):
    for k, v in (patch or {}).items():
        if v is None:
            continue
        if isinstance(v, dict) and v.get("delete"):
            state.pop(k, None)
        else:
            if k not in state:
                state[k] = {}
            if isinstance(v, dict):
                state[k].update(v)
            else:
                state[k] = v

@sio.event
async def connect(sid, environ, auth):
    if world_state:
        await sio.emit("scene_update", world_state, room=sid)

@sio.event
async def upstream_update(sid, payload):
    merge_into_state(world_state, payload)
    await sio.emit("scene_update", payload)

@sio.event
async def reset_scene(sid):
    world_state.clear()
    await sio.emit("scene_reset")


class ResetHandler(tornado.web.RequestHandler):
    """Clear the whole server-side scene synchronously.

    The ``reset_scene`` socket event is fire-and-forget — if the page
    reloads before it lands, ``world_state`` survives and the "cleared"
    scene comes right back on reconnect. This HTTP endpoint clears it
    in-band so the caller can await it *before* reloading."""
    def set_default_headers(self):
        self.set_header("Access-Control-Allow-Origin", "*")
        self.set_header("Access-Control-Allow-Headers", "content-type")
        self.set_header("Access-Control-Allow-Methods", "POST, OPTIONS")

    def options(self):
        self.set_status(204)
        self.finish()

    def post(self):
        global world_state
        world_state.clear()
        self.finish({"ok": True})


app.add_handlers(r".*$", [(r"/api/reset", ResetHandler)])

if __name__ == "__main__":
    app.listen(PORT)
    print(f"[builder] listening at http://127.0.0.1:{PORT}")
    print(" - static:", STATIC_DIR)
    print(" - web:", WEB_DIR)
    print(" - save_config:", OUT_PATH)

    for p in (STATIC_DIR, WEB_DIR):
        if os.path.exists(p):
            autoreload.watch(p)
    autoreload.start()
    tornado.ioloop.IOLoop.current().start()