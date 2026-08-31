# experiments/ — the data layout and the file contracts

**A result not persisted did not happen.** (`CLAUDE.md`, CLOSE ritual item 7.)
A number in a terminal scrollback is not evidence, a plot with no manifest
cannot be defended, and a run whose bench parameters were not written down
cannot be compared with any other run.

This file is the contract between the two halves of the bench software:

- the **capture** side (`tools/rover_bench/`, the host CLI) **writes** these files;
- the **analysis** side (`tools/analysis/`) **reads** them, and refuses to read
  anything that does not match.

If the two ever disagree, `tools/analysis/load.py` is the executable version of
this document and wins. Change both together.

---

## 1. Layout

```
experiments/
  <experiment-id>/            one directory per experiment ID in REGISTRY.md
    <run-id>/                 one directory per run — this is a "run directory"
      manifest.json           REQUIRED. Provenance + the bench parameters.
      telemetry.csv           REQUIRED. One row per control-loop sample.
      analyser.csv            optional. One row per logic-analyser sample.
      *.sr                    optional. Raw sigrok capture. GITIGNORED (large).
      notes.md                optional. Free text, written by a human.
  plots/                      every generated figure (PNG). Committed.
  reports/                    generated per-motor markdown. Committed.
  REGISTRY.md                 one row per experiment, written WHEN IT IS RUN.
```

A **run directory** is any directory containing a `manifest.json`.
`tools/analysis/load.py` finds them by walking for that filename, so the nesting
above is a convention rather than something the loader enforces.

Experiment IDs are the ones in [`REGISTRY.md`](REGISTRY.md); every planned ID
already has a directory with a `.gitkeep` so the layout exists before the data
does.

`.sr` and `.srzip` captures are **gitignored** — they are large and
regenerable from the bench. **The CSVs and manifests are committed.** They are
the record.

---

## 2. `telemetry.csv` — the column contract

One row per control-loop sample. Units are SI and are stated in the column
name; that convention is not decoration, it is the thing that stops a
ticks/s figure being read as rad/s.

### Required columns

| Column | Unit | Meaning |
|---|---|---|
| `t_s` | seconds | Time since the start of the run. **Must be monotonic non-decreasing** — the loader rejects a step backwards. Make it *strictly* increasing: two rows sharing a timestamp pass the loader and then make every derived speed refuse (`dt = 0`). |
| `duty_frac` | fraction, −1…+1 | Commanded PWM duty. **Signed: the sign is the commanded direction.** Positive = `AIN1` HIGH / `AIN2` LOW. A percentage in this column would scale every measured gain by 100, so the loader rejects any value outside ±1. |
| `counts` | encoder counts | **Cumulative, signed** encoder count. Not a per-sample delta. |

**Why `counts` and not `omega_rad_s`?** Because ticks-per-revolution is
*unknown and disputed* — Adafruit says 14 counts per motor revolution,
retailer listings say 11, giving 1100–1400 per output revolution and possibly
×2 or ×4 depending on the decoding. The firmware cannot honestly report rad/s
until that is measured. It reports what it actually observed — edges — and the
conversion happens host-side, where the uncertainty in `ticks_per_output_rev`
can be propagated into every speed number instead of being silently assumed
away.

### Optional columns

| Column | Unit | Meaning |
|---|---|---|
| `omega_rad_s` | rad/s | The firmware's own speed estimate, if it has a ticks/rev to use. |
| `setpoint_rad_s` | rad/s | PID target. **Required for step-response runs** — it is how the step time is found. |
| `current_a` | amperes | Motor supply current, if a shunt or meter is logged. |
| `supply_v` | volts | Measured `VM` at the driver. |
| `loop_dt_s` | seconds | The firmware's own measure of the last loop period. |
| `cpu_busy_frac` | fraction | The firmware's own compute-duty estimate. |

Unknown extra columns are **passed through**, not rejected. The capture side
must be able to add a column without a coordinated release.

### Accepted spellings

`tools/rover_bench`'s storage layer and `tools/analysis` were written in
parallel and settled on different names. Rather than a flag day, the reader
accepts both:

| Canonical | Also accepted | Note |
|---|---|---|
| filename `telemetry.csv` | `samples.csv` | what `rover_bench`'s storage layer writes |
| `t_s` | `time_s`, `timestamp_s` | |
| `counts` | any single column ending `_ticks` | `rover_bench`'s writer **refuses** a header with no unit suffix, so it cannot emit a bare `counts`; its documented suffix for raw encoder counts is `_ticks`. A `*_ticks_per_s` column is not a candidate. **Two** `_ticks` columns is an error — picking one would be a coin toss on the constant every figure scales with. |

