"""Documentation drift, tested.

This is the cheapest high-value test in the repo. The pin map exists in five
places - `docs/WIRING.md` §10.2, `docs/BENCH.md` §3, `firmware/src/board_config.h`,
`tools/rover_bench/analyser.py`, and the operator's memory - and four of them
are machine-readable. Documentation drift is the most likely real failure mode
on this project: a doc that says D6 is UART TX while the firmware toggles
LOOP_TICK on GP20 sends someone to probe pin 1 and spend an evening on a
channel that was never connected to the thing they were measuring.

None of this needs hardware, and none of it needs the code to be finished: the
doc-only assertions run today, and the doc-vs-code ones activate as each source
lands.
"""

from __future__ import annotations

import re

import pytest

from conftest import (
    CONTROL_LOOP_HZ,
    DOCS_DIR,
    EXPECTED_ANALYSER_CHANNELS,
    EXPECTED_BENCH_PINS,
    FAILSAFE_TIMEOUT_MS,
    FIRMWARE_SRC,
    FORBIDDEN_GPIO,
    PWM_HZ,
    REPO_ROOT,
    UART_BAUD,
    import_or_none,
)

WIRING = DOCS_DIR / "WIRING.md"
BENCH = DOCS_DIR / "BENCH.md"
BOARD_CONFIG = FIRMWARE_SRC / "board_config.h"

#: Pico 2 W header pin for each GP the bench uses. Physical pin numbers matter:
#: a wrong one puts a probe clip on the wrong pin, and the capture is of
#: something else entirely.
EXPECTED_HEADER_PINS = {
    0: 1, 1: 2, 2: 4, 3: 5, 4: 6, 5: 7, 12: 16, 13: 17, 20: 26, 21: 27,
}


# --------------------------------------------------------------------------
# Tiny markdown helpers. Boring on purpose - a markdown parser dependency for
# four tables would be a worse trade than twenty lines of regex.
# --------------------------------------------------------------------------


def clean(cell: str) -> str:
    """Strip the markdown decoration off a table cell."""
    return cell.replace("*", "").replace("`", "").replace("‑", "-").strip()


