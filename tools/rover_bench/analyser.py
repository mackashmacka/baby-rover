"""analyser.py — a thin wrapper over `sigrok-cli` for the FX2 logic analyser.

WHY sigrok-cli AND NOTHING GRAPHICAL
------------------------------------
The GUI viewer that ships with sigrok is for human eyes.  It cannot appear
anywhere in an automated path, because a capture that needs a mouse click
cannot be repeated identically on motor 4 three days after motor 1, and it
hangs outright on a machine with no display.  Everything here shells out to
`sigrok-cli`, headless, and every argument it uses is recorded in the manifest.

WHY THE SAMPLE RATE IS DISCOVERED AT RUNTIME
--------------------------------------------
The repo does not document this clone's maximum sample rate anywhere, and the
FX2 clones genuinely differ (the ceiling depends on how many channels are
enabled and on the USB host).  Hardcoding a rate would be inventing a fact.
`discover_sample_rates()` asks the device what it supports, and the number
actually used goes into the manifest.  If you cannot say what rate a capture
was taken at, you cannot say what its timing resolution was, and every edge
measurement in it is unfalsifiable.  There is deliberately **no default** for
`sample_rate_hz` anywhere in this module.

TWO LAYERS, ON PURPOSE
----------------------
* **Module-level functions** (`parse_scan_output`, `choose_sample_rate`,
  `build_capture_argv`, `scan`, `capture`, `decode_srzip`) — stateless.  The
  parsers and the argv builder are pure functions, which is where the bugs
  live and therefore where the tests live.
* **`Analyser`** — the same thing with the subprocess runner injected, for
  `--dry-run` and for anything that wants to hold onto discovered
  capabilities.

.. warning::
   **Never probe AO1/AO2/BO1/BO2.**  Those sit at motor voltage and will
   destroy the analyser (docs/WIRING.md §1 rule 4).  `build_capture_argv`
   refuses a channel list naming one, but the real defence is at the probe
   tip — this can only catch the typo, not the crocodile clip.
"""

from __future__ import annotations

import configparser
import io
import os
import re
import shutil
import subprocess
import zipfile
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Sequence

SIGROK_BIN = "sigrok-cli"
DRIVER = "fx2lafw"

#: The device has eight channels.  Asking for D8 gets a confusing sigrok error
#: at the bench instead of a clear one here.
MAX_CHANNELS = 8

#: The probe map from docs/WIRING.md **§10.2** — the characterisation-bench
#: map, the one with LOOP_TICK and COMPUTE_BUSY on D6/D7.  NOT §8, which is the
#: failsafe map and puts UART TX/RX (GP0/GP1) on those same two channels.  The
#: two maps swap according to the question being asked (§10.3), and clipping D6
#: to pin 1 because you read the wrong section measures a signal that has
#: nothing to do with the control loop.  Names are the sigrok channel names;
#: the value is what is physically on the pin.
CHANNEL_MAP: dict[str, str] = {
    "D0": "PWMA (GP2)",
    "D1": "AIN1 (GP3)",
    "D2": "AIN2 (GP4)",
    "D3": "STBY (GP5)",
    "D4": "ENC_A (GP12)",
    "D5": "ENC_B (GP13)",
    "D6": "LOOP_TICK (GP20)",
    "D7": "COMPUTE_BUSY (GP21)",
}

LOOP_TICK_CHANNEL = "D6"
COMPUTE_BUSY_CHANNEL = "D7"

#: Nets that sit at motor voltage.  Naming one as a probe channel is refused.
MOTOR_OUTPUT_NETS: tuple[str, ...] = ("AO1", "AO2", "BO1", "BO2")


class AnalyserError(RuntimeError):
    """Base class for analyser failures."""


class AnalyserNotFound(AnalyserError):
    """`sigrok-cli` is missing, or it reports no fx2lafw device."""


class CaptureFailed(AnalyserError):
    """sigrok-cli ran but produced no usable capture."""


