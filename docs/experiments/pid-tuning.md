# `pid-step` — Story 1.6, closed-loop speed control and tuning

**Registry ID:** `pid-step` · **Story:** 1.6 · **Status:** specified, not yet run

> **The question.** Story 1.5 measured four motors and found they are not the
> same. **Can a per-wheel closed loop at 100 Hz make them behave as if they
> were** — and can you show, on a plot, exactly what each part of the controller
> contributed?

**Prerequisites, and they are hard ones:**

- [`motor-char.md`](motor-char.md) complete: `m`, `b` and the deadband per
  motor **per direction**. Without them there is no feedforward
- Loop timing verified on analyser D6 — 100 Hz, jitter recorded
  ([`../BENCH.md`](../BENCH.md) §6 rung 4)
- `ticks_per_output_rev` settled by Story 1.4. Every gain you tune scales with
  it, so tuning against a provisional constant means tuning twice

**Bench, safety, probes:** [`../BENCH.md`](../BENCH.md). Same bench, same probe
map, same motor mounting. D6/D7 matter *more* here than in 1.5, not less.

---

## 1. The controller, in one diagram

```
                       (from Story 1.5, per motor, per direction)
                                  ┌──────────────┐
   omega_target_rad_s ────────────►  FEEDFORWARD ├──────────────┐
        │                         │  d0 + w/m    │              │
        │                         └──────────────┘              ▼
        │       e_rad_s   ┌─────┐                             ┌───┐    duty_frac
        ├────►(+)────────►│  Kp ├────────────────────────────►│ + ├──►[clamp 0..1]──► PWM
        │      ▲          └─────┘                             │   │         │
        │      │          ┌─────┐   (frozen while saturated)  │   │         │
        │      │       ┌─►│  Ki ├─►[integrator, clamped]─────►│   │         │
        │      │       │  └─────┘                             │   │         │
        │      │       │  ┌─────┐                             │   │         │
        │      │       │  │ Kd  │◄── d(omega_meas)/dt  ───────►│   │         │
        │      │       │  └─────┘    (on MEASUREMENT, negated) └───┘         │
        │      │       │                                                    │
        │   omega_meas_rad_s ◄── [filter] ◄── delta_ticks / dt_s ◄── PIO ◄───┘
        │                                                            encoder
        └────────────────────────────────────────────────────────────────────
                             100 Hz, dt_s = 0.010
```

**Read it as: the feedforward does the bulk of the work; the PID corrects the
residual.** That split is the single most important design decision in this
story, and §3 is the argument for it.

---

## 2. Units first — write them down before you write a gain

Mixed units are a leading cause of control bugs
([`../../CLAUDE.md`](../../CLAUDE.md) §Conventions), and a PID controller is
where they hide best, because a wrong gain and a wrong unit look identical from
the outside: both give you a number that is too big or too small.

| Quantity | Identifier | Unit |
|---|---|---|
| Setpoint | `omega_target_rad_s` | rad/s |
| Measurement | `omega_meas_rad_s` | rad/s |
| Error | `error_rad_s` = target − measured | rad/s |
| Integral of error | `integral_rad` | rad *(rad/s × s)* |
| Derivative of measurement | `domega_rad_s2` | rad/s² |
| Controller output | `duty_frac` | dimensionless, clamped 0…1 |
| **`Kp`** | | **duty per (rad/s)** |
| **`Ki`** | | **duty per rad** |
| **`Kd`** | | **duty·s² per rad** |
| Loop period | `dt_s` | s, nominal 0.010 |

**Sanity rule:** every term added to `duty_frac` must itself come out
dimensionless. If it does not, the bug is in the units and no amount of tuning
will hide it.

---

## 3. Feedforward — turning the Story 1.5 curve into a component

### 3.1 The arithmetic

Story 1.5 fits, per motor and per direction, over the linear region:

```
omega_rad_s  =  m * (duty_frac - d0)          for duty_frac > d0
```

where `m` is the slope in **rad/s per unit duty** and `d0` is the deadband.
Invert it and you have an open-loop duty that should produce the speed you want:

```
duty_ff  =  d0  +  |omega_target_rad_s| / m           (sign applied separately)
```

**Per motor and per direction.** `m_fwd` and `m_rev` differ — brush geometry and
gear-train friction are not symmetric — and `m` differs between the four motors,
which is exactly what 1.5 measured.

