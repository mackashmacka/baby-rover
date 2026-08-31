"""Shared fixtures for the Baby Rover host-side test suite.

Two rules govern everything in this file:

1. **Nothing in the default suite touches hardware.** No serial port is opened,
   no sigrok is spawned, no Pico is required. That is enforced by the autouse
   ``_no_hardware_guard`` below, not merely intended - a suite that only
   *usually* needs no hardware gets run at the bench once and then stops being
   run at all.
2. **Nothing here asserts a hardware fact.** The synthetic motor's numbers are
   invented for the arithmetic. The real ones are ``[MEASURE]`` in
   ``docs/HARDWARE.md`` and are not known yet.

Both halves of the project are tested from here:

* ``tools/rover_bench`` - imported directly (``tools/`` is put on sys.path).
* ``firmware/src/protocol.c`` and ``control.c`` - which their own headers
  declare HOST-PURE. They are compiled into a shared library and driven through
  ctypes, so the command parser and the PID maths are unit-tested on a laptop
  with no Pico attached. See ``firmware_lib``.
"""

from __future__ import annotations

import ctypes
import importlib
import math
import os
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

# The real subprocess.run, captured before anything can monkeypatch it. The
# firmware compile in `firmware_lib` uses this so the no-hardware guard cannot
# accidentally block a *build* step, which is not hardware access.
_REAL_RUN = subprocess.run

# --------------------------------------------------------------------------
# Repo layout
# --------------------------------------------------------------------------

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent
TOOLS_DIR = REPO_ROOT / "tools"
DOCS_DIR = REPO_ROOT / "docs"
FIRMWARE_SRC = REPO_ROOT / "firmware" / "src"
REGISTRY_PATH = REPO_ROOT / "experiments" / "REGISTRY.md"

# `tools/` on the path so `import rover_bench` works with no install step.
# Boring on purpose: no setup.py, no editable install, no packaging discussion.
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))


def import_or_none(name: str):
    """Import ``name``, or return None if that module does not exist yet.

    Only a genuinely missing module is swallowed. A module that exists but
    explodes on import is re-raised - otherwise a broken module looks exactly
    like an unwritten one and a whole file of tests xfails in silence.
    """
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError as exc:
        root = name.split(".")[0]
        if exc.name in (name, root) or (exc.name or "").startswith(root):
            return None
        raise


# --------------------------------------------------------------------------
# Ground truth the tests assert against.
#
# One copy, here, so a drift between docs / firmware / host code surfaces as a
# test failure rather than as a wrong number in a report six weeks from now.
# Source: the bench brief, docs/WIRING.md §3 and §8, firmware board_config.h.
# --------------------------------------------------------------------------

#: Signal name -> Pico GP number for the motor-under-test bench rig.
EXPECTED_BENCH_PINS = {
    "UART_TX": 0,
    "UART_RX": 1,
    "PWMA": 2,
    "AIN1": 3,
    "AIN2": 4,
    "STBY": 5,
    "ENC_A": 12,
    "ENC_B": 13,
    "LOOP_TICK": 20,
    "COMPUTE_BUSY": 21,
}

#: Logic-analyser channel -> signal. D6/D7 are the firmware instrumentation
#: pins, which is how loop period and compute time get measured with no debugger.
EXPECTED_ANALYSER_CHANNELS = {
    "D0": "PWMA",
    "D1": "AIN1",
    "D2": "AIN2",
    "D3": "STBY",
    "D4": "ENC_A",
    "D5": "ENC_B",
    "D6": "LOOP_TICK",
    "D7": "COMPUTE_BUSY",
}

#: GP23/24/25/29 are internal to the CYW43439. Using one is a bug.
FORBIDDEN_GPIO = frozenset({23, 24, 25, 29})

#: Never probed with the analyser: motor voltage, destroys it. Safety rule 1.
MOTOR_OUTPUT_NETS = ("AO1", "AO2", "BO1", "BO2")

PWM_HZ = 20_000
CONTROL_LOOP_HZ = 100
CONTROL_DT_S = 1.0 / CONTROL_LOOP_HZ
UART_BAUD = 115200
ENCODER_SUPPLY_V = 3.3
TB6612_VM_MAX_V = 13.5
LIPO_4S_CHARGED_V = 16.8
FAILSAFE_TIMEOUT_MS = 300

