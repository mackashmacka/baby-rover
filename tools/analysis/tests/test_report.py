"""The report assembles raw material and writes NO prose.

The prose boundary is the most important thing tested in this file. It comes
from CLAUDE.md ("The owner writes the report prose. This is a hard rule.") and
docs/career-track.md. A tool that quietly starts drafting his conclusions
destroys the thing the record exists for, and it would do it gradually.
"""

from __future__ import annotations

import pytest
from analysis import load, metrics, plots, report, synthetic


@pytest.fixture(scope="module")
def analyses(motor_char_root):
    return report.analyse_campaign(load.load_campaign(motor_char_root))


# --------------------------------------------------------------------------
# The prose boundary
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "forbidden",
    [
        "The results show that motor 2 is faster.",
        "This demonstrates the need for closed-loop control.",
        "We can conclude the deadband is small.",
        "As expected, the gains differ.",
        "In conclusion, motor 4 is the outlier.",
        "The data suggests a linear region.",
        "This is because the H-bridge drops 0.5 V.",
    ],
)
def test_interpretive_prose_is_caught(forbidden):
    with pytest.raises(report.ReportError, match="interpretive prose"):
        report.assert_no_generated_prose(forbidden)


@pytest.mark.parametrize(
    "allowed",
    [
        "Figure 2 — duty_frac against omega_rad_s, motors 1–4, forward direction.",
        "gain = 33.8 ± 0.02 rad/s per duty_frac, n = 14 points, R² = 0.99998",
        "Gain spread across 4 motors: 33.3 → 37.7 rad/s per duty_frac.",
    ],
)
def test_factual_captions_and_tables_are_allowed(allowed):
    report.assert_no_generated_prose(allowed)


def test_a_real_generated_report_contains_no_prose(analyses, tmp_path):
    """The tripwire run against actual output, not a hand-written string."""
    text = report.build_motor_report(
        analyses[1], others=list(analyses.values()), report_path=tmp_path / "r.md"
    )
    generated = text.split("## 7. The owner writes from here down")[0]
    report.assert_no_generated_prose(generated)


def test_every_interpretive_section_is_left_empty_for_the_owner(analyses, tmp_path):
    text = report.build_motor_report(analyses[1], report_path=tmp_path / "r.md")
    on_its_own_line = [ln for ln in text.splitlines() if ln.strip() == report.OWNER_MARKER]
    assert len(on_its_own_line) == 6
    assert text.count(report.UNFILLED_PLACEHOLDER) == 6


def test_the_owner_sections_ask_questions_rather_than_answering_them(analyses, tmp_path):
    text = report.build_motor_report(analyses[1], report_path=tmp_path / "r.md")
    owner_half = text.split("## 7. The owner writes from here down")[1]
    prompts = [line for line in owner_half.splitlines() if line.startswith("> ")]
    assert len(prompts) >= 15
    assert all(line.rstrip().endswith(("?", ".")) for line in prompts)


def test_owner_section_never_emits_an_answer():
    section = report.owner_section("A title", ["Why?", "How?"])
    assert report.OWNER_MARKER in section
    assert report.UNFILLED_PLACEHOLDER in section
    assert "> Why?" in section


# --------------------------------------------------------------------------
# Content
# --------------------------------------------------------------------------


def test_the_report_states_its_provenance(analyses, tmp_path):
    text = report.build_motor_report(analyses[1], report_path=tmp_path / "r.md")
    assert "## 1. Provenance" in text
    assert "m1-staircase-01" in text
    assert "ticks/rev" in text


def test_the_report_quotes_every_number_with_an_uncertainty(analyses, tmp_path):
    text = report.build_motor_report(analyses[1], report_path=tmp_path / "r.md")
    numbers = text.split("## 2. Measured numbers")[1].split("## 3.")[0]
    for row in ("deadband_duty_frac (bracketed)", "gain (rad/s per duty_frac)"):
        line = next(line for line in numbers.splitlines() if line.startswith(f"| {row}"))
        assert "±" in line


def test_the_report_compares_this_motor_with_the_others(analyses, tmp_path):
    text = report.build_motor_report(
        analyses[1],
        others=[a for k, a in analyses.items() if k != 1],
        report_path=tmp_path / "r.md",
    )
    comparison = text.split("## 5. Comparison")[1]
    for motor_id in (1, 2, 3, 4):
        assert f"motor {motor_id}" in comparison
    assert "Gain spread across 4 motors" in comparison


def test_synthetic_data_is_stamped_so_it_cannot_become_a_finding(analyses, tmp_path):
    text = report.build_motor_report(analyses[1], report_path=tmp_path / "r.md")
    assert "SYNTHETIC DATA" in text


def test_figures_are_linked_relative_to_the_report(analyses, tmp_path):
    figure = plots.plot_four_motor_overlay(
        [a.curve("forward") for a in analyses.values()],
        path=tmp_path / "plots" / "overlay.png",
    )
    text = report.build_motor_report(
        analyses[1],
        figures={"Figure — four motors": figure},
        report_path=tmp_path / "reports" / "r.md",
    )
    assert "](../plots/overlay.png)" in text


