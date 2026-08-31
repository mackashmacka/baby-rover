# Logic analyser

8-channel Cypress FX2 clone (Saleae-style), **VID:PID `0925:3881`**.
Detected by `sigrok-cli` under the `fx2lafw` driver.

## Capability — measured 2026-08-31, previously undocumented

`sigrok-cli -d fx2lafw --show` reports **8 channels and 17 supported sample
rates, 20 kHz to 48 MHz**. Treat **24 MHz as the working 8-channel ceiling**:
on an FX2 the top rate is only reachable with a reduced channel count, and the
USB 2.0 bulk pipe is the real limit. 24 MHz on all 8 channels is verified
working; 48 MHz on 8 is not, and has not been tried.

Earlier revisions of this page recorded no maximum at all, which is why
`tools/rover_bench/analyser.py` **discovers the rate from the device at runtime
and refuses to assume one**. A guessed rate produces a capture that looks
perfectly fine and whose timebase is silently wrong — the worst failure mode an
instrument has.

**Gotcha, found the hard way:** libsigrok 0.5.2 (what Ubuntu 24.04 ships) does
*not* print the rates inline after `samplerate:`. It prints a header ending in a
colon and then **one rate per indented line**. A parser that only knows the
inline comma-list and range forms reads a healthy analyser as having no rates.
See `tests/test_regressions.py`.

## Status — WORKING as of 2026-08-31

First capture ever made with this instrument, on the laptop:
**19,999.0 Hz at 50.00 % duty, 10 ns period jitter**, 240,000 samples at 24 MHz
against a commanded 20 kHz / 50 % square on `GP2`. Data in
`experiments/bench-verify/`, registry row `bench-verify`.

That capture also settled the **0.89 V** open thread: a 50 %-duty 3.3 V line
read 0.89 V on the DMM where ~1.65 V was expected. The analyser shows a clean
50.00 % square, so the meter was failing to average a 20 kHz waveform. The
instrument was fine; the meter was the wrong tool. See [[debugging-method]] —
the falsifying evidence was stated before the measurement was taken.

## Status: was blocked on Windows, is trivially fixable on Linux

On Windows it needed WinUSB bound via **Zadig**, which requires elevation and
GUI clicks and so could not be automated. It blocked **every** capture, and
nothing was ever captured with it.

**On Ubuntu that whole problem disappears** — it is a udev rule:

```bash
sudo tee /etc/udev/rules.d/60-libsigrok.rules >/dev/null <<'EOF'
SUBSYSTEM=="usb", ATTRS{idVendor}=="0925", ATTRS{idProduct}=="3881", MODE="0666"
EOF
sudo udevadm control --reload-rules && sudo udevadm trigger
```

Then unplug, replug, and **prove it on a signal you already understand** before
trusting it on one you don't. An analyser you have never successfully triggered
is not a working instrument, and you find that out at the worst moment.

## Channel map — all 3.3 V logic

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

⚠️ **Never probe `AO1`/`AO2`.** Those sit at motor voltage and will destroy the
analyser.

## What it is for

Answering "is the signal actually what I think it is?" **before** suspecting
code. Ground it to the rover's ground.

Outstanding job for it: an unexplained **0.89 V** DMM reading on a 50%-duty
3.3 V PWM line where ~1.65 V was expected. Most likely the meter failing to
average a 20 kHz square wave — but that is a hypothesis, not a finding.

Related: [[n20-motors]], [[debugging-method]]
