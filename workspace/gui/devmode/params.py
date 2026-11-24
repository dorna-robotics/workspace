"""The Parameters window's schema: which settings of a placed component
an operator sets on the bench (connection, rail, camera, simulation),
with their current value from the project's scene.

The library's ``DEFAULTS`` hold dozens of motion-tuning keys nobody
sets by hand, so the window shows a curated set: a field appears when
the component's class declares the key (scene_builder's AST reader),
the scene already sets it, or the type is one that always has it
(core → rail, inspection_* → camera).
"""
import glob
import os
import re

import gui.scene_builder.server as sb

CAMERA_TYPES = ["d405", "ueye_xs", "hikrobot", "oak"]

# key → (label, kind, hint). kind: text | int | float | bool | serial | enum
FIELDS = {
    "ip":                     ("IP address", "text", "Empty = no device; the component runs in simulation."),
    "port":                   ("Port", "int", ""),
    "address":                ("Bus address", "int", ""),
    "baud":                   ("Baud rate", "int", ""),
    "serial_number":          ("Serial number", "text", ""),
    "has_rail":               ("Has a rail", "bool", ""),
    "rail_cfg.type":          ("Rail", "enum", ""),
    "rail_cfg.offset":        ("Rail offset (mm)", "float", "Measured by rail calibration (docs/rail-calibration.md)."),
    "camera_cfg.server":      ("Vision server", "enum", "The vision_server component this camera is plugged into."),
    "camera_cfg.serial_number": ("Camera serial number", "text", ""),
    "camera_cfg.type":        ("Camera type", "enum", ""),
    "critical":               ("Critical", "bool", "A fault on this device stops the run."),
    "simulation":             ("Simulation", "bool", "On = nothing is sent to the real device."),
}
SERIAL_HINT = "A /dev/serial/by-id/… path — it stays the same across reboots."
SKIP_FOLDERS = {"probe"}            # passive tools whose module only mentions sim


def _src(ctype):
    fp = sb.COMPONENT_MAP.get(ctype)
    return fp if fp and os.path.isfile(fp) else None


def _folder_mentions_sim(fp):
    folder = os.path.dirname(fp)
    if os.path.basename(folder) in SKIP_FOLDERS:
        return False
    for f in glob.glob(os.path.join(folder, "*.py")):
        try:
            if "simulation" in open(f, encoding="utf-8", errors="ignore").read():
                return True
        except OSError:
            pass
    return False


_device_cache = {}


def is_device(ctype, cfg=None):
    """A component that talks to hardware (has a simulation switch)."""
    if isinstance(cfg, dict) and "simulation" in cfg:
        return True
    if ctype in _device_cache:
        return _device_cache[ctype]
    fp = _src(ctype)
    out = bool(fp and _folder_mentions_sim(fp)) or ctype.startswith("inspection_")
    _device_cache[ctype] = out
    return out


def kind_of(ctype):
    if ctype == "core" or ctype.startswith("core_"):
        return "robot"
    if ctype.startswith("inspection_") or ctype == "vision_server":
        return "camera"
    return "device"


def rail_types():
    fp = _src("core")
    if not fp:
        return []
    src = open(fp, encoding="utf-8", errors="ignore").read()
    return sorted(set(re.findall(r"rail_hd_\d+mm", src)), key=lambda r: int(re.search(r"\d+", r).group()))


def serial_ports():
    """[{port, description}] — the stable by-id names, then bare tty nodes."""
    out = []
    for p in sorted(glob.glob("/dev/serial/by-id/*")):
        try:
            target = os.path.realpath(p)
        except OSError:
            target = ""
        out.append({"port": p, "description": f"{os.path.basename(p)} → {os.path.basename(target)}"})
    if not out:
        for p in sorted(glob.glob("/dev/ttyUSB*") + glob.glob("/dev/ttyACM*")):
            out.append({"port": p, "description": os.path.basename(p)})
    return out


def _get(cfg, dotted):
    node = cfg
    for k in dotted.split("."):
        if not isinstance(node, dict) or k not in node:
            return None
        node = node[k]
    return node


