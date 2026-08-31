"""experiment.py — the experiments, expressed as data plus pure maths.

WHY "AS DATA, NOT CODE PATHS"
-----------------------------
Every experiment here is a *spec* dataclass (what to do) and a short runner
(do it).  Nothing about an experiment is expressed as a branch in the CLI.
Three consequences, all of which the project needs:

  1. The spec serialises straight into the manifest, so "what were the dwell
     times on motor 3?" is answered by a file, not by reading git history.
  2. `manifest.is_reproducible` can compare two runs parameter by parameter,
     because the parameters are values rather than argparse flags scattered
     through `main`.
  3. The analysis is pure functions on lists of numbers, so the interesting
     maths (step metrics, jitter statistics) is testable with no Pico.

THE UNITS RULE — THE MOST IMPORTANT THING IN THIS FILE
------------------------------------------------------
`ticks_per_rev` is **disputed and unmeasured**: Adafruit says 14 counts/rev,
retailer listings say 11, so somewhere between 1100 and 1400 per *output*
revolution, possibly x2 or x4 depending on how edges are decoded.  Every rad/s
number in this project scales directly with it.

Therefore: **speeds are ticks/s until somebody measures it.**  `TicksPerRev`
carries either a measured value with a named source, or an explicit
"unmeasured".  When it is unmeasured:

  * no `omega_rad_s` column is written at all — not zero, not NaN, absent, so
    it cannot be plotted by accident;
  * the manifest records `ticks_per_rev: null`;
  * every result carries a note saying the figures are un-converted.

A guessed constant that silently produces plausible rad/s numbers is worse than
no number, because it is not falsifiable by looking at the output.

WHY THE ANALYSER CARES ABOUT GP20/GP21
---------------------------------------
`jitter` is what makes this a *PID* characterisation rather than a motor
characterisation.  Firmware toggles GP20 once per control-loop iteration and
holds GP21 high while the loop body computes.  From those two pins you get the
loop period distribution (is it really 100 Hz, or 100 Hz on average with a
15 ms tail?) and the CPU duty (how much headroom before the loop overruns).  A
PID tuned on a loop that jitters is a PID tuned on a lie.

NOTE ON THE LOOP-TICK PIN: it *toggles* once per iteration, so one loop period
is the interval between **consecutive edges of either polarity**, not between
rising edges.  Measuring rising-to-rising would report exactly double the
period — the kind of factor of two that survives all the way into a report.

FAILURE IS DATA
---------------
`run()` raises only for a spec it refuses to attempt (bad motor, out-of-range
duty).  A failure *during* measurement is recorded in the manifest and returned
as `RunResult.ok == False`.  A failed run is what a debugging narrative is made
of; losing its manifest loses the only record of what was attempted.
"""

from __future__ import annotations

import math
import os
import statistics
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from . import manifest as manifest_mod
from . import storage as storage_mod
from .analyser import (
    Analyser,
    COMPUTE_BUSY_CHANNEL,
    ChannelTrace,
    DecodedCapture,
    LOOP_TICK_CHANNEL,
    decode_srzip,
)
from .link import Clock, EncoderSample, Link, LinkError, RealClock, as_link
from .safety import (
    Check,
    DriveGuard,
    Limits,
    SafetyViolation,
    assert_ok,
    check_duty,
    check_motor,
    preflight_run,
)

FORWARD = "forward"
REVERSE = "reverse"
BOTH_DIRECTIONS: tuple[str, ...] = (FORWARD, REVERSE)

#: One id per kind, deliberately distinct: the layout puts one experiment's
#: artifacts in one directory, so two kinds sharing an id would overwrite each
#: other's manifest.  `motor-char` stays the sweep's id because that is the
#: row already planned in experiments/REGISTRY.md.
DEFAULT_EXPERIMENT_IDS: dict[str, str] = {
    "deadband": "motor-deadband",
    "sweep": "motor-char",
    "step": "pid-step",
    "ticks_per_rev": "ticks-per-rev",
    "jitter": "loop-jitter",
    "hold": "motor-hold",
}

STORY_IDS: dict[str, str] = {
    "deadband": "1.5",
    "sweep": "1.5",
    "step": "1.6",
    "ticks_per_rev": "1.4",
    "jitter": "1.6",
    "hold": "1.5",
}


def direction_sign(direction: str) -> int:
    if direction == FORWARD:
        return 1
    if direction == REVERSE:
        return -1
    raise ValueError(f"direction must be {FORWARD!r} or {REVERSE!r}, got {direction!r}")


# --------------------------------------------------------------------------
# Units
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class TicksPerRev:
    """Encoder counts per **output** shaft revolution — measured, or absent.

    `source` is free text that must say where the number came from, e.g.
    `"measured: ticks-per-rev/motor-0"`.  There is deliberately no way to
    construct this from a datasheet figure without saying so out loud.
    """

    value: float | None = None
    source: str = "unmeasured"
    stdev: float | None = None

    @property
    def measured(self) -> bool:
        return self.value is not None and self.value > 0

    @classmethod
    def unmeasured(cls) -> "TicksPerRev":
        return cls(None, "unmeasured", None)

    @classmethod
    def from_measurement(cls, value: float, source: str,
                         stdev: float | None = None) -> "TicksPerRev":
        if value <= 0:
            raise ValueError(f"ticks_per_rev must be positive, got {value}")
        return cls(float(value), source, stdev)

    def omega_rad_s(self, ticks_per_s: float) -> float | None:
        """ticks/s -> rad/s, or None when the constant is unmeasured.

        Returning None rather than raising: callers write a column only when
        there is something honest to put in it, and None makes "there is no
        number" impossible to confuse with "the number is zero".
        """
        if not self.measured:
            return None
        return 2.0 * math.pi * ticks_per_s / float(self.value)


UNCONVERTED_NOTE = manifest_mod.UNCONVERTED_UNITS_NOTE

#: The other half of the units problem, and the half this tool cannot fix.
#: `SETRPS` takes rad/s, and the FIRMWARE converts that back to ticks using its
#: own `ticks_per_output_rev` (firmware/src/protocol.c) — a compile-time
#: PLACEHOLDER with no command to change it.  So measuring ticks-per-rev on the
#: host is necessary but NOT sufficient: until both ends carry the same
#: constant, a closed-loop setpoint is silently scaled by
#: measured/firmware_placeholder, and the loop servos to a speed nobody asked
#: for.  The host cannot detect this — there is nothing on the wire that
#: reports the firmware's constant — so it is recorded rather than checked.
CLOSED_LOOP_SETPOINT_NOTE = (
    "CLOSED-LOOP SETPOINT IS NOT VERIFIABLE FROM THE HOST: SETRPS is in rad/s "
    "and the firmware converts it back to ticks with its own "
    "ticks_per_output_rev, which is a compile-time placeholder with no command "
    "to set it.  The realised setpoint is this one scaled by "
    "(host ticks_per_rev / firmware ticks_per_output_rev).  Do not quote a "
    "closed-loop speed until the two constants are reconciled."
)


# --------------------------------------------------------------------------
# Pure analysis.  No hardware, no I/O.
# --------------------------------------------------------------------------

def describe(values: Sequence[float], unit: str = "") -> dict[str, Any]:
    """mean / stdev / min / max / n, with the unit in every key.

    `stdev` is the *sample* standard deviation and is `0.0` for a single
    value — reporting spread as 0 from n=1 would be a lie, so `n_count` is
    always reported beside it and the reader can judge.
    """
    suffix = f"_{unit}" if unit else ""
    if not values:
        return {f"mean{suffix}": None, f"stdev{suffix}": None,
                f"min{suffix}": None, f"max{suffix}": None, "n_count": 0}
    data = [float(v) for v in values]
    return {
        f"mean{suffix}": statistics.fmean(data),
        f"stdev{suffix}": statistics.stdev(data) if len(data) > 1 else 0.0,
        f"min{suffix}": min(data),
        f"max{suffix}": max(data),
        "n_count": len(data),
    }


