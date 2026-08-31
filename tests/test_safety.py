"""Safety interlocks. One test per interlock, and each one asserts a refusal.

Every rule here has a cost attached. Rule 6 already cost an evening on this
project - encoder wires driven into the H-bridge outputs, every logic test
point reading correctly, the motor silent, because the fault was past the end
of the chain being measured. Rule 1 costs a logic analyser. Rule 4 costs a
motor driver. So they live in code and they are tested like anything else.

Sources: CLAUDE.md safety invariants, docs/WIRING.md §1, docs/BENCH.md §2.
"""

from __future__ import annotations

import pytest

from conftest import (
    ENCODER_SUPPLY_V,
    FORBIDDEN_GPIO,
    LIPO_4S_CHARGED_V,
    MOTOR_OUTPUT_NETS,
    TB6612_VM_MAX_V,
    import_or_none,
)

safety = import_or_none("rover_bench.safety")
link_mod = import_or_none("rover_bench.link")

pytestmark = pytest.mark.xfail(
    safety is None,
    reason="rover_bench.safety has not landed yet; these activate when it does",
    strict=False,
)

SAFE_BENCH = {
    "analyser_probes": ["D0", "D1", "D2", "D3", "D4", "D5", "D6", "D7"],
    "encoder_supply_v": 3.3,
    "common_ground": True,
    "driver_vm_v": 5.0,
    "motor_rail_reaches_pico": False,
    "red_white_ohms": 8.0,
    "black_blue_ohms": float("inf"),
}


# --------------------------------------------------------------------------
# Interlock 1 - never probe the H-bridge outputs
# --------------------------------------------------------------------------


@pytest.mark.parametrize("net", MOTOR_OUTPUT_NETS)
def test_interlock_probing_a_motor_output_is_refused(net):
    """AO1/AO2/BO1/BO2 sit at motor voltage and switch inductively - the
    flyback spike goes well above VM. The FX2's inputs are 3.3 V logic with no
    series protection, and it does not fail loudly: it fails by reading garbage
    on a channel you then trust."""
    with pytest.raises(safety.SafetyViolation):
        safety.check_analyser_probes(["D0", net])


def test_interlock_probing_a_motor_output_is_refused_case_insensitively():
    with pytest.raises(safety.SafetyViolation):
        safety.check_analyser_probes(["ao1"])


def test_probing_logic_signals_is_allowed():
    assert safety.check_analyser_probes(["D0", "D4", "D6"]).ok


def test_the_refusal_names_the_signal_so_a_human_can_move_the_clip():
    with pytest.raises(safety.SafetyViolation) as excinfo:
        safety.check_analyser_probes(["AO1"])
    assert "AO1" in str(excinfo.value)


def test_the_refusal_carries_an_actionable_fix():
    """A failure the operator cannot act on is noise on a terminal at midnight."""
    with pytest.raises(safety.SafetyViolation) as excinfo:
        safety.check_analyser_probes(["AO1"])
    assert all(check.fix for check in excinfo.value.failures)


# --------------------------------------------------------------------------
# Interlock 2 - encoder supply is 3.3 V, never 5 V
# --------------------------------------------------------------------------


def test_interlock_five_volt_encoder_supply_is_refused():
    """The encoder module tolerates 3-5 V, but its hall OUTPUTS swing to
    whatever the blue wire is fed, and they land on GP12/GP13. RP2350 GPIO is
    not 5 V tolerant, so 5 V here damages the Pico, not the encoder - which is
    what makes the datasheet's "3-5 V" a trap."""
    with pytest.raises(safety.SafetyViolation):
        safety.check_encoder_supply_v(5.0)


def test_interlock_encoder_supply_at_3v3_is_accepted():
    assert safety.check_encoder_supply_v(ENCODER_SUPPLY_V).ok


def test_interlock_encoder_supply_far_too_low_is_refused():
    with pytest.raises(safety.SafetyViolation):
        safety.check_encoder_supply_v(1.8)


# --------------------------------------------------------------------------
# Interlock 3 - grounds must be common
# --------------------------------------------------------------------------


def test_interlock_a_floating_ground_is_refused():
    """A logic level is a voltage DIFFERENCE. With no shared reference "3.3 V"
    is meaningless, the driver cannot interpret its own inputs, and a floating
    analyser ground is the single most common cause of a capture that looks
    like noise."""
    with pytest.raises(safety.SafetyViolation):
        safety.check_common_ground(False)


