"""Project management for the main page — new, import, copy, rename,
delete, thumbnails.

A project is a registry card (``Orchestrator.workspaces[name]``) plus
its FOLDER, the parent of ``path_to_file`` (main.py). New and imported
projects land in the projects folder — ``~/Downloads/projects`` on the
bench, the convention every real project follows (CLAUDE.md), or
``WORKSPACE_PROJECTS_DIR``. A folder is only ever moved or renamed
when it is the project's OWN: not inside the platform's repository
(examples/*) and not holding or held by another registered project
(a repository with several projects, e.g. bna/_bna). Otherwise only
the card changes, and the answer says why.

Delete never removes files: the folder moves to
``~/.workspace/trash/<name>-<stamp>``.

Everything the server makes is handed to the invoking user (SUDO_UID)
— the GUI runs as root, the projects belong to the operator.
"""
from __future__ import annotations

import base64
import os
import re
import shutil
import stat
import subprocess
import tempfile
import threading
import time
import zipfile
from concurrent.futures import Future
from typing import Dict, List, Optional

from gui.data_home import data_path, _invoker

GUI_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WS_ROOT = os.path.dirname(GUI_DIR)              # workspace/
REPO_ROOT = os.path.dirname(WS_ROOT)            # the platform repository
SEED_DIR = os.path.join(REPO_ROOT, "examples", "base")
CHASSIS_DIR = os.path.join(REPO_ROOT, "scenes", "core")
CHASSIS = {"core_500": "Core 500", "core_1000": "Core 1000"}

THUMB_REL = os.path.join(".workspace", "thumb.png")
THUMB_MAX_BYTES = 4 * 1024 * 1024

# What a copy leaves behind: the run's output and the console, not the project.
COPY_SKIP = {"log", "records", "replays", "captures", "_uploads", "__pycache__", ".git"}

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


class ProjectError(ValueError):
    """A refusal the page shows as is."""


# ---------------------------------------------------------------- registry access
#
# The handlers run these functions in an executor thread (git clone, copies
# and moves block). The registry (orch.workspaces) belongs to the IOLoop —
# its status and websocket code iterate it — so every registry change is
# handed to the loop (on_loop), and one lock covers each whole mutation, so
# two requests never pick the same name, folder or port.

LOCK = threading.RLock()
_LOOP = None


def locked(fn):
    """One mutation at a time: name, folder and port are picked and taken
    under LOCK."""
    import functools

    @functools.wraps(fn)
    def run(*a, **kw):
        with LOCK:
            return fn(*a, **kw)
    return run


def set_loop(loop) -> None:
    global _LOOP
    _LOOP = loop


def on_loop(fn, *args, **kwargs):
    """``fn(*args)`` on the IOLoop, waited for from an executor thread."""
    loop = _LOOP
    if loop is None or loop.is_closed():
        return fn(*args, **kwargs)
    try:
        import asyncio
        if asyncio.get_running_loop() is loop:
            return fn(*args, **kwargs)
    except RuntimeError:
        pass
    fut: Future = Future()

    def call():
        try:
            fut.set_result(fn(*args, **kwargs))
        except BaseException as e:   # handed back to the caller's thread
            fut.set_exception(e)
    loop.call_soon_threadsafe(call)
    return fut.result(timeout=60)


# ---------------------------------------------------------------- folders

def projects_dir() -> str:
    env = os.environ.get("WORKSPACE_PROJECTS_DIR", "").strip()
    if env:
        path = os.path.expanduser(env)
    else:
        _, pw = _invoker()
        home = pw.pw_dir if pw else os.path.expanduser("~")
        path = os.path.join(home, "Downloads", "projects")
    os.makedirs(path, exist_ok=True)
    give_back(path, recursive=False)
    return path


def give_back(path: str, recursive: bool = True) -> None:
    """Hand ``path`` (and its tree) to the user who ran sudo. Best effort."""
    if os.geteuid() != 0:
        return
    uid, gid = os.environ.get("SUDO_UID"), os.environ.get("SUDO_GID")
    if uid is None:
        return
    uid, gid = int(uid), int(gid) if gid is not None else -1
    try:
        os.lchown(path, uid, gid)
        if recursive and os.path.isdir(path) and not os.path.islink(path):
            for root, dirs, files in os.walk(path):
                for n in dirs + files:
                    os.lchown(os.path.join(root, n), uid, gid)
    except OSError:
        pass


