# HARDWARE — ground truth spec

**This file is the stable reference.** Part numbers, electrical ratings,
datasheet figures, and the constants derived from them. It changes only when a
physical part changes or a figure is measured and confirmed.

**Wiring lives in [`WIRING.md`](WIRING.md)** — pin assignments, harness
colours, what is currently connected. That file changes constantly. Keeping
them apart is the point: this one must stay trustworthy.

Last verified against supplier pages **2026-08-31**.

| Tag | Meaning |
|---|---|
| **[SUPPLIER]** | Stated by the vendor or manufacturer. Not independently checked |
| **[DERIVED]** | Calculated here from other figures. Arithmetic shown |
| **[MEASURE]** | Must be measured on the bench. Do not trust a number until it is |
| **[CONFIRM]** | Unknown — needs the owner to answer. See §8 |

> **Rule:** a supplier figure is a hypothesis, not a fact. Where a **[SUPPLIER]**
> number matters to a design decision, it gets measured and re-tagged.

---

## 1. Compute

| Item | Part | Key spec |
|---|---|---|
| Dev machine | Laptop, Ubuntu 24.04 dual-boot | Firmware build host |
| High-level brain | **Raspberry Pi 5** (`baby-rover`) | 4× Cortex-A76 @ 2.4 GHz, 7.8 GB RAM, **no swap**, rootfs on a USB stick |
| Realtime controller | **Raspberry Pi Pico 2 W** (RP2350) | Dual Cortex-M33 @ 150 MHz, 520 KB SRAM, 4 MB flash, **3.3 V GPIO, NOT 5 V tolerant** |
| Bench tool | Arduino UNO R3 | 5 V logic — do not wire directly to the Pico |

**RP2350 peripherals that matter:** 3 PIO blocks × 4 state machines (quadrature
decoding), 12 PWM slices × 2 channels. Usable GPIO `GP0–GP22`, `GP26–GP28`.
**`GP23/24/25/29` are internal to the CYW43439** — unusable.

**Erratum RP2350-E9:** internal pull-downs can latch high. On a stuck-high
input, suspect this before suspecting the wiring.

---

## 2. Drive

### 2.1 Gearmotors — N20 ×4 [CONFIRM qty]

**N20 DC gearmotor with magnetic encoder — 6 V, 1:100.**

*Supplier reference only: Core Electronics `ADA4639` / Adafruit 4639. That is
the SKU for this exact N20 — quoted so the specs below are traceable, not
because the part is called anything other than an N20.*

> 🔴 **This supersedes the previous guess of `CHF-GM12-N20VA-50-12V` (12 V, 50:1),
> which appears nowhere in the supplier data and was never confirmed.** Every
> conclusion that depended on a 12 V rating is void — see §6.1.

| Parameter | Value | Tag |
|---|---|---|
| Rated voltage | **4.5–6 V DC**, 6 V nominal | [SUPPLIER] |
| Gear ratio | **1:100** | [SUPPLIER] |
| No-load current | ~100 mA | [SUPPLIER] |
| Stall current | ~200 mA | [SUPPLIER] ⚠️ see below |
| No-load output RPM | not published for this ratio | **[MEASURE]** |
| Stall torque | not published | **[MEASURE]** |
| Encoder | 2× hall sensors on a magnetic wheel, on the **motor** shaft | [SUPPLIER] |
| Encoder supply | **3–5 V DC** | [SUPPLIER] |
| Body | 30.5 × 18.5 mm excl. shaft; leads ~150 mm | [SUPPLIER] |

**⚠️ The 200 mA stall figure is suspiciously low** for an N20 — comparable
gearmotors are commonly several hundred mA to over an amp at stall. It is a
manufacturer figure and it feeds the driver-headroom calculation in §6.2.
**Measure it before relying on it** (Story 1.5).

#### Encoder resolution — the sources disagree

| Source | Claim |
|---|---|
| Adafruit product text | "**14 counts per revolution** on the encoder pins, which should be multiplied by the gear ratio" |
| Retailer listings for this N20 | "**11 pulses per revolution**" |

**[DERIVED]** ⇒ somewhere between **1100 and 1400 counts per revolution of the
output shaft** (motor-shaft count × 100), and possibly 2× or 4× that depending
on whether the decoder counts one edge or all four.

**This is not resolvable from datasheets, and it must be right** — every
downstream number (PID feedback, odometry, the EKF prediction step) scales
directly with it.

**[MEASURE]** Story 1.4: mark the output shaft, rotate it exactly one turn by
hand, count the edges the decoder reports. That single measurement settles it
and supersedes both figures above.

