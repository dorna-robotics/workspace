# PSD/4 doc

How to use the Hamilton PSD/4 syringe pump. The same API is available at
every level — `PSD4Station` directly (notebook, scripts), the
`syringe_pump_psd4` component (scene), and the recipe (protocols) — same
method names, same µL units, same 0-100 speed everywhere.

## Setup

```python
from workspace.components.pump.psd4_station import PSD4Station

pump = PSD4Station(
    port='/dev/serial/by-id/usb-FTDI_FT232R_USB_UART_BG01X3BN-if00-port0',
    address=1,                # rotary switch position on the back
    syringe_volume_ul=100.0,  # what the 30 mm stroke SWEEPS
    variant='smooth_flow',    # 'standard' or 'smooth_flow'
    high_resolution=False,
    simulation=False,
)
pump.recover()                # open the port, configure, home
```

What each parameter means and how to get it right:

| Parameter | Meaning | Getting it right |
|---|---|---|
| `port` | the USB RS-232 adapter | use the `/dev/serial/by-id/...` symlink — it survives replugs |
| `address` | rotary switch **position** on the back of the pump, 0-9 / A-F | read it off the switch; 0 is factory |
| `syringe_volume_ul` | µL the pump's fixed 30 mm stroke **sweeps** | NOT always the barrel label: PSD-specific syringes sweep their full nominal volume, but 1700-series gastights have ~60 mm of travel, so the stroke sweeps only half of nominal (a 1725/250 µL sweeps ~125). Wrong value = every volume off by the same ratio, silently |
| `variant` | which pump drive this is | `'standard'` = PSD/4, PSD/6 high-torque; `'smooth_flow'` = PSD/4 SF / PSD/6 SF (PN 97709-xx). The pump cannot report it; wrong value scales every move 8x, silently |
| `high_resolution` | step mode | leave `False`. High-res gives 8x finer steps but 8x slower strokes (~18 s vs ~2.3 s at full speed), and standard already resolves ~0.004 µL on a 100 µL sweep — far below the barrel's ±1% accuracy |
| `simulation` | sim intent | sim tracks the plunger and refuses overflows, so protocols test for real; the connection dot keeps showing hardware truth either way |
| `output_right` | which side "output" means | matches the plumbing: `True` = output on the right (viewed from the front). Wrong value sends fluid the wrong way with no error |

**Valve type is not a parameter.** DIP switches 4-6 on the back declare it,
and `initialize()` reads and adopts what the pump reports (`pump.valve_type`):

| DIP 4 | DIP 5 | DIP 6 | Type | Valve |
|---|---|---|---|---|
| off | off | off | 0 | 3-way 120° Y (factory) |
| ON | off | off | 1 | 4-way 90° T |
| off | ON | off | 2 | 3-way 90° distribution |
| ON | ON | off | 3 | 8-way 45° |
| off | off | ON | 4 | 4-way 90° / 4-port wash |

DIPs are read at power-up — power-cycle after changing them.

## Initialize

```python
pump.initialize()
```

Homes the plunger and valve. Required after every power-up and after any
stop or stall — until then the pump refuses syringe moves. `recover()`
runs it as part of connecting. Homing moves the valve and expels the
barrel through the output port, so clear the deck first. The last speed
set (default 100) is re-applied, so a freshly homed pump is always at a
known speed.

To switch barrels without re-declaring the scene:

```python
pump.initialize(syringe_volume_ul=125.0)
```

## Moving liquid

All volumes are µL. Every call **blocks until the motion is finished**
and returns `True`/`False` instead of raising — check the return in
protocols. Barrel limits are enforced in software (the pump itself
won't): overfilling or over-dispensing is refused with `False` and no
motion.

```python
pump.aspirate(50, port=1)        # valve to port 1, draw 50 µL more
pump.dispense(20, port=2)        # valve to port 2, push 20 µL out
pump.move_to_volume(30, port=3)  # absolute: end up holding exactly 30
pump.empty(port=3)               # move_to_volume(0), named for the common case
pump.prime(2, from_port=1, to_port=3)  # 2 full-barrel flush cycles
pump.volume()                    # µL currently held
```

- `port` accepts a number (`1`..N around the valve), a logical name
  (`"input"`, `"output"`, `"wash"`, ...), or an angle (`"90deg"`). Omit it
  to move the plunger through wherever the valve already points.
- **Name the port on dispense and empty** — an unqualified call dumps
  wherever the valve happens to be.
- `aspirate`/`dispense` are relative ("50 more"); `move_to_volume` is
  absolute — use it when the starting volume is unknown, e.g. first move
  of a protocol.
- `prime(n)` sweeps full fill/empty cycles at whatever volume is
  declared (0-100 on a 100 µL barrel, 0-250 on a 250), from any starting
  volume. Use it to flush air after connecting the fluid path.
- A 3-port distribution valve has no bypass position — `valve("bypass")`
  is refused by the pump.

## Speed

```python
pump.set_speed(100)   # 0-100: 100 = fastest preset, 0 = slowest
```

Normalized across variants and resolution modes. At 100 a full stroke is
~2.3 s on this bench; the 40-preset ladder is roughly geometric, so
mid-scale is much closer to the slow end. The value is remembered and
re-applied at every initialize. Applies to the next move, not one
already running.

## Status and connection

```python
pump.status()    # {"ready": bool, "error": int, "error_text": str}
pump.recover()   # (re)connect + home — what the bus Recover button runs
pump.release()   # close the port cleanly so unplugging doesn't alarm
```

The pump clears its error status once reported — whoever reads it first
holds the only copy. `stop()` aborts a move mid-stroke; position is
untrustworthy afterwards, so re-initialize before the next move.

Operator panel buttons: **Initialize · Volume · Status · Release**.

## In a protocol

The recipe exposes the same calls plus nothing extra to learn:

```python
rcp["pump"].prime(2, from_port=1, to_port=3)
rcp["pump"].aspirate(50, port=1)
rcp["pump"].dispense(50, port=3)
```

The Start form's kwargs override the scene per run: `syringe_volume_ul`
(barrel swap) and `speed` (0-100), both applied by `Start` before the
first move.
