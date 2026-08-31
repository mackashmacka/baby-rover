"""Property-based tests over the parsers and the PID.

The example-based tests elsewhere assert what we thought to check. These assert
what must hold for *every* input, which is the right shape for a trust
boundary: the UART carries whatever the cable and the far end produce, and the
interesting inputs are the ones nobody thought of.

Four properties, in order of what they would cost if they were false:

1. Any byte sequence the firmware parser rejects leaves the state bit-for-bit
   unchanged, and never crashes.
2. The PID output is always inside its actuator limits, for any finite input.
3. A registry cell survives escape -> render -> split -> unescape unchanged, so
   a Result containing a pipe cannot corrupt the table.
4. `validate_outgoing` never lets a multi-line or NUL-bearing frame onto the
   wire.

Hypothesis is a test-only dependency (`tests/requirements.txt`). If it is not
installed the file skips rather than failing - the rest of the suite still runs.
"""

from __future__ import annotations

import math

import pytest

hypothesis = pytest.importorskip("hypothesis")
from hypothesis import HealthCheck, given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

from conftest import import_or_none  # noqa: E402

registry = import_or_none("rover_bench.registry")
link = import_or_none("rover_bench.link")
analyser = import_or_none("rover_bench.analyser")

#: Function-scoped fixtures (rover_state) are re-created per example by design
#: here: each example needs a clean state, which is the point.
PER_EXAMPLE = settings(max_examples=200, deadline=None,
                       suppress_health_check=[HealthCheck.function_scoped_fixture])


# --------------------------------------------------------------------------
# 1. The firmware parser is total, and rejection is inert
# --------------------------------------------------------------------------

printable_line = st.text(
    alphabet=st.characters(min_codepoint=32, max_codepoint=126),
    min_size=0, max_size=120,
)

command_ish = st.builds(
    lambda verb, args: " ".join([verb, *args]),
    st.sampled_from(["SET", "STOP", "BRAKE", "STBY", "PING", "ID?", "ENC?",
                     "TELEM", "PID", "PIDEN", "SETRPS", "RESET", "FLY", ""]),
    st.lists(st.sampled_from(["0", "1", "2", "-1", "0.5", "-0.5", "1.0", "1.5",
                              "nan", "inf", "abc", "", "0x10", "1e3", " "]),
             max_size=4),
)


@PER_EXAMPLE
@given(line=st.one_of(printable_line, command_ish))
def test_the_parser_never_raises_and_never_half_applies(rover_state, line):
    """Total function: every input produces OK or ERR, and an ERR leaves the
    state byte-identical. protocol.h promises exactly this, in those words."""
    before = rover_state.snapshot()
    accepted, reply = rover_state.handle(line)
    assert isinstance(reply, str)
    if not accepted:
        assert reply.startswith("ERR ")
        assert rover_state.snapshot() == before, f"{line!r} mutated state on rejection"


@PER_EXAMPLE
@given(line=st.one_of(printable_line, command_ish))
def test_a_reply_never_contains_a_terminator(rover_state, line):
    """The transport appends the newline. A reply carrying one would
    desynchronise the host's line reader for every command that follows."""
    _, reply = rover_state.handle(line)
    assert "\n" not in reply and "\r" not in reply


#: Finite values plus the three specials, which are the interesting cases.
number_ish = st.one_of(
    st.floats(allow_nan=False, allow_infinity=False, min_value=-1e6, max_value=1e6),
    st.sampled_from([float("nan"), float("inf"), float("-inf")]),
)


@PER_EXAMPLE
@given(duty=number_ish)
def test_any_accepted_duty_is_a_legal_fraction(rover_state, duty):
    """Whatever spelling of a number arrives, an accepted SET leaves a duty in
    [-1, 1]. That is the only thing the PWM hardware can be asked for."""
    accepted, _ = rover_state.handle(f"SET {duty!r}")
    if accepted:
        stored = rover_state.duty_frac
        assert not math.isnan(stored)
        assert -1.0 <= stored <= 1.0


@PER_EXAMPLE
@given(omega=number_ish)
def test_any_accepted_setpoint_is_inside_the_sanity_bound(rover_state, omega):
    accepted, _ = rover_state.handle(f"SETRPS {omega!r}")
    if accepted:
        assert abs(rover_state.setpoint_omega_rad_s) <= 100.0


@PER_EXAMPLE
@given(data=st.binary(min_size=0, max_size=200))
def test_arbitrary_bytes_cannot_enable_the_driver(firmware_lib, rover_state, data):
    """Line noise on the UART must never end up raising STBY. The reader is
    what makes that true: an overlong or NUL-bearing line is rejected whole
    rather than having its tail parsed as a fresh command."""
    from test_firmware_protocol import Reader

    reader = Reader(firmware_lib)
    for byte in data:
        if reader.push(bytes([byte])):
            reader.handle(rover_state)
    assert rover_state.stby_enabled is False
    assert rover_state.duty_frac == 0.0 or -1.0 <= rover_state.duty_frac <= 1.0


# --------------------------------------------------------------------------
# 2. The PID output is always inside its limits
# --------------------------------------------------------------------------


finite = st.floats(allow_nan=False, allow_infinity=False,
                   min_value=-1e4, max_value=1e4)
gains = st.floats(allow_nan=False, allow_infinity=False,
                  min_value=0.0, max_value=1e3)


@PER_EXAMPLE
@given(kp=gains, ki=gains, kd=gains, setpoint=finite, measurement=finite,
       steps=st.integers(min_value=1, max_value=30))
