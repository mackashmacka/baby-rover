# NEXT-STEPS

Session state and handoff. **Assume you know nothing that is not written down
here.**

**Last updated:** 2026-08-31 (UTC) — end of the agent-machinery session.
**Current story:** Day 0 — setup and its seven acceptance criteria (`docs/PLAN.md` §4, runbook `docs/setup.md`).
**Session mode:** Ponytail full — one story per fresh conversation.

**Read first:** [`CLAUDE.md`](CLAUDE.md) → [`memory/MEMORY.md`](memory/MEMORY.md)
→ this file → [`docs/PLAN.md`](docs/PLAN.md) §5–7. Or just run:

```bash
python3 tools/session.py start
```

which prints the same orientation from live data — the memory index, the state
below, the open blockers, and any experiment with no data file.

**Hardware facts:** [`docs/HARDWARE.md`](docs/HARDWARE.md) is ground truth —
part numbers, ratings, derived constants. [`docs/WIRING.md`](docs/WIRING.md) is
the living connection record. Keep them separate; never copy a rating from the
first into the second.

---

## Current state

**The laptop is canonical.** The repo lives at `/home/oliver/baby-rover`, it is
a git repo, and every doc was migrated here from the Pi. **The Pi's copy is now
a backup, not the original.** Do not edit docs on the Pi.

**All the hardware is on the laptop.** The Pico 2 W, the FTDI FT232R and the
Saleae-clone analyser are plugged into this machine. **The Pi has no hardware
attached at all.** The bench, the firmware build and all analysis run here. The
Pi's role does not begin until Story 2.6 (RealSense) and Story 3.1
(navigation) — until then it is a spare Linux box.

**The Pico still has MicroPython 1.29 on it** and enumerates as
`2e8a:0005 MicroPython Board in FS mode`. That was the bring-up tool. Nothing
persists on it; every behaviour so far was typed at the REPL.

**Built so far:** Stage 1 only — one N20 spinning forward and reverse through
one TB6612FNG at 20 kHz PWM. No encoders, no closed loop, no firmware file, no
Pi↔Pico link, no vision, no sensors.

**Built this session (software, not rover):** the agent machinery
(`tools/session.py`, three skills, `docs/INDEX.yaml`), a host-side test harness
(`Makefile`, `pytest.ini`, `.coveragerc`, `tests/`), and the beginnings of the
bench driver (`tools/rover_bench/`, `tools/analysis/`).

---

## ⚠️ Verify the environment before trusting the next section

The environment changed *during* this session, so two sets of facts exist and
they disagree. What follows is what was **observed on this laptop at 13:27 UTC
on 2026-08-31**, with the command that proved it. Re-run these before relying
on them; they are cheap.

| Fact | How it was checked | Result |
|---|---|---|
| Toolchain installed | `command -v gcc make cmake pip3 sigrok-cli arm-none-eabi-gcc` | all present |
| Group membership | `id -nG oliver` | includes `dialout` **and** `plugdev` |
| Analyser enumerates | `sigrok-cli --scan` | `fx2lafw:conn=1.30 — Saleae Logic … 8 channels` |
| Pico attached | `lsusb` | `2e8a:0005 MicroPython Board in FS mode` |
| FT232R attached | `lsusb`, `sigrok-cli --scan` | `0403:6001`, S/N `A5069RR4` |
| Pi reachable | `ping -c1 10.0.0.5` | responds |
| Git remote | `git remote -v` | **nothing. There is no remote.** |

Earlier in the same session the laptop had none of the toolchain and `oliver`
was in neither group; `tools/bench-setup.sh` was written to fix exactly that
and has evidently been run. **If any row above comes back different, believe
the command, not this table**, and run `sudo bash tools/bench-setup.sh`.

---

## Day 0 acceptance — do not start Week 1 until all seven pass

- [ ] `git push` succeeds to a **public** GitHub repo — **not started, no remote**
- [ ] `ssh baby-rover` works from the laptop over **Tailscale** — blocked, see #9
- [x] `ip a show wlan0` shows an address and survives a reboot — up at `10.0.0.5`
- [ ] `sigrok-cli` has captured a real square wave you can read — **see #7**
- [ ] A blink binary built from source and flashed to the Pico
- [ ] `pytest` runs green on an empty-but-real suite — harness exists, suite red
- [ ] The firmware toolchain decision **written down with its reasoning** — the
      reasoning is written (`memory/pico-toolchain.md`); the *decision* is not
      signed off, see #8

---

## Blockers, in the order they block things

Numbers are stable labels, not positions. A closed blocker keeps its number and
moves to the closed list rather than being renumbered.

