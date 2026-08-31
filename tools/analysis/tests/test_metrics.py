"""Metrics: values, uncertainties, and the failure modes worth naming.

Where a metric has a closed-form answer, the test builds data with that answer
and checks the metric recovers it *within its own stated uncertainty*. A
metric whose error bar does not cover the truth is worse than no metric.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest
from analysis import load, metrics, synthetic

TAU = 2.0 * math.pi


# --------------------------------------------------------------------------
# Measurement / Aggregate
# --------------------------------------------------------------------------


def test_a_measurement_renders_with_its_error_bar_and_unit():
    m = metrics.Measurement(1.2345, 0.0123, "rad/s", n=7, method="x")
    assert "±" in m.render()
    assert "rad/s" in str(m)
    assert m.as_dict()["n"] == 7


def test_a_negative_uncertainty_is_a_bug_not_a_measurement():
    with pytest.raises(metrics.MetricError):
        metrics.Measurement(1.0, -0.1)


def test_relative_uncertainty_of_a_zero_value_is_infinite():
    assert metrics.Measurement(0.0, 0.1).relative == math.inf
    assert metrics.Measurement(2.0, 0.1).relative == pytest.approx(0.05)


def test_aggregate_uses_the_sample_standard_deviation():
    stats = metrics.aggregate([1.0, 2.0, 3.0], unit="s")
    assert stats.mean == pytest.approx(2.0)
    assert stats.stddev == pytest.approx(1.0)  # ddof=1, not 0.8165
    assert stats.sem == pytest.approx(1.0 / math.sqrt(3))
    assert stats.spread == pytest.approx(2.0)


def test_a_single_repeat_claims_no_spread():
    stats = metrics.aggregate([4.0])
    assert stats.stddev == 0.0 and stats.sem == 0.0 and stats.n == 1


def test_aggregate_of_nothing_is_an_error():
    with pytest.raises(metrics.MetricError):
        metrics.aggregate([])


def test_as_measurement_can_quote_either_spread_or_standard_error():
    stats = metrics.aggregate([1.0, 2.0, 3.0])
    assert stats.as_measurement("stddev").uncertainty == pytest.approx(1.0)
    assert stats.as_measurement("sem").uncertainty < 1.0


def test_uncertainties_add_in_quadrature():
    assert metrics.combine_uncertainties(3.0, 4.0) == pytest.approx(5.0)


@pytest.mark.parametrize("bad", [[np.nan, 1.0], [np.inf, 1.0]])
def test_non_finite_input_is_rejected(bad):
    with pytest.raises(metrics.MetricError, match="NaN or inf"):
        metrics._finite_array(bad, "x")


# --------------------------------------------------------------------------
# counts -> omega
# --------------------------------------------------------------------------


def test_omega_from_counts_recovers_a_known_constant_speed():
    ticks = 1400.0
    omega_true = 10.0
    t = np.arange(0, 1.0, 0.01)
    counts = omega_true * t * ticks / TAU
    omega = metrics.omega_from_counts(t, counts, ticks)
    assert omega[1:] == pytest.approx(omega_true, rel=1e-9)


def test_omega_needs_a_strictly_increasing_timebase():
    with pytest.raises(metrics.MetricError, match="strictly increasing"):
        metrics.omega_from_counts([0.0, 0.0, 0.1], [0, 1, 2], 1400.0)


def test_omega_rejects_mismatched_lengths():
    with pytest.raises(metrics.MetricError, match="differ in length"):
        metrics.omega_from_counts([0.0, 0.1], [0, 1, 2], 1400.0)


def test_omega_rejects_a_nonsense_ticks_per_rev():
    with pytest.raises(metrics.MetricError):
        metrics.omega_from_counts([0.0, 0.1], [0, 1], 0.0)


def test_smoothing_does_not_drag_the_ends_of_the_record_toward_zero():
    """REGRESSION. np.convolve(mode="same") zero-pads, so the last samples --
    exactly the steady-state tail every step metric is measured against -- got
    pulled toward 0 and the tail scatter got LARGER after smoothing. Found on
    synthetic data while tuning the settling-time test."""
    values = np.full(50, 12.0)
    smoothed = metrics.moving_average(values, 9)
    assert smoothed == pytest.approx(12.0)
    assert len(smoothed) == len(values)


def test_smoothing_actually_reduces_noise():
    rng = np.random.default_rng(0)
    noisy = 5.0 + rng.normal(0, 1.0, size=400)
    assert metrics.moving_average(noisy, 9).std() < noisy.std() / 2


def test_a_smoothing_window_longer_than_the_record_is_an_error():
    with pytest.raises(metrics.MetricError, match="exceeds the record length"):
        metrics.moving_average([1.0, 2.0], 9)


def test_the_speed_quantisation_floor_is_one_count_per_sample():
    # 1400 ticks/output rev, 100 Hz loop -> one count in 10 ms.
    assert metrics.omega_quantisation_rad_s(0.01, 1400.0) == pytest.approx(
        TAU / 14.0, rel=1e-9
    )


def test_ticks_uncertainty_scales_omega_proportionally():
    u = metrics.scale_uncertainty_from_ticks(20.0, 1400.0, 140.0)
    assert u == pytest.approx(2.0)  # 10% in ticks -> 10% in omega


# --------------------------------------------------------------------------
# Staircase reduction
# --------------------------------------------------------------------------


def test_split_holds_finds_each_constant_duty_segment():
    duty = np.array([0.0, 0.0, 0.5, 0.5, 0.5, -0.5])
    assert metrics.split_holds(duty) == [(0, 2), (2, 5), (5, 6)]


def test_duty_staircase_discards_the_settling_part_of_each_hold():
    model = synthetic.motor_model(1)
    telemetry = synthetic.simulate_staircase(
        model, duty_levels=[0.0, 0.5, 0.8], hold_s=1.0
    )
    steps = metrics.duty_staircase_steps(
        telemetry, ticks_per_output_rev=synthetic.DEFAULT_TICKS_PER_OUTPUT_REV
    )
    assert list(steps["duty_frac"]) == [0.0, 0.5, 0.8]
    assert steps["n"].min() >= 4
    truth = model.steady_state_omega_rad_s(0.5)
    measured = float(steps.loc[steps["duty_frac"] == 0.5, "omega_rad_s"].iloc[0])
    assert measured == pytest.approx(truth, rel=0.03)


def test_a_staircase_with_holds_too_short_to_settle_is_an_error():
    telemetry = pd.DataFrame(
        {"t_s": np.arange(6) * 0.01, "duty_frac": [0.1, 0.2, 0.3, 0.4, 0.5, 0.6],
         "counts": np.arange(6)}
    )
    with pytest.raises(metrics.MetricError, match="usable samples"):
        metrics.duty_staircase_steps(telemetry, ticks_per_output_rev=1400.0)


def test_staircase_needs_the_contract_columns():
    with pytest.raises(metrics.MetricError, match="counts"):
        metrics.duty_staircase_steps(
            pd.DataFrame({"t_s": [0.0], "duty_frac": [0.0]}), ticks_per_output_rev=1400.0
        )


def test_settle_frac_must_be_a_fraction():
    telemetry = pd.DataFrame({"t_s": [0.0], "duty_frac": [0.0], "counts": [0]})
    with pytest.raises(metrics.MetricError, match="settle_frac"):
        metrics.duty_staircase_steps(
            telemetry, ticks_per_output_rev=1400.0, settle_frac=1.0
        )


# --------------------------------------------------------------------------
# Deadband
# --------------------------------------------------------------------------


def test_deadband_is_bracketed_between_the_last_still_and_first_moving_duty():
    duty = np.array([0.05, 0.10, 0.15, 0.20])
    omega = np.array([0.0, 0.0, 3.0, 6.0])
    band = metrics.deadband_duty_frac(duty, omega, omega_threshold_rad_s=0.5)
    assert band.value == pytest.approx(0.125)
    assert band.uncertainty == pytest.approx(0.025)
    assert "bracketed" in band.method


def test_deadband_is_found_for_the_reverse_direction_too():
    duty = np.array([-0.05, -0.10, -0.15, -0.20])
    omega = np.array([0.0, 0.0, -3.0, -6.0])
    band = metrics.deadband_duty_frac(
        duty, omega, omega_threshold_rad_s=0.5, direction="reverse"
    )
    assert band.value == pytest.approx(0.125)


def test_an_unbracketed_deadband_says_so_instead_of_pretending():
    duty = np.array([0.10, 0.20, 0.30])
    omega = np.array([5.0, 10.0, 15.0])
    band = metrics.deadband_duty_frac(duty, omega, omega_threshold_rad_s=0.5)
    assert "UNBRACKETED" in band.method
    assert band.value == pytest.approx(0.05)
    assert band.uncertainty == pytest.approx(0.05)  # covers the whole [0, 0.1]


def test_a_motor_that_never_moves_is_an_error_not_a_deadband():
    duty = np.array([0.1, 0.2, 0.3])
    omega = np.zeros(3)
    with pytest.raises(metrics.MetricError, match="never exceeded"):
        metrics.deadband_duty_frac(duty, omega, omega_threshold_rad_s=0.5)


def test_asking_for_a_direction_with_no_data_is_an_error():
    with pytest.raises(metrics.MetricError, match="no reverse"):
        metrics.deadband_duty_frac(
            np.array([0.1, 0.2, 0.3]), np.array([1.0, 2.0, 3.0]),
            omega_threshold_rad_s=0.5, direction="reverse",
        )


def test_a_non_positive_threshold_is_rejected():
    with pytest.raises(metrics.MetricError, match="omega_threshold"):
        metrics.deadband_duty_frac([0.1], [1.0], omega_threshold_rad_s=0.0)


def test_deadband_spread_across_repeats():
    repeats = [
        metrics.Measurement(0.12, 0.01, "duty_frac"),
        metrics.Measurement(0.14, 0.01, "duty_frac"),
        metrics.Measurement(0.13, 0.01, "duty_frac"),
    ]
    spread = metrics.deadband_spread(repeats)
    assert spread.mean == pytest.approx(0.13)
    assert spread.spread == pytest.approx(0.02)


def test_deadband_spread_of_nothing_is_an_error():
    with pytest.raises(metrics.MetricError):
        metrics.deadband_spread([])


# --------------------------------------------------------------------------
# Linear fit
# --------------------------------------------------------------------------


def test_a_noiseless_line_is_recovered_exactly_with_a_tiny_error_bar():
    x = np.linspace(0.2, 0.9, 15)
    y = 30.0 * x - 4.0
    fit = metrics.linear_fit(x, y)
    assert fit.gain == pytest.approx(30.0)
    assert fit.intercept == pytest.approx(-4.0)
    assert fit.gain_stderr == pytest.approx(0.0, abs=1e-9)
    assert fit.r_squared == pytest.approx(1.0)


def test_a_noisy_lines_error_bar_covers_the_truth():
    rng = np.random.default_rng(3)
    x = np.linspace(0.2, 0.9, 25)
    y = 30.0 * x - 4.0 + rng.normal(0, 0.3, size=x.size)
    fit = metrics.linear_fit(x, y)
    assert abs(fit.gain - 30.0) < 3 * fit.gain_stderr
    assert fit.residual_rms == pytest.approx(0.3, rel=0.5)


def test_a_fit_needs_enough_points_to_have_an_error_bar():
    with pytest.raises(metrics.MetricError, match=">= 3 points"):
        metrics.linear_fit([1.0, 2.0], [1.0, 2.0])


def test_a_vertical_scatter_has_no_slope():
    with pytest.raises(metrics.MetricError, match="identical"):
        metrics.linear_fit([1.0, 1.0, 1.0], [1.0, 2.0, 3.0])


def test_the_fitted_x_intercept_is_the_fitted_deadband():
    fit = metrics.linear_fit(np.linspace(0.2, 0.9, 15), 30.0 * np.linspace(0.2, 0.9, 15) - 3.6)
    assert fit.x_at_zero().value == pytest.approx(0.12, abs=1e-6)


def test_a_flat_line_never_crosses_zero():
    fit = metrics.linear_fit([1.0, 2.0, 3.0], [5.0, 5.0, 5.0])
    with pytest.raises(metrics.MetricError, match="never crosses zero"):
        fit.x_at_zero()


def test_predict_and_the_measurement_wrappers():
    fit = metrics.linear_fit([0.0, 1.0, 2.0], [0.0, 2.0, 4.0], gain_unit="rad/s")
    assert fit.predict([3.0]) == pytest.approx([6.0])
    assert fit.gain_measurement().unit == "rad/s"
    assert "intercept" in fit.intercept_measurement().method


# --------------------------------------------------------------------------
# Linear region / saturation
# --------------------------------------------------------------------------


def test_the_linear_region_stops_where_saturation_starts():
    duty = np.round(np.arange(0.05, 1.01, 0.05), 2)
    omega = np.minimum(30.0 * (duty - 0.10), 20.0)
    omega[duty <= 0.10] = 0.0
    mask = metrics.find_linear_region(duty, omega, deadband=0.10)
    assert duty[mask].max() < 0.85  # the plateau above ~0.77 is excluded
    assert duty[mask].min() > 0.10


def test_a_curve_with_no_saturation_keeps_every_point():
    duty = np.round(np.arange(0.2, 1.01, 0.05), 2)
    omega = 30.0 * duty
    assert metrics.find_linear_region(duty, omega, deadband=0.1).all()


def test_too_few_points_to_detect_saturation_keeps_them_all():
    duty = np.array([0.2, 0.4, 0.6])
    assert metrics.find_linear_region(duty, 30.0 * duty, deadband=0.1).sum() == 3


# --------------------------------------------------------------------------
# duty -> omega model, against a motor whose truth we know
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def steps_for_motor_one():
    model = synthetic.motor_model(1)
    telemetry = synthetic.simulate_staircase(model, hold_s=0.8)
    steps = metrics.duty_staircase_steps(
        telemetry, ticks_per_output_rev=synthetic.DEFAULT_TICKS_PER_OUTPUT_REV
    )
    return model, steps


def test_the_fitted_gain_recovers_the_synthetic_motors_gain(steps_for_motor_one):
    model, steps = steps_for_motor_one
    result = metrics.fit_duty_to_omega(steps, direction="forward")
    assert result.gain.value == pytest.approx(model.gain_rad_s_per_duty, rel=0.02)


def test_the_fitted_x_intercept_recovers_the_true_deadband(steps_for_motor_one):
    """The bracketed deadband is biased high by threshold/gain; the fitted
    x-intercept is not, which is why the report quotes both."""
    model, steps = steps_for_motor_one
    result = metrics.fit_duty_to_omega(steps, direction="forward")
    assert result.fit.x_at_zero().value == pytest.approx(
        model.deadband_forward, abs=0.01
    )


def test_the_saturation_plateau_is_found(steps_for_motor_one):
    model, steps = steps_for_motor_one
    result = metrics.fit_duty_to_omega(steps, direction="forward")
    assert result.saturation_onset_duty_frac is not None
    assert result.omega_max is not None
    assert result.omega_max.value == pytest.approx(model.omega_max_rad_s, rel=0.03)


def test_both_directions_report_a_positive_gain(steps_for_motor_one):
    _, steps = steps_for_motor_one
    forward = metrics.fit_duty_to_omega(steps, direction="forward")
    reverse = metrics.fit_duty_to_omega(steps, direction="reverse")
    assert forward.gain.value > 0 and reverse.gain.value > 0
    assert forward.intercept.unit == "rad/s"


def test_too_few_duty_points_to_fit_is_an_error():
    steps = pd.DataFrame({"duty_frac": [0.5, 0.6], "omega_rad_s": [10.0, 12.0]})
    with pytest.raises(metrics.MetricError, match="need >= 3"):
        metrics.fit_duty_to_omega(steps)


def test_the_steps_frame_must_carry_the_expected_columns():
    with pytest.raises(metrics.MetricError, match="omega_rad_s"):
        metrics.fit_duty_to_omega(pd.DataFrame({"duty_frac": [0.1, 0.2, 0.3]}))


# --------------------------------------------------------------------------
# Step response
# --------------------------------------------------------------------------


def _first_order_step(tau=0.1, final=10.0, dt=0.01, duration=2.0):
    t = np.arange(0, duration, dt)
    return t, final * (1.0 - np.exp(-t / tau))


def test_rise_time_of_a_first_order_step_matches_the_closed_form():
    # 10%->90% of a first-order response is ln(9) * tau.
    t, y = _first_order_step(tau=0.1)
    result = metrics.step_response(t, y, setpoint=10.0)
    assert result.rise_time_s.value == pytest.approx(math.log(9) * 0.1, abs=0.01)
    assert result.rise_time_s.uncertainty == pytest.approx(0.01)


def test_a_first_order_step_has_no_overshoot():
    t, y = _first_order_step()
    result = metrics.step_response(t, y, setpoint=10.0)
    assert result.overshoot_frac.value == pytest.approx(0.0, abs=1e-6)


def test_overshoot_is_measured_as_a_fraction_of_the_step_taken():
    t = np.arange(0, 2.0, 0.01)
    y = np.where(t < 0.5, 12.0, 10.0)  # 20% over, then settled
    y[0] = 0.0
    result = metrics.step_response(t, y, setpoint=10.0, tail_frac=0.5)
    assert result.overshoot_frac.value == pytest.approx(0.2, abs=0.02)


def test_steady_state_error_is_setpoint_minus_the_achieved_tail():
    t, y = _first_order_step(final=9.0)
    result = metrics.step_response(t, y, setpoint=10.0)
    assert result.steady_state_error.value == pytest.approx(1.0, abs=0.01)
    assert result.steady_state_value.value == pytest.approx(9.0, abs=0.01)


def test_settling_time_of_a_first_order_step():
    # |y - final| < 5% of final at t = ln(20) * tau ~= 3 tau.
    t, y = _first_order_step(tau=0.1)
    result = metrics.step_response(t, y, setpoint=10.0, settle_band_frac=0.05)
    assert result.settling_time_s.value == pytest.approx(math.log(20) * 0.1, abs=0.02)


def test_a_settling_band_inside_the_noise_floor_is_refused():
    """At 100 Hz with ~1400 ticks/rev the single-sample speed resolution is
    ~0.45 rad/s, so a 2% band on a 15 rad/s setpoint is below the resolution
    floor. Reporting a number there would be reporting the last noise
    excursion, not the transient."""
    rng = np.random.default_rng(1)
    t = np.arange(0, 2.0, 0.01)
    y = 10.0 * (1.0 - np.exp(-t / 0.1)) + rng.normal(0, 0.5, size=t.size)
    with pytest.raises(metrics.MetricError, match="inside the measurement noise"):
        metrics.step_response(t, y, setpoint=10.0, settle_band_frac=0.02)


def test_a_response_still_moving_at_the_end_has_no_settling_time():
    # Smooth (so the noise-floor guard is not what fires) but still climbing
    # when the record ends: tau is as long as the capture.
    t, y = _first_order_step(tau=2.0, duration=2.0)
    with pytest.raises(metrics.MetricError, match="still outside"):
        metrics.step_response(t, y, setpoint=10.0, settle_band_frac=0.05)


def test_a_flat_record_is_not_a_step():
    t = np.arange(0, 1.0, 0.01)
    with pytest.raises(metrics.MetricError, match="no step"):
        metrics.step_response(t, np.full(t.size, 5.0), setpoint=5.0)


def test_a_step_needs_samples():
    with pytest.raises(metrics.MetricError, match=">= 5 samples"):
        metrics.step_response([0.0, 0.1], [0.0, 1.0], setpoint=1.0)


def test_tail_frac_must_be_a_fraction():
    t, y = _first_order_step()
    with pytest.raises(metrics.MetricError, match="tail_frac"):
        metrics.step_response(t, y, setpoint=10.0, tail_frac=0.0)


def test_step_rows_are_named_for_the_report():
    t, y = _first_order_step()
    names = [name for name, _ in metrics.step_response(t, y, setpoint=10.0).as_rows()]
    assert names == [
        "rise_time_s", "overshoot_frac", "settling_time_s",
        "steady_state_error", "steady_state_value",
    ]


def test_find_step_time_locates_the_setpoint_change():
    t = np.arange(0, 1.0, 0.01)
    setpoint = np.where(t >= 0.2, 15.0, 0.0)
    assert metrics.find_step_time(t, setpoint) == pytest.approx(0.2, abs=0.011)


def test_a_setpoint_that_never_changes_has_no_step():
    with pytest.raises(metrics.MetricError, match="never changes"):
        metrics.find_step_time([0.0, 0.1, 0.2], [5.0, 5.0, 5.0])


# --------------------------------------------------------------------------
# Loop timing
# --------------------------------------------------------------------------


def test_edge_times_finds_rising_falling_and_both():
    t = np.arange(6) * 0.1
    level = np.array([0, 1, 1, 0, 1, 0])
    assert metrics.edge_times(t, level, edge="rising") == pytest.approx([0.1, 0.4])
    assert metrics.edge_times(t, level, edge="falling") == pytest.approx([0.3, 0.5])
    assert len(metrics.edge_times(t, level, edge="both")) == 4


def test_edge_times_on_a_single_sample_is_empty():
    assert metrics.edge_times([0.0], [1]) .size == 0


def test_a_toggling_loop_tick_must_be_read_on_both_edges():
    """REGRESSION-GUARD. LOOP_TICK toggles once per iteration. Counting only
    rising edges reports exactly double the period and half the loop rate --
    the easiest factor-of-two in the whole project."""
    t = np.arange(0, 1.0, 1e-4)
    level = ((t // 0.01).astype(int) % 2).astype(np.int8)  # toggles every 10 ms
    toggle = metrics.loop_periods_s(t, level, mode="toggle")
    pulse = metrics.loop_periods_s(t, level, mode="pulse")
    assert toggle.mean() == pytest.approx(0.01, rel=0.02)
    assert pulse.mean() == pytest.approx(0.02, rel=0.02)


def test_a_capture_too_short_to_hold_an_edge_is_an_error():
    with pytest.raises(metrics.MetricError, match="LOOP_TICK edge"):
        metrics.loop_periods_s([0.0, 0.1, 0.2], [0, 0, 0])


def test_jitter_statistics():
    periods = np.array([0.0099, 0.0100, 0.0101, 0.0100, 0.0110])
    jitter = metrics.loop_jitter(periods, target_s=0.01, sample_period_s=1e-5)
    assert jitter.mean_s == pytest.approx(periods.mean())
    assert jitter.min_s == 0.0099 and jitter.max_s == 0.0110
    assert jitter.n == 5
    assert jitter.worst_deviation_s == pytest.approx(0.001)
    assert jitter.mean_measurement().uncertainty > 0
    assert jitter.jitter_measurement().value == pytest.approx(periods.std(ddof=1))


def test_worst_deviation_needs_a_target():
    assert metrics.loop_jitter([0.01, 0.011]).worst_deviation_s is None


def test_jitter_needs_more_than_one_period():
    with pytest.raises(metrics.MetricError, match=">= 2 periods"):
        metrics.loop_jitter([0.01])


def test_the_histogram_counts_every_period():
    counts, edges = metrics.jitter_histogram(np.linspace(0.009, 0.011, 50), bins=10)
    assert counts.sum() == 50
    assert len(edges) == 11


# --------------------------------------------------------------------------
# CPU duty
# --------------------------------------------------------------------------


def test_cpu_duty_over_a_whole_capture():
    t = np.arange(100) * 1e-4
    busy = np.zeros(100, dtype=np.int8)
    busy[:20] = 1
    result = metrics.cpu_duty_frac(t, busy)
    assert result.value == pytest.approx(0.20)
    assert result.uncertainty > 0


def test_cpu_duty_per_iteration_exposes_the_worst_iteration():
    t = np.arange(0, 0.1, 1e-5)
    tick = ((t // 0.01).astype(int) % 2).astype(np.int8)
    busy = ((t % 0.01) < 0.002).astype(np.int8)
    result = metrics.cpu_duty_frac(t, busy, loop_tick_level=tick)
    assert result.value == pytest.approx(0.2, abs=0.02)
    assert "worst iteration" in result.method


def test_cpu_duty_needs_enough_loop_edges():
    t = np.arange(10) * 1e-4
    with pytest.raises(metrics.MetricError, match="LOOP_TICK edge"):
        metrics.cpu_duty_frac(t, np.zeros(10), loop_tick_level=np.zeros(10))


def test_cpu_duty_rejects_mismatched_lengths():
    with pytest.raises(metrics.MetricError, match="differ in length"):
        metrics.cpu_duty_frac([0.0, 0.1], [1, 0, 1])


# --------------------------------------------------------------------------
# ticks per revolution
# --------------------------------------------------------------------------


def test_ticks_per_rev_averages_the_trials():
    result = metrics.ticks_per_rev([1400, 1402, 1398])
    assert result.value == pytest.approx(1400.0)
    assert result.n == 3


def test_the_angular_positioning_error_dominates_a_one_turn_measurement():
    """Two counts of quantisation is nothing next to stopping a hand-turned
    shaft 7 degrees off the mark: 0.02 rev of 1400 counts is 28 counts."""
    result = metrics.ticks_per_rev([1400, 1400, 1400], revolutions=1.0)
    assert result.uncertainty == pytest.approx(
        metrics.combine_uncertainties(0.0, 28.0, 2.0), rel=1e-6
    )


def test_turning_more_revolutions_shrinks_the_uncertainty():
    one = metrics.ticks_per_rev([1400], revolutions=1.0)
    ten = metrics.ticks_per_rev([14000], revolutions=10.0)
    assert ten.value == pytest.approx(one.value)
    assert ten.uncertainty < one.uncertainty / 5


def test_ticks_per_rev_needs_a_trial_and_a_positive_turn():
    with pytest.raises(metrics.MetricError):
        metrics.ticks_per_rev([])
    with pytest.raises(metrics.MetricError):
        metrics.ticks_per_rev([1400], revolutions=0.0)


def test_converting_to_the_motor_shaft_for_comparison_with_the_datasheets():
    """Adafruit says 14 counts/motor rev, retailers say 11. This is how a
    measured output-shaft figure is put beside them."""
    measured = metrics.ticks_per_rev([1400], revolutions=1.0)
    per_motor = metrics.ticks_per_motor_rev(measured, 100.0)
    assert per_motor.value == pytest.approx(14.0)
    assert per_motor.uncertainty == pytest.approx(measured.uncertainty / 100.0)


def test_a_zero_gear_ratio_is_rejected():
    with pytest.raises(metrics.MetricError):
        metrics.ticks_per_motor_rev(metrics.Measurement(1400, 10), 0.0)


def test_metres_per_count_propagates_both_measurements():
    ticks = metrics.Measurement(1400.0, 28.0, "ticks per output revolution")
    result = metrics.counts_to_metres(ticks, 0.065, 0.001)
    assert result.value == pytest.approx(math.pi * 0.065 / 1400.0)
    assert result.relative == pytest.approx(
        metrics.combine_uncertainties(0.001 / 0.065, 28.0 / 1400.0)
    )


@pytest.mark.parametrize("diameter,ticks", [(0.0, 1400.0), (0.065, 0.0)])
def test_metres_per_count_rejects_nonsense(diameter, ticks):
    with pytest.raises(metrics.MetricError):
        metrics.counts_to_metres(metrics.Measurement(ticks, 1.0), diameter)


# --------------------------------------------------------------------------
# End-to-end against a loaded run
# --------------------------------------------------------------------------


def test_metrics_run_against_a_loaded_run_directory(one_run_dir):
    run = load.load_run(one_run_dir)
    steps = metrics.duty_staircase_steps(
        run.telemetry, ticks_per_output_rev=run.ticks_per_output_rev
    )
    model = metrics.fit_duty_to_omega(steps, direction="forward")
    assert model.gain.value > 0
    periods = metrics.loop_periods_s(run.analyser["t_s"], run.channel("loop_tick"))
    jitter = metrics.loop_jitter(periods, target_s=1.0 / run.loop_hz)
    assert jitter.mean_s == pytest.approx(0.01, rel=0.05)
