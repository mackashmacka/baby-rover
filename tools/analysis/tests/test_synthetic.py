"""The fake data has to be realistic enough to be worth developing against.

Two things matter here. First, that what it writes actually satisfies the
``load`` contract -- a fixture that would be rejected by the real loader is
worse than useless. Second, that it is stamped ``synthetic: true`` everywhere,
so no invented number can ever be mistaken for a measurement of real hardware.
"""

from __future__ import annotations

import json

import numpy as np
import pytest
from analysis import load, metrics, synthetic

# --------------------------------------------------------------------------
# The motor model
# --------------------------------------------------------------------------


def test_the_motor_does_not_turn_inside_its_deadband():
    model = synthetic.MotorModel(motor_id=1, deadband_forward=0.2, deadband_reverse=0.2)
    assert model.steady_state_omega_rad_s(0.19) == 0.0
    assert model.steady_state_omega_rad_s(0.0) == 0.0
    assert model.steady_state_omega_rad_s(-0.19) == 0.0
    assert model.steady_state_omega_rad_s(0.5) > 0.0


def test_speed_saturates_at_the_supply_limit():
    model = synthetic.MotorModel(motor_id=1, omega_max_rad_s=20.0, gain_rad_s_per_duty=100.0)
    assert model.steady_state_omega_rad_s(1.0) == pytest.approx(20.0)
    assert model.steady_state_omega_rad_s(-1.0) == pytest.approx(-20.0)


def test_reverse_is_allowed_to_be_asymmetric():
    model = synthetic.MotorModel(motor_id=1, reverse_gain_factor=0.5, omega_max_rad_s=1e6)
    assert abs(model.steady_state_omega_rad_s(-0.5)) < model.steady_state_omega_rad_s(0.5)


def test_four_motors_are_measurably_different_which_is_the_whole_point():
    models = [synthetic.motor_model(i) for i in (1, 2, 3, 4)]
    gains = [m.gain_rad_s_per_duty for m in models]
    deadbands = [m.deadband_forward for m in models]
    assert max(gains) - min(gains) > 0.05 * np.mean(gains)
    assert max(deadbands) - min(deadbands) > 0.01


def test_a_motor_model_is_reproducible():
    assert synthetic.motor_model(2) == synthetic.motor_model(2)
    assert synthetic.motor_model(2) != synthetic.motor_model(3)


def test_saturation_is_reachable_inside_the_duty_range():
    """A fixture whose curve never saturates would leave the saturation branch
    of fit_duty_to_omega untested."""
    for motor_id in (1, 2, 3, 4):
        model = synthetic.motor_model(motor_id)
        onset = model.deadband_forward + model.omega_max_rad_s / model.gain_rad_s_per_duty
        assert onset < 1.0


def test_degrade_model_changes_only_the_gain():
    model = synthetic.motor_model(1)
    degraded = synthetic.degrade_model(model, gain_scale=0.5)
    assert degraded.gain_rad_s_per_duty == pytest.approx(model.gain_rad_s_per_duty * 0.5)
    assert degraded.deadband_forward == model.deadband_forward


# --------------------------------------------------------------------------
# Timebase
# --------------------------------------------------------------------------


def test_the_timebase_is_strictly_increasing_and_near_the_target_rate():
    t = synthetic.jittered_timebase(500, loop_hz=100.0)
    assert (np.diff(t) > 0).all()
    assert np.diff(t).mean() == pytest.approx(0.01, rel=0.02)


def test_the_timebase_has_jitter_and_the_occasional_late_iteration():
    t = synthetic.jittered_timebase(2000, loop_hz=100.0, rng=np.random.default_rng(1))
    periods = np.diff(t)
    assert periods.std() > 0
    assert periods.max() > 0.01 + 500e-6  # at least one visibly late loop


def test_a_zero_length_timebase_is_still_an_array():
    assert synthetic.jittered_timebase(1).tolist() == [0.0]


# --------------------------------------------------------------------------
# Telemetry
# --------------------------------------------------------------------------


def test_staircase_telemetry_satisfies_the_load_contract(tmp_path):
    run_dir = synthetic.write_staircase_run(tmp_path / "run", 1, hold_s=0.4)
    frame = load.load_telemetry(run_dir / "telemetry.csv")
    assert set(load.TELEMETRY_REQUIRED_COLUMNS) <= set(frame.columns)


def test_encoder_counts_are_integers_and_quantised():
    telemetry = synthetic.simulate_staircase(
        synthetic.motor_model(1), duty_levels=[0.0, 0.3], hold_s=0.3
    )
    counts = telemetry["counts"].to_numpy()
    assert counts.dtype.kind in "iu"
    assert (counts == np.round(counts)).all()


def test_the_firmwares_own_omega_estimate_matches_a_host_side_derivative():
    telemetry = synthetic.simulate_staircase(
        synthetic.motor_model(1), duty_levels=[0.0, 0.6], hold_s=0.5
    )
    host = metrics.omega_from_counts(
        telemetry["t_s"], telemetry["counts"], synthetic.DEFAULT_TICKS_PER_OUTPUT_REV
    )
    assert host[2:] == pytest.approx(telemetry["omega_rad_s"].to_numpy()[2:], abs=1e-6)


