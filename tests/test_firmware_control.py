"""Control maths: PID, unit conversion, PWM levels, the TB6612 truth table.

`firmware/src/control.h` says it out loud: "the PID maths is the part most
likely to be wrong and the part hardest to debug on a moving robot, so it is
the part that must be unit-testable on a laptop with no hardware attached at
all." This file is that test.

Every named concept here is searchable on purpose - integral windup, derivative
kick, derivative on measurement, slow decay. CLAUDE.md's prime directive is
that the owner can explain this, not merely that it passes.
"""

from __future__ import annotations

import ctypes
import math

import pytest

from conftest import CONTROL_DT_S, CONTROL_LOOP_HZ, FIRMWARE_SRC, PWM_HZ

DT = CONTROL_DT_S            # 10 ms, the 100 Hz control period
PICO_CLK_HZ = 150_000_000    # RP2350 at 150 MHz
INT32_MAX = 2**31 - 1
INT32_MIN = -(2**31)

#: Every ticks-per-output-rev figure docs/HARDWARE.md §2.1 still leaves open:
#: 11 or 14 counts per MOTOR revolution (the two sources disagree), x1/x2/x4
#: depending on whether the decoder counts one edge or all four, x100 gearbox.
#:
#: The suite asserts the firmware's default is ONE OF THESE, never which one.
#: Demanding a specific value here would turn a disputed, unmeasured number
#: into an invariant nobody could change without "breaking the tests" - and
#: the whole point of Story 1.4 is that the number is going to change.
DOCUMENTED_TICKS_PER_REV_CANDIDATES = frozenset(
    counts * edges * 100 for counts in (11, 14) for edges in (1, 2, 4)
)


class Pid:
    """`struct pid_config` + `struct pid_state`, addressed through the shim."""

    def __init__(self, lib, kp=0.0, ki=0.0, kd=0.0, dt_s=DT,
                 out=(-1.0, 1.0), i_limits=None):
        self.lib = lib
        self.cfg = ctypes.create_string_buffer(lib.shim_pidcfg_size())
        self.st = ctypes.create_string_buffer(lib.shim_pidst_size())
        lib.shim_pid_config_init(self.cfg, dt_s)
        lib.shim_pid_reset(self.st)
        lib.shim_pid_set_gains(self.cfg, kp, ki, kd)
        lib.shim_pid_set_out_limits(self.cfg, out[0], out[1])
        if i_limits is not None:
            lib.shim_pid_set_i_limits(self.cfg, i_limits[0], i_limits[1])

    def step(self, setpoint, measurement, feedforward=0.0) -> float:
        return self.lib.shim_pid_step(self.cfg, self.st, setpoint, measurement,
                                      feedforward)

    def reset(self) -> None:
        self.lib.shim_pid_reset(self.st)

    @property
    def integral(self) -> float:
        return self.lib.shim_pid_integral(self.st)

    @property
    def have_prev(self) -> bool:
        return bool(self.lib.shim_pid_have_prev(self.st))


@pytest.fixture
def pid(firmware_lib):
    def make(**kw):
        return Pid(firmware_lib, **kw)
    return make


WIDE = (-1e6, 1e6)   # output limits wide enough not to mask the term under test


# --------------------------------------------------------------------------
# The zero
# --------------------------------------------------------------------------


def test_zero_error_gives_zero_output(pid):
    assert pid(kp=0.5, ki=0.2, kd=0.01).step(0.0, 0.0) == pytest.approx(0.0)


def test_zero_error_at_a_nonzero_operating_point_gives_zero(pid):
    """A PID has no feedforward of its own. At setpoint, with no history, it
    outputs nothing - which is also why holding speed against friction is the
    integrator's job, and why feedforward exists as a separate slot."""
    assert pid(kp=0.5, ki=0.2, kd=0.01).step(10.0, 10.0) == pytest.approx(0.0)


def test_zero_gains_give_zero_output_for_any_error(pid):
    assert pid().step(50.0, 0.0) == pytest.approx(0.0)


