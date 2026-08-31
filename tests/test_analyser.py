"""The sigrok-cli wrapper.

Three things this file exists to prevent:

1. **A hardcoded sample rate.** The FX2's rate list is a device property; the
   repo documents no maximum anywhere, on purpose. A hardcoded 24 MHz that the
   device silently clamps to 12 MHz produces a capture whose timebase is wrong
   by 2x, and every duty and frequency computed from it is wrong with it.
2. **PulseView in an automated path.** PulseView is for human eyes. A capture
   that needs a mouse click cannot be repeated identically on motor 3 three
   days after motor 0.
3. **Probing a motor output.** Safety rule 1. It destroys the instrument, and
   not loudly - it fails by reading garbage on a channel you then trust.

Nothing here runs sigrok. The subprocess runner is injected, and the autouse
guard in conftest makes that enforced rather than intended.
"""

from __future__ import annotations

import inspect

import pytest

from conftest import (
    SIGROK_SCAN_EMPTY,
    SIGROK_SCAN_OUTPUT,
    SIGROK_SHOW_OUTPUT,
    SIGROK_SHOW_RANGE,
    SIGROK_SHOW_RATES_HZ,
    import_or_none,
)

analyser = import_or_none("rover_bench.analyser")

pytestmark = pytest.mark.xfail(
    analyser is None,
    reason="rover_bench.analyser has not landed yet; these activate when it does",
    strict=False,
)

BENCH_CHANNELS = ["D0", "D1", "D2", "D3", "D4", "D5", "D6", "D7"]


# --------------------------------------------------------------------------
# Frequency parsing
# --------------------------------------------------------------------------


@pytest.mark.parametrize("text,hz", [
    ("20 kHz", 20_000),
    ("24 MHz", 24_000_000),
    ("1 MHz", 1_000_000),
    ("500 Hz", 500),
    ("1000", 1000),
    ("1.5 MHz", 1_500_000),
    ("  250 kHz  ", 250_000),
    ("24MHz", 24_000_000),
])
def test_frequencies_parse(text, hz):
    assert analyser.parse_frequency(text) == hz


@pytest.mark.parametrize("text", ["", "fast", "MHz", "24 furlongs", "-5 kHz"])
def test_an_unparseable_frequency_raises(text):
    """Guessing here would put an unfalsifiable number in the manifest."""
    with pytest.raises(analyser.AnalyserError):
        analyser.parse_frequency(text)


def test_a_discrete_rate_list_parses():
    rates = analyser.parse_samplerate_spec(
        "samplerate (RW): 20 kHz, 25 kHz, 1 MHz, 24 MHz")
    assert rates == (20_000, 25_000, 1_000_000, 24_000_000)


def test_a_continuous_rate_range_parses_to_its_endpoints():
    """sigrok prints two shapes and the difference matters: a discrete device
    can only do the listed rates, a range device can do anything between."""
    assert analyser.parse_samplerate_spec(
        "samplerate (RW): 20 kHz - 24 MHz (in steps of 1 Hz)") == \
        (20_000, 24_000_000)


def test_an_empty_rate_spec_parses_to_nothing():
    assert analyser.parse_samplerate_spec("samplerate:") == ()


# --------------------------------------------------------------------------
# Capability discovery from real sigrok output
# --------------------------------------------------------------------------


def test_scan_output_is_parsed():
    assert len(analyser.parse_scan_output(SIGROK_SCAN_OUTPUT)) == 1


def test_scan_output_yields_the_driver_name():
    assert analyser.parse_scan_output(SIGROK_SCAN_OUTPUT)[0]["driver"] == "fx2lafw"


def test_scan_output_yields_the_connection_string():
    """`conn=1.10` is how you address one device when two are plugged in."""
    assert analyser.parse_scan_output(SIGROK_SCAN_OUTPUT)[0]["conn"] == "1.10"


def test_scan_output_yields_the_channel_list():
    assert analyser.parse_scan_output(SIGROK_SCAN_OUTPUT)[0]["channels"] == \
        BENCH_CHANNELS


def test_scan_output_with_no_devices_parses_to_nothing():
    assert analyser.parse_scan_output(SIGROK_SCAN_EMPTY) == []


def test_scan_output_of_empty_text_parses_to_nothing():
    assert analyser.parse_scan_output("") == []


def test_an_unrecognised_banner_line_does_not_take_the_bench_down():
    """sigrok changes its banner between releases."""
    text = "Some new banner sigrok added in 0.8\n" + SIGROK_SCAN_OUTPUT
    assert len(analyser.parse_scan_output(text)) == 1