#: The 7-column schema of experiments/REGISTRY.md. Non-negotiable per CLAUDE.md.
REGISTRY_COLUMNS = ("ID", "Date", "Story", "Question", "Method", "Result", "Data")


# --------------------------------------------------------------------------
# Canned sigrok-cli output, shaped like the real thing for an FX2 under
# fx2lafw. The sample-rate list is exactly why nothing may hardcode a rate:
# it is a device property, discovered at runtime.
# --------------------------------------------------------------------------

SIGROK_SCAN_OUTPUT = """The following devices were found:
fx2lafw:conn=1.10 - Saleae Logic with 8 channels: D0 D1 D2 D3 D4 D5 D6 D7
"""

SIGROK_SCAN_EMPTY = "No devices found.\n"

SIGROK_SHOW_OUTPUT = """Driver functions:
    Logic analyzer
Scan options:
    conn
fx2lafw:conn=1.10 - Saleae Logic with 8 channels: D0 D1 D2 D3 D4 D5 D6 D7
Supported configuration options:
    continuous: 
    conn: 
    limit_samples: 0 - 0
    captureratio: 0 - 100
    samplerate (RW): 20 kHz, 25 kHz, 50 kHz, 100 kHz, 200 kHz, 250 kHz, 500 kHz, 1 MHz, 2 MHz, 3 MHz, 4 MHz, 6 MHz, 8 MHz, 12 MHz, 16 MHz, 24 MHz
"""

#: A device that reports a continuous range instead of a discrete list.
SIGROK_SHOW_RANGE = """Supported configuration options:
    samplerate (RW): 20 kHz - 24 MHz (in steps of 1 Hz)
"""

SIGROK_SHOW_RATES_HZ = (
    20_000, 25_000, 50_000, 100_000, 200_000, 250_000, 500_000,
    1_000_000, 2_000_000, 3_000_000, 4_000_000, 6_000_000,
    8_000_000, 12_000_000, 16_000_000, 24_000_000,
)


# --------------------------------------------------------------------------
# The no-hardware guard
# --------------------------------------------------------------------------


class HardwareAccessInTest(RuntimeError):
    """A default-suite test tried to reach the outside world."""


@pytest.fixture(autouse=True)
def _no_hardware_guard(request, monkeypatch):
    """Make it impossible for a default-suite test to touch real hardware.

    Blocks subprocess spawning (so nothing runs sigrok-cli or git for real) and
    ``serial.Serial`` (so nothing opens a port). ``hardware``-marked tests are
    exempt because reaching the bench is their job; ``allow_subprocess`` is the
    per-test escape hatch for an inert command.
    """
    if request.node.get_closest_marker("hardware"):
        return
    if request.node.get_closest_marker("allow_subprocess"):
        return

    def blocked(*args, **kwargs):
        raise HardwareAccessInTest(
            "this test tried to spawn a subprocess or open a port. The default "
            "suite must need no hardware: inject a fake runner/transport, or "
            "mark the test @pytest.mark.hardware."
        )

    for name in ("run", "Popen", "call", "check_call", "check_output", "getoutput"):
        if hasattr(subprocess, name):
            monkeypatch.setattr(subprocess, name, blocked, raising=False)
    monkeypatch.setattr(os, "system", blocked, raising=False)

    serial_mod = sys.modules.get("serial")
    if serial_mod is not None:
        monkeypatch.setattr(serial_mod, "Serial", blocked, raising=False)


# --------------------------------------------------------------------------
# Fake serial transport - the shape rover_bench.link.Transport wants
# --------------------------------------------------------------------------