def steady_state(values: Sequence[float], discard_frac: float = 0.5) -> float:
    """Mean of the tail of a settling series.

    The transient is the part being thrown away; `discard_frac=0.5` keeps the
    last half.  Averaging the whole dwell would drag every steady-state speed
    downwards by however long the motor took to spin up, which shows up as a
    duty->speed curve that bends the wrong way at low duty.
    """
    if not values:
        raise ValueError("steady_state needs at least one sample")
    if not 0.0 <= discard_frac < 1.0:
        raise ValueError(f"discard_frac must be in [0, 1), got {discard_frac}")
    start = int(len(values) * discard_frac)
    tail = values[start:] or values[-1:]
    return statistics.fmean(float(v) for v in tail)


@dataclass(frozen=True)
class StepMetrics:
    """The four numbers a step response is for."""

    final_value: float
    target_value: float
    rise_time_s: float | None
    overshoot_frac: float | None
    settling_time_s: float | None
    steady_state_error: float
    steady_state_error_frac: float | None

    def as_dict(self, unit: str = "ticks_per_s") -> dict[str, Any]:
        return {
            f"final_{unit}": self.final_value,
            f"target_{unit}": self.target_value,
            "rise_time_s": self.rise_time_s,
            "overshoot_frac": self.overshoot_frac,
            "settling_time_s": self.settling_time_s,
            f"steady_state_error_{unit}": self.steady_state_error,
            "steady_state_error_frac": self.steady_state_error_frac,
        }


def step_metrics(times_s: Sequence[float], values: Sequence[float],
                 target: float, *, settle_band_frac: float = 0.02,
                 tail_frac: float = 0.2) -> StepMetrics:
    """Rise time, overshoot, settling time and steady-state error.

    Definitions are stated because everyone's differ, and a number without its
    definition cannot be compared to anybody else's:

      * **final** — mean of the last `tail_frac` of the record.  Not the last
        sample: one noisy sample must not define the answer.
      * **rise time** — first crossing of 10% of final to first crossing of
        90% of final.  `None` if it never reaches 90%.
      * **overshoot** — (peak - final) / |final|, as a fraction, floored at 0.
      * **settling time** — time after which the signal stays inside
        ±`settle_band_frac` of final.  `None` if it never settles.
      * **steady-state error** — target - final.  Signed, because the sign says
        whether the loop is under- or over-driving.
    """
    if len(times_s) != len(values):
        raise ValueError("times_s and values must be the same length")
    if len(values) < 2:
        raise ValueError("step_metrics needs at least two samples")

    data = [float(v) for v in values]
    times = [float(t) for t in times_s]
    tail_start = max(0, int(len(data) * (1.0 - tail_frac)))
    final = statistics.fmean(data[tail_start:])

    rise_time_s: float | None = None
    overshoot_frac: float | None = None
    settling_time_s: float | None = None

    if final != 0.0:
        t_low = _first_crossing(times, data, 0.1 * final, final)
        t_high = _first_crossing(times, data, 0.9 * final, final)
        if t_low is not None and t_high is not None and t_high >= t_low:
            rise_time_s = t_high - t_low

        peak = max(data) if final > 0 else min(data)
        overshoot = (peak - final) / abs(final)
        overshoot_frac = max(0.0, overshoot if final > 0 else -overshoot)

        band = abs(final) * settle_band_frac
        settling_time_s = 0.0
        for index in range(len(data) - 1, -1, -1):
            if abs(data[index] - final) > band:
                settling_time_s = (times[index + 1] - times[0]
                                   if index + 1 < len(times) else None)
                break

    error = target - final
    return StepMetrics(final, target, rise_time_s, overshoot_frac,
                       settling_time_s, error,
                       error / abs(target) if target else None)


def _first_crossing(times: Sequence[float], values: Sequence[float],
                    level: float, final: float) -> float | None:
    """First time the series reaches `level`, moving in the sign of `final`."""
    rising = final > 0
    for time_s, value in zip(times, values):
        if (rising and value >= level) or (not rising and value <= level):
            return time_s
    return None


def loop_periods_s(edge_times_s: Sequence[float]) -> list[float]:
    """Loop periods from LOOP_TICK edges.

    The pin **toggles** once per iteration, so every edge — rising or
    falling — is one iteration boundary.  Using only rising edges would double
    every period.  See the module docstring.
    """
    return [b - a for a, b in zip(edge_times_s, edge_times_s[1:]) if b > a]


def high_intervals_s(trace: ChannelTrace,
                     end_time_s: float) -> list[tuple[float, float]]:
    """(start, end) of every interval the channel is HIGH.

    Handles a capture that starts or ends mid-pulse: an unterminated pulse is
    closed at `end_time_s` rather than dropped, so a busy signal that is still
    high when the capture stops contributes its measured time.
    """
    intervals: list[tuple[float, float]] = []
    level = trace.initial_level
    start = 0.0
    for edge in trace.edges:
        if edge.level == 1 and level == 0:
            start = edge.time_s
        elif edge.level == 0 and level == 1:
            intervals.append((start, edge.time_s))
        level = edge.level
    if level == 1:
        intervals.append((start, end_time_s))
    return intervals


def busy_fraction(trace: ChannelTrace, end_time_s: float) -> float:
    """Fraction of wall time COMPUTE_BUSY was high — the loop's CPU duty."""
    if end_time_s <= 0:
        return 0.0
    return sum(end - start
               for start, end in high_intervals_s(trace, end_time_s)) / end_time_s


def histogram(values: Sequence[float], bins: int = 20) -> dict[str, list[float]]:
    """A plain histogram, stdlib only.

    Returned as `{edges, counts}` so it drops straight into JSON.  A *histogram*
    rather than a mean is the whole point: a loop that is 10 ms on average with
    a 40 ms tail and one that is 10 ms every time have the same mean and
    completely different control behaviour.
    """
    if bins < 1:
        raise ValueError("bins must be >= 1")
    if not values:
        return {"edges": [], "counts": []}
    data = [float(v) for v in values]
    low, high = min(data), max(data)
    if high == low:
        return {"edges": [low, low], "counts": [float(len(data))]}
    width = (high - low) / bins
    counts = [0.0] * bins
    for value in data:
        counts[min(int((value - low) / width), bins - 1)] += 1.0
    return {"edges": [low + i * width for i in range(bins + 1)], "counts": counts}


def linear_fit(x: Sequence[float], y: Sequence[float]) -> dict[str, float | None]:
    """Least-squares `y = slope*x + intercept`, stdlib only.

    Used on the sweep to give a feed-forward gain and, more usefully, an
    x-intercept — the duty at which the fitted line predicts zero speed, which
    should agree with the measured deadband.  When it does not, one of the two
    experiments is wrong, and that disagreement is worth more than either
    number on its own.
    """
    if len(x) != len(y):
        raise ValueError("x and y must be the same length")
    empty: dict[str, float | None] = {"slope": None, "intercept": None,
                                      "x_intercept": None, "r2": None}
    if len(x) < 2:
        return empty
    mean_x, mean_y = statistics.fmean(x), statistics.fmean(y)
    sxx = sum((xi - mean_x) ** 2 for xi in x)
    if sxx == 0:
        return empty
    sxy = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, y))
    slope = sxy / sxx
    intercept = mean_y - slope * mean_x
    syy = sum((yi - mean_y) ** 2 for yi in y)
    return {
        "slope": slope,
        "intercept": intercept,
        "x_intercept": (-intercept / slope) if slope else None,
        "r2": (sxy * sxy) / (sxx * syy) if syy > 0 else None,
    }


