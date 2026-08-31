# tools/analysis — raw CSV in, defensible numbers and figures out

The offline half of the bench. Nothing here touches a Pico, a serial port or a
logic analyser: it reads files that are already on disk. That is deliberate —
it means the whole half is developable and testable on a laptop with nothing
plugged in, using `synthetic.py` for data.

| Module | Job |
|---|---|
| `load.py` | Read a run directory, validate the schema, and **refuse to analyse runs whose manifests make them incomparable.** |
| `metrics.py` | The derived numbers, one small pure function each. Every one returns the value **and** its uncertainty. |
| `plots.py` | Matplotlib figures in one house style, written to `experiments/plots/`. |
| `report.py` | Assemble per-motor markdown: numbers, tables, figures, provenance. **Never prose.** |
| `synthetic.py` | Realistic fake runs, so all of the above works with zero hardware. |

The file formats are specified in
[`../../experiments/README.md`](../../experiments/README.md); `load.py` is the
executable version of that document.

---

## Two rules that are not negotiable

**1. A number without an error bar is not a measurement.** Every metric returns
a `Measurement(value, uncertainty, unit, n, method)`. The `method` field says
how the uncertainty was arrived at, so the report can quote it and the owner
can defend it. Four motors whose gains differ by 3 % mean nothing if the
per-motor uncertainty is 5 %.

**2. The owner writes all prose.** `report.py` generates tables, figures,
captions and numbers, and leaves every interpretive section empty with
prompting questions. It does not write "the results show that…". This comes
from `CLAUDE.md` and `docs/career-track.md`: a report he did not write is one
he cannot defend when an interviewer asks a follow-up, and defending it is the
entire point of having it. The boundary is enforced by a banned-phrase check
(`report.assert_no_generated_prose`) that runs on every generated report.

---

## Usage

Everything imports as the `analysis` package with `tools/` on `PYTHONPATH`.

```bash
export PYTHONPATH=tools

# 1. Generate a synthetic four-motor campaign (no hardware needed)
python -m analysis.synthetic /tmp/fake-campaign

# 2. Reduce a campaign to reports + figures
python -m analysis.report /tmp/fake-campaign/motor-char \
       --out experiments/reports --plots experiments/plots
```

From Python:

```python
from analysis import load, metrics, plots, report

runs = load.load_campaign("experiments/motor-char")   # raises if incomparable
analyses = report.analyse_campaign(runs)
plots.plot_four_motor_overlay(
    [a.curve("forward") for a in analyses.values()],
    path=plots.plot_path("motor-char-four-motor-overlay"),
)
```

---

## Tests

238 host-side tests, no hardware, ~10 s.

```bash
PYTHONPATH=tools .venv/bin/python -m pytest tools/analysis/tests -q
```

