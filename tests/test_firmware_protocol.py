"""The firmware command parser, unit-tested on the laptop.

`firmware/src/protocol.h` declares itself HOST-PURE: no pico-sdk includes, the
parser a pure function of (line, state). This file is what cashes that promise
in. `firmware_lib` compiles protocol.c + control.c into a shared library and
drives them through ctypes, so every malformed-input path is exercised with no
Pico attached - and the compile itself is the check that the file stayed
host-pure.

**The property that matters is at the bottom: a rejected command never mutates
state.** protocol.c enforces it structurally (validate every argument into
locals, check arity, only then write to `*st`), and these tests hold it to that
for every malformed input we could think of. A half-applied command is a rover
doing something nobody asked for, and the UART is exactly where half-formed
lines come from.
"""

from __future__ import annotations

import ctypes

import pytest

from conftest import FAILSAFE_TIMEOUT_MS, RoverState, import_or_none

PROTO_MAX_LINE = 96      # protocol.h
TOK_MAX = 32             # protocol.c
OMEGA_MAX_RAD_S = 100.0  # protocol.h
TELEM_MAX_HZ = 1000      # protocol.h


# --------------------------------------------------------------------------
# Power-on state: everything that could move the motor is inert
# --------------------------------------------------------------------------


def test_power_on_duty_is_zero(rover_state):
    assert rover_state.duty_frac == 0.0


def test_power_on_stby_is_low(rover_state):
    """STBY low means the TB6612 outputs are high-Z. A Pico that resets
    mid-drive must not come back up still driving."""
    assert rover_state.stby_enabled is False


def test_power_on_pid_is_off(rover_state):
    assert rover_state.pid_enabled is False


def test_power_on_telemetry_is_off(rover_state):
    assert rover_state.telem_hz == 0


def test_power_on_failsafe_is_already_tripped(rover_state):
    """Nothing has spoken to us yet, so "no valid command in the last 300 ms"
    is literally true. It clears on the first good command."""
    assert rover_state.failsafe_tripped is True


def test_failsafe_timeout_is_in_the_architecture_document_band(rover_state):
    """ARCHITECTURE.md specifies 200-500 ms and calls the failsafe
    non-negotiable. 300 ms sits in the middle of that band."""
    assert rover_state.failsafe_timeout_us == FAILSAFE_TIMEOUT_MS * 1000
    assert 200_000 <= rover_state.failsafe_timeout_us <= 500_000


# --------------------------------------------------------------------------
# Well-formed commands
# --------------------------------------------------------------------------


def test_ping_is_accepted(rover_state):
    ok, reply = rover_state.handle("PING")
    assert ok
    assert reply.startswith("PONG")


def test_ping_reply_carries_firmware_identity(rover_state):
    """The manifest records which firmware took the data. If PING cannot say,
    no run from this board is traceable."""
    _, reply = rover_state.handle("PING")
    assert len(reply.split()) >= 3


def test_id_query_is_accepted(rover_state):
    ok, reply = rover_state.handle("ID?")
    assert ok and reply.startswith("ID ")


@pytest.mark.parametrize("duty", [0.0, 1.0, -1.0, 0.5, -0.5, 0.0001, -0.9999])
def test_set_accepts_every_legal_duty(rover_state, duty):
    ok, reply = rover_state.handle(f"SET {duty}")
    assert ok, reply
    assert rover_state.duty_frac == pytest.approx(duty, abs=1e-6)


def test_set_accepts_the_signed_format_a_host_would_send(rover_state):
    """`%+.4f` is what a host formatter naturally produces. A parser that
    chokes on a leading `+` fails only in integration, which is the expensive
    place to find out."""
    ok, _ = rover_state.handle("SET +0.4500")
    assert ok
    assert rover_state.duty_frac == pytest.approx(0.45, abs=1e-6)


def test_commands_are_case_insensitive(rover_state):
    """A human types at this port during bring-up."""
    assert rover_state.handle("ping")[0]
    assert rover_state.handle("set 0.25")[0]
    assert rover_state.duty_frac == pytest.approx(0.25, abs=1e-6)


def test_leading_and_trailing_whitespace_is_ignored(rover_state):
    assert rover_state.handle("   SET 0.25   ")[0]
    assert rover_state.duty_frac == pytest.approx(0.25, abs=1e-6)


