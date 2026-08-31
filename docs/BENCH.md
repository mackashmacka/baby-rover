# BENCH — the motor characterisation bench

**This is the setup manual.** It tells you how to build the bench, how to prove
the instrument before you trust it, how to move to the next motor without
invalidating the campaign, and what to do when a reading makes no sense.

**Read it end-to-end the first time.** After that, §4 (hookup), §6 (verify) and
§7 (swap) are checklists you re-run every session.

**Related:** specs in [`HARDWARE.md`](HARDWARE.md) · connections in
[`WIRING.md`](WIRING.md) §10 · the experiments themselves in
[`experiments/`](experiments/README.md) · the debugging rule in
[`../memory/debugging-method.md`](../memory/debugging-method.md).

Last updated **2026-08-31** (UTC).

---

## 1. What this bench is

One motor, one H-bridge channel, one Pico, one logic analyser, on a desk.

```
   lab PSU  5.00 V  ──────────────► TB6612  VM
   (current limited)                   │
                                       ├── AO1 ──► motor  RED    ┐
                                       └── AO2 ──► motor  WHITE  │  N20, 1:100
                                                                 │  free shaft,
   Pico 2 W ── 3V3 (pin 36) ──┬──────► TB6612  VCC               │  no load
                              └──────► motor  BLUE  (hall power) │
                                                                 │
   Pico GP2/3/4/5 ────────────────────► PWMA / AIN1 / AIN2 / STBY│
   Pico GP12/GP13 ◄───────────────────  motor YELLOW / GREEN ────┘
   Pico GP20/GP21 ────────────────────► instrumentation (loop timing)
   Pico GP0/GP1  ◄───────────────────► FTDI @3.3 V ──► laptop, 115200 telemetry

   8-ch FX2 analyser  D0..D7 ─────────► the eight points in §3
   ONE COMMON GROUND for all of the above.
```

**What it produces.** Two data streams that must agree, which is the point:

| Stream | Comes from | Rate | What it is good for |
|---|---|---|---|
| **Telemetry** | Pico → UART → laptop CSV | 50–100 Hz | Long runs. Duty, encoder ticks, derived `omega_rad_s`, measured loop period |
| **Capture** | Analyser → `.sr` files | MHz, short windows | Short, high-resolution windows. Actual PWM duty and frequency, quadrature edges, loop period and jitter |

The telemetry is the *firmware's own account of itself*. The capture is an
**independent witness** that can falsify it. A number that only ever appears in
one of the two streams has never been checked by anything.

**What this bench deliberately is not:** it is not the rover. One motor at a
time, no chassis, no load, no battery. Everything here is about making four
runs comparable to each other — see §7.

---

## 2. ⚠️ SAFETY INTERLOCKS — read before touching anything

> ### These six rules have each already cost money or an evening.
>
> **1. NEVER put an analyser probe on `AO1`, `AO2`, `BO1` or `BO2`.**
> Those are H-bridge *outputs*. They sit at motor voltage and switch inductively
> — the flyback spike goes well above `VM`. The FX2's inputs are 3.3 V logic
> with no series protection. One touch destroys the analyser, and it does not
> fail loudly; it fails by reading garbage on a channel you then trust.
> **If you want to see what the motor sees, use a scope with a proper probe, or
> do not look.** The eight legitimate probe points are in §3 and nothing else
> on this bench is probeable.
>
> **2. Encoder power is 3.3 V. Never 5 V.**
> The blue wire goes to Pico pin 36 `3V3(OUT)` or a 3.3 V rail — **never** to
> the 5 V motor rail, never to `VBUS`, never to the PSU. The hall outputs
> (yellow/green) swing to whatever you feed the blue wire, and they land
> directly on `GP12`/`GP13`. **RP2350 GPIO is not 5 V tolerant.** Powering the
> encoder from 5 V puts 5 V on two Pico inputs.
>
> **3. Grounds must be common.** Pico, TB6612, bench PSU, motor black wire, and
> the analyser all share one rail. A logic level is a *voltage difference*; with
> no shared reference, "3.3 V" is meaningless and the driver cannot interpret
> its own inputs. A floating analyser ground is the single most common cause of
> a capture that looks like noise.
>
> **4. The 4S LiPo never touches the TB6612.** 16.8 V charged against a 13.5 V
> `VM` maximum. It goes through the D24V90F5 regulator, every time, with no
> exceptions and no "just for a second". On this bench, use the lab PSU and
> leave the LiPo in the bag.
>
> **5. No motor current enters the Pico.** No wire from the bench PSU to the
> Pico, and no wire from a driver output back to it. The Pico is powered by USB
> on this bench, full stop. (`VM` on `VBUS` was a bring-up stopgap — see
> [`WIRING.md`](WIRING.md) §2 — and it is **not** how this bench is built.)
>
> **6. Confirm the motor pair by resistance BEFORE connecting.**
> Two measurements, thirty seconds:
> - **red ↔ white: a few ohms.** That is the winding. This is the motor pair.
> - **black ↔ blue: NOT a few ohms** (open, or a large reading). That confirms
>   you have not swapped the pairs.
>
> **An entire evening was lost on this project to encoder wires driven into the
> H-bridge outputs.** Every logic test point read correctly. The motor sat
> silent. The fault was *past the end of the chain being measured*, so no amount
> of correct measurement upstream could find it. See
> [`../memory/debugging-method.md`](../memory/debugging-method.md).

**Power sequencing** (both directions matter):

| | Order | Why |
|---|---|---|
| **Power on** | Pico USB **first**, then `VM` | The Pico must be driving `STBY` low and the direction pins to a defined state *before* the H-bridge has a supply. `VM` first, against undefined inputs, means the motor can lurch |
| **Power off** | `VM` **first**, then Pico USB | Same reason in reverse: never leave the bridge energised while its control inputs go undefined |

