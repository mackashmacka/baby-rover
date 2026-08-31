# FAILSAFE

`CLAUDE.md` calls this non-negotiable, and `docs/ARCHITECTURE.md` lists it as
an unimplemented invariant. This is where it is implemented.

## The rule

**If no valid command arrives within the timeout, the motor stops.**

Default timeout **300 ms** — the middle of the 200–500 ms band in
`docs/ARCHITECTURE.md` Boundary 1.

## Where it lives

| Piece | File | Line of reasoning |
|---|---|---|
| The timeout value | `src/board_config.h` — `FAILSAFE_TIMEOUT_MS` | compile-time default |
| The runtime value | `struct rover_state.failsafe_timeout_us` | configurable without a rebuild |
| The **pet** | `src/main.c` — `handle_ready_line()` | `last_valid_cmd_us = time_us_32()` on `PROTO_OK`, and **only** on `PROTO_OK` |
| The **check** | `src/main.c` — `control_tick()` | runs at 100 Hz inside the alarm interrupt |
| The **action** | `src/main.c` — `control_tick()` | `motor_coast()` then `motor_set_stby(false)`, **and the command is disarmed** |

## Five decisions, and why

**1. It is checked in the control interrupt, not in the main loop.**
The main loop can block: a USB CDC write with nothing reading the other end
stalls until it times out. If the failsafe lived there, the one failure that
most needs stopping the motor — the host going away — is exactly the failure
that would stop the failsafe from running. In the timer interrupt it keeps
running whatever the USB stack is doing.

**2. Only a well-formed command resets the timer.**
`protocol_handle_line()` returns `PROTO_OK` or `PROTO_ERR`; only `PROTO_OK`
pets the watchdog. Line noise on the UART must not count as the Pi being
alive. A blank line returns `ERR empty` for the same reason — it would
otherwise be a one-byte keepalive that proves nothing.

**3. The action is COAST, then STBY LOW — not brake.**
Coast because the link may have dropped while the rover is moving, and
braking a vehicle from an unknown speed is a bigger surprise than letting it
roll to a stop. Then `STBY` low, because that is a **hardware** all-stop: the
TB6612's outputs go high-impedance regardless of what `IN1`/`IN2`/`PWM` are
doing, so the stop does not depend on the rest of `control_tick()` being
correct.

**3a. The trip DISARMS the command; it does not merely zero the duty.**
The trip clears on the next `PROTO_OK` of any kind — a `PING` from a
monitoring script counts. If `pid_enabled` and `setpoint_omega_rad_s` survived
the trip, that `PING` would put `STBY` back high and the next tick would spin
the motor straight back up to its old setpoint, with nobody having asked for
motion. So the trip also clears `pid_enabled`, `setpoint_omega_rad_s`,
`brake` and `stby_enabled`. **Recovery is an explicit act: `STBY 1`, then
`SET` or `PIDEN`.** Same argument as booting with `STBY` low, applied to the
mid-drive case.

**4. It is tripped at boot.**
`protocol_state_init()` sets `failsafe_tripped = true`. Nothing has spoken to
the board yet, so "no valid command in the last 300 ms" is simply true. It
clears on the first good command. A board that powers up already driving is
the failure this avoids.

## How to prove it works

The claim is worthless without a measurement (`docs/PLAN.md` Story 2.1
acceptance: *"a test that pulls the cable mid-drive and shows the motors
stopping, on video, with the timeout measured on the analyser"*).

0. Use the **`docs/WIRING.md` §8** analyser map, not the §10.2 bench map:
   step 4 measures from the *last command byte*, which needs UART TX on D6.
   The §10.2 map has `LOOP_TICK`/`COMPUTE_BUSY` there instead and cannot see
   the wire. Record which map the capture was taken under.
1. Probe `PWMA` (GP2, D0) and `STBY` (GP5, D3) — never `AO1`/`AO2`.
2. `STBY 1`, then `SET 0.6`. Confirm PWM is switching and STBY is high.
3. Pull the USB or UART cable.
4. In the capture, measure the interval from the last command byte to the
   falling edge of `STBY`. It should be between one and two control periods
   past 300 ms, i.e. **300–320 ms**, because the check happens on a tick
   boundary rather than instantly.

If that interval is not what this file claims, this file is wrong and the
measurement is right.

## Known limits

- The timeout is checked at the control rate, so the worst-case latency is
  `timeout + one control period` = 310 ms nominal.
- There is no command to change `failsafe_timeout_us` over the wire — it is
  configurable in the sense of "one field, no rebuild of the logic", not in
  the sense of a runtime knob. Adding one is a protocol change and needs the
  host stream to agree.
- A wedged control interrupt would disable the failsafe with no external
  sign except `LOOP_TICK` (GP20) going quiet. The analyser sees that; the
  firmware cannot.
- The disarm in 3a means a host that reconnects mid-experiment does **not**
  resume the run. That is deliberate, and it means a capture containing a
  failsafe trip is a void run, not a run with a gap in it.
