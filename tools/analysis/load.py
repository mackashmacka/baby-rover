"""Read and validate a run directory. Refuse to analyse incomparable runs.

A "run" is one directory containing a ``manifest.json`` plus its data files::

    experiments/motor-char/2026-09-02T10-15-00Z_m1_fwd/
        manifest.json      provenance + the parameters the run was taken under
        telemetry.csv      one row per control-loop sample (from the Pico, over UART)
        analyser.csv       one row per logic-analyser sample (from sigrok-cli)
        notes.md           optional, human, free text

WHY THIS MODULE IS PARANOID
---------------------------
Story 1.5 compares four motors that are supposed to be identical. The single
most likely way this campaign produces a *wrong* conclusion is comparing motor
1 against motor 4 when something else also changed -- a different supply
voltage, a different PWM frequency, a different assumed ticks/rev, a firmware
rebuild that changed the duty scaling. The difference then gets attributed to
the motors, the report says "four identical motors differ by 18%", and the
number is garbage.

So: comparing runs whose manifests disagree on a *critical* parameter is an
error, not a warning. It raises. Loudly. With a table of exactly what differs.
``motor_id`` is explicitly exempt, because differing motors is the point.

THE COLUMN CONTRACT
-------------------
Defined here, documented in ``experiments/README.md``. The capture side (the
host CLI stream) and this module must agree, so there is exactly one place
where the contract lives and this is it.

Units are SI and are stated in the column name (CLAUDE.md conventions).
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

# --------------------------------------------------------------------------
# Contract constants
# --------------------------------------------------------------------------

SCHEMA_VERSION = 1

MANIFEST_NAME = "manifest.json"
DEFAULT_TELEMETRY_NAME = "telemetry.csv"
DEFAULT_ANALYSER_NAME = "analyser.csv"

#: Columns every telemetry.csv must have. ``counts`` and not ``omega_rad_s``
#: is required on purpose: ticks-per-rev is UNKNOWN AND DISPUTED (11 vs 14
#: counts/motor-rev), so the firmware cannot honestly report rad/s. It reports
#: what it actually measured -- edges -- and the conversion happens host-side
#: where the uncertainty in ticks_per_output_rev can be propagated.
TELEMETRY_REQUIRED_COLUMNS: tuple[str, ...] = ("t_s", "duty_frac", "counts")

#: Recognised optional telemetry columns. Anything else is passed through
#: untouched (an unknown column is not an error -- forbidding them would make
#: the capture side unable to add a column without a coordinated release).
TELEMETRY_OPTIONAL_COLUMNS: tuple[str, ...] = (
    "omega_rad_s",  # firmware's own estimate, if it has a ticks/rev to use
    "setpoint_rad_s",  # PID target; present for step-response runs
    "current_a",  # motor supply current, if a shunt/meter is logged
    "supply_v",  # measured VM at the driver
    "loop_dt_s",  # firmware's own measure of the last loop period
    "cpu_busy_frac",  # firmware's own compute-duty estimate
)

#: Logic-analyser channel map, from the bench wiring (docs/WIRING.md and the
#: hardware ground truth). A manifest may override it under
#: ``analyser.channel_map``; this is the default when it does not.
DEFAULT_CHANNEL_MAP: dict[str, str] = {
    "D0": "pwm_a",  # GP2  PWMA
    "D1": "ain1",  # GP3  AIN1
    "D2": "ain2",  # GP4  AIN2
    "D3": "stby",  # GP5  STBY
    "D4": "enc_a",  # GP12 encoder A
    "D5": "enc_b",  # GP13 encoder B
    "D6": "loop_tick",  # GP20 toggles once per PID iteration
    "D7": "compute_busy",  # GP21 HIGH while the loop body computes
}

#: Top-level manifest keys that must be present (after alias normalisation).
MANIFEST_REQUIRED_KEYS: tuple[str, ...] = (
    "schema_version",
    "experiment_id",
    "utc_started",
    "params",
)

#: Accepted spellings for the same manifest field. The capture side
#: (``tools/rover_bench``) and this reader were written in parallel by
#: different hands and settled on different names; normalising here is one
#: dict, whereas a flag day across two streams is not. Canonical name first.
MANIFEST_ALIASES: dict[str, tuple[str, ...]] = {
    "utc_started": ("timestamp_utc", "started_utc", "timestamp"),
    "motor_id": ("motor",),
    "git_commit": ("git_sha",),
    "run_id": ("id",),
}

#: Fields the capture side records at the TOP LEVEL that this reader expects
#: nested. ``dotted target -> top-level source``. A ``None`` on the source is
#: not copied: ``rover_bench`` writes ``ticks_per_rev: null`` when nobody has
#: measured it yet, and that must stay a refusal rather than become a guess.
PARAM_FALLBACKS: dict[str, str] = {
    "params.ticks_per_output_rev": "ticks_per_rev",
    "analyser.samplerate_hz": "sample_rate_hz",
}

#: Key under which :func:`normalise_manifest` records what it moved, so
#: :func:`load_run` can warn about it. ``ticks_per_rev`` in particular does not
#: say WHICH SHAFT, and reading a motor-shaft figure as an output-shaft one is
#: a silent factor of 100.
NORMALISATION_KEY = "analysis_normalisation"

#: Accepted spellings for telemetry columns. ``rover_bench``'s storage layer
#: refuses a CSV header with no unit suffix, so it cannot write a column
#: literally called ``counts``; its documented suffix for raw encoder counts is
#: ``_ticks``. Rather than guess at a specific name, any single column ending
#: in ``_ticks`` is accepted as ``counts`` -- and two candidates is an error,
#: not a coin toss.
TELEMETRY_COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "t_s": ("time_s", "timestamp_s"),
}
COUNTS_COLUMN_SUFFIX = "_ticks"

#: A column carrying direction separately from the duty sign. NOT aliased onto
#: anything: its presence means the file was written to the other convention,
#: and :func:`load_telemetry` refuses rather than guessing which one won.
DIRECTION_COLUMN = "direction"

#: Column names known to mean "duty" under a convention this reader does NOT
#: implement. Mapping them onto ``duty_frac`` would be a silent direction bug,
#: so they produce an explanatory refusal instead of an alias.
FOREIGN_DUTY_COLUMNS: tuple[str, ...] = ("duty_frac_cmd", "duty_cmd", "duty_pct")

#: Keys required inside ``manifest["params"]`` -- the ones without which the
#: data cannot be analysed or defended at all.
PARAMS_REQUIRED_KEYS: tuple[str, ...] = (
    "supply_voltage_v",
    "pwm_hz",
    "loop_hz",
    "ticks_per_output_rev",
)

#: Strongly wanted, but their absence is a warning rather than a refusal: if
#: NO run in a comparison states them, none of them differ, so the comparison
#: is still sound. If one run states them and another does not, the
#: comparability check catches it -- absent and present are "different".
PARAMS_RECOMMENDED_KEYS: tuple[str, ...] = (
    "gear_ratio",
    "encoder_decode",
    "direction_convention",
    "ticks_per_output_rev_uncertainty",
)

#: Differ on any of these and the runs are NOT comparable. Raises.
#: Dotted names index into nested dicts.
CRITICAL_PARAMS: tuple[str, ...] = (
    "schema_version",
    "params.supply_voltage_v",
    "params.pwm_hz",
    "params.loop_hz",
    "params.ticks_per_output_rev",
    "params.encoder_decode",
    "params.gear_ratio",
    "params.direction_convention",
)

#: Differ on any of these and the runs are still comparable, but the
#: difference is recorded and goes into the report's provenance section, so a
#: surprising result has somewhere to be blamed.
ADVISORY_PARAMS: tuple[str, ...] = (
    "firmware_version",
    "git_commit",
    "operator",
    "params.supply_kind",
    "params.ambient_c",
    "analyser.samplerate_hz",
)

#: Absolute tolerance for float comparisons of critical params. A measured
#: 6.00 V and 5.98 V supply are the same bench setup; 6.0 V and 5.0 V are not,
#: and the duty->omega gain scales with supply voltage, so the tolerance is
#: tight. Anything not listed compares exactly.
CRITICAL_TOLERANCE: dict[str, float] = {
    "params.supply_voltage_v": 0.05,  # ~1% of 6 V; below meter noise
    "params.ticks_per_output_rev": 0.0,  # a different assumption is a different run
    "params.gear_ratio": 0.0,
    "params.pwm_hz": 0.0,
    "params.loop_hz": 0.0,
}

#: Probe points that must never appear in a channel map. These sit at motor
#: voltage; connecting the 3.3 V-only analyser to them destroys it. An
#: evening (and, elsewhere, an analyser) has already been lost to wiring
#: mistakes, so the rule lives in code and not only in a doc.
FORBIDDEN_PROBE_POINTS = re.compile(
    r"^(ao1|ao2|bo1|bo2|motor_[ab]?out\d*|vm|vmotor)$", re.IGNORECASE
)

#: RP2350 GPIO is not 5 V tolerant, and the encoder hall supply must be 3.3 V.
#: A manifest claiming otherwise describes a bench that is damaging hardware.
MAX_ENCODER_SUPPLY_V = 3.6

#: TB6612FNG absolute maximum VM.
MAX_DRIVER_VM_V = 13.5


# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------


class AnalysisError(Exception):
    """Base class for everything this package raises."""


class RunSchemaError(AnalysisError):
    """A run directory is missing, malformed, or violates the column contract."""


class IncomparableRunsError(AnalysisError):
    """Runs differ in a way that makes comparing them meaningless."""


class SafetyInvariantError(AnalysisError):
    """A manifest describes a bench setup that damages hardware."""


# --------------------------------------------------------------------------
# Run object
# --------------------------------------------------------------------------


@dataclass
class Run:
    """One loaded, validated run directory."""

    path: Path
    manifest: dict[str, Any]
    telemetry: pd.DataFrame
    analyser: pd.DataFrame | None = None
    warnings: list[str] = field(default_factory=list)

    # -- convenience accessors; all read-only views onto the manifest --

    @property
    def run_id(self) -> str:
        return str(self.manifest["run_id"])

    @property
    def experiment_id(self) -> str:
        return str(self.manifest["experiment_id"])

    @property
    def motor_id(self) -> Any:
        """Which motor this run is of, or ``None`` for a run that is not
        per-motor (a bench-verification capture, say).

        ``None`` is a real grouping key: :func:`group_by_motor` puts every
        unlabelled run in one bucket, so four unlabelled motor-char runs would
        be fitted as if they were one motor. :func:`load_run` therefore warns
        when the key is absent rather than leaving that to be discovered in a
        figure.
        """
        return self.manifest.get("motor_id")

    @property
    def params(self) -> dict[str, Any]:
        return dict(self.manifest["params"])

    @property
    def ticks_per_output_rev(self) -> float:
        return float(self.manifest["params"]["ticks_per_output_rev"])

    @property
    def ticks_per_output_rev_uncertainty(self) -> float:
        """Uncertainty on ticks/rev, defaulting to the full 1100-1400 spread.

        If the manifest does not state one, the honest default is the width of
        the unresolved literature disagreement, not zero. A wrong-but-precise
        constant is how a whole campaign silently rescales.
        """
        params = self.manifest["params"]
        stated = params.get("ticks_per_output_rev_uncertainty")
        if stated is not None:
            return float(stated)
        return 150.0  # half of the 1100..1400 spread; see memory/n20-motors.md

    @property
    def loop_hz(self) -> float:
        return float(self.manifest["params"]["loop_hz"])

    @property
    def analyser_samplerate_hz(self) -> float | None:
        rate = self.manifest.get("analyser", {}).get("samplerate_hz")
        return None if rate is None else float(rate)

    def channel(self, name: str) -> pd.Series:
        """Return one named analyser channel as a Series of 0/1 levels."""
        if self.analyser is None:
            raise RunSchemaError(f"{self.path}: no analyser capture in this run")
        if name not in self.analyser.columns:
            raise RunSchemaError(
                f"{self.path}: analyser capture has no channel {name!r}; "
                f"present: {sorted(c for c in self.analyser.columns if c != 't_s')}"
            )
        return self.analyser[name]

    def label(self) -> str:
        """Short human label used in plot legends and report tables."""
        return f"motor {self.motor_id} / {self.run_id}"


# --------------------------------------------------------------------------
# Manifest helpers
# --------------------------------------------------------------------------


_MISSING = object()


def dotted_get(mapping: Mapping[str, Any], dotted: str, default: Any = _MISSING) -> Any:
    """Fetch ``a.b.c`` out of nested dicts. Returns ``default`` if absent."""
    node: Any = mapping
    for part in dotted.split("."):
        if not isinstance(node, Mapping) or part not in node:
            return default
        node = node[part]
    return node


def _values_differ(a: Any, b: Any, tolerance: float) -> bool:
    """True if two manifest values should be treated as different."""
    if a is _MISSING or b is _MISSING:
        return a is not b
    if isinstance(a, bool) or isinstance(b, bool):
        return a != b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        if math.isnan(float(a)) and math.isnan(float(b)):
            return False
        return abs(float(a) - float(b)) > tolerance
    return a != b


def _fmt(value: Any) -> str:
    return "<absent>" if value is _MISSING else repr(value)


def normalise_manifest(raw: Mapping[str, Any], *, default_run_id: str = "") -> dict[str, Any]:
    """Rename accepted aliases onto the canonical keys. Non-destructive.

    The original spellings are left in place as well, so nothing is lost and a
    manifest round-trips through the analysis side unchanged.
    """
    manifest = dict(raw)
    for canonical, aliases in MANIFEST_ALIASES.items():
        if canonical in manifest:
            continue
        for alias in aliases:
            if alias in manifest:
                manifest[canonical] = manifest[alias]
                break
    if "run_id" not in manifest and default_run_id:
        # A run's directory name is a perfectly good identity, and inventing a
        # different one would break the link between a report row and a folder.
        manifest["run_id"] = default_run_id

    moved: dict[str, str] = {}
    for dotted, source in PARAM_FALLBACKS.items():
        if dotted_get(manifest, dotted) is not _MISSING:
            continue
        value = manifest.get(source, _MISSING)
        if value is _MISSING or value is None:
            continue
        section, _, leaf = dotted.rpartition(".")
        if section not in manifest:
            if section != "analyser":
                # Never invent a params block: a manifest with no params at all
                # should fail on that, with that message.
                continue
            manifest[section] = {}
        if isinstance(manifest[section], dict):
            manifest[section][leaf] = value
            moved[dotted] = source
    if moved:
        manifest[NORMALISATION_KEY] = moved
    return manifest


def read_manifest(run_dir: Path | str) -> dict[str, Any]:
    """Read and validate ``manifest.json`` from a run directory."""
    run_dir = Path(run_dir)
    manifest_path = run_dir / MANIFEST_NAME
    if not manifest_path.is_file():
        raise RunSchemaError(
            f"{run_dir}: no {MANIFEST_NAME}. A run directory without a manifest "
            f"has no provenance, so it cannot be analysed or compared."
        )
    try:
        raw = json.loads(manifest_path.read_text())
    except json.JSONDecodeError as exc:
        raise RunSchemaError(f"{manifest_path}: not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise RunSchemaError(f"{manifest_path}: top level must be a JSON object")

    manifest = normalise_manifest(raw, default_run_id=run_dir.name)

    missing = [k for k in MANIFEST_REQUIRED_KEYS if k not in manifest]
    if missing:
        hints = {
            k: MANIFEST_ALIASES[k] for k in missing if k in MANIFEST_ALIASES
        }
        extra = f" (accepted aliases: {hints})" if hints else ""
        raise RunSchemaError(
            f"{manifest_path}: missing required key(s): {', '.join(missing)}{extra}"
        )

    version = manifest["schema_version"]
    if not isinstance(version, int) or isinstance(version, bool):
        raise RunSchemaError(
            f"{manifest_path}: schema_version must be an integer, got {version!r}"
        )
    if version > SCHEMA_VERSION:
        # Newer than this reader: refuse. Older: read it, because the whole
        # point of a version number is that old data stays readable.
        raise RunSchemaError(
            f"{manifest_path}: schema_version {version} is newer than this "
            f"reader understands ({SCHEMA_VERSION}). Update tools/analysis "
            f"rather than guessing at a format it has never seen."
        )

    params = manifest["params"]
    if not isinstance(params, dict):
        raise RunSchemaError(f"{manifest_path}: 'params' must be a JSON object")
    missing_params = [k for k in PARAMS_REQUIRED_KEYS if k not in params]
    if missing_params:
        raise RunSchemaError(
            f"{manifest_path}: params missing required key(s): "
            f"{', '.join(missing_params)}. Without these the run cannot be "
            f"converted to SI or compared with another run. See the manifest "
            f"contract in experiments/README.md."
        )

    check_safety_invariants(manifest, source=str(manifest_path))
    return manifest


def check_safety_invariants(manifest: Mapping[str, Any], source: str = "manifest") -> None:
    """Refuse manifests describing a bench that damages hardware.

    These are the hardware invariants from CLAUDE.md / docs/HARDWARE.md that a
    *manifest* can actually witness. Encoding them here means a bad bench
    setup is caught the first time its data is opened, not after the smoke.
    """
    params = manifest.get("params", {})

    encoder_v = params.get("encoder_supply_v")
    if encoder_v is not None and float(encoder_v) > MAX_ENCODER_SUPPLY_V:
        raise SafetyInvariantError(
            f"{source}: encoder_supply_v={encoder_v} V. The hall sensors run at "
            f"3.3 V and RP2350 GPIO is NOT 5 V tolerant "
            f"(max {MAX_ENCODER_SUPPLY_V} V). Fix the bench, not the manifest."
        )

    vm = params.get("supply_voltage_v")
    if vm is not None and float(vm) > MAX_DRIVER_VM_V:
        raise SafetyInvariantError(
            f"{source}: supply_voltage_v={vm} V exceeds the TB6612FNG VM maximum "
            f"of {MAX_DRIVER_VM_V} V. A 4S LiPo (16.8 V) must go through the "
            f"regulator before it reaches the driver."
        )

    channel_map = manifest.get("analyser", {}).get("channel_map", {}) or {}
    for channel, name in channel_map.items():
        if FORBIDDEN_PROBE_POINTS.match(str(name)):
            raise SafetyInvariantError(
                f"{source}: analyser channel {channel} is mapped to {name!r}. "
                f"AO1/AO2/BO1/BO2 sit at motor voltage and will destroy the "
                f"3.3 V analyser. Never probe the H-bridge outputs."
            )


# --------------------------------------------------------------------------
# CSV loading
# --------------------------------------------------------------------------


def _read_csv(path: Path) -> pd.DataFrame:
    """Read a CSV, tolerating sigrok-cli's ``;`` comment header."""
    try:
        frame = pd.read_csv(path, comment=";", skipinitialspace=True)
    except pd.errors.EmptyDataError as exc:
        raise RunSchemaError(f"{path}: file is empty") from exc
    except OSError as exc:
        raise RunSchemaError(f"{path}: cannot read: {exc}") from exc
    frame.columns = [str(c).strip() for c in frame.columns]
    return frame