def check_name(name: str) -> str:
    name = (name or "").strip()
    if not NAME_RE.match(name):
        raise ProjectError("A project name is letters, digits, - _ . (up to 64), "
                           "starting with a letter or digit.")
    return name


def project_dir(ws) -> str:
    d = os.path.dirname(ws.path_to_file or "")
    if not d:
        raise ProjectError("This project has no folder on this machine.")
    return os.path.realpath(d)


def _inside(child: str, parent: str) -> bool:
    child, parent = os.path.realpath(child), os.path.realpath(parent)
    return child == parent or child.startswith(parent + os.sep)


def folder_owned(orch, name: str) -> Optional[str]:
    """None when the project's folder is its own to move, else why not.

    Only a folder directly in the projects folder is ever moved — what New,
    Import (GitHub, zip) and Copy make. A project registered where it is
    (Import → path, or a card of a larger repository like bna/_bna) stays
    put: delete and rename change only the card."""
    ws = orch.workspaces[name]
    if ws.is_remote():
        return "a remote project"
    d = project_dir(ws)
    if _inside(d, REPO_ROOT):
        return "it is part of the Workspace installation"
    if os.path.dirname(d) != os.path.realpath(projects_dir()):
        return "it is outside the projects folder (registered where it is)"
    for other, ows in orch.workspaces.items():
        if other == name or ows.is_remote() or not ows.path_to_file:
            continue
        od = os.path.realpath(os.path.dirname(ows.path_to_file))
        if _inside(od, d) or _inside(d, od):
            return f"its folder is shared with {other}"
    return None


def unique_name(orch, base: str) -> str:
    base = re.sub(r"[^A-Za-z0-9_.-]+", "_", base).strip("._-") or "project"
    base = base[:56]
    root = projects_dir()
    name, i = base, 2
    while name in orch.workspaces or os.path.exists(os.path.join(root, name)):
        name, i = f"{base}_{i}", i + 1
    return name


def _free_target(orch, name: str) -> str:
    if name in orch.workspaces:
        raise ProjectError(f"A project named {name} already exists.")
    target = os.path.join(projects_dir(), name)
    if os.path.exists(target):
        raise ProjectError(f"{target} already exists.")
    return target


def _refuse_launched(orch, name: str, verb: str) -> None:
    if name not in orch.workspaces:
        raise ProjectError(f"Unknown project: {name}")
    if orch.is_launched(name):
        raise ProjectError(f"Kill {name} before you {verb} it.")


# ---------------------------------------------------------------- listing

def _chassis_of(d: str) -> str:
    try:
        import yaml
        with open(os.path.join(d, "launch.yaml")) as f:
            scene = (yaml.safe_load(f) or {}).get("scene") or []
        for s in scene if isinstance(scene, list) else [scene]:
            stem = os.path.splitext(os.path.basename(str(s)))[0]
            if stem.startswith("core_"):
                return stem
    except Exception:
        pass
    return ""


def listing(orch) -> Dict:
    out = []
    for name, ws in orch.workspaces.items():
        row = ws.to_dict()
        thumb_t, chassis, folder = 0, "", ""
        if not ws.is_remote() and ws.path_to_file:
            folder = os.path.dirname(ws.path_to_file)
            t = os.path.join(folder, THUMB_REL)
            try:
                lt = os.lstat(t)
                ok = stat.S_ISREG(lt.st_mode) and not os.path.islink(os.path.dirname(t))
                thumb_t = int(lt.st_mtime) if ok else 0
            except OSError:
                thumb_t = 0
            chassis = _chassis_of(folder)
        row.update(dir=folder, thumb=thumb_t, chassis=chassis,
                   git=bool(folder) and os.path.isdir(os.path.join(folder, ".git")))
        out.append(row)
    return {"projects": out, "projects_dir": projects_dir(), "chassis": CHASSIS}


# ---------------------------------------------------------------- thumbnails

def thumb_path(orch, name: str) -> str:
    """``<project>/.workspace/thumb.png`` — refused when ``.workspace`` is a
    symbolic link (a cloned repository can carry one pointing anywhere).
    The file itself is opened with O_NOFOLLOW (read_thumb / save_thumb)."""
    if name not in orch.workspaces:
        raise ProjectError(f"Unknown project: {name}")
    d = project_dir(orch.workspaces[name])
    meta = os.path.join(d, ".workspace")
    try:
        st = os.lstat(meta)
        if not stat.S_ISDIR(st.st_mode):
            raise ProjectError("The project's .workspace is not a folder.")
    except FileNotFoundError:
        pass
    return os.path.join(meta, "thumb.png")


