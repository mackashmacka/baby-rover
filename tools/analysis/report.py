"""Assemble a per-motor markdown report -- numbers, tables, figures, provenance.

╔══════════════════════════════════════════════════════════════════════════╗
║  THE OWNER WRITES ALL PROSE. THIS MODULE MUST NEVER GENERATE NARRATIVE.  ║
╚══════════════════════════════════════════════════════════════════════════╝

This boundary comes from ``CLAUDE.md`` ("The owner writes the report prose.
This is a hard rule.") and ``docs/career-track.md`` ("A report he did not write
is one he cannot defend when an interviewer asks a follow-up, and defending it
is the entire point of having it."). It is written here, in the module that
would be tempted to erode it, so that a future session reads it before editing.

**Allowed** -- these are raw material, not argument:

* tables of measured values and their uncertainties
* figure embeds with *factual* captions ("Figure 2 - duty_frac vs omega_rad_s,
  motors 1-4, forward direction, n = 20 duty steps each")
* the manifest provenance of every run that fed a number
* a comparison table of this motor against the others measured so far
* empty sections with prompting QUESTIONS for the owner to answer

**Forbidden** -- these are the owner's job, and his alone:

* "the results show that...", "this demonstrates...", "as expected..."
* any sentence that interprets, concludes, explains *why*, or recommends
* filling in a section marked for him, even with something obviously true

:func:`assert_no_generated_prose` enforces the letter of this with a banned-
phrase list, and a test calls it on real generated output. That check is a
tripwire, not the rule; the rule is the paragraph above. If a future change
needs to defeat the tripwire, that change is almost certainly the erosion this
docstring exists to prevent.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from . import load as load_mod
from . import metrics as metrics_mod
from . import plots as plots_mod
from .load import Run
from .metrics import (
    Aggregate,
    DutyOmegaModel,
    LoopJitter,
    Measurement,
    MetricError,
    StepResponse,
)

#: Phrases that mean somebody started writing the report for him.
#: Deliberately blunt -- a false positive costs one reworded caption; a false
#: negative costs the thing the whole record exists for.
BANNED_PROSE_PATTERNS: tuple[str, ...] = (
    r"\bthe results? (show|suggest|indicate|demonstrate)",
    r"\bthis (shows|suggests|indicates|demonstrates|means|implies|confirms)",
    r"\bwe can (conclude|see|infer)",
    r"\bit is clear that\b",
    r"\bas expected\b",
    r"\bunsurprisingly\b",
    r"\bin conclusion\b",
    r"\boverall,",
    r"\bthe data (shows|suggests|indicates)",
    r"\bwhich (means|suggests|implies) that\b",
    r"\bthis is because\b",
    r"\bthe reason (is|for this)\b",
)

#: Marker dropped into every section the owner has to fill in. Grep-able, so
#: "is the report finished?" is a one-line shell command.
OWNER_MARKER = "<!-- OWNER-WRITES-THIS -->"

UNFILLED_PLACEHOLDER = "_(your words go here — this tool does not write prose)_"


class ReportError(ValueError):
    """A report cannot be assembled from what was passed in."""


# --------------------------------------------------------------------------
# The prose tripwire
# --------------------------------------------------------------------------


def assert_no_generated_prose(text: str) -> None:
    """Raise if generated text contains interpretive prose.

    Only the *generated* parts should ever be passed here. Once the owner has
    written his sections, his words are his; this is a check on the tool, not
    a style guide for him.
    """
    lowered = text.lower()
    hits = [p for p in BANNED_PROSE_PATTERNS if re.search(p, lowered)]
    if hits:
        raise ReportError(
            "generated report text contains interpretive prose, which this "
            "module must never write (see the module docstring and CLAUDE.md). "
            f"Matched: {hits}"
        )


# --------------------------------------------------------------------------
# Analysis orchestration
# --------------------------------------------------------------------------


@dataclass
class MotorAnalysis:
    """Everything computed for one motor, plus what could not be computed."""

    motor_id: Any
    runs: list[Run]
    steps: pd.DataFrame | None = None
    forward: DutyOmegaModel | None = None
    reverse: DutyOmegaModel | None = None
    deadband_forward_repeats: Aggregate | None = None
    deadband_reverse_repeats: Aggregate | None = None
    jitter: LoopJitter | None = None
    cpu_duty: Measurement | None = None
    step: StepResponse | None = None
    problems: list[str] = field(default_factory=list)

    @property
    def is_synthetic(self) -> bool:
        return any(bool(r.manifest.get("synthetic")) for r in self.runs)

    def curve(self, direction: str = "forward") -> plots_mod.MotorCurve | None:
        """The measured points for one direction, ready to plot."""
        model = self.forward if direction == "forward" else self.reverse
        if self.steps is None or model is None:
            return None
        sign = 1.0 if direction == "forward" else -1.0
        subset = self.steps[np.sign(self.steps["duty_frac"].to_numpy()) == sign]
        return plots_mod.MotorCurve(
            motor_id=self.motor_id,
            duty_frac=subset["duty_frac"].to_numpy(dtype=float),
            omega_rad_s=subset["omega_rad_s"].to_numpy(dtype=float),
            omega_err_rad_s=subset["omega_stddev_rad_s"].to_numpy(dtype=float),
            deadband=model.deadband,
            gain=model.gain,
        )


def analyse_motor(
    runs: Sequence[Run],
    *,
    omega_threshold_rad_s: float | None = None,
    settle_frac: float = 0.5,
) -> MotorAnalysis:
    """Run every metric that this motor's data supports.

    Failures are collected in ``problems`` rather than raised: a report that
    is missing its jitter section because D6 was not probed is far more useful
    than no report at all, and the gap is stated rather than hidden.
    """
    if not runs:
        raise ReportError("analyse_motor() needs at least one run")
    motor_id = runs[0].motor_id
    analysis = MotorAnalysis(motor_id=motor_id, runs=list(runs))

    primary = runs[0]
    ticks = primary.ticks_per_output_rev
    if omega_threshold_rad_s is None:
        # One encoder count per control period: the floor below which "moving"
        # and "noise" are the same measurement.
        omega_threshold_rad_s = metrics_mod.omega_quantisation_rad_s(
            1.0 / primary.loop_hz, ticks
        )

    per_run_steps: list[pd.DataFrame] = []
    forward_deadbands: list[Measurement] = []
    reverse_deadbands: list[Measurement] = []
    for run in runs:
        try:
            steps = metrics_mod.duty_staircase_steps(
                run.telemetry,
                ticks_per_output_rev=run.ticks_per_output_rev,
                settle_frac=settle_frac,
            )
        except MetricError as exc:
            analysis.problems.append(f"{run.run_id}: duty staircase — {exc}")
            continue
        per_run_steps.append(steps)
        for direction, sink in (("forward", forward_deadbands), ("reverse", reverse_deadbands)):
            try:
                sink.append(
                    metrics_mod.deadband_duty_frac(
                        steps["duty_frac"].to_numpy(dtype=float),
                        steps["omega_rad_s"].to_numpy(dtype=float),
                        omega_threshold_rad_s=omega_threshold_rad_s,
                        direction=direction,  # type: ignore[arg-type]
                    )
                )
            except MetricError as exc:
                analysis.problems.append(f"{run.run_id}: {direction} deadband — {exc}")

    if per_run_steps:
        analysis.steps = pd.concat(per_run_steps, ignore_index=True)
        for direction in ("forward", "reverse"):
            try:
                model = metrics_mod.fit_duty_to_omega(
                    analysis.steps,
                    direction=direction,  # type: ignore[arg-type]
                    omega_threshold_rad_s=omega_threshold_rad_s,
                )
            except MetricError as exc:
                analysis.problems.append(f"motor {motor_id}: {direction} fit — {exc}")
                continue
            setattr(analysis, direction, model)

    if len(forward_deadbands) > 1:
        analysis.deadband_forward_repeats = metrics_mod.deadband_spread(forward_deadbands)
    if len(reverse_deadbands) > 1:
        analysis.deadband_reverse_repeats = metrics_mod.deadband_spread(reverse_deadbands)

    for run in runs:
        if run.analyser is None:
            continue
        sample_period = (
            1.0 / run.analyser_samplerate_hz if run.analyser_samplerate_hz else None
        )
        try:
            periods = metrics_mod.loop_periods_s(
                run.analyser["t_s"], run.channel("loop_tick")
            )
            analysis.jitter = metrics_mod.loop_jitter(
                periods, target_s=1.0 / run.loop_hz, sample_period_s=sample_period
            )
        except (MetricError, load_mod.RunSchemaError) as exc:
            analysis.problems.append(f"{run.run_id}: loop jitter — {exc}")
        try:
            analysis.cpu_duty = metrics_mod.cpu_duty_frac(
                run.analyser["t_s"],
                run.channel("compute_busy"),
                loop_tick_level=run.channel("loop_tick"),
            )
        except (MetricError, load_mod.RunSchemaError) as exc:
            analysis.problems.append(f"{run.run_id}: CPU duty — {exc}")
        break  # one analyser capture is enough; they are all the same firmware

    return analysis


def analyse_campaign(runs: Iterable[Run], **kwargs: Any) -> dict[Any, MotorAnalysis]:
    """Analyse every motor in a loaded campaign, keyed by ``motor_id``."""
    grouped = load_mod.group_by_motor(runs)
    return {
        motor_id: analyse_motor(motor_runs, **kwargs)
        for motor_id, motor_runs in sorted(grouped.items(), key=lambda kv: str(kv[0]))
    }


def analyse_step_run(
    run: Run, *, smooth_n: int = 5, settle_band_frac: float = 0.05
) -> tuple[plots_mod.StepTrace, StepResponse | None, str | None]:
    """Reduce one PID step run to a plottable trace plus its metrics."""
    t = run.telemetry["t_s"].to_numpy(dtype=float)
    omega = metrics_mod.omega_from_counts(
        t, run.telemetry["counts"].to_numpy(dtype=float),
        run.ticks_per_output_rev, smooth_n=smooth_n,
    )
    if "setpoint_rad_s" not in run.telemetry.columns:
        raise ReportError(
            f"{run.run_id}: a step-response run needs a setpoint_rad_s column"
        )
    setpoint_series = run.telemetry["setpoint_rad_s"].to_numpy(dtype=float)
    setpoint = float(np.abs(setpoint_series).max())
    try:
        t_step = metrics_mod.find_step_time(t, setpoint_series)
    except MetricError as exc:
        return (
            plots_mod.StepTrace(
                label=str(run.manifest.get("tuning", run.run_id)),
                t_s=t, omega_rad_s=omega, setpoint_rad_s=setpoint,
            ),
            None,
            str(exc),
        )

    problem: str | None = None
    result: StepResponse | None = None
    try:
        result = metrics_mod.step_response(
            t, omega, setpoint=setpoint, t_step_s=t_step,
            settle_band_frac=settle_band_frac,
        )
    except MetricError as exc:
        problem = str(exc)

    trace = plots_mod.StepTrace(
        label=str(run.manifest.get("tuning", run.run_id)),
        t_s=t,
        omega_rad_s=omega,
        setpoint_rad_s=setpoint,
        metrics=result,
        t_step_s=t_step,
    )
    return trace, result, problem


# --------------------------------------------------------------------------
# Markdown building blocks -- tables and facts only
# --------------------------------------------------------------------------


def format_measurement(value: Measurement | None, digits: int = 4) -> str:
    return "—" if value is None else value.render(digits)


def markdown_table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
    """A GitHub-flavoured markdown table. Empty rows render as a stated gap."""
    if not rows:
        return "_no rows — see the gaps table below._\n"
    head = "| " + " | ".join(str(h) for h in headers) + " |"
    rule = "|" + "|".join("---" for _ in headers) + "|"
    body = [
        "| " + " | ".join("" if c is None else str(c) for c in row) + " |"
        for row in rows
    ]
    return "\n".join([head, rule, *body]) + "\n"


def measurement_rows(pairs: Sequence[tuple[str, Measurement | None]]) -> list[list[str]]:
    rows: list[list[str]] = []
    for name, measurement in pairs:
        if measurement is None:
            rows.append([name, "—", "—", "—", "not computed"])
            continue
        rows.append(
            [
                name,
                f"{measurement.value:.4g}",
                f"± {measurement.uncertainty:.3g}",
                measurement.unit or "—",
                measurement.method or "—",
            ]
        )
    return rows


def provenance_table(runs: Sequence[Run]) -> str:
    """Which files, which commit, which bench parameters produced these numbers."""
    rows = []
    for run in runs:
        params = run.params
        rows.append(
            [
                f"`{run.run_id}`",
                run.manifest.get("utc_started", "—"),
                f"`{run.manifest.get('git_commit', '—')}`",
                run.manifest.get("firmware_version", "—"),
                f"{params.get('supply_voltage_v', '—')} V",
                f"{params.get('pwm_hz', '—')} Hz",
                f"{params.get('loop_hz', '—')} Hz",
                f"{params.get('ticks_per_output_rev', '—')}",
                len(run.telemetry),
                "yes" if run.analyser is not None else "no",
            ]
        )
    return markdown_table(
        ["run", "started (UTC)", "commit", "firmware", "supply", "PWM",
         "loop", "ticks/rev", "samples", "analyser"],
        rows,
    )


def loader_warnings_block(runs: Sequence[Run]) -> str:
    """Everything ``load.py`` warned about while reading these runs.

    The loader's warnings are the ones that change how a number should be
    read -- "ticks/rev was borrowed from a key that does not say which shaft",
    "the analyser channel map was assumed". Leaving them on the ``Run`` object
    means they are only seen by whoever is at a REPL, which is nobody the day
    the report is read. So they go in the report.
    """
    rows = [[f"`{run.run_id}`", warning] for run in runs for warning in run.warnings]
    if not rows:
        return ""
    return (
        "\nWarnings raised while loading these runs (each one changes how a "
        "number below should be read):\n\n"
        + markdown_table(["run", "warning from the loader"], rows)
    )


def scale_uncertainty_note(analysis: MotorAnalysis) -> str:
    """State the ticks/rev scale uncertainty carried by every rad/s figure.

    WHY THIS IS A SEPARATE STATEMENT AND NOT A COLUMN. Every speed in this
    package comes from ``omega = 2*pi * dcounts / (ticks_per_output_rev * dt)``,
    so every rad/s number is inversely proportional to a constant nobody has
    measured yet (Adafruit says 14 counts per motor revolution, retailers say
    11 -- 1100 to 1400 per output revolution). At the default ±150 that is
    roughly ±11%, which is larger than any statistical error bar the fits
    produce, so a report that quoted only the fit's ± would be claiming a
    precision it does not have.

    It is not folded into each ``Measurement`` because it is a *common* scale
    factor: it cancels in a motor-to-motor ratio, and adding it to both sides
    of that comparison would inflate the one number Story 1.5 rests on. Stated
    once, separately, is the honest form -- the same split as statistical vs
    systematic error anywhere else.
    """
    run = analysis.runs[0]
    ticks = run.ticks_per_output_rev
    if ticks <= 0:
        return ""
    uncertainty = run.ticks_per_output_rev_uncertainty
    source = (
        "stated in the manifest"
        if "ticks_per_output_rev_uncertainty" in run.params
        else "the default, being half the unresolved 1100-1400 literature spread; "
        "no manifest here states one"
    )
    relative = uncertainty / ticks
    example = ""
    if analysis.forward is not None:
        gain = analysis.forward.gain
        on_the_gain = metrics_mod.scale_uncertainty_from_ticks(
            gain.value, ticks, uncertainty
        )
        example = (
            f" On the forward gain of {gain.value:.4g} rad/s per duty_frac that is "
            f"± {on_the_gain:.3g} rad/s from this term alone, against a fit "
            f"standard error of ± {gain.uncertainty:.3g}."
        )
    return (
        f"\n**Scale uncertainty on every rad/s figure in this section.** "
        f"`ticks_per_output_rev` = {ticks:g} ± {uncertainty:g} counts per output "
        f"revolution ({source}). omega is inversely proportional to it, so every "
        f"absolute speed, gain and saturation figure in the tables below carries "
        f"a further ± {100.0 * relative:.1f} % that is NOT in their ± columns, which are "
        f"statistical only.{example} The same factor multiplies every motor "
        f"measured under the same manifest value, so the §5 spread between "
        f"motors is unaffected by it. Story 1.4 measures the constant "
        f"(`metrics.ticks_per_rev`); until then no absolute rad/s figure here "
        f"is better than ± {100.0 * relative:.1f} %.\n"
    )


def comparison_table(
    this: MotorAnalysis, others: Sequence[MotorAnalysis], direction: str = "forward"
) -> str:
    """This motor's headline numbers beside every other motor measured so far."""
    everyone = [this, *[o for o in others if o.motor_id != this.motor_id]]
    rows = []
    gains: list[float] = []
    for analysis in everyone:
        model = getattr(analysis, direction)
        if model is None:
            rows.append([f"motor {analysis.motor_id}", "—", "—", "—", "—"])
            continue
        gains.append(model.gain.value)
        rows.append(
            [
                f"**motor {analysis.motor_id}**"
                if analysis.motor_id == this.motor_id
                else f"motor {analysis.motor_id}",
                format_measurement(model.deadband, 3),
                format_measurement(model.gain, 4),
                format_measurement(model.omega_max, 4) if model.omega_max else "—",
                "—" if model.saturation_onset_duty_frac is None
                else f"{model.saturation_onset_duty_frac:.2f}",
            ]
        )
    table = markdown_table(
        ["motor", "deadband (duty_frac)", "gain (rad/s per duty_frac)",
         "saturated omega_rad_s", "saturation onset (duty_frac)"],
        rows,
    )
    if len(gains) >= 2:
        spread = max(gains) - min(gains)
        mean = sum(gains) / len(gains)
        table += (
            f"\nGain spread across {len(gains)} motors: "
            f"{min(gains):.3g} – {max(gains):.3g} rad/s per duty_frac, "
            f"a spread of {spread:.3g} "
            f"({100.0 * spread / mean:.1f} % of the mean).\n"
        )
    return table


