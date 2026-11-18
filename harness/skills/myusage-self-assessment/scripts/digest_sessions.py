#!/usr/bin/env python3
"""Deterministic digest of Claude Code session transcripts.

Phase 1 of the /self-assessment method. Pure Python, no model calls.
Walks ~/.claude/projects/*/*.jsonl (one file per session) plus
~/.claude/history.jsonl (typed prompts) and emits a compact per-project +
aggregate digest that mining subagents can read without touching the raw
~1 GB of transcripts.

Design notes / hard-won schema facts (verified against the live corpus
2026-07-03):
  * Each project dir under ~/.claude/projects/ holds one .jsonl per session
    (filename stem == sessionId).
  * Record `type` is one of: user, assistant, attachment, system,
    ai-title, last-prompt, mode, queue-operation, file-history-snapshot.
  * The human-readable session title lives on `ai-title` records in the
    field **aiTitle** (NOT `title`/`summary`) — the historically-flubbed
    field this script fixes.
  * `isSidechain: true` marks subagent (fan-out) records. We separate the
    main conversation from sidechains so babysitting/interrupt metrics
    reflect the human loop, not internal agent chatter.
  * assistant records carry message.model (Counter → model mix) and
    message.usage (token totals).
  * Tool errors surface two ways: a user tool_result block with
    is_error:true (content often <tool_use_error>…</tool_use_error>), or a
    top-level toolUseResult dict with is_error.
  * API errors surface as assistant text blocks containing "API Error".
  * Interrupts surface as user text "[Request interrupted…".

Usage:
    python3 digest_sessions.py [--projects DIR] [--history FILE]
                               [--out FILE] [--max-samples N] [--pretty]
                               [--since-days N] [--include-temp-projects]
Writes JSON to --out (default stdout). Exit 0 on success even if some files
are malformed (they are counted in `skipped`, never fatal).
"""
from __future__ import annotations
import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

HOME = Path(os.path.expanduser("~"))
DEFAULT_PROJECTS = HOME / ".claude" / "projects"
DEFAULT_HISTORY = HOME / ".claude" / "history.jsonl"

API_ERR_RE = re.compile(r"API Error", re.I)
INTERRUPT_RE = re.compile(r"\[Request interrupted", re.I)
DENY_RE = re.compile(r"(permission|denied|not allowed|blocked by|requires approval)", re.I)
# Auto-spawned / non-interactive entrypoints we want to be able to segment on.
# The desktop app is where the owner works since 2026-09; without it the digest
# called 73 of 399 real sessions interactive (run of 2026-11-12).
INTERACTIVE_ENTRYPOINTS = {"cli", "vscode", "ide", "claude-desktop", None}
# User-type text the owner did not type: command wrappers, hook and harness notices.
MACHINE_TEXT_RE = re.compile(
    r"^\s*(<|\[SYSTEM NOTIFICATION|\[Request interrupted|Caveat:|This session is being continued"
    r"|Stop hook feedback|Base directory for this skill)")
COMMAND_TAG_RE = re.compile(r"<command-name>(/[^<\s]+)</command-name>")
CLASSIFIER_RE = re.compile(r"denied by the Claude Code auto mode classifier\. Reason: \[([^\]]+)\]")
BABYSIT_TERMS = {"proceed", "continue", "yes", "check", "retry", "go",
                 "y", "c", "ok", "next", "do it"}
# Project dirs under a temp folder are eval-harness checkouts (41,987 of 44,104 files).
TEMP_PROJECT_RE = re.compile(r"^-(private-)?(var-folders|tmp)")


