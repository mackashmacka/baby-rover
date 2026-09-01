"""`rover-bench doctor` - "why doesn't my bench work?"

Doctor's whole job is to run on a machine where nothing is installed and say
what to install. So it must never raise, never require an import it is
reporting as missing, and never need the hardware it is looking for. Its whole
environment is injected, which is what lets this file describe a broken laptop
that is not this one.

The property that matters most is the last section: **a failing check always
carries an actionable fix.** A diagnostic that says "analyser: FAIL" and stops
is a diagnostic nobody uses twice.
"""

from __future__ import annotations

import pytest

from conftest import import_or_none

doctor = import_or_none("rover_bench.doctor")

pytestmark = pytest.mark.xfail(
    doctor is None,
    reason="rover_bench.doctor has not landed yet; these activate when it does",
    strict=False,
)


def an_environment(**kw):
    """A machine with nothing on it, unless a test says otherwise."""
    defaults = dict(
        which=lambda name: None,
        exists=lambda path: False,
        globber=lambda pattern: [],
        read_text=lambda path: "",
        groups=lambda: [],
        analyser=None,
        can_write=lambda path: True,
        sleep=lambda seconds: None,
    )
    defaults.update(kw)
    return doctor.Environment(**defaults)


def a_healthy_environment(**kw):
    """A machine where everything is installed and plugged in."""
    class OkAnalyser:
        def available(self):
            return True

        def present(self):
            return True

        def scan(self):
            return [{"driver": "fx2lafw", "conn": "1.10",
                     "description": "Saleae Logic",
                     "channels": [f"D{i}" for i in range(8)]}]

        def capabilities(self):
            class Caps:
                sample_rates_hz = (20_000, 24_000_000)
                channels = tuple(f"D{i}" for i in range(8))
                max_sample_rate_hz = 24_000_000
            return Caps()

    def globber(pattern):
        if "idVendor" in pattern:
            # doctor reads (vid, pid) straight out of sysfs rather than
            # shelling out to lsusb, so a healthy machine has to look like one
            # there too.
            return ["/sys/bus/usb/devices/1-1/idVendor",
                    "/sys/bus/usb/devices/1-2/idVendor"]
        return ["/dev/ttyACM0"]

    def read_text(path):
        if path.endswith("1-1/idVendor"):
            return "0925\n"          # the FX2 analyser
        if path.endswith("1-1/idProduct"):
            return "3881\n"
        if path.endswith("1-2/idVendor"):
            return "2e8a\n"          # the Pico
        if path.endswith("1-2/idProduct"):
            return "0005\n"
        return ""

    defaults = dict(
        which=lambda name: f"/usr/bin/{name}",
        exists=lambda path: True,
        globber=globber,
        read_text=read_text,
        groups=lambda: ["dialout", "plugdev"],
        analyser=OkAnalyser(),
        can_write=lambda path: True,
        sleep=lambda seconds: None,
    )
    defaults.update(kw)
    return doctor.Environment(**defaults)


def by_name(findings):
    return {f.name: f for f in findings}


# --------------------------------------------------------------------------
# Doctor runs on a broken machine
# --------------------------------------------------------------------------


def test_doctor_runs_on_a_machine_with_nothing_installed(tmp_path):
    """The machine being diagnosed is by definition the broken one, so doctor
    raising is doctor failing at its only job."""
    findings = doctor.diagnose(str(tmp_path), env=an_environment())
    assert findings


def test_doctor_reports_missing_sigrok_rather_than_crashing(tmp_path):
    findings = by_name(doctor.diagnose(str(tmp_path), env=an_environment()))
    sigrok = next(f for name, f in findings.items() if "sigrok" in name.lower())
    assert sigrok.failed or sigrok.status == doctor.WARN


def test_doctor_reports_a_missing_pico(tmp_path):
    findings = doctor.diagnose(str(tmp_path), env=an_environment())
    assert any("pico" in f.name.lower() for f in findings)


