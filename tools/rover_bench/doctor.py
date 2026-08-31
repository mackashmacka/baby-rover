"""doctor.py — "why doesn't my bench work?", answered in one screen.

THE DESIGN CONSTRAINT THAT SHAPES EVERYTHING HERE
--------------------------------------------------
**It must run and give useful output when nothing is installed.**  That is its
main use right now: the laptop may have no sigrok-cli, no pyserial, no udev
rule, and the user may not be in `plugdev` yet.  A doctor that needs the thing
it is diagnosing is not a doctor.

So every probe is defensive: `shutil.which` rather than running the binary,
reading `/sys` rather than shelling out to `lsusb`, `try: import` rather than a
top-level import.  Nothing here may raise; a probe that cannot answer reports
"unknown" with a fix.

EVERY FAILURE CARRIES ITS FIX
-----------------------------
A red line with no next action is just anxiety.  Each check prints the exact
command to run.  Most of them are one line of `tools/bench-setup.sh`, which is
the authority on how this machine is set up — doctor only *checks*, it never
changes anything.

WHAT IT CHECKS, AND WHY EACH ONE IS ON THE LIST
------------------------------------------------
  python           — 3.12 is what the venv is built on
  pyserial         — no serial link without it
  groups           — `dialout` for /dev/ttyACM*, `plugdev` for the FX2.  This
                     is the classic "it works as root" failure, and group
                     membership needs a re-login to take effect
  sigrok-cli       — every capture goes through it
  fx2lafw firmware — the FX2 has no firmware of its own; sigrok uploads it on
                     every plug.  Without the firmware package the device
                     enumerates and then does nothing
  udev rule        — what replaces the Windows Zadig step; without it the
                     device is root-only
  analyser on USB  — 0925:3881 before firmware upload, 0925:3882 after
  analyser answers — enumerating is not the same as working
  pico symlink     — /dev/rover-pico, the stable name
  pico on USB      — 2e8a:*; BOOTSEL mode is a *different* product id and looks
                     like a disk, not a serial port
  experiments dir  — a run that cannot write its data is a run that did not
                     happen
"""

from __future__ import annotations

import glob
import os
import platform
import shutil
import sys
from dataclasses import dataclass
from typing import Callable, Sequence

from .analyser import Analyser, AnalyserError, DRIVER, SIGROK_BIN
from .link import DEFAULT_PORT_SYMLINK, FALLBACK_PORT_GLOB

#: VID:PID of the Saleae-clone, before and after fx2lafw is uploaded to it.
FX2_VID = "0925"
FX2_PIDS = ("3881", "3882")

#: Raspberry Pi vendor id.  The Pico enumerates under this in every mode.
PICO_VID = "2e8a"

FIRMWARE_GLOBS = (
    "/usr/share/sigrok-firmware/fx2lafw-*.fx2",
    "/usr/local/share/sigrok-firmware/fx2lafw-*.fx2",
)

UDEV_RULE_GLOBS = (
    "/etc/udev/rules.d/60-rover-instruments.rules",
    "/etc/udev/rules.d/60-libsigrok.rules",
    "/lib/udev/rules.d/60-libsigrok.rules",
)

REQUIRED_GROUPS = ("dialout", "plugdev")

SETUP_HINT = "sudo bash tools/bench-setup.sh"

OK, WARN, FAIL, INFO = "ok", "warn", "fail", "info"


@dataclass(frozen=True)
class Finding:
    """One diagnostic line.

    `fix` is not optional in spirit: a check whose failure the operator cannot
    act on should not be on the list.
    """

    name: str
    status: str
    detail: str = ""
    fix: str = ""

    @property
    def failed(self) -> bool:
        return self.status == FAIL

    def render(self, width: int = 22) -> str:
        mark = {OK: "[ ok ]", WARN: "[warn]", FAIL: "[FAIL]", INFO: "[info]"}[self.status]
        line = f"{mark} {self.name.ljust(width)} {self.detail}".rstrip()
        if self.fix and self.status in (WARN, FAIL):
            line += f"\n       {'':{width}} fix: {self.fix}"
        return line


