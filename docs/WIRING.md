# WIRING — the living connection record

**This file changes as the rover is built.** Pin assignments, harness colours,
what is actually connected right now, and what is still planned.

**Specs live in [`HARDWARE.md`](HARDWARE.md)** — part numbers, ratings, derived
constants. That file is ground truth and stays stable; this one is allowed to
churn. Never copy a rating into this file: link to it instead, so there is only
ever one place a number can be wrong.

Last updated **2026-09-01**. Log at §9.

| | |
|---|---|
| ✅ | wired and verified |
| 🟡 | wired, not yet verified |
| ⬜ | planned, not wired |

---

## 1. Safety rules — read before touching anything

These are not style preferences. Each has already cost someone time or money.

1. **Confirm the motor pair by resistance before connecting it.** Red–white
   reads a few ohms; any other pair does not. An evening was lost on this
   project to encoder wires driven into the H-bridge outputs — every logic test
   point read correctly and the motor sat silent.
2. **Encoder power is 3.3 V, never 5 V.** RP2350 GPIO is not 5 V tolerant.
3. **Power the I²C modules from 3.3 V**, so their pull-ups cannot present 5 V
   to the Pico.
4. **Never probe `AO1`/`AO2`/`BO1`/`BO2` with the logic analyser.** Motor
   voltage will destroy it.
5. **Grounds must be common** — Pico, drivers, battery, and analyser — or the
   driver cannot interpret a logic level at all.
6. **The 4S LiPo never reaches the TB6612 directly.** 16.8 V against a 13.5 V
   maximum. It goes through the D24V90F5 first, every time.
7. **Set the FTDI adapter to 3.3 V and verify on its VCC pin** before it goes
   anywhere near the Pico.
8. **No motor current enters the Pico.** No wire from the battery to the Pico,
   none from a driver output back to it.

---

## 2. Currently wired — Stage 1 ✅

One motor, one driver, everything on USB power. This is the entire built
surface today.

| From | Pico pin | To | Status |
|---|---|---|---|
| `3V3(OUT)` | 36 | TB6612 `VCC` | ✅ |
| `GND` | 3 | TB6612 `GND` + common rail | ✅ |
| `GP2` | 4 | TB6612 `PWMA` | ✅ |
| `GP3` | 5 | TB6612 `AIN1` | ✅ |
| `GP4` | 6 | TB6612 `AIN2` | ✅ |
| `GP5` | 7 | TB6612 `STBY` | ✅ |
| `VBUS` | 40 | TB6612 `VM` | 🟡 **temporary** |
| — | — | one motor on `AO1`/`AO2` | ✅ |

**Verified:** forward and reverse, 20 kHz PWM, from the MicroPython REPL.

⚠️ **`VM` on VBUS violates rule 8** — motor current shares a rail with the
Pico. Acceptable for one motor at 5 V during bring-up; **not acceptable for
four.** Replace with a proper supply before Story 1.2.

**Nothing persists.** All of this was typed at the REPL and does not survive a
power cycle.

---

## 3. Target pin map — Pico 2 W

19 of 26 usable GPIO. Direction pins are **shared per side** — both wheels on a
side always turn together — which preserves independent per-wheel *speed*
control for PID while saving four pins.

| GP | Pin | Signal | Status |
|---|---|---|---|
| `GP0` | 1 | UART TX → Pi `GPIO15` | ⬜ |
| `GP1` | 2 | UART RX ← Pi `GPIO14` | ⬜ |
| `GP2` | 4 | `PWMA` — left front speed | ✅ |
| `GP3` | 5 | `AIN1`/`BIN1` — left side direction | ✅ |
| `GP4` | 6 | `AIN2`/`BIN2` — left side direction | ✅ |
| `GP5` | 7 | `STBY` — both drivers, global enable | ✅ |
| `GP6` | 9 | `PWMB` — left rear speed | ⬜ |
| `GP7` | 10 | `PWMA` — right front speed | ⬜ |
| `GP8` | 11 | `AIN1`/`BIN1` — right side direction | ⬜ |
| `GP9` | 12 | `AIN2`/`BIN2` — right side direction | ⬜ |
| `GP10` | 14 | `PWMB` — right rear speed | ⬜ |
| `GP12` | 16 | Encoder — left front, ch A | ❓ **held low — §10.2.2** |
| `GP13` | 17 | Encoder — left front, ch B | ❓ **held low — §10.2.2** |
| `GP14` | 19 | Encoder — left rear, ch A | ⬜ |
| `GP15` | 20 | Encoder — left rear, ch B | ⬜ |
| `GP16` | 21 | Encoder — right front, ch A | ⬜ |
| `GP17` | 22 | Encoder — right front, ch B | ⬜ |
| `GP18` | 24 | Encoder — right rear, ch A | ⬜ |
| `GP19` | 25 | Encoder — right rear, ch B | ⬜ |