def test_the_step_run_closes_the_loop_on_quantised_feedback():
    model = synthetic.motor_model(1)
    telemetry = synthetic.simulate_step(model, setpoint_rad_s=12.0)
    assert "setpoint_rad_s" in telemetry.columns
    assert telemetry["duty_frac"].abs().max() <= 1.0
    tail = telemetry["omega_rad_s"].to_numpy()[-30:]
    assert tail.mean() == pytest.approx(12.0, rel=0.15)


def test_anti_windup_keeps_duty_inside_its_range():
    model = synthetic.motor_model(1)
    telemetry = synthetic.simulate_step(model, setpoint_rad_s=1e3, ki=50.0)
    assert telemetry["duty_frac"].max() <= 1.0
    assert telemetry["duty_frac"].min() >= -1.0


# --------------------------------------------------------------------------
# Analyser capture
# --------------------------------------------------------------------------


def test_the_capture_has_the_eight_bench_channels():
    frame = synthetic.simulate_analyser(duration_s=0.05, samplerate_hz=50_000.0)
    assert list(frame.columns) == ["t_s"] + [f"D{i}" for i in range(8)]
    assert set(np.unique(frame["D6"])) <= {0, 1}


def test_loop_tick_toggles_at_the_loop_rate():
    frame = synthetic.simulate_analyser(
        duration_s=0.5, samplerate_hz=100_000.0, loop_hz=100.0
    )
    periods = metrics.loop_periods_s(frame["t_s"], frame["D6"], mode="toggle")
    assert periods.mean() == pytest.approx(0.01, rel=0.05)


def test_compute_busy_sits_around_the_requested_cpu_duty():
    frame = synthetic.simulate_analyser(
        duration_s=0.5, samplerate_hz=100_000.0, cpu_duty_frac=0.30
    )
    result = metrics.cpu_duty_frac(frame["t_s"], frame["D7"], loop_tick_level=frame["D6"])
    assert result.value == pytest.approx(0.30, abs=0.05)


def test_the_direction_pins_follow_the_tb6612_truth_table():
    forward = synthetic.simulate_analyser(duration_s=0.01, samplerate_hz=20_000.0, direction=1)
    reverse = synthetic.simulate_analyser(duration_s=0.01, samplerate_hz=20_000.0, direction=-1)
    assert (forward["D1"] == 1).all() and (forward["D2"] == 0).all()
    assert (reverse["D1"] == 0).all() and (reverse["D2"] == 1).all()
    assert (forward["D3"] == 1).all()  # STBY HIGH = enabled


def test_the_quadrature_pair_is_ninety_degrees_apart():
    frame = synthetic.simulate_analyser(
        duration_s=0.01, samplerate_hz=200_000.0, encoder_hz=1000.0
    )
    a_edges = metrics.edge_times(frame["t_s"], frame["D4"], edge="rising")
    b_edges = metrics.edge_times(frame["t_s"], frame["D5"], edge="rising")
    period = 1.0 / 1000.0
    lag = (b_edges[0] - a_edges[0]) % period  # B lags A by a quarter cycle
    assert lag == pytest.approx(0.25 * period, abs=2e-5)


def test_the_pwm_duty_is_what_was_asked_for():
    frame = synthetic.simulate_analyser(
        duration_s=0.01, samplerate_hz=2_000_000.0, pwm_hz=20_000.0, pwm_duty_frac=0.35
    )
    assert frame["D0"].mean() == pytest.approx(0.35, abs=0.01)


# --------------------------------------------------------------------------
# Manifests and campaigns
# --------------------------------------------------------------------------


def test_every_synthetic_manifest_says_it_is_synthetic(tmp_path):
    written = synthetic.write_campaign(tmp_path, motors=(1, 2), hold_s=0.3)
    for paths in written.values():
        for path in paths:
            manifest = json.loads((path / "manifest.json").read_text())
            assert manifest["synthetic"] is True
            assert "not a measurement" in manifest["notes"]


def test_a_written_campaign_loads_and_is_comparable(tmp_path):
    synthetic.write_campaign(tmp_path, motors=(1, 2, 3), hold_s=0.3, with_analyser=False)
    runs = load.load_campaign(tmp_path / "motor-char")
    assert [r.motor_id for r in runs] == [1, 2, 3]


def test_manifest_kwargs_reach_the_params_block(tmp_path):
    run_dir = synthetic.write_staircase_run(
        tmp_path / "run", 1, hold_s=0.3, supply_voltage_v=5.0
    )
    assert load.load_run(run_dir).params["supply_voltage_v"] == 5.0


def test_a_manifest_without_an_analyser_omits_the_analyser_block():
    manifest = synthetic.make_manifest(
        run_id="x", motor_id=1, analyser_samplerate_hz=None
    )
    assert "analyser" not in manifest
    assert "analyser" not in manifest["files"]


def test_extra_manifest_fields_are_merged():
    manifest = synthetic.make_manifest(
        run_id="x", motor_id=1, extra={"tuning": "after"}
    )
    assert manifest["tuning"] == "after"


def test_the_step_pair_records_its_gains(tmp_path):
    before = synthetic.write_step_run(tmp_path / "b", 1, tuning="before")
    after = synthetic.write_step_run(tmp_path / "a", 1, tuning="after")
    gains_before = json.loads((before / "manifest.json").read_text())["pid"]
    gains_after = json.loads((after / "manifest.json").read_text())["pid"]
    assert gains_after["ki"] > gains_before["ki"]
