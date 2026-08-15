#!/usr/bin/env python3
"""retro_scan.py — deterministic session-transcript scanner for /routing-retro.

Walks ~/.claude/projects/*/<session>.jsonl transcripts and emits per-session JSON:
model mix, token breakdown, cost (computed against the SSOT `prices:` block),
duration, error/truncation counts, routing receipts, and /model-/effort switch
commands — so the judgment layer never has to read raw megabyte JSONLs.

stdlib-only (no PyYAML — regex parse of the SSOT, matching verify-routing.sh's
house rule). Read-only: writes nothing anywhere.

Usage:
  retro_scan.py [--last N] [--since YYYY-MM-DD] [--project SUBSTR]
                [--exclude-session ID ...] [--ssot PATH] [--projects-dir PATH]

Selection: the N most-recently-modified session files (default 20), newest first,
optionally floor-bounded by --since (file mtime). Sessions whose records carry
`entrypoint: sdk-py` (headless Agent-SDK runs) and any --exclude-session id
(pass the CURRENT session's id!) are skipped and reported in `skipped`.
"""

import argparse
import glob
import json
import os
import re
import sys
from datetime import datetime, timezone

# Cache pricing multipliers relative to the SSOT input rate (Anthropic standard:
# cache read = 0.1x input; cache write = 1.25x (5m TTL) / 2x (1h TTL) input).
# The base in/out rates themselves come from the SSOT — never hardcoded here.
CACHE_READ_X = 0.10
CACHE_W5M_X = 1.25
CACHE_W1H_X = 2.00

RECEIPT_RX = re.compile(
    r"\b(mechanical|standard_build|agentic_build|deep_reasoning|linchpin)\b"
    r"\s*(?:→|->)\s*([\w./·]+)"
)
SWITCH_RX = re.compile(r"(?:^|\s|>)(/model|/effort)\b\s*([\w.\-\[\]]*)")


def parse_ssot_prices(ssot_path):
    """{family: (in_rate, out_rate)} from the SSOT `prices:` block, $/MTok."""
    with open(ssot_path, encoding="utf-8") as f:
        text = f.read()
    start = text.find("\nprices:\n")
    if start == -1:
        sys.exit("FATAL: no `prices:` block in " + ssot_path)
    m = re.search(r"^\S", text[start + len("\nprices:\n"):], re.M)
    end = start + len("\nprices:\n") + (m.start() if m else len(text))
    block = text[start:end]
    prices = {}
    for mo in re.finditer(r"^\s{2}(\w+):\s*\{\s*in:\s*([\d.]+),\s*out:\s*([\d.]+)", block, re.M):
        prices[mo.group(1)] = (float(mo.group(2)), float(mo.group(3)))
    if not prices:
        sys.exit("FATAL: parsed zero rows from SSOT `prices:` — parser or SSOT broken")
    return prices


def model_family(model_id):
    """Map an API model id (e.g. claude-fable-5, claude-opus-4-8[1m]) to a prices: key."""
    mid = (model_id or "").lower()
    for fam in ("fable", "opus", "sonnet", "haiku"):
        if fam in mid:
            return fam
    return None


def iter_text_blocks(content):
    """Yield text strings from a message content (str or list-of-blocks)."""
    if isinstance(content, str):
        yield content
    elif isinstance(content, list):
        for blk in content:
            if isinstance(blk, dict) and blk.get("type") == "text":
                yield blk.get("text") or ""


