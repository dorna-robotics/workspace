# Liquid handling

Pumps, nozzles and the tubes between them — how a workspace moves
liquid. This is the single doc for the fluid side of a bench: wiring a
pump into a scene, plumbing one or more nozzles to it, which recipe
drives what, and the pump-specific settings that are silently wrong if
you guess them.

Code involved: `workspace/components/pump/` (driver, station, component,
the `PumpLink` capability), the plumbed components
(`gripper_syringe_needle`, `syringe_dispense_arm`, …) and the recipes
that sequence them (`pipetting.py`, `dispense_arm.py`, `doser.py`,
`syringe_pump.py`). Runnable reference: `examples/syringe_pump`.

---

## 1. The big picture

A pump is a **device**. A nozzle is **plumbing**. That one sentence
decides the whole design:

```
        syringe_pump_1                    ← the DEVICE
        ┌──────────────────┐                driver + station + bus row
        │  PSD/4 drive     │                sim flag, recovery, operator buttons
        │  barrel + valve  │                the ONLY publisher of its device id
        └────────┬─────────┘
        valve ports │
          ┌─────────┴──────────┐
       port 3                port 2
          │                     │
  gripper_syringe_needle_1   syringe_dispense_arm_1     ← PLUMBING
  (the robot carries it)     (bolted to the bench)        no device id
                                                          no panel row
```

The barrel is shared: every nozzle draws from and pushes through the
same syringe, just via a different valve port. Nothing else on the
bench owns a fluid device.

**Why the nozzles hold a name, not an instance.** A carried needle and a
fixed arm are opposites kinematically — one travels to the liquid, the
other waits for it — so they share no base class (`Gripper` vs `Arm`).
What they share is a capability: *"a nozzle at the end of a tube on pump
port N."* That is composed in (`PumpLink`), not inherited. If a nozzle
owned its own station instead, two publishers would fight over one
physical pump — exactly the failure the device bus refuses
(`device-guide.md` §4).

---

## 2. Wiring the pump into a scene

```yaml
syringe_pump_1:
  type: "pump_psd4"
  port: ""                      # "" → no bus claim; set the by-id path for real
  address: 1                    # rotary switch on the back
  baud: 9600
  syringe_volume_ul: 1000.0     # the INSTALLED barrel — see below
  simulation: true
  critical: true
  attach:
    parent_name: "fixture_plate_1"
    ...
```

Empty `port` means "no device claimed": the component still works (sim
keeps the volume bookkeeping), it just takes no bus id and renders no
Devices-panel row. Use the `/dev/serial/by-id/...` symlink, never
`/dev/ttyUSB0` (`device-guide.md` §9).

What each setting means and how to get it right:

| Parameter | Meaning | Getting it right |
|---|---|---|
| `port` | the USB RS-232 adapter | use the `/dev/serial/by-id/...` symlink — it survives replugs |
| `address` | rotary switch **position** on the back, 0-9 / A-F | read it off the switch; 0 is factory |
| `syringe_volume_ul` | µL the pump's fixed 30 mm stroke **sweeps** | NOT always the barrel label: PSD-specific syringes sweep their full nominal volume, but 1700-series gastights have ~60 mm of travel, so the stroke sweeps only half of nominal (a 1725/250 µL sweeps ~125). Wrong value = every volume off by the same ratio, silently |
| `variant` | which pump drive this is | `'standard'` = PSD/4, PSD/6 high-torque; `'smooth_flow'` = PSD/4 SF / PSD/6 SF (PN 97709-xx). The pump cannot report it; wrong value scales every move 8x, silently |
| `high_resolution` | step mode | defaults to `True` (8x finer steps). The cost is stroke time — a full stroke is ~18 s instead of ~2.3 s at full speed — so set `False` when throughput matters more than granularity; standard mode already resolves ~0.004 µL on a 100 µL sweep, far below the barrel's ±1% accuracy |
| `simulation` | sim intent | sim tracks the plunger and refuses overflows, so protocols test for real; the connection dot keeps showing hardware truth either way (`device-guide.md` §16) |
| `output_right` | which side "output" means | matches the plumbing: `True` = output on the right (viewed from the front). Wrong value sends fluid the wrong way with no error |

**Valve type is not a parameter.** DIP switches 4-6 on the back declare
it, and `initialize()` reads and adopts what the pump reports
(`pump.valve_type`):

| DIP 4 | DIP 5 | DIP 6 | Type | Valve |
|---|---|---|---|---|
| off | off | off | 0 | 3-way 120° Y (factory) |
| ON | off | off | 1 | 4-way 90° T |
| off | ON | off | 2 | 3-way 90° distribution |
| ON | ON | off | 3 | 8-way 45° |
| off | off | ON | 4 | 4-way 90° / 4-port wash |

