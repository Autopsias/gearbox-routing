"""List what the auto-mode classifier refused, by reason, with the commands.

Window and file selection are the digest's (digest_sessions.select_files):
main-session files only, chosen by file time; subagent refusals are in neither count.
A refusal is counted per tool_result block (is_error true), exactly as the digest
does, never per regex match on a line (the text is stored twice per line).
The report can hold client terms: it is written outside the repo.
"""
import argparse
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from digest_sessions import (CLASSIFIER_RE, DEFAULT_PROJECTS, HOME,  # noqa: E402
                             _iter_records, select_files)

DEFAULT_OUT = HOME / ".claude" / "usage-data" / "classifier-report.md"


def refusals(path: Path):
    """Yield (reason, tool, command[:160], project, session_id) per refused call."""
    uses = {}
    for rec in _iter_records(path):
        content = (rec.get("message") or {}).get("content")
        if not isinstance(content, list):
            continue
        for it in content:
            if not isinstance(it, dict):
                continue
            if it.get("type") == "tool_use":
                uses[it.get("id")] = it
            # the digest counts tool_result blocks in "user" records only
            elif rec.get("type") == "user" and it.get("type") == "tool_result" and it.get("is_error"):
                reasons = CLASSIFIER_RE.findall(str(it.get("content")))
                use = uses.get(it.get("tool_use_id")) or {}
                inp = use.get("input") or {}
                cmd = inp.get("command") if isinstance(inp, dict) and "command" in inp else inp
                cmd = " ".join(str(cmd).split())[:160]
                for r in reasons:
                    yield r, use.get("name", "?"), cmd, path.parent.name, path.stem


def collect(files):
    rows = []
    for f in files:
        try:
            rows.extend(refusals(f))
        except Exception as e:  # noqa: BLE001 — report loop keeps going: a bad file is skipped with a stderr note
            sys.stderr.write(f"[classifier_report] skipped {f}: {e}\n")
    return rows


def render(rows, n_files, since_days):
    by = defaultdict(list)
    for row in rows:
        by[row[0]].append(row)
    out = [f"# Auto-mode refusals, last {since_days} days" if since_days else "# Auto-mode refusals",
           "",
           "Window and file selection are the digest's: main-session files only, chosen by file time. "
           "Subagent refusals are in neither count.",
           f"Files read: {n_files}. Total refusals: {len(rows)}. "
           f"Sessions with a refusal: {len({r[4] for r in rows})}.", "",
           "| Reason | Count | Sessions |", "|---|---|---|"]
    for reason, rs in sorted(by.items(), key=lambda kv: -len(kv[1])):
        out.append(f"| {reason} | {len(rs)} | {len({r[4] for r in rs})} |")
    for reason, rs in sorted(by.items(), key=lambda kv: -len(kv[1])):
        out += ["", f"## {reason} ({len(rs)})", ""]
        for (tool, cmd), n in Counter((r[1], r[2]) for r in rs).most_common(5):
            out.append(f"- {n}x `{tool}`: `{cmd}`")
    return "\n".join(out) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--projects", default=str(DEFAULT_PROJECTS))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--since-days", type=int, default=None)
    ap.add_argument("--include-temp-projects", action="store_true")
    a = ap.parse_args(argv)
    files, _, _ = select_files(Path(a.projects), a.since_days, a.include_temp_projects)
    rows = collect(files)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(rows, len(files), a.since_days))
    print(f"{len(rows)} refusals in {len(files)} files -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