def test_the_pid_output_never_leaves_the_actuator_limits(firmware_lib, kp, ki, kd,
                                                         setpoint, measurement,
                                                         steps):
    """No combination of gains and inputs may produce a duty the hardware
    cannot be asked for, or a NaN. This is the property that makes the output
    safe to hand straight to the PWM register."""
    from test_firmware_control import Pid

    controller = Pid(firmware_lib, kp=kp, ki=ki, kd=kd)
    for _ in range(steps):
        out = controller.step(setpoint, measurement)
        assert not math.isnan(out)
        assert -1.0 <= out <= 1.0


@PER_EXAMPLE
@given(ki=gains, setpoint=finite, steps=st.integers(min_value=1, max_value=50))
def test_the_integrator_is_always_bounded(firmware_lib, ki, setpoint, steps):
    """Anti-windup as a property: the accumulator cannot exceed the limits no
    matter how long an unreachable setpoint is held."""
    from test_firmware_control import Pid

    controller = Pid(firmware_lib, ki=ki)
    for _ in range(steps):
        controller.step(setpoint, 0.0)
    assert -1.0 - 1e-6 <= controller.integral <= 1.0 + 1e-6


# --------------------------------------------------------------------------
# 3. Registry cells survive the round trip
# --------------------------------------------------------------------------


#: Two strategies, because broad unicode text almost never produces the
#: characters this property is actually about. Random text hits a `|` in maybe
#: one example in a hundred and a `\` before a `|` essentially never, so a
#: property drawn only from it would report "escaping is fine" while never
#: having escaped anything. The second alphabet is the markdown table's
#: metacharacters and nothing else.
cell_text = st.one_of(
    st.text(alphabet=st.characters(blacklist_categories=("Cs",), max_codepoint=0x2FFF),
            min_size=0, max_size=80),
    st.text(alphabet=st.sampled_from(list("ab|\\ \t\n\r*`_-")), min_size=0, max_size=25),
)


@settings(max_examples=200, deadline=None)
@given(question=cell_text, result=cell_text)
def test_a_registry_row_survives_render_and_reparse(question, result):
    """A Result containing a pipe must not split the row and shift every later
    cell one to the left.

    Newlines collapse to spaces and runs of whitespace are squeezed - lossy,
    but stated, and `escape_cell` is the statement of it. The property is that
    the SEVEN columns survive and each carries its normalised text.
    """
    if registry is None:
        pytest.skip("rover_bench.registry not present")
    row = {"ID": "motor-char", "Date": "2026-08-31", "Story": "1.5",
           "Question": question, "Method": "m", "Result": result,
           "Data": "experiments/motor-char/"}
    cells = registry.split_row(registry.format_row(row))
    assert len(cells) == 7
    normalise = lambda text: registry.unescape_cell(registry.escape_cell(text))  # noqa: E731
    assert cells[3] == normalise(question)
    assert cells[5] == normalise(result)


@settings(max_examples=200, deadline=None)
@given(text=cell_text)
def test_an_escaped_cell_never_contains_a_bare_pipe(text):
    if registry is None:
        pytest.skip("rover_bench.registry not present")
    escaped = registry.escape_cell(text)
    unescaped = escaped.replace(r"\|", "")
    assert "|" not in unescaped


@settings(max_examples=200, deadline=None)
@given(text=cell_text)
def test_an_escaped_cell_is_a_single_line(text):
    if registry is None:
        pytest.skip("rover_bench.registry not present")
    assert "\n" not in registry.escape_cell(text)
    assert "\r" not in registry.escape_cell(text)


# --------------------------------------------------------------------------
# 4. Nothing malformed reaches the wire
# --------------------------------------------------------------------------


@settings(max_examples=300, deadline=None)
@given(text=st.text(max_size=300))
def test_validate_outgoing_accepts_only_single_ascii_lines(text):
    if link is None:
        pytest.skip("rover_bench.link not present")
    try:
        accepted = link.validate_outgoing(text)
    except (ValueError, UnicodeEncodeError, link.LinkError):
        return
    assert "\n" not in accepted and "\r" not in accepted
    assert "\x00" not in accepted
    assert accepted.strip()
    assert len(accepted) <= link.MAX_LINE_CHARS
    accepted.encode("ascii", "strict")


# --------------------------------------------------------------------------
# 5. Frequency parsing round-trips
# --------------------------------------------------------------------------


@settings(max_examples=200, deadline=None)
@given(value=st.integers(min_value=1, max_value=999),
       unit=st.sampled_from(["Hz", "kHz", "MHz"]))
def test_a_rendered_frequency_parses_back(value, unit):
    if analyser is None:
        pytest.skip("rover_bench.analyser not present")
    multiplier = {"Hz": 1, "kHz": 1_000, "MHz": 1_000_000}[unit]
    assert analyser.parse_frequency(f"{value} {unit}") == value * multiplier


@settings(max_examples=200, deadline=None)
@given(rates=st.lists(st.integers(min_value=1, max_value=10**8),
                      min_size=1, max_size=20, unique=True),
       wanted=st.integers(min_value=1, max_value=10**8))
def test_a_chosen_rate_is_always_offered_and_always_sufficient(rates, wanted):
    """Two properties at once, and both matter: never invent a rate the device
    did not offer, and never return one that is too slow for what was asked."""
    if analyser is None:
        pytest.skip("rover_bench.analyser not present")
    try:
        chosen = analyser.choose_sample_rate(rates, wanted)
    except analyser.AnalyserError:
        assert max(rates) < wanted
        return
    assert chosen in rates
    assert chosen >= wanted
    assert chosen == min(r for r in rates if r >= wanted)
