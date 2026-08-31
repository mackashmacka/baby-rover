"""One test per bug actually found and fixed. Never delete a test from here.

CLAUDE.md: "a regression test per bug fixed, in every change." A bug that was
fixed without a test is a bug that is free to come back.
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools"))

import pytest
from rover_bench import analyser, doctor


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
