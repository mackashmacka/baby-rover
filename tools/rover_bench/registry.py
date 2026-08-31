"""registry.py — append a row to `experiments/REGISTRY.md`, idempotently.

WHY THE TOOL WRITES THE ROW AND NOT THE HUMAN
---------------------------------------------
"A result not persisted did not happen" is CLOSE-ritual item 7, and the way it
fails in practice is never dishonesty — it is a run finishing at 23:40 and the
row getting written "tomorrow".  So the row is written by the same command that
took the data, in the same second, or not at all.

THE SCHEMA IS FIXED BY experiments/REGISTRY.md
----------------------------------------------
    | ID | Date | Story | Question | Method | Result | Data |

Seven columns, in that order.  Order included: the registry is a markdown table
a human reads, and a reordered column set silently changes what every previous
row means.

This module finds *that* table by matching its header, which matters because
the file also contains a three-column "Planned" table further down.  Matching
on the header rather than on "the first table" means adding more tables to the
file cannot corrupt the append.

IDEMPOTENCY, AND WHAT IT IS NOT
-------------------------------
Re-running the same command must not produce a second identical row — two
identical rows read as two runs, which is worse than none.  So an *identical*
row is a no-op returning False.

A row with the same ID but different content is **appended, not overwritten**.
Same ID, different Result is either a second run (keep both) or a mistake
(keep both, and let a human see them side by side).  Silently replacing the
first one is the single unacceptable answer: it destroys a result.

NOT CORRUPTING THE FILE
-----------------------
Cells are escaped (`|` becomes `\\|`, newlines become spaces) so a Result
containing a pipe cannot split a row and shift every later cell one to the
left.  The write is atomic — temp file in the same directory, then
`os.replace` — because a half-written REGISTRY.md after a Ctrl-C would be
worse than no row at all.
"""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

COLUMNS: tuple[str, ...] = ("ID", "Date", "Story", "Question", "Method",
                            "Result", "Data")
#: Kept as an alias because "schema" is what CLAUDE.md calls it.
SCHEMA = COLUMNS
SEPARATOR_ROW = "|" + "|".join("---" for _ in COLUMNS) + "|"


class RegistryError(RuntimeError):
    """The row is malformed, or the registry file is not shaped as expected."""


# --------------------------------------------------------------------------
# Cells
# --------------------------------------------------------------------------

def escape_cell(text: object) -> str:
    """Make a value safe to put between pipes.

    Escaping rather than rejecting: a Result cell genuinely wants to say
    `deadband 0.12 (fwd) | 0.13 (rev)`, and refusing that would push people
    back to editing the table by hand — which is how rows stop getting written.
    """
    value = "" if text is None else str(text)
    value = value.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    # Backslash FIRST, then pipe.  The other order is a data-corruption bug:
    # a cell already containing `\|` (what a human writes when they think they
    # must escape the pipe themselves) would render as `\\|`, `split_row` would
    # read that trailing `|` as a real cell boundary, the row would gain a cell,
    # and `read_rows` would zip the seven column names one place left — silently
    # overwriting Data, the one column whose whole job is to say where the raw
    # data is.  Regression test: tests/test_registry.py::
    # test_a_backslash_before_a_pipe_does_not_shift_every_later_cell.
    value = value.replace("\\", "\\\\").replace("|", r"\|")
    return " ".join(value.split()).strip()


def unescape_cell(text: str) -> str:
    """Exact inverse of `escape_cell`, so read/write round-trips.

    Single pass rather than two `replace` calls: with two escapes in play
    (`\\\\` and `\\|`) sequential replaces unescape each other's output and the
    round-trip stops being an identity.  A backslash takes the next character
    literally; that is the whole rule.
    """
    out: list[str] = []
    escaped = False
    for char in text:
        if escaped:
            out.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        else:
            out.append(char)
    if escaped:            # trailing lone backslash — keep it rather than eat it
        out.append("\\")
    return "".join(out).strip()


def validate_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """Exactly the seven columns — no more, no fewer.

    An unknown key is rejected rather than dropped: a typo'd column name would
    otherwise take its value with it, silently, and the data would be gone.
    """
    if not isinstance(row, Mapping):
        raise RegistryError(f"registry row must be a mapping, got {type(row).__name__}")
    missing = [c for c in COLUMNS if c not in row]
    if missing:
        raise RegistryError(f"registry row is missing column(s): {missing}")
    unknown = [k for k in row if k not in COLUMNS]
    if unknown:
        raise RegistryError(
            f"registry row has unknown column(s): {unknown}; the schema is "
            f"{list(COLUMNS)}"
        )
    return dict(row)


def format_row(row: Mapping[str, Any]) -> str:
    """Render one validated row as a markdown table line."""
    validated = validate_row(row)
    cells = [escape_cell(validated[column]) for column in COLUMNS]
    return "| " + " | ".join(cells) + " |"


@dataclass(frozen=True)
class RegistryRow:
    """A typed convenience wrapper for callers building a row in code.

    The dict form is the interface; this just stops experiment code from
    fat-fingering a column name.
    """

    id: str
    date: str
    story: str
    question: str
    method: str
    result: str
    data: str

    @classmethod
    def today(cls, *, id: str, story: str, question: str, method: str,
              result: str, data: str, on: date | None = None) -> "RegistryRow":
        """Date is UTC `YYYY-MM-DD` — CLAUDE.md fixes the timezone at UTC."""
        day = on or datetime.now(timezone.utc).date()
        return cls(id=id, date=day.isoformat(), story=story, question=question,
                   method=method, result=result, data=data)

    def as_dict(self) -> dict[str, str]:
        return {"ID": self.id, "Date": self.date, "Story": self.story,
                "Question": self.question, "Method": self.method,
                "Result": self.result, "Data": self.data}

    def render(self) -> str:
        return format_row(self.as_dict())