### 3.2 Which deadband: breakaway or dropout?

1.5 produces two: `breakaway_duty_frac` (sweeping up, must beat *static*
friction) and the lower `dropout_duty_frac` (sweeping down, only *kinetic*
friction).

**Use `dropout` in the feedforward.** If you use `breakaway`, then for any tiny
commanded speed the feedforward alone already applies enough duty to break the
motor loose, and it accelerates straight past the target — a guaranteed
overshoot at every small command. With `dropout`, small commands start slightly
too weak and the **integrator** supplies the extra to break away. Slower to
start, no overshoot, and the correction is exactly what integral action is for.

*(This is why 1.5 measures both. If you had only swept upward you would have one
number, would use it, and would spend an afternoon wondering why low-speed steps
overshoot.)*

### 3.3 Why bother — the argument in four points

1. **Smaller errors ⇒ smaller gains.** The PID only ever sees the *residual*
   after the feedforward, so it can be gentle. Gentle loops are stable loops.
2. **Much less integral action ⇒ much less windup.** The integrator's job
   shrinks from "supply the entire operating duty" to "trim a few percent".
3. **Consistent behaviour across the speed range.** A pure PID must supply a
   duty of ~0.15 at low speed and ~0.85 at high speed entirely out of the error
   term, so its effective response differs at each end. Feedforward flattens
   that.
4. **It is what makes four different motors behave the same.** Each motor gets
   *its own* `m` and `d0`. The controller downstream is then identical for all
   four, with identical gains — which is a far better answer than four
   separately-tuned PIDs, because it is explainable and it degrades predictably.

**Prove it, do not assert it:** run the step test with feedforward **off**
(`d0 = 0`, `m = ∞`) and with it **on**, same gains, same axes. That pair of
plots is one of the most legible things in the whole report, and it takes four
minutes to produce.

---

## 4. The measurement: speed from an encoder, and its quantisation floor

Before tuning anything, know how good your feedback signal is. **A control loop
cannot be better than its measurement**, and this one has a hard floor.

Counting ticks in a fixed 10 ms window gives a speed quantised in steps of one
tick per window:

```
omega_quant_rad_s  =  2*pi / (ticks_per_output_rev * dt_s)
                   =  628.3 / ticks_per_output_rev        (at dt_s = 0.010)
```

| `ticks_per_output_rev` | `omega_quant_rad_s` |
|---|---|
| 1100 | 0.57 |
| 1400 | 0.45 |
| 2800 (2× decoding) | 0.22 |
| 5600 (4× decoding) | 0.11 |

**Now compare it against the speeds you actually command.** Taking the plausible
~300 output RPM ≈ 31 rad/s from [`../HARDWARE.md`](../HARDWARE.md) §6.3 — a
figure Story 1.5 replaces with a measured one:

- at top speed, 0.45 / 31 ≈ **1.4%** — irrelevant
- at 3 rad/s, 0.45 / 3 ≈ **15%** — your feedback is mostly staircase

**⇒ The low-speed end is measurement-limited, not controller-limited.** No gain
fixes it. Naming that correctly is worth more than tuning around it, because it
tells you which of four different fixes applies:

| Fix | Gains you | Costs you |
|---|---|---|
| **Average over more loop periods** | Finer resolution | Delay — the estimate is now old, and delay in a feedback path is destabilising |
| **Measure the *interval between edges*** instead of edges per window (PIO can timestamp) | Excellent low-speed resolution | Poor at high speed; two estimators and a crossover to get right. **YAGNI unless you need it** |
| **Low-pass filter the speed estimate** (first-order IIR) | Smoother derivative, quieter output | **Phase lag**, which eats the phase margin that lets you raise `Kp` |
| **Declare a minimum usable speed** and stay above it | Free, honest | You cannot creep |

**Boring recommendation:** a first-order IIR on `omega_meas_rad_s` with a cutoff
around **10–20 Hz** (roughly `alpha` 0.4–0.6 at 100 Hz), **plus** a stated
minimum usable commanded speed of about **10 × `omega_quant_rad_s`**. Record the
cutoff as a tuned parameter alongside the gains — it is one, and forgetting that
is how a filter becomes an unexplained constant.

> **Filtering is not free.** Every filter on the feedback path trades noise for
> delay. That sentence is worth being able to say in an interview.