def scan_session(path, prices):
    s = {
        "session_id": os.path.splitext(os.path.basename(path))[0],
        "project": os.path.basename(os.path.dirname(path)),
        "file": path,
        "entrypoint": None,
        "git_branch": None,
        "ai_title": None,
        "first_prompt": None,
        "started": None,
        "ended": None,
        "duration_min": None,
        "assistant_messages": 0,
        "sidechain_messages": 0,
        "models": {},          # family -> {messages, in, out, cache_w, cache_r, cost_usd}
        "unknown_models": {},  # raw id -> message count (no prices: row)
        "cost_usd": 0.0,
        "context_peak_tokens": 0,   # largest single-message context (fresh + cache read + cache write)
        "reread_cost_usd": 0.0,     # spend on cache READS only — the price of carrying context forward
        "tool_errors": 0,
        "api_errors": 0,
        "max_tokens_truncations": 0,
        "routing_receipts": [],
        "model_effort_switches": [],
    }
    ts_first = ts_last = None
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                d = json.loads(line)
            except Exception:
                continue
            ts = d.get("timestamp")
            if isinstance(ts, str):
                ts_first = ts_first or ts
                ts_last = ts
            if s["entrypoint"] is None and d.get("entrypoint"):
                s["entrypoint"] = d["entrypoint"]
            if s["git_branch"] is None and d.get("gitBranch"):
                s["git_branch"] = d["gitBranch"]
            if d.get("type") == "ai-title" and d.get("aiTitle"):
                s["ai_title"] = d["aiTitle"]
            if d.get("isApiErrorMessage"):
                s["api_errors"] += 1

            rtype = d.get("type")
            msg = d.get("message") or {}

            if rtype == "user":
                content = msg.get("content")
                if isinstance(content, list):
                    for blk in content:
                        if isinstance(blk, dict) and blk.get("type") == "tool_result" and blk.get("is_error"):
                            s["tool_errors"] += 1
                for text in iter_text_blocks(content):
                    if s["first_prompt"] is None and text.strip() and not d.get("isSidechain"):
                        s["first_prompt"] = text.strip()[:300]
                    for mo in SWITCH_RX.finditer(text):
                        s["model_effort_switches"].append((mo.group(1) + " " + mo.group(2)).strip())

            elif rtype == "assistant":
                if d.get("isSidechain"):
                    s["sidechain_messages"] += 1
                s["assistant_messages"] += 1
                fam = model_family(msg.get("model"))
                usage = msg.get("usage") or {}
                # Context size is priceless-agnostic — measure it even for unpriced models.
                ctx = (
                    (usage.get("input_tokens") or 0)
                    + (usage.get("cache_read_input_tokens") or 0)
                    + (usage.get("cache_creation_input_tokens") or 0)
                )
                s["context_peak_tokens"] = max(s["context_peak_tokens"], ctx)
                if fam is None or fam not in prices:
                    key = msg.get("model") or "?"
                    s["unknown_models"][key] = s["unknown_models"].get(key, 0) + 1
                else:
                    row = s["models"].setdefault(fam, {"messages": 0, "in": 0, "out": 0, "cache_w": 0, "cache_r": 0, "cost_usd": 0.0})
                    row["messages"] += 1
                    tin = usage.get("input_tokens") or 0
                    tout = usage.get("output_tokens") or 0
                    cw = usage.get("cache_creation_input_tokens") or 0
                    cr = usage.get("cache_read_input_tokens") or 0
                    cc = usage.get("cache_creation") or {}
                    w5 = cc.get("ephemeral_5m_input_tokens") or 0
                    w1 = cc.get("ephemeral_1h_input_tokens") or 0
                    if w5 + w1 == 0:
                        w5 = cw  # no TTL breakdown — assume the cheaper 5m rate
                    in_rate, out_rate = prices[fam]
                    cost = (
                        tin * in_rate
                        + tout * out_rate
                        + cr * in_rate * CACHE_READ_X
                        + w5 * in_rate * CACHE_W5M_X
                        + w1 * in_rate * CACHE_W1H_X
                    ) / 1_000_000
                    row["in"] += tin
                    row["out"] += tout
                    row["cache_w"] += cw
                    row["cache_r"] += cr
                    row["cost_usd"] += cost
                    s["cost_usd"] += cost
                    s["reread_cost_usd"] += cr * in_rate * CACHE_READ_X / 1_000_000
                if (msg.get("stop_reason") == "max_tokens") or (
                    isinstance(msg.get("stop_details"), dict) and msg["stop_details"].get("reason") == "max_tokens"
                ):
                    s["max_tokens_truncations"] += 1
                for text in iter_text_blocks(msg.get("content")):
                    for mo in RECEIPT_RX.finditer(text):
                        s["routing_receipts"].append(f"{mo.group(1)} -> {mo.group(2)}")

    if ts_first and ts_last:
        try:
            t0 = datetime.fromisoformat(ts_first.replace("Z", "+00:00"))
            t1 = datetime.fromisoformat(ts_last.replace("Z", "+00:00"))
            s["started"], s["ended"] = ts_first, ts_last
            s["duration_min"] = round((t1 - t0).total_seconds() / 60, 1)
        except ValueError:
            pass
    total_msgs = sum(r["messages"] for r in s["models"].values())
    for r in s["models"].values():
        r["mix_pct"] = round(100.0 * r["messages"] / total_msgs, 1) if total_msgs else 0.0
        r["cost_usd"] = round(r["cost_usd"], 4)
    # Share of session spend that went on re-reading carried context rather than on
    # fresh input + output. HIGH IS NOT AUTOMATICALLY BAD — see the rubric's
    # context-bloat flag: cache reads are the cheap outcome, the question the judgment
    # layer answers is whether the carried context was still RELEVANT.
    s["reread_cost_pct"] = (
        round(100.0 * s["reread_cost_usd"] / s["cost_usd"], 1) if s["cost_usd"] else 0.0
    )
    s["reread_cost_usd"] = round(s["reread_cost_usd"], 4)
    s["cost_usd"] = round(s["cost_usd"], 4)
    s["routing_receipts"] = s["routing_receipts"][:20]
    s["model_effort_switches"] = s["model_effort_switches"][:20]
    return s


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--last", type=int, default=20, help="max sessions to scan (default 20)")
    ap.add_argument("--since", default=None, help="only sessions modified on/after YYYY-MM-DD")
    ap.add_argument("--project", default=None, help="substring filter on the project dir name")
    ap.add_argument("--exclude-session", action="append", default=[], help="session id to skip (pass the CURRENT session id)")
    ap.add_argument("--ssot", default=os.path.expanduser("~/.claude/model-routing.yaml"))
    ap.add_argument("--projects-dir", default=os.path.expanduser("~/.claude/projects"))
    args = ap.parse_args()

    prices = parse_ssot_prices(args.ssot)

    since_ts = None
    if args.since:
        since_ts = datetime.strptime(args.since, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()

    files = glob.glob(os.path.join(args.projects_dir, "*", "*.jsonl"))
    if args.project:
        files = [f for f in files if args.project in os.path.basename(os.path.dirname(f))]
    if since_ts:
        files = [f for f in files if os.path.getmtime(f) >= since_ts]
    files.sort(key=os.path.getmtime, reverse=True)

    sessions, skipped = [], []
    for fp in files:
        if len(sessions) >= args.last:
            break
        sid = os.path.splitext(os.path.basename(fp))[0]
        if sid in args.exclude_session:
            skipped.append({"session_id": sid, "reason": "excluded (current session?)"})
            continue
        s = scan_session(fp, prices)
        if s["entrypoint"] and s["entrypoint"].startswith("sdk"):
            skipped.append({"session_id": sid, "reason": f"entrypoint={s['entrypoint']}"})
            continue
        if s["assistant_messages"] == 0:
            skipped.append({"session_id": sid, "reason": "no assistant messages"})
            continue
        sessions.append(s)

    out = {
        "note": (
            "Costs are computed against the SSOT prices: block "
            f"({args.ssot}) with standard cache multipliers "
            f"(read {CACHE_READ_X}x-in, write {CACHE_W5M_X}x/{CACHE_W1H_X}x-in). "
            "Effort is NOT recorded in transcripts — it must be INFERRED "
            "(settings.json effortLevel + env overrides + receipts), never claimed as measured."
        ),
        "params": {"last": args.last, "since": args.since, "project": args.project,
                   "excluded": args.exclude_session},
        "prices_used": {k: {"in": v[0], "out": v[1]} for k, v in sorted(prices.items())},
        "sessions": sessions,
        "skipped": skipped,
        "totals": {
            "sessions": len(sessions),
            "cost_usd": round(sum(s["cost_usd"] for s in sessions), 2),
            "reread_cost_usd": round(sum(s["reread_cost_usd"] for s in sessions), 2),
            "tool_errors": sum(s["tool_errors"] for s in sessions),
            "api_errors": sum(s["api_errors"] for s in sessions),
            "max_tokens_truncations": sum(s["max_tokens_truncations"] for s in sessions),
            "receipts_found": sum(len(s["routing_receipts"]) for s in sessions),
        },
    }
    json.dump(out, sys.stdout, indent=1)
    print()


if __name__ == "__main__":
    main()
