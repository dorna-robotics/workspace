# Liquid handling

Pumps, nozzles and the tubes between them — how a workspace moves
liquid. This is the single doc for the fluid side of a bench: wiring a
pump into a scene, plumbing one or more nozzles to it, which recipe
drives what, and the pump-specific settings that are silently wrong if
you guess them.

Code involved: `workspace/components/pump/` (driver, station,
component, the `PumpLink` capability), the plumbed nozzle components
in `workspace/components/needle/` (`needle_gripper`,
`needle_dispense_arm`, the `Needle` fitted-needle helper) and the
recipes that sequence them (`pipetting.py`, `dispense_arm.py`,
`doser.py`, `pump.py`). Runnable reference: `examples/pump`. The
PSD/4 manual lives at `docs/manuals/PSD4_Manual_8892-01b.pdf`.

---

## 1. The big picture

A pump is a **device**. A nozzle is **plumbing**. That one sentence
decides the whole design:

```
           pump_1                            ← the DEVICE
        ┌──────────────────┐                driver + station + bus row
        │  PSD/4 drive     │                sim flag, recovery, operator buttons
        │  barrel + valve  │                the ONLY publisher of its device id
        └────────┬─────────┘
        valve ports │
          ┌─────────┴──────────┐
     3 "needle"            2 "flush"
          │                     │
   needle_gripper_1        needle_dispense_arm_1   ← PLUMBING
  (the robot carries it)     (bolted to the bench)       no device id
                                                         no panel row
```

The barrel is shared: every nozzle draws from and pushes through the
same syringe, just via a different valve port. Nothing else on the
bench owns a fluid device.

**One pump = one barrel = one component = one bus row.** The syringe
is a parameter (`syringe_volume_ul`), not a component — it is a
hardware fact the drive cannot report, like the pump variant. "Two
syringes" means two pump drives, so it is two scene entries, each on
its own USB adapter with its own address (the bna bench runs an LV and
an HV pump exactly this way). Daisy-chaining several drives on one
RS-485 adapter is not supported — two stations would fight over one
half-duplex line.

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
pump_1:
  type: "pump"
  driver: "psd4"                # which brand backend drives it
  port: ""                      # "" → no bus claim; set the by-id path for real
  address: 1                    # rotary switch on the back
  baud: 9600
  simulation: true
  critical: true
  syringe_volume_ul: 100.0      # the INSTALLED barrel — see below
  valve_type: 3                 # the valve BODY — verified at initialize
  variant: "standard"           # which drive this is — see below
  high_resolution: true
  output_right: true
  default_speed: 100
  valve_ports:                  # port → name, or port → {name, tube_volume_ul}
    1: {name: reservoir, tube_volume_ul: 0}
    2: {name: flush,     tube_volume_ul: 150}
    3: {name: needle,    tube_volume_ul: 150}
  outlets: [2, 3]               # these ports are nozzles; the rest are sources
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
| `driver` | which brand backend | `"psd4"` (Hamilton PSD/4 family) is the only one today |
| `port` | the USB RS-232 adapter | use the `/dev/serial/by-id/...` symlink — it survives replugs |
| `address` | rotary switch **position** on the back, 0-9 / A-F | read it off the switch; 0 is factory |
| `syringe_volume_ul` | µL the pump's fixed 30 mm stroke **sweeps** | NOT always the barrel label: PSD-specific syringes sweep their full nominal volume, but 1700-series gastights have ~60 mm of travel, so the stroke sweeps only half of nominal (a 1725/250 µL sweeps ~125). Wrong value = every volume off by the same ratio, silently |
| `valve_type` | the valve BODY the firmware reports | see the DIP table below. Declared here and **verified at every initialize** — a mismatch refuses to home, with a message naming both sides. `valve_type: null` skips the check |
| `variant` | which pump drive this is | `'standard'` = PSD/4, PSD/6 high-torque; `'smooth_flow'` = PSD/4 SF / PSD/6 SF (PN 97709-xx). The pump cannot report it; wrong value scales every move 8x, silently. **Always declare it** |
| `high_resolution` | step mode | defaults to `True` (8x finer steps). On SF drives the cost is stroke time — a full stroke is ~8x slower — so set `False` when throughput matters more than granularity; standard mode already resolves far below the barrel's ±1% accuracy |
| `output_right` | which side "output" means at homing | matches the plumbing: `True` = output on the right (viewed from the front). Homing expels the barrel through that side |
| `default_speed` | plunger speed used when a call gives no `speed=` | 0-100 percent; 100 is the fastest preset |
| `simulation` | sim intent | sim tracks the plunger, refuses overflows, and **takes the time the real move would take** (§6), so protocols test for real; the connection dot keeps showing hardware truth either way (`device-guide.md` §16) |
| `valve_ports` | valve port → what hangs on it | the live holes only — an undeclared port is refused. `tube_volume_ul` is that line's WHOLE dead volume: reservoir→valve for a source; valve→needle **tip** for an outlet, so it includes the fitted needle's internal bore, not just the tubing. `prime` sizes its cycle count from it — and from nothing else (§3) |
| `outlets` | which ports are nozzles | numbers or names; every other listed port is a source. Materials are tracked per outlet (§5) |

