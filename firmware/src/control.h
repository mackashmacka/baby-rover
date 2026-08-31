/*
 * control.h — pure control maths: PID, feedforward, unit conversion.
 *
 * *** HOST-PURE. NO PICO-SDK INCLUDES ARE PERMITTED IN control.h/control.c. ***
 *
 * Everything in here is a plain function of its arguments. That is deliberate:
 * the PID maths is the part most likely to be wrong and the part hardest to
 * debug on a moving robot, so it is the part that must be unit-testable on a
 * laptop with no hardware attached at all.
 *
 * Units are SI and stated in the identifier (CLAUDE.md conventions):
 *   omega_rad_s   angular velocity of the OUTPUT shaft, radians per second
 *   duty_frac     signed PWM duty, -1.0 .. +1.0; sign is direction
 *   dt_s          seconds
 */
#ifndef ROVER_CONTROL_H
#define ROVER_CONTROL_H

#include <stdbool.h>
#include <stdint.h>

/* ------------------------------------------------------------------ */
/* Small helpers, exposed because tests want them                     */
/* ------------------------------------------------------------------ */

/* Clamp v into [lo, hi]. NaN in => lo out (a NaN must never escape into a
 * duty cycle; coasting is the safe interpretation of "no idea"). */
float clampf(float v, float lo, float hi);

/* Wrapping difference between two encoder counts.
 *
 * The PIO counter is a free-running 32-bit value that wraps at +/-2^31. The
 * subtraction is done in uint32_t, where wraparound is defined behaviour, and
 * only then reinterpreted as signed. Doing it directly in int32_t would be
 * signed overflow, which is undefined behaviour in C. */
int32_t enc_count_delta(int32_t now, int32_t prev);

/* ------------------------------------------------------------------ */
/* Unit conversion                                                    */
/* ------------------------------------------------------------------ */

/* ticks in one control period -> output-shaft angular velocity.
 *
 *   omega_rad_s = (delta_ticks / ticks_per_output_rev) * 2*pi / dt_s
 *
 * ticks_per_output_rev is a MEASURED constant (docs/PLAN.md Story 1.4). The
 * datasheets disagree (11 vs 14 counts per motor revolution x 100:1 gearing
 * x 4 for x4 edge decoding), which is exactly why it is a parameter here and
 * not a literal buried in the loop.
 *
 * Returns 0 for a non-positive dt_s or ticks_per_output_rev rather than
 * dividing by zero. */
float ticks_to_omega_rad_s(int32_t delta_ticks, float dt_s,
                           float ticks_per_output_rev);

/* ------------------------------------------------------------------ */
/* Feedforward                                                        */
/* ------------------------------------------------------------------ */

/*
 * Feedforward is the open-loop guess: "to spin at omega, a motor like this
 * one usually needs about this much duty". The PID then only has to correct
 * the error in the guess, so it can run with smaller gains and still respond
 * quickly. Without it, the integrator has to do all the work of finding the
 * operating point, which is slow and is the main source of overshoot.
 *
 * The model is the simplest one that matches a brushed DC motor: a deadband
 * (below which static friction wins and the shaft does not move at all) plus
 * a straight line.
 *
 *      |duty| = deadband_duty + |omega| / omega_per_duty_rad_s
 *
 * Both numbers come from the Story 1.5 duty -> rad/s sweep, per motor. Until
 * that experiment has been run, `enabled` is false and this contributes
 * exactly zero. It is a slot, deliberately left empty, not a guess.
 */
struct ff_curve {
    float deadband_duty;         /* duty at which the shaft first turns */
    float omega_per_duty_rad_s;  /* slope of the linear region, rad/s per unit duty */
    bool  enabled;               /* false until Story 1.5 fills the numbers in */
};

void  ff_curve_init(struct ff_curve *c);
float ff_duty_for_omega(const struct ff_curve *c, float omega_rad_s);

/* ------------------------------------------------------------------ */
/* PID                                                                */
/* ------------------------------------------------------------------ */

struct pid_gains {
    float kp;
    float ki;   /* per second: the integral term accumulates ki * error * dt */
    float kd;   /* per second: the derivative term is kd * d(measurement)/dt */
};

struct pid_config {
    struct pid_gains gains;
    float out_min, out_max;   /* actuator limits, in duty_frac */
    float i_min,  i_max;      /* ANTI-WINDUP: hard limits on the integrator */
    float dt_s;               /* nominal control period */
};

struct pid_state {
    float integral;
    float prev_meas;
    bool  have_prev;          /* false until the first sample: no derivative
                               * can be formed from one point, and pretending
                               * otherwise produces a large bogus kick. */
};

void pid_config_init(struct pid_config *cfg, float dt_s);
void pid_reset(struct pid_state *st);