# --------------------------------------------------------------------------
# Specs — the experiment definitions
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class DeadbandSpec:
    """Ramp |duty| up until the shaft first moves.

    Why it matters: below the deadband the PID's output does literally nothing,
    so the integrator winds up while the wheel sits still.  Knowing the number
    lets the controller feed-forward past it instead of rediscovering it the
    hard way on every start from rest.
    """

    motor: int
    duty_start_frac: float = 0.02
    duty_step_frac: float = 0.01
    duty_max_frac: float = 0.60
    dwell_s: float = 0.30
    repeats: int = 3
    directions: tuple[str, ...] = BOTH_DIRECTIONS
    move_threshold_ticks: int = 5
    rest_s: float = 0.5
    kind: str = "deadband"

    @property
    def duties_frac(self) -> tuple[float, ...]:
        return _ramp(self.duty_start_frac, self.duty_max_frac, self.duty_step_frac)

    @property
    def estimated_drive_s(self) -> float:
        return (len(self.duties_frac) * self.dwell_s
                * self.repeats * len(self.directions))


@dataclass(frozen=True)
class SweepSpec:
    """duty -> steady-state speed, in 5% steps, both directions.

    The output is the feed-forward curve for Story 1.6 and the evidence for
    "four identical motors never are".
    """

    motor: int
    duty_min_frac: float = 0.05
    duty_max_frac: float = 1.00
    duty_step_frac: float = 0.05
    settle_s: float = 0.80          # transient, discarded
    window_s: float = 0.50          # measurement window
    directions: tuple[str, ...] = BOTH_DIRECTIONS
    rest_s: float = 1.0
    kind: str = "sweep"

    @property
    def duties_frac(self) -> tuple[float, ...]:
        return _ramp(self.duty_min_frac, self.duty_max_frac, self.duty_step_frac)

    @property
    def estimated_drive_s(self) -> float:
        return (len(self.duties_frac) * (self.settle_s + self.window_s)
                * len(self.directions))


@dataclass(frozen=True)
class StepSpec:
    """Step response, open loop and closed loop.

    Open loop answers "what is this motor's time constant".  Closed loop
    answers "did the controller improve it, and at what cost in overshoot".
    Running both in one experiment is what makes the before/after plot honest —
    same motor, same session, same temperature.
    """

    motor: int
    duty_frac: float = 0.60                  # open-loop step height
    target_ticks_per_s: float = 0.0          # closed-loop setpoint, in ticks/s
    pre_s: float = 0.25
    post_s: float = 1.50
    sample_hz: float = 50.0
    modes: tuple[str, ...] = ("open", "closed")
    kp: float = 0.0
    ki: float = 0.0
    kd: float = 0.0
    rest_s: float = 1.0
    kind: str = "step"

    @property
    def estimated_drive_s(self) -> float:
        return (self.pre_s + self.post_s) * len(self.modes)


@dataclass(frozen=True)
class TicksPerRevSpec:
    """Guided measurement of counts per **output** revolution.

    Procedure, and why each step is there:

      1. Drop STBY (outputs high-Z) and coast the motor (IN1=IN2=0).  A braked
         H-bridge shorts the windings and the shaft fights you, and a driver
         that is enabled at all is a driver that can turn under your fingers.
      2. Read the counter.  (Nothing zeroes it: the measurement is a delta.)
      3. The operator turns the marked output shaft exactly
         `revolutions_rev` turns by hand and confirms.
      4. Read the counter again.  ticks/rev = |delta| / revolutions.

    More than one revolution per trial on purpose: the error in landing the
    mark back on its line is a fixed number of ticks, so turning five
    revolutions divides that error by five.  Repeats give the spread.

    Measured, never defaulted: the published figures disagree (11 vs 14 counts
    per motor rev) and the gearbox multiplies whichever is right.
    """

    motor: int
    revolutions_rev: float = 5.0
    repeats: int = 3
    kind: str = "ticks_per_rev"

    @property
    def estimated_drive_s(self) -> float:
        return 0.0  # the operator turns the shaft; nothing is driven


@dataclass(frozen=True)
class JitterSpec:
    """Capture GP20/GP21 and characterise the control loop's timing.

    `duty_frac` is non-zero by default because a loop measured while idle is
    not the loop that runs the robot — the PID does its real work while the
    motor is moving and the encoder is generating edges.
    """

    motor: int
    duration_s: float = 2.0
    duty_frac: float = 0.50
    minimum_sample_rate_hz: int = 1_000_000
    channels: tuple[str, ...] = (LOOP_TICK_CHANNEL, COMPUTE_BUSY_CHANNEL)
    expected_loop_hz: float = 100.0
    histogram_bins: int = 20
    kind: str = "jitter"

    @property
    def estimated_drive_s(self) -> float:
        return self.duration_s + 1.0


@dataclass(frozen=True)
class HoldSpec:
    """Hold one duty and log the speed.  The simplest useful experiment.

    It exists because the generic `ExperimentSpec` needs something to *do*, and
    because "drive at 50% for a second and tell me what the encoder said" is
    the first thing anyone wants from a new bench.
    """

    motor: int
    duty_frac: float = 0.5
    duration_s: float = 1.0
    samples_count: int = 10
    kind: str = "hold"

    @property
    def estimated_drive_s(self) -> float:
        return self.duration_s


def _ramp(start: float, stop: float, step: float) -> tuple[float, ...]:
    """Inclusive float ramp, rounded, with a positive-step guard."""
    if step <= 0:
        raise ValueError(f"step must be positive, got {step}")
    values: list[float] = []
    value = start
    while value <= stop + 1e-9:
        values.append(round(value, 6))
        value += step
    return tuple(values)


SPEC_TYPES: dict[str, type] = {
    "deadband": DeadbandSpec,
    "sweep": SweepSpec,
    "step": StepSpec,
    "ticks_per_rev": TicksPerRevSpec,
    "jitter": JitterSpec,
    "hold": HoldSpec,
}


# --------------------------------------------------------------------------
# Result
# --------------------------------------------------------------------------

@dataclass
class ExperimentResult:
    """Everything one experiment produced, ready for storage and the manifest."""

    kind: str
    motor: int
    columns: tuple[str, ...]
    rows: list[dict[str, Any]]
    summary: dict[str, Any]
    params: dict[str, Any]
    notes: list[str] = field(default_factory=list)
    capture_path: str | None = None
    sample_rate_hz: int | None = None
    ticks_per_rev: TicksPerRev = field(default_factory=TicksPerRev.unmeasured)
    question: str = ""
    method: str = ""

    @property
    def story(self) -> str:
        return STORY_IDS.get(self.kind, "1.5")

    def headline(self) -> str:
        """One line for the registry's Result column."""
        return self.summary.get("headline") or f"{self.kind} on motor {self.motor}"


# --------------------------------------------------------------------------
# The bench: what an experiment is handed
# --------------------------------------------------------------------------

