# Experiment specifications

**Specifications live here. Results live in
[`../../experiments/`](../../experiments/REGISTRY.md).**

That split is deliberate and worth stating once:

| | Where | Nature |
|---|---|---|
| **How to run it** — parameters, procedure, acceptance criteria, what invalidates a run | `docs/experiments/` (here) | Written **before** the experiment. Changes only by deliberate decision, and a change means re-running |
| **What happened** — CSVs, `.sr` captures, plots, run logs | `experiments/<id>/` | Written **during** the experiment |
| **The one-line record** — question, method, result, where the data is | [`experiments/REGISTRY.md`](../../experiments/REGISTRY.md) | Written **when the experiment is run** |

A specification that gets edited to match what actually happened is not a
specification, it is a report. Keep them apart.

---

## 🔴 The rule

> **Every experiment gets a row in
> [`experiments/REGISTRY.md`](../../experiments/REGISTRY.md) WHEN IT IS RUN.**
>
> Not at the end of the day. Not at the end of the week. Not when the report is
> written. **A result not persisted did not happen** — CLOSE ritual item 7
> ([`../agent-bootstrap.md`](../agent-bootstrap.md)).

Schema, from the registry itself:

```
| ID | Date | Story | Question | Method | Result | Data |
```

- **ID** — the registry ID, matching the spec filename here and the data
  directory under `experiments/`
- **Date** — **UTC** ([`../../CLAUDE.md`](../../CLAUDE.md))
- **Question** — the *question*, not the activity. "What is the deadband duty
  for each motor?" is a question. "Characterise the motors" is a task
- **Method** — one line, enough that a reader knows whether to believe the
  result. Link to the spec here for the detail
- **Result** — **the number, with its units, and its uncertainty.** A result
  without an error bar is an anecdote
- **Data** — the path to the raw data. **A row with no data path is a claim,
  not a result**

**A negative or inconclusive result still gets a row.** "Measured, could not
resolve, here is why" is a real outcome and it stops the next session from
silently repeating the work.

---

## Before you run anything

**Read [`../BENCH.md`](../BENCH.md).** It carries the safety interlocks, the
analyser probe map, the hookup order, the instrument verification ladder and
the motor-swap checklist. No experiment here is runnable without it.

Two of its rules will destroy hardware if you skip them, so they are repeated
here:

> ⚠️ **Never probe `AO1`/`AO2`/`BO1`/`BO2` with the logic analyser** — motor
> voltage destroys it.
> ⚠️ **Encoder power is 3.3 V, never 5 V** — RP2350 GPIO is not 5 V tolerant.
> ⚠️ **Confirm the motor pair by resistance before connecting** — red–white a
> few ohms, black–blue not. An evening has already been lost to this one.

---

## Specifications in this directory

| Spec | Registry ID | Story | Question | Status |
|---|---|---|---|---|
| [`motor-char.md`](motor-char.md) | `motor-char` | **1.5** ⭐ | Deadband, duty→`omega_rad_s`, no-load and stall current — for each of four motors, and **how much do they differ?** | Specified, not run |
| [`pid-tuning.md`](pid-tuning.md) | `pid-step` | **1.6** | Can a 100 Hz closed loop with feedforward make four different motors hold the same commanded speed? | Specified, not run |

**They are a pair, in order.** `motor-char` produces the duty→speed curve;
`pid-tuning` consumes it as the feedforward term. Running 1.6 without 1.5's
numbers means tuning a controller with no model, which works badly and teaches
less.

---

## Planned, not yet specified

These have registry IDs reserved in
[`experiments/REGISTRY.md`](../../experiments/REGISTRY.md) but no spec file
here yet. **Write the spec before running the experiment**, in the same shape as
the two above: parameters fixed up front, acceptance criteria, and the failure
modes that invalidate a run.

| Registry ID | Story | Question |
|---|---|---|
| `power-sag` | 1.1 | Terminal voltage vs load current, for each candidate supply |
| `wiring-map` | 1.2 | Resistance/continuity of all 24 motor wires |
| `pwm-capture` | 1.3 | Actual duty, frequency and reversal deadtime on all 4 channels |
| `quad-phase` | 1.4 | Is A/B phasing correct in both directions? |
| `ticks-per-m` | 1.4 | Encoder ticks per metre, by pushing a known distance |
| `failsafe` | 2.1 | Measured time from last valid command to motors stopped |
| `gyro-drift` | 2.2 | Heading drift of an integrated stationary gyro over 10 min |
| `mag-motor` | 2.3 ⭐ | Heading error vs motor duty cycle |
| `gps-scatter` | 2.4 | Static fix scatter over 20 min; CEP |
| `fusion-ladder` | 2.5 ⭐ | Closure error on one course, at each of 4 fusion stages |
| `stop-distance` | 2.6 | Minimum stopping distance vs the D415's 0.45 m blind zone |

**`quad-phase` and `ticks-per-m` block `motor-char`**, because
`ticks_per_output_rev` is what turns encoder ticks into `omega_rad_s` and the
sources genuinely disagree about it (11 vs 14 counts per motor revolution,
possibly ×2 or ×4 depending on the decoding —
[`../HARDWARE.md`](../HARDWARE.md) §2.1). See `motor-char.md` §1 for the
recording decision that makes a later correction a rescale instead of a re-run.

---

## What a good spec in this directory contains

The two existing files are the template. In order:

1. **The question**, in one sentence, phrased as a question
2. **Measurands and units** — SI, stated in the identifier
   (`omega_rad_s`, `duty_frac`, `ticks_per_rev`)
3. **Fixed conditions** — every parameter, with **why that value**
4. **The procedure** — exact steps, dwell times, repeat counts, ordering, and
   what is discarded as transient
5. **What is recorded** — the CSV schema, including the header fields that make
   two runs comparable
6. **Acceptance criteria** — a checklist that is either satisfied or not
7. **Failure modes that invalidate a run** — decided *before* the run, so the
   decision is not made under the temptation of not wanting to re-run
8. **Assumptions**, explicitly listed, so a future session can find and
   challenge them

Item 7 is the one people skip, and it is the one that matters: deciding what
counts as a spoiled run *after* seeing the data is how bad data survives.

---

## The prose rule

**The owner writes the report prose. This is a hard rule**
([`../../CLAUDE.md`](../../CLAUDE.md)).

These specs, the registry, the plots and the numbers are the raw material.
Claude Code assembles them and critiques drafts hard. It does not ghostwrite.
A report the owner did not write is one he cannot defend when an interviewer
asks a follow-up, and defending it is the entire point of having it.
