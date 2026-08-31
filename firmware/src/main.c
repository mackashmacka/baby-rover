/*
 * main.c — Baby Rover motor-characterisation bench firmware.
 *
 * ONE motor at a time (docs/PLAN.md Story 1.5 runs the identical experiment
 * on each of the four in turn), driven over a line-oriented ASCII protocol.
 *
 * SHAPE OF THE PROGRAM
 * --------------------
 * Two contexts, and the split is the whole design:
 *
 *   control_tick()   runs in a hardware alarm interrupt at CONTROL_HZ.
 *                    Reads the encoder, runs the PID, writes the PWM, checks
 *                    the failsafe, drops a telemetry sample in a queue.
 *                    Never prints, never blocks, never waits on USB.
 *
 *   main()           runs everything else: draining the USB and UART byte
 *                    streams, parsing commands, printing replies and
 *                    telemetry. All of that CAN block - a USB CDC write with
 *                    no host reading it stalls until it times out - and none
 *                    of it is allowed to delay a control tick.
 *
 * Putting the loop in the interrupt is what makes the failsafe trustworthy:
 * if the USB stack wedges for half a second, the control loop keeps running,
 * notices that no command has arrived, and stops the motor. A loop that lived
 * in the same thread as the I/O would be wedged too, and the motor would keep
 * spinning. See FAILSAFE.md.
 *
 * The two contexts share exactly one object, `g_state`. Every access to it
 * from main() is inside a critical section (interrupts off for a few
 * microseconds) so the ISR cannot observe a half-applied command. The
 * telemetry queue is the other direction and needs no lock: one producer
 * (the ISR), one consumer (main), and a power-of-two ring.
 */
#include <stdio.h>
#include <string.h>

#include "hardware/sync.h"
#include "hardware/uart.h"
#include "hardware/watchdog.h"
#include "pico/stdlib.h"

#include "board_config.h"
#include "control.h"
#include "encoder.h"
#include "instrument.h"
#include "motor.h"
#include "protocol.h"

/* ------------------------------------------------------------------ */
/* Shared state                                                       */
/* ------------------------------------------------------------------ */

static struct rover_state g_state;

/* PID working state. Lives here, not in rover_state, because rover_state is
 * the host-testable command state and the integrator is not part of it. */
static struct pid_config s_pid_cfg;
static struct pid_state  s_pid;
static struct ff_curve   s_ff;

static int32_t s_prev_count;
static bool    s_have_prev_count;

/* Did the PIO decoder actually load? If not there is NO feedback, and closed
 * loop must be refused rather than run against a measurement of zero. See the
 * guard in control_tick(). */
static bool    s_encoder_ok;

/* ------------------------------------------------------------------ */
/* Telemetry queue: ISR produces, main consumes                       */
/* ------------------------------------------------------------------ */

#define TELEM_RING 32u   /* power of two */

struct telem_sample {
    uint32_t t_us;
    int32_t  count;
    float    duty_frac;
    uint8_t  sink;
};

static struct telem_sample s_telem[TELEM_RING];
static volatile uint32_t   s_telem_head;   /* written by the ISR  */
static volatile uint32_t   s_telem_tail;   /* written by main()   */
static volatile uint32_t   s_telem_dropped;
static uint32_t            s_telem_ctr;

static void telem_push(uint32_t t_us, int32_t count, float duty, uint8_t sink)
{
    uint32_t head = s_telem_head;
    uint32_t next = (head + 1u) & (TELEM_RING - 1u);

    if (next == s_telem_tail) {
        /* Full: the host is not draining fast enough. Drop the NEW sample
         * and count it. Dropping silently would make a gap in a dataset look
         * like a control glitch, which is a debugging trap; the count is
         * reported so a run with drops can be thrown away honestly. */
        s_telem_dropped++;
        return;
    }
    s_telem[head].t_us = t_us;
    s_telem[head].count = count;
    s_telem[head].duty_frac = duty;
    s_telem[head].sink = sink;
    s_telem_head = next;
}

