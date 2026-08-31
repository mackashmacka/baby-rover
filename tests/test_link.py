"""The serial link to the Pico.

Everything runs against the fake transport in conftest. Nothing opens a port -
enforced by the autouse guard, not merely intended.

Two invariants earn their keep here:

* **Stale bytes are drained before every command.** One leftover line makes
  every subsequent reply off by one, and the symptom is a data set that looks
  entirely plausible and is shifted by one point throughout.
* **`stop()` does not retry.** It runs on the error path. A retrying stop
  multiplies the time a motor keeps turning after something has already gone
  wrong.
"""

from __future__ import annotations

import pytest

from conftest import UART_BAUD, FakeTransport, import_or_none, pico_responder

link = import_or_none("rover_bench.link")

pytestmark = pytest.mark.xfail(
    link is None,
    reason="rover_bench.link has not landed yet; these activate when it does",
    strict=False,
)


def a_link(transport, clock=None, **kw):
    kwargs = dict(retries=0, timeout_s=1.0, clock=clock)
    kwargs.update(kw)
    return link.Link(transport, **kwargs)


# --------------------------------------------------------------------------
# Outgoing framing
# --------------------------------------------------------------------------


def test_send_terminates_the_line(fake_transport, frozen_clock):
    a_link(fake_transport, frozen_clock).send("PING")
    assert fake_transport.writes[-1] == b"PING\n"


def test_send_does_not_double_terminate(fake_transport, frozen_clock):
    a_link(fake_transport, frozen_clock).send("PING\n")
    assert fake_transport.writes[-1] == b"PING\n"


def test_send_writes_exactly_one_frame(fake_transport, frozen_clock):
    a_link(fake_transport, frozen_clock).send("SET +0.5000")
    assert fake_transport.lines_written == ["SET +0.5000"]


def test_send_writes_bytes_not_text(fake_transport, frozen_clock):
    a_link(fake_transport, frozen_clock).send("PING")
    assert isinstance(fake_transport.writes[-1], (bytes, bytearray))


def test_sending_on_a_closed_link_raises(fake_transport, frozen_clock):
    conn = a_link(fake_transport, frozen_clock)
    conn.close()
    with pytest.raises(link.LinkError):
        conn.send("PING")


@pytest.mark.parametrize("bad", [
    pytest.param("SET 0.5\nSTOP", id="embedded-newline"),
    pytest.param("SET 0.5\rSTOP", id="embedded-cr"),
    pytest.param("SET \x00", id="embedded-nul"),
    pytest.param("D" * 5000, id="overlong"),
    pytest.param("SET µ", id="non-ascii"),
    pytest.param("", id="empty"),
    pytest.param("   ", id="whitespace-only"),
    pytest.param(b"PING", id="bytes-not-str"),
    pytest.param(None, id="none"),
])
def test_a_malformed_outgoing_line_is_refused_before_it_is_written(fake_transport,
                                                                   frozen_clock, bad):
    """The firmware's parser is the real trust boundary, but sending a frame
    you already know is bad wastes a debugging session at the far end of a
    115200 link."""
    with pytest.raises((link.LinkError, ValueError, UnicodeEncodeError)):
        a_link(fake_transport, frozen_clock).send(bad)
    assert fake_transport.writes == []


def test_validate_outgoing_strips_a_single_terminator():
    assert link.validate_outgoing("PING\r\n") == "PING"


def test_the_outgoing_length_limit_is_declared():
    assert isinstance(link.MAX_LINE_CHARS, int) and link.MAX_LINE_CHARS > 0


# --------------------------------------------------------------------------
# Incoming framing
# --------------------------------------------------------------------------


def test_read_line_strips_the_terminator(fake_transport, frozen_clock):
    fake_transport.queue("OK PING\n")
    assert a_link(fake_transport, frozen_clock).read_line() == "OK PING"


def test_read_line_handles_crlf(fake_transport, frozen_clock):
    fake_transport.queue("OK PING\r\n")
    assert a_link(fake_transport, frozen_clock).read_line() == "OK PING"