def resolve_telemetry_columns(frame: pd.DataFrame, source: str = "telemetry") -> pd.DataFrame:
    """Rename accepted column spellings onto the canonical contract names.

    Two things are bridged here, both because ``tools/rover_bench``'s storage
    layer and this reader were written in parallel:

    * simple aliases (``time_s`` -> ``t_s``), and
    * the encoder-count column. That writer *refuses* a header with no unit
      suffix, so it cannot emit a column called ``counts``; its documented
      suffix for raw counts is ``_ticks``. Any single ``*_ticks`` column is
      taken as ``counts``. Two of them is an error -- picking one would be a
      coin toss on the number every downstream figure scales with.
    """
    renames: dict[str, str] = {}
    for canonical, aliases in TELEMETRY_COLUMN_ALIASES.items():
        if canonical in frame.columns:
            continue
        for alias in aliases:
            if alias in frame.columns:
                renames[alias] = canonical
                break

    if "counts" not in frame.columns:
        candidates = [
            c for c in frame.columns
            if c.endswith(COUNTS_COLUMN_SUFFIX) and not c.endswith("_per_s")
        ]
        if len(candidates) > 1:
            raise RunSchemaError(
                f"{source}: {len(candidates)} columns could be the encoder count "
                f"({', '.join(candidates)}). Name the right one 'counts' in the "
                f"CSV so the choice is recorded, not guessed."
            )
        if len(candidates) == 1:
            renames[candidates[0]] = "counts"

    return frame.rename(columns=renames) if renames else frame


