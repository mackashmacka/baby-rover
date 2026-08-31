# AGENTS.md

All agent instructions for this repository live in **[CLAUDE.md](CLAUDE.md)**.

Read it in full before acting. It contains, in order:

1. **Prime directive** — this is a learning project. A working rover the owner
   does not understand is a failure.
2. **Working agreement** — how to explain, how to build up to code, the
   division of labour between owner and agent.
3. **Architecture** — the Pi 5 / Pico 2 W split, and the failsafe rule.
4. **Hardware** — board-level detail, pin budgets, errata, component limits.
5. **Goals** — PID speed control, skid steering, height-based obstacle detection.
6. **Development environment** and **repo layout**.
7. **Conventions** — units, secrets, recording rationale.
8. **Operating modes** — Ponytail, BMAD, Memory, Skills, Architecture mapping,
   task management, definition of done, testing discipline, the CLOSE ritual.

Then, in order:

- **[NEXT-STEPS.md](NEXT-STEPS.md)** — current state, blockers, open threads.
  Read this second.
- **[docs/PLAN.md](docs/PLAN.md)** — the three-week roadmap: epics, stories,
  acceptance criteria, and what is deliberately out of scope. Read this third.
- **[docs/setup.md](docs/setup.md)** — the Day 0 setup runbook.
- **[docs/HARDWARE.md](docs/HARDWARE.md)** — ground-truth hardware spec: part
  numbers, ratings, derived constants. Stable; changes only when a part does.
- **[docs/WIRING.md](docs/WIRING.md)** — the living connection record: pin
  maps, harness colours, what is wired right now. Changes constantly.
- **[docs/career-track.md](docs/career-track.md)** — report, LinkedIn, CV and
  outreach. Runs in parallel with the build from Day 0, not at the end.
- **[experiments/REGISTRY.md](experiments/REGISTRY.md)** — one row per
  experiment. A result not persisted did not happen.
- **[BABY-ROVER.md](BABY-ROVER.md)** — full system audit of the Pi 5. Read
  before changing anything on that box.
- **[memory/MEMORY.md](memory/MEMORY.md)** — the memory index. Read at every
  session start.
- **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** — the living map of
  components, data flows and invariants.
- **[docs/project-state.md](docs/project-state.md)** — full hardware inventory
  and verified/unverified state as of 2026-08-31.
- **[docs/agent-bootstrap.md](docs/agent-bootstrap.md)** — the operating-modes
  spec as originally given, preserved verbatim. Reusable as a bootstrap prompt
  for other repositories. `CLAUDE.md` holds the operative version.
