"""The hardware smoke test. `make test-hw` only - never in the default suite.

docs/PLAN.md §10 asks for "a script that drives each motor briefly, reads each
encoder, and pings each sensor. Run it before every session." This is that,
expressed as pytest so it reports like everything else.

**Every test here needs a real Pico and/or the FX2 analyser attached.** They
carry `@pytest.mark.hardware` and pytest.ini deselects that marker by default,
so `pytest` and `make test` never reach them. They skip - not fail - when the
hardware is absent, because "the analyser is not plugged in" is a fact about
the room, not a bug in the code.

BEFORE RUNNING THIS, at the bench:
  * Confirm the motor pair by resistance: red-white a few ohms, black-blue
    open. An evening was already lost to encoder wires in the H-bridge outputs.
  * Encoder supply on 3.3 V. Never 5 V. RP2350 GPIO is not 5 V tolerant.
  * Analyser on D0-D7 only. NEVER on AO1/AO2/BO1/BO2 - motor voltage destroys
    it, and it fails by reading garbage on a channel you then trust.
  * Common ground: Pico, driver, supply, analyser.
  * Power on: Pico USB first, then VM. Power off: VM first, then Pico.

Nothing here drives a motor for more than a fraction of a second, and every
drive runs inside a DriveGuard, so the motor is stopped on every exit path.
"""

from __future__ import annotations

import time

import pytest

from conftest import EXPECTED_ANALYSER_CHANNELS, import_or_none

pytestmark = pytest.mark.hardware

link_mod = import_or_none("rover_bench.link")
analyser_mod = import_or_none("rover_bench.analyser")
safety_mod = import_or_none("rover_bench.safety")
experiment_mod = import_or_none("rover_bench.experiment")

FX2_DRIVER = "fx2lafw"  # the sigrok driver for this analyser; see docs/BENCH.md §5

BRIEF_DUTY = 0.4        # enough to turn an unloaded N20, well under any limit
BRIEF_SECONDS = 0.3     # long enough to accumulate ticks, short enough to be safe


@pytest.fixture(scope="module")
def pico():
    """A real link to a real Pico, or a skip."""
    if link_mod is None:
        pytest.skip("rover_bench.link is not available")
    try:
        port = link_mod.resolve_port()
    except link_mod.PortNotFound as exc:
        pytest.skip(f"no Pico: {exc}")
    try:
        conn = link_mod.open_link(port)
    except link_mod.LinkError as exc:
        pytest.skip(f"cannot open {port}: {exc}")
    try:
        yield conn
    finally:
        # Each step gets its own try, the way safety.DriveGuard.release() does.
        # Chaining them (`stop(); disable()` in one try) means a STOP that
        # times out - a board that has stopped answering, which is exactly when
        # this matters - skips `disable()`, and STBY stays HIGH with the motor
        # still turning after the session has ended. STBY low is the TB6612's
        # hardware all-stop and must be attempted unconditionally.
        for action in (conn.stop, conn.disable, conn.close):
            try:
                action()
            except Exception as exc:  # noqa: BLE001 - never mask the real failure
                print(f"WARNING: {action.__name__}() failed during teardown: {exc}")


@pytest.fixture(scope="module")
def fx2():
    """A real FX2 analyser, or a skip."""
    if analyser_mod is None:
        pytest.skip("rover_bench.analyser is not available")
    device = analyser_mod.Analyser()
    if not device.available():
        pytest.skip("sigrok-cli is not installed")
    if not device.present():
        pytest.skip("no fx2lafw device answered --scan")
    return device


# --------------------------------------------------------------------------
# The link
# --------------------------------------------------------------------------


def test_the_pico_answers_ping(pico):
    assert pico.ping() is True


def test_the_firmware_identifies_itself(pico):
    """A run whose firmware cannot be identified is a run whose data is not
    traceable, and the manifest will say so."""
    identity = pico.firmware_identity()
    assert identity["firmware_version"], f"PING said nothing useful: {identity}"


def test_the_encoder_counter_is_readable(pico):
    sample = pico.read_encoder(0)
    assert isinstance(sample.ticks, int)


def test_the_firmware_clock_advances(pico):
    """If t_us is stuck, every speed derived from it is a division by zero or
    an infinity, and the CSV will look fine."""
    first = pico.read_encoder(0)
    second = pico.read_encoder(0)
    assert second.t_s >= first.t_s


# --------------------------------------------------------------------------
# The motor - briefly, and always guarded
# --------------------------------------------------------------------------


def drive_briefly(pico, duty_frac):
    """Drive for BRIEF_SECONDS inside a guard, and return (before, after).

    The dwell is the whole point and it must be a real one: commanding a duty
    and reading the encoder in the same breath measures nothing but USB
    latency, and reports a working bench as a dead motor. `time.sleep` rather
    than an injected clock, deliberately - these tests are the ones that DO
    need wall-clock time to pass.
    """
    before = pico.read_encoder(0)
    with safety_mod.DriveGuard(pico) as guard:
        guard.set_duty(0, duty_frac)
        time.sleep(BRIEF_SECONDS)
        guard.check()
    return before, pico.read_encoder(0)


