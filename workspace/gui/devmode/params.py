"""The Parameters window's schema: which settings of a placed component
an operator sets on the bench (connection, rail, camera, simulation),
with their current value from the project's scene.

The library's ``DEFAULTS`` hold dozens of motion-tuning keys nobody
sets by hand, so the window shows a curated set: a field appears when
the component's class declares the key (scene_builder's AST reader),
the scene already sets it, or the type is one that always has it
(core → rail, inspection_* → camera).

I/O wiring the human fills in on the bench — the robot output pins a
decapper, gripper or shaker switches (``output_*``: [[pin, value, wait]])
and an input pin it reads (``*_input_index``) — comes from the class's
``DEFAULTS`` (merged down its bases, like the classes merge them).
"""
import copy
import glob
import os
import re

import gui.scene_builder.server as sb
from workspace.components import factory as comp_factory

from .camera import NETWORK_TYPES

CAMERA_TYPES = ["d405", "ueye_xs", "hikrobot", "oak"]

# key → (label, kind, hint). kind: text | int | float | bool | serial | enum
FIELDS = {
    "ip":                     ("IP address", "text", "Its address on the network, e.g. 192.168.1.20."),
    "port":                   ("Port", "int", ""),
    "address":                ("Bus address", "int", ""),
    "baud":                   ("Baud rate", "int", ""),
    "serial_number":          ("Serial number", "text", ""),
    "has_rail":               ("Has a rail", "bool", ""),
    "rail_cfg.type":          ("Rail", "enum", ""),
    "rail_cfg.offset":        ("Rail offset (mm)", "float", "Measured by rail calibration (docs/rail-calibration.md)."),
    "camera_cfg.server":      ("Vision server", "enum", "The vision_server component this camera is plugged into."),
    "camera_cfg.serial_number": ("Camera serial number", "text", ""),
    "camera_cfg.ip":          ("Camera IP address", "text", "A PoE / GigE camera is reached by its IP address "
                                                            "or its serial number."),
    "camera_cfg.type":        ("Camera type", "enum", ""),
    "critical":               ("Critical", "bool", "A fault on this device stops the run."),
    "simulation":             ("Simulation", "bool", "On = nothing is sent to the real device."),
}
SERIAL_HINT = "A /dev/serial/by-id/… path — it stays the same across reboots."
IO_PINS = 16                        # the robot's outputs 0–15 (core.output)
IO_HINT = "Each step sets one robot output pin to 0 or 1, then waits. An empty pin does nothing."
IO_SEQ_RE = re.compile(r"^output_[a-z0-9_]+$")
IO_IN_RE = re.compile(r"^[a-z0-9_]*input_index$")
SKIP_FOLDERS = {"probe"}            # passive tools whose module only mentions sim


def _src(ctype):
    fp = sb.COMPONENT_MAP.get(ctype) or sb._project_component_map.get(ctype)
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


_defaults_cache = {}      # type → (class, merged DEFAULTS)


def defaults(ctype):
    """The type's ``DEFAULTS``, base classes first (a project component
    registers on set_project; an unknown type has none)."""
    cls = comp_factory._registry.get(ctype)
    hit = _defaults_cache.get(ctype)
    if hit and hit[0] is cls:
        return hit[1]
    out = {}
    for c in reversed(getattr(cls, "__mro__", ())):
        d = c.__dict__.get("DEFAULTS")
        if isinstance(d, dict):
            out.update(d)
    _defaults_cache[ctype] = (cls, out)
    return out


def _is_seq(v):
    return isinstance(v, list) and bool(v) and all(isinstance(r, (list, tuple)) and len(r) == 3 for r in v)


def io_keys(ctype):
    """{key: default} of the type's I/O wiring settings."""
    out = {}
    for k, v in defaults(ctype).items():
        if IO_SEQ_RE.match(k) and _is_seq(v):
            out[k] = v
        elif IO_IN_RE.match(k) and (v is None or isinstance(v, int)):
            out[k] = v
    return out


def _io_label(k):
    if IO_IN_RE.match(k):
        return k[:-len("_index")].replace("_", " ").capitalize() + " pin"
    return "Output pins · " + k[len("output_"):].replace("_", " ")


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
        for k in ("camera_cfg.server", "camera_cfg.type", "camera_cfg.serial_number", "camera_cfg.ip"):
            want(k)
    if ctype == "vision_server":
        want("ip")
        want("port")
    io = io_keys(ctype)
    for k in io:
        want(k)
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
        if k in io:
            seq = IO_SEQ_RE.match(k) is not None
            fields.append({"key": k, "label": _io_label(k), "kind": "io" if seq else "int",
                           "hint": IO_HINT if seq else f"The robot input pin (0–{IO_PINS - 1}) it reads.",
                           "value": _get(cfg, k), "default": copy.deepcopy(io[k]), "group": "io"})
            continue
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
            dflt = (defaults(ctype).get("camera_cfg") or {}).get("type")
            if dflt:
                f["default"] = dflt
        elif k == "camera_cfg.ip":
            f["types"] = sorted(NETWORK_TYPES)       # PoE / GigE: the window shows it for these only
        if k == "simulation" and f["value"] is None:
            f["value"] = True               # the library default
        fields.append(f)
    return {"name": name, "type": ctype, "kind": kind_of(ctype), "device": device, "fields": fields}


