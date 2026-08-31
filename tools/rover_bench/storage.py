"""storage.py — deterministic output layout.  You never choose a filename.

WHY
---
Ad-hoc filenames are how a data set dies.  `sweep_motor1_final_v2_REAL.csv` is
funny until it is six months later and you need to know whether it was taken
before or after the encoder was rewired.

So the path is *derived*, never typed::

    experiments/<experiment_id>/motor-<n>/manifest.json    the record
    experiments/<experiment_id>/motor-<n>/samples.csv      the data
    experiments/<experiment_id>/motor-<n>/capture.sr       the raw capture

Nothing in the path comes from the wall clock.  A timestamp in a directory name
means two runs of the same experiment land somewhere different and the second
can never be diffed against the first without a search; the timestamp lives
*inside* the manifest, where it can be read precisely and compared.  Re-running
an experiment overwrites its own output — which is the behaviour you want,
because the manifest beside it says exactly what produced it.

When several attempts must be kept side by side (the deadband on motor 2, three
times, because the first two disagreed) use the `Storage` class, which
allocates `run-0001`, `run-0002`, ... from what is already on disk.  Sequential
rather than timestamped, so "the third attempt at motor 2" is a thing you can
say out loud and then find.

PATH DERIVATION IS PURE
-----------------------
`experiment_dir()` and friends create nothing.  Asking for a name must not
litter the tree, or `--dry-run` stops being dry.  `ensure_experiment_dir()` is
the one that makes directories, and it says so in its name.

CSV COLUMN NAMES CARRY THEIR UNITS
----------------------------------
`speed_ticks_per_s`, not `speed`.  CLAUDE.md makes this a convention because
mixed units are a leading cause of control bugs, and the ticks->rad/s boundary
is precisely where this project will get bitten (`ticks_per_rev` is disputed
and unmeasured).  `write_csv` refuses a header with no unit suffix, so the
convention is enforced by code rather than by remembering.

`.sr` FILES ARE GITIGNORED
--------------------------
`.gitignore` already carries `experiments/**/*.sr`.  They are megabytes of raw
samples; the CSV and the manifest are the reviewable artifacts.  Keep the `.sr`
locally for as long as it is useful — it is what you open when a number looks
wrong.
"""

from __future__ import annotations

import csv
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

EXPERIMENTS_DIRNAME = "experiments"
RUN_PREFIX = "run-"
RUN_DIGITS = 4

#: Four motors, indexed 0-3 (left front, left rear, right front, right rear —
#: docs/WIRING.md §4).  Motor 4 is a typo, and a typo that silently created a
#: directory would produce data nobody ever looks at again.
MOTORS: tuple[int, ...] = (0, 1, 2, 3)

DEFAULT_CSV_NAME = "samples"
DEFAULT_CAPTURE_NAME = "capture"
MANIFEST_NAME = "manifest.json"

_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_RUN_RE = re.compile(rf"^{RUN_PREFIX}(\d{{{RUN_DIGITS}}})$")
_NAME_RE = re.compile(r"^[A-Za-z0-9]+(?:[-_][A-Za-z0-9]+)*$")

#: Unit suffixes a CSV column may end with.  Anything else is rejected, which
#: is how the SI-units convention stops being a thing people have to remember.
ALLOWED_UNIT_SUFFIXES: tuple[str, ...] = (
    "_s",                # seconds
    "_ms",               # milliseconds (loop period histograms read better in ms)
    "_us",               # microseconds
    "_hz",               # hertz
    "_ticks",            # raw encoder counts
    "_ticks_per_s",      # encoder counts per second — the honest speed unit
    "_rev",              # revolutions (also covers ticks_per_rev)
    "_rad_s",            # radians per second — only when ticks_per_rev is measured
    "_rad",              # radians
    "_frac",             # dimensionless fraction, e.g. duty_frac
    "_pct",              # percent, when a fraction would read badly
    "_a",                # amperes
    "_v",                # volts
    "_count",            # a pure count of things
    "_index",            # an index into something
)

#: Columns that are labels or bare units rather than measurements.
UNITLESS_COLUMNS: frozenset[str] = frozenset({
    "run", "motor", "direction", "phase", "mode", "trial", "repeat",
    "channel", "experiment_id", "note", "loop", "sample", "ticks",
})


class StorageError(RuntimeError):
    """A path could not be derived, or a file could not be written."""


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------

