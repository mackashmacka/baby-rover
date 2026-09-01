"""link.py — the serial link to the Pico.

WHY A TRANSPORT OBJECT INSTEAD OF pyserial CALLS EVERYWHERE
-----------------------------------------------------------
Every bug in a bench rig eventually looks like "the serial port did something
weird".  If `serial.Serial(...)` is constructed in fifteen places, you cannot
test any of them without a Pico plugged in, and you cannot reproduce a failure
without recreating the electrical conditions that caused it.

So there is exactly one seam: `Link` takes a *transport* — anything with
`write`/`readline`/`close`.  The real one wraps pyserial.  The fake one
(`fakes.FakePico`) simulates a motor.  Tests and `--dry-run` use the fake and
exercise the identical code path.  This is dependency injection, and it is the
single reason the test suite can hit its coverage gate with no hardware.

THE LINE PROTOCOL
-----------------
ASCII, newline-terminated, request/response, **one line in, one line out**.
The vocabulary is `firmware/src/protocol.c`'s, not this module's invention —
the two were reconciled once the firmware landed, and the constants below are
the single place to change if it moves again.

    > PING            < PONG 0.3.1 a1b2c3d
    > STBY 1          < OK
    > SET +0.4500     < OK
    > ENC?            < ENC 10432 8123456
    > PIDEN 1         < OK
    > SETRPS 12.5     < OK
    > (anything bad)  < ERR range

A reply is one line.  `ERR <reason>` is a refusal; anything else is the answer,
which may be a bare `OK` or a payload line whose first word names it (`PONG`,
`ENC`, `ID`).  There is no universal `OK` terminator, so the reader takes
exactly one line rather than scanning for one — scanning would hang waiting for
a terminator after `PONG` that is never sent.

**ONE MOTOR AT A TIME.**  The bench firmware drives the single motor under
test: `SET` takes a duty and no channel index, matching the bench pin map.  The
`motor` argument on these wrappers therefore says *which motor is physically on
the bench* — for the manifest and the output path — and does not select a
channel.  Characterising four motors means four sessions, which is also the
only way to get four comparable data sets out of one set of probe clips.

Why ASCII and not a binary framed protocol: this link is for the *bench*, not
for the rover.  The rover's Pi<->Pico link (Story 2.1) is framed and
checksummed because a moving robot must detect corruption.  The bench link runs
over USB CDC on a desk and is read by a human on a logic analyser.  Boring wins.

.. note::
   `SETRPS` takes **rad/s of the output shaft**, so commanding a closed-loop
   setpoint needs `ticks_per_rev` — which is disputed and unmeasured.  The host
   refuses to convert a ticks/s target until it has been measured, rather than
   quietly picking 1100 or 1400 and producing a plausible wrong number.

WHY `Reply` IS A `str` SUBCLASS
-------------------------------
`command()` returns the reply line, so `link.command("PING")` compares equal to
the string the board sent — the simplest possible thing to assert.  It *also*
carries the parsed words and any `key=value` fields, so a caller does not
re-split the line at every site.  One object, two ways to read it.

CLOSING STOPS THE MOTORS
------------------------
`close()` sends `STOP` before it closes the port, and the context manager does
the same on the way out — including when the body raised.  The firmware
failsafe is the real backstop, but this is the host's half of it, and it is
what stands between a `KeyboardInterrupt` mid-ramp and a motor that keeps
spinning on a bench with a loose harness.

RETRIES
-------
Only *timeouts* are retried.  An `ERR` reply is a deterministic answer from a
board that is alive and understood you; retrying it just produces the same
error more slowly and can double-apply a command.  A timeout might be a dropped
USB frame, so one resend is reasonable.
"""

from __future__ import annotations

import glob
import os
import time
from dataclasses import dataclass
from typing import Callable, Protocol, Sequence

# --------------------------------------------------------------------------
# Protocol constants.  Change these here and nowhere else if the firmware
# stream lands a different vocabulary.
# --------------------------------------------------------------------------

TERMINATOR = "\n"
OK_PREFIX = "OK"
ERR_PREFIX = "ERR"

