"""The `rover-bench` CLI.

The bar is deliberately low and deliberately universal: **every subcommand runs
under `--dry-run` and exits 0.** That single property catches the failure that
actually happens - an argparse typo or an import error in a subcommand nobody
has run since it was written, discovered at the bench with a motor wired up and
twenty minutes of daylight left.

`--dry-run` must also be genuinely inert. The autouse no-hardware guard in
conftest makes that testable rather than aspirational: a dry run that spawns
sigrok or opens a port raises instead of quietly working.
"""

from __future__ import annotations

import pytest

from conftest import import_or_none

cli = import_or_none("rover_bench.cli")

pytestmark = pytest.mark.xfail(
    cli is None,
    reason="rover_bench.cli has not landed yet; these activate when it does",
    strict=False,
)

#: The minimum set. The module may offer more; it may not offer fewer.
EXPECTED_SUBCOMMANDS = {"doctor", "scan", "capture", "run"}


def invoke(argv):
    """Run main() and normalise the exit code, however it comes back."""
    try:
        return cli.main(argv)
    except SystemExit as exc:
        return exc.code if exc.code is not None else 0


def test_help_exits_zero():
    assert invoke(["--help"]) == 0


def test_no_arguments_prints_usage_rather_than_a_traceback():
    assert invoke([]) in (0, 1, 2)


def test_the_expected_subcommands_are_declared():
    assert EXPECTED_SUBCOMMANDS <= set(cli.SUBCOMMANDS)


def test_every_subcommand_runs_under_dry_run():
    """Looped rather than parametrised on purpose: it picks up a new subcommand
    the day it is added, with no edit to this file."""
    failures = []
    for name in cli.SUBCOMMANDS:
        try:
            code = invoke([name, "--dry-run"])
        except Exception as exc:  # noqa: BLE001 - report them all at once
            failures.append(f"{name}: raised {type(exc).__name__}: {exc}")
            continue
        if code != 0:
            failures.append(f"{name}: exit {code}")
    assert not failures, "subcommands failed under --dry-run:\n  " + \
        "\n  ".join(failures)


def test_every_subcommand_accepts_help():
    for name in cli.SUBCOMMANDS:
        assert invoke([name, "--help"]) == 0, f"{name} --help did not exit 0"


def test_an_unknown_subcommand_is_a_nonzero_exit():
    assert invoke(["definitely-not-a-subcommand"]) not in (0, None)


def test_main_returns_an_int():
    """`sys.exit(main())` needs an int. Returning None means "success" by
    accident, and hides a failing subcommand from any CI that checks."""
    assert isinstance(invoke(["doctor", "--dry-run"]), int)


def test_a_dry_run_writes_nothing_into_the_working_directory(tmp_path, monkeypatch):
    """A dry run that leaves files in the repo is a dry run that can be
    committed as if it were data. The scratch root is outside the tree on
    purpose."""
    monkeypatch.chdir(tmp_path)
    for name in cli.SUBCOMMANDS:
        invoke([name, "--dry-run"])
    assert list(tmp_path.iterdir()) == []


def test_the_dry_run_scratch_root_is_outside_the_repo():
    from conftest import REPO_ROOT
    assert REPO_ROOT not in type(REPO_ROOT)(cli.DRY_RUN_ROOT).parents


def test_a_dry_run_touches_no_hardware():
    """Belt and braces: the autouse guard raises HardwareAccessInTest if a dry
    run spawns a process or opens a port, so reaching the end IS the assertion."""
    for name in cli.SUBCOMMANDS:
        invoke([name, "--dry-run"])


def test_the_capture_subcommand_refuses_to_probe_a_motor_output():
    """The CLI is where a tired human at a bench types a channel name."""
    assert invoke(["capture", "--dry-run", "--channels", "D0,AO1"]) not in (0, None)


def test_an_out_of_range_duty_is_refused_by_the_cli():
    """45 where 0.45 was meant. It clamps harmlessly on a TB6612 and produces
    plausible nonsense data, which is the expensive kind."""
    assert invoke(["run", "--dry-run", "--motor", "0", "--duty", "45"]) not in (0, None)


def test_an_invalid_motor_is_refused_by_the_cli():
    assert invoke(["run", "--dry-run", "--motor", "9"]) not in (0, None)


def test_a_safety_violation_prints_its_fix_rather_than_a_traceback(capsys):
    """A traceback at a bench with a motor wired up tells the operator nothing
    they can act on."""
    invoke(["capture", "--dry-run", "--channels", "AO1"])
    captured = capsys.readouterr()
    assert "AO1" in (captured.out + captured.err)
