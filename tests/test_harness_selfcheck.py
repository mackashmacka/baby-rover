"""The harness testing itself.

A coverage gate that is not enforced, a marker that is not registered, a
"hardware is excluded by default" claim that is not true - each of these fails
silently and leaves everyone believing something that is not so. These are
cheap, and they are the reason the CLOSE ritual's "the suite is green" means
something.
"""

from __future__ import annotations

import configparser
import os
import pathlib
import subprocess
import tempfile

import pytest

from conftest import FORBIDDEN_GPIO, REPO_ROOT, HardwareAccessInTest

PYTEST_INI = REPO_ROOT / "pytest.ini"
COVERAGERC = REPO_ROOT / ".coveragerc"
MAKEFILE = REPO_ROOT / "Makefile"
REQUIREMENTS = REPO_ROOT / "tests" / "requirements.txt"
DOCTOR = REPO_ROOT / "tests" / "doctor.sh"


def ini() -> configparser.ConfigParser:
    parser = configparser.ConfigParser()
    parser.read(PYTEST_INI)
    return parser


def coveragerc() -> configparser.ConfigParser:
    parser = configparser.ConfigParser()
    parser.read(COVERAGERC)
    return parser


# --------------------------------------------------------------------------
# The default invocation excludes hardware
# --------------------------------------------------------------------------


def test_the_default_run_deselects_hardware_tests(request):
    """Asked of the LIVE session, not of the file: this is what is actually in
    force right now. `make test-hw` overrides it with its own -m."""
    assert "not hardware" in (request.config.getoption("-m") or "")


def test_the_hardware_marker_is_registered():
    assert "hardware:" in ini()["pytest"]["markers"]


def test_the_slow_marker_is_registered():
    assert "slow:" in ini()["pytest"]["markers"]


def test_markers_are_strict():
    """A typo'd marker must be an error, not a silently skipped test."""
    assert "--strict-markers" in ini()["pytest"]["addopts"]


def test_the_ini_config_is_strict():
    assert "--strict-config" in ini()["pytest"]["addopts"]


def test_testpaths_point_at_the_suite():
    assert ini()["pytest"]["testpaths"].strip() == "tests"


def test_xfail_is_not_strict_by_default():
    """The placeholder tests for modules that have not landed must go green the
    moment the module appears, not blow up."""
    assert ini()["pytest"]["xfail_strict"].strip().lower() in ("false", "no", "0")


# --------------------------------------------------------------------------
# The coverage gate is real
# --------------------------------------------------------------------------


def test_coverage_measures_the_host_package():
    assert coveragerc()["run"]["source"].strip() == "tools/rover_bench"


def test_the_coverage_gate_is_eighty_percent():
    assert int(coveragerc()["report"]["fail_under"]) == 80


def test_coverage_shows_missing_lines():
    """A percentage tells you the gate passed; the missing-line list tells you
    what to test next."""
    assert coveragerc()["report"]["show_missing"].strip().lower() == "true"


def test_coverage_measures_lines_not_a_blend_of_lines_and_branches():
    """The owner asked for 80% LINE coverage. With branch measurement on,
    coverage's total becomes a blended figure and "80%" quietly means something
    other than what was asked for."""
    assert coveragerc()["run"]["branch"].strip().lower() == "false"


def test_coverage_omits_the_tests_themselves():
    assert "tests" in coveragerc()["run"]["omit"]


def test_the_makefile_wires_the_gate_into_make_test():
    """`coverage report` exits 2 under fail_under, and `make test` must not
    swallow that - otherwise the gate is decorative."""
    text = MAKEFILE.read_text()
    assert "coverage" in text and "--rcfile=.coveragerc" in text


@pytest.mark.parametrize("target", ["test", "test-hw", "lint", "coverage", "doctor"])
def test_every_promised_make_target_exists(target):
    assert f"\n{target}:" in "\n" + MAKEFILE.read_text()


def test_make_test_hw_selects_the_hardware_marker():
    assert "-m hardware" in MAKEFILE.read_text()


def test_the_doctor_script_exists_and_is_executable():
    import os
    assert DOCTOR.is_file()
    assert os.access(DOCTOR, os.X_OK)


def test_the_test_dependencies_are_pinned_to_a_major_version():
    text = REQUIREMENTS.read_text()
    for package in ("pytest", "coverage"):
        assert package in text
    assert "<" in text, "requirements should carry an upper bound"


# --------------------------------------------------------------------------
# The no-hardware guard actually guards
# --------------------------------------------------------------------------


def test_the_guard_blocks_subprocess_spawning():
    """If this ever stops raising, every "needs no hardware" claim in this
    suite becomes an assumption instead of a fact."""
    with pytest.raises(HardwareAccessInTest):
        subprocess.run(["echo", "hello"])


def test_the_guard_blocks_popen():
    with pytest.raises(HardwareAccessInTest):
        subprocess.Popen(["echo", "hello"])


def test_the_guard_blocks_os_system():
    import os
    with pytest.raises(HardwareAccessInTest):
        os.system("echo hello")


