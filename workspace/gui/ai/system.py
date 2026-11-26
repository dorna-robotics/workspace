"""The robot computer itself: any file and folder, and shell commands.

    fs_list / fs_read / fs_search   read anywhere the Workspace's user can (no approval)
    fs_write / fs_edit              inside a project folder as the project tools; anywhere else the user approves first
    fs_move / fs_delete             the user approves first
    run_command                     a shell command (or a script) as the Workspace's user; the user approves first

Secret files (the account's key, .env, private keys …: account/sync.py's
SECRET test) are never read, written or listed with their contents, so they
never reach the model. A command is shown to the user verbatim on its card.
"""
import os
import re
import shutil
import signal
import subprocess
import time
from pathlib import Path

from . import images
from .images import Shot
from . import tools as _t
from .tools import MAX_FILE_BYTES, SKIP_DIRS, ToolError, _read_text, _tool

from gui.account.sync import SECRET

OUT_CHARS = 20_000
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
RUNS_DIR = Path.home() / ".workspace" / "ai_runs"

_PATH = {"type": "string", "description": "An absolute path, or one starting with ~ (the Workspace user's home)."}

TOOLS = [
    _tool("fs_list", "List a folder anywhere on this computer (its files with sizes, and its folders). "
          "depth > 1 lists the subfolders too.",
          {"path": _PATH, "depth": {"type": "integer", "description": "Levels to list (default 1, max 6)."},
           "hidden": {"type": "boolean", "description": "Include dot files and folders."}}, ["path"]),
    _tool("fs_read", "Read a file anywhere on this computer: a text file's lines (offset / limit for a long one), "
          "or a picture (png / jpg …), which you see and the chat shows.",
          {"path": _PATH, "offset": {"type": "integer"}, "limit": {"type": "integer"}}, ["path"]),
    _tool("fs_search", "Search the text files under a folder for a regular expression (glob limits the file "
          "names, e.g. '*.py').",
          {"path": _PATH, "pattern": {"type": "string"}, "glob": {"type": "string"}}, ["path", "pattern"]),
    _tool("fs_write", "Create or replace a file anywhere on this computer (folders are made). Inside a project "
          "folder it is written at once; anywhere else the user approves it in the chat first.",
          {"path": _PATH, "content": {"type": "string"}}, ["path", "content"]),
    _tool("fs_edit", "Replace an exact piece of text in a file anywhere on this computer (old_text unique unless "
          "replace_all). Outside a project folder the user approves it first.",
          {"path": _PATH, "old_text": {"type": "string"}, "new_text": {"type": "string"},
           "replace_all": {"type": "boolean"}}, ["path", "old_text", "new_text"]),
    _tool("fs_move", "Move or rename (or with copy=true copy) a file or folder. The user approves it first.",
          {"path": _PATH, "to": _PATH, "copy": {"type": "boolean"}}, ["path", "to"]),
    _tool("fs_delete", "Delete a file, or a folder with recursive=true. The user approves it first.",
          {"path": _PATH, "recursive": {"type": "boolean"}}, ["path"]),
    _tool("run_command", "Run a shell command (bash) on this computer as the Workspace's user: run a script or "
          "a project file, check the network (ping, ip addr), USB devices (lsusb), disks, services, install a "
          "package … The user approves the exact command in the chat first. Output (stdout + stderr) comes back; "
          "background=true starts a long job and returns its pid and log file (read it with fs_read). Use "
          "'sudo' only where the platform's own instructions say to.",
          {"command": {"type": "string"}, "cwd": _PATH,
           "timeout": {"type": "integer", "description": "Seconds (default 120, max 900); not for background."},
           "background": {"type": "boolean"},
           "reason": {"type": "string", "description": "One line on why, shown on the approval card."}}, ["command"]),
]


# ── paths ────────────────────────────────────────────────────────────

def _path(s):
    s = str(s or "").strip()
    if not s:
        raise ToolError("give a path")
    p = Path(os.path.expanduser(s))
    if not p.is_absolute():
        raise ToolError(f"{s}: give an absolute path (or ~/…)")
    return Path(os.path.abspath(p))


def _secret(p):
    r = p.resolve() if p.exists() else p
    return any(SECRET.search(str(x)) for x in (p, r)) or bool(re.match(r"/proc/[^/]+/environ$", str(r)))


def _open(p):
    """A path the model may touch at all."""
    if _secret(p):
        raise ToolError(f"{p}: a secret file (keys, passwords, the account's key) — not readable or writable from the chat")
    return p


def _project_roots():
    roots = []
    for ws in (_t.ORCH.workspaces.values() if _t.ORCH is not None else []):
        try:
            if ws.is_remote():
                continue
            p = Path(ws.path_to_file)
            roots.append((p.parent if p.suffix else p).resolve())
        except Exception:
            continue
    return roots


def _in_project(p):
    r = p.resolve() if p.exists() else p.parent.resolve() / p.name
    return any(r == b or b in r.parents for b in _project_roots()) and ".git" not in r.parts


