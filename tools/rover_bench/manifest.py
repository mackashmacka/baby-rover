"""manifest.py — the reproducibility record.

WHY EVERY RUN GETS A SIDECAR JSON
---------------------------------
Six months from now an interviewer asks "how do you know motor 3's deadband is
higher than motor 1's, and not just that you measured it on a different day
with different firmware?"  The only acceptable answer is a file.

So every run writes a manifest containing, at minimum:

  * when      — UTC ISO 8601 timestamp
  * what code — git SHA of the repo (and whether the tree was dirty), plus the
                firmware version and SHA the Pico reported to `PING`
  * how       — the exact argv, the host, every experiment parameter, and the
                sample rate *actually used* (not the one requested)
  * the units — the measured `ticks_per_rev`, or an explicit `null`

Plain JSON on purpose: greppable, diffable, and readable in six months without
this repo's code.

`ticks_per_rev` IS A TOP-LEVEL FIELD, NOT A PARAMETER
------------------------------------------------------
It is the constant every downstream number scales with, and it is *disputed*:
Adafruit says 14 counts/rev, retailers say 11, giving 1100-1400 per output
revolution, possibly x2 or x4 depending on edge decoding.  A run whose manifest
says `"ticks_per_rev": null` is a run whose speeds are in ticks/s and must
never be quoted in rad/s.  Burying that in `params` would let it be missed.

AN UNKNOWN VALUE IS RECORDED, NOT OMITTED
------------------------------------------
`git_sha: null` beats an absent key.  An absent key reads as "nobody thought
about it"; an explicit null reads as "this run is not fully reproducible", and
that is the honest statement.

COMPARING TWO RUNS IS THE POINT OF THE WHOLE FILE
--------------------------------------------------
Story 1.5 wants four motors characterised *comparably*.  Comparable means: run
the diff, and the only things that changed are the motor number and the clock.
Anything else is a confound, and these functions name it.

  * `diff_manifests(a, b)` — every field that differs, as `{key: (a, b)}`.
  * `is_reproducible(a, b)` — True when nothing that could confound the
    comparison differs.  Timestamps, host, run id and motor number are excused,
    because two runs are never simultaneous and comparing motor 1 to motor 4 is
    the entire exercise.
  * `verify_reproducible(a, b)` — the same judgement as a printable report,
    splitting critical differences from expected ones.
"""

from __future__ import annotations

import getpass
import json
import os
import platform
import shlex
import socket
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from . import __version__

SCHEMA_VERSION = 1

#: Without these a manifest cannot do its job, so `validate_manifest` refuses
#: one that is missing any of them.
REQUIRED_KEYS: tuple[str, ...] = (
    "schema_version",
    "experiment_id",
    "motor",
    "timestamp_utc",
    "argv",
    "params",
    "git_sha",
    "tool_version",
    "ticks_per_rev",
)

#: Fields that differ between any two honest runs of the same experiment.
#: Excused by `is_reproducible`; still reported by `diff_manifests`, because
#: "which of these was taken first" is a question you do ask.
VOLATILE_KEYS: frozenset[str] = frozenset({
    "timestamp_utc", "run_id", "host", "user", "motor", "argv",
    "outputs", "summary", "duration_s", "git_dirty", "notes",
    "python_version", "tool_version",
})


class ManifestError(RuntimeError):
    """A manifest is missing, malformed, or incomplete."""


# --------------------------------------------------------------------------
# Time and provenance
# --------------------------------------------------------------------------

def utc_now_iso(now: datetime | Callable[[], datetime] | None = None) -> str:
    """UTC, ISO 8601, seconds resolution, explicit `Z`.

    CLAUDE.md fixes the timezone at UTC.  A bench whose files are half in local
    time is a bench where you cannot order two runs, and ordering runs is how
    you notice a number drifted after the motor got warm.

    Accepts a `datetime` or a callable returning one, so a frozen test clock
    can be passed either way.
    """
    if callable(now):
        now = now()
    stamp = now or datetime.now(timezone.utc)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return (stamp.astimezone(timezone.utc)
            .replace(microsecond=0).isoformat().replace("+00:00", "Z"))


