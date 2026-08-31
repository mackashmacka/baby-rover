# Experiment registry

One row per experiment. **A result not persisted did not happen** — CLOSE
ritual item 7.

Add the row when the experiment is run, not later. Raw data goes in
`experiments/<id>/`, plots in `experiments/plots/`.

| ID | Date | Story | Question | Method | Result | Data |
|---|---|---|---|---|---|---|
| _(example)_ | 2026-08-31 | 1.1 | What is the 6F22's internal resistance? | Measure open-circuit V, then V under ~50 mA | 8.2 V → 7.7 V ⇒ **~10 Ω**. Unusable for motors | — |

## Planned

| ID | Story | Question |
|---|---|---|
| `power-sag` | 1.1 | Terminal voltage vs load current, for each candidate supply |
| `wiring-map` | 1.2 | Resistance/continuity of all 24 motor wires |
| `pwm-capture` | 1.3 | Actual duty, frequency and reversal deadtime on all 4 channels |
| `quad-phase` | 1.4 | Is A/B phasing correct in both directions? |
| `ticks-per-m` | 1.4 | Encoder ticks per metre, measured by pushing a known distance |
| `motor-char` | 1.5 | ⭐ Deadband, duty→rad/s, no-load and stall current, ×4 motors |
| `pid-step` | 1.6 | Step response before vs after tuning |
| `failsafe` | 2.1 | Measured time from last valid command to motors stopped |
| `gyro-drift` | 2.2 | Heading drift of an integrated stationary gyro over 10 min |
| `mag-motor` | 2.3 | ⭐ Heading error vs motor duty cycle |
| `gps-scatter` | 2.4 | Static fix scatter over 20 min; CEP |
| `fusion-ladder` | 2.5 | ⭐ Closure error on one course, at each of 4 fusion stages |
| `stop-distance` | 2.6 | Minimum stopping distance vs the D415's 0.45 m blind zone |
