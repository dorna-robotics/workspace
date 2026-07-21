# tmove — PVT trajectory command: firmware specification

Status: host side implemented and tested (workspace `pp` branch; TOPP-RA
parameterization in `core.traj_points`, behavioral reference executor in
`SimulationAPI.tmove`). This document specifies the controller-firmware
side for implementation.

## 1. Purpose

`tmove` executes a **time-parameterized joint trajectory**: the host
sends timed position samples; the firmware plays them back. All motion
intelligence — path planning, collision checking, corner fillets,
velocity/acceleration profiling — happens on the host **before** the
command is sent. The firmware does NOT fit splines, compute profiles,
or make speed decisions. It interpolates between samples at the servo
tick and tracks. tmove is deliberately *simpler* than smove.

Why it exists: smove applies one global S-curve over the whole path, so
it carries full cruise speed through corners — measured commanded
accelerations up to 7x the configured cap (1807 deg/s^2 on the rail
axis vs a 250 cap on a real workflow path). The host-side profile
(TOPP-RA) bounds every joint's velocity AND acceleration everywhere on
the path at equal cycle time. The firmware just has to reproduce it
faithfully.

## 2. Host guarantees (what firmware may assume)

The host guarantees, for every stream it sends:

- G1. The trajectory starts at the robot's current joint position
  (within encoder noise) and starts and ends at zero velocity.
- G2. Samples are on a **uniform time grid**: sample `i` is the target
  position at `t = i * dt` after execution start.
- G3. Per-joint velocity and acceleration implied by the samples are
  within the machine's limits (host caps are configured below firmware
  hard limits).
- G4. The geometry was collision-checked before sending.

Firmware still validates (section 6) — guards against host bugs — but
must not "improve" the trajectory (no re-profiling, no smoothing that
leads rather than lags, no corner cutting).

## 3. Wire protocol

Three commands. JSON, one object per line, same transport and framing
as every existing command. All joint values are degrees (aux/rail axes:
mm), floats, up to 8 axes (j0..j7).

### 3.1 Header — allocate and describe the stream

```json
{"cmd": "tmove", "id": 4821, "n": 224, "dt": 0.01, "axes": 8}
```

| field | type | meaning | valid range |
|---|---|---|---|
| `n` | int | total number of samples that will follow | 2 .. 10000 |
| `dt` | float s | uniform sample spacing | 0.005 .. 0.1 |
| `axes` | int | joints per sample | 1 .. 8 |

Effect: firmware allocates/clears the trajectory buffer, records the
header, enters state `LOADING` for this `id`. A header while another
tmove is LOADING or RUNNING is an error (one trajectory at a time).

### 3.2 Data — chunked sample upload

```json
{"cmd": "tdata", "id": 4821, "i": 0, "points": [[j0..j7], [j0..j7], ...]}
```

| field | meaning |
|---|---|
| `i` | index of the FIRST sample in this chunk (0-based) |
| `points` | consecutive sample rows, each `axes` floats |

- Chunks arrive in order, no gaps: chunk k starts where k-1 ended
  (`i` equal to samples received so far). Out-of-order or gapped `i`
  → error, discard stream.
- Recommended host chunk size: 50 rows (~4 KB JSON) — keeps every
  command far below the receive-buffer ceiling. Firmware must accept
  any chunk size whose command fits the normal receive buffer.
- No `t` values on the wire: time is implicit from the header grid
  (`t_i = i * dt`). This halves the payload.

### 3.3 Start — execution trigger

Execution starts automatically when sample `n-1` has been received and
the whole stream passed validation. No separate start command. (If the
host wants a delayed start it simply delays the upload.)

### 3.4 Replies

Follow the existing motion-command status convention on the header
`id`:

- On header accept: `{"id": 4821, "cmd": "tmove", "stat": 0}`
- On execution start (stream complete + valid): `stat: 1`
- On completion (final sample reached, position settled): `stat: 2`
- On any rejection/abort: existing error/alarm reporting with a
  distinct error code per failure class (section 6).

`tdata` commands are acknowledged per the normal command convention but
carry no motion status of their own.

## 4. Execution semantics

Let `F` be the servo/interpolation tick rate the firmware already uses
to generate smove setpoints.

- At tick time `t` since start (`0 <= t <= (n-1)*dt`):
  - `i = floor(t / dt)`, `u = (t - i*dt) / dt`
  - setpoint per joint: `q = rows[i] + u * (rows[i+1] - rows[i])`
    (**linear interpolation** — MVP; see section 8 for the v2
    follower).
- After `t >= (n-1)*dt`: hold `rows[n-1]` as the commanded position,
  report `stat: 2` once the existing settle criterion is met.
- The trajectory clock starts at the tick execution begins; it is not
  gated on the host (no streaming during execution in the MVP — the
  full stream is buffered before start).

Interaction with the rest of the controller:

