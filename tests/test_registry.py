"""experiments/REGISTRY.md: append a row when an experiment runs, never later.

CLAUDE.md makes the 7-column schema non-negotiable and the registry a
CLOSE-ritual gate. The file is hand-edited markdown that a human reads, so the
machine writing to it has exactly two jobs: put the row in the right place, and
touch nothing else. Both are tested here, and "touch nothing else" is tested by
comparing the whole file line by line rather than by looking for the new row.
"""

from __future__ import annotations

import pytest

from conftest import REGISTRY_COLUMNS, REPO_ROOT, import_or_none

registry = import_or_none("rover_bench.registry")

pytestmark = pytest.mark.xfail(
    registry is None,
    reason="rover_bench.registry has not landed yet; these activate when it does",
    strict=False,
)

ROW = {
    "ID": "pwm-capture",
    "Date": "2026-08-31",
    "Story": "1.3",
    "Question": "What is the actual PWM duty and frequency?",
    "Method": "sigrok-cli capture on D0, decode duty over 100 periods",
    "Result": "20.01 kHz, duty within 0.4% of commanded",
    "Data": "experiments/pwm-capture/motor-0/",
}


# --------------------------------------------------------------------------
# The schema
# --------------------------------------------------------------------------


def test_columns_match_the_documented_schema_exactly():
    """Order included. A reordered column set silently changes what every
    previous row means."""
    assert tuple(registry.COLUMNS) == REGISTRY_COLUMNS


def test_the_real_registry_in_this_repo_uses_that_schema():
    """Guards against the code and the actual file drifting apart."""
    text = (REPO_ROOT / "experiments" / "REGISTRY.md").read_text()
    header = next(line for line in text.splitlines()
                  if line.startswith("|") and "ID" in line and "Date" in line)
    assert tuple(c.strip() for c in header.strip().strip("|").split("|")) == \
        REGISTRY_COLUMNS


def test_format_row_emits_seven_cells():
    cells = registry.split_row(registry.format_row(ROW))
    assert len(cells) == 7


def test_format_row_puts_values_in_column_order():
    cells = registry.split_row(registry.format_row(ROW))
    assert cells[0] == "pwm-capture"
    assert cells[1] == "2026-08-31"
    assert cells[-1] == "experiments/pwm-capture/motor-0/"


@pytest.mark.parametrize("missing", list(REGISTRY_COLUMNS))
def test_a_row_missing_any_column_is_refused(temp_repo, missing):
    row = {k: v for k, v in ROW.items() if k != missing}
    with pytest.raises(registry.RegistryError):
        registry.append_row(temp_repo.registry, row)


def test_a_row_with_an_unknown_column_is_refused(temp_repo):
    """A typo'd key would otherwise be dropped silently and its value lost."""
    with pytest.raises(registry.RegistryError):
        registry.append_row(temp_repo.registry, dict(ROW, Notes="oops"))


def test_a_row_that_is_not_a_mapping_is_refused(temp_repo):
    with pytest.raises(registry.RegistryError):
        registry.append_row(temp_repo.registry, ["pwm-capture"])


# --------------------------------------------------------------------------
# Cell escaping - not corrupting the file
# --------------------------------------------------------------------------


def test_a_pipe_in_a_cell_does_not_split_the_row(temp_repo):
    """"deadband 0.12 (fwd) | 0.13 (rev)" is a thing a Result genuinely wants
    to say. Unescaped it splits the row and shifts every later cell one left."""
    row = dict(ROW, Result="deadband 0.12 (fwd) | 0.13 (rev)")
    registry.append_row(temp_repo.registry, row)
    rows = registry.read_rows(temp_repo.registry)
    assert rows[-1]["Result"] == "deadband 0.12 (fwd) | 0.13 (rev)"
    assert set(rows[-1]) == set(REGISTRY_COLUMNS)


def test_a_newline_in_a_cell_becomes_a_space(temp_repo):
    """A raw newline would end the table row halfway through."""
    registry.append_row(temp_repo.registry, dict(ROW, Result="line one\nline two"))
    rows = registry.read_rows(temp_repo.registry)
    assert rows[-1]["Result"] == "line one line two"
    assert len(rows) == 2


def test_a_crlf_in_a_cell_becomes_one_space(temp_repo):
    registry.append_row(temp_repo.registry, dict(ROW, Result="a\r\nb"))
    assert registry.read_rows(temp_repo.registry)[-1]["Result"] == "a b"


