# phased

The layout for a **phased protocol** — the shape every multi-phase
project on this platform follows. The bench is `examples/scale`'s (one
2 ml rack, one balance) and the protocol is deliberately small: every
tube is weighed, the batch regroups, every tube is weighed again.

What it teaches, and where:

| Question | Look in |
|---|---|
| What does the bench look like at each phase boundary? | `doc/phases.md` — the boundary table, written first |
| What does a fact mean? | `actions/predicates.py` — every fact, grouped by phase |
| How is a motion done? | `actions/base.py` — abstract bases (`register = False`), shared helpers |
| What happens in phase N? | `actions/phase_N.py` — the concrete actions, in execution order |
| Where are Start / Park / setup? | `actions/__init__.py` — imports the phases, nothing else per phase |
| Which line does the batch cross next? | `phases.py` — one class per phase, same order |

Conventions this encodes:

- **`actions:` is a package** (`actions/` in launch.yaml). Importing it
  imports every phase module; importing a module registers its actions.
- **Per-pass facts are pass-indexed** (`home_1`, `home_2`), never a
  `pass` parameter: an action takes one param, and a phase fact must
  be one nothing removes.
- **Shared motion is an abstract base plus thin subclasses** carrying
  `PASS`. A base exists only once a body has a second user.
- **The audit row is written where the value is produced**: `Weigh`
  writes `weight_N_g` on a valid reading, Start seeds the rows,
  Park derives `status` from the facts (`rt.record`, project-guide §3).
- **Build phase by phase, gate each alone**: replay at batch 1 and 4,
  then the bench, before the next phase's row is written.

```bash
cd ~/Downloads/workspace/workspace && sudo python3 -m workspace.bt.replay ~/Downloads/workspace/examples/phased --batch 1 4
cd ~/Downloads/workspace/examples/phased && sudo python3 main.py
```