---

## 5. Logging for a step response — different from 1.5

[`motor-char.md`](motor-char.md) §5 decimates telemetry to 50 Hz. **Do not do
that here.** The rise is over in tens of milliseconds; at 50 Hz you get about
three points on it and the overshoot may fall between samples.

**⇒ Log at the full 100 Hz into a RAM buffer, then dump it over the UART after
the window closes**, when the timing no longer matters.

```
2 s window x 100 Hz x ~7 float columns  ~  a few kB      (520 KB SRAM available)
```

Buffer columns, per sample: `t_s`, `omega_target_rad_s`, `omega_meas_rad_s`,
`duty_frac_ff`, `duty_frac_out`, `error_rad_s`, `integral_rad`, `loop_period_s`.

**Log the feedforward and the integrator separately from the total output.** A
plot of the total tells you *that* it misbehaved; the decomposition tells you
*which term did it*. That is the difference between tuning and guessing.

---

## 6. Tuning method

**Order: `Kp`, then `Ki`, then probably never `Kd`.** One gain at a time, one
change at a time, and a saved plot after every change with the gains in the
filename. Tuning without a record is a random walk.

### 6.0 Before you start

- [ ] Feedforward **on**, from this motor's own 1.5 fit
- [ ] `Ki = 0`, `Kd = 0`
- [ ] Integrator clamp and conditional integration implemented (§7) — **before**
      the first test, not after windup surprises you
- [ ] Derivative on **measurement**, not on error (§7)
- [ ] `dt_s` fixed at 0.010 (§9 assumption 1)
- [ ] Analyser on D6/D7, loop confirmed at 100 Hz
- [ ] Test setpoint chosen: **50% of this motor's measured no-load top speed**
      from 1.5. High enough that quantisation is negligible; low enough that the
      controller keeps duty headroom to correct with. *(Step to 95% of top speed
      and you are measuring saturation, not a tuning.)*

### 6.1 `Kp`

**A principled starting point, not a guess.** `m` is rad/s per unit duty, so
`1/m` is *the duty needed per rad/s*. A `Kp` of `1/m` therefore means "an error
of 1 rad/s asks for exactly the duty that produces 1 rad/s" — dimensionally
natural, and roughly unity loop gain at DC.

```
Kp_start  =  0.2 / m          then raise
```

Raise `Kp` — doubling is a reasonable step early, then finer — until **one** of:

| What you observe | What it means | Do |
|---|---|---|
| Small overshoot appears (~10%) and settling is quick | You are near the useful maximum | Back off ~30% and stop |
| Sustained oscillation / ringing | Loop gain too high for the phase margin you have | Halve it. If it only rings *with* the filter, the filter's phase lag is the cost you paid in §4 |
| An audible buzz or whine at low speed | `Kp` amplifying **encoder quantisation** into the duty output | Back off, or filter more, or raise the minimum commanded speed. The buzz is your quantisation floor made audible — it is a *measurement* |
| Nothing changes | The error is tiny because the feedforward is doing everything | Good. Check with feedforward off that the loop is actually wired up |

**Expect a steady-state error to remain.** Proportional control produces output
*in proportion to error*, so a non-zero output requires a non-zero error. The
friction the feedforward did not exactly predict is a constant disturbance, and
`Kp` alone cannot null it. That is §10.5, and seeing it here is the point.

### 6.2 `Ki`

**Starting point, again derived rather than guessed.** The classical
parameterisation is `Ki = Kp / T_i` with the integral time `T_i` set near the
plant's own time constant. You measured `tau_m` in
[`motor-char.md`](motor-char.md) §4.2:

```
Ki_start  =  Kp / tau_m         (tau_m ~ tens of ms  =>  Ki ~ 20-50 x Kp)
```

Raise `Ki` until the steady-state error is gone within an acceptable settling
time. Watch for the two costs, both of which you should be able to see on the
plot:

- **Overshoot grows.** The integrator keeps pushing after the error crosses
  zero, because it is acting on accumulated history.
- **Settling gets slower and more oscillatory** if you keep going. Stop before
  that.

**Stop as soon as the steady-state error is inside your acceptance band.** More
integral action than you need is pure downside.

### 6.3 `Kd` — start at zero, and expect to leave it there