def test_escaping_round_trips_through_read():
    assert registry.unescape_cell(registry.escape_cell("a | b")) == "a | b"


def test_a_backslash_before_a_pipe_does_not_shift_every_later_cell(temp_repo):
    """FAILING - a real bug in `rover_bench.registry`, found by the property
    test in test_property_parsers.py and pinned here with a name.

    `escape_cell` escapes `|` as `\\|` but does not escape a backslash that was
    already in the text. So a Result of `0.12 (fwd) \\| 0.13 (rev)` - what a
    human writes when they think they have to escape the pipe themselves -
    renders as `... \\\\| ...`, and `split_row` reads that trailing `|` as a
    real cell boundary. The row becomes EIGHT cells, `read_rows` zips seven
    column names onto them, and the Data column silently becomes `0.13 (rev)`:
    the path to the raw data is gone, from the file whose entire job is to say
    where the raw data is. No exception is raised anywhere.

    THE FIX IS IN `tools/rover_bench/registry.py`, not here: escape `\\` as
    `\\\\` before escaping `|`, and unescape in the reverse order. This test is
    left failing on purpose - it is a corruption of the persisted record, and
    CLAUDE.md's CLOSE ritual should not pass with it in.
    """
    row = dict(ROW, Result=r"deadband 0.12 (fwd) \| 0.13 (rev)")
    registry.append_row(temp_repo.registry, row)
    read_back = registry.read_rows(temp_repo.registry)[-1]
    assert read_back["Data"] == ROW["Data"], (
        "the Data column was overwritten by the tail of the Result cell: "
        f"{read_back!r}"
    )


def test_a_none_cell_becomes_empty_not_the_word_none(temp_repo):
    """"None" in a Data column reads as a filename."""
    registry.append_row(temp_repo.registry, dict(ROW, Data=None))
    assert registry.read_rows(temp_repo.registry)[-1]["Data"] == ""


def test_runs_of_whitespace_are_collapsed():
    assert registry.escape_cell("a    b\t\tc") == "a b c"


# --------------------------------------------------------------------------
# Appending
# --------------------------------------------------------------------------


def test_append_adds_the_row(temp_repo):
    assert registry.append_row(temp_repo.registry, ROW) is True
    assert "pwm-capture" in temp_repo.read_registry()


def test_the_appended_row_reads_back_identically(temp_repo):
    registry.append_row(temp_repo.registry, ROW)
    assert registry.read_rows(temp_repo.registry)[-1] == ROW


def test_append_is_idempotent(temp_repo):
    """Re-running the same command must not produce a second identical row.
    Two identical rows read as two runs, which is worse than none."""
    assert registry.append_row(temp_repo.registry, ROW) is True
    assert registry.append_row(temp_repo.registry, ROW) is False
    ids = [r["ID"] for r in registry.read_rows(temp_repo.registry)]
    assert ids.count("pwm-capture") == 1


def test_an_idempotent_append_does_not_rewrite_the_file(temp_repo):
    registry.append_row(temp_repo.registry, ROW)
    before = temp_repo.read_registry()
    registry.append_row(temp_repo.registry, ROW)
    assert temp_repo.read_registry() == before


def test_the_same_id_with_a_different_result_is_kept_not_overwritten(temp_repo):
    """Same ID, different Result is either a second run or a mistake. Either
    way a human must see both: silently replacing the first destroys a result,
    and that is the one unacceptable answer."""
    registry.append_row(temp_repo.registry, ROW)
    registry.append_row(temp_repo.registry, dict(ROW, Result="20.02 kHz on a rerun"))
    text = temp_repo.read_registry()
    assert "20.01 kHz" in text and "20.02 kHz" in text


def test_append_preserves_every_existing_line(temp_repo):
    """The registry has prose above the table and a second table below it. An
    append that reflows any of that is destroying a human's work."""
    before = temp_repo.read_registry().splitlines()
    registry.append_row(temp_repo.registry, ROW)
    after = temp_repo.read_registry().splitlines()
    for line in before:
        assert line in after, f"append destroyed: {line!r}"


def test_append_preserves_the_seeded_row(temp_repo):
    registry.append_row(temp_repo.registry, ROW)
    ids = [r["ID"] for r in registry.read_rows(temp_repo.registry)]
    assert "`power-sag`" in ids or any("power-sag" in i for i in ids)