@dataclass
class Bench:
    """The injected world an experiment runs in.

    Every field is a seam.  With the real ones you get a motor; with
    `fakes.build_fake_bench()` you get the identical code path and no hardware,
    which is what `--dry-run` and the test suite are.
    """

    link: Link
    clock: Clock = field(default_factory=RealClock)
    limits: Limits = field(default_factory=Limits)
    analyser: Analyser | None = None
    ticks_per_rev: TicksPerRev = field(default_factory=TicksPerRev.unmeasured)
    log: Callable[[str], None] = staticmethod(lambda _m: None)
    prompt: Callable[[str], str] = staticmethod(input)
    capture_path: str | None = None
    decode: Callable[[str], DecodedCapture] = staticmethod(decode_srzip)

    def guard(self) -> DriveGuard:
        return DriveGuard(self.link, self.limits, self.clock, self.log)


def measure_speed_ticks_per_s(bench: Bench, motor: int,
                              window_s: float) -> tuple[float, int]:
    """Speed over one window, using the *firmware's* timestamps.

    Returns `(ticks_per_s, delta_ticks)`.  Host time is never used for the
    division — USB CDC latency is variable and would smear straight into the
    speed.  See `link.EncoderSample`.
    """
    first = bench.link.read_encoder(motor)
    bench.clock.sleep(window_s)
    second = bench.link.read_encoder(motor)
    return second.rate_ticks_per_s(first), second.ticks - first.ticks


def _speed_columns(tpr: TicksPerRev) -> tuple[str, ...]:
    """`omega_rad_s` exists as a column only when the constant is measured."""
    return ("speed_ticks_per_s", "omega_rad_s") if tpr.measured else ("speed_ticks_per_s",)


def _speed_fields(tpr: TicksPerRev, ticks_per_s: float) -> dict[str, Any]:
    fields: dict[str, Any] = {"speed_ticks_per_s": ticks_per_s}
    omega = tpr.omega_rad_s(ticks_per_s)
    if omega is not None:
        fields["omega_rad_s"] = omega
    return fields


def _notes_for(tpr: TicksPerRev) -> list[str]:
    return [] if tpr.measured else [UNCONVERTED_NOTE]


def _safe_rate(sample: EncoderSample, previous: EncoderSample) -> float:
    """Rate between two samples, tolerating a repeated firmware timestamp.

    A duplicate timestamp means the host polled faster than the firmware
    updates its counter; that is a sampling artifact, not a measurement, so it
    reports 0 rather than dividing by zero and killing the run.
    """
    if sample.t_s <= previous.t_s:
        return 0.0
    return sample.rate_ticks_per_s(previous)


# --------------------------------------------------------------------------
# deadband
# --------------------------------------------------------------------------

def deadband(bench: Bench, spec: DeadbandSpec) -> ExperimentResult:
    """Ramp |duty| from ~0 until the encoder first moves.  Both directions."""
    rows: list[dict[str, Any]] = []
    found: dict[str, list[float]] = {d: [] for d in spec.directions}

    with bench.guard() as guard:
        for direction in spec.directions:
            sign = direction_sign(direction)
            for trial in range(1, spec.repeats + 1):
                guard.rest(spec.rest_s, [spec.motor])
                threshold_duty: float | None = None
                for duty in spec.duties_frac:
                    guard.check()
                    before = bench.link.read_encoder(spec.motor)
                    guard.set_duty(spec.motor, sign * duty)
                    bench.clock.sleep(spec.dwell_s)
                    after = bench.link.read_encoder(spec.motor)
                    delta = after.ticks - before.ticks
                    moved = abs(delta) >= spec.move_threshold_ticks
                    rows.append({
                        "trial": trial,
                        "direction": direction,
                        "duty_frac": sign * duty,
                        "delta_ticks": delta,
                        "dwell_s": spec.dwell_s,
                        "moved_count": 1 if moved else 0,
                    })
                    if moved:
                        threshold_duty = duty
                        break
                guard.set_duty(spec.motor, 0.0)
                if threshold_duty is not None:
                    found[direction].append(threshold_duty)
                else:
                    bench.log(
                        f"WARNING: motor {spec.motor} {direction}: no movement up "
                        f"to |duty| {spec.duty_max_frac:.2f} on trial {trial}"
                    )

    summary: dict[str, Any] = {}
    headline_bits: list[str] = []
    for direction in spec.directions:
        stats = describe(found[direction], "duty_frac")
        summary[direction] = stats
        mean = stats["mean_duty_frac"]
        headline_bits.append(
            f"{direction}: never moved" if mean is None
            else f"{direction}: {mean:.3f} ± {stats['stdev_duty_frac']:.3f}"
        )
    summary["all"] = describe([v for vs in found.values() for v in vs], "duty_frac")
    summary["headline"] = "deadband duty " + "; ".join(headline_bits)

    return ExperimentResult(
        kind=spec.kind,
        motor=spec.motor,
        columns=("trial", "direction", "duty_frac", "delta_ticks",
                 "dwell_s", "moved_count"),
        rows=rows,
        summary=summary,
        params=asdict(spec),
        ticks_per_rev=bench.ticks_per_rev,
        question=f"At what duty does motor {spec.motor} first turn, each way?",
        method=(f"Ramp |duty| from {spec.duty_start_frac} in "
                f"{spec.duty_step_frac} steps, {spec.dwell_s}s dwell, movement "
                f"= {spec.move_threshold_ticks} ticks, {spec.repeats} repeats "
                "per direction"),
    )


# --------------------------------------------------------------------------
# sweep
# --------------------------------------------------------------------------

def sweep(bench: Bench, spec: SweepSpec) -> ExperimentResult:
    """duty -> steady-state speed, discarding the transient at every point."""
    rows: list[dict[str, Any]] = []
    per_direction: dict[str, list[tuple[float, float]]] = {d: [] for d in spec.directions}

    with bench.guard() as guard:
        for direction in spec.directions:
            sign = direction_sign(direction)
            guard.rest(spec.rest_s, [spec.motor])
            for duty in spec.duties_frac:
                guard.check()
                guard.set_duty(spec.motor, sign * duty)
                bench.clock.sleep(spec.settle_s)     # transient — discarded
                guard.check()
                ticks_per_s, delta = measure_speed_ticks_per_s(
                    bench, spec.motor, spec.window_s)
                row: dict[str, Any] = {
                    "direction": direction,
                    "duty_frac": sign * duty,
                    "delta_ticks": delta,
                    "settle_s": spec.settle_s,
                    "window_s": spec.window_s,
                }
                row.update(_speed_fields(bench.ticks_per_rev, ticks_per_s))
                rows.append(row)
                per_direction[direction].append((duty, abs(ticks_per_s)))
            guard.set_duty(spec.motor, 0.0)

    summary: dict[str, Any] = {}
    headline_bits: list[str] = []
    for direction, points in per_direction.items():
        speeds = [speed for _duty, speed in points]
        top = max(speeds) if speeds else 0.0
        summary[direction] = {
            "max_speed_ticks_per_s": top,
            "max_omega_rad_s": bench.ticks_per_rev.omega_rad_s(top),
            "points_count": len(points),
            "linear_fit": linear_fit([d for d, _ in points], speeds),
        }
        headline_bits.append(f"{direction} max {top:.0f} ticks/s")
    summary["units_converted"] = bench.ticks_per_rev.measured
    summary["headline"] = ("duty->speed sweep: " + "; ".join(headline_bits)
                           + ("" if bench.ticks_per_rev.measured
                              else "  [ticks/s, UN-CONVERTED]"))

    return ExperimentResult(
        kind=spec.kind,
        motor=spec.motor,
        columns=("direction", "duty_frac", "delta_ticks", "settle_s", "window_s")
                + _speed_columns(bench.ticks_per_rev),
        rows=rows,
        summary=summary,
        params=asdict(spec),
        notes=_notes_for(bench.ticks_per_rev),
        ticks_per_rev=bench.ticks_per_rev,
        question=f"What is motor {spec.motor}'s duty -> steady-state speed curve?",
        method=(f"{spec.duty_step_frac:.0%} duty steps from {spec.duty_min_frac:.2f} "
                f"to {spec.duty_max_frac:.2f}, {spec.settle_s}s settle discarded, "
                f"{spec.window_s}s window, both directions"),
    )