`Kd` acts on the *rate of change* of a signal that is quantised (§4) and then
filtered. Differentiating a staircase amplifies the steps; differentiating a
filtered staircase amplifies them slightly less and adds the filter's lag.

**For a DC motor speed loop at 100 Hz with a feedforward term, `Kd` usually
makes things worse.** Leave it at zero.

**What would justify adding it:** you have a genuine overshoot problem that `Kp`
and `Ki` cannot resolve, the measurement is clean enough to differentiate (you
have measured the noise, not assumed it), and derivative-on-measurement plus
extra filtering is in place. Then try `Kd` around `Kp * dt_s`, and only keep it
if the plot is *visibly* better on the same axes.

**Record that you tried it and it did not help.** A negative result you can
explain is a real result, and "I left the D at zero and here is why" is a better
interview answer than a tuned `Kd` you cannot justify.

### 6.4 What about Ziegler–Nichols?

Worth knowing, and worth deliberately not using here. Z–N derives gains from the
ultimate gain and period at the point of sustained oscillation. Two reasons to
skip it:

1. It is designed for **process control with significant dead time**, and it
   tends to produce aggressive, oscillatory tunings — famously about 25%
   overshoot. A DC motor speed loop has almost no dead time.
2. It assumes a **pure PID with no feedforward**. Here the feedforward is
   carrying most of the output, so the "plant" the PID sees is not the plant Z–N
   assumes.

Deliberately driving a motor into sustained oscillation to find `Ku` is also a
thing you would rather not do to a gearbox you have not characterised yet.

**Boring wins**: `Kp` up until it complains, `Ki` until the offset goes, stop.

---

## 7. The three implementation details that are not tuning

These are **structural**. Getting them wrong produces symptoms that look like
bad gains, so no amount of tuning will fix them.

### 7.1 Saturation and anti-windup

`duty_frac` clamps to `[0, 1]`. Once clamped, **the loop is open** — more error
produces no more output — and the plant is nonlinear. Two things must follow:

```
u_unclamped = duty_ff + Kp*e + Ki*integral + Kd*(-domega)
u_out       = clamp(u_unclamped, 0.0, 1.0)

# conditional integration: do not accumulate while saturated *in the direction
# that would make it worse*
if not (u_out != u_unclamped and sign(e) == sign(u_unclamped - u_out)):
    integral += e * dt_s

integral = clamp(integral, -I_MAX, +I_MAX)   # belt and braces
```

Pick `I_MAX` so `Ki * I_MAX` is around **0.3** of full duty: the integrator is
allowed to trim, not to dominate. Log `integral_rad` (§5) so you can see it hit
the clamp instead of inferring it.

### 7.2 Derivative on measurement, not on error

`d(error)/dt` contains `d(setpoint)/dt`. A step change in setpoint is
mathematically an infinite derivative — in practice, one enormous single-sample
spike straight into the duty output. That is **derivative kick** (§10.3).

**Fix:** differentiate the *measurement* and negate it. For a constant setpoint
the two are identical; at a step they are not, and only one of them slams the
output.

```
d_term = -Kd * (omega_meas[k] - omega_meas[k-1]) / dt_s
```

### 7.3 Fixed `dt_s`, not measured `dt_s`

The loop measures its own period (`loop_period_s`, and D6 confirms it). It is
tempting to use that measured period in the integral and derivative terms.

**Don't.** Jitter in `dt_s` would then be injected directly into the control
output — worst in the derivative, where you divide by it. You would be coupling
timing noise into the motor.

**Use a fixed nominal `dt_s = 0.010`, and make the loop actually hit it.** D6 is
how you know it does; §8 makes it an acceptance criterion rather than a hope.
This is a genuine trade-off, so state it as one: fixed `dt_s` is *wrong* by the
jitter amount, but wrong by a small consistent amount beats right-but-noisy in a
feedback path.

---

## 8. The step-response plot — what it must show

Every step-response plot in this story must have **all** of the following.
A plot missing the duty trace is half a plot.

1. **Two y-axes on one time axis.**
   - Left: `omega_target_rad_s` (dashed) and `omega_meas_rad_s` (solid), rad/s
   - Right: **`duty_frac_out`** (the controller output), 0…1
2. **The duty trace is not optional.** It is the only place saturation and
   windup are visible. A speed trace that overshoots looks the same whether the
   cause is too much `Kp` or an integrator unwinding out of saturation; the duty
   trace separates them instantly.
