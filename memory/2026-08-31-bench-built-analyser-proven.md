# 2026-08-31 — Bench built analyser proven

## What happened

## Decisions

_Record why, not just what._

## Open threads

## Session log
- 14:46Z — Migrated all docs from the Pi to the laptop. Laptop is now canonical and a git repo, pushed public to github.com/mackashmacka/baby-rover. The Pi has NO hardware attached - Pico, FTDI and analyser are all on the laptop. Pi role deferred to Story 2.6.
- 14:46Z — DECISION: pico-sdk (C/CMake), not MicroPython. The characterisation firmware needs PWM, PIO quadrature and a serial protocol - that is ~80% of the production firmware, so MicroPython would mean building it twice. MicroPython 1.29 stays on the board for bench poking. Firmware builds clean to rover_bench.uf2 (105K).
- 14:46Z — ANALYSER PROVEN - the first capture this project has ever made. It was blocked on Windows waiting for a Zadig driver bind; on Linux it was one udev rule. Drove GP2 at 20 kHz / 50% with STBY held LOW (driver disabled, nothing could spin) and captured 240,000 samples at 24 MHz: 19,999.0 Hz, duty 50.00%, period jitter 10 ns. Data in experiments/bench-verify/.
- 14:46Z — CLOSED the 0.89 V open thread. A 50%-duty 3.3 V line reading 0.89 V on the DMM where ~1.65 V was expected: the analyser shows a clean 50.00% square, so the meter was failing to average a 20 kHz waveform. Hypothesis confirmed, instrument exonerated, meter blamed. This is the falsification test stated before the measurement, per memory/debugging-method.md.
- 14:46Z — MEASURED and previously undocumented: the analyser is 8 channels with 17 supported rates up to 48 MHz (24 MHz is the realistic 8-channel ceiling on an FX2). memory/logic-analyser.md recorded no maximum at all.
- 14:46Z — Added GP20 (LOOP_TICK) and GP21 (COMPUTE_BUSY) to the pin map. They cost two analyser channels and buy the loop-jitter and CPU-headroom measurements - the difference between characterising a motor and characterising a control system.
- 14:46Z — Three bugs found and fixed in the bench's own doctor, each with a regression test: (1) sigrok 0.5.2 prints sample rates one per indented line, not inline, so capabilities() reported 'no rates' against a healthy analyser - on the exact code path whose job is to refuse to guess a rate; (2) the fx2lafw firmware glob looked for *.fx2 but Ubuntu ships *.fw, so installed firmware read as missing; (3) a group granted by usermod but not yet active in this login was reported as 'not a member', advice for the wrong bug. Fixing (3) introduced a fourth: the fix read the real /etc/group from inside a unit test, making the result depend on who ran the suite. Now injected through the Environment seam.
- 14:46Z — Test suite: 1137 passing, 89.0% line coverage against an enforced 80% gate. No test needs hardware. Before anything touched a motor the suite had already caught a host/firmware wire-format disagreement (host sent DUTY <motor> <duty>, firmware expected SET <duty>) and a manifest bug that recorded a clean git tree as 'unknown'.
- 14:46Z — OPEN: only analyser D0 is physically probed - D1-D7 need hooking up before any real run. Motors 2-4 unwired. Bench supply still the VBUS stopgap. ticks-per-rev still unmeasured, so every rad/s figure is flagged unconverted until Story 1.4.