`GP11`, `GP22`, `GP26–GP28` spare. **`GP20`/`GP21` are no longer spare** — they
are bench instrumentation, §10.1. **`GP23/24/25/29` unusable** — CYW43439.

> **"Channel A" is a naming convention, not a wiring constraint.** Yellow and
> green are interchangeable at the connector — pick one as A, verify the phase
> direction on the analyser, and correct the sign **in firmware**. Never fix a
> direction sign by swapping wires: left and right wheels are physical mirrors,
> so all four motors get wired identically and firmware owns the polarity.

---

## 4. Motor harness — per motor ⬜

Colours confirmed against the supplier documentation. See
[`HARDWARE.md` §2.1](HARDWARE.md).

| Wire | Function | Destination |
|---|---|---|
| **Red** | Motor + | Driver `AO1` (or `BO1`) |
| **White** | Motor − | Driver `AO2` (or `BO2`) |
| **Black** | **Ground** | Common ground rail |
| **Blue** | **Encoder power** | **3.3 V** — Pico pin 36 or a 3.3 V rail |
| **Yellow** | Hall output | Pico, the channel-A pin of the pair |
| **Green** | Hall output | Pico, the channel-B pin of the pair |

**Before connecting each motor** — two measurements, thirty seconds, and they
have already saved an evening once:

1. Red–white: **a few ohms.** That confirms the motor pair.
2. Black–blue: **not a few ohms.** That confirms you have not swapped the pairs.

### Channel assignment

| Motor | Position | Driver | Channel | PWM | Encoder A/B |
|---|---|---|---|---|---|
| **0** | Left front | #1 | A | `GP2` | `GP12` / `GP13` |
| **1** | Left rear | #1 | B | `GP6` | `GP14` / `GP15` |
| **2** | Right front | #2 | A | `GP7` | `GP16` / `GP17` |
| **3** | Right rear | #2 | B | `GP10` | `GP18` / `GP19` |

The **Motor** column is the number the tooling uses: `rover-bench --motor <n>`,
the `motor-<n>/` directory in `experiments/`, and `MOTORS = (0, 1, 2, 3)` in
`tools/rover_bench/storage.py`. It was already implicit in the row order of this
table and in that module's comment; it is written down here so a directory
called `motor-2` is unambiguously the right front wheel. Added 2026-08-31 —
nothing was rewired.

Driver #1 direction pins ← `GP3`/`GP4` (both channels, tied).
Driver #2 direction pins ← `GP8`/`GP9` (both channels, tied).
Both `STBY` ← `GP5`.

---

## 5. Power distribution ⬜

```
  4S LiPo  16.8 V ──► Pololu D24V90F5 ──► 5 V, 9 A rail
                                            │
                        ┌───────────────────┼───────────────────┐
                        ▼                   ▼                   ▼
                  TB6612 ×2 VM        Raspberry Pi 5      (D415 via Pi USB)
                     5 V                  5 V
                        │
                        └──► motors, 4.5–6 V window
```

- **Pico** powered over USB during development; from the 5 V rail on the rover
- **Driver logic `VCC`** from Pico 3V3 (a few mA) — *not* from the motor rail
- **Encoder `VCC` (blue)** from 3.3 V
- **One common ground**, star-connected at the regulator output where possible