# --------------------------------------------------------------------------
# Proportional
# --------------------------------------------------------------------------


def test_proportional_is_positive_when_measurement_is_below_setpoint(pid):
    assert pid(kp=0.1, out=WIDE).step(10.0, 0.0) > 0


def test_proportional_is_negative_when_measurement_is_above_setpoint(pid):
    assert pid(kp=0.1, out=WIDE).step(0.0, 10.0) < 0


def test_proportional_magnitude_is_kp_times_error(pid):
    assert pid(kp=0.05, out=WIDE).step(10.0, 4.0) == pytest.approx(0.05 * 6.0, rel=1e-5)


def test_proportional_is_symmetric_under_sign_flip(pid):
    a = pid(kp=0.05, out=WIDE).step(10.0, 4.0)
    b = pid(kp=0.05, out=WIDE).step(-10.0, -4.0)
    assert a == pytest.approx(-b, rel=1e-6)


# --------------------------------------------------------------------------
# Integral, and the clamp that stops it eating the rover
# --------------------------------------------------------------------------


def test_integral_accumulates_over_repeated_calls(pid):
    controller = pid(ki=1.0, out=WIDE, i_limits=(-1e6, 1e6))
    first = controller.step(1.0, 0.0)
    second = controller.step(1.0, 0.0)
    third = controller.step(1.0, 0.0)
    assert third > second > first > 0


def test_integral_term_is_ki_times_error_times_dt_after_one_step(pid):
    controller = pid(ki=2.0, dt_s=0.01, out=WIDE, i_limits=(-1e6, 1e6))
    assert controller.step(3.0, 0.0) == pytest.approx(2.0 * 3.0 * 0.01, rel=1e-5)


def test_integral_unwinds_when_the_error_reverses(pid):
    controller = pid(ki=1.0, out=WIDE, i_limits=(-1e6, 1e6))
    for _ in range(10):
        controller.step(1.0, 0.0)
    high = controller.integral
    for _ in range(10):
        controller.step(-1.0, 0.0)
    assert controller.integral < high


def test_integral_is_clamped_to_the_configured_limit(pid):
    """Anti-windup in its explicit form: the accumulator itself is bounded."""
    controller = pid(ki=1.0, i_limits=(-0.5, 0.5))
    for _ in range(1000):
        controller.step(100.0, 0.0)
    assert controller.integral <= 0.5 + 1e-6


def test_integral_is_clamped_on_the_negative_side_too(pid):
    controller = pid(ki=1.0, i_limits=(-0.5, 0.5))
    for _ in range(1000):
        controller.step(-100.0, 0.0)
    assert controller.integral >= -0.5 - 1e-6


def test_the_default_integral_limits_are_the_actuator_limits(pid):
    """pid_config_init sets i_min/i_max to +/-1. Whatever happens, the stored
    history can never demand something the hardware could not have delivered.
    It is the cheapest anti-windup there is, and it needs no extra state."""
    controller = pid(ki=5.0)
    for _ in range(5000):
        controller.step(100.0, 0.0)
    assert controller.integral == pytest.approx(1.0, abs=1e-5)


def test_windup_recovery_is_prompt(pid):
    """The behavioural test, and the one worth trusting.

    Integral windup: hold a saturated controller against an error it cannot
    clear - a wheel against a wall, or a speed 6 V cannot deliver - and an
    unbounded accumulator grows without limit while changing nothing. Remove
    the obstruction and the output stays pinned until an equally long period of
    OPPOSITE error discharges it: the rover drives into the wall, and then
    keeps driving after you pick it up.

    Saturate for 3 s of loop time, reverse the setpoint, and count iterations
    to leave the rail. Clamped: a few. Unclamped: hundreds.
    """
    controller = pid(kp=0.1, ki=5.0)
    for _ in range(int(3.0 / DT)):
        controller.step(100.0, 0.0)
    assert controller.step(100.0, 0.0) == pytest.approx(1.0)

    iterations = None
    for i in range(400):
        if controller.step(-100.0, 0.0) < 1.0:
            iterations = i
            break
    assert iterations is not None, "still pinned after 4 s of reversed error"
    assert iterations < 20