def test_tabs_separate_arguments(rover_state):
    assert rover_state.handle("SET\t0.25")[0]


def test_a_trailing_cr_is_ignored(rover_state):
    """CRLF terminals exist and the FTDI adapter is one."""
    assert rover_state.handle("PING\r")[0]


def test_set_turns_the_pid_off(rover_state):
    """SET is open loop. If it did not disable the PID, the next control tick
    would overwrite the commanded duty and SET would silently be a no-op."""
    rover_state.handle("PIDEN 1")
    assert rover_state.pid_enabled is True
    rover_state.handle("SET 0.3")
    assert rover_state.pid_enabled is False


def test_stop_coasts(rover_state):
    rover_state.handle("SET 0.7")
    assert rover_state.handle("STOP")[0]
    assert rover_state.duty_frac == 0.0
    assert rover_state.brake is False


def test_brake_is_not_stop(rover_state):
    """Brake shorts the windings and stops hard; coast freewheels. Conflating
    them makes a deadband measurement meaningless, because the two decay
    differently."""
    rover_state.handle("SET 0.7")
    assert rover_state.handle("BRAKE")[0]
    assert rover_state.duty_frac == 0.0
    assert rover_state.brake is True


def test_stby_enables_and_disables(rover_state):
    assert rover_state.handle("STBY 1")[0]
    assert rover_state.stby_enabled is True
    assert rover_state.handle("STBY 0")[0]
    assert rover_state.stby_enabled is False


def test_stby_low_does_not_clear_the_commanded_duty(rover_state):
    """STBY is the hardware interlock, not a command. Keeping them separate is
    what makes "the driver is disabled but still commanded 0.7" a state you can
    see and reason about rather than one that silently rewrites itself."""
    rover_state.handle("SET 0.7")
    rover_state.handle("STBY 0")
    assert rover_state.duty_frac == pytest.approx(0.7, abs=1e-6)


def test_enc_query_reports_the_planted_counter(rover_state):
    rover_state.set_encoder(12345, 678901)
    ok, reply = rover_state.handle("ENC?")
    assert ok
    assert reply.split() == ["ENC", "12345", "678901"]


def test_enc_query_reports_a_negative_count(rover_state):
    """Reverse is a sign. A parser that prints an unsigned count turns every
    reverse sweep into a 4-billion-tick jump."""
    rover_state.set_encoder(-4321, 1000)
    _, reply = rover_state.handle("ENC?")
    assert reply.split()[1] == "-4321"


def test_telem_sets_the_rate(rover_state):
    assert rover_state.handle("TELEM 50")[0]
    assert rover_state.telem_hz == 50


def test_telem_zero_turns_it_off(rover_state):
    rover_state.handle("TELEM 50")
    rover_state.handle("TELEM 0")
    assert rover_state.telem_hz == 0


def test_pid_sets_all_three_gains(rover_state):
    assert rover_state.handle("PID 1.5 0.25 0.01")[0]
    kp, ki, kd = rover_state.gains
    assert (kp, ki, kd) == (pytest.approx(1.5), pytest.approx(0.25),
                            pytest.approx(0.01, abs=1e-7))


def test_changing_gains_requests_an_integrator_reset(rover_state):
    """Changing ki without clearing the accumulator rescales all the history
    that accumulated under the old gain, and the output steps."""
    rover_state.handle("PID 1 1 1")
    assert rover_state.pid_reset_requested is True


def test_setrps_sets_the_closed_loop_setpoint(rover_state):
    assert rover_state.handle("SETRPS 12.5")[0]
    assert rover_state.setpoint_omega_rad_s == pytest.approx(12.5)


def test_setrps_accepts_a_negative_setpoint(rover_state):
    assert rover_state.handle("SETRPS -12.5")[0]
    assert rover_state.setpoint_omega_rad_s == pytest.approx(-12.5)


def test_piden_off_drops_to_coast(rover_state):
    """Leaving closed loop while still commanding the last PID duty would be a
    surprise; the operator has to ask again with SET."""
    rover_state.handle("PIDEN 1")
    rover_state.handle("PIDEN 0")
    assert rover_state.duty_frac == 0.0