**Sizing note:** the Pi 5 and D415 together dominate this budget at several
amps; the whole motor subsystem is under 1 A. The 9 A regulator exists for the
computer, not the drivetrain. See [`HARDWARE.md` §6.2](HARDWARE.md).

### Bench supply ⬜

🔴 **The previously-planned 6×AA pack is the wrong part** — 9 V against a 6 V
motor. Use a **lab PSU at 6.0 V with current limiting** (best: a wiring mistake
beeps instead of smoking), or 4× alkaline / 5× NiMH.

---

## 6. Sensor bus ⬜

Recommended: **encoders on the Pico** (they need hardware timing), **everything
else on the Pi** (the fusion runs there, and the UART link stays a thin
command/telemetry channel).

| Device | Interface | Pi pin | Address |
|---|---|---|---|
| MPU-6050 | I²C1 | `GPIO2` SDA / `GPIO3` SCL | `0x68` |
| Magnetometer | I²C1 | same bus | `0x1E` / `0x0D` / `0x30` — **scan to find out** |
| NEO-M9N | UART | ⬜ **see below** | — |
| Pico link | UART0 | `GPIO14` TX / `GPIO15` RX | — |

**Both I²C modules powered from Pi 3.3 V (pin 1 or 17), not 5 V** — rule 3.

⚠️ **Open: the GPS has no UART yet.** `GPIO14/15` are committed to the Pico.
Either enable a second Pi UART with a `dtoverlay`, or use a USB-serial adapter.
Decide in Week 2, when the M9N module is chosen.

⚠️ **Magnetometer placement is a wiring decision, not a mounting afterthought.**
Near the motors and their current-carrying leads it reads garbage that no
calibration can fix, because the error moves with motor current. Route motor
leads as twisted pairs, keep them away from the compass, and plan for the mast
(Story 3.3). Story 2.3 measures exactly how bad it is.

---

## 7. Pi ⇄ Pico link ⬜

| Pi 5 | | Pico 2 W |
|---|---|---|
| `GPIO14` TXD (pin 8) | → | `GP1` RX (pin 2) |
| `GPIO15` RXD (pin 10) | ← | `GP0` TX (pin 1) |
| `GND` (pin 6) | — | `GND` (pin 3) |

**TX↔RX crossed. Both sides 3.3 V — no level shifter.** Common ground required.

The **USB debug serial is a separate channel** from this UART; flashing and
monitoring must never contend with the control link.

**Bench substitute:** the FTDI FT232RL stands in for the Pi — `TXO`→`GP1`,
`RXI`→`GP0`, `GND`→rail, **`VCC` left disconnected**. This lets the protocol and
the failsafe be built and tested before the Pi is involved. **Set it to 3.3 V
first.**

---

## 8. Logic analyser probe points

All 3.3 V logic. Ground the analyser to the common rail.

| CH | Signal | Pico pin |
|---|---|---|
| 0 | `PWMA` | 4 |
| 1 | `AIN1` | 5 |
| 2 | `AIN2` | 6 |
| 3 | `STBY` | 7 |
| 4 | Encoder A | 16 |
| 5 | Encoder B | 17 |
| 6 | UART TX | 1 |
| 7 | UART RX | 2 |

⚠️ **Never `AO1`/`AO2`** — rule 4.

---

## 9. Change log

Append a line whenever wiring changes. Newest last.

| Date | Change |
|---|---|
| 2026-08-31 | Stage 1 recorded: one motor, driver #1, `VM` on VBUS as a stopgap |
| 2026-08-31 | Harness colours corrected to the real N20 mapping (red/white motor, black GND, blue 3.3 V, yellow/green halls). **Supersedes the previous `P1`–`P6` numbered pinout, which described a different motor and is void** |
| 2026-08-31 | Full four-motor pin map drafted; encoders assigned `GP12`–`GP19` |
| 2026-08-31 | **Bench instrumentation committed — see §10.** `GP20` = `LOOP_TICK`, `GP21` = `COMPUTE_BUSY`. Analyser D6/D7 move off the UART pins for Stories 1.5/1.6 |
| 2026-09-01 | **Probe map measured, not assumed** (`probe-map`). D0/D1/D6/D7 correct. **D2↔D3 swapped at the clips** — §10.2.1. Analyser ground confirmed common |
| 2026-09-01 | `GP12`/`GP13` found **held low by something external** — §10.2.2. §3 had them as ⬜ *not wired*; that was wrong. Blocks Stories 1.4 and 1.5 |

