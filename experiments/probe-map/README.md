# `probe-map` — is each analyser channel on the pin the doc claims?

**2026-09-01.** Story 0 (bench acceptance). Registry row in
[`../REGISTRY.md`](../REGISTRY.md).

## Why

`bench-verify` (2026-08-31) proved the analyser *works* by driving one pin,
`GP2`, and reading it back on D0. It did not prove that the other seven leads
are where `WIRING.md` says they are — D1–D7 were flat in that capture, which is
exactly what an unprobed channel and a correctly-probed idle channel both look
like.

`WIRING.md` §10.2 warns that a bad channel map "fails by reading garbage on a
channel you then trust". That is the failure this experiment exists to catch,
and it caught one.

## Method

Each pin driven **alone**, with a **unique edge count** as an unforgeable
signature — so the mapping is read off the capture rather than inferred:

| Driven | Bursts | Edges |
|---|---|---|
| `GP2` | 5 | 10 |
| `GP3` | 10 | 20 |
| `GP4` | 20 | 40 |
| `GP5` | 40 | 80 |

Two channels showing the same count would be ambiguous; four distinct counts
cannot be. 10 s at 1 MHz on D0–D7. Script: [`method-signature.py`](method-signature.py).

**Safety.** `STBY` only ever went HIGH with `PWM = 0` and `IN1 = IN2 = 0` —
TB6612 "Stop". No rotation was possible at any point. `GP20`/`GP21` were
verified in a separate run (3000 commanded cycles → 6000 edges on each of
D6/D7), which incidentally proves analyser ground is common with the Pico: with
a floating ground nothing would have decoded at all.

`GP12`/`GP13` were tested **passively** — configured as inputs with the internal
pull-up, then the pull-down, and read back on the Pico. No pin was driven, so
there was no contention risk against an encoder that might be live.
Script: [`method-encoder-passive.py`](method-encoder-passive.py).

## Result

| CH | Doc said | Actually carries |
|---|---|---|
| D0 | `GP2` | `GP2` ✅ |
| D1 | `GP3` | `GP3` ✅ |
| D2 | `GP4` | **`GP5`** ❌ |
| D3 | `GP5` | **`GP4`** ❌ |
| D4 | `GP12` | nothing — pin held low |
| D5 | `GP13` | nothing — pin held low |
| D6 | `GP20` | `GP20` ✅ |
| D7 | `GP21` | `GP21` ✅ |

**D2 and D3 are swapped at the clips.** A capture taken before this is fixed
records `STBY` on D2 and `AIN2` on D3 — reversing the direction decode and
pointing the failsafe measurement at the wrong edge.

**`GP12`/`GP13` do not float.** Both read 0 against the RP2350's ≈55 kΩ internal
pull-up, so something external is holding them down. `WIRING.md` §3 had both
marked ⬜ *planned, not wired*, which is now known to be wrong. Diagnosis and
next tests in `WIRING.md` §10.2.2.

## Data

Captures are stored as **transitions, not samples** — run-length encoded at the
point of capture. Logic data is almost entirely repeats: 10,000,000 samples
reduce to 151 transitions with no loss whatsoever, 275 MB to 312 KB. Sample
indices are at 1 MHz, so index ÷ 1e6 = seconds.

| File | What |
|---|---|
| [`probe-signature-transitions.csv`](probe-signature-transitions.csv) | The one-pin-at-a-time run. 151 transitions |
| [`d6d7-toggle-transitions.csv`](d6d7-toggle-transitions.csv) | `GP20`/`GP21` at 1 kHz. 12,001 transitions |
| [`method-signature.py`](method-signature.py) | Exact script run |
| [`method-encoder-passive.py`](method-encoder-passive.py) | Exact script run |

## What this changes

- `WIRING.md` §10.2 gains a per-channel **Verified** column. A map nobody has
  measured is a hypothesis, and it should not look like a record.
- Re-run this after any re-clipping, and before the first Story 1.5 capture.
