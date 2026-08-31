/*
 * board_config.h — pin map and fixed timing constants for the Baby Rover
 *                  characterisation bench.
 *
 * HOST-PURE: this header contains only preprocessor constants. It has no
 * pico-sdk includes, so host unit tests can include it freely.
 *
 * Source of truth for the pin numbers: docs/WIRING.md §3 and §8. If a number
 * here disagrees with that file, WIRING.md wins and this file is a bug.
 *
 * SAFETY (docs/WIRING.md §1) — these are encoded as invariants in the code:
 *   - GP23/24/25/29 are wired to the CYW43439 internally and must never be
 *     used. Nothing in this file references them.
 *   - The encoder hall sensors run on 3.3 V. RP2350 GPIO is NOT 5 V tolerant.
 *   - The analyser never touches AO1/AO2/BO1/BO2 — those sit at motor voltage.
 *     Every pin listed here is 3.3 V logic and safe to probe.
 */
#ifndef ROVER_BOARD_CONFIG_H
#define ROVER_BOARD_CONFIG_H

/* ---- TB6612FNG, channel A, "motor under test" ------------------------- */
#define PIN_PWMA            2   /* PWM  -> TB6612 PWMA      analyser D0 */
#define PIN_AIN1            3   /* out  -> TB6612 AIN1      analyser D1 */
#define PIN_AIN2            4   /* out  -> TB6612 AIN2      analyser D2 */
#define PIN_STBY            5   /* out  -> TB6612 STBY      analyser D3 */

/* ---- Quadrature encoder (motor-shaft hall sensors) -------------------- */
/* Yellow and green are interchangeable at the connector. "A" is whichever
 * one is on the lower pin; if the count runs backwards, the sign is fixed in
 * firmware, NEVER by swapping wires (docs/WIRING.md §3). */
#define PIN_ENC_A          12   /* in   <- hall output      analyser D4 */
#define PIN_ENC_B          13   /* in   <- hall output      analyser D5 */

/* ---- Instrumentation outputs (see instrument.h) ----------------------- */
#define PIN_LOOP_TICK      20   /* out  toggles once per control iteration  D6 */
#define PIN_COMPUTE_BUSY   21   /* out  HIGH while the loop body computes   D7 */

/* ---- UART0 to the Pi (or an FTDI FT232R standing in for it) ----------- */
#define PIN_UART_TX         0   /* -> Pi GPIO15 RXD */
#define PIN_UART_RX         1   /* <- Pi GPIO14 TXD */
#define UART_BAUD      115200

/* ---- Timing ----------------------------------------------------------- */
#define PWM_FREQ_HZ     20000u  /* 20 kHz: above audible, well inside the
                                 * TB6612's switching capability. */
#define CONTROL_HZ        100u  /* 100 Hz control loop. */
#define CONTROL_PERIOD_US (1000000u / CONTROL_HZ)   /* 10000 us */

/* ---- Failsafe (docs/PLAN.md Story 2.1, CLAUDE.md "non-negotiable") ---- */
/* If no valid command arrives inside this window the motor is coasted.
 * 300 ms sits in the middle of the 200-500 ms band the architecture doc
 * specifies. Runtime-configurable via rover_state.failsafe_timeout_us. */
#define FAILSAFE_TIMEOUT_MS  300u

#endif /* ROVER_BOARD_CONFIG_H */
