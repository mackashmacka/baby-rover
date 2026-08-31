"""Figures: they render, they are readable, and the palette is provably safe.

A plot test cannot assert "this looks good". What it CAN assert is the part of
"looks good" that is computable: the colours are distinguishable to a reader
with colour vision deficiency, no series relies on colour alone, and every
figure actually writes a file instead of raising halfway through.
"""

from __future__ import annotations

import numpy as np
import pytest
from analysis import load, metrics, plots, report

# --------------------------------------------------------------------------
# Palette -- computed, not eyeballed
# --------------------------------------------------------------------------


def test_the_palette_clears_the_colour_vision_floors():
    """Run the numbers rather than trusting the choice of hexes.

    Floors: normal-vision OKLab dE >= 15 (below that, full-colour readers cannot
    tell a pair apart) and CVD dE >= 8 for protanopia, deuteranopia and
    tritanopia. All pairs, because a four-motor overlay compares every motor
    with every other, not just legend neighbours.
    """
    worst = plots.check_palette()
    assert worst["normal"] >= 15.0, worst
    for mode in ("protan", "deutan", "tritan"):
        assert worst[mode] >= 8.0, (mode, worst)


def test_the_palette_is_capped_at_four_series():
    """Slot 5 onward cannot clear the floors, so the cap is enforced in code
    rather than left as a comment nobody reads."""
    assert len(plots.SERIES_COLOURS) == plots.MAX_SERIES == 4
    with pytest.raises(plots.PlotError, match="outside 0"):
        plots.series_style(4)


def test_no_series_relies_on_colour_alone():
    markers = {plots.series_style(i)["marker"] for i in range(plots.MAX_SERIES)}
    linestyles = {str(plots.series_style(i)["linestyle"]) for i in range(plots.MAX_SERIES)}
    assert len(markers) == plots.MAX_SERIES
    assert len(linestyles) == plots.MAX_SERIES


def test_a_bad_hex_is_rejected():
    with pytest.raises(plots.PlotError, match="hex"):
        plots.check_palette(["#fff", "#000000"])


# --------------------------------------------------------------------------
# Figures render
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def analyses(motor_char_root):
    runs = load.load_campaign(motor_char_root)
    return report.analyse_campaign(runs)


def _assert_is_a_real_png(path):
    assert path.exists()
    assert path.stat().st_size > 5_000
    assert path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_the_four_motor_overlay_renders(analyses, tmp_path):
    curves = [a.curve("forward") for a in analyses.values()]
    path = plots.plot_four_motor_overlay(curves, path=tmp_path / "overlay.png")
    _assert_is_a_real_png(path)


def test_the_overlay_refuses_a_fifth_motor(analyses, tmp_path):
    curves = [a.curve("forward") for a in analyses.values()]
    with pytest.raises(plots.PlotError, match="Facet"):
        plots.plot_four_motor_overlay(curves + curves[:1], path=tmp_path / "x.png")


def test_the_overlay_refuses_an_empty_set(tmp_path):
    with pytest.raises(plots.PlotError, match="no motor curves"):
        plots.plot_four_motor_overlay([], path=tmp_path / "x.png")


def test_the_overlay_survives_a_reference_duty_outside_the_data(analyses, tmp_path):
    curves = [a.curve("forward") for a in analyses.values()]
    path = plots.plot_four_motor_overlay(
        curves, path=tmp_path / "overlay2.png", reference_duty_frac=9.0
    )
    _assert_is_a_real_png(path)


def test_the_per_motor_curve_renders_both_directions(analyses, tmp_path):
    analysis = analyses[1]
    path = plots.plot_duty_omega(
        analysis.curve("forward"),
        analysis.curve("reverse"),
        forward_model=analysis.forward,
        reverse_model=analysis.reverse,
        path=tmp_path / "m1.png",
    )
    _assert_is_a_real_png(path)


def test_the_per_motor_curve_renders_with_forward_only(analyses, tmp_path):
    path = plots.plot_duty_omega(
        analyses[1].curve("forward"), None, path=tmp_path / "fwd.png",
        title="forward only",
    )
    _assert_is_a_real_png(path)


def test_the_jitter_histogram_renders(analyses, tmp_path):
    path = plots.plot_loop_jitter(analyses[1].jitter, path=tmp_path / "jitter.png")
    _assert_is_a_real_png(path)


def test_the_jitter_histogram_needs_data(tmp_path):
    jitter = metrics.loop_jitter([0.01, 0.0101])
    jitter = metrics.LoopJitter(
        mean_s=0.01, stddev_s=0.0, min_s=0.01, max_s=0.01, p99_s=0.01, n=1,
        target_s=0.01, sample_period_s=None, periods_s=np.array([0.01]),
    )
    with pytest.raises(plots.PlotError, match="at least 2 periods"):
        plots.plot_loop_jitter(jitter, path=tmp_path / "x.png")


def test_the_step_response_overlay_renders(pid_step_root, tmp_path):
    runs = load.load_campaign(pid_step_root, require_comparable=False)
    traces = [report.analyse_step_run(run)[0] for run in runs]
    path = plots.plot_step_response(traces, path=tmp_path / "step.png")
    _assert_is_a_real_png(path)


def test_the_step_overlay_refuses_nothing_and_too_much(tmp_path):
    with pytest.raises(plots.PlotError, match="no step traces"):
        plots.plot_step_response([], path=tmp_path / "x.png")
    trace = plots.StepTrace(
        label="a", t_s=np.arange(10) * 0.01,
        omega_rad_s=np.ones(10), setpoint_rad_s=1.0,
    )
    with pytest.raises(plots.PlotError, match="at most"):
        plots.plot_step_response([trace] * 5, path=tmp_path / "x.png")


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------


def test_motor_curve_sorts_by_duty_and_labels_itself():
    curve = plots.MotorCurve(
        motor_id=3,
        duty_frac=np.array([0.5, 0.1, 0.3]),
        omega_rad_s=np.array([5.0, 1.0, 3.0]),
        omega_err_rad_s=np.array([0.5, 0.1, 0.3]),
    )
    duty, omega, err = curve.sorted_by_duty()
    assert list(duty) == [0.1, 0.3, 0.5]
    assert list(omega) == [1.0, 3.0, 5.0]
    assert list(err) == [0.1, 0.3, 0.5]
    assert curve.display_label() == "Motor 3"


def test_a_custom_label_wins():
    curve = plots.MotorCurve(
        motor_id=1, duty_frac=np.array([0.1]), omega_rad_s=np.array([1.0]),
        label="left front",
    )
    assert curve.display_label() == "left front"
    assert curve.sorted_by_duty()[2] is None


def test_plot_path_defaults_to_the_experiments_plots_directory():
    assert plots.plot_path("x").parent == plots.PLOTS_DIR
    assert plots.plot_path("a b/c").name == "a-b-c.png"
    assert plots.plot_path("already.png").name == "already.png"


def test_house_style_is_applied_on_import_of_a_figure(tmp_path):
    import matplotlib.pyplot as plt

    plots.apply_house_style()
    assert plt.rcParams["axes.spines.top"] is False
    assert plt.rcParams["savefig.dpi"] == 200


def test_the_backend_is_headless():
    """PulseView is for human eyes; so is an interactive matplotlib window.
    Neither belongs in an automated path."""
    import matplotlib

    assert matplotlib.get_backend().lower() == "agg"
