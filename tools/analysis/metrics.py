"""Derived numbers, one small pure function each.

THE RULE: every function returns the value **and** its uncertainty wherever an
uncertainty is meaningful. A number without an error bar is not a measurement,
it is an opinion with decimal places. Four motors whose measured gains differ
by 3% mean nothing if the per-motor uncertainty is 5%; the whole argument of
Story 1.5 rests on the error bars being real.

Everything here is a pure function over numpy arrays or pandas frames. No file
I/O, no hardware, no globals. That is what makes it testable against
``synthetic.py`` with nothing plugged in.

Where an uncertainty is estimated rather than propagated, the estimate and its
justification are stated in the function's docstring and carried in the
``method`` field of the returned :class:`Measurement`, so the report can quote
it and the owner can defend it.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np
import pandas as pd

from .load import AnalysisError

TAU = 2.0 * math.pi


class MetricError(AnalysisError):
    """The data cannot support the metric being asked for."""


# --------------------------------------------------------------------------
# Value + uncertainty containers
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Measurement:
    """A number with an error bar, its unit, and how it was obtained."""

    value: float
    uncertainty: float
    unit: str = ""
    n: int = 0
    method: str = ""

    def __post_init__(self) -> None:
        if self.uncertainty < 0 or math.isnan(self.uncertainty):
            raise MetricError(
                f"uncertainty must be finite and >= 0, got {self.uncertainty!r}"
            )

    @property
    def relative(self) -> float:
        """Fractional uncertainty; ``inf`` for a zero-valued measurement."""
        if self.value == 0:
            return math.inf
        return abs(self.uncertainty / self.value)

    def render(self, digits: int = 4) -> str:
        unit = f" {self.unit}" if self.unit else ""
        return f"{self.value:.{digits}g} ± {self.uncertainty:.{digits}g}{unit}"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.render()

    def as_dict(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "uncertainty": self.uncertainty,
            "unit": self.unit,
            "n": self.n,
            "method": self.method,
        }


@dataclass(frozen=True)
class Aggregate:
    """Summary of a set of repeat values -- mean, spread, and extremes."""

    mean: float
    stddev: float
    sem: float
    minimum: float
    maximum: float
    n: int
    unit: str = ""

    @property
    def spread(self) -> float:
        """Peak-to-peak, which is what a reader of a repeatability claim wants."""
        return self.maximum - self.minimum

    def as_measurement(
        self, uncertainty: Literal["sem", "stddev"] = "sem", method: str = ""
    ) -> Measurement:
        """Collapse to a Measurement.

        ``sem`` answers "how well do we know the mean" (use for a best
        estimate); ``stddev`` answers "how much do repeats scatter" (use when
        quoting repeatability).
        """
        u = self.sem if uncertainty == "sem" else self.stddev
        return Measurement(
            value=self.mean,
            uncertainty=u,
            unit=self.unit,
            n=self.n,
            method=method or f"mean of {self.n} repeats, uncertainty = {uncertainty}",
        )


def aggregate(values: Iterable[float], unit: str = "") -> Aggregate:
    """Mean/stddev/sem/min/max of a set of repeats.

    Uses the sample standard deviation (ddof=1) because these are samples of a
    process, not a whole population. A single value has no spread, so its
    stddev and sem are reported as 0 -- honest, but a caller should not claim
    repeatability from n=1.
    """
    arr = np.asarray(list(values), dtype=float)
    if arr.size == 0:
        raise MetricError("aggregate() needs at least one value")
    stddev = float(arr.std(ddof=1)) if arr.size > 1 else 0.0
    sem = stddev / math.sqrt(arr.size) if arr.size > 1 else 0.0
    return Aggregate(
        mean=float(arr.mean()),
        stddev=stddev,
        sem=sem,
        minimum=float(arr.min()),
        maximum=float(arr.max()),
        n=int(arr.size),
        unit=unit,
    )


def combine_uncertainties(*terms: float) -> float:
    """Add independent uncertainties in quadrature."""
    return float(math.sqrt(sum(float(t) ** 2 for t in terms)))


def _finite_array(values: Any, name: str) -> np.ndarray:
    arr = np.asarray(values, dtype=float)
    if arr.ndim != 1:
        raise MetricError(f"{name} must be 1-D, got shape {arr.shape}")
    if arr.size == 0:
        raise MetricError(f"{name} is empty")
    if not np.isfinite(arr).all():
        raise MetricError(f"{name} contains NaN or inf")
    return arr


# --------------------------------------------------------------------------
# Encoder counts -> angular velocity
# --------------------------------------------------------------------------


def omega_from_counts(
    t_s: Any, counts: Any, ticks_per_output_rev: float, *, smooth_n: int = 1
) -> np.ndarray:
    """Output-shaft angular velocity, rad/s, from cumulative encoder counts.

    ``omega = 2*pi * dcounts / (ticks_per_output_rev * dt)``, backward
    difference, with the first sample repeated so the array keeps its length.

    ``smooth_n`` applies a centred moving average of that many samples
    afterwards. At 100 Hz with ~1400 ticks/rev the per-sample quantisation is
    coarse at low speed (one count in 10 ms is ~0.45 rad/s), which is exactly
    why the caller usually wants a plateau mean rather than a single sample.
    """
    t = _finite_array(t_s, "t_s")
    c = _finite_array(counts, "counts")
    if t.size != c.size:
        raise MetricError(f"t_s ({t.size}) and counts ({c.size}) differ in length")
    if ticks_per_output_rev <= 0:
        raise MetricError("ticks_per_output_rev must be > 0")
    if t.size < 2:
        raise MetricError("need at least 2 samples to differentiate")

    dt = np.diff(t)
    if (dt <= 0).any():
        raise MetricError("t_s must be strictly increasing to differentiate")
    omega = TAU * np.diff(c) / (ticks_per_output_rev * dt)
    omega = np.concatenate(([omega[0]], omega))

    if smooth_n > 1:
        omega = moving_average(omega, int(smooth_n))
    return omega


def moving_average(values: Any, window: int) -> np.ndarray:
    """Centred moving average that does NOT corrupt the ends of the record.

    ``np.convolve(..., mode="same")`` zero-pads, which drags the first and last
    ``window//2`` samples toward zero -- and the last samples are exactly the
    steady-state tail every step-response metric is measured against. Padding
    with the edge value instead leaves the tail unbiased. (This was a real bug
    found on synthetic data: smoothing made the tail scatter *larger*.)
    """
    arr = _finite_array(values, "values")
    k = int(window)
    if k <= 1:
        return arr
    if k > arr.size:
        raise MetricError(f"smoothing window {k} exceeds the record length {arr.size}")
    left = k // 2
    right = k - 1 - left
    padded = np.pad(arr, (left, right), mode="edge")
    kernel = np.ones(k) / float(k)
    return np.convolve(padded, kernel, mode="valid")


def omega_quantisation_rad_s(dt_s: float, ticks_per_output_rev: float) -> float:
    """Speed resolution of a one-count-per-sample encoder difference.

    This is the floor on any single-sample speed measurement, and it is the
    reason plateau averaging exists.
    """
    if dt_s <= 0 or ticks_per_output_rev <= 0:
        raise MetricError("dt_s and ticks_per_output_rev must be > 0")
    return TAU / (ticks_per_output_rev * dt_s)


def scale_uncertainty_from_ticks(
    omega_rad_s: float, ticks_per_output_rev: float, ticks_uncertainty: float
) -> float:
    """Uncertainty in omega caused by not knowing ticks/rev.

    omega is inversely proportional to ticks_per_output_rev, so the fractional
    uncertainty carries straight across. With the literature spread unresolved
    (1100-1400 counts/output rev) this term DOMINATES every speed number, and
    it is a scale error common to all four motors -- which is why the four-motor
    *comparison* survives it even though the absolute rad/s numbers do not.
    """
    if ticks_per_output_rev <= 0:
        raise MetricError("ticks_per_output_rev must be > 0")
    return abs(omega_rad_s) * abs(ticks_uncertainty) / ticks_per_output_rev


# --------------------------------------------------------------------------
# Duty staircase -> (duty, omega) steady-state points
# --------------------------------------------------------------------------


def split_holds(duty_frac: Any, *, tolerance: float = 1e-9) -> list[tuple[int, int]]:
    """Split a duty staircase into ``(start, stop)`` index ranges, stop exclusive.

    A "hold" is a maximal run of consecutive samples at one commanded duty.
    """
    duty = _finite_array(duty_frac, "duty_frac")
    changes = np.flatnonzero(np.abs(np.diff(duty)) > tolerance) + 1
    bounds = np.concatenate(([0], changes, [duty.size]))
    return [(int(a), int(b)) for a, b in itertools.pairwise(bounds) if b > a]


def duty_staircase_steps(
    telemetry: pd.DataFrame,
    *,
    ticks_per_output_rev: float,
    settle_frac: float = 0.5,
    min_samples: int = 4,
) -> pd.DataFrame:
    """Reduce a duty-staircase run to one steady-state point per duty level.

    The first ``settle_frac`` of each hold is discarded so the motor's
    first-order rise is not averaged into its plateau. What comes back is one
    row per hold with the plateau mean, its scatter, and its standard error.

    Returned columns: ``duty_frac``, ``omega_rad_s``, ``omega_stddev_rad_s``,
    ``omega_sem_rad_s``, ``n``, ``t_start_s``, ``t_end_s``.
    """
    for column in ("t_s", "duty_frac", "counts"):
        if column not in telemetry.columns:
            raise MetricError(f"telemetry is missing required column {column!r}")
    if not 0.0 <= settle_frac < 1.0:
        raise MetricError("settle_frac must be in [0, 1)")

    t = telemetry["t_s"].to_numpy(dtype=float)
    omega = omega_from_counts(t, telemetry["counts"].to_numpy(dtype=float),
                              ticks_per_output_rev)
    duty = telemetry["duty_frac"].to_numpy(dtype=float)

    rows: list[dict[str, Any]] = []
    for start, stop in split_holds(duty):
        length = stop - start
        skip = round(length * settle_frac)
        # Always drop at least the first sample: its backward difference
        # straddles the duty change and belongs to neither hold.
        plateau_start = start + max(skip, 1)
        if stop - plateau_start < min_samples:
            continue
        segment = omega[plateau_start:stop]
        stats = aggregate(segment, unit="rad/s")
        rows.append(
            {
                "duty_frac": float(duty[start]),
                "omega_rad_s": stats.mean,
                "omega_stddev_rad_s": stats.stddev,
                "omega_sem_rad_s": stats.sem,
                "n": stats.n,
                "t_start_s": float(t[plateau_start]),
                "t_end_s": float(t[stop - 1]),
            }
        )
    if not rows:
        raise MetricError(
            f"no duty hold had {min_samples} usable samples after discarding "
            f"the first {settle_frac:.0%} for settling"
        )
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------
# Deadband
# --------------------------------------------------------------------------


def deadband_duty_frac(
    duty_frac: Any,
    omega_rad_s: Any,
    *,
    omega_threshold_rad_s: float,
    direction: Literal["forward", "reverse"] = "forward",
) -> Measurement:
    """Duty at which the shaft first turns, for one direction.

    The staircase only ever brackets the deadband between the largest duty
    that did not move and the smallest that did. The honest answer is the
    midpoint of that bracket, with half the bracket width as the uncertainty:
    the measurement is limited by the duty step size, not by noise.

    ``omega_threshold_rad_s`` should be set above the encoder quantisation
    floor (see :func:`omega_quantisation_rad_s`) or noise will read as motion.
    """
    duty = _finite_array(duty_frac, "duty_frac")
    omega = _finite_array(omega_rad_s, "omega_rad_s")
    if duty.size != omega.size:
        raise MetricError("duty_frac and omega_rad_s differ in length")
    if omega_threshold_rad_s <= 0:
        raise MetricError("omega_threshold_rad_s must be > 0")

    sign = 1.0 if direction == "forward" else -1.0
    mask = np.sign(duty) == sign
    if not mask.any():
        raise MetricError(f"no {direction} (sign {sign:+.0f}) duty points in this run")

    d = np.abs(duty[mask])
    w = np.abs(omega[mask])
    order = np.argsort(d)
    d, w = d[order], w[order]

    moving = w >= omega_threshold_rad_s
    if not moving.any():
        raise MetricError(
            f"the motor never exceeded {omega_threshold_rad_s} rad/s in the "
            f"{direction} direction; the deadband is above the highest duty tested "
            f"({d.max():.3f}), or the motor/encoder is not working"
        )

    first = int(np.argmax(moving))
    if first == 0:
        # Already moving at the smallest duty tested: the deadband is bounded
        # above only. Report the midpoint of [0, d0] with a matching error bar
        # and say so, rather than pretending to a bracket that was not measured.
        upper = float(d[0])
        return Measurement(
            value=upper / 2.0,
            uncertainty=upper / 2.0,
            unit="duty_frac",
            n=int(d.size),
            method=(
                "UNBRACKETED: moving already at the smallest duty tested "
                f"({upper:.3f}); value is the midpoint of [0, {upper:.3f}]. "
                "Re-run with smaller duty steps to bracket it."
            ),
        )

    low = float(d[first - 1])
    high = float(d[first])
    return Measurement(
        value=(low + high) / 2.0,
        uncertainty=(high - low) / 2.0,
        unit="duty_frac",
        n=int(d.size),
        method=(
            f"bracketed between {low:.3f} (still) and {high:.3f} (moving) at "
            f"threshold {omega_threshold_rad_s:g} rad/s; uncertainty = half the "
            "duty step, i.e. resolution-limited"
        ),
    )


def deadband_spread(measurements: Sequence[Measurement]) -> Aggregate:
    """Spread of a deadband across repeats of the same experiment.

    Repeatability is the point: a deadband that moves 0.02 between repeats
    cannot support a claim that two motors differing by 0.01 are different.
    """
    if not measurements:
        raise MetricError("deadband_spread() needs at least one measurement")
    return aggregate([m.value for m in measurements], unit="duty_frac")


# --------------------------------------------------------------------------
# duty -> omega curve
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class LinearFit:
    """An ordinary least-squares straight line, with standard errors."""

    gain: float
    gain_stderr: float
    intercept: float
    intercept_stderr: float
    r_squared: float
    residual_rms: float
    n: int
    x_min: float
    x_max: float
    gain_unit: str = ""
    intercept_unit: str = ""

    def predict(self, x: Any) -> np.ndarray:
        return self.gain * np.asarray(x, dtype=float) + self.intercept

    def gain_measurement(self) -> Measurement:
        return Measurement(
            value=self.gain,
            uncertainty=self.gain_stderr,
            unit=self.gain_unit,
            n=self.n,
            method=f"OLS slope over duty {self.x_min:.3f}..{self.x_max:.3f}",
        )

    def intercept_measurement(self) -> Measurement:
        return Measurement(
            value=self.intercept,
            uncertainty=self.intercept_stderr,
            unit=self.intercept_unit,
            n=self.n,
            method="OLS intercept (extrapolated to duty = 0)",
        )

    def x_at_zero(self) -> Measurement:
        """Duty at which the fitted line crosses omega = 0.

        For a motor this is the *fitted* deadband -- the x-intercept of the
        linear region extrapolated down. It is a different estimator from the
        directly bracketed :func:`deadband_duty_frac`, and comparing the two is
        a genuine consistency check on the whole curve.
        """
        if self.gain == 0:
            raise MetricError("gain is zero; the line never crosses zero")
        x0 = -self.intercept / self.gain
        # Propagate both standard errors, treating them as independent. They
        # are in truth anti-correlated in OLS, so this slightly over-states the
        # error bar -- the conservative direction.
        u = abs(x0) * combine_uncertainties(
            self.intercept_stderr / self.intercept if self.intercept else 0.0,
            self.gain_stderr / self.gain,
        )
        return Measurement(
            value=float(x0),
            uncertainty=float(u),
            unit="duty_frac",
            n=self.n,
            method="x-intercept of the linear-region fit (fitted deadband)",
        )


def linear_fit(
    x: Any, y: Any, *, gain_unit: str = "", intercept_unit: str = ""
) -> LinearFit:
    """OLS straight-line fit with textbook standard errors.

    ``se_slope = sqrt(s^2 / Sxx)``, ``se_intercept = sqrt(s^2 (1/n + xbar^2/Sxx))``
    with ``s^2 = SSR/(n-2)``. Deliberately not weighted: the plateau SEMs are
    all of similar size, and an unweighted fit is the one the owner can derive
    on paper if an interviewer asks.
    """
    xa = _finite_array(x, "x")
    ya = _finite_array(y, "y")
    if xa.size != ya.size:
        raise MetricError("x and y differ in length")
    if xa.size < 3:
        raise MetricError(f"need >= 3 points for a fit with error bars, got {xa.size}")

    xbar = float(xa.mean())
    sxx = float(((xa - xbar) ** 2).sum())
    if sxx == 0:
        raise MetricError("all x values are identical; slope is undefined")

    gain = float(((xa - xbar) * (ya - ya.mean())).sum() / sxx)
    intercept = float(ya.mean() - gain * xbar)
    residuals = ya - (gain * xa + intercept)
    ssr = float((residuals**2).sum())
    dof = xa.size - 2
    s_squared = ssr / dof
    sst = float(((ya - ya.mean()) ** 2).sum())

    return LinearFit(
        gain=gain,
        gain_stderr=float(math.sqrt(s_squared / sxx)),
        intercept=intercept,
        intercept_stderr=float(math.sqrt(s_squared * (1.0 / xa.size + xbar**2 / sxx))),
        r_squared=float(1.0 - ssr / sst) if sst > 0 else float("nan"),
        residual_rms=float(math.sqrt(ssr / xa.size)),
        n=int(xa.size),
        x_min=float(xa.min()),
        x_max=float(xa.max()),
        gain_unit=gain_unit,
        intercept_unit=intercept_unit,
    )


def find_linear_region(
    duty_frac: Any,
    omega_rad_s: Any,
    *,
    deadband: float = 0.0,
    seed_points: int = 4,
    slope_tol_frac: float = 0.30,
) -> np.ndarray:
    """Boolean mask of the points that belong to the straight part of the curve.

    Boring and inspectable, on purpose: take the mean omega at each distinct
    duty level above the deadband, take the median secant slope of the first
    ``seed_points`` levels as the reference, then walk outward and stop at the
    first level whose secant slope falls below ``(1 - slope_tol_frac)`` of it.
    That stopping point is the onset of saturation -- the motor running out of
    supply voltage, not out of duty.

    Duplicate duty levels (which is what repeats of the same staircase produce)
    are collapsed before the secants are taken. Without that, ``diff(duty)``
    contains zeros and every secant is an infinity.
    """
    duty = np.abs(_finite_array(duty_frac, "duty_frac"))
    omega = np.abs(_finite_array(omega_rad_s, "omega_rad_s"))
    if duty.size != omega.size:
        raise MetricError("duty_frac and omega_rad_s differ in length")

    above = duty > deadband
    if not above.any():
        return np.zeros(duty.size, dtype=bool)

    levels, inverse = np.unique(duty[above], return_inverse=True)
    mean_omega = np.bincount(inverse, weights=omega[above]) / np.bincount(inverse)

    if levels.size < seed_points + 1:
        # Too few distinct levels to detect saturation; keep everything.
        return above

    secants = np.diff(mean_omega) / np.diff(levels)
    reference = float(np.median(secants[: max(seed_points - 1, 1)]))
    if reference <= 0:
        return above

    cutoff = float(levels[-1])
    for i in range(seed_points - 1, secants.size):
        if secants[i] < reference * (1.0 - slope_tol_frac):
            cutoff = float(levels[i])
            break

    return above & (duty <= cutoff)


@dataclass(frozen=True)
class DutyOmegaModel:
    """Fitted duty -> omega behaviour for one motor in one direction."""

    direction: str
    deadband: Measurement
    fit: LinearFit
    saturation_onset_duty_frac: float | None
    omega_max: Measurement | None
    duty_used: np.ndarray = field(repr=False, default_factory=lambda: np.array([]))
    omega_used: np.ndarray = field(repr=False, default_factory=lambda: np.array([]))

    @property
    def gain(self) -> Measurement:
        return self.fit.gain_measurement()

    @property
    def intercept(self) -> Measurement:
        return self.fit.intercept_measurement()


def fit_duty_to_omega(
    steps: pd.DataFrame,
    *,
    direction: Literal["forward", "reverse"] = "forward",
    omega_threshold_rad_s: float = 0.5,
    slope_tol_frac: float = 0.30,
) -> DutyOmegaModel:
    """Full duty->omega characterisation for one direction of one motor.

    Takes the output of :func:`duty_staircase_steps`, brackets the deadband,
    finds the linear region, fits it, and reports the saturation plateau if the
    staircase went high enough to reach one.

    Sign convention: both duty and omega are folded to positive magnitudes, so
    the reported gain is positive in both directions and the two directions are
    directly comparable. (Asymmetry between directions is a real and expected
    finding for a brushed motor with a gearbox.)
    """
    for column in ("duty_frac", "omega_rad_s"):
        if column not in steps.columns:
            raise MetricError(f"steps frame is missing column {column!r}")

    sign = 1.0 if direction == "forward" else -1.0
    subset = steps[np.sign(steps["duty_frac"].to_numpy()) == sign]
    if len(subset) < 3:
        raise MetricError(
            f"only {len(subset)} {direction} duty points; need >= 3 to fit a line "
            f"with an error bar"
        )

    duty = np.abs(subset["duty_frac"].to_numpy(dtype=float))
    omega = np.abs(subset["omega_rad_s"].to_numpy(dtype=float))

    deadband = deadband_duty_frac(
        subset["duty_frac"].to_numpy(dtype=float),
        subset["omega_rad_s"].to_numpy(dtype=float),
        omega_threshold_rad_s=omega_threshold_rad_s,
        direction=direction,
    )

    mask = find_linear_region(
        duty, omega, deadband=deadband.value, slope_tol_frac=slope_tol_frac
    )
    if mask.sum() < 3:
        raise MetricError(
            f"only {int(mask.sum())} points survive deadband + linear-region "
            f"selection for {direction}; the staircase is too coarse to fit"
        )

    fit = linear_fit(
        duty[mask],
        omega[mask],
        gain_unit="rad/s per duty_frac",
        intercept_unit="rad/s",
    )

    saturated = (~mask) & (duty > deadband.value)
    onset: float | None = None
    omega_max: Measurement | None = None
    if saturated.any():
        onset = float(duty[saturated].min())
        plateau = aggregate(omega[saturated], unit="rad/s")
        omega_max = plateau.as_measurement(
            "stddev" if plateau.n > 1 else "sem",
            method=f"mean of {plateau.n} plateau point(s) above duty {onset:.3f}",
        )

    return DutyOmegaModel(
        direction=direction,
        deadband=deadband,
        fit=fit,
        saturation_onset_duty_frac=onset,
        omega_max=omega_max,
        duty_used=duty[mask],
        omega_used=omega[mask],
    )


# --------------------------------------------------------------------------
# Step response
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class StepResponse:
    """Classic step-response metrics, each with an uncertainty."""

    rise_time_s: Measurement
    overshoot_frac: Measurement
    settling_time_s: Measurement
    steady_state_error: Measurement
    steady_state_value: Measurement
    setpoint: float
    initial_value: float
    t_step_s: float
    settle_band_frac: float
    n: int

    def as_rows(self) -> list[tuple[str, Measurement]]:
        return [
            ("rise_time_s", self.rise_time_s),
            ("overshoot_frac", self.overshoot_frac),
            ("settling_time_s", self.settling_time_s),
            ("steady_state_error", self.steady_state_error),
            ("steady_state_value", self.steady_state_value),
        ]


def _crossing_time(t: np.ndarray, y: np.ndarray, level: float, rising: bool) -> float | None:
    """First time ``y`` crosses ``level``, linearly interpolated between samples."""
    if rising:
        hits = np.flatnonzero(y >= level)
    else:
        hits = np.flatnonzero(y <= level)
    if hits.size == 0:
        return None
    i = int(hits[0])
    if i == 0:
        return float(t[0])
    y0, y1 = float(y[i - 1]), float(y[i])
    if y1 == y0:
        return float(t[i])
    frac = (level - y0) / (y1 - y0)
    return float(t[i - 1] + frac * (t[i] - t[i - 1]))


def step_response(
    t_s: Any,
    y: Any,
    *,
    setpoint: float,
    t_step_s: float | None = None,
    rise_low_frac: float = 0.10,
    rise_high_frac: float = 0.90,
    settle_band_frac: float = 0.05,
    tail_frac: float = 0.20,
) -> StepResponse:
    """Rise time, overshoot, settling time and steady-state error.

    Conventions, stated because every textbook picks slightly different ones:

    * rise time is 10%->90% of the *achieved* steady state, not of the
      setpoint. A loop with 20% steady-state error would otherwise never reach
      90% and report an infinite rise time.
    * overshoot is ``(peak - steady_state) / (steady_state - initial)``, i.e. a
      fraction of the step actually taken. Never negative.
    * settling time is measured from the step, and is the time after which
      ``|y - steady_state|`` stays inside ``settle_band_frac`` of the steady
      state for the rest of the record.
    * steady state is the mean of the last ``tail_frac`` of the record.

    Uncertainties: the two time metrics are quantised by the sample period
    (100 Hz loop => 10 ms), which dominates; the amplitude metrics propagate
    the scatter of the steady-state tail.
    """
    t = _finite_array(t_s, "t_s")
    ya = _finite_array(y, "y")
    if t.size != ya.size:
        raise MetricError("t_s and y differ in length")
    if t.size < 5:
        raise MetricError("need >= 5 samples for a step response")
    if not 0.0 < tail_frac < 1.0:
        raise MetricError("tail_frac must be in (0, 1)")

    dt = float(np.median(np.diff(t)))
    if dt <= 0:
        raise MetricError("t_s must be increasing")

    t0 = float(t[0]) if t_step_s is None else float(t_step_s)
    pre = ya[t < t0]
    initial = float(pre.mean()) if pre.size else float(ya[0])

    post_mask = t >= t0
    tp = t[post_mask]
    yp = ya[post_mask]
    if tp.size < 4:
        raise MetricError("fewer than 4 samples after t_step_s")

    tail_start = max(int(tp.size * (1.0 - tail_frac)), 1)
    tail = aggregate(yp[tail_start:], unit="rad/s")
    ss = tail.mean
    step_size = ss - initial
    if step_size == 0:
        raise MetricError("steady state equals the initial value; there is no step")

    ss_measurement = Measurement(
        value=ss,
        uncertainty=max(tail.sem, 0.0),
        unit="rad/s",
        n=tail.n,
        method=f"mean of the last {tail_frac:.0%} of the record ({tail.n} samples)",
    )

    # -- rise time ---------------------------------------------------------
    rising = step_size > 0
    lo_level = initial + rise_low_frac * step_size
    hi_level = initial + rise_high_frac * step_size
    t_lo = _crossing_time(tp, yp, lo_level, rising)
    t_hi = _crossing_time(tp, yp, hi_level, rising)
    if t_lo is None or t_hi is None:
        raise MetricError(
            f"the response never crossed the {rise_low_frac:.0%}/{rise_high_frac:.0%} "
            f"levels; it may not have settled inside the record"
        )
    rise = Measurement(
        value=max(float(t_hi - t_lo), 0.0),
        uncertainty=dt,
        unit="s",
        n=int(tp.size),
        method=(
            f"{rise_low_frac:.0%}-{rise_high_frac:.0%} of the achieved steady state, "
            f"linearly interpolated; uncertainty = one sample period ({dt * 1e3:.1f} ms)"
        ),
    )

    # -- overshoot ---------------------------------------------------------
    peak = float(yp.max()) if rising else float(yp.min())
    overshoot_value = max((peak - ss) / step_size, 0.0)
    noise = tail.stddev
    overshoot_u = combine_uncertainties(
        noise / abs(step_size),  # a noisy peak reads high
        abs(peak - ss) * tail.sem / step_size**2,  # uncertainty in ss itself
    )
    overshoot = Measurement(
        value=overshoot_value,
        uncertainty=overshoot_u,
        unit="fraction of step",
        n=int(tp.size),
        method=(
            "(peak - steady_state) / (steady_state - initial), clipped at 0; "
            "uncertainty from the steady-state scatter"
        ),
    )

    # -- settling time -----------------------------------------------------
    band = settle_band_frac * abs(ss if ss != 0 else step_size)
    if tail.stddev > band:
        # A settling band narrower than the measurement noise is not a
        # settling time, it is a noise-crossing time. At 100 Hz with ~1400
        # ticks/rev the single-sample speed resolution is ~0.45 rad/s, so a 2%
        # band on a 15 rad/s setpoint (0.30 rad/s) is BELOW the resolution
        # floor. Smooth the speed first, or widen the band, and say which.
        raise MetricError(
            f"settling band ±{band:.3g} (={settle_band_frac:.0%} of the steady "
            f"state) is inside the measurement noise (tail stddev "
            f"{tail.stddev:.3g}). Any 'settling time' from this record would be "
            f"the last noise excursion, not the transient. Smooth omega "
            f"(metrics.moving_average / omega_from_counts(smooth_n=...)) or "
            f"widen settle_band_frac, and state which in the report."
        )
    outside = np.flatnonzero(np.abs(yp - ss) > band)
    if outside.size == 0:
        settle_value = 0.0
        settle_note = "already inside the band at the step"
    else:
        last = int(outside[-1])
        if last + 1 >= tp.size:
            raise MetricError(
                f"the response is still outside the ±{settle_band_frac:.0%} band at "
                f"the end of the record; capture for longer before claiming a "
                f"settling time"
            )
        settle_value = float(tp[last + 1] - t0)
        settle_note = f"first entry into the ±{settle_band_frac:.0%} band it never leaves"
    settling = Measurement(
        value=settle_value,
        uncertainty=dt,
        unit="s",
        n=int(tp.size),
        method=f"{settle_note}; uncertainty = one sample period ({dt * 1e3:.1f} ms)",
    )

    # -- steady-state error ------------------------------------------------
    error = Measurement(
        value=float(setpoint - ss),
        uncertainty=max(tail.sem, 0.0),
        unit="rad/s",
        n=tail.n,
        method="setpoint minus the mean of the steady-state tail",
    )

    return StepResponse(
        rise_time_s=rise,
        overshoot_frac=overshoot,
        settling_time_s=settling,
        steady_state_error=error,
        steady_state_value=ss_measurement,
        setpoint=float(setpoint),
        initial_value=initial,
        t_step_s=t0,
        settle_band_frac=settle_band_frac,
        n=int(tp.size),
    )


def find_step_time(t_s: Any, setpoint_rad_s: Any) -> float:
    """Time of the first change in the setpoint column."""
    t = _finite_array(t_s, "t_s")
    sp = _finite_array(setpoint_rad_s, "setpoint_rad_s")
    if t.size != sp.size:
        raise MetricError("t_s and setpoint_rad_s differ in length")
    changes = np.flatnonzero(np.diff(sp) != 0)
    if changes.size == 0:
        raise MetricError("the setpoint never changes; there is no step in this run")
    return float(t[int(changes[0]) + 1])


# --------------------------------------------------------------------------
# Loop timing from the analyser (D6 LOOP_TICK, D7 COMPUTE_BUSY)
# --------------------------------------------------------------------------


def edge_times(
    t_s: Any, level: Any, *, edge: Literal["rising", "falling", "both"] = "both"
) -> np.ndarray:
    """Times of digital transitions on one analyser channel.

    The time returned is that of the first sample at the new level, so every
    edge carries up to one sample period of late bias. That bias is common to
    all edges and therefore cancels in a *period*; it is why the jitter
    uncertainty below is a sample period and not zero.
    """
    t = _finite_array(t_s, "t_s")
    lv = np.asarray(level)
    lv = (np.asarray(lv, dtype=float) != 0).astype(np.int8)
    if t.size != lv.size:
        raise MetricError("t_s and level differ in length")
    if t.size < 2:
        return np.array([], dtype=float)

    delta = np.diff(lv.astype(np.int16))
    if edge == "rising":
        idx = np.flatnonzero(delta > 0) + 1
    elif edge == "falling":
        idx = np.flatnonzero(delta < 0) + 1
    else:
        idx = np.flatnonzero(delta != 0) + 1
    return t[idx]


@dataclass(frozen=True)
class LoopJitter:
    """Control-loop period statistics measured on the LOOP_TICK line."""

    mean_s: float
    stddev_s: float
    min_s: float
    max_s: float
    p99_s: float
    n: int
    target_s: float | None
    sample_period_s: float | None
    periods_s: np.ndarray = field(repr=False, default_factory=lambda: np.array([]))

    @property
    def worst_deviation_s(self) -> float | None:
        """Largest absolute departure from the target period."""
        if self.target_s is None or self.periods_s.size == 0:
            return None
        return float(np.abs(self.periods_s - self.target_s).max())

    def mean_measurement(self) -> Measurement:
        """Mean period with the uncertainty that matters for a *mean*.

        The standard error of the mean, widened by the analyser's own sample
        period (a systematic that does not average away).
        """
        sem = self.stddev_s / math.sqrt(self.n) if self.n > 1 else 0.0
        systematic = (self.sample_period_s or 0.0)
        return Measurement(
            value=self.mean_s,
            uncertainty=combine_uncertainties(sem, systematic),
            unit="s",
            n=self.n,
            method=(
                "mean of LOOP_TICK periods; uncertainty = SEM combined in "
                "quadrature with one analyser sample period"
            ),
        )

    def jitter_measurement(self) -> Measurement:
        """Jitter itself, i.e. the spread, quoted as a value with no error bar
        of its own beyond the sampling resolution."""
        return Measurement(
            value=self.stddev_s,
            uncertainty=(self.sample_period_s or 0.0),
            unit="s",
            n=self.n,
            method="sample stddev of LOOP_TICK periods (ddof=1)",
        )


def loop_periods_s(
    t_s: Any,
    loop_tick_level: Any,
    *,
    mode: Literal["toggle", "pulse"] = "toggle",
) -> np.ndarray:
    """Per-iteration control-loop periods from the LOOP_TICK channel.

    ``mode="toggle"`` (the default, and what the firmware ground truth
    specifies -- "toggles once per PID iteration") means the line *flips* every
    iteration, so consecutive edges of EITHER polarity are one iteration apart.
    Counting only rising edges here would report exactly double the period and
    halve the apparent rate. That factor of two is the single easiest way to
    misread this line, so it is a named parameter rather than an assumption.

    ``mode="pulse"`` is for firmware that emits a short pulse per iteration
    instead; then rising edge to rising edge is one iteration.
    """
    edge = "both" if mode == "toggle" else "rising"
    times = edge_times(t_s, loop_tick_level, edge=edge)
    if times.size < 2:
        raise MetricError(
            f"only {times.size} LOOP_TICK edge(s) in this capture; need >= 2 to "
            f"measure a period. Is D6 connected, and is the capture long enough?"
        )
    return np.diff(times)


def loop_jitter(
    periods_s: Any, *, target_s: float | None = None, sample_period_s: float | None = None
) -> LoopJitter:
    """Mean / stddev / min / max / p99 of a set of loop periods."""
    periods = _finite_array(periods_s, "periods_s")
    if periods.size < 2:
        raise MetricError("need >= 2 periods for jitter statistics")
    return LoopJitter(
        mean_s=float(periods.mean()),
        stddev_s=float(periods.std(ddof=1)),
        min_s=float(periods.min()),
        max_s=float(periods.max()),
        p99_s=float(np.percentile(periods, 99)),
        n=int(periods.size),
        target_s=target_s,
        sample_period_s=sample_period_s,
        periods_s=periods,
    )


def jitter_histogram(
    periods_s: Any, *, bins: int = 40, range_s: tuple[float, float] | None = None
) -> tuple[np.ndarray, np.ndarray]:
    """Histogram of loop periods: ``(counts, bin_edges)``."""
    periods = _finite_array(periods_s, "periods_s")
    counts, edges = np.histogram(periods, bins=bins, range=range_s)
    return counts, edges


def cpu_duty_frac(
    t_s: Any,
    compute_busy_level: Any,
    *,
    loop_tick_level: Any = None,
    mode: Literal["toggle", "pulse"] = "toggle",
) -> Measurement:
    """Fraction of each control period spent inside the loop body (D7 HIGH).

    Two estimators, and which one you get depends on what you pass:

    * with ``loop_tick_level``: the busy fraction is computed *per iteration*
      and the answer is the mean over iterations, with the scatter across
      iterations as the uncertainty. This is the useful one -- it exposes the
      worst-case iteration, which is what actually threatens the deadline.
    * without it: the overall HIGH sample fraction, with the uncertainty taken
      as one sample period divided by the record length (pure quantisation).

    A cpu_duty_frac near 1.0 means the loop has no headroom and the 100 Hz
    deadline is about to be missed.
    """
    t = _finite_array(t_s, "t_s")
    busy = (np.asarray(compute_busy_level, dtype=float) != 0).astype(np.int8)
    if t.size != busy.size:
        raise MetricError("t_s and compute_busy_level differ in length")
    if t.size < 2:
        raise MetricError("need >= 2 samples")

    if loop_tick_level is None:
        fraction = float(busy.mean())
        span = float(t[-1] - t[0])
        dt = span / max(t.size - 1, 1)
        return Measurement(
            value=fraction,
            uncertainty=dt / span if span > 0 else 0.0,
            unit="fraction",
            n=int(t.size),
            method="HIGH samples / total samples over the whole capture",
        )

    edge = "both" if mode == "toggle" else "rising"
    ticks = edge_times(t, loop_tick_level, edge=edge)
    if ticks.size < 3:
        raise MetricError(
            f"only {ticks.size} LOOP_TICK edge(s); need >= 3 to bound whole iterations"
        )

    fractions: list[float] = []
    for start, stop in itertools.pairwise(ticks):
        window = (t >= start) & (t < stop)
        if window.sum() == 0:
            continue
        fractions.append(float(busy[window].mean()))
    if not fractions:
        raise MetricError("no analyser samples fell inside a loop iteration")

    stats = aggregate(fractions, unit="fraction")
    return stats.as_measurement(
        "stddev",
        method=(
            f"mean over {stats.n} loop iterations of (D7 HIGH time / period); "
            f"uncertainty = scatter across iterations; worst iteration "
            f"{stats.maximum:.3f}"
        ),
    )


# --------------------------------------------------------------------------
# ticks per revolution
# --------------------------------------------------------------------------


def ticks_per_rev(
    counts_per_trial: Sequence[float],
    *,
    revolutions: float = 1.0,
    revolutions_uncertainty: float = 0.02,
    count_uncertainty: float = 2.0,
) -> Measurement:
    """Encoder counts per OUTPUT-shaft revolution, with an explicit error bar.

    Method (Story 1.4): mark the output shaft, turn it exactly ``revolutions``
    turns by hand, read the counter. Repeat.

    Three independent error terms, combined in quadrature:

    1. **Repeat scatter** -- the standard error of the mean across trials.
    2. **Angular positioning** -- you cannot stop a hand-turned shaft exactly
       on the mark. The default 0.02 rev is about 7 degrees, which is a fair
       estimate for a pen mark against a fixed reference, and it scales the
       whole answer.
    3. **Count quantisation** -- ±2 counts per trial covers the start and end
       edge each being uncertain by one count.

    Terms 2 and 3 do NOT average away across trials: they are systematic per
    trial. Term 1 does. Turning more revolutions per trial shrinks both
    systematics linearly and is the cheapest available improvement.

    The result supersedes both literature figures (Adafruit's 14 counts/motor
    rev and the retailers' 11), which disagree and therefore cannot both be
    used. Until this is measured, ``load.Run.ticks_per_output_rev_uncertainty``
    defaults to the full width of that disagreement.
    """
    values = list(counts_per_trial)
    if not values:
        raise MetricError("ticks_per_rev() needs at least one trial")
    if revolutions <= 0:
        raise MetricError("revolutions must be > 0")

    per_trial = [abs(float(c)) / revolutions for c in values]
    stats = aggregate(per_trial, unit="ticks/output rev")

    u_scatter = stats.sem
    u_angle = stats.mean * (revolutions_uncertainty / revolutions)
    u_quant = count_uncertainty / revolutions

    return Measurement(
        value=stats.mean,
        uncertainty=combine_uncertainties(u_scatter, u_angle, u_quant),
        unit="ticks per output revolution",
        n=stats.n,
        method=(
            f"{stats.n} hand-turn trial(s) of {revolutions:g} rev; "
            f"uncertainty = quadrature(SEM {u_scatter:.1f}, "
            f"angle {u_angle:.1f}, quantisation {u_quant:.1f})"
        ),
    )


def ticks_per_motor_rev(measured: Measurement, gear_ratio: float) -> Measurement:
    """Convert ticks/output-rev to ticks/motor-rev, for comparison with the
    datasheet claims (11 vs 14) which are quoted on the motor shaft."""
    if gear_ratio <= 0:
        raise MetricError("gear_ratio must be > 0")
    return Measurement(
        value=measured.value / gear_ratio,
        uncertainty=measured.uncertainty / gear_ratio,
        unit="ticks per motor revolution",
        n=measured.n,
        method=f"{measured.method}; divided by gear ratio {gear_ratio:g}",
    )


def counts_to_metres(
    ticks: Measurement, wheel_diameter_m: float, wheel_diameter_uncertainty_m: float = 0.0
) -> Measurement:
    """metres per count = pi * wheel_diameter / ticks_per_output_rev.

    The odometry constant from docs/HARDWARE.md §6.4. Both inputs are bench
    measurements, and both uncertainties propagate.
    """
    if wheel_diameter_m <= 0:
        raise MetricError("wheel_diameter_m must be > 0")
    if ticks.value <= 0:
        raise MetricError("ticks per rev must be > 0")
    value = math.pi * wheel_diameter_m / ticks.value
    relative = combine_uncertainties(
        wheel_diameter_uncertainty_m / wheel_diameter_m, ticks.uncertainty / ticks.value
    )
    return Measurement(
        value=value,
        uncertainty=value * relative,
        unit="m per count",
        n=ticks.n,
        method="pi * wheel_diameter / ticks_per_output_rev, both terms measured",
    )