@dataclass(frozen=True)
class GitState:
    """What the repo looked like when the data was taken."""

    sha: str | None = None
    dirty: bool | None = None

    @classmethod
    def read(cls, repo_root: str,
             runner: Callable[[Sequence[str]], Any] | None = None) -> "GitState":
        sha = _run_git(["rev-parse", "HEAD"], repo_root, runner)
        if not sha:
            return cls(None, None)
        status = _run_git(["status", "--porcelain"], repo_root, runner)
        # None (git could not be asked) and "" (clean tree) are different
        # things, and the difference is the whole point of the field.
        return cls(sha, None if status is None else bool(status))


def _run_git(argv: Sequence[str], repo_root: str,
             runner: Callable[[Sequence[str]], Any] | None = None) -> str | None:
    """Run one git command.  Returns its stripped stdout, or None if it failed.

    **Empty output is not failure.**  `git status --porcelain` prints nothing
    for a clean tree, and collapsing that to None would make a manifest say
    "I do not know whether the tree was clean" when it knows perfectly well
    that it was — which is exactly the claim a reproducibility record exists
    to make.  So `""` and `None` are different return values here.

    Returning None rather than raising: a manifest with `git_sha: null` is
    honest and still useful (it says "taken outside a git checkout"), whereas
    a crash here would block data collection over bookkeeping.
    """
    command = ["git", "-C", str(repo_root), *argv]
    try:
        if runner is not None:
            proc = runner(command)
        else:
            proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
                command, capture_output=True, text=True, check=False, timeout=10
            )
    except Exception:  # noqa: BLE001 - no git, no repo, sandbox: all the same answer
        return None
    if getattr(proc, "returncode", 1) != 0:
        return None
    return (getattr(proc, "stdout", "") or "").strip()


def _hostname() -> str:
    try:
        return socket.gethostname()
    except OSError:  # pragma: no cover - defensive
        return "unknown-host"


def _username() -> str:
    try:
        return getpass.getuser()
    except Exception:  # pragma: no cover - getuser raises in odd environments
        return "unknown-user"


UNCONVERTED_UNITS_NOTE = (
    "ticks_per_rev is NOT measured: speeds in this run are ticks/s and MUST "
    "NOT be quoted in rad/s.  Run `rover-bench ticks-per-rev` first."
)


# --------------------------------------------------------------------------
# Building
# --------------------------------------------------------------------------

def _argv_list(argv: Sequence[str] | str | None) -> list[str]:
    """Normalise argv to a list of words.

    A caller that passes a command *string* would otherwise get
    `["r", "o", "v", ...]` — a manifest that passes validation and records
    nothing whatsoever about how the run was invoked.  A string is split the
    way a shell would split it, which is what the caller meant.
    """
    if argv is None:
        return list(sys.argv)
    if isinstance(argv, str):
        return shlex.split(argv)
    return [str(part) for part in argv]


