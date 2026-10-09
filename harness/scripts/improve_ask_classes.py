#!/usr/bin/env python3
"""Count the questions agents asked the owner (AskUserQuestion), group them into
classes, and apply the bar for a standing default.

Ported from two measurement scripts. Reads Claude Code transcripts:
the tool_use input holds questions[] (question, header, options[].label); the
matching tool_result line holds toolUseResult.answers (keyed by question text)
and a content string 'Your questions have been answered: "<q>"="<label>", ...'.

Nothing runs at import and nothing binds the home directory at import: the
projects directory is a parameter, resolved inside the function.

ponytail: regex classes, first match wins; a model reads --members / --candidates
for the judgement a regex cannot make (do the members share ONE answer?).
Exit 0 = counted fine; 2 = the transcript format looks changed (fail loud).
"""
import argparse
import collections
import datetime as dt
import glob
import json
import os
import re
import sys
from pathlib import Path

BAR_MIN_QUESTIONS = 10
BAR_FIRST_PCT = 95
BAR_MIN_SESSIONS = 3
NEAR_MIN_PCT = 85
UNREADABLE_MAX_PCT = 20
FREE_MAX_PCT = 15

REWORK = re.compile(r"\bis halted\b|\bhalted\b|rework|re-gate|budget (is |was )?(spent|exhausted)|review (gate|found)|adversarial (review|gate)|gate (failed|came back red|keeps|is pinned|exhausted)|failed (the gate|three|a third)|\b(round|attempt) \d (found|closed|still|came)|more edge cases|(third|fourth|fifth|sixth) (rework|time|adversarial)|how (do you want|should i|do i) (to |it )?close|\bclose s\d")
IRREV = r"land|merge|push|deploy|publish|delete|remove|discard|release|install|retire|raise|force-push|testpypi|fire"
MONEY = r"\$\d|\bspend\b|wallet|top up"
# Excluded terms: the source script's IRREV and MONEY terms, whole list.
EXCLUDED_TERMS = re.compile(rf"\b(?:{IRREV})\b|{MONEY}")
MONEY_RE = re.compile(MONEY)
IRREV_RE = re.compile(rf"\b({IRREV})\b")
NOW = re.compile(r"\b(now|today)\b|^(run|arm|start|restart|load)\b")
LATER = re.compile(r"wait|not now|hold|later|tomorrow|02:00|tonight|first")

# (key, slug, label); order = classification order, first match wins.
CLASSES = [
    ("1", "improve-finding", "/improve finding: accept or reject"),
    ("1b", "improve-standing-default", "/improve standing default question"),
    ("5", "improve-scope", "/improve scope"),
    ("2", "gate-red", "gate red or rework spent: how to close"),
    ("3x", "money", "money"),
    ("3", "irreversible", "land, merge, deploy, publish, delete"),
    ("4", "run-now-or-wait", "run it now or wait"),
    ("6", "approve-changeset", "approve a prepared changeset"),
    ("7", "harden-plan", "harden the plan?"),
    ("9", "one-off", "one-off design or scope decision"),
]
SLUG = {k: s for k, s, _ in CLASSES}
LABEL = {k: lab for k, _, lab in CLASSES}
# Never eligible for a standing default, whatever the numbers. Explicit keys.
NEVER_ELIGIBLE = {
    "1": "/improve's own question",
    "1b": "/improve's own question",
    "5": "/improve's own question",
    "3x": "money",
    "3": "land, merge, deploy, publish or delete",
    "9": "one-off class mixes unrelated questions (use --candidates)",
}


def classify(question: str, opts: list, header: str) -> str:
    ql, a, hl = question.lower(), opts[0].lower(), header.lower()
    b = (opts[1] if len(opts) > 1 else "").lower()
    if (a.startswith("accept") and re.match(r"reject|modify|accept", b)) or a.startswith("apply all"):
        return "1"
    if a.startswith("apply this default"):
        return "1b"
    if "retrospective cover" in ql:
        return "5"
    if REWORK.search(ql):
        return "2"
    if MONEY_RE.search(ql) or MONEY_RE.search(a) or "spend" in hl:
        return "3x"
    if IRREV_RE.search(a) or IRREV_RE.search(hl):
        return "3"
    if NOW.search(a) and LATER.search(b):
        return "4"
    if a.startswith("approve"):
        return "6"
    if "harden" in ql:
        return "7"
    return "9"


