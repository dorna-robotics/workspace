"""Files the user gives the assistant: stored, described, and turned into
what the model reads.

    save(session, name, src_path, project=None) -> record
    get(session, upload_id) -> record | None
    content_blocks(session, ids) -> [message content blocks]

Every upload is kept per conversation under the data home,
``~/.workspace/ai/uploads/<session>/<id>/<safe name>`` (owner-only), with
an ``index.json`` of the records. When a project is in context the file is
also copied into the project's own ``uploads/`` folder, so a model file or
a drawing the user handed over stays with the project.

What the model receives for each kind (``content_blocks``):

- image (png / jpg / webp / gif) — an image block (down-scaled when large);
- pdf — a document block: Claude reads the text AND the scanned pages
  itself (that is the OCR). A big PDF goes as its first pages plus the
  extracted text of the rest; past that, the text only;
- small text (csv / json / yaml / py / txt / md …) — its content inline;
- everything else (CAD, vision models, zips) — a text note with the path,
  kind and size; the tools (list_uploads, read_upload,
  add_component_from_file, install_vision_model) act on it.
"""
import base64
import io
import json
import os
import re
import shutil
import threading
import time
import uuid
from pathlib import Path

from gui.data_home import data_path

MAX_BYTES = 50 * 1024 * 1024                    # one upload
MAX_PER_SESSION = 200
IMAGE_MAX_SIDE = 1568                           # the model's useful resolution
IMAGE_MAX_BYTES = 3_700_000                     # under the API's 5 MB base64 cap
PDF_MAX_BYTES = 20 * 1024 * 1024                # one document block
PDF_MAX_PAGES = 100
PDF_TEXT_CHARS = 150_000
INLINE_TEXT_BYTES = 60_000
_LOCK = threading.Lock()                        # the index's read-modify-write (uploads arrive in parallel)

IMAGE_EXT = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif"}
KINDS = {
    "pdf": {".pdf"},
    "cad": {".glb", ".gltf", ".step", ".stp", ".stl", ".obj", ".3mf", ".iges", ".igs", ".bin"},
    "vision_model": {".pkl", ".onnx", ".pt", ".pth", ".engine", ".blob", ".tflite", ".xml", ".pdmodel", ".torchscript"},
    "archive": {".zip", ".tar", ".gz", ".tgz", ".7z"},
    "text": {".csv", ".tsv", ".json", ".yaml", ".yml", ".py", ".txt", ".md", ".j2", ".ini", ".cfg", ".toml", ".log",
             ".js", ".html", ".css", ".sh", ".ipynb"},
}
KIND_WORDS = {"image": "image", "pdf": "PDF document", "cad": "3D model / CAD file", "vision_model": "vision model",
              "archive": "archive", "text": "text file", "other": "file"}


class UploadError(Exception):
    pass


def kind_of(name):
    ext = Path(name).suffix.lower()
    if ext in IMAGE_EXT:
        return "image"
    for k, exts in KINDS.items():
        if ext in exts:
            return k
    return "other"


def safe_name(name):
    """A file name that is only a name: no folders, no dot-files, no odd
    characters, at most 100 characters (the extension kept)."""
    base = os.path.basename(str(name or "").replace("\\", "/")).strip()
    stem, ext = os.path.splitext(base)
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._-")[:90] or "file"
    ext = re.sub(r"[^A-Za-z0-9.]+", "", ext)[:10].lower()
    return stem + ext


def human_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def _dir(sid):
    if not re.fullmatch(r"[0-9a-f]{1,32}", str(sid or "")):
        raise UploadError("bad session")
    d = Path(data_path("ai", "uploads", sid, "x")).parent
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
    return d


def _index_path(sid):
    return _dir(sid) / "index.json"


def records(sid):
    try:
        return json.loads(_index_path(sid).read_text()) or []
    except (OSError, ValueError, UploadError):
        return []


def _write_index(sid, recs):
    p = _index_path(sid)
    tmp = p.with_name(f"index.{uuid.uuid4().hex[:8]}.tmp")
    tmp.write_text(json.dumps(recs, indent=1))
    os.replace(tmp, p)


def get(sid, upload_id):
    return next((r for r in records(sid) if r["id"] == upload_id), None)


def find(sid, ref):
    """An upload by id, or by its (original or stored) file name."""
    recs = records(sid)
    ref = str(ref or "").strip()
    return (next((r for r in recs if r["id"] == ref), None)
            or next((r for r in reversed(recs) if ref in (r["name"], r["file"], Path(r["path"]).name)), None))