def test_reset_is_deferred_not_immediate(rover_state):
    """Reply first, reboot second - rebooting inside the parser eats the
    acknowledgement and the host never learns the command was accepted."""
    ok, _ = rover_state.handle("RESET")
    assert ok
    assert rover_state.reset_requested is True


def test_the_parser_itself_does_not_touch_the_diagnostic_counters(rover_state):
    """protocol.h: the counters are maintained by `protocol_reader_handle`, one
    layer up, precisely so the parser can promise a rejected line leaves the
    state BIT-FOR-BIT identical. A counter bumped inside the parser would break
    that promise for every malformed input at once."""
    before = (rover_state.ok_count, rover_state.err_count)
    rover_state.handle("PING")
    rover_state.handle("SET nan")
    assert (rover_state.ok_count, rover_state.err_count) == before


# --------------------------------------------------------------------------
# Malformed input. One id per failure mode, so a regression names itself.
# --------------------------------------------------------------------------

MALFORMED = [
    pytest.param("FLY 1", "unknown_cmd", id="unknown-command"),
    pytest.param("XYZZY", "unknown_cmd", id="unknown-command-no-args"),
    pytest.param("SE T 0.5", "unknown_cmd", id="split-verb"),
    pytest.param("", "empty", id="empty-line"),
    pytest.param("   ", "empty", id="whitespace-only"),
    pytest.param("\t", "empty", id="tab-only"),
    pytest.param("\r", "empty", id="cr-only"),
    pytest.param("PING 1", "arity", id="arity-ping-extra"),
    pytest.param("ID? x", "arity", id="arity-id-extra"),
    pytest.param("SET", "arity", id="arity-set-none"),
    pytest.param("SET 0.5 0.6", "arity", id="arity-set-two"),
    pytest.param("STOP now", "arity", id="arity-stop-extra"),
    pytest.param("BRAKE hard", "arity", id="arity-brake-extra"),
    pytest.param("STBY", "arity", id="arity-stby-none"),
    pytest.param("STBY 0 1", "arity", id="arity-stby-two"),
    pytest.param("ENC? 1", "arity", id="arity-enc-extra"),
    pytest.param("TELEM", "arity", id="arity-telem-none"),
    pytest.param("TELEM 10 20", "arity", id="arity-telem-two"),
    pytest.param("PID 1 2", "arity", id="arity-pid-two"),
    pytest.param("PID 1 2 3 4", "arity", id="arity-pid-four"),
    pytest.param("PIDEN", "arity", id="arity-piden-none"),
    pytest.param("SETRPS", "arity", id="arity-setrps-none"),
    pytest.param("RESET now", "arity", id="arity-reset-extra"),
    pytest.param("SET 1.5", "range", id="duty-over-range"),
    pytest.param("SET -1.5", "range", id="duty-under-range"),
    pytest.param("SET 1e9", "range", id="duty-absurd"),
    pytest.param("SET 45", "range", id="duty-as-a-percentage"),
    pytest.param("STBY 2", "range", id="stby-not-boolean"),
    pytest.param("PIDEN 2", "range", id="piden-not-boolean"),
    pytest.param("TELEM 10000", "range", id="telem-too-fast"),
    pytest.param("PID -1 0 0", "range", id="negative-kp"),
    pytest.param("PID 0 -1 0", "range", id="negative-ki"),
    pytest.param("PID 0 0 -1", "range", id="negative-kd"),
    pytest.param("SETRPS 1000", "range", id="omega-over-range"),
    pytest.param("SETRPS -1000", "range", id="omega-under-range"),
    pytest.param("SET nan", "not_a_number", id="duty-nan"),
    pytest.param("SET NaN", "not_a_number", id="duty-nan-mixed-case"),
    pytest.param("SET -nan", "not_a_number", id="duty-negative-nan"),
    pytest.param("SET inf", "not_a_number", id="duty-inf"),
    pytest.param("SET -inf", "not_a_number", id="duty-negative-inf"),
    pytest.param("SET infinity", "not_a_number", id="duty-infinity-spelled-out"),
    pytest.param("SET abc", "not_a_number", id="duty-not-a-number"),
    pytest.param("SET 0.5.5", "not_a_number", id="duty-two-decimal-points"),
    pytest.param("SET 0.5x", "not_a_number", id="duty-trailing-garbage"),
    pytest.param("SET .", "not_a_number", id="duty-bare-point"),
    pytest.param("STBY -1", "not_a_number", id="stby-negative"),
    pytest.param("STBY 1.0", "not_a_number", id="stby-float"),
    pytest.param("STBY +1", "not_a_number", id="stby-signed"),
    pytest.param("TELEM 1e3", "not_a_number", id="telem-scientific"),
    pytest.param("SETRPS twelve", "not_a_number", id="omega-not-a-number"),
    pytest.param("SET " + "0" * TOK_MAX, "token_too_long", id="token-too-long"),
    pytest.param("A" * (TOK_MAX + 5), "token_too_long", id="verb-token-too-long"),
    pytest.param("SET " + "0" * PROTO_MAX_LINE, "line_too_long", id="overlong-line"),
    pytest.param("A" * 5000, "line_too_long", id="absurdly-overlong-line"),
]