def figure_block(caption: str, figure_path: Path, report_path: Path) -> str:
    """Embed a figure with a factual caption, linked relative to the report."""
    relative = os.path.relpath(Path(figure_path).resolve(), Path(report_path).resolve().parent)
    return f"![{caption}]({relative})\n\n*{caption}*\n"


def owner_section(title: str, questions: Sequence[str], level: int = 3) -> str:
    """An EMPTY section for the owner, with prompting questions.

    The questions are prompts, not a template to be filled in by a tool. This
    function never emits an answer.

    Level 3 by default: these sit under the report's "## 7. The owner writes
    from here down", so they are subsections of it.
    """
    heading = "#" * level
    lines = [f"{heading} {title}", "", OWNER_MARKER, ""]
    lines += [f"> {q}" for q in questions]
    lines += ["", UNFILLED_PLACEHOLDER, ""]
    return "\n".join(lines)


def gaps_table(analysis: MotorAnalysis) -> str:
    """Everything that could not be computed, and the message that said why."""
    if not analysis.problems:
        return "_nothing failed to compute for this motor._\n"
    return markdown_table(
        ["what could not be computed", "reason reported by the analysis"],
        [[p.split(" — ", 1)[0], p.split(" — ", 1)[-1]] for p in analysis.problems],
    )


