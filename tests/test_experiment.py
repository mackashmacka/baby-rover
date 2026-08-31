"""Experiment orchestration and the pure analysis behind it.

Two halves:

* **The maths** - `describe`, `steady_state`, `step_metrics`, `loop_periods_s`,
  `linear_fit`. These are pure functions of a list of numbers, they are where
  every headline figure in the report comes from, and they need no hardware.
* **The orchestration** - `plan` says what a run would do without doing it, and
  `run` leaves the record behind whether or not the run worked. CLOSE ritual
  item 7: a result not persisted did not happen. A *failed* run is data too -
  it is what a debugging narrative is made of - so losing its manifest loses
  the only record of what was tried.
"""

from __future__ import annotations

import math

import pytest

from conftest import import_or_none

experiment = import_or_none("rover_bench.experiment")
fakes = import_or_none("rover_bench.fakes")
manifest_mod = import_or_none("rover_bench.manifest")

pytestmark = pytest.mark.xfail(
    experiment is None,
    reason="rover_bench.experiment has not landed yet; these activate when it does",
    strict=False,
)


# --------------------------------------------------------------------------
# Units: the constant that must not be invented
# --------------------------------------------------------------------------


def test_ticks_per_rev_starts_unmeasured():
    assert experiment.TicksPerRev.unmeasured().measured is False


def test_an_unmeasured_constant_converts_to_none_not_to_zero():
    """None makes "there is no number" impossible to confuse with "the number
    is zero". A zero rad/s column would be plotted."""
    assert experiment.TicksPerRev.unmeasured().omega_rad_s(1000.0) is None


def test_a_measured_constant_converts():
    """1000.0 is a flat invented number, not a candidate value. Nothing in this
    suite should carry a plausible-looking ticks_per_rev: the real one is
    disputed and unmeasured (docs/HARDWARE.md §2.1, Story 1.4), and a test
    fixture is one grep away from being read as the project's answer."""
    tpr = experiment.TicksPerRev.from_measurement(1000.0, "measured: story 1.4")
    assert tpr.omega_rad_s(1000.0) == pytest.approx(2 * math.pi)


def test_a_measurement_must_say_where_it_came_from():
    """There is deliberately no way to construct this from a datasheet figure
    without saying so out loud."""
    tpr = experiment.TicksPerRev.from_measurement(1000.0, "measured: story 1.4")
    assert "measured" in tpr.source


def test_a_non_positive_constant_is_refused():
    with pytest.raises(ValueError):
        experiment.TicksPerRev.from_measurement(0.0, "nonsense")


def test_direction_is_a_sign_not_a_code_path():
    assert experiment.direction_sign("forward") == 1
    assert experiment.direction_sign("reverse") == -1


def test_an_unknown_direction_is_refused():
    with pytest.raises(ValueError):
        experiment.direction_sign("sideways")


# --------------------------------------------------------------------------
# describe
# --------------------------------------------------------------------------


def test_describe_reports_the_mean():
    assert experiment.describe([1.0, 2.0, 3.0])["mean"] == pytest.approx(2.0)


def test_describe_puts_the_unit_in_every_key():
    """CLAUDE.md: units are stated in the identifier. A summary key called
    `mean` is a number whose meaning has to be remembered."""
    keys = experiment.describe([1.0, 2.0], unit="ticks_per_s")
    assert "mean_ticks_per_s" in keys and "stdev_ticks_per_s" in keys


def test_describe_reports_the_sample_count_beside_the_spread():
    """stdev is 0.0 for n=1, which would otherwise read as "perfectly
    repeatable" rather than "measured once"."""
    summary = experiment.describe([5.0])
    assert summary["n_count"] == 1
    assert summary["stdev"] == 0.0


def test_describe_of_nothing_is_none_not_zero():
    summary = experiment.describe([])
    assert summary["mean"] is None and summary["n_count"] == 0


# --------------------------------------------------------------------------
# steady_state
# --------------------------------------------------------------------------


def test_steady_state_ignores_the_transient():
    """Averaging the whole dwell drags every steady-state speed down by however
    long the motor took to spin up, which shows up as a duty->speed curve that
    bends the wrong way at low duty."""
    settling = [0.0, 2.0, 4.0, 6.0, 8.0, 10.0, 10.0, 10.0, 10.0, 10.0]
    assert experiment.steady_state(settling) == pytest.approx(10.0)


def test_steady_state_of_a_flat_series_is_that_value():
    assert experiment.steady_state([7.0] * 10) == pytest.approx(7.0)


def test_steady_state_of_one_sample_is_that_sample():
    assert experiment.steady_state([7.0]) == pytest.approx(7.0)


