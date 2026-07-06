# Rail Calibration

How to measure the rail homing offset for a Core-on-rail bench. The
result is a single number that goes into the core's `rail_cfg.offset`
in the scene yaml — it makes `home_with_stop` land the carriage at the
true zero of the bench after every homing run.

The bench for this procedure is the `rail_calibration` example
(`workspace/projects/examples/rail_calibration/`): a bare core_500
chassis with one `probe_rail_calibration` stand mounted at
**fixture_plate_2 / G3** (`hole_0` on the plate hole).

---

## Procedure

1. **Home the rail** — direction **negative**, homing value **0**:
   the carriage drives into the hard stop and that position is taken
   as rail zero for the rest of the procedure.

   ```python
   rt.home_with_stop(index=6, val=0, dir=-1)
   ```

2. **Turn the motor off** so the robot can be moved by hand:

   ```python
   rt.motor(0)
   ```

3. **Mount the probe calibration tool** — the `probe_rail_calibration`
   stand, `hole_0` onto **fixture_plate_2 / G3**, exactly as in the
   `rail_calibration` example scene:

   ```yaml
   probe_rail_calibration_1:
     type: "probe_rail_calibration"
     attach:
       parent_name: "fixture_plate_2"
       parent_solid: "body"
       parent_anchor: "G3"
       child_solid: "body"
       child_anchor: "hole_0"
       offset: [0, 0, 0, 0, 0, 0]
   ```

4. **Back the robot off along the rail toward the probe until it
   touches it**, and record the rail joint value at contact — the rail
   is **j6** on a standard setup (`rail_cfg.axis: 6`).

5. **Compute the offset** from the recorded rail value, by rail
   length:

   | Core rail | Offset formula |
   |---|---|
   | Rail 500  (`rail_hd_500mm`)  | `-148 − rail_value` |
   | Rail 1000 (`rail_hd_1000mm`) | `-198 − rail_value` |
   | Rail 2000 (`rail_hd_2000mm`) | `-398 − rail_value` |

6. **Record the offset** in the core's scene yaml (`rail_cfg.offset`):

   ```yaml
   core:
     has_rail: true
     rail_cfg:
       type: "rail_hd_500mm"
       offset: <computed value>   # from step 5
       ...
   ```

From then on, every startup homing (`set_axis_with_stop(core.rail_cfg)`
in a project's Start action) passes this offset to `home_with_stop`, so
the carriage's zero matches the physical bench.

---

## Worked example

Rail 500 bench. After homing to the stop (`val=0, dir=-1`) and touching
the probe by hand, the pendant shows the rail at `j6 = -121.7`:

```
offset = -148 − (-121.7) = -26.3
```

Set `rail_cfg.offset: -26.3` in the scene yaml and relaunch.