def validate_experiment_id(experiment_id: Any) -> str:
    """Experiment ids are kebab-case slugs, matching experiments/REGISTRY.md.

    Enforced because the id becomes a directory name *and* a registry key.  It
    also closes the obvious path-traversal hole: `../../etc` is not kebab-case,
    and neither is anything with a slash, a space or a NUL in it.
    """
    if not isinstance(experiment_id, str) or not _ID_RE.match(experiment_id):
        raise StorageError(
            f"experiment id {experiment_id!r} must be kebab-case "
            "(lowercase letters, digits and single hyphens), e.g. 'motor-char'"
        )
    return experiment_id


def validate_motor(motor: Any) -> int:
    """Motors are 0-3.  `bool` is excluded because `True == 1` and that is a bug."""
    if isinstance(motor, bool) or not isinstance(motor, int):
        raise StorageError(
            f"motor must be an int in {MOTORS}, got {motor!r} "
            f"({type(motor).__name__})"
        )
    if motor not in MOTORS:
        raise StorageError(f"motor {motor} is not one of {MOTORS}")
    return motor


def validate_name(name: str) -> str:
    """Artifact stems are simple identifiers — no slashes, no dots, no spaces."""
    if not isinstance(name, str) or not _NAME_RE.match(name):
        raise StorageError(
            f"artifact name {name!r} must be alphanumeric with - or _ separators"
        )
    return name


def validate_columns(columns: Sequence[str]) -> tuple[str, ...]:
    """Every column either carries a unit suffix or is a known label."""
    if not columns:
        raise StorageError("refusing to write a CSV with no columns")
    bad: list[str] = []
    for name in columns:
        lowered = str(name).lower()
        if lowered in UNITLESS_COLUMNS:
            continue
        if any(lowered.endswith(suffix) for suffix in ALLOWED_UNIT_SUFFIXES):
            continue
        bad.append(name)
    if bad:
        raise StorageError(
            f"CSV columns must state their units: {bad} — allowed suffixes "
            f"{ALLOWED_UNIT_SUFFIXES}, or one of {sorted(UNITLESS_COLUMNS)}"
        )
    return tuple(columns)


# --------------------------------------------------------------------------
# Path derivation — pure functions, no side effects
# --------------------------------------------------------------------------

def experiment_dir(root: str | os.PathLike[str], experiment_id: str,
                   motor: int) -> Path:
    """`<root>/experiments/<experiment_id>/motor-<n>`.  Creates nothing."""
    validate_experiment_id(experiment_id)
    validate_motor(motor)
    return Path(root) / EXPERIMENTS_DIRNAME / experiment_id / f"motor-{motor}"


def ensure_experiment_dir(root: str | os.PathLike[str], experiment_id: str,
                          motor: int) -> Path:
    """The same path, but on disk.  Idempotent."""
    path = experiment_dir(root, experiment_id, motor)
    path.mkdir(parents=True, exist_ok=True)
    return path


def manifest_path(root: str | os.PathLike[str], experiment_id: str,
                  motor: int) -> Path:
    return experiment_dir(root, experiment_id, motor) / MANIFEST_NAME


def csv_path(root: str | os.PathLike[str], experiment_id: str, motor: int,
             name: str = DEFAULT_CSV_NAME) -> Path:
    return experiment_dir(root, experiment_id, motor) / f"{validate_name(name)}.csv"


def capture_path(root: str | os.PathLike[str], experiment_id: str, motor: int,
                 name: str = DEFAULT_CAPTURE_NAME) -> Path:
    """`.sr` — what sigrok writes, and what `.gitignore` already excludes."""
    return experiment_dir(root, experiment_id, motor) / f"{validate_name(name)}.sr"


def plot_path(root: str | os.PathLike[str], experiment_id: str, motor: int,
              name: str) -> Path:
    return experiment_dir(root, experiment_id, motor) / f"{validate_name(name)}.png"


# --------------------------------------------------------------------------
# Writing
# --------------------------------------------------------------------------

def write_csv(path: str | os.PathLike[str], columns: Sequence[str],
              rows: Iterable[Mapping[str, Any]]) -> Path:
    """Write a CSV whose header states its units.

    `extrasaction="raise"` on purpose: a row carrying a key that is not in the
    header is a bug in the experiment, and silently dropping the column is how
    a measurement disappears without anybody noticing.
    """
    validate_columns(columns)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(columns), extrasaction="raise")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return target


