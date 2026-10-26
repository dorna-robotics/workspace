# Upgrade Guide

How to update your Dorna Workspace to the latest version.

---

## Update

```bash
cd /home/dorna/Downloads/workspace-release
git pull
cd workspace
sudo pip3 install --break-system-packages -e .
```

That's it. Your projects and license are not affected.

---

## Verify

After updating, verify everything works:

```bash
# Check the license is still valid
sudo python3 -m workspace.license verify

# Start the server
cd /home/dorna/Downloads/workspace-release/workspace
sudo python3 gui/server.py
```

---

## A project's latest — the admin page's Version panel

A PROJECT (bna, apc, …) updates from its own page: the **ⓘ Version** icon
at the top right shows the branch, the commit the bench runs, how it
compares with GitHub, and what is local. **Get latest…** lists every risk
as a warning — a run in flight, uncommitted changes, local commits, no
access to GitHub, other projects in the same repository — and the exact
git commands it will run, then waits for the confirm:

```bash
git fetch origin
git stash push -u -m "before update <stamp>"   # only with uncommitted changes
git branch backup/<stamp>                       # only with local commits
git reset --hard origin/<branch>
```

Nothing is lost: changes are in `git stash list` (back with `git stash pop`),
local commits on `backup/<stamp>`. Git-ignored data — records, uploads,
calibration, `scene/bench.j2` — is never touched, and nothing is pushed.
Git runs as the repository's owner, so the bench needs that user's read
key for the repo. Relaunch the project afterwards to run the new code.
A project inside the platform's own repository (`examples/*`) is the
platform: update it with the steps above instead.

## Troubleshooting

| Issue | Solution |
|-------|----------|
| `git pull` fails | Check your internet connection. Try `git status` to see if there are local changes conflicting. |
| `pip install` fails | Make sure you include `--break-system-packages`. |
| License error after update | Your license should survive updates. If not, contact Dorna support. |
| Import errors | Run `sudo pip3 install --break-system-packages -e .` again from the `workspace/` folder. |

## Device-bus broker (site fleet rule)

Every upgrade, run once on the workspace host:

    sudo python3 -m workspace.devices.provision_broker

Idempotent. Makes this host the site's device-bus broker (LAN
listener); device units point themselves here automatically via the
``bus_connect`` handshake (device-guide §8).
