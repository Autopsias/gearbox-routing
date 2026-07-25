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
INTERACTIVE_ENTRYPOINTS = {"cli", "vscode", "ide", None}


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
        "user_msgs": 0,          # human turns in the main conversation
        "sidechain_user_msgs": 0,
        "assistant_msgs": 0,
        "commands": Counter(),   # slash commands typed by the human
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
                for it in content:
                    if isinstance(it, dict) and it.get("type") == "tool_result" and it.get("is_error"):
                        s["tool_errors"] += 1
                        if len(s["err_samples"]) < max_samples:
                            s["err_samples"].append(str(it.get("content"))[:200])
            elif isinstance(content, (str, list)) and txt.strip():
                if side:
                    s["sidechain_user_msgs"] += 1
                else:
                    s["user_msgs"] += 1
                    stripped = txt.strip()
                    if stripped.startswith("/"):
                        cmd = stripped.split()[0]
                        s["commands"][cmd] += 1
                    if INTERRUPT_RE.search(stripped):
                        s["interrupts"] += 1
                    if DENY_RE.search(stripped) and "denied" in stripped.lower():
                        s["denials"] += 1
        elif t == "assistant":
            s["assistant_msgs"] += 1
            msg = d.get("message") or {}
            if msg.get("model"):
                s["models"][msg["model"]] += 1
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
    s["interactive"] = s["user_msgs"] > 0 and s.get("entrypoint") in INTERACTIVE_ENTRYPOINTS
    s["commands"] = dict(s["commands"])
    s["models"] = dict(s["models"])
    return s


def digest_history(path: Path) -> dict:
    """Typed-prompt frequency table from ~/.claude/history.jsonl."""
    out = {"records": 0, "by_project": Counter(), "commands": Counter(),
           "babysit": Counter(), "top_prompts": []}
    babysit_terms = {"proceed", "continue", "yes", "check", "retry", "go",
                     "y", "c", "ok", "next", "do it"}
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
        },
        "model_mix": dict(models.most_common(20)),
        "command_usage": dict(commands.most_common(50)),
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


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--projects", default=str(DEFAULT_PROJECTS))
    ap.add_argument("--history", default=str(DEFAULT_HISTORY))
    ap.add_argument("--out", default="-")
    ap.add_argument("--max-samples", type=int, default=5)
    ap.add_argument("--pretty", action="store_true")
    ap.add_argument("--per-session-out", default=None,
                    help="optional path to write the full per-session array")
    args = ap.parse_args(argv)

    projects = Path(args.projects)
    files = sorted(projects.glob("*/*.jsonl")) if projects.exists() else []
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
