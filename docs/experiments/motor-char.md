# `motor-char` — Story 1.5, per-motor characterisation

**Registry ID:** `motor-char` · **Story:** 1.5 · **Status:** specified, not yet run

> **The question.** For each of the four N20 motors: at what duty does the shaft
> first turn, what steady-state output speed does each duty produce in each
> direction, how much current does it draw, and **how much do the four motors
> differ from each other?**
>
> That last clause is the point. Four "identical" motors never are. Quantifying
> the difference is what motivates closed-loop control in Story 1.6 — and the
> curve itself becomes the **feedforward** term there, so this experiment is
> both a measurement and a component.

**Bench setup, safety and instrument verification:** [`../BENCH.md`](../BENCH.md).
Do not run this experiment without having run BENCH §6.

**This document exists so that two runs a week apart are comparable.** Every
number in it is fixed on purpose. If you change one, you change it for all four
motors, and the ones already run get re-run.

---

## 1. Measurands and units

SI, stated in the identifier ([`../../CLAUDE.md`](../../CLAUDE.md) §Conventions):

| Symbol | Unit | Meaning |
|---|---|---|
| `duty_frac_cmd` | 0.0–1.0 | Duty the firmware was asked for |
| `duty_frac_measured` | 0.0–1.0 | Duty measured at `GP2` on analyser D0 |
| `omega_rad_s` | rad/s | Angular velocity **of the output shaft** (after the 1:100 gearbox) |
| `ticks_cumulative` | ticks | Raw encoder count. **Recorded raw. Never only as a derived speed** |
| `dt_s` | s | The interval `delta_ticks` was accumulated over |
| `vm_volts` | V | Measured at the TB6612's `VM` pin |
| `current_a` | A | Supply current into `VM` |
| `loop_period_s` | s | Measured control-loop period |
| `deadband_duty_frac` | 0.0–1.0 | Lowest duty at which the shaft turns |

**Output shaft, not motor shaft.** The encoder is on the *motor* shaft, so
every count is divided by the 1:100 gearbox before it becomes an output speed:

```
omega_rad_s = 2*pi * (delta_ticks / ticks_per_output_rev) / dt_s
```

`ticks_per_output_rev` comes from Story 1.4 (`quad-phase` / `ticks-per-m`) and
is **still disputed** — Adafruit says 14 counts/rev on the motor shaft,
retailers say 11, and the decoder may count 1, 2 or 4 edges, giving anywhere
from 1100 to 5600 counts per output revolution
([`../HARDWARE.md`](../HARDWARE.md) §2.1).

> ### 🔴 The single most important recording decision in this experiment
> **Log `ticks_cumulative` and `dt_s` as raw columns, and put
> `ticks_per_output_rev` in the CSV *header*, not baked into the numbers.**
>
> If Story 1.4's constant is later corrected — and there is a real chance it
> will be, by a factor of exactly 2 or 4 — every CSV can be **rescaled in one
> line** instead of every motor being re-run. A derived-only log is a log you
> have to repeat. This costs two columns.

---

## 2. Fixture and conditions

| Parameter | Value | Why this value |
|---|---|---|
| `VM` at the driver | **5.00 V** | The rover's rail is 5 V; the motor window is 4.5–6 V; the H-bridge drops ~0.5 V so the motor sees ~4.5 V loaded. **Characterise at the voltage you will drive at.** *(Assumption — see §9)* |
| PSU current limit | **1.00 A** | Above anything one N20 should draw, below the TB6612's 1.2 A continuous rating |
| PWM frequency | **20 kHz** | Above audible; short enough that the winding inductance filters the current |
| Control loop | **100 Hz**, `dt_s` nominal 0.010 | Verified on analyser D6 before each run |
| Load | **None.** Shaft free, nothing on it | This is a motor characterisation, not a drivetrain one. Adding a wheel adds an unmeasured load |
| Mounting | Clamped or firmly taped, **same way for all four** | Mounting changes friction *and* the thermal path. Both move the curve |
| Telemetry | UART 115200, **50 Hz** ASCII rows | See §5 for the bandwidth arithmetic |
| Analyser | 4 MSa/s, short windows only | See §7. Continuous capture is not possible — [`../BENCH.md`](../BENCH.md) §5.3. **4 MSa/s is a choice, not a device limit: confirm it is in this device's own samplerate list (`--driver fx2lafw --show`, BENCH §5.1) and read the rate back out of the `.sr`, because a silently rounded rate rescales every interval you measure** |

