# Logic analyser

8-channel Cypress FX2 clone (Saleae-style), **VID:PID `0925:3881`**.
Detected by `sigrok-cli`.

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