def read_thumb(orch, name: str) -> Optional[bytes]:
    """The PNG bytes, or None when there is no (regular, unlinked) thumb."""
    path = thumb_path(orch, name)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None                        # missing, or a symbolic link (ELOOP)
    with os.fdopen(fd, "rb") as f:
        if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
            return None
        data = f.read(THUMB_MAX_BYTES + 1)
    return data if len(data) <= THUMB_MAX_BYTES else None


def save_thumb(orch, name: str, data_url: str) -> Dict:
    m = re.match(r"^data:image/png;base64,(.+)$", (data_url or "").strip(), re.S)
    if not m:
        raise ProjectError("Expected a PNG data URL (data:image/png;base64,…).")
    try:
        png = base64.b64decode(m.group(1), validate=False)
    except Exception:
        raise ProjectError("The image is not valid base64.")
    if not png.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ProjectError("The image is not a PNG.")
    if len(png) > THUMB_MAX_BYTES:
        raise ProjectError("The image is larger than 4 MB.")
    path = thumb_path(orch, name)
    meta = os.path.dirname(path)
    try:
        os.mkdir(meta)
    except FileExistsError:
        pass
    if not stat.S_ISDIR(os.lstat(meta).st_mode):
        raise ProjectError("The project's .workspace is not a folder.")
    try:
        if not stat.S_ISREG(os.lstat(path).st_mode):
            raise ProjectError("The project's thumb.png is not a plain file.")
    except FileNotFoundError:
        pass
    # A fresh temp file (O_EXCL never follows or reuses a planted link).
    fd, tmp = tempfile.mkstemp(prefix=".thumb-", suffix=".png", dir=meta)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(png)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)            # replaces the name, never writes through it
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    give_back(meta)
    return {"status": "ok", "thumb": int(os.lstat(path).st_mtime)}


# ---------------------------------------------------------------- register

def _register(orch, name: str, main_py: str, label: str = "") -> Dict:
    def add():
        port = orch.free_port()
        orch.add_workspace(name, main_py, port, label=label, sync_remote=False, persist=True)
        return port
    port = on_loop(add)
    return {"name": name, "path_to_file": main_py, "port": port}


def find_projects(root: str, depth: int = 2) -> List[str]:
    """Folders under ``root`` (itself first) holding main.py + launch.yaml."""
    found = []
    for cur, dirs, files in os.walk(root):
        rel = os.path.relpath(cur, root)
        level = 0 if rel == "." else rel.count(os.sep) + 1
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in COPY_SKIP)
        if "main.py" in files and "launch.yaml" in files:
            found.append(cur)
            dirs[:] = []                  # a project's subfolders are its own
            continue
        if level >= depth:
            dirs[:] = []
    return found


def _register_found(orch, root: str, base: str) -> List[Dict]:
    found = find_projects(root)
    if not found:
        raise ProjectError("No project found — a project folder holds main.py and launch.yaml.")
    added = []
    for d in found:
        nm = base if d == root else unique_name(orch, os.path.basename(d))
        if nm in orch.workspaces:
            nm = unique_name(orch, nm)
        added.append(_register(orch, nm, os.path.join(d, "main.py")))
    return added


# ---------------------------------------------------------------- new

def _set_launch_key(text: str, key: str, value: str) -> str:
    pat = re.compile(rf"^({re.escape(key)}:\s*)(\S.*?)(\s*#.*)?$", re.M)
    if pat.search(text):
        return pat.sub(lambda m: f"{m.group(1)}{value}{m.group(3) or ''}", text, count=1)
    return text + f"\n{key}: {value}\n"


