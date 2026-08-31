"""cli.py — `rover-bench`, the one command that characterises a motor.

THE POINT OF THE WHOLE PACKAGE, IN ONE LINE
--------------------------------------------
    tools/rover-bench run --motor 0

That drives the motor through every experiment in Story 1.5, captures the
analyser, writes CSV + `.sr` + a JSON manifest onto a deterministic path, and
appends a row to `experiments/REGISTRY.md`.  Repeat for motors 1, 2 and 3 and
the four data sets are comparable *by construction*, because the manifests can
be diffed and the only thing that changed is the motor number.

`--dry-run` IS A FIRST-CLASS FEATURE
------------------------------------
Every subcommand accepts it, and under it the CLI runs the identical code path
with `fakes.FakePico` and `fakes.FakeSigrok` in place of the hardware.  Three
things that buys:

  * an agent (or a tired human) can self-check before touching a real motor;
  * the test suite gets coverage of the orchestration, not just the leaves;
  * a dry run touches no hardware **and writes nothing into the repo** — its
    output goes to a scratch directory under the system temp dir, so a dry run
    can never be mistaken for data.

EXIT CODES
----------
0 success, 1 the thing it was asked to check is broken (doctor found a blocker,
a run failed), 2 the command itself was wrong (bad arguments, refused by an
interlock).  `main()` always returns an int — returning None would read as
success and hide a failing subcommand from CI.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

from . import __version__, TOOL_NAME
from . import doctor as doctor_mod
from . import experiment as experiment_mod
from . import manifest as manifest_mod
from . import registry as registry_mod
from . import storage as storage_mod
from .analyser import (
    Analyser,
    AnalyserError,
    CHANNEL_MAP,
    decode_srzip,
    validate_channels,
)
from .experiment import (
    DEFAULT_EXPERIMENT_IDS,
    ExperimentSpec,
    STORY_IDS,
    TicksPerRev,
)
from .fakes import build_fake_bench
from .link import Link, LinkError, RealClock, open_link
from .safety import Limits, SafetyViolation, WIRING_CHECKLIST

#: The minimum set.  `test_every_subcommand_runs_under_dry_run` walks this, so
#: adding a name here without a handler fails loudly rather than quietly.
SUBCOMMANDS: tuple[str, ...] = (
    "doctor", "scan", "ping", "capture",
    "deadband", "sweep", "step", "ticks-per-rev", "jitter",
    "run", "report",
)

#: Scratch root for `--dry-run`.  Stable (so successive dry runs land in the
#: same place and can be inspected) and outside the repo (so a dry run can
#: never be committed as if it were data).
DRY_RUN_ROOT = os.path.join(tempfile.gettempdir(), "rover-bench-dryrun")

EXIT_OK, EXIT_PROBLEM, EXIT_USAGE = 0, 1, 2

#: The interlock defaults, read from `safety.Limits` rather than repeated here.
#: They were repeated once, and the copy said 30 s where `Limits` said 60 s —
#: so every CLI run silently got a limit below the longest legitimate sweep.
_LIMIT_DEFAULTS = Limits()


# --------------------------------------------------------------------------
# The world a command runs in
# --------------------------------------------------------------------------

@dataclass
class World:
    """Everything a subcommand needs, already resolved.

    Built once, in one place, so no handler has to know whether it is running
    against hardware or fakes.
    """

    args: argparse.Namespace
    root: str
    dry_run: bool
    clock: Any
    limits: Limits
    log: Callable[[str], None]
    out: Callable[[str], None]
    link: Link | None = None
    analyser: Analyser | None = None
    prompt: Callable[[str], str] = input
    registry_path: str = ""

    def close(self) -> None:
        if self.link is not None:
            self.link.close()


def _make_world(args: argparse.Namespace, *, need_link: bool = False,
                need_analyser: bool = False) -> World:
    """Resolve hardware (or fakes) once, according to `--dry-run`."""
    verbose = getattr(args, "verbose", False)
    out: Callable[[str], None] = print
    log: Callable[[str], None] = (lambda msg: print(msg)) if verbose else (lambda _m: None)

    limits = Limits(
        max_abs_duty_frac=getattr(args, "max_duty", 1.0),
        max_continuous_drive_s=getattr(args, "max_drive_s",
                                       _LIMIT_DEFAULTS.max_continuous_drive_s),
        max_total_drive_s=getattr(args, "max_total_drive_s",
                                  _LIMIT_DEFAULTS.max_total_drive_s),
    )
    try:
        limits = limits.validated()
    except ValueError as exc:
        # A bad --max-* is a refusal, not a bug: print it with its fix instead
        # of a traceback at a bench with a motor wired up.
        raise SafetyViolation(
            f"the configured limits are not usable: {exc}",
            "--max-duty is a fraction in (0, 1]; --max-total-drive-s must be "
            ">= --max-drive-s",
        ) from exc

    if args.dry_run:
        bench = build_fake_bench(log=log)
        # The fake board, like the real one, drives one motor at a time.  Point
        # it at the motor the operator named so `--motor 2` exercises motor 2's
        # (deliberately different) fake constants.
        motor = getattr(args, "motor", 0)
        if motor in bench.pico.motors:
            bench.pico.under_test = motor
        # `--repo` is deliberately ignored under --dry-run.  This module
        # promises that a dry run writes nothing into the repo, and honouring
        # --repo would break that promise in the most expensive way available:
        # simulated CSVs, a simulated registry row, and — worst — a simulated
        # `ticks-per-rev` manifest sitting in experiments/ where the next REAL
        # run would inherit its FAKE constant as the divisor of every rad/s.
        requested = getattr(args, "repo", None)
        root = DRY_RUN_ROOT
        if requested and os.path.abspath(requested) != os.path.abspath(root):
            out(f"note: --dry-run writes to {root}, not {requested} — "
                "simulated data never lands in the repo")
        os.makedirs(os.path.join(root, "experiments"), exist_ok=True)
        registry_path = os.path.join(root, "experiments", "REGISTRY.md")
        registry_mod.ensure_registry(registry_path)
        return World(
            args=args, root=root, dry_run=True, clock=bench.clock, limits=limits,
            log=log, out=out, link=bench.link, analyser=bench.analyser,
            prompt=_fake_prompt(bench), registry_path=registry_path,
        )

    root = getattr(args, "repo", None) or _find_repo_root()
    world = World(args=args, root=root, dry_run=False, clock=RealClock(),
                  limits=limits, log=log, out=out,
                  registry_path=os.path.join(root, "experiments", "REGISTRY.md"))
    if need_analyser:
        world.analyser = Analyser()
    if need_link:
        world.link = open_link(getattr(args, "port", None), clock=world.clock,
                               log=log)
    return world


def _fake_prompt(bench: Any) -> Callable[[str], str]:
    """In a dry run the operator is simulated: the shaft "gets turned".

    Without this, `ticks-per-rev --dry-run` would block on stdin forever, which
    is exactly the failure `--dry-run` exists to catch before the bench.
    """
    def prompt(message: str) -> str:
        bench.pico.hand_turn(bench.pico.under_test, 5.0)
        return ""
    return prompt


def _find_repo_root(start: str | None = None) -> str:
    """Walk up looking for the repo markers, else use the cwd.

    Boring on purpose: `experiments/` and `CLAUDE.md` are the two things this
    tool actually needs, so those are what it looks for.
    """
    current = os.path.abspath(start or os.getcwd())
    while True:
        if (os.path.isdir(os.path.join(current, "experiments"))
                and os.path.exists(os.path.join(current, "CLAUDE.md"))):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            return os.path.abspath(start or os.getcwd())
        current = parent


# --------------------------------------------------------------------------
# Argument parsing
# --------------------------------------------------------------------------

def _common_parser() -> argparse.ArgumentParser:
    """Options every subcommand shares.  Added as a parent, so `--dry-run`
    works after the subcommand name — which is where people type it."""
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--dry-run", action="store_true",
                        help="run the whole path against fakes; touch no hardware "
                             "and write nothing into the repo")
    common.add_argument("--repo", default=None,
                        help="repo root (default: found by walking up from cwd)")
    common.add_argument("--port", default=None,
                        help="serial port (default: /dev/rover-pico, then ttyACM*)")
    common.add_argument("--verbose", "-v", action="store_true",
                        help="echo every line sent to and received from the Pico")
    return common


def _motor_parser() -> argparse.ArgumentParser:
    motor = argparse.ArgumentParser(add_help=False)
    motor.add_argument("--motor", type=int, default=0,
                       help="motor index 0-3 (0=left front, 1=left rear, "
                            "2=right front, 3=right rear)")
    motor.add_argument("--experiment-id", default=None,
                       help="override the experiment id (and so the output "
                            "directory and registry row)")
    motor.add_argument("--max-duty", type=float, default=1.0,
                       help="refuse any |duty| above this (default 1.0)")
    motor.add_argument("--max-drive-s", type=float,
                       default=_LIMIT_DEFAULTS.max_continuous_drive_s,
                       help="refuse to drive continuously for longer (default "
                            f"{_LIMIT_DEFAULTS.max_continuous_drive_s:g} s — "
                            "safety.Limits owns this number, see its docstring "
                            "for why)")
    motor.add_argument("--max-total-drive-s", type=float,
                       default=_LIMIT_DEFAULTS.max_total_drive_s,
                       help="refuse an experiment planning more driving than "
                            f"this (default {_LIMIT_DEFAULTS.max_total_drive_s:g} s)")
    motor.add_argument("--ticks-per-rev", type=float, default=None,
                       help="a MEASURED ticks per output revolution.  Omit it "
                            "and speeds stay in ticks/s, un-converted, which "
                            "is the honest default")
    motor.add_argument("--no-registry", action="store_true",
                       help="do not append a row to experiments/REGISTRY.md")
    return motor


def build_parser() -> argparse.ArgumentParser:
    common = _common_parser()
    motor = _motor_parser()

    parser = argparse.ArgumentParser(
        prog=TOOL_NAME,
        description="Baby Rover motor characterisation bench.",
        epilog="Safety rules that this tool cannot enforce:\n  "
               + "\n  ".join(WIRING_CHECKLIST),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version",
                        version=f"{TOOL_NAME} {__version__}")
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")

    subparsers.add_parser(
        "doctor", parents=[common],
        help="check the bench: groups, sigrok, firmware, udev, USB, ports")

    subparsers.add_parser(
        "scan", parents=[common],
        help="list logic analysers and the sample rates they advertise")

    subparsers.add_parser(
        "ping", parents=[common], help="ask the Pico who it is")

    capture = subparsers.add_parser(
        "capture", parents=[common],
        help="one analyser capture to a .sr file")
    capture.add_argument("--channels", default="D6,D7",
                         help="comma-separated, e.g. D0,D1 (bench probe map: "
                              "docs/WIRING.md §10.2)")
    capture.add_argument("--duration-s", type=float, default=0.2)
    capture.add_argument("--min-rate-hz", type=int, default=200_000,
                         help="the slowest rate that would still resolve the "
                              "signal; the actual rate is chosen from what the "
                              "device advertises")
    capture.add_argument("--out", default=None, help="output .sr path")

    deadband = subparsers.add_parser(
        "deadband", parents=[common, motor],
        help="duty at which the shaft first turns, both directions")
    deadband.add_argument("--repeats", type=int, default=3)
    deadband.add_argument("--duty-step", type=float, default=0.01)
    deadband.add_argument("--dwell-s", type=float, default=0.3)

    sweep = subparsers.add_parser(
        "sweep", parents=[common, motor],
        help="duty -> steady-state speed curve, both directions")
    sweep.add_argument("--duty-step", type=float, default=0.05)
    sweep.add_argument("--settle-s", type=float, default=0.8)
    sweep.add_argument("--window-s", type=float, default=0.5)

    step = subparsers.add_parser(
        "step", parents=[common, motor],
        help="step response, open loop and closed loop")
    step.add_argument("--duty", type=float, default=0.6)
    step.add_argument("--target-ticks-per-s", type=float, default=800.0)
    step.add_argument("--kp", type=float, default=0.0)
    step.add_argument("--ki", type=float, default=0.0)
    step.add_argument("--kd", type=float, default=0.0)
    step.add_argument("--open-loop-only", action="store_true")

    ticks = subparsers.add_parser(
        "ticks-per-rev", parents=[common, motor],
        help="guided measurement of counts per OUTPUT revolution")
    ticks.add_argument("--revolutions", type=float, default=5.0)
    ticks.add_argument("--repeats", type=int, default=3)

    jitter = subparsers.add_parser(
        "jitter", parents=[common, motor],
        help="control-loop period histogram and CPU duty, from GP20/GP21")
    jitter.add_argument("--duration-s", type=float, default=2.0)
    jitter.add_argument("--duty", type=float, default=0.5)
    jitter.add_argument("--min-rate-hz", type=int, default=1_000_000)
    jitter.add_argument("--loop-hz", type=float, default=100.0)

    campaign = subparsers.add_parser(
        "run", parents=[common, motor],
        help="the full per-motor campaign: deadband, sweep, step, jitter")
    campaign.add_argument("--skip", default="",
                          help="comma-separated experiment kinds to skip")
    campaign.add_argument("--measure-ticks-per-rev", action="store_true",
                          help="start with the guided ticks-per-rev procedure "
                               "(needs a human to turn the shaft)")

    report = subparsers.add_parser(
        "report", parents=[common],
        help="assemble saved runs into raw material for the owner's report")
    report.add_argument("--experiment-id", default=None,
                        help="only this experiment (default: all of them)")
    report.add_argument("--out", default=None, help="write the markdown here")
    report.add_argument("--compare", nargs=2, metavar=("A", "B"), default=None,
                        help="diff two manifests and say whether the runs are "
                             "comparable")
    report.add_argument("--plot", action="store_true",
                        help="also draw PNGs (needs matplotlib)")
    return parser


# --------------------------------------------------------------------------
# Handlers
# --------------------------------------------------------------------------

def cmd_doctor(args: argparse.Namespace) -> int:
    world = _make_world(args, need_analyser=not args.dry_run)
    # Under --dry-run the analyser is the fake one, so the "does it answer"
    # checks exercise the same code without spawning anything.
    env = doctor_mod.Environment(analyser=world.analyser or Analyser())
    findings = doctor_mod.diagnose(world.root, env=env)
    world.out(doctor_mod.render(findings))
    if args.dry_run:
        world.out("\n(--dry-run: the analyser answers were faked)")
        return EXIT_OK
    return doctor_mod.exit_code(findings)


def cmd_scan(args: argparse.Namespace) -> int:
    world = _make_world(args, need_analyser=True)
    analyser = world.analyser
    assert analyser is not None
    try:
        devices = analyser.scan()
    except AnalyserError as exc:
        world.out(f"scan failed: {exc}")
        return EXIT_PROBLEM
    if not devices:
        world.out("No logic analyser found.  Run `rover-bench doctor`.")
        return EXIT_PROBLEM
    for device in devices:
        world.out(f"{device['driver']}  conn={device['conn'] or '-'}  "
                  f"{device['description']}  "
                  f"channels: {' '.join(device['channels']) or '?'}")
    try:
        caps = analyser.capabilities()
    except AnalyserError as exc:
        world.out(f"could not read capabilities: {exc}")
        return EXIT_PROBLEM
    world.out(f"\nsample rates advertised ({len(caps.sample_rates_hz)}): "
              + ", ".join(f"{r:,}" for r in caps.sample_rates_hz))
    world.out(f"maximum: {caps.max_sample_rate_hz:,} Hz  "
              "(discovered at runtime — never assumed)")
    world.out("\nprobe map (docs/WIRING.md §10.2 — the characterisation bench; "
              "\n§8 is the failsafe map and puts UART TX/RX on D6/D7 instead):")
    for name, signal in CHANNEL_MAP.items():
        world.out(f"  {name} -> {signal}")
    world.out("  NEVER probe AO1/AO2/BO1/BO2 — motor voltage destroys the analyser.")
    return EXIT_OK


def cmd_ping(args: argparse.Namespace) -> int:
    world = _make_world(args, need_link=True)
    assert world.link is not None
    try:
        identity = world.link.firmware_identity()
    except LinkError as exc:
        world.out(f"no answer from the Pico: {exc}")
        return EXIT_PROBLEM
    finally:
        world.close()
    world.out(f"firmware version : {identity['firmware_version'] or 'unknown'}")
    world.out(f"firmware sha     : {identity['firmware_sha'] or 'unknown'}")
    world.out(f"protocol version : {identity['protocol_version'] or 'unknown'}")
    return EXIT_OK


def cmd_capture(args: argparse.Namespace) -> int:
    world = _make_world(args, need_analyser=True)
    analyser = world.analyser
    assert analyser is not None
    channels = [c.strip() for c in args.channels.split(",") if c.strip()]
    try:
        validate_channels(channels)          # refuses AO1/AO2 before anything runs
    except AnalyserError as exc:
        world.out(f"refused: {exc}")
        return EXIT_USAGE
    out_path = args.out or os.path.join(world.root, "experiments", "bench-verify",
                                        "capture.sr")
    try:
        caps = analyser.capabilities()
        rate_hz = analyser.choose_sample_rate(args.min_rate_hz, caps)
        result = analyser.capture(channels, rate_hz, args.duration_s, out_path)
        decoded = decode_srzip(result.path)
    except AnalyserError as exc:
        world.out(f"capture failed: {exc}")
        return EXIT_PROBLEM
    world.out(f"captured {decoded.n_samples:,} samples at "
              f"{decoded.sample_rate_hz:,} Hz -> {result.path}")
    for name in channels:
        trace = decoded.channels.get(name)
        if trace is not None:
            world.out(f"  {name} ({CHANNEL_MAP[name]}): {len(trace.edges)} edges, "
                      f"starts {'high' if trace.initial_level else 'low'}")
    return EXIT_OK


# -- experiments -----------------------------------------------------------

def _spec_for(kind: str, args: argparse.Namespace) -> ExperimentSpec:
    """Turn argparse flags into an `ExperimentSpec`.

    The one place flags become parameters.  Everything below this line sees
    data, never `args` — which is what keeps the manifest's `params` a faithful
    record of the run rather than a re-derivation of it.
    """
    params: dict[str, Any] = {}
    if kind == "deadband":
        params = {"repeats": args.repeats, "duty_step_frac": args.duty_step,
                  "dwell_s": args.dwell_s}
    elif kind == "sweep":
        params = {"duty_step_frac": args.duty_step, "settle_s": args.settle_s,
                  "window_s": args.window_s}
    elif kind == "step":
        params = {"duty_frac": args.duty,
                  "target_ticks_per_s": args.target_ticks_per_s,
                  "kp": args.kp, "ki": args.ki, "kd": args.kd}
        if args.open_loop_only:
            params["modes"] = ("open",)
    elif kind == "ticks_per_rev":
        params = {"revolutions_rev": args.revolutions, "repeats": args.repeats}
    elif kind == "jitter":
        params = {"duration_s": args.duration_s, "duty_frac": args.duty,
                  "minimum_sample_rate_hz": args.min_rate_hz,
                  "expected_loop_hz": args.loop_hz}
    experiment_id = (getattr(args, "experiment_id", None)
                     or DEFAULT_EXPERIMENT_IDS[kind])
    return ExperimentSpec(experiment_id=experiment_id, motor=args.motor,
                          params=params, kind=kind)


def _dry_run_shrink(spec: ExperimentSpec) -> ExperimentSpec:
    """Make a dry run quick without changing which code runs.

    Only sizes change — fewer repeats, a shorter capture.  The sequence of
    calls is identical, which is the whole value of the dry run.
    """
    params = dict(spec.params)
    if spec.kind == "deadband":
        params["repeats"] = min(params.get("repeats", 3), 2)
    if spec.kind == "jitter":
        params["duration_s"] = min(params.get("duration_s", 2.0), 0.2)
        params["minimum_sample_rate_hz"] = min(
            params.get("minimum_sample_rate_hz", 1_000_000), 200_000)
    return ExperimentSpec(spec.experiment_id, spec.motor, params, spec.kind)


def _ticks_per_rev_for(world: World, args: argparse.Namespace) -> TicksPerRev:
    """Use a measured constant if there is one; otherwise stay in ticks/s.

    Order: an explicit `--ticks-per-rev` (the operator asserting a measurement),
    then a previous `ticks-per-rev` run's manifest, then unmeasured.  There is
    no fourth option, and in particular no datasheet default: the published
    figures disagree by 27% and everything downstream scales with this number.

    A *simulated* previous run only counts when this run is simulated too.  A
    real run inheriting a fake constant is the single most expensive mistake
    available here, because 1200 looks exactly as plausible as 1100 or 1400.
    """
    explicit = getattr(args, "ticks_per_rev", None)
    if explicit:
        return TicksPerRev.from_measurement(explicit, "operator supplied --ticks-per-rev")
    candidate = storage_mod.manifest_path(world.root, "ticks-per-rev",
                                          args.motor)
    if candidate.exists():
        try:
            return experiment_mod.ticks_per_rev_from_manifest(
                manifest_mod.read_manifest(candidate),
                # A dry run may inherit a dry run's constant — that is how the
                # units gate is demonstrated before anyone is at the bench.  A
                # real run may not: see ticks_per_rev_from_manifest.
                allow_simulated=world.dry_run)
        except (manifest_mod.ManifestError, ValueError):
            pass
    return TicksPerRev.unmeasured()


def _run_one(world: World, spec: ExperimentSpec,
             args: argparse.Namespace) -> tuple[int, Any]:
    """Run one experiment, print the headline, write the registry row."""
    if world.dry_run:
        spec = _dry_run_shrink(spec)
    needs_analyser = spec.kind in experiment_mod.NEEDS_ANALYSER
    result = experiment_mod.run(
        spec,
        link=world.link,
        root=world.root,
        analyser=world.analyser if needs_analyser else None,
        clock=world.clock,
        limits=world.limits,
        ticks_per_rev_value=_ticks_per_rev_for(world, args),
        log=world.log,
        prompt=world.prompt,
        argv=list(sys.argv),
        simulated=world.dry_run,
        git=manifest_mod.GitState() if world.dry_run else None,
    )
    headline = (result.manifest.get("summary", {}) or {}).get("headline", "")
    world.out(f"\n{spec.kind}  motor {spec.motor}")
    world.out(f"  {headline}")
    world.out(f"  manifest : {result.manifest_path}")
    if result.data_path.exists():
        world.out(f"  data     : {result.data_path}")
    if not manifest_mod.units_are_converted(result.manifest):
        world.out("  UNITS    : ticks/s, NOT converted to rad/s "
                  "(ticks_per_rev is unmeasured — run `rover-bench ticks-per-rev`)")
    if not result.ok:
        world.out(f"  FAILED   : {result.error}")
        for check in result.checks:
            if not check.ok and not check.skipped:
                world.out(f"    [{check.status}] {check.name}: {check.detail}")
                if check.fix:
                    world.out(f"           fix: {check.fix}")
        return EXIT_PROBLEM, result
    if not getattr(args, "no_registry", False):
        _write_registry_row(world, spec, result)
    return EXIT_OK, result


def _write_registry_row(world: World, spec: ExperimentSpec, result: Any) -> None:
    """A result not persisted did not happen — so the row goes in now."""
    experiment_result = result.result
    data_rel = os.path.relpath(str(result.data_path.parent), world.root)
    row = registry_mod.RegistryRow.today(
        id=spec.experiment_id,
        story=STORY_IDS.get(spec.kind, "1.5"),
        question=(experiment_result.question if experiment_result
                  else f"{spec.kind} on motor {spec.motor}"),
        method=(experiment_result.method if experiment_result else str(spec.params)),
        result=(result.manifest.get("summary", {}) or {}).get("headline", "—"),
        data=f"[{data_rel}/]({data_rel}/)",
    ).as_dict()
    try:
        written = registry_mod.append_row(world.registry_path, row)
    except (registry_mod.RegistryError, FileNotFoundError) as exc:
        world.out(f"  registry : NOT written — {exc}")
        return
    world.out(f"  registry : {'row added' if written else 'row already present'} "
              f"in {world.registry_path}")


def _experiment_command(kind: str) -> Callable[[argparse.Namespace], int]:
    def handler(args: argparse.Namespace) -> int:
        needs_analyser = kind in experiment_mod.NEEDS_ANALYSER
        world = _make_world(args, need_link=True, need_analyser=needs_analyser)
        try:
            code, _result = _run_one(world, _spec_for(kind, args), args)
        except SafetyViolation as exc:
            world.out(str(exc))
            return EXIT_USAGE
        finally:
            world.close()
        return code
    return handler


def cmd_run(args: argparse.Namespace) -> int:
    """The full per-motor campaign.

    Order matters: `ticks-per-rev` first when asked for, because every speed
    measured after it can then be reported in rad/s instead of ticks/s.  Then
    deadband (cheap, and it bounds the sweep), then the sweep, then the step
    response, then the loop jitter.
    """
    skip = {s.strip() for s in args.skip.split(",") if s.strip()}
    kinds: list[str] = []
    if args.measure_ticks_per_rev:
        kinds.append("ticks_per_rev")
    kinds += ["deadband", "sweep", "step", "jitter"]
    kinds = [k for k in kinds if k not in skip]

    world = _make_world(args, need_link=True, need_analyser=True)
    if getattr(args, "experiment_id", None):
        world.out("note: --experiment-id is ignored for a campaign; each "
                  "experiment writes under its own id so their manifests do "
                  "not overwrite each other")
    world.out(f"campaign: motor {args.motor} — {', '.join(kinds)}")
    worst = EXIT_OK
    try:
        for kind in kinds:
            spec = _spec_for(kind, _fill_defaults(args, kind))
            try:
                code, _result = _run_one(world, spec, args)
            except SafetyViolation as exc:
                world.out(f"\n{kind}: refused\n{exc}")
                code = EXIT_USAGE
            worst = max(worst, code)
    finally:
        world.close()
    world.out("\ncampaign finished.")
    return worst


def _fill_defaults(args: argparse.Namespace, kind: str) -> argparse.Namespace:
    """The campaign parser has no per-experiment flags, so supply their defaults.

    Written out rather than looked up, so the numbers a campaign uses are
    visible in one place — they end up in four manifests and someone will ask
    where they came from.
    """
    filled = argparse.Namespace(**vars(args))
    defaults = {
        "repeats": 3, "duty_step": 0.05, "dwell_s": 0.3,
        "settle_s": 0.8, "window_s": 0.5,
        "duty": 0.6, "target_ticks_per_s": 800.0,
        "kp": 0.0, "ki": 0.0, "kd": 0.0, "open_loop_only": False,
        "revolutions": 5.0,
        "duration_s": 2.0, "min_rate_hz": 1_000_000, "loop_hz": 100.0,
    }
    if kind == "deadband":
        defaults["duty_step"] = 0.01
    for key, value in defaults.items():
        if not hasattr(filled, key) or getattr(filled, key) is None:
            setattr(filled, key, value)
    setattr(filled, "experiment_id", None)   # each kind uses its own default id
    return filled


# -- report ----------------------------------------------------------------

def cmd_report(args: argparse.Namespace) -> int:
    """Assemble what exists into raw material.  It does **not** write prose.

    CLAUDE.md is explicit: the owner writes the report.  This produces the
    tables, the numbers and (optionally) the plots he writes *from*, and says
    so at the top of the file so nobody mistakes it for a draft.
    """
    world = _make_world(args)
    if args.compare:
        return _compare_manifests(world, args.compare[0], args.compare[1])

    lines: list[str] = [
        "# Bench data — raw material",
        "",
        "Generated by `rover-bench report`.  **This is not the report.**  The",
        "prose is the owner's to write (CLAUDE.md, hard rule); this file is the",
        "numbers to write it from.",
        "",
        f"Generated {manifest_mod.utc_now_iso()} from `{world.root}`.",
        "",
    ]
    experiments_dir = Path(world.root) / "experiments"
    ids = ([args.experiment_id] if args.experiment_id
           else sorted(p.name for p in experiments_dir.iterdir()
                       if p.is_dir() and p.name not in ("plots", "reports"))
           if experiments_dir.is_dir() else [])

    store = storage_mod.Storage(world.root)
    found_any = False
    for experiment_id in ids:
        try:
            motors = store.list_motors(experiment_id)
        except storage_mod.StorageError:
            continue
        if not motors:
            continue
        found_any = True
        lines += [f"## {experiment_id}", "",
                  "| motor | when | firmware | git | ticks/rev | result |",
                  "|---|---|---|---|---|---|"]
        for motor in motors:
            path = storage_mod.manifest_path(world.root, experiment_id, motor)
            if not path.exists():
                continue
            try:
                payload = manifest_mod.read_manifest(path)
            except manifest_mod.ManifestError as exc:
                lines.append(f"| {motor} | unreadable | | | | {exc} |")
                continue
            sha = payload.get("git_sha") or "?"
            lines.append(
                f"| {motor} | {payload.get('timestamp_utc', '?')} "
                f"| {payload.get('firmware_version') or '?'} "
                f"| {sha[:8]} "
                f"| {payload.get('ticks_per_rev') if payload.get('ticks_per_rev') else '**unmeasured**'} "
                f"| {(payload.get('summary') or {}).get('headline', '')} |"
            )
        lines.append("")
        lines += _comparability_section(world.root, experiment_id, motors)
        if args.plot:
            lines += _plot_section(world, experiment_id, motors)

    if not found_any:
        lines += ["_No runs found yet._  Take some data:",
                  "", "```", "tools/rover-bench run --motor 0", "```", ""]

    text = "\n".join(lines) + "\n"
    world.out(text)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding="utf-8")
        world.out(f"written to {args.out}")
    return EXIT_OK


def _comparability_section(root: str, experiment_id: str,
                           motors: Sequence[int]) -> list[str]:
    """Say, per pair, whether the runs may be put on the same axes.

    This is the claim Story 1.5 rests on, so it is stated explicitly rather
    than assumed by anyone who sees four lines on one plot.
    """
    if len(motors) < 2:
        return []
    lines = ["**Comparability** (same conditions apart from the motor):", ""]
    first = motors[0]
    try:
        base = manifest_mod.read_manifest(
            storage_mod.manifest_path(root, experiment_id, first))
    except (FileNotFoundError, manifest_mod.ManifestError):
        return []
    for motor in motors[1:]:
        try:
            other = manifest_mod.read_manifest(
                storage_mod.manifest_path(root, experiment_id, motor))
        except (FileNotFoundError, manifest_mod.ManifestError):
            continue
        report = manifest_mod.verify_reproducible(base, other)
        verdict = "comparable" if report.comparable else (
            "NOT comparable — " + "; ".join(str(d) for d in report.critical))
        lines.append(f"* motor {first} vs motor {motor}: {verdict}")
    lines.append("")
    return lines


def _plot_section(world: World, experiment_id: str,
                  motors: Sequence[int]) -> list[str]:
    """Overlay every motor's CSV on one figure.  matplotlib is imported here.

    Lazily, because `doctor` must run on a machine with nothing installed, and
    a top-level matplotlib import would break exactly that case.
    """
    try:
        import matplotlib  # noqa: PLC0415 - lazy on purpose
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt  # noqa: PLC0415
    except ImportError:
        return ["_Plots skipped: matplotlib is not installed "
                "(`pip install matplotlib`)._", ""]

    figure, axes = plt.subplots(figsize=(7, 4.5))
    plotted = 0
    for motor in motors:
        csv_file = storage_mod.csv_path(world.root, experiment_id, motor)
        if not csv_file.exists():
            continue
        columns, rows = storage_mod.read_csv(csv_file)
        x_name = "duty_frac" if "duty_frac" in columns else columns[0]
        y_name = next((c for c in ("speed_ticks_per_s", "omega_rad_s", "period_ms")
                       if c in columns), None)
        if y_name is None:
            continue
        points = [(float(r[x_name]), float(r[y_name])) for r in rows
                  if r.get(x_name) not in (None, "") and r.get(y_name) not in (None, "")]
        if not points:
            continue
        points.sort()
        axes.plot([p[0] for p in points], [p[1] for p in points],
                  marker="o", markersize=3, label=f"motor {motor}")
        plotted += 1
        axes.set_xlabel(x_name)
        axes.set_ylabel(y_name)
    if not plotted:
        plt.close(figure)
        return ["_Plots skipped: no plottable CSV columns found._", ""]
    axes.set_title(f"{experiment_id} — all motors")
    axes.grid(True, alpha=0.3)
    axes.legend()
    out_path = Path(world.root) / "experiments" / "plots" / f"{experiment_id}.png"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    figure.tight_layout()
    figure.savefig(out_path, dpi=140)
    plt.close(figure)
    return [f"![{experiment_id}](../{out_path.relative_to(Path(world.root))})", ""]


def _compare_manifests(world: World, left: str, right: str) -> int:
    """`--compare A B` — the reproducibility check, on the command line."""
    try:
        a = manifest_mod.read_manifest(left)
        b = manifest_mod.read_manifest(right)
    except (FileNotFoundError, manifest_mod.ManifestError) as exc:
        world.out(f"cannot compare: {exc}")
        return EXIT_USAGE
    report = manifest_mod.verify_reproducible(a, b)
    world.out(report.text())
    return EXIT_OK if report.comparable else EXIT_PROBLEM


# --------------------------------------------------------------------------
# Dispatch
# --------------------------------------------------------------------------

HANDLERS: dict[str, Callable[[argparse.Namespace], int]] = {
    "doctor": cmd_doctor,
    "scan": cmd_scan,
    "ping": cmd_ping,
    "capture": cmd_capture,
    "deadband": _experiment_command("deadband"),
    "sweep": _experiment_command("sweep"),
    "step": _experiment_command("step"),
    "ticks-per-rev": _experiment_command("ticks_per_rev"),
    "jitter": _experiment_command("jitter"),
    "run": cmd_run,
    "report": cmd_report,
}


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point.  Always returns an int; never lets a traceback escape.

    A traceback at a bench with a motor wired up tells the operator nothing
    they can act on.  Every expected failure is caught and printed with its
    fix; anything genuinely unexpected still propagates, because a silent
    `except Exception` is how a real bug hides for a fortnight.
    """
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    if not getattr(args, "command", None):
        parser.print_help()
        return EXIT_USAGE
    handler = HANDLERS.get(args.command)
    if handler is None:  # pragma: no cover - argparse rejects unknown commands
        parser.print_help()
        return EXIT_USAGE
    try:
        return int(handler(args))
    except SafetyViolation as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_USAGE
    except (LinkError, AnalyserError, storage_mod.StorageError,
            registry_mod.RegistryError, manifest_mod.ManifestError) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_PROBLEM
    except KeyboardInterrupt:  # pragma: no cover - interactive only
        print("\ninterrupted — motors were stopped on the way out",
              file=sys.stderr)
        return EXIT_PROBLEM


if __name__ == "__main__":  # pragma: no cover - module entry point
    raise SystemExit(main())