**Shaft direction sanity:** before the run, command duty 0.30 forward for one
second and confirm the shaft turns and the encoder counts **increase**. If it
counts down, fix the sign **in firmware** and record that you did — never by
swapping the yellow/green wires ([`../BENCH.md`](../BENCH.md) §8 row 15).

---

## 3. Warm-up — and why it is not optional

**Procedure: 60 s at `duty_frac` 0.50 forward, then 30 s at rest. Discard all
of it.**

A cold gearbox is a slower gearbox. The grease in a 1:100 N20 gear train is
stiff at room temperature and thins as it warms; brush contact resistance also
settles. The observable consequence is that **the first few points of a cold
sweep read slower than the same points read ten minutes later** — which
masquerades as a larger deadband and a shallower slope.

If you skip the warm-up, motor 1 (measured cold, first thing) and motor 4
(measured on a bench that has been running for an hour) differ by an amount that
has nothing to do with the motors. That is precisely the error this whole
experiment exists to measure, so contaminating it with a warm-up artefact makes
the result worthless.

**The same warm-up for every motor. No exceptions, including for the motor you
are re-running because something went wrong.**

---

## 4. The sweep

### 4.1 Duty steps

Two sweeps, because the interesting region is not evenly spread:

| Sweep | Range | Step | Points | Why |
|---|---|---|---|---|
| **Coarse** | `duty_frac` 0.00 → 1.00 | 0.05 | 21 | The main duty→speed curve. 0.05 is fine enough to see curvature and coarse enough to run in a reasonable time |
| **Fine (deadband)** | `duty_frac` 0.00 → 0.25 | 0.01 | 26 | The deadband is the most interesting single number here and 0.05 resolution cannot locate it. A deadband quoted as "somewhere between 0.10 and 0.15" is not a number you can put in a feedforward term |

### 4.2 Dwell and what is discarded

**Dwell = 1.5 s per step.**

| Portion | Duration | Treatment |
|---|---|---|
| Transient | first **0.5 s** | **Discarded.** Not logged as steady state |
| Steady state | last **1.0 s** | Averaged ⇒ one row in the results table (50 telemetry samples at 50 Hz) |

**Why 0.5 s, and how to check it rather than assume it.** A small DC motor's
mechanical time constant `tau_m` (the time to reach 63% of final speed after a
step) is tens of milliseconds; the 1:100 gearbox adds inertia and friction but
does not change the order of magnitude. 0.5 s is therefore >5·`tau_m` and the
motor is at steady state well inside it.

**Do not take that on faith. Measure it once:** capture one step from
`duty_frac` 0 → 0.6 at 100 Hz telemetry, plot speed vs time, and read `tau_m`
off it (the time to 63% of final). **If 0.5 s is not at least 5·`tau_m`,
lengthen the discard for all four motors and re-run the ones already done.**
That plot is also the "before" step response for Story 1.6, so it costs nothing.

### 4.3 Order — and the hysteresis it exists to catch

**Within one direction: ascending sweep, then immediately descending.**

Static friction (stiction) is larger than kinetic friction. So:

- **Breakaway duty** — sweeping *up* from zero, the duty at which the shaft
  first moves. Must overcome *static* friction.
- **Dropout duty** — sweeping *down* from motion, the duty at which the shaft
  stops. Only has to overcome *kinetic* friction, so it is **lower**.

**These are two different numbers and both are real.** Their difference is a
direct measure of stiction, it differs between motors, and if you only sweep up
you will report the breakaway duty as "the deadband", then be surprised in
Story 1.6 when the feedforward term overshoots at low speeds.

The full order for one **block**:

```
  1. FWD, coarse ascending   0.00 → 1.00   (21 steps)
  2. FWD, coarse descending  1.00 → 0.00   (21 steps)
  3. FWD, fine  ascending    0.00 → 0.25   (26 steps)
  4. FWD, fine  descending   0.25 → 0.00   (26 steps)
     ── coast 10 s ──
  5. REV, coarse ascending   (21)
  6. REV, coarse descending  (21)
  7. REV, fine  ascending    (26)
  8. REV, fine  descending   (26)
     ── coast 10 s ──
```