/*
 * One control iteration. Returns the duty_frac to command.
 *
 * Two named behaviours worth being able to explain out loud:
 *
 * 1. INTEGRAL WINDUP, and why the integrator is clamped.
 *    If the setpoint is unreachable - the wheel is against a wall, or the
 *    commanded speed is above what 6 V can deliver - the error never reaches
 *    zero, so the integrator keeps growing without limit. The output is
 *    already clamped at 1.0, so that growth changes nothing while the error
 *    persists. Then the obstacle is removed. Now the integrator holds a huge
 *    value that can only be discharged by an equally long period of OPPOSITE
 *    error, so the motor overshoots wildly and takes seconds to settle. That
 *    is windup. The fix used here is the simplest one that works: clamp the
 *    accumulator itself to [i_min, i_max], so the worst it can ever ask for
 *    is bounded. (The other standard fixes - conditional integration, i.e.
 *    stop integrating while the output is saturated, and back-calculation -
 *    are better behaved but need more state. Clamping first; measure; only
 *    then reach for the clever one.)
 *
 * 2. DERIVATIVE ON MEASUREMENT, not on error, and why.
 *    The textbook D term is kd * d(error)/dt. But error = setpoint - meas, so
 *    a step change in the setpoint makes d(error)/dt momentarily enormous and
 *    the output slams to its limit for one sample. That is "derivative kick".
 *    Since d(error)/dt = d(setpoint)/dt - d(meas)/dt and the setpoint is
 *    piecewise constant, differentiating the measurement alone gives the same
 *    damping with no kick. The sign flips out of the algebra, which is why
 *    the term below is NEGATIVE kd times the change in measurement.
 *
 * feedforward is added ahead of the clamp; pass 0.0f for none.
 */
float pid_step(const struct pid_config *cfg, struct pid_state *st,
               float setpoint, float measurement, float feedforward);

/* ------------------------------------------------------------------ */
/* Duty -> hardware                                                   */
/* ------------------------------------------------------------------ */

/* PWM wrap value for a requested frequency, given the peripheral clock and a
 * clock divider of 1. Returns 0 if the request is impossible (which the
 * caller must treat as a fatal configuration error rather than ignore).
 *
 * wrap = clk_hz / freq_hz - 1, and wrap+1 has to fit in the 16-bit counter -
 * so the largest wrap this returns is 0xFFFE, never 0xFFFF. See the comment in
 * control.c: wrap+1 is the compare level meaning 100% duty, and at 0xFFFF it
 * would overflow to 0 and turn full duty into full off. */
uint16_t pwm_wrap_for_freq(uint32_t clk_hz, uint32_t freq_hz);

/* Magnitude of duty -> PWM compare level.
 *
 * level = round(|duty| * (wrap + 1)), clamped to [0, wrap+1]. A level of
 * wrap+1 is "always above the counter", i.e. 100% on; a level of 0 is off.
 * NaN maps to 0. Host-testable on purpose: an off-by-one here is a silent
 * 0.013% duty error at 20 kHz that no scope reading would ever catch. */
uint16_t duty_to_pwm_level(float duty_abs, uint16_t wrap);

/* ------------------------------------------------------------------ */
/* TB6612FNG truth table                                              */
/* ------------------------------------------------------------------ */

/*
 * Per the TB6612FNG datasheet (and docs/HARDWARE.md §2.2):
 *
 *   IN1 IN2  PWM  STBY  OUT1 OUT2   mode
 *    H   H    -    H     L    L     short brake
 *    L   H    H    H     L    H     reverse (CCW)
 *    L   H    L    H     L    L     short brake   <-- note!
 *    H   L    H    H     H    L     forward (CW)
 *    H   L    L    H     L    L     short brake   <-- note!
 *    L   L    -    H    OFF  OFF    stop (coast)
 *    -   -    -    L    OFF  OFF    standby
 *
 * The two marked rows are the reason 20 kHz PWM produces a usable linear
 * duty->speed curve: during the PWM low phase the bridge brakes rather than
 * coasting ("slow decay"). Current keeps circulating through the winding and
 * the ripple is small.
 */
typedef enum {
    MOTOR_DIR_COAST   = 0,  /* IN1=0 IN2=0 - outputs high-Z, motor freewheels */
    MOTOR_DIR_FORWARD = 1,  /* IN1=1 IN2=0 */
    MOTOR_DIR_REVERSE = 2,  /* IN1=0 IN2=1 */
    MOTOR_DIR_BRAKE   = 3   /* IN1=1 IN2=1 - windings shorted, hard stop */
} motor_dir_t;

/* Decode a direction into the two logic levels. Pure, so the truth table can
 * be asserted line by line in a unit test instead of on a bench. */
void motor_dir_pins(motor_dir_t dir, bool *in1, bool *in2);

/* Direction implied by a signed duty. Exactly 0 (and NaN) means coast. */
motor_dir_t motor_dir_for_duty(float duty_frac);

#endif /* ROVER_CONTROL_H */
