"""Matplotlib figures, one house style, written to ``experiments/plots/``.

HOUSE RULES (all four are enforceable by reading the code, not by taste):

1. **Headless.** The Agg backend is selected on import. Nothing here opens a
   window; figures are files. Same reason the logic analyser is driven by
   ``sigrok-cli`` and never PulseView.
2. **Colourblind-safe, and never colour alone.** Every series carries a colour
   *and* a marker *and* a linestyle. The palette below was checked by
   computation, not by eye -- see :func:`check_palette` and the test that calls
   it. Four series is the cap; a fifth would break the separation floors, and
   the rover has four wheels, so the cap is not a limitation.
3. **Axes are labelled with units, always.** ``duty_frac``, ``omega_rad_s``,
   ``ms``. Mixed units are a leading cause of control bugs (CLAUDE.md), and an
   unlabelled axis is how they get in.
4. **Readable at thumbnail size.** These figures go in a report and on
   LinkedIn. Large type, thick lines, direct labels on the series, and no
   decoration that stops working at 300 px wide.

The centrepiece is :func:`plot_four_motor_overlay`. Story 1.5's entire argument
-- four "identical" motors are measurably different, therefore open loop cannot
work -- is that one figure. It is worth making properly.
"""

from __future__ import annotations

import itertools
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")  # headless: import order matters, this must precede pyplot

import matplotlib.pyplot as plt
import numpy as np

from .metrics import DutyOmegaModel, LoopJitter, Measurement, StepResponse

#: Default output directory: <repo>/experiments/plots
PLOTS_DIR = Path(__file__).resolve().parents[2] / "experiments" / "plots"

#: Categorical series colours, in fixed assignment order.
#:
#: Slots 1, 2, 3 and 7 of the reference categorical palette. Slot 4 (yellow
#: ``#eda100``) is skipped deliberately: against slot 2 (orange) its
#: normal-vision separation is dE 13.7, below the 15 floor, and a four-motor
#: overlay is an all-pairs form -- every motor is compared with every other, not
#: just its neighbour in the legend.
#:
#: Measured all-pairs separation for the four below (OKLab dE x100, Machado
#: 2009 dichromacy at full severity): normal 16.3, protan 12.6, deutan 9.2,
#: tritan 9.6. Floors are 15 (normal) and 8 (CVD). :func:`check_palette`
#: recomputes this, and a test asserts it, so a future colour change cannot
#: quietly break it.
SERIES_COLOURS: tuple[str, ...] = ("#2a78d6", "#eb6834", "#1baf7a", "#4a3aa7")

#: Secondary encoding. Never rely on colour alone -- print, projectors and
#: colour vision deficiency all remove it.
SERIES_MARKERS: tuple[str, ...] = ("o", "s", "^", "D")
SERIES_LINESTYLES: tuple[Any, ...] = ("-", "--", "-.", (0, (1, 1)))

#: Ink and surface. Text wears text colours, never a series colour.
INK = "#0b0b0b"
INK_MUTED = "#52514e"
GRID = "#dcdcd8"
SURFACE = "#ffffff"
REFERENCE = "#52514e"  # target lines, setpoints: neutral, never a series hue

MAX_SERIES = len(SERIES_COLOURS)


class PlotError(ValueError):
    """A figure cannot be drawn as asked."""


# --------------------------------------------------------------------------
# House style
# --------------------------------------------------------------------------


def apply_house_style() -> None:
    """Set the rcParams every figure in this project shares.

    Called by every plotting function, so a figure drawn from a REPL looks the
    same as one drawn by ``report.py``. DejaVu Sans is matplotlib's bundled
    font: choosing a system font would make figures render differently on the
    laptop and the Pi.
    """
    plt.rcParams.update(
        {
            "figure.figsize": (7.6, 4.8),
            "figure.dpi": 110,
            "savefig.dpi": 200,
            "savefig.bbox": "tight",
            "savefig.facecolor": SURFACE,
            "figure.facecolor": SURFACE,
            "axes.facecolor": SURFACE,
            "font.family": "DejaVu Sans",
            "font.size": 11,
            "axes.titlesize": 13,
            "axes.titleweight": "bold",
            "axes.labelsize": 11.5,
            "axes.labelcolor": INK,
            "axes.edgecolor": INK_MUTED,
            "axes.linewidth": 0.9,
            "axes.grid": True,
            "axes.axisbelow": True,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "grid.color": GRID,
            "grid.linewidth": 0.8,
            "xtick.color": INK_MUTED,
            "ytick.color": INK_MUTED,
            "xtick.labelsize": 10.5,
            "ytick.labelsize": 10.5,
            "text.color": INK,
            "lines.linewidth": 2.0,
            "lines.markersize": 6.0,
            "legend.frameon": True,
            "legend.framealpha": 0.92,
            "legend.edgecolor": GRID,
            "legend.fontsize": 10.5,
        }
    )