@locked
def new_project(orch, chassis: str, name: str = "") -> Dict:
    if chassis not in CHASSIS:
        raise ProjectError(f"Choose one of: {', '.join(CHASSIS.values())}.")
    src_chassis = os.path.join(CHASSIS_DIR, f"{chassis}.j2")
    if not os.path.isdir(SEED_DIR) or not os.path.isfile(src_chassis):
        raise ProjectError("The project seed is missing from this installation.")
    name = check_name(name) if name else unique_name(orch, chassis.replace("_", ""))
    target = _free_target(orch, name)
    shutil.copytree(SEED_DIR, target, ignore=shutil.ignore_patterns(*COPY_SKIP))
    try:
        scene_dir = os.path.join(target, "scene")
        for f in os.listdir(scene_dir):
            if f.startswith("core_") and f.endswith(".j2"):
                os.remove(os.path.join(scene_dir, f))
        shutil.copyfile(src_chassis, os.path.join(scene_dir, f"{chassis}.j2"))
        lp = os.path.join(target, "launch.yaml")
        with open(lp) as f:
            text = f.read()
        text = _set_launch_key(text, "project_name", name)
        text = _set_launch_key(text, "scene", f"[scene/{chassis}.j2, scene/layout.j2]")
        with open(lp, "w") as f:
            f.write(text)
        layout = os.path.join(scene_dir, "layout.j2")
        with open(layout) as f:
            text = f.read()
        with open(layout, "w") as f:
            f.write(text.replace("base — layout on the shared core_500 chassis.",
                                 f"{name} — layout on the shared {chassis} chassis."))
        give_back(target)
        out = _register(orch, name, os.path.join(target, "main.py"), label=CHASSIS[chassis])
    except Exception:
        shutil.rmtree(target, ignore_errors=True)
        raise
    return {"status": "ok", "added": [out]}


# ---------------------------------------------------------------- import

GIT_URL_RE = re.compile(r"^(https://[\w.-]+/[\w.~/-]+?|git@[\w.-]+:[\w.~/-]+?)(\.git)?/?$")


@locked
def import_git(orch, url: str, name: str = "") -> Dict:
    url = (url or "").strip()
    if not GIT_URL_RE.match(url):
        raise ProjectError("Paste a repository link, e.g. https://github.com/<owner>/<repo>.")
    base = re.sub(r"\.git/?$", "", url.rstrip("/")).rsplit("/", 1)[-1].rsplit(":", 1)[-1]
    name = check_name(name) if name else unique_name(orch, base)
    target = _free_target(orch, name)
    env = ["GIT_TERMINAL_PROMPT=0", "GIT_SSH_COMMAND=ssh -o BatchMode=yes -o ConnectTimeout=10"]
    user, _ = _invoker()
    cmd = ["env", *env, "git", "clone", "--", url, target]
    if os.geteuid() == 0 and user:
        cmd = ["sudo", "-u", user, "-H", *cmd]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        shutil.rmtree(target, ignore_errors=True)
        raise ProjectError("git clone took longer than 5 minutes.")
    if p.returncode != 0:
        shutil.rmtree(target, ignore_errors=True)
        msg = (p.stderr or p.stdout).strip().splitlines()
        raise ProjectError("git clone failed: " + (msg[-1] if msg else f"exit {p.returncode}"))
    try:
        return {"status": "ok", "added": _register_found(orch, target, name)}
    except ProjectError:
        shutil.rmtree(target, ignore_errors=True)
        raise