def _read_text(path: str) -> str:
    """Read a small file.  Used on /sys entries, so it must close its handle:
    doctor reads one per USB device and leaking them would be silly."""
    with open(path, encoding="utf-8", errors="replace") as handle:
        return handle.read()


@dataclass
class Environment:
    """Everything doctor touches, injected so it can be tested off this laptop."""

    which: Callable[[str], str | None] = shutil.which
    exists: Callable[[str], bool] = os.path.exists
    globber: Callable[[str], list[str]] = glob.glob
    read_text: Callable[[str], str] = staticmethod(_read_text)
    groups: Callable[[], list[str]] | None = None
    analyser: Analyser | None = None
    can_write: Callable[[str], bool] = staticmethod(
        lambda path: os.access(path, os.W_OK)
    )


# --------------------------------------------------------------------------
# Probes
# --------------------------------------------------------------------------

def _current_groups(env: Environment) -> list[str]:
    """The user's groups, without shelling out to `id`."""
    if env.groups is not None:
        return list(env.groups())
    try:
        import grp  # noqa: PLC0415 - unix only, and only needed here
        names = {grp.getgrgid(gid).gr_name for gid in os.getgroups()}
        names.add(grp.getgrgid(os.getgid()).gr_name)
        return sorted(names)
    except Exception:  # noqa: BLE001 - non-unix, or a gid with no group entry
        return []


def check_python() -> Finding:
    major, minor = sys.version_info[:2]
    detail = f"{platform.python_version()} ({sys.executable})"
    if (major, minor) < (3, 12):
        return Finding("python", WARN, detail,
                       "this tool targets Python 3.12; older may still work")
    return Finding("python", OK, detail)


def check_module(name: str, *, why: str, fix: str,
                 required: bool = True) -> Finding:
    """Is an import available?  Never imports it for real side effects."""
    try:
        __import__(name)
    except Exception as exc:  # noqa: BLE001 - a broken install is also "missing"
        return Finding(f"python: {name}", FAIL if required else WARN,
                       f"not importable ({exc.__class__.__name__}) — {why}", fix)
    return Finding(f"python: {name}", OK, "installed")


def check_groups(env: Environment) -> list[Finding]:
    """`dialout` and `plugdev` — the classic "works as root" failure.

    Reported per group so the fix names the one that is missing, and with the
    re-login caveat, because `usermod -aG` does not affect the shell you typed
    it in.
    """
    have = _current_groups(env)
    findings: list[Finding] = []
    if not have:
        return [Finding("groups", WARN, "could not read group membership",
                        f"check with: id -nG; then {SETUP_HINT}")]
    for group in REQUIRED_GROUPS:
        if group in have:
            findings.append(Finding(f"group: {group}", OK, "member"))
        else:
            findings.append(Finding(
                f"group: {group}", FAIL, "not a member",
                f"sudo usermod -aG {group} $USER   then log out and back in "
                f"(or: newgrp {group})",
            ))
    return findings


def check_sigrok(env: Environment) -> Finding:
    path = env.which(SIGROK_BIN)
    if not path:
        return Finding("sigrok-cli", FAIL, "not on PATH",
                       f"sudo apt install sigrok-cli   (or {SETUP_HINT})")
    return Finding("sigrok-cli", OK, path)


def check_fx2_firmware(env: Environment) -> Finding:
    """The FX2 has no firmware of its own — sigrok uploads it on every plug.

    Without the firmware package the device enumerates as 0925:3881 and then
    never becomes a working analyser, which looks exactly like a broken cable.
    """
    for pattern in FIRMWARE_GLOBS:
        found = env.globber(pattern)
        if found:
            return Finding("fx2lafw firmware", OK,
                           f"{len(found)} file(s) in {os.path.dirname(pattern)}")
    return Finding(
        "fx2lafw firmware", FAIL, "no fx2lafw-*.fx2 found",
        f"sudo apt install sigrok-firmware-fx2lafw   (or {SETUP_HINT})",
    )


