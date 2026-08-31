# WIRING — the living connection record

**This file changes as the rover is built.** Pin assignments, harness colours,
what is actually connected right now, and what is still planned.

**Specs live in [`HARDWARE.md`](HARDWARE.md)** — part numbers, ratings, derived
constants. That file is ground truth and stays stable; this one is allowed to
churn. Never copy a rating into this file: link to it instead, so there is only
ever one place a number can be wrong.

Last updated **2026-08-31**. Log at §9.

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
| `GP12` | 16 | Encoder — left front, ch A | ⬜ |
| `GP13` | 17 | Encoder — left front, ch B | ⬜ |
| `GP14` | 19 | Encoder — left rear, ch A | ⬜ |
| `GP15` | 20 | Encoder — left rear, ch B | ⬜ |
| `GP16` | 21 | Encoder — right front, ch A | ⬜ |
| `GP17` | 22 | Encoder — right front, ch B | ⬜ |
| `GP18` | 24 | Encoder — right rear, ch A | ⬜ |
| `GP19` | 25 | Encoder — right rear, ch B | ⬜ |

`GP11`, `GP20–GP22`, `GP26–GP28` spare. **`GP23/24/25/29` unusable** — CYW43439.

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

| Position | Driver | Channel | PWM | Encoder A/B |
|---|---|---|---|---|
| Left front | #1 | A | `GP2` | `GP12` / `GP13` |
| Left rear | #1 | B | `GP6` | `GP14` / `GP15` |
| Right front | #2 | A | `GP7` | `GP16` / `GP17` |
| Right rear | #2 | B | `GP10` | `GP18` / `GP19` |

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
