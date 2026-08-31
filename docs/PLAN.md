# PLAN — the three weeks

Master roadmap. **Read this after `CLAUDE.md` and before starting any story.**

Written 2026-08-31 for a ~3-week holiday build. Days are numbered, not dated —
fill in real dates on Day 0.

---

## 1. What "done" means

**One rover. Done properly.** Not four half-features.

The deliverable is two things, and the second is worth as much as the first:

1. **A rover** that drives under closed-loop control, knows where it is, and
   avoids obstacles without being told where they are.
2. **The engineering record** that proves it — characterisation data,
   calibration measurements, plots, debugging narratives, and a written report.

A rover that works but has no record is half the project. A record with no
rover is none of it. The record is built *daily*, not assembled in week three —
by week three the memory of why anything was done is gone.

### Explicitly out of scope

- **SLAM.** Tempting, and a trap. A half-working SLAM demo is a weaker artifact
  than a fully-characterised EKF with error plots. Say no now.
- **RTK.** Deliberately dropped. Cheap sensors with honest error analysis teach
  more than good sensors that just work.
- **ROS 2.** Adds a framework to learn on top of the fundamentals. The point is
  the fundamentals. Write the loop.

---

## 2. The single most important structural decision: two venues

**A u-blox M9N does not get a fix indoors.** No sky view, no satellites, no
position. This is not a tuning problem.

So the mission splits in two, sharing one firmware and control stack:

| | **Indoor course** | **Outdoor course** |
|---|---|---|
| Venue | a room, boxes on the floor | grass or carpark, cones or witches hats |
| Localisation | encoders + IMU (+ visual) | encoders + IMU + mag + **GPS** |
| Perception | **D415** depth, obstacle avoidance | GPS waypoints, no camera needed |
| Proves | perception → local planning | sensor fusion → global navigation |

Do not try to make one course prove both. Two courses, two datasets, two
sections of the report.

---

## 3. Spine vs stretch

Three weeks is tight. Protect the spine; the stretch is genuinely optional.

### Spine — if this is all that happens, the project succeeded

- Every motor rewired, verified, and **characterised with the logic analyser**
- Closed-loop PID wheel speed control, tuned, with step-response plots
- Pi↔Pico UART protocol **with the failsafe timeout implemented**
- Encoder odometry + gyro heading fusion, error measured on a closed course
- D415 obstacle detection, indoor course completed autonomously
- Daily record, experiment registry, report, LinkedIn post, CV, outreach sent

### Stretch — take in this order if ahead

1. GPS + magnetometer EKF, outdoor waypoint following
2. Magnetometer interference study (motors vs heading — see §6.2)
3. CAD + printed sensor mounts
4. Visual odometry from the D415

**If you are behind, cut from the bottom of the stretch list. Never cut the
record.**

---

## 4. Phase 0 — setup (Day 0, budget a full day)

There is a lot of it, and it all has to happen before anything interesting can.
Full runbook in [`setup.md`](setup.md). Headlines:

- **GitHub account + repo + SSH key.** Nothing is backed up until this exists,
  and this Pi's rootfs is a **disposable USB stick** — treat it as if it will
  die, because it might.
- **Tailscale** on the Pi and the laptop. This is what lets a rover on wheels
  be reachable without hunting for its IP.
- **wlan0 is DOWN on the Pi.** A rover on a wheeled chassis cannot stay on
  ethernet. Configuring WiFi is a Day 0 blocker, not a detail.
- **No `gcc`, no `make`, no `pip3` on the Pi.** Every build step fails until
  `build-essential` and `python3-pip` are installed.
- **udev rules for the logic analyser** — on Linux this replaces the Zadig
  step that blocked the whole thing on Windows. This unblocks all measurement.
- Firmware toolchain **decision** (see [`../memory/pico-toolchain.md`](../memory/pico-toolchain.md)).

**Day 0 acceptance:** a commit pushed to GitHub from the Pi over Tailscale,
`sigrok-cli` capturing a real square wave, and `claude` running on both machines.

