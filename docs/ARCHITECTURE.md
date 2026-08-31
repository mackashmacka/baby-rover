# ARCHITECTURE

Living map of components, data flows, boundaries and invariants.
Update as part of finishing any story that adds or changes structure.

**Status legend:** ✅ built and verified · 🟡 partially built · ⬜ planned, not started

Last updated **2026-08-31**.

---

## 1. System boundaries

Three domains. Each boundary exists for a specific reason, and the reason is
the invariant.

```
┌─────────────────────┐   UART 115200    ┌─────────────────────┐
│   Raspberry Pi 5    │  GP0/GP1 ⇄ 14/15 │  Pico 2 W (RP2350)  │
│   Linux, 3.3 V      │◄────────────────►│  bare-metal, 3.3 V  │
│                     │                  │                     │
│  RealSense D415  ⬜ │                  │  PWM gen         ✅ │
│  obstacle detect ⬜ │                  │  H-bridge ctrl   ✅ │
│  path planning   ⬜ │                  │  encoder count   ⬜ │
│  logging/teleop  ⬜ │                  │  PID loop        ⬜ │
│                     │                  │  failsafe stop   ⬜ │
└─────────────────────┘                  └──────────┬──────────┘
                                                    │ 3.3 V logic
                                         ═══════════╪═══════════
                                                    │ motor power
                                         ┌──────────▼──────────┐
                                         │  2x TB6612FNG    🟡 │
                                         │  4x N20 + encoder🟡 │
                                         └─────────────────────┘
```

### Boundary 1 — Pi ⇄ Pico: **timing determinism**

Anything needing hard timing guarantees (PID loop, encoder edges, PWM) lives on
the Pico. Linux cannot promise a jitter-free 100 Hz loop; a microcontroller can.
Anything needing compute, floating point at scale, or libraries lives on the Pi.

**Invariants:**
- Both sides are 3.3 V logic — **no level shifter**. TX↔RX crossed, common ground.
- **Failsafe:** the Pico stops the motors if no valid command arrives within
  ~200–500 ms. Non-negotiable. ⬜ *not yet implemented*
- Control stays on the **wired UART**. WiFi/BLE is telemetry only.
- USB debug serial is **independent** of the hardware UART — flashing and
  monitoring must never contend for one port.

### Boundary 2 — logic ⇄ power: **the H-bridge**

The TB6612 is the only crossing point. Logic signals enter at 3.3 V and a few
milliamps; battery energy enters separately; the motor receives switched power.

**Invariants:**
- **No motor current ever enters the Pico.** No wire from battery to Pico, none
  from `AO*`/`BO*` back to Pico.
- **Grounds must be common** or the driver cannot interpret any logic signal.
- `VM` ≤ 13.5 V. The 4S LiPo (16.8 V charged) **must** pass through the
  D24V90F5 regulator first.
- Encoder hall sensors run at **3.3 V, never 5 V** — RP2350 GPIO is not 5 V
  tolerant.

---

## 2. Pin map (Pico 2 W)

19 of 26 usable GPIO. Direction pins are shared per side — both wheels on a
side always turn together — preserving independent per-wheel *speed* control
while saving four pins.

| GP | Physical | Signal | Status |
|---|---|---|---|
| GP0 | 1 | UART TX → Pi | ⬜ |
| GP1 | 2 | UART RX ← Pi | ⬜ |
| GP2 | 4 | PWMA — left front speed | ✅ |
| GP3 | 5 | AIN1/BIN1 — left side direction 1 | ✅ |
| GP4 | 6 | AIN2/BIN2 — left side direction 2 | ✅ |
| GP5 | 7 | STBY — global hardware enable | ✅ |
| GP6–GP11 | — | right side PWM + direction | ⬜ |
| GP12–GP19 | 16–24 | 8 encoder channels (A = lower pin, B = higher) | ⬜ |

Do not use **GP23/24/25/29** — wired to the CYW43439 internally.

---

## 3. Data flows

### 3.1 Command path ⬜ *not built*

```
Pi: (v_linear, ω_angular)
  → skid-steer mixer → (v_left, v_right)
  → UART frame @115200
  → Pico: parse, validate, reset failsafe timer
  → per-wheel PID setpoint (rad/s)
```

### 3.2 Control loop ⬜ *not built* — target 100 Hz

```
encoder edges → PIO quadrature decoder → counts
  → Δcounts / Δt → ticks/s → rad/s          ← units boundary, see conventions
  → PID(setpoint − measured) → duty
  → PWM slice → TB6612 → motor
```

**Why PIO:** 4 encoders × 2 channels × 4 edges of per-edge GPIO interrupts
would swamp the CPU and starve the control loop. PIO decodes independently of
the core.

### 3.3 Perception path ⬜ *not built*

```
D415 depth frame → point cloud → transform to robot frame
  → classify by HEIGHT ABOVE GROUND PLANE
  → {drivable ground | obstacle | passable overhead}
  → occupancy grid → path plan → (v, ω)
```

**Why height, not distance:** naive distance thresholding flags the floor as an
obstacle. Height-based classification separates the three cases.

**Sensor invariants:** FOV ≈ 65° × 40°, blind to the sides. **Cannot see closer
than ~0.45 m** — stopping distance must account for that blind zone. Rolling
shutter smears geometry under motion.

---

## 4. Currently built (Stage 1) ✅

One motor, one driver, Pico and `VM` both on USB 5 V.

`Pico GP2/3/4/5 → TB6612 PWMA/AIN1/AIN2/STBY`, `VM ← Pico VBUS (pin 40)`,
one N20 on `AO1`/`AO2`, common ground rail.

Verified: forward and reverse, 20 kHz PWM, commanded from the MicroPython REPL.

**Known deviations from the target design:**
- `VM` comes from USB VBUS, not a battery — motor current shares a rail with
  the Pico, violating Boundary 2's separation. Temporary. See
  [`memory/power-supply.md`](../memory/power-supply.md).
- No firmware file exists; all behaviour was typed at the REPL and does not
  survive a power cycle.

---

## 5. Repo layout

```
firmware/          Pico firmware + Arduino learning sketches
pi/            ⬜  Python: RealSense, obstacle detection, navigation
docs/              wiring guides, architecture, decisions and rationale
tools/         ⬜  host-side scripts (serial monitor, analyser helpers)
memory/            the memory wiki — read MEMORY.md first
```

---

## 6. Conventions that are invariants

- **Units are SI and stated in names or comments when ambiguous**
  (`speed_mps`, `omega_rad_s`, `ticks_per_rev`). Mixed units are a leading
  cause of control bugs, and the ticks→rad/s conversion in §3.2 is where they
  will bite.
- **Per-wheel direction sign lives in firmware, not in the wiring.** Left and
  right wheels are physical mirrors; wire all four identically.
- Never commit secrets. Commit a `*.example`.

---

## 7. Known structural gaps

| Gap | Consequence |
|---|---|
| Failsafe timeout not implemented | a rover that keeps driving when the Pi dies — the one hazard `CLAUDE.md` calls non-negotiable |
| No encoder decoding | no closed loop; everything is open-loop duty |
| Firmware toolchain undecided | PIO quadrature is the forcing function |
| No test harness | "run the FULL suite" has nothing to run |
| Skid-steer scrub | odometry unreliable during turns; current spikes; behaviour changes with surface friction |