# --------------------------------------------------------------------------
# step
# --------------------------------------------------------------------------

def step(bench: Bench, spec: StepSpec) -> ExperimentResult:
    """Step response, open loop and/or closed loop, on one motor."""
    rows: list[dict[str, Any]] = []
    summary: dict[str, Any] = {}
    headline_bits: list[str] = []

    notes: list[str] = []
    with bench.guard() as guard:
        for mode in spec.modes:
            guard.rest(spec.rest_s, [spec.motor])
            series_t, series_v = [], []
            target = float(spec.target_ticks_per_s)

            # The firmware's setpoint command takes rad/s of the output shaft,
            # so a ticks/s target cannot be commanded until ticks_per_rev has
            # been MEASURED.  Guessing it (1100? 1400?) would command a speed
            # 27% away from the one in the manifest and nothing would say so.
            omega_target = bench.ticks_per_rev.omega_rad_s(target)
            if mode == "closed" and omega_target is None:
                reason = ("closed loop skipped: the firmware setpoint is in "
                          "rad/s and ticks_per_rev is unmeasured.  Run "
                          "`rover-bench ticks-per-rev` first.")
                bench.log(f"WARNING: {reason}")
                notes.append(reason)
                summary[mode] = {"skipped_reason": reason, "samples_count": 0}
                headline_bits.append("closed: skipped (units)")
                continue

            bench.link.set_pid_enabled(spec.motor, mode == "closed")
            if mode == "closed":
                bench.link.set_pid(spec.motor, spec.kp, spec.ki, spec.kd)
                if CLOSED_LOOP_SETPOINT_NOTE not in notes:
                    bench.log(f"WARNING: {CLOSED_LOOP_SETPOINT_NOTE}")
                    notes.append(CLOSED_LOOP_SETPOINT_NOTE)

            previous = bench.link.read_encoder(spec.motor)
            t_zero = previous.t_s

            # Baseline at rest, so the plot shows where it started from.
            previous = _sample_into(bench, spec, guard, mode, rows, series_t,
                                    series_v, previous, t_zero, spec.pre_s,
                                    commanded_duty=0.0, target=target,
                                    check=False)

            # The step itself.
            if mode == "closed":
                bench.link.set_target_omega_rad_s(spec.motor, omega_target)
                # The guard cannot see a closed-loop setpoint as "moving", so
                # tell it: the drive-time interlock must count this too.
                guard.moving_since_s = guard.clock.now()
                guard.stopped = False
                commanded = 0.0
            else:
                guard.set_duty(spec.motor, spec.duty_frac)
                commanded = spec.duty_frac

            _sample_into(bench, spec, guard, mode, rows, series_t, series_v,
                         previous, t_zero, spec.post_s,
                         commanded_duty=commanded, target=target, check=True)

            if mode == "closed":
                bench.link.set_target_omega_rad_s(spec.motor, 0.0)
                bench.link.set_pid_enabled(spec.motor, False)
            guard.set_duty(spec.motor, 0.0)

            if len(series_v) >= 2:
                metrics = step_metrics(series_t, series_v, target)
                summary[mode] = metrics.as_dict()
                summary[mode]["samples_count"] = len(series_v)
                rise = metrics.rise_time_s
                headline_bits.append(
                    f"{mode}: rise {rise * 1000:.0f} ms" if rise is not None
                    else f"{mode}: never rose")
            else:
                summary[mode] = {"samples_count": len(series_v)}
                headline_bits.append(f"{mode}: too few samples")

    summary["units_converted"] = bench.ticks_per_rev.measured
    summary["headline"] = "step response — " + "; ".join(headline_bits)

    return ExperimentResult(
        kind=spec.kind,
        motor=spec.motor,
        columns=("mode", "time_s", "duty_frac", "target_ticks_per_s")
                + _speed_columns(bench.ticks_per_rev),
        rows=rows,
        summary=summary,
        params=asdict(spec),
        notes=_notes_for(bench.ticks_per_rev) + notes,
        ticks_per_rev=bench.ticks_per_rev,
        question=(f"How does motor {spec.motor} respond to a step, open loop "
                  "vs closed loop?"),
        method=(f"{spec.pre_s}s at rest then a step (open: duty {spec.duty_frac}; "
                f"closed: {spec.target_ticks_per_s} ticks/s), sampled at "
                f"{spec.sample_hz} Hz for {spec.post_s}s"),
    )


def _sample_into(bench: Bench, spec: StepSpec, guard: DriveGuard, mode: str,
                 rows: list[dict[str, Any]], series_t: list[float],
                 series_v: list[float], previous: EncoderSample, t_zero: float,
                 window_s: float, *, commanded_duty: float, target: float,
                 check: bool) -> EncoderSample:
    """Sample the encoder for `window_s`, appending rows and the series."""
    period_s = 1.0 / spec.sample_hz
    deadline = bench.clock.now() + window_s
    while bench.clock.now() < deadline:
        if check:
            guard.check()
        bench.clock.sleep(period_s)
        sample = bench.link.read_encoder(spec.motor)
        rate = _safe_rate(sample, previous)
        previous = sample
        elapsed = sample.t_s - t_zero
        row: dict[str, Any] = {
            "mode": mode,
            "time_s": elapsed,
            "duty_frac": commanded_duty,
            "target_ticks_per_s": target,
        }
        row.update(_speed_fields(bench.ticks_per_rev, rate))
        rows.append(row)
        series_t.append(elapsed)
        series_v.append(rate)
    return previous


# --------------------------------------------------------------------------
# ticks per revolution
# --------------------------------------------------------------------------