---

## 5. Week 1 — electrical, characterisation, control

The week that makes everything after it trustworthy. Nothing here involves
autonomy; all of it involves measurement.

### Story 1.1 — Power that doesn't sag
The 9 V 6F22 is **unusable** (~10 Ω internal, browns out the driver). See
[`../memory/power-supply.md`](../memory/power-supply.md).
- 🔴 **The motor is 6 V (4.5–6 V), so the old 6×AA plan is the wrong part** —
  9 V is 150% of rated. Use a **lab PSU at 6.0 V with current limiting**, or
  4× alkaline / 5× NiMH. See [`HARDWARE.md`](HARDWARE.md) §6.1
- Bench supply standing up under a stalled motor
- 4S LiPo → D24V90F5 chain wired and measured
- **AC:** a plot of terminal voltage vs load current for each supply. A supply
  is judged by sag, not open-circuit volts.

### Story 1.2 — Rewire all four motors
- **Confirm the motor pair by resistance (a few ohms) before wiring anything.**
  An evening was already lost to encoder wires in the H-bridge outputs.
- Both TB6612s, four channels, common ground, motor current isolated from Pico
- **AC:** all four motors spin both directions; a continuity/resistance table
  for every one of the 24 wires committed to the repo

### Story 1.3 — Logic analyser on everything
This is the "full-on intense analysis" pass. Do it properly once.
- Capture PWM duty and frequency on all four channels
- Capture direction and STBY transitions, verify against the TB6612 truth table
- Capture the deadtime around direction reversal
- **Never probe `AO1`/`AO2`** — those sit at motor voltage and will kill the
  analyser
- **AC:** saved `.sr` captures + annotated screenshots in `experiments/`, one
  per channel

### Story 1.4 — Encoders and quadrature
- Eight channels on GP12–GP19, hall power on **3.3 V, never 5 V**
- PIO quadrature decoding (per-edge GPIO interrupts would starve the loop)
- **Verify A/B phasing on the analyser** — P3 is B and P4 is A, not alphabetical
- **Calibrate ticks/rev by turning the output shaft exactly one revolution by
  hand and counting edges.** Do not trust the datasheet — the sources genuinely
  **disagree** (14 counts/rev per Adafruit vs 11 per retailer listings), giving
  anywhere from 1100 to 1400 counts per output revolution. Then confirm
  metres/count by pushing the rover a measured distance
- **AC:** a quadrature capture showing correct phase in both directions, and a
  measured ticks-per-metre with an error bar

### Story 1.5 — Per-motor characterisation ⭐
The centrepiece of week 1. **One identical, repeatable experiment, run on all
four motors, producing comparable data.**
- Deadband (duty at which the shaft first turns)
- Duty → steady-state rad/s curve, both directions
- No-load current, stall current. The supplier claims **~100 mA / ~200 mA**,
  which is suspiciously low for an N20 and feeds the driver-headroom sum —
  **measure it and re-tag it**
- ✅ **Rated voltage is settled: 6 V (4.5–6 V), 1:100.** Testing at 5 V is 83%
  of rated, so this no longer blocks the experiment — it did when the motor was
  wrongly believed to be 12 V.
- **AC:** one CSV per motor, one overlay plot of all four, and a paragraph on
  how much they differ. Four "identical" motors never are — quantifying that is
  the whole point, and it is what motivates closed-loop control.

### Story 1.6 — PID speed control
- Per-wheel closed loop at 100 Hz on the Pico
- Concepts to name in the write-up: loop rate, ticks/s → rad/s, integral
  windup, derivative noise, feedforward from the Story 1.5 curve
- **AC:** step-response plots before and after tuning; the four motors now hold
  the same commanded speed despite the differences measured in 1.5

---

## 6. Week 2 — sensing and state estimation

