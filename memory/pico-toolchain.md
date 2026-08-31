# Pico firmware toolchain

**Status: open decision. Make the call on Day 0 and revise this page in place.**

MicroPython 1.29.0 is on the Pico now and works — motor forward/reverse at
20 kHz PWM, driven from the REPL over USB serial.

**It was chosen as a bring-up tool, not as the production answer.** The REPL
lets one pin be toggled and measured immediately, which is the right instrument
for "is my wiring correct?". Setting up a build system first would have meant
every failure had two possible causes.

## The forcing questions

PIO quadrature decoding, and a jitter-free 100 Hz PID loop.

| Option | For | Against |
|---|---|---|
| **pico-sdk** (C/CMake) | Full PIO access, real determinism, the thing embedded roles ask about | Slowest iteration; a build system to learn |
| **arduino-pico** | Fast to write, decent PIO libraries | Another abstraction between him and the hardware |
| **MicroPython** | Instant REPL iteration | Thinner PIO support; **GC pauses in a 100 Hz control loop are a real risk** |

**Recommendation: pico-sdk.** The learning goal is embedded systems; a garbage
collector cannot promise loop determinism; and "I wrote PIO assembly for
quadrature decoding" is a much stronger interview sentence than "I used a
library." Keep MicroPython flashed on a spare Pico for bench poking.

Note: PlatformIO's official `raspberrypi` platform supports **RP2040 only, not
RP2350** — verified with `pio boards rp2350`, no matches. That rules out the
obvious PlatformIO path.

The Pico **cannot be bricked** — the ROM bootloader is unerasable. Flash freely.

Related: [[power-supply]], [[n20-motors]]