def load_telemetry(path: Path | str) -> pd.DataFrame:
    """Load and validate ``telemetry.csv`` against the column contract."""
    path = Path(path)
    if not path.is_file():
        raise RunSchemaError(f"{path}: telemetry file not found")
    frame = _read_csv(path)
    frame = resolve_telemetry_columns(frame, source=str(path))

    missing = [c for c in TELEMETRY_REQUIRED_COLUMNS if c not in frame.columns]
    if missing:
        foreign = [c for c in FOREIGN_DUTY_COLUMNS if c in frame.columns]
        hint = ""
        if "duty_frac" in missing and foreign:
            # Deliberately NOT auto-renamed: docs/experiments/motor-char.md
            # §6.1 defines duty_frac_cmd as 0..1 with direction in its own
            # column, so the rename would quietly discard the sign.
            hint = (
                f" Found {', '.join(foreign)} instead — that is a different "
                f"convention (unsigned duty plus a separate direction column, "
                f"docs/experiments/motor-char.md §6.1), and renaming it here "
                f"would silently drop the commanded direction. Reconcile the "
                f"two specs first."
            )
        raise RunSchemaError(
            f"{path}: missing required column(s): {', '.join(missing)}. "
            f"Contract: {', '.join(TELEMETRY_REQUIRED_COLUMNS)} "
            f"(see experiments/README.md).{hint}"
        )
    if len(frame) == 0:
        raise RunSchemaError(f"{path}: no data rows")

    for column in TELEMETRY_REQUIRED_COLUMNS:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
        if frame[column].isna().any():
            bad = int(frame[column].isna().sum())
            raise RunSchemaError(
                f"{path}: column {column!r} has {bad} non-numeric/empty value(s). "
                f"A gap in a time series silently becomes a wrong derivative."
            )

    t = frame["t_s"].to_numpy()
    if (t[1:] < t[:-1]).any():
        first = int((t[1:] < t[:-1]).argmax()) + 1
        raise RunSchemaError(
            f"{path}: t_s goes backwards at row {first} "
            f"({t[first - 1]} -> {t[first]}). Timestamps must be monotonic."
        )

    # The sign of duty_frac IS the commanded direction here. A capture that
    # carries direction in its own column is written to a different convention
    # (docs/experiments/motor-char.md §6.1: unsigned `duty_frac_cmd` 0..1 plus
    # `direction` in {fwd, rev}), and reading it with this one folds every
    # reverse point onto the forward curve -- the reverse deadband and gain
    # would simply vanish, and the forward fit would be run over two
    # directions' points at once. Refuse, rather than pick.
    if DIRECTION_COLUMN in frame.columns and not (frame["duty_frac"] < 0).any():
        raise RunSchemaError(
            f"{path}: has a {DIRECTION_COLUMN!r} column and no negative "
            f"duty_frac. This reader's contract is a SIGNED duty_frac in "
            f"[-1, 1] (positive = AIN1 HIGH / AIN2 LOW); a separate direction "
            f"column is the other convention in this repo "
            f"(docs/experiments/motor-char.md §6.1). Reading one as the other "
            f"folds reverse onto forward silently. Pick one convention, write "
            f"it into experiments/README.md, and convert the CSV."
        )

    duty = frame["duty_frac"].to_numpy()
    if (abs(duty) > 1.0 + 1e-9).any():
        worst = float(abs(duty).max())
        raise RunSchemaError(
            f"{path}: duty_frac reaches {worst:.3f}; it is a fraction in "
            f"[-1, 1] where the sign is the commanded direction. A percentage "
            f"in this column would scale every gain by 100."
        )

    return frame


