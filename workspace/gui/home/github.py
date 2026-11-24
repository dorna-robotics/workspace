"""A project's GitHub on this robot — as on the AI builder website
(builder/github_sync.py + versions.py there): one private repository per
project in Dorna's organization, ``<account>_<project>``, which the website's
project of the same name shares.

The account server holds the GitHub App; for each push or fetch this robot
asks it (``POST /s/device/api/github/token``, the account's bearer token) for
a short-lived token that reaches that one repository only. The token goes to
git as an ``http.extraHeader`` through the process environment
(``GIT_CONFIG_COUNT``) — never on a command line, never in ``.git/config`` —
and is scrubbed from every message.

    state(dir, name, fetch)      {git, changes, ahead, behind, url, versions, ...}
    draft(dir)                   what changed, in words — the Save version text
    save(dir, message, email)    everything as one commit (git init + .gitignore
                                 for a project with no history yet)
    push(dir, name)              the saved versions (HEAD) to the repository's main
    pull(dir, name, email)       its main fetched and merged — or, when both sides
                                 changed the same lines, nothing changed and the
                                 files named

The project's own remotes (origin — where it was imported from) are never
touched: the repository is reached by URL and fetched into
``refs/remotes/dorna/main``. Everything here blocks — the handlers run it in
an executor.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import threading
from typing import Dict, List, Optional

from gui.orchestrator.handlers import AuthedHandler, _run_state, _status_pool
from gui.orchestrator.project_git import GitError, _commit, _git

REF = "refs/remotes/dorna/main"
NET_TIMEOUT_S = 120
TOKEN_TIMEOUT_S = 30

# what a run writes, a machine keeps, or the builder derives — never versioned
# (the AI builder's own list, so a project keeps one rule on both sides)
IGNORE = """# what a run writes, a machine keeps, or the builder derives (the AI builder's version history)
__pycache__/
*.pyc
*.log
.ipynb_checkpoints/
log/
core/
rec/
results/
records/
replays/
captures/
counts/
uploads/
scene/bench.j2
*.key
.ai/*
!.ai/requirements.json
!.ai/isolate.json
.workspace/
"""

_LOCKS: Dict[str, threading.Lock] = {}
_URLS: Dict[str, str] = {}          # project → its repository's page (shown before a check)


def _lock(d: str) -> threading.Lock:
    return _LOCKS.setdefault(os.path.realpath(d), threading.Lock())


# ── the account server's token ──────────────────────────────────────────
def _repo(name: str) -> dict:
    """{url, remote, token, expires_at} for this project's repository."""
    from gui.account import account
    from gui.account.client import _http
    tok = account.token()
    if not tok:
        raise GitError("Log in to use GitHub.")
    try:
        code, j = _http("POST", "/s/device/api/github/token", {"project": name}, token=tok,
                        timeout=TOKEN_TIMEOUT_S)
    except OSError:
        raise GitError("The Dorna account server can't be reached — check this robot's network.")
    if code == 401:
        account.invalidate()
        raise GitError("This robot isn't logged in any more — log in again.")
    if code != 200 or not isinstance(j, dict) or not j.get("token"):
        raise GitError((j or {}).get("error") if isinstance(j, dict) else f"the account server answered {code}")
    _URLS[name] = j.get("url") or ""
    return j


class _Auth:
    """The token as git's extra header, and the scrubber for what git says."""

    def __init__(self, repo: dict):
        self.remote = repo["remote"]
        self._tok = repo["token"]
        self._basic = base64.b64encode(f"x-access-token:{self._tok}".encode()).decode()
        self.env = {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "http.extraHeader",
                    "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {self._basic}"}

    def scrub(self, text: str) -> str:
        return str(text).replace(self._basic, "…").replace(self._tok, "…")

    def git(self, d: str, *args: str, check: bool = True):
        try:
            return _git(d, *args, timeout=NET_TIMEOUT_S, check=check, secret_env=self.env)
        except GitError as e:
            raise GitError(self.scrub(e))


def _fetch(d: str, auth: _Auth) -> Optional[str]:
    """The repository's main into REF; None while it has nothing yet."""
    rc, _, err = auth.git(d, "fetch", "--no-tags", "-q", auth.remote, f"+refs/heads/main:{REF}", check=False)
    if rc:
        err = auth.scrub(err)
        if "couldn't find remote ref" in err.lower():
            _git(d, "update-ref", "-d", REF, check=False)
            return None
        raise GitError(f"couldn't read the project's GitHub repository: {err.strip()[-300:]}")
    return _rev(d, REF)


# ── the project's own git ───────────────────────────────────────────────
def _rev(d: str, ref: str) -> Optional[str]:
    rc, out, _ = _git(d, "rev-parse", "--verify", "-q", f"{ref}^{{commit}}", check=False)
    return out.strip() if rc == 0 and out.strip() else None


def _own(d: str) -> Optional[bool]:
    """True: the folder is its own git repository; False: none yet; a bigger
    repository holding it raises (its history isn't the project's alone)."""
    rc, top, _ = _git(d, "rev-parse", "--show-toplevel", check=False)
    if rc:
        return False
    if os.path.realpath(top) != os.path.realpath(d):
        raise GitError(f"This project is inside a bigger git repository ({top}) — make a copy of it "
                       "to give it a version history of its own.")
    return True


def _changes(d: str) -> List[str]:
    _, st, _ = _git(d, "status", "--porcelain", "--untracked-files=all")
    return [l[3:].strip().strip('"') for l in st.splitlines() if l.strip()]


def _branch(d: str) -> str:
    rc, b, _ = _git(d, "symbolic-ref", "-q", "--short", "HEAD", check=False)
    if rc:
        raise GitError("The project isn't on a branch (detached HEAD) — check a branch out first.")
    return b.strip()


def _who(email: str) -> List[str]:
    email = email or "workspace@dorna.ai"
    return ["-c", f"user.name={email.split('@')[0]}", "-c", f"user.email={email}"]


def _log(d: str, n: int = 5) -> List[dict]:
    rc, raw, _ = _git(d, "log", f"-n{n}", "--format=%H%x1f%h%x1f%an%x1f%ct%x1f%s%x1e", "HEAD", check=False)
    out = []
    for rec in raw.split("\x1e") if rc == 0 else []:
        f = rec.strip("\n").split("\x1f")
        if len(f) >= 5:
            out.append({"rev": f[0], "short": f[1], "author": f[2], "at": int(f[3]), "title": f[4]})
    return out


def _counts(d: str, remote: Optional[str]):
    if not remote or not _rev(d, "HEAD"):
        return 0, (int(_git(d, "rev-list", "--count", remote)[1] or 0) if remote else 0)
    _, c, _ = _git(d, "rev-list", "--left-right", "--count", f"HEAD...{remote}")
    ahead, behind = (int(x) for x in c.split())
    return ahead, behind


def state(d: str, name: str, fetch: bool = True) -> dict:
    """The project against its GitHub repository (fetched first when ``fetch``;
    when that fails, ``github_error`` says why and the rest is the last known)."""
    with _lock(d):
        own = _own(d)
        out = {"git": own, "url": _URLS.get(name, ""), "checked": False, "changes": [],
               "versions": [], "ahead": 0, "behind": 0, "on_github": False, "origin": ""}
        remote = None
        if fetch:                      # GitHub out of reach: the project's own state still shows
            try:
                repo = _repo(name)
                out["url"] = repo.get("url") or out["url"]
                if own:
                    remote = _fetch(d, _Auth(repo))
                out["checked"] = True
            except GitError as e:
                out["github_error"] = str(e)
        if not own:
            return out
        remote = remote or _rev(d, REF)
        out["ahead"], out["behind"] = _counts(d, remote)
        out["on_github"] = bool(remote)
        out["changes"] = _changes(d)
        out["versions"] = _log(d)
        out["head"] = _commit(d, "HEAD") if _rev(d, "HEAD") else None
        rc, origin, _ = _git(d, "remote", "get-url", "origin", check=False)
        out["origin"] = origin.strip() if rc == 0 else ""
        return out


# ── Save version ────────────────────────────────────────────────────────
def _kind(p: str) -> str:
    return ("the bench" if p.startswith("scene/") else
            "the workflow" if p.startswith(("flow/", "actions", "phases")) or p == "actions.py" else
            "the run screens" if p.startswith("hmi/") else "the stations" if p == "recipes.j2" else
            "its parts" if p.startswith(("components/", "CAD/")) else
            "its sample lists" if p.startswith("data/") else "its settings" if p == "launch.yaml" else
            "its manual" if p.startswith(("manual/", "docs/")) else "other files")


def draft(d: str) -> str:
    """What changed since the last version, by what the files are — the Save
    version dialog's starting text, for the person to edit (no AI)."""
    with _lock(d):
        if not _own(d):
            return "First version"
        paths = _changes(d)
    if not paths:
        return ""
    kinds: Dict[str, List[str]] = {}
    for p in paths:
        p = p.split(" -> ")[-1]
        shown = p.split("/")[1].replace("_", " ") if p.startswith("components/") and p.count("/") >= 2 else p
        if shown not in kinds.setdefault(_kind(p), []):
            kinds[_kind(p)].append(shown)
    lines = [f"Changed {k}: " + ", ".join(ps[:4]) + (f" and {len(ps) - 4} more" if len(ps) > 4 else "")
             for k, ps in kinds.items()]
    return "Update " + ", ".join(list(kinds)[:3]) + "\n\n" + "\n".join(f"- {l}" for l in lines)


def save(d: str, message: str, email: str = "") -> dict:
    """The project as it is, as one commit with ``message``. A project with no
    history yet starts one (and a .gitignore, if it has none)."""
    message = (message or "").strip()
    if not message:
        raise ValueError("A version needs a message.")
    with _lock(d):
        if not _own(d):
            _git(d, "init", "-q", "-b", "main")
            gi = os.path.join(d, ".gitignore")
            if not os.path.exists(gi):
                with open(gi, "w") as f:
                    f.write(IGNORE)
                try:
                    st = os.stat(d)
                    os.chown(gi, st.st_uid, st.st_gid)
                except OSError:
                    pass
        elif _rev(d, "HEAD"):
            _branch(d)
        if not _changes(d):
            raise ValueError("Nothing changed since the last version.")
        _git(d, "add", "-A")
        _git(d, *_who(email), "commit", "-q", "-m", message)
        h = _rev(d, "HEAD") or ""
        return {"rev": h, "short": h[:7]}


# ── Push / Pull ─────────────────────────────────────────────────────────
def push(d: str, name: str) -> dict:
    """The saved versions (HEAD) to the repository's main."""
    with _lock(d):
        if not _own(d) or not _rev(d, "HEAD"):
            raise ValueError("Save a version first — there is nothing to push yet.")
        _branch(d)
        repo = _repo(name)
        auth = _Auth(repo)
        rc, out, err = auth.git(d, "push", "--porcelain", auth.remote, "HEAD:refs/heads/main", check=False)
        if rc:
            text = auth.scrub(err or out)
            if "rejected" in text or "non-fast-forward" in text or "fetch first" in text:
                raise GitError("GitHub has changes this robot doesn't have (pushed from somewhere else) — "
                               "Pull them in first, then push.")
            raise GitError(f"push failed: {text.strip()[-300:]}")
        h = _rev(d, "HEAD")
        _git(d, "update-ref", REF, h)
        return {"url": repo.get("url"), "rev": h, "short": h[:7]}


def _merge_tree(d: str, a: str, b: str):
    """git's three-way merge of two commits, the files untouched: (tree, conflicted files)."""
    rc, out, err = _git(d, "merge-tree", "--write-tree", "--name-only", "--no-messages",
                        "--allow-unrelated-histories", a, b, check=False)
    lines = [l for l in out.splitlines() if l.strip()]
    if rc not in (0, 1) or not lines:
        raise GitError(f"git merge-tree: {err.strip()[:300]}")
    return lines[0], (lines[1:] if rc == 1 else [])


def pull(d: str, name: str, email: str = "") -> dict:
    """What's on GitHub brought in: a fast-forward, or both sides merged as a new
    version. Nothing changes — and the files are named — when GitHub and this
    project changed the same lines, or GitHub changed a file with unsaved edits
    here. Returns {pulled, merged, conflicts, unsaved}."""
    with _lock(d):
        if not _own(d):
            raise ValueError("Save a version first — then Pull brings GitHub's changes in.")
        auth = _Auth(_repo(name))
        remote = _fetch(d, auth)
        head = _rev(d, "HEAD")
        if head:
            _branch(d)
        if not remote or (head and _git(d, "merge-base", "--is-ancestor", remote, head, check=False)[0] == 0):
            return {"pulled": 0, "merged": False, "conflicts": []}
        _, behind = _counts(d, remote)
        if not head:                       # a history just started, nothing in it yet
            raise ValueError("Save a version first — then Pull brings GitHub's changes in.")
        ff = _git(d, "merge-base", "--is-ancestor", head, remote, check=False)[0] == 0
        tree = remote if ff else None
        if not ff:
            tree, conflicts = _merge_tree(d, head, remote)
            if conflicts:
                return {"pulled": 0, "merged": False, "conflicts": conflicts, "behind": behind}
        # unsaved edits here to a file the pull changes: git would refuse anyway — say which
        _, moved, _ = _git(d, "diff", "--name-only", head, tree)
        clash = sorted(set(_changes(d)) & set(moved.splitlines()))
        if clash:
            return {"pulled": 0, "merged": False, "conflicts": clash, "unsaved": True, "behind": behind}
        rc, out, err = _git(d, *_who(email), "merge", "--no-edit", "--allow-unrelated-histories",
                            "-m", "Merged the changes made on GitHub", REF, check=False)
        if rc:
            _git(d, "merge", "--abort", check=False)
            raise GitError(f"the merge didn't go: {(err or out).strip()[-300:]}")
        return {"pulled": behind, "merged": not ff, "conflicts": []}


# ── the routes: /home/api/projects/<n>/github ───────────────────────────
class GitHubHandler(AuthedHandler):
    """``GET  …/github?fetch=1``  the project against its repository (state())
    ``POST …/github``  ``{cmd: draft|save|push|pull, message?, push?}``

    Logged in only (401 {error: "login required"}); a project on another
    node is refused — its folder is there."""

    def initialize(self, orch):
        self.orch = orch

    def _dir(self, name):
        from gui.home import projects as P
        ws = self.orch.workspaces.get(name)
        if ws is None:
            raise ValueError(f"No project {name}.")
        if ws.is_remote():
            raise ValueError("This project is on another robot — open its page there.")
        return P.project_dir(ws)

    def _gate(self):
        from gui.home.handlers import logged_in
        if not self.ensure_auth():
            return False
        if not logged_in():
            self.set_status(401)
            self.write({"error": "login required"})
            return False
        return True

    async def _off(self, fn, *a):
        return await asyncio.get_running_loop().run_in_executor(None, fn, *a)

    async def _email(self):
        from gui.account import account
        return (await self._off(account.status)).get("email") or ""

    async def get(self, name):
        if not self._gate():
            return
        try:
            d = self._dir(name)
            out = await self._off(state, d, name, self.get_argument("fetch", "1") != "0")
            out.update(await asyncio.get_running_loop().run_in_executor(_status_pool, _run_state, self.orch, name))
            self.write(out)
        except (GitError, ValueError, OSError) as e:
            self.set_status(400)
            self.write({"error": str(e)})

    async def post(self, name):
        if not self._gate():
            return
        try:
            b = json.loads(self.request.body.decode() or "{}")
            if not isinstance(b, dict):
                raise ValueError("The request body is not a JSON object.")
            d, cmd = self._dir(name), b.get("cmd")
            if cmd == "draft":
                out = {"message": await self._off(draft, d)}
            elif cmd == "save":
                out = await self._off(save, d, str(b.get("message") or ""), await self._email())
                if b.get("push"):
                    try:
                        out["github"] = await self._off(push, d, name)
                    except (GitError, ValueError) as e:
                        out["push_error"] = str(e)
            elif cmd == "push":
                out = await self._off(push, d, name)
            elif cmd == "pull":
                out = await self._off(pull, d, name, await self._email())
            else:
                raise ValueError("cmd: draft, save, push or pull")
            self.write(out)
        except (GitError, ValueError, OSError) as e:
            self.set_status(400)
            self.write({"error": str(e)})


def github_routes(orch):
    """The GitHub panel's routes, for gui/server.py make_app()."""
    return [(r"/home/api/projects/([^/]+)/github", GitHubHandler, dict(orch=orch))]