def ticks_per_rev(bench: Bench, spec: TicksPerRevSpec) -> ExperimentResult:
    """Guided, hands-on measurement of counts per output revolution.

    Nothing is driven.  The operator turns the shaft; the tool only counts.
    """
    rows: list[dict[str, Any]] = []
    values: list[float] = []

    # STBY stays LOW for the whole procedure, and that is the point.  This is
    # the one experiment where a human's fingers are on the shaft, and STBY low
    # is the TB6612's hardware all-stop: the outputs are high-Z, so no command,
    # no glitch and no half-configured PWM slice can spin the motor while he is
    # holding it.  It costs nothing — the encoder is powered from the 3.3 V rail
    # (blue wire) and counted by the Pico's PIO, both entirely independent of the
    # driver — and high-Z is also the freest the shaft can be to turn by hand.
    bench.link.disable()
    try:
        bench.link.coast(spec.motor)   # duty 0, PID off: nothing is commanded
        for trial in range(1, spec.repeats + 1):
            # No counter-zero command on this firmware, and none needed: the
            # measurement is a *delta*, so reading before and after is both
            # sufficient and one fewer thing that can go wrong mid-procedure.
            start = bench.link.read_encoder(spec.motor)
            bench.prompt(
                f"\n  Motor {spec.motor}, trial {trial}/{spec.repeats}\n"
                f"  Mark the OUTPUT shaft, turn it exactly "
                f"{spec.revolutions_rev:g} revolutions by hand,\n"
                f"  land the mark back on its line, then press Enter: "
            )
            end = bench.link.read_encoder(spec.motor)
            delta = abs(end.ticks - start.ticks)
            per_rev = delta / spec.revolutions_rev if spec.revolutions_rev else 0.0
            rows.append({
                "trial": trial,
                "revolutions_rev": spec.revolutions_rev,
                "delta_ticks": delta,
                "ticks_per_rev": per_rev,
            })
            if per_rev > 0:
                values.append(per_rev)
            else:
                bench.log(
                    f"WARNING: trial {trial} counted 0 ticks — is the encoder "
                    "powered (blue wire, 3.3 V) and wired to GP12/GP13?"
                )
    finally:
        bench.link.disable()

    stats = describe(values, "ticks_per_rev")
    mean = stats["mean_ticks_per_rev"]
    summary: dict[str, Any] = dict(stats)
    summary["measured"] = mean is not None
    if mean is not None:
        summary["headline"] = (
            f"{mean:.0f} ± {stats['stdev_ticks_per_rev']:.0f} ticks per OUTPUT "
            f"rev (n={stats['n_count']}, {spec.revolutions_rev:g} rev per trial)"
        )
        # The disputed published figures, recorded for comparison — never used.
        summary["published_candidates_ticks_per_rev"] = {
            "adafruit_14_counts_per_motor_rev_x100": 1400.0,
            "retailer_11_counts_per_motor_rev_x100": 1100.0,
        }
    else:
        summary["headline"] = "no ticks counted — measurement FAILED"

    measured = (TicksPerRev.from_measurement(mean, "measured: this run",
                                             stats["stdev_ticks_per_rev"])
                if mean is not None else TicksPerRev.unmeasured())

    return ExperimentResult(
        kind=spec.kind,
        motor=spec.motor,
        columns=("trial", "revolutions_rev", "delta_ticks", "ticks_per_rev"),
        rows=rows,
        summary=summary,
        params=asdict(spec),
        notes=[
            "Measured by hand-turning the OUTPUT shaft with the H-bridge "
            "coasting.  The published figures disagree (11 vs 14 counts per "
            "motor rev) and are recorded in the summary for comparison only.",
        ],
        ticks_per_rev=measured,
        question=("How many encoder ticks are there per OUTPUT shaft revolution "
                  f"on motor {spec.motor}?"),
        method=(f"STBY low (outputs high-Z), read the counter, hand-turn "
                f"{spec.revolutions_rev:g} revolutions of the OUTPUT shaft, "
                f"read it again; {spec.repeats} repeats"),
    )


# --------------------------------------------------------------------------
# jitter
# --------------------------------------------------------------------------

def jitter(bench: Bench, spec: JitterSpec) -> ExperimentResult:
    """Loop period histogram and CPU duty, from GP20/GP21 on the analyser.

    This is the experiment that turns "I wrote a PID" into "I measured the loop
    that runs it".  It needs the analyser; `safety.check_analyser` refuses the
    run earlier if it is missing.
    """
    if bench.analyser is None:
        raise RuntimeError(
            "jitter needs the logic analyser (GP20 -> D6, GP21 -> D7).  None "
            "was provided — this should have been caught by pre-flight."
        )
    if bench.capture_path is None:
        raise RuntimeError("jitter needs an output path for the .sr capture")

    capabilities = bench.analyser.capabilities()
    rate_hz = bench.analyser.choose_sample_rate(spec.minimum_sample_rate_hz,
                                                capabilities)
    _warn_if_rate_is_marginal(bench, rate_hz, spec)

    with bench.guard() as guard:
        guard.set_duty(spec.motor, spec.duty_frac)
        bench.clock.sleep(0.5)             # let the loop reach steady work
        guard.check()
        capture = bench.analyser.capture(list(spec.channels), rate_hz,
                                         spec.duration_s, str(bench.capture_path))
        guard.set_duty(spec.motor, 0.0)

    decoded = bench.decode(capture.path)
    tick_trace = decoded.trace(LOOP_TICK_CHANNEL)
    busy_trace = decoded.trace(COMPUTE_BUSY_CHANNEL)
    end_s = decoded.duration_s or spec.duration_s

    periods_s = loop_periods_s(tick_trace.edge_times_s)
    compute_s = [end - start for start, end in high_intervals_s(busy_trace, end_s)]

    rows: list[dict[str, Any]] = []
    for index, period in enumerate(periods_s):
        row: dict[str, Any] = {
            "loop": index,
            "period_ms": period * 1000.0,
            "rate_hz": 1.0 / period if period else 0.0,
            "compute_ms": "",
            "compute_frac": "",
        }
        if index < len(compute_s):
            row["compute_ms"] = compute_s[index] * 1000.0
            row["compute_frac"] = compute_s[index] / period if period else 0.0
        rows.append(row)

    period_stats = describe([p * 1000.0 for p in periods_s], "ms")
    summary: dict[str, Any] = {
        "period": period_stats,
        "compute": describe([c * 1000.0 for c in compute_s], "ms"),
        "cpu_duty_frac": busy_fraction(busy_trace, end_s),
        "loops_count": len(periods_s),
        "expected_period_ms": 1000.0 / spec.expected_loop_hz,
        "sample_rate_hz": decoded.sample_rate_hz or rate_hz,
        "timing_resolution_us": (1e6 / decoded.sample_rate_hz
                                 if decoded.sample_rate_hz else None),
        "histogram_period_ms": histogram([p * 1000.0 for p in periods_s],
                                         spec.histogram_bins),
    }
    mean_ms = period_stats["mean_ms"]
    if mean_ms:
        expected = summary["expected_period_ms"]
        summary["mean_error_frac"] = (mean_ms - expected) / expected
        summary["peak_to_peak_ms"] = ((period_stats["max_ms"] or 0.0)
                                      - (period_stats["min_ms"] or 0.0))
        summary["headline"] = (
            f"loop {mean_ms:.3f} ms mean (±{period_stats['stdev_ms']:.3f} ms, "
            f"p-p {summary['peak_to_peak_ms']:.3f} ms), "
            f"CPU duty {summary['cpu_duty_frac']:.1%}"
        )
    else:
        summary["headline"] = (
            "no LOOP_TICK edges captured — is the firmware toggling GP20, and "
            "is D6 on the right pin?"
        )

    return ExperimentResult(
        kind=spec.kind,
        motor=spec.motor,
        columns=("loop", "period_ms", "rate_hz", "compute_ms", "compute_frac"),
        rows=rows,
        summary=summary,
        params=asdict(spec),
        notes=[
            "LOOP_TICK (GP20/D6) toggles once per iteration, so one loop period "
            "is the interval between consecutive edges of EITHER polarity — not "
            "rising-to-rising.",
        ],
        capture_path=capture.path,
        sample_rate_hz=decoded.sample_rate_hz or rate_hz,
        ticks_per_rev=bench.ticks_per_rev,
        question=("What is the control loop's period distribution and CPU duty "
                  f"while driving motor {spec.motor}?"),
        method=(f"Capture D6/D7 for {spec.duration_s}s at {rate_hz} Hz while "
                f"driving at duty {spec.duty_frac}"),
    )


def _warn_if_rate_is_marginal(bench: Bench, rate_hz: int, spec: JitterSpec) -> None:
    """A sample rate near the signal's own rate measures nothing useful.

    Rule of thumb used here: to resolve jitter you want the sample period at
    least ~1000x finer than the loop period, i.e. ~0.1% timing resolution.
    Below 100x the histogram is mostly quantisation.
    """
    loop_period_s = 1.0 / spec.expected_loop_hz
    resolution_s = 1.0 / rate_hz if rate_hz else float("inf")
    ratio = loop_period_s / resolution_s if resolution_s else 0.0
    if ratio < 100:
        bench.log(
            f"WARNING: {rate_hz} Hz gives {resolution_s * 1e6:.1f} us resolution "
            f"on a {loop_period_s * 1000:.1f} ms loop ({ratio:.0f}x).  The "
            "histogram will be dominated by quantisation."
        )


