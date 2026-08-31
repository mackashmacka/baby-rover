"""Generate realistic fake run data, so every other module is testable with
zero hardware.

What is modelled, and why each piece is here:

* **First-order motor with a deadband.** A gearmotor does not turn at all
  below a duty that overcomes stiction and the H-bridge drop, then rises
  roughly linearly, then saturates when it runs out of supply voltage. All
  three regions have to exist or ``metrics.fit_duty_to_omega`` is never
  exercised on the shape it will actually meet.
* **Quantised encoder.** Counts are integers. At low speed one count per
  10 ms sample is a coarse speed step, and that quantisation is the reason
  plateau averaging and error bars exist at all.
* **Measurement noise.** Angular noise on the encoder position, so derived
  speed is noisy in the way a real derivative of a quantised signal is.
* **Loop jitter.** The 100 Hz loop does not tick at exactly 10.000 ms. A
  small Gaussian jitter plus occasional late iterations, which is what the
  D6 LOOP_TICK analysis has to survive.

⚠️ **THE NUMBERS IN HERE ARE INVENTED.** No-load RPM and stall torque are
unpublished for this motor and the encoder resolution is disputed
(11 vs 14 counts per motor revolution). Nothing produced by this module is
evidence about the real hardware, and every run it writes carries
``"synthetic": true`` in its manifest so it can never be mistaken for a
measurement. The defaults are chosen to be *plausible*, which is all a test
fixture needs to be.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .load import DEFAULT_CHANNEL_MAP, SCHEMA_VERSION

TAU = 2.0 * math.pi

#: Plausible-but-invented nominal behaviour of an N20 6 V 1:100 at 6.0 V.
#: ~250 output rpm at the top of the duty range => ~26 rad/s. INVENTED, and
#: chosen so that saturation is reached before duty 1.0 -- a fixture whose
#: curve never saturates would leave the saturation branch of
#: metrics.fit_duty_to_omega untested.
NOMINAL_OMEGA_MAX_RAD_S = 26.0
NOMINAL_DEADBAND = 0.13
NOMINAL_TAU_S = 0.08

#: INVENTED FIXTURE CONSTANT -- NEVER A PROJECT CONSTANT.
#:
#: 1400 is the TOP of the unresolved 1100-1400 counts/output-rev disagreement
#: (Adafruit's 14 counts per motor revolution x the 1:100 gearbox), not its
#: middle. It is here only so the synthetic encoder has *some* resolution. The
#: real figure is unmeasured (Story 1.4), and anything that reads a number out
#: of this module and calls it ticks/rev has manufactured a measurement.
#:
#: ``ticks_per_output_rev`` and ``encoder_decode`` must always be changed
#: together: 1100-1400 is the single-edge count, so an x4 decoder on the same
#: encoder sees roughly 4x it. The fixture manifests below therefore pair this
#: value with ``encoder_decode: "x1"``. Pairing it with "x4" would assert a
#: combination the hardware notes say cannot both be true.
DEFAULT_TICKS_PER_OUTPUT_REV = 1400.0


@dataclass(frozen=True)
class MotorModel:
    """One synthetic motor. Four of these differ, which is the whole point."""

    motor_id: int
    deadband_forward: float = NOMINAL_DEADBAND
    deadband_reverse: float = NOMINAL_DEADBAND
    gain_rad_s_per_duty: float = 36.0
    reverse_gain_factor: float = 0.97
    omega_max_rad_s: float = NOMINAL_OMEGA_MAX_RAD_S
    tau_s: float = NOMINAL_TAU_S
    encoder_noise_rad: float = 0.004
    process_noise_rad_s: float = 0.05

    def steady_state_omega_rad_s(self, duty_frac: float) -> float:
        """Signed steady-state speed for a signed duty, with deadband + saturation."""
        magnitude = abs(duty_frac)
        sign = math.copysign(1.0, duty_frac) if duty_frac != 0 else 0.0
        deadband = self.deadband_forward if sign >= 0 else self.deadband_reverse
        gain = self.gain_rad_s_per_duty * (1.0 if sign >= 0 else self.reverse_gain_factor)
        if magnitude <= deadband:
            return 0.0
        omega = gain * (magnitude - deadband)
        return sign * min(omega, self.omega_max_rad_s)


def motor_model(motor_id: int, *, seed: int | None = None) -> MotorModel:
    """A motor with reproducible per-unit variation.

    The spread applied here (roughly ±10% in gain, ±25% in deadband) is the
    order of variation cheap gearmotors actually show, and it is deliberately
    large enough that the four-motor overlay plot has something to show. It is
    a fixture, not a prediction.
    """
    rng = np.random.default_rng(1000 + motor_id if seed is None else seed)
    return MotorModel(
        motor_id=motor_id,
        deadband_forward=float(NOMINAL_DEADBAND * rng.uniform(0.78, 1.25)),
        deadband_reverse=float(NOMINAL_DEADBAND * rng.uniform(0.80, 1.30)),
        gain_rad_s_per_duty=float(36.0 * rng.uniform(0.90, 1.10)),
        reverse_gain_factor=float(rng.uniform(0.93, 1.01)),
        omega_max_rad_s=float(NOMINAL_OMEGA_MAX_RAD_S * rng.uniform(0.93, 1.07)),
        tau_s=float(NOMINAL_TAU_S * rng.uniform(0.8, 1.25)),
    )


# --------------------------------------------------------------------------
# Timebase
# --------------------------------------------------------------------------


def jittered_timebase(
    n: int,
    *,
    loop_hz: float = 100.0,
    jitter_sigma_s: float = 150e-6,
    late_probability: float = 0.02,
    late_extra_s: float = 900e-6,
    rng: np.random.Generator | None = None,
) -> np.ndarray:
    """Sample times for ``n`` control iterations, with realistic jitter.

    Periods are ``1/loop_hz`` plus Gaussian jitter, plus an occasional late
    iteration (a longer-than-usual loop body, a flash access, an interrupt).
    Periods are floored at 10% of nominal so the timebase is always strictly
    increasing -- a non-monotonic timebase is a bug in the *fixture*, and
    ``load`` would rightly reject it.
    """
    rng = np.random.default_rng(0) if rng is None else rng
    nominal = 1.0 / float(loop_hz)
    periods = nominal + rng.normal(0.0, jitter_sigma_s, size=max(n - 1, 0))
    if late_probability > 0 and periods.size:
        late = rng.random(periods.size) < late_probability
        periods = periods + late * late_extra_s
    periods = np.maximum(periods, nominal * 0.1)
    return np.concatenate(([0.0], np.cumsum(periods)))


# --------------------------------------------------------------------------
# Plant simulation
# --------------------------------------------------------------------------


def _simulate_plant(
    model: MotorModel,
    duty: np.ndarray,
    t_s: np.ndarray,
    *,
    ticks_per_output_rev: float,
    rng: np.random.Generator,
) -> tuple[np.ndarray, np.ndarray]:
    """Integrate the first-order plant; return ``(true_omega, counts)``."""
    n = duty.size
    omega = np.zeros(n)
    angle_rad = 0.0
    counts = np.zeros(n, dtype=np.int64)
    for k in range(1, n):
        dt = float(t_s[k] - t_s[k - 1])
        target = model.steady_state_omega_rad_s(float(duty[k - 1]))
        alpha = 1.0 - math.exp(-dt / model.tau_s)
        omega[k] = omega[k - 1] + alpha * (target - omega[k - 1])
        omega[k] += rng.normal(0.0, model.process_noise_rad_s) * math.sqrt(dt)
        angle_rad += omega[k] * dt
        measured_angle = angle_rad + rng.normal(0.0, model.encoder_noise_rad)
        counts[k] = round(measured_angle * ticks_per_output_rev / TAU)
    return omega, counts


def simulate_staircase(
    model: MotorModel,
    *,
    duty_levels: Sequence[float] | None = None,
    hold_s: float = 1.0,
    loop_hz: float = 100.0,
    ticks_per_output_rev: float = DEFAULT_TICKS_PER_OUTPUT_REV,
    seed: int | None = None,
) -> pd.DataFrame:
    """A duty staircase run: hold each duty, let it settle, move on.

    Returns a telemetry frame obeying the ``load`` column contract:
    ``t_s, duty_frac, counts, omega_rad_s``.
    """
    if duty_levels is None:
        forward = [round(0.05 * i, 2) for i in range(1, 21)]  # 0.05 .. 1.00
        duty_levels = [0.0] + forward + [0.0] + [-d for d in forward] + [0.0]
    rng = np.random.default_rng(2000 + model.motor_id if seed is None else seed)

    samples_per_hold = max(round(hold_s * loop_hz), 4)
    duty = np.repeat(np.asarray(duty_levels, dtype=float), samples_per_hold)
    t_s = jittered_timebase(duty.size, loop_hz=loop_hz, rng=rng)
    _, counts = _simulate_plant(
        model, duty, t_s, ticks_per_output_rev=ticks_per_output_rev, rng=rng
    )

    # The firmware's own omega estimate is a backward difference of the same
    # quantised counts -- so it is noisy in exactly the way the real one is.
    dcounts = np.diff(counts, prepend=counts[0])
    dt = np.diff(t_s, prepend=t_s[1] - t_s[0] if t_s.size > 1 else 0.01)
    with np.errstate(divide="ignore", invalid="ignore"):
        omega_est = TAU * dcounts / (ticks_per_output_rev * dt)
    omega_est = np.nan_to_num(omega_est)

    return pd.DataFrame(
        {
            "t_s": t_s,
            "duty_frac": duty,
            "counts": counts,
            "omega_rad_s": omega_est,
        }
    )


def simulate_step(
    model: MotorModel,
    *,
    setpoint_rad_s: float = 15.0,
    kp: float = 0.02,
    ki: float = 0.25,
    kd: float = 0.0,
    feedforward_per_rad_s: float = 0.0,
    pre_step_s: float = 0.2,
    post_step_s: float = 1.5,
    loop_hz: float = 100.0,
    ticks_per_output_rev: float = DEFAULT_TICKS_PER_OUTPUT_REV,
    seed: int | None = None,
) -> pd.DataFrame:
    """A closed-loop PID step response.

    The controller sees only what the encoder gives it -- quantised counts
    differenced over one loop period -- so derivative noise and the low-speed
    quantisation floor are both present, as they are on the bench.

    Integral term is clamped when the duty output saturates (anti-windup);
    without that, a sluggish "before tuning" gain set winds up and the step
    looks better than it is.
    """
    rng = np.random.default_rng(3000 + model.motor_id if seed is None else seed)
    n = max(round((pre_step_s + post_step_s) * loop_hz), 8)
    t_s = jittered_timebase(n, loop_hz=loop_hz, rng=rng)
    setpoint = np.where(t_s >= pre_step_s, setpoint_rad_s, 0.0)

    duty = np.zeros(n)
    counts = np.zeros(n, dtype=np.int64)
    measured = np.zeros(n)
    omega = 0.0
    angle_rad = 0.0
    integral = 0.0
    previous_error = 0.0

    for k in range(1, n):
        dt = float(t_s[k] - t_s[k - 1])

        # --- plant, advanced with the previous duty -----------------------
        target = model.steady_state_omega_rad_s(float(duty[k - 1]))
        alpha = 1.0 - math.exp(-dt / model.tau_s)
        omega = omega + alpha * (target - omega)
        omega += rng.normal(0.0, model.process_noise_rad_s) * math.sqrt(dt)
        angle_rad += omega * dt
        counts[k] = round(
            (angle_rad + rng.normal(0.0, model.encoder_noise_rad))
            * ticks_per_output_rev / TAU
        )

        # --- what the controller can actually see -------------------------
        measured[k] = TAU * float(counts[k] - counts[k - 1]) / (ticks_per_output_rev * dt)

        error = float(setpoint[k] - measured[k])
        derivative = (error - previous_error) / dt if dt > 0 else 0.0
        candidate = (
            kp * error
            + ki * (integral + error * dt)
            + kd * derivative
            + feedforward_per_rad_s * float(setpoint[k])
        )
        if -1.0 < candidate < 1.0:  # anti-windup: only integrate when unsaturated
            integral += error * dt
        duty[k] = float(np.clip(candidate, -1.0, 1.0))
        previous_error = error

    return pd.DataFrame(
        {
            "t_s": t_s,
            "duty_frac": duty,
            "counts": counts,
            "omega_rad_s": measured,
            "setpoint_rad_s": setpoint,
        }
    )


# --------------------------------------------------------------------------
# Logic-analyser capture
# --------------------------------------------------------------------------


def simulate_analyser(
    *,
    duration_s: float = 0.5,
    samplerate_hz: float = 100_000.0,
    loop_hz: float = 100.0,
    cpu_duty_frac: float = 0.18,
    cpu_duty_spread: float = 0.04,
    pwm_hz: float = 20_000.0,
    pwm_duty_frac: float = 0.6,
    direction: int = 1,
    encoder_hz: float = 2_000.0,
    jitter_sigma_s: float = 150e-6,
    seed: int = 7,
) -> pd.DataFrame:
    """An 8-channel digital capture matching the bench channel map.

    Columns are ``t_s`` plus ``D0..D7`` -- the raw sigrok column names, so the
    channel-map path in ``load.load_analyser`` gets exercised by the fixtures.

    ⚠️ The default 100 kHz is only 5 samples per 20 kHz PWM cycle. That is
    plenty for the *timing* channels (D6/D7 at 100 Hz) which is what this
    fixture exists for, and nowhere near enough to measure PWM duty. The real
    maximum sample rate of the FX2 clone is not documented in this repo and
    must be discovered from ``sigrok-cli --scan`` at capture time, never
    assumed here.

    D6 LOOP_TICK **toggles** once per iteration (it does not pulse), matching
    the firmware contract. Anything reading it must count both edges.
    """
    rng = np.random.default_rng(seed)
    n = max(round(duration_s * samplerate_hz), 2)
    t = np.arange(n, dtype=float) / float(samplerate_hz)

    # D0 PWM: a plain square wave at pwm_hz with the requested duty.
    phase = (t * pwm_hz) % 1.0
    pwm = (phase < pwm_duty_frac).astype(np.int8)

    # D1/D2 direction, per the TB6612 truth table: 10 forward, 01 reverse.
    ain1 = np.full(n, 1 if direction >= 0 else 0, dtype=np.int8)
    ain2 = np.full(n, 0 if direction >= 0 else 1, dtype=np.int8)

    # D3 STBY: HIGH = enabled, for the whole capture.
    stby = np.ones(n, dtype=np.int8)

    # D4/D5 quadrature, B lagging A by 90 degrees when moving forward.
    enc_phase = (t * encoder_hz) % 1.0
    enc_a = (enc_phase < 0.5).astype(np.int8)
    lag = 0.25 if direction >= 0 else -0.25
    enc_b = (((enc_phase - lag) % 1.0) < 0.5).astype(np.int8)

    # D6 LOOP_TICK and D7 COMPUTE_BUSY, sharing one jittered iteration clock.
    iterations = int(duration_s * loop_hz) + 2
    edges = jittered_timebase(iterations, loop_hz=loop_hz,
                              jitter_sigma_s=jitter_sigma_s, rng=rng)
    loop_tick = np.zeros(n, dtype=np.int8)
    busy = np.zeros(n, dtype=np.int8)
    level = 0
    for k in range(len(edges) - 1):
        start, stop = edges[k], edges[k + 1]
        window = (t >= start) & (t < stop)
        if not window.any():
            continue
        loop_tick[window] = level
        level ^= 1
        busy_frac = float(np.clip(rng.normal(cpu_duty_frac, cpu_duty_spread), 0.01, 0.95))
        busy_until = start + busy_frac * (stop - start)
        busy[window & (t < busy_until)] = 1

    return pd.DataFrame(
        {
            "t_s": t,
            "D0": pwm,
            "D1": ain1,
            "D2": ain2,
            "D3": stby,
            "D4": enc_a,
            "D5": enc_b,
            "D6": loop_tick,
            "D7": busy,
        }
    )


# --------------------------------------------------------------------------
# Writing run directories
# --------------------------------------------------------------------------


def make_manifest(
    *,
    run_id: str,
    motor_id: int,
    experiment_id: str = "motor-char",
    story: str = "1.5",
    utc_started: str = "2026-09-02T00:00:00Z",
    supply_voltage_v: float = 6.0,
    pwm_hz: float = 20_000.0,
    loop_hz: float = 100.0,
    ticks_per_output_rev: float = DEFAULT_TICKS_PER_OUTPUT_REV,
    ticks_uncertainty: float = 150.0,
    gear_ratio: float = 100.0,
    encoder_decode: str = "x1",
    analyser_samplerate_hz: float | None = 100_000.0,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a manifest that satisfies the ``load`` schema.

    ``synthetic: true`` is not decoration -- ``report.py`` reads it and stamps
    the report, so fake numbers can never quietly become findings.
    """
    manifest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "synthetic": True,
        "run_id": run_id,
        "experiment_id": experiment_id,
        "story": story,
        "utc_started": utc_started,
        "motor_id": motor_id,
        "operator": "synthetic.py",
        "firmware_version": "synthetic-0",
        "git_commit": "0000000",
        "notes": "GENERATED BY tools/analysis/synthetic.py -- not a measurement.",
        "params": {
            "supply_voltage_v": supply_voltage_v,
            "supply_kind": "synthetic",
            "pwm_hz": pwm_hz,
            "loop_hz": loop_hz,
            "ticks_per_output_rev": ticks_per_output_rev,
            "ticks_per_output_rev_uncertainty": ticks_uncertainty,
            "gear_ratio": gear_ratio,
            "encoder_decode": encoder_decode,
            "encoder_supply_v": 3.3,
            "direction_convention": "positive duty_frac = AIN1 HIGH / AIN2 LOW",
        },
        "files": {"telemetry": "telemetry.csv"},
    }
    if analyser_samplerate_hz is not None:
        manifest["analyser"] = {
            "samplerate_hz": analyser_samplerate_hz,
            "channel_map": dict(DEFAULT_CHANNEL_MAP),
        }
        manifest["files"]["analyser"] = "analyser.csv"
    if extra:
        manifest.update(extra)
    return manifest


