"""Run manifests: what was run, from which commit, with which arguments, when.

CLAUDE.md, CLOSE ritual item 7: "a result not persisted did not happen." A CSV
with no manifest is a column of numbers whose provenance is a memory, and by
week three the memory is gone.

Two things here are worth more than the rest:

* **`ticks_per_rev` is a top-level field that defaults to None**, and a manifest
  with `ticks_per_rev: null` carries an explicit note saying its speeds are in
  ticks/s and must not be quoted in rad/s. The 11-vs-14 dispute is unresolved
  (docs/HARDWARE.md §2.1); a silent default would scale every downstream number
  by an unknown factor and nothing would ever notice.
* **`is_reproducible`** is what proves motor 0 and motor 3 were characterised
  under the same conditions. Without it, "these four curves are comparable" is
  an assertion nobody can check.
"""

from __future__ import annotations

import json

import pytest

from conftest import import_or_none

manifest = import_or_none("rover_bench.manifest")

pytestmark = pytest.mark.xfail(
    manifest is None,
    reason="rover_bench.manifest has not landed yet; these activate when it does",
    strict=False,
)

SHA = "0123456789abcdef0123456789abcdef01234567"
ARGV = ["rover-bench", "run", "--motor", "0", "--duty", "0.5"]
PARAMS = {"duty_frac": 0.5, "pwm_hz": 20000, "loop_hz": 100}


def a_manifest(clock=None, **overrides):
    kwargs = dict(experiment_id="motor-char", motor=0, argv=list(ARGV),
                  params=dict(PARAMS), git_sha=SHA, git_dirty=False,
                  host="bench-laptop", user="tester",
                  now=clock.utcnow() if clock else None)
    kwargs.update(overrides)
    return manifest.build_manifest(**kwargs)


# --------------------------------------------------------------------------
# Contents
# --------------------------------------------------------------------------


def test_manifest_records_the_git_sha(frozen_clock):
    assert a_manifest(frozen_clock)["git_sha"] == SHA


def test_manifest_records_argv(frozen_clock):
    assert a_manifest(frozen_clock)["argv"] == ARGV


def test_manifest_records_a_timestamp(frozen_clock):
    assert a_manifest(frozen_clock)["timestamp_utc"].startswith("2026-08-31T12:00:00")


def test_the_timestamp_is_explicitly_utc(frozen_clock):
    """CLAUDE.md fixes the timezone at UTC. A naive local timestamp cannot be
    compared against the analyser capture beside it."""
    assert a_manifest(frozen_clock)["timestamp_utc"].endswith("Z")


def test_a_naive_datetime_is_treated_as_utc():
    from datetime import datetime
    assert manifest.utc_now_iso(datetime(2026, 8, 31, 12, 0, 0)).endswith("Z")


def test_an_aware_non_utc_datetime_is_converted():
    """Sydney is UTC+10. 22:00 local on the 31st is 12:00Z on the 31st - the
    conversion has to happen or two runs cannot be ordered."""
    from datetime import datetime, timedelta, timezone
    sydney = timezone(timedelta(hours=10))
    stamp = manifest.utc_now_iso(datetime(2026, 8, 31, 22, 0, 0, tzinfo=sydney))
    assert stamp == "2026-08-31T12:00:00Z"


def test_manifest_records_the_experiment_id_and_motor(frozen_clock):
    payload = a_manifest(frozen_clock, motor=2)
    assert payload["experiment_id"] == "motor-char"
    assert payload["motor"] == 2


def test_manifest_records_the_parameters(frozen_clock):
    assert a_manifest(frozen_clock)["params"]["duty_frac"] == 0.5


def test_manifest_carries_a_schema_version(frozen_clock):
    """Old manifests must stay readable after the schema changes, or week-one
    data quietly becomes unparseable in week three."""
    assert a_manifest(frozen_clock)["schema_version"] == manifest.SCHEMA_VERSION


def test_manifest_records_the_tool_version(frozen_clock):
    """So a number you plotted can be traced to the code that produced it."""
    assert a_manifest(frozen_clock)["tool_version"]


def test_manifest_records_the_firmware_identity(frozen_clock):
    payload = a_manifest(frozen_clock, firmware={"firmware_version": "0.1.0",
                                                 "firmware_sha": "deadbee",
                                                 "protocol_version": "1"})
    assert payload["firmware_version"] == "0.1.0"
    assert payload["firmware_sha"] == "deadbee"


def test_manifest_records_the_sample_rate_actually_used(frozen_clock):
    """Not the one requested. Those differ whenever the analyser cannot do what
    you asked, and that difference is what makes two runs incomparable."""
    assert a_manifest(frozen_clock, sample_rate_hz=4_000_000)["sample_rate_hz"] == \
        4_000_000