**Valve bodies vs. holes.** `valve_type` declares the BODY (what
`?21000` reports); `valve_ports` lists the live holes. A 6-port ceramic
distribution valve is an 8-position type-3 body with two blind
positions — declare type 3 and leave the blind positions out of the
map. Ports are the pump's NUMBERED positions, so a distribution valve
is assumed; a plain logical-letter Y/T valve would be driven from
`psd4_driver.py` directly if one were ever fitted.

DIP switches 4-6 on the back declare the body to the firmware:

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
needle_gripper_1:               # carried by the robot
  type: "needle_gripper"
  has_tool_changer: true
  pump: "pump_1"                 # which pump feeds this nozzle
  pump_port: 3                   # which valve port its tube lands on

needle_dispense_arm_1:          # bolted to the bench
  type: "needle_dispense_arm"
  pump: "pump_1"                 # same pump…
  pump_port: 2                   # …different port
```

`pump_port` is a number or a name from the pump's `valve_ports` map —
either way it must be declared there, so the map stays the single
statement of what is plumbed.

### The fitted needle

A needle is a consumable — the same head takes 22G x 2" today and
16G x 4" tomorrow — so it is declared beside the nozzle rather than
modelled in CAD:

```yaml
needle_gripper_1:
  needle_gauge: 22
  needle_length: 50.8

needle_dispense_arm_1:
  needle_gauge: 16
  needle_length: 40.0
```

Both are **declarative**: they record what is fitted and change no
geometry — anchors, tcp/tip and collision boxes stay exactly as the CAD
models them. Read them off the component:

```python
tool.needle.gauge     # 22
tool.needle.length    # 50.8
tool.needle.od        # 0.718 mm, from the ISO 9626 table (None if unknown)
str(tool.needle)      # "22G x 50.8 mm (0.718 mm od)"
```

### The wrist lock (`lock_j5`)

The carried needle wears a stripper-weight-and-rod assembly: pulling
out of a septum vial, the weight holds the vial down so the needle
strips clean. The rods clash with j4 / the robot body unless the wrist
roll is at 0 during the vertical entry and exit, so the tool declares
it:

```yaml
needle_gripper_1:
  lock_j5: 0          # degrees; null = wrist free (rod-less variant)