def _text_of(content):
    """Flatten a message.content (str | list-of-blocks) to plain text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for it in content:
            if isinstance(it, dict):
                if it.get("type") == "text" and isinstance(it.get("text"), str):
                    parts.append(it["text"])
        return "\n".join(parts)
    return ""


def _iter_records(path):
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def _count_tool_results(s, content, max_samples):
    for it in content:
        if isinstance(it, dict) and it.get("type") == "tool_result" and it.get("is_error"):
            s["tool_errors"] += 1
            s["classifier_denials"].update(CLASSIFIER_RE.findall(str(it.get("content"))))
            if len(s["err_samples"]) < max_samples:
                s["err_samples"].append(str(it.get("content"))[:200])


def _count_main_user_text(s, stripped, is_meta):
    s["user_msgs"] += 1
    if stripped.startswith("/"):
        s["commands"][stripped.split()[0]] += 1
    # Desktop and current CLI wrap a typed command in a tag.
    s["commands"].update(COMMAND_TAG_RE.findall(stripped))
    if not is_meta and not MACHINE_TEXT_RE.match(stripped):
        s["human_msgs"] += 1
        if stripped.lower().rstrip(".!") in BABYSIT_TERMS:
            s["babysit_msgs"] += 1
    if INTERRUPT_RE.search(stripped):
        s["interrupts"] += 1
    if DENY_RE.search(stripped) and "denied" in stripped.lower():
        s["denials"] += 1


def digest_session(path: Path, max_samples: int) -> dict:
    s = {
        "session_id": path.stem,
        "project_dir": path.parent.name,
        "title": None,
        "cwd": None,
        "git_branch": None,
        "entrypoint": None,
        "interactive": True,
        "records": 0,
        "main_records": 0,
        "sidechain_records": 0,
        "user_msgs": 0,          # user-type text turns in the main conversation (includes machine text)
        "human_msgs": 0,         # of those, the ones the owner typed
        "babysit_msgs": 0,       # typed turns that are only a go-ahead
        "sidechain_user_msgs": 0,
        "assistant_msgs": 0,
        "commands": Counter(),   # slash commands typed by the human
        "skills": Counter(),     # skills the model invoked
        "classifier_denials": Counter(),  # auto-mode refusals by reason
        "models": Counter(),
        "tool_errors": 0,
        "api_errors": 0,
        "interrupts": 0,
        "denials": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "err_samples": [],
        "first_ts": None,
        "last_ts": None,
    }
    for d in _iter_records(path):
        s["records"] += 1
        t = d.get("type")
        side = bool(d.get("isSidechain"))
        if side:
            s["sidechain_records"] += 1
        else:
            s["main_records"] += 1
        ts = d.get("timestamp")
        if ts:
            s["first_ts"] = s["first_ts"] or ts
            s["last_ts"] = ts
        if s["cwd"] is None and d.get("cwd"):
            s["cwd"] = d.get("cwd")
        if s["git_branch"] is None and d.get("gitBranch"):
            s["git_branch"] = d.get("gitBranch")
        if s["entrypoint"] is None and d.get("entrypoint"):
            s["entrypoint"] = d.get("entrypoint")

        if t == "ai-title" and d.get("aiTitle"):
            s["title"] = d["aiTitle"]  # last one wins
        elif t == "user":
            msg = d.get("message") or {}
            content = msg.get("content")
            txt = _text_of(content)
            # Distinguish a real human turn from a tool_result echo.
            is_tool_result = isinstance(content, list) and any(
                isinstance(it, dict) and it.get("type") == "tool_result" for it in content
            )
            if is_tool_result:
                _count_tool_results(s, content, max_samples)
            elif isinstance(content, (str, list)) and txt.strip():
                if side:
                    s["sidechain_user_msgs"] += 1
                else:
                    _count_main_user_text(s, txt.strip(), bool(d.get("isMeta")))
        elif t == "assistant":
            s["assistant_msgs"] += 1
            msg = d.get("message") or {}
            if msg.get("model"):
                s["models"][msg["model"]] += 1
            if not side and isinstance(msg.get("content"), list):
                for it in msg["content"]:
                    if isinstance(it, dict) and it.get("type") == "tool_use" and it.get("name") == "Skill":
                        s["skills"][(it.get("input") or {}).get("skill") or "?"] += 1
            usage = msg.get("usage") or {}
            s["input_tokens"] += int(usage.get("input_tokens") or 0)
            s["output_tokens"] += int(usage.get("output_tokens") or 0)
            txt = _text_of(msg.get("content"))
            if txt and API_ERR_RE.search(txt):
                s["api_errors"] += 1
                if len(s["err_samples"]) < max_samples:
                    s["err_samples"].append("API: " + txt[:180])

        tur = d.get("toolUseResult")
        if isinstance(tur, dict) and tur.get("is_error"):
            s["tool_errors"] += 1
            if len(s["err_samples"]) < max_samples:
                s["err_samples"].append(str(tur.get("content") or tur)[:200])

    # Heuristic: sessions dominated by sidechain/no human turns, or spawned
    # from a non-interactive entrypoint, are automation not human work.
    s["interactive"] = s["human_msgs"] > 0 and s.get("entrypoint") in INTERACTIVE_ENTRYPOINTS
    s["commands"] = dict(s["commands"])
    s["skills"] = dict(s["skills"])
    s["classifier_denials"] = dict(s["classifier_denials"])
    s["models"] = dict(s["models"])
    return s


def digest_history(path: Path) -> dict:
    """Typed-prompt frequency table from ~/.claude/history.jsonl."""
    out = {"records": 0, "by_project": Counter(), "commands": Counter(),
           "babysit": Counter(), "top_prompts": []}
    babysit_terms = BABYSIT_TERMS
    prompts = Counter()
    if not path.exists():
        out["missing"] = True
        return out
    for d in _iter_records(path):
        out["records"] += 1
        disp = (d.get("display") or "").strip()
        proj = d.get("project") or "?"
        out["by_project"][proj] += 1
        if disp.startswith("/"):
            out["commands"][disp.split()[0]] += 1
        low = disp.lower()
        if low in babysit_terms:
            out["babysit"][low] += 1
        if disp:
            prompts[disp[:60]] += 1
    out["by_project"] = dict(out["by_project"].most_common(30))
    out["commands"] = dict(out["commands"].most_common(40))
    out["babysit"] = dict(out["babysit"])
    out["top_prompts"] = prompts.most_common(40)
    return out


def aggregate(sessions: list[dict], history: dict) -> dict:
    total = len(sessions)
    interactive = [s for s in sessions if s["interactive"]]
    per_project = defaultdict(lambda: {"sessions": 0, "interactive": 0,
                                        "tool_errors": 0, "api_errors": 0,
                                        "user_msgs": 0, "output_tokens": 0})
    models = Counter()
    commands = Counter()
    skills: Counter[str] = Counter()
    classifier: Counter[str] = Counter()
    human = babysit = 0
    tool_err = api_err = interrupts = denials = 0
    for s in sessions:
        p = per_project[s["project_dir"]]
        p["sessions"] += 1
        p["interactive"] += 1 if s["interactive"] else 0
        p["tool_errors"] += s["tool_errors"]
        p["api_errors"] += s["api_errors"]
        p["user_msgs"] += s["user_msgs"]
        p["output_tokens"] += s["output_tokens"]
        models.update(s["models"])
        commands.update(s["commands"])
        skills.update(s["skills"])
        classifier.update(s["classifier_denials"])
        human += s["human_msgs"]
        babysit += s["babysit_msgs"]
        tool_err += s["tool_errors"]
        api_err += s["api_errors"]
        interrupts += s["interrupts"]
        denials += s["denials"]
    babysit_total = sum(history.get("babysit", {}).values())
    hist_records = history.get("records", 0) or 1
    return {
        "generated": None,
        "totals": {
            "sessions": total,
            "interactive_sessions": len(interactive),
            "interactive_share": round(len(interactive) / total, 3) if total else 0,
            "tool_errors": tool_err,
            "api_errors": api_err,
            "interrupts": interrupts,
            "denials": denials,
            "history_prompts": history.get("records", 0),
            "babysit_prompts": babysit_total,
            "babysit_share": round(babysit_total / hist_records, 3),
            # history.jsonl gets no desktop prompts; these two come from the transcripts.
            "human_msgs": human,
            "babysit_msgs": babysit,
            "classifier_denials": sum(classifier.values()),
        },
        "model_mix": dict(models.most_common(20)),
        "command_usage": dict(commands.most_common(50)),
        "skill_usage": dict(skills.most_common(50)),
        "classifier_denials": dict(classifier.most_common(20)),
        "per_project": {k: v for k, v in sorted(
            per_project.items(), key=lambda kv: -kv[1]["sessions"])},
        # External benchmark thresholds for the miners to check against.
        "benchmarks": {
            "read_edit_ratio_target": ">6",
            "edits_without_prior_read_target": "<10%",
            "frustration_target": "<6%",
            "note": "Read:Edit ratio and edit-without-Read need per-tool "
                    "counts; miners derive them from raw transcripts when a "
                    "cluster warrants it. Digest surfaces the denominators.",
        },
    }


def select_files(projects: Path, since_days, include_temp):
    """Main-session transcript files by the digest's rules. Returns (files, n_all, n_temp)."""
    files = sorted(projects.glob("*/*.jsonl")) if projects.exists() else []
    n_all = len(files)
    if not include_temp:
        files = [f for f in files if not TEMP_PROJECT_RE.match(f.parent.name)]
    n_temp = n_all - len(files)
    if since_days is not None:
        import time
        cutoff = time.time() - since_days * 86400
        files = [f for f in files if f.stat().st_mtime >= cutoff]
    return files, n_all, n_temp


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--projects", default=str(DEFAULT_PROJECTS))
    ap.add_argument("--history", default=str(DEFAULT_HISTORY))
    ap.add_argument("--out", default="-")
    ap.add_argument("--max-samples", type=int, default=5)
    ap.add_argument("--pretty", action="store_true")
    ap.add_argument("--per-session-out", default=None,
                    help="optional path to write the full per-session array")
    ap.add_argument("--since-days", type=int, default=None,
                    help="read only session files modified in the last N days")
    ap.add_argument("--include-temp-projects", action="store_true",
                    help="also read eval-harness checkouts under temp folders (slow: ~9 min on 18 GB)")
    args = ap.parse_args(argv)

    files, n_all, n_temp = select_files(Path(args.projects), args.since_days, args.include_temp_projects)
    n_old = n_all - n_temp - len(files)
    sessions, skipped = [], 0
    for f in files:
        try:
            sessions.append(digest_session(f, args.max_samples))
        except Exception as e:  # never let one bad file abort the run
            skipped += 1
            sys.stderr.write(f"[digest] skipped {f}: {e}\n")

    history = digest_history(Path(args.history))
    agg = aggregate(sessions, history)
    import datetime
    agg["generated"] = datetime.datetime.now().isoformat(timespec="seconds")
    agg["skipped_files"] = skipped
    agg["session_files"] = len(files)
    agg["skipped_temp_project_files"] = n_temp
    agg["skipped_old_files"] = n_old
    agg["history"] = {k: history[k] for k in
                      ("records", "by_project", "commands", "babysit", "top_prompts")
                      if k in history}

    if args.per_session_out:
        Path(args.per_session_out).write_text(
            json.dumps(sessions, indent=2, ensure_ascii=False))

    payload = json.dumps(agg, indent=2 if args.pretty else None, ensure_ascii=False)
    if args.out == "-":
        print(payload)
    else:
        Path(args.out).write_text(payload)
        sys.stderr.write(
            f"[digest] {len(sessions)} sessions, {history.get('records',0)} "
            f"history prompts → {args.out}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
