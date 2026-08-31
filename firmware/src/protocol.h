/*
 * protocol.h — line-oriented ASCII command protocol for the bench firmware.
 *
 * *** HOST-PURE. NO PICO-SDK INCLUDES ARE PERMITTED IN protocol.h/protocol.c. ***
 *
 * The parser is a pure function of (line, state). It never reads a clock,
 * never touches a GPIO, and never prints. That makes the entire command
 * surface - including every malformed-input path - testable on a laptop:
 *
 *     protocol_handle_line("SET 0.5", out, sizeof out, &st);
 *
 * Contract, and it is absolute:
 *   - A well-formed command returns PROTO_OK, writes its reply into `out`,
 *     and may mutate `st`.
 *   - Anything else returns PROTO_ERR, writes "ERR <reason>" into `out`, and
 *     LEAVES `st` BIT-FOR-BIT UNCHANGED. Arguments are parsed and validated
 *     into locals first; state is committed only once every argument is
 *     known good. A half-applied command is a rover that does something
 *     nobody asked for. This is literal and testable: memcmp() the struct
 *     either side of a rejected line and it matches. The ok_count/err_count
 *     diagnostics are therefore maintained one layer up, by
 *     protocol_reader_handle(), and never by the parser itself.
 *   - The reply never contains a newline. The transport appends one.
 *
 * The caller (main.c) is responsible for the two things the parser cannot do
 * itself: resetting the failsafe timer when PROTO_OK comes back, and acting
 * on st->reset_requested.
 *
 * Commands are matched case-insensitively so a human at a serial terminal can
 * type `ping`. Arguments are separated by spaces or tabs; leading and
 * trailing whitespace and a trailing \r (CRLF terminals) are ignored.
 */
#ifndef ROVER_PROTOCOL_H
#define ROVER_PROTOCOL_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "control.h"   /* struct pid_gains - also host-pure */

/* Longest accepted command line, excluding the terminating newline. Anything
 * longer is rejected wholesale rather than silently truncated: a truncated
 * "SET 0.95" could become "SET 0.9" and quietly do the wrong thing. */
#define PROTO_MAX_LINE   96

/* Reply buffer size the caller should provide. The longest reply is
 * "PONG <version> <sha>"; 128 bytes leaves room for a long SHA. */
#define PROTO_MAX_REPLY 128

typedef enum {
    PROTO_ERR = 0,   /* malformed; state untouched; out holds "ERR <reason>" */
    PROTO_OK  = 1    /* accepted; failsafe timer should be reset by the caller */
} proto_result_t;

/* Where a reply or telemetry stream should be written. */
typedef enum {
    PROTO_SINK_USB  = 0,
    PROTO_SINK_UART = 1
} proto_sink_t;

/*
 * The complete commanded state of one motor bench. main.c owns exactly one of
 * these; the control loop reads it, the parser writes it.
 */
struct rover_state {
    /* --- actuator command ------------------------------------------- */
    float    duty_frac;             /* -1..+1, signed; sign is direction */
    bool     brake;                 /* true => windings shorted (not coast) */
    bool     stby_enabled;          /* TB6612 STBY line; false = hardware stop */

    /* --- closed loop -------------------------------------------------- */
    struct pid_gains gains;
    bool     pid_enabled;
    float    setpoint_omega_rad_s;  /* OUTPUT-shaft angular velocity */
    float    ticks_per_output_rev;  /* MEASURED constant, Story 1.4 */

    /* --- telemetry ---------------------------------------------------- */
    uint32_t telem_hz;              /* 0 = off */
    proto_sink_t telem_sink;        /* channel TELEM was last enabled on */
    proto_sink_t active_sink;       /* channel the line being parsed arrived
                                     * on; set by protocol_reader_handle() */

    /* --- feedback, refreshed by the control loop each tick ------------ */
    int32_t  enc_count;
    uint32_t t_us;
    float    omega_meas_rad_s;

    /* --- failsafe ------------------------------------------------------ */
    uint32_t last_valid_cmd_us;     /* set by main.c on every PROTO_OK */
    uint32_t failsafe_timeout_us;   /* configurable; default FAILSAFE_TIMEOUT_MS */
    bool     failsafe_tripped;