def build_manifest(experiment_id: str, *,
                   motor: int | None = None,
                   argv: Sequence[str] | None = None,
                   params: Mapping[str, Any] | None = None,
                   git_sha: str | None = None,
                   git_dirty: bool | None = None,
                   now: datetime | Callable[[], datetime] | None = None,
                   kind: str = "",
                   run_id: str = "",
                   firmware: Mapping[str, Any] | None = None,
                   sample_rate_hz: int | None = None,
                   analyser_channels: Sequence[str] = (),
                   ticks_per_rev: float | None = None,
                   ticks_per_rev_source: str = "unmeasured",
                   summary: Mapping[str, Any] | None = None,
                   outputs: Mapping[str, str] | None = None,
                   notes: Sequence[str] = (),
                   duration_s: float | None = None,
                   dry_run: bool = False,
                   host: str | None = None,
                   user: str | None = None) -> dict[str, Any]:
    """Assemble a manifest dict from the pieces the caller already has.

    Everything environmental is injectable, and nothing is read from a global
    at write time, so two runs of the same experiment produce byte-identical
    manifests apart from the fields that genuinely differ.  That is what makes
    `is_reproducible` meaningful rather than decorative.
    """
    note_list = list(notes)
    if ticks_per_rev is None and UNCONVERTED_UNITS_NOTE not in note_list:
        note_list.append(UNCONVERTED_UNITS_NOTE)
    firmware = dict(firmware or {})
    return {
        "schema_version": SCHEMA_VERSION,
        "experiment_id": experiment_id,
        "kind": kind,
        "motor": motor,
        "run_id": run_id,
        "timestamp_utc": utc_now_iso(now),
        "argv": _argv_list(argv),
        "params": dict(params or {}),
        "git_sha": git_sha,
        "git_dirty": git_dirty,
        "firmware_version": firmware.get("firmware_version"),
        "firmware_sha": firmware.get("firmware_sha"),
        "protocol_version": firmware.get("protocol_version"),
        "sample_rate_hz": sample_rate_hz,
        "analyser_channels": list(analyser_channels),
        "ticks_per_rev": ticks_per_rev,
        "ticks_per_rev_source": ticks_per_rev_source,
        "summary": dict(summary or {}),
        "outputs": dict(outputs or {}),
        "notes": note_list,
        "duration_s": duration_s,
        "dry_run": dry_run,
        "host": host if host is not None else _hostname(),
        "user": user if user is not None else _username(),
        "tool_version": __version__,
        "python_version": platform.python_version(),
    }


def validate_manifest(payload: Mapping[str, Any]) -> None:
    """Raise `ManifestError` unless every required key is present.

    Presence, not truthiness: `git_sha: None` is a valid (and honest) value,
    while a *missing* `git_sha` means nobody thought about provenance.
    """
    if not isinstance(payload, Mapping):
        raise ManifestError(f"manifest must be a mapping, got {type(payload).__name__}")
    missing = [key for key in REQUIRED_KEYS if key not in payload]
    if missing:
        raise ManifestError(f"manifest is missing required key(s): {missing}")
    if not isinstance(payload.get("argv"), (list, tuple)):
        raise ManifestError("manifest 'argv' must be a list")
    if not isinstance(payload.get("params"), Mapping):
        raise ManifestError("manifest 'params' must be a mapping")


def units_are_converted(payload: Mapping[str, Any]) -> bool:
    """False means every speed in this run is ticks/s and stays that way."""
    return payload.get("ticks_per_rev") is not None


def describe_manifest(payload: Mapping[str, Any]) -> str:
    """One line, for a bench operator reading terminal output."""
    sha = payload.get("git_sha")
    bits = [
        f"{payload.get('experiment_id')}/motor-{payload.get('motor')}",
        str(payload.get("timestamp_utc")),
        f"fw={payload.get('firmware_version') or '?'}",
        f"git={sha[:8] if sha else '?'}" + ("+dirty" if payload.get("git_dirty") else ""),
    ]
    if payload.get("sample_rate_hz"):
        bits.append(f"{payload['sample_rate_hz']} Hz")
    bits.append(
        f"ticks_per_rev={payload['ticks_per_rev']}" if units_are_converted(payload)
        else "ticks_per_rev=UNMEASURED (speeds are ticks/s)"
    )
    return "  ".join(bits)


# --------------------------------------------------------------------------
# Persistence
# --------------------------------------------------------------------------

def write_manifest(path: str | os.PathLike[str],
                   payload: Mapping[str, Any], *,
                   validate: bool = True) -> Path:
    """Write the manifest, creating parent directories.  Returns the path.

    Validates first, by default.  An incomplete manifest written beside good
    data is worse than no manifest: it looks like a provenance record right up
    until the moment someone needs it, six months later, to defend a number.
    Fail at the write, where the run can still be repeated.
    """
    if validate:
        validate_manifest(payload)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        json.dump(dict(payload), handle, indent=2, sort_keys=True, default=str)
        handle.write("\n")
    return target