def test_steady_state_of_nothing_raises():
    with pytest.raises(ValueError):
        experiment.steady_state([])


def test_an_impossible_discard_fraction_is_refused():
    with pytest.raises(ValueError):
        experiment.steady_state([1.0, 2.0], discard_frac=1.0)


# --------------------------------------------------------------------------
# step_metrics
# --------------------------------------------------------------------------


def first_order_step(target=10.0, tau=0.2, dt=0.01, duration=2.0):
    n = int(duration / dt)
    times = [i * dt for i in range(n)]
    values = [target * (1 - math.exp(-t / tau)) for t in times]
    return times, values


def test_a_first_order_step_has_the_textbook_rise_time():
    """10% to 90% of a first-order response is 2.197 tau. If this number moves,
    either the definition or the arithmetic changed, and both matter because
    the report quotes it."""
    times, values = first_order_step(tau=0.2)
    metrics = experiment.step_metrics(times, values, target=10.0)
    assert metrics.rise_time_s == pytest.approx(2.197 * 0.2, rel=0.05)


def test_a_first_order_step_does_not_overshoot():
    times, values = first_order_step()
    # The tail average sits a hair below the asymptote, so "peak above final"
    # is a few parts in 10^4 rather than exactly zero. Anything above ~1% would
    # be a real overshoot.
    assert experiment.step_metrics(times, values, target=10.0).overshoot_frac < 0.01


def test_an_overshooting_response_is_reported_as_overshoot():
    times = [i * 0.01 for i in range(100)]
    values = [12.0 if 20 <= i <= 30 else 10.0 for i in range(100)]
    assert experiment.step_metrics(times, values, target=10.0).overshoot_frac == \
        pytest.approx(0.2, rel=1e-6)


def test_the_steady_state_error_is_signed():
    """The sign says whether the loop is under- or over-driving, which is the
    difference between raising ki and lowering it."""
    times, values = first_order_step(target=9.0)
    assert experiment.step_metrics(times, values, target=10.0).steady_state_error > 0


def test_the_final_value_is_a_tail_average_not_the_last_sample():
    """One noisy sample must not define the answer."""
    times = [i * 0.01 for i in range(100)]
    values = [10.0] * 99 + [50.0]
    assert experiment.step_metrics(times, values, target=10.0).final_value < 20.0


def test_mismatched_series_lengths_are_refused():
    with pytest.raises(ValueError):
        experiment.step_metrics([0.0, 1.0], [1.0], target=1.0)


def test_a_single_sample_cannot_be_a_step_response():
    with pytest.raises(ValueError):
        experiment.step_metrics([0.0], [1.0], target=1.0)


# --------------------------------------------------------------------------
# Loop timing from the instrumentation channels
# --------------------------------------------------------------------------


def test_loop_periods_use_every_edge_because_the_pin_toggles():
    """LOOP_TICK toggles once per iteration, so every edge - rising or falling -
    is one iteration boundary. Using only rising edges doubles every period and
    halves the reported loop rate."""
    edges = [i * 0.01 for i in range(11)]     # 100 Hz of toggles
    periods = experiment.loop_periods_s(edges)
    assert len(periods) == 10
    assert all(p == pytest.approx(0.01) for p in periods)


def test_a_single_edge_yields_no_period():
    assert experiment.loop_periods_s([0.01]) == []


def test_no_edges_yield_no_periods():
    """A dead loop produces no edges, and inventing a period for it would hide
    exactly the failure the channel exists to catch."""
    assert experiment.loop_periods_s([]) == []


def test_a_jitter_histogram_separates_two_loops_with_the_same_mean():
    """A loop that is 10 ms on average with a 40 ms tail and one that is 10 ms
    every time have the same mean and completely different control behaviour."""
    steady = [0.010] * 100
    tailed = [0.0075] * 99 + [0.2575]
    assert sum(steady) == pytest.approx(sum(tailed), rel=1e-6)
    assert experiment.histogram(steady, bins=5)["counts"] != \
        experiment.histogram(tailed, bins=5)["counts"]


def test_a_histogram_of_identical_values_does_not_divide_by_zero():
    hist = experiment.histogram([0.01] * 10)
    assert sum(hist["counts"]) == 10


def test_a_histogram_of_nothing_is_empty():
    assert experiment.histogram([])["counts"] == []


def test_zero_bins_are_refused():
    with pytest.raises(ValueError):
        experiment.histogram([1.0, 2.0], bins=0)


# --------------------------------------------------------------------------
# linear_fit
# --------------------------------------------------------------------------