def test_show_output_yields_the_sample_rates_in_hz():
    """The whole point of discovery: these come from the device, not from us."""
    assert analyser.parse_show_output(SIGROK_SHOW_OUTPUT)["samplerates_hz"] == \
        list(SIGROK_SHOW_RATES_HZ)


def test_show_output_rates_are_integers():
    rates = analyser.parse_show_output(SIGROK_SHOW_OUTPUT)["samplerates_hz"]
    assert all(isinstance(r, int) for r in rates)


def test_show_output_reports_a_discrete_device_as_discrete():
    assert analyser.parse_show_output(SIGROK_SHOW_OUTPUT)["discrete"] is True


def test_show_output_reports_a_range_device_as_not_discrete():
    assert analyser.parse_show_output(SIGROK_SHOW_RANGE)["discrete"] is False


def test_show_output_yields_the_channel_list():
    assert analyser.parse_show_output(SIGROK_SHOW_OUTPUT)["channels"] == BENCH_CHANNELS


def test_show_output_with_no_samplerate_line_raises():
    """A device whose rates cannot be read is a device we must not guess for."""
    with pytest.raises(analyser.AnalyserError):
        analyser.parse_show_output("Driver functions:\n    Logic analyzer\n")


# --------------------------------------------------------------------------
# Choosing a rate - from what the device advertises, never from a constant
# --------------------------------------------------------------------------


def test_the_chosen_rate_comes_from_the_advertised_list():
    assert analyser.choose_sample_rate(SIGROK_SHOW_RATES_HZ, 150_000) in \
        SIGROK_SHOW_RATES_HZ


def test_the_chosen_rate_is_the_cheapest_one_that_is_fast_enough():
    """Cheapest, not fastest: samples are memory and the FX2 streams over USB
    2, so an unnecessarily high rate starts dropping samples - and dropped
    samples look exactly like phantom edges."""
    assert analyser.choose_sample_rate(SIGROK_SHOW_RATES_HZ, 150_000) == 200_000


def test_an_exactly_available_rate_is_chosen_as_is():
    assert analyser.choose_sample_rate(SIGROK_SHOW_RATES_HZ, 1_000_000) == 1_000_000


def test_a_requirement_no_device_rate_satisfies_raises():
    """Silently returning the maximum would give a capture that looks fine and
    aliases. Refusing forces the question "what can I actually measure?"."""
    with pytest.raises(analyser.AnalyserError):
        analyser.choose_sample_rate(SIGROK_SHOW_RATES_HZ, 100_000_000)


def test_choosing_from_an_empty_rate_list_raises():
    with pytest.raises(analyser.AnalyserError):
        analyser.choose_sample_rate([], 1_000_000)


def test_an_unsorted_rate_list_still_yields_the_cheapest():
    assert analyser.choose_sample_rate([24_000_000, 20_000, 1_000_000], 20_000) == \
        20_000


def test_a_pwm_capture_gets_at_least_ten_samples_per_period():
    """20 kHz PWM, and duty is what is being measured, so two samples a period
    is not enough. 10x -> 200 kHz, which this device has."""
    assert analyser.choose_sample_rate(SIGROK_SHOW_RATES_HZ, 20_000 * 10) >= 200_000


def test_a_control_loop_capture_resolves_loop_jitter():
    """The loop tick is 100 Hz on D6; jitter of interest is microseconds, so
    the rate has to be far above the loop rate, not just above it."""
    assert analyser.choose_sample_rate(SIGROK_SHOW_RATES_HZ, 1_000_000) >= 1_000_000


# --------------------------------------------------------------------------
# The invocation
# --------------------------------------------------------------------------


def an_argv(tmp_path, **kw):
    kwargs = dict(sample_rate_hz=1_000_000, samples=100_000,
                  channels=list(BENCH_CHANNELS), output_file=tmp_path / "capture.sr")
    kwargs.update(kw)
    return [str(a) for a in analyser.build_capture_argv(**kwargs)]


def test_the_capture_invokes_sigrok_cli(tmp_path):
    assert an_argv(tmp_path)[0].endswith("sigrok-cli")


def test_the_capture_never_invokes_pulseview(tmp_path):
    assert "pulseview" not in " ".join(an_argv(tmp_path)).lower()


def test_the_capture_selects_the_fx2lafw_driver(tmp_path):
    argv = an_argv(tmp_path)
    assert argv[argv.index("--driver") + 1] == "fx2lafw"


def test_the_capture_passes_the_sample_rate_it_was_given(tmp_path):
    argv = an_argv(tmp_path, sample_rate_hz=4_000_000)
    assert "samplerate=4000000" in argv