@pytest.mark.parametrize("line,reason", MALFORMED)
def test_malformed_line_is_rejected(rover_state, line, reason):
    ok, reply = rover_state.handle(line)
    assert not ok, f"{line!r} was accepted, reply {reply!r}"


@pytest.mark.parametrize("line,reason", MALFORMED)
def test_rejection_reply_is_an_err_line_naming_the_reason(rover_state, line, reason):
    """The reason is what a human at a terminal acts on, and what the host's
    log shows six weeks later. "ERR" alone is not a diagnosis."""
    _, reply = rover_state.handle(line)
    assert reply.startswith("ERR "), reply
    assert reply.split(None, 1)[1].strip() == reason, (
        f"{line!r} -> {reply!r}, expected reason {reason!r}"
    )


@pytest.mark.parametrize("line,reason", MALFORMED)
def test_rejection_never_produces_a_newline_in_the_reply(rover_state, line, reason):
    """The transport appends the terminator. A reply containing one would
    desynchronise the host's line reader for every subsequent command."""
    _, reply = rover_state.handle(line)
    assert "\n" not in reply and "\r" not in reply


def test_a_bare_newline_is_rejected_so_it_cannot_pet_the_watchdog(rover_state):
    """A stream of empty lines from a half-dead host must NOT count as "the Pi
    is alive". Rejecting the empty line is what keeps the failsafe honest."""
    ok, reply = rover_state.handle("")
    assert not ok
    assert reply == "ERR empty"


def test_line_exactly_at_the_limit_is_accepted(rover_state):
    """Off-by-one at a fixed buffer boundary is the classic embedded bug."""
    padding = PROTO_MAX_LINE - len("SET 0.5")
    line = "SET 0.5" + " " * padding
    assert len(line) == PROTO_MAX_LINE
    ok, reply = rover_state.handle(line)
    assert ok, reply


def test_line_one_over_the_limit_is_rejected(rover_state):
    line = "SET 0.5" + " " * (PROTO_MAX_LINE - len("SET 0.5") + 1)
    assert len(line) == PROTO_MAX_LINE + 1
    assert not rover_state.handle(line)[0]


def test_boundary_duties_are_inside_the_range(rover_state):
    assert rover_state.handle("SET 1.0")[0]
    assert rover_state.handle("SET -1.0")[0]


def test_boundary_omega_is_inside_the_range(rover_state):
    assert rover_state.handle(f"SETRPS {OMEGA_MAX_RAD_S}")[0]
    assert rover_state.handle(f"SETRPS -{OMEGA_MAX_RAD_S}")[0]


def test_boundary_telem_rate_is_inside_the_range(rover_state):
    assert rover_state.handle(f"TELEM {TELEM_MAX_HZ}")[0]


def test_nan_is_rejected_even_though_it_parses_as_a_float(rover_state):
    """`strtof("nan")` succeeds. Every subsequent range comparison against NaN
    is False, so `duty < -1 || duty > 1` lets it straight through - an explicit
    isnan test is the only thing that catches it, and a NaN reaching the
    integrator poisons it permanently."""
    ok, reply = rover_state.handle("SET nan")
    assert not ok and reply.endswith("not_a_number")


@pytest.mark.parametrize("literal", ["0x1p-3", "0X1P3", "0x10"])
def test_a_hex_float_literal_is_rejected(rover_state, literal):
    """REGRESSION. `strtof` happily parses "0x1p-3" as 0.125, so a hex literal
    arriving over the wire used to be accepted as a duty. Nothing legitimate
    sends one, and a parser that accepts spellings nobody intended is a parser
    whose behaviour nobody can state."""
    ok, _ = rover_state.handle(f"SET {literal}")
    assert not ok


