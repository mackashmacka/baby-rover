#!/usr/bin/env python3
"""Baby Rover session tool — makes the CLAUDE.md rituals mechanical.

The rituals in `CLAUDE.md` / `docs/agent-bootstrap.md` are prose, and prose is
forgettable. This script turns the forgettable parts into commands with exit
codes. It does not invent a new system: session state IS `NEXT-STEPS.md`, the
journal IS the `memory/` log layer, the index IS `memory/MEMORY.md`. This is
only machinery for those.

    tools/session.py start              orientation brief for a fresh session
    tools/session.py journal <slug>     today's memory log entry + index line
    tools/session.py wiki <slug>        revise an undated topic page, safely
    tools/session.py close              the CLOSE ritual as a real checklist
    tools/session.py lint               the third verb: memory + skill lint
    tools/session.py index              the annotated repo index

Constraints, deliberate:
  * stdlib only. It must run on a bare Python 3.12 with nothing installed,
    because on 2026-08-31 that is exactly what this laptop is.
  * dates are UTC (repo convention, CLAUDE.md "Timezone is UTC").
  * it reports; it does not delete. Lint never removes a file.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import os
import re
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Repo location and small shared helpers
# ---------------------------------------------------------------------------

# Files that mark the repo root. CLAUDE.md alone is not enough — a session may
# be run from a subdirectory of some other checkout — so require both.
ROOT_MARKERS = ("CLAUDE.md", "memory/MEMORY.md")

# Extensions treated as "source" for the ARCHITECTURE.md staleness check.
# Assumption (not in the ground truth): firmware is C/CMake and host code is
# Python/shell. Widen this list if another language shows up.
SOURCE_SUFFIXES = {".c", ".h", ".cpp", ".hpp", ".py", ".sh", ".pio", ".cmake", ".S"}
SOURCE_NAMES = {"CMakeLists.txt"}

DATED_PAGE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-(.+)\.md$")
WIKILINK_RE = re.compile(r"\[\[([^\]|#]+?)(?:[#|][^\]]*)?\]\]")
INDEX_ENTRY_RE = re.compile(r"^\s*[-*]\s*\[([^\]]+)\]\(([^)]+)\)\s*(?:[—–-]\s*)?(.*)$")


def utc_today() -> _dt.date:
    """Today in UTC. The repo keeps one dating convention and it is UTC."""
    return _dt.datetime.now(_dt.timezone.utc).date()


def utc_stamp() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%H:%M")


def find_repo_root(start: Path | None = None) -> Path:
    """Walk up from `start` (default: this file) until the root markers appear."""
    here = (start or Path(__file__).resolve()).resolve()
    for candidate in [here, *here.parents]:
        if candidate.is_dir() and all((candidate / m).exists() for m in ROOT_MARKERS):
            return candidate
    raise SystemExit(
        "session.py: could not find the repo root "
        f"(looked for {' and '.join(ROOT_MARKERS)} from {here})"
    )


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def git(root: Path, *args: str) -> tuple[int, str]:
    """Run git; return (returncode, stdout). Never raises — git may be absent."""
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return 127, ""
    return proc.returncode, proc.stdout


def git_files(root: Path) -> list[str]:
    """Tracked files, plus untracked-and-not-ignored ones (other streams' WIP)."""
    rc, out = git(root, "ls-files", "--cached", "--others", "--exclude-standard")
    if rc != 0:
        return []
    return sorted({line.strip() for line in out.splitlines() if line.strip()})


def git_tracked(root: Path) -> list[str]:
    rc, out = git(root, "ls-files")
    if rc != 0:
        return []
    return sorted({line.strip() for line in out.splitlines() if line.strip()})


def slug_title(slug: str) -> str:
    return slug.replace("-", " ").replace("_", " ").strip().capitalize()


# ---------------------------------------------------------------------------
# Pure parsers (no I/O — these are the parts worth unit-testing)
# ---------------------------------------------------------------------------


def parse_frontmatter(text: str) -> dict[str, str]:
    """Parse a leading `---` YAML-ish frontmatter block into a flat dict.

    Only `key: value` lines are understood — that is all this repo uses, and a
    real YAML parser is not available on a bare Python.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    out: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        out[key.strip().lower()] = value.strip().strip("'\"")
    return out


def is_reviewed(text: str) -> bool:
    """True if a human hand-edited this page (`reviewed: true` frontmatter).

    Such a page is revised around, never overwritten wholesale.
    """
    return parse_frontmatter(text).get("reviewed", "").lower() in {"true", "yes", "1"}


def parse_memory_index(text: str) -> list[dict[str, str]]:
    """Parse memory/MEMORY.md into index entries.

    Each entry: {"label", "target", "summary", "layer"} where layer is
    "wiki" | "log" | "" depending on the `## ... layer` heading it sits under.
    """
    entries: list[dict[str, str]] = []
    layer = ""
    for line in text.splitlines():
        stripped = line.strip()
        low = stripped.lower()
        if low.startswith("##"):
            if "wiki" in low:
                layer = "wiki"
            elif "log" in low:
                layer = "log"
            else:
                layer = ""
            continue
        m = INDEX_ENTRY_RE.match(line)
        if m:
            entries.append(
                {
                    "label": m.group(1).strip(),
                    "target": m.group(2).strip(),
                    "summary": m.group(3).strip(),
                    "layer": layer,
                }
            )
    return entries


def split_table_row(line: str) -> list[str]:
    """Split one markdown table row into stripped cells."""
    return [c.strip() for c in line.strip().strip("|").split("|")]


def is_separator_row(cells: list[str]) -> bool:
    return bool(cells) and all(set(c) <= set("-: ") and c for c in cells)


REGISTRY_COLUMNS = ["id", "date", "story", "question", "method", "result", "data"]


def parse_registry(text: str) -> list[dict[str, str]]:
    """Parse the 7-column experiment table out of experiments/REGISTRY.md.

    The `## Planned` table has 3 columns and is deliberately skipped: a planned
    experiment has not run, so it owes no data file.
    """
    rows: list[dict[str, str]] = []
    for line in text.splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = split_table_row(line)
        if len(cells) != len(REGISTRY_COLUMNS):
            continue
        if is_separator_row(cells):
            continue
        if [c.lower() for c in cells] == REGISTRY_COLUMNS:
            continue
        row = dict(zip(REGISTRY_COLUMNS, cells))
        row["example"] = "yes" if "example" in row["id"].lower() else ""
        rows.append(row)
    return rows


DATA_EMPTY = {"", "—", "–", "-", "n/a", "none", "tbd"}
MD_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
PATHISH_RE = re.compile(r"[A-Za-z0-9_./-]+/[A-Za-z0-9_./*-]+")


def registry_data_paths(cell: str) -> list[str]:
    """Extract candidate repo-relative paths from a registry Data cell."""
    cell = cell.strip().strip("`")
    if cell.lower() in DATA_EMPTY:
        return []
    links = MD_LINK_RE.findall(cell)
    if links:
        return [l.strip() for l in links]
    found = PATHISH_RE.findall(cell)
    return [f.strip("`") for f in found]


def parse_open_blockers(text: str) -> list[str]:
    """Blocker headings from NEXT-STEPS.md that are not marked closed."""
    blockers: list[str] = []
    in_section = False
    for line in text.splitlines():
        if line.startswith("## "):
            in_section = line.lower().startswith("## blocker")
            continue
        if in_section and line.startswith("### "):
            title = line[4:].strip()
            if title.startswith("✅") or title.lower().startswith("closed"):
                continue
            blockers.append(title)
    return blockers


def parse_section(text: str, heading_prefix: str) -> list[str]:
    """Return the body lines of the first `## ` section whose title starts with
    `heading_prefix` (case-insensitive)."""
    out: list[str] = []
    grabbing = False
    for line in text.splitlines():
        if line.startswith("## "):
            if grabbing:
                break
            grabbing = line[3:].strip().lower().startswith(heading_prefix.lower())
            continue
        if grabbing:
            out.append(line)
    while out and not out[0].strip():
        out.pop(0)
    while out and not out[-1].strip():
        out.pop()
    return out


def collapse_bullets(lines: list[str]) -> list[str]:
    """Fold a markdown bullet list into one plain string per bullet.

    A bullet wrapped over three lines is still one open thread, and printing
    only its first line hides half of it.
    """
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(("- ", "* ")):
            out.append(stripped[2:].strip())
        elif stripped and out and line[:1] in " \t":
            out[-1] += " " + stripped
    return [re.sub(r"\s+", " ", re.sub(r"[*`]", "", t)).strip() for t in out]


FIELD_RE_CACHE: dict[str, re.Pattern[str]] = {}


def parse_field(text: str, name: str) -> str:
    """Pull a `**Name:** value` line out of a markdown document."""
    pattern = FIELD_RE_CACHE.get(name)
    if pattern is None:
        pattern = re.compile(
            r"^\s*\**" + re.escape(name) + r"\**\s*:?\**\s*(.+?)\s*$",
            re.IGNORECASE | re.MULTILINE,
        )
        FIELD_RE_CACHE[name] = pattern
    m = pattern.search(text)
    return re.sub(r"\*+", "", m.group(1)).strip() if m else ""


def parse_index_yaml(text: str) -> list[tuple[str, list[tuple[str, str]]]]:
    """Parse docs/INDEX.yaml — a deliberately tiny YAML subset.

    Accepted, and nothing else:

        # comment
        Group name:
          path/to/file: one-line purpose
          "quoted/path": "quoted purpose"

    Two levels, no lists, no nesting. Written by hand, parsed by hand, because
    PyYAML is a third-party import and this must run on a bare Python.
    """
    groups: list[tuple[str, list[tuple[str, str]]]] = []
    current: list[tuple[str, str]] | None = None
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indented = line[0] in " \t"
        body = line.strip()
        if ":" not in body:
            raise ValueError(f"docs/INDEX.yaml:{lineno}: no ':' in {body!r}")
        key, _, value = body.partition(":")
        key = key.strip().strip("'\"")
        value = value.strip().strip("'\"")
        if not indented:
            if value:
                raise ValueError(
                    f"docs/INDEX.yaml:{lineno}: group heading {key!r} must have no value"
                )
            current = []
            groups.append((key, current))
        else:
            if current is None:
                raise ValueError(
                    f"docs/INDEX.yaml:{lineno}: entry {key!r} before any group heading"
                )
            current.append((key, value))
    return groups


def extract_wikilinks(text: str) -> set[str]:
    return {m.strip() for m in WIKILINK_RE.findall(text) if m.strip()}


def extract_md_local_links(text: str) -> set[str]:
    """Relative markdown links to sibling `.md` files (same directory only)."""
    out = set()
    for target in MD_LINK_RE.findall(text):
        target = target.split("#", 1)[0].strip()
        if target.endswith(".md") and "/" not in target:
            out.add(target[:-3])
    return out


TOKEN_RE = re.compile(r"[a-z0-9]+")
STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "for", "in", "on", "is", "it",
    "this", "that", "with", "from", "by", "at", "as", "be", "not", "md",
}


def slug_tokens(slug: str) -> set[str]:
    """Tokens of a slug, crudely singularised.

    The singularisation is one rule — drop a trailing 's' from words longer
    than three characters — and it exists because `n20-motors` and
    `n20-motor-notes` are obviously the same topic to a human and share no
    tokens at all without it. A real stemmer is a dependency; this is not.
    """
    out = set()
    for t in TOKEN_RE.findall(slug.lower()):
        if t in STOPWORDS or len(t) <= 2:
            continue
        out.add(t[:-1] if len(t) > 3 and t.endswith("s") else t)
    return out


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


# Claim tokens: the kind of fact that goes stale — a rating, a ratio, a part
# number. Used by `lint` to look for a page still asserting what a later page
# says is wrong. This is a heuristic that produces CANDIDATES, not proof.
CLAIM_TOKEN_RES = [
    re.compile(r"\b\d+(?:\.\d+)?\s?(?:V|A|mA|kHz|MHz|Hz|Ω|ohm|ohms|rpm|RPM)\b"),
    re.compile(r"\b\d+\s?:\s?\d+\b"),
    re.compile(r"\b[A-Z]{2,}[A-Z0-9]*[-_]?\d{2,}[A-Z0-9-]*\b"),
]
# Deliberately narrow. An earlier draft included bare "not" and "never", which
# fired on "that is a hypothesis, not a finding" — a sentence that supersedes
# nothing. A lint that cries wolf gets ignored, and an ignored lint is worse
# than no lint. These are supersession phrasings, not general negation.
NEGATION_WORDS = (
    "was wrong", "were wrong", "is wrong", "now wrong", "wrong part",
    "void", "supersed", "no longer", "revised", "corrected", "previously",
    "distrust", "instead of", "was believed", "turned out", "obsolete",
    "deprecated", "never confirmed", "not confirmed", "unusable",
)


def claim_tokens(line: str) -> set[str]:
    out: set[str] = set()
    for rx in CLAIM_TOKEN_RES:
        for m in rx.findall(line):
            out.add(re.sub(r"\s+", " ", m).strip())
    return out


def line_is_negating(line: str) -> bool:
    low = line.lower()
    return any(w in low for w in NEGATION_WORDS)


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------


def hr(title: str = "") -> None:
    if title:
        print(f"\n== {title} " + "=" * max(3, 72 - len(title)))
    else:
        print("=" * 76)


def bullet(text: str, indent: int = 2) -> None:
    print(" " * indent + text)


# ---------------------------------------------------------------------------
# start — orientation brief. READS ONLY.
# ---------------------------------------------------------------------------


def cmd_start(root: Path, args: argparse.Namespace) -> int:
    memory_dir = root / "memory"
    memory_index = memory_dir / "MEMORY.md"
    next_steps = root / "NEXT-STEPS.md"

    print(f"Baby Rover — session start · {utc_today().isoformat()} (UTC) · {root}")
    rc, branch = git(root, "rev-parse", "--abbrev-ref", "HEAD")
    rc2, head = git(root, "log", "-1", "--format=%h %s")
    if rc == 0 and rc2 == 0:
        print(f"git: {branch.strip()} @ {head.strip()}")
    rc3, dirty = git(root, "status", "--porcelain")
    if rc3 == 0:
        n = len([l for l in dirty.splitlines() if l.strip()])
        print(f"working tree: {'clean' if n == 0 else str(n) + ' file(s) changed/untracked'}")

    hr("Read in this order")
    bullet("1. CLAUDE.md              — the operating agreement")
    bullet("2. memory/MEMORY.md       — the index below, then the pages it names")
    bullet("3. NEXT-STEPS.md          — current state, blockers, open threads")
    bullet("4. docs/PLAN.md §5-7      — pick ONE story. One story per conversation.")

    hr("memory/MEMORY.md")
    entries = parse_memory_index(read_text(memory_index))
    if not entries:
        bullet("(index is empty or unparseable — that is itself a finding, run: lint)")
    for layer, label in (("wiki", "Wiki layer (revise in place)"), ("log", "Log layer (append-only)")):
        rows = [e for e in entries if e["layer"] == layer]
        if not rows:
            continue
        print(f"  {label}:")
        for e in rows:
            exists = (memory_dir / e["target"]).exists()
            mark = "" if exists else "   [MISSING FILE]"
            bullet(f"- {e['label']}: {e['summary']}{mark}", indent=4)

    text_ns = read_text(next_steps)
    hr("NEXT-STEPS.md")
    last = parse_field(text_ns, "Last updated")
    story = parse_field(text_ns, "Current story")
    mode = parse_field(text_ns, "Session mode")
    bullet(f"Last updated : {last or '(not stated — NEXT-STEPS.md has no Last updated field)'}")
    bullet(f"Current story: {story or '(not stated — set a **Current story:** line)'}")
    if mode:
        bullet(f"Session mode : {mode}")

    state = parse_section(text_ns, "Current state")
    if state:
        print()
        for line in state[:40]:
            if line.strip() in {"---", "***", "___"}:  # horizontal rule ends the section
                break
            print("  " + line)

    hr("Open blockers")
    blockers = parse_open_blockers(text_ns)
    if blockers:
        for b in blockers:
            bullet(f"- {b}")
    else:
        bullet("(none listed — check NEXT-STEPS.md has a '## Blockers' section)")

    threads = collapse_bullets(parse_section(text_ns, "Open threads"))
    hr(f"Open threads ({len(threads)})")
    for t in threads:
        bullet("- " + (t if len(t) <= 150 else t[:147] + "…"))

    hr("Experiments with no data file")
    reg = parse_registry(read_text(root / "experiments" / "REGISTRY.md"))
    problems = []
    for row in reg:
        if row.get("example"):
            continue
        paths = registry_data_paths(row["data"])
        if not paths:
            problems.append((row["id"], "no data path in the Data column"))
            continue
        for p in paths:
            if not (root / p).exists():
                problems.append((row["id"], f"data path missing: {p}"))
    if problems:
        for rid, why in problems:
            bullet(f"- {rid or '(no id)'}: {why}")
        bullet("A result not persisted did not happen (CLOSE ritual item 7).")
    else:
        bullet(f"(none — {len(reg)} row(s) in the registry, all with data or example rows)")

    hr("Skills available")
    skills = sorted((root / ".claude" / "skills").glob("*/SKILL.md"))
    if not skills:
        bullet("(none yet)")
    for s in skills:
        fm = parse_frontmatter(read_text(s))
        name = fm.get("name") or s.parent.name
        desc = fm.get("description", "")
        prov = " [PROVISIONAL]" if fm.get("provisional", "").lower() in {"true", "yes", "1"} else ""
        bullet(f"- {name}{prov}: {desc[:110]}")

    hr("Before you declare anything done")
    bullet("tools/session.py journal <slug> --summary '...'   file the day's entry")
    bullet("tools/session.py lint                             the third verb")
    bullet("tools/session.py close                            the CLOSE ritual, checked")
    print()
    return 0


# ---------------------------------------------------------------------------
# journal — today's memory log entry, append-only
# ---------------------------------------------------------------------------

JOURNAL_TEMPLATE = """# {date} — {title}

## What happened

## Decisions

_Record why, not just what._

## Open threads

## Session log
"""


def ensure_index_entry(
    memory_index: Path, target: str, label: str, summary: str, layer: str
) -> str:
    """Ensure MEMORY.md carries a one-line index entry for `target`.

    Returns a human-readable note about what happened. Only entries pointing at
    TODAY's log file are ever rewritten; past days are never touched.
    """
    text = read_text(memory_index)
    entries = parse_memory_index(text)
    existing = next((e for e in entries if e["target"] == target), None)
    if existing is not None:
        if summary and existing["summary"] != summary:
            return (
                f"index entry for {target} already exists with a different summary — "
                "left as-is (past index lines are not rewritten)"
            )
        return f"index entry for {target} already present"

    line = f"- [{label}]({target}) — {summary}"
    lines = text.splitlines()
    heading_word = "wiki" if layer == "wiki" else "log"

    insert_at = None
    in_section = False
    for i, l in enumerate(lines):
        if l.startswith("## "):
            if in_section:
                insert_at = i
                break
            in_section = heading_word in l.lower()
        elif in_section and l.strip().startswith(("-", "*")):
            insert_at = i + 1
    if insert_at is None:
        if not in_section:
            lines += ["", f"## {'Wiki' if layer == 'wiki' else 'Log'} layer", ""]
        lines.append(line)
    else:
        while insert_at > 0 and not lines[insert_at - 1].strip():
            insert_at -= 1
        lines.insert(insert_at, line)

    memory_index.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return f"indexed {target} in MEMORY.md"


def cmd_journal(root: Path, args: argparse.Namespace) -> int:
    memory_dir = root / "memory"
    date = utc_today().isoformat()
    slug = args.slug.strip().strip("/")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", slug):
        print(f"session.py journal: slug must be lowercase-hyphenated, got {slug!r}", file=sys.stderr)
        return 2

    path = memory_dir / f"{date}-{slug}.md"
    created = False
    if not path.exists():
        if not args.summary:
            print(
                "session.py journal: creating a new entry needs its index line.\n"
                f"  tools/session.py journal {slug} --summary 'one line, what this day was'",
                file=sys.stderr,
            )
            return 2
        memory_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(
            JOURNAL_TEMPLATE.format(date=date, title=slug_title(slug)), encoding="utf-8"
        )
        created = True

    # Append-only: messages go to the end of the Session log section, never over
    # anything already written.
    if args.message:
        text = read_text(path)
        if "## Session log" not in text:
            text = text.rstrip() + "\n\n## Session log\n"
        stamp = utc_stamp()
        additions = "".join(f"\n- {stamp}Z — {m.strip()}" for m in args.message)
        path.write_text(text.rstrip() + additions + "\n", encoding="utf-8")

    note = ensure_index_entry(
        memory_dir / "MEMORY.md",
        target=path.name,
        label=path.stem,
        summary=args.summary or "",
        layer="log",
    ) if args.summary or created else "index entry not checked (no --summary given)"

    print(f"{'created' if created else 'appended to'}: {path.relative_to(root)}")
    print(f"MEMORY.md: {note}")
    if created:
        print(
            "\nNow write the entry itself — decisions and learnings, skipping what\n"
            "git and the code already record. The template has the sections."
        )
    return 0


# ---------------------------------------------------------------------------
# wiki — revise an undated topic page, refusing to clobber human edits
# ---------------------------------------------------------------------------


def cmd_wiki(root: Path, args: argparse.Namespace) -> int:
    memory_dir = root / "memory"
    slug = args.slug.strip().removesuffix(".md")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", slug):
        print(f"session.py wiki: slug must be lowercase-hyphenated, got {slug!r}", file=sys.stderr)
        return 2
    if DATED_PAGE_RE.match(slug + ".md"):
        print(
            "session.py wiki: that is a dated log page, not a wiki page. "
            "Log pages are append-only — use `journal`.",
            file=sys.stderr,
        )
        return 2

    path = memory_dir / f"{slug}.md"
    exists = path.exists()
    text = read_text(path) if exists else ""
    reviewed = is_reviewed(text)

    if args.check:
        print(f"{path.relative_to(root)}: {'exists' if exists else 'does not exist'}"
              f"{', reviewed: true (human-edited)' if reviewed else ''}")
        return 1 if reviewed else 0

    # --- wholesale replace: the operation the rule exists to stop -----------
    if args.from_file:
        if reviewed:
            print(
                f"REFUSED: {path.relative_to(root)} carries `reviewed: true` frontmatter.\n"
                "A page a human hand-edited is revised AROUND, never overwritten wholesale.\n"
                "Use --append to add a dated revision section, or edit the page by hand\n"
                "leaving the reviewed content intact.",
                file=sys.stderr,
            )
            return 3
        src = Path(args.from_file)
        if not src.is_file():
            print(f"session.py wiki: no such file: {src}", file=sys.stderr)
            return 2
        new = src.read_text(encoding="utf-8")
        if exists and not args.force:
            print(
                f"REFUSED: {path.relative_to(root)} already exists.\n"
                "Wholesale replacement of an existing page loses history that git alone\n"
                "will not explain. Pass --force if replacing really is the intent.",
                file=sys.stderr,
            )
            return 3
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(new if new.endswith("\n") else new + "\n", encoding="utf-8")
        print(f"{'replaced' if exists else 'created'}: {path.relative_to(root)}")
    elif args.append:
        if not exists:
            if not args.summary:
                print(
                    "session.py wiki: creating a new topic page needs its index line.\n"
                    f"  tools/session.py wiki {slug} --summary 'one line' --append '...'",
                    file=sys.stderr,
                )
                return 2
            path.write_text(f"# {slug_title(slug)}\n", encoding="utf-8")
            exists = False
            text = read_text(path)
        body = read_text(path).rstrip()
        stamp = utc_today().isoformat()
        additions = "".join(f"\n\n## Revised {stamp}\n\n{m.strip()}" for m in args.append)
        path.write_text(body + additions + "\n", encoding="utf-8")
        print(f"appended a dated revision section to: {path.relative_to(root)}")
        if reviewed:
            print(
                "NOTE: this page is `reviewed: true`. The appended section must not\n"
                "      silently contradict the human-reviewed text above it — if it\n"
                "      does, say so explicitly in the section and tell the owner."
            )
    elif not exists:
        if not args.summary:
            print(
                "session.py wiki: creating a new topic page needs its index line.\n"
                f"  tools/session.py wiki {slug} --summary 'one line, what this topic is'",
                file=sys.stderr,
            )
            return 2
        # The seed text deliberately does NOT contain a literal double-bracket
        # link. It used to say "cross-link it with [[wikilinks]]", which made
        # every freshly created page ship with a dead link to a page named
        # "wikilinks" — a finding `lint` then reported, on a file this tool had
        # just written. A tool that manufactures the rot its own linter exists
        # to catch teaches the next session to ignore the linter.
        path.write_text(
            f"# {slug_title(slug)}\n\n_New topic page. Write it, then cross-link it to "
            "related pages using double-bracket wikilink syntax._\n",
            encoding="utf-8",
        )
        print(f"created: {path.relative_to(root)}")

    # --- status report + index maintenance ---------------------------------
    text = read_text(path)
    if args.summary:
        print("MEMORY.md: " + ensure_index_entry(
            memory_dir / "MEMORY.md", f"{slug}.md", slug, args.summary, "wiki"
        ))

    indexed = any(
        e["target"] == f"{slug}.md" for e in parse_memory_index(read_text(memory_dir / "MEMORY.md"))
    )
    print()
    print(f"page      : {path.relative_to(root)}")
    print(f"exists    : {path.exists()}")
    print(f"reviewed  : {is_reviewed(text)}  (true ⇒ revise around it, never overwrite)")
    print(f"indexed   : {indexed}")
    print(f"links out : {', '.join(sorted(extract_wikilinks(text))) or '(none)'}")
    inbound = []
    for other in sorted(memory_dir.glob("*.md")):
        if other.name in {"MEMORY.md", path.name}:
            continue
        otext = read_text(other)
        if slug in extract_wikilinks(otext) or slug in extract_md_local_links(otext):
            inbound.append(other.stem)
    print(f"links in  : {', '.join(inbound) or '(none — orphan; lint will flag it)'}")
    if not args.append and not args.from_file and path.exists():
        print("\nRevise it in place by hand. Knowledge compounds by revision — one fact\n"
              "smeared across five dated files is rot, not memory.")
    return 0


# ---------------------------------------------------------------------------
# close — the CLOSE ritual as a checklist with real checks
# ---------------------------------------------------------------------------

PASS, FAIL, PROMPT_ = "PASS", "FAIL", "PROMPT"


class Check:
    """One checklist item and its outcome."""

    def __init__(self, number: str, title: str):
        self.number = number
        self.title = title
        self.status = PASS
        self.details: list[str] = []

    def fail(self, detail: str) -> "Check":
        self.status = FAIL
        self.details.append(detail)
        return self

    def note(self, detail: str) -> "Check":
        self.details.append(detail)
        return self

    def emit(self) -> None:
        print(f"[{self.status:<6}] {self.number}. {self.title}")
        for d in self.details:
            for line in d.splitlines():
                print(f"           {line}")


def ask(question: str, assume_yes: bool) -> tuple[bool, str]:
    """Prompt for a human answer. Never silently passes."""
    if assume_yes:
        return True, "asserted by --yes on the command line"
    if not sys.stdin.isatty():
        return False, "UNANSWERED — no terminal to ask on. Re-run interactively, or pass --yes to assert it."
    try:
        reply = input(f"           ? {question} [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False, "UNANSWERED — input aborted"
    return (reply in {"y", "yes"}), ("answered yes" if reply in {"y", "yes"} else "answered no")


def newest_source_file(root: Path) -> tuple[Path | None, float]:
    """The most recently modified source file, by mtime.

    Caveat, stated here rather than hidden: mtimes are set by checkout, so on a
    fresh clone every file looks new and this check is advisory. It is right on
    the machine where the work actually happened, which is the case that matters.
    """
    newest: Path | None = None
    newest_mtime = 0.0
    for rel in git_files(root):
        p = root / rel
        if not p.is_file():
            continue
        if p.suffix not in SOURCE_SUFFIXES and p.name not in SOURCE_NAMES:
            continue
        try:
            mtime = p.stat().st_mtime
        except OSError:
            continue
        if mtime > newest_mtime:
            newest, newest_mtime = p, mtime
    return newest, newest_mtime


def check_tests(root: Path, assume_yes: bool) -> Check:
    c = Check("1", "Full test suite green and recorded")
    tests_dir = root / "tests"
    if not tests_dir.is_dir():
        return c.fail(
            "no tests/ directory — there is no suite to run, so item 1 cannot pass.\n"
            "CLAUDE.md demands a green FULL suite; docs/PLAN.md §10 says define it on\n"
            "Day 0 or the ritual is theatre."
        )
    # Prefer the repo-local venv the Makefile builds; fall back to whatever
    # interpreter is running this. Assumption: `make test` is the canonical way
    # to run the suite, and it puts its venv at <root>/.venv.
    venv_python = root / ".venv" / "bin" / "python"
    python = str(venv_python) if venv_python.exists() else sys.executable
    # `-m "not hardware"` is passed EXPLICITLY even though pytest.ini already
    # sets it in addopts, and the reason is safety rather than tidiness: this
    # runs unattended, from an agent, with no human at the bench. A hardware
    # test spins a real motor and drives a real H-bridge. Relying on a default
    # in a config file another stream owns means one edit to pytest.ini turns
    # `session.py close` into something that energises the rig with nobody
    # watching. A command-line -m beats addopts (last -m wins), so this is the
    # cheap belt to pytest.ini's braces. `make test-hw` remains the only way
    # to run the hardware suite, deliberately and by hand.
    try:
        proc = subprocess.run(
            [python, "-m", "pytest", "-q", "-m", "not hardware"],
            cwd=str(root), capture_output=True, text=True, timeout=900,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return c.fail(f"could not run pytest: {exc}")

    tail = [l for l in proc.stdout.strip().splitlines() if l.strip()][-3:]
    if "No module named pytest" in (proc.stderr + proc.stdout):
        ok, how = ask("pytest is not installed — was the suite run and green elsewhere?", assume_yes)
        c.status = PASS if ok else PROMPT_
        return c.note(f"pytest missing (bench-setup.sh installs it). {how}")
    for line in tail:
        c.note(line)
    if proc.returncode != 0:
        return c.fail(f"pytest exited {proc.returncode} — the suite is not green")

    # "and recorded": a suite you ran but did not write down is not evidence.
    todays = list((root / "memory").glob(f"{utc_today().isoformat()}-*.md"))
    recorded = any(
        re.search(r"pytest|test suite|coverage", read_text(p), re.IGNORECASE) for p in todays
    )
    if not recorded:
        c.fail("suite is green but not RECORDED — no mention of the run in today's memory entry.\n"
               "  tools/session.py journal <slug> -m 'pytest: <paste the summary line>'")
    return c


def check_architecture(root: Path) -> Check:
    c = Check("3", "ARCHITECTURE.md matches reality (newer than the last source change)")
    arch = root / "docs" / "ARCHITECTURE.md"
    if not arch.exists():
        return c.fail("docs/ARCHITECTURE.md does not exist")
    newest, newest_mtime = newest_source_file(root)
    if newest is None:
        return c.note("no source files yet — nothing to be stale against")
    if arch.stat().st_mtime < newest_mtime:
        return c.fail(
            f"{newest.relative_to(root)} changed after ARCHITECTURE.md was last touched.\n"
            "Either the map gained a component/flow/invariant and was not updated, or\n"
            "it genuinely did not change — in which case touch it and say so in the diff."
        )
    return c.note(f"newest source: {newest.relative_to(root)}")


def check_next_steps(root: Path) -> Check:
    c = Check("4", "NEXT-STEPS.md rewritten for the next session")
    ns = root / "NEXT-STEPS.md"
    if not ns.exists():
        return c.fail("NEXT-STEPS.md does not exist")
    text = read_text(ns)
    today = utc_today().isoformat()
    stated = parse_field(text, "Last updated")
    if today not in stated:
        c.fail(f"'Last updated' says {stated or '(nothing)'}, not {today}")
    mtime_date = _dt.datetime.fromtimestamp(ns.stat().st_mtime, _dt.timezone.utc).date().isoformat()
    if mtime_date != today:
        c.fail(f"file was last modified {mtime_date}, not today ({today})")
    if not parse_open_blockers(text) and "## Blocker" not in text:
        c.fail("no '## Blockers' section — the handoff is missing its blocker list")
    if not parse_field(text, "Current story"):
        c.fail("no '**Current story:**' line — `session.py start` has nothing to report")
    return c


def check_memory(root: Path) -> Check:
    c = Check("5", "Memory ritual: today's entry exists and is indexed")
    today = utc_today().isoformat()
    memory_dir = root / "memory"
    todays = sorted(memory_dir.glob(f"{today}-*.md"))
    if not todays:
        return c.fail(
            f"no memory/{today}-<slug>.md — this session left no log entry.\n"
            "  tools/session.py journal <slug> --summary '...'"
        )
    index_targets = {e["target"] for e in parse_memory_index(read_text(memory_dir / "MEMORY.md"))}
    for p in todays:
        if p.name not in index_targets:
            c.fail(f"{p.name} exists but has no MEMORY.md index line")
        else:
            c.note(f"{p.name} — indexed")
    return c


def check_experiments(root: Path) -> Check:
    c = Check("7", "Experiments that ran have registry rows with data files")
    reg_path = root / "experiments" / "REGISTRY.md"
    rows = parse_registry(read_text(reg_path))
    real_rows = [r for r in rows if not r.get("example")]

    exp_dir = root / "experiments"
    ran_dirs = []
    if exp_dir.is_dir():
        ran_dirs = [
            d for d in sorted(exp_dir.iterdir())
            if d.is_dir() and d.name != "plots" and not d.name.startswith(".")
            and any(f.is_file() and f.name != ".gitkeep" for f in d.rglob("*"))
        ]

    referenced = set()
    for r in real_rows:
        for p in registry_data_paths(r["data"]):
            referenced.add(p.rstrip("/"))
            if not (root / p).exists():
                c.fail(f"row '{r['id']}' points at {p}, which does not exist")
        if not registry_data_paths(r["data"]):
            c.fail(f"row '{r['id']}' has no data path — a result not persisted did not happen")

    for d in ran_dirs:
        rel = str(d.relative_to(root))
        if not any(ref == rel or ref.startswith(rel + "/") for ref in referenced):
            c.fail(f"{rel}/ holds data but no registry row references it")

    if not real_rows and not ran_dirs:
        c.note("no experiments have run yet")
    return c


def check_committed(root: Path, assume_yes: bool) -> Check:
    c = Check("8", "Working tree committed (and pushed)")
    rc, out = git(root, "status", "--porcelain")
    if rc != 0:
        ok, how = ask("git unavailable — is the work committed and pushed?", assume_yes)
        c.status = PASS if ok else PROMPT_
        return c.note(how)
    dirty = [l for l in out.splitlines() if l.strip()]
    if dirty:
        c.fail(f"{len(dirty)} uncommitted path(s):")
        for line in dirty[:15]:
            c.note("  " + line)
        if len(dirty) > 15:
            c.note(f"  … and {len(dirty) - 15} more")
    rc2, _ = git(root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    if rc2 != 0:
        c.fail("no upstream branch configured — nothing has been pushed anywhere.\n"
               "The Pi's rootfs is a disposable USB stick; the laptop is the only copy.")
    else:
        rc3, ahead = git(root, "rev-list", "--count", "@{u}..HEAD")
        if rc3 == 0 and ahead.strip() not in {"", "0"}:
            c.fail(f"{ahead.strip()} commit(s) not pushed")
    return c


def check_prompted(number: str, title: str, question: str, assume_yes: bool) -> Check:
    c = Check(number, title)
    ok, how = ask(question, assume_yes)
    c.status = PASS if ok else PROMPT_
    c.note(how)
    return c


def cmd_close(root: Path, args: argparse.Namespace) -> int:
    print(f"CLOSE ritual — {utc_today().isoformat()} (UTC) · {root}")
    print("Nothing is 'done' until ALL of these. Partial progress does not close a story.\n")

    checks: list[Check] = []

    checks.append(check_tests(root, args.yes))
    checks[-1].emit()

    checks.append(check_prompted(
        "1b", "Self-review gate passed (6 points, CLAUDE.md)",
        "Ran the adversarial pass — trust boundaries, entity-set completeness, "
        "verification honesty, regression risk, output integrity, failure modes?",
        args.yes))
    checks[-1].emit()

    checks.append(check_prompted(
        "2", "Story status annotated in docs/PLAN.md",
        "Is the story marked DONE or partial in docs/PLAN.md, stating what changed "
        "vs its acceptance criteria (not 'done' as a bare word)?",
        args.yes))
    checks[-1].emit()

    checks.append(check_architecture(root)); checks[-1].emit()
    checks.append(check_next_steps(root)); checks[-1].emit()
    checks.append(check_memory(root)); checks[-1].emit()

    checks.append(check_prompted(
        "5b", "Wiki pages touched this session revised in place; gotchas harvested",
        "Every topic page this session learned something about revised IN PLACE, and "
        "hard-won gotchas harvested into .claude/skills/ (no speculative skills)?",
        args.yes))
    checks[-1].emit()

    # Item 6 — light lint. This one IS mechanical, so run it rather than ask.
    lint_findings = run_lint(root)
    c6 = Check("6", "Memory lint (light) — no unreconciled contradictions")
    if lint_findings:
        by_kind: dict[str, int] = {}
        for f in lint_findings:
            by_kind[f[0]] = by_kind.get(f[0], 0) + 1
        c6.note("lint findings: " + ", ".join(f"{k}×{v}" for k, v in sorted(by_kind.items())))
        ok, how = ask("Lint has findings — have they been reconciled (revise/merge/delete, "
                      "not a new dated file beside a wrong one)?", args.yes)
        c6.status = PASS if ok else PROMPT_
        c6.note(how + "  — see: tools/session.py lint")
    else:
        c6.note("clean")
    checks.append(c6); c6.emit()

    checks.append(check_experiments(root)); checks[-1].emit()
    checks.append(check_committed(root, args.yes)); checks[-1].emit()

    failed = [c for c in checks if c.status != PASS]
    print()
    print(f"{len(checks) - len(failed)}/{len(checks)} items pass.")
    if failed:
        print("NOT CLOSEABLE. Open items: " + ", ".join(c.number for c in failed))
        print("Skipping any item means the branch stays open.")
        return 1
    print("CLOSE ritual complete. Commit and push — the doc and memory updates ride")
    print("in the SAME commit, never as an afterthought.")
    return 0


# ---------------------------------------------------------------------------
# lint — the third verb
# ---------------------------------------------------------------------------

Finding = tuple[str, str, str]  # (kind, location, message)


def run_lint(root: Path) -> list[Finding]:
    """Lint memory/ and .claude/skills/. Returns findings; deletes nothing."""
    findings: list[Finding] = []
    memory_dir = root / "memory"
    skills_dir = root / ".claude" / "skills"

    pages: dict[str, Path] = {}
    for p in sorted(memory_dir.glob("*.md")):
        if p.name == "MEMORY.md":
            continue
        pages[p.stem] = p
    texts = {slug: read_text(p) for slug, p in pages.items()}
    dated = {slug for slug in pages if DATED_PAGE_RE.match(slug + ".md")}
    wiki_pages = set(pages) - dated

    # --- dead wikilinks ----------------------------------------------------
    inbound: dict[str, set[str]] = {slug: set() for slug in pages}
    for slug, text in texts.items():
        for target in extract_wikilinks(text) | extract_md_local_links(text):
            if target in pages:
                inbound.setdefault(target, set()).add(slug)
            elif target != slug:
                findings.append(("dead-link", f"memory/{slug}.md", f"[[{target}]] has no memory/{target}.md"))

    # --- orphans (wiki layer only; a dated log page nothing links to is normal)
    for slug in sorted(wiki_pages):
        if not inbound.get(slug):
            findings.append((
                "orphan", f"memory/{slug}.md",
                "no other memory page links to it — link it from a related page or "
                "merge it; the index alone is not a link",
            ))

    # --- index sync --------------------------------------------------------
    index_path = memory_dir / "MEMORY.md"
    entries = parse_memory_index(read_text(index_path))
    targets = {e["target"] for e in entries}
    for e in entries:
        if not (memory_dir / e["target"]).exists():
            findings.append(("index", "memory/MEMORY.md", f"indexes {e['target']}, which does not exist"))
    for slug, p in pages.items():
        if p.name not in targets:
            findings.append(("index", f"memory/{p.name}", "exists but has no MEMORY.md index line"))
    index_lines = len([l for l in read_text(index_path).splitlines() if l.strip()])
    if index_lines > 60:
        findings.append((
            "index-size", "memory/MEMORY.md",
            f"{index_lines} non-blank lines — past one screen. CLAUDE.md says that is "
            "the trigger for a merge pass.",
        ))

    # --- duplicate topics --------------------------------------------------
    titles: dict[str, str] = {}
    for slug, text in texts.items():
        m = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
        titles[slug] = re.sub(r"[^a-z0-9 ]", "", (m.group(1) if m else slug).lower()).strip()
    wiki_list = sorted(wiki_pages)
    for i, a in enumerate(wiki_list):
        for b in wiki_list[i + 1:]:
            same_title = titles[a] and titles[a] == titles[b]
            overlap = jaccard(slug_tokens(a), slug_tokens(b))
            if same_title or overlap >= 0.5:
                findings.append((
                    "duplicate", f"memory/{a}.md + memory/{b}.md",
                    f"look like the same topic ({'identical H1' if same_title else f'slug overlap {overlap:.2f}'}) "
                    "— merge into one page and delete the loser",
                ))

    # --- stale claims (heuristic: candidates, never proof) ------------------
    # Build: token -> {slug: [(is_negating, line)]}. If one page negates a claim
    # token and another asserts it plainly, the second is a stale-claim candidate.
    claims: dict[str, dict[str, list[tuple[bool, str]]]] = {}
    for slug, text in texts.items():
        for line in text.splitlines():
            for tok in claim_tokens(line):
                claims.setdefault(tok, {}).setdefault(slug, []).append(
                    (line_is_negating(line), line.strip())
                )
    for tok, per_page in sorted(claims.items()):
        if len(per_page) < 2:
            continue
        negators = {s for s, occ in per_page.items() if any(neg for neg, _ in occ)}
        asserters = {s for s, occ in per_page.items() if any(not neg for neg, _ in occ)}
        plain = asserters - negators
        if negators and plain:
            for s in sorted(plain):
                example = next(line for neg, line in per_page[sorted(negators)[0]] if neg)
                findings.append((
                    "stale-claim?", f"memory/{s}.md",
                    f"still states {tok!r}, which {sorted(negators)[0]}.md marks as "
                    f"superseded/wrong: “{example[:110]}”  — VERIFY BY READING, this is a "
                    "text heuristic, not a fact",
                ))

    # --- skills ------------------------------------------------------------
    if skills_dir.is_dir():
        for d in sorted(p for p in skills_dir.iterdir() if p.is_dir()):
            skill_md = d / "SKILL.md"
            if not skill_md.exists():
                findings.append(("skill", str(d.relative_to(root)), "directory with no SKILL.md"))
                continue
            text = read_text(skill_md)
            fm = parse_frontmatter(text)
            rel = str(skill_md.relative_to(root))
            for key in ("name", "description"):
                if not fm.get(key):
                    findings.append(("skill", rel, f"frontmatter has no `{key}:`"))
            if fm.get("name") and fm["name"] != d.name:
                findings.append(("skill", rel, f"frontmatter name {fm['name']!r} != directory {d.name!r}"))
            if fm.get("provisional", "").lower() in {"true", "yes", "1"}:
                confirm = fm.get("confirmed-by") or "(no confirmed-by: stated)"
                findings.append((
                    "skill-provisional", rel,
                    f"still provisional — confirmed by: {confirm}. Promote it or delete it "
                    "once the run that would confirm it has happened.",
                ))
            for ref in set(re.findall(r"`([A-Za-z0-9_./-]+/[A-Za-z0-9_./-]+)`", text)):
                base = ref.split("#")[0].rstrip("/")
                if base.startswith(("http", "/dev/", "/etc/", "/usr/", "/sys/")):
                    continue
                if re.search(r"\.(md|py|sh|yaml|yml|c|h|txt|csv|sr)$", base) and not (root / base).exists():
                    findings.append(("skill-stale", rel, f"references `{base}`, which does not exist"))
    return findings


def cmd_lint(root: Path, args: argparse.Namespace) -> int:
    findings = run_lint(root)
    print(f"memory/skill lint — {utc_today().isoformat()} (UTC)")
    print("Reports only. It never deletes anything; merging and deleting are judgement calls.\n")
    if not findings:
        print("clean — no findings.")
        return 0
    by_kind: dict[str, list[Finding]] = {}
    for f in findings:
        by_kind.setdefault(f[0], []).append(f)
    for kind in sorted(by_kind):
        print(f"— {kind} ({len(by_kind[kind])})")
        for _, loc, msg in by_kind[kind]:
            print(f"    {loc}")
            for line in re.sub(r"\s+", " ", msg).split(". "):
                if line.strip():
                    print(f"        {line.strip()}")
    print(f"\n{len(findings)} finding(s). Merge, revise, or delete — reactive deletion alone")
    print("is not maintenance. `stale-claim?` findings are candidates: read before acting.")
    return 1 if args.strict else 0


# ---------------------------------------------------------------------------
# index — the annotated repo index, from checked-in data
# ---------------------------------------------------------------------------


def cmd_index(root: Path, args: argparse.Namespace) -> int:
    index_path = root / "docs" / "INDEX.yaml"
    if not index_path.exists():
        print("session.py index: docs/INDEX.yaml does not exist", file=sys.stderr)
        return 2
    try:
        groups = parse_index_yaml(read_text(index_path))
    except ValueError as exc:
        print(f"session.py index: {exc}", file=sys.stderr)
        return 2

    # Both sides of this audit come from git. If git cannot answer, the
    # "which files are not indexed?" half did not run at all — and an audit
    # that did not run must not exit 0, because `--check` is what other
    # people trust to mean "the index is complete". Same rule as `close`:
    # never a silent pass.
    rc_git, _ = git(root, "rev-parse", "--git-dir")
    git_ok = rc_git == 0

    tracked = set(git_tracked(root))
    present = set(git_files(root))
    indexed: set[str] = set()

    print(f"Baby Rover — annotated repo index · {utc_today().isoformat()} (UTC)")
    print("Source: docs/INDEX.yaml (maintained data, not regenerated guesswork)\n")

    width = max((len(p) for _, es in groups for p, _ in es), default=20)
    width = min(width, 46)
    for group, entries in groups:
        print(f"{group}")
        for path, purpose in entries:
            indexed.add(path)
            on_disk = (root / path).exists()
            if not on_disk:
                mark = "  ⬜ not created yet"
            elif path not in tracked:
                mark = "  · untracked"
            else:
                mark = ""
            print(f"  {path:<{width}}  {purpose}{mark}")
        print()

    missing_from_index = sorted(present - indexed)
    if missing_from_index:
        print("NOT IN docs/INDEX.yaml — add a one-line purpose for each:")
        for p in missing_from_index:
            print(f"  {p}")
        print()
    counts = sum(len(e) for _, e in groups)
    print(f"{counts} indexed path(s) in {len(groups)} group(s); "
          f"{len(present)} path(s) in the working tree; "
          f"{len(missing_from_index)} unindexed.")
    if not git_ok:
        print(
            "\nWARNING: git could not list the working tree here, so the unindexed-file\n"
            "half of this audit did not run. What printed above is the INDEX only. It\n"
            "is NOT evidence that every file has a purpose recorded.",
            file=sys.stderr,
        )
        if args.check:
            return 2
    if args.check and missing_from_index:
        return 1
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="tools/session.py",
        description="Baby Rover session rituals, made mechanical. See CLAUDE.md.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Session state is NEXT-STEPS.md. The journal is the memory/ log layer.\n"
            "This tool maintains those; it does not replace them."
        ),
    )
    p.add_argument("--repo", default=None, help="repo root (default: found from this script)")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("start", help="orientation brief for a fresh session (reads only)")
    s.set_defaults(func=cmd_start)

    j = sub.add_parser("journal", help="create/append today's memory/YYYY-MM-DD-<slug>.md")
    j.add_argument("slug", help="lowercase-hyphenated slug for today's entry")
    j.add_argument("--summary", help="the one-line MEMORY.md index entry (required to create)")
    j.add_argument("-m", "--message", action="append", default=[],
                   help="append a timestamped line to the Session log (repeatable)")
    j.set_defaults(func=cmd_journal)

    w = sub.add_parser("wiki", help="revise an undated memory/ topic page, safely")
    w.add_argument("slug", help="topic slug, e.g. n20-motors")
    w.add_argument("--summary", help="the one-line MEMORY.md index entry (required to create)")
    w.add_argument("-m", "--append", action="append", default=[],
                   help="append a dated '## Revised' section (repeatable)")
    w.add_argument("--from-file", metavar="FILE",
                   help="replace the page wholesale from FILE (refused on reviewed pages)")
    w.add_argument("--force", action="store_true",
                   help="allow --from-file to replace an existing, non-reviewed page")
    w.add_argument("--check", action="store_true",
                   help="exit 1 if the page is reviewed: true; print status and stop")
    w.set_defaults(func=cmd_wiki)

    c = sub.add_parser("close", help="run the CLOSE ritual as a checked checklist")
    c.add_argument("--yes", action="store_true",
                   help="assert every human-judgement item without prompting (say so in the log)")
    c.set_defaults(func=cmd_close)

    l = sub.add_parser("lint", help="lint memory/ and .claude/skills/ — the third verb")
    l.add_argument("--strict", action="store_true", help="exit 1 when there are findings")
    l.set_defaults(func=cmd_lint)

    i = sub.add_parser("index", help="print the annotated repo index from docs/INDEX.yaml")
    i.add_argument("--check", action="store_true",
                   help="exit 1 if any working-tree file is missing from docs/INDEX.yaml")
    i.set_defaults(func=cmd_index)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.repo).resolve() if args.repo else find_repo_root()
    if not root.is_dir():
        print(f"session.py: not a directory: {root}", file=sys.stderr)
        return 2
    try:
        return int(args.func(root, args))
    except BrokenPipeError:  # `| head`
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return 0
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