**Between phases, and at every zero-duty point: COAST (`IN1`=`IN2`=`0`), never
BRAKE (`1`/`1`).** Three reasons, and they are worth being able to say out loud:
brake shorts the windings so the motor's own back-EMF drives current through the
H-bridge; a brake immediately followed by the opposite direction is the largest
current transient this bench can produce; and coasting gives every step the
**same initial condition** — at rest, no residual field — which is what makes
steps comparable.

### 4.4 Repeats

**3 blocks per motor, back to back.** Report the **median** of the three at each
duty, and the **spread** (max − min) as the error bar.

Why three and not two: with two you can see that they disagree but you cannot
tell which is the outlier. Why median and not mean: one block spoiled by a
mechanical snag should not drag the answer.

**Block 3 vs block 1 is also your drift check.** If they differ by more than the
within-block spread, something changed over the ~16 minutes — thermal, supply,
or break-in — and §8 tells you what that invalidates.

### 4.5 Run time

```
per direction : (21 + 21 + 26 + 26) steps x 1.5 s   = 141 s
per block     : 2 directions + 2 x 10 s coast       = 302 s   ~ 5 min
per motor     : 3 blocks + 90 s warm-up             = 996 s   ~ 17 min
campaign      : 4 motors + 3 swaps at ~5 min        ~ 85 min
              + the M1 re-run (section 8) + 1 swap  ~ 22 min
              = ~105-110 min
```

**The M1 re-run is not optional and it is not free** — it is the repeatability
control in section 8, it is a full motor run, and it needs its own swap. Budget
it or you will skip it at the end of a long evening, which is exactly when the
bench has drifted the most.

**Budget an unhurried two and a half hours** — the arithmetic above is run
time only, before the §6 verification ladder at each swap — and do all four
plus the M1 re-run in one sitting. Splitting the
campaign across two days introduces exactly the between-run variation the
experiment is trying to measure.

---

## 5. Telemetry bandwidth — why 50 Hz and not 100 Hz

The control loop runs at 100 Hz, so the temptation is to log at 100 Hz.

```
UART 115200 8N1  ->  115200 / 10 bits per byte  =  11,520 bytes/s
one ASCII CSV row, all columns                  ~     60 bytes
100 Hz x 60 B = 6,000 B/s   =  52% of the link
 50 Hz x 60 B = 3,000 B/s   =  26% of the link
```

100 Hz *fits*, but with only 2× headroom on a link that is also carrying
commands — and a UART write that blocks because the buffer is full stalls the
control loop, which then shows up on analyser D7 as a `COMPUTE_BUSY` excursion
([`../BENCH.md`](../BENCH.md) §3.1). **Logging harder makes the thing you are
measuring worse.** That is worth naming: the instrumentation is part of the
system.

**⇒ 50 Hz for the sweep.** Each dwell is averaged over 1.0 s anyway, so 50
samples per point is ample and the extra 50 buy nothing.

> ⚠️ **Decimating the telemetry must not decimate the ticks.** If the firmware
> emits every second loop iteration, `delta_ticks` on the row must be the ticks
> accumulated over **both** iterations and `dt_s` must be 0.020 — that is what
> §1 means by "the interval `delta_ticks` was accumulated over". Report one
> iteration's ticks against two iterations' time and every speed is out by a
> factor of two, uniformly, silently. This is the same trap as the `LOOP_TICK`
> one ([`../BENCH.md`](../BENCH.md) §3.1) wearing different clothes; logging
> `ticks_cumulative` as well is what lets you check it after the fact.

> ### ⚠️ Do NOT carry this decimation into Story 1.6
> A step response needs **every** sample — the rise is over in tens of ms and
> at 50 Hz you get about three points on it. For step responses, log at the
> **full 100 Hz into a RAM buffer** for a 2-second window and dump it over the
> UART **afterwards**, when the timing no longer matters.
> See [`pid-tuning.md`](pid-tuning.md) §5.

---

## 6. What is recorded