def _usb_devices(env: Environment) -> list[tuple[str, str]]:
    """(vid, pid) for everything on the USB bus, read straight from /sys.

    Reading sysfs rather than running `lsusb` keeps doctor working on a machine
    with no usbutils installed — which is exactly the machine that needs it.
    """
    devices: list[tuple[str, str]] = []
    for vid_path in env.globber("/sys/bus/usb/devices/*/idVendor"):
        try:
            vid = env.read_text(vid_path).strip().lower()
            pid = env.read_text(vid_path.replace("idVendor", "idProduct")).strip().lower()
        except OSError:
            continue
        devices.append((vid, pid))
    return devices


def check_analyser_usb(env: Environment) -> Finding:
    devices = _usb_devices(env)
    if not devices:
        return Finding("analyser on USB", WARN, "could not read /sys/bus/usb",
                       "check with: lsusb | grep 0925")
    matches = [f"{v}:{p}" for v, p in devices if v == FX2_VID and p in FX2_PIDS]
    if matches:
        after = "3882" in matches[0]
        return Finding("analyser on USB", OK,
                       f"{matches[0]}" + (" (firmware loaded)" if after else ""))
    return Finding("analyser on USB", FAIL, f"no {FX2_VID}:{'/'.join(FX2_PIDS)}",
                   "plug the analyser in; try another cable — some are power-only")


def check_pico_usb(env: Environment) -> Finding:
    devices = _usb_devices(env)
    if not devices:
        return Finding("pico on USB", WARN, "could not read /sys/bus/usb",
                       "check with: lsusb | grep 2e8a")
    matches = [f"{v}:{p}" for v, p in devices if v == PICO_VID]
    if matches:
        return Finding("pico on USB", OK, matches[0])
    return Finding(
        "pico on USB", FAIL, f"no {PICO_VID}:* device",
        "plug the Pico in.  If it is in BOOTSEL it appears as a disk, not a "
        "serial port — replug without holding the button",
    )


def check_udev_rules(env: Environment) -> Finding:
    """On Linux the udev rule replaces the Windows Zadig step entirely."""
    present = [path for path in UDEV_RULE_GLOBS if env.exists(path)]
    if present:
        return Finding("udev rules", OK, present[0])
    return Finding(
        "udev rules", WARN, "no rover/libsigrok rule found",
        f"{SETUP_HINT}   then: sudo udevadm control --reload-rules && "
        "sudo udevadm trigger, and replug",
    )


def check_pico_port(env: Environment) -> Finding:
    """`/dev/rover-pico` is the stable name; `/dev/ttyACM0` is not.

    A bench that renumbers its instruments between runs quietly ruins a data
    set, which is why the symlink is worth a check of its own.
    """
    if env.exists(DEFAULT_PORT_SYMLINK):
        return Finding("pico port", OK, DEFAULT_PORT_SYMLINK)
    fallback = sorted(env.globber(FALLBACK_PORT_GLOB))
    if fallback:
        return Finding(
            "pico port", WARN,
            f"no {DEFAULT_PORT_SYMLINK}; falling back to {fallback[0]}",
            f"{SETUP_HINT} installs the udev SYMLINK rule that makes the name stable",
        )
    return Finding("pico port", FAIL,
                   f"neither {DEFAULT_PORT_SYMLINK} nor {FALLBACK_PORT_GLOB}",
                   "plug the Pico in and check it is running firmware, not BOOTSEL")