#: firmware/src/protocol.c's vocabulary.  Reconciled with the firmware once it
#: landed; this is the single place to change if it moves again.
CMD_PING = "PING"          # -> PONG <fw_version> <git_sha>
CMD_ID = "ID?"             # -> ID <board> <fw_version>
CMD_STBY = "STBY"          # STBY <0|1>       — TB6612 hardware enable
CMD_SET = "SET"            # SET <duty_frac>  — open loop; also turns the PID off
CMD_STOP = "STOP"          # STOP             — duty 0, PID off.  This is COAST.
CMD_BRAKE = "BRAKE"        # BRAKE            — windings shorted; stops hard
CMD_ENC = "ENC?"           # -> ENC <count> <t_us>
CMD_PID = "PID"            # PID <kp> <ki> <kd>
CMD_PID_ENABLE = "PIDEN"   # PIDEN <0|1>      — closed loop on/off
CMD_SETRPS = "SETRPS"      # SETRPS <omega_rad_s>   — NOTE the unit
CMD_TELEM = "TELEM"        # TELEM <hz>       — streaming telemetry
CMD_RESET = "RESET"

REPLY_PONG = "PONG"
REPLY_ENC = "ENC"
REPLY_ID = "ID"

#: First words that END a reply.  Anything else the board sends between a
#: command and its reply is an unsolicited line — `TELEM` streams `T <t_us>
#: <ticks> <duty>` rows — and is collected rather than mistaken for the answer.
#: Collecting instead of ignoring matters: those rows are data.
TERMINAL_TAGS: frozenset[str] = frozenset({
    OK_PREFIX, ERR_PREFIX, REPLY_PONG, REPLY_ENC, REPLY_ID,
})

#: firmware/src/protocol.h.  A setpoint outside this is refused by the board,
#: so the host checks it first and says why.
OMEGA_MAX_RAD_S = 100.0

DEFAULT_PORT_SYMLINK = "/dev/rover-pico"   # created by tools/bench-setup.sh udev rule
FALLBACK_PORT_GLOB = "/dev/ttyACM*"        # RP2350 native USB CDC
DEFAULT_BAUD = 115200                      # fixed by docs/WIRING.md §7, not a knob
DEFAULT_TIMEOUT_S = 1.0
DEFAULT_RETRIES = 2

#: Longest line this tool will put on the wire.  This is `PROTO_MAX_LINE` from
#: firmware/src/protocol.h, not a round number: the board refuses anything
#: longer with `ERR line_too_long`, and the point of checking here is to catch
#: the bad frame before it costs a round trip on a 115200 link.  A limit *above*
#: the firmware's would let 97-256 char lines through to be refused at the far
#: end, which is the one thing this constant exists to prevent.
MAX_LINE_CHARS = 96


# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------

class LinkError(RuntimeError):
    """Base class for every failure of the Pico link."""


class LinkTimeout(LinkError):
    """The Pico did not produce a terminal line before the deadline."""


class DeviceError(LinkError):
    """The Pico replied `ERR` — it is alive and it is refusing.

    Carries the command so a traceback names the thing that was refused, not
    just the reason.
    """

    def __init__(self, command: str, code: str, detail: str) -> None:
        super().__init__(f"{command!r} -> ERR {code} {detail}".rstrip())
        self.command = command
        self.code = code
        self.detail = detail


class PortNotFound(LinkError):
    """No `/dev/rover-pico` and no `/dev/ttyACM*`.  Run `rover-bench doctor`."""


# --------------------------------------------------------------------------
# Clock — injected so tests and --dry-run run instantly and deterministically
# --------------------------------------------------------------------------

class Clock(Protocol):
    """Just enough of `time` to be faked.

    Experiments dwell for hundreds of milliseconds at a time.  With a real
    clock a full sweep takes minutes; with a virtual one the same code path
    runs in milliseconds, which is what makes `--dry-run` usable as a
    pre-flight self-check rather than something nobody waits for.
    """

    def now(self) -> float: ...
    def sleep(self, seconds: float) -> None: ...