def series_style(index: int) -> dict[str, Any]:
    """Colour + marker + linestyle for series ``index`` (0-based)."""
    if not 0 <= index < MAX_SERIES:
        raise PlotError(
            f"series index {index} is outside 0..{MAX_SERIES - 1}. The palette is "
            f"capped at {MAX_SERIES} series because a fifth colour cannot clear "
            f"the colour-vision separation floors. Facet instead of adding one."
        )
    return {
        "color": SERIES_COLOURS[index],
        "marker": SERIES_MARKERS[index],
        "linestyle": SERIES_LINESTYLES[index],
    }


def save_figure(fig: plt.Figure, path: Path | str) -> Path:
    """Write a figure and close it. Returns the path written."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path)
    plt.close(fig)
    return path


# --------------------------------------------------------------------------
# Palette validation -- computed, not eyeballed
# --------------------------------------------------------------------------

# Machado, Oliveira & Fernandes (2009) dichromacy matrices at severity 1.0,
# applied in linear RGB. Used to check that two series stay distinguishable
# for a reader with colour vision deficiency.
_CVD_MATRICES: dict[str, np.ndarray] = {
    "normal": np.eye(3),
    "protan": np.array(
        [[0.152286, 1.052583, -0.204868],
         [0.114503, 0.786281, 0.099216],
         [-0.003882, -0.048116, 1.051998]]
    ),
    "deutan": np.array(
        [[0.367322, 0.860646, -0.227968],
         [0.280085, 0.672501, 0.047413],
         [-0.011820, 0.042940, 0.968881]]
    ),
    "tritan": np.array(
        [[1.255528, -0.076749, -0.178779],
         [-0.078411, 0.930809, 0.147602],
         [0.004733, 0.691367, 0.303900]]
    ),
}

_OKLAB_M1 = np.array(
    [[0.4122214708, 0.5363325363, 0.0514459929],
     [0.2119034982, 0.6806995451, 0.1073969566],
     [0.0883024619, 0.2817188376, 0.6299787005]]
)
_OKLAB_M2 = np.array(
    [[0.2104542553, 0.7936177850, -0.0040720468],
     [1.9779984951, -2.4285922050, 0.4505937099],
     [0.0259040371, 0.7827717662, -0.8086757660]]
)


def _hex_to_linear_rgb(value: str) -> np.ndarray:
    text = value.lstrip("#")
    if len(text) != 6:
        raise PlotError(f"{value!r} is not a 6-digit hex colour")
    srgb = np.array([int(text[i: i + 2], 16) / 255.0 for i in (0, 2, 4)])
    return np.where(srgb <= 0.04045, srgb / 12.92, ((srgb + 0.055) / 1.055) ** 2.4)


def _linear_rgb_to_oklab(linear: np.ndarray) -> np.ndarray:
    lms = np.cbrt(np.clip(_OKLAB_M1 @ linear, 0.0, None))
    return _OKLAB_M2 @ lms


def check_palette(colours: Sequence[str] = SERIES_COLOURS) -> dict[str, float]:
    """Worst all-pairs OKLab separation (x100) under normal and CVD vision.

    Returns ``{"normal": dE, "protan": dE, "deutan": dE, "tritan": dE}``. The
    project's floors are 15 for normal vision and 8 for each CVD type. This is
    the computable part of "is this palette readable", so it is computed.
    """
    linear = [_hex_to_linear_rgb(c) for c in colours]
    worst: dict[str, float] = {}
    for mode, matrix in _CVD_MATRICES.items():
        labs = [_linear_rgb_to_oklab(np.clip(matrix @ rgb, 0.0, 1.0)) for rgb in linear]
        worst[mode] = min(
            float(np.linalg.norm(labs[i] - labs[j]) * 100.0)
            for i, j in itertools.combinations(range(len(labs)), 2)
        )
    return worst


# --------------------------------------------------------------------------
# Data carriers (plain, so the plotting functions stay pure)
# --------------------------------------------------------------------------


@dataclass
class MotorCurve:
    """One motor's measured duty->omega points, plus what was fitted to them."""

    motor_id: Any
    duty_frac: np.ndarray
    omega_rad_s: np.ndarray
    omega_err_rad_s: np.ndarray | None = None
    deadband: Measurement | None = None
    gain: Measurement | None = None
    label: str | None = None

    def display_label(self) -> str:
        return self.label or f"Motor {self.motor_id}"

    def sorted_by_duty(self) -> tuple[np.ndarray, np.ndarray, np.ndarray | None]:
        order = np.argsort(np.asarray(self.duty_frac, dtype=float))
        duty = np.asarray(self.duty_frac, dtype=float)[order]
        omega = np.asarray(self.omega_rad_s, dtype=float)[order]
        err = (
            np.asarray(self.omega_err_rad_s, dtype=float)[order]
            if self.omega_err_rad_s is not None
            else None
        )
        return duty, omega, err