def load_analyser(
    path: Path | str,
    *,
    samplerate_hz: float | None = None,
    channel_map: Mapping[str, str] | None = None,
) -> pd.DataFrame:
    """Load a logic-analyser capture and normalise it.

    Accepts either of the two shapes that turn up in practice:

    * a ``t_s`` column plus channel columns, or
    * bare channel columns (raw ``sigrok-cli -O csv``), in which case
      ``samplerate_hz`` is required and ``t_s`` is synthesised from the row
      index. Discovering the achievable sample rate is a runtime job for
      ``sigrok-cli --scan``; this module only records what it was told.

    Channel columns named ``D0``..``D7`` are renamed via ``channel_map``
    (default :data:`DEFAULT_CHANNEL_MAP`). Columns already carrying logical
    names are left alone.
    """
    path = Path(path)
    if not path.is_file():
        raise RunSchemaError(f"{path}: analyser file not found")
    frame = _read_csv(path)
    if len(frame) == 0:
        raise RunSchemaError(f"{path}: no data rows")

    mapping = dict(DEFAULT_CHANNEL_MAP if channel_map is None else channel_map)
    for channel, name in mapping.items():
        if FORBIDDEN_PROBE_POINTS.match(str(name)):
            raise SafetyInvariantError(
                f"{path}: channel {channel} mapped to {name!r} -- that is an "
                f"H-bridge output at motor voltage. Never probe it."
            )
    frame = frame.rename(columns={k: v for k, v in mapping.items() if k in frame.columns})

    # The capture's OWN header can also name a forbidden probe point -- sigrok
    # labels channels from whatever the operator typed, so a file with an
    # `AO1` column is a record of the analyser having been connected to an
    # H-bridge output. Refuse it here too: the map is not the only witness.
    for column in frame.columns:
        if FORBIDDEN_PROBE_POINTS.match(str(column)):
            raise SafetyInvariantError(
                f"{path}: the capture has a channel named {column!r}. "
                f"AO1/AO2/BO1/BO2 sit at motor voltage and destroy the 3.3 V "
                f"analyser. Check the probe leads before capturing again; if the "
                f"column is misnamed, rename it in the CSV."
            )

    if "t_s" not in frame.columns:
        if samplerate_hz is None or samplerate_hz <= 0:
            raise RunSchemaError(
                f"{path}: no 't_s' column and no positive analyser.samplerate_hz "
                f"in the manifest, so sample times are unknowable. Every timing "
                f"number (loop jitter, CPU duty, PWM frequency) depends on it."
            )
        frame.insert(0, "t_s", frame.index.to_numpy(dtype=float) / float(samplerate_hz))
    else:
        frame["t_s"] = pd.to_numeric(frame["t_s"], errors="coerce")
        if frame["t_s"].isna().any():
            raise RunSchemaError(f"{path}: 't_s' contains non-numeric values")

    for column in frame.columns:
        if column == "t_s":
            continue
        numeric = pd.to_numeric(frame[column], errors="coerce")
        if numeric.isna().any():
            raise RunSchemaError(
                f"{path}: channel {column!r} contains non-numeric values; "
                f"digital channels must be 0/1."
            )
        frame[column] = (numeric != 0).astype("int8")

    return frame


