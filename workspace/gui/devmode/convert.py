"""Other model formats → one .glb for the component builder.

``to_glb([(name, bytes), …]) -> (glb bytes, note)``: the files the user
chose together (an .obj with its .mtl and texture pictures; a .step / .stp;
an .stl, .ply, .3mf, .off, .dae; a .gltf with its .bin) are written to a
temporary folder and read with trimesh — the .obj finds its .mtl and
pictures there by name — then exported as one binary glTF. The vertices
keep the file's own numbers and axes: the builder's Units and Up axis
choices apply to the result as to any .glb. STEP is read through trimesh's
``cascadio`` (OpenCASCADE) when it is installed on this robot.
"""
import os
import re
import tempfile
from pathlib import Path

MODEL_SUFFIXES = (".step", ".stp", ".obj", ".stl", ".ply", ".3mf", ".off", ".dae", ".gltf")
MAX_BYTES = 200 * 1024 * 1024


class ConvertError(Exception):
    pass


def _safe(name):
    name = Path(str(name or "")).name
    if not name or not re.fullmatch(r"[\w .()+-]+", name) or name.startswith("."):
        raise ConvertError(f"{name or 'a file'}: rename it to letters, digits, spaces, . _ - and try again")
    return name


def to_glb(files):
    try:
        import trimesh
    except ImportError:
        raise ConvertError("converting models needs the trimesh package on this robot (sudo pip3 install trimesh)")
    if not files:
        raise ConvertError("choose the model file")
    if sum(len(b) for _, b in files) > MAX_BYTES:
        raise ConvertError("those files are over 200 MB together")
    main = [n for n, _ in files if Path(n).suffix.lower() in MODEL_SUFFIXES]
    if len(main) != 1:
        raise ConvertError("choose one model file (.obj, .step, .stl …) — with its .mtl and pictures for an .obj"
                           if not main else f"choose one model at a time (got {', '.join(main)})")
    main = _safe(main[0])
    suffix = Path(main).suffix.lower()
    with tempfile.TemporaryDirectory(prefix="cb_convert_") as tmp:
        for n, b in files:
            Path(tmp, _safe(n)).write_bytes(b)
        try:
            scene = trimesh.load(os.path.join(tmp, main), force="scene")
        except (ImportError, ModuleNotFoundError) as e:
            if suffix in (".step", ".stp"):
                raise ConvertError("STEP files need the cascadio package on this robot (sudo pip3 install cascadio) — "
                                   "or export the part as .glb, .obj or .stl")
            raise ConvertError(f"reading {main} needs a package this robot doesn't have: {e}")
        except Exception as e:
            raise ConvertError(f"couldn't read {main}: {e}")
        if scene.is_empty:
            raise ConvertError(f"{main} has no geometry")
        notes = []
        if suffix == ".obj":
            mtl = [n for n, _ in files if n.lower().endswith(".mtl")]
            if not mtl and "mtllib" in Path(tmp, main).read_text(errors="ignore"):
                notes.append("no .mtl came with it — choose it together with the .obj for its colours")
        try:
            glb = scene.export(file_type="glb")
        except Exception as e:
            raise ConvertError(f"couldn't write the .glb: {e}")
    return glb, "; ".join(notes)
