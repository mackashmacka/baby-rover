/*
 * motor.c — TB6612FNG hardware layer. The maths it uses lives in control.c
 * so that the maths can be tested without a Pico; this file is only the
 * plumbing between that maths and the peripherals.
 */
#include "motor.h"

#include <math.h>

#include "hardware/clocks.h"
#include "hardware/gpio.h"
#include "hardware/pwm.h"
#include "pico/stdlib.h"

#include "board_config.h"
#include "control.h"

static uint     s_slice;
static uint     s_chan;
static uint16_t s_wrap;
static bool     s_pwm_ok;   /* false until a valid 20 kHz wrap was programmed */
static float    s_duty;
static motor_dir_t s_dir;

/* Write the two direction lines. Ordering matters when reversing: setting
 * both LOW first passes through coast rather than through brake, which
 * avoids a momentary shoot-through-adjacent condition and keeps the
 * direction-change signature on the analyser unambiguous (docs/PLAN.md
 * Story 1.3 captures exactly this transition). */
static void apply_dir(motor_dir_t dir)
{
    bool in1 = false;
    bool in2 = false;

    motor_dir_pins(dir, &in1, &in2);

    if (dir != s_dir) {
        gpio_put(PIN_AIN1, 0);
        gpio_put(PIN_AIN2, 0);
    }
    gpio_put(PIN_AIN1, in1);
    gpio_put(PIN_AIN2, in2);
    s_dir = dir;
}

static void apply_level(float duty_abs)
{
    if (!s_pwm_ok) {
        return;   /* the slice was never configured; see motor_init() */
    }
    pwm_set_chan_level(s_slice, s_chan, duty_to_pwm_level(duty_abs, s_wrap));
}

void motor_init(void)
{
    /* --- direction and standby lines --- */
    gpio_init(PIN_AIN1);
    gpio_init(PIN_AIN2);
    gpio_init(PIN_STBY);
    gpio_set_dir(PIN_AIN1, GPIO_OUT);
    gpio_set_dir(PIN_AIN2, GPIO_OUT);
    gpio_set_dir(PIN_STBY, GPIO_OUT);
    gpio_put(PIN_AIN1, 0);
    gpio_put(PIN_AIN2, 0);
    gpio_put(PIN_STBY, 0);      /* driver DISABLED until asked otherwise */

    s_dir = MOTOR_DIR_COAST;
    s_duty = 0.0f;

    /* --- PWM --- */
    gpio_set_function(PIN_PWMA, GPIO_FUNC_PWM);
    s_slice = pwm_gpio_to_slice_num(PIN_PWMA);
    s_chan  = pwm_gpio_to_channel(PIN_PWMA);

    /* Derive the wrap from the live system clock rather than hardcoding
     * 7499 for 150 MHz: if the clock is ever changed, a hardcoded wrap
     * silently changes the PWM frequency and every duty measurement taken
     * against it becomes wrong without anything looking broken. */
    uint32_t clk_hz = clock_get_hz(clk_sys);
    s_wrap = pwm_wrap_for_freq(clk_hz, PWM_FREQ_HZ);
    if (s_wrap == 0u) {
        /* control.h says the caller must treat this as fatal, so treat it as
         * fatal. There is no wrap that gives PWM_FREQ_HZ at this clock, and
         * substituting some other wrap would silently move the PWM off 20 kHz
         * - possibly above the TB6612's switching spec - while every duty
         * measurement taken against it still looked plausible. Leave the slice
         * disabled and the driver in standby instead: motor_get_pwm_wrap()
         * reads 0, motor_set_duty() is inert, and motor_set_stby(true) refuses.
         * Unreachable at any sane system clock (150 MHz / 20 kHz = 7500). */
        s_pwm_ok = false;
        return;
    }
    s_pwm_ok = true;

    pwm_config cfg = pwm_get_default_config();
    pwm_config_set_clkdiv(&cfg, 1.0f);
    pwm_config_set_wrap(&cfg, s_wrap);
    pwm_init(s_slice, &cfg, false);
    pwm_set_chan_level(s_slice, s_chan, 0);
    pwm_set_enabled(s_slice, true);
}

void motor_set_duty(float duty_frac)
{
    /* NaN first: clampf() maps NaN to its lower bound, which for a signed
     * duty would be full reverse. "No idea" must mean coast, never -100%. */
    float d = isnan(duty_frac) ? 0.0f : clampf(duty_frac, -1.0f, 1.0f);

    motor_dir_t dir = motor_dir_for_duty(d);

    /* Level first, then direction, when slowing down; direction first when
     * speeding up. Simpler and adequate here: set the level to zero across
     * any direction change so the bridge never flips while conducting. */
    if (dir != s_dir) {
        pwm_set_chan_level(s_slice, s_chan, 0);
    }
    apply_dir(dir);
    apply_level(d < 0.0f ? -d : d);

    s_duty = d;
}

void motor_brake(void)
{
    /* Short brake: IN1=IN2=1. The datasheet marks PWM as don't-care in this
     * row, so the level is dropped to 0 to keep the analyser trace honest -
     * a high PWM line next to a braking bridge reads like a drive command. */
    pwm_set_chan_level(s_slice, s_chan, 0);
    apply_dir(MOTOR_DIR_BRAKE);
    s_duty = 0.0f;
}

void motor_coast(void)
{
    pwm_set_chan_level(s_slice, s_chan, 0);
    apply_dir(MOTOR_DIR_COAST);
    s_duty = 0.0f;
}

void motor_set_stby(bool enabled)
{
    /* Refuse to enable the H-bridge if the PWM was never configured (see
     * motor_init()). With no PWM the commanded duty means nothing, and an
     * enabled driver you cannot modulate is only a way to be surprised. */
    gpio_put(PIN_STBY, (enabled && s_pwm_ok) ? 1 : 0);
}

float       motor_get_duty(void)     { return s_duty; }
motor_dir_t motor_get_dir(void)      { return s_dir; }
uint16_t    motor_get_pwm_wrap(void) { return s_wrap; }
