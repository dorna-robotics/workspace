# phased

The layout for a **phased protocol** — the shape every multi-phase
project on this platform follows. The bench is `examples/shaker`'s
(one amber 40 ml rack, one 4-seat shaker) plus `examples/scale`'s
balance, and the protocol is deliberately small: every tube is
weighed, the batch regroups, every tube is shaken in banks of four and
weighed again.

What it teaches, and where:

| Question | Look in |
|---|---|
| How can each phase end for a tube, and where does it rest? | `doc/phases.md` — the boundary table (closure fact, outcomes, measurements), written first |
| What does a fact mean? | `actions/predicates.py` — every fact, grouped by phase |
| How is a motion done? | `actions/base.py` — abstract bases (`register = False`), shared helpers |
| What happens in phase N? | `actions/phase_N.py` — the concrete actions, in execution order |
| Where are Start / Park / setup? | `actions/__init__.py` — imports the phases, nothing else per phase |
| Which line does the batch cross next? | `phases.py` — one class per phase, same order |
| Which tubes move together, and how do their stages overlap? | `phases.py` — `Weighed2.group` names the shaker banks; `actions/phase_2.py` — the shake holds the shaker, not the arm (bt-framework-guide §13 "Groups") |
| How do I check ONE phase, from a notebook, with no numbers of its own? | `dev/phase.ipynb` — `Bench(PROJ).phase(name)`; the project is the only source of truth (bt-framework-guide §13 "Checking one phase") |

Conventions this encodes:

- **`actions:` is a package** (`actions/` in launch.yaml). Importing it
  imports every phase module; importing a module registers its actions.
- **Per-pass facts are pass-indexed** (`home_1`, `home_2`), never a
  `pass` parameter: an action takes one param, and a phase fact must
  be one nothing removes.
- **Shared motion is an abstract base plus thin subclasses** carrying
  `PASS`. A base exists only once a body has a second user — the
  shaker's three actions are used once and stay plain classes.
- **A device that holds several items couples them into a group.**
  The shaker seats four; a tube's seat (`t mod 4`) is a capacity fact
  and its bank (`t // 4`) is a static rule. `Weighed2` declares the
  banks (`group`) and widens its window to the rack, so the planner
  stamps one bank's chain per bank (never a search) and the scheduler
  overlaps them: bank 2 shakes while bank 1 is weighed. The shake and
  the second weighing are ONE phase for that reason — a barrier
  between them would idle the arm through every shake.
- **The audit row is written where the value is produced**: `Weigh`
  writes `weight_N_g` on a valid reading, Start seeds the rows,
  Park derives `status` from the facts (`rt.record`, project-guide §3).
- **Build phase by phase, gate each alone**: replay at batch 1, 4 and
  8, then the bench, before the next phase's row is written. On the
  bench, `dev/phase.ipynb` runs one phase from the project's own
  files — seeds the earlier phases, applies their `layout`, stops
  when the phase closes.

```bash
cd ~/Downloads/workspace/workspace && sudo python3 -m workspace.bt.replay ~/Downloads/workspace/examples/phased --batch 1 4 8
cd ~/Downloads/workspace/examples/phased && sudo python3 main.py
```

At batch 8 the schedule (`--show`) reads: bank 1 loaded, shaken,
unloaded; bank 2 loaded and shaken on the shaker lane while the robot
lane weighs bank 1.