def write_run(
    directory: Path | str,
    manifest: dict[str, Any],
    telemetry: pd.DataFrame,
    analyser: pd.DataFrame | None = None,
) -> Path:
    """Write manifest.json + telemetry.csv (+ analyser.csv) into a directory."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    telemetry.to_csv(directory / manifest["files"]["telemetry"], index=False)
    if analyser is not None:
        name = manifest["files"].get("analyser", "analyser.csv")
        analyser.to_csv(directory / name, index=False)
    return directory


def write_staircase_run(
    directory: Path | str,
    motor_id: int,
    *,
    with_analyser: bool = False,
    hold_s: float = 1.0,
    analyser_duration_s: float = 0.5,
    analyser_samplerate_hz: float = 100_000.0,
    ticks_per_output_rev: float = DEFAULT_TICKS_PER_OUTPUT_REV,
    repeat: int = 1,
    model: MotorModel | None = None,
    **manifest_kwargs: Any,
) -> Path:
    """Write one complete synthetic duty-staircase run directory."""
    model = motor_model(motor_id) if model is None else model
    telemetry = simulate_staircase(
        model,
        hold_s=hold_s,
        ticks_per_output_rev=ticks_per_output_rev,
        seed=4000 + motor_id * 10 + repeat,
    )
    manifest = make_manifest(
        run_id=f"m{motor_id}-staircase-{repeat:02d}",
        motor_id=motor_id,
        ticks_per_output_rev=ticks_per_output_rev,
        analyser_samplerate_hz=analyser_samplerate_hz if with_analyser else None,
        **manifest_kwargs,
    )
    analyser = None
    if with_analyser:
        analyser = simulate_analyser(
            duration_s=analyser_duration_s,
            samplerate_hz=analyser_samplerate_hz,
            seed=5000 + motor_id,
        )
    return write_run(directory, manifest, telemetry, analyser)


def write_step_run(
    directory: Path | str,
    motor_id: int,
    *,
    tuning: str = "after",
    kp: float | None = None,
    ki: float | None = None,
    kd: float | None = None,
    setpoint_rad_s: float = 15.0,
    ticks_per_output_rev: float = DEFAULT_TICKS_PER_OUTPUT_REV,
    model: MotorModel | None = None,
    **manifest_kwargs: Any,
) -> Path:
    """Write one synthetic PID step-response run.

    ``tuning="before"`` uses a deliberately sluggish gain set (large
    steady-state error, slow rise); ``tuning="after"`` a brisker one with a
    little overshoot. That pair is what the before/after overlay plot needs.
    """
    model = motor_model(motor_id) if model is None else model
    if tuning == "before":
        gains = (0.004, 0.05, 0.0)
    else:
        gains = (0.020, 0.90, 0.0005)
    kp = gains[0] if kp is None else kp
    ki = gains[1] if ki is None else ki
    kd = gains[2] if kd is None else kd

    telemetry = simulate_step(
        model,
        setpoint_rad_s=setpoint_rad_s,
        kp=kp,
        ki=ki,
        kd=kd,
        ticks_per_output_rev=ticks_per_output_rev,
        seed=6000 + motor_id + (0 if tuning == "before" else 1),
    )
    manifest = make_manifest(
        run_id=f"m{motor_id}-step-{tuning}",
        motor_id=motor_id,
        experiment_id="pid-step",
        story="1.6",
        ticks_per_output_rev=ticks_per_output_rev,
        analyser_samplerate_hz=None,
        extra={"tuning": tuning, "pid": {"kp": kp, "ki": ki, "kd": kd}},
        **manifest_kwargs,
    )
    return write_run(directory, manifest, telemetry)


def write_campaign(
    root: Path | str,
    *,
    motors: Iterable[int] = (1, 2, 3, 4),
    repeats: int = 1,
    hold_s: float = 1.0,
    with_analyser: bool = True,
) -> dict[str, list[Path]]:
    """Write a whole synthetic Story 1.5 + 1.6 campaign under ``root``.

    Returns ``{"motor-char": [...], "pid-step": [...]}``. Every run shares the
    same critical parameters, so the set is comparable -- which is what makes
    it a useful fixture for the four-motor overlay.
    """
    root = Path(root)
    written: dict[str, list[Path]] = {"motor-char": [], "pid-step": []}
    for motor_id in motors:
        for repeat in range(1, repeats + 1):
            path = root / "motor-char" / f"m{motor_id}-staircase-{repeat:02d}"
            written["motor-char"].append(
                write_staircase_run(
                    path,
                    motor_id,
                    with_analyser=with_analyser and repeat == 1,
                    hold_s=hold_s,
                    repeat=repeat,
                )
            )
    for tuning in ("before", "after"):
        path = root / "pid-step" / f"m1-step-{tuning}"
        written["pid-step"].append(write_step_run(path, 1, tuning=tuning))
    return written


def degrade_model(model: MotorModel, *, gain_scale: float = 0.9) -> MotorModel:
    """A copy of a motor with a different gain -- handy for building fixtures
    where two runs must legitimately differ."""
    return replace(model, gain_rad_s_per_duty=model.gain_rad_s_per_duty * gain_scale)


def _main(argv: Sequence[str] | None = None) -> int:  # pragma: no cover - CLI glue
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "output", type=Path, help="directory to write the synthetic campaign into"
    )
    parser.add_argument("--motors", type=int, nargs="+", default=[1, 2, 3, 4])
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--no-analyser", action="store_true")
    args = parser.parse_args(argv)

    written = write_campaign(
        args.output,
        motors=args.motors,
        repeats=args.repeats,
        with_analyser=not args.no_analyser,
    )
    for group, paths in written.items():
        for path in paths:
            print(f"{group}: {path}")
    print("\nThese runs are SYNTHETIC (manifest carries synthetic: true).")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