def test_read_line_against_a_silent_port_times_out(fake_transport, frozen_clock):
    """A blocking read against a crashed Pico hangs a bench script with a motor
    possibly still turning, and nobody notices until they look up."""
    with pytest.raises(link.LinkTimeout):
        a_link(fake_transport, frozen_clock, timeout_s=0.0).read_line()


def test_a_timeout_is_a_link_error(fake_transport, frozen_clock):
    """So one `except LinkError` at a call site covers every failure mode."""
    assert issubclass(link.LinkTimeout, link.LinkError)


# --------------------------------------------------------------------------
# Reply parsing
# --------------------------------------------------------------------------


def test_a_reply_is_the_status_line():
    assert link.parse_status_line("PING", "PONG 0.1.0 deadbee") == "PONG 0.1.0 deadbee"


def test_a_reply_exposes_its_key_value_fields():
    reply = link.parse_status_line("PING", "OK PING fw=0.1.0 sha=deadbee proto=1")
    assert reply.get("fw") == "0.1.0"
    assert reply.get("sha") == "deadbee"


def test_a_reply_tag_is_the_same_whether_or_not_the_board_echoes_ok():
    """`ENC 10 20` and `OK ENC ticks=10` both tag as ENC, so a caller keyed on
    the tag works against either shape."""
    assert link.parse_status_line("ENC?", "ENC 10 20").tag == "ENC"
    assert link.parse_status_line("ENC?", "OK ENC ticks=10").tag == "ENC"


def test_a_missing_field_raises_rather_than_returning_none():
    """`require` is for the fields an experiment cannot proceed without, and
    silently getting None there produces a TypeError three frames later."""
    reply = link.parse_status_line("ENC?", "OK ENC")
    with pytest.raises(link.LinkError):
        reply.require("ticks")


def test_a_non_numeric_field_raises_where_it_is_read():
    reply = link.parse_status_line("ENC?", "OK ENC ticks=lots")
    with pytest.raises(link.LinkError):
        reply.as_int("ticks")


def test_an_err_reply_raises_a_device_error():
    """The board is alive and refusing. That is a different problem from
    silence, and it needs a different response from the caller."""
    with pytest.raises(link.DeviceError):
        link.parse_status_line("FLY", "ERR unknown_cmd FLY")


def test_the_device_error_carries_the_command_and_the_code():
    with pytest.raises(link.DeviceError) as excinfo:
        link.parse_status_line("SET", "ERR range duty out of range")
    assert excinfo.value.command == "SET"
    assert excinfo.value.code == "range"


def test_an_empty_status_line_raises():
    with pytest.raises(link.LinkError):
        link.parse_status_line("PING", "   ")


def test_a_named_payload_line_is_an_answer_not_a_refusal():
    """The firmware answers a query with a named payload (`PONG`, `ENC`, `ID`)
    and a command with a bare `OK`. Only `ERR` means no."""
    reply = link.parse_status_line("PING", "PONG 0.1.0 deadbee")
    assert reply.ok is True
    assert reply.tag == "PONG"


def test_a_query_payload_is_the_reply_and_the_link_does_not_wait_for_an_ok(
        fake_pico, frozen_clock):
    """The firmware answers a query with a payload line (`PONG`, `ENC`, `ID`)
    and sends no `OK` after it. A link that scanned on until it saw an `OK`
    would wait for a terminator that is never coming - i.e. hang the bench on
    the very first PING."""
    reply = a_link(fake_pico, frozen_clock).command("ENC?")
    assert reply.tag == "ENC"
    assert reply.ok is True


def test_one_command_consumes_exactly_one_line(fake_transport, frozen_clock):
    """Streamed telemetry lines are not swallowed by whatever command happens
    to be in flight; they stay in the buffer to be read as telemetry."""
    fake_transport.set_responder(lambda line: "OK\nT 1000 10 0.5000\n")
    conn = a_link(fake_transport, frozen_clock)
    assert conn.command("TELEM 100") == "OK"
    assert conn.read_line().startswith("T 1000")


