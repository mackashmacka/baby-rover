"""One test per bug actually found and fixed. Never delete a test from here.

CLAUDE.md: "a regression test per bug fixed, in every change." A bug that was
fixed without a test is a bug that is free to come back.
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools"))

import pytest
from rover_bench import analyser, doctor, link


# ---------------------------------------------------------------------------
# 2026-09-01 — sigrok multi-line samplerate output was parsed as "no rates"
# ---------------------------------------------------------------------------
# libsigrok 0.5.2 (what Ubuntu 24.04 ships) does NOT print rates inline. It
# prints a header ending in ':' and then one rate per indented line. The parser
# only knew the inline comma-list and range forms, so capabilities() raised
# "reported no sample rates" against a perfectly healthy analyser — and the
# whole point of that code path is to refuse to GUESS a rate. A false negative
# there sends you hunting a cable fault that does not exist.

SIGROK_052_SHOW = """\
Driver functions:
    Logic analyzer
Scan options:
    conn
fx2lafw:conn=1.30 - Saleae Logic [S/N: Saleae Logic] with 8 channels: D0 D1 D2 D3 D4 D5 D6 D7
Channel groups:
    Logic: channels D0 D1 D2 D3 D4 D5 D6 D7
Supported configuration options across all channel groups:
    continuous: on, off
    limit_samples: 0 (current)
    conn: 1.30 (current)
    samplerate - supported samplerates:
      20 kHz (current)
      25 kHz
      1 MHz
      24 MHz
      48 MHz