### Story 2.1 — Pi↔Pico protocol and the failsafe 🔴
**Do this before anything drives untethered.**
- Framed, checksummed messages at 115200 on GP0/GP1
- **Failsafe: motors stop if no valid command arrives in ~200–500 ms.**
  Non-negotiable — a rover that keeps driving when the Pi dies is a hazard
- Build and test it against the **FTDI adapter first** (set to 3.3 V), which
  stands in for the Pi
- **AC:** a test that pulls the cable mid-drive and shows the motors stopping,
  on video, with the timeout measured on the analyser

### Story 2.2 — IMU bring-up
- **Choose a raw-output IMU, not a BNO055.** A BNO055 fuses onboard and hands
  you a finished quaternion — which removes exactly the thing worth learning.
  MPU-6050 / MPU-9250 / ICM-20948 give raw rates and accelerations.
- Gyro bias calibration (average at rest), accelerometer levelling
- **AC:** an Allan-variance-style plot, or at minimum a measured gyro drift
  rate in deg/min. Integrate a stationary gyro for 10 minutes and show the
  heading walking away — this is the motivation for every correction step later.

### Story 2.3 — Magnetometer and the interference problem
- Hard-iron and soft-iron calibration (the figure-eight, then an ellipsoid fit)
- **Then the interesting part:** a compass mounted near four brushed motors and
  their current-carrying leads reads badly, and calibration cannot fix it,
  because the error moves with motor current.
- **AC:** heading error plotted against motor duty cycle. Then the fix —
  distance (a mast), lead routing (twisted pairs), or rejecting mag updates
  when current is high. **This single experiment is one of the best things in
  the whole report.** Cheap sensors, honest error analysis.

### Story 2.4 — GPS bring-up
- M9N over UART, NMEA or UBX parsing, fix quality and HDOP logged
- **Outdoors only.** Log a stationary fix for 20 minutes and plot the scatter —
  that scatter *is* the measurement noise the EKF needs to know about
- **AC:** a static-scatter plot with a CEP figure, and a walked-path track

### Story 2.5 — Sensor fusion, built as a ladder ⭐
**Do not start with a 9-state EKF.** Climb the rungs, and measure the same
closed-loop test course at every rung:

| Rung | Estimator | What it fixes | Measured on |
|---|---|---|---|
| 0 | Wheel odometry alone | — | closure error driving a square |
| 1 | + gyro heading | skid-steer scrub, which makes the wheels *lie* in turns | same square |
| 2 | + magnetometer | gyro heading drift | a long slow loop |
| 3 | + GPS (outdoor) | unbounded position drift | outdoor course |

- **AC:** one plot, four tracks, same course. Each rung visibly better than the
  last, with a closure-error number for each.

**Why this ordering is the right teaching structure:** skid steer makes wheel
odometry *provably* wrong in turns — the wheels scrub sideways. He can measure
exactly how wrong, then watch each added sensor close the gap. That narrative
is worth more in an interview than a working filter he can't justify.

### Story 2.6 — D415 and obstacle detection
- librealsense on the Pi (**build with `-j2`** — 7.8 GB RAM and **no swap**)
- Ground-plane fit, then classify by **height above the ground plane, not raw
  distance**. Naive distance thresholding flags the floor as an obstacle.
- **Respect the blind zone: it cannot see closer than ~0.45 m.** Stopping
  distance must account for that, and the FOV is ~65°×40° — blind to the sides.
- **AC:** a live occupancy grid, plus a measured minimum stopping distance that
  is provably larger than the blind zone

---

## 7. Week 3 — navigation, integration, and the record

### Story 3.1 — Indoor autonomous run (spine)
Skid-steer mixer, local planner, obstacle avoidance. **AC:** three consecutive
successful crossings of a room with boxes, on video, with logs.

### Story 3.2 — Outdoor waypoint run (stretch)
GPS waypoints, EKF pose, heading hold. **AC:** a plotted commanded-vs-actual
track with cross-track error.