def test_a_perfect_line_fits_perfectly():
    fit = experiment.linear_fit([0, 1, 2, 3], [1, 3, 5, 7])
    assert fit["slope"] == pytest.approx(2.0)
    assert fit["intercept"] == pytest.approx(1.0)
    assert fit["r2"] == pytest.approx(1.0)


def test_the_x_intercept_is_the_predicted_deadband():
    """The duty at which the fitted line predicts zero speed. When it disagrees
    with the measured deadband, one of the two experiments is wrong - and that
    disagreement is worth more than either number on its own."""
    fit = experiment.linear_fit([0.2, 0.4, 0.6], [0.0, 10.0, 20.0])
    assert fit["x_intercept"] == pytest.approx(0.2)


def test_a_vertical_fit_returns_none_rather_than_dividing_by_zero():
    assert experiment.linear_fit([1.0, 1.0], [0.0, 5.0])["slope"] is None


def test_a_single_point_cannot_be_fitted():
    assert experiment.linear_fit([1.0], [2.0])["slope"] is None


def test_mismatched_fit_inputs_are_refused():
    with pytest.raises(ValueError):
        experiment.linear_fit([1.0, 2.0], [1.0])


# --------------------------------------------------------------------------
# Planning is inert
# --------------------------------------------------------------------------


def a_spec(**kw):
    kwargs = dict(experiment_id="motor-char", motor=0, kind="hold",
                  params={"duty_frac": 0.5, "duration_s": 0.5})
    kwargs.update(kw)
    return experiment.ExperimentSpec(**kwargs)


def test_the_plan_is_human_readable_steps():
    steps = experiment.plan(a_spec())
    assert steps and all(isinstance(s, str) for s in steps)


def test_the_plan_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    experiment.plan(a_spec())
    assert list(tmp_path.iterdir()) == []


def test_the_plan_says_the_interlocks_run():
    """A check a human cannot see is one nobody trusts and everybody bypasses."""
    text = " ".join(experiment.plan(a_spec())).lower()
    assert "pre-flight" in text or "interlock" in text or "safety" in text


def test_the_plan_says_the_record_gets_written():
    text = " ".join(experiment.plan(a_spec())).lower()
    assert "manifest" in text and "registry" in text


def test_an_unknown_experiment_kind_is_refused():
    with pytest.raises(ValueError):
        experiment.plan(a_spec(kind="teleport"))


def test_an_unknown_parameter_is_refused_rather_than_ignored():
    """A silently dropped `dwell_s` is a run that did something other than what
    the manifest says it did - and the manifest is the whole point."""
    with pytest.raises(ValueError):
        experiment.plan(a_spec(params={"dwel_s": 1.0}))


# --------------------------------------------------------------------------
# Running, against the fake bench
# --------------------------------------------------------------------------


@pytest.fixture
def bench():
    if fakes is None:
        pytest.skip("rover_bench.fakes has not landed yet")
    return fakes.build_fake_bench()


def a_run(spec, tmp_path, bench, **kw):
    kwargs = dict(link=bench.link, root=tmp_path, clock=bench.clock,
                  analyser=bench.analyser, git=manifest_mod.GitState("a" * 40, False),
                  argv=["rover-bench", "run", "--motor", "0"])
    kwargs.update(kw)
    return experiment.run(spec, **kwargs)


def test_a_dry_run_sends_nothing_to_the_pico(tmp_path, bench):
    experiment.run(a_spec(), link=bench.link, root=tmp_path, dry_run=True,
                   clock=bench.clock)
    assert bench.pico.lines_written == []


def test_a_dry_run_writes_nothing(tmp_path, bench):
    """A dry run that leaves files behind is not a dry run."""
    experiment.run(a_spec(), link=bench.link, root=tmp_path, dry_run=True,
                   clock=bench.clock)
    assert list(tmp_path.rglob("*.json")) == []
    assert list(tmp_path.rglob("*.csv")) == []


def test_a_dry_run_still_builds_a_manifest_to_show_you(tmp_path, bench):
    result = experiment.run(a_spec(), link=bench.link, root=tmp_path, dry_run=True,
                            clock=bench.clock)
    assert result.manifest["dry_run"] is True


def test_a_dry_run_is_marked_as_such_so_it_is_never_compared_with_real_data(tmp_path,
                                                                           bench):
    dry = experiment.run(a_spec(), link=bench.link, root=tmp_path, dry_run=True,
                         clock=bench.clock).manifest
    wet = a_run(a_spec(), tmp_path, bench).manifest
    assert manifest_mod.is_reproducible(dry, wet) is False


def test_a_run_writes_a_manifest(tmp_path, bench):
    assert a_run(a_spec(), tmp_path, bench).manifest_path.is_file()