"""


def test_multiline_samplerate_output_is_parsed():
    """The real output of the real analyser on this machine."""
    got = analyser.parse_show_output(SIGROK_052_SHOW)
    assert got["samplerates_hz"] == [20_000, 25_000, 1_000_000, 24_000_000, 48_000_000]
    assert got["channels"] == ["D0", "D1", "D2", "D3", "D4", "D5", "D6", "D7"]


def test_multiline_form_does_not_swallow_the_next_key():
    """A following non-indented key must terminate the rate list, not be eaten
    as a frequency."""
    text = SIGROK_052_SHOW + "captureratio: 0\n"
    assert 48_000_000 in analyser.parse_show_output(text)["samplerates_hz"]


def test_inline_forms_still_work():
    """The fix must not regress the shapes that already worked."""
    inline = "    samplerate: 20 kHz, 1 MHz, 24 MHz\n"
    assert analyser.parse_show_output(inline)["samplerates_hz"] == [
        20_000, 1_000_000, 24_000_000]
    rng = "    samplerate: 20 kHz - 24 MHz (in steps of 1 Hz)\n"
    assert analyser.parse_show_output(rng)["samplerates_hz"] == [20_000, 24_000_000]


def test_genuinely_absent_rates_still_raise():
    """The refusal must survive the fix. Guessing a timebase is the failure
    this code exists to prevent."""
    with pytest.raises(analyser.AnalyserError):
        analyser.parse_show_output("Driver functions:\n    Logic analyzer\n")


# ---------------------------------------------------------------------------
# 2026-09-01 — fx2lafw firmware glob looked for the wrong extension
# ---------------------------------------------------------------------------
# Ubuntu's sigrok-firmware-fx2lafw package installs fx2lafw-saleae-logic.FW,
# not .fx2. doctor reported "no firmware" with the firmware sitting installed,
# and pointed at an apt install that had already run.

def test_firmware_glob_matches_the_fw_extension_ubuntu_ships():
    assert any(g.endswith("fx2lafw-*.fw") for g in doctor.FIRMWARE_GLOBS), \
        "Ubuntu ships fx2lafw-*.fw; a .fx2-only glob reports installed firmware as missing"


def test_firmware_glob_still_matches_fx2():
    assert any(g.endswith("fx2lafw-*.fx2") for g in doctor.FIRMWARE_GLOBS)


# ---------------------------------------------------------------------------
# 2026-09-01 — "not a member" reported for a group that WAS granted
# ---------------------------------------------------------------------------
# usermod -aG takes effect at next login. Between running bench-setup.sh and
# logging out, membership is granted but every open() still fails. Reporting
# that as "not a member" sends you to re-run a command that already worked.

def test_granted_but_inactive_group_is_reported_distinctly():
    env = doctor.Environment(groups=lambda: ["plugdev"],
                             granted_groups=lambda: ["dialout", "plugdev"])
    findings = {f.name: f for f in doctor.check_groups(env)}
    dialout = findings["group: dialout"]
    assert "granted" in dialout.detail
    assert "newgrp" in dialout.fix
    assert "usermod" not in dialout.fix, "advice for the wrong bug"


def test_genuinely_missing_group_still_says_usermod():
    env = doctor.Environment(groups=lambda: ["plugdev"],
                             granted_groups=lambda: ["plugdev"])
    findings = {f.name: f for f in doctor.check_groups(env)}
    assert "usermod" in findings["group: dialout"].fix


def test_a_synthetic_group_view_never_falls_back_to_the_real_system():
    """The bug in the fix for the bug: reading the laptop's real /etc/group from
    inside a unit test made the result depend on who ran the suite."""
    assert doctor._granted_groups(doctor.Environment(groups=lambda: ["users"])) == []


# --------------------------------------------------------------------------
# 2026-09-01 — doctor called a healthy analyser broken
# --------------------------------------------------------------------------

def test_doctor_retries_a_cold_analyser_instead_of_failing_it():
    """A cold FX2 has no firmware in RAM.

    The first sigrok call after a replug uploads it; the device then drops off
    the bus and re-enumerates, and anything racing that window answers "No
    devices found".  Observed for real on 2026-09-01: `doctor` reported
    `--show failed`, and three seconds later the identical command listed all
    eight channels.  The instrument was fine.

    This matters more than a cosmetic wrong label.  `doctor` is the thing that
    decides whether the bench may run at all, so a false FAIL here stops a
    session that had nothing wrong with it — and it trains the owner to ignore
    the one check whose whole job is to be believed.
    """
    # NOTE: `analyser.AnalyserError` from this module's own import, NOT
    # `tools.rover_bench.analyser`.  conftest puts `tools/` on sys.path, so the
    # same file is importable under two names and yields two distinct class
    # objects — an `except` against the wrong one silently never matches.
    AnalyserError = analyser.AnalyserError
    from test_doctor import a_healthy_environment, by_name

    class ColdThenWarm:
        """Fails exactly as the real device does, then comes good."""

        def __init__(self):
            self.calls = 0

        def available(self):
            return True

        def present(self):
            return True

        def scan(self):
            return [{"driver": "fx2lafw", "description": "Saleae Logic",
                     "channels": [f"D{i}" for i in range(8)]}]

        def capabilities(self):
            self.calls += 1
            if self.calls == 1:
                raise AnalyserError("sigrok-cli --driver fx2lafw --show "
                                    "failed (rc=1): No devices found.")

            class Caps:
                sample_rates_hz = (20_000, 24_000_000)
                channels = tuple(f"D{i}" for i in range(8))
                max_sample_rate_hz = 48_000_000
            return Caps()

    instrument = ColdThenWarm()
    findings = by_name(doctor.diagnose(
        ".", env=a_healthy_environment(analyser=instrument)))

    assert findings["analyser answers"].status == doctor.OK
    assert instrument.calls == 2, "should have retried exactly once, not given up"


def test_doctor_still_warns_when_the_analyser_never_answers():
    """The retry must not paper over a genuinely dead instrument.

    Three failures is a real fault, and doctor has to keep saying so — the
    point of the retry is to remove a false alarm, not to become unfalsifiable.
    """
    # NOTE: `analyser.AnalyserError` from this module's own import, NOT
    # `tools.rover_bench.analyser`.  conftest puts `tools/` on sys.path, so the
    # same file is importable under two names and yields two distinct class
    # objects — an `except` against the wrong one silently never matches.
    AnalyserError = analyser.AnalyserError
    from test_doctor import a_healthy_environment, by_name

    class NeverAnswers:
        def __init__(self):
            self.calls = 0

        def available(self):
            return True

        def present(self):
            return True

        def scan(self):
            return [{"driver": "fx2lafw", "description": "Saleae Logic"}]

        def capabilities(self):
            self.calls += 1
            raise AnalyserError("No devices found.")

    instrument = NeverAnswers()
    findings = by_name(doctor.diagnose(
        ".", env=a_healthy_environment(analyser=instrument)))

    assert not findings["analyser answers"].status == doctor.OK
    assert instrument.calls == doctor.ANALYSER_COLD_RETRIES
    assert "3 attempts" in findings["analyser answers"].detail


def test_doctor_does_not_really_sleep_between_retries_in_tests():
    """The delay is injected, so the suite never pays a second of wall clock.

    A retry loop that calls `time.sleep` directly is untestable without making
    the suite slow, and a slow suite is one that stops being run.
    """
    import inspect
    source = inspect.getsource(doctor.check_analyser_answers)
    assert "env.sleep" in source
    assert "time.sleep(" not in source


# --------------------------------------------------------------------------
# 2026-09-01 — a "no hardware needed" test needed hardware
# --------------------------------------------------------------------------

def test_open_link_never_touches_the_real_filesystem_when_seams_are_injected():
    """`open_link`'s test seam was incomplete and nobody could tell.

    `transport_factory` let a test supply a fake transport, but `open_link`
    still called `resolve_port(port)` with the default `os.path.exists`.  So the
    port-existence check went to the real /dev regardless, and
    `test_open_link_uses_the_injected_transport_factory` passed only because a
    Pico happened to be attached to this laptop.  When the USB dock dropped on
    2026-09-01 it failed with `PortNotFound` — from a pure unit test.

    That is the worst kind of suite defect: it does not report a false failure,
    it reports a false *pass*, and it makes the whole "1143 tests, none need
    hardware" claim untrue in a way no one would notice until CI, or a laptop
    without a rover on it.
    """
    calls = []

    def never_call_me(path):
        calls.append(path)
        raise AssertionError("open_link reached the real filesystem")

    class Dummy:
        def write(self, data): pass
        def read_line(self, timeout_s=None): return ""
        def close(self): pass

    conn = link.open_link(
        "/dev/does-not-exist",
        transport_factory=lambda port, baud, timeout_s: Dummy(),
        exists=lambda path: True,
        globber=never_call_me,
    )
    conn.close()
    assert calls == []


def test_open_link_still_refuses_a_port_that_is_really_absent():
    """The seam must not become a way to skip the check in production.

    With no injection, an absent port is still a hard error — the operator gets
    told the Pico is not there rather than a confusing failure three calls later.
    """
    with pytest.raises(link.PortNotFound):
        link.open_link("/dev/definitely-not-a-real-port-12345")
