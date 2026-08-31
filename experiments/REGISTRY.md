# Experiment registry

One row per experiment. **A result not persisted did not happen** — CLOSE
ritual item 7.

Add the row when the experiment is run, not later. Raw data goes in
`experiments/<id>/`, plots in `experiments/plots/`.

| ID | Date | Story | Question | Method | Result | Data |
|---|---|---|---|---|---|---|
| _(example)_ | 2026-08-31 | 1.1 | What is the 6F22's internal resistance? | Measure open-circuit V, then V under ~50 mA | 8.2 V → 7.7 V ⇒ **~10 Ω**. Unusable for motors | — |

## Planned

| ID | Story | Question |
|---|---|---|
| `power-sag` | 1.1 | Terminal voltage vs load current, for each candidate supply |
| `wiring-map` | 1.2 | Resistance/continuity of all 24 motor wires |
| `pwm-capture` | 1.3 | Actual duty, frequency and reversal deadtime on all 4 channels |
| `quad-phase` | 1.4 | Is A/B phasing correct in both directions? |
| `ticks-per-m` | 1.4 | Encoder ticks per metre, measured by pushing a known distance |
| `motor-char` | 1.5 | ⭐ Deadband, duty→rad/s, no-load and stall current, ×4 motors |
| `pid-step` | 1.6 | Step response before vs after tuning |
| `failsafe` | 2.1 | Measured time from last valid command to motors stopped |
| `gyro-drift` | 2.2 | Heading drift of an integrated stationary gyro over 10 min |
| `mag-motor` | 2.3 | ⭐ Heading error vs motor duty cycle |
| `gps-scatter` | 2.4 | Static fix scatter over 20 min; CEP |
| `fusion-ladder` | 2.5 | ⭐ Closure error on one course, at each of 4 fusion stages |
| `stop-distance` | 2.6 | Minimum stopping distance vs the D415's 0.45 m blind zone |

---

## How to use this registry

### The row format

One row per experiment, in the table at the top. Seven columns, and each one
answers a question a reader (an interviewer, or you in three weeks) will ask.

| Column | What goes in it | Example |
|---|---|---|
| **ID** | The experiment ID. Matches a directory: `experiments/<ID>/`. Lower-case, hyphenated, stable — never renamed once data exists under it. | `motor-char` |
| **Date** | UTC date the experiment was **run**, `YYYY-MM-DD`. Not the date it was planned or written up. | `2026-09-02` |
| **Story** | The story from [`../docs/PLAN.md`](../docs/PLAN.md) §5–7 that this serves. | `1.5` |
| **Question** | The question the experiment was supposed to answer, in one sentence, phrased **before** the answer was known. If you cannot write it without referring to the result, the experiment did not have a question. | `Do four "identical" N20s share a duty→rad/s curve?` |
| **Method** | How it was measured, briefly, and enough to repeat it. Name the instrument. | `Duty staircase 0.05→1.00, 1 s per step, both directions, lab PSU 5.00 V / 1.00 A limit (BENCH.md), D6/D7 on the analyser` |
| **Result** | The number **with its uncertainty**, and the conclusion in the smallest number of words. A number without an error bar is not a measurement. Where a figure rests on an unmeasured constant, say so. | `Gain 33.3–37.7 rad/s per duty_frac (fit ±0.06; a further ±11 % common scale from unmeasured ticks/rev) → 13 % spread across 4 motors` |
| **Data** | Relative link to the run directory, or `—` if there is genuinely no file (a meter reading, say). | `[motor-char/](motor-char/)` |

**The Example column above is illustrative, not a record.** Those numbers were
invented to show the shape of a row; the real ones come from the bench. Nothing
in this file is evidence until it has a run directory behind it.

An experiment that fails, or that answers its question with "no", still gets a
row. A dead end that is recorded is a result; a dead end that is not is an
evening you will spend again.

### When the row is written

**When the experiment is run.** Not at the end of the session, not in week
three, not "once the plot looks right".

This is CLOSE ritual item 7 in [`../CLAUDE.md`](../CLAUDE.md): *a result not
persisted did not happen.* The row, the raw data and the plots go in the **same
commit**. By week three the memory of why a run was configured the way it was
is gone, and a registry filled in from a cold repo reads like one.

Practically: run it → the run directory is written by the capture tool → add
the row → generate the plots → commit all of it together.

### Where things live

| What | Where | Committed? |
|---|---|---|
| Raw telemetry + manifests | `experiments/<ID>/<run-id>/` | **yes** — this is the record |
| Raw analyser captures (`.sr`, `.srzip`) | beside them | **no** — gitignored; large and regenerable |
| Generated figures (PNG) | `experiments/plots/` | **yes** |
| Generated per-motor markdown | `experiments/reports/` | **yes** |
| The owner's written report | his prose, not generated | see [`../docs/career-track.md`](../docs/career-track.md) §3 |

Every planned ID above already has a directory with a `.gitkeep`, so the layout
exists before the data does. The file formats — the CSV columns, the manifest
keys, and the rule about which runs may be compared with which — are specified
in [`README.md`](README.md), and enforced by `tools/analysis/load.py`.

### The comparability rule, in one line

Two runs may only be compared if their manifests agree on supply voltage, PWM
frequency, loop rate, assumed ticks/rev, gear ratio, encoder decoding and
direction convention. Differing motors is fine — that is the experiment. See
[`README.md`](README.md) §5 for why this is a hard error rather than a warning.
