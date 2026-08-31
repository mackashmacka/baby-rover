---
name: rover-bench
description: How to run a Baby Rover motor-characterisation campaign safely and comparably — the probe map, the electrical interlocks, what a good run looks like, and what silently invalidates one. Use before touching the bench, wiring the logic analyser, or running Story 1.3/1.4/1.5 captures. PROVISIONAL until the first real run.
provisional: true
confirmed-by: the first complete Story 1.5 run — four motors, one identical procedure, data in experiments/ and a registry row — plus a sigrok-cli capture that survives being re-read a week later.
---

# Running a characterisation campaign

> ⚠️ **This skill is PROVISIONAL.** `CLAUDE.md` forbids speculative skills, and
> as of 2026-08-31 **no capture has ever been taken with this analyser** and no
> characterisation run has ever happened. Everything below is derived from the
> hardware documents (`docs/HARDWARE.md`, `docs/WIRING.md`,
> `memory/logic-analyser.md`) and from the failure that cost an evening — not
> from bench experience. Treat the safety interlocks as binding (they come from
> ground truth) and every *number* as a hypothesis to check.
>
> **What would confirm it:** one complete Story 1.5 campaign — four motors, one
> identical procedure, comparable data committed with a registry row. Revise
> this file in place from what actually happened, and delete the numbers that
> turn out wrong. Until then, do not quote it as if it were measured.

## The single-motor probe map

One motor is characterised at a time, and the same map is reused for each. All
probe points are **3.3 V logic**, and the analyser ground goes to the **common
rail**.

| Pico GP | Signal | Direction | Analyser |
|---|---|---|---|
| GP2 | `PWMA` | out | D0 |
| GP3 | `AIN1` | out | D1 |
| GP4 | `AIN2` | out | D2 |
| GP5 | `STBY` | out | D3 |
| GP12 | Encoder A | in | D4 |
| GP13 | Encoder B | in | D5 |
| GP20 | `LOOP_TICK` | out | D6 |
| GP21 | `COMPUTE_BUSY` | out | D7 |
| GP0 / GP1 | UART TX / RX to the Pi (or the FT232R standing in), 115200 | — | not probed in this map |

This is `docs/WIRING.md` §10.2. **There is a second map, §8, and only D6/D7
differ** — there they are UART TX (`GP0`) and RX (`GP1`), which is what the
Story 2.1 failsafe measurement needs. **Record which map a capture was taken
under**, in the filename or the run log: a `.sr` file whose D6 could be either
`LOOP_TICK` or UART TX is not evidence of anything.

`LOOP_TICK` toggles once per PID iteration and `COMPUTE_BUSY` is high while the
loop body computes. Together they turn "is the 100 Hz loop actually 100 Hz, and
how much headroom is left?" into something you can *see* rather than argue
about. Cheap to add, and the first thing you will wish you had.

## Interlocks — check every one, every time, before power

These come from the hardware ground truth. Violating one damages hardware.

1. **Never probe `AO1`/`AO2`/`BO1`/`BO2`.** Those sit at motor voltage. It
   destroys the analyser. There is no version of this that is worth trying.
2. **Encoder supply is 3.3 V. Never 5 V.** RP2350 GPIO is not 5 V tolerant.
3. **Grounds common** — Pico, driver, supply, analyser. Without it the driver
   cannot interpret any logic level, and the analyser reads fiction.
4. **The 4S LiPo (16.8 V charged) must never reach the TB6612 directly.** Its
   `VM` maximum is 13.5 V. The regulator goes in between, always.
5. **No motor current enters the Pico.** No wire from battery to Pico, none from
   the H-bridge outputs back to it.
6. **Confirm the motor pair by resistance before connecting anything**: a few
   ohms red-to-white, open black-to-blue. An evening has already been lost to
   encoder wires sitting in the H-bridge outputs while every logic test point
   read perfectly correct. See `memory/debugging-method.md`.

`STBY` low is an instant hardware all-stop. Know which pin it is before you need
it, and drive it low when a run ends rather than leaving a duty cycle applied.

## The instrument

`sigrok-cli` with driver `fx2lafw`, on the Cypress FX2 clone `0925:3881`.
**Headless only** — PulseView is for human eyes and cannot appear in an
automated path, because a capture that needs a mouse click cannot be repeated
identically on motor 4 three days after motor 1.

**Discover the sample rate; never assume it.** This repo does not document this
clone's maximum anywhere, and the sustainable rate depends on how many channels
are enabled and on USB bandwidth on the day.

```bash
sigrok-cli --scan
sigrok-cli -d fx2lafw --show     # capabilities, including the samplerate list
```

Record what it printed in the run manifest. A capture whose sample rate you
cannot state later is not evidence.

**Sanity floor, from arithmetic rather than experience:** a 20 kHz PWM period is
50 µs, so resolving duty to ~1% needs samples ~500 ns apart — 2 MS/s bare
minimum, and comfortably more if you also want the reversal deadtime. Encoder
edges are slow by comparison (roughly 28,000 counts/s across all four motors),
so PWM sets the requirement, not the encoders.