class RealClock:
    """Wall clock.  `monotonic` because we only ever measure *intervals*, and
    monotonic cannot jump backwards when NTP steps the system clock."""

    def now(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            time.sleep(seconds)


# --------------------------------------------------------------------------
# Transport seam
# --------------------------------------------------------------------------

class Transport(Protocol):
    """The whole surface `Link` needs from a serial port.

    Deliberately tiny.  Anything bigger and the fake stops being obviously
    equivalent to the real thing.
    """

    def write(self, data: bytes) -> int: ...
    def readline(self) -> bytes: ...
    def close(self) -> None: ...


class SerialTransport:
    """pyserial-backed transport.

    pyserial is imported *inside* the constructor, not at module import, so
    `rover-bench doctor` still runs on a machine where nothing is installed —
    which is doctor's entire reason to exist.
    """

    def __init__(self, port: str, baud: int = DEFAULT_BAUD,
                 timeout_s: float = DEFAULT_TIMEOUT_S) -> None:
        try:
            import serial  # noqa: PLC0415 - lazy on purpose, see docstring
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise LinkError(
                "pyserial is not installed.  Fix:  pip install pyserial   "
                "(or run tools/bench-setup.sh)"
            ) from exc
        self.port = port
        self.baud = baud
        self.is_open = True
        self._serial = serial.Serial(port=port, baudrate=baud, timeout=timeout_s)

    def write(self, data: bytes) -> int:
        return int(self._serial.write(data) or 0)

    def readline(self) -> bytes:
        return bytes(self._serial.readline())

    def reset_input_buffer(self) -> None:
        self._serial.reset_input_buffer()

    def close(self) -> None:
        self.is_open = False
        self._serial.close()


# --------------------------------------------------------------------------
# Reply parsing (pure functions — the easiest thing in the package to test)
# --------------------------------------------------------------------------

def parse_fields(text: str) -> dict[str, str]:
    """Pull `key=value` tokens out of a status line.

    Tokens without an `=` are ignored here (they are picked up as `words`).
    Values keep their raw string form; callers ask for the type they want, so
    a malformed number fails at the point that cares about it.
    """
    fields: dict[str, str] = {}
    for token in text.split():
        if "=" in token:
            key, _, value = token.partition("=")
            if key:
                fields[key] = value
    return fields


class Reply(str):
    """The Pico's status line, with its parsed fields attached.

    It *is* the string, so `command("PING") == "OK PING"`.  See the module
    docstring for why that is worth a str subclass.
    """

    command: str
    ok: bool
    tag: str
    words: tuple[str, ...]
    fields: dict[str, str]
    lines: tuple[str, ...]

    def __new__(cls, status_line: str, *, command: str = "",
                data_lines: Sequence[str] = ()) -> "Reply":
        obj = super().__new__(cls, status_line)
        words = tuple(status_line.split())
        obj.command = command
        # Anything that is not a refusal is an answer: the firmware replies
        # with a bare `OK` for a command and with a named payload line
        # (`PONG`, `ENC`, `ID`) for a query.  Only `ERR` means no.
        obj.ok = bool(words) and words[0].upper() != ERR_PREFIX
        # The tag names the reply.  A payload line names itself in its first
        # word (`PONG`, `ENC`, `ID`); a bare acknowledgement is `OK`, and a
        # board that echoes what it acknowledged (`OK ENC ...`) puts the name
        # second.  Skip the leading OK so `tag` means the same thing either way.
        obj.tag = ""
        if words:
            obj.tag = words[0].upper()
            if obj.tag == OK_PREFIX and len(words) > 1 and "=" not in words[1]:
                obj.tag = words[1].upper()
        obj.words = words
        obj.fields = parse_fields(status_line)
        obj.lines = tuple(data_lines)
        return obj

    # -- typed field access ---------------------------------------------

    def get(self, key: str, default: str | None = None) -> str | None:
        return self.fields.get(key, default)

    def require(self, key: str) -> str:
        if key not in self.fields:
            raise LinkError(
                f"{self.command!r} reply is missing field {key!r}: {str(self)!r}"
            )
        return self.fields[key]

    def as_float(self, key: str) -> float:
        raw = self.require(key)
        try:
            return float(raw)
        except ValueError as exc:
            raise LinkError(f"{self.command!r} field {key}={raw!r} is not a number") from exc

    def as_int(self, key: str) -> int:
        raw = self.require(key)
        try:
            return int(raw, 0)
        except ValueError as exc:
            raise LinkError(f"{self.command!r} field {key}={raw!r} is not an integer") from exc


def parse_status_line(command: str, line: str,
                      data_lines: Sequence[str] = ()) -> Reply:
    """Turn a terminal line into a `Reply`, or raise `DeviceError` on `ERR`."""
    stripped = line.strip()
    words = stripped.split()
    if not words:
        raise LinkError(f"{command!r}: empty status line")
    if words[0].upper() == ERR_PREFIX:
        code = words[1] if len(words) > 1 else "?"
        raise DeviceError(command, code, " ".join(words[2:]))
    return Reply(stripped, command=command, data_lines=data_lines)


def validate_outgoing(line: str) -> str:
    """Reject a line that is already known to be bad, before it is written.

    The firmware's parser is the real trust boundary, but sending a frame you
    already know is malformed wastes a debugging session at the far end of a
    115200 link.  A single trailing terminator is stripped rather than
    rejected, so `send("PING\\n")` and `send("PING")` mean the same thing.
    """
    if not isinstance(line, str):
        raise ValueError(f"command must be a string, got {type(line).__name__}")
    text = line.rstrip("\r\n")
    if not text.strip():
        raise ValueError("refusing to send an empty command")
    if "\n" in text or "\r" in text:
        raise ValueError(f"command must be a single line: {line!r}")
    if "\x00" in text:
        raise ValueError(f"command contains a NUL byte: {line!r}")
    if len(text) > MAX_LINE_CHARS:
        raise ValueError(
            f"command is {len(text)} chars, limit is {MAX_LINE_CHARS}: {text[:40]!r}..."
        )
    text.encode("ascii", "strict")   # raises UnicodeEncodeError on non-ASCII
    return text


@dataclass(frozen=True)
class EncoderSample:
    """A tick count with the *firmware's* timestamp beside it.

    WHY the firmware timestamp and not `time.monotonic()` on the host: USB CDC
    latency is tens of microseconds to tens of milliseconds and it is not
    constant.  Dividing tick deltas by host time smears that jitter straight
    into every speed number.  The Pico knows exactly when it read the counter;
    the host does not.  Always divide by `t_s`.
    """

    ticks: int
    t_s: float

    def rate_ticks_per_s(self, earlier: "EncoderSample") -> float:
        dt = self.t_s - earlier.t_s
        if dt <= 0:
            raise LinkError(
                f"encoder timestamps did not advance ({earlier.t_s} -> {self.t_s}); "
                "the firmware clock is stuck or the samples are out of order"
            )
        return (self.ticks - earlier.ticks) / dt


# --------------------------------------------------------------------------
# The link itself
# --------------------------------------------------------------------------

class Link:
    """Request/response conversation with the Pico.

    Not thread safe, on purpose: one command in flight at a time is the whole
    protocol.  If you want concurrency here you have a different problem.
    """

    def __init__(self, transport: Transport, *,
                 timeout_s: float = DEFAULT_TIMEOUT_S,
                 retries: int = DEFAULT_RETRIES,
                 clock: Clock | None = None,
                 log: Callable[[str], None] | None = None,
                 description: str = "pico") -> None:
        self.transport = transport
        self.timeout_s = timeout_s
        self.retries = retries
        self.clock: Clock = clock or RealClock()
        self.log = log or (lambda _msg: None)
        self.description = description
        self.closed = False

    # -- raw framing -----------------------------------------------------

    def send(self, line: str) -> None:
        """Validate, then write exactly one terminated frame."""
        if self.closed:
            raise LinkError("link is closed")
        text = validate_outgoing(line)
        self.log(f"-> {text}")
        self.transport.write((text + TERMINATOR).encode("ascii", "strict"))

    def read_line(self, timeout_s: float | None = None) -> str:
        """Read one line, stripped of its terminator.

        Raises `LinkTimeout` rather than blocking forever: a blocking read
        against a Pico that has crashed hangs a bench script with a motor
        possibly still turning, and nobody notices until they look up.
        """
        deadline_s = self.timeout_s if timeout_s is None else timeout_s
        started = self.clock.now()
        while True:
            raw = self.transport.readline()
            if raw:
                text = raw.decode("ascii", "replace").strip("\r\n").strip()
                if text:
                    self.log(f"<- {text}")
                    return text
            elapsed = self.clock.now() - started
            if elapsed > deadline_s or deadline_s <= 0:
                raise LinkTimeout(
                    f"no line within {deadline_s:.3f} s on {self.description}"
                )

    # -- request / response ----------------------------------------------

    def command(self, cmd: str, *, retries: int | None = None,
                timeout_s: float | None = None) -> Reply:
        """Send one command line, return its `Reply` (which is the status line).

        Raises `DeviceError` on `ERR` (not retried — see the module docstring)
        and `LinkTimeout` when no terminal line arrives.
        """
        text = validate_outgoing(cmd)
        attempts = self.retries if retries is None else retries
        deadline_s = self.timeout_s if timeout_s is None else timeout_s

        last_error: LinkError | None = None
        for attempt in range(attempts + 1):
            self._drain()
            self.send(text)
            try:
                return self._read_reply(text, deadline_s)
            except LinkTimeout as exc:
                last_error = exc
                self.log(f"!! timeout on {text!r} "
                         f"(attempt {attempt + 1}/{attempts + 1})")
        assert last_error is not None
        raise last_error

    def _drain(self) -> None:
        """Discard anything already waiting.

        Stale bytes in the RX buffer make every subsequent reply off by one,
        and the symptom is a run that reports the wrong speed for every point —
        a data set that looks fine and is entirely shifted.
        """
        reset = getattr(self.transport, "reset_input_buffer", None)
        if callable(reset):
            reset()

    def _read_reply(self, cmd: str, deadline_s: float) -> Reply:
        """Read until a line that ends a reply, keeping anything before it.

        Two shapes have to work, and getting either wrong hangs the bench:

        * A query answers with a *payload* line — `PONG ...`, `ENC ...`,
          `ID ...` — and sends no `OK` after it.  Scanning for an `OK`
          terminator would wait forever for one that is never coming.
        * `TELEM` streams `T <t_us> <ticks> <duty>` rows and then an `OK`.
          Treating the first row as the answer would return telemetry as if it
          were a status, and throw the rest away.

        So a line whose first word is a known reply tag ends the reply; every
        other line is kept in `Reply.lines`.  An unrecognised reply shape times
        out rather than being silently accepted — the firmware's vocabulary is
        in `TERMINAL_TAGS`, and a new reply belongs there.
        """
        started = self.clock.now()
        data_lines: list[str] = []
        while True:
            raw = self.transport.readline()
            if raw:
                text = raw.decode("ascii", "replace").strip()
                if text:
                    self.log(f"<- {text}")
                    if text.split(None, 1)[0].upper() in TERMINAL_TAGS:
                        return parse_status_line(cmd, text, data_lines)
                    data_lines.append(text)
                    continue
            elapsed = self.clock.now() - started
            if elapsed > deadline_s or deadline_s <= 0:
                raise LinkTimeout(
                    f"no reply to {cmd!r} within {deadline_s:.3f} s on "
                    f"{self.description}"
                    + (f" (kept {len(data_lines)} unsolicited line(s))"
                       if data_lines else "")
                )

    # -- convenience wrappers --------------------------------------------
    # These exist so experiment code reads like the experiment rather than like
    # string formatting.  They are all one-liners on purpose.

    def ping(self) -> bool:
        """Is anything alive on the other end?

        Returns a bool, not a `Reply`: "is anything there?" is a question with
        a No answer, and silence is that answer rather than an exception.  Use
        `firmware_identity()` when you need what it said.
        """
        try:
            return self.command(CMD_PING).ok
        except LinkError:
            return False

    def firmware_identity(self) -> dict[str, str | None]:
        """`{version, sha, protocol}` from PING — straight into the manifest.

        The firmware answers `PONG <fw_version> <git_sha>`, positionally.  A
        `key=value` form is also accepted, because that is what a stubbed or
        future board may send and identity is not worth failing a run over.
        Missing fields come back as None: an old firmware that answers a bare
        `OK` should still let you take data, it just cannot claim the data is
        traceable to a build.
        """
        reply = self.command(CMD_PING)
        version = reply.get("fw")
        sha = reply.get("sha")
        if reply.words and reply.words[0].upper() == REPLY_PONG and len(reply.words) >= 2:
            version = version or reply.words[1]
            sha = sha or (reply.words[2] if len(reply.words) > 2 else None)
        return {
            "firmware_version": version,
            "firmware_sha": sha,
            "protocol_version": reply.get("proto"),
        }

    def enable(self) -> Reply:
        """STBY HIGH.  Nothing moves until this happens."""
        return self.command(f"{CMD_STBY} 1")

    def disable(self) -> Reply:
        """STBY LOW — the TB6612's instant hardware all-stop.

        This is the one that works even if the PWM peripheral is misconfigured,
        which is why every guard path ends here rather than at `duty 0`.
        """
        return self.command(f"{CMD_STBY} 0")

    def set_duty(self, motor: int, duty_frac: float) -> Reply:
        """Signed duty: positive is forward, negative is reverse.

        `motor` records which motor is on the bench; it is not on the wire,
        because the bench firmware drives one motor at a time (see the module
        docstring).  Range checking lives in `safety.py`, not here — `Link` is
        a transport, and an interlock buried in a transport is one nobody can
        find.
        """
        return self.command(f"{CMD_SET} {float(duty_frac):+.4f}")

    def stop(self) -> Reply:
        """Duty 0 and PID off.  On this firmware STOP is a *coast*.

        `retries=0` because this runs on error paths, where failing fast and
        falling through to `disable()` beats retrying at a board that has
        stopped answering.
        """
        return self.command(CMD_STOP, retries=0)

    def brake(self, motor: int) -> Reply:
        """Short brake — windings shorted, stops hard.  Not the same as STOP."""
        return self.command(CMD_BRAKE)

    def coast(self, motor: int) -> Reply:
        """Freewheel.  Required before hand-turning a shaft for ticks-per-rev:
        a braked H-bridge shorts the windings and the shaft fights you."""
        return self.command(CMD_STOP)

    def read_encoder(self, motor: int) -> EncoderSample:
        """`ENC?` -> `ENC <count> <t_us>`.

        Positional, as the firmware sends it, with a `key=value` fallback so a
        differently-shaped reply does not take the bench down.
        """
        reply = self.command(CMD_ENC)
        # Keyed on the FIRST word, not on `tag`: `tag` skips a leading `OK`,
        # so `OK ENC ticks=... t_us=...` also tags as ENC — and that form is
        # key=value, not positional.  Getting this wrong turns a good reply
        # into "malformed ENC reply".
        if reply.words and reply.words[0].upper() == REPLY_ENC and len(reply.words) >= 3:
            try:
                return EncoderSample(ticks=int(reply.words[1]),
                                     t_s=int(reply.words[2]) / 1_000_000.0)
            except ValueError as exc:
                raise LinkError(f"malformed ENC reply {str(reply)!r}") from exc
        return EncoderSample(ticks=reply.as_int("ticks"),
                             t_s=reply.as_float("t_us") / 1_000_000.0)

    def set_pid(self, motor: int, kp: float, ki: float, kd: float) -> Reply:
        """Gains, in one command.  The firmware refuses a negative gain —
        negative feedback becomes positive feedback and the motor runs away."""
        return self.command(f"{CMD_PID} {kp:.6f} {ki:.6f} {kd:.6f}")

    def set_pid_enabled(self, motor: int, enabled: bool) -> Reply:
        """Closed loop on or off.

        Turning it off drops the motor to coast on this firmware, deliberately:
        leaving the last PID duty commanded after leaving closed loop would be
        a surprise, and surprises on a bench have motors attached.
        """
        return self.command(f"{CMD_PID_ENABLE} {1 if enabled else 0}")

    def set_target_omega_rad_s(self, motor: int, omega_rad_s: float) -> Reply:
        """Closed-loop setpoint, in **rad/s of the output shaft**.

        The unit is the firmware's, and it is the whole reason `ticks_per_rev`
        has to be measured before closed loop can be commanded from a ticks/s
        figure: converting needs the constant, and the published constant is
        disputed by 27%.  `experiment.step` refuses rather than guessing.

        .. warning::
           Measuring it on the host is necessary but **not sufficient**.  The
           board converts this rad/s back to ticks with *its own*
           `ticks_per_output_rev`, which today is a compile-time placeholder in
           `firmware/src/protocol.c` with no command to change it.  Nothing on
           this wire reports that constant, so the host cannot check the two
           agree; `experiment.step` records the hazard in the manifest instead.
        """
        omega = float(omega_rad_s)
        if abs(omega) > OMEGA_MAX_RAD_S:
            raise ValueError(
                f"setpoint {omega} rad/s is outside the firmware's "
                f"±{OMEGA_MAX_RAD_S} rad/s limit; it would be refused"
            )
        return self.command(f"{CMD_SETRPS} {omega:.4f}")

    def set_telemetry_hz(self, hz: int) -> Reply:
        return self.command(f"{CMD_TELEM} {int(hz)}")

    # -- shutdown --------------------------------------------------------

    def close(self) -> None:
        """Stop the motors, then close the port.  Idempotent.

        `send` rather than `command`: this runs on the exception path and on
        `__exit__`, and blocking for a reply from a board that may have just
        stopped answering would turn a clean shutdown into a hang.  The bytes
        going out is what matters.
        """
        if self.closed:
            return
        try:
            self.send(CMD_STOP)
        except Exception as exc:  # noqa: BLE001 - never mask the original error
            self.log(f"WARNING: could not send STOP while closing: {exc}")
        self.closed = True
        try:
            self.transport.close()
        except Exception as exc:  # noqa: BLE001 - same reason
            self.log(f"WARNING: closing the transport failed: {exc}")

    def __enter__(self) -> "Link":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


# --------------------------------------------------------------------------
# Port discovery
# --------------------------------------------------------------------------

def resolve_port(explicit: str | None = None, *,
                 symlink: str = DEFAULT_PORT_SYMLINK,
                 fallback_glob: str = FALLBACK_PORT_GLOB,
                 exists: Callable[[str], bool] = os.path.exists,
                 globber: Callable[[str], list[str]] = glob.glob) -> str:
    """Find the Pico's serial port.

    Order, and why:

    1. `--port` if the operator said one.  Explicit beats clever.
    2. `/dev/rover-pico` — the udev symlink from `tools/bench-setup.sh`.
       Stable across replugs; `/dev/ttyACM0` is not, and a bench that
       renumbers its instruments between runs quietly ruins a data set.
    3. `/dev/ttyACM*`, sorted, first match — so the tool still works before
       anyone has run the setup script.

    `exists` and `globber` are injected purely so tests can describe a /dev
    that does not exist on this laptop.
    """
    if explicit:
        if not exists(explicit):
            raise PortNotFound(f"{explicit} does not exist")
        return explicit
    if exists(symlink):
        return symlink
    candidates = sorted(globber(fallback_glob))
    if candidates:
        return candidates[0]
    raise PortNotFound(
        f"no {symlink} and nothing matching {fallback_glob}.  "
        "Is the Pico plugged in and running firmware (not in BOOTSEL)?  "
        "Run `rover-bench doctor`."
    )


def open_link(port: str | None = None, *,
              baud: int = DEFAULT_BAUD,
              timeout_s: float = DEFAULT_TIMEOUT_S,
              retries: int = DEFAULT_RETRIES,
              clock: Clock | None = None,
              log: Callable[[str], None] | None = None,
              transport_factory: Callable[..., Transport] | None = None,
              exists: Callable[[str], bool] = os.path.exists,
              globber: Callable[[str], list[str]] = glob.glob) -> Link:
    """Open a real link.  `transport_factory` is the seam for tests.

    `exists`/`globber` are forwarded to `resolve_port` for a reason found the
    hard way on 2026-09-01: without them, injecting a fake transport still left
    the port *existence* check hitting the real /dev.  The seam looked complete
    and was not, so `test_open_link_uses_the_injected_transport_factory` passed
    only while a Pico happened to be plugged in, and failed the moment the USB
    dock dropped.  A test whose result depends on what is on the bench is not
    testing what it claims to test.
    """
    resolved = resolve_port(port, exists=exists, globber=globber)
    factory = transport_factory or SerialTransport
    transport = factory(resolved, baud, timeout_s)
    return Link(transport, timeout_s=timeout_s, retries=retries, clock=clock,
                log=log, description=resolved)


def as_link(candidate: object, **kwargs: object) -> Link:
    """Accept either a `Link` or a bare transport and always give back a `Link`.

    Callers at the top of the package (the CLI, `experiment.run`) are handed
    whichever the caller had.  Normalising here means no downstream code has to
    ask which one it got.
    """
    if isinstance(candidate, Link):
        return candidate
    return Link(candidate, **kwargs)  # type: ignore[arg-type]