def norm_label(label: str) -> str:
    return re.sub(r"\s*\(recommended\)\s*", "", label.lower()).strip()


def norm_text(text: str) -> str:
    return " ".join(text.lower().split())


def default_projects_dir() -> Path:
    return Path.home() / ".claude" / "projects"


def parse_ts(ts):
    try:
        t = dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def answer_for(question: str, result_line: dict, content):
    """The answer to one question: toolUseResult.answers first, else the content string."""
    tr = result_line.get("toolUseResult")
    if isinstance(tr, dict) and isinstance(tr.get("answers"), dict):
        a = tr["answers"].get(question)
        if isinstance(a, str):
            return a
    if isinstance(content, str):
        m = re.search(r'"' + re.escape(question) + r'"="(.*?)"(?:, "|\. You can now|$)', content, re.S)
        if m:
            return m.group(1)
    return None


# Line types that quote the tool's name without calling it (measured:
# 790 'attachment' prompt snapshots in 30 days). Anything else is a call or a format change.
NOT_A_CALL = {"attachment"}
ASK_NAME = re.compile(r'"name":\s*"AskUserQuestion"')


def ask_blocks(d):
    """tool_use blocks of one parsed line, or None when the shape is unexpected."""
    if not isinstance(d, dict):
        return None
    c = (d.get("message") or {}).get("content") if isinstance(d.get("message"), dict) else None
    if not isinstance(c, list):
        return None
    out = [p for p in c if isinstance(p, dict) and p.get("type") == "tool_use" and p.get("name") == "AskUserQuestion"]
    if not out or not all(isinstance(p.get("input"), dict) and isinstance(p["input"].get("questions"), list) for p in out):
        return None
    return out


def _note_ask(line, res, pending, cutoff):
    """One line that names AskUserQuestion: count it and remember its questions as pending."""
    try:
        d = json.loads(line)
    except ValueError:
        d = None
    if isinstance(d, dict) and d.get("type") in NOT_A_CALL:
        return  # tool-schema text inside a snapshot, not a call
    res["ask_lines"] += 1
    blocks = ask_blocks(d)
    ts = parse_ts(d.get("timestamp")) if isinstance(d, dict) else None
    if blocks is None or ts is None:
        res["unreadable"] += 1
        return
    res["parsed_lines"] += 1
    for p in blocks:
        qs = [q for q in p["input"]["questions"] if isinstance(q, dict)]
        res["questions"] += len(qs)
        if ts >= cutoff:
            pending[p.get("id")] = (ts, qs)


def _resolve_asks(ts, qs, d, p, repo, session, res):
    """Turn one answered tool_result into entries; count the answers we could not read."""
    for q in qs:
        opts = [o.get("label", "") for o in q.get("options") or [] if isinstance(o, dict)]
        question = q.get("question") or ""
        ans = answer_for(question, d, p.get("content"))
        if not opts or ans is None:
            res["unparsed_answers"] += 1  # answer slot we could not read
            continue
        res["entries"].append({
            "date": ts.strftime("%Y-%m-%d"), "repo": repo, "question": question,
            "header": q.get("header") or "", "opts": opts, "answer": ans,
            "session": session, "key": classify(question, opts, q.get("header") or ""),
        })


def _note_result(line, res, pending, repo, session):
    """One tool_result line that mentions a pending id: resolve the matching questions."""
    res["result_lines"] += 1
    try:
        d = json.loads(line)
        items = d["message"]["content"]
    except (ValueError, KeyError, TypeError):
        items = None
    if not isinstance(items, list):
        res["unreadable_results"] += 1
        return
    for p in items:
        if not (isinstance(p, dict) and p.get("type") == "tool_result" and p.get("tool_use_id") in pending):
            continue
        ts, qs = pending.pop(p["tool_use_id"])
        _resolve_asks(ts, qs, d, p, repo, session, res)


def _scan_file(f, lines, cutoff, res):
    proj = os.path.basename(os.path.dirname(f))
    repo = re.sub(r"^-Users-[^-]+-(DeveloperFolder-)?", "", proj).split("--")[0]
    pending = {}
    for line in lines:
        if ASK_NAME.search(line):
            _note_ask(line, res, pending, cutoff)
        elif pending and "tool_result" in line and any(i and i in line for i in pending):
            _note_result(line, res, pending, repo, Path(f).stem)
    # Whatever is still pending was asked in the window and never matched a result line
    # (renamed key, new id field, interrupted session): counted, so it cannot pass as a quiet month.
    res["no_result"] += sum(len(qs) for _, qs in pending.values())