/* ------------------------------------------------------------------ */
/* Output                                                             */
/* ------------------------------------------------------------------ */

static void emit_line(proto_sink_t sink, const char *s)
{
    if (sink == PROTO_SINK_UART) {
        uart_puts(uart0, s);
        uart_putc(uart0, '\n');
    } else {
        printf("%s\n", s);
    }
}

/* ------------------------------------------------------------------ */
/* The control loop                                                   */
/* ------------------------------------------------------------------ */

static void control_tick(void)
{
    instrument_busy_begin();
    instrument_loop_tick();

    const uint32_t now_us = time_us_32();
    const int32_t  count  = encoder_get_count();

    /* --- feedback: ticks -> rad/s ------------------------------------ */
    int32_t delta = 0;
    if (s_have_prev_count) {
        delta = enc_count_delta(count, s_prev_count);
    }
    s_prev_count = count;
    s_have_prev_count = true;

    const float dt_s = (float)CONTROL_PERIOD_US * 1e-6f;
    const float omega = ticks_to_omega_rad_s(delta, dt_s,
                                             g_state.ticks_per_output_rev);

    g_state.enc_count = count;
    g_state.t_us = now_us;
    g_state.omega_meas_rad_s = omega;

    /* --- failsafe (FAILSAFE.md) -------------------------------------- */
    /* Unsigned subtraction, so this stays correct across the ~71 minute
     * wrap of time_us_32(). A signed comparison would trip once per wrap. */
    const uint32_t age_us = now_us - g_state.last_valid_cmd_us;
    if (age_us > g_state.failsafe_timeout_us) {
        g_state.failsafe_tripped = true;
    }

    if (g_state.pid_reset_requested) {
        pid_reset(&s_pid);
        g_state.pid_reset_requested = false;
    }

    /* --- act ---------------------------------------------------------- */
    if (g_state.failsafe_tripped) {
        /* Coast, not brake: the link may have dropped while the rover is
         * moving, and braking a moving vehicle from an unknown speed is a
         * bigger surprise than letting it roll to a stop. Then STBY LOW,
         * which is the hardware all-stop and does not depend on the rest of
         * this function being correct. */
        pid_reset(&s_pid);

        /* DISARM the whole command, do not merely zero the duty.
         *
         * The failsafe clears on the next PROTO_OK of ANY kind - a PING from a
         * monitoring script counts. If pid_enabled and setpoint_omega_rad_s
         * survived the trip, that PING would put STBY back high and the very
         * next tick would spin the motor back up to its old setpoint, with
         * nobody having asked for motion. Coming back from a link failure has
         * to be an explicit act: STBY 1, then SET or PIDEN. Same argument as
         * booting with STBY LOW, applied to the mid-drive case. */
        g_state.duty_frac = 0.0f;
        g_state.brake = false;
        g_state.pid_enabled = false;
        g_state.setpoint_omega_rad_s = 0.0f;
        g_state.stby_enabled = false;

        motor_coast();
        motor_set_stby(false);
    } else {
        motor_set_stby(g_state.stby_enabled);

        if (g_state.pid_enabled && s_encoder_ok) {
            s_pid_cfg.gains = g_state.gains;
            const float ff = ff_duty_for_omega(&s_ff,
                                               g_state.setpoint_omega_rad_s);
            const float duty = pid_step(&s_pid_cfg, &s_pid,
                                        g_state.setpoint_omega_rad_s,
                                        omega, ff);
            g_state.duty_frac = duty;
            motor_set_duty(duty);
        } else if (g_state.pid_enabled) {
            /* Closed loop was asked for, but the decoder never loaded, so the
             * measurement is a constant zero. A PID fed a constant zero against
             * a non-zero setpoint winds its integrator to the clamp within a
             * few ticks and holds FULL duty forever, while the operator watches
             * a screen that says "closed loop". Refuse instead: coast, and let
             * encoder_get_count()'s stall counter say so on the wire once a
             * second. */
            g_state.duty_frac = 0.0f;
            motor_coast();
        } else if (g_state.brake) {
            motor_brake();
        } else {
            motor_set_duty(g_state.duty_frac);
        }
    }

    /* --- telemetry ---------------------------------------------------- */
    const uint32_t div = telem_tick_divider(g_state.telem_hz, CONTROL_HZ);
    if (div == 0u) {
        s_telem_ctr = 0u;
    } else {
        s_telem_ctr++;
        if (s_telem_ctr >= div) {
            s_telem_ctr = 0u;
            telem_push(now_us, count, g_state.duty_frac,
                       (uint8_t)g_state.telem_sink);
        }
    }

    instrument_busy_end();
}