# --------------------------------------------------------------------------
# Request / response
# --------------------------------------------------------------------------


def test_command_returns_the_reply(fake_pico, frozen_clock):
    """A `Reply` IS its status line, so the common case reads as a string
    comparison and the parsed fields are there when you need them."""
    assert a_link(fake_pico, frozen_clock).command("STOP") == "OK"


def test_command_sends_the_line(fake_pico, frozen_clock):
    a_link(fake_pico, frozen_clock).command("SET +0.2500")
    assert fake_pico.lines_written == ["SET +0.2500"]


def test_an_error_reply_is_not_retried(fake_pico, frozen_clock):
    """An ERR is a deterministic answer from a board that understood you.
    Retrying produces the same error more slowly and can double-apply."""
    with pytest.raises(link.DeviceError):
        a_link(fake_pico, frozen_clock, retries=3).command("FLY")
    assert fake_pico.lines_written == ["FLY"]


def test_a_timeout_is_retried(fake_transport, frozen_clock):
    """A dropped USB frame is worth one resend."""
    with pytest.raises(link.LinkTimeout):
        a_link(fake_transport, frozen_clock, retries=2, timeout_s=0.0).command("PING")
    assert fake_transport.lines_written == ["PING", "PING", "PING"]


def test_stale_bytes_are_drained_before_a_command(fake_transport, frozen_clock):
    """One leftover line makes every subsequent reply off by one, and the
    symptom is a data set that looks fine and is shifted throughout."""
    fake_transport.queue("OK STALE\n")
    fake_transport.set_responder(pico_responder)
    assert a_link(fake_transport, frozen_clock).command("PING").tag == "PONG"


def test_the_drain_is_optional_on_a_transport_that_has_no_buffer(frozen_clock):
    """The Transport protocol is deliberately tiny. A transport without
    reset_input_buffer must still work."""

    class Minimal:
        def __init__(self):
            self.rx = bytearray(b"PONG 0.1.0 deadbee\n")

        def write(self, data):
            return len(data)

        def readline(self):
            out = bytes(self.rx)
            self.rx.clear()
            return out

        def close(self):
            pass

    assert a_link(Minimal(), frozen_clock).command("PING") == "PONG 0.1.0 deadbee"


# --------------------------------------------------------------------------
# Convenience wrappers
# --------------------------------------------------------------------------


def test_ping_is_true_against_a_live_pico(fake_pico, frozen_clock):
    assert a_link(fake_pico, frozen_clock).ping() is True


def test_ping_is_false_against_silence(fake_transport, frozen_clock):
    """"Is anything there?" is a question with a No answer. Silence is that
    answer, not an exception."""
    assert a_link(fake_transport, frozen_clock, timeout_s=0.0).ping() is False


def test_firmware_identity_reaches_the_manifest(fake_pico, frozen_clock):
    identity = a_link(fake_pico, frozen_clock).firmware_identity()
    assert identity["firmware_version"] == "0.1.0"
    assert identity["firmware_sha"] == "deadbee"


def test_firmware_identity_tolerates_an_old_bare_ok(fake_transport, frozen_clock):
    """An old firmware that answers a bare `OK PING` should still let you take
    data - it just cannot claim the data is traceable."""
    fake_transport.set_responder(lambda line: "OK\n")
    identity = a_link(fake_transport, frozen_clock).firmware_identity()
    assert identity["firmware_version"] is None


def test_enable_raises_stby(fake_pico, frozen_clock):
    a_link(fake_pico, frozen_clock).enable()
    assert fake_pico.lines_written == ["STBY 1"]


def test_disable_drops_stby(fake_pico, frozen_clock):
    """STBY low is the TB6612's hardware all-stop, and works even if the PWM
    peripheral is misconfigured - which is why every guard path ends here."""
    a_link(fake_pico, frozen_clock).disable()
    assert fake_pico.lines_written == ["STBY 0"]


def test_duty_is_sent_signed_so_direction_is_unambiguous(fake_pico, frozen_clock):
    conn = a_link(fake_pico, frozen_clock)
    conn.set_duty(0, 0.45)
    conn.set_duty(0, -0.45)
    assert fake_pico.lines_written == ["SET +0.4500", "SET -0.4500"]