def test_interlock_common_ground_is_accepted():
    assert safety.check_common_ground(True).ok


# --------------------------------------------------------------------------
# Interlock 4 - the 4S LiPo never reaches the TB6612
# --------------------------------------------------------------------------


def test_interlock_lipo_voltage_on_vm_is_refused():
    """16.8 V charged against a 13.5 V absolute maximum. Through the D24V90F5
    regulator, every time, with no "just for a second"."""
    with pytest.raises(safety.SafetyViolation):
        safety.check_driver_vm_v(LIPO_4S_CHARGED_V)


def test_interlock_vm_just_above_the_absolute_maximum_is_refused():
    with pytest.raises(safety.SafetyViolation):
        safety.check_driver_vm_v(TB6612_VM_MAX_V + 0.1)


def test_interlock_vm_below_the_driver_minimum_is_refused():
    """Below 2.5 V the driver does not work, and "the motor does not spin"
    sends you hunting through firmware instead of looking at the supply."""
    with pytest.raises(safety.SafetyViolation):
        safety.check_driver_vm_v(1.0)


@pytest.mark.parametrize("volts", [5.0, 6.0])
def test_interlock_a_legitimate_bench_vm_is_accepted(volts):
    """5 V from the regulator is a real operating point, not a compromise:
    inside the motor's 4.5-6 V window with margin at both ends
    (HARDWARE.md §6.1)."""
    assert safety.check_driver_vm_v(volts).ok


# --------------------------------------------------------------------------
# Interlock 5 - no motor current enters the Pico
# --------------------------------------------------------------------------


def test_interlock_the_motor_rail_reaching_the_pico_is_refused():
    """Currently violated on the bring-up rig by design: VM comes from Pico
    VBUS as a documented stopgap for one motor. The check exists so that
    stopgap has to be declared rather than forgotten on the day the fourth
    motor is wired."""
    with pytest.raises(safety.SafetyViolation):
        safety.check_pico_power_isolation(True)


def test_interlock_an_isolated_pico_supply_is_accepted():
    assert safety.check_pico_power_isolation(False).ok


# --------------------------------------------------------------------------
# Interlock 6 - confirm the motor pair by resistance
# --------------------------------------------------------------------------


def test_interlock_a_correctly_identified_harness_is_accepted():
    assert safety.check_motor_pair_resistance(8.0, float("inf")).ok


def test_interlock_swapped_pairs_are_refused():
    """The evening-losing failure: an open red-white with a low black-blue is
    the encoder pair presented as the motor pair."""
    with pytest.raises(safety.SafetyViolation):
        safety.check_motor_pair_resistance(float("inf"), 8.0)


def test_interlock_an_open_motor_winding_is_refused():
    with pytest.raises(safety.SafetyViolation):
        safety.check_motor_pair_resistance(float("inf"), float("inf"))


def test_interlock_a_low_encoder_pair_reading_is_refused():
    with pytest.raises(safety.SafetyViolation):
        safety.check_motor_pair_resistance(8.0, 0.5)


def test_interlock_an_unmeasured_harness_is_refused_not_assumed_good():
    """"I did not measure" must not read the same as "I measured and it was
    fine". Absence of evidence is the entire failure mode here."""
    with pytest.raises(safety.SafetyViolation):
        safety.check_motor_pair_resistance(None, None)


def test_interlock_a_nan_reading_is_refused():
    """A meter that has not settled reads NaN through most capture paths, and
    NaN passes every ordinary range comparison."""
    with pytest.raises(safety.SafetyViolation):
        safety.check_motor_pair_resistance(float("nan"), float("inf"))


def test_interlock_a_non_numeric_reading_is_refused():
    with pytest.raises(safety.SafetyViolation):
        safety.check_motor_pair_resistance("8 ohms", float("inf"))


# --------------------------------------------------------------------------
# Interlock 7 - the CYW43439 pins
# --------------------------------------------------------------------------


@pytest.mark.parametrize("gp", sorted(FORBIDDEN_GPIO))
def test_interlock_a_cyw43439_pin_is_refused(gp):
    """GP23/24/25/29 are wired to the wireless chip inside the module.
    Assigning one produces a signal that never appears on a header pin, and
    hours of debugging a wire that was never connected to anything."""
    with pytest.raises(safety.SafetyViolation):
        safety.check_gpio(gp)


