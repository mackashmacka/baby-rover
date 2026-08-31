/*
 * instrument.c — see instrument.h for what these pins are for and how to
 * read them on the analyser.
 */
#include "instrument.h"

#include "hardware/gpio.h"
#include "pico/stdlib.h"

#include "board_config.h"

static uint32_t s_busy_start_us;
static uint32_t s_max_busy_us;

void instrument_init(void)
{
    gpio_init(PIN_LOOP_TICK);
    gpio_init(PIN_COMPUTE_BUSY);
    gpio_set_dir(PIN_LOOP_TICK, GPIO_OUT);
    gpio_set_dir(PIN_COMPUTE_BUSY, GPIO_OUT);
    gpio_put(PIN_LOOP_TICK, 0);
    gpio_put(PIN_COMPUTE_BUSY, 0);
    s_busy_start_us = 0;
    s_max_busy_us = 0;
}

void instrument_loop_tick(void)
{
    /* Single-register XOR: one bus write, no read-modify-write, so the pin
     * moves at a fixed offset from the start of the tick. A gpio_get() +
     * gpio_put() pair would add a variable read latency to the very quantity
     * being measured. */
    gpio_xor_mask(1u << PIN_LOOP_TICK);
}

void instrument_busy_begin(void)
{
    gpio_put(PIN_COMPUTE_BUSY, 1);
    s_busy_start_us = time_us_32();
}

void instrument_busy_end(void)
{
    uint32_t dt = time_us_32() - s_busy_start_us;   /* wrap-safe in unsigned */
    if (dt > s_max_busy_us && dt < 1000000u) {
        s_max_busy_us = dt;
    }
    gpio_put(PIN_COMPUTE_BUSY, 0);
}

uint32_t instrument_max_busy_us(void)
{
    return s_max_busy_us;
}