#### Wire colours — confirmed, and they differ from the old docs

| Colour | Function | Goes to |
|---|---|---|
| **Red** | Motor terminal | H-bridge output |
| **White** | Motor terminal | H-bridge output |
| **Black** | **Ground** (encoder / microcontroller) | Common ground rail |
| **Blue** | **Encoder power, 3–5 V** | **3.3 V** |
| **Yellow** | Hall sensor output | Pico GPIO |
| **Green** | Hall sensor output | Pico GPIO |

> Two supplier summaries disagreed on black vs blue. Adafruit's own wording is
> definitive: *"Connect the black wire to your microcontroller ground pin, and
> the blue wire to 3-5V DC."* **Black is ground. Blue is power.**
>
> **Confirm with a meter before connecting anyway.** An evening has already
> been lost on this project to mis-identified motor wires. Red–white should
> read a few ohms across the motor; black–blue should not.

**Which of yellow/green is channel A is not specified and does not matter** —
pick one, then verify the phase direction on the analyser and fix the sign in
firmware, never in the wiring.

### 2.2 Motor driver — TB6612FNG ×2

Generic TB6612FNG breakout.

| Parameter | Value | Tag |
|---|---|---|
| Motor supply `VM` | 2.5–13.5 V | [SUPPLIER] |
| Logic supply `VCC` | 2.7–5.5 V | [SUPPLIER] |
| Output current | **1.2 A continuous, 3.2 A peak per channel** | [SUPPLIER] |
| Channels | 2 per chip ⇒ 2 chips = 4 motors | — |
| Control | `xIN1`, `xIN2`, `PWMx` per channel; one `STBY` per chip | — |

Truth table per channel: `IN1/IN2` = `10` forward · `01` reverse · `11` brake ·
`00` coast. **`STBY` low is an instant hardware all-stop.** Brake shorts the
windings and stops hard; coast freewheels — they are not the same thing.

**Forward voltage drop:** the H-bridge drops roughly 0.5 V total under load, so
the motor sees less than `VM`. At `VM` = 6.0 V the motor sees ~5.5 V. To deliver
a true 6 V at the terminals, `VM` must be slightly above 6 V — but the motor's
4.5–6 V window makes 5 V a perfectly sensible operating point anyway.

---

## 3. Sensing

### 3.1 IMU — MPU-6050 module

Core Electronics SKU `018-MPU-6050`. **6-axis: gyroscope + accelerometer, no
magnetometer** — which is why §3.2 exists.

| Parameter | Value | Tag |
|---|---|---|
| Module supply | 3–5 V, **onboard regulator** | [SUPPLIER] |
| **Logic level** | **3.3 V** | [SUPPLIER] |
| Interface | I²C |  |
| I²C address | **`0x68`** default; `0x69` by tying `ADR` to 3.3 V | [SUPPLIER] |
| Gyro ranges | ±250 / 500 / 1000 / 2000 °/s | [SUPPLIER] |
| Accel ranges | ±2 / 4 / 8 / 16 g | [SUPPLIER] |
| Size | 21 × 16 mm | [SUPPLIER] |

> ⚠️ **Power this module from 3.3 V, not 5 V.** These breakouts pull the I²C
> lines up to their supply rail. Powered at 5 V, `SDA`/`SCL` sit at 5 V — and
> **RP2350 GPIO is not 5 V tolerant.** Powering from 3.3 V makes it impossible
> to get that wrong.

**Headers ship loose — soldering required** [SUPPLIER].

**Choice rationale:** raw gyro and accelerometer output, deliberately *not* a
BNO055. A BNO055 fuses onboard and returns a finished quaternion, which removes
the single most valuable thing in this project. See `PLAN.md` §6.

### 3.2 Magnetometer — Jaycar XC4496 (Duinotech 3-axis compass)

🟠 **The chip varies by production run, and the three candidates are not
software-compatible** — different registers, different addresses.

| Chip | I²C address | PCB marking | Era |
|---|---|---|---|
| Honeywell **HMC5883L** | `0x1E` | `L883` | original module |
| **QMC5883L** (GY-271 clone) | `0x0D` | `5883` | common substitute |
| MEMSIC **MMC5883MA** | reported `0x30` — **confirm by scan** | — | Duinotech, ~2021 onward |

**Identify it in 30 seconds with an I²C scan** before writing a line of driver
code. The address alone tells you which chip you have, and therefore which
library. A Duinotech-specific `MMC5883MA` Arduino library exists (Reefwing) if
it turns out to be that one.