def test_doctor_reports_missing_group_membership(tmp_path):
    """Not being in `dialout` is a permissions failure that presents as "the
    port does not exist", which sends people off to check their wiring."""
    findings = doctor.diagnose(str(tmp_path),
                               env=an_environment(groups=lambda: ["users"]))
    dialout = next(f for f in findings if "dialout" in f.name)
    assert dialout.failed
    assert "usermod" in dialout.fix


def test_doctor_says_so_when_it_cannot_read_group_membership(tmp_path):
    """"I could not check" must not read as "you are a member"."""
    findings = doctor.diagnose(str(tmp_path), env=an_environment(groups=lambda: []))
    assert any(f.status == doctor.WARN and "group" in f.name.lower()
               for f in findings)


def test_doctor_is_happy_with_a_healthy_machine(tmp_path):
    """The only thing that may still fail here is a genuinely absent Python
    module - `check_module` really imports, and pyserial is not a test
    dependency."""
    findings = doctor.diagnose(str(tmp_path), env=a_healthy_environment())
    blocking = [f.name for f in findings
                if f.failed and "serial" not in f.name.lower()]
    assert blocking == [], blocking


def test_doctor_distinguishes_enumerating_from_working(tmp_path):
    """memory/logic-analyser.md is blunt about this: an analyser you have never
    successfully triggered is not a working instrument, and you find that out
    at the worst possible moment. Enumerating on USB and answering a scan are
    two different findings, on purpose."""
    findings = by_name(doctor.diagnose(str(tmp_path), env=a_healthy_environment()))
    assert "analyser on USB" in findings and "analyser answers" in findings


def test_doctor_reports_an_analyser_that_enumerates_but_does_not_answer(tmp_path):
    class Silent:
        def available(self):
            return True

        def present(self):
            return False

        def scan(self):
            return []

    findings = by_name(doctor.diagnose(
        str(tmp_path), env=a_healthy_environment(analyser=Silent())))
    assert findings["analyser on USB"].status == doctor.OK
    assert findings["analyser answers"].failed


def test_doctor_finds_the_analyser_by_its_usb_id(tmp_path):
    """0925:3881, read from sysfs rather than by shelling out to lsusb - which
    keeps doctor working on the machine that has no usbutils, i.e. exactly the
    machine that needs doctor."""
    findings = by_name(doctor.diagnose(str(tmp_path), env=a_healthy_environment()))
    assert "0925:3881" in findings["analyser on USB"].detail


def test_doctor_says_the_analyser_is_absent_when_it_is(tmp_path):
    env = a_healthy_environment(read_text=lambda path: "1234\n")
    findings = by_name(doctor.diagnose(str(tmp_path), env=env))
    assert findings["analyser on USB"].failed


def test_doctor_never_needs_the_analyser_to_be_present(tmp_path):
    """`probe_analyser=False` exists so doctor can run with nothing plugged in."""
    findings = doctor.diagnose(str(tmp_path), env=an_environment(),
                               probe_analyser=False)
    assert findings


# --------------------------------------------------------------------------
# Verdict and exit code
# --------------------------------------------------------------------------


def test_a_blocking_problem_is_a_nonzero_exit(tmp_path):
    """So a session-start script can gate on it."""
    findings = doctor.diagnose(str(tmp_path), env=an_environment())
    assert doctor.exit_code(findings) == 1


def test_a_healthy_machine_is_a_zero_exit(tmp_path):
    findings = [doctor.Finding("a", doctor.OK), doctor.Finding("b", doctor.WARN)]
    assert doctor.exit_code(findings) == 0


def test_a_warning_alone_does_not_block(tmp_path):
    """matplotlib is only needed for plots. Refusing to take data because a
    plotting library is missing would be absurd."""
    assert doctor.exit_code([doctor.Finding("x", doctor.WARN)]) == 0


# --------------------------------------------------------------------------
# The report a human reads
# --------------------------------------------------------------------------


def test_the_report_lists_every_finding(tmp_path):
    findings = doctor.diagnose(str(tmp_path), env=an_environment())
    text = doctor.render(findings)
    for finding in findings:
        assert finding.name in text