With the coverage gate (the owner's instruction this session was "shitty code,
lots of tests" at an enforced 80 % line gate; this package sits around 95 %):

```bash
PYTHONPATH=tools .venv/bin/python -m pytest tools/analysis/tests -q \
    --cov=analysis --cov-report=term-missing
```

> **Integration note for whoever owns the root test harness.** The repo-level
> `pytest.ini` has `testpaths = tests` and `.coveragerc` has
> `source = tools/rover_bench`, so `make test` does **not** currently collect
> these tests or measure this package. Two one-line changes fix it:
> add `tools/analysis/tests` to `testpaths`, and `tools/analysis` to
> `source`. Both files belong to another stream, so they were left alone.

### What the tests are actually for

- **`test_load.py`** — the comparability refusal, mostly. Comparing motor 1
  against motor 4 across a changed bench parameter is the single most likely
  way this campaign produces a wrong conclusion, so the refusal is tested as
  carefully as the happy path. Also the hardware-safety invariants: a manifest
  claiming a 5 V encoder supply, a 16.8 V driver rail, or an analyser channel
  on an H-bridge output is refused rather than loaded.
- **`test_metrics.py`** — every metric that has a closed-form answer is checked
  against data built to have that answer, and the check is that the metric
  recovers it *within its own stated uncertainty*.
- **`test_plots.py`** — the computable part of "does this figure read well":
  the palette clears the colour-vision separation floors (all pairs, computed
  with the Machado 2009 dichromacy matrices in OKLab), no series relies on
  colour alone, and every figure writes a real PNG.
- **`test_report.py`** — the prose boundary, from both directions: interpretive
  sentences are caught, factual captions are not, and a real generated report
  is run through the tripwire.
- **`test_synthetic.py`** — that the fake data satisfies the real loader's
  contract, and that every synthetic manifest is stamped `synthetic: true`.

### Regression tests worth knowing about

- **Smoothing must not corrupt the ends of a record.**
  `np.convolve(..., mode="same")` zero-pads, which dragged the last samples —
  exactly the steady-state tail every step-response metric is measured against
  — toward zero, so *smoothing made the tail scatter larger*. Fixed with
  edge-padding in `metrics.moving_average`;
  `test_smoothing_does_not_drag_the_ends_of_the_record_toward_zero` guards it.
- **D6 toggles, it does not pulse.** Counting only rising edges reports exactly
  double the loop period. `test_a_toggling_loop_tick_must_be_read_on_both_edges`
  pins both readings so the factor of two cannot come back.
- **A duplicated `run_id` used to hide a bench change.** The difference table
  was keyed by `run_id`, and two runs can share one (the capture side falls back
  to the directory name). The dict collapsed them into a single entry, which
  cannot disagree with itself, so an incomparable campaign passed.
  `load.comparison_labels` disambiguates;
  `test_two_runs_sharing_a_run_id_still_refuse_an_incomparable_bench` guards it.
- **Duplicate duty levels.** Concatenating repeats of the same staircase puts
  two rows at the same duty, which made the saturation-detection secants
  divide by zero. `find_linear_region` now collapses duplicate levels first.

---

## Design notes worth carrying forward

**Why the loader wants `counts`, not `omega_rad_s`.** Ticks-per-revolution is
unknown and disputed (Adafruit says 14 counts per motor rev, retailers say 11).
The firmware cannot honestly report rad/s until it is measured, so it reports
edges and the conversion happens here, where the uncertainty propagates into
every speed number. A manifest with no stated `ticks_per_output_rev_uncertainty`
is read as ±150 counts — half the literature spread — not as zero.

**Statistical error bars are not the whole error.** Every rad/s number is
`2*pi*dcounts / (ticks_per_output_rev * dt)`, so it scales inversely with a
constant nobody has measured (11 vs 14 counts per motor revolution). At the
default ±150 that is ±11 %, against fit standard errors of well under 1 % — so
`report.scale_uncertainty_note` states it once per report rather than folding it
into each `Measurement`. Folding it in would double-count it in the
motor-to-motor comparison, where a common scale factor cancels. Statistical and
systematic are kept apart, as anywhere else.

**Two deadband estimators, on purpose.** `deadband_duty_frac` brackets it
between the last duty that did not move and the first that did; its uncertainty
is half the duty step, and it is biased *high* by roughly
`omega_threshold / gain`. `LinearFit.x_at_zero` extrapolates the linear region
down to zero and is unbiased but model-dependent. The report quotes both, and
the two agreeing is a genuine consistency check on the whole curve.

**The four-motor overlay is the point.** Story 1.5's argument — four
"identical" motors are measurably different, therefore open loop was never
going to work — is that one figure. It is the one that goes in the report and
on LinkedIn, so it gets direct labels as well as a legend, a computed spread
annotation at a reference duty, deadband ticks on the axis, and a palette that
survives being printed in greyscale.

**The palette is capped at four series.** A fifth colour cannot clear the
all-pairs separation floors. Four wheels, four colours; anything more should
facet, and `series_style` raises rather than cycling.
