# Agent bootstrap prompt

**Source document, preserved verbatim.** Given by the owner on **2026-08-31**
as the bootstrap first-LLM instruction for this project (to be used after
installing BMAD).

This is the *original*. It has been applied to
[`CLAUDE.md`](../CLAUDE.md) under `# Operating modes`, where it is the
operative version. Keep this copy as the source of truth for the wording — it
is reusable as a bootstrap prompt for other repositories.

> **Note on the "First actions" section:** items 1–4 were carried out on
> 2026-08-31 with four deviations, recorded in
> [`memory/2026-08-31-first-motor-under-pico.md`](../memory/2026-08-31-first-motor-under-pico.md).
> Most significantly, CLAUDE.md was **appended to, not overwritten**, and the
> scaffolds were populated rather than left empty. BMAD was **not** installed —
> no authorisation was given.

---

## Modes (always on):

**Ponytail (`/ponytail full`)** — laziest solution that works. YAGNI. Shortest
working diff.

**BMAD** — planning via `bmad-agent-pm` (John); build via `create-story` →
`dev-story`. PRD → epics/stories → one story per fresh conversation.

**Memory (in-repo, local — never external):** `./memory/` is a small wiki, not
just a log — two layers:

- **Log layer:** one markdown file per conversation (`YYYY-MM-DD-<slug>.md`:
  decisions, learnings, open threads — skip what git/code already records).
  Append-only history, never rewritten.
- **Wiki layer:** undated topic pages (`<topic-slug>.md`) for anything that
  recurs across sessions — an entity, an evolving decision, a subsystem's
  quirks. When a session learns something about an existing topic, REVISE that
  page in place (the day's log entry records that it happened). Knowledge
  compounds by revision — one fact smeared across five dated files is rot, not
  memory. Cross-link pages with `[[wikilinks]]`.

`memory/MEMORY.md` is the index, one line per file, both layers. Read
MEMORY.md at every session start. Update the matching files each session;
delete memories that turn out wrong.

**Write-back rule:** if an answer took real digging this session, file it as a
memory page even when it isn't a gotcha — a synthesis not persisted did not
happen.

A page a human hand-edited carries `reviewed: true` frontmatter: revise around
it, never overwrite it wholesale.

**Memory/skill lint (the third verb, periodic):** ingest and query alone let any
knowledge base rot — lint is what keeps it honest. Every few sessions (or
whenever MEMORY.md stops fitting on one screen), run a lint pass over `memory/`
and `.claude/skills/`: contradictions between pages, stale claims, duplicates,
orphan pages nothing links to, dead `[[links]]`, skills that no longer match
reality. Merge, revise, or delete. Reactive deletion alone is not maintenance.

**Skills (continual internal development):** reusable skills live in
`.claude/skills/<name>/SKILL.md`. Before solving anything, check for an existing
skill and reuse it. When we learn something reusable (a gotcha, pattern,
convention), harvest it into a skill or memory file — never leave it only in
chat. Never write speculative skills; only hard-won ones.

**Architecture mapping (continual):** maintain `docs/ARCHITECTURE.md` — a living
map of key components, data flows, module boundaries, and their invariants.
Update it as part of finishing any story that adds or changes structure. It is
the map I read to orient before touching unfamiliar code.

**Task management:** track story state in the BMAD sprint files; keep a
`NEXT-STEPS.md` handoff so a fresh session knows the current state, blockers,
and open threads. Assume the next session knows nothing not written down.

**Definition of done — self-review gate.** Before calling any non-trivial code
done, run an adversarial pass myself: (1) trust boundaries / malformed input,
(2) entity-set completeness vs ground truth, (3) verification honesty (no
assumed-as-verified labels), (4) regression risk (run the FULL suite),
(5) output integrity, (6) failure modes. External review should find nits, not
criticals.

**Testing discipline:** unit + a growing e2e suite + a regression test per bug
fixed, in every change. Run the full suite before declaring done.

**CLOSE ritual — branch/story shutdown** (nothing is "done" until ALL of these).
A story or branch is not closeable on partial progress. Before declaring done
and handing off:

1. Self-review gate passed and the FULL test suite is green and recorded
   (paste/summarize the run — a suite you didn't run isn't green).
2. Status annotated in the epics/story file: DONE or partial, stating explicitly
   what changed vs. the acceptance criteria (not "done" as a bare word).
3. `ARCHITECTURE.md` updated to match reality — any new component, data flow, or
   invariant this branch added or moved is on the map, or the map is stale by
   definition.
4. `NEXT-STEPS.md` rewritten for the next session: current state, what the next
   story needs to know, and every open thread. Assume the next session knows
   nothing not written down.
5. Memory ritual done: the day's `memory/YYYY-MM-DD-<slug>.md` + its `MEMORY.md`
   index line; any wiki-layer topic pages this session touched revised in place;
   hard-won gotchas harvested into skills (never speculative ones).
6. Memory lint (light): do today's learnings contradict, duplicate, or
   stale-date anything already in MEMORY.md or a skill? Reconcile now — revise
   or delete the old page, never add a new dated file beside a wrong one.
7. Any experiments/results have their registry rows written — a result not
   persisted did not happen.
8. Commit and push — and only then. The push carries the doc/memory updates in
   the same commit, never as an afterthought.

Skipping any item means the branch stays open. External review after close
should surface nits, not gaps I left in the handoff.

**Pre-push ritual:** (unchanged — this is enforced by the CLOSE ritual above;
the two are the same gate.)

---

## First actions now:

1. write all of the above into `CLAUDE.md`;
2. create `AGENTS.md` pointing to it;
3. scaffold empty `memory/MEMORY.md`, `docs/ARCHITECTURE.md`, `NEXT-STEPS.md`;
4. confirm, then wait for my first real task.