def _unique(folder, name):
    p = folder / name
    stem, ext = os.path.splitext(name)
    i = 2
    while p.exists():
        p = folder / f"{stem}_{i}{ext}"
        i += 1
    return p


def save(sid, name, src, project_dir=None, project=None):
    """Move the received file ``src`` (a temp file) into the session's
    store; copy it into ``<project>/uploads/`` when a project is given."""
    size = os.path.getsize(src)
    if size > MAX_BYTES:
        raise UploadError(f"the file is larger than {MAX_BYTES // (1024 * 1024)} MB")
    if size == 0:
        raise UploadError("the file is empty")
    if len(records(sid)) >= MAX_PER_SESSION:
        raise UploadError("too many files in this conversation — start a new chat")
    uid = uuid.uuid4().hex[:10]
    fname = safe_name(name)
    folder = _dir(sid) / uid
    folder.mkdir()
    dest = folder / fname
    shutil.move(src, dest)
    os.chmod(dest, 0o600)
    kind = kind_of(fname)
    rec = {"id": uid, "name": os.path.basename(str(name))[:200] or fname, "file": fname, "path": str(dest),
           "size": size, "kind": kind, "mime": IMAGE_EXT.get(Path(fname).suffix.lower()) or
           ("application/pdf" if kind == "pdf" else "application/octet-stream"),
           "t": time.time(), "project": None, "project_path": None}
    if project_dir is not None:
        try:
            up = Path(project_dir) / "uploads"
            up.mkdir(exist_ok=True)
            with _LOCK:
                pcopy = _unique(up, fname)
                shutil.copyfile(dest, pcopy)
            rec["project"] = project
            rec["project_path"] = str(pcopy.relative_to(project_dir))
        except OSError as e:
            rec["project_error"] = str(e)
    if kind == "pdf":
        rec["pages"] = _pdf_pages(dest)
    with _LOCK:
        recs = records(sid)
        recs.append(rec)
        _write_index(sid, recs)
    return rec


def public(rec):
    return {k: rec.get(k) for k in ("id", "name", "file", "size", "kind", "mime", "project", "project_path", "pages")}


# ── what the model gets ──────────────────────────────────────────────

def _pdf_reader(path):
    try:
        from pypdf import PdfReader
    except ImportError:
        return None
    try:
        return PdfReader(str(path))
    except Exception:
        return None


def _pdf_pages(path):
    r = _pdf_reader(path)
    try:
        return len(r.pages) if r is not None else None
    except Exception:
        return None


def pdf_text(path, first=1, last=None, limit=PDF_TEXT_CHARS):
    r = _pdf_reader(path)
    if r is None:
        return None
    out, n = [], len(r.pages)
    last = min(n, last or n)
    for i in range(max(1, first), last + 1):
        try:
            t = r.pages[i - 1].extract_text() or ""
        except Exception:
            t = ""
        out.append(f"--- page {i} ---\n{t.strip()}")
        if sum(len(x) for x in out) > limit:
            out.append(f"… [text cut at page {i} of {n}]")
            break
    return "\n".join(out)


def _pdf_head(path, pages):
    """The first ``pages`` pages as a new PDF (bytes), or None."""
    r = _pdf_reader(path)
    if r is None:
        return None
    try:
        from pypdf import PdfWriter
        w = PdfWriter()
        for p in r.pages[:pages]:
            w.add_page(p)
        buf = io.BytesIO()
        w.write(buf)
        return buf.getvalue()
    except Exception:
        return None


def _image_bytes(rec):
    """(media_type, bytes) within the API's limits — down-scaled when big."""
    raw = Path(rec["path"]).read_bytes()
    mt = rec["mime"]
    try:
        from PIL import Image
    except ImportError:
        if len(raw) > IMAGE_MAX_BYTES:
            raise UploadError("the image is too large to show the assistant")
        return mt, raw
    try:
        im = Image.open(io.BytesIO(raw))
        big = max(im.size) > IMAGE_MAX_SIDE
        if not big and len(raw) <= IMAGE_MAX_BYTES and mt != "image/gif":
            return mt, raw
        if getattr(im, "is_animated", False):
            im.seek(0)
        im = im.convert("RGB")
        im.thumbnail((IMAGE_MAX_SIDE, IMAGE_MAX_SIDE))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=88)
        return "image/jpeg", buf.getvalue()
    except Exception:
        if len(raw) > IMAGE_MAX_BYTES:
            raise UploadError("the image could not be read")
        return mt, raw