def test_ki_zero_never_accumulates(pid):
    controller = pid(kp=0.1, ki=0.0)
    for _ in range(100):
        controller.step(10.0, 0.0)
    assert controller.integral == pytest.approx(0.0)


# --------------------------------------------------------------------------
# Derivative on measurement
# --------------------------------------------------------------------------


def test_derivative_does_not_kick_on_a_setpoint_step(pid):
    """Derivative kick, and why the D term differentiates the MEASUREMENT.

    The textbook term is kd * d(error)/dt. error = setpoint - measurement, so a
    step in the setpoint makes d(error)/dt momentarily enormous and the output
    slams to its limit for one sample - straight into the H-bridge. Since the
    setpoint is piecewise constant, differentiating the measurement alone gives
    identical damping with no kick.

    Two controllers identical but for kd; step the setpoint with the
    measurement held. Their outputs must agree.
    """
    with_d = pid(kp=0.1, kd=1.0, out=WIDE)
    without_d = pid(kp=0.1, kd=0.0, out=WIDE)
    assert with_d.step(0.0, 0.0) == pytest.approx(without_d.step(0.0, 0.0))
    assert with_d.step(50.0, 0.0) == pytest.approx(without_d.step(50.0, 0.0))


def test_derivative_still_responds_to_a_measurement_change(pid):
    """The control for the test above: the D term must not simply be dead."""
    controller = pid(kd=1.0, out=WIDE)
    controller.step(0.0, 0.0)
    assert controller.step(0.0, 5.0) < 0


def test_derivative_magnitude_is_minus_kd_times_measurement_slope(pid):
    controller = pid(kd=2.0, dt_s=0.01, out=WIDE)
    controller.step(0.0, 0.0)
    assert controller.step(0.0, 0.5) == pytest.approx(-2.0 * 0.5 / 0.01, rel=1e-5)


def test_the_first_update_has_no_derivative_contribution(pid):
    """There is no previous measurement on the first call. Treating the
    implicit zero as a real sample fabricates an enormous slope."""
    assert pid(kd=10.0, out=WIDE).step(0.0, 7.0) == pytest.approx(0.0)


def test_have_prev_is_false_before_the_first_sample(pid):
    controller = pid(kd=1.0)
    assert controller.have_prev is False
    controller.step(0.0, 1.0)
    assert controller.have_prev is True


# --------------------------------------------------------------------------
# Saturation and feedforward
# --------------------------------------------------------------------------


def test_output_saturates_at_the_upper_limit(pid):
    assert pid(kp=1000.0).step(100.0, 0.0) == pytest.approx(1.0)


def test_output_saturates_at_the_lower_limit(pid):
    assert pid(kp=1000.0).step(-100.0, 0.0) == pytest.approx(-1.0)


def test_the_default_output_limits_are_a_legal_duty_fraction(pid):
    """The output of this loop is a duty fraction. Outside [-1, 1] is not a
    thing the PWM hardware can be asked for."""
    controller = pid(kp=1000.0)
    assert -1.0 <= controller.step(1e6, 0.0) <= 1.0
    assert -1.0 <= controller.step(-1e6, 0.0) <= 1.0


def test_asymmetric_output_limits_are_respected(pid):
    controller = pid(kp=1000.0, out=(0.0, 0.5))
    assert controller.step(100.0, 0.0) == pytest.approx(0.5)
    assert controller.step(-100.0, 0.0) == pytest.approx(0.0)


def test_feedforward_is_added_before_the_clamp(pid):
    assert pid(out=WIDE).step(0.0, 0.0, feedforward=0.3) == pytest.approx(0.3)


def test_feedforward_cannot_escape_the_output_limits(pid):
    assert pid().step(0.0, 0.0, feedforward=5.0) == pytest.approx(1.0)