def test_every_required_key_is_present(frozen_clock):
    payload = a_manifest(frozen_clock)
    for key in manifest.REQUIRED_KEYS:
        assert key in payload


def test_an_unknown_git_sha_is_recorded_as_null_not_omitted(frozen_clock):
    """An absent key reads as "nobody thought about provenance"; an explicit
    null reads as "this run is not fully reproducible", which is honest."""
    payload = a_manifest(frozen_clock, git_sha=None)
    assert "git_sha" in payload and payload["git_sha"] is None


# --------------------------------------------------------------------------
# The unmeasured constant
# --------------------------------------------------------------------------


def test_ticks_per_rev_defaults_to_null(frozen_clock):
    """The 11-vs-14 dispute is unresolved. A default here would scale every
    rad/s, every odometry number and every EKF state by an unknown factor, and
    the error would surface in week three as "the map is skewed"."""
    assert a_manifest(frozen_clock)["ticks_per_rev"] is None


def test_ticks_per_rev_is_top_level_not_buried_in_params(frozen_clock):
    """Buried in `params` it gets missed. It is the constant every downstream
    number scales with."""
    payload = a_manifest(frozen_clock)
    assert "ticks_per_rev" in payload
    assert "ticks_per_rev" not in payload["params"]


def test_a_manifest_with_no_ticks_per_rev_carries_a_loud_note(frozen_clock):
    payload = a_manifest(frozen_clock)
    joined = " ".join(payload["notes"]).lower()
    assert "ticks_per_rev" in joined
    assert "rad/s" in joined


def test_units_are_not_converted_without_a_measured_constant(frozen_clock):
    assert manifest.units_are_converted(a_manifest(frozen_clock)) is False


def test_units_are_converted_once_the_constant_is_supplied(frozen_clock):
    """The value is a flat invented 1000.0 on purpose. The real constant is
    disputed and unmeasured (Story 1.4); a suite that sprinkles plausible
    candidates around is a suite one of them gets quoted from."""
    assert manifest.units_are_converted(
        a_manifest(frozen_clock, ticks_per_rev=1000.0)) is True


def test_the_source_of_the_constant_is_recorded(frozen_clock):
    """"unmeasured" is a claim about provenance, and it is the claim that stops
    a placeholder being quoted as a measurement."""
    assert a_manifest(frozen_clock)["ticks_per_rev_source"] == "unmeasured"


def test_supplying_the_constant_does_not_add_the_warning_note(frozen_clock):
    payload = a_manifest(frozen_clock, ticks_per_rev=1000.0,
                         ticks_per_rev_source="measured 2026-09-01, story 1.4")
    assert manifest.UNCONVERTED_UNITS_NOTE not in payload["notes"]


def test_the_describe_line_says_unmeasured_out_loud(frozen_clock):
    """This line is what a bench operator reads in the terminal. It has to say
    the speeds are not rad/s."""
    assert "UNMEASURED" in manifest.describe_manifest(a_manifest(frozen_clock)).upper()


# --------------------------------------------------------------------------
# Persistence
# --------------------------------------------------------------------------


def test_write_then_read_round_trips(tmp_path, frozen_clock):
    path = tmp_path / "manifest.json"
    manifest.write_manifest(path, a_manifest(frozen_clock))
    assert manifest.read_manifest(path) == a_manifest(frozen_clock)


def test_the_written_file_is_plain_readable_json(tmp_path, frozen_clock):
    """Greppable, diffable, and readable in six months without this repo."""
    path = tmp_path / "manifest.json"
    manifest.write_manifest(path, a_manifest(frozen_clock))
    assert json.loads(path.read_text())["experiment_id"] == "motor-char"


def test_write_creates_missing_parent_directories(tmp_path, frozen_clock):
    path = tmp_path / "experiments" / "motor-char" / "motor-0" / "manifest.json"
    manifest.write_manifest(path, a_manifest(frozen_clock))
    assert path.is_file()


def test_writing_an_invalid_manifest_is_refused(tmp_path, frozen_clock):
    """Better to fail at write time than to leave an unusable record beside
    good data, where it is only discovered by whoever tries to read it."""
    broken = a_manifest(frozen_clock)
    broken.pop("git_sha")
    with pytest.raises(manifest.ManifestError):
        manifest.write_manifest(tmp_path / "m.json", broken)


def test_reading_a_missing_manifest_raises(tmp_path):
    with pytest.raises((FileNotFoundError, manifest.ManifestError)):
        manifest.read_manifest(tmp_path / "nope.json")