def scan(projects_dir=None, days=30, after=None, now=None):
    """Read transcripts. Returns dict: entries (answered questions), ask_lines, parsed_lines,
    unreadable, questions, files. A question is in the window by its own timestamp; file mtime
    only chooses which files to open."""
    pdir = Path(projects_dir) if projects_dir else default_projects_dir()
    now = now or dt.datetime.now(dt.timezone.utc)
    cutoff = now - dt.timedelta(days=days)
    if after:
        d = dt.datetime.strptime(after, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc) + dt.timedelta(days=1)
        cutoff = max(cutoff, d)
    mtime_cut = cutoff.timestamp() - 86400
    res = {"entries": [], "ask_lines": 0, "parsed_lines": 0, "unreadable": 0, "questions": 0, "files": 0,
           "result_lines": 0, "unreadable_results": 0, "unparsed_answers": 0, "no_result": 0, "file_errors": 0}
    for f in sorted(glob.glob(os.path.join(str(pdir), "*", "*.jsonl"))):
        try:
            if os.path.getmtime(f) < mtime_cut:
                continue
            lines = open(f, errors="ignore").read().splitlines()
        except OSError:
            res["file_errors"] += 1  # counted and printed, never silent
            continue
        res["files"] += 1
        _scan_file(f, lines, cutoff, res)
    return res


def excluded_reason(key, members):
    if key in NEVER_ELIGIBLE:
        return NEVER_ELIGIBLE[key]
    for e in members:
        if e["opts"][0].lower().startswith("approve"):
            return "approving is consent"
        texts = [e["question"], e["header"]] + e["opts"]
        for t in texts:
            m = EXCLUDED_TERMS.search(t.lower())
            if m:
                return f"member mentions excluded term '{m.group(0)}'"
    return None


def class_stats(entries):
    """One row per defined class, zero-member classes included."""
    by = collections.defaultdict(list)
    for e in entries:
        by[e["key"]].append(e)
    rows = []
    for key, slug, _ in CLASSES:
        ms = by.get(key, [])
        first = sum(e["answer"] == e["opts"][0] for e in ms)
        other = sum(e["answer"] != e["opts"][0] and e["answer"] in e["opts"] for e in ms)
        n = len(ms)
        pct = 100 * first / n if n else 0
        sessions = len({e["session"] for e in ms})
        variants = collections.defaultdict(set)
        for e in ms:
            variants[norm_text(e["question"])].add(norm_label(e["opts"][0]))
        split = any(len(v) > 1 for v in variants.values())
        why = excluded_reason(key, ms)
        if why:
            status = f"EXCLUDED ({why})"
        elif split:
            status = "SPLIT"
        elif n >= BAR_MIN_QUESTIONS and pct >= BAR_FIRST_PCT and sessions >= BAR_MIN_SESSIONS:
            status = "PASS"
        elif n >= BAR_MIN_QUESTIONS and pct >= NEAR_MIN_PCT:
            status = "NEAR"
        else:
            status = "NO"
        repos = collections.Counter(e["repo"] for e in ms).most_common(3)
        rows.append({
            "key": key, "slug": slug, "n": n, "first": first, "other": other, "free": n - first - other,
            "pct": pct, "sessions": sessions, "non_first": n - first, "status": status,
            "distinct": len({norm_label(e["opts"][0]) for e in ms}),
            "repos": ", ".join(f"{r} {v}" for r, v in repos),
        })
    return rows


def print_table(rows):
    print("class | slug | asked | first option | other | free text | first % | sessions | answers other than first | distinct answers | status | top repos")
    for r in rows:
        print(f"{r['key']} {LABEL[r['key']]} | {r['slug']} | {r['n']} | {r['first']} | {r['other']} | {r['free']} | "
              f"{r['pct']:.0f}% | {r['sessions']} | {r['non_first']} | distinct answers: {r['distinct']} | {r['status']} | {r['repos']}")