# --------------------------------------------------------------------------
# Garbage in
# --------------------------------------------------------------------------


def test_a_nan_measurement_coasts_rather_than_poisoning_the_loop(pid):
    controller = pid(kp=1.0, ki=1.0, out=WIDE, i_limits=(-1e6, 1e6))
    controller.step(1.0, 0.0)
    before = controller.integral
    assert controller.step(1.0, float("nan")) == pytest.approx(0.0)
    assert controller.integral == pytest.approx(before), "NaN reached the integrator"


def test_a_nan_setpoint_coasts_rather_than_poisoning_the_loop(pid):
    controller = pid(kp=1.0, ki=1.0, out=WIDE, i_limits=(-1e6, 1e6))
    controller.step(1.0, 0.0)
    before = controller.integral
    assert controller.step(float("nan"), 0.0) == pytest.approx(0.0)
    assert controller.integral == pytest.approx(before)


def test_a_nan_stored_in_the_integrator_would_be_permanent(pid):
    """Why the guard above matters: NaN is absorbing under addition, so one bad
    sample would leave the integrator NaN for the rest of the run and every
    subsequent output would clamp to out_min. This asserts it never gets in."""
    controller = pid(ki=1.0, out=WIDE)
    for _ in range(5):
        controller.step(float("nan"), float("nan"))
    assert not math.isnan(controller.integral)


def test_a_nan_feedforward_is_treated_as_no_feedforward(pid):
    assert pid(out=WIDE).step(0.0, 0.0, feedforward=float("nan")) == pytest.approx(0.0)


def test_a_non_positive_dt_falls_back_to_feedforward_only(pid):
    """dt divides the derivative term. Firmware cannot raise, so a misconfigured
    period degrades to open-loop feedforward instead of producing an infinity."""
    assert pid(kp=1.0, ki=1.0, kd=1.0, dt_s=0.0, out=WIDE).step(10.0, 0.0,
                                                                feedforward=0.2) \
        == pytest.approx(0.2)


def test_reset_clears_the_integral(pid):
    controller = pid(ki=1.0, out=WIDE, i_limits=(-1e6, 1e6))
    for _ in range(10):
        controller.step(1.0, 0.0)
    controller.reset()
    assert controller.integral == pytest.approx(0.0)


def test_reset_clears_the_derivative_memory(pid):
    controller = pid(kd=1.0, out=WIDE)
    controller.step(0.0, 5.0)
    controller.reset()
    assert controller.step(0.0, 5.0) == pytest.approx(0.0)


# --------------------------------------------------------------------------
# Closed loop against the synthetic motor
# --------------------------------------------------------------------------


@pytest.mark.slow
def test_closed_loop_converges_on_the_synthetic_motor(pid, synthetic_motor):
    """Not a tuning claim - proof that the signs are consistent end to end.

    A wrong P sign, a wrong D sign, or a units slip and this diverges instead
    of settling. It is the cheapest possible check that the loop is closed the
    right way round, and it needs no hardware.
    """
    controller = pid(kp=0.02, ki=0.4)
    target = 20.0
    for _ in range(int(4.0 / DT)):
        duty = controller.step(target, synthetic_motor.omega_rad_s)
        synthetic_motor.step(duty, DT)
    assert synthetic_motor.omega_rad_s == pytest.approx(target, rel=0.05)


@pytest.mark.slow
def test_closed_loop_stays_bounded_with_an_absurd_gain(pid, synthetic_motor):
    """Saturation must keep a badly tuned loop bounded rather than exploding."""
    controller = pid(kp=5.0, ki=20.0)
    for _ in range(int(2.0 / DT)):
        duty = controller.step(20.0, synthetic_motor.omega_rad_s)
        assert -1.0 <= duty <= 1.0
        synthetic_motor.step(duty, DT)
    assert abs(synthetic_motor.omega_rad_s) < 10 * synthetic_motor.gain


