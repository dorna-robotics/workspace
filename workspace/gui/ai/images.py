"""Pictures in the assistant's tool results — what the model sees and the
user sees beside it.

* ``Shot`` — one picture a tool shows: JPEG bytes (≤ MAX_SIDE px on the
  long side) and a caption. A tool returns it in its content list; the
  agent turns it into an image block for the model and a ``t: image``
  event for the chat, and keeps the JPEG in the session's shot store
  (``~/.workspace/ai/shots/<session>/<id>.jpg``, served by /ai/api/shot).
* ``stats`` — objective numbers about a frame (exposure, clipping, color
  cast, sharpness), so the model never has to guess them from the picture.
* ``outline`` / ``crop`` / ``grid`` — drawing on a frame (an ROI, a pixel
  ruler) for a preview.
* ``prune`` — only the last KEEP_IMAGES tool images stay in the history the
  model is sent; older ones become a one-line stub.
"""
import base64
import io
import os
import re
import uuid

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from gui.data_home import data_path

MAX_SIDE = 1280          # the long side of a picture the model (and the chat) gets
QUALITY = 82
KEEP_IMAGES = 4          # tool images kept in the history sent to the model
MAX_SHOTS = 80           # per session on disk; the oldest go first


class Shot:
    """A picture for the model and the user: ``jpeg`` already downscaled."""

    def __init__(self, jpeg, caption=""):
        self.jpeg = jpeg
        self.caption = caption


def decode(jpeg):
    """JPEG bytes → an RGB PIL image."""
    im = Image.open(io.BytesIO(jpeg))
    return im.convert("RGB")


def encode(im, max_side=MAX_SIDE, quality=QUALITY):
    """A PIL image → JPEG bytes, downscaled so its long side is ≤ max_side."""
    im = im.convert("RGB")
    w, h = im.size
    s = max(w, h)
    if s > max_side:
        k = max_side / s
        im = im.resize((max(1, round(w * k)), max(1, round(h * k))), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=quality)
    return buf.getvalue()


def shot(im, caption=""):
    return Shot(encode(im), caption)


def block(jpeg):
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                         "data": base64.b64encode(jpeg).decode()}}


# ── stats ────────────────────────────────────────────────────────────

def stats(im, box=None):
    """Objective numbers about a frame (or the box [x0, y0, x1, y1] of it):
    luma mean and percentiles, clipped highlights / shadows, channel means
    and the cast they imply, a sharpness estimate. Plain dict."""
    if box:
        im = im.crop(tuple(int(v) for v in box))
    w, h = im.size
    if max(w, h) > MAX_SIDE:       # the same scale for every frame: sharpness compares
        k = MAX_SIDE / max(w, h)
        im = im.resize((max(1, round(w * k)), max(1, round(h * k))), Image.BILINEAR)
    a = np.asarray(im, dtype=np.float32)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    y = 0.299 * r + 0.587 * g + 0.114 * b
    p = np.percentile(y, [1, 5, 50, 95, 99])
    clip_hi = float(np.mean(np.max(a, axis=2) >= 250) * 100)
    clip_lo = float(np.mean(np.min(a, axis=2) <= 5) * 100)
    means = [float(r.mean()), float(g.mean()), float(b.mean())]
    gray = sum(means) / 3 or 1.0
    # the gray-world cast, on the pixels that are neither clipped nor black
    mid = (y > 20) & (y < 235)
    if mid.sum() > 100:
        cm = [float(c[mid].mean()) for c in (r, g, b)]
    else:
        cm = means
    cg = sum(cm) / 3 or 1.0
    ratio = [c / cg for c in cm]
    names = ("red", "green", "blue")
    hi = int(np.argmax(ratio))
    lo = int(np.argmin(ratio))
    spread = ratio[hi] - ratio[lo]
    if spread < 0.08:
        cast = "neutral"
    else:
        opposite = {"red": "cyan", "green": "magenta", "blue": "yellow"}
        cast = f"{names[hi]} cast" if ratio[hi] - 1 >= 1 - ratio[lo] else f"{opposite[names[lo]]} cast"
        cast += f" ({'strong' if spread > 0.2 else 'mild'})"
    lap = y[:-2, 1:-1] + y[2:, 1:-1] + y[1:-1, :-2] + y[1:-1, 2:] - 4 * y[1:-1, 1:-1]
    sharp = float(lap.var()) if lap.size else 0.0
    mean = float(y.mean())
    verdict = []
    if clip_hi > 2 or p[2] > 200:
        verdict.append("overexposed")
    elif mean < 50 or clip_lo > 25:
        verdict.append("underexposed")
    else:
        verdict.append("exposure OK")
    verdict.append("white balance off: " + cast if cast != "neutral" else "white balance neutral")
    return {
        "size": [w, h] if not box else [int(box[2] - box[0]), int(box[3] - box[1])],
        "luma_mean": round(mean, 1),
        "luma_p1_p5_p50_p95_p99": [round(float(v), 1) for v in p],
        "clipped_highlights_pct": round(clip_hi, 2),
        "clipped_shadows_pct": round(clip_lo, 2),
        "rgb_means": [round(v, 1) for v in means],
        "rgb_balance": [round(v, 3) for v in ratio],
        "cast": cast,
        "sharpness_laplacian_var": round(sharp, 1),
        "verdict": "; ".join(verdict),
    }


def stats_text(st, what="frame"):
    return (f"{what} stats: {st['verdict']}. luma mean {st['luma_mean']} (p1/p5/p50/p95/p99 "
            f"{st['luma_p1_p5_p50_p95_p99']}), clipped highlights {st['clipped_highlights_pct']}% / "
            f"shadows {st['clipped_shadows_pct']}%, RGB means {st['rgb_means']} (balance R/G/B "
            f"{st['rgb_balance']}: {st['cast']}), sharpness (Laplacian variance at ≤{MAX_SIDE}px) "
            f"{st['sharpness_laplacian_var']}")


