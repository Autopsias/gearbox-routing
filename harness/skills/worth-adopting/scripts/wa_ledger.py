#!/usr/bin/env python3
"""Read and write docs/worth-adopting/LEDGER.md without printing it whole.

Why: a ledger row is 1 to 5 KB, and ad-hoc reads overflowed the tool output five times
(2026-11-13 three, 2026-11-17 two). A cell cut at 500 characters then became a wrong fact
in a report. Every list command here prints short lines; `row` prints one row in full.

    wa_ledger.py find <regex>            rows that match, one short line each
    wa_ledger.py row <text> [--field F]  ONE row in full (F: date source author url verdicts
                                      largest report decision result)
    wa_ledger.py open [--today D]        rows still open, or with a due date that has passed
    wa_ledger.py overrides [--all]       owner overrides not marked absorbed
    wa_ledger.py retro-due               says when a retro is due
    wa_ledger.py add-row < row.txt       put a new row at the end of the table
    wa_ledger.py add-override "<line>"   put a line at the end of the overrides
    wa_ledger.py note <text> "<note>" [--field decision]
                                      add a note to the Result (or Decision) cell of one row;
                                      a cell that says only "open" is replaced
    wa_ledger.py normalize [--write]     move rows that sit below the overrides back to the table

`--ledger PATH` names the file; the default is docs/worth-adopting/LEDGER.md under the
current directory.
"""
import argparse
import datetime
import os
import re
import sys

FIELDS = ["date", "source", "author", "url", "verdicts", "largest", "report", "decision", "result"]
ROW = re.compile(r"^\| \d{4}-\d{2}-\d{2} \|")
OVERRIDE = re.compile(r"^- \d{4}-\d{2}-\d{2} ")
HEADING = "## Owner overrides"
LINE_CAP = 600      # one list line
TOTAL_CAP = 12000   # one list command
DONE = re.compile(r"\b(closed|met|none needed|no measurement defined|declined)\b", re.I)


def cells(line):
    """Split one row. Only the Verdicts cell may hold ' | ', so cut 4 from each side."""
    body = line.strip()
    body = body[1:] if body.startswith("|") else body
    body = body[:-1] if body.endswith("|") else body
    left = body.split(" | ", 4)
    if len(left) < 5:
        raise ValueError("row has fewer than 9 cells: " + line[:80])
    right = left[4].rsplit(" | ", 4)
    if len(right) < 5:
        raise ValueError("row has fewer than 9 cells: " + line[:80])
    return dict(zip(FIELDS, [c.strip() for c in left[:4] + right]))


def short(text, cap=LINE_CAP):
    return text if len(text) <= cap else text[: cap - 12] + " …(+%d)" % (len(text) - cap + 12)


def emit(lines):
    """Print list output under one total cap, and say what was left out."""
    used = 0
    for i, line in enumerate(lines):
        line = short(line)
        if used + len(line) > TOTAL_CAP:
            print("… %d more lines not shown; narrow the search" % (len(lines) - i))
            return
        print(line)
        used += len(line) + 1


def load(path):
    with open(path, encoding="utf-8") as f:
        return f.read().split("\n")


def save(path, lines):
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines).rstrip("\n") + "\n")


def rows(lines):
    return [(i, line) for i, line in enumerate(lines) if ROW.match(line)]


def heading_at(lines):
    for i, line in enumerate(lines):
        if line.strip() == HEADING:
            return i
    return None


def table_end(lines):
    """Index after the last row of the main table (the rows above the overrides heading)."""
    stop = heading_at(lines)
    stop = len(lines) if stop is None else stop
    last = [i for i, _ in rows(lines) if i < stop]
    if not last:  # a new ledger: the row goes below the header separator
        last = [i for i, ln in enumerate(lines[:stop]) if ln.startswith("|---")]
    if not last:
        raise SystemExit("ledger: no table found above '%s'" % HEADING)
    return last[-1] + 1