# --------------------------------------------------------------------------
# The report
# --------------------------------------------------------------------------


def build_motor_report(
    analysis: MotorAnalysis,
    *,
    others: Sequence[MotorAnalysis] = (),
    figures: Mapping[str, Path] | None = None,
    report_path: Path | str = Path("report.md"),
    title: str | None = None,
) -> str:
    """Assemble the markdown for one motor. Tables, figures, provenance, gaps.

    Every interpretive section is left empty with prompting questions. That is
    the deliverable: raw material the owner can write from, not a draft he has
    to argue with.
    """
    figures = dict(figures or {})
    report_path = Path(report_path)
    parts: list[str] = []

    heading = title or f"Motor {analysis.motor_id} — characterisation data"
    parts.append(f"# {heading}\n")
    parts.append(
        f"Generated by `tools/analysis/report.py` from "
        f"{len(analysis.runs)} run(s). Numbers and figures only: "
        f"every heading marked `{OWNER_MARKER}` is the owner's to write.\n"
    )
    if analysis.is_synthetic:
        parts.append(
            "> ⚠️ **SYNTHETIC DATA.** At least one manifest carries "
            "`synthetic: true`. These numbers came from "
            "`tools/analysis/synthetic.py`, not from hardware, and are not "
            "evidence about any motor.\n"
        )

    # -- 1. provenance -----------------------------------------------------
    parts.append("## 1. Provenance\n")
    parts.append(provenance_table(analysis.runs))
    advisory = load_mod.compare_manifests(analysis.runs)[1]
    if advisory:
        parts.append(
            "\nAdvisory differences between these runs (not fatal, recorded here "
            "so a surprising number has somewhere to be checked):\n\n"
        )
        parts.append(
            markdown_table(
                ["manifest key", "values"],
                [[d.key, "; ".join(f"`{k}` = `{v}`" for k, v in d.values.items())]
                 for d in advisory],
            )
        )

    warnings_block = loader_warnings_block(analysis.runs)
    if warnings_block:
        parts.append(warnings_block)

    # -- 2. measured numbers ----------------------------------------------
    parts.append("\n## 2. Measured numbers\n")
    parts.append(scale_uncertainty_note(analysis))
    for direction in ("forward", "reverse"):
        model: DutyOmegaModel | None = getattr(analysis, direction)
        parts.append(f"\n### 2.{1 if direction == 'forward' else 2} {direction.title()}\n")
        if model is None:
            parts.append(f"_no {direction} model was computed; see §6._\n")
            continue
        repeats = getattr(analysis, f"deadband_{direction}_repeats")
        rows = measurement_rows(
            [
                ("deadband_duty_frac (bracketed)", model.deadband),
                ("deadband_duty_frac (fitted x-intercept)", _safe_x_at_zero(model)),
                ("gain (rad/s per duty_frac)", model.gain),
                ("intercept (rad/s at duty 0)", model.intercept),
                ("saturated omega_rad_s", model.omega_max),
            ]
        )
        parts.append(markdown_table(
            ["quantity", "value", "uncertainty", "unit", "method"], rows))
        parts.append(
            f"\nLinear-region fit: n = {model.fit.n} points over duty "
            f"{model.fit.x_min:.3f}–{model.fit.x_max:.3f}, "
            f"R² = {model.fit.r_squared:.5f}, "
            f"residual RMS = {model.fit.residual_rms:.4g} rad/s. "
            + (
                f"Saturation onset at duty_frac {model.saturation_onset_duty_frac:.2f}.\n"
                if model.saturation_onset_duty_frac is not None
                else "No saturation reached inside the duty range tested.\n"
            )
        )
        if repeats is not None:
            parts.append(
                f"\nDeadband across {repeats.n} repeats: mean {repeats.mean:.4f}, "
                f"s.d. {repeats.stddev:.4f}, range {repeats.minimum:.4f}–"
                f"{repeats.maximum:.4f} duty_frac (peak-to-peak {repeats.spread:.4f}).\n"
            )

    # -- 3. loop timing ----------------------------------------------------
    parts.append("\n## 3. Loop timing (logic analyser D6 / D7)\n")
    if analysis.jitter is None and analysis.cpu_duty is None:
        parts.append(
            "_no analyser capture in these runs, so loop period and CPU duty "
            "were not measured._\n"
        )
    else:
        rows: list[list[str]] = []
        if analysis.jitter is not None:
            j = analysis.jitter
            rows += [
                ["loop period, mean", f"{j.mean_s * 1e3:.4f}", "ms",
                 f"n = {j.n} iterations, LOOP_TICK toggle edges"],
                ["loop period, s.d. (jitter)", f"{j.stddev_s * 1e6:.1f}", "µs", "sample s.d., ddof=1"],
                ["loop period, min", f"{j.min_s * 1e3:.4f}", "ms", ""],
                ["loop period, max", f"{j.max_s * 1e3:.4f}", "ms", ""],
                ["loop period, p99", f"{j.p99_s * 1e3:.4f}", "ms", "99th percentile"],
            ]
            if j.target_s is not None:
                rows.append(
                    ["target period", f"{j.target_s * 1e3:.4f}", "ms",
                     f"worst departure {(j.worst_deviation_s or 0.0) * 1e6:.0f} µs"]
                )
        if analysis.cpu_duty is not None:
            rows.append(
                ["cpu_duty_frac", f"{analysis.cpu_duty.value:.4f}",
                 f"± {analysis.cpu_duty.uncertainty:.3g}", analysis.cpu_duty.method]
            )
        parts.append(markdown_table(["quantity", "value", "unit", "note"], rows))

    # -- 4. figures --------------------------------------------------------
    parts.append("\n## 4. Figures\n")
    if not figures:
        parts.append("_no figures were generated for this report._\n")
    for caption, figure_path in figures.items():
        parts.append(figure_block(caption, Path(figure_path), report_path) + "\n")

    # -- 5. comparison -----------------------------------------------------
    parts.append("\n## 5. Comparison with the other motors measured so far\n")
    parts.append(comparison_table(analysis, others, direction="forward"))

    # -- 6. gaps -----------------------------------------------------------
    parts.append("\n## 6. What could not be computed\n")
    parts.append(gaps_table(analysis))

    generated = "\n".join(parts)
    assert_no_generated_prose(generated)

    # -- 7-12. the owner's sections ---------------------------------------
    owner_parts = [
        "\n---\n",
        "## 7. The owner writes from here down\n",
        (
            f"Every section below is empty on purpose. Grep for `{OWNER_MARKER}` "
            f"to find what is still unwritten.\n"
        ),
        owner_section(
            "7.1 What question was this run supposed to answer?",
            [
                "State the question in one sentence, before looking at the answer.",
                "What result would have told you the question was wrong?",
                "What did you expect the numbers above to be, and why?",
            ],
        ),
        owner_section(
            "7.2 What do the numbers in §2 say, in your words?",
            [
                "Quote the specific numbers you are relying on, with their error bars.",
                "Is any difference you are claiming bigger than its uncertainty?",
                "Which number here would you least like an interviewer to probe, and why?",
            ],
        ),
        owner_section(
            "7.3 How does this motor compare with the others?",
            [
                "Name the largest difference in §5 and say whether it survives the error bars.",
                "What would have to be true for that difference to be a measurement artefact?",
                "Does the spread justify closed-loop control? Argue it with the numbers.",
            ],
        ),
        owner_section(
            "7.4 What does this mean for the control loop?",
            [
                "What does the deadband imply for a PID integrator at low setpoints?",
                "What feedforward would the gain in §2 give you, and in what units?",
                "Where does the saturation point put a ceiling on the achievable speed?",
            ],
        ),
        owner_section(
            "7.5 What broke, and how did you find it?",
            [
                "What was the first symptom, and what was the actual cause?",
                "Which measurement falsified the wrong hypothesis?",
                "What would you check first if it happened again?",
            ],
        ),
        owner_section(
            "7.6 What would you do differently?",
            [
                "What would you change about the experiment, not the motor?",
                "Which uncertainty above dominates, and what would shrink it?",
                "What did this run cost in time, and was it worth it?",
            ],
        ),
    ]
    return generated + "\n" + "\n".join(owner_parts)