@pytest.mark.slow
def test_the_loop_pushes_through_the_deadband(pid, synthetic_motor):
    """A real N20 does not move below some duty. Without an integrator the loop
    parks just under the deadband and reports a steady non-zero error forever -
    which is what "why is it humming but not turning" looks like."""
    controller = pid(kp=0.005, ki=0.5)
    for _ in range(int(4.0 / DT)):
        synthetic_motor.step(controller.step(5.0, synthetic_motor.omega_rad_s), DT)
    assert synthetic_motor.omega_rad_s > 0.0


# --------------------------------------------------------------------------
# clampf
# --------------------------------------------------------------------------


def test_clamp_passes_a_value_inside_the_range(firmware_lib):
    assert firmware_lib.shim_clampf(0.5, -1.0, 1.0) == pytest.approx(0.5)


def test_clamp_limits_above_and_below(firmware_lib):
    assert firmware_lib.shim_clampf(5.0, -1.0, 1.0) == pytest.approx(1.0)
    assert firmware_lib.shim_clampf(-5.0, -1.0, 1.0) == pytest.approx(-1.0)


def test_clamp_maps_nan_to_the_low_limit(firmware_lib):
    """NaN fails every comparison, so without an explicit test it would fall
    through both branches and be returned unchanged - straight into a duty
    cycle. Coasting is the safe reading of "no idea"."""
    assert firmware_lib.shim_clampf(float("nan"), -1.0, 1.0) == pytest.approx(-1.0)


# --------------------------------------------------------------------------
# Encoder counter arithmetic
# --------------------------------------------------------------------------


def test_encoder_delta_of_a_simple_advance(firmware_lib):
    assert firmware_lib.shim_enc_delta(150, 100) == 50


def test_encoder_delta_of_a_simple_retreat(firmware_lib):
    assert firmware_lib.shim_enc_delta(100, 150) == -50


def test_encoder_delta_across_the_positive_wrap(firmware_lib):
    """The PIO counter is free-running 32-bit. Subtracting directly in int32_t
    would be signed overflow - undefined behaviour in C - and the symptom is a
    single 4-billion-tick spike in the middle of an otherwise clean sweep."""
    assert firmware_lib.shim_enc_delta(INT32_MIN, INT32_MAX) == 1


def test_encoder_delta_across_the_negative_wrap(firmware_lib):
    assert firmware_lib.shim_enc_delta(INT32_MAX, INT32_MIN) == -1


def test_encoder_delta_of_zero(firmware_lib):
    assert firmware_lib.shim_enc_delta(42, 42) == 0


# --------------------------------------------------------------------------
# ticks -> rad/s, the units boundary
# --------------------------------------------------------------------------

#: A flat invented ticks-per-output-rev for the arithmetic below. NOT a
#: candidate value: 1400 (14 x 100) and 5600 (14 x 4 x 100) look like
#: answers to the open question in docs/HARDWARE.md §2.1, and a number that
#: looks like an answer gets quoted as one. The arithmetic does not care.
ARBITRARY_TPR = 1000.0


def test_one_output_revolution_in_one_second_is_two_pi_rad_s(firmware_lib):
    tpr = ARBITRARY_TPR
    assert firmware_lib.shim_ticks_to_omega(1000, 1.0, tpr) == pytest.approx(2 * math.pi,
                                                                            rel=1e-5)


def test_half_a_revolution_in_half_a_second_is_also_two_pi(firmware_lib):
    assert firmware_lib.shim_ticks_to_omega(500, 0.5, ARBITRARY_TPR) == pytest.approx(
        2 * math.pi, rel=1e-5)


def test_zero_ticks_is_zero_rad_s(firmware_lib):
    assert firmware_lib.shim_ticks_to_omega(0, DT, ARBITRARY_TPR) == 0.0


def test_negative_ticks_give_negative_omega(firmware_lib):
    """Reverse is a sign, not a separate code path."""
    assert firmware_lib.shim_ticks_to_omega(-1000, 1.0, ARBITRARY_TPR) == pytest.approx(
        -2 * math.pi, rel=1e-5)