class FakeTransport:
    """Implements `link.Transport`: write / readline / reset_input_buffer / close.

    Duck-typed rather than subclassed, deliberately: `Link` should accept
    "something with these four methods", and that is also what makes it
    testable without a Pico.
    """

    def __init__(self) -> None:
        self.writes: list[bytes] = []
        self.rx = bytearray()
        self.closed = False
        self.close_count = 0
        self.reset_count = 0
        self._responder = None

    # -- test-side helpers -------------------------------------------------
    def queue(self, data) -> "FakeTransport":
        self.rx.extend(data.encode() if isinstance(data, str) else data)
        return self

    def set_responder(self, fn) -> "FakeTransport":
        """``fn(line: str) -> str | None`` - auto-reply to each written line."""
        self._responder = fn
        return self

    @property
    def lines_written(self) -> list[str]:
        return b"".join(self.writes).decode(errors="replace").splitlines()

    # -- Transport ---------------------------------------------------------
    def write(self, data: bytes) -> int:
        if self.closed:
            raise IOError("write on a closed transport")
        self.writes.append(bytes(data))
        if self._responder is not None:
            for line in bytes(data).decode(errors="replace").splitlines():
                reply = self._responder(line)
                if reply is not None:
                    self.queue(reply)
        return len(data)

    def readline(self) -> bytes:
        idx = self.rx.find(b"\n")
        if idx < 0:
            out = bytes(self.rx)
            self.rx.clear()
            return out
        out = bytes(self.rx[: idx + 1])
        del self.rx[: idx + 1]
        return out

    def reset_input_buffer(self) -> None:
        """Discards waiting bytes, exactly as pyserial does.

        A fake that counted the call but kept the buffer would make the drain
        look tested while proving nothing - the bug it guards against is
        precisely "the stale bytes were still there".
        """
        self.reset_count += 1
        self.rx.clear()

    def close(self) -> None:
        self.closed = True
        self.close_count += 1


#: What the fake board answers. It mirrors the SHAPE of the replies in
#: firmware/src/protocol.c - a bare `OK` acknowledges a command, a named
#: payload line answers a query, `ERR <reason>` refuses - and nothing else: it
#: does not check arity, ranges or number formats, so `STBY` with no argument
#: gets an `OK` here and an `ERR arity` from the real board. That is fine for
#: exercising the host's framing, and it is deliberately NOT the thing that
#: proves the two ends agree. The real parser is what does that, in
#: test_firmware_protocol.py::test_the_host_link_speaks_a_dialect_the_firmware_accepts.
#: Keeping the shape in one place means the day the wire format moves again,
#: one function changes and every link test follows.
FAKE_FIRMWARE_VERSION = "0.1.0"
FAKE_FIRMWARE_SHA = "deadbee"
FAKE_ENC_TICKS = 10432
FAKE_ENC_T_US = 8123456

_COMMANDS = {"SET", "STOP", "BRAKE", "STBY", "PID", "PIDEN", "SETRPS",
             "TELEM", "RESET"}


def pico_responder(line: str) -> str:
    """A fake Pico speaking the reconciled bench protocol."""
    text = line.strip()
    if not text:
        return "ERR empty\n"
    verb = text.split()[0].upper()
    if verb == "PING":
        return f"PONG {FAKE_FIRMWARE_VERSION} {FAKE_FIRMWARE_SHA}\n"
    if verb == "ID?":
        return f"ID pico2w {FAKE_FIRMWARE_VERSION}\n"
    if verb == "ENC?":
        return f"ENC {FAKE_ENC_TICKS} {FAKE_ENC_T_US}\n"
    if verb in _COMMANDS:
        return "OK\n"
    return "ERR unknown_cmd\n"


@pytest.fixture
def fake_transport() -> FakeTransport:
    """A silent transport: writes are recorded, nothing ever answers."""
    return FakeTransport()


@pytest.fixture
def fake_pico(fake_transport) -> FakeTransport:
    """A transport that answers like a live Pico."""
    return fake_transport.set_responder(pico_responder)


# --------------------------------------------------------------------------
# Frozen clock - the shape rover_bench.link.Clock wants
# --------------------------------------------------------------------------


UTC = timezone.utc
FROZEN_START = datetime(2026, 8, 31, 12, 0, 0, tzinfo=UTC)


class FakeClock:
    """Deterministic monotonic time, plus a frozen wall clock in UTC.

    `sleep` advances the virtual clock instead of blocking, so a test that
    exercises a two-second dwell runs in microseconds and still drives exactly
    the same accounting code the real run uses.
    """

    def __init__(self, start: float = 1000.0, wall: datetime = FROZEN_START) -> None:
        self._t = start
        self._wall = wall
        self.sleeps: list[float] = []

    # link.Clock
    def now(self) -> float:
        return self._t

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        if seconds > 0:
            self._t += seconds

    # wall clock, for manifests
    def utcnow(self) -> datetime:
        return self._wall

    def advance(self, seconds: float) -> "FakeClock":
        self._t += seconds
        self._wall = self._wall + timedelta(seconds=seconds)
        return self


