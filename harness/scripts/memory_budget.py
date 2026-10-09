#!/usr/bin/env python3
"""memory_budget — what each project's auto-memory costs and where it drifts.

    python3 scripts/memory_budget.py [--root ~/.claude/projects] [--age-days 90]

REPORT ONLY (the owner's call): the learning-capture rule stays —
"delete, don't archive; age alone is never stale" — so nothing here moves,
decays or deletes a note. It shows the numbers a decay pass would act on, so the
decision can be made on data instead of on kunchenguid/firstmate's defaults.

The budget that matters is MEMORY.md: it is loaded into context at EVERY session
start (tokens ~ bytes/4). The notes themselves cost nothing until recalled.
Per project: index size, note count and bytes, notes untouched for --age-days
(mtime), the biggest notes, and the two kinds of drift — a note the index does
not link (unreachable) and an index link with no file (dangling).
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

LINK_RE = re.compile(r"\]\(([^)#]+\.md)\)")
WIKI_RE = re.compile(r"\[\[([^\]|#]+)")   # [[name]] -> name.md beside the index


def report_project(mem: Path, age_days: int, now: float | None = None) -> dict:
    now = now or time.time()
    index = mem / "MEMORY.md"
    notes = sorted(p for p in mem.rglob("*.md") if p.name != "MEMORY.md")
    # Reachable = linked from MEMORY.md, or from a file it links (some projects keep
    # MEMORY-ARCHIVE-*.md index files that link the closed programs' notes).
    linked, todo = set(), [index] if index.exists() else []
    while todo:
        src = todo.pop()
        text = src.read_text(errors="replace")
        targets = LINK_RE.findall(text) + [w.strip() + ".md" for w in WIKI_RE.findall(text)]
        for target in targets:
            t = (src.parent / target).resolve()
            if t not in linked:
                linked.add(t)
                if t.exists() and t.name.startswith("MEMORY"):
                    todo.append(t)
    sizes = {p: p.stat().st_size for p in notes}
    old = [p for p in notes if now - p.stat().st_mtime > age_days * 86400]
    index_lines = index.read_text(errors="replace").splitlines() if index.exists() else []
    long_lens = [len(line) for line in index_lines if len(line) > 200]
    return {
        "project": mem.parent.name.replace("-Users-" + Path.home().name + "-", ""),
        "index_bytes": index.stat().st_size if index.exists() else 0,
        "notes": len(notes),
        "note_bytes": sum(sizes.values()),
        "old": len(old),
        "biggest": sorted(sizes.items(), key=lambda kv: -kv[1])[:3],
        "unreachable": [p for p in notes if p.resolve() not in linked],
        "dangling": sorted(t.name for t in linked if not t.exists()),
        "long_lines": len(long_lens),
        "longest_line": max(long_lens) if long_lens else 0,
    }


def render(rows: list[dict], age_days: int) -> str:
    out = ["%-46s %8s %6s %8s %6s %7s %6s %6s %7s" % (
        "project", "index~tok", "notes", "note KB", f">{age_days}d", "unrch", "dangl",
        ">200c", "longest")]
    for r in rows:
        out.append("%-46s %8d %6d %8.0f %6d %7d %6d %6d %7d" % (
            r["project"][-46:], r["index_bytes"] // 4, r["notes"], r["note_bytes"] / 1024,
            r["old"], len(r["unreachable"]), len(r["dangling"]),
            r["long_lines"], r["longest_line"]))
    out.append("")
    out.append("index~tok = MEMORY.md bytes/4, loaded at every session start. "
               "unrch = notes MEMORY.md does not link. dangl = index links with no file. "
               ">200c = index lines over 200 chars (report only, nothing here shortens them). "
               "longest = longest index line, chars.")
    for r in rows:
        if r["biggest"]:
            out.append("%s biggest: %s" % (r["project"][-30:], ", ".join(
                "%s (%dKB)" % (p.name, s // 1024) for p, s in r["biggest"])))
        for t in r["dangling"]:
            out.append("%s dangling: %s" % (r["project"][-30:], t))
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=str(Path.home() / ".claude" / "projects"))
    ap.add_argument("--age-days", type=int, default=90)
    args = ap.parse_args(argv)
    mems = sorted(p for p in Path(args.root).expanduser().glob("*/memory") if p.is_dir())
    rows = [report_project(m, args.age_days) for m in mems]
    rows = [r for r in rows if r["notes"] or r["index_bytes"]]   # scratch dirs with no memory
    rows.sort(key=lambda r: -r["index_bytes"])
    print(render(rows, args.age_days))
    return 0


if __name__ == "__main__":
    sys.exit(main())
