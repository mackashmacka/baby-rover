/*
 * control.c — pure control maths. See control.h for the reasoning.
 *
 * *** HOST-PURE. Do not add a pico-sdk include to this file. ***
 * The only headers permitted here are C standard library headers.
 */
#include "control.h"

#include <math.h>

#define TWO_PI 6.283185307179586f

/* ------------------------------------------------------------------ */

float clampf(float v, float lo, float hi)
{
    /* NaN fails every comparison, so an explicit test is required: without
     * it a NaN would fall through both branches and be returned unchanged. */
    if (isnan(v)) {
        return lo;
    }
    if (v < lo) {
        return lo;
    }
    if (v > hi) {
        return hi;
    }
    return v;
}

int32_t enc_count_delta(int32_t now, int32_t prev)
{
    /* Do the subtraction in unsigned, where wraparound is defined, then
     * reinterpret. (int32_t)(uint32_t) of a value above INT32_MAX is
     * implementation-defined in C99 but two's-complement everywhere this
     * code will ever run; gcc documents it as a modular reduction. */
    uint32_t d = (uint32_t)now - (uint32_t)prev;
    return (int32_t)d;
}

/* ------------------------------------------------------------------ */

float ticks_to_omega_rad_s(int32_t delta_ticks, float dt_s,
                           float ticks_per_output_rev)
{
    if (!(dt_s > 0.0f)) {
        return 0.0f;
    }
    if (!(ticks_per_output_rev > 0.0f)) {
        return 0.0f;
    }
    float revs = (float)delta_ticks / ticks_per_output_rev;
    return revs * TWO_PI / dt_s;
}

/* ------------------------------------------------------------------ */

void ff_curve_init(struct ff_curve *c)
{
    if (!c) {
        return;
    }
    c->deadband_duty = 0.0f;
    c->omega_per_duty_rad_s = 0.0f;
    /* Disabled until Story 1.5 measures the curve for THIS motor. An
     * invented feedforward is worse than none: it biases every step
     * response and makes the PID tuning that follows meaningless. */
    c->enabled = false;
}

float ff_duty_for_omega(const struct ff_curve *c, float omega_rad_s)
{
    if (!c || !c->enabled) {
        return 0.0f;
    }
    if (!(c->omega_per_duty_rad_s > 0.0f)) {
        return 0.0f;
    }
    if (isnan(omega_rad_s) || omega_rad_s == 0.0f) {
        return 0.0f;
    }

    float mag = c->deadband_duty + (fabsf(omega_rad_s) / c->omega_per_duty_rad_s);
    mag = clampf(mag, 0.0f, 1.0f);
    return (omega_rad_s > 0.0f) ? mag : -mag;
}

/* ------------------------------------------------------------------ */

void pid_config_init(struct pid_config *cfg, float dt_s)
{
    if (!cfg) {
        return;
    }
    cfg->gains.kp = 0.0f;
    cfg->gains.ki = 0.0f;
    cfg->gains.kd = 0.0f;
    cfg->out_min = -1.0f;
    cfg->out_max = +1.0f;
    /* The integrator alone is allowed to ask for full duty, but never more.
     * Bounding it at the actuator limit is the cheapest anti-windup there
     * is: whatever happens, the stored history can never demand something
     * the hardware could not have delivered anyway. */
    cfg->i_min = -1.0f;
    cfg->i_max = +1.0f;
    cfg->dt_s = dt_s;
}

void pid_reset(struct pid_state *st)
{
    if (!st) {
        return;
    }
    st->integral = 0.0f;
    st->prev_meas = 0.0f;
    st->have_prev = false;
}