```

`immerse` and `retract` read `lock_j5` off the mounted tool and pin
**every** joint target they execute — hover, dive, and lift, including
motion-planned intermediate waypoints — to that roll. IK would
otherwise spin j5 freely (it is the cheapest joint in the branch
metric). Aspirate/dispense involve no motion, so nothing else is
needed. A call-site `approach_j5=` / `exit_j5=` still overrides, and
tools without the key are unaffected. `needle_gripper` defaults to
`0` because the rods are part of the tool; declare it explicitly in
the scene anyway.

**They do not feed the fluid math either.** The needle's internal bore
is real dead volume — roughly 7 µL for a 22G x 2", ~44 µL for a
16G x 4" — and it is accounted for as part of the outlet's
`tube_volume_ul` on the **pump** entry, whose definition is
valve→needle *tip*. `needle_gauge` / `needle_length` are a record for
the panel and provenance, nothing more. So a needle swap is TWO scene
edits: the gauge/length here, and the outlet's `tube_volume_ul` on the
pump — get the second one wrong and `prime` under- or over-flushes with
no error anywhere.

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
| No motion at all | — | — | the pump component | `Pump` |

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
pump.aspirate(50, port="reservoir")    # valve there, draw 50 µL more
pump.aspirate(50, port=1, speed=25)    # …slowly (percent, see Speed)
pump.dispense(20, port="needle")       # valve there, push 20 µL out
pump.move_to_volume(30, port=3)        # absolute: end up holding exactly 30
pump.empty(port="needle")              # move_to_volume(0), named for the common case
pump.prime(to_port="needle")           # flush the path; cycles sized from tube volumes
pump.volume()                          # µL currently held
```

All volumes are µL. Every call **blocks until the motion is finished**
and returns `True`/`False` instead of raising — check the return in
protocols. Barrel limits are enforced in software (the pump itself
won't): overfilling or over-dispensing is refused with `False` and no
motion.

- `port` is a number or a name from the scene's `valve_ports` map.
  Omit it and the single declared **outlet** is used; with zero or
  several outlets the call refuses and says so. An undeclared port is
  refused too — dry and blind holes are not addressable. Through a
  `PumpLink` the nozzle's own port is always the default.
- `aspirate` / `dispense` are relative ("50 more"); `move_to_volume` is
  absolute — use it when the starting volume is unknown, e.g. the first
  move of a protocol.
- `prime(...)` sweeps fill/empty cycles from a source to an outlet.
  `from_port` defaults to the single declared source, `to_port` to the
  single outlet, and `cycles` to what the declared tube volumes need:
  ceil(dead volume / barrel) + 1 margin cycle, so a 25 µL barrel
  behind 150 µL of tubing gets 7 cycles, not 1. With no tube volumes
  declared it falls back to 1 cycle.

### What's where — material tracking

The component keeps the bookkeeping the pump cannot do:

```python
pump.material_in_barrel()     # name of the last SOURCE drawn from
pump.material_at("needle")    # what was last pushed out of that outlet
pump.last_op()                # {op, port, name, target_ul, seconds, ok, error}
pump.op_log(50)               # the last N of those
pump.summary()                # everything at once (also the Report button)
```

Names come from `valve_ports`, so a protocol can assert "the needle tip
holds surrogate" without tracking it itself.

### Initialize

```python
pump.initialize()
pump.initialize(syringe_volume_ul=125.0)   # barrel swap without touching the scene
```

Configures the pump and homes the plunger and valve. Required after
every power-up and after any stop or stall — until then the pump
refuses syringe moves. **The component initializes on construction**,
so a protocol never has to remember: `recover()` does the whole
configure-and-home for a claimed pump, an unclaimed / sim pump gets the
explicit call, and any op after a `stop()` re-initializes
automatically. It stays a public method for barrel swaps and post-stall
recovery.

Initialize also **verifies the scene** against the hardware: the
resolution mode is asserted (written to the pump — there is no DIP for
it), and the declared `valve_type` is checked against what the DIPs
report; a mismatch refuses to home with a message naming both sides.
`half_force` defaults to `True` (homes at reduced plunger force).
Homing moves the valve and expels the barrel through the output port,
**so clear the deck first**. The last speed set (default
`default_speed`) is re-applied, so a freshly homed pump is always at a
known speed.

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

Normalized across variants and resolution modes: the percent addresses
the drive's 40-preset ladder (manual table 8-33, 1.2 s to 600 s per
stroke), not wall-clock time, so the same number means the same
relative speed everywhere. The ladder is roughly geometric, so
mid-scale sits much closer to the slow end. The value is remembered and
re-applied at every initialize. Applies to the next move, not one
already running.