def test_a_report_with_no_figures_says_so(analyses, tmp_path):
    text = report.build_motor_report(analyses[1], report_path=tmp_path / "r.md")
    assert "no figures were generated" in text


def test_markdown_table_renders_and_handles_emptiness():
    table = report.markdown_table(["a", "b"], [[1, 2], [3, None]])
    assert table.splitlines()[0] == "| a | b |"
    assert table.splitlines()[3] == "| 3 |  |"
    assert "no rows" in report.markdown_table(["a"], [])


def test_format_measurement_handles_absence():
    assert report.format_measurement(None) == "—"


# --------------------------------------------------------------------------
# Analysis orchestration
# --------------------------------------------------------------------------


def test_analyse_motor_produces_both_directions(analyses):
    analysis = analyses[1]
    assert analysis.forward is not None and analysis.reverse is not None
    assert analysis.jitter is not None
    assert analysis.cpu_duty is not None
    assert analysis.problems == []


def test_analyse_motor_needs_a_run():
    with pytest.raises(report.ReportError, match="at least one run"):
        report.analyse_motor([])


def test_a_missing_analyser_becomes_a_stated_gap_not_a_crash(tmp_path):
    run_dir = synthetic.write_staircase_run(
        tmp_path / "no-analyser", 1, hold_s=0.5, with_analyser=False
    )
    analysis = report.analyse_motor([load.load_run(run_dir)])
    assert analysis.jitter is None
    text = report.build_motor_report(analysis, report_path=tmp_path / "r.md")
    assert "no analyser capture in these runs" in text


def test_unanalysable_data_is_listed_in_the_gaps_section(tmp_path):
    """A run whose holds are far too short to settle: the report must say what
    failed, not silently omit a section."""
    model = synthetic.motor_model(1)
    telemetry = synthetic.simulate_staircase(model, duty_levels=[0.0, 0.5], hold_s=0.02)
    manifest = synthetic.make_manifest(
        run_id="tiny", motor_id=1, analyser_samplerate_hz=None
    )
    run_dir = synthetic.write_run(tmp_path / "tiny", manifest, telemetry)
    analysis = report.analyse_motor([load.load_run(run_dir)])
    assert analysis.problems
    text = report.build_motor_report(analysis, report_path=tmp_path / "r.md")
    assert "## 6. What could not be computed" in text
    assert "duty staircase" in text


def test_deadband_repeats_are_summarised_when_a_run_is_repeated(tmp_path):
    runs = [
        load.load_run(
            synthetic.write_staircase_run(
                tmp_path / f"rep{i}", 1, hold_s=0.5, repeat=i
            )
        )
        for i in (1, 2, 3)
    ]
    analysis = report.analyse_motor(runs)
    assert analysis.deadband_forward_repeats is not None
    assert analysis.deadband_forward_repeats.n == 3
    text = report.build_motor_report(analysis, report_path=tmp_path / "r.md")
    assert "Deadband across 3 repeats" in text


def test_advisory_manifest_differences_reach_the_provenance_section(tmp_path):
    import json

    runs = []
    for i in (1, 2):
        run_dir = synthetic.write_staircase_run(
            tmp_path / f"fw{i}", 1, hold_s=0.5, repeat=i
        )
        manifest = json.loads((run_dir / "manifest.json").read_text())
        manifest["firmware_version"] = f"synthetic-{i}"
        (run_dir / "manifest.json").write_text(json.dumps(manifest))
        runs.append(load.load_run(run_dir))
    text = report.build_motor_report(
        report.analyse_motor(runs), report_path=tmp_path / "r.md"
    )
    assert "Advisory differences" in text
    assert "firmware_version" in text


# --------------------------------------------------------------------------
# Step runs
# --------------------------------------------------------------------------


def test_step_runs_reduce_to_a_trace_and_metrics(pid_step_root):
    for run in load.load_campaign(pid_step_root, require_comparable=False):
        trace, result, problem = report.analyse_step_run(run)
        assert problem is None
        assert result is not None
        assert trace.label in ("before", "after")
        assert result.rise_time_s.value > 0


def test_tuning_makes_the_step_measurably_better(pid_step_root):
    """Not an interpretation in the report -- an assertion about the FIXTURE,
    so the before/after figure has something to show."""
    results = {}
    for run in load.load_campaign(pid_step_root, require_comparable=False):
        _, result, _ = report.analyse_step_run(run)
        results[run.manifest["tuning"]] = result
    assert results["after"].rise_time_s.value < results["before"].rise_time_s.value
    assert abs(results["after"].steady_state_error.value) < abs(
        results["before"].steady_state_error.value
    )


def test_a_step_run_without_a_setpoint_column_is_refused(one_run_dir):
    with pytest.raises(report.ReportError, match="setpoint_rad_s"):
        report.analyse_step_run(load.load_run(one_run_dir))


# --------------------------------------------------------------------------
# The whole pipeline
# --------------------------------------------------------------------------