float pid_step(const struct pid_config *cfg, struct pid_state *st,
               float setpoint, float measurement, float feedforward)
{
    if (!cfg || !st) {
        return 0.0f;
    }
    if (isnan(feedforward)) {
        feedforward = 0.0f;
    }

    /* A garbage input must not be allowed to poison the stored integral or
     * the stored previous measurement; drop the sample and coast. */
    if (isnan(setpoint) || isnan(measurement)) {
        return 0.0f;
    }

    const float dt = cfg->dt_s;
    if (!(dt > 0.0f)) {
        /* Misconfigured period: the integral and derivative terms are both
         * meaningless. Fall back to feedforward only. */
        return clampf(feedforward, cfg->out_min, cfg->out_max);
    }

    const float error = setpoint - measurement;

    /* --- proportional --- */
    const float p = cfg->gains.kp * error;

    /* --- integral, with the accumulator clamped (see control.h) --- */
    st->integral += cfg->gains.ki * error * dt;
    st->integral = clampf(st->integral, cfg->i_min, cfg->i_max);

    /* --- derivative ON MEASUREMENT (see control.h) --- */
    float d = 0.0f;
    if (st->have_prev) {
        d = -cfg->gains.kd * (measurement - st->prev_meas) / dt;
    }
    st->prev_meas = measurement;
    st->have_prev = true;

    const float out = feedforward + p + st->integral + d;
    return clampf(out, cfg->out_min, cfg->out_max);
}

/* ------------------------------------------------------------------ */

uint16_t pwm_wrap_for_freq(uint32_t clk_hz, uint32_t freq_hz)
{
    if (freq_hz == 0u || clk_hz == 0u) {
        return 0u;
    }
    uint32_t top = clk_hz / freq_hz;   /* counts per PWM period */
    if (top == 0u) {
        return 0u;                     /* requested faster than the clock */
    }
    /* `top` itself - not `top - 1` - has to fit in 16 bits. duty_to_pwm_level()
     * uses wrap+1 as the compare level that means 100% duty, and that level is
     * a uint16_t: a wrap of 0xFFFF would make wrap+1 overflow to 0, so a
     * request for FULL duty would silently program full OFF. Capping wrap at
     * 0xFFFE costs one count of resolution at the very bottom of the usable
     * frequency range (~2.29 kHz at 150 MHz) and removes that whole class of
     * bug. */
    if (top > 0xFFFFu) {
        return 0u;                     /* will not fit the 16-bit counter */
    }
    return (uint16_t)(top - 1u);
}

uint16_t duty_to_pwm_level(float duty_abs, uint16_t wrap)
{
    /* (wrap + 1) is the number of counter states per period and the level that
     * means 100% duty. Computed in uint32_t: at wrap == 0xFFFF it does not fit
     * a uint16_t, and letting it wrap to 0 would turn "full duty" into "off".
     * pwm_wrap_for_freq() refuses to hand back 0xFFFF for exactly that reason;
     * this is the second line of defence, because this function is public and
     * a test (or a future caller) may pass any wrap at all. */
    const uint32_t period = (uint32_t)wrap + 1u;
    const uint32_t max_level = (period > 0xFFFFu) ? 0xFFFFu : period;

    if (isnan(duty_abs) || duty_abs <= 0.0f) {
        return 0u;
    }
    if (duty_abs >= 1.0f) {
        return (uint16_t)max_level;    /* strictly above the counter => 100% */
    }
    /* +0.5 for round-to-nearest rather than the truncation a plain cast
     * would give. */
    float level = duty_abs * (float)period + 0.5f;
    if (level > (float)max_level) {
        level = (float)max_level;
    }
    return (uint16_t)level;
}

/* ------------------------------------------------------------------ */

void motor_dir_pins(motor_dir_t dir, bool *in1, bool *in2)
{
    bool a = false;
    bool b = false;

    switch (dir) {
    case MOTOR_DIR_FORWARD: a = true;  b = false; break;  /* 10 */
    case MOTOR_DIR_REVERSE: a = false; b = true;  break;  /* 01 */
    case MOTOR_DIR_BRAKE:   a = true;  b = true;  break;  /* 11 */
    case MOTOR_DIR_COAST:                                  /* 00 */
    default:                a = false; b = false; break;
    }

    if (in1) {
        *in1 = a;
    }
    if (in2) {
        *in2 = b;
    }
}

motor_dir_t motor_dir_for_duty(float duty_frac)
{
    if (isnan(duty_frac)) {
        return MOTOR_DIR_COAST;
    }
    if (duty_frac > 0.0f) {
        return MOTOR_DIR_FORWARD;
    }
    if (duty_frac < 0.0f) {
        return MOTOR_DIR_REVERSE;
    }
    return MOTOR_DIR_COAST;
}