@pytest.mark.allow_subprocess
def test_the_guard_can_be_opted_out_of_deliberately():
    """The escape hatch exists for genuinely inert commands - the firmware
    compile is the only user today - and using it has to be visible in the
    test's own decorator list."""
    assert subprocess.run(["true"]).returncode == 0


# --------------------------------------------------------------------------
# The ground truth this suite asserts against
# --------------------------------------------------------------------------


def test_no_bench_signal_is_assigned_to_a_cyw43439_pin():
    from conftest import EXPECTED_BENCH_PINS
    assert not (set(EXPECTED_BENCH_PINS.values()) & FORBIDDEN_GPIO)


def test_every_bench_signal_has_a_distinct_pin():
    from conftest import EXPECTED_BENCH_PINS
    pins = list(EXPECTED_BENCH_PINS.values())
    assert len(pins) == len(set(pins))


def test_the_analyser_channel_map_covers_eight_channels():
    from conftest import EXPECTED_ANALYSER_CHANNELS
    assert sorted(EXPECTED_ANALYSER_CHANNELS) == [f"D{i}" for i in range(8)]


def test_every_analyser_channel_maps_to_a_known_signal():
    from conftest import EXPECTED_ANALYSER_CHANNELS, EXPECTED_BENCH_PINS
    assert set(EXPECTED_ANALYSER_CHANNELS.values()) <= set(EXPECTED_BENCH_PINS)


# --------------------------------------------------------------------------
# The fixtures behave like the things they fake
# --------------------------------------------------------------------------


def test_the_fake_transport_drains_like_a_real_port(fake_transport):
    """A fake that counted reset_input_buffer but kept the bytes would make the
    link's drain look tested while proving nothing."""
    fake_transport.queue("stale\n")
    fake_transport.reset_input_buffer()
    assert fake_transport.readline() == b""


def test_the_fake_transport_records_what_was_written(fake_transport):
    fake_transport.write(b"PING\n")
    assert fake_transport.lines_written == ["PING"]


def test_the_frozen_clock_does_not_really_sleep(frozen_clock):
    """A two-second dwell must cost microseconds, or nobody runs the suite."""
    before = frozen_clock.now()
    frozen_clock.sleep(2.0)
    assert frozen_clock.now() == before + 2.0


def test_the_synthetic_motor_has_a_deadband(synthetic_motor):
    """Real N20s do not move below some duty, and a controller tested against a
    frictionless motor never meets the integrator's actual job."""
    assert synthetic_motor.effective_duty(0.01) == 0.0
    assert synthetic_motor.effective_duty(0.5) > 0.0


def test_the_synthetic_motor_settles_towards_its_steady_state(synthetic_motor):
    for _ in range(200):
        synthetic_motor.step(0.5)
    assert synthetic_motor.omega_rad_s == pytest.approx(
        synthetic_motor.steady_state_omega(0.5), rel=0.02)


def test_the_synthetic_motor_counts_ticks_in_both_directions(synthetic_motor):
    for _ in range(100):
        synthetic_motor.step(0.5)
    forward = synthetic_motor.ticks
    assert forward > 0
    for _ in range(300):
        synthetic_motor.step(-0.5)
    assert synthetic_motor.ticks < forward


def test_the_temp_repo_has_a_real_shaped_registry(temp_repo):
    assert "| ID | Date | Story | Question | Method | Result | Data |" in \
        temp_repo.read_registry()


def test_import_or_none_distinguishes_missing_from_broken():
    """A module that exists but explodes on import must not look identical to
    one that was never written - otherwise a whole file of tests xfails in
    silence."""
    from conftest import import_or_none
    assert import_or_none("rover_bench.definitely_not_a_module") is None
    assert import_or_none("json") is not None


# --------------------------------------------------------------------------
# The firmware half of the suite must not be able to vanish quietly
# --------------------------------------------------------------------------


@pytest.mark.allow_subprocess   # compiling C into a temp dir is inert
def test_the_firmware_library_compiles_when_a_compiler_exists():
    """434 of the 1127 default tests drive `firmware/src/*.c` through ctypes.
    `conftest._compile_firmware` returns a SKIP rather than an error when the
    compile fails, so that a bare checkout with no toolchain still runs the
    Python half - reasonable. The hole is that a *broken* firmware source (say
    somebody adds `#include "pico/stdlib.h"` to a file whose header swears it is
    HOST-PURE) produces the same 434 skips, and `make test` still exits 0 with
    the coverage gate green, because the gate measures `tools/rover_bench` only.

    So: if there IS a compiler on this machine, the compile must succeed. On a
    machine with no compiler this test skips along with the rest, which is the
    behaviour that was wanted in the first place.
    """
    import shutil

    import conftest

    compiler = os.environ.get("CC") or "cc"
    if shutil.which(compiler) is None:
        pytest.skip(f"no {compiler} on PATH - the firmware tests skip too, as designed")

    build = pathlib.Path(tempfile.mkdtemp(prefix="rover-fw-selfcheck-"))
    lib, reason = conftest._compile_firmware(build)
    assert lib is not None, (
        "a compiler is available but the host-pure firmware did not build, so "
        "every firmware test in this suite is silently skipping while the build "
        f"stays green:\n{reason}"
    )