def test_reading_corrupt_json_raises_a_manifest_error(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text("{not json")
    with pytest.raises(manifest.ManifestError):
        manifest.read_manifest(path)


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------


def test_validate_accepts_a_well_formed_manifest(frozen_clock):
    manifest.validate_manifest(a_manifest(frozen_clock))


@pytest.mark.parametrize("key", ["experiment_id", "git_sha", "argv",
                                 "timestamp_utc", "params", "ticks_per_rev",
                                 "schema_version"])
def test_validate_refuses_a_manifest_missing_a_required_key(frozen_clock, key):
    payload = a_manifest(frozen_clock)
    payload.pop(key, None)
    with pytest.raises(manifest.ManifestError):
        manifest.validate_manifest(payload)


def test_validate_checks_presence_not_truthiness(frozen_clock):
    """`git_sha: None` is a valid and honest value; a *missing* git_sha is not."""
    manifest.validate_manifest(a_manifest(frozen_clock, git_sha=None))


def test_validate_refuses_a_non_mapping():
    with pytest.raises(manifest.ManifestError):
        manifest.validate_manifest(["not", "a", "manifest"])


def test_validate_refuses_a_string_argv(frozen_clock):
    """A string argv silently iterates as characters wherever it is used."""
    with pytest.raises(manifest.ManifestError):
        manifest.validate_manifest(dict(a_manifest(frozen_clock),
                                        argv="rover-bench run"))


def test_validate_refuses_a_non_mapping_params(frozen_clock):
    with pytest.raises(manifest.ManifestError):
        manifest.validate_manifest(dict(a_manifest(frozen_clock), params=["duty"]))


def test_a_string_argv_is_not_exploded_into_characters(frozen_clock):
    """REGRESSION. `list("rover-bench run")` is fifteen single characters, and
    a manifest whose argv reads ['r','o','v',...] records nothing about how the
    run was invoked - while still passing validate_manifest, because it is a
    list."""
    payload = a_manifest(frozen_clock, argv="rover-bench run --motor 0")
    assert payload["argv"] != list("rover-bench run --motor 0")


# --------------------------------------------------------------------------
# Provenance without shelling out
# --------------------------------------------------------------------------


def test_git_state_uses_the_injected_runner():
    """The manifest must be buildable without spawning git - that is what lets
    the whole suite run with the no-subprocess guard on."""
    calls = []

    class Proc:
        returncode = 0

        def __init__(self, out):
            self.stdout = out

    def runner(argv):
        calls.append(argv)
        return Proc(SHA if "rev-parse" in argv else "")

    state = manifest.GitState.read("/repo", runner)
    assert state.sha == SHA
    assert calls
    assert ["git", "-C", "/repo", "rev-parse", "HEAD"] in calls


def test_a_dirty_tree_is_recorded_as_dirty():
    class Proc:
        returncode = 0

        def __init__(self, out):
            self.stdout = out

    def runner(argv):
        return Proc(SHA if "rev-parse" in argv else " M tools/rover_bench/link.py")

    assert manifest.GitState.read("/repo", runner).dirty is True


def test_a_failed_git_call_yields_an_unknown_sha_rather_than_crashing():
    """A manifest with a null SHA is honest and still useful. Crashing here
    would block data collection over bookkeeping."""
    class Proc:
        returncode = 128
        stdout = ""

    state = manifest.GitState.read("/repo", lambda argv: Proc())
    assert state.sha is None and state.dirty is None


def test_a_clean_tree_is_recorded_as_clean_not_as_unknown():
    """REGRESSION. `git status --porcelain` prints nothing for a clean tree, and
    an early version collapsed that empty string to None - so a clean tree
    recorded git_dirty=null ("unknown") and a manifest could never make the one
    claim a reproducibility record exists to make."""
    class Proc:
        def __init__(self, rc, out):
            self.returncode = rc
            self.stdout = out

    def runner(argv):
        return Proc(0, SHA) if "rev-parse" in argv else Proc(0, "")

    assert manifest.GitState.read("/repo", runner).dirty is False


def test_a_failed_status_command_is_recorded_as_unknown():
    """The other half of the same distinction: if git could not be asked, the
    honest answer is null, not "clean"."""
    class Proc:
        def __init__(self, rc, out):
            self.returncode = rc
            self.stdout = out

    def runner(argv):
        return Proc(0, SHA) if "rev-parse" in argv else Proc(1, "")

    assert manifest.GitState.read("/repo", runner).dirty is None


# --------------------------------------------------------------------------
# The reproducibility diff
# --------------------------------------------------------------------------


def test_identical_runs_diff_to_nothing(frozen_clock):
    assert manifest.diff_manifests(a_manifest(frozen_clock),
                                   a_manifest(frozen_clock)) == {}


def test_a_changed_parameter_is_flagged(frozen_clock):
    """The headline case: same experiment, same commit, one parameter moved.
    That is exactly the difference that explains two disagreeing plots."""
    first = a_manifest(frozen_clock)
    second = a_manifest(frozen_clock, params=dict(PARAMS, duty_frac=0.8))
    flat = json.dumps(manifest.diff_manifests(first, second), default=str)
    assert "duty_frac" in flat and "0.5" in flat and "0.8" in flat


def test_a_changed_commit_is_flagged(frozen_clock):
    diff = manifest.diff_manifests(a_manifest(frozen_clock),
                                   a_manifest(frozen_clock, git_sha="f" * 40))
    assert "git_sha" in diff


def test_a_changed_sample_rate_is_flagged(frozen_clock):
    diff = manifest.diff_manifests(a_manifest(frozen_clock, sample_rate_hz=1_000_000),
                                   a_manifest(frozen_clock, sample_rate_hz=4_000_000))
    assert "sample_rate_hz" in diff


def test_an_added_parameter_is_flagged(frozen_clock):
    second = a_manifest(frozen_clock, params=dict(PARAMS, ramp_s=2.0))
    assert manifest.diff_manifests(a_manifest(frozen_clock), second)


def test_a_removed_parameter_is_flagged(frozen_clock):
    second = a_manifest(frozen_clock, params={"duty_frac": 0.5})
    assert manifest.diff_manifests(a_manifest(frozen_clock), second)


def test_the_diff_names_the_same_keys_in_either_direction(frozen_clock):
    first = a_manifest(frozen_clock)
    second = a_manifest(frozen_clock, params=dict(PARAMS, duty_frac=0.8))
    assert set(manifest.diff_manifests(first, second)) == \
        set(manifest.diff_manifests(second, first))


def test_a_timestamp_alone_does_not_break_reproducibility(frozen_clock):
    """Two runs are never simultaneous. If the wall clock counted, nothing
    would ever compare equal and the check would be worthless."""
    first = a_manifest(frozen_clock)
    second = a_manifest(frozen_clock.advance(3600))
    assert manifest.is_reproducible(first, second) is True


def test_a_different_motor_does_not_break_reproducibility(frozen_clock):
    """Comparing motor 0 with motor 3 IS the exercise (Story 1.5)."""
    assert manifest.is_reproducible(a_manifest(frozen_clock, motor=0),
                                    a_manifest(frozen_clock, motor=3)) is True


def test_a_different_host_does_not_break_reproducibility(frozen_clock):
    assert manifest.is_reproducible(a_manifest(frozen_clock, host="a"),
                                    a_manifest(frozen_clock, host="b")) is True


def test_a_changed_parameter_breaks_reproducibility(frozen_clock):
    second = a_manifest(frozen_clock, params=dict(PARAMS, duty_frac=0.8))
    assert manifest.is_reproducible(a_manifest(frozen_clock), second) is False


def test_a_changed_commit_breaks_reproducibility(frozen_clock):
    assert manifest.is_reproducible(a_manifest(frozen_clock),
                                    a_manifest(frozen_clock, git_sha="f" * 40)) is False


def test_a_changed_firmware_breaks_reproducibility(frozen_clock):
    """Two motors characterised under different firmware are not comparable,
    however identical the host-side parameters were."""
    first = a_manifest(frozen_clock, firmware={"firmware_version": "0.1.0"})
    second = a_manifest(frozen_clock, firmware={"firmware_version": "0.2.0"})
    assert manifest.is_reproducible(first, second) is False


def test_a_changed_ticks_per_rev_breaks_reproducibility(frozen_clock):
    """Every speed scales with it. Two runs converted with different constants
    are on different axes even if the raw ticks are identical."""
    first = a_manifest(frozen_clock, ticks_per_rev=1000.0)
    second = a_manifest(frozen_clock, ticks_per_rev=2000.0)
    assert manifest.is_reproducible(first, second) is False


def test_a_dry_run_is_not_comparable_with_a_real_one(frozen_clock):
    assert manifest.is_reproducible(a_manifest(frozen_clock, dry_run=True),
                                    a_manifest(frozen_clock, dry_run=False)) is False


def test_a_field_can_be_deliberately_excused(frozen_clock):
    """Excusing a field is then a recorded decision rather than an oversight -
    for example a firmware fix you know does not touch the measurement path."""
    first = a_manifest(frozen_clock, firmware={"firmware_version": "0.1.0"})
    second = a_manifest(frozen_clock, firmware={"firmware_version": "0.2.0"})
    assert manifest.is_reproducible(first, second, ignore=["firmware_version"]) is True


def test_the_report_says_comparable_in_words(frozen_clock):
    """A bench operator reads this, not a boolean."""
    report = manifest.verify_reproducible(a_manifest(frozen_clock),
                                          a_manifest(frozen_clock))
    assert report.comparable is True
    assert "COMPARABLE" in report.text().upper()


def test_the_report_lists_the_critical_difference(frozen_clock):
    second = a_manifest(frozen_clock, params=dict(PARAMS, duty_frac=0.8))
    report = manifest.verify_reproducible(a_manifest(frozen_clock), second)
    assert report.comparable is False
    assert "duty_frac" in report.text()
