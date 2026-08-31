"""Where a run's files go: derived from the experiment id and motor number,
and from nothing else.

Deterministic paths are what make a re-run overwrite its own output instead of
scattering timestamped directories nobody can correlate with a registry row.
The other half of this file is the units convention: `write_csv` refuses a
header whose columns do not state their units, which is how CLAUDE.md's
SI-units rule stops being a thing people have to remember.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import import_or_none

storage = import_or_none("rover_bench.storage")

pytestmark = pytest.mark.xfail(
    storage is None,
    reason="rover_bench.storage has not landed yet; these activate when it does",
    strict=False,
)


# --------------------------------------------------------------------------
# Determinism
# --------------------------------------------------------------------------


def test_same_inputs_give_the_same_path(tmp_path):
    assert storage.experiment_dir(tmp_path, "motor-char", 0) == \
        storage.experiment_dir(tmp_path, "motor-char", 0)


def test_no_timestamp_leaks_into_the_path(tmp_path, frozen_clock):
    """A wall-clock component would put two identical runs in different
    directories, and the second could never be diffed against the first without
    a search. The timestamp lives inside the manifest, where it is precise."""
    text = str(storage.experiment_dir(tmp_path, "motor-char", 0))
    for token in ("2026", "12-00", "12:00", "T12"):
        assert token not in text


def test_different_motors_get_different_directories(tmp_path):
    dirs = {storage.experiment_dir(tmp_path, "motor-char", m) for m in storage.MOTORS}
    assert len(dirs) == len(storage.MOTORS)


def test_different_experiments_get_different_directories(tmp_path):
    assert storage.experiment_dir(tmp_path, "motor-char", 0) != \
        storage.experiment_dir(tmp_path, "pwm-capture", 0)


def test_the_motor_number_is_visible_in_the_path(tmp_path):
    """A human has to find these directories by eye at the bench."""
    assert storage.experiment_dir(tmp_path, "motor-char", 2).name == "motor-2"


def test_the_experiment_id_is_visible_in_the_path(tmp_path):
    assert "motor-char" in storage.experiment_dir(tmp_path, "motor-char", 0).parts


def test_paths_live_under_the_given_root(tmp_path):
    assert Path(tmp_path) in storage.experiment_dir(tmp_path, "motor-char", 0).parents


def test_paths_live_under_experiments(tmp_path):
    """CLAUDE.md: raw data goes in experiments/<id>/."""
    assert storage.EXPERIMENTS_DIRNAME in \
        storage.experiment_dir(tmp_path, "motor-char", 0).parts


def test_path_derivation_creates_nothing(tmp_path):
    """Asking for a name must not litter the tree, or --dry-run stops being dry."""
    for fn in (storage.experiment_dir, storage.manifest_path, storage.csv_path,
               storage.capture_path):
        fn(tmp_path, "motor-char", 0)
    assert list(Path(tmp_path).iterdir()) == []


def test_ensure_experiment_dir_creates_it(tmp_path):
    assert storage.ensure_experiment_dir(tmp_path, "motor-char", 0).is_dir()


def test_ensure_experiment_dir_is_idempotent(tmp_path):
    first = storage.ensure_experiment_dir(tmp_path, "motor-char", 0)
    second = storage.ensure_experiment_dir(tmp_path, "motor-char", 0)
    assert first == second and second.is_dir()


def test_ensure_experiment_dir_does_not_destroy_existing_content(tmp_path):
    directory = storage.ensure_experiment_dir(tmp_path, "motor-char", 0)
    (directory / "keepme.csv").write_text("data")
    storage.ensure_experiment_dir(tmp_path, "motor-char", 0)
    assert (directory / "keepme.csv").read_text() == "data"


# --------------------------------------------------------------------------
# The artefacts of one run
# --------------------------------------------------------------------------


def test_every_artefact_lives_in_the_experiment_directory(tmp_path):
    base = storage.experiment_dir(tmp_path, "motor-char", 1)
    for path in (storage.manifest_path(tmp_path, "motor-char", 1),
                 storage.csv_path(tmp_path, "motor-char", 1),
                 storage.capture_path(tmp_path, "motor-char", 1),
                 storage.plot_path(tmp_path, "motor-char", 1, "deadband")):
        assert path.parent == base


def test_the_three_artefacts_have_distinct_names(tmp_path):
    names = {storage.manifest_path(tmp_path, "motor-char", 1).name,
             storage.csv_path(tmp_path, "motor-char", 1).name,
             storage.capture_path(tmp_path, "motor-char", 1).name}
    assert len(names) == 3


def test_the_manifest_is_json(tmp_path):
    assert storage.manifest_path(tmp_path, "motor-char", 0).suffix == ".json"


def test_the_data_is_csv(tmp_path):
    assert storage.csv_path(tmp_path, "motor-char", 0).suffix == ".csv"


def test_the_capture_is_a_dot_sr(tmp_path):
    """.sr is what sigrok writes, and `.gitignore` already excludes it - those
    two facts have to agree or megabytes of raw samples land in git."""
    assert storage.capture_path(tmp_path, "motor-char", 0).suffix == ".sr"


def test_the_gitignore_actually_excludes_captures():
    from conftest import REPO_ROOT
    text = (REPO_ROOT / ".gitignore").read_text()
    assert "*.sr" in text


def test_named_artefacts_are_distinct(tmp_path):
    a = storage.csv_path(tmp_path, "motor-char", 0, name="ramp")
    b = storage.csv_path(tmp_path, "motor-char", 0, name="steady")
    assert a != b


# --------------------------------------------------------------------------
# Refusing nonsense
# --------------------------------------------------------------------------


@pytest.mark.parametrize("bad_id", [
    pytest.param("../../etc", id="path-traversal"),
    pytest.param("..", id="parent-dir"),
    pytest.param("/absolute", id="absolute"),
    pytest.param("with/slash", id="embedded-slash"),
    pytest.param("", id="empty"),
    pytest.param("   ", id="whitespace"),
    pytest.param("with space", id="space"),
    pytest.param("with\x00nul", id="nul"),
    pytest.param("UPPER", id="uppercase"),
    pytest.param("trailing-", id="trailing-hyphen"),
    pytest.param("double--hyphen", id="double-hyphen"),
    pytest.param(None, id="none"),
    pytest.param(7, id="not-a-string"),
])
def test_a_dangerous_or_malformed_experiment_id_is_refused(tmp_path, bad_id):
    """The id becomes a directory name AND a registry key. Kebab-case closes
    the path-traversal hole for free: `../../etc` is not kebab-case."""
    with pytest.raises(storage.StorageError):
        storage.experiment_dir(tmp_path, bad_id, 0)


@pytest.mark.parametrize("bad_motor", [-1, 4, 99, "0", None, 1.5, True])
def test_an_invalid_motor_number_is_refused(tmp_path, bad_motor):
    """Four motors. Motor 4 is a typo, and a typo that silently created a
    directory would produce data nobody ever looks at again. `True` is excluded
    explicitly because `True == 1` in Python and that is a bug, not an index."""
    with pytest.raises(storage.StorageError):
        storage.experiment_dir(tmp_path, "motor-char", bad_motor)


@pytest.mark.parametrize("motor", [0, 1, 2, 3])
def test_every_real_motor_is_accepted(tmp_path, motor):
    assert storage.experiment_dir(tmp_path, "motor-char", motor)


@pytest.mark.parametrize("bad_name", ["../escape", "with/slash", "with space",
                                      "dot.name", "", None])
def test_a_dangerous_artefact_name_is_refused(tmp_path, bad_name):
    with pytest.raises(storage.StorageError):
        storage.csv_path(tmp_path, "motor-char", 0, name=bad_name)


# --------------------------------------------------------------------------
# CSV: the units convention, enforced
# --------------------------------------------------------------------------


GOOD_COLUMNS = ["t_s", "duty_frac", "ticks", "speed_ticks_per_s", "motor"]


def test_a_csv_with_unit_bearing_columns_is_written(tmp_path):
    path = storage.write_csv(tmp_path / "samples.csv", GOOD_COLUMNS,
                             [dict.fromkeys(GOOD_COLUMNS, 0)])
    assert Path(path).is_file()


def test_a_written_csv_reads_back(tmp_path):
    rows = [{"t_s": 0.01, "duty_frac": 0.5, "ticks": 12,
             "speed_ticks_per_s": 1200, "motor": 0}]
    storage.write_csv(tmp_path / "s.csv", GOOD_COLUMNS, rows)
    columns, read = storage.read_csv(tmp_path / "s.csv")
    assert columns == GOOD_COLUMNS
    assert read[0]["duty_frac"] == "0.5"


@pytest.mark.parametrize("column", ["speed", "velocity", "current", "time",
                                    "omega", "duty"])
def test_a_column_with_no_units_is_refused(tmp_path, column):
    """"Mixed units are a leading cause of control bugs" (CLAUDE.md), and the
    ticks->rad/s boundary is exactly where this project will get bitten. A
    column called `speed` is a number whose meaning has to be remembered."""
    with pytest.raises(storage.StorageError):
        storage.write_csv(tmp_path / "s.csv", [column], [])


def test_a_csv_with_no_columns_is_refused(tmp_path):
    with pytest.raises(storage.StorageError):
        storage.write_csv(tmp_path / "s.csv", [], [])


def test_a_row_carrying_an_unknown_column_is_refused(tmp_path):
    """Silently dropping the extra column is how a measurement disappears."""
    with pytest.raises(Exception):  # noqa: B017 - csv raises its own ValueError
        storage.write_csv(tmp_path / "s.csv", ["t_s"], [{"t_s": 0, "surprise_v": 1}])


def test_label_columns_are_exempt_from_the_units_rule(tmp_path):
    """`motor` and `direction` are labels, not measurements."""
    storage.validate_columns(["motor", "direction", "phase", "t_s"])


def test_the_rad_s_suffix_is_allowed_but_ticks_per_s_is_the_honest_default():
    """rad/s is only meaningful once ticks_per_rev is measured; ticks/s is what
    the hardware actually counts and needs no calibration to be true."""
    assert "_rad_s" in storage.ALLOWED_UNIT_SUFFIXES
    assert "_ticks_per_s" in storage.ALLOWED_UNIT_SUFFIXES


# --------------------------------------------------------------------------
# JSON
# --------------------------------------------------------------------------


def test_json_round_trips(tmp_path):
    payload = {"b": 2, "a": {"nested": True}}
    storage.write_json(tmp_path / "m.json", payload)
    assert storage.read_json(tmp_path / "m.json") == payload


def test_json_is_written_with_sorted_keys(tmp_path):
    """So `diff` between two manifests is readable, which is the whole point of
    the reproducibility check."""
    storage.write_json(tmp_path / "m.json", {"zebra": 1, "apple": 2})
    text = (tmp_path / "m.json").read_text()
    assert text.index("apple") < text.index("zebra")


def test_json_ends_with_a_newline(tmp_path):
    storage.write_json(tmp_path / "m.json", {"a": 1})
    assert (tmp_path / "m.json").read_text().endswith("\n")


def test_write_json_creates_missing_parents(tmp_path):
    target = tmp_path / "experiments" / "motor-char" / "motor-0" / "manifest.json"
    storage.write_json(target, {"a": 1})
    assert target.is_file()


# --------------------------------------------------------------------------
# Numbered attempts
# --------------------------------------------------------------------------


def test_run_ids_start_at_one(tmp_path):
    store = storage.Storage(tmp_path)
    assert store.next_run_id("motor-char", 0) == "run-0001"


def test_run_ids_are_zero_padded_so_ls_sorts_correctly(tmp_path):
    store = storage.Storage(tmp_path)
    assert store.next_run_id("motor-char", 0).endswith("0001")


def test_run_ids_advance_as_runs_land(tmp_path):
    store = storage.Storage(tmp_path)
    paths = store.run_paths("motor-char", 0)
    paths.csv.parent.mkdir(parents=True, exist_ok=True)
    paths.csv.write_text("t_s\n")
    assert store.next_run_id("motor-char", 0) == "run-0002"


def test_run_numbering_is_per_motor(tmp_path):
    """`motor-1/run-0002` and `motor-3/run-0002` are the second attempt at
    each, which is what you want when comparing one motor to another."""
    store = storage.Storage(tmp_path)
    paths = store.run_paths("motor-char", 0)
    paths.csv.parent.mkdir(parents=True, exist_ok=True)
    paths.csv.write_text("t_s\n")
    assert store.next_run_id("motor-char", 1) == "run-0001"


def test_the_three_run_artefacts_share_a_stem(tmp_path):
    """One stem, one run, three artifacts - that is the whole filing system."""
    paths = storage.Storage(tmp_path).run_paths("motor-char", 0, "run-0001")
    stems = {paths.csv.stem, paths.json.stem, paths.sr.stem}
    assert stems == {"run-0001"}


def test_an_invented_run_id_is_refused(tmp_path):
    """Run ids are allocated from what is on disk, not typed."""
    with pytest.raises(storage.StorageError):
        storage.Storage(tmp_path).run_paths("motor-char", 0, "final_v2_REAL")