def check_analyser_answers(env: Environment) -> Finding:
    """Enumerating is not the same as working.

    memory/logic-analyser.md is blunt about this: an analyser you have never
    successfully triggered is not a working instrument, and you find that out
    at the worst possible moment.
    """
    analyser = env.analyser
    if analyser is None:
        return Finding("analyser answers", INFO, "not probed")
    if not analyser.available():
        return Finding("analyser answers", FAIL, "sigrok-cli missing",
                       f"sudo apt install sigrok-cli   (or {SETUP_HINT})")
    try:
        devices = analyser.scan()
    except AnalyserError as exc:
        return Finding("analyser answers", FAIL, str(exc).splitlines()[0],
                       "check the udev rule and the plugdev group, then replug")
    matching = [d for d in devices if d.get("driver") == DRIVER]
    if not matching:
        return Finding("analyser answers", FAIL, "scan found no fx2lafw device",
                       "replug; check the plugdev group and the udev rule")
    try:
        caps = analyser.capabilities()
        rates = caps.sample_rates_hz
        detail = (f"{matching[0].get('description', DRIVER)}, "
                  f"{len(caps.channels)} channels, "
                  f"{len(rates)} sample rates up to {caps.max_sample_rate_hz} Hz")
    except AnalyserError as exc:
        return Finding("analyser answers", WARN,
                       f"device found but --show failed: {exc}",
                       "the capture rate must be discovered, never assumed")
    return Finding("analyser answers", OK, detail)


def check_experiments_dir(env: Environment, repo_root: str) -> Finding:
    """A run that cannot write its data is a run that did not happen."""
    path = os.path.join(repo_root, "experiments")
    if not env.exists(path):
        return Finding("experiments dir", WARN, f"{path} does not exist",
                       f"mkdir -p {path}")
    if not env.can_write(path):
        return Finding("experiments dir", FAIL, f"{path} is not writable",
                       "check ownership; do not run the bench as root")
    return Finding("experiments dir", OK, path)


# --------------------------------------------------------------------------
# The whole examination
# --------------------------------------------------------------------------

def diagnose(repo_root: str = ".", *, env: Environment | None = None,
             probe_analyser: bool = True) -> list[Finding]:
    """Run every check and return the findings, in the order they matter.

    Never raises: the machine being diagnosed is by definition the broken one.
    """
    env = env or Environment()
    findings: list[Finding] = [check_python()]
    findings.append(check_module(
        "serial", why="the Pico link needs it",
        fix="pip install pyserial   (or " + SETUP_HINT + ")"))
    findings.append(check_module(
        "matplotlib", why="only needed for `rover-bench report` plots",
        fix="pip install matplotlib", required=False))
    findings.extend(check_groups(env))
    findings.append(check_sigrok(env))
    findings.append(check_fx2_firmware(env))
    findings.append(check_udev_rules(env))
    findings.append(check_analyser_usb(env))
    if probe_analyser:
        findings.append(check_analyser_answers(env))
    findings.append(check_pico_usb(env))
    findings.append(check_pico_port(env))
    findings.append(check_experiments_dir(env, repo_root))
    return findings


def render(findings: Sequence[Finding]) -> str:
    """The report, plus a one-line verdict and the wiring rules."""
    from .safety import WIRING_CHECKLIST

    width = max((len(f.name) for f in findings), default=10) + 1
    lines = ["rover-bench doctor", "=" * 60]
    lines.extend(f.render(width) for f in findings)
    failures = [f for f in findings if f.failed]
    warnings = [f for f in findings if f.status == WARN]
    lines.append("=" * 60)
    if failures:
        lines.append(f"{len(failures)} blocking problem(s): "
                     + ", ".join(f.name for f in failures))
        lines.append(f"Most of these are fixed by:  {SETUP_HINT}")
    elif warnings:
        lines.append(f"No blockers.  {len(warnings)} thing(s) worth a look.")
    else:
        lines.append("Bench looks ready.")
    lines.append("")
    lines.append("Before touching hardware — these are not style preferences:")
    lines.extend(f"  * {rule}" for rule in WIRING_CHECKLIST)
    return "\n".join(lines)


def exit_code(findings: Sequence[Finding]) -> int:
    """Non-zero when something blocks the bench, so scripts can gate on it."""
    return 1 if any(f.failed for f in findings) else 0
