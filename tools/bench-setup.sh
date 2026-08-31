#!/usr/bin/env bash
# Baby Rover — one-shot laptop bench setup.
#   sudo bash tools/bench-setup.sh
# Touches only the Ubuntu root (nvme0n1p5) and /home/oliver. Never the NTFS partitions.
set -euo pipefail
U=oliver; H=/home/$U

say(){ printf '\n\033[1;36m== %s\033[0m\n' "$*"; }

say "1/7  Instrument + serial access for $U"
usermod -aG dialout,plugdev "$U"

say "2/7  udev rules — Saleae/fx2 analyser, Pico (run + BOOTSEL), FTDI"
cat > /etc/udev/rules.d/60-rover-instruments.rules <<'RULE'
# Saleae Logic (FX2) — before and after fx2lafw firmware upload
SUBSYSTEM=="usb", ATTRS{idVendor}=="0925", ATTRS{idProduct}=="3881", MODE="0660", GROUP="plugdev"
SUBSYSTEM=="usb", ATTRS{idVendor}=="0925", ATTRS{idProduct}=="3882", MODE="0660", GROUP="plugdev"
# Raspberry Pi Pico / RP2350 — CDC serial, BOOTSEL mass-storage, and debug probe
SUBSYSTEM=="usb", ATTRS{idVendor}=="2e8a", MODE="0660", GROUP="plugdev"
SUBSYSTEM=="tty", ATTRS{idVendor}=="2e8a", MODE="0660", GROUP="dialout", SYMLINK+="rover-pico"
# FTDI FT232R — Pi stand-in for the UART protocol
SUBSYSTEM=="tty", ATTRS{idVendor}=="0403", ATTRS{idProduct}=="6001", MODE="0660", GROUP="dialout", SYMLINK+="rover-ftdi"
RULE
udevadm control --reload-rules && udevadm trigger

say "3/7  Base toolchain"
apt-get update -qq
apt-get install -y -qq build-essential cmake git pkg-config \
                      python3-pip python3-venv python3-dev libusb-1.0-0-dev

say "4/7  Logic analyser stack (sigrok + fx2lafw firmware)"
apt-get install -y -qq sigrok-cli pulseview sigrok-firmware-fx2lafw

say "5/7  Pico cross-compiler"
apt-get install -y -qq gcc-arm-none-eabi libnewlib-arm-none-eabi libstdc++-arm-none-eabi-newlib

say "6/7  Python bench environment (venv, owned by $U)"
sudo -u "$U" python3 -m venv "$H/baby-rover/.venv"
sudo -u "$U" "$H/baby-rover/.venv/bin/pip" install -q --upgrade pip
sudo -u "$U" "$H/baby-rover/.venv/bin/pip" install -q \
    pyserial pytest pytest-cov numpy scipy matplotlib pandas mpremote

say "7/7  pico-sdk"
if [ ! -d "$H/pico-sdk" ]; then
  sudo -u "$U" git clone -q -b master --depth 1 https://github.com/raspberrypi/pico-sdk.git "$H/pico-sdk"
  sudo -u "$U" git -C "$H/pico-sdk" submodule update --init --depth 1
fi
grep -q PICO_SDK_PATH "$H/.bashrc" || echo "export PICO_SDK_PATH=$H/pico-sdk" >> "$H/.bashrc"

say "DONE"
cat <<EOF
Now, as $U:   newgrp plugdev   (or log out/in)
Verify:       sigrok-cli --scan
              ls -l /dev/rover-pico
EOF