@pytest.mark.parametrize("gp", [0, 1, 2, 5, 12, 13, 20, 21, 22, 26, 27, 28])
def test_a_usable_gpio_is_accepted(gp):
    assert safety.check_gpio(gp).ok


@pytest.mark.parametrize("gp", [-1, 30, 99, "2", None, True])
def test_a_nonexistent_gpio_is_refused(gp):
    """A pin that does not exist is a programming error rather than a bench
    hazard, so ValueError is an equally good refusal - what matters is that it
    does not silently return a passing Check."""
    with pytest.raises((safety.SafetyViolation, ValueError)):
        safety.check_gpio(gp)


def test_the_forbidden_gpio_set_matches_the_hardware_documentation():
    assert set(safety.FORBIDDEN_GPIO) == set(FORBIDDEN_GPIO)


# --------------------------------------------------------------------------
# Interlock 8 - duty is a fraction
# --------------------------------------------------------------------------


@pytest.mark.parametrize("duty", [1.01, -1.01, 2.0, 45.0, 100.0,
                                  float("inf"), float("-inf")])
def test_interlock_an_out_of_range_duty_is_refused(duty):
    """The commonest real bug: a percentage (45) where a fraction (0.45) was
    meant. On a TB6612 that clamps harmlessly, but the DATA it produces is
    nonsense - and nonsense data that looks plausible is the expensive kind."""
    with pytest.raises(safety.SafetyViolation):
        safety.check_duty(duty)


def test_interlock_a_nan_duty_is_refused():
    """`not (nan > 1.0)` is True, so a naive range guard lets NaN straight
    through to the PWM register."""
    with pytest.raises(safety.SafetyViolation):
        safety.check_duty(float("nan"))


def test_a_non_numeric_duty_is_refused():
    with pytest.raises(safety.SafetyViolation):
        safety.check_duty("half")


def test_a_numeric_string_duty_is_coerced_rather_than_refused():
    """Documented deliberately: `check_duty("0.5")` accepts. Argparse hands
    strings around, and refusing one here would push a str/float conversion
    into every call site - which is where it would be forgotten."""
    assert safety.check_duty("0.5").ok


@pytest.mark.parametrize("duty", [0.0, 1.0, -1.0, 0.5, -0.5])
def test_a_legal_duty_is_accepted(duty):
    assert safety.check_duty(duty).ok


def test_a_configured_duty_ceiling_is_enforced():
    """Raising it has to be a decision someone typed, so that it lands in the
    manifest's argv."""
    limits = safety.Limits(max_abs_duty_frac=0.3)
    assert safety.check_duty(0.25, limits).ok
    with pytest.raises(safety.SafetyViolation):
        safety.check_duty(0.5, limits)


def test_a_duty_ceiling_above_one_is_refused():
    with pytest.raises(ValueError):
        safety.Limits(max_abs_duty_frac=2.0).validated()


def test_a_total_budget_smaller_than_the_continuous_limit_is_refused():
    with pytest.raises(ValueError):
        safety.Limits(max_continuous_drive_s=60.0, max_total_drive_s=30.0).validated()


# --------------------------------------------------------------------------
# Interlock 9 - 3.3 V logic
# --------------------------------------------------------------------------


def test_interlock_five_volt_logic_is_refused():
    """The FTDI adapter is voltage-selectable and ships at 5 V. Set it to 3.3 V
    and verify on its VCC pin BEFORE it goes near the Pico (WIRING.md rule 7)."""
    with pytest.raises(safety.SafetyViolation):
        safety.check_logic_level_v(5.0)


def test_interlock_3v3_logic_is_accepted():
    assert safety.check_logic_level_v(3.3).ok


# --------------------------------------------------------------------------
# Motor index
# --------------------------------------------------------------------------


@pytest.mark.parametrize("motor", [-1, 4, 99, "0", None, 1.5, True])
def test_an_invalid_motor_index_is_refused(motor):
    with pytest.raises(safety.SafetyViolation):
        safety.check_motor(motor)


@pytest.mark.parametrize("motor", [0, 1, 2, 3])
def test_every_real_motor_index_is_accepted(motor):
    assert safety.check_motor(motor).ok


def test_the_motor_numbering_agrees_with_storage():
    """Two modules that disagree about whether motors are 0-3 or 1-4 will file
    a run under one number and command the other."""
    storage = import_or_none("rover_bench.storage")
    if storage is None:
        pytest.skip("rover_bench.storage not present")
    assert tuple(safety.MOTOR_NUMBERS) == tuple(storage.MOTORS)


