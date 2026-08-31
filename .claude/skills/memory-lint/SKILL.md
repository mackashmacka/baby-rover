---
name: memory-lint
description: When and how to run the memory/skill lint pass — the third verb from the operating agreement. Use every few sessions, whenever memory/MEMORY.md stops fitting on one screen, before closing a story, or when two memory pages seem to disagree. Explains each finding kind from tools/session.py lint and what to do about it.
---

# The third verb: lint

Ingest and query alone let any knowledge base rot. Lint is what keeps it
honest. **Reactive deletion alone is not maintenance** — waiting until a wrong
page bites you means it has already bitten you.

```bash
python3 tools/session.py lint            # report; always exits 0
python3 tools/session.py lint --strict   # exit 1 when there are findings
```

It **reports and never deletes**. Merging and deleting are judgement calls with
consequences, and a script that quietly removes a memory page is a script that
loses knowledge.

## When to run it

- **Every few sessions.** The bootstrap document's own cadence.
- **Whenever `memory/MEMORY.md` stops fitting on one screen** — lint flags this
  itself at 60 non-blank lines. A long index means topics have fragmented and
  want merging.
- **As part of closing a story.** `tools/session.py close` runs it for you and
  asks whether the findings are reconciled (CLOSE item 6).
- **The moment two pages seem to disagree.** Do not wait for the cadence.

## The finding kinds, and what to do with each

**`dead-link`** — a `[[wikilink]]` pointing at a page that does not exist.
Either the page was renamed (fix the link), or the link was aspirational (write
the page, or remove the link). Never leave it: a dead link teaches a future
session that the wiki is unreliable, and then it stops reading it.

**`orphan`** — a wiki-layer page no other memory page links to. Only dated log
pages are exempt; a log entry nothing links to is normal. An orphan topic page
is knowledge nobody will find, because navigation is by link, not by search.
Link it from the page that should have mentioned it, or fold it into that page.
Being listed in `MEMORY.md` does not count — the index is a table of contents,
not a link.

**`index`** — `MEMORY.md` names a file that does not exist, or a file exists
with no index line. The index is the entry point read at every session start;
if it is wrong, everything downstream of it is wrong. `tools/session.py journal`
and `tools/session.py wiki` both maintain it for you, so this finding usually
means a page was created by hand.

**`index-size`** — the index has grown past one screen. That is the merge
trigger, not a formatting complaint.

**`duplicate`** — two wiki pages that look like the same topic, by identical
title or overlapping slug tokens. Merge into one page and delete the loser.
Two pages on one topic means the next session updates whichever it finds first
and the other silently rots.

**`stale-claim?`** — the heuristic one, and the question mark is deliberate.
Lint extracts claim-shaped tokens (ratings like `12 V`, ratios like `50:1`,
part numbers like `N20VA`) and flags a page that still asserts a token which a
*different* page marks as superseded, revised, wrong or void. It is text
matching, not comprehension. **Read both pages before acting.** Expect false
positives — a page legitimately quoting an old value while explaining why it was
wrong looks identical, to this tool, to a page that still believes it. **The
rate is not known and no number should be quoted for it**; the lint has almost
no track record yet. What is known is that reading two pages costs less than
trusting a dead number.

When it is real, the fix is always the same shape: **revise the page that is
wrong, in place.** Do not add a new dated file next to it. The dated log entry
records *that* the revision happened; the topic page carries what is now true.
The existing pages model this well — `memory/n20-motors.md` and
`memory/power-supply.md` both carry an explicit "⚠️ Revised" block saying what
they used to claim and why it was wrong. Copy that pattern: it preserves the
reasoning without leaving the wrong fact standing anywhere.

**`skill`** — a skill directory with no `SKILL.md`, missing `name:` or
`description:` frontmatter, or a `name` that disagrees with its directory.
Mechanical; just fix it.

**`skill-provisional`** — a skill carrying `provisional: true`. Not a defect: a
reminder. Each one should carry a `confirmed-by:` line naming the event that
would settle it. When that event has happened, either promote the skill (delete
the provisional banner and revise the numbers to what was actually observed) or
delete the skill. A provisional skill that has outlived its confirming event
without being revised is the most dangerous file in the repo — it reads as
authority and is a guess.

**`skill-stale`** — a skill references a repo path that does not exist. Usually
means a file was renamed under it. Skills that describe a layout that changed
are how an agent confidently does the wrong thing.

## What lint cannot see

It is a text tool. It cannot tell you that a page is *right but useless*, that
two pages disagree in prose without sharing a token, or that a memory entry
records what happened without recording why. Those are read-the-pages jobs, and
they are the reason the cadence is "every few sessions" rather than "whenever
CI is red".

The lint over `docs/` is a different job and is not attempted here: `docs/` is
owned by the CLOSE ritual's ARCHITECTURE check and by
`tools/session.py index --check`, which flags any file with no indexed purpose.