@dataclass
class StepTrace:
    """One step-response trace, with its metrics if they were computed."""

    label: str
    t_s: np.ndarray
    omega_rad_s: np.ndarray
    setpoint_rad_s: float
    metrics: StepResponse | None = None
    t_step_s: float = 0.0


# --------------------------------------------------------------------------
# Figure 1 -- one motor, both directions, deadband marked
# --------------------------------------------------------------------------


def plot_duty_omega(
    forward: MotorCurve,
    reverse: MotorCurve | None = None,
    *,
    forward_model: DutyOmegaModel | None = None,
    reverse_model: DutyOmegaModel | None = None,
    path: Path | str,
    title: str | None = None,
) -> Path:
    """duty_frac -> omega_rad_s for one motor, both directions, deadband marked.

    Duty is plotted signed (reverse to the left of zero) because that is how it
    is commanded, and because the asymmetry between directions is a real result
    that a folded plot would hide.
    """
    apply_house_style()
    fig, ax = plt.subplots()

    for index, (curve, model, name) in enumerate(
        ((forward, forward_model, "forward"), (reverse, reverse_model, "reverse"))
    ):
        if curve is None:
            continue
        style = series_style(index)
        duty, omega, err = curve.sorted_by_duty()
        if err is not None:
            ax.errorbar(
                duty, omega, yerr=err, fmt="none", ecolor=style["color"],
                elinewidth=1.0, capsize=2.5, alpha=0.8, zorder=2,
            )
        ax.plot(
            duty, omega, label=f"{name} (measured)", markerfacecolor="white",
            markeredgewidth=1.6, zorder=3, **style,
        )
        if model is not None:
            sign = 1.0 if name == "forward" else -1.0
            fit_duty = np.linspace(model.fit.x_min, model.fit.x_max, 50)
            ax.plot(
                sign * fit_duty,
                sign * model.fit.predict(fit_duty),
                color=style["color"], linewidth=1.2, linestyle=":", alpha=0.9,
                zorder=1,
                label=(
                    f"{name} fit: {model.gain.value:.3g}"
                    f" ± {model.gain.uncertainty:.2g} rad/s per duty_frac"
                ),
            )
            _mark_deadband(
                ax,
                sign * model.deadband.value,
                model.deadband.uncertainty,
                style["color"],
                label=(
                    f"{name} deadband: {model.deadband.value:.3f}"
                    f" ± {model.deadband.uncertainty:.3f}"
                ),
            )

    ax.axhline(0.0, color=INK_MUTED, linewidth=0.9, zorder=0)
    ax.axvline(0.0, color=INK_MUTED, linewidth=0.9, zorder=0)
    ax.set_xlabel("commanded duty_frac  (negative = reverse)")
    ax.set_ylabel("measured omega_rad_s  (output shaft)")
    ax.set_title(
        title or f"Motor {forward.motor_id}: duty → angular velocity", pad=12
    )
    # The deadband numbers live in the legend rather than as in-plot text: an
    # annotation near the origin lands on top of the curve for any motor whose
    # deadband is small, which is all of them.
    ax.legend(loc="upper left", fontsize=9.0)
    fig.tight_layout()
    return save_figure(fig, path)