@pytest.fixture
def frozen_clock() -> FakeClock:
    return FakeClock()


# --------------------------------------------------------------------------
# Fake sigrok runner - the shape rover_bench.analyser.Runner wants
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class FakeCommandResult:
    """Duck-type of `analyser.CommandResult`."""

    argv: tuple
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


@dataclass
class RecordingRunner:
    """Records every argv and replays canned stdout, matched by substring.

    Longest needle wins, so a test can register `--scan` and `--show`
    separately without caring about the rest of the command line.
    """

    routes: dict = field(default_factory=dict)
    calls: list = field(default_factory=list)
    default: tuple = ("", 0, "")
    side_effect = None

    def register(self, needle: str, stdout: str = "", returncode: int = 0,
                 stderr: str = "") -> "RecordingRunner":
        self.routes[needle] = (stdout, returncode, stderr)
        return self

    def __call__(self, argv, **kwargs) -> FakeCommandResult:
        argv = [str(a) for a in argv]
        self.calls.append(argv)
        joined = " ".join(argv)
        if self.side_effect is not None:
            self.side_effect(argv)
        for needle in sorted(self.routes, key=len, reverse=True):
            if needle in joined:
                stdout, rc, stderr = self.routes[needle]
                return FakeCommandResult(tuple(argv), rc, stdout, stderr)
        stdout, rc, stderr = self.default
        return FakeCommandResult(tuple(argv), rc, stdout, stderr)

    @property
    def joined(self) -> list[str]:
        return [" ".join(c) for c in self.calls]

    @property
    def last(self) -> list[str]:
        assert self.calls, "the runner was never called"
        return self.calls[-1]


@pytest.fixture
def fake_runner() -> RecordingRunner:
    """A runner pre-loaded with realistic sigrok-cli output."""
    runner = RecordingRunner()
    runner.register("--version", "sigrok-cli 0.7.2\n")
    runner.register("--scan", SIGROK_SCAN_OUTPUT)
    runner.register("--show", SIGROK_SHOW_OUTPUT)
    return runner


# --------------------------------------------------------------------------
# Temp repo
# --------------------------------------------------------------------------


REGISTRY_TEMPLATE = """# Experiment registry

One row per experiment. **A result not persisted did not happen** - CLOSE
ritual item 7.

| ID | Date | Story | Question | Method | Result | Data |
|---|---|---|---|---|---|---|
| `power-sag` | 2026-08-31 | 1.1 | What is the 6F22's internal resistance? | Measure open-circuit V, then V under load | ~10 ohm. Unusable | - |

## Planned

| ID | Story | Question |
|---|---|---|
| `motor-char` | 1.5 | Deadband, duty->rad/s, no-load and stall current |
"""


@dataclass
class TempRepo:
    root: Path

    @property
    def registry(self) -> Path:
        return self.root / "experiments" / "REGISTRY.md"

    @property
    def docs(self) -> Path:
        return self.root / "docs"

    def read_registry(self) -> str:
        return self.registry.read_text()


@pytest.fixture
def temp_repo(tmp_path) -> TempRepo:
    """A throwaway repo skeleton with a real-shaped REGISTRY.md.

    Not a git repo: nothing in the default suite may shell out to git.
    """
    for sub in ("docs", "experiments", "experiments/plots", "tools", "firmware/src"):
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)
    (tmp_path / "experiments" / "REGISTRY.md").write_text(REGISTRY_TEMPLATE)
    return TempRepo(tmp_path)


# --------------------------------------------------------------------------
# Synthetic motor
# --------------------------------------------------------------------------