def test_duty_is_sent_with_fixed_precision(fake_pico, frozen_clock):
    """Fixed precision keeps two runs of the same sweep byte-identical on the
    wire, which is one less thing that can differ between motor 0 and motor 3."""
    a_link(fake_pico, frozen_clock).set_duty(0, 1 / 3)
    assert fake_pico.lines_written == ["SET +0.3333"]


def test_stop_does_not_retry(fake_transport, frozen_clock):
    """stop() runs on the error path. A retrying stop multiplies the time a
    motor keeps turning after something has already gone wrong."""
    with pytest.raises(link.LinkTimeout):
        a_link(fake_transport, frozen_clock, retries=5, timeout_s=0.0).stop()
    assert fake_transport.lines_written == ["STOP"]


def test_coast_and_brake_are_different_commands(fake_pico, frozen_clock):
    """Brake shorts the windings and stops hard; coast freewheels. Measuring
    ticks-per-rev means turning the output shaft by hand, and a braked H-bridge
    fights you."""
    conn = a_link(fake_pico, frozen_clock)
    conn.coast(0)
    conn.brake(0)
    assert fake_pico.lines_written == ["STOP", "BRAKE"]


def test_a_setpoint_beyond_the_firmware_bound_is_refused_before_it_is_sent(
        fake_pico, frozen_clock):
    """The board refuses it anyway; refusing here says why, in the terminal
    the operator is already looking at."""
    with pytest.raises((ValueError, link.LinkError)):
        a_link(fake_pico, frozen_clock).set_target_omega_rad_s(
            0, link.OMEGA_MAX_RAD_S * 2)
    assert fake_pico.lines_written == []


def test_the_closed_loop_setpoint_states_its_unit(fake_pico, frozen_clock):
    """SETRPS is spelled for historical reasons; the argument is rad/s of the
    OUTPUT shaft, not revolutions per second. Mixed units are the leading cause
    of control bugs, so the wrapper is named after the unit it takes."""
    a_link(fake_pico, frozen_clock).set_target_omega_rad_s(0, 12.5)
    assert fake_pico.lines_written[-1].startswith("SETRPS 12.5")


# --------------------------------------------------------------------------
# Encoder samples
# --------------------------------------------------------------------------


def test_an_encoder_sample_uses_the_firmware_timestamp(fake_pico, frozen_clock):
    """USB CDC latency is tens of microseconds to tens of milliseconds and it
    is not constant. Dividing tick deltas by HOST time smears that jitter
    straight into every speed number; the Pico knows when it read the counter."""
    sample = a_link(fake_pico, frozen_clock).read_encoder(0)
    assert sample.ticks == 10432
    assert sample.t_s == pytest.approx(8.123456)


def test_an_encoder_sample_also_parses_the_key_value_form(fake_transport,
                                                          frozen_clock):
    """A stubbed or future board may answer `OK ENC ticks=... t_us=...`.
    Identity of shape is not worth failing a run over."""
    fake_transport.set_responder(lambda line: "OK ENC ticks=99 t_us=1000000\n")
    sample = a_link(fake_transport, frozen_clock).read_encoder(0)
    assert sample.ticks == 99 and sample.t_s == pytest.approx(1.0)


def test_a_malformed_encoder_reply_raises_rather_than_returning_zero(
        fake_transport, frozen_clock):
    """A silent 0 would enter the CSV as a real measurement."""
    fake_transport.set_responder(lambda line: "ENC lots soon\n")
    with pytest.raises(link.LinkError):
        a_link(fake_transport, frozen_clock).read_encoder(0)


def test_a_tick_rate_is_ticks_over_the_firmware_time_delta():
    earlier = link.EncoderSample(ticks=1000, t_s=1.0)
    later = link.EncoderSample(ticks=2000, t_s=1.5)
    assert later.rate_ticks_per_s(earlier) == pytest.approx(2000.0)