### Story 3.3 — CAD and printed mounts (stretch)
The natural CAD task, because he needs the parts anyway: a camera bracket, a
magnetometer mast (which is also the fix from Story 2.3), and sensor trays.
**AC:** STLs + source CAD in the repo, printed and fitted.

### Story 3.4 — The report ⭐
**He writes it. This is a hard rule.**
Claude Code's job is to assemble the raw material — pull the experiment
registry, the plots, the memory log, the git history — and to critique drafts
hard. Claude Code does **not** write the prose. A report he did not write is
one he cannot defend in an interview, and defending it is the entire point.

Structure that falls straight out of the work: problem → hardware →
characterisation → control → sensing → fusion ladder → navigation → what broke
and how it was found → what he'd do differently.

### Story 3.5 — LinkedIn, CV, outreach
See [`career-track.md`](career-track.md).

---

## 8. The continuous thread: build the agent, not just the rover

The operating-modes document in [`agent-bootstrap.md`](agent-bootstrap.md) is
not scaffolding to tolerate — it is a **second thing being built**, and it is a
genuinely distinctive thing to have on a CV in 2026.

Every session: memory log entry, wiki pages revised in place, gotchas harvested
into `.claude/skills/`. Every few sessions: a **lint pass** — contradictions,
stale claims, dead `[[links]]`, orphan pages. Ingest and query alone let a
knowledge base rot; lint is what keeps it honest.

By week three he should be able to say, truthfully: *"I built a rover, and I
built the agent workflow that built it, and here is the commit history of
both."* Very few graduates can say that.

---

## 9. The daily loop

1. Read `memory/MEMORY.md`, then `NEXT-STEPS.md`
2. Pick **one** story. One story per fresh conversation
3. Build it, narrating hypothesis → test → result. **Diagnose out loud; don't
   silently fix** — the debugging method is more valuable than the fix
4. Run the **CLOSE ritual** (`CLAUDE.md` §CLOSE) before calling anything done
5. Commit and push. The doc and memory updates ride in the *same* commit

---

## 10. Test harness — what "the suite" means

The CLOSE ritual demands a green full suite. Nothing exists yet, so define it
on Day 0 or the ritual is theatre:

- **Host-side `pytest`** for everything that is a pure function — the EKF
  update step, the skid-steer mixer, the protocol parser, coordinate
  transforms, the ground-plane fit. These are the bug-prone parts and they need
  no hardware.
- **Hardware smoke test** — a script that drives each motor briefly, reads each
  encoder, and pings each sensor. Run it before every session.
- **A regression test for every bug fixed.** Every one.

---

## 11. Risks, named honestly

| Risk | Why it bites | Mitigation |
|---|---|---|
| **Three weeks is not much time** | Every item here is a day's work for someone who already knows it, and he doesn't yet — that's the point | The spine/stretch split in §3. Cut from the bottom |
| **Pi rootfs is a disposable USB stick** | ~20 MB/s, no wear levelling, no swap. It *will* fail eventually | Push to GitHub daily. Never keep the only copy here |
| **Unattended-upgrades can reboot the Pi** | Mid-characterisation-run, unprompted | Disable it, or never leave a long run unattended |
| **Encoder counts/rev unknown** | Sources disagree (11 vs 14 per motor rev). PID, odometry and the EKF all scale directly with it | Measure it in Story 1.4 by hand-turning the shaft. Do not adopt a datasheet figure |
| **Wheel diameter and track width undocumented** | `metres per count` is unobtainable, so odometry and kinematics stall | Measure both with a ruler on the day the encoders first spin. Re-measure if the wheels are reprinted |
| **Magnetometer near motors** | Heading garbage that calibration can't fix | Turn it into Story 2.3 — measure it, then mount it on a mast |
| **GPS indoors** | Simply will not work | The two-venue split in §2 |
| **Scope creep into SLAM** | Eats the record, delivers a demo | Named out-of-scope in §1. Hold the line |
| **The record slips to week 3** | By then the reasoning is forgotten and the report gets thin | Daily memory entries are part of the CLOSE ritual, not optional |