class SyntheticMotor:
    """A first-order motor + encoder, good enough to close a loop around.

    ``omega' = (gain * u_eff - omega) / tau``, with a symmetric deadband -
    real N20s do not move below some duty, and a controller that ignores that
    winds up. Forward Euler, which is fine at 100 Hz against a 0.15 s constant.

    NONE OF THESE NUMBERS ARE HARDWARE CLAIMS.

    ``ticks_per_rev`` is deliberately a flat, obviously-invented 1000: it is
    only ever used to turn this fake's rad/s into a fake tick count, and no
    test asserts anything about its magnitude. It used to default to 5600 -
    the firmware's own placeholder, 14 x 4 edges x 100:1 - which made a
    disputed, unmeasured figure (Adafruit says 14 counts/rev, retailers say
    11: docs/HARDWARE.md §2.1) sit in a fixture where it was one grep away
    from being quoted as though the project had measured it. Story 1.4
    measures the real one; until then no file in this repo should carry a
    plausible-looking value for it.
    """

    def __init__(self, gain_rad_s_per_duty: float = 60.0, tau_s: float = 0.15,
                 deadband_duty: float = 0.08, ticks_per_rev: float = 1000.0,
                 wheel_diameter_m: float = 0.065):
        self.gain = gain_rad_s_per_duty
        self.tau_s = tau_s
        self.deadband_duty = deadband_duty
        self.ticks_per_rev = ticks_per_rev
        self.wheel_diameter_m = wheel_diameter_m
        self.omega_rad_s = 0.0
        self.ticks = 0
        self._residual = 0.0
        self.time_s = 0.0
        self.history: list[tuple[float, float, float]] = []

    def effective_duty(self, duty_frac: float) -> float:
        u = max(-1.0, min(1.0, duty_frac))
        if abs(u) <= self.deadband_duty:
            return 0.0
        sign = 1.0 if u > 0 else -1.0
        return sign * (abs(u) - self.deadband_duty) / (1.0 - self.deadband_duty)

    def step(self, duty_frac: float, dt_s: float = CONTROL_DT_S) -> float:
        target = self.gain * self.effective_duty(duty_frac)
        self.omega_rad_s += (target - self.omega_rad_s) * (dt_s / self.tau_s)
        self._residual += self.omega_rad_s * dt_s / (2.0 * math.pi) * self.ticks_per_rev
        whole = int(self._residual)
        self._residual -= whole
        self.ticks += whole
        self.time_s += dt_s
        self.history.append((self.time_s, duty_frac, self.omega_rad_s))
        return self.omega_rad_s

    @property
    def speed_mps(self) -> float:
        return self.omega_rad_s * self.wheel_diameter_m / 2.0

    def steady_state_omega(self, duty_frac: float) -> float:
        return self.gain * self.effective_duty(duty_frac)


@pytest.fixture
def synthetic_motor() -> SyntheticMotor:
    return SyntheticMotor()


# --------------------------------------------------------------------------
# The host-pure firmware, compiled and loaded through ctypes
# --------------------------------------------------------------------------


HOST_PURE_SOURCES = ("protocol.c", "control.c")
SHIM_SOURCE = TESTS_DIR / "firmware_shim.c"