### 1. Encoder counts per revolution unresolved 🔴
Sources disagree — Adafruit says 14 counts/rev, retailer listings say 11 —
giving 1100–1400 counts per **output** revolution, possibly ×2 or ×4 depending
on the edge decoding. **Measure it** (Story 1.4) by hand-turning the output
shaft exactly one revolution and counting edges. Never adopt a datasheet figure.
Everything downstream — PID, odometry, the whole fusion ladder — scales directly
with this number.

### 2. Bench supply — and the old plan was wrong 🟠
The 9 V 6F22 is carbon-zinc, ~10 Ω internal, and cannot hold 4.5 V under load.
`VM` currently comes from Pico VBUS as a stopgap, which violates the
logic/power separation invariant and is fine for smoke tests and **not** for
characterisation data. **The previously-planned 6×AA pack is the wrong part**
now the motor is known to be 6 V — 9 V is 150% of rated. Use a **lab PSU at
6.0 V with current limiting**, or 4× alkaline. See
[`memory/power-supply.md`](memory/power-supply.md).

### 3. Three constants to measure, not look up 🟡
Not blockers, just work: **wheel diameter**, **track width** (left-to-right
wheel centres), and **encoder counts/rev**. A ruler and a hand-turned shaft.
`metres per count` needs all three, and nothing downstream works without it.
Re-measure the wheels if they get reprinted in Story 3.3.

### 5. No remote — nothing is backed up anywhere 🔴
The repo is a real git repo now, but `git remote -v` is empty and nothing has
ever been pushed. **The laptop holds the only copy**, and the Pi's copy is a
stale pre-migration snapshot on a disposable USB stick. This is the first Day 0
item and `tools/session.py close` will keep failing item 8 until it is done.
The repo is meant to be **public** — a private repo proves nothing to anyone.

### 6. BMAD referenced but not installed 🟡
`CLAUDE.md` names it; it has never been installed or authorised.
`docs/PLAN.md` carries the epics and stories in the meantime, which for a
three-week solo build is probably enough. **Decide, and record why.** What is
not optional is the part BMAD was wanted for: one story per fresh conversation.

### 7. The logic analyser has still never captured anything 🔴
It now *enumerates* (`sigrok-cli --scan` finds `fx2lafw`), which closes the
udev/permissions half of the problem. **No capture from the real device exists
in this repo.** The one file under `experiments/bench-verify/` was produced by
libsigrok's session-emulating driver, not by the hardware — its own header says
so. **Prove the instrument on a known square wave before trusting it on a
signal you do not understand.** An analyser you have never successfully
triggered is not a working instrument, and you find that out at the worst
possible moment. This is a Day 0 acceptance item and it gates Story 1.3.

### 8. Firmware toolchain: recommended, not signed off 🟠
**Recommendation: pico-sdk (C/CMake).** The reasoning is written up in
[`memory/pico-toolchain.md`](memory/pico-toolchain.md): PIO quadrature decoding
and a jitter-free 100 Hz loop are the forcing questions, and a garbage
collector cannot promise loop determinism. `arm-none-eabi-gcc` and `cmake` are
installed. **It awaits the owner's explicit sign-off** — this is a learning
project, and the toolchain is his call to make, not one to inherit silently.
Nothing blocks it except saying yes.

### 9. Tailscale on the Pi is installed but logged out 🟡
`ssh baby-rover` over Tailscale is a Day 0 acceptance item and will not work
until someone runs `tailscale up` on the Pi and authenticates. The Pi is
reachable at **10.0.0.5** on the local network in the meantime, which is enough
for everything before Story 2.6 — so this is a real blocker with a low priority,
not an urgent one.

### ✅ Closed — motor identity confirmed
The motors are **N20s, 6 V, 1:100** — not the `CHF-GM12-N20VA-50-12V`
12 V / 50:1 part earlier docs guessed at. Testing at 5 V is 83% of rated, so
characterisation is no longer blocked on this. The IMU (**MPU-6050**) and
magnetometer (**Jaycar XC4496**) are chosen; the **NEO-M9N** is deliberately
deferred to Week 2.

### ✅ Closed — 4. `wlan0` is down on the Pi
`wlan0` is **up at 10.0.0.5**. Confirm it survives a reboot before ticking the
Day 0 box; a rover on wheels cannot stay on a cable.

### ✅ Closed — no git repo
`/home/oliver/baby-rover` is a git repo and the docs are migrated. The *remote*
half of the old blocker is still open and is now #5.

### ✅ Closed — laptop toolchain not installed
`gcc`, `make`, `cmake`, `pip3`, `sigrok-cli` and `arm-none-eabi-gcc` are all
present, and `oliver` is in `dialout` and `plugdev`. Re-verify with the table
above before believing it; `tools/bench-setup.sh` is idempotent and re-runnable.