def _mark_deadband(
    ax, centre: float, half_width: float, colour: str, *, label: str
) -> None:
    """Shade the bracketed deadband; the numbers go in the legend."""
    ax.axvspan(
        centre - half_width, centre + half_width, color=colour, alpha=0.16,
        zorder=0, label=label,
    )
    ax.axvline(centre, color=colour, linewidth=1.1, linestyle=(0, (4, 3)), alpha=0.9)


# --------------------------------------------------------------------------
# Figure 2 -- THE FOUR-MOTOR OVERLAY (the centrepiece)
# --------------------------------------------------------------------------


def plot_four_motor_overlay(
    curves: Sequence[MotorCurve],
    *,
    path: Path | str,
    title: str = "Four “identical” N20 gearmotors are not identical",
    subtitle: str | None = None,
    reference_duty_frac: float = 0.60,
    direction_label: str = "forward",
) -> Path:
    """The Story 1.5 figure: every motor's duty→omega curve on one axis.

    Design decisions, and why:

    * **Direct labels at the right-hand end of each curve** as well as a
      legend, so identity survives a greyscale print and a thumbnail.
    * **A spread annotation at one reference duty**, stating the peak-to-peak
      difference in rad/s and as a percentage of the mean. That number is the
      argument for closed-loop control, so it is drawn, not left to the reader.
    * **Deadband ticks along the bottom axis**, because the motors differ in
      where they start turning as well as in how fast they go.
    * **No dual axis, no secondary scale.** One measure, one axis.
    """
    if not curves:
        raise PlotError("no motor curves to plot")
    if len(curves) > MAX_SERIES:
        raise PlotError(
            f"{len(curves)} motors, but the palette is capped at {MAX_SERIES}. "
            f"Facet into small multiples rather than inventing a fifth colour."
        )

    apply_house_style()
    fig, ax = plt.subplots(figsize=(8.2, 5.2))

    max_duty = 0.0
    for index, curve in enumerate(curves):
        style = series_style(index)
        duty, omega, err = curve.sorted_by_duty()
        duty, omega = np.abs(duty), np.abs(omega)
        max_duty = max(max_duty, float(duty.max()) if duty.size else 0.0)
        if err is not None:
            ax.errorbar(
                duty, omega, yerr=err, fmt="none", ecolor=style["color"],
                elinewidth=1.0, capsize=2.0, alpha=0.7, zorder=2,
            )
        ax.plot(
            duty, omega, markerfacecolor="white", markeredgewidth=1.6,
            markersize=6.5, zorder=3,
            label=_overlay_legend_label(curve), **style,
        )
        # Direct label at the end of the line: identity without the legend.
        if duty.size:
            ax.annotate(
                f"M{curve.motor_id}",
                xy=(float(duty[-1]), float(omega[-1])),
                xytext=(6, 0),
                textcoords="offset points",
                va="center",
                fontsize=10.5,
                fontweight="bold",
                color=style["color"],
            )
        if curve.deadband is not None:
            ax.plot(
                [curve.deadband.value], [0.0], marker="|", markersize=14,
                markeredgewidth=2.2, color=style["color"], zorder=4, clip_on=False,
            )

    _annotate_spread(ax, curves, reference_duty_frac)

    ax.set_xlabel("commanded duty_frac  (0 = stopped, 1 = full scale)")
    ax.set_ylabel("measured omega_rad_s  (output shaft)")
    ax.set_xlim(0.0, max_duty * 1.10 if max_duty else 1.0)
    ax.set_ylim(bottom=0.0)
    ax.set_title(title, pad=14)
    default_subtitle = (
        f"{direction_label} direction · steady-state plateau per duty step · "
        "error bars = 1 s.d. of the plateau · deadband marked ┃ on the axis"
    )
    ax.text(
        0.0, 1.015, subtitle or default_subtitle, transform=ax.transAxes,
        fontsize=9.5, color=INK_MUTED, va="bottom",
    )
    ax.legend(loc="upper left", fontsize=9.5)
    fig.tight_layout()
    return save_figure(fig, path)


def _overlay_legend_label(curve: MotorCurve) -> str:
    if curve.gain is None:
        return curve.display_label()
    return (
        f"{curve.display_label()}: "
        f"{curve.gain.value:.3g} ± {curve.gain.uncertainty:.2g} rad/s per duty_frac"
    )


