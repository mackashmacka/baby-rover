---
name: close-ritual
description: How to close a Baby Rover story or session properly. Use when finishing a story, ending a session, before committing and pushing, or whenever tempted to say "done". Runs tools/session.py close and explains what each item actually means and how to fix a failing one.
---

# Closing a story or a session

`CLAUDE.md` defines the CLOSE ritual in prose. Prose is forgettable, so the
checkable parts are a command:

```bash
python3 tools/session.py close        # prompts for the human-judgement items
python3 tools/session.py close --yes  # asserts them without asking — see below
```

It prints `PASS` / `FAIL` / `PROMPT` per item and **exits non-zero if any item
is not passing**. Non-zero means the branch stays open. Not "mostly done".

Run it *before* you commit, not after: the doc and memory updates ride in the
same commit as the code, never as an afterthought.

## The rule the whole ritual exists for

> A story or branch is not closeable on partial progress.

The failure mode this prevents is the one that actually happens: the code works,
the session ends, and three weeks later nobody can say what was measured, why a
constant has the value it has, or which of two contradicting memory pages is
current. The rover survives that. The record — the thing that is half the point
of this project — does not.

## What each item means, and how to fix it

**1 · Full test suite green and recorded.** `close` runs `pytest` itself and
reads the tail of the output. "Recorded" is a separate check: today's memory
entry must mention the run. A suite you ran but did not write down is not
evidence. Fix:

```bash
make test
# --summary is required the FIRST time today's entry is created; -m alone
# exits 2 rather than filing an unindexed page.
python3 tools/session.py journal <slug> --summary "<what this session was>"
python3 tools/session.py journal <slug> -m "pytest: <paste the summary line>"
```

If `pytest` is not installed yet, this whole item degrades to a prompt — see
`tools/bench-setup.sh`. Do not paper over it; an unrunnable suite is open thread
material, not a pass.

**1b · Self-review gate.** Not mechanically checkable, so it prompts. Six
passes, and they are adversarial — you are looking for your own mistakes:
trust boundaries and malformed input; entity-set completeness against ground
truth (did you handle all four motors, all eight encoder channels, both
directions?); verification honesty (nothing labelled verified that was only
assumed); regression risk; output integrity; failure modes. External review
should find nits, not criticals.

**2 · Story status annotated.** In `docs/PLAN.md`, against the story: DONE or
partial, and *what changed versus the acceptance criteria*. "Done" as a bare
word is not an annotation — a story whose AC said "one CSV per motor and an
overlay plot" is not done with three CSVs and no plot, and next week you will
not remember which.

**3 · ARCHITECTURE.md matches reality.** Checked by comparing modification
times: if any source file is newer than `docs/ARCHITECTURE.md`, the map is stale
by definition. Either a component, data flow or invariant moved and the map
needs updating, or nothing structural changed — in which case touch the file and
say so in the commit. (Caveat, stated honestly: mtimes are rewritten by a fresh
clone, so on a clone this check is advisory. It is correct on the machine the
work happened on.)

**4 · NEXT-STEPS.md rewritten.** This is the session state. Three sub-checks:
the `**Last updated:**` field is today, the file was actually modified today,
and it still has a `**Current story:**` line and a `## Blockers` section.
Write it for a reader who knows *nothing* not written down — including which
machine the hardware is plugged into.

**5 · Memory ritual.** `memory/YYYY-MM-DD-<slug>.md` must exist for today and be
indexed in `memory/MEMORY.md`. Both happen through the tool:

The angle brackets below stay angle brackets until the bench fills them in.
**Counts per output revolution is unmeasured and disputed** —
`docs/HARDWARE.md` §2.1 and `NEXT-STEPS.md` blocker 1. Never paste an example
number out of a doc into a memory entry: a plausible figure written down once
becomes the constant everything downstream is silently scaled by.

```bash
python3 tools/session.py journal encoder-quadrature --summary "measured ticks/rev; A/B phasing confirmed"
python3 tools/session.py journal encoder-quadrature -m "<count> counts/output rev over <n> hand turns, ±<spread>"
```

**5b · Wiki pages revised in place, gotchas harvested.** Prompted, because only
you know what this session learned. The rule that matters: when a session learns
something about an existing topic, **revise that topic page** — do not file a
new dated page beside it. One fact smeared across five dated files is rot, not
memory. `tools/session.py wiki <topic>` shows a page's status, inbound and
outbound links, and refuses to overwrite anything a human marked
`reviewed: true`.

Harvest a skill only when something was genuinely hard-won. Speculative skills
are forbidden — they are confident-sounding guesses that later get trusted.

**6 · Memory lint (light).** This one is mechanical, so `close` runs it instead
of asking. If lint has findings you are asked whether they are reconciled.
Reconciling means revising, merging or deleting the wrong page — never adding a
new dated file next to it. See the `memory-lint` skill.

**7 · Experiments have registry rows with data files.** Checked both ways: every
non-example row in `experiments/REGISTRY.md` must name a data path that exists,
and every directory under `experiments/` that contains data must be referenced by
a row. A result not persisted did not happen. Write the row **when the
experiment runs**, not at close — by close it is already an archaeology problem.

**8 · Committed and pushed.** Clean working tree, an upstream branch, and
nothing unpushed. The laptop is canonical and there is no remote yet (blocker
in `NEXT-STEPS.md`), so this item will fail until GitHub exists. That failure is
correct: right now the repo has exactly one copy.

## When `--yes` is legitimate

`--yes` asserts every human-judgement item without prompting. It is honest only
when you have actually done them and are running non-interactively. If you find
yourself reaching for it to make the output green, you are lying to a future
reader — which is the one thing this repo's whole record discipline exists to
prevent. Prefer to leave the item failing and write the reason into
`NEXT-STEPS.md` as an open thread.

## Closing a session that did not finish its story

Normal, and not a failure. Do all of it anyway: the memory entry, the
`NEXT-STEPS.md` rewrite, the registry rows for anything measured. Annotate the
story "partial" with what is and is not done. Then say plainly, in
`NEXT-STEPS.md`, what the next session should pick up first.