def test_a_negative_tick_delta_is_a_negative_rate():
    earlier = link.EncoderSample(ticks=2000, t_s=1.0)
    later = link.EncoderSample(ticks=1000, t_s=1.5)
    assert later.rate_ticks_per_s(earlier) < 0


def test_a_stuck_firmware_clock_raises_rather_than_dividing_by_zero():
    """A rate of infinity would sail into a CSV and only be noticed on a plot."""
    sample = link.EncoderSample(ticks=1000, t_s=1.0)
    with pytest.raises(link.LinkError):
        sample.rate_ticks_per_s(link.EncoderSample(ticks=0, t_s=1.0))


def test_out_of_order_samples_raise():
    earlier = link.EncoderSample(ticks=1000, t_s=2.0)
    later = link.EncoderSample(ticks=2000, t_s=1.0)
    with pytest.raises(link.LinkError):
        later.rate_ticks_per_s(earlier)


# --------------------------------------------------------------------------
# Lifecycle
# --------------------------------------------------------------------------


def test_close_closes_the_transport(fake_pico, frozen_clock):
    a_link(fake_pico, frozen_clock).close()
    assert fake_pico.closed is True


def test_close_is_idempotent(fake_pico, frozen_clock):
    conn = a_link(fake_pico, frozen_clock)
    conn.close()
    conn.close()
    assert fake_pico.close_count == 1


def test_the_link_does_not_open_the_port_itself(fake_transport, frozen_clock):
    """Dependency injection, deliberately: Link takes a transport it did not
    create, which is the entire reason this file needs no hardware."""
    a_link(fake_transport, frozen_clock)
    assert fake_transport.writes == []


def test_the_context_manager_closes(fake_pico, frozen_clock):
    with a_link(fake_pico, frozen_clock):
        pass
    assert fake_pico.closed is True


# --------------------------------------------------------------------------
# Port discovery
# --------------------------------------------------------------------------


def test_an_explicit_port_wins():
    """Explicit beats clever."""
    assert link.resolve_port("/dev/ttyACM3", exists=lambda p: True,
                             globber=lambda p: []) == "/dev/ttyACM3"


def test_an_explicit_port_that_does_not_exist_raises():
    with pytest.raises(link.PortNotFound):
        link.resolve_port("/dev/nope", exists=lambda p: False, globber=lambda p: [])


def test_the_udev_symlink_is_preferred_over_ttyacm():
    """/dev/ttyACM0 is not stable across replugs, and a bench that renumbers
    its instruments between runs quietly ruins a data set."""
    resolved = link.resolve_port(
        None, exists=lambda p: p == link.DEFAULT_PORT_SYMLINK,
        globber=lambda p: ["/dev/ttyACM0"])
    assert resolved == link.DEFAULT_PORT_SYMLINK


def test_ttyacm_is_the_fallback_before_the_setup_script_has_been_run():
    resolved = link.resolve_port(None, exists=lambda p: False,
                                 globber=lambda p: ["/dev/ttyACM1", "/dev/ttyACM0"])
    assert resolved == "/dev/ttyACM0"


def test_no_port_at_all_raises_with_an_actionable_message():
    with pytest.raises(link.PortNotFound) as excinfo:
        link.resolve_port(None, exists=lambda p: False, globber=lambda p: [])
    assert "doctor" in str(excinfo.value).lower() or "bootsel" in str(excinfo.value).lower()


def test_open_link_uses_the_injected_transport_factory():
    """The seam that keeps `open_link` testable without a Pico."""
    made = {}

    def factory(port, baud, timeout_s):
        made["port"] = port
        made["baud"] = baud
        return FakeTransport()

    conn = link.open_link("/dev/ttyACM0", transport_factory=factory)
    assert made["port"] == "/dev/ttyACM0"
    assert made["baud"] == UART_BAUD
    conn.close()


def test_the_default_baud_is_fixed_by_the_wiring_document():
    """115200 is fixed by docs/WIRING.md §7 and by the firmware. Not a knob."""
    assert link.DEFAULT_BAUD == UART_BAUD