def read_csv(path: str | os.PathLike[str]) -> tuple[list[str], list[dict[str, str]]]:
    with Path(path).open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        columns = list(reader.fieldnames or [])
        rows = [dict(row) for row in reader]
    return columns, rows


def write_json(path: str | os.PathLike[str], payload: Mapping[str, Any]) -> Path:
    """Pretty-printed, sorted keys, trailing newline.

    Sorted so `diff` between two manifests is readable, which is the whole
    point of the reproducibility check.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True, default=str)
        handle.write("\n")
    return target


def read_json(path: str | os.PathLike[str]) -> dict[str, Any]:
    with Path(path).open(encoding="utf-8") as handle:
        return json.load(handle)


# --------------------------------------------------------------------------
# Multi-attempt runs
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class RunPaths:
    """The three artifacts of one numbered attempt, sharing a stem.

    One stem, one run, three artifacts — that is the whole filing system.
    """

    root: str
    experiment_id: str
    motor: int
    run_id: str

    @property
    def directory(self) -> Path:
        return experiment_dir(self.root, self.experiment_id, self.motor)

    @property
    def csv(self) -> Path:
        return self.directory / f"{self.run_id}.csv"

    @property
    def json(self) -> Path:
        return self.directory / f"{self.run_id}.json"

    @property
    def sr(self) -> Path:
        return self.directory / f"{self.run_id}.sr"

    def as_dict(self) -> dict[str, str]:
        """Goes into the manifest so a manifest can find its own siblings."""
        return {"csv": str(self.csv), "json": str(self.json), "sr": str(self.sr)}


class Storage:
    """Owns the `experiments/` tree under one root."""

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self.root = os.path.abspath(str(root))

    def experiment_dir(self, experiment_id: str, motor: int) -> Path:
        return experiment_dir(self.root, experiment_id, motor)

    def existing_run_ids(self, experiment_id: str, motor: int) -> tuple[str, ...]:
        directory = self.experiment_dir(experiment_id, motor)
        if not directory.is_dir():
            return ()
        found = {p.stem for p in directory.iterdir() if _RUN_RE.match(p.stem)}
        return tuple(sorted(found))

    def next_run_id(self, experiment_id: str, motor: int) -> str:
        """Next free `run-NNNN`, derived from what is on disk.

        Deterministic given the directory, and it never overwrites a previous
        attempt.  Numbering restarts per motor, so `motor-1/run-0002` and
        `motor-3/run-0002` are the second attempt at each — which is what you
        want when putting them on the same axes.
        """
        highest = 0
        for run_id in self.existing_run_ids(experiment_id, motor):
            match = _RUN_RE.match(run_id)
            if match:
                highest = max(highest, int(match.group(1)))
        return f"{RUN_PREFIX}{highest + 1:0{RUN_DIGITS}d}"

    def run_paths(self, experiment_id: str, motor: int,
                  run_id: str | None = None, *, create: bool = False) -> RunPaths:
        validate_experiment_id(experiment_id)
        validate_motor(motor)
        resolved = run_id or self.next_run_id(experiment_id, motor)
        if not _RUN_RE.match(resolved):
            raise StorageError(
                f"run id {resolved!r} must look like {RUN_PREFIX}0001 — "
                "run ids are allocated, not invented"
            )
        if create:
            ensure_experiment_dir(self.root, experiment_id, motor)
        return RunPaths(self.root, experiment_id, motor, resolved)

    # -- discovery -------------------------------------------------------

    def list_motors(self, experiment_id: str) -> tuple[int, ...]:
        validate_experiment_id(experiment_id)
        directory = Path(self.root) / EXPERIMENTS_DIRNAME / experiment_id
        if not directory.is_dir():
            return ()
        motors: list[int] = []
        for child in sorted(directory.iterdir()):
            match = re.fullmatch(r"motor-(\d+)", child.name)
            if match:
                motors.append(int(match.group(1)))
        return tuple(sorted(motors))

    def list_manifests(self, experiment_id: str,
                       motor: int | None = None) -> tuple[Path, ...]:
        """Every manifest under an experiment, sorted — the index of what exists."""
        motors = (motor,) if motor is not None else self.list_motors(experiment_id)
        found: list[Path] = []
        for number in motors:
            directory = self.experiment_dir(experiment_id, number)
            if not directory.is_dir():
                continue
            for child in sorted(directory.iterdir()):
                if child.suffix == ".json":
                    found.append(child)
        return tuple(found)