# --------------------------------------------------------------------------
# Table parsing
# --------------------------------------------------------------------------

def split_row(line: str) -> list[str]:
    """Split a markdown table row into cells, honouring `\\\\` and `\\|` escapes.

    The cells come back UNESCAPED — this is the full inverse of `format_row`,
    and `read_rows` therefore does not unescape again.  It used to unescape
    only the pipe and leave `\\\\` alone, so a second `unescape_cell` pass was
    needed and the two disagreed about backslashes; one escape, one place that
    removes it.
    """
    stripped = line.strip()
    if not stripped.startswith("|"):
        raise RegistryError(f"not a table row: {line!r}")
    body = stripped[1:]
    # Drop the closing pipe only if it is a real terminator: an ODD run of
    # backslashes before it means it is escaped and belongs to the last cell.
    if body.endswith("|"):
        run = len(body[:-1]) - len(body[:-1].rstrip("\\"))
        if run % 2 == 0:
            body = body[:-1]
    cells: list[str] = []
    current: list[str] = []
    escaped = False
    for char in body:
        if escaped:
            current.append(char)      # a backslash takes the next char literally
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == "|":
            cells.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    if escaped:
        current.append("\\")
    cells.append("".join(current).strip())
    return cells


def is_separator(line: str) -> bool:
    stripped = line.strip()
    return bool(stripped.startswith("|") and re.fullmatch(r"\|[\s:|-]+\|", stripped))


@dataclass(frozen=True)
class TableLocation:
    """Where the schema table lives in the file, as line indices."""

    header_index: int
    separator_index: int
    first_row_index: int
    end_index: int          # exclusive — the line after the last data row

    @property
    def row_indices(self) -> range:
        return range(self.first_row_index, self.end_index)


def locate_table(lines: Sequence[str],
                 columns: Sequence[str] = COLUMNS) -> TableLocation | None:
    """Find the table whose header is exactly `columns` (case-insensitive)."""
    wanted = [c.strip().lower() for c in columns]
    for index, line in enumerate(lines):
        if not line.strip().startswith("|"):
            continue
        cells = [c.strip().lower() for c in split_row(line)]
        if cells != wanted:
            continue
        separator = index + 1
        if separator >= len(lines) or not is_separator(lines[separator]):
            continue
        end = separator + 1
        while end < len(lines) and lines[end].strip().startswith("|"):
            end += 1
        return TableLocation(index, separator, separator + 1, end)
    return None


def _read_lines(path: str | os.PathLike[str]) -> list[str]:
    return Path(path).read_text(encoding="utf-8").splitlines()


def read_rows(path: str | os.PathLike[str]) -> list[dict[str, str]]:
    """Every data row of the schema table, as dicts keyed by column.

    Raises `FileNotFoundError` if the file is not there: a caller asking to
    read the registry when there is no registry has a bigger problem than an
    empty list would suggest.
    """
    lines = _read_lines(path)
    location = locate_table(lines)
    if location is None:
        return []
    rows: list[dict[str, str]] = []
    for index in location.row_indices:
        cells = split_row(lines[index])
        if len(cells) < len(COLUMNS):
            continue
        # `split_row` already unescaped; a second pass here would eat a real
        # backslash the author wrote.
        rows.append({name: cells[position].strip()
                     for position, name in enumerate(COLUMNS)})
    return rows


# --------------------------------------------------------------------------
# Appending
# --------------------------------------------------------------------------

def append_row(path: str | os.PathLike[str], row: Mapping[str, Any]) -> bool:
    """Add one row.  Returns True if written, False if it was already there.

    Raises `RegistryError` if the row is malformed or the file has no table
    with the schema header.  Appending to a file with no table would produce an
    orphan line no markdown renderer shows — a result silently not persisted,
    which is exactly the failure the registry exists to prevent.
    """
    rendered = format_row(row)
    lines = _read_lines(path)
    location = locate_table(lines)
    if location is None:
        raise RegistryError(
            f"{path} has no table with the header | {' | '.join(COLUMNS)} |.  "
            "Refusing to guess where the row goes — fix the file by hand."
        )
    for index in location.row_indices:
        if lines[index].strip() == rendered.strip():
            return False
    lines.insert(location.end_index, rendered)
    _atomic_write(path, lines)
    return True


def default_registry_text(title: str = "Experiment registry") -> str:
    """A fresh, empty registry.  Used to seed a scratch copy for `--dry-run`."""
    return "\n".join([
        f"# {title}",
        "",
        "One row per experiment. **A result not persisted did not happen.**",
        "",
        "| " + " | ".join(COLUMNS) + " |",
        SEPARATOR_ROW,
        "",
    ])


def ensure_registry(path: str | os.PathLike[str]) -> Path:
    """Create an empty registry if there is none.  Never touches an existing one."""
    target = Path(path)
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(default_registry_text(), encoding="utf-8")
    return target


def _atomic_write(path: str | os.PathLike[str], lines: Iterable[str]) -> None:
    target = Path(path)
    directory = str(target.parent) or "."
    os.makedirs(directory, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=directory, prefix=".registry-", suffix=".tmp",
        delete=False,
    )
    try:
        with handle:
            handle.write("\n".join(lines).rstrip("\n") + "\n")
        os.replace(handle.name, str(target))
    except BaseException:  # pragma: no cover - cleanup path
        try:
            os.unlink(handle.name)
        except OSError:
            pass
        raise