@locked
def import_zip(orch, filename: str, body: bytes, name: str = "") -> Dict:
    root = projects_dir()
    stage = tempfile.mkdtemp(prefix=".import-", dir=root)
    try:
        zpath = os.path.join(stage, "upload.zip")
        with open(zpath, "wb") as f:
            f.write(body)
        try:
            zf = zipfile.ZipFile(zpath)
        except zipfile.BadZipFile:
            raise ProjectError("That file is not a zip archive.")
        out_dir = os.path.join(stage, "x")
        with zf:
            for info in zf.infolist():
                n = info.filename
                if n.startswith("/") or ".." in n.split("/") or "\\" in n:
                    raise ProjectError(f"Refused: the zip holds an unsafe path ({n}).")
                if (info.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ProjectError(f"Refused: the zip holds a symbolic link ({n}).")
                if n.startswith("__MACOSX/"):
                    continue
                zf.extract(info, out_dir)
        top = [e for e in os.listdir(out_dir) if not e.startswith(".")] if os.path.isdir(out_dir) else []
        src = os.path.join(out_dir, top[0]) if len(top) == 1 and os.path.isdir(os.path.join(out_dir, top[0])) else out_dir
        if not find_projects(src):
            raise ProjectError("No project in the zip — a project folder holds main.py and launch.yaml.")
        stem = os.path.basename(src) if src != out_dir else os.path.splitext(os.path.basename(filename or "project"))[0]
        name = check_name(name) if name else unique_name(orch, stem)
        target = _free_target(orch, name)
        shutil.move(src, target)
        give_back(target)
        try:
            return {"status": "ok", "added": _register_found(orch, target, name)}
        except Exception:
            shutil.rmtree(target, ignore_errors=True)
            raise
    finally:
        shutil.rmtree(stage, ignore_errors=True)


@locked
def import_path(orch, path: str, name: str = "") -> Dict:
    path = os.path.realpath(os.path.expanduser((path or "").strip()))
    if os.path.isfile(path):
        path = os.path.dirname(path)
    if not os.path.isdir(path):
        raise ProjectError(f"No folder at {path}.")
    if not os.path.isfile(os.path.join(path, "main.py")):
        found = find_projects(path)
        if len(found) != 1:
            raise ProjectError("No single project there — point at the folder holding main.py and launch.yaml."
                               if not found else
                               f"{len(found)} projects there — point at one of: "
                               + ", ".join(os.path.relpath(d, path) for d in found))
        path = found[0]
    for other, ws in orch.workspaces.items():
        if ws.path_to_file and os.path.realpath(ws.path_to_file) == os.path.join(path, "main.py"):
            raise ProjectError(f"That project is already on the page as {other}.")
    name = check_name(name) if name else unique_name(orch, os.path.basename(path))
    if name in orch.workspaces:
        raise ProjectError(f"A project named {name} already exists.")
    return {"status": "ok", "added": [_register(orch, name, os.path.join(path, "main.py"))]}


# ---------------------------------------------------------------- copy / rename / delete

@locked
def copy_project(orch, name: str, new: str = "") -> Dict:
    if name not in orch.workspaces:
        raise ProjectError(f"Unknown project: {name}")
    ws = orch.workspaces[name]
    if ws.is_remote():
        raise ProjectError("A remote project is copied on its own node.")
    src = project_dir(ws)
    new = check_name(new) if new else unique_name(orch, f"{name}_copy")
    target = _free_target(orch, new)
    shutil.copytree(src, target, symlinks=True, ignore=shutil.ignore_patterns(*COPY_SKIP))
    give_back(target)
    rel = os.path.relpath(os.path.realpath(ws.path_to_file), src)
    try:
        out = _register(orch, new, os.path.join(target, rel), label=ws.label)
    except Exception:
        shutil.rmtree(target, ignore_errors=True)
        raise
    return {"status": "ok", "added": [out]}


@locked
def rename_project(orch, name: str, new: str) -> Dict:
    new = check_name(new)
    if new == name:
        return {"status": "ok", "name": name, "folder_renamed": False}
    _refuse_launched(orch, name, "rename")
    if new in orch.workspaces:
        raise ProjectError(f"A project named {new} already exists.")
    ws = orch.workspaces[name]
    why = folder_owned(orch, name)
    moved_from = moved_to = None
    main_py = ws.path_to_file
    if why is None:
        d = project_dir(ws)
        if os.path.basename(d) == name:
            dest = os.path.join(os.path.dirname(d), new)
            if os.path.exists(dest):
                raise ProjectError(f"{dest} already exists.")
            os.rename(d, dest)
            moved_from, moved_to = d, dest
            main_py = os.path.join(dest, os.path.relpath(os.path.realpath(ws.path_to_file), d))
        else:
            why = "its folder is named differently from the project"
    try:
        on_loop(orch.rename_workspace, name, new, main_py)
    except Exception:
        if moved_to:
            os.rename(moved_to, moved_from)
        raise
    return {"status": "ok", "name": new, "folder_renamed": bool(moved_to),
            "path_to_file": main_py, "folder_kept_why": "" if moved_to else (why or "")}


@locked
def delete_project(orch, name: str, confirm: str) -> Dict:
    if (confirm or "").strip() != name:
        raise ProjectError("Type the project's name to delete it.")
    _refuse_launched(orch, name, "delete")
    ws = orch.workspaces[name]
    why = folder_owned(orch, name)
    trashed = ""
    if why is None:
        d = project_dir(ws)
        if os.path.isdir(d):
            trash = data_path("trash")
            os.makedirs(trash, exist_ok=True)
            trashed = os.path.join(trash, f"{name}-{time.strftime('%Y%m%d-%H%M%S')}")
            shutil.move(d, trashed)
    try:
        on_loop(orch.remove_workspace, name)
    except Exception:
        if trashed:
            shutil.move(trashed, project_dir(ws))
        raise
    return {"status": "ok", "removed": name, "trashed_to": trashed,
            "folder_kept_why": "" if trashed else (why or "")}