_declared_cache = {}      # type → (mtime, option names)


def _declared(ctype):
    """The keys the type's class reads (scene_builder's AST reader), cached
    per source mtime — core.py is large and the window opens per click."""
    fp = _src(ctype)
    if not fp:
        return set()
    mt = os.path.getmtime(fp)
    hit = _declared_cache.get(ctype)
    if hit and hit[0] == mt:
        return hit[1]
    try:
        names = {o["name"] for o in sb.ast_extract_cfg_get_options(open(fp, encoding="utf-8").read())}
    except Exception:
        names = set()
    _declared_cache[ctype] = (mt, names)
    return names


def schema(name, cfg, scene):
    """The Parameters window for component ``name`` (its merged scene
    ``cfg``; ``scene`` = the whole merged scene, for the vision-server
    list). Returns {name, type, kind, device, fields:[…]}."""
    cfg = cfg if isinstance(cfg, dict) else {}
    ctype = str(cfg.get("type") or "")
    declared = _declared(ctype)
    keys = []

    def want(k):
        if k not in keys:
            keys.append(k)

    for k in ("ip", "port", "address", "baud", "serial_number"):
        if k in declared or k in cfg:
            want(k)
    if kind_of(ctype) == "robot":
        want("ip")
        want("has_rail")
        want("rail_cfg.type")
        want("rail_cfg.offset")
    if ctype.startswith("inspection_"):
        for k in ("camera_cfg.server", "camera_cfg.serial_number", "camera_cfg.type"):
            want(k)
    if ctype == "vision_server":
        want("ip")
        want("port")
    device = is_device(ctype, cfg)
    if device and ("critical" in declared or "critical" in cfg):
        want("critical")
    if device:
        want("simulation")

    serial = "baud" in keys or str(cfg.get("port") or "").startswith("/dev")
    servers = sorted(n for n, c in (scene or {}).items()
                     if isinstance(c, dict) and c.get("type") == "vision_server")
    fields = []
    for k in keys:
        label, kind, hint = FIELDS[k]
        f = {"key": k, "label": label, "kind": kind, "hint": hint, "value": _get(cfg, k)}
        if k == "port" and serial:
            f.update(kind="serial", label="USB port", hint=SERIAL_HINT)
        elif k == "rail_cfg.type":
            f["options"] = rail_types()
        elif k == "camera_cfg.server":
            f["options"] = [""] + servers
        elif k == "camera_cfg.type":
            f["options"] = CAMERA_TYPES
        if k == "simulation" and f["value"] is None:
            f["value"] = True               # the library default
        fields.append(f)
    return {"name": name, "type": ctype, "kind": kind_of(ctype), "device": device, "fields": fields}


def coerce(field, value):
    """A value from the window, typed for the scene; raises ValueError."""
    kind = field["kind"]
    if kind == "bool":
        if isinstance(value, bool):
            return value
        if str(value).lower() in ("true", "1", "on", "yes"):
            return True
        if str(value).lower() in ("false", "0", "off", "no"):
            return False
        raise ValueError(f"{field['label']}: on or off")
    if kind == "int":
        if value in ("", None):
            return ""                       # unset, per the scene convention
        try:
            v = int(str(value).strip())
        except ValueError:
            raise ValueError(f"{field['label']}: a whole number")
        if field["key"] == "port" and not 0 < v < 65536:
            raise ValueError("Port: 1 to 65535")
        return v
    if kind == "float":
        try:
            return float(str(value).strip())
        except ValueError:
            raise ValueError(f"{field['label']}: a number")
    v = "" if value is None else str(value).strip()
    if field["key"] == "ip" and v and not re.fullmatch(r"[A-Za-z0-9.\-:]+", v):
        raise ValueError("IP address: an address like 192.168.1.20 or a host name")
    if kind == "enum" and v and field.get("options") and v not in field["options"]:
        raise ValueError(f"{field['label']}: one of {', '.join(o for o in field['options'] if o)}")
    if kind == "serial" and v and not v.startswith("/dev/"):
        raise ValueError("USB port: a /dev/… path")
    return v