# --------------------------------------------------------------------------
# hold — the simple one the generic spec runs
# --------------------------------------------------------------------------

def hold(bench: Bench, spec: HoldSpec) -> ExperimentResult:
    """Drive at one duty and log the speed.

    Tolerant of a firmware that cannot answer `ENC` yet: the speed column is
    left empty rather than aborting.  Bring-up order matters — PWM works before
    the encoder does, and this experiment is useful in between.
    """
    rows: list[dict[str, Any]] = []
    speeds: list[float] = []
    period_s = spec.duration_s / max(1, spec.samples_count)

    with bench.guard() as guard:
        guard.set_duty(spec.motor, spec.duty_frac)
        previous = _try_read_encoder(bench, spec.motor)
        t_zero = previous.t_s if previous else 0.0
        for index in range(spec.samples_count):
            guard.check()
            bench.clock.sleep(period_s)
            sample = _try_read_encoder(bench, spec.motor)
            row: dict[str, Any] = {
                "sample": index,
                "time_s": (sample.t_s - t_zero) if sample else index * period_s,
                "duty_frac": spec.duty_frac,
                "speed_ticks_per_s": "",
            }
            if sample and previous:
                rate = _safe_rate(sample, previous)
                row["speed_ticks_per_s"] = rate
                speeds.append(rate)
            if sample:
                previous = sample
            rows.append(row)
        guard.set_duty(spec.motor, 0.0)

    summary: dict[str, Any] = describe(speeds, "ticks_per_s")
    summary["units_converted"] = bench.ticks_per_rev.measured
    mean = summary["mean_ticks_per_s"]
    summary["headline"] = (
        f"held duty {spec.duty_frac:+.2f} for {spec.duration_s:g}s: "
        + (f"{mean:.0f} ticks/s mean" if mean is not None
           else "no encoder data (firmware did not answer ENC)")
    )

    return ExperimentResult(
        kind=spec.kind,
        motor=spec.motor,
        columns=("sample", "time_s", "duty_frac", "speed_ticks_per_s"),
        rows=rows,
        summary=summary,
        params=asdict(spec),
        notes=_notes_for(bench.ticks_per_rev),
        ticks_per_rev=bench.ticks_per_rev,
        question=(f"What speed does motor {spec.motor} hold at duty "
                  f"{spec.duty_frac}?"),
        method=(f"Drive at {spec.duty_frac} for {spec.duration_s}s, "
                f"{spec.samples_count} encoder samples"),
    )


def _try_read_encoder(bench: Bench, motor: int) -> EncoderSample | None:
    """Read the encoder, or None if the firmware cannot answer.

    Deliberately swallowing: on a board where the encoder is not implemented
    yet, "no speed data" is the honest result and the duty/timing data is still
    worth keeping.  It is logged so it is never silent.
    """
    try:
        return bench.link.read_encoder(motor)
    except LinkError as exc:
        bench.log(f"note: encoder read failed ({exc})")
        return None


# --------------------------------------------------------------------------
# Dispatch
# --------------------------------------------------------------------------

RUNNERS: dict[str, Callable[[Bench, Any], ExperimentResult]] = {
    "deadband": deadband,
    "sweep": sweep,
    "step": step,
    "ticks_per_rev": ticks_per_rev,
    "jitter": jitter,
    "hold": hold,
}

NEEDS_ANALYSER: frozenset[str] = frozenset({"jitter"})
NEEDS_OPERATOR: frozenset[str] = frozenset({"ticks_per_rev"})


def run_experiment(bench: Bench, spec: Any) -> ExperimentResult:
    """Dispatch by `spec.kind`.  The only place kinds are looked up."""
    kind = getattr(spec, "kind", None)
    if kind not in RUNNERS:
        raise ValueError(f"unknown experiment kind {kind!r}; have {sorted(RUNNERS)}")
    return RUNNERS[kind](bench, spec)


def ticks_per_rev_from_manifest(payload: Mapping[str, Any], *,
                                allow_simulated: bool = False) -> TicksPerRev:
    """Recover a measured `ticks_per_rev` from a previous run's manifest.

    This is how a sweep on Tuesday inherits the constant measured on Monday
    without anyone retyping it — and the `source` string records exactly which
    run it came from, so the provenance survives into the next manifest.

    **A failed run is never a source**, and **a simulated one only when the
    consumer is itself simulated** (`allow_simulated`, which the CLI sets from
    `--dry-run`).  The fake motor's ticks-per-rev (`fakes.FakeMotor`) sits
    squarely inside the disputed 1100-1400 band, so a number inherited from a
    dry run is indistinguishable by eye from a measured one — and it would then
    become the divisor of every `omega_rad_s` in a real data set, silently.
    Inside a dry run that inheritance is exactly what should happen (it is how
    the operator sees the units gate open before he is at the bench); crossing
    from fake to real is what must not.  Defaulting to False means a caller
    that has not thought about it gets the safe answer.
    """
    if payload.get("error"):
        return TicksPerRev.unmeasured()
    if payload.get("dry_run") and not allow_simulated:
        return TicksPerRev.unmeasured()
    summary = payload.get("summary") or {}
    value = summary.get("mean_ticks_per_rev") or payload.get("ticks_per_rev")
    if not value:
        return TicksPerRev.unmeasured()
    label = (f"{'SIMULATED' if payload.get('dry_run') else 'measured'}: "
             f"{payload.get('experiment_id')}/motor-{payload.get('motor')}")
    return TicksPerRev.from_measurement(float(value), label,
                                        summary.get("stdev_ticks_per_rev"))


# --------------------------------------------------------------------------
# The generic orchestrator
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class ExperimentSpec:
    """One experiment, addressed the way the CLI and the registry address it.

    `params` is a plain dict so it survives a JSON round-trip into the manifest
    untouched — which is what makes two runs diffable parameter by parameter.
    `kind` selects the runner; it defaults to the simplest useful one.
    """

    experiment_id: str
    motor: int
    params: Mapping[str, Any] = field(default_factory=dict)
    kind: str = "hold"

    def build_spec(self) -> Any:
        """Turn `params` into the typed spec its runner expects.

        Unknown parameters are refused rather than ignored: a silently dropped
        `dwell_s` is a run that did something other than what the manifest says
        it did, and the manifest is the whole point.
        """
        spec_type = SPEC_TYPES.get(self.kind)
        if spec_type is None:
            raise ValueError(f"unknown experiment kind {self.kind!r}; "
                             f"have {sorted(SPEC_TYPES)}")
        allowed = {f for f in spec_type.__dataclass_fields__ if f != "kind"}
        unknown = [k for k in self.params if k not in allowed]
        if unknown:
            raise ValueError(
                f"{self.kind} does not take parameter(s) {unknown}; "
                f"it takes {sorted(allowed - {'motor'})}"
            )
        return spec_type(motor=self.motor, **dict(self.params))


@dataclass
class RunResult:
    """What one orchestrated run produced, and whether it worked."""

    spec: ExperimentSpec
    manifest: dict[str, Any]
    manifest_path: Path
    data_path: Path
    capture_path: Path | None = None
    result: ExperimentResult | None = None
    ok: bool = True
    error: str | None = None
    checks: list[Check] = field(default_factory=list)