def _annotate_spread(
    ax, curves: Sequence[MotorCurve], reference_duty_frac: float
) -> str | None:
    """Draw the spread bracket at one duty, and return a caption for it."""
    values: list[float] = []
    for curve in curves:
        duty, omega, _ = curve.sorted_by_duty()
        duty, omega = np.abs(duty), np.abs(omega)
        mask = duty > 0
        if mask.sum() < 2:
            continue
        if not (duty[mask].min() <= reference_duty_frac <= duty[mask].max()):
            continue
        values.append(float(np.interp(reference_duty_frac, duty[mask], omega[mask])))
    if len(values) < 2:
        return None

    low, high = min(values), max(values)
    mean = sum(values) / len(values)
    ax.vlines(
        reference_duty_frac, low, high, color=INK_MUTED, linewidth=1.4,
        linestyle=(0, (3, 2)), zorder=5,
    )
    for level in (low, high):
        ax.plot(
            [reference_duty_frac - 0.012, reference_duty_frac + 0.012], [level, level],
            color=INK_MUTED, linewidth=1.4, zorder=5,
        )
    percent = 100.0 * (high - low) / mean if mean else float("nan")
    # The label goes in the empty bottom-right corner with a leader line, not
    # beside the bracket: beside the bracket it lands on the curves.
    ax.annotate(
        f"spread at duty_frac {reference_duty_frac:.2f}:\n"
        f"{low:.1f} → {high:.1f} rad/s "
        f"= {high - low:.1f} rad/s ({percent:.0f} % of the mean)",
        xy=(reference_duty_frac, (low + high) / 2.0),
        xycoords="data",
        xytext=(0.98, 0.04),
        textcoords="axes fraction",
        ha="right",
        va="bottom",
        fontsize=10,
        color=INK,
        bbox={"boxstyle": "round,pad=0.35", "facecolor": SURFACE,
              "edgecolor": GRID, "linewidth": 0.9},
        arrowprops={"arrowstyle": "-", "color": INK_MUTED, "linewidth": 1.0,
                    "shrinkB": 3},
        zorder=6,
    )
    return (
        f"At duty_frac {reference_duty_frac:.2f}: "
        f"{low:.1f} → {high:.1f} rad/s across {len(values)} motors "
        f"({percent:.0f} % of the mean)"
    )


# --------------------------------------------------------------------------
# Figure 3 -- step response, before vs after tuning
# --------------------------------------------------------------------------


def plot_step_response(
    traces: Sequence[StepTrace],
    *,
    path: Path | str,
    title: str = "PID step response, before and after tuning",
    settle_band_frac: float = 0.05,
) -> Path:
    """Overlay step responses (typically before-tuning vs after-tuning).

    The setpoint and its settling band are drawn in neutral ink, never in a
    series colour: they are a reference, not a measurement.
    """
    if not traces:
        raise PlotError("no step traces to plot")
    if len(traces) > MAX_SERIES:
        raise PlotError(f"at most {MAX_SERIES} traces per step-response figure")

    apply_house_style()
    fig, ax = plt.subplots(figsize=(8.0, 4.9))

    setpoint = float(traces[0].setpoint_rad_s)
    band = settle_band_frac * abs(setpoint)
    ax.axhspan(
        setpoint - band, setpoint + band, color=REFERENCE, alpha=0.10, zorder=0,
        label=f"±{settle_band_frac:.0%} settling band",
    )
    ax.axhline(
        setpoint, color=REFERENCE, linewidth=1.4, linestyle=(0, (5, 3)), zorder=1,
        label=f"setpoint {setpoint:.1f} rad/s",
    )

    for index, trace in enumerate(traces):
        style = series_style(index)
        t = np.asarray(trace.t_s, dtype=float) - float(trace.t_step_s)
        ax.plot(
            t, np.asarray(trace.omega_rad_s, dtype=float),
            color=style["color"], linestyle=style["linestyle"], marker="None",
            linewidth=2.1, zorder=3 + index, label=_step_legend_label(trace),
        )
        if trace.metrics is not None:
            ax.plot(
                [trace.metrics.settling_time_s.value],
                [trace.metrics.steady_state_value.value],
                marker=style["marker"], markersize=8, markerfacecolor="white",
                markeredgewidth=1.8, color=style["color"], zorder=6, linestyle="None",
            )

    ax.set_xlabel("time since step, s")
    ax.set_ylabel("omega_rad_s  (output shaft)")
    ax.set_title(title, pad=12)
    ax.legend(loc="lower right", fontsize=9.5)
    ax.text(
        0.0, -0.20,
        "marker = settling point, measured against each trace's OWN steady "
        "state, not against the setpoint band",
        transform=ax.transAxes, fontsize=9, color=INK_MUTED, va="top",
    )
    fig.tight_layout()
    return save_figure(fig, path)


