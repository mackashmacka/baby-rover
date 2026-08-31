# firmware — Pico 2 W motor-characterisation bench

One motor at a time, driven over a line-oriented ASCII protocol, instrumented
so the logic analyser can measure the control loop and not just the motor.

Target: **Raspberry Pi Pico 2 W (RP2350)**, pico-sdk, C11.
Build artifact: `build/rover_bench.uf2`.

---

## 1. Build

Needs `arm-none-eabi-gcc`, `cmake`, and a pico-sdk checkout. `pioasm` is built
by the SDK; nothing extra to install for the PIO.

```bash
git clone --branch master --recurse-submodules \
    https://github.com/raspberrypi/pico-sdk ~/pico-sdk
export PICO_SDK_PATH=~/pico-sdk        # put this in ~/.bashrc

cmake -S firmware -B firmware/build
cmake --build firmware/build -j4
# -> firmware/build/rover_bench.uf2
```

`PICO_BOARD` is pinned to `pico2_w` in `CMakeLists.txt`. Setting it wrong
produces a binary that flashes and then does nothing, with no error.

The build stamps the firmware version and the git SHA into the binary; `PING`
reports them, and `-dirty` is appended when the working tree has uncommitted
changes. **A dataset whose firmware cannot be identified cannot be
reproduced.**

## 2. Flash

1. Hold **BOOTSEL**, plug in the USB cable, release.
2. The board appears as a USB mass-storage device, `RP2350`.
3. Copy `rover_bench.uf2` onto it. It reboots into the new firmware itself.

```bash
cp firmware/build/rover_bench.uf2 /media/$USER/RP2350/
```

**The board cannot be bricked this way.** The RP2350's BOOTSEL loader lives in
mask ROM, is not writable, and is not something a bad .uf2 can damage. Whatever
you flash, holding BOOTSEL gets you back to the drive.

Then talk to it:

```bash
picocom -b 115200 /dev/ttyACM0     # or /dev/rover-pico via the udev rule
```

The USB serial is a *different channel* from the UART on GP0/GP1. Flashing and
monitoring never contend with the link to the Pi.

## 3. Pin map

Everything here is 3.3 V logic and safe to probe. Source of truth is
`docs/WIRING.md`; `src/board_config.h` mirrors it.

| GP | Pico pin | Signal | Direction | Analyser |
|---|---|---|---|---|
| GP0 | 1 | UART0 TX → Pi RXD | out | — |
| GP1 | 2 | UART0 RX ← Pi TXD | in | — |
| GP2 | 4 | `PWMA` — 20 kHz | out | D0 |
| GP3 | 5 | `AIN1` | out | D1 |
| GP4 | 6 | `AIN2` | out | D2 |
| GP5 | 7 | `STBY` | out | D3 |
| GP12 | 16 | Encoder A (hall) | in, pull-up | D4 |
| GP13 | 17 | Encoder B (hall) | in, pull-up | D5 |
| GP20 | 26 | `LOOP_TICK` | out | D6 |
| GP21 | 27 | `COMPUTE_BUSY` | out | D7 |

> **Eight channels, ten interesting signals**, and only D6/D7 differ between
> the two maps. `docs/WIRING.md` **§10.2** (the characterisation bench, and the
> one this table shows) puts `LOOP_TICK`/`COMPUTE_BUSY` there; **§8** puts UART
> TX/RX there. Both cannot be probed at once. Stories 1.5/1.6 want the
> instrument pins; Story 2.1 (the failsafe, measured from the last UART byte)
> wants the UART pair — see `FAILSAFE.md`. Move the two probe leads between
> runs and **record which map the capture was taken under**; `WIRING.md` §10.3
> is the table that decides which one a given question needs.

**Never probe `AO1`/`AO2`/`BO1`/`BO2`** — motor voltage, destroys the analyser
(`docs/WIRING.md` §1 rule 4). **Encoder power is 3.3 V, never 5 V.**

`GP23/24/25/29` are CYW43439 and are not referenced anywhere in this firmware.

## 4. Protocol

ASCII, one command per line, `\n` terminated. Commands are matched
case-insensitively; leading/trailing whitespace and a trailing `\r` are
ignored. Max line length **96 bytes**.