- **halt**: identical semantics to halting an smove — abort the
  trajectory using the existing halt deceleration behavior, discard
  the buffer, report the standard halted status.
- **alarm**: any alarm (following error, limits) aborts and discards,
  exactly like smove.
- **Digital outputs with `"queue": 0`** execute on the IO thread during
  a tmove, unchanged (the host relies on this for overlapped gripper
  IO).
- **Queued commands**: a tmove occupies the motion queue like one
  motion command. Commands queued behind it run after `stat: 2`.

## 5. Memory budget

Worst case buffer: `10000 samples x 8 axes x 4 bytes (float32) =
320 KB`. If that exceeds available RAM, reduce the max `n` accordingly
and state the limit — the host adapts by using a larger `dt` (e.g.
50 ms samples for long motions; its interpolation error over 50 ms at
the accel cap is < 0.05 deg, invisible). A practical minimum ceiling:
`n_max >= 2000` (20 s of motion at 10 ms).

float32 per sample is sufficient (encoder resolution >> float32
epsilon at these magnitudes).

## 6. Validation (reject BEFORE motion, never during)

All checks run on upload; a stream that passes executes without
further rejection logic. Each failure class gets its own error code so
the host log names the cause.

- V1 **Header sanity**: `n`, `dt`, `axes` in range.
- V2 **Sequence integrity**: chunks in order, no gaps, exactly `n`
  rows total, every row exactly `axes` values, all values finite.
- V3 **Start-pose match**: `|rows[0][j] - current_position[j]| <=
  0.5 deg (mm)` for every joint. Prevents a start jump.
- V4 **Position limits**: every sample within the configured joint
  limits.
- V5 **Velocity guard**: for every consecutive pair,
  `|rows[i+1][j] - rows[i][j]| / dt <= vel_hard_limit[j]`.
- V6 **Acceleration guard**: second difference
  `|rows[i+1][j] - 2*rows[i][j] + rows[i-1][j]| / dt^2 <=
  acc_hard_limit[j]`.
- V7 **Upload timeout**: if a LOADING stream receives no tdata for
  5 s, discard and report an error (host died mid-upload).

V5/V6 use the firmware's own hard limits (not the host's soft caps) —
they are a guard against host bugs, not a re-profiling step.

## 7. Edge cases

- `n = 2`: legal — a single linear segment.
- All samples identical: legal — hold position for `(n-1)*dt`, then
  `stat: 2`.
- Duplicate consecutive samples inside a stream: legal (zero velocity
  interval). No spline math exists, so the zero-length-segment failure
  mode of smove (alarm −110 class) cannot occur by construction.
- Header re-sent with the same `id` while LOADING: error, discard.
- tdata for an unknown/completed `id`: error, ignore rows.
- Power-of-two or odd `n`: no special cases; only the range matters.

## 8. v2 (after MVP is proven) — jerk-limited tracking

Replace the direct linear interpolation with a per-joint jerk-limited
follower: the interpolated sample stream becomes the follower's moving
target; the follower's own vel/acc/jerk caps (config, per joint) round
the acceleration steps at sample boundaries.

Requirements that keep it correct:
- The follower may only **lag and round** — it must never lead the
  target or increase speed above the sample-implied speed (a host-side
  experiment with time-axis smoothing was rejected precisely because
  it sped up corners; the same failure is possible in a bad follower).
- Following error between follower output and raw interpolation is
  bounded and configurable; exceeding it is an alarm.

This is also where jerk control belongs for the whole system — the
host intentionally does not jerk-bound its profile.

## 9. Acceptance tests

1. **Golden playback**: given a reference stream (host can generate
   these from the sim executor on request), commanded setpoints at
   every tick match linear interpolation exactly (float32 tolerance).
2. **Chunk fuzz**: same stream split at every chunk size 1..n arrives
   identically; any out-of-order/gapped upload is rejected with the
   right error.
3. **Guard triggers**: streams violating V3..V6 (one violation each)
   are rejected before any motion.
4. **Halt mid-trajectory**: robot decelerates per existing halt
   behavior; buffer discarded; next tmove works.
5. **IO overlap**: `output` with `queue: 0` fires during a running
   tmove within one IO-thread tick.
6. **Back-to-back**: 100 consecutive tmoves without reconnect or
   memory growth.
7. **Size ceiling**: a 10000-sample stream (or the stated `n_max`)
   uploads and runs; every individual command stays under the receive
   buffer limit.

## 10. What the host will send in practice

Typical fused hop today: 50–120 knots → 200–450 samples at 10 ms,
1.8–4.1 s of motion, velocities <= 100 deg/s, accelerations <= 250
deg/s^2 (recipe-configured; firmware hard limits sit above these).
The host currently emits a final sample at the exact trajectory end
time; it will be conformed to the uniform grid (host-side change,
already planned) so the wire format above holds exactly.
