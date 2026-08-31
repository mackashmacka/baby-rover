# Rover — 4-wheel skid-steer robot

## Prime directive: this is a learning project

**A working rover the owner does not understand is a failure.** Optimize for
the owner's understanding, not for shipping code fast.

Skills being built, in priority order:
1. Robotics (control, kinematics, perception)
2. Embedded systems (RP2350, timing, peripherals, signal debugging)
3. Using Claude Code effectively

### Working agreement

- **Explain reasoning as you go.** Name the concept behind each decision
  (e.g. "this is integral windup", "this is a rolling-shutter artifact") so
  the owner can search for it independently later.
- **Build up to code, don't dump it.** Introduce a file in pieces with the
  reasoning attached. No large finished implementations delivered cold.
- **Division of labour (owner's call, 2026-08-28):** the owner is learning the
  *wiring and electrical* side hands-on; Claude writes the firmware and code.
  Still explain what firmware does and why, but the depth of explanation goes
  into electrical/hardware topics, not into teaching code line by line.
- **Walk through diagnosis, don't silently fix.** When something is broken,
  narrate the hypothesis → test → result chain. The debugging method is more
  valuable than the fix. Say what evidence would falsify the hypothesis.
- **Ask before installing anything** — system packages, toolchains, Python
  packages, PlatformIO libraries. State what it is and why it's needed.
- Prefer the boring, inspectable solution over the clever one.
- When there's a real engineering tradeoff, present it as a tradeoff and give
  a recommendation — don't silently pick and move on.

## Architecture

Two-brain split. The dividing line is **timing determinism**:

```
  Raspberry Pi 5                        Pico 2 W (RP2350)
  (Linux, non-realtime)                 (bare-metal, realtime)
  - RealSense D415 depth                - PID speed loop per wheel
  - obstacle detection                  - encoder counting
  - navigation decisions        UART    - PWM generation
  - logging / teleop           <----->  - motor driver control
                              115200    - failsafe stop on comms loss
```

Anything needing hard timing guarantees (PID loop, encoder edges, PWM) lives
on the Pico. Linux cannot promise a jitter-free 100 Hz loop; a microcontroller
can. Anything needing compute, floating point at scale, or libraries (vision)
lives on the Pi.

**Failsafe rule:** the Pico stops the motors if it has not received a valid
command within a timeout (~200-500 ms). A rover that keeps driving when the
Pi crashes or the cable pops out is a hazard. This is non-negotiable.

## Hardware

### Raspberry Pi 5
- High-level brain. 3.3 V GPIO logic.
- UART to the Pico on GPIO14 (TXD) / GPIO15 (RXD).

### Intel RealSense D415
- Active IR stereo depth camera. Rolling shutter (D435 is global shutter —
  the D415 smears geometry under motion; relevant when driving).
- Depth FOV ≈ 65° x 40° — narrower than the D435. Blind to the sides.
- Usable depth range ≈ 0.45 m to ~3 m. **It cannot see obstacles closer than
  ~0.45 m.** Stopping distance must account for this blind zone.

### Raspberry Pi Pico 2 W (RP2350) — motor controller
- **Not an ESP32.** RP2350, dual Cortex-M33 @ 150 MHz (also has RISC-V
  Hazard3 cores selectable at boot), 520 KB SRAM, 4 MB flash.
- 3.3 V logic — matches the Pi 5, so **no level shifter** on the UART.
  TX->RX crossed, common ground required.
- **Native USB** (no CH340/CP2102/FTDI). Flash by holding BOOTSEL and
  dropping a .uf2 on the mass-storage drive. No USB drivers needed, ever.
  USB serial for debug is independent of the hardware UART to the Pi.
- Key peripherals:
  - **PIO** — 3 blocks x 4 state machines = 12 programmable I/O processors
    running independently of the CPU. **Use PIO for quadrature encoder
    decoding.** Per-edge GPIO interrupts (4 encoders x 2 ch x 4 edges) would
    swamp the CPU and starve the control loop.
  - **PWM** — 12 slices x 2 channels. The two channels in a slice share a
    counter/frequency but have independent duty. All motors run the same
    PWM frequency, so this is not a constraint here.
- Usable GPIO: GP0-GP22, GP26-GP28. GP23/24/25/29 are wired to the CYW43439
  wireless chip internally — do not use them.
- Wireless (CYW43439, WiFi + BLE) available for telemetry. **Control stays
  on the wired UART** — latency and reliability matter for a moving robot.
- **Erratum RP2350-E9:** internal pull-downs can latch high. If an input
  reads stuck-high, suspect this before suspecting wiring. Encoders using
  pull-ups are unaffected.

### 2x TB6612FNG motor drivers (one per side)
- Dual H-bridge. Each chip has 2 independent channels, so 2 chips = 4
  channels = one per motor.
- Per channel: `xIN1`, `xIN2` (direction), `PWMx` (speed). Plus one `STBY`
  per chip — held HIGH to enable, driven LOW for an instant hardware
  all-stop.
- Truth table per channel: IN1/IN2 = 10 forward, 01 reverse, 11 brake,
  00 coast. Brake and coast are different — brake shorts the motor windings
  and stops hard; coast freewheels.
- Limits: ~1.2 A continuous per channel, ~3.2 A peak. With the actual motors
  (N20, ~200 mA claimed stall) that is roughly **6x headroom**, so
  the usual "stalling into a wall kills the driver" warning is weak here —
  but the stall figure is a supplier claim and wants measuring before it is
  relied on. Exact figures: `docs/HARDWARE.md`.
- Logic VCC and motor VM are separate supplies. Grounds must be common.

### 4x gearmotors
- **N20 gearmotors — 6 V (4.5-6 V), 1:100, with magnetic encoders.** Full
  spec in `docs/HARDWARE.md`; wiring in `docs/WIRING.md`. Earlier docs guessed
  a 12 V / 50:1 part — that was wrong and every conclusion resting on it is
  void.
- **Pin budget** (26 usable): 4 PWM + 4 direction + 1 STBY + 8 encoder + 2
  UART = 19. Direction pins are shared per side (both wheels on a side
  always turn the same way), which keeps independent per-wheel *speed*
  control for PID while saving 4 pins.
- Skid steer: left pair driven together, right pair driven together.
- Turning is achieved by differential wheel speed; the wheels **scrub**
  sideways against the ground. Consequences: odometry from encoders is
  unreliable during turns, current draw spikes when turning, and behavior
  changes a lot with surface friction (carpet vs. hardwood).

### Cheap logic analyzer
- For inspecting PWM duty/frequency, UART framing, and encoder quadrature.
- Use it to answer "is the signal actually what I think it is?" before
  suspecting code. Ground the analyzer to the rover's ground.

### Localization sensors

- **u-blox NEO-M9N GPS** — absolute position, **outdoors only**. It will not
  get a fix indoors; this is physics, not tuning, and it is why the mission
  splits into an indoor and an outdoor course (`docs/PLAN.md` §2).
- **IMU** — angular rate and acceleration. **Use a raw-output part**
  (MPU-6050 / MPU-9250 / ICM-20948), *not* a BNO055. A BNO055 fuses onboard and
  hands over a finished quaternion, which removes exactly the thing worth
  learning here.
- **Magnetometer** — absolute heading, and a genuine problem: mounted near four
  brushed motors and their current-carrying leads it reads badly, and
  hard/soft-iron calibration cannot fix it, because the error moves with motor
  current. Measure it, then solve it with distance (a mast) and lead routing.
- **Wheel encoders** — the backbone of both the PID loop and odometry.
  Calibrate ticks/metre by pushing the rover a measured distance; do not trust
  the datasheet.

These are cheap sensors with large, honest, **measurable** error. That is the
point: characterising the error and watching each fusion stage shrink it is the
lesson. Good sensors would just work, and teach nothing.

## Goals

1. **PID speed control** — closed-loop wheel speed from encoder feedback,
   per wheel. Concepts in play: loop rate, units (ticks/s -> rad/s),
   integral windup, derivative noise, feedforward.
2. **Skid steering** — map (linear velocity, angular velocity) to left/right
   wheel speed targets.
3. **Obstacle detection by depth height** — project depth pixels into the
   robot frame and classify by **height above the ground plane**, not by raw
   distance. Naive distance thresholding flags the floor as an obstacle;
   height-based classification separates drivable ground, real obstacles,
   and passable overheads.
4. **State estimation, built as a ladder** — wheel odometry, then + gyro
   heading, then + magnetometer, then + GPS. Each rung measured on the *same*
   closed course, so each is provably better than the last. Do not start by
   writing a 9-state EKF. Concepts in play: process vs measurement noise,
   observability, dead reckoning drift, coordinate frames.
5. **Autonomous navigation** — an indoor course (depth-based obstacle
   avoidance) and an outdoor course (GPS waypoint following).

**Explicitly out of scope: SLAM, RTK, and ROS 2.** Each would trade depth for
breadth, and depth is the goal. See `docs/PLAN.md` §1.

## Development environment

Two machines, both **Ubuntu 24.04**, both running Claude Code.

- **Laptop** — daily driver and firmware build host.
- **Raspberry Pi 5** (`baby-rover`) — rides on the rover, runs vision and
  navigation. Full system audit in `BABY-ROVER.md`; **read it before changing
  anything on that box.**

Things about the Pi that will bite if forgotten:

- Its **rootfs is a disposable USB flash drive**. Slow, no wear levelling, and
  it will fail eventually. **Push to GitHub daily.** Never keep the only copy
  of anything here.
- **No swap**, 7.8 GB RAM. Build large C++ projects with `-j2`, not `-j4`.
- `wlan0` starts **down** — a rover on a chassis cannot stay on ethernet.
- **No `gcc`, `make`, or `pip3`** until Day 0 installs them.
- Claude Code runs in **bypassPermissions** — no approval prompts, ever.
- Timezone is **UTC**, not Sydney. Keep one dating convention in `memory/`.

**Toolchain: pico-sdk (C/CMake)** is the recommendation, decided on Day 0 and
recorded in `memory/pico-toolchain.md`. MicroPython was the *bring-up* tool —
the REPL is the right instrument for "is my wiring correct?" — but a garbage
collector cannot promise a jitter-free 100 Hz control loop, and PIO quadrature
decoding is the forcing question.

**Tailscale** on both machines, so the rover is reachable without hunting for
an IP on a strange network. **GitHub public repo** — a private repo proves
nothing to anyone.

Setup runbook: `docs/setup.md`.

## Repo layout

```
  firmware/      Pico 2 W firmware (motor control, PID, PIO encoders, UART)
  pi/            Python: RealSense, EKF, obstacle detection, navigation
  docs/          plan, setup, hardware, architecture, decisions and rationale
  tools/         host-side scripts (serial monitor, logic analyzer helpers)
  experiments/   raw data, plots, and REGISTRY.md — one row per experiment
  memory/        the memory wiki — read MEMORY.md first
```

## The record — a first-class deliverable

The engineering record is worth as much as the rover, and it is built **daily**.
By week three the reasoning behind a decision is gone, and a report written from
a cold repo reads like one.

- Every experiment gets a row in `experiments/REGISTRY.md` **when it is run.**
  A result not persisted did not happen.
- Photograph the bench. **Film the failures too** — a rover driving into a box
  because the ground-plane fit was wrong is a better story than one that never
  failed, and interviewers know it.
- **The owner writes the report prose. This is a hard rule.** Claude Code
  assembles the raw material — registry, plots, git log, memory pages — and
  critiques drafts hard. It does not ghostwrite. A report he did not write is
  one he cannot defend when an interviewer asks a follow-up, and defending it
  is the entire point of having it.

Full pipeline: `docs/career-track.md`.

## Conventions

- Units are SI and stated in variable names or comments when ambiguous
  (`speed_mps`, `omega_rad_s`, `ticks_per_rev`). Mixed units are a leading
  cause of control bugs.
- Never commit secrets (WiFi creds, hostnames). Commit a `*.example` instead.
- Record *why* a decision was made in `docs/`, not just what was decided.

---

# Operating modes (always on)

## Ponytail (`/ponytail full`)

Laziest solution that works. YAGNI. Shortest working diff.

## BMAD — an open decision, not an installed tool

Planning via `bmad-agent-pm` (John); build via `create-story` → `dev-story`.
PRD → epics/stories → **one story per fresh conversation**.

⚠️ **BMAD is not installed and has never been authorised.** Until it is, the
epics and stories in `docs/PLAN.md` serve the same role, and story state is
tracked there. For a three-week solo build, that is very likely enough — decide
on Day 0 and record the reasoning. What is **not** optional is the part BMAD was
wanted for: **one story per fresh conversation.**

## Memory (in-repo, local — never external)

`./memory/` is a small wiki, not just a log. **Two layers:**

- **Log layer:** one markdown file per conversation
  (`YYYY-MM-DD-<slug>.md`: decisions, learnings, open threads — skip what
  git/code already records). Append-only history, never rewritten.
- **Wiki layer:** undated topic pages (`<topic-slug>.md`) for anything that
  recurs across sessions — an entity, an evolving decision, a subsystem's
  quirks. When a session learns something about an existing topic, **REVISE
  that page in place** (the day's log entry records that it happened).
  Knowledge compounds by revision — one fact smeared across five dated files
  is rot, not memory. Cross-link pages with `[[wikilinks]]`.

`memory/MEMORY.md` is the index, one line per file, both layers. **Read
MEMORY.md at every session start.** Update the matching files each session;
delete memories that turn out wrong.

**Write-back rule:** if an answer took real digging this session, file it as a
memory page even when it isn't a gotcha — a synthesis not persisted did not
happen.

A page a human hand-edited carries `reviewed: true` frontmatter: revise around
it, never overwrite it wholesale.

## Memory/skill lint (the third verb, periodic)

Ingest and query alone let any knowledge base rot — lint is what keeps it
honest. Every few sessions (or whenever `MEMORY.md` stops fitting on one
screen), run a lint pass over `memory/` and `.claude/skills/`: contradictions
between pages, stale claims, duplicates, orphan pages nothing links to, dead
`[[links]]`, skills that no longer match reality. Merge, revise, or delete.
**Reactive deletion alone is not maintenance.**

## Skills (continual internal development)

Reusable skills live in `.claude/skills/<name>/SKILL.md`. Before solving
anything, check for an existing skill and reuse it. When we learn something
reusable (a gotcha, pattern, convention), harvest it into a skill or memory
file — never leave it only in chat. **Never write speculative skills; only
hard-won ones.**

## Architecture mapping (continual)

Maintain `docs/ARCHITECTURE.md` — a living map of key components, data flows,
module boundaries, and their invariants. Update it as part of finishing any
story that adds or changes structure. It is the map to read to orient before
touching unfamiliar code.

## Task management

Track story state in the BMAD sprint files; keep a `NEXT-STEPS.md` handoff so a
fresh session knows the current state, blockers, and open threads. **Assume the
next session knows nothing not written down.**

## Definition of done — self-review gate

Before calling any non-trivial code done, run an adversarial pass yourself:

1. Trust boundaries / malformed input
2. Entity-set completeness vs ground truth
3. Verification honesty (no assumed-as-verified labels)
4. Regression risk (run the FULL suite)
5. Output integrity
6. Failure modes

External review should find nits, not criticals.

## Testing discipline

Unit + a growing e2e suite + a regression test per bug fixed, in every change.
Run the full suite before declaring done.

## CLOSE ritual — branch/story shutdown

**Nothing is "done" until ALL of these.** A story or branch is not closeable on
partial progress. Before declaring done and handing off:

1. Self-review gate passed and the FULL test suite is green **and recorded**
   (paste/summarize the run — a suite you didn't run isn't green).
2. Status annotated in the epics/story file: DONE or partial, stating
   explicitly what changed vs. the acceptance criteria (not "done" as a bare
   word).
3. `ARCHITECTURE.md` updated to match reality — any new component, data flow,
   or invariant this branch added or moved is on the map, or the map is stale
   by definition.
4. `NEXT-STEPS.md` rewritten for the next session: current state, what the next
   story needs to know, and every open thread. Assume the next session knows
   nothing not written down.
5. Memory ritual done: the day's `memory/YYYY-MM-DD-<slug>.md` + its
   `MEMORY.md` index line; any wiki-layer topic pages this session touched
   revised in place; hard-won gotchas harvested into skills (never speculative
   ones).
6. Memory lint (light): do today's learnings contradict, duplicate, or
   stale-date anything already in `MEMORY.md` or a skill? Reconcile now —
   revise or delete the old page, never add a new dated file beside a wrong one.
7. Any experiments/results have their registry rows written — **a result not
   persisted did not happen.**
8. Commit and push — and only then. The push carries the doc/memory updates in
   the same commit, never as an afterthought.

**Skipping any item means the branch stays open.** External review after close
should surface nits, not gaps left in the handoff.

## Pre-push ritual

Unchanged — enforced by the CLOSE ritual above; the two are the same gate.