def test_the_campaign_pipeline_writes_reports_and_figures(motor_char_root, tmp_path):
    written = report.generate_campaign_reports(
        motor_char_root, out_dir=tmp_path / "reports", plots_dir=tmp_path / "plots"
    )
    assert "four_motor_overlay" in written
    for motor_id in (1, 2, 3, 4):
        assert written[f"report_m{motor_id}"].exists()
        assert written[f"duty_omega_m{motor_id}"].exists()
        assert written[f"loop_jitter_m{motor_id}"].exists()
    text = written["report_m1"].read_text()
    assert "![" in text and report.OWNER_MARKER in text


def test_the_pipeline_refuses_an_incomparable_campaign(tmp_path):
    root = tmp_path / "mixed"
    synthetic.write_staircase_run(root / "a", 1, hold_s=0.4)
    synthetic.write_staircase_run(root / "b", 2, hold_s=0.4, supply_voltage_v=5.0)
    with pytest.raises(load.IncomparableRunsError):
        report.generate_campaign_reports(
            root, out_dir=tmp_path / "out", plots_dir=tmp_path / "plots"
        )


def test_the_refusal_can_be_overridden_when_the_owner_says_so(tmp_path):
    root = tmp_path / "mixed"
    synthetic.write_staircase_run(root / "a", 1, hold_s=0.4)
    synthetic.write_staircase_run(root / "b", 2, hold_s=0.4, supply_voltage_v=5.0)
    written = report.generate_campaign_reports(
        root, out_dir=tmp_path / "out", plots_dir=tmp_path / "plots",
        require_comparable=False,
    )
    assert written["report_m1"].exists()


def test_write_motor_report_creates_missing_directories(analyses, tmp_path):
    path = report.write_motor_report(
        analyses[1], tmp_path / "deep" / "nested" / "r.md"
    )
    assert path.exists()


# --------------------------------------------------------------------------
# Regressions found in adversarial review
# --------------------------------------------------------------------------


def test_the_report_states_the_ticks_per_rev_scale_uncertainty(analyses, tmp_path):
    """REGRESSION. Every rad/s figure is inversely proportional to
    ticks_per_output_rev, which nobody has measured (11 vs 14 counts per motor
    revolution). The report used to quote only the fit's statistical error bar
    -- two orders of magnitude smaller than the scale term -- so a gain read
    '33.8 ± 0.01' when the honest absolute figure was '33.8 ± 3.6'.
    """
    analysis = analyses[1]
    text = report.build_motor_report(analysis, report_path=tmp_path / "r.md")
    section = text.split("## 2. Measured numbers")[1].split("## 3.")[0]
    assert "Scale uncertainty" in section
    assert "ticks_per_output_rev" in section

    run = analysis.runs[0]
    scale = metrics.scale_uncertainty_from_ticks(
        analysis.forward.gain.value,
        run.ticks_per_output_rev,
        run.ticks_per_output_rev_uncertainty,
    )
    # The point of stating it: it dwarfs the error bar in the table.
    assert scale > 10 * analysis.forward.gain.uncertainty
    assert f"{scale:.3g}" in section


def test_the_scale_uncertainty_note_survives_the_prose_tripwire(analyses, tmp_path):
    report.assert_no_generated_prose(report.scale_uncertainty_note(analyses[1]))


def test_loader_warnings_reach_the_report(tmp_path):
    """REGRESSION. `ticks_per_rev` at the top level does not say which shaft,
    and reading a motor-shaft figure as an output-shaft one is a silent factor
    of 100. The loader warned about it on the Run object, where nobody reading
    the report would ever see it."""
    import json

    run_dir = synthetic.write_staircase_run(tmp_path / "run", 1, hold_s=0.5)
    manifest = json.loads((run_dir / "manifest.json").read_text())
    del manifest["params"]["ticks_per_output_rev"]
    manifest["ticks_per_rev"] = 1400.0
    (run_dir / "manifest.json").write_text(json.dumps(manifest))

    analysis = report.analyse_motor([load.load_run(run_dir)])
    text = report.build_motor_report(analysis, report_path=tmp_path / "r.md")
    assert "WHICH SHAFT" in text
    assert "100x" in text


def test_a_report_with_nothing_to_warn_about_omits_the_warnings_table(analyses, tmp_path):
    text = report.build_motor_report(analyses[1], report_path=tmp_path / "r.md")
    assert "warning from the loader" not in text


def test_the_jitter_caption_states_the_runs_own_target_period(tmp_path):
    """REGRESSION. The caption hardcoded '10 ms target marked', so a run taken
    at any other loop rate got a figure captioned with a falsehood."""
    root = tmp_path / "campaign"
    synthetic.write_staircase_run(
        root / "m1", 1, hold_s=0.6, with_analyser=True, analyser_duration_s=0.4,
        loop_hz=250.0,
    )
    written = report.generate_campaign_reports(
        root, out_dir=tmp_path / "out", plots_dir=tmp_path / "plots"
    )
    text = written["report_m1"].read_text()
    assert "4 ms target marked" in text
    assert "10 ms target" not in text