| Command | Reply | Notes |
|---|---|---|
| `PING` | `PONG <fw_version> <git_sha>` | identity + build provenance |
| `ID?` | `ID <board> <fw_version>` | `ID pico2w 0.1.0` |
| `SET <duty_frac>` | `OK` | −1.0…+1.0, sign is direction. **Turns the PID off** — it is an open-loop command |
| `STOP` | `OK` | coast: `IN1=IN2=0`, outputs high-Z, motor freewheels |
| `BRAKE` | `OK` | short brake: `IN1=IN2=1`, windings shorted, stops hard |
| `STBY <0\|1>` | `OK` | TB6612 enable. `0` is an instant hardware all-stop |
| `ENC?` | `ENC <signed_count> <t_us>` | snapshot from the last control tick (≤10 ms old) |
| `TELEM <hz>` | `OK` | `0` = off. Streams `T <t_us> <count> <duty_frac>` back on the channel that enabled it |
| `PID <kp> <ki> <kd>` | `OK` | gains ≥ 0. Clears the integrator (see below) |
| `PIDEN <0\|1>` | `OK` | enter/leave closed loop; clears the integrator; leaving also coasts. **Closed loop only actually runs if the PIO decoder loaded** — see below |
| `SETRPS <omega_rad_s>` | `OK` | closed-loop setpoint. **Unit is rad/s of the OUTPUT shaft**, despite the name. Range ±100 |
| `RESET` | `OK` | replies, coasts, then reboots via the watchdog |

### Unsolicited lines

Two lines arrive without being asked for, on the channel telemetry is enabled
on. Both mean the data you are collecting is suspect, and both are rate-limited
to one per second:

| Line | Meaning |
|---|---|
| `DROP <n>` | telemetry samples discarded because the host was not draining fast enough — there is a gap in the dataset, and it is not a control glitch |
| `STALL <n>` | `encoder_get_count()` had no fresh count: it timed out on the PIO FIFO, or the decoder never loaded at all — either way the control loop is not running on real feedback |

### Errors

Anything else returns `ERR <reason>` **and changes nothing**:

| Reason | Cause |
|---|---|
| `unknown_cmd` | no such command |
| `arity` | too few or too many arguments |
| `range` | numerically valid, outside the permitted range |
| `not_a_number` | unparsable, or `nan`/`inf`, or a hex float (`0x1p-3`), or trailing junk (`0.5x`) |
| `token_too_long` | a single token over 31 bytes |
| `line_too_long` | line over 96 bytes — rejected whole, never truncated |
| `embedded_nul` | a `0x00` byte inside the line |
| `empty` | a blank line. **Not** a keepalive — see `FAILSAFE.md` |
| `internal` | a NULL argument; only reachable from a test |

> ⚠️ **This vocabulary differs from `tools/rover_bench/link.py`**, which was
> written in parallel and assumes `OK <TAG> key=value`, per-motor arguments
> (`DUTY 1 +0.45`), and `COAST`/`ENCZERO`/`MODE`/`TARGET`. That module says so
> itself and keeps its constants in one block for this reason. The two must be
> reconciled before the first real bench run; the firmware here implements the
> command set given in the bench brief.

### Failsafe

**No valid command in 300 ms ⇒ the motor stops.** Full reasoning, and where to
find each piece of it, in [`FAILSAFE.md`](FAILSAFE.md).

The trip also **disarms the command**: `pid_enabled`, `setpoint_omega_rad_s`,
`brake` and `stby_enabled` are all cleared, not just the duty. Otherwise the
next valid line of any kind — a `PING` from a monitoring script — would clear
the trip and the next tick would spin the motor back up to its old setpoint.
**Recovery is explicit: `STBY 1`, then `SET` or `PIDEN`.** A run whose capture
contains a failsafe trip is a void run, not a run with a gap in it.

### No feedback ⇒ no closed loop

If `encoder_init()` fails (the PIO program will not fit, or SM0 is already
claimed) the firmware prints `ERR encoder_init_failed` at boot **and
`control_tick()` refuses to run the PID** — `PIDEN 1` is accepted by the
parser but the loop coasts instead of closing. This is not defensive
programming for its own sake: a PID fed a measurement that is permanently zero
against a non-zero setpoint winds its integrator to the anti-windup clamp
within a few ticks and then holds **full duty forever**, while the host screen
says "closed loop". `encoder_get_count()` counts every such call as a stall, so
the `STALL <n>` line keeps saying so once a second to anyone who connected
after the boot message scrolled past.

Open-loop work — the Story 1.5 deadband and duty sweeps — is unaffected and
still valid without feedback.

## 5. Instrumentation

`LOOP_TICK` (GP20) toggles **once per control iteration** — one edge per
iteration, so a full square-wave period is **two** iterations. At 100 Hz that
is 10 ms edge-to-edge and a 20 ms period. Reading the period as the loop rate
is the obvious mistake and makes the loop look half as fast as it is.

`COMPUTE_BUSY` (GP21) is high across the loop body.

- **loop jitter** = spread of the edge-to-edge intervals on D6. This is the
  measurement that justifies "the Pico runs the control loop, not Linux".
- **CPU headroom** = mean D7 high-time ÷ 10 ms.
- **overrun** = D7 still high at the next D6 edge.

Headless only — `sigrok-cli`, never PulseView in an automated path. The max
sample rate of the FX2 clone is not documented in this repo; discover it from
`sigrok-cli --scan` / `--show` rather than assuming one.

