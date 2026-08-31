/*
 * firmware_shim.c — a thin C shim so the test suite can drive the host-pure
 * firmware through ctypes.
 *
 * WHY A SHIM AND NOT ctypes.Structure MIRRORS
 * -------------------------------------------
 * Mirroring `struct rover_state` in Python means the tests carry a second,
 * hand-maintained copy of the struct layout. Add a field to the firmware and
 * every test silently reads the wrong offset — a test suite that lies is worse
 * than no test suite. Here the compiler owns the layout: Python only ever sees
 * an opaque byte buffer of `shim_*_size()` bytes and calls accessors.
 *
 * Only protocol.c and control.c are linked in. Both headers declare themselves
 * HOST-PURE with no pico-sdk includes; if that ever stops being true, this file
 * stops compiling, which is exactly the alarm we want.
 *
 * This file is part of the test harness (tests/), not of the firmware.
 */
#include <stddef.h>
#include <stdint.h>
#include <stdbool.h>

#include "protocol.h"
#include "control.h"

/* ---- rover_state ------------------------------------------------------ */

size_t shim_state_size(void) { return sizeof(struct rover_state); }

void shim_state_init(void *p) { protocol_state_init((struct rover_state *)p); }

int shim_handle(const char *line, char *out, size_t outlen, void *p)
{
    return (int)protocol_handle_line(line, out, outlen, (struct rover_state *)p);
}

#define ST ((const struct rover_state *)p)

float    shim_duty_frac(const void *p)            { return ST->duty_frac; }
int      shim_brake(const void *p)                { return ST->brake ? 1 : 0; }
int      shim_stby(const void *p)                 { return ST->stby_enabled ? 1 : 0; }
int      shim_pid_enabled(const void *p)          { return ST->pid_enabled ? 1 : 0; }
int      shim_pid_reset_requested(const void *p)  { return ST->pid_reset_requested ? 1 : 0; }
int      shim_reset_requested(const void *p)      { return ST->reset_requested ? 1 : 0; }
float    shim_setpoint(const void *p)             { return ST->setpoint_omega_rad_s; }
float    shim_kp(const void *p)                   { return ST->gains.kp; }
float    shim_ki(const void *p)                   { return ST->gains.ki; }
float    shim_kd(const void *p)                   { return ST->gains.kd; }
float    shim_ticks_per_rev(const void *p)        { return ST->ticks_per_output_rev; }
uint32_t shim_telem_hz(const void *p)             { return ST->telem_hz; }
uint32_t shim_ok_count(const void *p)             { return ST->ok_count; }
uint32_t shim_err_count(const void *p)            { return ST->err_count; }
uint32_t shim_failsafe_timeout_us(const void *p)  { return ST->failsafe_timeout_us; }
int      shim_failsafe_tripped(const void *p)     { return ST->failsafe_tripped ? 1 : 0; }

#undef ST

/* The control loop writes these; the parser only reads them back out via
 * ENC?. Tests need to be able to plant a value. */
void shim_set_enc(void *p, int32_t count, uint32_t t_us)
{
    struct rover_state *st = (struct rover_state *)p;
    st->enc_count = count;
    st->t_us = t_us;
}

/* ---- the byte-stream reader ------------------------------------------- */

size_t shim_reader_size(void) { return sizeof(struct proto_reader); }

void shim_reader_init(void *r)
{
    protocol_reader_init((struct proto_reader *)r, PROTO_SINK_USB);
}

int shim_reader_push(void *r, char c)
{
    return protocol_reader_push((struct proto_reader *)r, c) ? 1 : 0;
}

int shim_reader_handle(void *r, char *out, size_t outlen, void *p)
{
    return (int)protocol_reader_handle((struct proto_reader *)r, out, outlen,
                                       (struct rover_state *)p);
}

/* ---- telemetry --------------------------------------------------------- */

