"""rover_bench — the host-side bench driver for Baby Rover motor characterisation.

WHY THIS PACKAGE EXISTS
-----------------------
Story 1.5 says: run *one identical, repeatable experiment* on all four motors
and produce comparable data.  "Comparable" is the hard word.  Two runs are only
comparable if you can prove that everything except the motor was the same —
same firmware, same sample rate, same dwell times, same ticks-per-rev.  A human
typing commands at a REPL cannot prove that six months later in an interview.

So the whole package is built around one idea: **"characterise a motor" is ONE
command, and every run leaves behind enough evidence to defend its numbers.**

    tools/rover-bench run --motor 1

That command drives the motor, captures the analyser, writes CSV + .sr + a JSON
manifest into a deterministic path, and appends a row to
`experiments/REGISTRY.md`.  A result not persisted did not happen.

MODULE MAP (read in this order — each layer only depends on the ones above it)
-----------------------------------------------------------------------------
    link.py        serial transport to the Pico.  Injectable, so tests never
                   need a real board.
    analyser.py    subprocess wrapper over `sigrok-cli`.  Also injectable.
    safety.py      pre-flight interlocks.  Refuses to run rather than damage
                   hardware.  Fails loud and early.
    storage.py     deterministic output paths.  You never choose a filename.
    manifest.py    the reproducibility record + a diff tool for two runs.
    registry.py    appends the row to experiments/REGISTRY.md, idempotently.
    experiment.py  the experiments themselves, expressed as data + pure maths.
    fakes.py       a fake Pico and a fake sigrok.  These power `--dry-run`
                   and the test suite.
    doctor.py      "why doesn't my bench work?"  Runs with nothing installed.
    cli.py         argparse front end.  `cli.cmd_report` turns saved runs into
                   raw material for the owner's report: tables of numbers and
                   optional plots.  It does NOT write prose — that is the
                   owner's job, by rule (CLAUDE.md), and there is deliberately
                   no module here whose job is to produce sentences.

DEPENDENCY POLICY (deliberate, see docs)
----------------------------------------
Importing this package must work on a bare Python 3.12 with *nothing* pip
installed, because `doctor`'s main job today is telling you what to install.
Therefore:

  * pyserial      imported lazily, only when a real port is opened.
  * matplotlib    imported lazily, only when a plot is actually drawn.
  * numpy/pandas  not used.  `statistics` and `csv` from the stdlib do
                  everything here, and a dependency you do not need is a
                  dependency that can break `doctor`.  (Ponytail mode: YAGNI.)
"""

from __future__ import annotations

__all__ = ["__version__", "TOOL_NAME"]

TOOL_NAME = "rover-bench"

# Bumped by hand.  It lands in every manifest, so a number you plotted can
# always be traced back to the code that produced it.
__version__ = "0.1.0"