DIPs are read at power-up — power-cycle after changing them.

---

## 3. Plumbing a nozzle

Two scene keys, on any component that can carry liquid:

```yaml
gripper_syringe_needle_1:        # carried by the robot
  type: "gripper_syringe_needle"
  has_tool_changer: true
  pump: "syringe_pump_1"         # which pump feeds this nozzle
  pump_port: 3                   # which valve port its tube lands on

syringe_dispense_arm_1:          # bolted to the bench
  type: "syringe_dispense_arm"
  pump: "syringe_pump_1"         # same pump…
  pump_port: 2                   # …different port
```

The tube is real hardware, so it is described where hardware is
described — in the scene, not in project code. Both keys are optional:
omit them and the component is unplumbed, still mounts and moves, and
every fluid call says so in plain English instead of raising a
`KeyError` three frames deep.

At the call site the port is already bound:

```python
tool = core.current_tool()      # or the component directly
tool.aspirate(200)              # → pump.aspirate(200, port=3)
tool.dispense(200)
tool.pump                       # the pump component, for anything narrower
```

Never repeating the port is the point: dispensing down the wrong line is
silent and unrecoverable, so it should not be typeable.

**Adding the capability to a new component** — mix `PumpedTool` in front
of the kinematic base and bind after `super().__init__`:

```python
from workspace.components.pump.pump_link import PumpedTool

@register("my_nozzle")
class MyNozzle(PumpedTool, Gripper):
    DEFAULTS = dict(..., pump="", pump_port=None)

    def __init__(self, name, cfg, workspace, **kwargs):
        ...
        super().__init__(name=name, workspace=workspace, **prm)
        self._init_pump_link(workspace, prm)
```

---

## 4. Which recipe drives what

There is **no pumping recipe family**, on purpose. The nozzle's fluid
methods use the pipettor's names, so the existing recipes already fit —
what differs is only *who holds the nozzle*:

| Case | Nozzle | Motion targets | Fluid resolves via | Recipe |
|---|---|---|---|---|
| Carried needle | robot's tool | the vessel | `core.current_tool()` | `PipettingSite` |
| Fixed arm | bench fixture | the arm's own anchor | the arm component | `DispenseArm` |
| Dosing site | either | the site | site first, then carried tool | `DosingSite` |
| No motion at all | — | — | the pump component | `SyringePump` |

`DispenseArm.dispense(volume_ul=…)` pushes through the arm's port when
the arm is plumbed, and falls back to its historical timed hold for
valve/solenoid rigs that have no pump. `DosingSite` resolves the site's
own plumbing first and the carried tool second, and refuses with a clear
message when neither declares a `pump:`.

---

## 5. Moving liquid

The same API at every level — station (notebook), component (scene),
link (nozzle), recipe (protocol): same names, same µL units, same 0-100
speed.

```python
pump.aspirate(50, port=1)        # valve to port 1, draw 50 µL more
pump.aspirate(50, port=1, speed=25)   # …slowly (percent, see Speed)
pump.dispense(20, port=2)        # valve to port 2, push 20 µL out
pump.move_to_volume(30, port=3)  # absolute: end up holding exactly 30
pump.empty(port=3)               # move_to_volume(0), named for the common case
pump.prime(2, from_port=1, to_port=3)  # 2 full-barrel flush cycles
pump.volume()                    # µL currently held
```