---

## 6. Connection, recovery and sim

```python
pump.status()    # {"ready": bool, "error": int, "error_text": str}
pump.stop()      # abort the move in progress; next op re-initializes
pump.summary()   # connection + barrel + materials, one dict
```

The pump clears its error status once reported — **whoever reads it
first holds the only copy**. `stop()` aborts a move mid-stroke; position
is untrustworthy afterwards, so the next op re-initializes first.

The station implements the Device protocol, so a claimed pump gets the
full treatment for free: a red bus row and a paused runtime when a
`critical` pump drops mid-run, AutoRecover retrying in the background,
and a Recover button that reconnects **and re-homes** — a recovered
pump is a pump in a known state. Recovery never auto-resumes the run;
that decision stays with the operator. `release_pump` closes the port
cleanly so unplugging doesn't alarm.

Operator panel buttons: **Initialize · Stop · Empty · Report ·
Reconnect · Release**.

Sim is orthogonal to connection (`device-guide.md` §16): the bus dot
keeps showing hardware truth while sim tracks the plunger and refuses
overflows, so a sim run exercises the same barrel accounting the real
drive does. **Sim also takes real time**: every modeled move blocks for
the duration the real move would take — stroke fraction × the manual's
table 8-33 seconds at the current speed (×8 on SF high-resolution),
valve moves at the spec's 250 ms per 120° of rotation, homing charged a
turn plus the expel. A sim protocol therefore has honest timing for the
scheduler, and `stop()` cuts a sim move short exactly like the real
`T`. A plumbed nozzle never sees the flag — the station branches once.

---

## 7. In a protocol

The recipe exposes the same calls, plus nothing new to learn:

```python
rcp["pump"].prime(to_port="needle")
rcp["pump"].aspirate(50, port="reservoir")
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

The Start form's kwargs override the scene per run:
`initialize(syringe_volume_ul=…)` for a barrel swap and
`set_speed(…)` (0-100), both applied by `Start` before the first move.

---

## 8. Gotchas

- **`syringe_volume_ul` is the sweep, not the label.** Get it wrong and
  every volume is off by the same ratio, with no error anywhere.
- **`variant` cannot be detected.** A `smooth_flow` drive declared as
  `standard` scales every move by 8x, silently. Always declare it.
- **An undeclared port is refused.** That is the feature: the
  `valve_ports` map is the single statement of what is plumbed, so a
  typo'd port is a loud `PortError`, not a silent dispense into a blind
  hole.
- **Dispense with no port needs exactly one outlet.** With several, name
  it — or better, go through the nozzle's `PumpLink`, which owns its
  port.
- **An outlet's `tube_volume_ul` includes the needle.** Its definition
  is valve→needle *tip*, and the bore is not negligible (~7 µL for a
  22G x 2", ~44 µL for a 16G x 4"). `needle_gauge`/`needle_length` on
  the nozzle are declarative only — swapping the needle means updating
  the pump's `tube_volume_ul` too, or `prime` mis-sizes silently (§3).
- **Homing expels the barrel** through the output port — clear the deck
  before `initialize()`, and remember `recover()` homes too.
- **`valve_type` mismatches refuse to home.** The DIPs own the body;
  fix the switches (then power-cycle) or the scene.
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
- `docs/manuals/PSD4_Manual_8892-01b.pdf` — the drive itself (speed
  table 8-33, valve DIPs, command set)
- `examples/pump` — one pump, a carried needle (port 3) and a
  fixed nozzle over its own cup (port 2)