def _safe_x_at_zero(model: DutyOmegaModel) -> Measurement | None:
    try:
        return model.fit.x_at_zero()
    except MetricError:
        return None


def write_motor_report(
    analysis: MotorAnalysis,
    path: Path | str,
    *,
    others: Sequence[MotorAnalysis] = (),
    figures: Mapping[str, Path] | None = None,
) -> Path:
    """Write one motor's report to ``path``."""
    path = Path(path)
    text = build_motor_report(
        analysis, others=others, figures=figures, report_path=path
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


# --------------------------------------------------------------------------
# Whole-campaign pipeline
# --------------------------------------------------------------------------


def generate_campaign_reports(
    campaign_root: Path | str,
    *,
    out_dir: Path | str,
    plots_dir: Path | str | None = None,
    require_comparable: bool = True,
) -> dict[str, Path]:
    """Load, analyse, plot and write reports for a whole motor-char campaign.

    Returns ``{name: path}`` for every file written. This is the one function
    a session runs after a bench evening; everything else is a piece of it.
    """
    campaign_root = Path(campaign_root)
    out_dir = Path(out_dir)
    plots_dir = plots_mod.PLOTS_DIR if plots_dir is None else Path(plots_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    plots_dir.mkdir(parents=True, exist_ok=True)

    runs = load_mod.load_campaign(campaign_root, require_comparable=require_comparable)
    analyses = analyse_campaign(runs)
    written: dict[str, Path] = {}

    curves = [
        analysis.curve("forward")
        for analysis in analyses.values()
        if analysis.curve("forward") is not None
    ]
    overlay_path: Path | None = None
    if len(curves) >= 2:
        overlay_path = plots_mod.plot_four_motor_overlay(
            [c for c in curves if c is not None],
            path=plots_dir / "motor-char-four-motor-overlay.png",
        )
        written["four_motor_overlay"] = overlay_path

    for motor_id, analysis in analyses.items():
        figures: dict[str, Path] = {}
        forward = analysis.curve("forward")
        reverse = analysis.curve("reverse")
        if forward is not None:
            per_motor = plots_mod.plot_duty_omega(
                forward,
                reverse,
                forward_model=analysis.forward,
                reverse_model=analysis.reverse,
                path=plots_dir / f"motor-char-m{motor_id}-duty-omega.png",
            )
            written[f"duty_omega_m{motor_id}"] = per_motor
            figures[
                f"Figure — motor {motor_id}: commanded duty_frac against measured "
                f"omega_rad_s, both directions, deadband marked"
            ] = per_motor
        if analysis.jitter is not None:
            jitter_path = plots_mod.plot_loop_jitter(
                analysis.jitter,
                path=plots_dir / f"loop-jitter-m{motor_id}.png",
            )
            written[f"loop_jitter_m{motor_id}"] = jitter_path
            # The target period comes from the run's own loop_hz. Hardcoding
            # "10 ms" here would state a falsehood the day a run is taken at
            # any other loop rate, and a caption is evidence like anything else.
            target = analysis.jitter.target_s
            target_text = (
                f"{target * 1e3:.4g} ms target marked" if target is not None
                else "no target period stated in the manifest"
            )
            figures[
                f"Figure — control-loop period measured on D6 LOOP_TICK, "
                f"n = {analysis.jitter.n} iterations, {target_text}"
            ] = jitter_path
        if overlay_path is not None:
            figures[
                f"Figure — duty_frac against omega_rad_s for all "
                f"{len(curves)} motors measured, forward direction"
            ] = overlay_path

        report_path = out_dir / f"motor-{motor_id}-characterisation.md"
        written[f"report_m{motor_id}"] = write_motor_report(
            analysis,
            report_path,
            others=[a for k, a in analyses.items() if k != motor_id],
            figures=figures,
        )

    return written


def _main(argv: Sequence[str] | None = None) -> int:  # pragma: no cover - CLI glue
    import argparse

    parser = argparse.ArgumentParser(
        description="Assemble per-motor characterisation reports (numbers only, no prose)."
    )
    parser.add_argument("campaign_root", type=Path,
                        help="directory holding the run directories, e.g. experiments/motor-char")
    parser.add_argument("--out", type=Path, required=True, help="where to write the markdown")
    parser.add_argument("--plots", type=Path, default=None, help="where to write figures")
    parser.add_argument(
        "--allow-incomparable", action="store_true",
        help="analyse runs whose manifests differ on a critical parameter (say so in the report)",
    )
    args = parser.parse_args(argv)

    written = generate_campaign_reports(
        args.campaign_root,
        out_dir=args.out,
        plots_dir=args.plots,
        require_comparable=not args.allow_incomparable,
    )
    for name, path in written.items():
        print(f"{name}: {path}")
    print(f"\nUnwritten owner sections: grep -c '{OWNER_MARKER}' {args.out}/*.md")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
