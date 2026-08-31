# Rover — Project State Export

> ⚠️ **ARCHIVED SNAPSHOT — superseded for hardware facts.**
> Preserved verbatim as written on 2026-08-31. Two claims in it are now known
> wrong: the motor is an **Adafruit 4639, 6 V, 1:100** (not `CHF-GM12-N20VA-50-12V`,
> 12 V, 50:1), and the six-wire `P1`–`P6` pinout describes a different motor.
> **For hardware truth use [`HARDWARE.md`](HARDWARE.md); for wiring use
> [`WIRING.md`](WIRING.md).** Nothing below has been edited.

---

Snapshot for handoff to a Claude Code session on the Raspberry Pi 5.
**Written 2026-08-31.** Read alongside `CLAUDE.md`, `docs/wiring.html`
(Stage 1) and `docs/harness.html` (Stage 2).

Every claim below is tagged:

- **[VERIFIED]** — measured or observed directly
- **[SPEC]** — from a datasheet, `CLAUDE.md`, or the wiring docs; not independently confirmed
- **[UNCONFIRMED]** — believed but never checked

---

## 1. Goal

Build a 4-wheel skid-steer rover that crosses a room on a flat floor, detecting
and avoiding boxes placed in its path, using depth-based obstacle detection and
path planning.

Two purposes, in priority order:

1. **Learning.** The owner is doing electrical engineering and wants
   first-principles understanding of robotics, embedded systems and electronics.
   A working rover the owner does not understand is a failure.
2. **A portfolio artifact.** The finished project, and the documented method
   behind it, becomes evidence for job applications. What makes it credible is
   the engineering record — measurements, characterisation data, schematics,
   debugging narratives — not the fact that a robot moves.

