# Day 0 — setup runbook

Budget a full day. There is a lot of it, and almost none of it is interesting,
but every item blocks something later. Work top to bottom.

**Two machines.** The **laptop** (dual-booted Ubuntu 24.04) is the daily driver
and the firmware build host. The **Pi 5** (`baby-rover`, Ubuntu 24.04) rides on
the rover and runs the vision and navigation code. Both run Claude Code.

Full detail on the Pi's existing state is in [`../BABY-ROVER.md`](../BABY-ROVER.md) —
read it before changing anything on that box.

---

## 1. Accounts

### GitHub
Nothing is backed up until this exists, and **the Pi's root filesystem is a
disposable USB flash drive**. Assume it will die.

```bash
# on BOTH machines
ssh-keygen -t ed25519 -C "rover-<machine>"
cat ~/.ssh/id_ed25519.pub        # paste into github.com/settings/keys
ssh -T git@github.com            # expect "successfully authenticated"
```

Then, once, from whichever machine:
```bash
cd ~/rover  &&  git init  &&  git add -A
git commit -m "Initial commit: docs, plan, memory scaffold"
git remote add origin git@github.com:<user>/rover.git
git push -u origin main
```

**Make it public.** This repo is a portfolio artifact — a private repo proves
nothing to anyone. Never commit secrets (WiFi passwords, API keys, the
Tailscale key); commit a `*.example` instead.

Install `gh` too — it is used later for the outreach work:
```bash
sudo apt install gh  &&  gh auth login
```

### Tailscale
This is what makes a rover on wheels reachable without hunting for its IP on a
strange network.

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up --ssh
```
Run on **both** machines, same account. After this, `ssh baby-rover` works from
the laptop anywhere — at home, on the university network, tethered in a
carpark. That last one matters for the outdoor GPS testing.

---

## 2. Pi 5 — make it able to build things

Straight from the system audit: **`gcc`, `make`, and `pip3` are not installed.**
Every build step fails until they are.

```bash
sudo apt update
sudo apt install -y build-essential cmake git python3-pip python3-venv \
                    pkg-config libusb-1.0-0-dev sigrok-cli pulseview \
                    minicom python3-serial gh
```

### WiFi — a blocker, not a detail
`wlan0` is **DOWN**; the Pi is on ethernet only. A rover on a chassis cannot
stay on a cable.

```bash
nmcli device wifi list
sudo nmcli device wifi connect "<SSID>" password "<password>"
ip a show wlan0                  # expect an address
```
Verify it survives a reboot **before** trusting it on the floor.

### Turn off unattended reboots
The Pi patches itself and may reboot on its own schedule — which is fine for a
server and awful in the middle of a two-hour characterisation run.

```bash
sudo systemctl disable --now unattended-upgrades
```

### Disk discipline
25 GB free on a slow USB stick, **and no swap**. `librealsense` from source is
a large build.

```bash
df -h /                          # watch this
free -h                          # confirms: no swap
```
Build with **`make -j2`**, not `-j4`. With no swap, a four-way parallel C++
build on 7.8 GB is a plausible OOM kill.

---

## 3. Instruments — the udev rules that unblock everything

The logic analyser was **completely blocked on Windows** waiting on a Zadig
driver bind. On Linux this is a udev rule instead, and it takes a minute.

```bash
sudo tee /etc/udev/rules.d/60-libsigrok.rules >/dev/null <<'EOF'
SUBSYSTEM=="usb", ATTRS{idVendor}=="0925", ATTRS{idProduct}=="3881", MODE="0666"
EOF
sudo udevadm control --reload-rules && sudo udevadm trigger
```
Unplug, replug, then prove it works on a signal you already understand:
```bash
sigrok-cli --scan
sigrok-cli -d fx2lafw --samples 1000 --channels D0    # probe a known square wave
```

**Do not skip the verification capture.** An analyser you have never
successfully triggered is not a working instrument, and you will find that out
at the worst moment.

Serial access already works — the `baby-rover` user is in **`dialout`**, so the
Pico, the FTDI adapter, and the GPS are reachable without `sudo`. The user is
also in `video`, which the RealSense needs.

---

## 4. Pico toolchain — a decision, not a default

MicroPython was the **bring-up** tool: the REPL lets one pin be toggled and
measured immediately, which is exactly right for "is my wiring correct?".

It is **not** automatically the production answer. The forcing question is PIO
quadrature decoding and a jittery-free 100 Hz PID loop. Make the call on Day 0
and record the reasoning in [`../memory/pico-toolchain.md`](../memory/pico-toolchain.md):

| Option | For | Against |
|---|---|---|
| **pico-sdk** (C/CMake) | Full PIO access, real-time determinism, the thing embedded roles ask about | Slowest to iterate; a build system to learn |
| **arduino-pico** core | Fast to write, good PIO libraries exist | Another abstraction between him and the hardware |
| **MicroPython** | Instant REPL iteration | PIO support is thinner; GC pauses in a 100 Hz control loop are a real risk |

**Recommendation: pico-sdk.** The learning goal is embedded systems, the PID
loop needs determinism a garbage collector cannot promise, and "I wrote PIO
assembly for quadrature decoding" is a far stronger interview sentence than "I
used a library." Keep MicroPython flashed on a spare Pico for bench poking.

```bash
sudo apt install -y gcc-arm-none-eabi libnewlib-arm-none-eabi
git clone -b master https://github.com/raspberrypi/pico-sdk.git ~/pico-sdk
cd ~/pico-sdk && git submodule update --init
echo 'export PICO_SDK_PATH=$HOME/pico-sdk' >> ~/.bashrc
```
The Pico **cannot be bricked** — the ROM bootloader is unerasable. Flash freely.

---

## 5. Vision

```bash
python3 -m venv ~/rover/pi/.venv && source ~/rover/pi/.venv/bin/activate
pip install numpy scipy matplotlib pytest pyserial
```
Then librealsense — try the prebuilt path first, and only build from source if
it fails. Budget an hour either way, and remember `-j2`.

---

## 6. Claude Code

Already installed on the Pi (v2.1.251) and running in **bypassPermissions** —
no approval prompts, ever. Understand what that means before working on it: any
session on that box can do anything you can do, without asking. It is a
deliberate tradeoff that holds because the box is on a trusted LAN with a
disposable rootfs. See `BABY-ROVER.md` §8.

Install it on the laptop too. Then, on both:

- `CLAUDE.md` is read automatically — it is the operating agreement
- Read `memory/MEMORY.md` at **every** session start
- One story per fresh conversation

**Note the clock:** the Pi's timezone is **UTC**, not Sydney. Dated memory
filenames will be UTC dates. Either accept it consistently or set the timezone —
just don't end up with two different conventions in `memory/`.

---

## 7. Day 0 acceptance

Do not move to Week 1 until all six pass:

- [ ] `git push` succeeds from the Pi to a public GitHub repo
- [ ] `ssh baby-rover` works from the laptop over Tailscale
- [ ] `ip a show wlan0` shows an address, and survives a reboot
- [ ] `sigrok-cli` has captured a real square wave you can read
- [ ] A blink binary is built from source and flashed to the Pico
- [ ] `pytest` runs green on an empty-but-real test suite
- [ ] The firmware toolchain decision is **written down** with its reasoning

Then write the Day 0 memory entry and index it in `memory/MEMORY.md`.