# --------------------------------------------------------------------------
# The interlock set, and preflight
# --------------------------------------------------------------------------


def test_every_documented_interlock_has_a_check():
    """Entity-set completeness against CLAUDE.md's six safety invariants. If a
    rule has no entry here the module is a subset of the rules, and nobody
    notices which one is missing."""
    expected = {"analyser_probes", "encoder_supply", "common_ground", "driver_vm",
                "pico_power_isolation", "motor_pair_resistance"}
    assert expected <= set(safety.INTERLOCKS)


def test_all_interlocks_are_callable():
    missing = [name for name, fn in safety.INTERLOCKS.items() if not callable(fn)]
    assert not missing, f"interlocks with no implementation: {missing}"


def test_the_written_checklist_covers_all_six_rules():
    """The rules the host CANNOT enforce are printed instead of silently
    assumed. Six rules, six lines."""
    assert len(safety.WIRING_CHECKLIST) >= 6
    joined = " ".join(safety.WIRING_CHECKLIST).lower()
    for token in ("ao1", "3.3 v", "ground", "13.5 v", "pico", "resistance"):
        assert token in joined


def test_preflight_passes_a_safe_bench():
    safety.preflight(dict(SAFE_BENCH))


@pytest.mark.parametrize("key,value", [
    ("analyser_probes", ["D0", "AO1"]),
    ("encoder_supply_v", 5.0),
    ("common_ground", False),
    ("driver_vm_v", 16.8),
    ("motor_rail_reaches_pico", True),
    ("red_white_ohms", float("inf")),
    ("black_blue_ohms", 0.5),
])
def test_preflight_refuses_a_bench_that_breaks_any_single_interlock(key, value):
    """One test case per interlock, driven through the front door callers use."""
    config = dict(SAFE_BENCH)
    config[key] = value
    with pytest.raises(safety.SafetyViolation):
        safety.preflight(config)


@pytest.mark.parametrize("missing", sorted(SAFE_BENCH))
def test_preflight_refuses_an_incomplete_bench_description(missing):
    """An unstated value is not a safe value. Skipping a check because its
    input is absent is how an interlock stops existing."""
    config = {k: v for k, v in SAFE_BENCH.items() if k != missing}
    with pytest.raises(safety.SafetyViolation):
        safety.preflight(config)


def test_preflight_refuses_something_that_is_not_a_config():
    with pytest.raises(safety.SafetyViolation):
        safety.preflight(["common_ground"])


def test_preflight_checks_optional_gpio_when_given():
    config = dict(SAFE_BENCH, gpio=[2, 3, 4, 5, 25])
    with pytest.raises(safety.SafetyViolation):
        safety.preflight(config)


def test_a_safety_violation_is_not_caught_by_a_bare_value_error_handler():
    """A caller that retries on ValueError must not retry its way past an
    interlock."""
    assert not issubclass(safety.SafetyViolation, (ValueError, TypeError))


def test_assert_ok_reports_every_failure_not_just_the_first():
    """One failure at a time is a miserable way to set up a bench."""
    checks = [safety.Check("a", False, "bad a", "fix a"),
              safety.Check("b", True, "fine"),
              safety.Check("c", False, "bad c", "fix c")]
    with pytest.raises(safety.SafetyViolation) as excinfo:
        safety.assert_ok(checks)
    assert len(excinfo.value.failures) == 2
    assert "bad a" in str(excinfo.value) and "bad c" in str(excinfo.value)


def test_assert_ok_ignores_skipped_checks():
    safety.assert_ok([safety.Check("x", False, "n/a", skipped=True)])


def test_a_skipped_check_reports_as_skip_not_as_pass():
    """A check that did not run must not read as a check that passed."""
    assert safety.Check("x", False, skipped=True).status == "SKIP"


# --------------------------------------------------------------------------
# The live half: DriveGuard
# --------------------------------------------------------------------------


@pytest.fixture
def guard_link(fake_pico):
    if link_mod is None:
        pytest.skip("rover_bench.link not present")
    return link_mod.Link(fake_pico, retries=0)


def test_the_guard_enables_the_driver_on_entry(guard_link, fake_pico, frozen_clock):
    with safety.DriveGuard(guard_link, clock=frozen_clock):
        pass
    assert any(line.startswith("STBY 1") for line in fake_pico.lines_written)