### One spelling that is deliberately NOT accepted

`docs/experiments/motor-char.md` §6.1 specifies **`duty_frac_cmd` as 0.0–1.0
with the direction in its own `direction` column** (`fwd`/`rev`). This reader's
contract is the opposite: the **sign of `duty_frac` is the direction**. The two
are not aliasable — folding an unsigned duty onto a signed one makes every
reverse point read as a forward one, so the reverse deadband and gain vanish and
the forward fit is run over both directions at once.

So `load.py` **refuses** a telemetry file that carries a `direction` column with
no negative duty, and refuses `duty_frac_cmd` with a message naming the clash,
rather than converting either. 🔴 **This needs one decision from the owner**
(signed duty, or unsigned duty plus a direction column) and then one edit here,
in `load.py`, and in `docs/experiments/motor-char.md`. Making it before motor 1
is a five-minute edit; making it after motor 4 is four unloadable runs.

---

## 3. `analyser.csv` — the logic-analyser capture

Either of the two shapes that occur in practice is accepted:

1. a `t_s` column plus channel columns; or
2. bare channel columns (raw `sigrok-cli -O csv`), in which case
   `analyser.samplerate_hz` **must** be in the manifest and `t_s` is
   synthesised as `row_index / samplerate_hz`.

Lines beginning with `;` (sigrok's comment header) are skipped. Channel values
must be 0/1.

Channels named `D0`…`D7` are renamed via `analyser.channel_map`. The bench
default, from `docs/WIRING.md`:

| Channel | Pico | Signal | Notes |
|---|---|---|---|
| D0 | GP2 | `pwm_a` | PWM A, 20 kHz |
| D1 | GP3 | `ain1` | direction |
| D2 | GP4 | `ain2` | direction |
| D3 | GP5 | `stby` | HIGH = enabled |
| D4 | GP12 | `enc_a` | encoder A |
| D5 | GP13 | `enc_b` | encoder B |
| D6 | GP20 | `loop_tick` | **toggles once per PID iteration** |
| D7 | GP21 | `compute_busy` | HIGH while the loop body computes |

> ⚠️ **D6 toggles; it does not pulse.** Consecutive edges of *either* polarity
> are one control iteration apart. Counting only rising edges reports exactly
> double the period and half the loop rate. `metrics.loop_periods_s(...,
> mode="toggle")` is the default for this reason.

> ⚠️ **Never map a channel to `AO1`/`AO2`/`BO1`/`BO2`.** Those sit at motor
> voltage and will destroy the 3.3 V analyser. `load.py` refuses any manifest
> that names them, and so should you.

The analyser's **maximum sample rate is not documented in this repo** and must
be discovered at capture time from `sigrok-cli --scan` / `--show`. Whatever it
turns out to be goes in the manifest; nothing downstream assumes a value.

Note the rate needed depends on what is being measured. 100 kHz is ample for
the 100 Hz timing channels (D6/D7) and nowhere near enough to measure the duty
of a 20 kHz PWM — that wants a rate high enough to put many samples inside one
PWM period.

---

## 4. `manifest.json` — the provenance contract

Without a manifest a CSV is a column of numbers with no defence. The manifest
is what lets a figure in the report survive an interviewer's follow-up
question.

### Required

| Key | Meaning |
|---|---|
| `schema_version` | Integer. The analysis side refuses a version *newer* than it understands, and reads older ones. |
| `experiment_id` | Matches a row in `REGISTRY.md`. |
| `utc_started` | ISO-8601 UTC. (`timestamp_utc` is accepted.) |
| `params` | Object; see below. |

### Required inside `params`

Without these the run cannot be converted to SI or compared with any other run.

| Key | Unit | Why it is not optional |
|---|---|---|
| `supply_voltage_v` | V | Speed scales with it. Two runs at different supplies are not comparable. |
| `pwm_hz` | Hz | Changes the effective drive. |
| `loop_hz` | Hz | Sets the sample period, and every timing metric. |
| `ticks_per_output_rev` | counts | Every speed number scales with it. |

### Strongly wanted (absence is a warning, not a refusal)

`gear_ratio`, `encoder_decode` (`x1`/`x2`/`x4`), `direction_convention`,
`ticks_per_output_rev_uncertainty`.

If **no** run in a comparison states one of these, they do not differ and the
comparison is still sound. If **one** run states it and another does not, the
comparability check treats absent and present as different and refuses.

`ticks_per_output_rev_uncertainty` deserves a note: if it is absent, the
analysis side assumes **±150 counts** — half the width of the unresolved
1100–1400 literature disagreement. A manifest that omits it is not read as
claiming the constant is known exactly.

### Accepted spellings, and one that needs fixing

| Canonical | Also accepted | Note |
|---|---|---|
| `utc_started` | `timestamp_utc`, `started_utc`, `timestamp` | |
| `motor_id` | `motor` | |
| `git_commit` | `git_sha` | |
| `run_id` | `id`, else the directory name | |
| `params.ticks_per_output_rev` | top-level `ticks_per_rev` | ⚠️ see below |
| `analyser.samplerate_hz` | top-level `sample_rate_hz` | |

> ⚠️ **`ticks_per_rev` does not say which shaft.** The encoder is on the
> *motor* shaft and the gearbox is 1:100, so a motor-shaft figure read as an
> output-shaft one makes every speed number wrong by 100× — silently. The
> reader borrows it, assumes the output shaft, and attaches a warning to the
> run. **The capture side should write `params.ticks_per_output_rev`** and this
> fallback should then never fire.
>
> A `ticks_per_rev` of `null` (which `rover_bench` writes when nobody has
> measured it yet) is **not** borrowed: the run is refused, because there is no
> honest way to produce rad/s without it.

> ⚠️ **The bridge is partial, and the two sides still need reconciling.** As
> written, `rover_bench` records `expected_loop_hz` inside `params` and does not
> record `supply_voltage_v` or `pwm_hz` at all, so one of its manifests is still
> **refused** by `tools/analysis/load.py` — loudly, naming the missing keys.
> That refusal is correct (a run with no stated supply voltage cannot be
> compared with another), so the fix belongs on the capture side or in a
> deliberate joint decision, not in another alias. Whoever closes it should
> change this file, `load.py` and the capture side in one commit.

### Recorded, and compared only as advisory

`run_id`, `motor_id`, `git_commit`, `firmware_version`, `operator`,
`params.supply_kind`, `params.ambient_c`, `analyser.samplerate_hz`.

These may differ between runs without blocking a comparison, but the
difference is printed in the report's provenance section, so a surprising
number has somewhere to be checked.

### Safety fields the loader enforces

| Field | Rule | Why |
|---|---|---|
| `params.encoder_supply_v` | ≤ 3.6 V | RP2350 GPIO is **not** 5 V tolerant. |
| `params.supply_voltage_v` | ≤ 13.5 V | TB6612FNG `VM` absolute maximum. A 4S LiPo (16.8 V) must pass through the regulator first. |
| `analyser.channel_map` | no `AO*`/`BO*` | H-bridge outputs sit at motor voltage. |

A manifest that violates one of these describes a bench that is damaging
hardware, so it raises rather than loading.

The loader also refuses an `analyser.csv` whose **own header** names an
H-bridge output (a column called `AO1`), not just a manifest that maps one:
sigrok labels channels from whatever the operator typed, so such a file is a
record of the probe having been on a motor output.

### Warnings the loader attaches to a run

Not fatal, but each one changes how a number should be read, so they are
carried on the `Run` and printed in the generated report:

| Situation | Why it matters |
|---|---|
| `params` omits a recommended key | A later comparison against a run that *does* state it will be refused. |
| `ticks/rev` borrowed from top-level `ticks_per_rev` | The key does not say which shaft — a motor-shaft figure read as an output-shaft one is 100× out. |
| No `motor_id` | Unlabelled runs all group under one key, so four of them would be fitted as if they were one motor. |
| No `analyser.channel_map` | `docs/WIRING.md` carries **two** channel maps that differ at exactly D6/D7 (§8 has UART TX/RX there, §10.2 has `LOOP_TICK`/`COMPUTE_BUSY`). The loader assumes §10.2; a §8 capture read that way yields a "loop period" measured off UART traffic. |

### Example

⚠️ **Illustrative — no such run exists yet, and every number in it is a
placeholder.** In particular `ticks_per_output_rev` is **not measured**: 1400 is
the top of the disputed 1100–1400 range, written here only so the example
parses. Replace it with the Story 1.4 bench measurement before quoting a rad/s
figure from any run. `encoder_decode` and `ticks_per_output_rev` must be changed
**together** — 1100–1400 is the single-edge count, so an ×4 decoder on the same
encoder sees roughly 4× it. `analyser.samplerate_hz` is whatever
`sigrok-cli --scan` reported for that capture; it is not a documented capability
of the FX2 clone and nothing may assume it.

```json
{
  "schema_version": 1,
  "run_id": "m1-staircase-01",
  "experiment_id": "motor-char",
  "story": "1.5",
  "utc_started": "2026-09-02T10:15:00Z",
  "motor_id": 1,
  "operator": "oliver",
  "git_commit": "5bbc226",
  "firmware_version": "motor-char-0.3",
  "notes": "lab PSU at 6.00 V, current limit 1.0 A. Motor pair confirmed at 4.7 ohm before wiring.",
  "params": {
    "supply_voltage_v": 6.0,
    "supply_kind": "lab PSU, current limited",
    "pwm_hz": 20000,
    "loop_hz": 100,
    "ticks_per_output_rev": 1400.0,
    "ticks_per_output_rev_uncertainty": 150.0,
    "gear_ratio": 100.0,
    "encoder_decode": "x1",
    "encoder_supply_v": 3.3,
    "direction_convention": "positive duty_frac = AIN1 HIGH / AIN2 LOW"
  },
  "analyser": {
    "samplerate_hz": 1000000,
    "channel_map": {
      "D0": "pwm_a", "D1": "ain1", "D2": "ain2", "D3": "stby",
      "D4": "enc_a", "D5": "enc_b", "D6": "loop_tick", "D7": "compute_busy"
    }
  },
  "files": { "telemetry": "telemetry.csv", "analyser": "analyser.csv" }
}
```

A manifest carrying `"synthetic": true` was produced by
`tools/analysis/synthetic.py` and is **not evidence about any real motor**.
Generated reports stamp it loudly.

---

## 5. Comparability — why the analysis side refuses runs

Story 1.5's argument is that four supposedly identical motors are measurably
different. The single most likely way to get that argument **wrong** is to
compare motor 1 against motor 4 when something else also changed — a different
supply voltage, a different PWM frequency, a different assumed ticks/rev, a
firmware rebuild that changed the duty scaling. The difference then gets
attributed to the motors, the report says "four identical motors differ by
18 %", and the number is worthless.

So comparing runs whose manifests disagree on a **critical** parameter
(`schema_version`, `supply_voltage_v`, `pwm_hz`, `loop_hz`,
`ticks_per_output_rev`, `encoder_decode`, `gear_ratio`,
`direction_convention`) is an **error**. It raises, with a table of exactly
what differs.

`supply_voltage_v` is compared to ±0.05 V — a measured 6.00 and 5.98 are the
same bench; 6.0 and 5.0 are not.

`motor_id` is deliberately **exempt**: differing motors is the point.

The refusal can be overridden (`require_comparable=False`,
`--allow-incomparable`), and if it is, say so in the report.

---

## 6. Running the analysis

```bash
# generate a synthetic campaign to develop against (no hardware needed)
python -m analysis.synthetic /tmp/fake-campaign

# reduce a real campaign to reports + figures
python -m analysis.report experiments/motor-char \
       --out experiments/reports --plots experiments/plots
```

Run these with `tools/` on `PYTHONPATH` (e.g. `PYTHONPATH=tools`). See
[`../tools/analysis/README.md`](../tools/analysis/README.md).

Every rad/s figure in a generated report is inversely proportional to
`ticks_per_output_rev`, which is not yet measured, so each report states that
scale uncertainty (±150/1400 ≈ ±11 % by default) separately from the fits'
statistical error bars. It is a *common* factor, so it cancels between motors
and does not cancel in any absolute speed.

The generated reports contain **numbers, tables, figures and provenance only**.
Every interpretive section is left empty, marked `<!-- OWNER-WRITES-THIS -->`,
with prompting questions. The owner writes the prose — that is a hard rule from
`CLAUDE.md` and `docs/career-track.md`, and it is enforced in code by a
banned-phrase check in `tools/analysis/report.py`.

To find what is still unwritten:

```bash
grep -rc 'OWNER-WRITES-THIS' experiments/reports/
```