def test_the_capture_passes_the_sample_count(tmp_path):
    argv = an_argv(tmp_path, samples=12345)
    assert argv[argv.index("--samples") + 1] == "12345"


def test_the_capture_names_the_channels(tmp_path):
    argv = an_argv(tmp_path, channels=["D0", "D4"])
    assert argv[argv.index("--channels") + 1] == "D0,D4"


def test_the_capture_writes_to_the_requested_file(tmp_path):
    out = tmp_path / "run.sr"
    assert str(out) in an_argv(tmp_path, output_file=out)


def test_the_capture_asks_for_srzip(tmp_path):
    """`.sr` is the archival artifact - what a human opens when a number looks
    wrong - and `.gitignore` already excludes it."""
    argv = an_argv(tmp_path)
    assert argv[argv.index("--output-format") + 1] == "srzip"


def test_the_argv_is_a_list_not_a_shell_string(tmp_path):
    """A shell string is a quoting bug waiting for a path with a space in it."""
    assert isinstance(analyser.build_capture_argv(
        sample_rate_hz=1_000_000, samples=1000, channels=["D0"],
        output_file=tmp_path / "c.sr"), list)


def test_a_path_with_a_space_survives(tmp_path):
    out = tmp_path / "a folder" / "capture.sr"
    assert str(out) in an_argv(tmp_path, output_file=out)


def test_the_same_inputs_build_the_same_argv(tmp_path):
    """Two runs of the same experiment must be byte-identical invocations, or
    "we ran the same thing on all four motors" is not a claim anyone can make."""
    assert an_argv(tmp_path) == an_argv(tmp_path)


# --------------------------------------------------------------------------
# The thing that must never be hardcoded
# --------------------------------------------------------------------------


def test_the_sample_rate_parameter_has_no_default():
    """Structural, and the most important test here: if sample_rate_hz can be
    omitted, some call site eventually omits it and the timebase quietly
    becomes whatever the author assumed on a Tuesday."""
    param = inspect.signature(analyser.build_capture_argv).parameters["sample_rate_hz"]
    assert param.default is inspect.Parameter.empty


def test_the_capture_function_also_requires_a_rate():
    param = inspect.signature(analyser.capture).parameters["sample_rate_hz"]
    assert param.default is inspect.Parameter.empty


def test_the_module_declares_no_sample_rate_constant():
    """No MAX_SAMPLE_RATE / DEFAULT_SAMPLERATE. The repo does not document this
    clone's ceiling anywhere, on purpose: it is discovered."""
    offenders = [
        name for name, value in vars(analyser).items()
        if name.isupper()
        and any(token in name for token in ("RATE", "SAMPLERATE", "HZ", "MHZ", "KHZ"))
        and isinstance(value, (int, float)) and not isinstance(value, bool)
    ]
    assert not offenders, f"hardcoded sample-rate constants: {offenders}"


def test_the_module_source_never_mentions_pulseview_as_a_command():
    source = inspect.getsource(analyser)
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or stripped.startswith("*"):
            continue
        assert "pulseview" not in stripped.lower(), (
            f"pulseview appears in executable code: {line}")


# --------------------------------------------------------------------------
# Safety at the probe tip
# --------------------------------------------------------------------------


@pytest.mark.parametrize("net", ["AO1", "AO2", "BO1", "BO2", "ao1"])
def test_building_a_capture_that_probes_a_motor_output_is_refused(tmp_path, net):
    with pytest.raises(analyser.AnalyserError) as excinfo:
        analyser.build_capture_argv(sample_rate_hz=1_000_000, samples=1000,
                                    channels=["D0", net],
                                    output_file=tmp_path / "c.sr")
    assert net.lower() in str(excinfo.value).lower()


def test_more_than_eight_channels_is_refused(tmp_path):
    """The device has eight. Asking for D8 produces a cryptic sigrok error at
    the bench instead of a clear one here."""
    with pytest.raises(analyser.AnalyserError):
        analyser.build_capture_argv(sample_rate_hz=1_000_000, samples=1000,
                                    channels=[f"D{i}" for i in range(9)],
                                    output_file=tmp_path / "c.sr")


def test_an_unknown_channel_name_is_refused(tmp_path):
    with pytest.raises(analyser.AnalyserError):
        analyser.build_capture_argv(sample_rate_hz=1_000_000, samples=1000,
                                    channels=["PWMA"], output_file=tmp_path / "c.sr")


def test_zero_channels_is_refused(tmp_path):
    with pytest.raises(analyser.AnalyserError):
        analyser.build_capture_argv(sample_rate_hz=1_000_000, samples=1000,
                                    channels=[], output_file=tmp_path / "c.sr")