def match_one(lines, text):
    hits = [(i, ln) for i, ln in rows(lines) if ln.startswith("| %s |" % text)]  # a date names its own row first
    if len(hits) != 1:
        hits = [(i, ln) for i, ln in rows(lines) if text.lower() in " ".join(ln.split(" | ")[:4]).lower()]
    if len(hits) != 1:
        emit(["%d rows match '%s' in date, source, author or url:" % (len(hits), text)]
             + ["  " + " | ".join(ln.split(" | ")[:2])[2:] for _, ln in hits])
        raise SystemExit(2)
    return hits[0]


def cmd_find(a, lines):
    rx = re.compile(a.regex, re.I)
    out = []
    for _, line in rows(lines):
        if rx.search(line):
            c = cells(line)
            out.append("%s | %s | %s | decision: %s" % (c["date"], short(c["source"], 80),
                                                       short(c["verdicts"], 300), short(c["decision"], 100)))
    emit(out or ["no row matches"])


def cmd_row(a, lines):
    _, line = match_one(lines, a.text)
    c = cells(line)
    for name in ([a.field] if a.field else FIELDS):
        print("%s: %s" % (name, c[name]))  # in full on purpose: one row, asked for by name


EVENT = re.compile(r"(?P<open>^open\b|\bstill open\b)|(?P<due>\bdue (\d{4}-\d{2}-\d{2}))|(?P<done>%s)" % DONE.pattern, re.I)


def is_open(c, today):
    """The LAST status word in a cell decides: a cell grows by notes, so old words stay in it."""
    for name in ("decision", "result"):
        last = None
        for last in EVENT.finditer(c[name]):
            pass
        if last is None or last.group("done"):
            continue
        if last.group("open"):
            return "%s open" % name
        if name == "result" and last.group(3) <= today:
            return "due %s passed" % last.group(3)
    return None


def cmd_open(a, lines):
    today = a.today or datetime.date.today().isoformat()
    out = []
    for _, line in rows(lines):
        c = cells(line)
        why = is_open(c, today)
        if why:
            out.append("%s | %s | %s | result ends: …%s" % (c["date"], short(c["source"], 70), why, c["result"][-300:]))
    emit(out or ["no open row"])


def override_lines(lines):
    return [(i, ln) for i, ln in enumerate(lines) if OVERRIDE.match(ln)]


def cmd_overrides(a, lines):
    out = [ln for _, ln in override_lines(lines) if a.all or "absorbed" not in ln]
    emit(out or ["no override"])


def cmd_retro_due(a, lines):
    rs = rows(lines)
    last = max([n for n, (_, ln) in enumerate(rs) if cells(ln)["source"].lower().startswith("retro")] or [-1])
    since = len(rs) - 1 - last
    live = sum(1 for _, ln in override_lines(lines) if "absorbed" not in ln)
    print("rows after the last retro: %d (bar 8) · overrides not absorbed: %d (bar 3)" % (since, live))
    if since >= 8 or live >= 3:
        print("Enough outcomes for a retro: `/worth-adopting retro`.")


BIG_PICTURE = ("## Above the target", "## Principles")
REPORT_PATH = re.compile(r"`(docs/worth-adopting/[^`]+\.md)`")


def check_big_picture(row, ledger_path):
    """Refuse a row whose report skips the look past the target: six owner corrections to 2026-11-18."""
    found = REPORT_PATH.search(row["report"])
    if row["source"].lower().startswith("retro") or not found:
        return  # shortcut: a report outside docs/worth-adopting/ is not checked; none exists today
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(ledger_path))))
    report = os.path.join(root, found.group(1))
    if not os.path.isfile(report):
        raise SystemExit("ledger: write the report %s before its row" % found.group(1))
    text = load(report)
    for head in BIG_PICTURE:
        if head not in text:
            raise SystemExit("ledger: %s has no '%s' section" % (found.group(1), head))
        body = text[text.index(head) + 1:]
        body = body[:next((i for i, ln in enumerate(body) if ln.startswith("## ")), len(body))]
        if not any(ln.startswith("- ") and not ln.startswith(("- <", "- **<")) for ln in body):
            raise SystemExit("ledger: '%s' in %s is empty or still the template; write a find, "
                             "or '- none: <the reason>'" % (head, found.group(1)))