int shim_format_telem(char *out, size_t outlen, uint32_t t_us, int32_t count,
                      float duty_frac)
{
    return protocol_format_telem(out, outlen, t_us, count, duty_frac);
}

uint32_t shim_telem_divider(uint32_t telem_hz, uint32_t loop_hz)
{
    return telem_tick_divider(telem_hz, loop_hz);
}

/* ---- control.c passthroughs ------------------------------------------- */

float   shim_clampf(float v, float lo, float hi) { return clampf(v, lo, hi); }
int32_t shim_enc_delta(int32_t now, int32_t prev) { return enc_count_delta(now, prev); }

float shim_ticks_to_omega(int32_t delta_ticks, float dt_s, float tpr)
{
    return ticks_to_omega_rad_s(delta_ticks, dt_s, tpr);
}

uint16_t shim_pwm_wrap_for_freq(uint32_t clk_hz, uint32_t freq_hz)
{
    return pwm_wrap_for_freq(clk_hz, freq_hz);
}

uint16_t shim_duty_to_pwm_level(float duty_abs, uint16_t wrap)
{
    return duty_to_pwm_level(duty_abs, wrap);
}

int shim_motor_dir_for_duty(float duty_frac)
{
    return (int)motor_dir_for_duty(duty_frac);
}

void shim_motor_dir_pins(int dir, int *in1, int *in2)
{
    bool a = false, b = false;
    motor_dir_pins((motor_dir_t)dir, &a, &b);
    if (in1) { *in1 = a ? 1 : 0; }
    if (in2) { *in2 = b ? 1 : 0; }
}

/* ---- PID --------------------------------------------------------------- */

size_t shim_pidcfg_size(void) { return sizeof(struct pid_config); }
size_t shim_pidst_size(void)  { return sizeof(struct pid_state); }

void shim_pid_config_init(void *cfg, float dt_s)
{
    pid_config_init((struct pid_config *)cfg, dt_s);
}

void shim_pid_set_gains(void *cfg, float kp, float ki, float kd)
{
    struct pid_config *c = (struct pid_config *)cfg;
    c->gains.kp = kp;
    c->gains.ki = ki;
    c->gains.kd = kd;
}

void shim_pid_set_out_limits(void *cfg, float lo, float hi)
{
    struct pid_config *c = (struct pid_config *)cfg;
    c->out_min = lo;
    c->out_max = hi;
}

void shim_pid_set_i_limits(void *cfg, float lo, float hi)
{
    struct pid_config *c = (struct pid_config *)cfg;
    c->i_min = lo;
    c->i_max = hi;
}

void shim_pid_set_dt(void *cfg, float dt_s)
{
    ((struct pid_config *)cfg)->dt_s = dt_s;
}

void shim_pid_reset(void *st) { pid_reset((struct pid_state *)st); }

float shim_pid_step(const void *cfg, void *st, float setpoint,
                    float measurement, float feedforward)
{
    return pid_step((const struct pid_config *)cfg, (struct pid_state *)st,
                    setpoint, measurement, feedforward);
}

float shim_pid_integral(const void *st)
{
    return ((const struct pid_state *)st)->integral;
}

int shim_pid_have_prev(const void *st)
{
    return ((const struct pid_state *)st)->have_prev ? 1 : 0;
}

/* ---- feedforward ------------------------------------------------------- */

size_t shim_ff_size(void) { return sizeof(struct ff_curve); }

void shim_ff_init(void *c) { ff_curve_init((struct ff_curve *)c); }

void shim_ff_set(void *c, float deadband, float slope, int enabled)
{
    struct ff_curve *curve = (struct ff_curve *)c;
    curve->deadband_duty = deadband;
    curve->omega_per_duty_rad_s = slope;
    curve->enabled = enabled ? true : false;
}

float shim_ff_duty_for_omega(const void *c, float omega_rad_s)
{
    return ff_duty_for_omega((const struct ff_curve *)c, omega_rad_s);
}