# --------------------------------------------------------------------------
# The run loader
# --------------------------------------------------------------------------


#: Telemetry filenames tried, in order, when the manifest does not name one.
#: ``samples.csv`` is what ``tools/rover_bench``'s storage layer writes.
TELEMETRY_FALLBACK_NAMES: tuple[str, ...] = (DEFAULT_TELEMETRY_NAME, "samples.csv")


def _resolve_telemetry_path(run_dir: Path, files: Mapping[str, Any]) -> Path:
    """Find the telemetry CSV: manifest first, then the known conventions."""
    named = files.get("telemetry")
    if named:
        return run_dir / str(named)
    for candidate in TELEMETRY_FALLBACK_NAMES:
        if (run_dir / candidate).is_file():
            return run_dir / candidate
    raise RunSchemaError(
        f"{run_dir}: no telemetry CSV. Name it in the manifest as "
        f'files.telemetry, or use one of {", ".join(TELEMETRY_FALLBACK_NAMES)}.'
    )


def load_run(run_dir: Path | str, *, require_analyser: bool = False) -> Run:
    """Load one run directory: manifest, telemetry, and analyser if present."""
    run_dir = Path(run_dir)
    if not run_dir.is_dir():
        raise RunSchemaError(f"{run_dir}: not a directory")

    manifest = read_manifest(run_dir)
    files = manifest.get("files", {}) or {}

    warnings: list[str] = []
    telemetry = load_telemetry(_resolve_telemetry_path(run_dir, files))

    absent = [k for k in PARAMS_RECOMMENDED_KEYS if k not in manifest["params"]]
    if absent:
        warnings.append(
            f"{run_dir.name}: manifest params omit {', '.join(absent)}; "
            f"comparisons against a run that DOES state them will be refused"
        )

    if "params.ticks_per_output_rev" in manifest.get(NORMALISATION_KEY, {}):
        warnings.append(
            f"{run_dir.name}: ticks/rev was taken from the top-level "
            f"'ticks_per_rev' ({manifest['params']['ticks_per_output_rev']}), "
            f"which does not say WHICH SHAFT. This reader assumes the OUTPUT "
            f"shaft. If it is a motor-shaft figure, every speed here is wrong "
            f"by the gear ratio (100x). Write params.ticks_per_output_rev."
        )

    if manifest.get("motor_id") is None:
        warnings.append(
            f"{run_dir.name}: manifest states no motor_id, so this run groups "
            f"under motor 'None' with every other unlabelled run. Four "
            f"unlabelled runs would be fitted as if they were one motor."
        )

    analyser_name = files.get("analyser", DEFAULT_ANALYSER_NAME)
    analyser_path = run_dir / analyser_name
    analyser: pd.DataFrame | None = None
    stated_map = dotted_get(manifest, "analyser.channel_map", None)
    if analyser_path.is_file():
        analyser = load_analyser(
            analyser_path,
            samplerate_hz=dotted_get(manifest, "analyser.samplerate_hz", None),
            channel_map=stated_map,
        )
        if stated_map is None and any(
            c in analyser.columns for c in DEFAULT_CHANNEL_MAP.values()
        ):
            # docs/WIRING.md carries TWO channel maps that differ at exactly
            # D6/D7: §8 (failsafe) has UART TX/RX there, §10.2 (characterisation)
            # has LOOP_TICK/COMPUTE_BUSY. Assuming the wrong one turns UART
            # traffic into a "loop period". WIRING §10.3: "a .sr file whose D6
            # could be either signal is not evidence of anything."
            warnings.append(
                f"{run_dir.name}: the manifest states no analyser.channel_map, "
                f"so D0-D7 were read with the characterisation-bench default "
                f"(D6=loop_tick GP20, D7=compute_busy GP21, docs/WIRING.md "
                f"§10.2). A capture taken under the §8 map has UART TX/RX on "
                f"D6/D7 and its 'loop timing' would be nonsense. Write "
                f"analyser.channel_map into the manifest."
            )
    elif require_analyser:
        raise RunSchemaError(
            f"{run_dir}: no analyser capture ({analyser_name}), but one was required. "
            f"Loop jitter and CPU duty cannot be measured without D6/D7."
        )
    else:
        warnings.append(f"no analyser capture in {run_dir.name}; timing metrics unavailable")

    return Run(path=run_dir, manifest=manifest, telemetry=telemetry,
               analyser=analyser, warnings=warnings)


