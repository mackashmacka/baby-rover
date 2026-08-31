# NEXT-STEPS

Handoff for a fresh session. **Assume you know nothing not written down here.**

Last updated **2026-08-31** — project handover, before Day 0.

**Read first:** [`CLAUDE.md`](CLAUDE.md) → [`memory/MEMORY.md`](memory/MEMORY.md)
→ [`docs/PLAN.md`](docs/PLAN.md). Then [`docs/setup.md`](docs/setup.md) and
start work.

**Hardware facts:** [`docs/HARDWARE.md`](docs/HARDWARE.md) is ground truth —
part numbers, ratings, derived constants. [`docs/WIRING.md`](docs/WIRING.md) is
the living connection record. Keep them separate; never copy a rating from the
first into the second.

---

## You are at Day 0

Nothing has been built by the current owner. What exists is the plan, the
documentation scaffold, and one prototype rover with **one motor proven to
spin**, done in a prior session on different hardware.

**The whole of Day 0 is setup**, and there is a lot of it. Runbook:
[`docs/setup.md`](docs/setup.md). Do not skip to the interesting part — every
item on that list blocks something later.

### Day 0 acceptance — do not start Week 1 until all seven pass

- [ ] `git push` succeeds from the Pi to a **public** GitHub repo
- [ ] `ssh baby-rover` works from the laptop over **Tailscale**
- [ ] `ip a show wlan0` shows an address, and survives a reboot
- [ ] `sigrok-cli` has captured a real square wave you can read
- [ ] A blink binary built from source and flashed to the Pico
- [ ] `pytest` runs green on an empty-but-real suite
- [ ] The firmware toolchain decision **written down with its reasoning**

---

## Inherited state

**Stage 1 works.** One N20 runs forward and reverse through a TB6612FNG from a
Pico 2 W at 20 kHz PWM, from the MicroPython REPL. That is the entire built
surface. See [`memory/2026-08-31-first-motor-under-pico.md`](memory/2026-08-31-first-motor-under-pico.md).

No encoders, no closed loop, no persisted firmware, no Pi↔Pico link, no vision,
no sensors beyond the motors.

---

## Blockers, in the order they block things

### 1. Encoder counts per revolution unresolved 🔴
The sources disagree — Adafruit says 14 counts/rev, retailer listings say 11 —
giving 1100–1400 counts per output revolution, possibly ×2 or ×4 depending on
the decoding. **Measure it** (Story 1.4) rather than adopting either figure.

### 2. Bench supply — and the old plan was wrong 🟠
The 9 V 6F22 is carbon-zinc, ~10 Ω internal, and cannot hold 4.5 V under load.
`VM` currently comes from Pico VBUS as a stopgap. **The previously-planned 6×AA
pack is the wrong part** now the motor is known to be 6 V — 9 V is 150% of
rated. Use a **lab PSU at 6.0 V with current limiting**, or 4× alkaline.
See [`memory/power-supply.md`](memory/power-supply.md).

### 3. Three constants to measure, not look up 🟡
Not blockers, just work: **wheel diameter**, **track width** (left-to-right
wheel centres), and **encoder counts/rev**. A ruler and a hand-turned shaft.
`metres per count` needs all three, and nothing downstream works without it.
Re-measure the wheels if they get reprinted in Story 3.3.

### ✅ Closed — motor identity confirmed
The motors are **N20s, 6 V, 1:100** — not the `CHF-GM12-N20VA-50-12V`
12 V / 50:1 part earlier docs guessed at. Testing at 5 V is 83% of rated, so
**characterisation is no longer blocked on this.** The IMU (**MPU-6050**) and
magnetometer (**Jaycar XC4496**) are chosen; the **NEO-M9N** module is
deliberately deferred to Week 2.

### 4. `wlan0` is down on the Pi 🟠
Ethernet only. A rover on wheels cannot stay on a cable. Day 0 item.

### 5. No git repo, no remote, nothing backed up 🟠
And **the Pi's rootfs is a disposable USB stick.** First Day 0 item.

### 6. BMAD referenced but not installed 🟡
`CLAUDE.md` names it; it has never been installed or authorised. `docs/PLAN.md`
carries the epics and stories in the meantime, which for a three-week solo build
is probably enough. **Decide on Day 0 and record why.**

---

## What the next session needs to know

- **One story per fresh conversation.** The stories are in `docs/PLAN.md` §5–7.
- **Confirm the motor pair by resistance before wiring anything.** An evening
  has already been lost to encoder wires in the H-bridge outputs, with every
  logic test point reading correctly and the motor silent.
- **Encoder halls run on 3.3 V, never 5 V.** RP2350 GPIO is not 5 V tolerant.
- **Never probe `AO1`/`AO2` with the analyser** — motor voltage will destroy it.
- **GPS does not work indoors.** The mission splits into an indoor course and an
  outdoor course; see `docs/PLAN.md` §2. This is the single most important
  structural decision in the plan.
- **The failsafe is not implemented**, and `CLAUDE.md` calls it non-negotiable.
  Nothing drives untethered until it exists (Story 2.1).
- **Nothing persists on the Pico.** All behaviour so far was typed at the REPL.
- **The record is built daily, not in week three.** Registry row per experiment,
  memory entry per session. See `docs/career-track.md`.

---

## Open threads

- Unexplained **0.89 V** on a 50%-duty 3.3 V PWM line where ~1.65 V was
  expected. Probably the meter failing to average 20 kHz — unconfirmed, and a
  job for the analyser now that Linux unblocks it.
- **No test harness exists.** The CLOSE ritual requires a green full suite and
  there is currently nothing to run. Define it on Day 0 — see `docs/PLAN.md` §10 —
  or the ritual is theatre.
- `docs/wiring.html` and `docs/harness.html` are referenced by
  `docs/project-state.md` but **were not carried over**. Either recreate them or
  drop the references.
- The **FTDI FT232RL** can stand in for the Pi to build and test the UART
  protocol and failsafe before the Pi is involved. **Set it to 3.3 V first.**