static bool control_timer_cb(repeating_timer_t *t)
{
    (void)t;
    control_tick();
    return true;   /* keep repeating */
}

/* ------------------------------------------------------------------ */
/* Command handling                                                   */
/* ------------------------------------------------------------------ */

static struct proto_reader s_rd_usb;
static struct proto_reader s_rd_uart;

/* Returns true if a reboot was requested. */
static bool handle_ready_line(struct proto_reader *rd)
{
    char reply[PROTO_MAX_REPLY];
    proto_result_t res;
    bool reboot;

    /* Critical section: the parser mutates g_state field by field, and the
     * control ISR must never see it halfway through a command. A few
     * microseconds of interrupts-off is a few microseconds of loop jitter,
     * which is visible on GP20 and is the honest cost of sharing state. */
    const uint32_t save = save_and_disable_interrupts();

    res = protocol_reader_handle(rd, reply, sizeof(reply), &g_state);
    if (res == PROTO_OK) {
        /* THE failsafe reset. Only a well-formed command counts; garbage on
         * the wire must not keep the motor alive. */
        g_state.last_valid_cmd_us = time_us_32();
        g_state.failsafe_tripped = false;
    }
    reboot = g_state.reset_requested;

    restore_interrupts(save);

    emit_line(rd->sink, reply);
    return reboot;
}

/* Drain up to `budget` bytes so one chatty channel cannot starve the other
 * or delay telemetry output indefinitely. */
static bool pump_usb(uint32_t budget)
{
    bool reboot = false;

    while (budget-- > 0u) {
        int c = getchar_timeout_us(0);
        if (c == PICO_ERROR_TIMEOUT) {
            break;
        }
        if (protocol_reader_push(&s_rd_usb, (char)c)) {
            if (handle_ready_line(&s_rd_usb)) {
                reboot = true;
            }
        }
    }
    return reboot;
}

static bool pump_uart(uint32_t budget)
{
    bool reboot = false;

    while (budget-- > 0u && uart_is_readable(uart0)) {
        char c = (char)uart_getc(uart0);
        if (protocol_reader_push(&s_rd_uart, c)) {
            if (handle_ready_line(&s_rd_uart)) {
                reboot = true;
            }
        }
    }
    return reboot;
}

/*
 * Two silent failures made loud.
 *
 * A dropped telemetry sample leaves a gap in a dataset that looks exactly
 * like a control glitch, and a stalled encoder read leaves the control loop
 * running on stale feedback. Both are invisible from the host unless the
 * firmware says so. Rate-limited to one line per second per condition so a
 * sustained overload cannot flood the very channel that is already
 * struggling.
 */
static void pump_health(void)
{
    static uint32_t last_drop;
    static uint32_t last_stall;
    static uint32_t last_report_us;
    char line[PROTO_MAX_REPLY];

    const uint32_t drops  = s_telem_dropped;
    const uint32_t stalls = encoder_stall_count();

    if (drops == last_drop && stalls == last_stall) {
        return;
    }
    if ((uint32_t)(time_us_32() - last_report_us) < 1000000u) {
        return;
    }
    last_report_us = time_us_32();

    if (drops != last_drop) {
        last_drop = drops;
        snprintf(line, sizeof(line), "DROP %lu", (unsigned long)drops);
        emit_line(g_state.telem_sink, line);
    }
    if (stalls != last_stall) {
        last_stall = stalls;
        snprintf(line, sizeof(line), "STALL %lu", (unsigned long)stalls);
        emit_line(g_state.telem_sink, line);
    }
}

