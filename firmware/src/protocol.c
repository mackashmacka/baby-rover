/*
 * protocol.c — the command parser. See protocol.h for the contract.
 *
 * *** HOST-PURE. Do not add a pico-sdk include to this file. ***
 *
 * Structure of every handler, without exception:
 *      1. tokenise and validate ALL arguments into locals
 *      2. check there are no leftover tokens (arity)
 *      3. only then write to *st
 * Step 3 is unreachable if step 1 or 2 failed, which is how the "a malformed
 * command never changes state" guarantee is enforced structurally rather than
 * by remembering to be careful.
 */
#include "protocol.h"

#include <errno.h>
#include <inttypes.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "board_config.h"   /* host-pure: #defines only */

#ifndef FW_VERSION
#define FW_VERSION "0.1.0"
#endif

#ifndef FW_GIT_SHA
/* CMake overrides this with the real short SHA. "unknown" is what a host
 * unit-test build sees, and what a build outside a git checkout sees. */
#define FW_GIT_SHA "unknown"
#endif

#ifndef FW_BOARD
#define FW_BOARD "pico2w"
#endif

/*
 * ticks_per_output_rev PLACEHOLDER.
 *
 * docs/HARDWARE.md §2.1: Adafruit says 14 counts per motor revolution,
 * retailer listings say 11; times the 1:100 gearbox that is 1100-1400 per
 * output revolution, "possibly 2x or 4x that depending on whether the decoder
 * counts one edge or all four". This decoder is x4 (quadrature.pio counts
 * every edge), so the doc-consistent upper figure is 14 * 4 * 100 = 5600.
 *
 * It is a PLACEHOLDER and every rad/s number the firmware reports is wrong by
 * whatever factor it is wrong by. Story 1.4 settles it by hand-turning the
 * output shaft one revolution and reading ENC?. Nothing downstream should be
 * trusted until that has been done.
 */
#define DEFAULT_TICKS_PER_OUTPUT_REV 5600.0f

/* ------------------------------------------------------------------ */
/* Identity                                                           */
/* ------------------------------------------------------------------ */

const char *protocol_fw_version(void) { return FW_VERSION; }
const char *protocol_git_sha(void)    { return FW_GIT_SHA; }
const char *protocol_board(void)      { return FW_BOARD; }

/* ------------------------------------------------------------------ */
/* State                                                              */
/* ------------------------------------------------------------------ */

void protocol_state_init(struct rover_state *st)
{
    if (!st) {
        return;
    }
    memset(st, 0, sizeof(*st));

    st->duty_frac = 0.0f;
    st->brake = false;
    /* STBY starts LOW. The TB6612 outputs are high-Z until something
     * deliberately enables the driver, so a Pico that resets mid-drive
     * cannot come back up still driving. */
    st->stby_enabled = false;

    st->gains.kp = 0.0f;
    st->gains.ki = 0.0f;
    st->gains.kd = 0.0f;
    st->pid_enabled = false;
    st->setpoint_omega_rad_s = 0.0f;
    st->ticks_per_output_rev = DEFAULT_TICKS_PER_OUTPUT_REV;

    st->telem_hz = 0u;
    st->telem_sink = PROTO_SINK_USB;
    st->active_sink = PROTO_SINK_USB;

    st->enc_count = 0;
    st->t_us = 0u;
    st->omega_meas_rad_s = 0.0f;

    st->last_valid_cmd_us = 0u;
    st->failsafe_timeout_us = FAILSAFE_TIMEOUT_MS * 1000u;
    /* Tripped at boot: nothing has spoken to us yet, so "no valid command in
     * the last 300 ms" is literally true. It clears on the first good
     * command. The motor is coasting either way. */
    st->failsafe_tripped = true;

    st->reset_requested = false;
    st->pid_reset_requested = true;
    st->ok_count = 0u;
    st->err_count = 0u;
}

/* ------------------------------------------------------------------ */
/* Tokenising and scalar parsing                                      */
/* ------------------------------------------------------------------ */

#define TOK_MAX 32

static bool is_sep(char c)
{
    return c == ' ' || c == '\t' || c == '\r' || c == '\n';
}