def _size(n):
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024 or u == "GB":
            return f"{n:.0f} {u}" if u == "B" else f"{n:.1f} {u}"
        n /= 1024


# ── the tools ────────────────────────────────────────────────────────

def t_fs_list(session, a):
    root = _open(_path(a["path"]))
    if not root.is_dir():
        raise ToolError(f"no such folder: {root}")
    depth, hidden, out = max(1, min(6, int(a.get("depth") or 1))), bool(a.get("hidden")), []

    def walk(d, lvl):
        try:
            kids = sorted(d.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
        except OSError as e:
            out.append(f"{'  ' * lvl}({e.strerror})")
            return
        for k in kids:
            if len(out) >= 500:
                return
            if (k.name.startswith(".") and not hidden) or k.name in SKIP_DIRS:
                continue
            if k.is_dir() and not k.is_symlink():
                out.append(f"{'  ' * lvl}{k.name}/")
                if lvl + 1 < depth:
                    walk(k, lvl + 1)
            else:
                try:
                    sz = _size(k.stat().st_size)
                except OSError:
                    sz = "?"
                out.append(f"{'  ' * lvl}{k.name}  ({sz}{', secret' if _secret(k) else ''})")

    walk(root, 0)
    more = "\n… more not listed — name a subfolder" if len(out) >= 500 else ""
    return f"{root}/\n" + ("\n".join(out) or "(empty)") + more, []


def t_fs_read(session, a):
    p = _open(_path(a["path"]))
    if not p.is_file():
        raise ToolError(f"no such file: {p}")
    if p.suffix.lower() in IMAGE_SUFFIXES:
        try:
            im = images.decode(p.read_bytes())
        except Exception:
            raise ToolError(f"{p} is not a picture this robot can read")
        return [f"{p}: {im.size[0]}x{im.size[1]} px\n" + images.stats_text(images.stats(im), "picture"),
                Shot(images.encode(im), p.name)], []
    try:
        with open(p, "rb") as f:
            head = f.read(4096)
    except OSError as e:
        raise ToolError(f"{p}: {e.strerror}")
    if b"\0" in head:
        raise ToolError(f"{p} is a binary file ({_size(p.stat().st_size)}) — run_command can inspect it (file, xxd …)")
    return _read_text(p, a.get("offset"), a.get("limit")).replace(p.name, str(p), 1), []


def _walk(root):
    for dp, ds, fs in os.walk(root):
        ds[:] = [d for d in ds if d not in SKIP_DIRS and not d.startswith(".")]
        yield from (Path(dp) / f for f in fs)


def t_fs_search(session, a):
    root = _open(_path(a["path"]))
    try:
        rx = re.compile(a["pattern"], re.I)
    except re.error as e:
        raise ToolError(f"bad pattern: {e}")
    glob = a.get("glob") or "*"
    files = [root] if root.is_file() else None
    hits, seen = [], 0
    if files is None:
        if not root.is_dir():
            raise ToolError(f"no such folder: {root}")
        files = _walk(root)
    for f in files:
        if not Path(f.name).match(glob) or _secret(f):
            continue
        seen += 1
        if seen > 20_000:
            hits.append("… stopped after 20000 files — name a smaller folder")
            break
        try:
            if f.stat().st_size > MAX_FILE_BYTES:
                continue
            with open(f, "rb") as fh:
                data = fh.read()
            if b"\0" in data[:4096]:
                continue
            for i, line in enumerate(data.decode(errors="ignore").splitlines(), 1):
                if rx.search(line):
                    hits.append(f"{f}:{i}: {line.strip()[:200]}")
                    if len(hits) >= 100:
                        return "\n".join(hits) + "\n… more matches — narrow the pattern or the folder", []
        except OSError:
            continue
    return "\n".join(hits) or "no matches", []


def _reload(p):
    return [{"type": "reload", "what": "files"}] if _in_project(p) else []


def t_fs_write(session, a):
    p = _open(_path(a["path"]))
    if p.is_dir():
        raise ToolError(f"{p} is a folder")
    existed = p.is_file()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(a["content"])
    except OSError as e:
        raise ToolError(f"{p}: {e.strerror} (a system file may need run_command with sudo)")
    return f"{'replaced' if existed else 'created'} {p} ({len(a['content'])} chars)", _reload(p)


def t_fs_edit(session, a):
    p = _open(_path(a["path"]))
    if not p.is_file():
        raise ToolError(f"no such file: {p}")
    text = p.read_text(errors="replace")
    n = text.count(a["old_text"])
    if n == 0:
        raise ToolError("old_text not found — read the file again and copy the text exactly")
    if n > 1 and not a.get("replace_all"):
        raise ToolError(f"old_text appears {n} times — give more context or set replace_all")
    text = text.replace(a["old_text"], a["new_text"], -1 if a.get("replace_all") else 1)
    try:
        p.write_text(text)
    except OSError as e:
        raise ToolError(f"{p}: {e.strerror}")
    return f"edited {p} ({n if a.get('replace_all') else 1} change(s))", _reload(p)


def t_fs_move(session, a):
    src, dst = _open(_path(a["path"])), _open(_path(a["to"]))
    if not src.exists():
        raise ToolError(f"no such file or folder: {src}")
    if dst.is_dir():
        dst = dst / src.name
    if dst.exists():
        raise ToolError(f"{dst} already exists — delete it first or pick another name")
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        if a.get("copy"):
            (shutil.copytree if src.is_dir() else shutil.copy2)(src, dst)
        else:
            shutil.move(str(src), str(dst))
    except OSError as e:
        raise ToolError(f"{e.strerror or e}")
    return f"{'copied' if a.get('copy') else 'moved'} {src} → {dst}", _reload(dst) or _reload(src)


def t_fs_delete(session, a):
    p = _open(_path(a["path"]))
    if str(p) in ("/", str(Path.home())) or len(p.parts) < 3:
        raise ToolError(f"{p}: not deleting a top-level folder")
    if not p.exists() and not p.is_symlink():
        raise ToolError(f"no such file or folder: {p}")
    try:
        if p.is_dir() and not p.is_symlink():
            if not a.get("recursive"):
                raise ToolError(f"{p} is a folder — set recursive=true to delete it and everything in it")
            shutil.rmtree(p)
        else:
            p.unlink()
    except OSError as e:
        raise ToolError(f"{p}: {e.strerror}")
    return f"deleted {p}", _reload(p)


def _tail(s):
    return s if len(s) <= OUT_CHARS else f"… {len(s) - OUT_CHARS} chars cut …\n" + s[-OUT_CHARS:]


def t_run_command(session, a):
    cwd = _path(a["cwd"]) if a.get("cwd") else Path.home()
    if not cwd.is_dir():
        raise ToolError(f"no such folder: {cwd}")
    if a.get("background"):
        RUNS_DIR.mkdir(parents=True, exist_ok=True)
        log = RUNS_DIR / time.strftime("%Y%m%d_%H%M%S.log")
        with open(log, "wb") as f:
            p = subprocess.Popen(["bash", "-c", a["command"]], cwd=cwd, stdin=subprocess.DEVNULL, stdout=f,
                                 stderr=subprocess.STDOUT, start_new_session=True)
        return f"started in the background: pid {p.pid}, output in {log} (fs_read it; stop with `kill {p.pid}`)", []
    timeout = max(1, min(900, int(a.get("timeout") or 120)))
    p = subprocess.Popen(["bash", "-c", a["command"]], cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, start_new_session=True)
    t0, why = time.monotonic(), None
    while True:
        try:
            out = p.communicate(timeout=0.5)[0]
            break
        except subprocess.TimeoutExpired:
            if session.cancel:
                why = "stopped by the user"
            elif time.monotonic() - t0 > timeout:
                why = f"timed out after {timeout} s"
            if why:
                try:
                    os.killpg(p.pid, signal.SIGTERM)
                except OSError:
                    pass
                out = p.communicate()[0]
                break
    text = _tail(out.decode(errors="replace").rstrip())
    head = f"exit {p.returncode}" + (f" — {why}" if why else "") + f" ({time.monotonic() - t0:.1f} s, in {cwd})"
    return head + ("\n" + text if text else "\n(no output)"), []


HANDLERS = {t["name"]: globals()["t_" + t["name"]] for t in TOOLS}

LABELS = {
    "fs_list": lambda a: f"Listing {a.get('path', '')}",
    "fs_read": lambda a: f"Reading {a.get('path', '')}",
    "fs_search": lambda a: f"Searching {a.get('path', '')} for “{a.get('pattern', '')}”",
    "fs_write": lambda a: f"Writing {a.get('path', '')}",
    "fs_edit": lambda a: f"Editing {a.get('path', '')}",
    "fs_move": lambda a: f"{'Copying' if a.get('copy') else 'Moving'} {a.get('path', '')}",
    "fs_delete": lambda a: f"Deleting {a.get('path', '')}",
    "run_command": lambda a: f"Running {a.get('command', '')[:60]}",
}


def approval(name, a, session):
    """The card text for a call the user approves first, or None."""
    try:
        if name in ("fs_write", "fs_edit"):
            p = _path(a["path"])
            if _secret(p) or _in_project(p):
                return None              # refused on its own / a project file, as the project tools
            verb = "Edit" if name == "fs_edit" else ("Replace" if p.is_file() else "Create")
            return f"{verb} {p} (outside the projects)."
        if name == "fs_move":
            return f"{'Copy' if a.get('copy') else 'Move'} {_path(a['path'])} → {_path(a['to'])}."
        if name == "fs_delete":
            p = _path(a["path"])
            return f"Delete {p}{' and everything in it' if p.is_dir() else ''} — this can't be undone."
    except ToolError:
        return None
    if name == "run_command":
        why = f" Why: {a['reason'].strip()}" if (a.get("reason") or "").strip() else ""
        where = f" in {a['cwd']}" if a.get("cwd") else ""
        bg = " (in the background)" if a.get("background") else ""
        return f"Run{bg}{where}: {a['command']}{why}"
    return None