Module has an onboard regulator and accepts 3.3–6 V [SUPPLIER, HMC5883L
version]. **Power it from 3.3 V** for the same pull-up reason as §3.1.

**No address conflict with the MPU-6050** (`0x68`) on any variant — the two can
share one I²C bus.

### 3.3 GPS — u-blox NEO-M9N

⏸ **Deferred — the exact module gets pinned down in Week 2, when it is needed.**
Nothing before then depends on it. What is already settled:

- **Outdoors only.** No sky view, no fix. This is why the mission splits into
  an indoor and an outdoor course (`PLAN.md` §2)
- Serial NMEA or UBX
- u-blox **M9** platform — a generation up from the M8 the earlier notes
  assumed. Concrete figures deliberately **not** quoted here until the module
  is chosen; the number that matters is measured anyway
- **[MEASURE]** static scatter over 20 minutes (Story 2.4). That scatter *is*
  the measurement noise the fusion stage needs, and it beats any datasheet CEP
- RTK deliberately not used

### 3.4 Depth camera — Intel RealSense D415

| Parameter | Value |
|---|---|
| Type | Active IR stereo, **rolling shutter** (D435 is global shutter) |
| Depth FOV | ≈ 65° × 40° — **blind to the sides** |
| Usable range | ≈ **0.45 m** to ~3 m |
| Interface | USB 3.0 |

**The 0.45 m minimum is a hard constraint on stopping distance**, not a
guideline — inside it the rover is blind. Rolling shutter smears geometry under
motion, which matters precisely when driving.

---

## 4. Power

| Supply | Spec | Status |
|---|---|---|
| **4S LiPo** | 16.8 V charged, 14.8 V nominal [CONFIRM capacity] | Rover pack |
| **Pololu D24V90F5** | 5 V out, **9 A**, 4.5–38 V in [CONFIRM owned] | **Mandatory** between LiPo and everything |
| 9 V 6F22 block | carbon-zinc, **~10 Ω internal** [MEASURED] | 🔴 **Unusable — see §6.1** |
| Bench supply | — | 🔴 **Needed. See §6.1 — the 6×AA plan is now wrong** |
| Pico VBUS (USB 5 V) | — | Current stopgap for `VM` |

**16.8 V exceeds the TB6612's 13.5 V `VM` maximum.** The LiPo must never reach
the driver directly. The regulator is not optional.

---

## 5. Instruments

| Item | Detail |
|---|---|
| **8-ch logic analyser** | Cypress FX2 clone, USB ID **`0925:3881`**. On Linux needs only a udev rule |
| **FTDI FT232RL** | **Voltage-selectable — set to 3.3 V and verify on the VCC pin before use** |
| Multimeter | Does *not* reliably average a 20 kHz square wave |
| 3D printer | Chassis, mounts, magnetometer mast [CONFIRM model] |

⚠️ **Never probe `AO1`/`AO2`/`BO1`/`BO2` with the analyser** — those sit at motor
voltage and will destroy it.

---

## 6. Derived figures

### 6.1 Voltage — what the 6 V rating changes

The motor is **6 V**, not 12 V. Three consequences, and the second one reverses
a recommendation already written into the older docs:

1. ✅ **The characterisation blocker is closed.** Running at 5 V is **83% of
   rated**, not 42%. Numbers taken at 5 V are broadly representative, and
   characterisation no longer has to wait on a new supply.

2. 🔴 **The 6×AA pack is now the wrong part.** [DERIVED]
   - 6× alkaline = **9.0 V** = **150% of rated** — would cook the motors
   - 6× NiMH = 7.2 V = 120% of rated — still over
   - **Correct bench supply:** 4× alkaline (6.0 V), 5× NiMH (6.0 V), or a lab
     PSU set to 6.0 V. **A lab PSU is the better answer** — current limiting
     turns a wiring mistake into a beep instead of smoke.

3. ✅ **`VM` = 5 V from the D24V90F5 is a legitimate operating point**, not a
   compromise. It sits inside the 4.5–6 V window with margin at both ends.

**The 6F22 is still unusable, and now quantifiably so.** [DERIVED] At 4 motors
× 100 mA = 400 mA into ~10 Ω internal, the drop is 4.0 V from 8.2 V open
circuit ⇒ **4.2 V delivered, below the motor's 4.5 V minimum before it does any
work at all.** At stall it collapses entirely. The chemistry is the problem, not
the charge.

### 6.2 Current budget [DERIVED]