# ── drawing ──────────────────────────────────────────────────────────

def _font(px):
    try:
        return ImageFont.truetype("DejaVuSans-Bold.ttf", px)
    except OSError:
        return ImageFont.load_default()


def grid(im, step=None):
    """A copy with a pixel ruler: lines every ``step`` frame pixels, labelled
    in FRAME pixels (so coordinates read off the picture are frame pixels,
    whatever it is shown at)."""
    im = im.copy()
    w, h = im.size
    step = step or (100 if max(w, h) <= 1600 else 200)
    d = ImageDraw.Draw(im, "RGBA")
    f = _font(max(12, max(w, h) // 90))
    lw = max(1, max(w, h) // 1000)
    for x in range(step, w, step):
        d.line([(x, 0), (x, h)], fill=(255, 255, 0, 110), width=lw)
        d.text((x + 3, 3), str(x), fill=(255, 255, 0, 230), font=f, stroke_width=2, stroke_fill=(0, 0, 0, 200))
    for y in range(step, h, step):
        d.line([(0, y), (w, y)], fill=(255, 255, 0, 110), width=lw)
        d.text((3, y + 3), str(y), fill=(255, 255, 0, 230), font=f, stroke_width=2, stroke_fill=(0, 0, 0, 200))
    return im


def outline(im, corners, label=None, inv=False):
    """A copy with the polygon ``corners`` drawn (outside dimmed, or inside
    for an inverted ROI) and its bounding box size labelled."""
    im = im.convert("RGB").copy()
    w, h = im.size
    pts = [(float(x), float(y)) for x, y in corners]
    mask = Image.new("L", im.size, 0)
    ImageDraw.Draw(mask).polygon(pts, fill=255)
    if inv:
        mask = Image.eval(mask, lambda v: 255 - v)
    dim = Image.blend(im, Image.new("RGB", im.size, (0, 0, 0)), 0.55)
    im = Image.composite(im, dim, mask)
    d = ImageDraw.Draw(im)
    lw = max(2, max(w, h) // 400)
    d.line(pts + [pts[0]], fill=(0, 255, 120), width=lw, joint="curve")
    for x, y in pts:
        r = lw * 2
        d.ellipse([x - r, y - r, x + r, y + r], fill=(0, 255, 120))
    if label:
        x0, y0 = min(p[0] for p in pts), min(p[1] for p in pts)
        d.text((x0 + 4, max(0, y0 - max(14, h // 45) - 6)), label, fill=(0, 255, 120),
               font=_font(max(14, max(w, h) // 70)), stroke_width=2, stroke_fill=(0, 0, 0))
    return im


def bbox(corners, size):
    xs = [float(p[0]) for p in corners]
    ys = [float(p[1]) for p in corners]
    w, h = size
    return [max(0, int(min(xs))), max(0, int(min(ys))), min(w, int(round(max(xs)))), min(h, int(round(max(ys))))]


def corners_of(roi):
    """The pixel polygon of an ROI given as corners (≥ 3 points, or 2 =
    opposite corners of a rectangle) — a list of [x, y]."""
    pts = [[float(v) for v in p] for p in roi]
    if len(pts) == 2:
        (x0, y0), (x1, y1) = pts
        pts = [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]
    return pts


# ── the shot store ───────────────────────────────────────────────────

_SID = re.compile(r"^[0-9a-f]{1,32}$")


def _dir(sid):
    if not _SID.match(sid or ""):
        raise ValueError("bad session id")
    return os.path.dirname(data_path("ai", "shots", sid, "x"))


def save(sid, jpeg):
    """Keep a shot for the chat; its id. The oldest go past MAX_SHOTS."""
    d = _dir(sid)
    os.makedirs(d, exist_ok=True)
    sh = uuid.uuid4().hex[:12]
    tmp = os.path.join(d, sh + ".tmp")
    with open(tmp, "wb") as f:
        f.write(jpeg)
    os.replace(tmp, os.path.join(d, sh + ".jpg"))
    try:
        files = sorted((os.path.join(d, n) for n in os.listdir(d) if n.endswith(".jpg")), key=os.path.getmtime)
        for p in files[:-MAX_SHOTS]:
            os.unlink(p)
    except OSError:
        pass
    return sh


def path(sid, sh):
    if not re.fullmatch(r"[0-9a-f]{12}", sh or ""):
        return None
    try:
        p = os.path.join(_dir(sid), sh + ".jpg")
    except ValueError:
        return None
    return p if os.path.isfile(p) else None


# ── the history ──────────────────────────────────────────────────────

STUB = "[a picture shown here earlier — dropped from the history; look again (grab it again) to see it]"


def _tool_images(messages):
    """(message index, block index, inner index) of every image inside a
    tool result, oldest first."""
    out = []
    for i, m in enumerate(messages):
        c = m.get("content")
        if m.get("role") != "user" or not isinstance(c, list):
            continue
        for j, b in enumerate(c):
            if isinstance(b, dict) and b.get("type") == "tool_result" and isinstance(b.get("content"), list):
                for k, x in enumerate(b["content"]):
                    if isinstance(x, dict) and x.get("type") == "image":
                        out.append((i, j, k))
    return out


def prune(messages, keep=KEEP_IMAGES):
    """Replace all but the last ``keep`` tool-result images with a text stub
    (in place). Returns how many were dropped."""
    found = _tool_images(messages)
    drop = found[:-keep] if keep else found
    for i, j, k in drop:
        messages[i]["content"][j]["content"][k] = {"type": "text", "text": STUB}
    return len(drop)