def plan(spec: ExperimentSpec) -> list[str]:
    """The steps this run would take, as text.  No side effects at all.

    The plan a human reads before pressing go must say that the interlocks run.
    An invisible check is one nobody trusts and everybody bypasses.
    """
    typed = spec.build_spec()
    drive_s = getattr(typed, "estimated_drive_s", 0.0)
    steps = [
        f"experiment: {spec.experiment_id} ({spec.kind}) on motor {spec.motor}",
        "safety pre-flight: motor index, duty range, drive-time budget, "
        "PING response, analyser presence (interlocks refuse before STBY goes high)",
        f"parameters: {dict(spec.params) or 'defaults'}",
        f"estimated drive time: {drive_s:.1f} s",
        "enable STBY, run the measurement, and STOP + STBY low on every exit path",
        "write manifest.json (UTC time, git SHA, firmware SHA, argv, params, "
        "ticks_per_rev or explicit null)",
        "write samples.csv with SI units in every column name",
        "offer a row for experiments/REGISTRY.md — a result not persisted did "
        "not happen",
    ]
    if spec.kind in NEEDS_ANALYSER:
        steps.insert(3, "discover the analyser's sample rates at runtime and "
                        "capture D6/D7 (never AO1/AO2 — motor voltage)")
    if spec.kind in NEEDS_OPERATOR:
        steps.insert(3, "prompt the operator to hand-turn the output shaft "
                        "(nothing is driven)")
    return steps


def run(spec: ExperimentSpec, *, link: Any = None,
        root: str | os.PathLike[str] = ".",
        dry_run: bool = False,
        simulated: bool = False,
        analyser: Analyser | None = None,
        clock: Clock | None = None,
        limits: Limits | None = None,
        ticks_per_rev_value: TicksPerRev | None = None,
        log: Callable[[str], None] | None = None,
        prompt: Callable[[str], str] | None = None,
        argv: Sequence[str] | None = None,
        git: manifest_mod.GitState | None = None,
        now: Any = None) -> RunResult:
    """Run one experiment end to end and leave the record behind.

    Two classes of failure, treated differently on purpose:

    * **A spec this bench refuses to attempt** — bad motor number, duty outside
      [-1, 1] — raises *before* a single byte reaches the Pico.
    * **A failure during the run** — no PING, an `ERR` from the firmware, a
      stalled motor — is caught, recorded in the manifest, and returned as
      `ok=False`.  A failed run is data: it is what a debugging narrative is
      made of, and losing its manifest loses the only record of what was tried.

    The manifest is written in a `finally`, so it exists either way.

    `dry_run` means "do nothing at all": send no bytes, write no files.
    `simulated` means "this really ran, but against fakes" — everything is
    written, and the manifest says `dry_run: true` so no fake number can ever
    be mistaken for a measurement.
    """
    logger = log or (lambda _m: None)

    # --- refusals, before anything is sent ------------------------------
    check_motor(spec.motor)
    typed = spec.build_spec()
    for duty in _duties_of(typed):
        check_duty(duty, limits)

    tpr = ticks_per_rev_value or TicksPerRev.unmeasured()
    paths_root = Path(root)
    manifest_file = storage_mod.manifest_path(paths_root, spec.experiment_id,
                                              spec.motor)
    data_file = storage_mod.csv_path(paths_root, spec.experiment_id, spec.motor)
    capture_file = storage_mod.capture_path(paths_root, spec.experiment_id,
                                            spec.motor)

    state = manifest_mod.GitState() if (git is None and dry_run) else (
        git if git is not None else manifest_mod.GitState.read(str(paths_root)))

    payload = manifest_mod.build_manifest(
        spec.experiment_id,
        motor=spec.motor,
        kind=spec.kind,
        argv=argv,
        params=dict(spec.params),
        git_sha=state.sha,
        git_dirty=state.dirty,
        ticks_per_rev=tpr.value,
        ticks_per_rev_source=tpr.source,
        dry_run=dry_run or simulated,
        now=now,
        outputs={"csv": str(data_file), "json": str(manifest_file),
                 "sr": str(capture_file)},
    )

    if dry_run:
        # Nothing is sent, nothing is written.  A dry run that leaves files
        # behind is not a dry run.
        payload["summary"] = {"headline": "dry run — nothing was driven"}
        logger("\n".join(plan(spec)))
        return RunResult(spec=spec, manifest=payload, manifest_path=manifest_file,
                         data_path=data_file, ok=True)

    result: ExperimentResult | None = None
    error: str | None = None
    checks: list[Check] = []
    bench_link: Link | None = None
    try:
        bench_link = as_link(link) if link is not None else None
        if bench_link is None:
            raise LinkError("no link was provided and none could be opened")
        bench = Bench(
            link=bench_link,
            clock=clock or RealClock(),
            limits=limits or Limits(),
            analyser=analyser,
            ticks_per_rev=tpr,
            log=logger,
            prompt=prompt or input,
            capture_path=str(capture_file),
        )
        checks = preflight_run(
            link=bench_link,
            analyser=analyser,
            capabilities=analyser.capabilities() if (
                analyser is not None and spec.kind in NEEDS_ANALYSER) else None,
            motors=[spec.motor],
            duties=list(_duties_of(typed)),
            estimated_drive_s=getattr(typed, "estimated_drive_s", 0.0),
            needs_analyser=spec.kind in NEEDS_ANALYSER,
            limits=limits,
        )
        assert_ok(checks)
        result = run_experiment(bench, typed)
        payload["summary"] = result.summary
        payload["notes"] = list(payload.get("notes", [])) + list(result.notes)
        if result.sample_rate_hz:
            payload["sample_rate_hz"] = result.sample_rate_hz
            payload["analyser_channels"] = list(result.params.get("channels", ()))
        if result.ticks_per_rev.measured:
            payload["ticks_per_rev"] = result.ticks_per_rev.value
            payload["ticks_per_rev_source"] = result.ticks_per_rev.source
        try:
            payload.update(_firmware_fields(bench_link))
        except LinkError:      # identity is nice to have, not worth failing over
            pass
    except (SafetyViolation, LinkError, RuntimeError, ValueError) as exc:
        error = f"{type(exc).__name__}: {exc}"
        payload["summary"] = {"headline": f"run FAILED — {error}"}
        logger(f"run failed: {error}")
    finally:
        if bench_link is not None:
            try:
                bench_link.stop()
            except LinkError:  # pragma: no cover - already failing
                pass
        payload["error"] = error
        payload["checks"] = [
            {"name": c.name, "status": c.status, "detail": c.detail}
            for c in checks
        ]
        manifest_mod.write_manifest(manifest_file, payload)
        if result is not None and result.rows:
            storage_mod.write_csv(data_file, result.columns, result.rows)

    return RunResult(
        spec=spec,
        manifest=payload,
        manifest_path=manifest_file,
        data_path=data_file,
        capture_path=Path(result.capture_path) if result and result.capture_path else None,
        result=result,
        ok=error is None,
        error=error,
        checks=checks,
    )


def _duties_of(typed: Any) -> list[float]:
    """Every duty a spec would command, for the range interlock.

    Checking the whole plan up front rather than each value as it is reached
    means a sweep that would end at duty 1.5 is refused before the first point,
    not after nineteen good ones.
    """
    duties: list[float] = []
    if hasattr(typed, "duties_frac"):
        duties.extend(typed.duties_frac)
    if hasattr(typed, "duty_frac"):
        duties.append(float(typed.duty_frac))
    if hasattr(typed, "duty_max_frac"):
        duties.append(float(typed.duty_max_frac))
    return duties


def _firmware_fields(link: Link) -> dict[str, Any]:
    identity = link.firmware_identity()
    return {
        "firmware_version": identity.get("firmware_version"),
        "firmware_sha": identity.get("firmware_sha"),
        "protocol_version": identity.get("protocol_version"),
    }