def describe(rec):
    bits = [f"id {rec['id']}", KIND_WORDS.get(rec["kind"], "file"), human_size(rec["size"]),
            f"saved at {rec['path']}"]
    if rec.get("project_path"):
        bits.append(f"copied into project {rec['project']} as {rec['project_path']}")
    if rec.get("pages"):
        bits.append(f"{rec['pages']} pages")
    return f"Attached file “{rec['name']}” — " + "; ".join(bits)


def blocks_for(rec):
    """The content blocks that carry one upload to the model."""
    head = {"type": "text", "text": describe(rec)}
    path = Path(rec["path"])
    if not path.is_file():
        return [{"type": "text", "text": describe(rec) + " — (the file is gone)"}]
    kind = rec["kind"]
    if kind == "image":
        mt, data = _image_bytes(rec)
        return [head, {"type": "image", "source": {"type": "base64", "media_type": mt,
                                                    "data": base64.b64encode(data).decode()}}]
    if kind == "pdf":
        pages = rec.get("pages")
        if rec["size"] <= PDF_MAX_BYTES and (pages is None or pages <= PDF_MAX_PAGES):
            return [head, {"type": "document", "title": rec["name"][:200],
                           "source": {"type": "base64", "media_type": "application/pdf",
                                      "data": base64.b64encode(path.read_bytes()).decode()}}]
        out = [head]
        part = _pdf_head(path, PDF_MAX_PAGES) if pages else None
        if part and len(part) <= PDF_MAX_BYTES:
            out.append({"type": "document", "title": f"{rec['name'][:150]} (pages 1–{PDF_MAX_PAGES})",
                        "source": {"type": "base64", "media_type": "application/pdf",
                                   "data": base64.b64encode(part).decode()}})
            rest = pdf_text(path, PDF_MAX_PAGES + 1)
            what = f"pages {PDF_MAX_PAGES + 1}–{pages}"
        else:
            rest = pdf_text(path)
            what = "all pages"
        if rest:
            out.append({"type": "document", "title": f"{rec['name'][:150]} — text of {what}",
                        "source": {"type": "text", "media_type": "text/plain", "data": rest}})
        else:
            out.append({"type": "text", "text": "(this PDF is too large to show whole and its text could not be "
                                                "extracted — read_upload reads it page by page)"})
        return out
    if kind == "text" and rec["size"] <= INLINE_TEXT_BYTES:
        try:
            body = path.read_text(errors="replace")
            return [{"type": "text", "text": f"{describe(rec)}\n<file name=\"{rec['file']}\">\n{body}\n</file>"}]
        except OSError:
            pass
    hint = {
        "cad": " — add_component_from_file makes a project component from a .glb/.gltf/.stl",
        "vision_model": " — install_vision_model puts it in the project's vision/ folder",
        "archive": " — read_upload lists what is inside",
        "text": " — read_upload reads it",
    }.get(kind, "")
    return [{"type": "text", "text": describe(rec) + hint}]


def content_blocks(sid, ids):
    out, missing = [], []
    for i in ids or []:
        rec = get(sid, str(i))
        if rec is None:
            missing.append(str(i))
            continue
        try:
            out += blocks_for(rec)
        except UploadError as e:
            out.append({"type": "text", "text": f"{describe(rec)} — could not be shown: {e}"})
    if missing:
        out.append({"type": "text", "text": f"(attachments not found: {', '.join(missing)})"})
    return out


def strip_binary(messages):
    """A copy of the history for the session file: image and PDF data are
    replaced by a short note (the file itself stays in the upload store),
    so a session file never carries megabytes of base64."""
    out = []
    for m in messages:
        c = m.get("content")
        if not isinstance(c, list) or not any(isinstance(b, dict) and b.get("type") in ("image", "document") for b in c):
            out.append(m)
            continue
        nc = []
        for b in c:
            if isinstance(b, dict) and b.get("type") in ("image", "document"):
                nc.append({"type": "text", "text": f"[{b['type']} {b.get('title') or ''} shown here earlier — "
                                                    "the file is in the upload store (list_uploads)]"})
            else:
                nc.append(b)
        out.append({**m, "content": nc})
    return out