/* Returns 1 = token produced, 0 = end of line, -1 = token longer than TOK_MAX. */
static int next_token(const char **p, char *tok, size_t toklen)
{
    const char *s = *p;
    size_t n = 0;

    while (*s != '\0' && is_sep(*s)) {
        s++;
    }
    if (*s == '\0') {
        *p = s;
        tok[0] = '\0';
        return 0;
    }
    while (*s != '\0' && !is_sep(*s)) {
        if (n + 1 >= toklen) {
            *p = s;
            tok[0] = '\0';
            return -1;
        }
        tok[n++] = *s++;
    }
    tok[n] = '\0';
    *p = s;
    return 1;
}

/* True if the rest of the line holds no further tokens. */
static bool at_end(const char *p)
{
    while (*p != '\0') {
        if (!is_sep(*p)) {
            return false;
        }
        p++;
    }
    return true;
}

static bool streq_ci(const char *a, const char *b)
{
    while (*a != '\0' && *b != '\0') {
        char ca = *a;
        char cb = *b;
        if (ca >= 'a' && ca <= 'z') { ca = (char)(ca - 'a' + 'A'); }
        if (cb >= 'a' && cb <= 'z') { cb = (char)(cb - 'a' + 'A'); }
        if (ca != cb) {
            return false;
        }
        a++;
        b++;
    }
    return *a == '\0' && *b == '\0';
}

/* Strict float: the whole token must be consumed, and NaN/Inf and C99
 * hex-float literals are all refused. strtof happily parses "nan", "inf" and
 * "0x1p3" (== 8.0); none of those belong in a duty cycle, and a NaN that
 * reached the PID would poison the integrator permanently. */
static bool parse_float(const char *tok, float *out)
{
    char *end = NULL;

    /* Hex floats are rejected by inspection rather than by strtof, which has
     * no way to switch them off. There is no legitimate way for "0x1p-3" to
     * arrive on this wire - it is a typo, a fuzzer, or a host library being
     * clever - and two spellings of the same duty is one more than a bench
     * protocol needs. */
    for (const char *h = tok; *h != '\0'; h++) {
        if (*h == 'x' || *h == 'X') {
            return false;
        }
    }

    errno = 0;
    float v = strtof(tok, &end);

    if (end == tok || end == NULL || *end != '\0') {
        return false;
    }
    if (isnan(v) || isinf(v)) {
        return false;
    }
    *out = v;
    return true;
}

/* Strict non-negative integer. Rejects "-1", "1.0", "1x" and empty. */
static bool parse_u32(const char *tok, uint32_t *out)
{
    char *end = NULL;

    if (tok[0] == '\0' || tok[0] == '-' || tok[0] == '+') {
        return false;
    }
    errno = 0;
    unsigned long v = strtoul(tok, &end, 10);
    if (end == tok || end == NULL || *end != '\0') {
        return false;
    }
    if (errno == ERANGE || v > 0xFFFFFFFFul) {
        return false;
    }
    *out = (uint32_t)v;
    return true;
}

/* ------------------------------------------------------------------ */
/* Reply helpers                                                      */
/* ------------------------------------------------------------------ */

/*
 * Neither of these touches *st. The "a rejected line changes nothing"
 * guarantee is only worth having if it is exact, so even the diagnostic
 * counters are left to protocol_reader_handle().
 */
static proto_result_t reply_err(char *out, size_t outlen, const char *reason)
{
    if (out && outlen > 0) {
        snprintf(out, outlen, "ERR %s", reason);
    }
    return PROTO_ERR;
}

static proto_result_t reply_ok(char *out, size_t outlen)
{
    if (out && outlen > 0) {
        snprintf(out, outlen, "OK");
    }
    return PROTO_OK;
}

/* ------------------------------------------------------------------ */
/* The parser                                                         */
/* ------------------------------------------------------------------ */

proto_result_t protocol_handle_line(const char *in, char *out, size_t outlen,
                                    struct rover_state *st)
{
    char tok[TOK_MAX];
    char cmd[TOK_MAX];

    if (!st) {
        if (out && outlen > 0) {
            snprintf(out, outlen, "ERR internal");
        }
        return PROTO_ERR;
    }
    if (!in) {
        return reply_err(out, outlen, "internal");
    }
    /* Defence in depth: protocol_reader_handle() already rejects overlong
     * lines, but this function is public and a host test may call it with
     * anything at all. */
    if (strlen(in) > PROTO_MAX_LINE) {
        return reply_err(out, outlen, "line_too_long");
    }

