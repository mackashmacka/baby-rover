/*
 * motor.h — TB6612FNG channel-A control for the motor under test.
 *
 * This HEADER is host-pure (it includes only control.h and the standard
 * library) so tests can include it. The IMPLEMENTATION, motor.c, is not: it
 * touches PWM and GPIO. The pure parts that are worth testing - the truth
 * table and the duty->register conversion - deliberately live in control.c,
 * not here, so a host test never has to link a single pico-sdk symbol.
 *
 * Wiring: docs/WIRING.md §2. Pin numbers: board_config.h.
 *   GP2 PWMA   GP3 AIN1   GP4 AIN2   GP5 STBY
 */
#ifndef ROVER_MOTOR_H
#define ROVER_MOTOR_H

#include <stdbool.h>
#include <stdint.h>

#include "control.h"   /* motor_dir_t, and it is host-pure */

/* Configure the PWM slice for PWM_FREQ_HZ and put every control line in a
 * safe state: STBY LOW (driver disabled, outputs high-Z), IN1=IN2=0, duty 0.
 *
 * The driver comes up DISABLED. A board that resets while driving must not
 * come back still driving, and STBY is the one line that guarantees it in
 * hardware rather than in software. */
void motor_init(void);

/* Drive with a signed duty in [-1.0, +1.0].
 *   sign      selects direction (positive = MOTOR_DIR_FORWARD)
 *   magnitude selects the PWM duty
 * Values outside the range are clamped; NaN coasts. Exactly 0.0 coasts. */
void motor_set_duty(float duty_frac);

/*
 * BRAKE and COAST ARE NOT THE SAME THING. This is the single most commonly
 * confused pair on an H-bridge, so both are explicit functions rather than a
 * flag, and the difference is spelled out here:
 *
 * motor_brake() - "short brake". IN1=1, IN2=1, so BOTH low-side transistors
 *   conduct and the two motor terminals are shorted together. A spinning
 *   motor is a generator; shorting it makes it drive current through its own
 *   winding resistance, and that current produces a torque OPPOSING the
 *   rotation. The shaft stops fast, and the rotational energy leaves as heat
 *   in the winding and the FETs. At speed this draws a large current spike -
 *   on a bigger motor that is exactly how drivers die. It is PROBABLY fine
 *   here, but "probably" is the honest word: the only figure available is the
 *   supplier's ~200 mA stall claim, which docs/HARDWARE.md §2.1 explicitly
 *   distrusts as suspiciously low and marks [MEASURE]. Until Story 1.5
 *   measures the real stall current, do not quote a headroom figure against
 *   the TB6612's 3.2 A peak - measure it with the PSU's current display, or
 *   brake from a low speed.
 *
 * motor_coast() - "stop". IN1=0, IN2=0, so all four transistors are off and
 *   both outputs are high impedance. The motor is electrically disconnected
 *   and freewheels; the only thing slowing it is friction. Nothing is
 *   dissipated in the driver.
 *
 * Which one you want:
 *   - Emergency stop, or holding position on a slope: BRAKE.
 *   - Ending a characterisation run, or measuring a coast-down curve to get
 *     at friction and inertia: COAST. Braking would destroy the measurement.
 *   - The failsafe uses COAST (see FAILSAFE.md for why).
 */
void motor_brake(void);
void motor_coast(void);

/* TB6612 STBY. LOW is an instant HARDWARE all-stop: outputs go high-Z
 * regardless of what IN1/IN2/PWM say, without any software agreeing to it. */
void motor_set_stby(bool enabled);

/* Current commanded state, for telemetry and for tests of the sequencing. */
float       motor_get_duty(void);
motor_dir_t motor_get_dir(void);

/* The PWM wrap actually programmed, so the measured PWM frequency can be
 * checked against the intended one instead of assumed. */
uint16_t motor_get_pwm_wrap(void);

#endif /* ROVER_MOTOR_H */
