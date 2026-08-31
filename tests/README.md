# tests — the host-side suite

`CLAUDE.md`'s CLOSE ritual will not let a branch close until "the FULL test
suite is green **and recorded**". This is the suite. `NEXT-STEPS.md` used to
say there wasn't one, "or the ritual is theatre".

```bash
make test        # the gate: fast, no hardware, 80% line coverage enforced
make test-hw     # opt-in: needs a real Pico and the FX2 analyser attached
make lint        # ruff over tools/ pi/ tests/ (bug rules, not style)
make coverage    # HTML report -> htmlcov/index.html
make doctor      # what is installed, what is plugged in, what is missing
```

Everything works from a bare checkout. The first `make test` builds `.venv/`
(gitignored) and installs `tests/requirements.txt` into it. Nothing is
installed system-wide, and running `make` is the "ask before installing"
CLAUDE.md requires — the install is opt-in and repo-local.

---

## Why hardware is excluded by default

`pytest.ini` carries `-m "not hardware"` in `addopts`. That is not a
convenience, it is the reason the suite gets run at all.

A suite that *usually* needs no hardware is a suite someone runs once at the
bench, with the analyser plugged in, and then stops running — because on the
laptop on the train it errors for reasons that have nothing to do with the
code. Excluding hardware by default means `pytest` is always meaningful,
always fast (about four seconds for ~1100 tests), and always safe to run.

It is enforced, not merely intended. `tests/conftest.py` installs an autouse
fixture that **blocks `subprocess.run`/`Popen`/`os.system` and
`serial.Serial`** for every non-hardware test. A test that tries to shell out
to `sigrok-cli` or open `/dev/ttyACM0` raises `HardwareAccessInTest` with a
message telling you to inject a fake instead. Two escape hatches:

| Marker | Meaning |
|---|---|
| `@pytest.mark.hardware` | Needs a real Pico and/or the FX2 analyser. Deselected by default; run with `make test-hw`. |
| `@pytest.mark.slow` | Takes more than about a second. Still runs by default; deselect with `-m "not slow"`. |
| `@pytest.mark.allow_subprocess` | Opts one test out of the no-subprocess guard. One user today: compiling the host-pure firmware. |

Markers are strict (`--strict-markers`), so a typo is an error rather than a
silently skipped test.

---

## What is tested, and where

| File | Covers |
|---|---|
| `test_firmware_protocol.py` | The C command parser, via ctypes. Round-trips and **every malformed input** — unknown command, wrong arity, out-of-range duty, NaN, inf, hex literal, empty line, overlong line, embedded NUL, missing terminator. |
| `test_firmware_control.py` | The C control maths: PID (windup, derivative-on-measurement, saturation), ticks→rad/s, PWM levels, the TB6612 truth table, feedforward. |
| `test_link.py` | The serial link: framing, reply parsing, retries, port discovery, and that `stop()` never retries. |
| `test_analyser.py` | The sigrok wrapper: capability discovery, rate choice, argv construction, `.sr` decoding. |
| `test_safety.py` | One test per interlock, each asserting a refusal. Plus `DriveGuard`. |
| `test_manifest.py` | Provenance, the unmeasured `ticks_per_rev`, and the reproducibility diff. |
| `test_registry.py` | The 7-column schema, idempotent appends, and not corrupting a hand-edited file. |
| `test_storage.py` | Deterministic paths, and the units-in-column-names rule. |
| `test_experiment.py` | The analysis maths, and that a run always leaves its record. |
| `test_doctor.py` | "Why doesn't my bench work?", against a described broken machine. |
| `test_cli.py` | Every subcommand runs under `--dry-run` and exits 0. |
| `test_docs_consistency.py` | **Docs vs firmware vs code.** See below. |
| `test_property_parsers.py` | Hypothesis properties over the parsers and the PID. |
| `test_harness_selfcheck.py` | The harness testing itself: the gate is real, the markers are registered, the fakes behave like the things they fake. |
| `test_hardware_smoke.py` | `@pytest.mark.hardware`. The pre-session smoke test `docs/PLAN.md` §10 asks for. |

### The firmware is tested from Python

`firmware/src/protocol.h` and `control.h` both declare themselves **HOST-PURE:
no pico-sdk includes**. `conftest.py` cashes that promise in — it compiles
`protocol.c` + `control.c` + `tests/firmware_shim.c` into a shared library with
`cc` and drives them through ctypes. The compile *is* the host-purity check: the
day someone adds `#include "pico/stdlib.h"` to either file, it fails and says so.

`firmware_shim.c` exists so the tests carry no copy of the struct layout. Python
only ever sees an opaque buffer of `shim_state_size()` bytes and calls
accessors, so adding a field to `struct rover_state` cannot silently make every
test read the wrong offset. The whole-struct byte comparison is also what makes
`test_a_rejected_command_leaves_the_state_bit_for_bit_unchanged` a real
assertion rather than a check of the four fields someone remembered.

No compiler, or no firmware sources yet? Those tests **skip** with a reason.

### `test_docs_consistency.py` is the cheap high-value one

The bench pin map exists in five places — `docs/WIRING.md` §10.2,
`docs/BENCH.md` §3, `firmware/src/board_config.h`,
`tools/rover_bench/analyser.py`, and the operator's memory. Four are
machine-readable, so they are compared. It also asserts:

* no document instructs probing `AO1`/`AO2`/`BO1`/`BO2` (a line mentioning one
  in a *probing* context must be a prohibition; "the red wire goes to AO1" is
  wiring, and fine);
* no pin table assigns GP23/24/25/29 (the CYW43439 pins);
* PWM frequency, loop rate, baud and failsafe timeout agree between the docs
  and the firmware;
* the §8 and §10.2 analyser maps differ at exactly D6/D7, which is the
  documented swap and not drift;
* `ticks_per_rev` is still flagged as disputed and unmeasured.

Documentation drift is the most likely real failure mode on this project, and
these tests cost nothing to run.

---

## Writing a test here

* **Assert something, not that nothing crashed.** Every test in this suite has
  a docstring saying what would break in the real world if it failed. If you
  cannot write that sentence, the test is not worth its maintenance.
* **Inject, don't patch.** `Link` takes a transport, `Analyser` takes a runner,
  `DriveGuard` takes a clock, `doctor` takes an `Environment`. Use the seam.
  The fixtures are in `conftest.py`: `fake_transport`, `fake_pico`,
  `fake_runner`, `frozen_clock`, `temp_repo`, `synthetic_motor`, `rover_state`.
* **A regression test for every bug fixed.** Every one. Three tests here are
  labelled `REGRESSION` and name the bug in their docstring.
* **A module that has not landed yet** gets tests anyway:

  ```python
  thing = import_or_none("rover_bench.thing")
  pytestmark = pytest.mark.xfail(thing is None, reason="...", strict=False)
  ```

  The tests then activate by themselves the day the module appears, instead of
  being forgotten. `xfail_strict` is off so an early landing is an XPASS, not a
  failure. When one xpasses, delete the marker — it is a real test now.

---

## The coverage gate

`.coveragerc`: `source = tools/rover_bench`, `fail_under = 80`,
`show_missing = True`. `coverage report` exits 2 below the threshold and
`make test` does not swallow that, so the gate genuinely fails the build.

`branch = False` deliberately. The owner asked for **80% line coverage**; with
branch measurement on, coverage's total becomes a blended line+branch figure
and "80%" would quietly mean something other than what was asked for.

The gate covers the host package only. The firmware's coverage is a different
question with a different tool, and `tools/analysis/` and `tools/session.py`
are outside this stream.

Until `tools/rover_bench/` existed, `make test` announced the gate and skipped
it rather than failing a bare checkout on an empty source tree. It arms itself
as soon as that package is present; no edit to the Makefile is needed.

---

## `make lint` is a bug check, not a style check

The rule set is **pinned** in the Makefile (`RUFF_SELECT ?= E4,E9,F`) rather
than left to ruff's default. Two reasons: ruff's default selection changes
between releases, so an unpinned target reports different things on two
machines and stops being trusted; and E4 (imports), E9 (syntax/runtime) and F
(pyflakes) are *bugs*, while E1/E2/E3/E7 are whitespace and naming opinions.
This project is explicitly "shitty code, lots of tests" — a style avalanche
would bury the one finding that matters.

For a stricter pass: `make lint RUFF_SELECT=E,F,B,I`.

It found a real one while this suite was being written: an `F841` in
`test_docs_consistency.py` where a dead variable meant half an assertion was
never made.

---

## Assumptions worth knowing

* The fake Pico in `conftest.py` mirrors `firmware/src/protocol.c`: a bare `OK`
  acknowledges a command, a named payload line (`PONG`, `ENC`, `ID`) answers a
  query, and `ERR <reason>` refuses. The host and the firmware were briefly out
  of step here — the host sent `DUTY <motor> <duty>` while the firmware took
  `SET <duty>` — which is why
  `test_the_host_link_speaks_a_dialect_the_firmware_accepts` exists: it drives
  every host wrapper against a silent transport and feeds the bytes it wrote to
  the real C parser. Nothing else compares the two, and that kind of drift is
  invisible until a bench session, where it looks like a dead board. If the
  wire format moves again, `pico_responder` in `conftest.py` is the one place
  to change.
* Numbers in the fixtures — `SyntheticMotor`'s gain, time constant, deadband,
  wheel diameter and `ticks_per_rev` — are **invented for the arithmetic** and
  are not hardware claims. The real values are `[MEASURE]` in
  `docs/HARDWARE.md` and are not known yet. `ticks_per_rev` is a flat `1000`
  on purpose: a fixture holding a *plausible* value (it used to hold the
  firmware's 5600 placeholder) is one grep away from being quoted as if the
  project had measured it, and the 11-vs-14 dispute is still open.
* Nothing in this suite asserts a particular counts-per-rev value.
  `test_firmware_control.py` checks only that the firmware's default is either
  zero or one of the derivations `docs/HARDWARE.md` §2.1 allows, and that the
  word `PLACEHOLDER` is still beside it. A test demanding 5600 would fight
  Story 1.4 instead of protecting it.
* `EXPECTED_HEADER_PINS` in `test_docs_consistency.py` encodes the Pico 2 W
  header pinout for the ten pins this bench uses. It is standard and is stated
  identically in four documents, but it is ground truth typed by hand.