    const char *p = in;
    int rc = next_token(&p, cmd, sizeof(cmd));
    if (rc == 0) {
        /* A bare newline. Not an error worth acting on, but it is not a valid
         * command either, so it must NOT pet the failsafe watchdog. */
        return reply_err(out, outlen, "empty");
    }
    if (rc < 0) {
        return reply_err(out, outlen, "token_too_long");
    }

    /* ---------------- PING ---------------- */
    if (streq_ci(cmd, "PING")) {
        if (!at_end(p)) {
            return reply_err(out, outlen, "arity");
        }
        if (out && outlen > 0) {
            snprintf(out, outlen, "PONG %s %s",
                     protocol_fw_version(), protocol_git_sha());
        }
        return PROTO_OK;
    }

    /* ---------------- ID? ---------------- */
    if (streq_ci(cmd, "ID?")) {
        if (!at_end(p)) {
            return reply_err(out, outlen, "arity");
        }
        if (out && outlen > 0) {
            snprintf(out, outlen, "ID %s %s",
                     protocol_board(), protocol_fw_version());
        }
        return PROTO_OK;
    }

    /* ---------------- SET <duty_frac> ---------------- */
    if (streq_ci(cmd, "SET")) {
        float duty;

        rc = next_token(&p, tok, sizeof(tok));
        if (rc == 0) {
            return reply_err(out, outlen, "arity");
        }
        if (rc < 0) {
            return reply_err(out, outlen, "token_too_long");
        }
        if (!parse_float(tok, &duty)) {
            return reply_err(out, outlen, "not_a_number");
        }
        if (duty < -1.0f || duty > 1.0f) {
            return reply_err(out, outlen, "range");
        }
        if (!at_end(p)) {
            return reply_err(out, outlen, "arity");
        }

        /* Commit. SET is an OPEN-LOOP command, so it turns the PID off: if it
         * did not, the very next control tick would overwrite this duty with
         * the PID's output and SET would silently be a no-op. */
        st->duty_frac = duty;
        st->brake = false;
        st->pid_enabled = false;
        st->pid_reset_requested = true;
        return reply_ok(out, outlen);
    }

    /* ---------------- STOP ---------------- */
    if (streq_ci(cmd, "STOP")) {
        if (!at_end(p)) {
            return reply_err(out, outlen, "arity");
        }
        /* COAST. Outputs go high-Z and the motor freewheels down. */
        st->duty_frac = 0.0f;
        st->brake = false;
        st->pid_enabled = false;
        st->setpoint_omega_rad_s = 0.0f;
        st->pid_reset_requested = true;
        return reply_ok(out, outlen);
    }

    /* ---------------- BRAKE ---------------- */
    if (streq_ci(cmd, "BRAKE")) {
        if (!at_end(p)) {
            return reply_err(out, outlen, "arity");
        }
        /* SHORT BRAKE. Windings shorted; the motor's own back-EMF is dumped
         * into that short and it stops hard. Not the same as STOP. */
        st->duty_frac = 0.0f;
        st->brake = true;
        st->pid_enabled = false;
        st->setpoint_omega_rad_s = 0.0f;
        st->pid_reset_requested = true;
        return reply_ok(out, outlen);
    }

    /* ---------------- STBY <0|1> ---------------- */
    if (streq_ci(cmd, "STBY")) {
        uint32_t v;

        rc = next_token(&p, tok, sizeof(tok));
        if (rc == 0) {
            return reply_err(out, outlen, "arity");
        }
        if (rc < 0) {
            return reply_err(out, outlen, "token_too_long");
        }
        if (!parse_u32(tok, &v)) {
            return reply_err(out, outlen, "not_a_number");
        }
        if (v > 1u) {
            return reply_err(out, outlen, "range");
        }
        if (!at_end(p)) {
            return reply_err(out, outlen, "arity");
        }

        st->stby_enabled = (v == 1u);
        return reply_ok(out, outlen);
    }

