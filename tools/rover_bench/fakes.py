"""fakes.py — a fake Pico and a fake sigrok, good enough to be useful.

WHY THESE LIVE IN THE PACKAGE AND NOT IN tests/
-----------------------------------------------
`--dry-run` uses them.  That is the point: the dry run is not a separate
simplified path through the CLI, it is *the same path* with the hardware
replaced.  If the fakes lived in the test suite, `--dry-run` would have to
fake something else, and then the thing the operator exercises before touching
a real motor would not be the thing that runs on the real motor.

WHAT THE FAKE MOTOR IS
----------------------
A first-order lag with a symmetric deadband::

    omega_ss = gain * (|duty| - deadband) / (1 - deadband),  zero below deadband
    omega    -> omega_ss with time constant tau

Real N20s do not move below some duty, and a controller that ignores that winds
its integrator up while the wheel sits still.  A fake without a deadband would
let a sweep produce a straight line through the origin and hide the single most
important feature of the real curve.

Each fake motor gets slightly different constants, because **four "identical"
motors never are** — quantifying that difference is the whole point of Story
1.5, and a fake where all four match would make the tooling look like it works
when it cannot tell them apart.

.. warning::
   **None of these numbers are hardware claims.**  They are made up so the
   arithmetic has something to chew on.  `ticks_per_rev` here is a fake
   constant; the real one is *disputed and unmeasured* (11 vs 14 counts per
   motor rev) and must be measured with `rover-bench ticks-per-rev`.  Every
   manifest written from a dry run carries `dry_run: true` for exactly this
   reason.

THE VIRTUAL CLOCK
-----------------
`VirtualClock` makes `sleep()` free.  A full sweep is minutes of dwell time; on
a virtual clock the identical code runs in milliseconds, which is what turns
`--dry-run` from a thing nobody waits for into a pre-flight check you actually
run before wiring up a motor.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Any, Sequence

from .analyser import (
    Analyser,
    CommandResult,
    COMPUTE_BUSY_CHANNEL,
    LOOP_TICK_CHANNEL,
    write_srzip,
)
from .link import Link, OMEGA_MAX_RAD_S, REPLY_ENC, REPLY_PONG

FAKE_FIRMWARE_VERSION = "0.0.0-fake"
FAKE_FIRMWARE_SHA = "deadbeef"
FAKE_PROTOCOL_VERSION = "1"


# --------------------------------------------------------------------------
# Clock
# --------------------------------------------------------------------------

class VirtualClock:
    """Time that only moves when someone sleeps.  Deterministic and instant."""

    def __init__(self, start: float = 0.0) -> None:
        self._now = float(start)

    def now(self) -> float:
        return self._now

    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            self._now += float(seconds)

    def advance(self, seconds: float) -> None:
        self._now += float(seconds)


# --------------------------------------------------------------------------
# The fake motor
# --------------------------------------------------------------------------

@dataclass
class FakeMotor:
    """One simulated N20 + encoder.  Integrated lazily, on demand.

    Lazy integration matters: the host asks for the encoder count at irregular
    intervals, and advancing the model to "now" at each read is both exact for
    a first-order system and free when nobody is looking.
    """

    index: int
    deadband_duty_frac: float = 0.10
    max_speed_ticks_per_s: float = 2200.0
    tau_s: float = 0.12
    ticks_per_rev: float = 1200.0     # a FAKE constant — the real one is unmeasured
    noise_ticks_per_s: float = 6.0

    duty_frac: float = 0.0
    enabled: bool = False
    mode: str = "open"
    target_ticks_per_s: float = 0.0
    kp: float = 0.0
    ki: float = 0.0
    kd: float = 0.0

    speed_ticks_per_s: float = 0.0
    ticks_exact: float = 0.0
    ticks: int = 0
    last_t_s: float = 0.0
    _rng: random.Random = field(default_factory=lambda: random.Random(0))

    @classmethod
    def for_index(cls, index: int, seed: int = 1234) -> "FakeMotor":
        """Four motors that differ, the way four real ones do."""
        return cls(
            index=index,
            deadband_duty_frac=0.10 + 0.012 * index,
            max_speed_ticks_per_s=2200.0 - 90.0 * index,
            tau_s=0.12 + 0.01 * index,
            _rng=random.Random(seed + index),
        )

    # -- model -----------------------------------------------------------

    def steady_state_ticks_per_s(self) -> float:
        """Deadband, then linear.  Zero when STBY is low, whatever the duty."""
        if not self.enabled:
            return 0.0
        duty = self._effective_duty()
        magnitude = abs(duty)
        if magnitude <= self.deadband_duty_frac:
            return 0.0
        span = 1.0 - self.deadband_duty_frac
        fraction = (magnitude - self.deadband_duty_frac) / span
        return math.copysign(fraction * self.max_speed_ticks_per_s, duty)

    def _effective_duty(self) -> float:
        """Closed loop is modelled as a stiff proportional map to duty.

        Not a faithful PID — the real controller lives in firmware.  It just
        has to be monotone and settle, so a closed-loop step response has a
        rise time and an overshoot for `step_metrics` to measure.
        """
        if self.mode != "closed":
            return self.duty_frac
        if self.max_speed_ticks_per_s <= 0:
            return 0.0
        wanted = self.target_ticks_per_s / self.max_speed_ticks_per_s
        if abs(wanted) < 1e-9:
            return 0.0
        span = 1.0 - self.deadband_duty_frac
        duty = math.copysign(self.deadband_duty_frac + abs(wanted) * span, wanted)
        return max(-1.0, min(1.0, duty))

    def advance_to(self, t_s: float) -> None:
        """Integrate the first-order lag exactly up to `t_s`."""
        dt = t_s - self.last_t_s
        if dt <= 0:
            return
        self.last_t_s = t_s
        target = self.steady_state_ticks_per_s()
        decay = math.exp(-dt / self.tau_s)
        start = self.speed_ticks_per_s
        end = target + (start - target) * decay
        # Exact integral of the exponential over the interval.
        travelled = target * dt + (start - target) * self.tau_s * (1.0 - decay)
        if abs(end) > 1e-9:
            travelled += self._rng.uniform(-1.0, 1.0) * self.noise_ticks_per_s * dt
        self.speed_ticks_per_s = end
        self.ticks_exact += travelled
        self.ticks = int(self.ticks_exact)


# --------------------------------------------------------------------------
# The fake Pico
# --------------------------------------------------------------------------

class FakePico:
    """A `link.Transport` that answers the bench protocol and turns a motor.

    Line in, line out, exactly like the real board.  It keeps the written lines
    so a test or a dry run can show what would have been sent.
    """

    def __init__(self, clock: VirtualClock | None = None, *,
                 motors: int = 4, seed: int = 1234,
                 firmware_version: str = FAKE_FIRMWARE_VERSION,
                 firmware_sha: str = FAKE_FIRMWARE_SHA) -> None:
        self.clock = clock or VirtualClock()
        self.motors = {i: FakeMotor.for_index(i, seed) for i in range(motors)}
        self.firmware_version = firmware_version
        self.firmware_sha = firmware_sha
        self.stby = False
        self.under_test = 0
        self.telem_hz = 0
        self.is_open = True
        self.close_count = 0
        self.lines_written: list[str] = []
        self._rx: list[bytes] = []

    # -- transport surface ----------------------------------------------

    def write(self, data: bytes) -> int:
        if not self.is_open:
            raise IOError("write on a closed port")
        text = bytes(data).decode("ascii", "replace")
        for line in text.splitlines():
            if line.strip():
                self.lines_written.append(line.strip())
                self._rx.append((self._handle(line.strip()) + "\n").encode("ascii"))
        return len(data)

    def readline(self) -> bytes:
        return self._rx.pop(0) if self._rx else b""

    def reset_input_buffer(self) -> None:
        self._rx.clear()

    def close(self) -> None:
        self.is_open = False
        self.close_count += 1

    # -- the protocol ----------------------------------------------------
    # Same vocabulary as firmware/src/protocol.c.  A fake that spoke a
    # different dialect would let a wire-format bug survive every dry run and
    # every test, and surface only with a motor wired up.

    def handle_line(self, line: str) -> str:
        """One command line in, one reply line out.  Public on purpose.

        Anything that needs a stand-in for the board's parser — a test fixture,
        a REPL experiment, another stream's fake transport — should call this
        rather than reimplement the dialect.  A second hand-written fake is a
        second thing to drift out of step with `firmware/src/protocol.c`.
        """
        return self._handle(line.strip())

    def _handle(self, line: str) -> str:
        words = line.split()
        if not words:
            # firmware/src/protocol.c answers `ERR empty`.  Raising IndexError
            # here instead would make this fake useless as the stand-in parser
            # it advertises itself as.
            return "ERR empty"
        verb = words[0].upper()
        args = words[1:]
        handler = self._HANDLERS.get(verb)
        if handler is None:
            return "ERR unknown_cmd"
        try:
            return handler(self, args)
        except (ValueError, IndexError, KeyError):
            return "ERR not_a_number"

    def _motor(self) -> FakeMotor:
        """The motor under test.

        One motor at a time, exactly like the firmware: `SET` carries no
        channel index.  The fake keeps four so a caller can characterise each
        in turn by selecting one, but only `under_test` is ever driven.
        """
        motor = self.motors[self.under_test]
        motor.advance_to(self.clock.now())
        return motor

    def _cmd_ping(self, args: Sequence[str]) -> str:
        if args:
            return "ERR arity"
        return f"{REPLY_PONG} {self.firmware_version} {self.firmware_sha}"

    def _cmd_id(self, args: Sequence[str]) -> str:
        if args:
            return "ERR arity"
        return f"ID pico2w-fake {self.firmware_version}"

    def _cmd_stby(self, args: Sequence[str]) -> str:
        if len(args) != 1:
            return "ERR arity"
        value = int(args[0])
        if value not in (0, 1):
            return "ERR range"
        for motor in self.motors.values():
            motor.advance_to(self.clock.now())
            motor.enabled = bool(value)
        self.stby = bool(value)
        return "OK"

    def _cmd_set(self, args: Sequence[str]) -> str:
        if len(args) != 1:
            return "ERR arity"
        duty = float(args[0])
        if not -1.0 <= duty <= 1.0:
            return "ERR range"
        motor = self._motor()
        motor.duty_frac = duty
        motor.mode = "open"          # SET is open loop; it turns the PID off
        return "OK"

    def _cmd_stop(self, args: Sequence[str]) -> str:
        if args:
            return "ERR arity"
        motor = self._motor()
        motor.duty_frac = 0.0
        motor.mode = "open"
        motor.target_ticks_per_s = 0.0
        return "OK"

    def _cmd_brake(self, args: Sequence[str]) -> str:
        if args:
            return "ERR arity"
        motor = self._motor()
        motor.duty_frac = 0.0
        motor.mode = "open"
        motor.speed_ticks_per_s = 0.0   # shorted windings: it stops hard
        return "OK"

    def _cmd_enc(self, args: Sequence[str]) -> str:
        if args:
            return "ERR arity"
        motor = self._motor()
        return f"{REPLY_ENC} {motor.ticks} {int(self.clock.now() * 1_000_000)}"

    def _cmd_pid(self, args: Sequence[str]) -> str:
        if len(args) != 3:
            return "ERR arity"
        gains = [float(a) for a in args]
        if any(g < 0 for g in gains):
            return "ERR range"       # negative feedback would become positive
        motor = self._motor()
        motor.kp, motor.ki, motor.kd = gains
        return "OK"

    def _cmd_piden(self, args: Sequence[str]) -> str:
        if len(args) != 1:
            return "ERR arity"
        value = int(args[0])
        if value not in (0, 1):
            return "ERR range"
        motor = self._motor()
        motor.mode = "closed" if value else "open"
        if not value:
            motor.duty_frac = 0.0    # leaving closed loop drops to coast
        return "OK"

    def _cmd_setrps(self, args: Sequence[str]) -> str:
        if len(args) != 1:
            return "ERR arity"
        omega = float(args[0])
        if abs(omega) > OMEGA_MAX_RAD_S:
            return "ERR range"
        motor = self._motor()
        # The wire unit is rad/s of the OUTPUT shaft; the model counts ticks.
        motor.target_ticks_per_s = omega * motor.ticks_per_rev / (2.0 * math.pi)
        return "OK"

    def _cmd_telem(self, args: Sequence[str]) -> str:
        if len(args) != 1:
            return "ERR arity"
        self.telem_hz = int(args[0])
        return "OK"

    def _cmd_reset(self, args: Sequence[str]) -> str:
        """The firmware has RESET and so does `link.CMD_RESET`.

        Without it here the fake answers `ERR unknown_cmd` to a command the
        board implements, and a dry run sends someone hunting a firmware bug
        that does not exist.
        """
        if args:
            return "ERR arity"
        motor = self._motor()
        motor.duty_frac = 0.0
        motor.mode = "open"
        motor.target_ticks_per_s = 0.0
        motor.kp = motor.ki = motor.kd = 0.0
        return "OK"

    _HANDLERS = {
        "PING": _cmd_ping,
        "ID?": _cmd_id,
        "STBY": _cmd_stby,
        "SET": _cmd_set,
        "STOP": _cmd_stop,
        "BRAKE": _cmd_brake,
        "ENC?": _cmd_enc,
        "PID": _cmd_pid,
        "PIDEN": _cmd_piden,
        "SETRPS": _cmd_setrps,
        "TELEM": _cmd_telem,
        "RESET": _cmd_reset,
    }

    # -- test/dry-run helper --------------------------------------------

    def hand_turn(self, motor_index: int, revolutions: float,
                  ticks_per_rev: float | None = None) -> None:
        """Simulate a human turning the output shaft, for `ticks-per-rev`.

        Slightly imprecise on purpose (±0.4%): the operator cannot land the
        mark perfectly on its line, and a dry run that reports zero spread
        would suggest a precision the real procedure does not have.
        """
        motor = self.motors[motor_index]
        per_rev = ticks_per_rev if ticks_per_rev is not None else motor.ticks_per_rev
        error = 1.0 + motor._rng.uniform(-0.004, 0.004)  # noqa: SLF001 - same module
        motor.ticks_exact += revolutions * per_rev * error
        motor.ticks = int(motor.ticks_exact)


# --------------------------------------------------------------------------
# The fake sigrok
# --------------------------------------------------------------------------

FAKE_SCAN_OUTPUT = (
    "The following devices were found:\n"
    "fx2lafw:conn=1.10 - Saleae Logic with 8 channels: D0 D1 D2 D3 D4 D5 D6 D7\n"
)

FAKE_SHOW_OUTPUT = (
    "Driver functions:\n"
    "    Logic analyzer\n"
    "Scan options:\n"
    "    conn\n"
    "fx2lafw:conn=1.10 - Saleae Logic with 8 channels: D0 D1 D2 D3 D4 D5 D6 D7\n"
    "Supported configuration options:\n"
    "    continuous: \n"
    "    limit_samples: 0 - 0\n"
    "    captureratio: 0 - 100\n"
    "    samplerate (RW): 20 kHz, 25 kHz, 50 kHz, 100 kHz, 200 kHz, 250 kHz, "
    "500 kHz, 1 MHz, 2 MHz, 3 MHz, 4 MHz, 6 MHz, 8 MHz, 12 MHz, 16 MHz, 24 MHz\n"
)


class FakeSigrok:
    """A runner that answers like `sigrok-cli` and writes a genuine `.sr`.

    Writing a real srzip rather than returning a decoded object in memory is
    deliberate: it means `--dry-run` exercises `decode_srzip` on the same bytes
    the hardware produces, so a bug in the decoder is caught by a dry run
    instead of at the bench.

    The synthesised loop signal is a 100 Hz toggle with a little jitter and a
    ~30% compute-busy window, which is roughly what the firmware should look
    like — enough for the statistics to be non-degenerate.
    """

    def __init__(self, *, loop_hz: float = 100.0, jitter_frac: float = 0.02,
                 busy_frac: float = 0.30, seed: int = 7) -> None:
        self.loop_hz = loop_hz
        self.jitter_frac = jitter_frac
        self.busy_frac = busy_frac
        self.calls: list[list[str]] = []
        self._rng = random.Random(seed)

    def __call__(self, argv: Sequence[str], **_kwargs: Any) -> CommandResult:
        args = [str(a) for a in argv]
        self.calls.append(args)
        joined = " ".join(args)
        if "--version" in args:
            return CommandResult(tuple(args), 0, "sigrok-cli 0.7.2-fake\n", "")
        if "--scan" in args:
            return CommandResult(tuple(args), 0, FAKE_SCAN_OUTPUT, "")
        if "--show" in args:
            return CommandResult(tuple(args), 0, FAKE_SHOW_OUTPUT, "")
        if "--output-file" in args:
            self._write_capture(args)
            return CommandResult(tuple(args), 0, "", "")
        return CommandResult(tuple(args), 1, "", f"fake sigrok: unhandled {joined}")

    # -- signal synthesis ------------------------------------------------

    def _write_capture(self, args: Sequence[str]) -> None:
        out_path = args[args.index("--output-file") + 1]
        channels = args[args.index("--channels") + 1].split(",")
        samples = int(args[args.index("--samples") + 1])
        rate_hz: int | None = None
        for index, arg in enumerate(args):
            if arg == "--config" and index + 1 < len(args):
                key, _, value = args[index + 1].partition("=")
                if key == "samplerate":
                    rate_hz = int(value)
        if rate_hz is None:
            # No default, here of all places.  The package's rule is that the
            # sample rate is discovered from the device and never assumed; a
            # fake that quietly invents one writes a .sr with a fabricated
            # timebase, and every edge measurement taken from it is wrong by
            # whatever ratio nobody noticed.
            raise ValueError(
                "fake sigrok: no --config samplerate= in the argv; refusing to "
                "invent a timebase.  build_capture_argv always sets it."
            )
        write_srzip(out_path, rate_hz, channels,
                    self._synthesise(channels, samples, rate_hz))

    def _synthesise(self, channels: Sequence[str], samples: int,
                    rate_hz: int) -> list[int]:
        """Build the sample words: a toggling loop tick and a busy window."""
        tick_bit = (1 << channels.index(LOOP_TICK_CHANNEL)
                    if LOOP_TICK_CHANNEL in channels else 0)
        busy_bit = (1 << channels.index(COMPUTE_BUSY_CHANNEL)
                    if COMPUTE_BUSY_CHANNEL in channels else 0)

        words = [0] * samples
        nominal = rate_hz / self.loop_hz
        position = 0.0
        level = 0
        while position < samples:
            period = nominal * (1.0 + self._rng.uniform(-self.jitter_frac,
                                                        self.jitter_frac))
            start = int(position)
            end = min(samples, int(position + period))
            busy_end = min(end, start + max(1, int(period * self.busy_frac)))
            word_idle = tick_bit if level else 0
            for i in range(start, busy_end):
                words[i] = word_idle | busy_bit
            for i in range(busy_end, end):
                words[i] = word_idle
            level ^= 1
            position += period
        return words


# --------------------------------------------------------------------------
# Assembly
# --------------------------------------------------------------------------

def make_firmware_responder(clock: VirtualClock | None = None, *,
                            seed: int = 1234) -> Any:
    """A `line -> reply` callable speaking the firmware's dialect.

    For any fake transport that wants a live-looking board without depending on
    this package's `Link`.  It is backed by a real `FakePico`, so the replies
    move with the motor model instead of being canned strings that quietly stop
    matching what `firmware/src/protocol.c` does.
    """
    pico = FakePico(clock or VirtualClock(), seed=seed)
    responder = lambda line: pico.handle_line(line) + "\n"  # noqa: E731
    responder.pico = pico          # type: ignore[attr-defined]
    return responder


@dataclass
class FakeBench:
    """Everything `--dry-run` needs, wired together."""

    clock: VirtualClock
    pico: FakePico
    link: Link
    sigrok: FakeSigrok
    analyser: Analyser


def build_fake_bench(*, seed: int = 1234, log: Any = None) -> FakeBench:
    """A complete fake bench: virtual clock, fake Pico, fake analyser.

    One factory so every caller gets the *same* fake.  A dry run that used a
    different fake from the test suite would let one of them drift.
    """
    clock = VirtualClock()
    pico = FakePico(clock, seed=seed)
    link = Link(pico, timeout_s=1.0, retries=0, clock=clock, log=log,
                description="fake-pico")
    sigrok = FakeSigrok(seed=seed)
    analyser = Analyser(runner=sigrok, which=lambda _name: "/usr/bin/sigrok-cli")
    return FakeBench(clock=clock, pico=pico, link=link, sigrok=sigrok,
                     analyser=analyser)
