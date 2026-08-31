# Memory index

One line per file. Read this at every session start.

## Wiki layer (undated topic pages — revise in place)

- [pico-toolchain](pico-toolchain.md) — MicroPython was the bring-up tool; pico-sdk is the recommendation, and the decision is due on Day 0.
- [power-supply](power-supply.md) — judge a supply by its sag under load, not open-circuit voltage; the 6F22 is unusable, and the 6xAA plan was wrong once the motor turned out to be 6 V.
- [n20-motors](n20-motors.md) — Adafruit 4639, 6 V 1:100 (the 12 V / 50:1 guess was wrong); wire colours, the wrong-wires trap, and an unresolved encoder count.
- [logic-analyser](logic-analyser.md) — FX2 clone `0925:3881`; the Zadig block was a *Windows* problem, and on Ubuntu it is one udev rule.
- [debugging-method](debugging-method.md) — walk the causal chain, then split the system in half.

## Log layer (dated, append-only — never rewritten)

- [2026-08-31-first-motor-under-pico](2026-08-31-first-motor-under-pico.md) — first N20 driven forward and reverse from the Pico; an evening lost to encoder wires in the H-bridge.