**Prove the instrument on a signal you already understand before trusting it on
one you do not.** An analyser that has never successfully triggered is not a
working instrument, and you discover that at the worst possible moment. Day 0
acceptance is exactly this: a known square wave, captured and read back.

## What a good run looks like

A run is good when it is *comparable* — when you can prove that everything
except the motor was the same. Concretely:

- **A manifest exists** naming: firmware build (git hash), sample rate, channel
  map (which of the two — see above), supply voltage and current limit, dwell
  times, commanded duty sequence, `ticks_per_rev` **and whether it was measured
  or assumed**, motor identity, UTC timestamp.
- **Raw tick counts are stored, not only derived `omega_rad_s`.** `ticks_per_rev`
  is disputed (11 vs 14 on the motor shaft, ×100 gearing, ×1/×2/×4 by decoding)
  and unmeasured — see `docs/HARDWARE.md` §2.1. A file holding raw counts can be
  rescaled when the real number is measured; a file holding only rad/s is scaled
  by an unknown constant forever and has to be re-run.
- **`PWMA` measures 20 kHz** at the commanded duty, on the analyser, not in
  code comments.
- **`AIN1`/`AIN2` match the TB6612 truth table** — `10` forward, `01` reverse,
  `11` brake, `00` coast — and the reversal deadtime is visible and consistent.
- **Encoder A/B are in genuine quadrature**: exactly one channel changes per
  transition, and the phase relationship inverts when direction inverts. Verify
  the phasing on the analyser rather than trusting which wire is which.
- **`LOOP_TICK` shows a 100 Hz *loop*** with jitter you can state a number for,
  and `COMPUTE_BUSY` leaves obvious headroom inside each period. It **toggles**
  once per iteration, so a loop period is **edge to edge**: a 100 Hz loop is a
  **50 Hz square wave — 100 edges/s, one edge per iteration**. Reading it
  rising-to-rising reports 50 Hz and looks like a loop running at half rate:
  double a frequency readout, never halve it. See `docs/WIRING.md` §10.1 and
  `docs/BENCH.md` §3.1.
- **The supply held up.** Terminal voltage stayed in the motor's 4.5–6 V band
  under load, including at stall. A supply is judged by its sag, not its
  open-circuit reading.
- **The same procedure ran on all four motors**, in one sitting where possible.

## What invalidates a run — and how to tell

The dangerous failures here are silent. None of these throws an error; all of
them quietly produce a plausible-looking CSV.

| Symptom / condition | What it actually means | Verdict |
|---|---|---|
| Firmware rebuilt mid-campaign | Motor 4 was measured under different code than motor 1 | **Void the campaign.** Re-run all four |
| Sample rate differs between runs | Duty and deadtime resolve differently; the numbers are not comparable | Void the affected runs |
| No manifest, or a manifest missing the git hash | The run cannot be defended in six months | Void — data with no provenance is an anecdote |
| Supply sagged below 4.5 V | You measured the supply, not the motor | Void; fix the supply first |
| `VM` fed from Pico VBUS (the Stage 1 stopgap) | Motor current shares a rail with the logic — a known invariant violation | Acceptable only for smoke tests, never for characterisation data |
| Ticks-per-rev assumed rather than measured | Every rad/s in the file is scaled by an unknown constant | Void. Measure it by hand-turning the output shaft (Story 1.4) |
| Encoder shows both channels changing in one sample | Either aliasing (sample rate too low) or a real electrical fault | Investigate before believing anything else in the capture |
| A channel is flat for the whole capture | Probe off, ground missing, or the pin was never driven | Void the capture; do not interpret the others |
| Motor warm from the previous run | Winding resistance has drifted; the duty→speed curve moves | Let it cool, and record cooldown time in the manifest |
| Capture length hit the analyser's buffer limit | Silently truncated, and the tail is where stall behaviour lives | Re-capture shorter, or at a lower rate |

**How to tell the difference between a bad motor and a bad run:** a bad run
usually shows an inconsistency *within* the capture — a flat channel, an
impossible quadrature transition, a `LOOP_TICK` that is not periodic. A real
motor difference is consistent within its own capture and only shows up when
you overlay all four. If the four curves differ *and* every internal
consistency check passes, that is a finding, not a fault — and quantifying that
difference is the entire point of Story 1.5. Four "identical" motors never are;
that is what motivates closed-loop control.

## Order of work

1. Resistance-check the motor pair. Only then wire it.
2. Interlocks, all six, out loud.
3. Analyser on a signal you already understand; confirm you can read it back.
4. Bring `STBY` up, run the commanded sequence, record the manifest as you go.
5. `STBY` low. Then write the registry row in `experiments/REGISTRY.md` — **when
   the experiment ran**, not at the end of the day. A result not persisted did
   not happen.
6. Overlay the four motors, and write a paragraph on how much they differ.