def find_run_dirs(root: Path | str) -> list[Path]:
    """Every directory under ``root`` (inclusive) that holds a manifest."""
    root = Path(root)
    if not root.is_dir():
        raise RunSchemaError(f"{root}: not a directory")
    found = sorted(p.parent for p in root.rglob(MANIFEST_NAME))
    return found


def load_runs(
    run_dirs: Iterable[Path | str], *, require_comparable: bool = True
) -> list[Run]:
    """Load several runs. By default, refuse to return an incomparable set."""
    runs = [load_run(d) for d in run_dirs]
    if require_comparable:
        assert_comparable(runs)
    return runs


def load_campaign(
    root: Path | str, *, require_comparable: bool = True
) -> list[Run]:
    """Load every run under ``root``, sorted by motor then run id."""
    runs = load_runs(find_run_dirs(root), require_comparable=require_comparable)
    return sorted(runs, key=lambda r: (str(r.motor_id), r.run_id))


# --------------------------------------------------------------------------
# Comparability -- the loud bit
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Difference:
    """One manifest key on which a set of runs disagrees."""

    key: str
    values: dict[str, Any]  # run_id -> value

    def render(self) -> str:
        cells = ", ".join(f"{rid}={_fmt(v)}" for rid, v in self.values.items())
        return f"  {self.key}: {cells}"