@pytest.mark.parametrize("bad", [0, -1])
def test_a_non_positive_sample_count_is_refused(tmp_path, bad):
    with pytest.raises(analyser.AnalyserError):
        analyser.build_capture_argv(sample_rate_hz=1_000_000, samples=bad,
                                    channels=["D0"], output_file=tmp_path / "c.sr")


@pytest.mark.parametrize("bad", [0, -1])
def test_a_non_positive_sample_rate_is_refused(tmp_path, bad):
    with pytest.raises(analyser.AnalyserError):
        analyser.build_capture_argv(sample_rate_hz=bad, samples=1000,
                                    channels=["D0"], output_file=tmp_path / "c.sr")


# --------------------------------------------------------------------------
# The channel map
# --------------------------------------------------------------------------


def test_the_channel_map_has_eight_channels():
    assert len(analyser.CHANNEL_MAP) == 8


def test_the_channel_map_names_no_motor_output():
    """The eight legitimate probe points are logic signals and nothing else on
    this bench is probeable."""
    joined = " ".join(analyser.CHANNEL_MAP.values()).upper()
    for net in ("AO1", "AO2", "BO1", "BO2"):
        assert net not in joined


def test_the_instrumentation_channels_are_d6_and_d7():
    """Loop tick and compute-busy: how loop period and compute time get
    measured with no debugger attached."""
    assert analyser.LOOP_TICK_CHANNEL == "D6"
    assert analyser.COMPUTE_BUSY_CHANNEL == "D7"
    assert "LOOP_TICK" in analyser.CHANNEL_MAP["D6"].upper()
    assert "COMPUTE_BUSY" in analyser.CHANNEL_MAP["D7"].upper()


# --------------------------------------------------------------------------
# Talking to the (fake) process
# --------------------------------------------------------------------------


def test_scan_runs_exactly_one_command(fake_runner):
    analyser.scan(runner=fake_runner)
    assert len(fake_runner.calls) == 1


def test_scan_runs_sigrok_cli_with_scan(fake_runner):
    analyser.scan(runner=fake_runner)
    assert "--scan" in fake_runner.last
    assert fake_runner.last[0].endswith("sigrok-cli")


def test_scan_returns_the_parsed_device(fake_runner):
    assert analyser.scan(runner=fake_runner)[0]["driver"] == "fx2lafw"


def test_discovery_asks_the_device(fake_runner):
    assert analyser.discover_sample_rates(runner=fake_runner) == \
        list(SIGROK_SHOW_RATES_HZ)
    assert any("--show" in call for call in fake_runner.joined)


def test_a_failed_scan_raises(fake_runner):
    fake_runner.register("--scan", stdout="", returncode=1,
                         stderr="sr: usb device not found")
    with pytest.raises(analyser.AnalyserError):
        analyser.scan(runner=fake_runner)


def test_the_error_carries_sigroks_own_complaint(fake_runner):
    """At the bench the useful information is sigrok's message, not ours."""
    fake_runner.register("--scan", stdout="", returncode=1,
                         stderr="sr: usb device not found")
    with pytest.raises(analyser.AnalyserError) as excinfo:
        analyser.scan(runner=fake_runner)
    assert "usb device not found" in str(excinfo.value)


def test_a_capture_runs_the_argv_it_built(fake_runner, tmp_path):
    out = tmp_path / "capture.sr"
    argv = analyser.capture(1_000_000, 1000, ["D0", "D1"], out, runner=fake_runner)
    assert fake_runner.last == [str(a) for a in argv]


def test_a_capture_returns_its_argv_for_the_manifest(fake_runner, tmp_path):
    """A run has to be repeatable by hand from what the manifest records."""
    argv = analyser.capture(1_000_000, 1000, ["D0"], tmp_path / "c.sr",
                            runner=fake_runner)
    assert "--samples" in argv and "1000" in argv


def test_a_failed_capture_raises_rather_than_returning_a_partial_file(fake_runner,
                                                                     tmp_path):
    """A truncated .sr that looks fine is how you publish a jitter histogram
    computed from three loop iterations."""
    fake_runner.default = ("", 1, "sr: device stopped responding")
    with pytest.raises(analyser.CaptureFailed):
        analyser.capture(1_000_000, 1000, ["D0"], tmp_path / "c.sr",
                         runner=fake_runner)