    /* ---------------- ENC? ---------------- */
    if (streq_ci(cmd, "ENC?")) {
        if (!at_end(p)) {
            return reply_err(out, outlen, "arity");
        }
        /* The snapshot is whatever the last control tick stored: at most one
         * control period (10 ms) old. Reading the PIO FIFO from here instead
         * would race the control loop for the same FIFO. */
        if (out && outlen > 0) {
            snprintf(out, outlen, "ENC %" PRId32 " %" PRIu32,
                     st->enc_count, st->t_us);
        }
        return PROTO_OK;
    }

    /* ---------------- TELEM <hz> ---------------- */
    if (streq_ci(cmd, "TELEM")) {
        uint32_t hz;

        rc = next_token(&p, tok, sizeof(tok));
        if (rc == 0) {
            return reply_err(out, outlen, "arity");
        }
        if (rc < 0) {
            return reply_err(out, outlen, "token_too_long");
        }
        if (!parse_u32(tok, &hz)) {
            return reply_err(out, outlen, "not_a_number");
        }
        if (hz > PROTO_TELEM_MAX_HZ) {
            return reply_err(out, outlen, "range");
        }
        if (!at_end(p)) {
            return reply_err(out, outlen, "arity");
        }

        st->telem_hz = hz;
        /* Stream back to the channel this TELEM arrived on, so a command
         * typed at the USB console does not start spraying the Pi's UART. */
        st->telem_sink = st->active_sink;
        return reply_ok(out, outlen);
    }

    /* ---------------- PID <kp> <ki> <kd> ---------------- */
    if (streq_ci(cmd, "PID")) {
        float k[3];

        for (int i = 0; i < 3; i++) {
            rc = next_token(&p, tok, sizeof(tok));
            if (rc == 0) {
                return reply_err(out, outlen, "arity");
            }
            if (rc < 0) {
                return reply_err(out, outlen, "token_too_long");
            }
            if (!parse_float(tok, &k[i])) {
                return reply_err(out, outlen, "not_a_number");
            }
            /* Negative gains turn negative feedback into positive feedback.
             * There is no legitimate use for one here, and the failure mode
             * is a motor accelerating to its limit. */
            if (k[i] < 0.0f) {
                return reply_err(out, outlen, "range");
            }
        }
        if (!at_end(p)) {
            return reply_err(out, outlen, "arity");
        }

        st->gains.kp = k[0];
        st->gains.ki = k[1];
        st->gains.kd = k[2];
        /* Changing ki without clearing the accumulator would rescale all the
         * history that accumulated under the OLD gain and step the output. */
        st->pid_reset_requested = true;
        return reply_ok(out, outlen);
    }

    /* ---------------- PIDEN <0|1> ---------------- */
    if (streq_ci(cmd, "PIDEN")) {
        uint32_t v;

        rc = next_token(&p, tok, sizeof(tok));
        if (rc == 0) {
            return reply_err(out, outlen, "arity");
        }
        if (rc < 0) {
            return reply_err(out, outlen, "token_too_long");
        }
        if (!parse_u32(tok, &v)) {
            return reply_err(out, outlen, "not_a_number");
        }
        if (v > 1u) {
            return reply_err(out, outlen, "range");
        }
        if (!at_end(p)) {
            return reply_err(out, outlen, "arity");
        }

        st->pid_enabled = (v == 1u);
        st->pid_reset_requested = true;
        if (!st->pid_enabled) {
            /* Leaving closed loop leaves the last PID duty commanded, which
             * would be a surprise. Drop to coast and make the operator ask
             * again with SET. */
            st->duty_frac = 0.0f;
            st->brake = false;
        }
        return reply_ok(out, outlen);
    }

    /* ---------------- SETRPS <omega_rad_s> ---------------- */
    if (streq_ci(cmd, "SETRPS")) {
        float omega;

        rc = next_token(&p, tok, sizeof(tok));
        if (rc == 0) {
            return reply_err(out, outlen, "arity");
        }
        if (rc < 0) {
            return reply_err(out, outlen, "token_too_long");
        }
        if (!parse_float(tok, &omega)) {
            return reply_err(out, outlen, "not_a_number");
        }
        if (omega < -PROTO_OMEGA_MAX_RAD_S || omega > PROTO_OMEGA_MAX_RAD_S) {
            return reply_err(out, outlen, "range");
        }
        if (!at_end(p)) {
            return reply_err(out, outlen, "arity");
        }

        /* NOTE the unit. The command is spelled SETRPS for historical
         * reasons but the argument is rad/s of the OUTPUT shaft, not
         * revolutions per second. Mixed units are the leading cause of
         * control bugs (CLAUDE.md), so it is said here in the one place
         * someone reading the parser will look. */
        st->setpoint_omega_rad_s = omega;
        return reply_ok(out, outlen);
    }