**Recommended hardware addition (needs the owner's sign-off):** a **10 kΩ
resistor from `STBY` to GND**. RP2350 comes out of reset with GPIO as inputs,
and **erratum RP2350-E9** says the internal pull-downs can latch high — so
"`STBY` is pulled down at reset" is not something this chip guarantees. An
external pull-down makes "driver disabled until firmware says otherwise" a
property of the *circuit* rather than of the software. It is one resistor and
it removes a whole class of startup lurch. Not yet in [`WIRING.md`](WIRING.md).

---

## 3. THE PROBE TABLE

All eight channels, all 3.3 V logic, all on the Pico header. **Nothing on this
bench except these eight pins may be probed.**

| CH | Pico GP | Header pin | Signal | Direction | What this channel buys you |
|---|---|---|---|---|---|
| **D0** | `GP2` | **4** | `PWMA` | Pico → driver | The **actual** duty and frequency on the wire. Firmware asks for 50%; the PWM divider truncates and the pin delivers 49.6%. This channel is the difference between the duty you commanded and the duty the motor received — and every point on the duty→rad/s curve is plotted against one of those two numbers. It also shows you instantly when PWM has *stopped* (a hung loop still leaves the last duty latched in the peripheral, so the motor keeps spinning while the firmware is dead) |
| **D1** | `GP3` | **5** | `AIN1` | Pico → driver | Direction bit 1. With D2, verifies the TB6612 truth table (`10` fwd, `01` rev, `11` brake, `00` coast) against what the firmware *thinks* it set |
| **D2** | `GP4` | **6** | `AIN2` | Pico → driver | Direction bit 2. D1+D2 together give you the **reversal deadtime** — the gap between one direction bit falling and the other rising. Also the exact timestamp of every direction change, which is what you align the encoder phase reversal against to prove the sign convention |
| **D3** | `GP5` | **7** | `STBY` | Pico → driver | Global enable. Proves the driver was actually enabled for the whole run (a mid-run `STBY` glitch produces a mysterious dropout in the speed curve that looks like a motor fault). Later, in Story 2.1, this channel measures the **failsafe latency** directly: last valid UART byte → `STBY` falling edge |
| **D4** | `GP12` | **16** | Encoder A (yellow) | motor → Pico | The raw hall waveform. Gives you edge counts and edge *intervals* — i.e. a speed measurement that does not pass through the firmware at all. This is the witness that can falsify the firmware's counter |
| **D5** | `GP13` | **17** | Encoder B (green) | motor → Pico | The quadrature partner. A+B give: **which channel leads** (⇒ direction sign, fixed in firmware, never in the wiring), **counts per revolution** by direct edge counting (Story 1.4, the number the datasheets disagree about), and the **phase symmetry** — a magnetic encoder with an off-centre magnet gives unequal A/B high times, which is a systematic count error you can only see in the raw waveform |
| **D6** | `GP20` | **26** | `LOOP_TICK` | Pico → analyser | **Firmware toggles this once per PID iteration.** Measures the real loop period and its jitter |
| **D7** | `GP21` | **27** | `COMPUTE_BUSY` | Pico → analyser | **Firmware holds this HIGH while the loop body computes.** Measures execution time per iteration, i.e. CPU headroom |

**Ground:** connect every ground lead the analyser has. The header GNDs nearest
each probe cluster are **pin 3** (next to D0–D3), **pin 18** (next to D4/D5) and
**pin 28** (next to D6/D7). One ground is the minimum; three is better and
costs nothing.

### 3.1 Why D6 and D7 are worth two of eight channels

They are the two most expensive channels on the bench — they cost you the UART
lines that used to sit there ([`WIRING.md`](WIRING.md) §8) — and they are worth
it. Here is the argument, because you will be asked it.

**The claim "the control loop runs at 100 Hz" is, without D6, an assertion in a
comment.** Firmware that sets a 10 ms timer and firmware that runs a 10 ms timer
look identical from the outside. The failure modes are not exotic:

- the timer is configured from a clock you got wrong by a factor of 2;
- the loop body occasionally takes 12 ms and silently skips a tick;
- a blocking `printf` over USB stalls the loop for 40 ms whenever the host is
  slow to drain the buffer;
- an interrupt you forgot about fires at 1 kHz and steals 30% of the CPU.

Every one of those produces plausible-looking telemetry.

**Why this ruins a motor characterisation specifically.** `omega_rad_s` is
computed as `delta_ticks / ticks_per_rev / delta_t_s`. If `delta_t_s` is the
*nominal* 0.010 s but the loop actually ran at 92 Hz, **every speed on every
curve is wrong by 8%, in the same direction, silently.** It will not look like
noise. It will look like a clean, repeatable, wrong answer — the worst kind. You
would then compare four motors, all measured on the same wrong timebase, and
conclude they agree beautifully. The instrument error cancels between motors and
survives every consistency check you would think to run.

**Why *two* channels and not one.** D6 alone tells you the loop was late. It
cannot tell you *why*, and the two causes have opposite fixes:

| What you see | Diagnosis | Fix |
|---|---|---|
| Period stretches, `COMPUTE_BUSY` high-time stays constant | The loop body is fine; something **outside** it is stealing time — an ISR, a DMA stall, the timer itself | Find the thief. Do not optimise the loop body |
| Period stretches **and** `COMPUTE_BUSY` high-time stretches with it | The loop body itself got slower — a branch you only take at high duty, a float divide, a blocking write | Optimise or move work out of the loop |
| Period is stable, `COMPUTE_BUSY` high-time is creeping toward the full period | Nothing is broken **yet**. You are running out of CPU and the next feature breaks it | Budget now, before it becomes a mystery |

That third row is the one that pays for the channel. `COMPUTE_BUSY` duty cycle
*is* your CPU headroom, read straight off the analyser as a percentage. You get
the warning before the failure instead of after it.

**And the honest framing:** loop jitter is the difference between
characterising a motor and characterising a control system. Without D6/D7 you
cannot tell which one you measured, and the entire value of Story 1.5 is that
its four runs are comparable to each other and to Story 1.6.

> ### 🔴 The factor-of-two trap on D6
> `LOOP_TICK` **toggles**, so:
> - **edge → next edge (either direction) = ONE loop period** ⇒ 10 ms at 100 Hz
> - rising → next rising = **TWO** loop periods ⇒ 20 ms, which a frequency
>   readout will report as **50 Hz**
>
> Measure **edge-to-edge**, or count edges: **one edge = one iteration**, so at
> 100 Hz you should see **100 edges per second** and the square wave on the wire
> is **50 Hz**. A frequency readout therefore shows *half* the loop rate —
> double it, never halve it. Write this on a sticky note. It will otherwise cost
> you an hour of believing the loop is half speed.
>
> Toggling was chosen over a short pulse because a toggle needs no pulse-width
> decision, cannot be missed by an under-sampled capture, and a **stuck** line
> is instantly obvious (a stuck pulse line just looks idle).

### 3.2 What you gave up, and when to give it back

| | D6 | D7 |
|---|---|---|
| [`WIRING.md`](WIRING.md) §8 (original) | UART TX (`GP0`, pin 1) | UART RX (`GP1`, pin 2) |
| This bench (Stories 1.5 / 1.6) | `LOOP_TICK` (`GP20`) | `COMPUTE_BUSY` (`GP21`) |

**The trade, stated plainly:** during characterisation the UART *content* is
already available — you are reading the decoded CSV at the other end of the
wire. Decoding it a second time on the analyser is redundant. Loop timing has
no other witness at all. So the timing pins win.

**Swap back for Story 2.1 (the failsafe).** There the question is "how long
from the last valid byte to the motors stopping?", which needs UART TX and
`STBY` on the same timebase — and the loop timing is the redundant pair. Move
D6/D7 back to pins 1 and 2, keep D3 on `STBY`, and measure the interval
directly. **Record which map a capture was taken under, in the filename.**

---

## 4. Physical hookup — the numbered procedure

Do it in this order every time. **Grounds first, signals last, motor power
last of all.**

**Before you start:** everything off. PSU output disabled, Pico USB unplugged,
analyser unplugged. Have the multimeter in your hand.

1. **Meter the motor, off the bench.** Red ↔ white: **a few ohms** — write the
   value down. Black ↔ blue: **not a few ohms** — write that down too. Both
   numbers go in the run log (§7). If red–white is open or black–blue reads a
   few ohms, **stop**: you have a different motor or a broken lead, and
   connecting it is how the evening gets lost again. *(Safety rule 6.)*

2. **Build the ground rail.** One rail, everything on it:
   Pico `GND` (pin 3) → rail; TB6612 `GND` → rail; bench PSU **−** → rail;
   motor **black** → rail; analyser ground lead(s) → rail.
   **Verify with the meter's continuity beep** from the rail to each of those
   five points before anything is powered. *(Safety rule 3.)*

3. **Logic supply, and prove it before you trust it.** Pico `3V3(OUT)`
   (pin 36) → TB6612 `VCC`, and → the point where the motor's **blue** wire
   will land. Plug in the Pico's USB. **Measure that landing point against the
   ground rail: it must read 3.3 V, not 5 V.** *(Safety rule 2.)*
   Then unplug the USB again.

4. **Land the encoder power.** Motor **blue** → the 3.3 V point you just
   measured. Only now, and only because you measured it.

5. **Control signals.** `GP2`→`PWMA` (pin 4), `GP3`→`AIN1` (pin 5),
   `GP4`→`AIN2` (pin 6), `GP5`→`STBY` (pin 7). Add the 10 kΩ `STBY`-to-GND
   pull-down here if it has been signed off (§2).

6. **Encoder signals.** **yellow** → `GP12` (pin 16), **green** → `GP13`
   (pin 17). Which of yellow/green is "A" is a *naming convention, not a wiring
   constraint* — pick this one, keep it identical for all four motors, verify
   the phase direction on the analyser, and **fix the sign in firmware, never by
   swapping wires.** The left and right wheels are physical mirrors; all four
   motors get wired identically and firmware owns the polarity.

7. **Analyser: ground leads first, then the eight signal probes** per the §3
   table. Ground first is not superstition — a probe landing on a live pin with
   no reference is how you get a spurious current path.
   **Count the probes against the table out loud. There is no ninth probe and
   nothing on the driver's output side is probeable.** *(Safety rule 1.)*

8. **Telemetry link.** FTDI `TXO` → `GP1` (pin 2), FTDI `RXI` → `GP0` (pin 1),
   FTDI `GND` → rail, **FTDI `VCC` left disconnected**. **Set the FTDI's
   voltage jumper to 3.3 V and verify it on its own VCC pin before it comes
   near the Pico.** TX↔RX crossed; both sides 3.3 V; no level shifter.

9. **Motor terminals, with `VM` still off.** **red** → `AO1`, **white** →
   `AO2`.

10. **Set the bench supply before enabling its output.**
    - **Voltage: 5.00 V.** *(Assumption, and it is deliberate: the rover's rail
      is 5 V, the motor window is 4.5–6 V, and the H-bridge drops ~0.5 V, so the
      motor sees ~4.5 V under load. **Characterise at the voltage you will
      drive at.** Whatever you choose, it is the same for all four motors and it
      is recorded in every CSV — the duty→rad/s curve scales with it.)*
    - **Current limit: 1.00 A.** Above anything one N20 should draw (supplier
      claims ~100 mA no-load / ~200 mA stall, both distrusted), below the
      TB6612's 1.2 A continuous rating. A current limit is what turns a wiring
      mistake into a beep instead of smoke. If the supply goes into constant-
      current during a run, **that is a finding, not a nuisance** — record it
      and read §8 row 12.

11. **Power up: Pico USB first, then enable `VM`.** Plug the analyser's USB in
    once its grounds and probes are landed (step 7), and confirm `STBY` is low
    on the analyser (D3 reads 0) *before* enabling `VM`.

12. **Run the §6 verification ladder.** Every session. It takes two minutes and
    it is the only thing standing between you and a campaign of confidently
    wrong data.

---

## 5. The analyser: what to know before the first capture

### 5.1 Maximum sample rate is NOT documented in this repo

Nowhere in `HARDWARE.md`, `WIRING.md` or here is there a maximum sample rate
for this FX2 clone, **and that is deliberate.** The figure depends on the
specific clone, the USB host controller, the cable, and how many channels you
enable. **Read it off the device at runtime:**

```bash
sigrok-cli --scan                     # is it there at all, and under what driver
sigrok-cli --driver fx2lafw --show    # ⇒ the samplerate list THIS device accepts
sigrok-cli -L                         # which decoders exist on this machine
```

`--show` prints the supported `samplerate` values. **That list is ground truth.
This document is not.** If a rate you ask for is silently rounded down, you will
compute frequencies from the rate you *asked* for and be wrong — so read the
rate back from the capture, not from your command line.

> ### What it returned on 2026-08-31 — an OBSERVATION, not a specification
> `sigrok-cli 0.7.2`, device `fx2lafw:conn=1.30 - Saleae Logic, 8 channels
> D0..D7`. `--show` listed:
>
> ```
> 20k 25k 50k 100k 200k 250k 500k  1M 2M 3M 4M 6M 8M 12M 16M 24M 48M   (Hz)
> Supported triggers: 0 1 r f e
> ```
>
> **Re-run `--show` anyway.** This is one machine, one USB port, one day. The
> repo's tagging convention applies: this is `[MEASURED]` on that date, not
> `[SUPPLIER]`, and it is not a promise about tomorrow.

> ### 🔴 Offered is not the same as sustainable
> That list is what the device will **accept**, not what it can **stream**.
> At 8 channels fx2lafw sends about one byte per sample, so 48 MSa/s asks for
> ~48 MB/s across USB 2.0 — more than the bus realistically delivers. It will
> not refuse; it will drop samples.
>
> **Verify, don't assume:** ask for a known number of samples and check you got
> them.
>
> ```bash
> sigrok-cli --driver fx2lafw --config samplerate=4m \
>            --channels D0,D1,D2,D3,D4,D5,D6,D7 --samples 400000 -o /tmp/rate-test.sr
> sigrok-cli -i /tmp/rate-test.sr -O bits --channels D0 | wc -c   # short => dropped
> ```
>
> A rule of thumb worth testing rather than trusting: enabling **4 channels or
> fewer** typically lets the device sustain a higher rate than 8. If you need
> speed more than breadth, capture in two passes of four channels.

### 5.2 Choosing a sample rate — the arithmetic, not a guess

Nyquist is the wrong rule here. Nyquist tells you whether you can *reconstruct*
a waveform; you are *timing edges*, and edge timing resolution is one sample
period. Work backwards from the smallest interval you need to resolve.

| Measurement | Interval to resolve | Minimum rate | Comfortable |
|---|---|---|---|
| PWM **frequency**, 20 kHz (50 µs period) | ~1 µs | 1 MSa/s | 4 MSa/s |
| PWM **duty to 1%** | 0.01 × 50 µs = **0.5 µs** | **2 MSa/s** | 8 MSa/s |
| PWM duty to 0.1% | 50 ns | 20 MSa/s | probably out of reach |
| Reversal **deadtime**, if it is ~µs | ~0.1 µs | 10 MSa/s | 16–24 MSa/s |
| **Encoder** edges, ~7 kHz per channel (~140 µs apart) | ~1 µs | 1 MSa/s | 4 MSa/s |
| **Loop period** jitter to 10 µs | 10 µs | 100 kSa/s | 1 MSa/s |

**Working number for this bench: 4 MSa/s on all eight channels** gives 1%-ish
duty resolution and enormous margin on everything else. Confirm your device
offers it (§5.1); drop to a lower listed rate if not, and recompute the duty
resolution rather than pretending.

*(Where the 7 kHz encoder figure comes from: `HARDWARE.md` §6.3 — ~1400 counts
per output revolution at a plausible 300 output RPM. **Both terms are still
[MEASURE], and this one is an order-of-magnitude sanity figure, not a spec.**
If "1400 counts" turns out to mean 1400 *cycles* per channel rather than 1400
quadrature counts, the per-channel edge rate is ~14 kHz and the spacing ~70 µs —
2× worse than the row above. 4 MSa/s resolves that to 0.25 µs and the
recommendation does not move, which is the only reason the ambiguity is
tolerable here. Do not carry this number into anything that scales with it.)*

### 5.3 Capture length — why the analyser cannot log the whole run

At 8 channels, fx2lafw sends roughly **one byte per sample**. So:

```
4 MSa/s × 1 byte × 8 channels-in-one-byte  =  4 MB/s  =  240 MB/minute
```

A full 18-minute characterisation run would be ~4 GB, streamed over USB 2.0
without a dropped sample. That is not going to happen, and you do not need it.

**⇒ The division of labour that makes the bench work:**

| Stream | Covers | Rate |
|---|---|---|
| **UART telemetry** | The **whole** run, continuously | 50–100 Hz — enough for steady-state averages |
| **Analyser** | **Short windows** around specific questions: one duty step, one reversal, ten seconds of loop timing | MHz — enough to time edges |

Capture windows of **2–10 seconds**, deliberately, at chosen moments. Name them
for the question they answer.

### 5.4 Triggers: mostly don't

fx2lafw has **no hardware trigger.** libsigrok implements triggering in
software on the host, which means there is no meaningful pre-trigger buffer and
the trigger costs you nothing on the device.

**Boring recommendation: don't use triggers.** Free-run a fixed time window that
you know contains the event, save the `.sr`, and find the event in
post-processing where you can look at it as many times as you like. A trigger
that misfires costs you the whole event; a time window that is 3× too long costs
you a few MB.

### 5.5 Decoders — also discovered, not assumed

`sigrok-cli -L` lists the protocol decoders installed on **this** machine.
Confirmed present on 2026-08-31, and every one of them is useful here:

| Decoder | Channels / options | What it does for this bench |
|---|---|---|
| `pwm` | `data=` · `polarity=` | Duty cycle and period on D0. Annotation rows: `duty-cycle`, `period` |
| `timing` | `data=` · `edge=any\|rising\|falling` · `avg_period=` · `delta=` | **Time between edges.** This is the D6 loop-period tool |
| `jitter` | `clk=` · `sig=` | Timing jitter between two signals — e.g. D6 against D7 |
| `counter` | `data=` · `data_edge=` · `reset=` | **Edge counting.** The Story 1.4 ticks-per-revolution tool |
| `graycode` | `d0=` · `d1=` · `edges=` · `avg_period=` | **Quadrature.** Accumulates increments and reports count, turns, interval and rate from D4/D5 |
| `uart` | `rx=` · `tx=` · `baudrate=` | For Story 2.1, when D6/D7 go back to the UART pins (§3.2) |

Annotation-row syntax is `-A <decoder>` for all rows, or `-A <decoder>=<row>`
for one — e.g. `-A timing=average`.

---

## 6. Verification — prove the instrument on a signal you already understand

> **An analyser you have never successfully triggered is not a working
> instrument, and you find that out at the worst possible moment.**
> — [`../memory/logic-analyser.md`](../memory/logic-analyser.md)

Run this ladder **at the start of every session, and again after every motor
swap.** Two minutes. Each rung tests exactly one thing, and each rung is a
signal whose correct answer you already know *before* you press enter.

Put the results in `experiments/bench-verify/<UTC-date>/`.

### Rung 0 — is the device there?

```bash
sigrok-cli --scan
# expect a line naming the fx2lafw driver and 8 channels D0..D7
lsusb | grep -i 0925
# expect 0925:3881 -- the ID in HARDWARE.md section 5, and what this device
# still reports after sigrok has uploaded the fx2lafw firmware into it
# (observed 2026-08-31: 0925:3881 present, and --scan found it).
```

**If `--scan` finds it, the firmware upload is not your problem** — sigrok
uploads `fx2lafw` into the FX2's RAM on every scan, so a device that scans has
already been loaded. Grep on the vendor ID (`0925`) rather than the full pair:
what matters at this rung is "is the USB device enumerating at all".


### Rung 1 — static levels: probe, channel, and ground

Nothing is running. Move **one** probe by hand.

```bash
mkdir -p ~/baby-rover/experiments/bench-verify/$(date -u +%Y-%m-%d)

# probe tip on the GROUND rail:
sigrok-cli --driver fx2lafw --config samplerate=1m \
           --channels D0 --samples 64 -O bits
# EXPECT: 64 zeros.

# probe tip on Pico 3V3(OUT), pin 36:
sigrok-cli --driver fx2lafw --config samplerate=1m \
           --channels D0 --samples 64 -O bits
# EXPECT: 64 ones.
```

**What this proves:** the probe conducts, the channel is the channel you think
it is, and the analyser's ground reference is the same ground the target uses.
**What falsifies "the ground is fine":** if the 3.3 V test does **not** give a
clean run of ones, the ground lead is the first suspect — not the firmware, not
the probe.

**Repeat for all eight probes.** It is tedious exactly once and it catches a
mis-numbered clip lead, which otherwise produces a capture where `AIN1` and
`AIN2` are swapped and the truth table appears violated.

### Rung 2 — a slow square wave you already know the answer to

Have the firmware toggle `GP20` **exactly once per second** — the same
one-edge-per-tick convention `LOOP_TICK` uses, just slowed down 100× (or blink
the LED and probe that, if the firmware is not up yet — any signal *whose edge
spacing you already know*).

```bash
# 10 s, because one edge per second means a 5 s window holds only four
# intervals -- too few to see a spread
sigrok-cli --driver fx2lafw --config samplerate=1m \
           --channels D6 --time 10s \
           -o ~/baby-rover/experiments/bench-verify/$(date -u +%Y-%m-%d)/tick-1hz.sr
```

```bash
sigrok-cli -i ~/baby-rover/experiments/bench-verify/$(date -u +%Y-%m-%d)/tick-1hz.sr \
           -P timing:data=D6:edge=any -A timing | head
```

**EXPECT edge-to-edge 1000 ms ± a few µs** — one *toggle* per second means one
*edge* per second, so the wire carries a **0.5 Hz** square wave and any
rising-to-rising or frequency readout says **2000 ms / 0.5 Hz**. That is the
§3.1 factor of two, practised on a signal where the right answer is obvious.
§6.1 does the same job without a decoder.

**What this proves: the timebase.** If the sample rate the device actually used
is not the one you asked for, this is where it shows up, as a period that is off
by a clean ratio. Every duration you ever measure is scaled by this.
**What falsifies "the timebase is fine":** a measured edge spacing that is a
simple multiple or fraction of 1000 ms — 500, 2000, 667 ms. That is a rate
mismatch, not a firmware bug. *(2000 ms specifically is more likely to be the
factor of two above than a rate mismatch — check which edges you measured
before you blame the clock.)*

### Rung 3 — the fast signal, still with a known answer

Motor **disconnected or `STBY` low** so nothing moves. Command a known duty —
**0.50 at 20 kHz**.

```bash
sigrok-cli --driver fx2lafw --config samplerate=4m \
           --channels D0 --time 1s \
           -o ~/baby-rover/experiments/bench-verify/$(date -u +%Y-%m-%d)/pwm-50pct.sr

# decode it (the pwm decoder was confirmed present on 2026-08-31; see §5.5)
sigrok-cli -i ~/baby-rover/experiments/bench-verify/$(date -u +%Y-%m-%d)/pwm-50pct.sr \
           -P pwm:data=D0 -A pwm | head -20
```

**EXPECT** 20.0 kHz ± the PWM divider's truncation, and ~50% duty. Then repeat
at **0.25** and **0.75** and check the duty tracks. Three points is a line;
one point is a coincidence.

**What this proves:** that the analyser resolves a 50 µs period well enough to
report duty, at the rate you chose in §5.2. **What falsifies "4 MSa/s is
enough":** re-capture at half the rate. If the duty figure changes, the rate was
marginal. If it is identical, it was not.

### Rung 4 — the loop timing channels

Firmware running its real 100 Hz loop, motor still stopped.

```bash
D=~/baby-rover/experiments/bench-verify/$(date -u +%Y-%m-%d)
sigrok-cli --driver fx2lafw --config samplerate=1m \
           --channels D6,D7 --time 10s -o $D/loop-idle.sr
```

```bash
# loop period, averaged over 100 edges
sigrok-cli -i $D/loop-idle.sr -P timing:data=D6:edge=any:avg_period=100 \
           -A timing | tail -5

# total edge count over the window: one edge per iteration => 100 Hz is
# 100 edges/s => 1000 edges in 10 s
sigrok-cli -i $D/loop-idle.sr -P counter:data=D6:data_edge=any \
           -A counter=edge_counts | tail -1
```

**EXPECT:** D6 edge-to-edge **10.00 ms**, i.e. **100 edges per second** (one
edge per iteration, §3.1), i.e. **1000 edges in the 10 s window**, i.e. a
**50 Hz** square wave on the wire. D7 high for some consistent fraction of each
10 ms.

**What this proves:** the loop is real, at the rate claimed, before any motor
data is taken against it.

### 6.1 Measuring intervals without depending on a decoder

Decoder names and options change between sigrok versions. This does not, and it
is inspectable, which is the house style. Use it as the second opinion whenever
a decoder's answer surprises you.

**First, read the sample rate back out of the capture rather than trusting your
command line** — the CSV output writes it into its own header:

```bash
sigrok-cli -i $D/loop-idle.sr -O csv --channels D6 | head -4
# ; CSV generated by libsigrok ...
# ; Channels (1/8): D6
# ; Samplerate: 1 MHz          <-- THIS is the rate the capture actually used
```

```bash
# Edge-to-edge intervals, in microseconds, from any single-channel capture.
# SR must match the "; Samplerate:" line above, not what you asked for.
SR=1000000
sigrok-cli -i $D/loop-idle.sr -O csv --channels D6 \
| awk -F, -v sr="$SR" '
    /^[01]/ {
      n++; v = $1 + 0
      if (prev != "" && v != prev) {
        if (last) { d = (n - last) * 1e6 / sr
                    c++; s += d
                    if (c == 1 || d < mn) mn = d
                    if (c == 1 || d > mx) mx = d }
        last = n
      }
      prev = v
    }
    END { printf "intervals=%d  mean=%.2f us  min=%.2f us  max=%.2f us  spread=%.2f us\n",
                 c, s/c, mn, mx, mx-mn }'
```

`spread` is your **peak-to-peak jitter**. For a 100 Hz loop, `mean` should be
**10000 µs** and `spread` is the number that decides whether Story 1.5's speeds
are trustworthy. Set a threshold and hold yourself to it — see §8 row 9.

**Verified against a known signal on 2026-08-31**, which is the same discipline
this whole section is about: fed a synthetic 1 kHz square wave at 1 MSa/s it
returned `intervals=38  mean=500.00 us  min=500.00 us  max=500.00 us
spread=0.00 us` — exactly the 500 µs edge spacing a 1 kHz square wave has. The
tool was proved on an answer that was known in advance, before it was used on
one that was not.

### 6.2 The failure this whole section prevents

You skip verification. The analyser is quietly sampling at a different rate than
you asked, or D4 and D5 are swapped, or a ground lead fell off during the last
motor swap. You take a full campaign of data. It is internally consistent,
because the error is systematic. It survives every sanity check you would think
to run, because all four motors were measured through the same broken
instrument.

You only discover it in Story 1.6, when the PID refuses to behave the way the
feedforward curve predicts — and at that point you have **two** hypotheses that
predict the same symptom (bad characterisation data / bad control code) and no
way to separate them, because the instrument that would arbitrate is the one
under suspicion.

**Proving the instrument on a signal you already understand is what keeps the
instrument out of the suspect list.** It is cheap, it is boring, and it is the
whole reason the ladder above starts with a wire touched to ground.

---

## 7. Motor swap procedure — the campaign runs FOUR times

> **The entire value of Story 1.5 is that the four runs are comparable.**
> Four excellent runs taken under four slightly different conditions are worth
> less than four mediocre runs taken identically, because the question is *how
> much do these motors differ from each other* — and any change you make between
> runs is indistinguishable from a difference between motors.

### The rule

**If any bench parameter changes mid-campaign, the campaign restarts.**
Firmware, PSU voltage, current limit, probe positions, sample rate, dwell time,
warm-up, the mounting, the ambient temperature within reason — all of it. Four
comparable runs, or nothing.

This is enforceable rather than remembered because the **firmware git SHA, PSU
voltage, sample rate and every experiment parameter go in the CSV header of
every run** (see [`experiments/motor-char.md`](experiments/motor-char.md) §6).
A mismatch is then a `diff`, not a memory test.

### The checklist

Run **every** item, in order, for each of motors 2, 3 and 4.

**Before disconnecting**
- [ ] **Label the outgoing motor** with tape — `M1`, `M2`, `M3`, `M4` — *before*
      it leaves the bench. An unlabelled motor in a drawer is a lost run
- [ ] Confirm its CSV exists, is non-empty, and its header carries the run
      parameters
- [ ] `VM` **off first**, then Pico USB off (§2 power sequencing)

**Disconnect and reconnect — motor wires ONLY**
- [ ] Remove exactly six wires: red, white, black, blue, yellow, green
- [ ] ❌ **Do not** move a single analyser probe
- [ ] ❌ **Do not** re-flash, rebuild, or edit the firmware
- [ ] ❌ **Do not** touch the PSU voltage or current limit
- [ ] ❌ **Do not** change the sigrok sample rate or channel list
- [ ] ❌ **Do not** "just improve" the experiment script

**The new motor**
- [ ] **Meter it: red ↔ white = a few ohms; black ↔ blue = not.** Record both
      numbers in the run log. *(Safety rule 6. Every motor. Every time. This is
      the step that was skipped on the evening that was lost)*
- [ ] Land the six wires in the same holes: red→`AO1`, white→`AO2`,
      black→rail, blue→3.3 V, yellow→`GP12`, green→`GP13`
- [ ] Mount it the same way — same clamp, same orientation, shaft free and
      unobstructed. A motor taped to the bench and a motor held in a vice have
      different friction and different thermal paths

**Re-verify before taking data**
- [ ] Meter the 3.3 V at the blue wire's landing point
- [ ] Power up: Pico first, then `VM`. Confirm D3 (`STBY`) reads 0 before `VM`
- [ ] **Re-run the §6 ladder, rungs 1, 3 and 4** (≈60 s). This is the highest-
      value item on the list: a probe knocked loose during the swap silently
      ruins this motor *and* every motor after it, and the ladder catches it
      before a single data point is taken
- [ ] Confirm the encoder counts **zero** ticks while duty is zero. Non-zero
      counts at rest are electrical noise and they will inflate every low-speed
      reading

**Record, in the run log, for every motor**
- [ ] Motor ID · UTC timestamp (start and end) · red–white Ω · black–blue Ω
- [ ] `VM` measured **at the TB6612's `VM` pin**, not read off the PSU display
- [ ] Firmware git SHA · sigrok sample rate · analyser channel map version
- [ ] Ambient temperature, roughly, and whether the motor was warm at the start
- [ ] Anything that went wrong, **including things you fixed**. A fix you made
      mid-campaign is a parameter change

**Then**
- [ ] Warm-up per [`experiments/motor-char.md`](experiments/motor-char.md) §3 —
      the same warm-up, for every motor, no exceptions
- [ ] Run
- [ ] Registry row in [`../experiments/REGISTRY.md`](../experiments/REGISTRY.md)
      **when the run happens**, not later

### Where the run log lives

`experiments/motor-char/run-log.md`, one section per motor, plus the CSVs
alongside. It is a lab notebook: written as you go, never reconstructed
afterwards.

---

## 8. Troubleshooting

**How to use this table.** Follow
[`../memory/debugging-method.md`](../memory/debugging-method.md):

1. **Walk the causal chain.** Measure at every link and find the *first* point
   where reality stops matching expectation. Everything upstream is fine;
   everything downstream is innocent until that link is fixed.
2. **State the falsifier before you test.** "I think it's the driver" is not a
   hypothesis. "If it's the driver, `AO1` will still be at 0 V while `AIN1` is
   high" is.
3. **When a chain of individually-correct readings still ends in nothing
   happening, stop testing links and test the assumption at the end of the
   chain.** That is what found the wrong-wires fault in ten seconds after an
   evening of correct measurements.

The last column is the *discriminating* measurement — the one whose outcome
splits the hypotheses, not one that is merely consistent with your favourite.

| # | Symptom | Likely causes | Discriminating measurement, and what falsifies what |
|---|---|---|---|
| 1 | `sigrok-cli --scan` finds nothing | udev rule missing · not in `plugdev` · fx2lafw firmware package absent · cable/port | `lsusb \| grep 0925`. **Device listed ⇒ it is permissions or driver, not hardware.** Then `sudo sigrok-cli --scan`: **if sudo also fails, permissions is dead as a hypothesis** — install `sigrok-firmware-fx2lafw`. Nothing in `lsusb` ⇒ cable, port, or dead device |
| 2 | Every channel reads 0 | No common ground · target unpowered · probes on the wrong header row | Touch **one** probe to Pico pin 36 (3.3 V) and capture (§6 rung 1). **Reads 1 ⇒ analyser and ground are fine; the fault is downstream at the target pin.** Still 0 ⇒ ground lead or the analyser itself. *Falsifier: if the ground were missing, the 3.3 V test could not give a clean run of ones* |
| 3 | Channels read all 1s or hash | Probe not actually landed (floating input) · RP2350 **erratum E9** pull-down latched high · ground bounce from a long lead | Capture with the target **powered off**: a properly-landed probe on a powered-off pin reads a solid 0. **Still 1s ⇒ the probe is floating**, i.e. it is not on the pin you think. If it is 0 when off and 1 when on, and firmware says the pin is an input, suspect E9 before suspecting your wiring |
| 4 | PWM duty reads 0% or 100%, or the frequency is wrong by a clean ratio | Sample rate too low (aliasing) · rate silently rounded down · firmware genuinely wrong | **Recapture at 4× the sample rate.** **The number changes ⇒ it was the instrument.** Identical ⇒ it is the firmware. Also read the actual rate back out of the `.sr` metadata rather than trusting your command line |
| 5 | Duty is close but consistently off by ~1% | PWM divider truncation — real, and worth knowing | Compare commanded duty to D0's measured duty across 0.1 … 0.9. **A consistent offset or slope ⇒ arithmetic, not noise.** This is a *finding*: plot the curve against measured duty, and record the mapping |
| 6 | Only one encoder channel toggles | Yellow and green in the same pin · a broken hall lead · hall unpowered · one hall dead | Meter blue↔black = **3.3 V**. Then turn the shaft **slowly by hand** and capture D4/D5 for 5 s. **Unpowered ⇒ BOTH channels sit at a constant level; one toggling falsifies "unpowered".** One flat + one toggling with power confirmed ⇒ a lead or a hall sensor |
| 7 | Encoder counts 2× or 4× what you expected | Nothing is broken: this is the **decoding scheme** (1× / 2× / 4× edges) | Count edges **in the analyser capture** over exactly one hand-turned output revolution, and compare with the firmware counter over the *same* window. Two numbers, one window ⇒ the ratio between them **is** the decoding factor. This is the Story 1.4 measurement that settles the 11-vs-14 dispute |
| 8 | Motor does not turn; every logic point reads correctly | 🔴 **The classic.** Wrong wires past the end of the chain you are measuring | **Split the system in half.** Disconnect red/white from the driver and touch them to the bench supply at ~3 V. **Motor turns ⇒ the fault is in the driver or the wiring to it. Motor does not turn ⇒ those two wires are not the motor**, and you are almost certainly holding the encoder pair. *This is the ten-second test that should have been run first, on the evening it was not* |
| 9 | `LOOP_TICK` period reads 20 ms / "50 Hz" | The **toggle** convention (§3.1) | Count edges over 1.000 s — one edge is one iteration. **100 edges/s ⇒ the loop is at 100 Hz and your reading method was wrong** (you measured rising-to-rising, which is two iterations). **50 edges/s ⇒ the loop really is at 50 Hz** |
| 10 | `LOOP_TICK` period jitters by ~1 ms or more | Something blocking in or around the loop | **Look at D7 in the same capture.** `COMPUTE_BUSY` widens with the period ⇒ **the loop body** got slower. `COMPUTE_BUSY` constant while the gap stretches ⇒ **something outside** the loop body (ISR, timer, blocking USB/UART write). *This is the entire reason D7 exists* |
| 11 | Encoder-derived `omega_rad_s` disagrees with the analyser's edge-interval speed | Firmware counter overflow · missed edges · wrong `dt_s` · wrong `ticks_per_rev` | Compare over one capture window. **A constant ratio ⇒ a scale error** (`ticks_per_rev` or `dt_s`) — check `dt_s` against D6 first, because that is testable in one capture. **A drift that grows with time ⇒ missed edges or overflow.** Constant ratio and drift are different signatures; do not conflate them |
| 12 | PSU drops into constant-current, or `VM` sags mid-run | Real motor current exceeds the limit · a short · the limit was set too low | Meter `VM` **at the TB6612's `VM` pin** while it happens. **Sagging at the pin but not at the PSU terminals ⇒ lead resistance.** Sagging at both ⇒ the supply is at its limit and **the motor is drawing more than the supplier claims** — which is a *result*, worth recording, not just an obstacle. Raise the limit in steps to **1.2 A maximum and no further**; if it needs more than that, stop and think, because you are near the driver's continuous rating |
| 13 | Duty→rad/s curve is not repeatable between blocks | Thermal drift (windings warm, friction falls) · supply sag · gearbox break-in · the shaft fouling | Compare block 1 against block 3 (same duties, ~10 min apart). **Difference tracks *elapsed time* ⇒ thermal or break-in.** Difference tracks *duty* ⇒ electrical. Log `VM` per step so sag is separable from both |
| 14 | Capture short, or "device only sent N samples" | USB bandwidth · a hub · channel count | **Halve the sample rate and repeat.** **Succeeds ⇒ bandwidth, not a fault.** Then either accept the lower rate (and recompute your duty resolution, §5.2) or drop to ≤4 channels, which typically doubles the sustainable rate |
| 15 | Motor spins the "wrong" way | Nothing is broken: red/white is a *convention*, and so is which hall is "A" | Verify on D1/D2 that the truth table matches the commanded direction, and on D4/D5 which channel leads. **Truth table correct + phase consistent ⇒ this is a sign convention.** **Fix it in firmware. Never by swapping wires** — all four motors must be wired identically because left and right are physical mirrors |

---

## 9. Quick reference

```bash
# ── discover (never assume) ─────────────────────────────────────────
sigrok-cli --scan
sigrok-cli --driver fx2lafw --show          # ⇒ the samplerate list. Read it.
sigrok-cli -L                               # ⇒ decoders available here

# ── a working capture ───────────────────────────────────────────────
D=~/baby-rover/experiments/motor-char/$(date -u +%Y-%m-%dT%H%M%SZ)
mkdir -p "$D"
sigrok-cli --driver fx2lafw --config samplerate=4m \
           --channels D0,D1,D2,D3,D4,D5,D6,D7 \
           --time 5s -o "$D/m1-duty050-fwd.sr"

# ── inspect ─────────────────────────────────────────────────────────
sigrok-cli -i "$D/m1-duty050-fwd.sr" -O bits --channels D0 | head
sigrok-cli -i "$D/m1-duty050-fwd.sr" -P pwm:data=D0      -A pwm      | head  # duty, period
sigrok-cli -i "$D/m1-duty050-fwd.sr" -P timing:data=D6:edge=any \
                                     -A timing   | tail  # loop period
sigrok-cli -i "$D/m1-duty050-fwd.sr" -P counter:data=D4:data_edge=any \
                                     -A counter=edge_counts | tail -1        # ticks
sigrok-cli -i "$D/m1-duty050-fwd.sr" -P graycode:d0=D4:d1=D5 \
                                     -A graycode | tail  # quadrature count/rate
sigrok-cli -i "$D/m1-duty050-fwd.sr" -O csv --channels D6 | ...   # §6.1 awk
```

**Filenames carry the experiment.** `<motor>-<what>-<direction>.sr`, inside a
UTC-timestamped directory. A file called `capture1.sr` is a file you will delete
in three weeks because you cannot prove what it is.

### The five things people get wrong on this bench

1. Probing `AO1`/`AO2`. **Destroys the analyser.** *(§2 rule 1)*
2. Encoder power on 5 V. **Damages the Pico.** *(§2 rule 2)*
3. Not metering red–white before connecting. **Costs an evening.** *(§2 rule 6)*
4. Reading `LOOP_TICK` rising-to-rising and reporting half the loop rate. *(§3.1)*
5. Changing something between motor 2 and motor 3. **Voids the campaign.** *(§7)*