| Load | No-load | Stall |
|---|---|---|
| 4 motors | 400 mA | **800 mA** |
| Encoders (4×, est.) | ~40 mA | ~40 mA |
| Pico 2 W | ~50 mA | ~50 mA |
| **Motor subsystem total** | **~0.5 A** | **~0.9 A** |

**Driver headroom: ~6×.** 200 mA against a 1.2 A continuous per-channel rating.
Even if the stall figure is off by 3×, the TB6612 is not close to its limit —
so the standing "stalling into a wall kills the driver" warning is much weaker
for *these* motors than for the generic case. **Re-check once stall current is
measured.**

**The counter-intuitive part:** the Pi 5 and D415 together draw **several
amps** — many times the entire motor subsystem. The 9 A regulator is sized for
the computer, not the drivetrain. Plan the wiring and the battery around that.

### 6.3 Encoder edge rate [DERIVED] — why PIO, not interrupts

Taking ~1400 counts per output revolution and a plausible 300 output RPM
(5 rev/s):

**5 × 1400 = 7,000 counts/s per motor ⇒ ~28,000 counts/s across four.**

As GPIO interrupts that is ~28k ISR entries per second competing with a 100 Hz
control loop — which is exactly what would starve it. **PIO decodes this in
hardware, independently of the cores.** The architecture decision holds, and now
it has a number behind it.

### 6.4 Odometry constant

`metres per count = (π × wheel_diameter) / counts_per_output_revolution`

Both terms are **[MEASURE]**, and both are bench measurements rather than
spec-sheet lookups:

| Term | How |
|---|---|
| `counts_per_output_revolution` | Turn the output shaft one full revolution by hand, count edges (§2.1) |
| `wheel_diameter` | Calipers or a ruler. Re-measure if the wheels are reprinted in Story 3.3 |

Also needed for skid-steer kinematics: **track width**, the centre-to-centre
distance between the left and right wheels.

None of this blocks anything today — it is three minutes of measuring on the
day the encoders first spin. It is listed here only so the constant has one
documented home instead of being hardcoded in three files.

---

## 7. Bus and address allocation

| Device | Bus | Address | Host |
|---|---|---|---|
| MPU-6050 | I²C | `0x68` | Pi (recommended) |
| Magnetometer | I²C | `0x1E` / `0x0D` / `0x30` | Pi (recommended) |
| NEO-M9N | UART | — | Pi |
| Pico ⇄ Pi | UART | — | both, 115200 |
| Encoders ×4 | GPIO/PIO | — | **Pico — must be** |

**Recommended split:** encoders on the Pico because they need hardware timing;
every other sensor on the Pi because the fusion runs there and the UART link
stays a thin command/telemetry channel rather than a sensor bus. Changeable —
recorded in `WIRING.md` if it changes.

⚠️ The Pi's `GPIO14/15` (UART0) are already committed to the Pico link. The GPS
therefore needs **a second Pi UART via a `dtoverlay`, or a USB-serial adapter.**
Decide before wiring — see §8.

---

## 8. Open items — need the owner

| # | Question | Blocks |
|---|---|---|
| 1 | **Quantities** — 4 motors and 2 drivers confirmed? Spare Pico? | Build order |
| 2 | **Battery + regulator** — LiPo capacity and connector; is the D24V90F5 actually in hand? | §4 |
| 3 | **3D printer model** — build volume and material | Story 3.3 |

**Deferred by decision, not missing:** the exact NEO-M9N module (§3.3) gets
chosen in Week 2 when it is needed.

**Not questions — bench measurements** he takes himself: encoder counts/rev,
wheel diameter, track width, stall current, no-load RPM. See §6.4.

---

## 9. Sources

- N20 6 V 1:100 — [Core Electronics](https://core-electronics.com.au/n20-dc-motor-with-magnetic-encoder-6v-with-1-100-gear-ratio.html) · [Adafruit](https://www.adafruit.com/product/4639)
- MPU-6050 — [Core Electronics](https://core-electronics.com.au/mpu-6050-module-3-axis-gyroscope-acce-lerometer.html)
- Magnetometer — [Jaycar XC4496](https://www.jaycar.com.au/arduino-compatible-3-axis-compass-magnetometer-module/p/XC4496) (page blocks automated fetch; specs from the chip datasheets)
- [HMC5883L datasheet](https://cdn-shop.adafruit.com/datasheets/HMC5883L_3-Axis_Digital_Compass_IC.pdf) · [QMC5883L driver notes](https://github.com/dthain/QMC5883L) · [MMC5883MA Duinotech library](https://github.com/Reefwing-Software/MMC5883MA-Arduino-Library)