def test_the_guard_stops_and_disables_on_exit(guard_link, fake_pico, frozen_clock):
    """STOP is the polite request; STBY 0 is the TB6612's hardware all-stop and
    works even if the PWM slice is misconfigured. Belt and braces, in that
    order."""
    with safety.DriveGuard(guard_link, clock=frozen_clock) as guard:
        guard.set_duty(0, 0.5)
    written = fake_pico.lines_written
    assert "STOP" in written
    assert "STBY 0" in written
    assert written.index("STOP") < written.index("STBY 0")


def test_the_guard_stops_the_motor_when_the_body_raises(guard_link, fake_pico,
                                                        frozen_clock):
    """The case that actually happens at a bench: Ctrl-C mid-ramp."""
    with pytest.raises(RuntimeError):
        with safety.DriveGuard(guard_link, clock=frozen_clock) as guard:
            guard.set_duty(0, 0.8)
            raise RuntimeError("operator hit ctrl-c")
    assert "STBY 0" in fake_pico.lines_written


def test_the_guard_refuses_an_illegal_duty_and_stops(guard_link, fake_pico,
                                                     frozen_clock):
    """The refusal has to happen BEFORE the wire, not after.

    The second assertion is the load-bearing one and it is written against the
    real wire format (`SET <duty>`, one motor on the bench): no duty-setting
    frame may have been written at all. An earlier version of this test looked
    for `DUTY 0 +45`, a string this link cannot emit under any circumstances,
    so it would have passed even if the illegal duty had gone out.
    """
    with safety.DriveGuard(guard_link, clock=frozen_clock) as guard:
        with pytest.raises(safety.SafetyViolation):
            guard.set_duty(0, 45.0)
    written = fake_pico.lines_written
    assert "STBY 0" in written
    assert not any(line.upper().startswith("SET") for line in written), \
        f"a refused duty reached the wire: {written}"


def test_the_guard_bounds_continuous_drive(guard_link, fake_pico, frozen_clock):
    """An experiment that stalls must not sit there heating a motor whose real
    stall current nobody has measured (the supplier's 200 mA is distrusted)."""
    limits = safety.Limits(max_continuous_drive_s=5.0)
    with pytest.raises(safety.DriveTimeExceeded):
        with safety.DriveGuard(guard_link, limits, clock=frozen_clock) as guard:
            guard.set_duty(0, 0.5)
            frozen_clock.advance(6.0)
            guard.check()
    assert "STBY 0" in fake_pico.lines_written


def test_the_guard_allows_drive_within_the_limit(guard_link, frozen_clock):
    limits = safety.Limits(max_continuous_drive_s=5.0)
    with safety.DriveGuard(guard_link, limits, clock=frozen_clock) as guard:
        guard.set_duty(0, 0.5)
        frozen_clock.advance(2.0)
        guard.check()


def test_resting_resets_the_continuous_drive_clock(guard_link, frozen_clock):
    """Resting with the motor stopped is the point: it lets the H-bridge and
    the motor cool, which stops a long sweep becoming a slow thermal ramp that
    shows up in the data as drift."""
    limits = safety.Limits(max_continuous_drive_s=5.0)
    with safety.DriveGuard(guard_link, limits, clock=frozen_clock) as guard:
        guard.set_duty(0, 0.5)
        frozen_clock.advance(4.0)
        guard.rest(1.0, motors=[0])
        guard.set_duty(0, 0.5)
        frozen_clock.advance(4.0)
        guard.check()


def test_zero_duty_does_not_count_as_driving(guard_link, frozen_clock):
    limits = safety.Limits(max_continuous_drive_s=1.0)
    with safety.DriveGuard(guard_link, limits, clock=frozen_clock) as guard:
        guard.set_duty(0, 0.0)
        frozen_clock.advance(60.0)
        guard.check()


def test_release_is_safe_to_call_twice(guard_link, frozen_clock):
    guard = safety.DriveGuard(guard_link, clock=frozen_clock)
    guard.release()
    guard.release()
    assert guard.stopped is True


def test_a_planned_run_over_the_total_budget_is_refused_before_stby_goes_high():
    """The early half of the interlock: the refusal happens before anything is
    enabled, so nothing has to be un-done."""
    check = safety.check_drive_budget(10_000.0, safety.Limits())
    assert not check.ok
    with pytest.raises(safety.SafetyViolation):
        safety.assert_ok([check])


def test_a_run_that_plans_no_driving_needs_no_budget():
    assert safety.check_drive_budget(0.0).ok