def test_omega_scales_linearly_with_tick_count(firmware_lib):
    a = firmware_lib.shim_ticks_to_omega(100, DT, ARBITRARY_TPR)
    b = firmware_lib.shim_ticks_to_omega(200, DT, ARBITRARY_TPR)
    assert b == pytest.approx(2 * a, rel=1e-5)


def test_omega_scales_inversely_with_dt(firmware_lib):
    a = firmware_lib.shim_ticks_to_omega(100, 0.01, ARBITRARY_TPR)
    b = firmware_lib.shim_ticks_to_omega(100, 0.02, ARBITRARY_TPR)
    assert b == pytest.approx(a / 2, rel=1e-5)


def test_a_missing_ticks_per_rev_yields_zero_not_a_fabricated_speed(firmware_lib):
    """The 11-vs-14 dispute, encoded.

    ticks_per_output_rev is UNMEASURED (Story 1.4). Firmware cannot raise, so
    the contract is: with no usable calibration, report zero. Zero is obviously
    wrong and gets investigated; a plausible-looking rad/s scaled by an unknown
    factor gets plotted, quoted, and believed.
    """
    assert firmware_lib.shim_ticks_to_omega(1000, 1.0, 0.0) == 0.0
    assert firmware_lib.shim_ticks_to_omega(1000, 1.0, -1.0) == 0.0


def test_a_non_positive_dt_yields_zero_rather_than_dividing_by_zero(firmware_lib):
    assert firmware_lib.shim_ticks_to_omega(1000, 0.0, ARBITRARY_TPR) == 0.0
    assert firmware_lib.shim_ticks_to_omega(1000, -0.01, ARBITRARY_TPR) == 0.0


def test_the_firmware_default_ticks_per_rev_is_a_documented_derivation_or_unset(
        rover_state):
    """The default must be traceable to docs/HARDWARE.md §2.1 - or be zero.

    §2.1 leaves the count disputed (Adafruit 14, retailers 11) and the edge
    decoding open (x1/x2/x4), so the honest set of possible values is the
    product of those with the 1:100 gearbox. Anything outside it is a number
    somebody invented, and every rad/s the firmware reports scales with it.

    Zero is also allowed, and is arguably the better answer: with no usable
    calibration `ticks_to_omega_rad_s` returns 0, which is obviously wrong and
    gets investigated, rather than a plausible speed scaled by an unknown
    factor. This test deliberately does NOT pin which value is in there -
    Story 1.4 measures it, and a test demanding 5600 would fight the fix.
    """
    value = rover_state.ticks_per_output_rev
    assert value == 0.0 or value in DOCUMENTED_TICKS_PER_REV_CANDIDATES, (
        f"ticks_per_output_rev defaults to {value}, which is neither 0 nor one "
        f"of the derivations docs/HARDWARE.md §2.1 allows "
        f"({sorted(DOCUMENTED_TICKS_PER_REV_CANDIDATES)}). If it has been "
        "MEASURED, say so in HARDWARE.md and in the manifest, not only here."
    )


def test_the_ticks_per_rev_default_is_labelled_a_placeholder():
    """A constant that is really a guess must SAY it is a guess, next to itself.

    Every rad/s the firmware reports is wrong by whatever factor this is wrong
    by, and the only defence against that number being quoted in a report is
    the word PLACEHOLDER sitting beside it in the source.
    """
    source = (FIRMWARE_SRC / "protocol.c").read_text()
    index = source.find("DEFAULT_TICKS_PER_OUTPUT_REV")
    assert index > 0, "the constant vanished; update this test with it"
    context = source[max(0, index - 1500):index + 200].upper()
    assert "PLACEHOLDER" in context
    assert "STORY 1.4" in context or "MEASURE" in context


# --------------------------------------------------------------------------
# duty -> PWM hardware
# --------------------------------------------------------------------------


