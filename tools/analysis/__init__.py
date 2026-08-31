"""Baby Rover host-side analysis package.

Raw CSV in, defensible numbers and publication-quality figures out.

Modules:
    load       read + validate a run directory; refuse incomparable runs
    metrics    pure functions producing values WITH uncertainties
    plots      matplotlib figures in one house style
    report     assemble per-motor markdown (numbers/tables/figures only, NO prose)
    synthetic  generate realistic fake runs so all of the above is testable
               with zero hardware

Nothing in this package touches a Pico, a serial port or a logic analyser.
Everything here is offline analysis of files already on disk. That is
deliberate: it means the whole half is unit-testable on a laptop with no
hardware plugged in.
"""

from __future__ import annotations

__all__ = ["load", "metrics", "plots", "report", "synthetic"]

# Bumped when the on-disk run format changes in a way that breaks readers.
# load.py refuses to read a manifest whose schema_version it does not know.
SCHEMA_VERSION = 1