def test_the_written_manifest_validates(tmp_path, bench):
    result = a_run(a_spec(), tmp_path, bench)
    manifest_mod.validate_manifest(manifest_mod.read_manifest(result.manifest_path))


def test_the_manifest_records_the_parameters(tmp_path, bench):
    result = a_run(a_spec(), tmp_path, bench)
    written = manifest_mod.read_manifest(result.manifest_path)
    assert written["params"]["duty_frac"] == 0.5


def test_the_manifest_records_the_motor(tmp_path, bench):
    result = a_run(a_spec(motor=2), tmp_path, bench)
    assert manifest_mod.read_manifest(result.manifest_path)["motor"] == 2


def test_the_manifest_lands_on_the_deterministic_path(tmp_path, bench):
    storage = import_or_none("rover_bench.storage")
    result = a_run(a_spec(), tmp_path, bench)
    assert result.manifest_path == storage.manifest_path(tmp_path, "motor-char", 0)


def test_a_run_writes_its_data(tmp_path, bench):
    assert a_run(a_spec(), tmp_path, bench).data_path.is_file()


def test_the_data_columns_state_their_units(tmp_path, bench):
    storage = import_or_none("rover_bench.storage")
    result = a_run(a_spec(), tmp_path, bench)
    columns, _ = storage.read_csv(result.data_path)
    storage.validate_columns(columns)


def test_speeds_are_in_ticks_per_second_while_the_constant_is_unmeasured(tmp_path,
                                                                         bench):
    """rad/s is only meaningful once ticks_per_rev is measured. A rad/s column
    computed from a placeholder would be quoted in the report."""
    storage = import_or_none("rover_bench.storage")
    result = a_run(a_spec(), tmp_path, bench)
    columns, _ = storage.read_csv(result.data_path)
    assert any(c.endswith("_ticks_per_s") for c in columns)
    assert not any(c.endswith("_rad_s") for c in columns)


def test_the_manifest_says_the_units_are_unconverted(tmp_path, bench):
    written = manifest_mod.read_manifest(a_run(a_spec(), tmp_path, bench).manifest_path)
    assert written["ticks_per_rev"] is None
    assert any("rad/s" in note for note in written["notes"])


def test_a_run_stops_the_motor_on_the_way_out(tmp_path, bench):
    a_run(a_spec(), tmp_path, bench)
    assert "STBY 0" in bench.pico.lines_written


def test_two_runs_of_one_spec_produce_comparable_manifests(tmp_path, bench):
    first = dict(a_run(a_spec(), tmp_path, bench).manifest)
    second = a_run(a_spec(), tmp_path, bench).manifest
    assert manifest_mod.is_reproducible(first, second)


def test_two_runs_with_a_changed_parameter_are_flagged(tmp_path, bench):
    """The reason the manifest exists: explaining why two plots disagree."""
    first = dict(a_run(a_spec(), tmp_path, bench).manifest)
    second = a_run(a_spec(params={"duty_frac": 0.9, "duration_s": 0.5}),
                   tmp_path, bench).manifest
    assert not manifest_mod.is_reproducible(first, second)


# --------------------------------------------------------------------------
# Refusals and failures
# --------------------------------------------------------------------------


@pytest.mark.parametrize("motor", [-1, 4, "0", None])
def test_an_invalid_motor_is_refused_before_a_byte_is_sent(tmp_path, bench, motor):
    with pytest.raises(Exception):  # noqa: B017 - the module owns the type
        a_run(a_spec(motor=motor), tmp_path, bench)
    assert bench.pico.lines_written == []


def test_an_out_of_range_duty_is_refused_before_a_byte_is_sent(tmp_path, bench):
    with pytest.raises(Exception):  # noqa: B017
        a_run(a_spec(params={"duty_frac": 45.0, "duration_s": 0.5}), tmp_path, bench)
    assert bench.pico.lines_written == []


def test_a_run_with_no_link_records_the_failure_rather_than_vanishing(tmp_path):
    """A failed run is data: it is what a debugging narrative is made of."""
    result = experiment.run(a_spec(), link=None, root=tmp_path,
                            git=manifest_mod.GitState("a" * 40, False))
    assert result.ok is False
    assert result.error
    assert result.manifest_path.is_file()


def test_a_failed_run_is_marked_as_failed_in_its_manifest(tmp_path):
    experiment.run(a_spec(), link=None, root=tmp_path,
                   git=manifest_mod.GitState("a" * 40, False))
    storage = import_or_none("rover_bench.storage")
    written = storage.read_json(storage.manifest_path(tmp_path, "motor-char", 0))
    assert "error" in str(written).lower() or written.get("summary")