def comparison_labels(runs: Sequence[Run]) -> list[str]:
    """One unique label per run, for keying the difference table.

    ``run_id`` alone is NOT safe here. Two runs can carry the same id -- the
    capture side falls back to the directory name, and
    ``experiments/<experiment>/motor-1/`` is the same name in two different
    experiments. Keying a dict by a duplicated id silently collapses two runs
    into one entry, and a collapsed entry cannot disagree with itself: the
    comparability check would pass a campaign whose bench voltage changed.
    Duplicates therefore get their directory (and, if that repeats too, their
    position) appended.
    """
    counts: dict[str, int] = {}
    for run in runs:
        counts[run.run_id] = counts.get(run.run_id, 0) + 1
    labels: list[str] = []
    used: set[str] = set()
    for index, run in enumerate(runs):
        label = run.run_id if counts[run.run_id] == 1 else f"{run.run_id} ({run.path.name})"
        if label in used:
            label = f"{label} #{index}"
        used.add(label)
        labels.append(label)
    return labels


def _collect_differences(
    runs: Sequence[Run], keys: Sequence[str], tolerances: Mapping[str, float]
) -> list[Difference]:
    differences: list[Difference] = []
    labels = comparison_labels(runs)
    for key in keys:
        values = {
            label: dotted_get(run.manifest, key)
            for label, run in zip(labels, runs, strict=True)
        }
        reference_id, reference = next(iter(values.items()))
        tolerance = float(tolerances.get(key, 0.0))
        if any(
            _values_differ(reference, value, tolerance)
            for rid, value in values.items()
            if rid != reference_id
        ):
            differences.append(Difference(key=key, values=values))
    return differences