**Division of labour** (owner's call, 2026-08-28): the owner learns the wiring
and electrical side hands-on; Claude writes the firmware. Explanations go deep
on electrical and hardware topics, not on teaching code line by line.

**Immediate next milestone:** characterise all four motors individually and
identically — a repeatable per-motor experiment producing comparable data.

---

## 2. Compute

| Item | Role | Status |
|---|---|---|
| **Raspberry Pi 5** | High-level brain; now also the dev machine running Claude Code | Being set up |
| **Raspberry Pi Pico 2 W (RP2350)** | Realtime motor controller | **[VERIFIED]** working |
| **Arduino UNO R3** (SunFounder starter kit) | Learning platform / bench tool | **[VERIFIED]** working |

**The architecture split is by timing determinism.** Hard-realtime work (PID
loop, encoder edges, PWM) lives on the Pico. Compute-heavy work (vision,
navigation) lives on the Pi. They talk over a hardware UART at 115200 on Pico
GP0/GP1.

**Failsafe rule (non-negotiable):** the Pico stops the motors if no valid
command arrives within ~200–500 ms.

### Pico 2 W details

- Dual Cortex-M33 @ 150 MHz, 520 KB SRAM, 4 MB flash **[SPEC]**
- 3.3 V logic — matches the Pi 5, so **no level shifter** on the UART
- **GPIO is not 5 V tolerant.** This constrains the encoder supply (see §3)
- Native USB, BOOTSEL mass-storage flashing. **Cannot be bricked** — the ROM
  bootloader is unerasable **[VERIFIED]**
- PIO: 3 blocks x 4 state machines, intended for quadrature decoding
- Do not use GP23/24/25/29 — wired to the CYW43439 internally **[SPEC]**
- Erratum RP2350-E9: internal pull-downs can latch high **[SPEC]**

---

## 3. Motor drive

### TB6612FNG dual H-bridge — 2 planned, 1 in use

- VM range 2.5–13.5 V, VCC 2.7–5.5 V, ~1.2 A continuous / 3.2 A peak per
  channel **[SPEC]**
- Truth table: IN1/IN2 = `10` forward, `01` reverse, `11` brake, `00` coast.
  STBY low = hardware all-stop
- **[VERIFIED]** forward and reverse both confirmed working

### N20 gearmotors — 4 planned, 1 wired

- Model believed to be **CHF-GM12-N20VA-50-12V** **[UNCONFIRMED]** — seen in an
  open datasheet tab, never confirmed by the owner. If correct: **12 V rated**,
  50:1 gearbox.
- **This matters.** Everything tested so far ran at 5 V — roughly 42% of rated
  voltage. Any deadband or speed figure measured at 5 V will not transfer.
  **Confirm the model number before characterisation begins.**
- Six wires per motor: two are the motor, four are a quadrature encoder.

### The six-wire connector **[SPEC — from docs/harness.html]**

| Pin | Function | Destination |
|---|---|---|
| P1 | Motor terminal M1 | driver `AO1` |
| P2 | Hall sensor ground | common ground rail |
| P3 | Encoder **channel B** | Pico — the *higher* GP of the pair |
| P4 | Encoder **channel A** | Pico — the *lower* GP of the pair |
| P5 | Hall sensor power | **3.3 V, NOT 5 V** |
| P6 | Motor terminal M2 | driver `AO2` |

**Two traps, both of which cost real time:**

- P3 is B and P4 is A — not alphabetical along the connector. Verify on an
  analyser before trusting it.
- Wire colours are not standardised between suppliers. **An entire session was
  lost to driving four encoder wires into the H-bridge outputs**, which produced
  perfectly correct readings at every logic test point and no motion at all.
  Always confirm the motor pair by resistance (a few ohms) before wiring.

---

## 4. Current wiring — Stage 1, verified working

One motor, one driver, Pico and VM both powered over USB.

| TB6612 | Pico GP | Physical pin | Purpose |
|---|---|---|---|
| `VCC` | 3V3(OUT) | 36 | driver logic supply |
| `GND` | GND | 3 | shared zero |
| `PWMA` | GP2 | 4 | speed |
| `AIN1` | GP3 | 5 | direction 1 |
| `AIN2` | GP4 | 6 | direction 2 |
| `STBY` | GP5 | 7 | hardware enable |
| `VM` | **VBUS** | **40** | **temporary — see §5** |
| `AO1` / `AO2` | — | — | motor pair |

GP0/GP1 (pins 1/2) reserved for the Pi 5 UART. GP12–GP19 reserved for eight
encoder channels; left-front would be GP12 = A (pin 16), GP13 = B (pin 17).

**[VERIFIED] working:** motor runs forward and reverse, PWM at 20 kHz,
commanded live from the MicroPython REPL over USB serial.

---

## 5. Power — the current blocker

| Supply | Status |
|---|---|
| **9 V 6F22 block** | **[VERIFIED] UNUSABLE** — carbon-zinc, ~10 ohm internal resistance |
| **6xAA pack** | **[SPEC]** the specified bench supply — **NOT YET OWNED** |
| **4S LiPo** | **[SPEC]** for the rover. 16.8 V charged — exceeds the TB6612's 15 V max |
| **Pololu D24V90F5** | **[SPEC]** 5 V 9 A regulator. Mandatory between LiPo and everything |
| **Pico VBUS (USB 5 V)** | **[VERIFIED]** current stopgap for VM |

**The 6F22 measurement, because the method is reusable:** 8.2 V open circuit,
7.7 V under only ~50 mA, giving roughly **10 ohms** of internal resistance. At an
N20's ~500 mA startup surge that is a 5 V internal drop, taking VM below the
TB6612's 2.5 V minimum. The driver browns out before the rotor moves.

**Lesson:** judge a supply by its **sag under load**, not its open-circuit
voltage. Open circuit tells you what a source *is*; loaded tells you what it can
*do*. Another 6F22 would behave identically — the chemistry is the problem
(alkaline is `6LR61`).

**Action required:** acquire a 6xAA holder before motor characterisation.
Characterising a 12 V motor on a 5 V USB rail produces numbers that do not
transfer to the rover.

---

## 6. Test equipment

### 8-channel logic analyser

- Cypress FX2 based, **VID:PID `0925:3881`** (Saleae Logic clone)
- **[VERIFIED]** physically present and detected by `sigrok-cli`
- **BLOCKED:** `LIBUSB_ERROR_NOT_SUPPORTED` — needs the WinUSB driver bound via
  Zadig (bundled at `C:\Program Files\sigrok\PulseView\zadig.exe`). Requires
  elevation and GUI interaction. Never completed.
- Planned channel map — all 3.3 V logic, deliberately **not** AO1/AO2, which sit
  at motor voltage and would destroy the analyser at 9 V:

| CH | Signal | Pico GP / pin |
|---|---|---|
| 0 | PWMA | GP2 / 4 |
| 1 | AIN1 | GP3 / 5 |
| 2 | AIN2 | GP4 / 6 |
| 3 | STBY | GP5 / 7 |
| 4 | Encoder A | GP12 / 16 |
| 5 | Encoder B | GP13 / 17 |
| 6 | UART TX | GP0 / 1 |
| 7 | UART RX | GP1 / 2 |

### FTDI FT232RL USB-to-serial breakout

- Red SparkFun-style clone, mini-USB, 6-pin header:
  `DTR · RXI · TXO · VCC · CTS · GND`
- **Voltage selectable 3.3 V / 5 V — must be set to 3.3 V** before touching the
  Pico. Verify on the `VCC` pin with a meter first.
- **Its real value here:** it stands in for the Pi 5. Wire `TXO`→GP1,
  `RXI`→GP0, `GND`→rail, leave `VCC` disconnected. That gives a second,
  independent serial channel, so the Pi↔Pico protocol and the failsafe timeout
  can be built and tested before the Pi is involved — and it mirrors the
  architecture's separation of USB debug serial from the hardware UART.

### Multimeter

Volts, ohms, continuity, diode test, mA ranges.

Known limitation: it does **not** reliably average a 20 kHz square wave. An
unexplained 0.89 V reading on a 50%-duty 3.3 V PWM line is still outstanding
and is a job for the analyser.

### Other

- 3D printer — for chassis, motor mounts, encoder brackets
- 7-inch drone — future, explicitly out of scope for now

---

## 7. Sensing (not yet started)

**Intel RealSense D415** depth camera on the Pi 5.

- Active IR stereo, **rolling shutter** — the D435 is global shutter; the D415
  smears geometry under motion, which matters when driving **[SPEC]**
- Depth FOV ≈ 65° x 40°, narrower than a D435. Blind to the sides
- Usable range ≈ 0.45 m to ~3 m. **It cannot see anything closer than ~0.45 m** —
  stopping distance must account for that blind zone

**Planned approach:** classify obstacles by **height above the ground plane**,
not by raw distance. Naive distance thresholding flags the floor as an obstacle;
height-based classification separates drivable ground, real obstacles, and
passable overheads.

---

## 8. Software state

| Tool | Status |
|---|---|
| MicroPython 1.29.0 on the Pico | **[VERIFIED]** running, REPL over USB serial |
| Arduino IDE 2.3.10 + AVR core | **[VERIFIED]** installed, uploads working |
| `sigrok-cli` 0.8.0-git + PulseView | **[VERIFIED]** installed |
| Zadig | bundled, **not yet run** |
| Git repo | `C:\claude-code\rover`, **no remote** — nothing pushed anywhere |

**The firmware toolchain is still an open decision.** Candidates: official
pico-sdk (C/CMake), the community arduino-pico core, or MicroPython.

MicroPython was chosen as a **bring-up tool**, not as the production answer. The
REPL lets one pin be toggled and measured immediately, which is the right
instrument for answering "is my wiring correct?" — setting up a build system
first would mean every failure had two possible causes. Revisit the decision
when the questions become PIO quadrature decoding and PID loop timing rather
than continuity.

`firmware/` holds two Arduino sketches (a serial throttle and a ramp profile)
and the MicroPython `.uf2`. Nothing is committed; `firmware/` is untracked.

---

## 9. What has NOT been done

- **Encoders have never been connected.** Zero encoder data exists.
- **Deadband never measured** — deliberately deferred, because a 12 V motor on
  a 5 V rail gives a number that does not transfer.
- **The logic analyser has never captured anything** — blocked on the driver.
- **No firmware file on the Pico.** Everything so far was typed at the REPL and
  does not survive a power cycle.
- **Motors 2, 3 and 4 are unwired.** Only one has ever been driven.
- **The Pi 5 has never talked to the Pico.**
- **No characterisation data of any kind exists yet.**

---

## 10. Debugging method — worth preserving

Two techniques earned their keep and should carry into the characterisation work.

**Walk the causal chain.** A circuit is a chain of causes. Measure at every link
and find the *first* point where reality stops matching expectation. Everything
upstream is fine; everything downstream is innocent. This located a missing base
resistor in two measurements.

**Split the system in half.** When a chain of individually-correct readings
still ends in nothing happening, stop testing links and test the *assumption at
the end of the chain*. Touching the motor's two leads directly to a supply found
the wrong-wires fault in ten seconds, after an evening of correct measurements
had pointed nowhere. It should have been done much earlier.
