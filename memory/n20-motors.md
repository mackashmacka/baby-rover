# N20 gearmotors

**N20 DC gearmotor with magnetic encoder — 6 V, 1:100.**
Supplier ref `ADA4639` (Core Electronics) if a datasheet is needed.

Full spec: [`docs/HARDWARE.md`](../docs/HARDWARE.md) §2.1. This page holds the
hard-won parts.

> ⚠️ **Revised 2026-08-31.** This page previously recorded the motor as
> `CHF-GM12-N20VA-50-12V` — **12 V, 50:1** — a guess from an open datasheet tab
> that was never confirmed. **The supplier link settled it: 6 V, 1:100.** Both
> old numbers were wrong. Everything that depended on the 12 V figure is void;
> see [[power-supply]].

## What the 6 V rating changed

**The characterisation blocker is closed.** Testing at 5 V is **83% of rated**,
not the 42% the old figure implied. Numbers taken at 5 V are broadly
representative, and Story 1.5 no longer has to wait on a new supply.

**And it reversed the bench supply recommendation** — 6×AA would now *over*-volt
these motors. See [[power-supply]].

## Wire colours

| Colour | Function |
|---|---|
| Red / White | Motor terminals |
| **Black** | **Ground** |
| **Blue** | **Encoder power, 3–5 V** → use 3.3 V |
| Yellow / Green | Hall sensor outputs |

Two supplier summaries disagreed on black vs blue. Adafruit's own wording is
definitive: *"Connect the black wire to your microcontroller ground pin, and the
blue wire to 3-5V DC."*

**The old `P1`–`P6` numbered pinout on this page was for a different motor and
has been deleted.** It is not a description of this hardware.

## The trap that still applies

Wire colours are not standardised between suppliers, and **an entire evening was
lost to four encoder wires driven into the H-bridge outputs.** Every logic test
point read correctly; the motor sat silent, because the fault was past the end
of the chain being measured. See [[debugging-method]].

**⇒ Two measurements before connecting any motor.** Red–white reads a few ohms.
Black–blue does not. Thirty seconds, and it has already cost an evening once.

## Encoder resolution — unresolved, and it matters

The sources **disagree**:

- Adafruit product text: **14 counts/rev** on the encoder pins, × gear ratio
- Retailer listings for this N20: **11 pulses/rev**

⇒ somewhere between **1100 and 1400 counts per output revolution**, possibly
2× or 4× that depending on the decoding scheme.

**Do not pick one. Measure it** (Story 1.4): mark the output shaft, turn it
exactly once by hand, count the edges. Every downstream number — PID feedback,
odometry, the EKF prediction step — scales directly with this constant.

`metres per count` also needs the **wheel diameter** — a ruler measurement on
the day, not a spec lookup, and worth re-taking if the wheels get reprinted.

Related: [[power-supply]], [[logic-analyser]], [[debugging-method]], [[pico-toolchain]]
