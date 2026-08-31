"""Schema validation and the comparability refusal.

The comparability tests are the important ones. Comparing motor 1 against
motor 4 across a changed bench parameter is the single most likely way this
campaign produces a wrong conclusion, so the refusal has to be tested as
carefully as the happy path.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest
from analysis import load, synthetic


def _rewrite_manifest(run_dir: Path, **changes) -> Path:
    path = run_dir / "manifest.json"
    manifest = json.loads(path.read_text())
    params = changes.pop("params", None)
    manifest.update(changes)
    if params:
        manifest["params"].update(params)
    path.write_text(json.dumps(manifest))
    return run_dir


# --------------------------------------------------------------------------
# Happy path
# --------------------------------------------------------------------------


def test_a_valid_run_loads(one_run_dir):
    run = load.load_run(one_run_dir)
    assert run.motor_id == 1
    assert len(run.telemetry) > 100
    assert run.analyser is not None
    assert set(load.TELEMETRY_REQUIRED_COLUMNS) <= set(run.telemetry.columns)


def test_run_exposes_bench_parameters(one_run_dir):
    run = load.load_run(one_run_dir)
    assert run.ticks_per_output_rev == synthetic.DEFAULT_TICKS_PER_OUTPUT_REV
    assert run.loop_hz == 100.0
    assert run.analyser_samplerate_hz == 100_000.0
    assert "motor 1" in run.label()


def test_missing_ticks_uncertainty_defaults_to_the_literature_spread(one_run_dir):
    """1100-1400 counts/output rev is unresolved. A manifest that does not
    state an uncertainty must not be read as claiming zero."""
    manifest_path = one_run_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    del manifest["params"]["ticks_per_output_rev_uncertainty"]
    manifest_path.write_text(json.dumps(manifest))
    run = load.load_run(one_run_dir)
    assert run.ticks_per_output_rev_uncertainty == pytest.approx(150.0)


def test_channels_are_renamed_from_the_channel_map(one_run_dir):
    run = load.load_run(one_run_dir)
    assert set(run.channel("loop_tick").unique()) <= {0, 1}
    assert "compute_busy" in run.analyser.columns
    assert "D6" not in run.analyser.columns


def test_asking_for_an_absent_channel_names_the_ones_present(one_run_dir):
    run = load.load_run(one_run_dir)
    with pytest.raises(load.RunSchemaError, match="enc_a"):
        run.channel("does_not_exist")


# --------------------------------------------------------------------------
# Manifest validation
# --------------------------------------------------------------------------


def test_a_run_without_a_manifest_is_refused(tmp_path):
    (tmp_path / "empty").mkdir()
    with pytest.raises(load.RunSchemaError, match="no manifest.json"):
        load.load_run(tmp_path / "empty")


def test_corrupt_json_is_refused(one_run_dir):
    (one_run_dir / "manifest.json").write_text("{not json")
    with pytest.raises(load.RunSchemaError, match="not valid JSON"):
        load.load_run(one_run_dir)


def test_a_json_list_is_not_a_manifest(one_run_dir):
    (one_run_dir / "manifest.json").write_text("[1, 2, 3]")
    with pytest.raises(load.RunSchemaError, match="JSON object"):
        load.load_run(one_run_dir)


def test_missing_required_manifest_key_names_it(one_run_dir):
    manifest = json.loads((one_run_dir / "manifest.json").read_text())
    del manifest["experiment_id"]
    (one_run_dir / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(load.RunSchemaError, match="experiment_id"):
        load.load_run(one_run_dir)


def test_a_newer_schema_version_is_refused_not_guessed_at(one_run_dir):
    _rewrite_manifest(one_run_dir, schema_version=load.SCHEMA_VERSION + 1)
    with pytest.raises(load.RunSchemaError, match="newer than this reader"):
        load.load_run(one_run_dir)


def test_a_non_integer_schema_version_is_refused(one_run_dir):
    _rewrite_manifest(one_run_dir, schema_version="1")
    with pytest.raises(load.RunSchemaError, match="must be an integer"):
        load.load_run(one_run_dir)


def test_params_missing_a_required_key_is_refused(one_run_dir):
    manifest = json.loads((one_run_dir / "manifest.json").read_text())
    del manifest["params"]["ticks_per_output_rev"]
    (one_run_dir / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(load.RunSchemaError, match="ticks_per_output_rev"):
        load.load_run(one_run_dir)


def test_recommended_params_only_warn(one_run_dir):
    manifest = json.loads((one_run_dir / "manifest.json").read_text())
    del manifest["params"]["gear_ratio"]
    (one_run_dir / "manifest.json").write_text(json.dumps(manifest))
    run = load.load_run(one_run_dir)
    assert any("gear_ratio" in w for w in run.warnings)


# --------------------------------------------------------------------------
# Alias normalisation -- interop with tools/rover_bench
# --------------------------------------------------------------------------


def test_the_capture_sides_spellings_are_accepted(one_run_dir):
    """rover_bench writes timestamp_utc / motor / git_sha; this reader wants
    utc_started / motor_id / git_commit. One dict beats a flag day."""
    manifest = json.loads((one_run_dir / "manifest.json").read_text())
    manifest["timestamp_utc"] = manifest.pop("utc_started")
    manifest["motor"] = manifest.pop("motor_id")
    manifest["git_sha"] = manifest.pop("git_commit")
    manifest.pop("run_id")
    (one_run_dir / "manifest.json").write_text(json.dumps(manifest))

    run = load.load_run(one_run_dir)
    assert run.motor_id == 1
    assert run.manifest["git_commit"] == "0000000"
    assert run.run_id == one_run_dir.name  # falls back to the directory name


def test_samples_csv_is_found_when_the_manifest_does_not_name_it(one_run_dir):
    manifest = json.loads((one_run_dir / "manifest.json").read_text())
    (one_run_dir / "telemetry.csv").rename(one_run_dir / "samples.csv")
    manifest["files"].pop("telemetry")
    (one_run_dir / "manifest.json").write_text(json.dumps(manifest))
    assert len(load.load_run(one_run_dir).telemetry) > 0


def test_a_run_with_no_csv_at_all_is_refused(one_run_dir):
    manifest = json.loads((one_run_dir / "manifest.json").read_text())
    (one_run_dir / "telemetry.csv").unlink()
    manifest["files"].pop("telemetry")
    (one_run_dir / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(load.RunSchemaError, match="no telemetry CSV"):
        load.load_run(one_run_dir)


# --------------------------------------------------------------------------
# Safety invariants encoded in code
# --------------------------------------------------------------------------


def test_five_volt_encoder_supply_is_refused(one_run_dir):
    _rewrite_manifest(one_run_dir, params={"encoder_supply_v": 5.0})
    with pytest.raises(load.SafetyInvariantError, match="NOT 5 V tolerant"):
        load.load_run(one_run_dir)


def test_a_4s_lipo_straight_into_the_driver_is_refused(one_run_dir):
    _rewrite_manifest(one_run_dir, params={"supply_voltage_v": 16.8})
    with pytest.raises(load.SafetyInvariantError, match="TB6612FNG VM maximum"):
        load.load_run(one_run_dir)


@pytest.mark.parametrize("probe", ["AO1", "ao2", "BO1", "bo2"])
def test_probing_an_h_bridge_output_is_refused(one_run_dir, probe):
    manifest = json.loads((one_run_dir / "manifest.json").read_text())
    manifest["analyser"]["channel_map"]["D0"] = probe
    (one_run_dir / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(load.SafetyInvariantError, match="motor voltage"):
        load.load_run(one_run_dir)


# --------------------------------------------------------------------------
# Telemetry column contract
# --------------------------------------------------------------------------


def test_missing_telemetry_column_is_refused(one_run_dir):
    frame = pd.read_csv(one_run_dir / "telemetry.csv").drop(columns=["counts"])
    frame.to_csv(one_run_dir / "telemetry.csv", index=False)
    with pytest.raises(load.RunSchemaError, match="counts"):
        load.load_run(one_run_dir)


def test_an_empty_csv_is_refused(one_run_dir):
    (one_run_dir / "telemetry.csv").write_text("")
    with pytest.raises(load.RunSchemaError, match="empty"):
        load.load_run(one_run_dir)


def test_a_header_only_csv_is_refused(one_run_dir):
    (one_run_dir / "telemetry.csv").write_text("t_s,duty_frac,counts\n")
    with pytest.raises(load.RunSchemaError, match="no data rows"):
        load.load_run(one_run_dir)


def test_a_gap_in_a_required_column_is_refused(one_run_dir):
    frame = pd.read_csv(one_run_dir / "telemetry.csv")
    frame.loc[5, "counts"] = None
    frame.to_csv(one_run_dir / "telemetry.csv", index=False)
    with pytest.raises(load.RunSchemaError, match="non-numeric"):
        load.load_run(one_run_dir)


def test_time_going_backwards_is_refused(one_run_dir):
    frame = pd.read_csv(one_run_dir / "telemetry.csv")
    frame.loc[10, "t_s"] = -1.0
    frame.to_csv(one_run_dir / "telemetry.csv", index=False)
    with pytest.raises(load.RunSchemaError, match="goes backwards"):
        load.load_run(one_run_dir)


def test_duty_as_a_percentage_is_refused(one_run_dir):
    """A percentage in duty_frac would scale every gain by 100 and nothing
    downstream would notice."""
    frame = pd.read_csv(one_run_dir / "telemetry.csv")
    frame["duty_frac"] = frame["duty_frac"] * 100.0
    frame.to_csv(one_run_dir / "telemetry.csv", index=False)
    with pytest.raises(load.RunSchemaError, match=r"\[-1, 1\]"):
        load.load_run(one_run_dir)


# --------------------------------------------------------------------------
# Analyser CSV
# --------------------------------------------------------------------------


def test_sigrok_comment_lines_are_skipped(tmp_path):
    path = tmp_path / "analyser.csv"
    path.write_text("; acquisition with 8 channels\n;\nD6,D7\n0,0\n1,1\n0,0\n")
    frame = load.load_analyser(path, samplerate_hz=1000.0)
    assert list(frame.columns) == ["t_s", "loop_tick", "compute_busy"]
    assert frame["t_s"].iloc[1] == pytest.approx(0.001)


def test_analyser_without_times_or_a_samplerate_is_refused(tmp_path):
    path = tmp_path / "analyser.csv"
    path.write_text("D6\n0\n1\n")
    with pytest.raises(load.RunSchemaError, match="samplerate"):
        load.load_analyser(path, samplerate_hz=None)


def test_a_supplied_time_column_is_used_as_is(tmp_path):
    path = tmp_path / "analyser.csv"
    path.write_text("t_s,D6\n0.0,0\n0.5,1\n")
    frame = load.load_analyser(path)
    assert frame["t_s"].tolist() == [0.0, 0.5]


def test_non_binary_channel_values_are_refused(tmp_path):
    path = tmp_path / "analyser.csv"
    path.write_text("t_s,D6\n0.0,high\n0.5,low\n")
    with pytest.raises(load.RunSchemaError, match="non-numeric"):
        load.load_analyser(path)


def test_requiring_an_absent_analyser_capture_is_an_error(tmp_path):
    run_dir = synthetic.write_staircase_run(
        tmp_path / "no-analyser", motor_id=1, hold_s=0.3, with_analyser=False
    )
    assert load.load_run(run_dir).analyser is None
    with pytest.raises(load.RunSchemaError, match="no analyser capture"):
        load.load_run(run_dir, require_analyser=True)


# --------------------------------------------------------------------------
# Comparability -- the whole point
# --------------------------------------------------------------------------


def test_a_clean_campaign_is_comparable(motor_char_root):
    runs = load.load_campaign(motor_char_root)
    assert len(runs) == 4
    assert [r.motor_id for r in runs] == [1, 2, 3, 4]


def test_different_motors_are_not_a_reason_to_refuse(motor_char_root):
    """Comparing different motors is the entire point of Story 1.5."""
    runs = load.load_campaign(motor_char_root)
    assert len({r.motor_id for r in runs}) == 4
    assert load.assert_comparable(runs) == []


@pytest.mark.parametrize(
    "param,value",
    [
        ("supply_voltage_v", 5.0),
        ("pwm_hz", 10_000.0),
        ("loop_hz", 50.0),
        ("ticks_per_output_rev", 1100.0),
        ("gear_ratio", 50.0),
        ("encoder_decode", "x4"),
        ("direction_convention", "positive duty_frac = AIN2 HIGH"),
    ],
)
def test_a_changed_bench_parameter_refuses_the_comparison(tmp_path, param, value):
    a = synthetic.write_staircase_run(tmp_path / "a", 1, hold_s=0.3)
    b = synthetic.write_staircase_run(tmp_path / "b", 2, hold_s=0.3)
    manifest = json.loads((b / "manifest.json").read_text())
    manifest["params"][param] = value
    (b / "manifest.json").write_text(json.dumps(manifest))

    with pytest.raises(load.IncomparableRunsError) as excinfo:
        load.load_runs([a, b])
    message = str(excinfo.value)
    assert param in message
    assert "Refusing to compare" in message
    assert str(value) in message  # the actual differing values are shown


def test_the_refusal_can_be_overridden_explicitly(tmp_path):
    a = synthetic.write_staircase_run(tmp_path / "a", 1, hold_s=0.3)
    b = synthetic.write_staircase_run(tmp_path / "b", 2, hold_s=0.3, supply_voltage_v=5.0)
    runs = load.load_runs([a, b], require_comparable=False)
    assert len(runs) == 2


def test_a_supply_voltage_within_meter_noise_is_still_comparable(tmp_path):
    a = synthetic.write_staircase_run(tmp_path / "a", 1, hold_s=0.3)
    b = synthetic.write_staircase_run(
        tmp_path / "b", 2, hold_s=0.3, supply_voltage_v=6.02
    )
    assert len(load.load_runs([a, b])) == 2


def test_a_param_absent_from_every_run_is_not_a_difference(tmp_path):
    paths = []
    for motor_id, name in ((1, "a"), (2, "b")):
        run_dir = synthetic.write_staircase_run(tmp_path / name, motor_id, hold_s=0.3)
        manifest = json.loads((run_dir / "manifest.json").read_text())
        del manifest["params"]["encoder_decode"]
        (run_dir / "manifest.json").write_text(json.dumps(manifest))
        paths.append(run_dir)
    assert len(load.load_runs(paths)) == 2


def test_a_param_present_in_only_one_run_is_a_difference(tmp_path):
    a = synthetic.write_staircase_run(tmp_path / "a", 1, hold_s=0.3)
    b = synthetic.write_staircase_run(tmp_path / "b", 2, hold_s=0.3)
    manifest = json.loads((b / "manifest.json").read_text())
    del manifest["params"]["encoder_decode"]
    (b / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(load.IncomparableRunsError, match="encoder_decode"):
        load.load_runs([a, b])


def test_advisory_differences_are_reported_but_do_not_refuse(tmp_path):
    a = synthetic.write_staircase_run(tmp_path / "a", 1, hold_s=0.3)
    b = synthetic.write_staircase_run(tmp_path / "b", 2, hold_s=0.3)
    manifest = json.loads((b / "manifest.json").read_text())
    manifest["firmware_version"] = "synthetic-1"
    (b / "manifest.json").write_text(json.dumps(manifest))

    runs = load.load_runs([a, b])
    advisory = load.assert_comparable(runs)
    assert [d.key for d in advisory] == ["firmware_version"]
    assert "synthetic-1" in advisory[0].render()


def test_a_single_run_is_trivially_comparable(one_run_dir):
    assert load.compare_manifests([load.load_run(one_run_dir)]) == ([], [])


# --------------------------------------------------------------------------
# Campaign helpers
# --------------------------------------------------------------------------


def test_find_run_dirs_walks_the_tree(motor_char_root):
    assert len(load.find_run_dirs(motor_char_root)) == 4


def test_find_run_dirs_on_a_file_is_an_error(one_run_dir):
    with pytest.raises(load.RunSchemaError, match="not a directory"):
        load.find_run_dirs(one_run_dir / "manifest.json")


def test_group_by_motor(motor_char_root):
    grouped = load.group_by_motor(load.load_campaign(motor_char_root))
    assert sorted(grouped) == [1, 2, 3, 4]
    assert all(len(v) == 1 for v in grouped.values())


def test_manifest_rows_flatten_params_for_the_report(motor_char_root):
    rows = list(load.iter_manifest_rows(load.load_campaign(motor_char_root)))
    assert len(rows) == 4
    assert rows[0]["params.supply_voltage_v"] == 6.0
    assert rows[0]["samples"] > 0


def test_dotted_get_returns_the_default_for_a_missing_path():
    assert load.dotted_get({"a": {"b": 1}}, "a.b") == 1
    assert load.dotted_get({"a": {"b": 1}}, "a.c", "fallback") == "fallback"
    assert load.dotted_get({"a": 1}, "a.b.c", None) is None


# --------------------------------------------------------------------------
# Interop with tools/rover_bench's actual on-disk shapes
# --------------------------------------------------------------------------


def test_top_level_ticks_per_rev_is_pulled_into_params(one_run_dir):
    """rover_bench records ticks/rev at the top level; this reader wants it in
    params. One dict, not a flag day."""
    manifest = json.loads((one_run_dir / "manifest.json").read_text())
    del manifest["params"]["ticks_per_output_rev"]
    manifest["ticks_per_rev"] = 1234.0
    (one_run_dir / "manifest.json").write_text(json.dumps(manifest))

    run = load.load_run(one_run_dir)
    assert run.ticks_per_output_rev == 1234.0


def test_borrowing_ticks_per_rev_warns_about_which_shaft(one_run_dir):
    """'ticks_per_rev' does not say motor shaft or output shaft, and reading one
    as the other is a silent factor of 100 in every speed number."""
    manifest = json.loads((one_run_dir / "manifest.json").read_text())
    del manifest["params"]["ticks_per_output_rev"]
    manifest["ticks_per_rev"] = 1234.0
    (one_run_dir / "manifest.json").write_text(json.dumps(manifest))

    run = load.load_run(one_run_dir)
    assert any("WHICH SHAFT" in w for w in run.warnings)


def test_an_unmeasured_ticks_per_rev_is_not_borrowed(one_run_dir):
    """rover_bench writes `ticks_per_rev: null` when nobody has measured it.
    That has to stay a refusal, not become a guess."""
    manifest = json.loads((one_run_dir / "manifest.json").read_text())
    del manifest["params"]["ticks_per_output_rev"]
    manifest["ticks_per_rev"] = None
    (one_run_dir / "manifest.json").write_text(json.dumps(manifest))

    with pytest.raises(load.RunSchemaError, match="ticks_per_output_rev"):
        load.load_run(one_run_dir)


def test_top_level_sample_rate_is_pulled_into_the_analyser_block(tmp_path):
    run_dir = synthetic.write_staircase_run(
        tmp_path / "run", 1, hold_s=0.3, with_analyser=True, analyser_duration_s=0.2
    )
    manifest = json.loads((run_dir / "manifest.json").read_text())
    del manifest["analyser"]["samplerate_hz"]
    manifest["sample_rate_hz"] = 100_000.0
    (run_dir / "manifest.json").write_text(json.dumps(manifest))
    assert load.load_run(run_dir).analyser_samplerate_hz == 100_000.0


def test_a_unit_suffixed_encoder_column_is_accepted_as_counts(one_run_dir):
    """rover_bench's writer refuses a header with no unit suffix, so it cannot
    emit a column called 'counts'. Its documented suffix is '_ticks'."""
    frame = pd.read_csv(one_run_dir / "telemetry.csv").rename(
        columns={"counts": "encoder_ticks"}
    )
    frame.to_csv(one_run_dir / "telemetry.csv", index=False)
    assert len(load.load_run(one_run_dir).telemetry) > 0


def test_two_candidate_count_columns_is_an_error_not_a_coin_toss(one_run_dir):
    frame = pd.read_csv(one_run_dir / "telemetry.csv").rename(
        columns={"counts": "encoder_ticks"}
    )
    frame["motor_ticks"] = frame["encoder_ticks"]
    frame.to_csv(one_run_dir / "telemetry.csv", index=False)
    with pytest.raises(load.RunSchemaError, match="could be the encoder count"):
        load.load_run(one_run_dir)


def test_a_ticks_per_second_column_is_not_mistaken_for_a_count(one_run_dir):
    frame = pd.read_csv(one_run_dir / "telemetry.csv").rename(
        columns={"counts": "encoder_ticks"}
    )
    frame["speed_ticks_per_s"] = 0.0
    frame.to_csv(one_run_dir / "telemetry.csv", index=False)
    assert "counts" in load.load_run(one_run_dir).telemetry.columns


def test_time_s_is_accepted_as_t_s(tmp_path):
    path = tmp_path / "telemetry.csv"
    path.write_text("time_s,duty_frac,encoder_ticks\n0.0,0.0,0\n0.01,0.5,3\n")
    frame = load.load_telemetry(path)
    assert "t_s" in frame.columns and "counts" in frame.columns


# --------------------------------------------------------------------------
# Regressions found in adversarial review
# --------------------------------------------------------------------------


def test_two_runs_sharing_a_run_id_still_refuse_an_incomparable_bench(tmp_path):
    """REGRESSION. The difference table used to be keyed by run_id alone. Two
    runs can share one -- the capture side falls back to the directory name,
    and `experiments/<experiment>/motor-1/` repeats across experiments -- and a
    dict keyed by a duplicated id collapses both runs into one entry. A
    collapsed entry cannot disagree with itself, so a campaign whose supply
    voltage changed between runs was passing the comparability check.
    """
    a = synthetic.write_staircase_run(tmp_path / "a", 1, hold_s=0.3)
    b = synthetic.write_staircase_run(
        tmp_path / "b", 2, hold_s=0.3, supply_voltage_v=5.0
    )
    for run_dir in (a, b):
        manifest = json.loads((run_dir / "manifest.json").read_text())
        manifest["run_id"] = "motor-1"  # the same id on both
        (run_dir / "manifest.json").write_text(json.dumps(manifest))

    with pytest.raises(load.IncomparableRunsError, match="supply_voltage_v"):
        load.load_runs([a, b])


def test_comparison_labels_disambiguate_duplicate_run_ids(tmp_path):
    a = synthetic.write_staircase_run(tmp_path / "a", 1, hold_s=0.3)
    b = synthetic.write_staircase_run(tmp_path / "b", 2, hold_s=0.3)
    for run_dir in (a, b):
        manifest = json.loads((run_dir / "manifest.json").read_text())
        manifest["run_id"] = "same"
        (run_dir / "manifest.json").write_text(json.dumps(manifest))
    labels = load.comparison_labels([load.load_run(a), load.load_run(b)])
    assert len(set(labels)) == 2
    assert all("same" in label for label in labels)


def test_a_capture_whose_own_header_names_an_h_bridge_output_is_refused(tmp_path):
    """The channel map is not the only witness: sigrok labels channels from
    whatever the operator typed, so a column called AO1 is a record of the
    analyser having been on a motor output."""
    path = tmp_path / "analyser.csv"
    path.write_text("t_s,AO1\n0.0,0\n0.5,1\n")
    with pytest.raises(load.SafetyInvariantError, match="motor voltage"):
        load.load_analyser(path)


def test_an_assumed_analyser_channel_map_is_warned_about(one_run_dir):
    """docs/WIRING.md carries two channel maps that differ at exactly D6/D7
    (§8 has UART TX/RX there, §10.2 has LOOP_TICK/COMPUTE_BUSY). Reading a §8
    capture with the §10.2 default turns UART traffic into a 'loop period'."""
    manifest = json.loads((one_run_dir / "manifest.json").read_text())
    del manifest["analyser"]["channel_map"]
    (one_run_dir / "manifest.json").write_text(json.dumps(manifest))
    run = load.load_run(one_run_dir)
    assert any("no analyser.channel_map" in w for w in run.warnings)


def test_a_run_without_a_motor_id_says_so(one_run_dir):
    """Unlabelled runs all group under motor None, so four of them would be
    fitted as one motor."""
    manifest = json.loads((one_run_dir / "manifest.json").read_text())
    del manifest["motor_id"]
    (one_run_dir / "manifest.json").write_text(json.dumps(manifest))
    run = load.load_run(one_run_dir)
    assert run.motor_id is None
    assert any("no motor_id" in w for w in run.warnings)


def test_a_separate_direction_column_is_refused_not_folded_onto_forward(tmp_path):
    """docs/experiments/motor-char.md §6.1 specifies an UNSIGNED duty plus a
    `direction` column; this reader's contract is a signed duty_frac. Reading
    one as the other makes every reverse point look like a forward one, so the
    reverse deadband and gain would silently disappear."""
    path = tmp_path / "telemetry.csv"
    path.write_text(
        "t_s,duty_frac,direction,counts\n"
        "0.00,0.0,fwd,0\n0.01,0.5,fwd,3\n0.02,0.5,rev,6\n"
    )
    with pytest.raises(load.RunSchemaError, match="folds reverse onto forward"):
        load.load_telemetry(path)


def test_a_signed_duty_is_still_accepted_alongside_a_direction_column(tmp_path):
    """The refusal is about ambiguity, not about the column existing: a file
    that does sign its duty is unambiguous and loads."""
    path = tmp_path / "telemetry.csv"
    path.write_text(
        "t_s,duty_frac,direction,counts\n"
        "0.00,0.5,fwd,0\n0.01,-0.5,rev,3\n0.02,-0.5,rev,6\n"
    )
    assert len(load.load_telemetry(path)) == 3


def test_the_other_specs_duty_column_gets_an_explanatory_refusal(tmp_path):
    path = tmp_path / "telemetry.csv"
    path.write_text("t_s,duty_frac_cmd,counts\n0.0,0.5,0\n0.01,0.5,3\n")
    with pytest.raises(load.RunSchemaError, match="different convention"):
        load.load_telemetry(path)