def compare_manifests(runs: Sequence[Run]) -> tuple[list[Difference], list[Difference]]:
    """Return ``(critical_differences, advisory_differences)`` for a run set."""
    if len(runs) < 2:
        return [], []
    critical = _collect_differences(runs, CRITICAL_PARAMS, CRITICAL_TOLERANCE)
    advisory = _collect_differences(runs, ADVISORY_PARAMS, {})
    return critical, advisory


def assert_comparable(runs: Sequence[Run]) -> list[Difference]:
    """Raise :class:`IncomparableRunsError` if these runs must not be compared.

    Returns the advisory (non-fatal) differences so a caller can put them in
    the report's provenance section.

    ``motor_id`` and ``run_id`` are deliberately NOT compared: comparing
    different motors is the entire point of Story 1.5. Everything else about
    the bench has to have been held still, or the difference between motors is
    not a difference between motors.
    """
    critical, advisory = compare_manifests(runs)
    if critical:
        listed = "\n".join(d.render() for d in critical)
        run_list = ", ".join(f"{r.run_id} (motor {r.motor_id})" for r in runs)
        raise IncomparableRunsError(
            "Refusing to compare these runs -- the bench changed between them, "
            "so any difference cannot be attributed to the motors.\n"
            f"Runs: {run_list}\n"
            f"Critical parameter(s) that differ:\n{listed}\n"
            "Fix: re-run the odd one out under the same conditions, or analyse "
            "them separately. If you are certain the difference is harmless, "
            "pass require_comparable=False and say so explicitly in the report."
        )
    return advisory


def group_by_motor(runs: Iterable[Run]) -> dict[Any, list[Run]]:
    """Group runs by ``motor_id``, preserving load order within each motor."""
    grouped: dict[Any, list[Run]] = {}
    for run in runs:
        grouped.setdefault(run.motor_id, []).append(run)
    return grouped


def iter_manifest_rows(runs: Iterable[Run]) -> Iterator[dict[str, Any]]:
    """Flatten manifests into rows for the report's provenance table."""
    for run in runs:
        row: dict[str, Any] = {
            "run_id": run.run_id,
            "motor_id": run.motor_id,
            "utc_started": run.manifest.get("utc_started"),
            "experiment_id": run.experiment_id,
            "git_commit": run.manifest.get("git_commit", ""),
            "firmware_version": run.manifest.get("firmware_version", ""),
            "samples": len(run.telemetry),
        }
        for key, value in run.params.items():
            row[f"params.{key}"] = value
        yield row