> ### 🔴 UNRESOLVED: this schema does not match the tooling's schema
> Three streams currently describe the same telemetry three ways, and they were
> written in parallel:
>
> | | Time | Duty | Encoder | Supply | Loop period |
> |---|---|---|---|---|---|
> | **This spec** (§6.1) | `t_s` | `duty_frac_cmd` | `ticks_cumulative` | `vm_volts` | `loop_period_s` |
> | `tools/analysis/load.py` | `t_s` | `duty_frac` | `counts` | `supply_v` | `loop_dt_s` |
> | Firmware (`protocol.c`) | `T <t_us> …` | 3rd field | 2nd field | not sent | not sent |
>
> The loader also wants a sidecar **`manifest.json`** (`params.supply_voltage_v`,
> `pwm_hz`, `loop_hz`, `ticks_per_output_rev`) where §6.1 puts `#` comment lines
> inside the CSV.
>
> **Reconcile this before motor 1, not after motor 4.** Whoever owns the capture
> path picks one set of names and the other two follow; a rename before the
> campaign is a five-minute edit, and after it is four unloadable runs or a
> conversion script nobody trusts. The *content* required here — raw ticks, the
> interval they were accumulated over, and every parameter that makes runs
> comparable — is not in dispute and does not change whichever names win.

### 6.1 CSV, one file per motor

`experiments/motor-char/<motor_id>-<utc-timestamp>.csv`

**Header (comment lines) — these are what make two runs comparable:**

```
# baby-rover motor-char schema v1
# motor_id=M1
# utc_start=2026-09-02T04:11:07Z
# utc_end=2026-09-02T04:28:31Z
# firmware_sha=<git rev-parse HEAD>
# bench_doc=docs/BENCH.md@<git sha>
# analyser_channel_map=bench-2026-08-31     # BENCH.md section 3
# analyser_samplerate_hz=<read back from the .sr metadata, NOT from the
#                         command line: a rate that was silently rounded
#                         down rescales every interval measured under it>
# vm_volts_setpoint=5.00
# vm_volts_measured_at_driver=5.02
# psu_current_limit_a=1.00
# pwm_hz_nominal=20000
# loop_hz_nominal=100
# dwell_s=1.5   discard_s=0.5   blocks=3
# coarse=0.00:0.05:1.00   fine=0.00:0.01:0.25
# ticks_per_output_rev=UNMEASURED  # literal string until Story 1.4 measures it.
#                                  # NEVER write a number here that was not counted
#                                  # off a hand-turned output shaft. 11 vs 14 is
#                                  # unresolved and the decode factor multiplies it
#                                  # again: the candidates span 1100-5600.
# encoder_decode_edges=<1|2|4>     # the scheme the firmware ACTUALLY counts, read
#                                  # off the PIO program, not assumed. It is a
#                                  # factor in the line above, not independent of it.
# motor_r_ohms_red_white=<measured>
# motor_black_blue=<open/measured>
# ambient_c=22
# notes=<anything that happened>
```

**Rows:**

```
t_s,block,phase,direction,duty_frac_cmd,duty_frac_measured,
ticks_cumulative,delta_ticks,dt_s,omega_rad_s,vm_volts,current_a,loop_period_s
```

- `phase` ∈ `warmup` · `coarse_up` · `coarse_down` · `fine_up` · `fine_down`
- `direction` ∈ `fwd` · `rev`
- `omega_rad_s` is **derived and redundant** — it is there for convenience, and
  `ticks_cumulative` + `dt_s` + the header constant are the authority.
  **While `ticks_per_output_rev=UNMEASURED`, leave this column empty rather than
  filling it from a guess.** An empty column is honestly unconverted; a column
  computed from a placeholder is a wrong number that looks like a measurement,
  and it will be plotted, quoted and believed. Story 1.4 lands, then one rescale
  pass fills it for every run at once
- `loop_period_s` is the firmware's own measurement; analyser D6 is the
  independent check on it

### 6.2 Derived results table, one row per (motor, direction, duty)

`experiments/motor-char/summary.csv` — median and spread across the 3 blocks:

```
motor_id,direction,sweep,duty_frac_cmd,duty_frac_measured_median,
omega_rad_s_median,omega_rad_s_min,omega_rad_s_max,current_a_median,n_blocks
```

### 6.3 Analyser captures — short, named, and few

Not continuous (BENCH §5.3). Take **five** captures per motor, 3 s each:

| File | Duty | Question it answers |
|---|---|---|
| `<m>-verify-loop.sr` | idle | Is the loop at 100 Hz *for this motor's run*? (D6, D7) |
| `<m>-duty020-fwd.sr` | 0.20 | Quadrature at low speed; is duty at the pin what was asked? |
| `<m>-duty050-fwd.sr` | 0.50 | Mid-range reference point, all 8 channels |
| `<m>-duty100-fwd.sr` | 1.00 | Top speed: highest encoder edge rate, worst case for the decoder |
| `<m>-reversal.sr` | 0.50 fwd→rev | Direction-bit deadtime (D1/D2) and encoder phase reversal (D4/D5) |

**The cross-check that makes the two streams worth having.** For each capture,
derive the speed from the analyser alone and compare it against the telemetry's
`omega_rad_s` over the same window. The decoders confirmed present on this
machine ([`../BENCH.md`](../BENCH.md) §5.5) do it directly:

```bash
# quadrature count and rate, straight off D4/D5 - firmware not involved.
# `edges=` is edges per rotation, i.e. ticks_per_output_rev; it only feeds the
# turns/rpm rows. Until story 1.4 measures it, OMIT the option (default 0) and
# read the raw count and interval rows instead of inventing a value to pass.
sigrok-cli -i m1-duty050-fwd.sr \
           -P graycode:d0=D4:d1=D5 \
           -A graycode | tail

# or just count edges and do the arithmetic yourself
sigrok-cli -i m1-duty050-fwd.sr -P counter:data=D4:data_edge=any \
           -A counter=edge_counts | tail -1

# and confirm the loop that produced the telemetry was actually at 100 Hz
sigrok-cli -i m1-verify-loop.sr -P timing:data=D6:edge=any:avg_period=100 \
           -A timing | tail -5
```

**They must agree within the quantisation of the shorter window.** They are two
independent paths to the same physical quantity, and a disagreement is
information: a **constant ratio** points at `ticks_per_output_rev` or `dt_s`; a
**growing divergence** points at missed edges or counter overflow
([`../BENCH.md`](../BENCH.md) §8 row 11). Record the comparison — it is the
evidence that the telemetry can be trusted for the other 17 minutes of the run,
where there is no analyser watching.

### 6.4 Run log

`experiments/motor-char/run-log.md` — the lab notebook. Per BENCH §7: motor ID,
UTC times, both resistance measurements, measured `VM`, firmware SHA, sample
rate, and **everything that went wrong, including what you fixed**. A mid-
campaign fix is a parameter change.

### 6.5 Registry

A row in [`../../experiments/REGISTRY.md`](../../experiments/REGISTRY.md)
**when the experiment is run**, not afterwards. Schema:
`| ID | Date | Story | Question | Method | Result | Data |`.
*A result not persisted did not happen.*

---

## 7. Current measurement — including stall, without stripping a gearbox

The supplier claims **~100 mA no-load and ~200 mA stall**, and
[`../HARDWARE.md`](../HARDWARE.md) §2.1 explicitly distrusts the stall figure as
suspiciously low for an N20. It feeds the driver-headroom sum, so it gets
measured and re-tagged.

### 7.1 No-load current

Read the **PSU's current display** at each dwell, or log it if the PSU has a
serial interface. On this bench the PSU supplies `VM` **only** — the Pico is on
USB and the encoder is on the Pico's 3.3 V — so PSU current is motor current
plus the TB6612's few-mA quiescent draw. That is a clean measurement, and it is
clean *because* of how the bench is wired.

**Note the bandwidth caveat honestly:** this is an *average* current. A DMM
"does not reliably average a 20 kHz square wave"
([`../HARDWARE.md`](../HARDWARE.md) §5) — but here it does not have to, because
the winding inductance is doing the averaging (§7.3). Record it as an average
and say so.

### 7.2 Stall current — the safe method, and the arithmetic that comes first

> ⚠️ **Do not clamp the output shaft at full duty.** The 1:100 gearbox means the
> output shaft sees ~100× the motor's torque; holding it stalls the *motor*
> through the whole gear train, and small N20 gearboxes shed teeth. Stall also
> heats the windings in seconds because there is no rotation and no cooling.

**Step 1 — predict it with the multimeter, before powering anything.** A stalled
DC motor is just a resistor: there is no rotation, so there is no back-EMF.

```
I_stall  ~=  V_motor / R_winding
```

