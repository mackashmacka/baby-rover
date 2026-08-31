"""safety.py — the interlocks.  Refuse to run rather than break things.

WHY THIS IS A MODULE AND NOT A HANDFUL OF `if` STATEMENTS
---------------------------------------------------------
An interlock scattered through the code is an interlock nobody can audit.  This
file is the single place where "what would this bench refuse to do" is written
down, so it can be read in one sitting and argued with.

Every rule here has a cost attached.  Rule 6 already cost an evening on this
project — encoder wires driven into the H-bridge outputs, every logic test
point reading correctly, and the motor silent.  Rule 1 costs a logic analyser.
Rule 4 costs a motor driver.  These are not style preferences, so they live in
code and are tested like anything else.

Sources: CLAUDE.md safety invariants, docs/WIRING.md §1, docs/HARDWARE.md.

TWO KINDS OF INTERLOCK
----------------------
1. **Bench configuration** — facts about how the rig is wired, which a human
   states and the tool refuses to proceed without.  `check_analyser_probes`,
   `check_encoder_supply_v`, `check_common_ground`, `check_driver_vm_v`,
   `check_pico_power_isolation`, `check_motor_pair_resistance`, `check_gpio`,
   `check_logic_level_v`.  Collected in `INTERLOCKS`; `preflight(config)`
   runs them all and raises.

   **An unstated value is not a safe value.**  `preflight` refuses an
   incomplete config rather than skipping the checks it has no input for —
   silently skipping a check because its input is absent is how an interlock
   stops existing.

2. **Run-time** — things the tool itself can get wrong: duty out of range, a
   Pico that does not answer PING, a missing analyser when a capture was asked
   for, a run that would drive for longer than the configured limit.
   `preflight_run(...)` returns them as a list so the operator sees all the
   failures at once, and `DriveGuard` enforces the drive-time limit live.

`SafetyViolation` DELIBERATELY DOES NOT SUBCLASS ValueError
------------------------------------------------------------
A caller that retries on `ValueError` must not be able to retry its way past an
interlock.  It subclasses `RuntimeError` only.

WHY A MAX-CONTINUOUS-DRIVE LIMIT AT ALL
---------------------------------------
The supplier claims ~100 mA no-load / ~200 mA stall for these N20s and the repo
explicitly distrusts both figures.  Until they are *measured*, a long unattended
drive is a thermal bet on a number nobody trusts, and a stalled motor is the
worst case of that bet.  The default is **60 s**, and the number is derived
rather than picked: one direction of a full sweep is 20 duty points x 1.3 s =
about 26 s of continuous drive, and a limit set below the longest legitimate
run is a limit that only ever fires on good runs — which teaches the operator
to raise it wholesale, which is how an interlock stops existing.  `Limits` is
the single source of this number; the CLI's `--max-drive-s` default reads it
from here rather than repeating it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Sequence

from .link import Clock, DeviceError, Link, LinkError, RealClock

# --------------------------------------------------------------------------
# Hardware limits.  Every number here is traceable to a datasheet or a doc;
# none of them are tuning knobs.
# --------------------------------------------------------------------------

#: A TB6612 channel cannot be driven harder than 100% duty.  Asking for more is
#: a units bug — someone passed a percentage where a fraction was wanted — not
#: an aggressive test.
ABSOLUTE_MAX_DUTY_FRAC = 1.0

#: Four motors, indexed 0-3 (left front, left rear, right front, right rear).
MOTOR_NUMBERS: tuple[int, ...] = (0, 1, 2, 3)

#: RP2350 GPIO that exist, and the four wired to the CYW43439 internally.
#: Assigning one of those produces a signal that never appears on a header pin
#: and hours of debugging a wire connected to nothing.
GPIO_MIN, GPIO_MAX = 0, 29
FORBIDDEN_GPIO: frozenset[int] = frozenset({23, 24, 25, 29})

#: Nets at motor voltage.  The analyser is a 3.3 V instrument and does not
#: survive being connected to them.
MOTOR_OUTPUT_NETS: tuple[str, ...] = ("AO1", "AO2", "BO1", "BO2")

#: Encoder hall supply.  3.3 V, never 5 V — RP2350 GPIO is not 5 V tolerant.
ENCODER_SUPPLY_MIN_V, ENCODER_SUPPLY_MAX_V = 3.0, 3.6

#: 3.3 V logic everywhere on this bench, including the FTDI adapter — which is
#: voltage-selectable and ships at 5 V (WIRING.md rule 7).
LOGIC_MIN_V, LOGIC_MAX_V = 3.0, 3.6

#: TB6612FNG motor supply window.  A charged 4S LiPo is 16.8 V and must pass
#: through the D24V90F5 regulator first, every time.
DRIVER_VM_MIN_V, DRIVER_VM_MAX_V = 2.5, 13.5

#: A motor winding reads a few ohms; the encoder pair does not.  These bounds
#: are deliberately wide — the check is "have you got the right pair", not
#: "what is the winding resistance".
MOTOR_PAIR_MIN_OHMS, MOTOR_PAIR_MAX_OHMS = 0.5, 200.0
ENCODER_PAIR_MIN_OHMS = 1_000.0

WIRING_CHECKLIST: tuple[str, ...] = (
    "Never probe AO1/AO2/BO1/BO2 with the analyser — motor voltage destroys it.",
    "Encoder power is 3.3 V, never 5 V — RP2350 GPIO is not 5 V tolerant.",
    "Grounds common: Pico, driver, supply, analyser.",
    "4S LiPo (16.8 V) never reaches the TB6612 (13.5 V max) directly.",
    "No motor current enters the Pico.",
    "Confirm the motor pair by resistance: red-white a few ohms, black-blue open.",
)


# --------------------------------------------------------------------------
# Errors and results
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Check:
    """One interlock result.

    `fix` is mandatory in spirit: a failure the operator cannot act on is just
    noise on a terminal at midnight.
    """

    name: str
    ok: bool
    detail: str = ""
    fix: str = ""
    skipped: bool = False

    @property
    def status(self) -> str:
        if self.skipped:
            return "SKIP"
        return "PASS" if self.ok else "FAIL"


class SafetyViolation(RuntimeError):
    """An interlock refused.  Nothing has been driven.

    Accepts either a message or a list of failed `Check`s, so the same
    exception type serves the bench-configuration checks (one refusal, one
    reason) and the run-time pre-flight (several at once).
    """

    def __init__(self, failures: "str | Sequence[Check]", fix: str = "") -> None:
        if isinstance(failures, str):
            message = failures + (f"\n  fix: {fix}" if fix else "")
            self.failures: tuple[Check, ...] = (
                Check("safety", False, failures, fix),
            )
        else:
            checks = tuple(failures)
            lines = ["refusing to run:"]
            for check in checks:
                lines.append(f"  [FAIL] {check.name}: {check.detail}")
                if check.fix:
                    lines.append(f"         fix: {check.fix}")
            message = "\n".join(lines)
            self.failures = checks
        super().__init__(message)


class DriveTimeExceeded(SafetyViolation):
    """The motor was driven for longer than the configured limit."""

    def __init__(self, elapsed_s: float, limit_s: float) -> None:
        super().__init__([Check(
            "max-continuous-drive", False,
            f"drove for {elapsed_s:.1f} s, limit is {limit_s:.1f} s",
            "raise --max-drive-s deliberately, or shorten the experiment",
        )])
        self.elapsed_s = elapsed_s
        self.limit_s = limit_s


def _refuse(name: str, detail: str, fix: str = "") -> None:
    raise SafetyViolation([Check(name, False, detail, fix)])


# --------------------------------------------------------------------------
# Bench-configuration interlocks.  Each raises; each returns a passing Check.
# --------------------------------------------------------------------------

def check_analyser_probes(signals: Iterable[str]) -> Check:
    """Interlock 1 — never probe the H-bridge outputs.

    AO1/AO2/BO1/BO2 sit at motor voltage.  The refusal names the offending
    signal, because the operator has to go and physically move a clip.
    """
    names = [str(s).strip() for s in signals]
    offenders = [n for n in names if n.upper() in MOTOR_OUTPUT_NETS]
    if offenders:
        _refuse(
            "analyser-probes",
            f"refusing to probe {', '.join(offenders)}: motor voltage will "
            "destroy the analyser (WIRING.md §1 rule 4)",
            "move the probe to a logic signal — D0-D7 map in WIRING.md §10.2",
        )
    return Check("analyser-probes", True, f"{len(names)} logic signal(s)")


def check_encoder_supply_v(volts: float) -> Check:
    """Interlock 2 — encoder power is 3.3 V, never 5 V.

    RP2350 GPIO is not 5 V tolerant, and the hall outputs go straight to it.
    Too *low* is refused as well: a hall sensor browning out produces missing
    edges, which read downstream as a motor that briefly stopped.
    """
    if volts is None or not isinstance(volts, (int, float)) or math.isnan(float(volts)):
        _refuse("encoder-supply", f"encoder supply not measured ({volts!r})",
                "measure the blue wire against ground before connecting")
    if not ENCODER_SUPPLY_MIN_V <= float(volts) <= ENCODER_SUPPLY_MAX_V:
        _refuse(
            "encoder-supply",
            f"encoder supply is {volts} V; it must be "
            f"{ENCODER_SUPPLY_MIN_V}-{ENCODER_SUPPLY_MAX_V} V",
            "blue wire to 3V3 (Pico pin 36), never to VBUS or a 5 V rail",
        )
    return Check("encoder-supply", True, f"{volts} V")


def check_common_ground(is_common: bool) -> Check:
    """Interlock 3 — one ground.

    Without a shared reference the driver cannot interpret a logic level at
    all, and the symptom is a motor that does nothing while every voltage you
    measure looks correct.
    """
    if not is_common:
        _refuse("common-ground", "grounds are not common",
                "star-connect Pico, driver, supply and analyser grounds")
    return Check("common-ground", True, "common")


def check_driver_vm_v(volts: float) -> Check:
    """Interlock 4 — VM within the TB6612's window.

    A charged 4S LiPo is 16.8 V against a 13.5 V absolute maximum: it goes
    through the D24V90F5 first, every time.  Below the minimum the driver
    browns out and the data is noise.
    """
    if volts is None or not isinstance(volts, (int, float)) or math.isnan(float(volts)):
        _refuse("driver-vm", f"VM not measured ({volts!r})",
                "measure VM against ground before enabling STBY")
    value = float(volts)
    if value > DRIVER_VM_MAX_V:
        _refuse("driver-vm",
                f"VM is {value} V, above the TB6612's {DRIVER_VM_MAX_V} V maximum",
                "route the battery through the D24V90F5 regulator")
    if value < DRIVER_VM_MIN_V:
        _refuse("driver-vm",
                f"VM is {value} V, below the TB6612's {DRIVER_VM_MIN_V} V minimum",
                "use a supply that holds up under load — see memory/power-supply.md")
    return Check("driver-vm", True, f"{value} V")


def check_pico_power_isolation(motor_rail_reaches_pico: bool) -> Check:
    """Interlock 5 — no motor current enters the Pico.

    No wire from the battery to the Pico, none from a driver output back to it.
    (Stage 1 currently violates this by feeding VM from Pico VBUS; that is
    recorded as a known deviation and must be fixed before four motors.)
    """
    if motor_rail_reaches_pico:
        _refuse("pico-isolation", "the motor rail reaches the Pico",
                "power VM from the bench supply, not from VBUS")
    return Check("pico-isolation", True, "isolated")


def check_motor_pair_resistance(red_white_ohms: float | None,
                                black_blue_ohms: float | None) -> Check:
    """Interlock 6 — confirm the motor pair before connecting it.

    Red-white reads a few ohms; black-blue does not.  Thirty seconds with a
    meter, and it has already cost an evening once: four encoder wires driven
    into the H-bridge outputs, every logic test point reading correctly, the
    motor silent, because the fault was past the end of the chain being
    measured.

    `None` is refused rather than assumed good — an unmeasured harness is
    exactly the state that caused the original failure.
    """
    for label, value in (("red-white", red_white_ohms), ("black-blue", black_blue_ohms)):
        if value is None or not isinstance(value, (int, float)):
            _refuse("motor-pair",
                    f"{label} resistance not measured ({value!r})",
                    "put a meter on it: red-white a few ohms, black-blue open")
    red_white = float(red_white_ohms)  # type: ignore[arg-type]
    black_blue = float(black_blue_ohms)  # type: ignore[arg-type]
    if math.isnan(red_white) or math.isnan(black_blue):
        _refuse("motor-pair", "resistance reading is NaN",
                "re-measure with a meter that settles")
    if not MOTOR_PAIR_MIN_OHMS <= red_white <= MOTOR_PAIR_MAX_OHMS:
        _refuse(
            "motor-pair",
            f"red-white reads {red_white} ohms; a motor winding is "
            f"{MOTOR_PAIR_MIN_OHMS}-{MOTOR_PAIR_MAX_OHMS} ohms",
            "you are probably on the encoder pair — recheck the colours",
        )
    if black_blue < ENCODER_PAIR_MIN_OHMS:
        _refuse(
            "motor-pair",
            f"black-blue reads {black_blue} ohms; it should be effectively open",
            "black is ground and blue is 3.3 V encoder power — they are not a pair",
        )
    return Check("motor-pair", True,
                 f"red-white {red_white} ohms, black-blue open")


def check_gpio(gp: int) -> Check:
    """Interlock 7 — GP23/24/25/29 belong to the CYW43439."""
    if isinstance(gp, bool) or not isinstance(gp, int):
        raise ValueError(f"GPIO number must be an int, got {gp!r}")
    if not GPIO_MIN <= gp <= GPIO_MAX:
        raise ValueError(f"GP{gp} does not exist on RP2350 "
                         f"(GP{GPIO_MIN}-GP{GPIO_MAX})")
    if gp in FORBIDDEN_GPIO:
        _refuse("gpio", f"GP{gp} is wired to the CYW43439 internally",
                "pick another pin — see the map in docs/WIRING.md §3")
    return Check("gpio", True, f"GP{gp}")


def check_logic_level_v(volts: float) -> Check:
    """Interlock 9 — 3.3 V logic.

    The FTDI adapter is voltage-selectable and ships at 5 V.  Set it to 3.3 V
    and verify on its VCC pin *before* it goes near the Pico.
    """
    if volts is None or not isinstance(volts, (int, float)) or math.isnan(float(volts)):
        _refuse("logic-level", f"logic level not measured ({volts!r})",
                "measure VCC on the adapter before connecting it")
    if not LOGIC_MIN_V <= float(volts) <= LOGIC_MAX_V:
        _refuse("logic-level",
                f"logic level is {volts} V; this bench is "
                f"{LOGIC_MIN_V}-{LOGIC_MAX_V} V",
                "set the FTDI jumper to 3.3 V and verify on its VCC pin")
    return Check("logic-level", True, f"{volts} V")


#: name -> callable, so a caller can enumerate the interlocks rather than
#: remembering them.  Entity-set completeness: if a rule in CLAUDE.md has no
#: entry here, the module is a subset of the rules and nobody notices which.
INTERLOCKS: dict[str, Callable[..., Check]] = {
    "analyser_probes": check_analyser_probes,
    "encoder_supply": check_encoder_supply_v,
    "common_ground": check_common_ground,
    "driver_vm": check_driver_vm_v,
    "pico_power_isolation": check_pico_power_isolation,
    "motor_pair_resistance": check_motor_pair_resistance,
    "gpio": check_gpio,
    "duty": None,          # filled in below, once check_duty is defined
    "logic_level": check_logic_level_v,
}

#: The config keys `preflight` demands.  Absent means refused, not skipped.
PREFLIGHT_KEYS: tuple[str, ...] = (
    "analyser_probes", "encoder_supply_v", "common_ground", "driver_vm_v",
    "motor_rail_reaches_pico", "red_white_ohms", "black_blue_ohms",
)


def preflight(config: Mapping[str, Any]) -> None:
    """Run every bench-configuration interlock.  Raises, or returns None.

    Refuses an incomplete config: an unstated value is not a safe value.
    """
    if not isinstance(config, Mapping):
        raise SafetyViolation(f"preflight config must be a mapping, "
                              f"got {type(config).__name__}")
    missing = [key for key in PREFLIGHT_KEYS if key not in config]
    if missing:
        raise SafetyViolation(
            "pre-flight config is incomplete; unstated is not safe. "
            f"Missing: {missing}",
            "state every value, even the obvious ones",
        )
    check_analyser_probes(config["analyser_probes"])
    check_encoder_supply_v(config["encoder_supply_v"])
    check_common_ground(config["common_ground"])
    check_driver_vm_v(config["driver_vm_v"])
    check_pico_power_isolation(config["motor_rail_reaches_pico"])
    check_motor_pair_resistance(config["red_white_ohms"], config["black_blue_ohms"])
    for gp in config.get("gpio", ()):
        check_gpio(gp)
    if "logic_level_v" in config:
        check_logic_level_v(config["logic_level_v"])


# --------------------------------------------------------------------------
# Run-time interlocks
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Limits:
    """The configurable half of the interlocks.

    Defaults are deliberately conservative.  Every one can be raised from the
    CLI, which means raising it is a decision someone typed — and a decision
    someone typed ends up in the manifest's argv.
    """

    max_abs_duty_frac: float = 1.0
    max_continuous_drive_s: float = 60.0
    max_total_drive_s: float = 900.0
    motors: tuple[int, ...] = MOTOR_NUMBERS

    def validated(self) -> "Limits":
        if not 0 < self.max_abs_duty_frac <= ABSOLUTE_MAX_DUTY_FRAC:
            raise ValueError(
                f"max_abs_duty_frac must be in (0, {ABSOLUTE_MAX_DUTY_FRAC}], "
                f"got {self.max_abs_duty_frac}"
            )
        if self.max_continuous_drive_s <= 0:
            raise ValueError("max_continuous_drive_s must be positive")
        if self.max_total_drive_s < self.max_continuous_drive_s:
            raise ValueError("max_total_drive_s must be >= max_continuous_drive_s")
        return self


def duty_check(duty_frac: float, limits: Limits | None = None) -> Check:
    """The duty interlock as a `Check`, without raising.  Used by `preflight_run`."""
    lim = limits or Limits()
    try:
        value = float(duty_frac)
    except (TypeError, ValueError):
        return Check("duty-range", False, f"duty {duty_frac!r} is not a number",
                     "pass a float in [-1, 1]")
    if math.isnan(value):
        # NaN fails every ordinary range comparison silently: `not (nan > 1.0)`
        # is True, so a naive guard lets it straight through to the PWM register.
        return Check("duty-range", False, "duty is NaN",
                     "check the arithmetic that produced it")
    magnitude = abs(value)
    if math.isinf(magnitude) or magnitude > ABSOLUTE_MAX_DUTY_FRAC:
        return Check(
            "duty-range", False,
            f"|duty| = {magnitude} exceeds 1.0 — this is a fraction, not a percent",
            "pass 0.45, not 45",
        )
    if magnitude > lim.max_abs_duty_frac:
        return Check(
            "duty-range", False,
            f"|duty| = {magnitude:.3f} exceeds the configured limit "
            f"{lim.max_abs_duty_frac:.3f}",
            "raise --max-duty if you really mean it",
        )
    return Check("duty-range", True, f"|duty| = {magnitude:.3f}")


def check_duty(duty_frac: float, limits: Limits | None = None) -> Check:
    """Interlock 8 — duty is a fraction in [-1, 1].  Raises on refusal.

    The most common real bug this catches is a percentage (`45`) where a
    fraction (`0.45`) was meant.  On a TB6612 that clamps harmlessly, but the
    *data* it produces is nonsense — and nonsense data that looks plausible is
    the expensive kind.
    """
    check = duty_check(duty_frac, limits)
    if not check.ok:
        raise SafetyViolation([check])
    return check


INTERLOCKS["duty"] = check_duty


def motor_check(motor: Any, limits: Limits | None = None) -> Check:
    lim = limits or Limits()
    if isinstance(motor, bool) or not isinstance(motor, int):
        return Check("motor-index", False,
                     f"motor must be an int, got {motor!r}",
                     f"motors are indexed {lim.motors}")
    if motor not in lim.motors:
        return Check("motor-index", False, f"motor {motor} is not one of {lim.motors}",
                     "0=left front, 1=left rear, 2=right front, 3=right rear")
    return Check("motor-index", True, f"motor {motor}")


def check_motor(motor: Any, limits: Limits | None = None) -> Check:
    check = motor_check(motor, limits)
    if not check.ok:
        raise SafetyViolation([check])
    return check


def check_drive_budget(estimated_drive_s: float, limits: Limits | None = None) -> Check:
    """Refuse an experiment whose *planned* drive time is already over budget.

    The early half of the interlock; `DriveGuard` is the live half.  Catching
    it here means the refusal happens before STBY ever goes high.
    """
    lim = limits or Limits()
    if estimated_drive_s <= 0:
        return Check("drive-budget", True, "no driving planned")
    if estimated_drive_s > lim.max_total_drive_s:
        return Check(
            "drive-budget", False,
            f"experiment plans {estimated_drive_s:.0f} s of driving, budget is "
            f"{lim.max_total_drive_s:.0f} s",
            "shorten dwell/repeats, or raise --max-total-drive-s deliberately",
        )
    return Check("drive-budget", True,
                 f"{estimated_drive_s:.0f} s planned of "
                 f"{lim.max_total_drive_s:.0f} s budget")


def check_ping(link: Link | None) -> Check:
    """Never drive a board you cannot talk to.

    If PING does not answer, the failsafe path (`STOP`, `STBY 0`) does not
    answer either — so the one thing that stops the motor is already broken
    before the motor starts.
    """
    if link is None:
        return Check("pico-ping", False, "no link was opened",
                     "check /dev/rover-pico (rover-bench doctor)")
    try:
        reply = link.command("PING")
    except DeviceError as exc:
        return Check("pico-ping", False, f"PING was refused: {exc}",
                     "is this the bench firmware?")
    except LinkError as exc:
        return Check("pico-ping", False, str(exc),
                     "replug the Pico, check it is not in BOOTSEL, run doctor")
    return Check("pico-ping", True, f"firmware {reply.get('fw') or 'unknown'}")


def check_analyser(analyser: object | None, *, required: bool) -> Check:
    """The analyser only has to exist when a capture was actually requested.

    Deadband and sweep do not need it; `jitter` is meaningless without it.
    Failing early beats discovering it after ten minutes of sweeping.
    """
    if not required:
        return Check("analyser", True, "not required by this experiment", skipped=True)
    if analyser is None:
        return Check("analyser", False, "a capture was requested but no analyser",
                     "plug in the FX2 and run rover-bench doctor")
    available = getattr(analyser, "available", None)
    if callable(available) and not available():
        return Check("analyser", False, "sigrok-cli is not installed",
                     "sudo apt install sigrok-cli sigrok-firmware-fx2lafw")
    present = getattr(analyser, "present", None)
    if callable(present) and not present():
        return Check("analyser", False, "no fx2lafw device answered --scan",
                     "check USB, the plugdev group, and the udev rule (doctor)")
    return Check("analyser", True, "fx2lafw present")


def check_sample_rate(rate_hz: int | None, capabilities: object | None) -> Check:
    """The rate must be one the device says it supports.

    Never falls back to a hardcoded rate: the repo does not document this
    clone's ceiling, and inventing one puts an unfalsifiable number in the
    manifest.
    """
    if rate_hz is None:
        return Check("sample-rate", True, "no capture requested", skipped=True)
    if capabilities is None:
        return Check("sample-rate", False,
                     "cannot verify the sample rate — no capabilities were read",
                     "run `rover-bench scan` and check the analyser answers --show")
    supports = getattr(capabilities, "supports", None)
    if callable(supports) and not supports(rate_hz):
        return Check("sample-rate", False,
                     f"{rate_hz} Hz is not supported by this analyser",
                     "let rover-bench choose the rate from the device's own list")
    return Check("sample-rate", True, f"{rate_hz} Hz")


def preflight_run(*, link: Link | None = None,
                  analyser: object | None = None,
                  capabilities: object | None = None,
                  duties: Iterable[float] = (),
                  motors: Iterable[Any] = (),
                  channels: Iterable[str] = (),
                  estimated_drive_s: float = 0.0,
                  sample_rate_hz: int | None = None,
                  needs_analyser: bool = False,
                  needs_link: bool = True,
                  limits: Limits | None = None) -> list[Check]:
    """Every run-time interlock, as a list of results.

    Returns rather than raises so callers can print the whole list — fixing a
    bench one failure at a time is a miserable way to spend an evening.  Call
    `assert_ok` to turn failures into a `SafetyViolation`.
    """
    lim = (limits or Limits()).validated()
    checks: list[Check] = [motor_check(m, lim) for m in motors]
    checks += [duty_check(d, lim) for d in duties]
    if channels:
        try:
            checks.append(check_analyser_probes(channels))
        except SafetyViolation as exc:
            checks.extend(exc.failures)
    checks.append(check_drive_budget(estimated_drive_s, lim))
    checks.append(check_ping(link) if needs_link
                  else Check("pico-ping", True, "no Pico needed", skipped=True))
    checks.append(check_analyser(analyser, required=needs_analyser))
    if needs_analyser and sample_rate_hz is None:
        # The rate is picked inside the experiment, from the device's own
        # advertised list, so there is nothing to verify here yet.  Saying so
        # beats "no capture requested", which would be a lie on a jitter run.
        checks.append(Check("sample-rate", True,
                            "chosen at capture time from the device's own list",
                            skipped=True))
    else:
        checks.append(check_sample_rate(sample_rate_hz if needs_analyser else None,
                                        capabilities))
    return checks


def assert_ok(checks: Sequence[Check]) -> None:
    """Raise `SafetyViolation` listing *every* failure, not just the first."""
    failures = [c for c in checks if not c.ok and not c.skipped]
    if failures:
        raise SafetyViolation(failures)


# --------------------------------------------------------------------------
# The live half of the interlock
# --------------------------------------------------------------------------

class DriveGuard:
    """Bounded, always-stopped motor drive.

    Usage::

        with DriveGuard(link, limits, clock) as guard:
            for duty in duties:
                guard.set_duty(motor, duty)
                guard.check()      # raises if the run has overrun

    Two guarantees, and both matter:

    * **The motor is always stopped on the way out** — normal return, raised
      exception, or Ctrl-C.  The exit path issues STOP *and then* STBY 0, belt
      and braces: STOP is the polite request, STBY 0 is the TB6612's hardware
      all-stop and works even if the PWM slice is misconfigured.
    * **Continuous drive is bounded.**  `check()` runs inside every experiment
      loop, so an experiment that stalls cannot sit there heating a motor whose
      real stall current nobody has measured yet.

    The clock is injected so `--dry-run` (virtual clock) exercises the same
    accounting the real run uses.
    """

    def __init__(self, link: Link, limits: Limits | None = None,
                 clock: Clock | None = None,
                 log: Callable[[str], None] | None = None) -> None:
        self.link = link
        self.limits = (limits or Limits()).validated()
        self.clock = clock or RealClock()
        self.log = log or (lambda _m: None)
        self.started_s: float | None = None
        self.moving_since_s: float | None = None
        self.total_drive_s = 0.0
        self.stopped = True

    def __enter__(self) -> "DriveGuard":
        self.started_s = self.clock.now()
        self.link.enable()
        self.log("STBY high — motors enabled")
        return self

    def __exit__(self, *_exc: object) -> None:
        self.release()

    def release(self) -> None:
        """Stop the motors.  Safe to call twice; never raises past the caller.

        Swallowing errors here is deliberate.  This runs on the exception path,
        and an exception raised *while cleaning up after another exception*
        loses the original diagnosis — which is the only thing that says why
        the motor is in a bad state.
        """
        self._account()
        for action, description in ((self.link.stop, "STOP"),
                                    (self.link.disable, "STBY low")):
            try:
                action()
                self.log(f"{description} — motors stopped")
            except LinkError as exc:  # pragma: no cover - depends on failure mode
                self.log(f"WARNING: {description} failed during cleanup: {exc}")
        self.stopped = True

    def _account(self) -> None:
        if self.moving_since_s is not None:
            self.total_drive_s += self.clock.now() - self.moving_since_s
            self.moving_since_s = None

    @property
    def continuous_drive_s(self) -> float:
        if self.moving_since_s is None:
            return 0.0
        return self.clock.now() - self.moving_since_s

    def set_duty(self, motor: int, duty_frac: float) -> None:
        """Range-check, then command.  The only way experiments drive."""
        try:
            check_duty(duty_frac, self.limits)
        except SafetyViolation:
            self.release()
            raise
        moving = abs(float(duty_frac)) > 0.0
        if moving and self.moving_since_s is None:
            self.moving_since_s = self.clock.now()
            self.stopped = False
        elif not moving:
            self._account()
            self.stopped = True
        self.link.set_duty(motor, duty_frac)

    def check(self) -> None:
        """Enforce the continuous-drive limit.  Call inside every loop."""
        elapsed = self.continuous_drive_s
        if elapsed > self.limits.max_continuous_drive_s:
            self.release()
            raise DriveTimeExceeded(elapsed, self.limits.max_continuous_drive_s)

    def rest(self, seconds: float, motors: Sequence[int] = ()) -> None:
        """Stop, then wait.

        Resting *with the motor stopped* is the point: it resets the continuous
        drive accounting and lets the H-bridge and motor cool, which keeps a
        long sweep from becoming a slow thermal ramp that shows up in the data
        as drift.
        """
        try:
            if motors:
                for motor in motors:
                    self.link.set_duty(motor, 0.0)
            else:
                # No motor named: STOP is the board's own "everything to zero",
                # and on a single-motor bench it is one frame instead of four
                # identical ones cluttering the analyser capture.
                self.link.stop()
        except LinkError:  # pragma: no cover - best effort on a rest
            pass
        self._account()
        self.stopped = True
        self.clock.sleep(seconds)