3. **Four annotated figures**, computed and printed on the plot:
   - `rise_time_s` — 10% → 90% of final value
   - `overshoot_pct` — (peak − final) / final × 100
   - `settling_time_s` — first entry into a **±2%** band it does not leave
   - `steady_state_error_rad_s` and as a percentage of target
   - shade the ±2% settling band so the eye can check the number
4. **Step up *and* step down**, as separate panels.
   **They will not be symmetric, and knowing why is the lesson:** acceleration
   is actuated (the bridge pushes) but deceleration mostly is not — a speed
   controller that clamps duty to `[0, 1]` can only stop pushing and let
   friction do the work. The plant is asymmetric. Do not tune the step-down out;
   explain it.
5. **A small step and a large step.** A large step saturates and exposes windup;
   a small one does not. You need both to claim the tuning works.
6. **Before and after, on identical axes and identical scales.** Different
   y-limits make any tuning look like an improvement.
7. **Feedforward off vs on**, same gains (§3.3).
8. ⭐ **All four motors at the same commanded speed, overlaid.** This is the
   payoff plot: put it next to the Story 1.5 open-loop overlay and the pair
   makes the argument for closed-loop control without a word of text.
9. **Title/caption carrying the provenance**: `motor_id`, `Kp`/`Ki`/`Kd`, filter
   cutoff, feedforward on/off, **measured** loop rate and jitter, firmware SHA,
   UTC timestamp. A plot you cannot reproduce is decoration.

Plots go in `experiments/plots/`, raw logs in `experiments/pid-step/`.

### 8.1 Two optional experiments that are worth the ten minutes

- **Disturbance rejection.** At steady state, load the shaft briefly (a rag, not
  fingers) and release. The plot shows the speed dip, the integrator rising, and
  the recovery. It is the clearest possible picture of what integral action
  *does*.
- **Deliberately break the loop timing.** Put a blocking `printf` inside the loop
  body, capture D6/D7, and watch `COMPUTE_BUSY` widen and the period stretch —
  then remove it. Two minutes, and it demonstrates both the failure mode and the
  instrument that catches it. See [`../BENCH.md`](../BENCH.md) §8 row 10.

```bash
# loop period and its average, from the capture
sigrok-cli -i pid-step-run.sr -P timing:data=D6:edge=any:avg_period=100 \
           -A timing | tail -5

# jitter of COMPUTE_BUSY against LOOP_TICK - i.e. how consistently the loop body
# starts relative to the tick
sigrok-cli -i pid-step-run.sr -P jitter:clk=D6:sig=D7 -A jitter | tail
```

*(Decoders confirmed present on this machine on 2026-08-31 —
[`../BENCH.md`](../BENCH.md) §5.5. Check `sigrok-cli -L` on any other box.)*

---

## 9. Acceptance criteria

- [ ] **Loop verified at 100 Hz** on analyser D6 for every run: mean
      **edge-to-edge** interval 10.00 ms (one edge per iteration — 100 edges/s,
      and a frequency readout will say 50 Hz;
      [`../BENCH.md`](../BENCH.md) §3.1), peak-to-peak jitter recorded and
      **below a threshold you write down before testing**
- [ ] **`COMPUTE_BUSY` duty cycle recorded** — the CPU headroom number
- [ ] **Feedforward derived from this motor's own 1.5 fit**, per direction, with
      `m`, `d0` and the source CSV named in the log
- [ ] **Before/after step-response plots** meeting every item in §8, for at
      least one motor, and the money plot (§8 item 8) for all four
- [ ] **Feedforward off vs on** plot pair
- [ ] **Final gains recorded with their units** (§2), plus the filter cutoff,
      the integrator clamp, and `dt_s`
- [ ] **Steady-state error inside the acceptance band** at the mid-speed
      setpoint, both directions, all four motors.
      *Provisional band: ≤3% of setpoint. **Set the real number once Story 1.5
      reports the open-loop spread** — the criterion that matters is "much
      smaller than the open-loop difference between motors", and 1.5 supplies
      that figure. Writing 3% before you have it would be guessing.*
- [ ] ⭐ **The four motors hold the same commanded speed** despite the
      differences measured in 1.5, quantified the same way 1.5 quantified them:
      `(max − min) / mean` at the same setpoint. **Open-loop spread vs
      closed-loop spread, two numbers, one sentence** — this is the result
