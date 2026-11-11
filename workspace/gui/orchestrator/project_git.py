"""A project's git state, and "Get latest" from its remote.

The admin page's Version panel (workspace.html) reads :func:`version`
and confirms :func:`plan`; :func:`update` runs exactly that plan. It
only ever READS from the remote — nothing here pushes.

Get latest makes the bench's branch match ``origin/<branch>`` and keeps
everything it would otherwise lose, the standard git way:

* uncommitted changes → ``git stash push -u`` (back with ``git stash pop``)
* local commits not on the remote → ``git branch backup/<stamp>``
* then ``git reset --hard origin/<branch>``

Git-ignored files (records, uploads, calibration, scene/bench.j2) are
never touched. Nothing is refused for the bench's state — a dirty tree,
local commits, a run in flight are WARNINGS the operator confirms past;
the one refusal is a plan that changed between the look and the confirm.

Git runs as the repository's OWNER, never as root: the orchestrator is
root, the repos (and their read keys) belong to the user who cloned
them — as root git refuses the "dubious ownership" and a reset would
leave root-owned files behind. Prompts are off, so a missing key fails
fast instead of hanging.
"""

from __future__ import annotations

import os
import pwd
import subprocess
import time
from typing import List, Optional

FETCH_TIMEOUT_S = 30
GIT_TIMEOUT_S = 60


class GitError(RuntimeError):
    """A git command failed — its stderr is the message."""


def _owner(path: str) -> str:
    return pwd.getpwuid(os.stat(path).st_uid).pw_name


def _git(cwd: str, *args: str, timeout: float = GIT_TIMEOUT_S, check: bool = True):
    """``git -C cwd args…`` as the folder's owner. Returns (rc, stdout, stderr)."""
    env = ["GIT_TERMINAL_PROMPT=0",
           "GIT_SSH_COMMAND=ssh -o BatchMode=yes -o ConnectTimeout=10"]
    owner = _owner(cwd)
    if os.geteuid() == 0 and owner != "root":
        cmd = ["sudo", "-u", owner, "-H", "env", *env, "git", "-C", cwd, *args]
    else:
        cmd = ["env", *env, "git", "-C", cwd, *args]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise GitError(f"git {' '.join(args)}: no answer in {timeout:g} s")
    if check and p.returncode != 0:
        raise GitError((p.stderr or p.stdout).strip() or f"git {' '.join(args)} failed ({p.returncode})")
    # rstrip only: status --porcelain lines START with a status column.
    return p.returncode, p.stdout.rstrip(), p.stderr.strip()


def _commit(repo: str, ref: str) -> Optional[dict]:
    rc, out, _ = _git(repo, "log", "-1", "--format=%h%x1f%s%x1f%cs", ref, check=False)
    if rc != 0 or not out:
        return None
    h, subject, date = (out.split("\x1f") + ["", "", ""])[:3]
    return {"hash": h, "subject": subject, "date": date}


def version(project_dir: str, fetch: bool = True) -> dict:
    """What this bench runs and how it compares with the remote.

    ``fetch`` asks the remote first (``git fetch origin``); a failure is
    reported as ``reachable: False`` with git's own message, and the rest
    is what the last successful fetch knew."""
    rc, repo, err = _git(project_dir, "rev-parse", "--show-toplevel", check=False)
    if rc != 0:
        return {"git": False, "error": err or "not a git repository"}
    _, branch, _ = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    _, url, _ = _git(repo, "remote", "get-url", "origin", check=False)
    out = {"git": True, "repo": repo, "project_dir": project_dir, "url": url, "branch": branch,
           "detached": branch == "HEAD", "head": _commit(repo, "HEAD"),
           "reachable": None, "fetch_error": "", "checked_at": None}
    if fetch:
        rc, _, err = _git(repo, "fetch", "origin", timeout=FETCH_TIMEOUT_S, check=False)
        out["reachable"] = rc == 0
        out["fetch_error"] = "" if rc == 0 else err
        out["checked_at"] = time.time()
    remote = f"origin/{branch}"
    rc, _, _ = _git(repo, "rev-parse", "--verify", "--quiet", remote, check=False)
    out["remote_ref"] = remote if (rc == 0 and not out["detached"]) else None
    out["remote"] = _commit(repo, remote) if out["remote_ref"] else None
    behind = ahead = 0
    local_commits: List[dict] = []
    if out["remote_ref"]:
        _, counts, _ = _git(repo, "rev-list", "--left-right", "--count", f"HEAD...{remote}")
        ahead, behind = (int(x) for x in counts.split())
        if ahead:
            _, log, _ = _git(repo, "log", "--format=%h%x1f%s", f"{remote}..HEAD")
            local_commits = [dict(zip(("hash", "subject"), l.split("\x1f"))) for l in log.splitlines() if l]
    _, st, _ = _git(repo, "status", "--porcelain", "--untracked-files=all")
    out.update(behind=behind, ahead=ahead, local_commits=local_commits,
               changes=[l[3:] for l in st.splitlines() if l.strip()])
    # A project inside the PLATFORM's own repository (examples/*): Get
    # latest there resets the whole workspace, not one project.
    here = os.path.dirname(os.path.abspath(__file__))
    rc, plat, _ = _git(here, "rev-parse", "--show-toplevel", check=False)
    out["platform"] = rc == 0 and os.path.realpath(plat) == os.path.realpath(repo)
    # The other projects that live in this repository update with it.
    out["shared_with"] = [] if out["platform"] else sorted(
        d for d in os.listdir(repo)
        if os.path.isfile(os.path.join(repo, d, "launch.yaml"))
        and os.path.realpath(os.path.join(repo, d)) != os.path.realpath(project_dir))
    return out