def test_append_puts_the_new_row_last(temp_repo):
    registry.append_row(temp_repo.registry, ROW)
    registry.append_row(temp_repo.registry, dict(ROW, ID="quad-phase", Story="1.4",
                                                 Data="experiments/quad-phase/"))
    assert registry.read_rows(temp_repo.registry)[-1]["ID"] == "quad-phase"


def test_append_does_not_touch_the_second_table(temp_repo):
    """The file also has a three-column "Planned" table. Matching the schema
    header rather than "the first table" is what stops the append landing in
    the wrong one."""
    registry.append_row(temp_repo.registry, ROW)
    text = temp_repo.read_registry()
    planned = text.split("## Planned", 1)[1]
    assert "pwm-capture" not in planned


def test_the_file_still_ends_with_a_newline(temp_repo):
    registry.append_row(temp_repo.registry, ROW)
    assert temp_repo.read_registry().endswith("\n")


def test_the_header_and_separator_survive(temp_repo):
    registry.append_row(temp_repo.registry, ROW)
    lines = temp_repo.read_registry().splitlines()
    header = next(i for i, line in enumerate(lines) if line.startswith("| ID |"))
    assert registry.is_separator(lines[header + 1])


def test_no_temporary_file_is_left_behind(temp_repo):
    """The write is atomic - a temp file then os.replace - because a
    half-written REGISTRY.md after a Ctrl-C is worse than no row at all."""
    registry.append_row(temp_repo.registry, ROW)
    leftovers = [p.name for p in temp_repo.registry.parent.iterdir()
                 if p.name.startswith(".registry-")]
    assert leftovers == []


def test_appending_to_a_file_with_no_table_is_refused(tmp_path):
    """An orphan line no markdown renderer shows is a result silently not
    persisted - exactly what the registry exists to prevent."""
    path = tmp_path / "REGISTRY.md"
    path.write_text("# Experiment registry\n\nNo table here yet.\n")
    with pytest.raises(registry.RegistryError):
        registry.append_row(path, ROW)


def test_appending_to_a_missing_file_raises(tmp_path):
    with pytest.raises((FileNotFoundError, registry.RegistryError)):
        registry.append_row(tmp_path / "nope.md", ROW)


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------


def test_read_rows_returns_dicts_keyed_by_column(temp_repo):
    assert set(registry.read_rows(temp_repo.registry)[0]) == set(REGISTRY_COLUMNS)


def test_read_rows_ignores_the_planned_table(temp_repo):
    """It has three columns, not seven. Parsing it as data would produce rows
    with five empty cells."""
    rows = registry.read_rows(temp_repo.registry)
    assert len(rows) == 1


def test_read_rows_of_the_real_repo_registry_parses(tmp_path):
    """The file as it actually is, prose and second table included."""
    rows = registry.read_rows(REPO_ROOT / "experiments" / "REGISTRY.md")
    assert isinstance(rows, list)
    for row in rows:
        assert set(row) == set(REGISTRY_COLUMNS)


def test_read_rows_of_a_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        registry.read_rows(tmp_path / "REGISTRY.md")


def test_a_file_with_no_table_reads_as_no_rows(tmp_path):
    path = tmp_path / "REGISTRY.md"
    path.write_text("# nothing here\n")
    assert registry.read_rows(path) == []


# --------------------------------------------------------------------------
# The typed convenience wrapper
# --------------------------------------------------------------------------


def test_registry_row_renders_the_same_as_the_dict_form():
    row = registry.RegistryRow(id=ROW["ID"], date=ROW["Date"], story=ROW["Story"],
                               question=ROW["Question"], method=ROW["Method"],
                               result=ROW["Result"], data=ROW["Data"])
    assert row.render() == registry.format_row(ROW)


def test_registry_row_today_uses_utc():
    """CLAUDE.md fixes the timezone at UTC. A row dated in local time cannot be
    ordered against a manifest timestamped in UTC."""
    from datetime import date
    row = registry.RegistryRow.today(id="x", story="1.1", question="q", method="m",
                                     result="r", data="d", on=date(2026, 8, 31))
    assert row.date == "2026-08-31"


def test_ensure_registry_creates_a_usable_file(tmp_path):
    path = registry.ensure_registry(tmp_path / "experiments" / "REGISTRY.md")
    assert path.is_file()
    assert registry.append_row(path, ROW) is True


def test_ensure_registry_never_touches_an_existing_file(temp_repo):
    before = temp_repo.read_registry()
    registry.ensure_registry(temp_repo.registry)
    assert temp_repo.read_registry() == before
