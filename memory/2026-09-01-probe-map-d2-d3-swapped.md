# 2026-09-01 — Probe map d2 d3 swapped

## What happened

Asked "are we ready to run the first test". Checked instead of answering, and
the answer is **no** — for two reasons neither of us knew about an hour ago.

Everything software-side was green: 1143 tests, 89.11% coverage, firmware
builds, `doctor` clean, `dialout` finally active in the shell. The bench looked
ready. It wasn't, and the gap was entirely in the physical layer that no test
can reach.

## Decisions

**Verify the channel map before trusting a single capture.** `bench-verify`
proved the analyser works by driving `GP2` and reading D0. That says nothing
about the other seven leads — and D1–D7 being flat in that capture is exactly
what an *unprobed* channel and a *correctly probed but idle* channel both look
like. Indistinguishable. So the first check today was: drive each pin alone
with a **unique edge count** (10/20/40/80), and read the mapping off the capture
instead of inferring it. Two channels with matching counts would be ambiguous;
four distinct ones cannot be.

It immediately found **D2 and D3 swapped**. Had the run gone ahead, every
capture would have recorded `STBY` on D2 and `AIN2` on D3 — the direction decode
comes out reversed, and the failsafe measurement watches the wrong edge. The
data would have looked entirely plausible. That is the expensive kind of wrong,
and `WIRING.md` §10.2 already warned about it in the abstract.

**Fix it at the clips, never in the analysis code.** Remapping channels in
software makes a `.sr` file mean something other than what its own filename
says, and nothing downstream can detect that.

**Test the encoder pins passively.** `WIRING.md` said `GP12`/`GP13` were
unwired, but driving them to find out would have meant contention if that was
wrong. Internal pull-up, then pull-down, read back on the Pico: no drive, no
risk, and a floating pin follows the pull. **They don't float** — both stay at 0
under ≈55 kΩ. Something is attached and holding them down, and the doc was
wrong. Trusting the doc and driving them would have been the plausible move.

**A false FAIL in `doctor` is worse than a cosmetic bug.** A cold FX2 has no
firmware in RAM; the first sigrok call uploads it, the device re-enumerates, and
anything racing that window gets "No devices found". `doctor` reported a healthy
analyser as broken — and `doctor` is the gate that decides whether the bench may
run. A check that cries wolf teaches its owner to ignore the one thing whose
entire job is to be believed. Now retries three times, with the delay injected
so the suite pays no wall clock.

## Open threads

- **D2/D3 need re-clipping**, then re-run `probe-map` and flip both rows to ✅.
- **`GP12`/`GP13` held low** — cheapest test first: turn the motor shaft by hand
  and re-read. A powered encoder toggles; an unpowered one stays flat. Prime
  suspect is blue never reaching 3.3 V.
- Firmware still **has never been flashed**. The Pico is running MicroPython.
- `ticks_per_rev` still unmeasured, and now visibly blocked behind the encoder.
- **The USB dock is a single point of failure.** It dropped the entire tree
  mid-session — Pico, analyser, FTDI and ethernet at once. The unattended
  characterisation loop must notice that and halt, because half a data set
  written across a disconnect looks like a real result.

Wiki: [[logic-analyser]], [[debugging-method]]. Experiment: `experiments/probe-map/`.

## Session log
- 00:57Z — Readiness check before first characterisation run. NOT ready.
- 00:57Z — probe-map experiment: drove each of GP2/GP3/GP4/GP5 alone with a unique edge count (10/20/40/80) so the channel map is read off the capture, not inferred.
- 00:57Z — FOUND: D2 carries GP5 (STBY), D3 carries GP4 (AIN2). Swapped. WIRING.md 10.2 corrected, Verified column added.
- 00:57Z — FOUND: GP12/GP13 read 0 under the internal ~55k pull-up, so they are held low externally - not floating-unwired as WIRING.md 3 claimed. Blocks Stories 1.4 and 1.5.
- 00:57Z — CONFIRMED GOOD: D0=GP2, D1=GP3, D6=GP20, D7=GP21 (6000 edges from 3000 commanded cycles). Analyser ground is common.
- 00:57Z — BUG FIXED: doctor called a healthy analyser broken. A cold FX2 needs a firmware upload on the first sigrok call; the device re-enumerates and the next call races it. Now retries 3x with an injected sleep. 3 regression tests.
- 00:57Z — GOTCHA: tests/ can import the same file as both rover_bench.X and tools.rover_bench.X - two distinct class objects, so 'except AnalyserError' silently never matches. Cost one failing test.
- 00:57Z — Captures stored run-length encoded: 275 MB of CSV -> 312 KB of transitions, lossless.
- 00:57Z — pytest: 1143 passed, 13 deselected, 89.11% coverage against the 80% gate.
- 00:59Z — BUG FIXED: open_link's test seam was incomplete - transport_factory was injected but resolve_port still hit the real os.path.exists. test_open_link_uses_the_injected_transport_factory passed only because a Pico was plugged in. A false PASS, not a false fail.
- 00:59Z — Discovered by accident: the HP dock dropped the entire USB tree at t=1464s (Pico, analyser, FTDI, ethernet). Suite then failed. exists/globber now forwarded through open_link; 2 regression tests.
- 00:59Z — RISK for the unattended loop: the dock takes the Pico AND the analyser down together. A multi-hour characterisation run needs to detect that and stop, not write garbage.
- 00:59Z — pytest: 1145 passed, 89.11% coverage, with NO hardware attached - the 'no test needs hardware' claim is now proven rather than assumed.