## 6. Host-pure files — what the test suite can link

These files contain **no pico-sdk includes** and compile with plain `gcc`.
This is the seam the "80% line coverage, no hardware in tests" rule depends
on:

| File | Contents |
|---|---|
| `src/protocol.c` / `src/protocol.h` | the whole command parser, the byte-stream reader, telemetry formatting |
| `src/control.c` / `src/control.h` | PID, anti-windup, feedforward, ticks→rad/s, duty→PWM register, the TB6612 truth table, wrapping encoder deltas |
| `src/board_config.h` | pin numbers and timing constants (`#define`s only) |
| `src/motor.h` | declarations only — includes `control.h`, nothing else |
| `src/encoder.h`, `src/instrument.h` | declarations only |

Everything else — `main.c`, `motor.c`, `encoder.c`, `instrument.c`,
`quadrature.pio` — touches hardware and is **not** host-buildable.

Build them into a shared library for `ctypes`:

```bash
gcc -std=c11 -O2 -fPIC -shared \
    -I firmware/src \
    -o build/libroverpure.so \
    firmware/src/protocol.c firmware/src/control.c -lm
```

or link them straight into a C test binary:

```bash
gcc -std=c11 -I firmware/src -o t test.c \
    firmware/src/protocol.c firmware/src/control.c -lm
```

Both are clean under `-Wall -Wextra -Wshadow -Wconversion -Wdouble-promotion`.

Note for `ctypes` callers: `protocol_handle_line` takes
`(const char*, char*, size_t, struct rover_state*)` and `struct rover_state`
is a plain C struct — mirror it with `ctypes.Structure` or just allocate a
generous opaque buffer and use the accessors.

## 7. The PIO decoder

`src/quadrature.pio` is the one file to read if you read only one. It is
commented line by line on purpose: it is the most interview-legible artifact
in the project, and the argument for using PIO at all (≈28,000 encoder edges
per second across four motors versus a 100 Hz control loop —
`docs/HARDWARE.md` §6.3) is an architectural decision worth being able to
defend out loud.

Summary of the mechanism: the state machine keeps the count in `Y`, the
previous 2-bit pin state in `OSR`, and each pass builds
`(previous << 2) | new` in `ISR` and executes `mov pc, isr` — a computed jump
into a 16-entry table, one entry per possible transition. x4 decoding, 30 of
the 32 instructions in a PIO block, zero CPU.

**`ticks_per_output_rev` is a placeholder, and this is the most dangerous
number in the repo.** `protocol.c` defaults it to `5600.0f` (Adafruit's 14
counts/rev × 4 edges × 100:1). Retailer listings say 11 counts/rev, which would
give 4400; if the decode turns out not to be ×4 it could be 1400. The sources
disagree and the real figure must be **measured** (`docs/PLAN.md` Story 1.4:
mark the output shaft, turn it exactly one revolution by hand, read `ENC?`).

What that means concretely, until Story 1.4 has been run:

- **Raw `ENC?` counts and the `T` telemetry line are trustworthy.** They carry
  ticks, not rad/s. Nothing derived from the placeholder goes on the wire —
  that is deliberate, and it is why the telemetry record is
  `T <t_us> <count> <duty_frac>` and not a speed.
- **`SETRPS` and closed loop are not.** The loop converges on a *measured*
  omega computed with the placeholder, so if the constant is 4× too large the
  measurement reads 4× low and the motor is driven **4× faster than asked**.
  `SETRPS 30` could mean full duty. Treat any closed-loop run taken before
  Story 1.4 as a shape, never as a number.
- **There is no wire command to change it.** It is a `rover_state` field, so
  fixing it is a one-line edit to `DEFAULT_TICKS_PER_OUTPUT_REV` and a
  reflash — not a rebuild of the logic, but still a reflash. Adding a `TPR`
  command is a protocol change and needs the host stream to agree (see the
  clash note in §4).
- Record the value **and whether it was measured** in every run manifest;
  `tools/analysis/load.py` already treats a different assumption as a
  different run.

## 8. Layout

```
firmware/
  CMakeLists.txt        pico-sdk project, target rover_bench, emits .uf2
  README.md             this file
  FAILSAFE.md           the non-negotiable one
  src/
    main.c              command loop + 100 Hz control ISR
    board_config.h      pin map, timing               [host-pure]
    protocol.c/.h       ASCII command parser          [host-pure]
    control.c/.h        PID, feedforward, conversions [host-pure]
    motor.c/.h          TB6612: PWM, direction, STBY
    encoder.c/.h        PIO loader + signed count
    instrument.c/.h     LOOP_TICK / COMPUTE_BUSY
    quadrature.pio      x4 quadrature decoder, PIO assembly
```