def cmd_add_row(a, lines, path):
    new = [ln for ln in sys.stdin.read().split("\n") if ln.strip()]
    if len(new) != 1 or not ROW.match(new[0]):
        raise SystemExit("ledger: give exactly one line that starts with '| YYYY-MM-DD |'")
    check_big_picture(cells(new[0]), path)  # cells() also refuses a row with the wrong number of cells
    stray = stray_rows(lines)
    if stray:  # else the new row lands above older rows and retro-due counts from the wrong place
        move_stray(lines, stray)
        print("moved %d table rows from below '%s' back into the table first" % (len(stray), HEADING))
    at = table_end(lines)
    lines.insert(at, new[0].rstrip())
    save(path, lines)
    print("row added at line %d, at the end of the table" % (at + 1))


def cmd_add_override(a, lines, path):
    if not OVERRIDE.match(a.line) or "the owner said:" not in a.line:
        raise SystemExit("ledger: format is '- <date> · <source> · the run said: <…> · the owner said: \"<…>\"'")
    if heading_at(lines) is None:
        raise SystemExit("ledger: no '%s' heading" % HEADING)
    existing = override_lines(lines)
    at = (existing[-1][0] if existing else heading_at(lines)) + 1
    lines.insert(at, a.line.rstrip())
    save(path, lines)
    print("override added at line %d" % (at + 1))


def cmd_note(a, lines, path):
    i, line = match_one(lines, a.text)
    cells(line)
    head, decision, result = line.rstrip()[:-1].rstrip().rsplit(" | ", 2)
    cell = {"decision": decision, "result": result}
    old = cell[a.field].strip()
    cell[a.field] = a.note.strip() if old.lower() == "open" else old + " " + a.note.strip()
    lines[i] = " | ".join([head, cell["decision"], cell["result"]]) + " |"
    save(path, lines)
    print("note added to the %s cell of the %s row" % (a.field, line[2:12]))


def stray_rows(lines):
    head = heading_at(lines)
    return [(i, ln) for i, ln in rows(lines) if head is not None and i > head]


def move_stray(lines, stray):
    for i, _ in reversed(stray):
        del lines[i]
    at = table_end(lines)
    lines[at:at] = [ln for _, ln in stray]


def cmd_normalize(a, lines, path):
    stray = stray_rows(lines)
    print("%d table rows sit below '%s'" % (len(stray), HEADING))
    if not stray or not a.write:
        return
    move_stray(lines, stray)
    save(path, lines)
    print("moved them to the end of the table; the overrides keep their order")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--ledger", default="docs/worth-adopting/LEDGER.md")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("find").add_argument("regex")
    s = sub.add_parser("row")
    s.add_argument("text")
    s.add_argument("--field", choices=FIELDS)
    sub.add_parser("open").add_argument("--today")
    sub.add_parser("overrides").add_argument("--all", action="store_true")
    sub.add_parser("retro-due")
    sub.add_parser("add-row")
    sub.add_parser("add-override").add_argument("line")
    s = sub.add_parser("note")
    s.add_argument("text")
    s.add_argument("note")
    s.add_argument("--field", choices=["decision", "result"], default="result")
    sub.add_parser("normalize").add_argument("--write", action="store_true")
    a = p.parse_args(argv)
    lines = load(a.ledger)
    read_only = {"find": cmd_find, "row": cmd_row, "open": cmd_open, "overrides": cmd_overrides,
                 "retro-due": cmd_retro_due}
    if a.cmd in read_only:
        read_only[a.cmd](a, lines)
    else:
        {"add-row": cmd_add_row, "add-override": cmd_add_override, "note": cmd_note,
         "normalize": cmd_normalize}[a.cmd](a, lines, a.ledger)


if __name__ == "__main__":
    main()