---

## What the next session needs to know

- **One story per fresh conversation.** Stories are in `docs/PLAN.md` §5–7.
- **Run `python3 tools/session.py start` first** and
  `python3 tools/session.py close` before declaring anything done. `close`
  exits non-zero if the ritual is incomplete, and non-zero means the story is
  still open. The `close-ritual` skill explains every item.
- **Confirm the motor pair by resistance before wiring anything** — a few ohms
  red-to-white, open black-to-blue. An evening has already been lost to encoder
  wires sitting in the H-bridge outputs while every logic test point read
  correctly and the motor sat silent.
- **Encoder halls run on 3.3 V, never 5 V.** RP2350 GPIO is not 5 V tolerant.
- **Never probe `AO1`/`AO2`/`BO1`/`BO2` with the analyser** — motor voltage
  destroys it.
- **Grounds common**, always: Pico, driver, supply, analyser.
- **The 4S LiPo (16.8 V) must never reach the TB6612 directly** — 13.5 V max.
- **The analyser's maximum sample rate is not documented in this repo.**
  Discover it at runtime from `sigrok-cli --scan` / `--show` and record what it
  said in the run manifest. Do not hard-code a number.
- **`sigrok-cli` only. PulseView is for human eyes** and must never appear in
  an automated path.
- **GPS does not work indoors.** The mission splits into an indoor and an
  outdoor course; `docs/PLAN.md` §2. The single most important structural
  decision in the plan.
- **The failsafe is not implemented**, and `CLAUDE.md` calls it non-negotiable.
  Nothing drives untethered until it exists (Story 2.1).
- **The record is built daily, not in week three.** A registry row per
  experiment *when it runs*; a memory entry per session.
- **The owner writes the report prose.** Claude assembles raw material and
  critiques drafts hard. It does not ghostwrite. Hard rule.

---

## Open threads

- Unexplained **0.89 V** on a 50%-duty 3.3 V PWM line where ~1.65 V was
  expected. Probably the meter failing to average a 20 kHz square wave —
  a hypothesis, not a finding, and now a job for the analyser.
- **The host suite is green as of 2026-08-31 14:31 UTC.** `tests/` reports
  **1125 passed, 13 deselected** (the 13 are `-m hardware`), coverage of
  `tools/rover_bench` **89.1 %** against the 80 % gate, and `ruff --select
  E4,E9,F` clean. Earlier in the session it was 150 failed / 6 passed / 199
  xfailed — the tests were written ahead of the code; the code has since landed
  and every conditional `xfail` is now inactive. Two things this does NOT cover:
  `tools/analysis/tests` (238 more tests) is outside `pytest.ini`'s `testpaths`
  and outside `.coveragerc`'s `source`, so `make test` never runs it and it
  needs `pandas`/`matplotlib`, which `tests/requirements.txt` does not list; and
  the ~424 firmware tests compile `firmware/src/*.c` with the host `cc` and
  **skip silently** if there is no compiler. Re-run `make test` before believing
  any of these numbers.
- **`tools/session.py` has no tests of its own.** Its parsers (`parse_registry`,
  `parse_memory_index`, `parse_index_yaml`, `parse_open_blockers`,
  `is_reviewed`) are pure functions written to be testable and are not yet
  covered by `tests/`. It is also outside `.coveragerc`'s `source`, so it does
  not currently affect the 80% gate. Decide deliberately: cover it, or say in
  `.coveragerc` why it is excluded.
- `docs/wiring.html` and `docs/harness.html` are referenced by
  `docs/project-state.md` but were never carried over from the Pi. Recreate
  them or drop the references.
- The **FTDI FT232RL** can stand in for the Pi to build and test the UART
  protocol and the failsafe before the Pi is involved. It is plugged in and
  sigrok sees it (S/N `A5069RR4`). **Set it to 3.3 V first.**
- `experiments/bench-verify/verify-20khz.csv` is synthetic — generated by
  libsigrok's session-emulating driver. Either replace it with a real capture
  when the analyser is proven, or rename it so nobody mistakes it for
  measurement. It currently has no registry row, which `session.py close`
  flags.
- The session-state and journal machinery deliberately reuses `NEXT-STEPS.md`
  and `memory/` rather than adding `SESSION-STATE.md` and `JOURNAL.md`. If the
  owner wanted those as separate files, say so — but two competing systems is
  the failure mode the memory rules exist to prevent.
- `.claude/skills/rover-bench/SKILL.md` is marked **provisional**. It is
  reasoning from the hardware documents, not from bench experience. The first
  complete Story 1.5 run confirms or corrects it; revise it in place then.