# --------------------------------------------------------------------------
# The property that matters: rejection is inert
# --------------------------------------------------------------------------


def primed(state: RoverState) -> RoverState:
    """A state with something to lose, so a mutation is visible."""
    for line in ("STBY 1", "SET 0.4", "PID 1.5 0.25 0.01", "TELEM 50",
                 "SETRPS 7.5", "PIDEN 1"):
        ok, reply = state.handle(line)
        assert ok, f"priming command {line!r} was rejected: {reply}"
    return state


def test_a_valid_command_does_mutate_state(rover_state):
    """Control for the test below. Without it the property could be vacuous."""
    before = primed(rover_state).snapshot_stable()
    rover_state.handle("SET -0.9")
    assert rover_state.snapshot_stable() != before


@pytest.mark.parametrize("line,reason", MALFORMED)
def test_a_rejected_command_leaves_the_state_bit_for_bit_unchanged(rover_state, line,
                                                                   reason):
    """protocol.h states this as an absolute: "memcmp() the struct either side
    of a rejected line and it matches". So this compares the raw bytes of the
    whole struct, not the handful of fields someone remembered - including the
    fields nobody thought about."""
    before = primed(rover_state).snapshot()
    ok, _ = rover_state.handle(line)
    assert not ok
    assert rover_state.snapshot() == before, f"{line!r} mutated state"


@pytest.mark.parametrize("line,reason", MALFORMED)
def test_a_rejected_command_leaves_every_commandable_field_alone(rover_state, line,
                                                                 reason):
    """The same property read field by field, so a failure names what moved
    instead of just saying "76 bytes differ"."""
    before = primed(rover_state).snapshot_stable()
    rover_state.handle(line)
    assert rover_state.snapshot_stable() == before


def test_a_rejected_command_does_not_disable_the_driver(rover_state):
    """A bad line must not silently coast the motors either: that is a mutation
    with a friendly face, and it makes behaviour depend on line noise."""
    primed(rover_state)
    rover_state.handle("SET 9.9")
    assert rover_state.stby_enabled is True
    assert rover_state.duty_frac == pytest.approx(0.4, abs=1e-6)


def test_a_rejected_command_does_not_partially_apply_a_multi_argument_command(rover_state):
    """PID takes three gains. A parser that writes kp before validating kd
    leaves the controller running on a mixture of old and new tuning - and the
    step response you then plot belongs to neither."""
    primed(rover_state)
    before = rover_state.gains
    ok, _ = rover_state.handle("PID 2.0 3.0 -1.0")
    assert not ok
    assert rover_state.gains == before


# --------------------------------------------------------------------------
# The byte-stream reader: the two failures a line-at-a-time parser cannot see
# --------------------------------------------------------------------------


class Reader:
    """Thin wrapper over `struct proto_reader`."""

    def __init__(self, lib):
        self.lib = lib
        self.buf = ctypes.create_string_buffer(lib.shim_reader_size())
        lib.shim_reader_init(self.buf)

    def push(self, byte: bytes) -> bool:
        return bool(self.lib.shim_reader_push(self.buf, byte))

    def feed(self, data: bytes) -> list[int]:
        """Push every byte; return the indices at which a line completed."""
        return [i for i, b in enumerate(data)
                if self.push(bytes([b]))]

    def handle(self, state: RoverState) -> tuple[bool, str]:
        out = ctypes.create_string_buffer(256)
        rc = self.lib.shim_reader_handle(self.buf, out, 256, state.buf)
        return bool(rc), out.value.decode("utf-8", "replace")


@pytest.fixture
def reader(firmware_lib) -> Reader:
    return Reader(firmware_lib)


def test_a_line_is_complete_only_at_the_newline(reader):
    assert reader.feed(b"SET 0.5") == []


def test_missing_terminator_never_completes_a_line(reader, rover_state):
    """An unterminated line is an incomplete frame, not a command. That
    distinction is what lets the failsafe tell "the host went quiet" from "the
    host is mid-sentence"."""
    reader.feed(b"SET 0.9")
    assert rover_state.duty_frac == 0.0


