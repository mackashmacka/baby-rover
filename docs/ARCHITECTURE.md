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

### Boundary 3 — firmware ⇄ bench: **commands, not behaviour**

Added 2026-08-31. The Pico exposes a line-oriented ASCII command surface over
USB CDC (`SET`, `STOP`, `ENC?`, `TELEM`, `PID`, …) and makes **no experimental
decisions**. The experiment lives entirely on the host, in
`tools/rover_bench/`.

The invariant: **the firmware never knows which experiment is running.** That
is what lets the same firmware serve Story 1.5 characterisation, Story 1.6 PID
tuning and the Story 2.1 failsafe test, and it is what stops an agent
improvising a slightly different measurement on motor 3 than it used on motor
1. Four datasets that cannot be compared are worse than none, because they look
like data.

The same framing becomes the Pi⇄Pico protocol at Story 2.1 — the FTDI FT232R
stands in for the Pi until then. Built once, not twice.

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
| GP20 | 26 | **LOOP_TICK** — toggles once per control iteration | ✅ |
| GP21 | 27 | **COMPUTE_BUSY** — HIGH across the loop body | ✅ |

Do not use **GP23/24/25/29** — wired to the CYW43439 internally.

`GP20`/`GP21` exist only to be watched. The firmware cannot measure its own
timing honestly — a loop that has stalled cannot notice it has stalled — so it
exports the two facts an external instrument can turn into numbers: iteration
period (from LOOP_TICK edges) and CPU duty (from COMPUTE_BUSY's high time).
Two GPIO writes per tick, and they are what make this a *control-system*
characterisation rather than a motor one.

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

---

## 4b. Currently built (Stage 2 — the bench) ✅ *added 2026-08-31*

The instrument, not the rover. All of it runs on the **laptop**; the Pi has no
hardware attached and its role does not begin until Story 2.6.

| Component | Where | State |
|---|---|---|
| Characterisation firmware | `firmware/` | builds clean → `rover_bench.uf2` (105 K), **not yet flashed** |
| PIO x4 quadrature decoder | `firmware/src/quadrature.pio` | written, **never run against a real encoder** |
| 100 Hz PID + anti-windup | `firmware/src/control.c` | host-tested, **never closed against a real motor** |
| Link-loss failsafe (300 ms) | `firmware/src/main.c` | implemented, **timing not yet measured on the analyser** |
| Host bench driver | `tools/rover_bench/` | 1137 tests, 89 % line coverage |
| Analysis + plots | `tools/analysis/` | works against synthetic data only |
| Session/journal machinery | `tools/session.py` | working; stdlib only, runs before anything is installed |
| Logic analyser | `sigrok-cli`, `fx2lafw` | **PROVEN** — 19,999.0 Hz @ 50.00 %, 10 ns jitter |

**The honest line:** everything above is verified in software and against one
known square wave. Nothing in it has yet moved a motor. The distinction matters
and should not be allowed to blur.

**Invariant introduced by the bench:** every run writes a manifest naming the
commit, the firmware SHA, the exact argv and the sample rate actually used. A
run without a manifest is not evidence, and two runs whose manifests differ in
a way that affects the measurement are not comparable — `tools/analysis/load.py`
refuses them rather than quietly averaging them.

---

## 5. Repo layout

```
firmware/          ✅ Pico 2 W firmware (pico-sdk C) — PWM, PIO quadrature,
                      100 Hz PID, command protocol, failsafe, instrumentation
pi/            ⬜  Python: RealSense, obstacle detection, navigation (Story 2.6)
docs/              ✅ wiring, architecture, bench manual, experiment specs
  docs/experiments/   per-story experiment specifications
tools/             ✅ host side
  tools/rover_bench/  the bench driver — link, analyser, experiment, manifest,
                      storage, registry, safety, doctor, cli, fakes
  tools/analysis/     metrics with uncertainties, plots, report assembly
  tools/session.py    session ritual — start, journal, wiki, close, lint, index
tests/             ✅ 1137 host-side tests, 80 % coverage gate, no hardware
experiments/       ✅ REGISTRY.md + one directory per experiment run
memory/            the memory wiki — read MEMORY.md first
.claude/skills/    ✅ close-ritual, rover-bench (provisional), memory-lint
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
- **This document is enforced, not aspirational.** `tools/session.py close`
  compares the last commit touching `ARCHITECTURE.md` against the last commit
  touching source, and refuses to close a story when source has moved and the
  map has not. Same commit is the good case: the map moves *with* the code.
  `tests/test_docs_consistency.py` additionally pins the pin map here against
  the firmware and `WIRING.md`, because the map drifting is the failure mode
  that costs an afternoon and leaves no trace.
- **Compile-time claims are labelled as such.** Section 4b says what has been
  built and what has been *run*, and a test asserts this file does not claim
  the firmware has been flashed until it has.

---

## 7. Known structural gaps

**Closed 2026-08-31:** firmware toolchain decided (pico-sdk, C/CMake — the
characterisation firmware is ~80 % of the production firmware, so MicroPython
would have meant building it twice); test harness exists and is enforced;
encoder decoding written; failsafe implemented.

| Gap | Consequence |
|---|---|
| **Nothing has been flashed** | every firmware claim above is a compile-time claim, not a runtime one |
| **`ticks_per_rev` unmeasured** (11 vs 14 disputed) | no ticks→rad/s conversion exists, so every speed figure is emitted in ticks/s and flagged unconverted. Poisons odometry and the EKF downstream if ever guessed |
| **Failsafe timing unmeasured** | it is implemented but the 300 ms is a constant, not yet a measured number. `CLAUDE.md` calls this non-negotiable |
| Only analyser D0 physically probed | D1–D7 carry no signal; no direction, encoder or loop-timing capture is possible yet |
| Motors 2–4 unwired; `VM` on the VBUS stopgap | the four-motor overlay — the point of Story 1.5 — cannot be produced |
| `tools/analysis/` never run on real data | it works against `synthetic.py` only |
| Skid-steer scrub | odometry unreliable during turns; current spikes; behaviour changes with surface friction |