All volumes are µL. Every call **blocks until the motion is finished**
and returns `True`/`False` instead of raising — check the return in
protocols. Barrel limits are enforced in software (the pump itself
won't): overfilling or over-dispensing is refused with `False` and no
motion.

- `port` accepts a number (`1`..N around the valve), a logical name
  (`"input"`, `"output"`, `"wash"`, …), or an angle (`"90deg"`). Omit it
  to move the plunger through wherever the valve already points.
- **Name the port on dispense and empty** — an unqualified call dumps
  wherever the valve happens to be. Through a `PumpLink` this is already
  handled: the nozzle's own port is the default.
- `aspirate` / `dispense` are relative ("50 more"); `move_to_volume` is
  absolute — use it when the starting volume is unknown, e.g. the first
  move of a protocol.
- `prime(n)` sweeps full fill/empty cycles at whatever volume is
  declared, from any starting volume. Use it to flush air after
  connecting the fluid path.
- A 3-port distribution valve has no bypass position — `valve("bypass")`
  is refused by the pump.

### Initialize

```python
pump.initialize()
pump.initialize(syringe_volume_ul=125.0)   # barrel swap without touching the scene
```

Homes the plunger and valve. Required after every power-up and after any
stop or stall — until then the pump refuses syringe moves. **The
component initializes on construction**, so a protocol never has to
remember: `recover()` does the whole configure-and-home for a claimed
pump, and an unclaimed / sim pump gets the explicit call. It stays a
public method for barrel swaps and post-stall recovery. `half_force`
defaults to `True` (homes at reduced plunger force). Homing moves the valve and expels the
barrel through the output port, **so clear the deck first**. The last
speed set (default 100) is re-applied, so a freshly homed pump is always
at a known speed.

### Speed

```python
pump.set_speed(100)              # 0-100: 100 = fastest preset, 0 = slowest
pump.aspirate(200, speed=25)     # or per move — same register, same units
```

`speed` on `aspirate` / `dispense` is a convenience for the common
"draw viscous liquid slowly, push it back fast" pair. The drive has ONE
speed register, so a per-move value **stays in effect** afterwards —
"for this move" is as close as the hardware gets.

Through a plumbed nozzle the parameter is called **`pump_speed`**:

```python
tool.dispense(200, pump_speed=40)
```

because `PipettingSite` passes an air-displacement pipettor's
`speed=` in **µL/s** to whatever tool is mounted, and forwarding that
to a drive that reads percent would turn `speed=500` into "100%" — the
opposite of slow. A nozzle therefore swallows `speed` / `blowout` and
takes its own `pump_speed` (0-100).

Normalized across variants and resolution modes. At 100 a full stroke is
~2.3 s on this bench; the 40-preset ladder is roughly geometric, so
mid-scale is much closer to the slow end. The value is remembered and
re-applied at every initialize. Applies to the next move, not one
already running.

---

## 6. Connection, recovery and sim

```python
pump.status()    # {"ready": bool, "error": int, "error_text": str}
pump.recover()   # (re)connect + home — what the bus Recover button runs
pump.release()   # close the port cleanly so unplugging doesn't alarm
```

The pump clears its error status once reported — **whoever reads it
first holds the only copy**. `stop()` aborts a move mid-stroke; position
is untrustworthy afterwards, so re-initialize before the next move.

Operator panel buttons: **Initialize · Volume · Status · Release**.

Sim is orthogonal to connection (`device-guide.md` §16): sim tracks the
plunger and refuses overflows, so a sim run exercises the same barrel
accounting the real drive does, while the bus dot keeps showing hardware
truth. A plumbed nozzle never sees the flag — the station branches once.

If the pump is `critical` and drops mid-run, its bus row goes red and
the runtime pauses; recovery restores the link but never auto-resumes
the run (that decision stays with the operator).

---

## 7. In a protocol

The recipe exposes the same calls, plus nothing new to learn:

```python
rcp["pump"].prime(2, from_port=1, to_port=3)
rcp["pump"].aspirate(50, port=1)
rcp["vials"].dispense(50)          # through the carried needle's own port
rcp["arm"].dispense(volume_ul=50)  # through the fixed nozzle's port
```

**Declarative retry** (`project-guide.md` §8): a dose is its own action
and asserts its fact only when the pump reports success. On `False` the
leaf fails, no effect is applied, and the planner re-selects that dose
after the device recovers — the arm never repeats motion it already
did:

```python
ok = rcp["vials"].dispense(ul)
if ok is False:
    rt.step("dose failed — will retry after recover")
    return False
return "dosed"
```

The Start form's kwargs override the scene per run: `syringe_volume_ul`
(barrel swap) and `speed` (0-100), both applied by `Start` before the
first move.

---

## 8. Gotchas

- **`syringe_volume_ul` is the sweep, not the label.** Get it wrong and
  every volume is off by the same ratio, with no error anywhere.
- **`variant` cannot be detected.** A `smooth_flow` drive declared as
  `standard` scales every move by 8x, silently.
- **Dispense without a port dumps wherever the valve is.** Declare
  `pump_port` on every nozzle and let the link supply it.
- **Homing expels the barrel** through the output port — clear the deck
  before `initialize()`.
- **The error status is read-once.** Log it where you read it; the next
  reader gets "no error".
- **An unplumbed component is not an error.** `pump: ""` is a valid
  scene: geometry only, and every fluid call explains itself.

---

## Canonical references

- `docs/device-guide.md` — the Device protocol, `attach_device`, one
  publisher per device id, sim model (§16), `sim_return` (§17)
- `docs/component-guide.md` §7 — atomic ops on the component, workflows
  in the recipe; §8 — operator actions
- `docs/project-guide.md` §8 — device reads + declarative retry
- `examples/syringe_pump` — one pump, a carried needle (port 3) and a
  fixed nozzle over its own cup (port 2)