static void pump_telemetry(void)
{
    char line[PROTO_MAX_REPLY];

    while (s_telem_tail != s_telem_head) {
        struct telem_sample s = s_telem[s_telem_tail];
        s_telem_tail = (s_telem_tail + 1u) & (TELEM_RING - 1u);

        if (protocol_format_telem(line, sizeof(line), s.t_us, s.count,
                                  s.duty_frac) > 0) {
            emit_line((proto_sink_t)s.sink, line);
        }
    }
}

/* ------------------------------------------------------------------ */
/* Entry point                                                        */
/* ------------------------------------------------------------------ */

int main(void)
{
    repeating_timer_t timer;

    stdio_init_all();   /* USB CDC only: pico_enable_stdio_uart(0) in CMake */

    /* UART0 to the Pi, or to an FTDI FT232R standing in for it. Separate
     * from the USB debug channel on purpose (docs/ARCHITECTURE.md Boundary 1:
     * "flashing and monitoring must never contend for one port"). */
    uart_init(uart0, UART_BAUD);
    gpio_set_function(PIN_UART_TX, GPIO_FUNC_UART);
    gpio_set_function(PIN_UART_RX, GPIO_FUNC_UART);

    instrument_init();
    motor_init();          /* comes up with STBY LOW - driver disabled */

    protocol_state_init(&g_state);
    pid_config_init(&s_pid_cfg, (float)CONTROL_PERIOD_US * 1e-6f);
    pid_reset(&s_pid);
    ff_curve_init(&s_ff);   /* disabled until Story 1.5 measures the curve */

    protocol_reader_init(&s_rd_usb, PROTO_SINK_USB);
    protocol_reader_init(&s_rd_uart, PROTO_SINK_UART);

    s_encoder_ok = encoder_init();
    const bool enc_ok = s_encoder_ok;
    encoder_zero();
    s_prev_count = 0;
    s_have_prev_count = false;

    /* Negative period: the next callback is scheduled relative to the START
     * of the previous one, so a slow tick does not push every later tick
     * late. That is the difference between jitter and drift, and GP20 shows
     * which one you have. */
    if (!add_repeating_timer_us(-(int64_t)CONTROL_PERIOD_US,
                                control_timer_cb, NULL, &timer)) {
        /* No control loop means no failsafe. Refuse to run at all rather
         * than run something that looks alive and cannot stop the motor. */
        motor_coast();
        motor_set_stby(false);
        while (true) {
            printf("ERR fatal control_timer\n");
            sleep_ms(1000);
        }
    }

    if (!enc_ok) {
        /* Not fatal: open-loop characterisation (Story 1.5 deadband and
         * duty sweeps) is still valid without feedback. Say so loudly at boot,
         * and control_tick() additionally refuses to close the loop, so a
         * closed-loop run cannot be started by accident on a board whose
         * decoder never loaded. */
        printf("ERR encoder_init_failed\n");
    }

    while (true) {
        bool reboot = false;

        if (pump_usb(64u)) {
            reboot = true;
        }
        if (pump_uart(64u)) {
            reboot = true;
        }
        pump_telemetry();
        pump_health();

        if (reboot) {
            /* Stop first, acknowledge second, reboot third. The OK has
             * already been queued by handle_ready_line(); give the USB stack
             * a moment to actually push it before the world disappears. */
            motor_coast();
            motor_set_stby(false);
            stdio_flush();
            sleep_ms(50);
            watchdog_reboot(0, 0, 0);
            while (true) {
                tight_loop_contents();
            }
        }

        /* Nothing to do: sleep until the next interrupt (the control timer
         * at worst, 10 ms away) instead of spinning. Costs nothing here, and
         * on the rover it is the difference between a warm Pico and a cool
         * one. */
        __wfi();
    }
}