You **already measured `R_winding`** — it is the red–white resistance from the
safety check ([`../BENCH.md`](../BENCH.md) §2 rule 6). With the motor seeing
~4.5 V under load, an 8 Ω winding predicts ~560 mA; a 20 Ω winding predicts
~225 mA. *(Illustrative — use your own measured value.)*

**This is a falsifiable prediction made before the experiment**, which is the
house style. If your measured `R_winding` predicts a stall current far above the
supplier's 200 mA, you have contradicted the datasheet with a multimeter and a
division, and the measurement below is a confirmation rather than a discovery.

**Step 2 — measure it at reduced duty and extrapolate.**

1. Clamp the output shaft properly (a vice or a purpose-made holder, **not
   fingers**).
2. Command `duty_frac` **0.20**, forward, for **≤2 s**. Read the PSU current.
3. Coast. **Let the motor cool for 60 s.**
4. Repeat at 0.30 and 0.40, same 2 s / 60 s discipline.
5. Plot `current_a` against `duty_frac`. It should be a **straight line through
   the origin**, because a locked rotor is a resistor and the average applied
   voltage is `duty_frac × V_motor`.
6. **Extrapolate to `duty_frac` = 1.0.** That is `I_stall`. **Record it as an
   extrapolation, and record the R² of the fit.**

**Cross-check:** the extrapolated `I_stall` and the `V/R` prediction from step 1
should agree within ~20%. **If they do not, one of them is wrong and the
disagreement is a finding** — most likely the average-voltage model (§7.3) or a
lead-resistance drop you have not accounted for.

**Never extrapolate silently.** Tag it `[DERIVED]` in `HARDWARE.md`, not
`[MEASURED]`.

### 7.3 The assumption underneath all of this

Everything above assumes the motor responds to the **average** applied voltage
`duty_frac × V_motor`, rather than to the instantaneous PWM square wave. That
holds when the electrical time constant `tau_e = L / R` is long compared to the
PWM period (50 µs at 20 kHz), so the winding current is smoothed rather than
switched.

`R` is measured; `L` is not, and this repo has no figure for it.

**How to test the assumption rather than assert it:** *if the average-voltage
model holds, the duty→`omega_rad_s` curve is a straight line above the
deadband*, because output speed is essentially proportional to applied voltage
for a lightly-loaded DC motor. **Visible curvature in the mid-range is evidence
against it** (or against the current limit, or against a supply sag — check
`vm_volts` first, since that is the cheapest of the three to rule out).

---

## 8. Acceptance criteria

The run is **complete** when all of these exist:

- [ ] **One CSV per motor**, four files, all with a complete §6.1 header, and
      all four headers **identical except** for `motor_id`, the timestamps and
      the per-motor resistance measurements. *(Diff them. This is the machine-
      checkable form of "the four runs were identical")*
- [ ] **`summary.csv`** with medians and spreads across the 3 blocks
- [ ] **One overlay plot**: `omega_rad_s` vs `duty_frac`, all four motors, both
      directions, with the block spread as error bars —
      `experiments/plots/motor-char-overlay.png`
- [ ] **Deadband table**: `breakaway_duty_frac` and `dropout_duty_frac`, per
      motor, per direction — 16 numbers, each with its spread
- [ ] **Linear fit** over the linear region (roughly deadband → 0.9) per motor
      per direction: `omega_rad_s = m * duty_frac + b`, with `m`, `b` and R².
      **`m` and the deadband are exactly what Story 1.6's feedforward consumes**
- [ ] **The spread between motors, quantified**: at `duty_frac` 0.5 forward,
      `(max − min) / mean` as a percentage. **This is the headline number of the
      whole experiment** and the reason closed-loop control exists
- [ ] **No-load current** per motor at 0.5 duty, and **extrapolated stall
      current** with its fit quality — both compared against the supplier's
      100 mA / 200 mA claims, and `HARDWARE.md` §2.1 re-tagged accordingly
- [ ] **Repeatability check**: motor **M1 re-run** at the end of the campaign,
      after the other three. Its two curves must overlay **within the block
      spread**. *(This is the experiment's own control. If M1-second disagrees
      with M1-first, the bench drifted during the campaign and the between-motor
      differences you just measured are not trustworthy)*
