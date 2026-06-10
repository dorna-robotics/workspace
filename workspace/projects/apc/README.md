# apc

Custom BT project. Starts as a scaffold with just the canonical
`Start` → `Park` shape; the workflow becomes meaningful as you add
project-specific components, recipes, and per-item actions.

## Project layout

```
apc/
├── main.py             # Standard BT entry point (do not edit)
├── launch.yaml         # project_name, port, scene paths, kwargs
├── recipes.j2          # Recipe aliases (gripper + your additions)
├── scene/
│   ├── base.j2         # Core + plates 1–6 + tool rack
│   └── layout.j2       # Gripper + items you load into the scene
├── actions.py          # Start, Park, OperatorPark (+ your actions)
├── checks.py           # Vision / sensor predicates (currently empty)
├── components/         # Project-local components (registered via @register)
├── recipes/            # Project-local recipe classes
└── CAD/                # Project-local .glb models
```

## Adding things

### Custom component

1. Create `components/my_holder.py`:

   ```python
   from copy import deepcopy
   from mergedeep import merge
   from workspace.components.factory import register
   from workspace.components.rack.rack import Rack   # or another base

   @register("my_holder")
   class MyHolder(Rack):
       DEFAULTS = dict(
           anchors={"body": {"center": [0, 0, 0, 0, 0, 0], "place": [0, 0, 5, 0, 0, 0], "top": [0, 0, 25, 0, 0, 0]}},
           ...
       )

       def __init__(self, name, cfg, workspace, **kwargs):
           prm = deepcopy(Rack.DEFAULTS); merge(prm, self.DEFAULTS); merge(prm, cfg); merge(prm, kwargs)
           prm.setdefault("type", getattr(self.__class__, "_registered_type", cfg.get("type")))
           super().__init__(name=name, workspace=workspace, **prm)
   ```

2. Drop `CAD/my_holder.glb` in the local CAD folder.
3. Import it in `main.py` (top of the file, before workspace boots):

   ```python
   from components.my_holder import MyHolder
   ```

4. Reference it in `scene/base.j2`:

   ```yaml
   my_holder_1:
     type: "my_holder"
     attach: { ... }
   ```

See `docs/component-guide.md` for the full pattern.

### Custom recipe

1. Create `recipes/my_recipe.py` (subclass `Recipe` or one of its
   specialised variants — see `docs/recipe-guide.md` §8).
2. Reference it in `recipes.j2` with the local import path:

   ```yaml
   my_thing:
     class: recipes.my_recipe.MyRecipe
     kwargs: { component: my_holder_1, speed_factor: 20 }
   ```

### Custom per-item action

Add a class to `actions.py` between `Start` and `Park`. Most BT
examples (`workspace/projects/examples/*`) are good templates —
copy one of their per-item actions and rename.

Don't change `Start` / `Park` / `OperatorPark` shape — they're the
platform-wide canonical bookends (see
`.claude/skills/add-bt-action/SKILL.md` rule 6).

## Run it

```bash
cd workspace/projects/apc
sudo python3 main.py
```

UI at `http://<ip>:5010/`. With no per-item actions yet, the
workflow runs Start (motor on + park to safe pose) → Park (motor
off) and ends. Add per-item actions to make the protocol do real
work.