def test_pwm_wrap_for_the_bench_frequency(firmware_lib):
    """150 MHz / 20 kHz = 7500 counts per period, so wrap is 7499."""
    assert firmware_lib.shim_pwm_wrap_for_freq(PICO_CLK_HZ, PWM_HZ) == 7499


def test_pwm_wrap_rejects_a_zero_frequency(firmware_lib):
    assert firmware_lib.shim_pwm_wrap_for_freq(PICO_CLK_HZ, 0) == 0


def test_pwm_wrap_rejects_a_zero_clock(firmware_lib):
    assert firmware_lib.shim_pwm_wrap_for_freq(0, PWM_HZ) == 0


def test_pwm_wrap_rejects_a_frequency_that_will_not_fit_the_counter(firmware_lib):
    """Below ~2.3 kHz at 150 MHz the wrap exceeds 16 bits. Returning 0 makes
    that a fatal configuration error the caller must handle, not a silently
    truncated frequency that shows up as the wrong duty on the analyser."""
    assert firmware_lib.shim_pwm_wrap_for_freq(PICO_CLK_HZ, 1000) == 0


def test_pwm_wrap_rejects_a_frequency_above_the_clock(firmware_lib):
    assert firmware_lib.shim_pwm_wrap_for_freq(PICO_CLK_HZ, PICO_CLK_HZ * 2) == 0


def test_zero_duty_is_level_zero(firmware_lib):
    assert firmware_lib.shim_duty_to_pwm_level(0.0, 7499) == 0


def test_full_duty_is_strictly_above_the_counter(firmware_lib):
    """level = wrap+1 means the compare is always above the counter, i.e. 100%
    on. wrap alone would leave one count of off time per period."""
    assert firmware_lib.shim_duty_to_pwm_level(1.0, 7499) == 7500


def test_half_duty_is_half_the_period(firmware_lib):
    assert firmware_lib.shim_duty_to_pwm_level(0.5, 7499) == 3750


def test_duty_level_rounds_to_nearest_not_toward_zero(firmware_lib):
    """A plain cast truncates, which biases every duty low by up to one count -
    a silent 0.013% error at 20 kHz that no scope reading would ever catch."""
    assert firmware_lib.shim_duty_to_pwm_level(0.99995, 7499) == 7500
    assert firmware_lib.shim_duty_to_pwm_level(0.00007, 7499) == 1


def test_negative_duty_is_level_zero(firmware_lib):
    """Sign is direction and lives in the IN1/IN2 pins; the level takes only
    the magnitude."""
    assert firmware_lib.shim_duty_to_pwm_level(-0.5, 7499) == 0


def test_nan_duty_is_level_zero(firmware_lib):
    assert firmware_lib.shim_duty_to_pwm_level(float("nan"), 7499) == 0


def test_duty_level_is_monotonic(firmware_lib):
    levels = [firmware_lib.shim_duty_to_pwm_level(d / 100.0, 7499) for d in range(101)]
    assert levels == sorted(levels)


def test_duty_level_never_exceeds_the_period(firmware_lib):
    for duty in (0.0, 0.5, 1.0, 2.0, 1e9):
        assert firmware_lib.shim_duty_to_pwm_level(duty, 7499) <= 7500


# --------------------------------------------------------------------------
# TB6612FNG truth table
# --------------------------------------------------------------------------


COAST, FORWARD, REVERSE, BRAKE = 0, 1, 2, 3


def dir_pins(lib, direction):
    in1 = ctypes.c_int(-1)
    in2 = ctypes.c_int(-1)
    lib.shim_motor_dir_pins(direction, ctypes.byref(in1), ctypes.byref(in2))
    return (in1.value, in2.value)


@pytest.mark.parametrize("direction,pins", [
    (FORWARD, (1, 0)),
    (REVERSE, (0, 1)),
    (BRAKE, (1, 1)),
    (COAST, (0, 0)),
])
def test_truth_table(firmware_lib, direction, pins):
    """Straight out of the TB6612FNG datasheet and docs/HARDWARE.md §2.2:
    IN1/IN2 = 10 forward, 01 reverse, 11 brake, 00 coast. Asserted line by
    line here instead of on a bench with a motor attached."""
    assert dir_pins(firmware_lib, direction) == pins