def test_the_newline_completes_the_line(reader):
    assert reader.feed(b"SET 0.5\n") == [7]


def test_a_completed_line_is_handled(reader, rover_state):
    reader.feed(b"SET 0.5\n")
    ok, _ = reader.handle(rover_state)
    assert ok
    assert rover_state.duty_frac == pytest.approx(0.5, abs=1e-6)


def test_crlf_is_handled(reader, rover_state):
    reader.feed(b"SET 0.5\r\n")
    ok, _ = reader.handle(rover_state)
    assert ok


def test_an_embedded_nul_rejects_the_whole_line(reader, rover_state):
    """A NUL terminates a C string early, silently turning "SET 0.9\\0junk"
    into "SET 0.9". Acting on the prefix is worse than refusing the line."""
    reader.feed(b"SET 0.9\x00 junk\n")
    ok, reply = reader.handle(rover_state)
    assert not ok
    assert reply == "ERR embedded_nul"


def test_an_embedded_nul_does_not_apply_the_prefix(reader, rover_state):
    reader.feed(b"SET 0.9\x00\n")
    reader.handle(rover_state)
    assert rover_state.duty_frac == 0.0


def test_an_overlong_line_is_rejected_at_the_reader(reader, rover_state):
    reader.feed(b"SET " + b"0" * 200 + b"\n")
    ok, reply = reader.handle(rover_state)
    assert not ok
    assert reply == "ERR line_too_long"


def test_the_tail_of_an_overlong_line_is_not_parsed_as_a_new_command(reader, rover_state):
    """Otherwise a burst of line noise ending in "...STBY 1" enables the
    driver."""
    reader.feed(b"X" * 200 + b" STBY 1\n")
    reader.handle(rover_state)
    assert rover_state.stby_enabled is False


def test_the_reader_recovers_after_a_bad_line(reader, rover_state):
    """One corrupt frame must not wedge the link forever."""
    reader.feed(b"X" * 200 + b"\n")
    reader.handle(rover_state)
    reader.feed(b"SET 0.25\n")
    ok, _ = reader.handle(rover_state)
    assert ok
    assert rover_state.duty_frac == pytest.approx(0.25, abs=1e-6)


def test_the_reader_counts_accepted_commands(reader, rover_state):
    """`ok_count` is what a health check reads."""
    before = rover_state.ok_count
    for line in (b"PING\n", b"STOP\n"):
        reader.feed(line)
        reader.handle(rover_state)
    assert rover_state.ok_count == before + 2


def test_the_reader_counts_rejected_commands(reader, rover_state):
    """...and a board that refuses everything must not look perfectly healthy."""
    before = rover_state.err_count
    for line in (b"FLY\n", b"SET nan\n", b"\n"):
        reader.feed(line)
        reader.handle(rover_state)
    assert rover_state.err_count == before + 3
    assert rover_state.ok_count == 0


def test_the_reader_counts_a_rejected_line_it_caught_itself(reader, rover_state):
    """An overlong line never reaches the parser, so if the reader did not
    count it, the two failure modes the parser structurally cannot see would
    also be invisible to the health check."""
    before = rover_state.err_count
    reader.feed(b"X" * 200 + b"\n")
    reader.handle(rover_state)
    assert rover_state.err_count == before + 1


def test_two_commands_in_one_burst_are_handled_separately(reader, rover_state):
    for chunk in (b"STBY 1\n", b"SET 0.5\n"):
        reader.feed(chunk)
        assert reader.handle(rover_state)[0]
    assert rover_state.stby_enabled is True
    assert rover_state.duty_frac == pytest.approx(0.5, abs=1e-6)


# --------------------------------------------------------------------------
# Telemetry framing
# --------------------------------------------------------------------------


def format_telem(lib, t_us: int, count: int, duty: float) -> str:
    out = ctypes.create_string_buffer(128)
    lib.shim_format_telem(out, 128, t_us, count, duty)
    return out.value.decode()


def test_telemetry_record_round_trips(firmware_lib):
    """The wire format is pinned here rather than by whatever the host parser
    happened to accept on the day."""
    text = format_telem(firmware_lib, 8123456, -10432, -0.4500)
    tag, t_us, count, duty = text.split()
    assert tag == "T"
    assert int(t_us) == 8123456
    assert int(count) == -10432
    assert float(duty) == pytest.approx(-0.45, abs=1e-4)