def print_members(entries, key):
    for e in entries:
        if e["key"] == key:
            print(f"{e['date']} | {e['repo']} | {e['question']} | {e['opts'][0]} | {e['answer']} | {e['session']}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--after", help="count only questions asked after the end of this UTC date (YYYY-MM-DD)")
    ap.add_argument("--candidates", action="store_true", help="list first-option questions of the one-off class")
    ap.add_argument("--members", metavar="SLUG", help="print every member of a class")
    ap.add_argument("--projects", help="projects directory (default ~/.claude/projects)")
    args = ap.parse_args(argv)
    r = scan(args.projects, args.days, args.after)
    entries = r["entries"]
    answered = len(entries)
    free = sum(e["answer"] not in e["opts"] for e in entries)
    free_pct = 100 * free / answered if answered else 0
    unread_pct = 100 * r["unreadable"] / r["ask_lines"] if r["ask_lines"] else 0
    unres_pct = 100 * r["unreadable_results"] / r["result_lines"] if r["result_lines"] else 0
    # every in-window question lands in exactly one of: answered, unparsed_answers, no_result
    slots = answered + r["unparsed_answers"] + r["no_result"]
    lost = r["unparsed_answers"] + r["no_result"]
    unparsed_pct = 100 * lost / slots if slots else 0
    print(f"window: {args.days} days" + (f", after end of {args.after} UTC" if args.after else "") +
          " (each question by its own timestamp); files opened: " + str(r["files"]))
    print("not scanned: subagent transcripts (projects/*/<id>/subagents)")
    print(f"bar: >= {BAR_MIN_QUESTIONS} answered questions, >= {BAR_FIRST_PCT}% first option, >= {BAR_MIN_SESSIONS} sessions "
          f"(BAR_MIN_QUESTIONS={BAR_MIN_QUESTIONS} BAR_FIRST_PCT={BAR_FIRST_PCT} BAR_MIN_SESSIONS={BAR_MIN_SESSIONS}); "
          f"NEAR = >= {BAR_MIN_QUESTIONS} asked and >= {NEAR_MIN_PCT}% first (listed, never proposed)")
    print(f"parsed {r['parsed_lines']} of {r['ask_lines']} AskUserQuestion lines; unreadable: {r['unreadable']} "
          f"({unread_pct:.0f}%, max {UNREADABLE_MAX_PCT}%); questions parsed: {r['questions']}; answered: {answered}")
    print(f"result lines: {r['result_lines']}; unreadable results: {r['unreadable_results']} ({unres_pct:.0f}%, max {UNREADABLE_MAX_PCT}%); "
          f"unparsed_answers: {r['unparsed_answers']}; no_result: {r['no_result']}; "
          f"unparsed + no_result: {lost} of {slots} questions asked in the window ({unparsed_pct:.0f}%, max {UNREADABLE_MAX_PCT}%); unreadable files: {r['file_errors']}")
    print(f"free-text answers: {free} of {answered} ({free_pct:.0f}%, max {FREE_MAX_PCT}%)")
    print("never eligible: classes " + ", ".join(sorted(NEVER_ELIGIBLE)) + "; also approve-first classes and any class with an excluded term in a member")
    if args.members:
        keys = [k for k, s, _ in CLASSES if s == args.members]
        if not keys:
            print(f"unknown slug '{args.members}'; slugs: {', '.join(SLUG.values())}", file=sys.stderr)
            return 1
        print_members(entries, keys[0])
    elif args.candidates:
        for e in entries:
            if e["key"] == "9" and e["answer"] == e["opts"][0]:
                print(f"{e['date']} | {e['repo']} | {e['question']} | {e['opts'][0]}")
    else:
        print_table(class_stats(entries))
    fail = []
    if r["ask_lines"] and r["questions"] == 0:
        fail.append("AskUserQuestion lines found but zero questions parsed: the transcript result format may have changed")
    if unread_pct > UNREADABLE_MAX_PCT:
        fail.append(f"{unread_pct:.0f}% of AskUserQuestion lines unreadable (max {UNREADABLE_MAX_PCT}%): the transcript format may have changed")
    if unres_pct > UNREADABLE_MAX_PCT:
        fail.append(f"{unres_pct:.0f}% of result lines unreadable (max {UNREADABLE_MAX_PCT}%): the transcript format may have changed")
    if unparsed_pct > UNREADABLE_MAX_PCT:
        fail.append(f"{unparsed_pct:.0f}% of in-window questions have no readable answer or no matching result (max {UNREADABLE_MAX_PCT}%): the result format may have changed")
    if free_pct > FREE_MAX_PCT:
        fail.append(f"free-text share {free_pct:.0f}% exceeds {FREE_MAX_PCT}%: answers match no option, the result format may have changed")
    for m in fail:
        print("FAIL: " + m, file=sys.stderr)
    return 2 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
