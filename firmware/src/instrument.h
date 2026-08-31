/*
 * instrument.h — two GPIO pins whose only job is to be watched by the logic
 * analyser.
 *
 * WHY THESE EXIST
 * ---------------
 * Without them this bench measures a MOTOR. With them it measures a CONTROL
 * LOOP, which is the actual subject of Story 1.6. Software cannot honestly
 * measure its own timing: any measurement it makes of "how long did that
 * take" is taken with the same clock, in the same interrupt context, by the
 * same code that might be the thing going wrong. An external instrument
 * watching a pin has none of those problems.
 *
 *   GP20  LOOP_TICK      analyser D6 - toggles ONCE per control iteration
 *   GP21  COMPUTE_BUSY   analyser D7 - HIGH for the duration of the loop body
 *
 * HOW TO READ THEM (sigrok-cli, headless - PulseView is for human eyes only)
 * -------------------------------------------------------------------------
 * LOOP_TICK toggles once per iteration, so ONE EDGE PER ITERATION and a full
 * square-wave period is TWO iterations. At 100 Hz: edge-to-edge 10 ms, period
 * 20 ms. Reading the period as the loop rate is the obvious mistake and it
 * makes the loop look half as fast as it is.
 *
 *   - loop jitter    = spread of the edge-to-edge intervals. This is the
 *                      number that justifies "the Pico, not Linux, runs the
 *                      control loop": if it is not tight, the argument fails.
 *   - CPU headroom   = mean(COMPUTE_BUSY high time) / 10 ms. 5% means
 *                      nineteen times more work would still fit; 90% means
 *                      the next feature will start dropping ticks.
 *   - overrun        = COMPUTE_BUSY still high at the next LOOP_TICK edge.
 *                      The loop body did not finish inside its own period.
 *
 * Both pins are 3.3 V logic and safe to probe. Never probe AO1/AO2
 * (docs/WIRING.md §1 rule 4).
 */
#ifndef ROVER_INSTRUMENT_H
#define ROVER_INSTRUMENT_H

#include <stdint.h>

void instrument_init(void);

/* Call exactly once per control iteration. */
void instrument_loop_tick(void);

/* Bracket the loop body. begin() must be paired with end() on every path,
 * including early returns, or the duty-cycle measurement lies. */
void instrument_busy_begin(void);
void instrument_busy_end(void);

/* Longest observed loop-body duration in microseconds, as measured by the
 * firmware itself. Cross-check only: the analyser is the instrument of
 * record, and if these two disagree the analyser is right. */
uint32_t instrument_max_busy_us(void);

#endif /* ROVER_INSTRUMENT_H */