def read_manifest(path: str | os.PathLike[str]) -> dict[str, Any]:
    """Read a manifest back.

    Corrupt JSON becomes a `ManifestError` rather than a `JSONDecodeError`, so
    a caller can handle "this record is unusable" in one place.  Missing files
    keep their `FileNotFoundError`, because that is a different problem with a
    different fix.
    """
    target = Path(path)
    try:
        text = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise
    except OSError as exc:  # pragma: no cover - permissions, device errors
        raise ManifestError(f"cannot read {target}: {exc}") from exc
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ManifestError(f"{target} is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ManifestError(f"{target} does not contain a JSON object")
    return payload


# --------------------------------------------------------------------------
# Comparing two runs
# --------------------------------------------------------------------------

def diff_manifests(a: Mapping[str, Any],
                   b: Mapping[str, Any]) -> dict[str, tuple[Any, Any]]:
    """Every field that differs, as `{key: (a_value, b_value)}`.

    Whole-value comparison for nested things like `params`: an experiment
    parameter set is one decision, and reporting "element 7 of duties differs"
    would bury the fact that the sweep was re-planned.  The pair is kept in
    order so the report can say "0.5 -> 0.8" rather than just "changed".
    """
    differences: dict[str, tuple[Any, Any]] = {}
    for key in sorted(set(a) | set(b)):
        left, right = a.get(key), b.get(key)
        if left != right:
            differences[key] = (left, right)
    return differences


def is_reproducible(a: Mapping[str, Any], b: Mapping[str, Any], *,
                    ignore: Sequence[str] = ()) -> bool:
    """True when nothing that could confound a comparison differs.

    `ignore` lets a difference be excused deliberately — comparing runs across
    a firmware fix you *know* does not touch the measurement path, say.
    Excusing it is then a decision someone made, not an oversight.
    """
    excused = VOLATILE_KEYS | set(ignore)
    return not [key for key in diff_manifests(a, b) if key not in excused]


@dataclass(frozen=True)
class Difference:
    key: str
    left: Any
    right: Any
    critical: bool

    def __str__(self) -> str:
        return f"{self.key}: {self.left!r} -> {self.right!r}"


@dataclass(frozen=True)
class ReproReport:
    """The answer to 'can I put these two runs on the same axes?'"""

    left: str
    right: str
    differences: tuple[Difference, ...]

    @property
    def critical(self) -> tuple[Difference, ...]:
        return tuple(d for d in self.differences if d.critical)

    @property
    def informational(self) -> tuple[Difference, ...]:
        return tuple(d for d in self.differences if not d.critical)

    @property
    def comparable(self) -> bool:
        return not self.critical

    def text(self) -> str:
        lines = [f"compare: {self.left}", f"     to: {self.right}", ""]
        if self.comparable:
            lines.append("COMPARABLE — no critical parameter differs.")
        else:
            lines.append(f"NOT COMPARABLE — {len(self.critical)} critical difference(s):")
            lines.extend(f"  !! {d}" for d in self.critical)
        if self.informational:
            lines.append("")
            lines.append("Expected differences (not confounds):")
            lines.extend(f"     {d}" for d in self.informational)
        return "\n".join(lines)


def verify_reproducible(a: Mapping[str, Any], b: Mapping[str, Any], *,
                        ignore: Sequence[str] = ()) -> ReproReport:
    """Diff two manifests and say, in prose a human can act on, whether the
    runs are comparable.

    This is what proves motor 1 and motor 4 were characterised under the same
    conditions — the single claim the whole of Story 1.5 rests on.
    """
    excused = VOLATILE_KEYS | set(ignore)
    differences = tuple(
        Difference(key, left, right, critical=key not in excused)
        for key, (left, right) in diff_manifests(a, b).items()
    )
    return ReproReport(left=_label(a), right=_label(b), differences=differences)


def _label(payload: Mapping[str, Any]) -> str:
    return (f"{payload.get('experiment_id', '?')}/motor-{payload.get('motor', '?')}"
            f" @ {payload.get('timestamp_utc', '?')}")