def _step_legend_label(trace: StepTrace) -> str:
    if trace.metrics is None:
        return trace.label
    m = trace.metrics
    return (
        f"{trace.label}: rise {m.rise_time_s.value * 1e3:.0f} ms, "
        f"overshoot {m.overshoot_frac.value:.0%}, "
        f"error {m.steady_state_error.value:+.2f} rad/s"
    )


# --------------------------------------------------------------------------
# Figure 4 -- loop jitter histogram
# --------------------------------------------------------------------------


def plot_loop_jitter(
    jitter: LoopJitter,
    *,
    path: Path | str,
    title: str = "Control-loop period (D6 LOOP_TICK)",
    bins: int = 40,
) -> Path:
    """Histogram of measured loop periods with the target period marked.

    One series, so no legend box: the title names it and the reference lines
    are labelled where they stand. X axis is milliseconds because a reader
    thinks in milliseconds; the underlying data stays in SI seconds.
    """
    periods_ms = np.asarray(jitter.periods_s, dtype=float) * 1e3
    if periods_ms.size < 2:
        raise PlotError("need at least 2 periods for a histogram")

    apply_house_style()
    fig, ax = plt.subplots(figsize=(7.8, 4.8))

    # Never more bins than the data can fill: 40 bins over 48 iterations draws
    # a comb, not a distribution.
    effective_bins = max(8, min(int(bins), max(int(periods_ms.size // 3), 8)))
    ax.hist(
        periods_ms, bins=effective_bins, color=SERIES_COLOURS[0], alpha=0.85,
        edgecolor=SURFACE, linewidth=0.6, zorder=2,
    )

    if jitter.target_s is not None:
        target_ms = jitter.target_s * 1e3
        ax.axvline(target_ms, color=INK, linewidth=1.8, zorder=4)
        ax.annotate(
            f"target {target_ms:.1f} ms",
            xy=(target_ms, ax.get_ylim()[1] * 0.95),
            xytext=(6, 0), textcoords="offset points",
            fontsize=10, fontweight="bold", color=INK, va="top",
        )
    mean_ms = jitter.mean_s * 1e3
    ax.axvline(mean_ms, color=SERIES_COLOURS[1], linewidth=1.8,
               linestyle=(0, (5, 3)), zorder=4)
    ax.annotate(
        f"mean {mean_ms:.3f} ms",
        xy=(mean_ms, ax.get_ylim()[1] * 0.72),
        xytext=(6, 0), textcoords="offset points",
        fontsize=10, color=SERIES_COLOURS[1], va="top",
    )
    p99_ms = jitter.p99_s * 1e3
    ax.axvline(p99_ms, color=INK_MUTED, linewidth=1.4, linestyle=":", zorder=4)
    ax.annotate(
        f"p99 {p99_ms:.3f} ms",
        xy=(p99_ms, ax.get_ylim()[1] * 0.50),
        xytext=(6, 0), textcoords="offset points",
        fontsize=9.5, color=INK_MUTED, va="top",
    )

    ax.set_xlabel("loop period, ms")
    ax.set_ylabel("iterations")
    ax.set_title(title, pad=24)
    summary = (
        f"n = {jitter.n} iterations · mean {mean_ms:.4f} ms · "
        f"s.d. {jitter.stddev_s * 1e6:.0f} µs · "
        f"min {jitter.min_s * 1e3:.3f} / max {jitter.max_s * 1e3:.3f} ms"
    )
    if jitter.sample_period_s:
        summary += f" · analyser resolution {jitter.sample_period_s * 1e6:.1f} µs"
    ax.text(
        0.0, 1.02, summary, transform=ax.transAxes, fontsize=9.5,
        color=INK_MUTED, va="bottom",
    )
    fig.tight_layout()
    return save_figure(fig, path)


# --------------------------------------------------------------------------
# Naming
# --------------------------------------------------------------------------


def plot_path(name: str, *, directory: Path | str | None = None) -> Path:
    """Canonical output path for a named figure, under experiments/plots/."""
    base = PLOTS_DIR if directory is None else Path(directory)
    safe = "".join(c if c.isalnum() or c in "-_." else "-" for c in name)
    if not safe.endswith(".png"):
        safe += ".png"
    return base / safe