def no_connection(sch, scene=None):
    """A real device (simulation off) with nothing to reach it by: its
    Settings ``sch`` (schema()) → {key, text} naming the field to fill, or
    None. The address is the schema's own connection field — ip, else a
    USB / serial port, else a serial number — so a part wired through the
    robot's I/O (no such field) never counts as unconnected."""
    if not sch.get("device"):
        return None
    f = {x["key"]: x for x in sch["fields"]}
    if (f.get("simulation") or {}).get("value", True) is not False:
        return None
    empty = lambda k: k in f and str(f[k].get("value") or "").strip() == ""
    fix = " — or turn on simulation."
    if sch["type"].startswith("inspection_"):
        srv = str(f.get("camera_cfg.server", {}).get("value") or "")
        if not srv or not isinstance((scene or {}).get(srv), dict):
            return {"key": "camera_cfg.server", "text": "No vision server chosen: pick the one this camera is plugged into" + fix}
        typ = str(f.get("camera_cfg.type", {}).get("value") or f.get("camera_cfg.type", {}).get("default") or "")
        if empty("camera_cfg.serial_number") and (typ not in NETWORK_TYPES or empty("camera_cfg.ip")):
            what = "its serial number or IP address" if typ in NETWORK_TYPES else "its serial number"
            return {"key": "camera_cfg.serial_number", "text": f"No camera set: enter {what}" + fix}
        return None
    for k, what in (("ip", "its IP address"), ("port", "its USB port"), ("serial_number", "its serial number")):
        if k in f:
            if k == "port" and f[k]["kind"] != "serial":
                continue                    # a TCP port number goes with the ip
            return {"key": k, "text": f"No connection set: enter {what}" + fix} if empty(k) else None
    return None


def coerce(field, value):
    """A value from the window, typed for the scene; raises ValueError."""
    kind = field["kind"]
    if kind == "io":
        return _coerce_io(field, value)
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
        if field.get("group") == "io" and not 0 <= v < IO_PINS:
            raise ValueError(f"{field['label']}: 0 to {IO_PINS - 1}")
        return v
    if kind == "float":
        try:
            return float(str(value).strip())
        except ValueError:
            raise ValueError(f"{field['label']}: a number")
    v = "" if value is None else str(value).strip()
    if field["key"] in ("ip", "camera_cfg.ip") and v and not re.fullmatch(r"[A-Za-z0-9.\-:]+", v):
        raise ValueError(f"{field['label']}: an address like 192.168.1.20 or a host name")
    if kind == "enum" and v and field.get("options") and v not in field["options"]:
        raise ValueError(f"{field['label']}: one of {', '.join(o for o in field['options'] if o)}")
    if kind == "serial" and v and not v.startswith("/dev/"):
        raise ValueError("USB port: a /dev/… path")
    return v


def _coerce_io(field, value):
    """[[pin, value, wait], …]: pin 0–15 or empty (None), value 0/1,
    wait seconds ≥ 0."""
    name = field["label"]
    if not isinstance(value, list) or not value:
        raise ValueError(f"{name}: at least one step")
    out = []
    for i, row in enumerate(value, 1):
        if not isinstance(row, (list, tuple)) or len(row) != 3:
            raise ValueError(f"{name}, step {i}: pin, value and wait")
        pin, val, wait = row
        if pin in ("", None):
            pin = None
        else:
            try:
                pin = int(str(pin).strip())
            except ValueError:
                raise ValueError(f"{name}, step {i}: the pin is a whole number")
            if not 0 <= pin < IO_PINS:
                raise ValueError(f"{name}, step {i}: pin 0 to {IO_PINS - 1}")
        if val in ("", None):
            val = None
        elif str(val).strip() in ("0", "1"):
            val = int(str(val).strip())
        else:
            raise ValueError(f"{name}, step {i}: the value is 0 or 1")
        if pin is not None and val is None:
            raise ValueError(f"{name}, step {i}: pin {pin} needs a value, 0 or 1")
        try:
            wait = float(str(wait).strip() or 0)
        except ValueError:
            raise ValueError(f"{name}, step {i}: the wait is a number of seconds")
        if wait < 0:
            raise ValueError(f"{name}, step {i}: the wait is 0 s or more")
        out.append([pin, val, wait])
    return out
