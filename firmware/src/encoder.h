/*
 * encoder.h — signed 32-bit quadrature count from the PIO decoder.
 *
 * The header is host-pure; encoder.c is not (it loads the PIO program and
 * reads the FIFO). There is no maths in here worth testing on the host: the
 * only arithmetic on encoder counts is enc_count_delta() and
 * ticks_to_omega_rad_s(), both of which live in control.c.
 *
 * See quadrature.pio for how the decode actually works.
 */
#ifndef ROVER_ENCODER_H
#define ROVER_ENCODER_H

#include <stdbool.h>
#include <stdint.h>

/* Load quadrature.pio into PIO0 and start state machine 0 on GP12/GP13.
 * Returns false if the program will not fit or the SM is already claimed.
 *
 * A false here is not fatal to the bench - open-loop duty sweeps (Story 1.5)
 * need no feedback - but it IS fatal to closed loop, because a control loop
 * with no feedback is an open-loop rover that thinks it is closed-loop. The
 * caller (main.c) refuses to run the PID in that state rather than letting the
 * integrator wind up against a measurement that is permanently zero. */
bool encoder_init(void);

/*
 * Latest count, signed, wrapping at +/-2^31.
 *
 * The absolute value is meaningless - the decoder starts from wherever the
 * shaft happened to be - and only DIFFERENCES between two reads matter. Use
 * enc_count_delta() for those so the wraparound is handled properly.
 *
 * Read this from ONE context only. The control ISR owns it; anything else
 * (the ENC? command, telemetry) reads the snapshot the ISR stored in
 * rover_state, because two readers draining the same RX FIFO would each get
 * a fraction of the values and both would be wrong.
 */
int32_t encoder_get_count(void);

/* Reset the software offset so the next read returns 0. Does not touch the
 * counter inside the state machine; there is no way to write to it without
 * stopping the SM, and stopping it would lose edges. */
void encoder_zero(void);

/* Diagnostic: how many times encoder_get_count() had no fresh count to hand
 * back - either it gave up waiting for the state machine and returned the
 * previous value, or the decoder never loaded at all and it returned zero.
 * Any non-zero value means the feedback is not real, and main.c reports it as
 * an unsolicited STALL line so a run taken in that state can be discarded. */
uint32_t encoder_stall_count(void);

#endif /* ROVER_ENCODER_H */
