#!/bin/sh
# Baby Rover — `make doctor`.
#
# Reports what is installed, what is plugged in, and what is missing. It is a
# DIAGNOSTIC, not a gate: it always exits 0, so it is safe to run anywhere,
# including on a bare checkout with nothing attached. Nothing here touches the
# motors, and nothing here drives a pin.
#
# Environment (set by the Makefile, defaulted here so the script also runs
# standalone as `sh tests/doctor.sh`):
#   PY    interpreter to build the venv with
#   VENV  venv location
#   SRC   host package coverage is measured over

PY="${PY:-python3}"
VENV="${VENV:-.venv}"
SRC="${SRC:-tools/rover_bench}"

ok()   { printf '  \033[32mok  \033[0m %s\n' "$1"; }
miss() { printf '  \033[33mmiss\033[0m %s\n' "$1"; }
info() { printf '  ---- %s\n' "$1"; }

echo "Baby Rover doctor"
echo "cwd: $(pwd)"
echo

echo "[host toolchain]"
if command -v "$PY" >/dev/null 2>&1; then ok "$PY $("$PY" --version 2>&1 | cut -d' ' -f2)"
else miss "$PY not found - install python3"; fi
if [ -x "$VENV/bin/python" ]; then ok "venv at $VENV"
else miss "no venv at $VENV - run 'make venv' (or just 'make test')"; fi
for tool in pytest coverage ruff; do
    if [ -x "$VENV/bin/$tool" ]; then ok "$VENV/bin/$tool"
    else miss "$tool not in the venv - run 'make venv'"; fi
done
if command -v git >/dev/null 2>&1; then ok "git $(git --version | cut -d' ' -f3)"
else miss "git not found - manifests cannot record a SHA"; fi
if command -v make >/dev/null 2>&1; then ok "make"; else miss "make"; fi
echo

echo "[repo]"
for f in pytest.ini .coveragerc Makefile tests/conftest.py; do
    if [ -f "$f" ]; then ok "$f"; else miss "$f"; fi
done
if [ -d "$SRC" ]; then ok "$SRC exists - the 80% coverage gate is ARMED"
else miss "$SRC does not exist yet - coverage gate is announced but skipped"; fi
if [ -n "$(find firmware -name '*.c' -print -quit 2>/dev/null)" ]; then ok "firmware C sources present"
else miss "no firmware/**/*.c yet - the docs-vs-firmware pin check will xfail"; fi
if [ -f experiments/REGISTRY.md ]; then ok "experiments/REGISTRY.md"
else miss "experiments/REGISTRY.md - a result not persisted did not happen"; fi
echo

echo "[bench: logic analyser]"
if command -v sigrok-cli >/dev/null 2>&1; then
    ok "sigrok-cli $(sigrok-cli --version 2>/dev/null | head -1 | tr -d '\n')"
    info "scanning for the FX2 (0925:3881)..."
    sigrok-cli --driver fx2lafw --scan 2>&1 | sed 's/^/       /'
    info "sample rates are DISCOVERED from --show, never hardcoded:"
    # Print the whole samplerate BLOCK, not just its heading: the rates are
    # indented children of that line, and `grep samplerate` matched only the
    # heading - so the one number this section exists to reveal was the one
    # number it never printed.
    sigrok-cli --driver fx2lafw --show 2>&1 \
        | awk '/^ *samplerate - /{show=1; print; next} show && /^      /{print; next} show{exit}' \
        | sed 's/^/       /'
else
    miss "sigrok-cli not found - headless capture unavailable"
fi
if command -v lsusb >/dev/null 2>&1; then
    if lsusb 2>/dev/null | grep -qi '0925:3881'; then ok "FX2 analyser 0925:3881 is plugged in"
    else miss "FX2 analyser 0925:3881 not on the USB bus"; fi
fi
if [ -f /etc/udev/rules.d/60-libsigrok.rules ]; then ok "udev rule 60-libsigrok.rules present"
else miss "no /etc/udev/rules.d/60-libsigrok.rules - see memory/logic-analyser.md"; fi
echo

echo "[bench: Pico link]"
found=""
for dev in /dev/ttyACM0 /dev/ttyACM1 /dev/ttyUSB0 /dev/ttyUSB1; do
    [ -e "$dev" ] && { ok "serial device $dev"; found=1; }
done
[ -z "$found" ] && miss "no /dev/ttyACM* or /dev/ttyUSB* - Pico or FTDI not attached"
# Two different questions, two different fixes. `id -nG` reports THIS PROCESS's
# groups; /etc/group reports the account's. A user added to dialout after the
# current login shows up in the second and not the first, and the fix for that
# is to log out and back in - not to run usermod again, which is what the old
# single message sent people off to do.
if id -nG 2>/dev/null | tr ' ' '\n' | grep -qx dialout; then
    ok "user is in the 'dialout' group"
elif id -nG "$(id -un)" 2>/dev/null | tr ' ' '\n' | grep -qx dialout; then
    miss "in 'dialout' but this LOGIN SESSION is not - log out and back in (or 'newgrp dialout')"
else
    miss "user not in 'dialout' - sudo usermod -aG dialout \"$(id -un)\", then log out and back in"
fi
echo

echo "[reminders]"
echo "  * NEVER probe AO1/AO2/BO1/BO2 - motor voltage destroys the analyser."
echo "  * Encoder supply is 3.3 V, never 5 V. RP2350 is not 5 V tolerant."
echo "  * Confirm the motor pair by resistance before connecting: red-white a few"
echo "    ohms, black-blue open. An evening was already lost to this."
echo "  * ticks_per_rev is UNMEASURED (11 vs 14 disputed). Do not let a default in."
echo
echo "doctor finished (always exit 0 - this is a report, not a gate)."
exit 0