def test_a_capture_creates_its_parent_directory(fake_runner, tmp_path):
    out = tmp_path / "experiments" / "motor-char" / "motor-0" / "capture.sr"
    analyser.capture(1_000_000, 1000, ["D0"], out, runner=fake_runner)
    assert out.parent.is_dir()


# --------------------------------------------------------------------------
# Capabilities value object
# --------------------------------------------------------------------------


def caps(rates=SIGROK_SHOW_RATES_HZ, discrete=True):
    return analyser.AnalyserCapabilities(
        driver="fx2lafw", description="Saleae Logic",
        channels=tuple(BENCH_CHANNELS), sample_rates_hz=tuple(rates),
        is_discrete=discrete)


def test_capabilities_report_the_channel_count():
    assert caps().channel_count == 8


def test_capabilities_report_the_maximum_rate():
    assert caps().max_sample_rate_hz == 24_000_000


def test_capabilities_with_no_rates_refuse_to_invent_a_maximum():
    with pytest.raises(analyser.AnalyserError):
        caps(rates=()).max_sample_rate_hz


def test_a_discrete_device_supports_only_the_listed_rates():
    assert caps().supports(1_000_000) is True
    assert caps().supports(1_100_000) is False


def test_a_range_device_supports_anything_inside_the_range():
    device = caps(rates=(20_000, 24_000_000), discrete=False)
    assert device.supports(1_100_000) is True
    assert device.supports(30_000_000) is False


def test_the_nearest_supported_rate_rounds_down():
    """Over-requesting an FX2 gives dropped samples, which look like phantom
    edges - a far more expensive failure than slightly coarser timing."""
    assert caps().nearest_supported(1_500_000) == 1_000_000


def test_the_nearest_supported_rate_never_goes_below_the_device_minimum():
    assert caps().nearest_supported(1) == 20_000


# --------------------------------------------------------------------------
# Decoding a capture, without a capture
# --------------------------------------------------------------------------


def square_wave(period_samples: int, cycles: int, bit: int = 0) -> list[int]:
    half = period_samples // 2
    return [(bit if i % period_samples < half else 0)
            for i in range(period_samples * cycles)]


def test_a_written_srzip_decodes_back(tmp_path):
    """`write_srzip` produces a real zip, so the dry-run path exercises the
    same bytes the hardware would produce rather than fabricating a decoded
    object in memory."""
    path = tmp_path / "c.sr"
    analyser.write_srzip(str(path), 1_000_000, ["D0"], square_wave(10, 5, bit=1))
    decoded = analyser.decode_srzip(str(path))
    assert decoded.sample_rate_hz == 1_000_000
    assert decoded.n_samples == 50


def test_a_decoded_square_wave_has_the_right_number_of_edges(tmp_path):
    path = tmp_path / "c.sr"
    analyser.write_srzip(str(path), 1_000_000, ["D0"], square_wave(10, 5, bit=1))
    trace = analyser.decode_srzip(str(path)).trace("D0")
    assert len(trace.edges) == 9  # starts high; 5 falls + 4 rises


def test_edge_times_come_from_the_recorded_sample_rate(tmp_path):
    """Every timing number in the report is sample_index / sample_rate. If the
    rate in the file were wrong, so is every microsecond quoted from it."""
    path = tmp_path / "c.sr"
    analyser.write_srzip(str(path), 1_000_000, ["D0"], square_wave(10, 5, bit=1))
    trace = analyser.decode_srzip(str(path)).trace("D0")
    assert trace.edges[0].time_s == pytest.approx(5e-6)


def test_the_capture_duration_is_samples_over_rate(tmp_path):
    path = tmp_path / "c.sr"
    analyser.write_srzip(str(path), 1_000_000, ["D0"], square_wave(10, 5, bit=1))
    assert analyser.decode_srzip(str(path)).duration_s == pytest.approx(50e-6)


def test_a_missing_capture_file_raises(tmp_path):
    with pytest.raises(analyser.CaptureFailed):
        analyser.decode_srzip(str(tmp_path / "nope.sr"))


def test_a_file_that_is_not_a_zip_raises(tmp_path):
    path = tmp_path / "c.sr"
    path.write_bytes(b"not a zip file at all")
    with pytest.raises(analyser.CaptureFailed):
        analyser.decode_srzip(str(path))


def test_asking_for_a_channel_that_was_not_captured_raises(tmp_path):
    """Better than returning an empty trace, which would silently read as "the
    signal never moved"."""
    path = tmp_path / "c.sr"
    analyser.write_srzip(str(path), 1_000_000, ["D0"], square_wave(10, 5, bit=1))
    with pytest.raises(analyser.AnalyserError):
        analyser.decode_srzip(str(path)).trace("D6")