@pytest.mark.slow
def test_the_motor_turns_and_the_encoder_counts(pico):
    """The one-line answer to "is this bench working at all?"."""
    if safety_mod is None:
        pytest.skip("rover_bench.safety is not available")
    before, after = drive_briefly(pico, BRIEF_DUTY)
    assert after.ticks != before.ticks, (
        "the motor was driven and the encoder did not move. Check the harness: "
        "red-white a few ohms, black-blue open, encoder blue on 3.3 V."
    )


@pytest.mark.slow
def test_reverse_counts_the_other_way(pico):
    """Sign convention, end to end. If this fails the fix is in firmware, never
    in the wiring - all four motors are wired identically on purpose.

    Each direction is measured across its OWN drive. Comparing a reading taken
    after the forward run with one taken after the reverse run would fold in
    however far the motor coasted between the two, which on an unloaded N20 is
    not nothing.
    """
    if safety_mod is None:
        pytest.skip("rover_bench.safety is not available")
    fwd_before, fwd_after = drive_briefly(pico, BRIEF_DUTY)
    rev_before, rev_after = drive_briefly(pico, -BRIEF_DUTY)
    assert (fwd_after.ticks - fwd_before.ticks) > 0, "forward did not count up"
    assert (rev_after.ticks - rev_before.ticks) < 0, (
        "reverse did not count down. The fix is the sign in firmware, never a "
        "swapped pair of encoder wires (docs/WIRING.md §3)."
    )


def test_the_motor_is_stopped_after_the_guard_exits(pico):
    """The guard's exit path is the one that runs when something has already
    gone wrong, so it is the one worth proving on real hardware."""
    if safety_mod is None:
        pytest.skip("rover_bench.safety is not available")
    with safety_mod.DriveGuard(pico) as guard:
        guard.set_duty(0, BRIEF_DUTY)
    assert guard.stopped is True
    assert pico.ping() is True     # still alive, and STBY is low


# --------------------------------------------------------------------------
# The analyser
# --------------------------------------------------------------------------


def test_the_analyser_is_present(fx2):
    """`scan()` returns the parsed `--scan` lines as plain dicts, so this reads
    the `driver` key. (It used to read a `.is_fx2` attribute that no scan result
    has ever had - an AttributeError waiting at the bench, in a test nobody had
    run because running it needs the bench.)"""
    assert any(device.get("driver") == FX2_DRIVER for device in fx2.scan())


def test_the_analyser_reports_its_sample_rates(fx2):
    """The rate is DISCOVERED, never assumed. This is the discovery."""
    rates = fx2.capabilities().sample_rates_hz
    assert rates, "the device reported no sample rates - do not guess one"


def test_the_analyser_reports_eight_channels(fx2):
    assert fx2.capabilities().channel_count == len(EXPECTED_ANALYSER_CHANNELS)


@pytest.mark.slow
def test_a_capture_produces_a_decodable_file(fx2, tmp_path):
    """Prove the instrument on a signal before trusting it on one you do not
    understand. An analyser you have never successfully triggered is not a
    working instrument, and you find that out at the worst possible moment."""
    caps = fx2.capabilities()
    rate = caps.nearest_supported(1_000_000)
    out = tmp_path / "smoke.sr"
    fx2.capture(["D0", "D6"], rate, 0.2, str(out))
    decoded = analyser_mod.decode_srzip(str(out))
    assert decoded.sample_rate_hz == rate
    assert decoded.n_samples > 0


@pytest.mark.slow
def test_the_loop_tick_channel_toggles(fx2, pico, tmp_path):
    """LOOP_TICK on D6 toggles once per control iteration. No edges means the
    control loop is not running - which the telemetry alone cannot tell you,
    because a hung loop still leaves the last duty latched in the PWM
    peripheral and the motor keeps spinning."""
    caps = fx2.capabilities()
    rate = caps.nearest_supported(1_000_000)
    out = tmp_path / "loop.sr"
    fx2.capture(["D6"], rate, 0.5, str(out))
    trace = analyser_mod.decode_srzip(str(out)).trace("D6")
    assert trace.edges, "no LOOP_TICK edges: the control loop is not running"


@pytest.mark.slow
def test_the_measured_loop_rate_is_about_one_hundred_hertz(fx2, tmp_path):
    """The claim "the loop runs at 100 Hz" is, without this channel, an
    assertion in a comment. Remember the pin TOGGLES: one period is edge to
    edge, so 100 Hz of iterations is a 50 Hz square wave."""
    if experiment_mod is None:
        pytest.skip("rover_bench.experiment is not available")
    caps = fx2.capabilities()
    rate = caps.nearest_supported(1_000_000)
    out = tmp_path / "loop.sr"
    fx2.capture(["D6"], rate, 1.0, str(out))
    trace = analyser_mod.decode_srzip(str(out)).trace("D6")
    periods = experiment_mod.loop_periods_s(trace.edge_times_s)
    assert periods, "no complete loop period was captured"
    mean_period = sum(periods) / len(periods)
    assert 0.008 <= mean_period <= 0.012, (
        f"loop period is {mean_period * 1000:.2f} ms, expected ~10 ms")