def part_state(path: str, branch: str, fetch: bool = True) -> dict:
    """One part of the bench's ``upgrade`` (the admin's System page): how
    the folder ``path`` compares with ``origin/<branch>`` — what the part's
    script makes it: ``git fetch``, ``checkout -B <branch> origin/<branch>``,
    ``reset --hard``, ``clean -fd``. So it reports what that would cost:

    * ``changes`` — uncommitted edits to tracked files: DISCARDED
    * ``untracked`` — new files git does not ignore: DELETED (ignored
      files — records, uploads, calibration, bench.j2 — are kept)
    * ``switches`` — the folder is on another branch: the upgrade runs
      ``branch`` from then on (the other branch and its commits stay)
    * ``lost_commits`` — on ``branch`` already, with commits the remote
      does not have: ``checkout -B`` moves the branch off them

    ``behind`` counts the remote's commits this folder does not have.
    ``fetch`` asks the remote first; a failure is ``reachable: False``."""
    if not os.path.isdir(path):
        return {"exists": False}
    rc, repo, err = _git(path, "rev-parse", "--show-toplevel", check=False)
    if rc != 0:
        return {"exists": True, "git": False, "error": err or "not a git repository"}
    _, current, _ = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    out = {"exists": True, "git": True, "branch": current, "head": _commit(repo, "HEAD"),
           "target_branch": branch, "reachable": None, "fetch_error": ""}
    if fetch and branch:
        rc, _, err = _git(repo, "fetch", "origin", branch, timeout=FETCH_TIMEOUT_S, check=False)
        out["reachable"] = rc == 0
        out["fetch_error"] = "" if rc == 0 else err
    target = f"origin/{branch}"
    rc, _, _ = _git(repo, "rev-parse", "--verify", "--quiet", target, check=False)
    out["target"] = _commit(repo, target) if (rc == 0 and branch) else None
    ahead = behind = 0
    lost: List[dict] = []
    if out["target"]:
        _, counts, _ = _git(repo, "rev-list", "--left-right", "--count", f"HEAD...{target}")
        ahead, behind = (int(x) for x in counts.split())
        if ahead and current == branch:
            _, log, _ = _git(repo, "log", "--format=%h%x1f%s", f"{target}..HEAD")
            lost = [dict(zip(("hash", "subject"), l.split("\x1f"))) for l in log.splitlines() if l]
    _, st, _ = _git(repo, "status", "--porcelain", "--untracked-files=all")
    lines = [l for l in st.splitlines() if l.strip()]
    out.update(behind=behind, ahead=ahead, switches=bool(branch) and current != branch,
               lost_commits=lost,
               changes=[l[3:] for l in lines if not l.startswith("??")],
               untracked=[l[3:] for l in lines if l.startswith("??")])
    return out


def plan(info: dict, stamp: str) -> List[str]:
    """The exact commands Get latest runs for this state, in order."""
    if not info.get("git"):
        raise GitError(info.get("error") or "not a git repository")
    if info.get("detached"):
        raise GitError("the repository is not on a branch (detached HEAD) — check a branch out first")
    if not info.get("remote_ref"):
        raise GitError(f"there is no origin/{info.get('branch')} to update to")
    cmds = ["git fetch origin"]
    if info["changes"]:
        cmds.append(f'git stash push -u -m "before update {stamp}"')
    if info["ahead"]:
        cmds.append(f"git branch backup/{stamp}")
    cmds.append(f"git reset --hard {info['remote_ref']}")
    return cmds


def update(project_dir: str, confirmed: List[str], stamp: str) -> dict:
    """Run :func:`plan` — only if it is still the plan the operator
    confirmed (``confirmed``). Returns what ran and what was set aside."""
    before = version(project_dir, fetch=True)
    if not before.get("reachable"):
        raise GitError(before.get("fetch_error") or "the remote cannot be reached")
    cmds = plan(before, stamp)
    if cmds != list(confirmed):
        raise GitError("the bench changed since you looked — check again and confirm the new plan")
    repo = before["repo"]
    ran = []
    stash = backup = None
    for c in cmds[1:]:              # the fetch above was the first
        if c.startswith("git stash push"):
            _git(repo, "stash", "push", "-u", "-m", f"before update {stamp}")
            stash = f"before update {stamp}"
        elif c.startswith("git branch backup/"):
            backup = f"backup/{stamp}"
            _git(repo, "branch", backup)
        elif c.startswith("git reset --hard "):
            _git(repo, "reset", "--hard", before["remote_ref"])
        ran.append(c)
    after = version(project_dir, fetch=False)
    return {"ran": [cmds[0]] + ran, "from": before["head"], "to": after["head"],
            "stash": stash, "backup": backup, "version": after}