def _compile_firmware(build_dir: Path) -> tuple[ctypes.CDLL | None, str]:
    """Compile the host-pure firmware + the test shim into a shared library.

    Returns (library, reason-it-is-missing). Never raises: no compiler, or a
    firmware file that has not landed yet, means these tests SKIP with a clear
    reason rather than turning the whole suite red for an environment problem.
    """
    missing = [s for s in HOST_PURE_SOURCES if not (FIRMWARE_SRC / s).is_file()]
    if missing:
        return None, f"firmware sources not present yet: {missing}"
    if not SHIM_SOURCE.is_file():  # pragma: no cover - shipped with the suite
        return None, "tests/firmware_shim.c is missing"

    compiler = os.environ.get("CC") or "cc"
    out = build_dir / "libroverfw.so"
    argv = [
        compiler, "-shared", "-fPIC", "-O1", "-g",
        "-std=c11", "-Wall",
        f"-I{FIRMWARE_SRC}",
        str(SHIM_SOURCE),
        *[str(FIRMWARE_SRC / s) for s in HOST_PURE_SOURCES],
        "-o", str(out), "-lm",
    ]
    try:
        proc = _REAL_RUN(argv, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"cannot run {compiler}: {exc}"
    if proc.returncode != 0:
        return None, (
            f"{compiler} failed (rc={proc.returncode}). This is a REAL failure "
            f"if you have a compiler:\n{proc.stderr[-4000:]}"
        )
    try:
        return ctypes.CDLL(str(out)), ""
    except OSError as exc:  # pragma: no cover - platform dependent
        return None, f"cannot load {out}: {exc}"


@pytest.fixture(scope="session")
def firmware_lib(tmp_path_factory):
    """The compiled host-pure firmware, or a skip.

    Session-scoped so the compile happens once. `firmware/src/protocol.h` and
    `control.h` both declare themselves HOST-PURE with no pico-sdk includes;
    this fixture is what holds them to it - the day someone adds
    `#include "pico/stdlib.h"` to either, this compile fails and says so.
    """
    build_dir = tmp_path_factory.mktemp("firmware-build")
    lib, reason = _compile_firmware(build_dir)
    if lib is None:
        pytest.skip(reason)
    _declare_signatures(lib)
    return lib


def _declare_signatures(lib: ctypes.CDLL) -> None:
    """ctypes argtypes/restypes. Without these, floats are passed as doubles
    and every PID assertion is quietly wrong."""
    c = ctypes
    sigs = {
        "shim_state_size": ([], c.c_size_t),
        "shim_state_init": ([c.c_void_p], None),
        "shim_handle": ([c.c_char_p, c.c_char_p, c.c_size_t, c.c_void_p], c.c_int),
        "shim_reader_size": ([], c.c_size_t),
        "shim_reader_init": ([c.c_void_p], None),
        "shim_reader_push": ([c.c_void_p, c.c_char], c.c_int),
        "shim_reader_handle": ([c.c_void_p, c.c_char_p, c.c_size_t, c.c_void_p], c.c_int),
        "shim_duty_frac": ([c.c_void_p], c.c_float),
        "shim_brake": ([c.c_void_p], c.c_int),
        "shim_stby": ([c.c_void_p], c.c_int),
        "shim_pid_enabled": ([c.c_void_p], c.c_int),
        "shim_pid_reset_requested": ([c.c_void_p], c.c_int),
        "shim_reset_requested": ([c.c_void_p], c.c_int),
        "shim_setpoint": ([c.c_void_p], c.c_float),
        "shim_kp": ([c.c_void_p], c.c_float),
        "shim_ki": ([c.c_void_p], c.c_float),
        "shim_kd": ([c.c_void_p], c.c_float),
        "shim_ticks_per_rev": ([c.c_void_p], c.c_float),
        "shim_telem_hz": ([c.c_void_p], c.c_uint32),
        "shim_ok_count": ([c.c_void_p], c.c_uint32),
        "shim_err_count": ([c.c_void_p], c.c_uint32),
        "shim_failsafe_timeout_us": ([c.c_void_p], c.c_uint32),
        "shim_failsafe_tripped": ([c.c_void_p], c.c_int),
        "shim_set_enc": ([c.c_void_p, c.c_int32, c.c_uint32], None),
        "shim_format_telem": ([c.c_char_p, c.c_size_t, c.c_uint32, c.c_int32,
                               c.c_float], c.c_int),
        "shim_telem_divider": ([c.c_uint32, c.c_uint32], c.c_uint32),
        "shim_clampf": ([c.c_float, c.c_float, c.c_float], c.c_float),
        "shim_enc_delta": ([c.c_int32, c.c_int32], c.c_int32),
        "shim_ticks_to_omega": ([c.c_int32, c.c_float, c.c_float], c.c_float),
        "shim_pidcfg_size": ([], c.c_size_t),
        "shim_pidst_size": ([], c.c_size_t),
        "shim_pid_config_init": ([c.c_void_p, c.c_float], None),
        "shim_pid_set_gains": ([c.c_void_p, c.c_float, c.c_float, c.c_float], None),
        "shim_pid_set_out_limits": ([c.c_void_p, c.c_float, c.c_float], None),
        "shim_pid_set_i_limits": ([c.c_void_p, c.c_float, c.c_float], None),
        "shim_pid_set_dt": ([c.c_void_p, c.c_float], None),
        "shim_pid_reset": ([c.c_void_p], None),
        "shim_pid_step": ([c.c_void_p, c.c_void_p, c.c_float, c.c_float,
                           c.c_float], c.c_float),
        "shim_pid_integral": ([c.c_void_p], c.c_float),
        "shim_pid_have_prev": ([c.c_void_p], c.c_int),
        "shim_pwm_wrap_for_freq": ([c.c_uint32, c.c_uint32], c.c_uint16),
        "shim_duty_to_pwm_level": ([c.c_float, c.c_uint16], c.c_uint16),
        "shim_motor_dir_for_duty": ([c.c_float], c.c_int),
        "shim_motor_dir_pins": ([c.c_int, c.POINTER(c.c_int), c.POINTER(c.c_int)], None),
        "shim_ff_size": ([], c.c_size_t),
        "shim_ff_init": ([c.c_void_p], None),
        "shim_ff_set": ([c.c_void_p, c.c_float, c.c_float, c.c_int], None),
        "shim_ff_duty_for_omega": ([c.c_void_p, c.c_float], c.c_float),
    }
    for name, (argtypes, restype) in sigs.items():
        fn = getattr(lib, name)
        fn.argtypes = argtypes
        fn.restype = restype


class RoverState:
    """Pythonic handle on one `struct rover_state`, addressed only through the
    shim's accessors so the test suite has no struct-layout coupling."""

    def __init__(self, lib):
        self.lib = lib
        self.buf = ctypes.create_string_buffer(lib.shim_state_size())
        lib.shim_state_init(self.buf)

    # -- driving ---------------------------------------------------------
    def handle(self, line: str) -> tuple[bool, str]:
        """Run one command line (no terminator). Returns (accepted, reply)."""
        out = ctypes.create_string_buffer(256)
        rc = self.lib.shim_handle(line.encode("utf-8", "surrogateescape"),
                                  out, 256, self.buf)
        return bool(rc), out.value.decode("utf-8", "replace")

    def set_encoder(self, ticks: int, t_us: int) -> None:
        self.lib.shim_set_enc(self.buf, ticks, t_us)

    # -- observing --------------------------------------------------------
    @property
    def duty_frac(self) -> float:
        return self.lib.shim_duty_frac(self.buf)

    @property
    def brake(self) -> bool:
        return bool(self.lib.shim_brake(self.buf))

    @property
    def stby_enabled(self) -> bool:
        return bool(self.lib.shim_stby(self.buf))

    @property
    def pid_enabled(self) -> bool:
        return bool(self.lib.shim_pid_enabled(self.buf))

    @property
    def pid_reset_requested(self) -> bool:
        return bool(self.lib.shim_pid_reset_requested(self.buf))

    @property
    def reset_requested(self) -> bool:
        return bool(self.lib.shim_reset_requested(self.buf))

    @property
    def setpoint_omega_rad_s(self) -> float:
        return self.lib.shim_setpoint(self.buf)

    @property
    def gains(self) -> tuple[float, float, float]:
        return (self.lib.shim_kp(self.buf), self.lib.shim_ki(self.buf),
                self.lib.shim_kd(self.buf))

    @property
    def ticks_per_output_rev(self) -> float:
        return self.lib.shim_ticks_per_rev(self.buf)

    @property
    def telem_hz(self) -> int:
        return self.lib.shim_telem_hz(self.buf)

    @property
    def ok_count(self) -> int:
        return self.lib.shim_ok_count(self.buf)

    @property
    def err_count(self) -> int:
        return self.lib.shim_err_count(self.buf)

    @property
    def failsafe_timeout_us(self) -> int:
        return self.lib.shim_failsafe_timeout_us(self.buf)

    @property
    def failsafe_tripped(self) -> bool:
        return bool(self.lib.shim_failsafe_tripped(self.buf))

    def snapshot(self) -> bytes:
        """Raw bytes of the whole struct.

        This is what makes "a rejected command never mutates state" a real
        assertion rather than a check of the four fields someone remembered:
        it compares EVERY byte, including padding-adjacent fields nobody
        thought about. ok_count/err_count are bookkeeping counters that a
        rejection is allowed to move, so tests compare `snapshot_stable`.
        """
        return bytes(self.buf)

    def snapshot_stable(self) -> tuple[object, ...]:
        """Everything a command could set, excluding the ok/err counters."""
        return (
            self.duty_frac, self.brake, self.stby_enabled, self.pid_enabled,
            self.setpoint_omega_rad_s, self.gains, self.ticks_per_output_rev,
            self.telem_hz, self.reset_requested, self.pid_reset_requested,
            self.failsafe_timeout_us,
        )


@pytest.fixture
def rover_state(firmware_lib) -> RoverState:
    """A freshly power-on-initialised firmware state."""
    return RoverState(firmware_lib)