    /* --- housekeeping -------------------------------------------------- */
    bool     reset_requested;       /* RESET seen; main.c reboots after flush */
    bool     pid_reset_requested;   /* integrator/derivative must be cleared */
    uint32_t ok_count;
    uint32_t err_count;
};

/* Reset a state struct to safe power-on defaults: coasting, PID off, STBY
 * off, telemetry off. Everything that could move the motor is inert. */
void protocol_state_init(struct rover_state *st);

/* Identity strings. Functions rather than macros so a host test can build the
 * expected PING/ID reply from the same source the firmware uses. */
const char *protocol_fw_version(void);
const char *protocol_git_sha(void);
const char *protocol_board(void);

/*
 * Handle one complete line (WITHOUT its terminating newline).
 *
 * `in` must be a NUL-terminated C string. Because a C string cannot itself
 * carry an embedded NUL, that particular malformed input is detected one
 * layer down, by the reader below, which sees the raw bytes.
 *
 * Returns PROTO_OK or PROTO_ERR. Always writes a NUL-terminated reply into
 * `out` when outlen > 0.
 */
proto_result_t protocol_handle_line(const char *in, char *out, size_t outlen,
                                    struct rover_state *st);

/* ------------------------------------------------------------------ */
/* Byte-stream reader                                                 */
/* ------------------------------------------------------------------ */

/*
 * Accumulates bytes from a serial channel into lines. It exists so the two
 * failure modes that a line-at-a-time parser structurally cannot see - an
 * overlong line and an embedded NUL byte - are still caught and reported
 * rather than silently mangling a command.
 */
struct proto_reader {
    char     buf[PROTO_MAX_LINE + 1];
    size_t   len;
    bool     overflow;   /* the line exceeded PROTO_MAX_LINE */
    bool     had_nul;    /* a 0x00 byte arrived inside the line */
    proto_sink_t sink;   /* which channel this reader is draining */
};

void protocol_reader_init(struct proto_reader *r, proto_sink_t sink);
void protocol_reader_reset(struct proto_reader *r);

/* Feed one byte. Returns true when a complete line is ready to be handled.
 * A bare \r is discarded (CRLF terminals); \n terminates. */
bool protocol_reader_push(struct proto_reader *r, char c);

/* Handle the line the reader has assembled, applying the overlong/NUL checks
 * first, then delegating to protocol_handle_line(). Resets the reader. */
proto_result_t protocol_reader_handle(struct proto_reader *r, char *out,
                                      size_t outlen, struct rover_state *st);

/* ------------------------------------------------------------------ */
/* Telemetry                                                          */
/* ------------------------------------------------------------------ */

/* Format one streamed telemetry record: "T <t_us> <count> <duty_frac>".
 * Pure, so the wire format is pinned by a host test rather than by whatever
 * the Pi's parser happened to accept on the day. */
int protocol_format_telem(char *out, size_t outlen, uint32_t t_us,
                          int32_t count, float duty_frac);

/* How many control ticks between telemetry samples. 0 means "off".
 * The loop cannot emit faster than it runs, so any requested rate at or
 * above loop_hz collapses to one sample per tick. */
uint32_t telem_tick_divider(uint32_t telem_hz, uint32_t loop_hz);

/* ------------------------------------------------------------------ */
/* Limits, exposed so tests assert against the same numbers            */
/* ------------------------------------------------------------------ */

/* Sanity bound on the closed-loop setpoint, in output-shaft rad/s.
 *
 * ASSUMPTION (not measured): a 1:100 N20 will not exceed ~300 output rpm
 * (~31 rad/s), so 100 rad/s is roughly 3x any plausible real speed. This is
 * a guard against a typo or a units mix-up arriving over the wire, NOT a
 * calibration figure. Story 1.5 measures the real ceiling. */
#define PROTO_OMEGA_MAX_RAD_S 100.0f

/* Largest accepted TELEM rate. */
#define PROTO_TELEM_MAX_HZ 1000u

#endif /* ROVER_PROTOCOL_H */