def test_the_report_ends_with_the_wiring_rules(tmp_path):
    """The six rules that have each already cost money or an evening, printed
    where someone about to touch hardware will see them."""
    text = doctor.render(doctor.diagnose(str(tmp_path), env=an_environment()))
    assert "AO1" in text
    assert "3.3 V" in text


def test_the_report_names_the_blockers(tmp_path):
    text = doctor.render(doctor.diagnose(str(tmp_path), env=an_environment()))
    assert "blocking problem" in text.lower()


def test_a_healthy_report_says_so():
    """Built from findings rather than from this laptop, so the verdict text is
    tested rather than whatever happens to be installed here."""
    text = doctor.render([doctor.Finding("python", doctor.OK, "3.12.3"),
                          doctor.Finding("sigrok-cli", doctor.OK, "/usr/bin/x")])
    assert "ready" in text.lower()


def test_a_report_with_only_warnings_says_there_are_no_blockers():
    text = doctor.render([doctor.Finding("matplotlib", doctor.WARN, "absent",
                                         "pip install matplotlib")])
    assert "no blockers" in text.lower()


def test_a_finding_renders_its_fix_when_it_failed():
    finding = doctor.Finding("sigrok", doctor.FAIL, "not on PATH",
                             "sudo apt install sigrok-cli")
    assert "fix:" in finding.render()


def test_a_passing_finding_does_not_nag_with_a_fix():
    finding = doctor.Finding("sigrok", doctor.OK, "0.7.2", "sudo apt install x")
    assert "fix:" not in finding.render()


def test_every_failing_check_carries_a_fix(tmp_path):
    """A diagnostic that says "FAIL" and stops is one nobody uses twice."""
    findings = doctor.diagnose(str(tmp_path), env=an_environment())
    unactionable = [f.name for f in findings
                    if f.status in (doctor.FAIL, doctor.WARN) and not f.fix]
    assert not unactionable, f"failing checks with no fix: {unactionable}"


def test_the_setup_script_is_offered_as_the_blanket_fix(tmp_path):
    """Most of these are one `tools/bench-setup.sh` away."""
    text = doctor.render(doctor.diagnose(str(tmp_path), env=an_environment()))
    assert "bench-setup.sh" in text


# --------------------------------------------------------------------------
# Individual probes
# --------------------------------------------------------------------------


def test_the_python_version_check_passes_on_this_interpreter():
    assert not doctor.check_python().failed


def test_a_missing_required_module_fails():
    finding = doctor.check_module("definitely_not_installed", why="w", fix="f")
    assert finding.failed


def test_a_missing_optional_module_only_warns():
    finding = doctor.check_module("definitely_not_installed", why="w", fix="f",
                                  required=False)
    assert not finding.failed


def test_a_present_module_passes():
    assert not doctor.check_module("json", why="w", fix="f").failed


def test_the_udev_rule_check_looks_for_the_documented_file():
    """memory/logic-analyser.md: on Linux the whole Zadig problem is one udev
    rule. Its absence is why the analyser enumerates for root and not for you."""
    missing = doctor.check_udev_rules(an_environment(exists=lambda p: False))
    present = doctor.check_udev_rules(an_environment(exists=lambda p: True))
    assert missing.status == doctor.WARN
    assert present.status == doctor.OK
    assert "udevadm" in missing.fix


def test_the_pico_port_check_finds_a_ttyacm():
    finding = doctor.check_pico_port(
        an_environment(globber=lambda p: ["/dev/ttyACM0"]))
    assert not finding.failed


def test_the_experiments_directory_check_notices_it_is_unwritable(tmp_path):
    """A run that cannot write its manifest has not happened, whatever the
    terminal said."""
    finding = doctor.check_experiments_dir(
        an_environment(exists=lambda p: True, can_write=lambda p: False),
        str(tmp_path))
    assert finding.failed or finding.status == doctor.WARN