---

## 10. Bench instrumentation — 2026-08-31 🟡

**Added for Story 1.5 (`motor-char`) and Story 1.6 (`pid-step`).** Setup
manual, safety interlocks and verification procedure:
[`BENCH.md`](BENCH.md). This section records only *what is connected*.

Two previously-spare GPIO are now committed to instrumentation, and the
analyser channel map changes for the duration of the characterisation campaign.

### 10.1 New instrumentation pins

| GP | Pin | Signal | Direction | Purpose | Status |
|---|---|---|---|---|---|
| `GP20` | 26 | `LOOP_TICK` | out | Firmware **toggles** once per PID iteration. Measures the real control-loop period and its jitter | 🟡 |
| `GP21` | 27 | `COMPUTE_BUSY` | out | Firmware holds **HIGH while the loop body computes**. Measures execution time per iteration ⇒ CPU headroom | 🟡 |

Both are outputs from the Pico to the analyser only. **Nothing else connects to
them** — no driver input, no motor, no Pi.

> ⚠️ **`LOOP_TICK` toggles.** One loop period is **edge to edge**, not rising to
> rising. One edge is one iteration, so at 100 Hz that is **100 edges/s** and a
> **50 Hz** square wave — a rising-to-rising or frequency reading reports half
> the loop rate. See [`BENCH.md`](BENCH.md) §3.1.

**Spare GPIO after this change:** `GP11`, `GP22`, `GP26`–`GP28`.
(`GP20`/`GP21` were listed as spare in §3; they are not any more.)
**`GP23/24/25/29` remain unusable** — CYW43439.

### 10.2 Analyser channel map — characterisation bench

All 3.3 V logic. Ground the analyser to the common rail — the nearest header
GNDs are **pin 3** (D0–D3), **pin 18** (D4/D5) and **pin 28** (D6/D7). Connect
every ground lead the analyser has.

| CH | Signal | Pico GP | Pico pin | Verified |
|---|---|---|---|---|
| D0 | `PWMA` | `GP2` | 4 | ✅ 2026-09-01 |
| D1 | `AIN1` | `GP3` | 5 | ✅ 2026-09-01 |
| D2 | `AIN2` | `GP4` | 6 | ❌ **probe is on `GP5`** — see below |
| D3 | `STBY` | `GP5` | 7 | ❌ **probe is on `GP4`** — see below |
| D4 | Encoder A (yellow) | `GP12` | 16 | ❓ pin held low, see §10.2.2 |
| D5 | Encoder B (green) | `GP13` | 17 | ❓ pin held low, see §10.2.2 |
| **D6** | **`LOOP_TICK`** | **`GP20`** | **26** | ✅ 2026-09-01 |
| **D7** | **`COMPUTE_BUSY`** | **`GP21`** | **27** | ✅ 2026-09-01 |

#### 10.2.1 ⚠️ D2/D3 are physically swapped right now

Measured 2026-09-01, experiment `probe-map`. Each pin was driven alone with a
unique edge count, so the mapping is not inferred — it is read off directly:

| Driven | Edges | Appeared on | Should be |
|---|---|---|---|
| `GP2` | 10 | D0 | D0 ✅ |
| `GP3` | 20 | D1 | D1 ✅ |
| `GP4` | 40 | **D3** | D2 ❌ |
| `GP5` | 80 | **D2** | D3 ❌ |

**Fix it at the clips, not in software.** Move the D2 lead to pin 6 (`GP4`) and
the D3 lead to pin 7 (`GP5`), then re-run `probe-map` and set both rows above to
✅. Correcting a channel map in the analysis code instead is how a capture ends
up meaning something different from what its own filename says.

