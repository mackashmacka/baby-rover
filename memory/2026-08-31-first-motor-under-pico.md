# 2026-08-31 — first motor under Pico control

## What happened

One N20 gearmotor driven forward and reverse through a TB6612FNG from a
Pico 2 W. 20 kHz PWM, commanded live from the MicroPython REPL over USB serial.
`Pico GP2/3/4/5 → PWMA/AIN1/AIN2/STBY`, `VM ← Pico VBUS (pin 40)`, common
ground.

Stage 1 works. Nothing beyond it exists — no encoders, no closed loop, no
persisted firmware, no Pi↔Pico link, no vision.

## The evening that was lost

Four encoder wires were driven into the H-bridge outputs. Every logic test
point read correctly; the motor sat silent. Diagnosed only by touching the
motor leads straight to a supply — ten seconds, after hours of correct
measurements. See [[debugging-method]] and [[n20-motors]].

## Decisions

- **MicroPython as a bring-up tool**, explicitly not as the production
  toolchain — see [[pico-toolchain]].
- **Deadband measurement deliberately deferred** until the motor's rated
  voltage is confirmed. A number taken at 42% of rated voltage does not
  transfer. See [[n20-motors]].
- The 9 V 6F22 was measured and **rejected on internal resistance**, not on
  charge — see [[power-supply]].

## Deviations from the bootstrap instruction

The agent bootstrap prompt (`docs/agent-bootstrap.md`) items 1–4 were carried
out with four deviations. Most significantly: **`CLAUDE.md` was appended to,
not overwritten**, and the scaffolds were populated rather than left empty.
**BMAD was not installed** — no authorisation was given.

## Open threads

- Unexplained 0.89 V on a 50%-duty 3.3 V PWM line — a job for the analyser
  once it is unblocked. See [[logic-analyser]].
- Failsafe timeout still not implemented, and `CLAUDE.md` calls it
  non-negotiable.