- [ ] **`Kd` decision recorded with its reasoning**, including "tried, did not
      help" if that is what happened
- [ ] **Registry row** in [`../../experiments/REGISTRY.md`](../../experiments/REGISTRY.md)
      when it is run
- [ ] **The owner can explain every concept in §10 out loud**, unprompted. This
      is a real acceptance criterion, not a flourish: a working rover the owner
      does not understand is a failure ([`../../CLAUDE.md`](../../CLAUDE.md))
- [ ] **The write-up prose is the owner's.** Claude assembles plots, tables and
      numbers, and critiques drafts hard. It does not write the report

---

## 10. The concepts you must be able to explain out loud

Interviewers ask follow-ups. Each of these has a one-line definition, the
mechanism, and where you saw it **on your own plot** — that last part is what
makes the answer yours.

### 10.1 Loop rate

**What:** the fixed frequency at which the controller samples, computes and
actuates. Here 100 Hz, `dt_s` = 0.010.

**Why it matters:** the loop rate sets the maximum closed-loop bandwidth you can
achieve. Rule of thumb: sample **10–20× faster** than the bandwidth you want, so
100 Hz supports roughly a 5–10 Hz closed loop. Everything derived from counting
ticks in a window scales with `dt_s`, so a loop that is *late* silently rescales
every speed you compute — and a controller tuned against a timebase that does
not exist is tuned for a different plant.

**Say it out loud:** *why does jitter in the loop period corrupt the speed
measurement and not just the control?* (Because `omega` is `delta_ticks / dt_s`,
and if the real `dt_s` was 11 ms while you divided by 10 ms, the speed is
overstated by 10% — before the controller does anything.)

**On your plot:** the D6 capture and the recorded jitter.

### 10.2 Integral windup

**What:** the integrator accumulating error it cannot act on, because the output
is already saturated.

**Mechanism, in order:** you command a large step → the controller demands
`duty_frac` 1.4 → the output clamps at 1.0 → the motor accelerates as fast as it
physically can, but the error stays positive the whole time → the integrator
keeps accumulating → the motor reaches the setpoint, error hits zero — **but the
integrator is now holding a large stored value that must be unwound before the
output can come down.** So the output stays high, the motor overshoots, and it
stays overshot until the integrator drains. The recovery is slow and looks
nothing like the gains you set.

**The fix:** stop integrating while saturated in the unhelpful direction
(conditional integration), and clamp the integrator anyway (§7.1).
Back-calculation is the more elegant alternative; conditional integration is the
boring one that works.

**Say it out loud:** *why does windup make the overshoot last so much longer
than a too-high `Kp` would?* (Because the excess is stored energy in the
integrator that has to be discharged by accumulated *negative* error, whereas a
`Kp` overshoot corrects the instant the error changes sign.)

**On your plot:** the large-step run with anti-windup disabled — the duty trace
pinned at 1.0 far past the moment the speed crossed the target.

### 10.3 Derivative kick

**What:** a large spurious derivative term caused by a step change in the
*setpoint*, not in the plant.

**Mechanism:** the D term computes `d(error)/dt`, and `error = setpoint −
measurement`. Step the setpoint by 10 rad/s in one sample and the derivative of
the error is `10 / 0.010` = 1000 rad/s² for exactly one sample — a single
enormous spike straight through `Kd` into the duty output. The plant did
nothing. The setpoint moved.

**The fix:** differentiate the **measurement** and negate it. Identical when the
setpoint is constant; no kick when it steps (§7.2).

**Say it out loud:** *why is derivative-on-measurement mathematically equivalent
for a constant setpoint?* (Because `d(setpoint)/dt` is zero, so
`d(error)/dt = −d(measurement)/dt`.)

**On your plot:** with derivative-on-error and any non-zero `Kd`, a one-sample
spike on the duty trace at the instant of the step.

### 10.4 Saturation

**What:** the actuator hitting a physical limit — here `duty_frac` clamped to
`[0, 1]`.

**Why it matters, beyond windup:** while saturated, **the feedback loop is
open.** More error produces no more output. Every linear intuition — gain,
bandwidth, stability margin — is derived from a linear model and stops applying.
This is also why acceleration and deceleration are asymmetric (§8 item 4): the
bridge can push, but a controller clamped to `[0, 1]` cannot pull.