Until it is fixed, **a capture taken now records `STBY` on D2 and `AIN2` on D3** —
which reverses the direction decode and makes the failsafe measurement read the
wrong edge.

#### 10.2.2 ❓ `GP12`/`GP13` are held low by something external

Also 2026-09-01. Configured as inputs with the RP2350's internal pull-up
(≈55 kΩ) and read back **0**; with pull-down, also 0. A floating pin follows the
pull. **These pins do not float, so something is attached** — which contradicts
§3, where both were still marked ⬜ *planned, not wired*.

Three candidates, cheapest test first:

1. **Encoder wired but unpowered** — blue never reached 3.3 V. Most likely.
   Check continuity blue → pin 36, and that the motor's black is on the common rail.
2. **A probe or jumper clipped to a GND pin** instead of 16/17. Pin 18 is GND and
   sits directly between them.
3. **Encoder on the wrong pins**, with 16/17 shorted to ground elsewhere.

Distinguishing test, no instruments needed: **turn the motor shaft by hand** and
re-read. A powered encoder toggles; an unpowered one stays flat.

**This blocks every speed measurement.** `ticks_per_rev` (Story 1.4) and the
duty→rad/s curve (Story 1.5) have no signal to count until it is resolved.

⚠️ **Never `AO1`/`AO2`/`BO1`/`BO2`** — rule 4. Motor voltage destroys the
analyser, and it fails by reading garbage on a channel you then trust.

### 10.3 Relationship to §8

**§8 is not superseded — the two maps swap according to which question is being
asked**, and only D6/D7 differ:

| CH | §8 map | §10.2 map (this one) |
|---|---|---|
| D6 | UART TX — `GP0`, pin 1 | `LOOP_TICK` — `GP20`, pin 26 |
| D7 | UART RX — `GP1`, pin 2 | `COMPUTE_BUSY` — `GP21`, pin 27 |

| Use this map | For | Because |
|---|---|---|
| **§10.2** | Stories **1.5**, **1.6** — characterisation and PID tuning | The UART *content* is already readable at the laptop end of the wire, so decoding it again is redundant. **Loop timing has no other witness**, and an unverified loop rate silently rescales every `omega_rad_s` |
| **§8** | Story **2.1** — the Pi↔Pico failsafe | There the measurement is "last valid byte → motors stopped", which needs UART TX and `STBY` on one timebase. Loop timing is then the redundant pair |

**Record which map a capture was taken under**, in the filename or the run log.
A `.sr` file whose D6 could be either signal is not evidence of anything.

### 10.4 Bench power — differs from §2

The characterisation bench does **not** use the §2 stopgap of `VM` on `VBUS`.
That arrangement puts motor current on the Pico's rail (rule 8) and was
acceptable only for bring-up smoke tests.

| Rail | Source | Feeds |
|---|---|---|
| `VM` | **Lab PSU, 5.00 V, current limit 1.00 A** | TB6612 `VM` only |
| Logic 3.3 V | Pico `3V3(OUT)`, pin 36 | TB6612 `VCC`, motor **blue** (encoder power) |
| Pico | USB from the laptop | — |
| Ground | one common rail | Pico, TB6612, PSU −, motor **black**, analyser |

**Power on: Pico USB first, then `VM`. Power off: `VM` first, then Pico.** The
Pico must be driving `STBY` and the direction pins to a defined state before the
H-bridge has a supply, or the motor can lurch on power-up.

### 10.5 Proposed, not yet wired ⬜

| Part | From | To | Why |
|---|---|---|---|
| 10 kΩ resistor | `STBY` (`GP5`, pin 7) | GND rail | RP2350 comes out of reset with GPIO as inputs, and **erratum RP2350-E9** says internal pull-downs can latch high — so "`STBY` is low at reset" is not guaranteed by the chip. An external pull-down makes *driver disabled until firmware says otherwise* a property of the circuit rather than of the software |

**Awaiting the owner's sign-off.** One resistor; removes a class of power-up
lurch.