    /* ---------------- RESET ---------------- */
    if (streq_ci(cmd, "RESET")) {
        if (!at_end(p)) {
            return reply_err(out, outlen, "arity");
        }
        /* Reply first, reboot second: main.c flushes the OK, then reboots
         * through the watchdog. Rebooting from inside the parser would eat
         * the acknowledgement. */
        st->reset_requested = true;
        return reply_ok(out, outlen);
    }

    return reply_err(out, outlen, "unknown_cmd");
}

/* ------------------------------------------------------------------ */
/* Byte-stream reader                                                 */
/* ------------------------------------------------------------------ */

void protocol_reader_init(struct proto_reader *r, proto_sink_t sink)
{
    if (!r) {
        return;
    }
    r->sink = sink;
    protocol_reader_reset(r);
}

void protocol_reader_reset(struct proto_reader *r)
{
    if (!r) {
        return;
    }
    r->len = 0;
    r->buf[0] = '\0';
    r->overflow = false;
    r->had_nul = false;
}

bool protocol_reader_push(struct proto_reader *r, char c)
{
    if (!r) {
        return false;
    }

    if (c == '\n') {
        r->buf[r->len] = '\0';
        return true;                   /* line complete, flags carried with it */
    }
    if (c == '\r') {
        return false;                  /* CRLF terminals: swallow the CR */
    }
    if (c == '\0') {
        /* An embedded NUL would terminate the C string early and silently
         * turn "SET 0.9\0 junk" into "SET 0.9". Record it and reject the
         * whole line rather than acting on the prefix. */
        r->had_nul = true;
        return false;
    }
    if (r->len >= PROTO_MAX_LINE) {
        /* Keep consuming until the newline so the REST of the overlong line
         * is not then parsed as a fresh command. */
        r->overflow = true;
        return false;
    }
    r->buf[r->len++] = c;
    return false;
}

proto_result_t protocol_reader_handle(struct proto_reader *r, char *out,
                                      size_t outlen, struct rover_state *st)
{
    proto_result_t res;

    if (!r || !st) {
        if (out && outlen > 0) {
            snprintf(out, outlen, "ERR internal");
        }
        return PROTO_ERR;
    }

    /* The parser needs to know which channel the line arrived on, but its
     * signature is fixed (host tests call it directly), so it is handed over
     * in the state struct rather than as an argument. */
    st->active_sink = r->sink;

    if (r->overflow) {
        res = reply_err(out, outlen, "line_too_long");
    } else if (r->had_nul) {
        res = reply_err(out, outlen, "embedded_nul");
    } else {
        r->buf[r->len] = '\0';
        res = protocol_handle_line(r->buf, out, outlen, st);
    }

    /* Diagnostic counters live at THIS layer, never in the parser, so that
     * protocol_handle_line() can promise a rejected line leaves the state
     * bit-for-bit identical. See protocol.h. */
    if (res == PROTO_OK) {
        st->ok_count++;
    } else {
        st->err_count++;
    }

    protocol_reader_reset(r);
    return res;
}

/* ------------------------------------------------------------------ */
/* Telemetry                                                          */
/* ------------------------------------------------------------------ */

int protocol_format_telem(char *out, size_t outlen, uint32_t t_us,
                          int32_t count, float duty_frac)
{
    if (!out || outlen == 0) {
        return -1;
    }
    if (isnan(duty_frac) || isinf(duty_frac)) {
        duty_frac = 0.0f;
    }
    return snprintf(out, outlen, "T %" PRIu32 " %" PRId32 " %.4f",
                    t_us, count, (double)duty_frac);
}

uint32_t telem_tick_divider(uint32_t telem_hz, uint32_t loop_hz)
{
    if (telem_hz == 0u || loop_hz == 0u) {
        return 0u;
    }
    if (telem_hz >= loop_hz) {
        return 1u;      /* cannot emit faster than the loop runs */
    }
    return loop_hz / telem_hz;
}