def tables(text: str) -> list[list[list[str]]]:
    """Every markdown table in `text`, as a list of rows of cleaned cells."""
    found: list[list[list[str]]] = []
    current: list[list[str]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("|"):
            cells = [clean(c) for c in stripped.strip("|").split("|")]
            if set("".join(cells)) <= set("-: "):
                continue                       # separator row
            current.append(cells)
        elif current:
            found.append(current)
            current = []
    if current:
        found.append(current)
    return found


def section(text: str, heading: str) -> str:
    """The text under a heading, up to the next heading of the same or higher level."""
    pattern = re.compile(rf"^#+\s*{re.escape(heading)}(?![0-9.]).*$", re.MULTILINE)
    match = pattern.search(text)
    assert match, f"heading {heading!r} not found - the doc was restructured"
    level = len(text[match.start():match.end()].split()[0])
    after = text[match.end():]
    nxt = re.search(rf"^#{{1,{level}}}\s", after, re.MULTILINE)
    return after[: nxt.start()] if nxt else after


def channel_table(text: str) -> dict[str, list[str]]:
    """The first table whose header names a channel column, keyed D0..D7."""
    for table in tables(text):
        header = [c.lower() for c in table[0]]
        if not header or header[0] not in ("ch", "channel"):
            continue
        rows = {}
        for row in table[1:]:
            key = row[0].upper()
            if re.fullmatch(r"D?[0-7]", key):
                rows[key if key.startswith("D") else f"D{key}"] = row
        if len(rows) == 8:
            return rows
    raise AssertionError("no 8-row channel table found")


#: How each ground-truth signal name may legitimately appear in prose.
SIGNAL_ALIASES = {
    "PWMA": ("PWMA",),
    "AIN1": ("AIN1",),
    "AIN2": ("AIN2",),
    "STBY": ("STBY",),
    "ENC_A": ("ENC_A", "ENCODER A", "ENCA"),
    "ENC_B": ("ENC_B", "ENCODER B", "ENCB"),
    "LOOP_TICK": ("LOOP_TICK", "LOOP TICK", "LOOPTICK"),
    "COMPUTE_BUSY": ("COMPUTE_BUSY", "COMPUTE BUSY", "COMPUTEBUSY"),
    "UART_TX": ("UART TX", "UART_TX", "TX"),
    "UART_RX": ("UART RX", "UART_RX", "RX"),
}


def names_signal(cells, signal: str) -> bool:
    joined = " ".join(cells).upper()
    return any(alias in joined for alias in SIGNAL_ALIASES[signal])


def gp_numbers(cells) -> list[int]:
    return [int(m) for cell in cells for m in re.findall(r"\bGP(\d+)\b", cell)]


# --------------------------------------------------------------------------
# The analyser channel map, in four places
# --------------------------------------------------------------------------


def test_wiring_section_10_2_has_a_channel_table():
    assert len(channel_table(section(WIRING.read_text(), "10.2"))) == 8


@pytest.mark.parametrize("channel,signal", sorted(EXPECTED_ANALYSER_CHANNELS.items()))
def test_the_wiring_bench_map_names_the_expected_signal(channel, signal):
    row = channel_table(section(WIRING.read_text(), "10.2"))[channel]
    assert names_signal(row, signal), \
        f"WIRING.md §10.2 {channel} is {row!r}, expected {signal}"


@pytest.mark.parametrize("channel,signal", sorted(EXPECTED_ANALYSER_CHANNELS.items()))
def test_the_bench_probe_table_names_the_expected_signal(channel, signal):
    row = channel_table(section(BENCH.read_text(), "3."))[channel]
    assert names_signal(row, signal), \
        f"BENCH.md §3 {channel} is {row!r}, expected {signal}"


@pytest.mark.parametrize("channel", sorted(EXPECTED_ANALYSER_CHANNELS))
def test_wiring_and_bench_agree_on_the_gp_number(channel):
    """Two documents, one bench. A reader who follows either must land on the
    same physical pin."""
    wiring = gp_numbers(channel_table(section(WIRING.read_text(), "10.2"))[channel])
    bench = gp_numbers(channel_table(section(BENCH.read_text(), "3."))[channel])
    assert wiring and bench
    assert wiring[0] == bench[0], f"{channel}: WIRING says GP{wiring[0]}, BENCH says GP{bench[0]}"


@pytest.mark.parametrize("channel,signal", sorted(EXPECTED_ANALYSER_CHANNELS.items()))
def test_the_documented_gp_matches_the_ground_truth_pin_map(channel, signal):
    documented = gp_numbers(channel_table(section(WIRING.read_text(), "10.2"))[channel])
    assert documented[0] == EXPECTED_BENCH_PINS[signal]


@pytest.mark.parametrize("channel,signal", sorted(EXPECTED_ANALYSER_CHANNELS.items()))
def test_the_documented_header_pin_matches_the_gp(channel, signal):
    """A wrong header pin number puts the clip on the wrong pin, and the
    capture is of something else entirely."""
    row = channel_table(section(WIRING.read_text(), "10.2"))[channel]
    gp = gp_numbers(row)[0]
    pins = [int(c) for c in row if c.isdigit()]
    assert EXPECTED_HEADER_PINS[gp] in pins, \
        f"{channel} is GP{gp} (header pin {EXPECTED_HEADER_PINS[gp]}) but the row says {row}"


# --------------------------------------------------------------------------
# ...and in the firmware
# --------------------------------------------------------------------------


def firmware_pins() -> dict[str, int]:
    if not BOARD_CONFIG.is_file():
        pytest.skip("firmware/src/board_config.h has not landed yet")
    text = BOARD_CONFIG.read_text()
    return {m.group(1): int(m.group(2))
            for m in re.finditer(r"#define\s+PIN_(\w+)\s+(\d+)", text)}


@pytest.mark.parametrize("signal,gp", sorted(EXPECTED_BENCH_PINS.items()))
def test_the_firmware_pin_map_matches_the_docs(signal, gp):
    """board_config.h says "if a number here disagrees with WIRING.md, WIRING.md
    wins and this file is a bug". This is the test that notices."""
    pins = firmware_pins()
    assert signal in pins, f"board_config.h has no PIN_{signal}"
    assert pins[signal] == gp, f"PIN_{signal} is GP{pins[signal]}, docs say GP{gp}"


def test_the_firmware_uses_no_cyw43439_pin():
    """GP23/24/25/29 are internal to the wireless chip. A signal assigned to one
    never appears on a header pin, and the debugging that follows is of a wire
    that was never connected to anything."""
    offenders = {name: gp for name, gp in firmware_pins().items()
                 if gp in FORBIDDEN_GPIO}
    assert not offenders, f"board_config.h assigns CYW43439 pins: {offenders}"


def test_the_firmware_assigns_each_pin_to_exactly_one_signal():
    """Two signals on one pin is a short between two outputs."""
    pins = firmware_pins()
    seen: dict[int, str] = {}
    clashes = []
    for name, gp in sorted(pins.items()):
        if gp in seen:
            clashes.append(f"GP{gp}: {seen[gp]} and {name}")
        seen[gp] = name
    assert not clashes, f"pins assigned twice: {clashes}"


# --------------------------------------------------------------------------
# ...and in the host code
# --------------------------------------------------------------------------


def host_channel_map() -> dict[str, str]:
    analyser = import_or_none("rover_bench.analyser")
    if analyser is None:
        pytest.skip("rover_bench.analyser has not landed yet")
    return analyser.CHANNEL_MAP


@pytest.mark.parametrize("channel,signal", sorted(EXPECTED_ANALYSER_CHANNELS.items()))
def test_the_host_channel_map_names_the_expected_signal(channel, signal):
    assert names_signal([host_channel_map()[channel]], signal)


@pytest.mark.parametrize("channel,signal", sorted(EXPECTED_ANALYSER_CHANNELS.items()))
def test_the_host_channel_map_carries_the_right_gp(channel, signal):
    """The host's map is what a capture's channel labels come from. If it
    disagrees with the firmware, the plot axes are labelled with the wrong
    signal and nothing in the pipeline notices."""
    described = host_channel_map()[channel]
    found = gp_numbers([described])
    assert found, f"CHANNEL_MAP[{channel}] = {described!r} names no GP"
    assert found[0] == EXPECTED_BENCH_PINS[signal]


# --------------------------------------------------------------------------
# The two channel maps, and the fact that they are two
# --------------------------------------------------------------------------


def test_the_older_map_still_exists_and_is_labelled_as_the_other_one():
    """§8 (UART on D6/D7) is NOT superseded - the two maps swap depending on
    whether the question is loop timing or failsafe latency. A doc that quietly
    dropped one would leave a `.sr` file whose D6 could be either signal, and
    that file is evidence of nothing."""
    text = WIRING.read_text()
    assert "## 8." in text and "10.2" in text
    reconciliation = section(text, "10.3")
    assert "8" in reconciliation and "10.2" in reconciliation


def test_the_two_maps_differ_only_at_d6_and_d7():
    """The bench map (§10.2) is the ground truth this suite asserts against;
    §8 is the failsafe map. They must agree on D0-D5 and differ on D6/D7. Any
    other difference is drift, not the documented swap."""
    text = WIRING.read_text()
    failsafe_map = channel_table(section(text, "8."))
    bench_map = channel_table(section(text, "10.2"))

    differing = {channel for channel, signal in EXPECTED_ANALYSER_CHANNELS.items()
                 if not names_signal(failsafe_map[channel], signal)}
    assert differing == {"D6", "D7"}, (
        "§8 and §10.2 should differ at exactly D6/D7 (UART vs instrumentation); "
        f"they differ at {sorted(differing)}"
    )
    failsafe_signals = {"D6": "UART_TX", "D7": "UART_RX"}
    for channel, signal in failsafe_signals.items():
        assert names_signal(failsafe_map[channel], signal)
        assert names_signal(bench_map[channel], EXPECTED_ANALYSER_CHANNELS[channel])


def test_the_docs_say_which_map_a_capture_was_taken_under_must_be_recorded():
    assert "record which map" in WIRING.read_text().lower()


# --------------------------------------------------------------------------
# Safety: no document may instruct probing a motor output
# --------------------------------------------------------------------------


PROBE_WORDS = ("probe", "analyser", "analyzer", "channel", "scope", "clip",
               "sigrok", " ch ", "d0", "d7")

#: A line naming a motor output in a probing context is excused only if it is
#: plainly a prohibition. Bare "no " is NOT in this list on purpose: "AO1, no
#: clip needed" would be excused by it, and this is the check that stands
#: between a tired operator and a destroyed analyser. Verified against the
#: current docs - the tighter list still finds zero offenders.
PROHIBITION_WORDS = ("never", "not ", "must not", "refus", "destroy",
                     "danger", "warning", "⚠", "do not", "forbidden")


def markdown_files():
    return sorted(p for p in REPO_ROOT.rglob("*.md")
                  if ".git" not in p.parts and "node_modules" not in p.parts)


def test_there_are_documents_to_check():
    """Guards the test below against passing because it found nothing."""
    assert len(markdown_files()) > 5


def test_no_document_instructs_probing_a_motor_output():
    """Safety rule 1. AO1/AO2/BO1/BO2 sit at motor voltage and switch
    inductively; the FX2's inputs are 3.3 V logic with no series protection.
    Any line that mentions one of those nets in a probing context must be a
    prohibition - a line saying "the red wire goes to AO1" is wiring, and fine.
    """
    offenders = []
    for path in markdown_files():
        for number, line in enumerate(path.read_text().splitlines(), 1):
            lowered = clean(line).lower()
            if not any(net.lower() in lowered for net in ("ao1", "ao2", "bo1", "bo2")):
                continue
            if not any(word in lowered for word in PROBE_WORDS):
                continue
            if any(word in lowered for word in PROHIBITION_WORDS):
                continue
            offenders.append(f"{path.relative_to(REPO_ROOT)}:{number}: {line.strip()[:110]}")
    assert not offenders, "documents appear to permit probing a motor output:\n  " + \
        "\n  ".join(offenders)


def test_the_bench_manual_states_all_six_safety_interlocks():
    """The six rules that have each already cost money or an evening. BENCH.md
    §2 is the page an operator reads before touching anything."""
    text = section(BENCH.read_text(), "2.").lower()
    for token, what in (("ao1", "never probe the H-bridge outputs"),
                        ("3.3 v", "encoder power is 3.3 V"),
                        ("ground", "grounds must be common"),
                        ("13.5", "the LiPo never reaches the TB6612"),
                        ("pico", "no motor current enters the Pico"),
                        ("resistance", "confirm the motor pair by resistance")):
        assert token in text, f"BENCH.md §2 does not state: {what}"


def test_the_safety_module_and_the_bench_manual_agree():
    safety = import_or_none("rover_bench.safety")
    if safety is None:
        pytest.skip("rover_bench.safety has not landed yet")
    checklist = " ".join(safety.WIRING_CHECKLIST).lower()
    manual = section(BENCH.read_text(), "2.").lower()
    for token in ("ao1", "3.3 v", "13.5"):
        assert token in checklist and token in manual


def test_no_document_assigns_a_cyw43439_pin_in_a_pin_table():
    """GP23/24/25/29. A table row assigning one is a wire that will never work
    and an evening spent finding out why."""
    offenders = []
    for path in (WIRING, BENCH):
        for table in tables(path.read_text()):
            header = [c.lower() for c in table[0]]
            if not any(h in ("gp", "pico gp", "ch", "channel") for h in header):
                continue
            for row in table[1:]:
                joined = " ".join(row)
                if "unusable" in joined.lower() or "cyw" in joined.lower():
                    continue      # the row that says they are unusable
                for gp in gp_numbers(row):
                    if gp in FORBIDDEN_GPIO:
                        offenders.append(f"{path.name}: {joined[:100]}")
    assert not offenders, f"CYW43439 pins assigned in a pin table: {offenders}"


# --------------------------------------------------------------------------
# Fixed constants, stated in more than one place
# --------------------------------------------------------------------------


def test_the_pwm_frequency_agrees_between_docs_and_firmware():
    text = BOARD_CONFIG.read_text() if BOARD_CONFIG.is_file() else pytest.skip("no firmware")
    assert re.search(rf"PWM_FREQ_HZ\s+{PWM_HZ}u?\b", text), \
        "board_config.h does not define PWM_FREQ_HZ as 20000"


def test_the_control_loop_rate_agrees_between_docs_and_firmware():
    text = BOARD_CONFIG.read_text() if BOARD_CONFIG.is_file() else pytest.skip("no firmware")
    assert re.search(rf"CONTROL_HZ\s+{CONTROL_LOOP_HZ}u?\b", text)


def test_the_uart_baud_agrees_between_docs_firmware_and_host():
    if BOARD_CONFIG.is_file():
        assert re.search(rf"UART_BAUD\s+{UART_BAUD}\b", BOARD_CONFIG.read_text())
    stated_in = [p.name for p in (WIRING, BENCH, DOCS_DIR / "ARCHITECTURE.md")
                 if p.is_file() and str(UART_BAUD) in p.read_text()]
    assert stated_in, f"no bench document states the {UART_BAUD} baud rate"
    link = import_or_none("rover_bench.link")
    if link is not None:
        assert link.DEFAULT_BAUD == UART_BAUD


def test_the_failsafe_timeout_is_inside_the_architecture_documents_band():
    """ARCHITECTURE.md specifies ~200-500 ms and CLAUDE.md calls the failsafe
    non-negotiable. A firmware default outside that band contradicts the
    architecture without anyone editing the architecture."""
    if not BOARD_CONFIG.is_file():
        pytest.skip("no firmware")
    match = re.search(r"FAILSAFE_TIMEOUT_MS\s+(\d+)u?", BOARD_CONFIG.read_text())
    assert match, "board_config.h defines no FAILSAFE_TIMEOUT_MS"
    assert 200 <= int(match.group(1)) <= 500
    assert int(match.group(1)) == FAILSAFE_TIMEOUT_MS


def test_the_docs_warn_that_loop_tick_toggles():
    """LOOP_TICK toggles, so one loop period is edge to edge. A frequency
    reading on that channel reports half the loop rate, and every omega derived
    from an assumed loop rate is then wrong by two."""
    for path in (WIRING, BENCH):
        text = path.read_text().lower()
        assert "toggle" in text, f"{path.name} does not say LOOP_TICK toggles"


def test_no_document_claims_a_100hz_loop_makes_200_edges_per_second():
    """REGRESSION. LOOP_TICK toggles ONCE per iteration, so one edge is one
    iteration: a 100 Hz loop is 100 edges/s and a 50 Hz square wave. Three
    documents once said "200 edges/s" beside "50 Hz square wave" — internally
    contradictory, and it inverts the acceptance check in BENCH §6 Rung 4: a
    genuine 100 Hz loop reads as a failure and a genuine 50 Hz loop reads as a
    pass. `.claude/skills/rover-bench/SKILL.md` still carried it after
    docs/BENCH.md and docs/WIRING.md were corrected, because the previous guard
    only looked for the word "toggle" and only in those two files. This one
    sweeps every markdown in the repo, skills included.
    """
    offenders = []
    for path in markdown_files():
        for number, line in enumerate(path.read_text().splitlines(), 1):
            lowered = line.lower()
            if "200 edges" in lowered:
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{number}: {line.strip()}")
    assert not offenders, (
        "one edge is one iteration — a 100 Hz loop is 100 edges/s and a 50 Hz "
        "square wave. Double a frequency readout, never halve it:\n"
        + "\n".join(offenders)
    )


def test_the_skill_and_the_wiring_record_agree_on_the_loop_tick_edge_rate():
    """The skill is what an agent reads instead of the docs. It must carry the
    same number as the connection record it cites."""
    skill = REPO_ROOT / ".claude" / "skills" / "rover-bench" / "SKILL.md"
    if not skill.exists():
        pytest.skip("the rover-bench skill is not present")
    text = skill.read_text().lower()
    assert "100 edges/s" in text, "the skill does not state the 100 edges/s rate"
    assert "50 hz square wave" in text, "the skill does not state the wire frequency"


def test_the_encoder_supply_is_stated_as_3v3_and_never_5v():
    for path in (WIRING, BENCH):
        text = path.read_text().lower()
        assert "3.3 v, never 5 v" in text or "3.3 v. never 5 v" in text, \
            f"{path.name} does not state the encoder supply rule"


def test_the_ticks_per_rev_dispute_is_still_flagged_as_unresolved():
    """The day this stops being true, someone has adopted one of the two
    datasheet figures without measuring - and every downstream number is then
    wrong by an unknown factor with nothing to catch it.

    Asserting on the actual sentences, not on the digits "11" and "14": those
    two appear in dates, pin numbers and section references all over the file,
    so a test that only looked for them would pass on a document that had
    quietly settled the question.
    """
    hardware = (DOCS_DIR / "HARDWARE.md").read_text().lower()
    for claim in ("14 counts per revolution", "11 pulses per revolution"):
        assert claim in hardware, (
            f"HARDWARE.md §2.1 no longer records the competing claim {claim!r}. "
            "If the count has been measured, the [MEASURE] tag and Story 1.4 "
            "instruction must go with it - and this test must be rewritten to "
            "assert the measured value and its provenance instead."
        )
    assert "1100 and 1400" in hardware, \
        "the derived output-shaft range is no longer stated as a range"
    assert "[measure]" in hardware, "the [MEASURE] tag has gone"
    assert "story 1.4" in hardware, \
        "HARDWARE.md no longer names the experiment that settles the count"


def test_the_bench_manual_sends_you_to_the_device_for_the_sample_rate():
    """The FX2 clones differ and the ceiling depends on channel count and USB
    host. A number written down here would be adopted as fact.

    So the section has to do two things, and both are asserted: point at the
    runtime query that answers the question, and label any rate list it quotes
    as an observation rather than a specification.
    """
    text = section(BENCH.read_text(), "5.1").lower()
    assert "--show" in text, \
        "BENCH.md §5.1 does not tell you how to read the rates off the device"
    assert "runtime" in text or "read it off the device" in text, \
        "BENCH.md §5.1 does not say the rate is discovered at runtime"
    assert "ground truth" in text, \
        "BENCH.md §5.1 no longer says the device's list is the ground truth"
    assert "observation" in text or "not a specification" in text, \
        ("BENCH.md §5.1 quotes rates without labelling them an observation - "
         "which is how a one-machine, one-day reading becomes a spec")