def test_telemetry_never_emits_nan(firmware_lib):
    """A NaN in a CSV column poisons every downstream statistic silently -
    mean, stdev and every plot axis."""
    text = format_telem(firmware_lib, 1, 1, float("nan"))
    assert "nan" not in text.lower()
    assert float(text.split()[3]) == 0.0


def test_telemetry_never_emits_inf(firmware_lib):
    text = format_telem(firmware_lib, 1, 1, float("inf"))
    assert "inf" not in text.lower()


@pytest.mark.parametrize("telem_hz,loop_hz,expected", [
    (0, 100, 0),        # off
    (100, 0, 0),        # no loop, no telemetry
    (100, 100, 1),      # every tick
    (1000, 100, 1),     # cannot emit faster than the loop runs
    (50, 100, 2),
    (10, 100, 10),
    (1, 100, 100),
])
def test_telemetry_divider(firmware_lib, telem_hz, loop_hz, expected):
    assert firmware_lib.shim_telem_divider(telem_hz, loop_hz) == expected


# --------------------------------------------------------------------------
# Cross-stream contract
# --------------------------------------------------------------------------


def test_the_host_link_speaks_a_dialect_the_firmware_accepts(rover_state):
    """THE CROSS-STREAM CONTRACT, and the test that would have caught the drift.

    The host builds command strings in `tools/rover_bench/link.py`; the firmware
    parses them in `firmware/src/protocol.c`. Nothing else compares the two, and
    for a while they disagreed - the host sent `DUTY <motor> <duty>` and expected
    `OK <TAG> k=v`, while the firmware took `SET <duty>` on a single-motor bench
    and answered a bare `OK`. That kind of drift is invisible until a bench
    session, where it looks like a dead board.

    So: drive every host wrapper against a silent transport, take the bytes it
    wrote, and feed them to the real parser.
    """
    link = import_or_none("rover_bench.link")
    if link is None:
        pytest.skip("rover_bench.link not present")
    from conftest import FakeTransport

    transport = FakeTransport()
    conn = link.Link(transport, retries=0, timeout_s=0.0)
    for call in (conn.ping,
                 conn.firmware_identity,
                 conn.enable,
                 conn.disable,
                 lambda: conn.set_duty(0, 0.45),
                 lambda: conn.set_duty(0, -0.45),
                 conn.stop,
                 lambda: conn.brake(0),
                 lambda: conn.coast(0),
                 lambda: conn.read_encoder(0),
                 lambda: conn.set_pid(0, 1.0, 0.5, 0.01),
                 lambda: conn.set_pid_enabled(0, True),
                 lambda: conn.set_target_omega_rad_s(0, 12.5),
                 lambda: conn.set_telemetry_hz(50)):
        try:
            call()
        except link.LinkError:
            pass  # a silent fake never answers; we only want the written bytes

    assert transport.lines_written, "no host wrapper wrote anything"
    rejected = []
    for line in transport.lines_written:
        ok, reply = rover_state.handle(line)
        if not ok:
            rejected.append(f"{line!r} -> {reply}")
    assert not rejected, "the firmware rejects host-generated commands:\n  " + \
        "\n  ".join(rejected)


def test_the_firmware_replies_are_shaped_the_way_the_host_parses_them(rover_state):
    """The other direction of the same contract.

    The firmware answers a query with a named payload line (`PONG`, `ENC`, `ID`)
    and a command with a bare `OK`. `link.parse_status_line` has to read all
    three as success and only `ERR` as refusal - a host that treated `PONG` as
    an unrecognised line would report a healthy board as broken.
    """
    link = import_or_none("rover_bench.link")
    if link is None:
        pytest.skip("rover_bench.link not present")

    for command in ("PING", "ID?", "ENC?", "STOP", "STBY 1", "SET +0.4500"):
        accepted, reply = rover_state.handle(command)
        assert accepted, f"firmware refused {command!r}: {reply}"
        parsed = link.parse_status_line(command, reply)
        assert parsed.ok is True, f"host read {reply!r} as a failure"

    accepted, reply = rover_state.handle("FLY")
    assert not accepted
    with pytest.raises(link.DeviceError):
        link.parse_status_line("FLY", reply)
