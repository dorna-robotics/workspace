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

## The bench's upgrade — the admin's System page

A bench unit is upgraded by the `upgrade` repo (`sudo upgrade`: OS
settings, the robot library, devices, workspace, path planning, the
vision client — then services checked, then a reboot). The admin's
**System** page (left nav) runs it from the browser:

- **This unit** — whether `upgrade` is installed, the last upgrade and
  how it ended, and one row per part: the folder, what this unit runs,
  what the upgrade installs ("2 newer", "same", "switches branch", "new
  install"). **The page names no part.** Every upgrade writes a record —
  `/var/lib/dorna/upgrade.json` — and each part writes its own entry from
  its own variables (the upgrade repo's `lib/record.sh`). A part added to
  the upgrade appears after the next upgrade; before the first recorded
  one the page says the parts appear then.
- **Upgrade…** — every risk is a warning, nothing is greyed out: launched
  projects and runs end; the unit reboots; and per part, what its
  `git fetch` / `checkout -B` / `reset --hard` / `clean -fd` would cost —
  a branch switched, uncommitted changes DISCARDED, untracked files
  DELETED, local commits left behind. Ignored files (records, uploads,
  calibration, `scene/bench.j2`) are kept.
- **Upgrade and reboot** starts it as its own system job —
  `systemd-run --unit=dorna-upgrade --collect /usr/local/bin/upgrade` —
  so it outlives the orchestrator's restart; one at a time. The page
  follows it from the journal (`journalctl -u dorna-upgrade`): the steps
  come from the `==> <part>` lines the upgrade prints, through the
  orchestrator's restart and the reboot, to the result — each part's
  commit before and now, or why it stopped.
- **The nav's System item shows a badge** with the number of parts to
  update (`system.needs_update` — missing, behind, or on another
  branch; one rule for the badge and the page's status). It is checked
  in the background at most hourly (`CHECK_EVERY_S`), and at once when
  the System page checks.
- If a service does not come back, the upgrade stops without rebooting;
  if that service is the orchestrator, the page cannot reconnect — SSH
  in and read `journalctl -u dorna-upgrade`.

Code: `gui/orchestrator/system.py` (state, start, log),
`project_git.part_state` (a part's git cost), `web/admin/system.js`.

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