def test_brake_and_coast_are_different_pin_patterns(firmware_lib):
    """Brake shorts the windings and stops hard; coast freewheels. They are not
    the same thing and a deadband measurement taken with the wrong one is not
    comparable with one taken with the right one."""
    assert dir_pins(firmware_lib, BRAKE) != dir_pins(firmware_lib, COAST)


def test_positive_duty_is_forward(firmware_lib):
    assert firmware_lib.shim_motor_dir_for_duty(0.5) == FORWARD


def test_negative_duty_is_reverse(firmware_lib):
    assert firmware_lib.shim_motor_dir_for_duty(-0.5) == REVERSE


def test_exactly_zero_duty_coasts(firmware_lib):
    assert firmware_lib.shim_motor_dir_for_duty(0.0) == COAST


def test_nan_duty_coasts(firmware_lib):
    """Coast is the safe reading of "no idea". Brake would be an active
    decision taken on garbage."""
    assert firmware_lib.shim_motor_dir_for_duty(float("nan")) == COAST


# --------------------------------------------------------------------------
# Feedforward
# --------------------------------------------------------------------------


class FeedForward:
    def __init__(self, lib, deadband=0.0, slope=0.0, enabled=False):
        self.lib = lib
        self.buf = ctypes.create_string_buffer(lib.shim_ff_size())
        lib.shim_ff_init(self.buf)
        if enabled or deadband or slope:
            lib.shim_ff_set(self.buf, deadband, slope, 1 if enabled else 0)

    def duty(self, omega_rad_s: float) -> float:
        return self.lib.shim_ff_duty_for_omega(self.buf, omega_rad_s)


def test_feedforward_is_disabled_until_it_is_measured(firmware_lib):
    """An invented feedforward is worse than none: it biases every step
    response, and the PID tuning that follows is then tuning against a lie.
    Story 1.5 fills the numbers in; until then this contributes exactly zero."""
    assert FeedForward(firmware_lib).duty(20.0) == 0.0


def test_feedforward_of_zero_speed_is_zero_duty(firmware_lib):
    ff = FeedForward(firmware_lib, deadband=0.1, slope=60.0, enabled=True)
    assert ff.duty(0.0) == 0.0


def test_feedforward_is_deadband_plus_linear(firmware_lib):
    ff = FeedForward(firmware_lib, deadband=0.1, slope=60.0, enabled=True)
    assert ff.duty(30.0) == pytest.approx(0.1 + 30.0 / 60.0, rel=1e-5)


def test_feedforward_sign_follows_the_requested_direction(firmware_lib):
    ff = FeedForward(firmware_lib, deadband=0.1, slope=60.0, enabled=True)
    assert ff.duty(-30.0) == pytest.approx(-(0.1 + 0.5), rel=1e-5)


def test_feedforward_is_clamped_to_a_legal_duty(firmware_lib):
    ff = FeedForward(firmware_lib, deadband=0.1, slope=60.0, enabled=True)
    assert ff.duty(1000.0) == pytest.approx(1.0)
    assert ff.duty(-1000.0) == pytest.approx(-1.0)


def test_feedforward_with_a_zero_slope_contributes_nothing(firmware_lib):
    """A zero slope means the curve was never measured; dividing by it would be
    an infinity."""
    ff = FeedForward(firmware_lib, deadband=0.1, slope=0.0, enabled=True)
    assert ff.duty(30.0) == 0.0


def test_the_control_period_constant_is_the_documented_one():
    """100 Hz, as stated in CLAUDE.md and board_config.h. Cheap guard against
    someone "just trying" 200 Hz and leaving it there."""
    import re

    header = (FIRMWARE_SRC / "board_config.h").read_text()
    assert re.search(rf"CONTROL_HZ\s+{CONTROL_LOOP_HZ}u?\b", header)