**Say it out loud:** *what is the difference between a controller that is
saturated and one that is badly tuned?* (A saturated controller is doing
everything it can — the limit is the plant. A badly tuned one is choosing to do
the wrong thing. The duty trace tells you which, in one glance.)

**On your plot:** the flat top on the duty trace during the large step.

### 10.5 Steady-state error

**What:** the constant offset that remains after everything has settled.

**Why proportional control leaves one:** `output = Kp × error`. To hold a
constant non-zero output — which you need, because friction is a constant
disturbance — you need a constant non-zero error. The error *is* the price of
the output. Raising `Kp` shrinks it but never removes it, and raises it toward
instability instead.

**Why integral action removes it:** the integrator's output depends on
accumulated error, not present error, so it can hold a non-zero output at zero
error. That is the whole reason `I` exists.

**Why feedforward reduces it without `I`:** the feedforward supplies most of the
required output directly from the setpoint, so much less is left for the error
term to generate.

**Say it out loud:** *your feedforward is good and your `Ki` is zero — where
does the residual error come from?* (The feedforward is a straight-line fit to a
curve, measured on a cold motor, at one supply voltage. Friction is temperature-
dependent. The residual is the model error.)

**On your plot:** the gap between the dashed target and the solid measured trace
after settling, in the `Ki = 0` run — and its absence once `Ki` is in.

### 10.6 Also be ready for

**Deadband** and **stiction** (§3.2) · **feedforward vs feedback** (feedforward
acts on the *setpoint* and cannot correct for anything it does not model;
feedback acts on the *error* and corrects everything, but only after it has
already happened) · **quantisation** (§4) · **phase lag** and why filtering the
feedback costs stability margin (§4) · **back-EMF** and why a DC motor's speed
is roughly proportional to applied voltage · **bandwidth** and why 100 Hz is
not the same as a 100 Hz closed loop (§10.1).

---

## 11. Failure modes

| Symptom | Look at | Likely cause |
|---|---|---|
| Overshoot that takes far longer to recover than to build | The **duty** trace: pinned at 1.0 past the crossing | **Windup.** §7.1 is missing or disabled |
| One-sample spike on duty at the instant of a step | The duty trace, first sample after the step | **Derivative kick.** Derivative is on error, not measurement (§7.2) |
| Audible buzz, jittery duty at low speed, calm at high speed | `omega_meas` at low setpoint | **Quantisation** amplified by `Kp` (§4). Not a tuning bug — a measurement floor |
| Gains that worked yesterday oscillate today | D6 jitter; `loop_period_s` | Loop rate changed. Everything scales with `dt_s` (§10.1) |
| Motor holds a speed that is consistently a clean ratio off target | `ticks_per_output_rev` in the header | Story 1.4's constant is wrong by 2× or 4×. The loop is fine; the units are not |
| Forward tuning good, reverse poor | `m_fwd` vs `m_rev` | One feedforward used for both directions (§3.1) |
| Steady-state error will not go away regardless of `Ki` | `integral_rad` | Integrator hitting `I_MAX`, or conditional integration freezing it permanently (§7.1) |
| Four motors still differ after tuning | Per-motor `m`, `d0` | Feedforward not per-motor, or gains tuned on one motor and the others never checked |

---

## 12. Assumptions made here

1. **Fixed `dt_s = 0.010`** in the integral and derivative terms rather than the
   measured period (§7.3). A stated trade-off, not an oversight.
2. **Positional (not velocity/incremental) PID form.** The boring one. Easier to
   clamp, easier to log, easier to explain.
3. **Conditional integration + clamp** for anti-windup, over back-calculation.
   Shortest working diff.
4. **First-order IIR on the speed estimate, 10–20 Hz cutoff** (§4). A starting
   point to be tuned and recorded, not a constant to be hidden.
5. **Test setpoint = 50% of measured no-load top speed** (§6.0).
6. **±2% settling band, 10–90% rise time.** Conventional definitions; state
   them on the plot so nobody has to guess which convention you used.
7. **`Kd = 0` expected.** A prediction, to be confirmed or refuted and recorded
   either way.
8. **Acceptance band for steady-state error is provisional** until Story 1.5
   reports the open-loop spread (§9).