- [ ] **Loop timing verified** on analyser D6 for every motor: mean
      **edge-to-edge** interval 10.00 ms (one edge per iteration — 100 edges/s,
      not 200; [`../BENCH.md`](../BENCH.md) §3.1), peak-to-peak jitter recorded
- [ ] **Registry row written** in `experiments/REGISTRY.md`
- [ ] **A paragraph on how much the four motors differ — written by the owner.**
      Claude assembles the numbers and the plots; the prose is the owner's, and
      that is a hard rule ([`../../CLAUDE.md`](../../CLAUDE.md))

---

## 9. Failure modes that invalidate a run

**"Invalidates" means: the data is not comparable to the other motors and must
not be plotted alongside them.** Delete it or move it to a `void/` directory
with a note saying why. Do not quietly keep it.

| # | Failure | How you detect it | What it invalidates |
|---|---|---|---|
| 1 | **Any bench parameter changed mid-campaign** — firmware, `VM`, current limit, probe position, dwell, warm-up, mounting, sample rate | `diff` the CSV headers. That is what they are for | **The whole campaign.** Motors measured before the change cannot be compared with motors measured after |
| 2 | **`VM` sagged below 4.75 V** at the driver pin at any point | `vm_volts` column | Every step at or after the sag. The motor's own supply changed, so `duty_frac` no longer means what it meant |
| 3 | **PSU entered constant-current limiting** | PSU front panel; `current_a` pinned flat at 1.00 A | Those steps — you measured the supply, not the motor. Also a *finding*: record it |
| 4 | **Loop period outside 100 Hz ± 1%**, or jitter above your recorded threshold | Analyser D6, and the `loop_period_s` column | **Every `omega_rad_s` in the run**, by the same factor — see [`../BENCH.md`](../BENCH.md) §3.1. This is the silent one |
| 5 | **Encoder counts while `duty_frac` = 0** | Non-zero `delta_ticks` at rest | The low-speed end of the curve. Electrical noise is being counted as motion, and it inflates exactly the region the deadband lives in |
| 6 | **A probe or lead knocked loose during the swap** | The BENCH §6 ladder, re-run after every swap | This motor and every motor after it, until the next successful ladder |
| 7 | **Shaft fouled / motor walked off its mount** | `omega_rad_s` = 0 at a duty that previously turned; or an audible change | The affected block. Re-mount and re-run the whole motor, not just the block |
| 8 | **Block 3 differs from block 1 by more than the within-block spread** | Compare directly | Nothing yet — it means there is a **time-dependent effect** (thermal, break-in, sag). Diagnose it before drawing conclusions: does the difference track *elapsed time* or *duty*? ([`../BENCH.md`](../BENCH.md) §8 row 13) |
| 9 | **M1's re-run does not overlay M1's first run** | The §8 repeatability check | **The between-motor comparison**, which is the entire experiment. Find the drift before believing any of it |
| 10 | **Motor got hot enough to be uncomfortable to touch** | Your hand, between blocks | Nothing directly — but stop, rest it, and record it. The next block will not be comparable to the last one, and a hot N20 is minutes from a damaged one |

---

## 10. Assumptions made here, that are not in the hardware ground truth

Flagged so a future session can find and challenge them:

1. **`VM` = 5.00 V** for the campaign. Chosen to match the rover's rail. Any
   value in 4.5–6.0 V would be defensible; what is **not** defensible is a
   different one per motor. Recorded in every CSV header so it is checkable.
2. **PSU current limit 1.00 A.** Above the expected worst case, below the
   TB6612's 1.2 A continuous rating.
3. **Dwell 1.5 s, discard 0.5 s.** From a mechanical-time-constant argument
   (§4.2), **with an explicit instruction to measure `tau_m` once and confirm.**
4. **3 blocks, median reported.** Enough to identify an outlier; cheap enough to
   run four times.
5. **Coarse 0.05 / fine 0.01 steps.** A resolution choice, not a physical one.
6. **Telemetry decimated to 50 Hz** for the sweep (§5). Explicitly **not**
   carried into Story 1.6.
7. **Stall current by extrapolation from ≤0.4 duty**, to protect the gearbox
   (§7.2). Tagged `[DERIVED]`, cross-checked against `V/R`.
8. **Coast, not brake, between phases** (§4.3).
9. **No load.** A wheel, a chassis or a dynamometer would each be a different
   experiment. This one deliberately measures the motor alone.