# --------------------------------------------------------------------------
# subprocess seam
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class CommandResult:
    """What a runner gives back.  Deliberately narrower than
    `subprocess.CompletedProcess` so a fake is trivial to write."""

    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def run_subprocess(argv: Sequence[str], *, timeout_s: float = 120.0) -> CommandResult:
    """Default runner: really execute the command.

    `check=False` on purpose — sigrok-cli's stderr is usually more informative
    than its exit code, and both belong in the error message.  `subprocess.run`
    is looked up on the module at call time so a test can patch it.
    """
    argv = [str(a) for a in argv]
    try:
        proc = subprocess.run(  # noqa: S603 - argv is built here, never a shell string
            argv, capture_output=True, text=True, timeout=timeout_s, check=False,
        )
    except FileNotFoundError as exc:
        raise AnalyserNotFound(
            f"{argv[0]!r} is not on PATH.  Fix:  sudo apt install sigrok-cli   "
            "(or run tools/bench-setup.sh)"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise CaptureFailed(
            f"{' '.join(argv)} did not finish within {timeout_s} s"
        ) from exc
    return CommandResult(tuple(argv), proc.returncode,
                         proc.stdout or "", proc.stderr or "")


Runner = Callable[..., CommandResult]


def _run(argv: Sequence[str], runner: Runner | None = None,
         **kwargs: Any) -> CommandResult:
    result = (runner or run_subprocess)(argv, **kwargs)
    return result


def _demand_ok(result: CommandResult, what: str) -> CommandResult:
    """Raise with sigrok's own complaint attached.

    At the bench the useful information is what sigrok said, not what we would
    have said about it.
    """
    if not result.ok:
        detail = (result.stderr or result.stdout or "").strip()
        raise AnalyserNotFound(
            f"{what} failed (rc={result.returncode}): {detail}"
        )
    return result


# --------------------------------------------------------------------------
# Pure parsers.  These are the bits most likely to be wrong, so they are the
# bits that are pure functions.
# --------------------------------------------------------------------------

_FREQ_UNITS = {"": 1, "hz": 1, "khz": 1_000, "mhz": 1_000_000, "ghz": 1_000_000_000}
_FREQ_RE = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*([kMG]?Hz)?\s*$", re.IGNORECASE)


def parse_frequency(text: str) -> int:
    """`"24 MHz"` -> `24000000`.  Raises on anything it does not understand.

    Rounding to int is safe: sigrok reports rates that are whole hertz.
    """
    match = _FREQ_RE.match(text)
    if not match:
        raise AnalyserError(f"cannot parse a sample rate from {text!r}")
    value, unit = match.group(1), (match.group(2) or "Hz").lower()
    return int(round(float(value) * _FREQ_UNITS[unit]))


def parse_samplerate_spec(text: str) -> tuple[int, ...]:
    """Parse the `samplerate:` line from `sigrok-cli --show`.

    sigrok prints one of two shapes and the difference matters:

      * a discrete list — ``20 kHz, 25 kHz, ..., 24 MHz``.  The device can only
        do those exact rates.
      * a range — ``20 kHz - 24 MHz (in steps of 1 Hz)``.  Anything between the
        endpoints is allowed.

    Returns a sorted tuple.  For a range only the endpoints come back, and
    `AnalyserCapabilities.is_discrete` records which shape it was.
    """
    body = text.split(":", 1)[1] if ":" in text else text
    body = body.strip()
    if not body:
        return ()
    if "-" in body and "," not in body:
        left, _, right = body.partition("-")
        right = right.split("(")[0]
        return tuple(sorted({parse_frequency(left), parse_frequency(right)}))
    rates: list[int] = []
    for chunk in body.split(","):
        chunk = chunk.strip()
        if chunk:
            rates.append(parse_frequency(chunk))
    return tuple(sorted(set(rates)))


_SCAN_RE = re.compile(
    r"^(?P<driver>[A-Za-z0-9_\-]+)"
    r"(?::(?P<opts>\S+))?"
    r"\s+-\s+(?P<desc>.*?)"
    r"(?:\s+with\s+(?P<n>\d+)\s+channels?:\s*(?P<chans>.*))?$"
)


def parse_scan_output(stdout: str) -> list[dict[str, Any]]:
    """Parse `sigrok-cli --scan` into a list of device dicts.

    Typical output::

        The following devices were found:
        fx2lafw:conn=1.10 - Saleae Logic with 8 channels: D0 D1 D2 D3 D4 D5 D6 D7

    `conn` is how you address one specific device when two are plugged in, so
    it is kept rather than discarded.  Unrecognised lines are skipped rather
    than raising: sigrok changes its banner text between releases and a new
    banner must not take the bench down.
    """
    devices: list[dict[str, Any]] = []
    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        lowered = line.lower()
        if lowered.startswith("the following devices") or lowered.startswith("no devices"):
            continue
        match = _SCAN_RE.match(line)
        if not match:
            continue
        opts = match.group("opts") or ""
        conn = ""
        for part in opts.split(":"):
            if part.startswith("conn="):
                conn = part[len("conn="):]
        devices.append({
            "driver": match.group("driver"),
            "conn": conn,
            "description": match.group("desc").strip(),
            "channels": (match.group("chans") or "").split(),
        })
    return devices


def parse_show_output(stdout: str) -> dict[str, Any]:
    """Parse `sigrok-cli --driver fx2lafw --show`.

    Returns ``{"samplerates_hz": [...], "channels": [...], "description": str,
    "discrete": bool}``.

    Raises when there is no `samplerate` line at all: a device whose rates
    cannot be read is a device we must not guess for.  Guessing produces a
    capture that looks fine and whose timebase is silently wrong.
    """
    rates: tuple[int, ...] = ()
    discrete = True
    found = False
    lines = (stdout or "").splitlines()
    for i, line in enumerate(lines):
        stripped = line.strip()
        if ":" not in stripped:
            continue
        key = stripped.split(":", 1)[0].split("(", 1)[0].strip().lower()
        # libsigrok labels this line three different ways depending on version:
        #   "samplerate:"                      (inline list or range)
        #   "samplerate - supported samplerates:"   (0.5.x, one rate per line)
        # Match the leading token so all of them land here.
        if key != "samplerate" and not key.startswith("samplerate"):
            continue
        body = stripped.split(":", 1)[1]
        found = True
        if body.strip():
            # Inline form: a comma list, or a "lo - hi (in steps of N)" range.
            discrete = "," in body or "-" not in body
            rates = parse_samplerate_spec(stripped)
            break
        # Multi-line form (libsigrok 0.5.2, which is what Ubuntu 24.04 ships):
        # the header ends in a colon and each supported rate follows on its own
        # indented line, the active one suffixed "(current)". Consume until the
        # indentation stops or a new key begins.
        collected: list[int] = []
        for follow in lines[i + 1:]:
            if not follow.strip():
                break
            if not (follow.startswith(" ") or follow.startswith("\t")):
                break
            token = follow.strip().split("(")[0].strip()
            if not token:
                continue
            try:
                collected.append(parse_frequency(token))
            except Exception:  # noqa: BLE001 - a new key, not a rate: stop
                break
        if collected:
            rates = tuple(sorted(set(collected)))
            discrete = True
        break
    if not found or not rates:
        raise AnalyserError(
            "sigrok --show reported no sample rates.  Refusing to assume one: "
            "the rate is a device property and the repo documents no maximum."
        )
    devices = parse_scan_output(stdout)
    device = next((d for d in devices if d["driver"] == DRIVER),
                  devices[0] if devices else {})
    return {
        "samplerates_hz": list(rates),
        "channels": list(device.get("channels", [])),
        "description": device.get("description", ""),
        "discrete": discrete,
    }


def choose_sample_rate(available_hz: Sequence[int], minimum_hz: int) -> int:
    """Cheapest advertised rate that is at least `minimum_hz`.

    Cheapest, not fastest: samples are memory and the FX2 streams over USB 2,
    so an unnecessarily high rate is how a capture starts dropping samples —
    and dropped samples look exactly like phantom edges.

    Raises when nothing on offer is fast enough.  Silently returning the
    maximum would hand back a capture that aliases and looks plausible; the
    refusal forces the question "what am I actually able to measure here?".
    """
    rates = sorted(int(r) for r in available_hz)
    if not rates:
        raise AnalyserError(
            "no sample rates available — discover them from the device first"
        )
    for rate in rates:
        if rate >= minimum_hz:
            return rate
    raise AnalyserError(
        f"no available sample rate reaches {minimum_hz} Hz "
        f"(maximum offered is {rates[-1]} Hz).  Measure something slower, or "
        "accept a coarser timebase deliberately."
    )


def validate_channels(channels: Sequence[str]) -> list[str]:
    """Refuse a channel list that would damage the instrument or confuse sigrok.

    Three refusals, in cost order: probing a motor output destroys the
    analyser; more than eight channels does not exist; an unknown name is a
    typo that would otherwise reach sigrok as a cryptic complaint.
    """
    if not channels:
        raise AnalyserError("refusing to capture zero channels")
    names = [str(c).strip() for c in channels]
    for name in names:
        if name.upper() in MOTOR_OUTPUT_NETS:
            raise AnalyserError(
                f"refusing to probe {name}: it sits at motor voltage and will "
                "destroy the analyser (docs/WIRING.md §1 rule 4)"
            )
    if len(names) > MAX_CHANNELS:
        raise AnalyserError(
            f"{len(names)} channels requested; the device has {MAX_CHANNELS} "
            f"({', '.join(CHANNEL_MAP)})"
        )
    unknown = [n for n in names if n not in CHANNEL_MAP]
    if unknown:
        raise AnalyserError(
            f"unknown analyser channel(s) {unknown}; known channels are "
            f"{', '.join(CHANNEL_MAP)} (see docs/WIRING.md §10.2)"
        )
    return names


def build_capture_argv(sample_rate_hz: int, samples: int,
                       channels: Sequence[str], output_file: str | os.PathLike[str],
                       *, binary: str = SIGROK_BIN,
                       driver: str = DRIVER) -> list[str]:
    """Build the exact `sigrok-cli` argv for one capture.

    A list, never a shell string: a shell string is a quoting bug waiting for
    a path with a space in it.

    `sample_rate_hz` has **no default**, and that is the most important line in
    this module.  If it could be omitted, some call site eventually omits it
    and the timebase quietly becomes whatever the author assumed on a Tuesday.

    `--samples` rather than a wall-clock limit: the sample count is exactly
    reproducible, so two runs of the same experiment produce captures of
    identical length, which is what makes their histograms comparable.
    """
    names = validate_channels(channels)
    if int(sample_rate_hz) <= 0:
        raise AnalyserError(f"sample rate must be positive, got {sample_rate_hz}")
    if int(samples) <= 0:
        raise AnalyserError(f"sample count must be positive, got {samples}")
    return [
        binary,
        "--driver", driver,
        "--config", f"samplerate={int(sample_rate_hz)}",
        "--channels", ",".join(names),
        "--samples", str(int(samples)),
        "--output-format", "srzip",
        "--output-file", str(output_file),
    ]


# --------------------------------------------------------------------------
# Talking to the device
# --------------------------------------------------------------------------

def scan(*, binary: str = SIGROK_BIN, runner: Runner | None = None) -> list[dict[str, Any]]:
    """`sigrok-cli --scan` -> parsed devices (possibly empty)."""
    result = _run([binary, "--scan"], runner)
    _demand_ok(result, f"{binary} --scan")
    return parse_scan_output(result.stdout)


def show(*, binary: str = SIGROK_BIN, driver: str = DRIVER,
         runner: Runner | None = None) -> dict[str, Any]:
    """`sigrok-cli --driver <d> --show` -> parsed capabilities."""
    argv = [binary, "--driver", driver, "--show"]
    result = _run(argv, runner)
    _demand_ok(result, " ".join(argv))
    return parse_show_output(result.stdout)


def discover_sample_rates(*, binary: str = SIGROK_BIN, driver: str = DRIVER,
                          runner: Runner | None = None) -> list[int]:
    """Ask the device which rates it supports.  Never assume; always ask."""
    return show(binary=binary, driver=driver, runner=runner)["samplerates_hz"]


def capture(sample_rate_hz: int, samples: int, channels: Sequence[str],
            output_file: str | os.PathLike[str], *,
            binary: str = SIGROK_BIN, driver: str = DRIVER,
            runner: Runner | None = None,
            timeout_s: float | None = None) -> list[str]:
    """Run one capture.  Returns the argv that was used, for the manifest."""
    argv = build_capture_argv(sample_rate_hz, samples, channels, output_file,
                              binary=binary, driver=driver)
    parent = os.path.dirname(os.path.abspath(str(output_file)))
    if parent:
        os.makedirs(parent, exist_ok=True)
    duration_s = samples / float(sample_rate_hz)
    budget = timeout_s if timeout_s is not None else max(30.0, duration_s * 4 + 15.0)
    result = _run(argv, runner, timeout_s=budget)
    if not result.ok:
        detail = (result.stderr or result.stdout or "").strip()
        raise CaptureFailed(
            f"capture failed (rc={result.returncode}): {detail}\n"
            f"argv: {' '.join(argv)}"
        )
    return argv


#: Module-level alias, so `Analyser.capture` (duration-based) can call the
#: module-level `capture` (sample-count based) without the name looking like a
#: recursive call to a reader.  Same function, clearer at the call site.
_run_capture = capture


# --------------------------------------------------------------------------
# Capabilities value object
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class AnalyserCapabilities:
    """What this specific analyser says it can do, discovered at runtime."""

    driver: str
    description: str
    channels: tuple[str, ...]
    sample_rates_hz: tuple[int, ...]
    is_discrete: bool = True

    @property
    def channel_count(self) -> int:
        return len(self.channels)

    @property
    def max_sample_rate_hz(self) -> int:
        if not self.sample_rates_hz:
            raise AnalyserError(
                "the analyser reported no sample rate.  Refusing to guess one."
            )
        return self.sample_rates_hz[-1]

    def supports(self, rate_hz: int) -> bool:
        if not self.sample_rates_hz:
            return False
        if self.is_discrete:
            return rate_hz in self.sample_rates_hz
        return self.sample_rates_hz[0] <= rate_hz <= self.sample_rates_hz[-1]

    def nearest_supported(self, wanted_hz: int) -> int:
        """Largest supported rate that is <= wanted, else the smallest offered.

        Rounding *down* rather than to nearest: over-requesting an FX2 gives
        dropped samples, which look like phantom edges — a more expensive
        failure than slightly coarser timing.
        """
        if not self.sample_rates_hz:
            raise AnalyserError("no sample rates reported; cannot choose one")
        if not self.is_discrete:
            lo, hi = self.sample_rates_hz[0], self.sample_rates_hz[-1]
            return max(lo, min(wanted_hz, hi))
        below = [r for r in self.sample_rates_hz if r <= wanted_hz]
        return below[-1] if below else self.sample_rates_hz[0]

    @classmethod
    def from_show(cls, parsed: dict[str, Any], driver: str = DRIVER) -> "AnalyserCapabilities":
        return cls(
            driver=driver,
            description=parsed.get("description", ""),
            channels=tuple(parsed.get("channels", ())),
            sample_rates_hz=tuple(parsed.get("samplerates_hz", ())),
            is_discrete=bool(parsed.get("discrete", True)),
        )


# --------------------------------------------------------------------------
# Decoding a .sr file
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Edge:
    """One transition on one channel."""

    sample_index: int
    time_s: float
    level: int          # the level *after* the transition, 0 or 1


@dataclass(frozen=True)
class ChannelTrace:
    name: str
    initial_level: int
    edges: tuple[Edge, ...]

    @property
    def edge_times_s(self) -> tuple[float, ...]:
        return tuple(e.time_s for e in self.edges)


@dataclass(frozen=True)
class DecodedCapture:
    """A decoded `.sr` file: sample rate plus per-channel edge lists."""

    path: str
    sample_rate_hz: int
    n_samples: int
    unitsize: int
    channels: dict[str, ChannelTrace] = field(default_factory=dict)

    @property
    def duration_s(self) -> float:
        return self.n_samples / self.sample_rate_hz if self.sample_rate_hz else 0.0

    def trace(self, name: str) -> ChannelTrace:
        if name not in self.channels:
            raise AnalyserError(
                f"channel {name!r} is not in this capture "
                f"(have: {', '.join(sorted(self.channels))})"
            )
        return self.channels[name]


def decode_srzip(path: str | os.PathLike[str]) -> DecodedCapture:
    """Decode a sigrok `srzip` (`.sr`) file into per-channel edge lists.

    The format is a zip containing:

      * ``version``  — the format version, "2" today
      * ``metadata`` — an INI file: sample rate, channel names, `unitsize`
      * ``logic-1-1``, ``logic-1-2``, ... — raw little-endian samples, one
        `unitsize`-byte word per sample, bit N = channel N

    WHY DECODE IT HERE instead of asking sigrok for CSV: a CSV of two million
    samples is ~40 MB of text to write, read and parse, and 99.99% of the rows
    say "nothing changed".  Edges are the only information in a digital
    capture.  Decoding straight from the zip keeps the `.sr` as the archival
    artifact — it is what a human opens when a number looks wrong — while the
    analysis only ever touches transitions.

    The inner loop compares whole *words* first and only splits into channels
    when a word changes, which is what makes a multi-megasample capture of a
    100 Hz loop tick decode in well under a second in pure Python.
    """
    path = str(path)
    if not os.path.exists(path):
        raise CaptureFailed(f"{path} does not exist")
    try:
        archive = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise CaptureFailed(f"{path} is not a valid .sr (srzip) file") from exc

    with archive:
        names = set(archive.namelist())
        if "metadata" not in names:
            raise CaptureFailed(f"{path} has no metadata member; not an srzip")
        parser = configparser.ConfigParser()
        parser.read_string(archive.read("metadata").decode("utf-8", "replace"))

        device_sections = [s for s in parser.sections() if s.startswith("device")]
        if not device_sections:
            raise CaptureFailed(f"{path} metadata has no [device N] section")
        device = parser[device_sections[0]]

        samplerate_text = device.get("samplerate", "").strip()
        sample_rate_hz = parse_frequency(samplerate_text) if samplerate_text else 0
        unitsize = int(device.get("unitsize", "1"))
        if unitsize < 1:
            raise CaptureFailed(f"{path}: nonsensical unitsize {unitsize}")

        # probeN=NAME.  N is 1-based and maps to bit N-1 of each sample word.
        probes: dict[int, str] = {}
        for key, value in device.items():
            match = re.fullmatch(r"probe(\d+)", key)
            if match and value.strip():
                probes[int(match.group(1))] = value.strip()
        if not probes:
            raise CaptureFailed(f"{path} metadata lists no probes")

        capturefile = device.get("capturefile", "logic-1").strip()
        chunks = sorted(
            (n for n in names if n == capturefile or n.startswith(capturefile + "-")),
            key=_chunk_sort_key,
        )
        if not chunks:
            raise CaptureFailed(f"{path} contains no {capturefile}* data chunks")
        payload = b"".join(archive.read(n) for n in chunks)

    n_samples = len(payload) // unitsize
    traces = _payload_to_traces(payload, n_samples, unitsize, probes, sample_rate_hz)
    return DecodedCapture(path=path, sample_rate_hz=sample_rate_hz,
                          n_samples=n_samples, unitsize=unitsize, channels=traces)


def _chunk_sort_key(name: str) -> tuple[int, str]:
    """`logic-1-10` must sort after `logic-1-9`, so sort the suffix numerically."""
    tail = name.rsplit("-", 1)[-1]
    return (int(tail), name) if tail.isdigit() else (0, name)


def _payload_to_traces(payload: bytes, n_samples: int, unitsize: int,
                       probes: dict[int, str],
                       sample_rate_hz: int) -> dict[str, ChannelTrace]:
    """Word-diff the sample stream into per-channel edge lists."""
    order = sorted(probes)
    masks = {probe: 1 << (probe - 1) for probe in order}
    dt = 1.0 / sample_rate_hz if sample_rate_hz else 0.0

    if n_samples == 0:
        return {probes[p]: ChannelTrace(probes[p], 0, ()) for p in order}

    if unitsize == 1:
        words: Iterable[int] = payload[:n_samples]
        first = payload[0]
    else:
        words = (
            int.from_bytes(payload[i * unitsize:(i + 1) * unitsize], "little")
            for i in range(n_samples)
        )
        first = int.from_bytes(payload[:unitsize], "little")

    initial = {p: 1 if first & masks[p] else 0 for p in order}
    edges: dict[int, list[Edge]] = {p: [] for p in order}

    previous = first
    for index, word in enumerate(words):
        if word == previous:
            continue
        changed = word ^ previous
        for probe in order:
            mask = masks[probe]
            if changed & mask:
                edges[probe].append(Edge(index, index * dt, 1 if word & mask else 0))
        previous = word

    return {probes[p]: ChannelTrace(probes[p], initial[p], tuple(edges[p]))
            for p in order}


def write_srzip(path: str, sample_rate_hz: int, channel_names: Sequence[str],
                samples: Sequence[int], unitsize: int = 1) -> str:
    """Write a minimal but *real* srzip file.

    This lives in the library rather than in the tests because `fakes.FakeSigrok`
    needs it to produce a genuine `.sr` during `--dry-run`.  That matters: a dry
    run that fabricates a decoded object in memory proves nothing about
    `decode_srzip`, whereas one that writes a real zip and decodes it exercises
    the same bytes the hardware would produce.
    """
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    metadata = io.StringIO()
    metadata.write("[global]\nsigrok version=0.0.0-rover-bench-fake\n\n")
    metadata.write("[device 1]\ncapturefile=logic-1\n")
    metadata.write(f"total probes={len(channel_names)}\n")
    metadata.write(f"samplerate={sample_rate_hz} Hz\n")
    for index, name in enumerate(channel_names, start=1):
        metadata.write(f"probe{index}={name}\n")
    metadata.write(f"unitsize={unitsize}\n")

    payload = b"".join(int(s).to_bytes(unitsize, "little") for s in samples)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("version", "2")
        archive.writestr("metadata", metadata.getvalue())
        archive.writestr("logic-1-1", payload)
    return path


# --------------------------------------------------------------------------
# The injectable facade
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class CaptureResult:
    """Everything needed to defend a capture later."""

    path: str
    sample_rate_hz: int
    channels: tuple[str, ...]
    duration_s: float
    samples: int
    argv: tuple[str, ...]


class Analyser:
    """`sigrok-cli` with its subprocess runner injected.

    Same behaviour as the module-level functions; holds the runner so
    `--dry-run` and the tests can swap in a fake without patching anything
    global.
    """

    def __init__(self, *, runner: Runner | None = None,
                 binary: str = SIGROK_BIN,
                 driver: str = DRIVER,
                 which: Callable[[str], str | None] = shutil.which) -> None:
        self.runner = runner or run_subprocess
        self.binary = binary
        self.driver = driver
        self._which = which

    # -- discovery -------------------------------------------------------

    def available(self) -> bool:
        """Is `sigrok-cli` even installed?  Used by doctor and by safety."""
        return self._which(self.binary) is not None

    def version(self) -> str:
        result = _run([self.binary, "--version"], self.runner)
        _demand_ok(result, f"{self.binary} --version")
        lines = result.stdout.splitlines()
        return lines[0].strip() if lines else ""

    def scan(self) -> list[dict[str, Any]]:
        return scan(binary=self.binary, runner=self.runner)

    def present(self) -> bool:
        """True when an fx2lafw device answers a scan.

        Swallows `AnalyserError` so `doctor` can report "sigrok missing" and
        "analyser missing" as two separate lines with two separate fixes.
        """
        try:
            return any(d["driver"] == self.driver for d in self.scan())
        except AnalyserError:
            return False

    def capabilities(self) -> AnalyserCapabilities:
        """Ask the device what it can do.  Never assume a rate."""
        parsed = show(binary=self.binary, driver=self.driver, runner=self.runner)
        return AnalyserCapabilities.from_show(parsed, self.driver)

    def choose_sample_rate(self, minimum_hz: int,
                           capabilities: AnalyserCapabilities | None = None) -> int:
        """Pick the rate actually used, and let the caller record it.

        The manifest stores the *chosen* rate, never the wanted one.  Those
        differ whenever the hardware cannot do what was asked, and the
        difference is exactly what makes two runs incomparable.
        """
        caps = capabilities or self.capabilities()
        return choose_sample_rate(caps.sample_rates_hz, minimum_hz)

    # -- capture ---------------------------------------------------------

    def capture(self, channels: Sequence[str], sample_rate_hz: int,
                duration_s: float, out_path: str, *,
                timeout_s: float | None = None) -> CaptureResult:
        """Capture `duration_s` of `channels` at `sample_rate_hz` into `out_path`.

        Raises rather than returning a partial capture: a truncated `.sr` that
        looks fine is how you end up publishing a jitter histogram computed
        from three loop iterations.
        """
        if duration_s <= 0:
            raise AnalyserError(f"duration_s must be positive, got {duration_s}")
        samples = int(round(sample_rate_hz * duration_s))
        argv = _run_capture(sample_rate_hz, samples, channels, out_path,
                       binary=self.binary, driver=self.driver,
                       runner=self.runner, timeout_s=timeout_s)
        if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
            raise CaptureFailed(
                f"sigrok-cli reported success but {out_path} is missing or empty.  "
                "Usually a permissions problem: check the plugdev group and the "
                "udev rule (rover-bench doctor)."
            )
        return CaptureResult(path=out_path, sample_rate_hz=sample_rate_hz,
                             channels=tuple(channels), duration_s=duration_s,
                             samples=samples, argv=tuple(argv))
