#!/usr/bin/env python3
"""Measure prompt-cache health, rewrite causes, effort mix and API-equivalent spend
from Claude Code transcripts (~/.claude/projects/**/*.jsonl) over the last N days.

Re-run after any effort or routing change and compare turns, $/day and the effort mix.

    python3 ~/.claude/skills/cost-audit/scripts/cache_effort_baseline.py [DAYS]
"""

import datetime
import glob
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = os.path.expanduser("~/.claude/projects")
# $/MTok (input, output, cache-read) per SERVED model id, from the SSOT model_prices:
# block — one table shared with /routing-retro. The hardcoded copy it replaces lacked
# claude-opus-5-5, so every Opus 5.5 request was dropped without a word.
_HARNESS = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_HARNESS / "scripts"))
import model_prices  # noqa: E402

BASE = model_prices.load(str(_HARNESS / "model-routing.yaml"))
UNPRICED = Counter()  # served id -> requests skipped because model_prices: has no row
WRITE_MULT = {"main": 2.0, "sub": 1.25}  # 1-hour TTL on the main conversation, 5-minute on subagents


def ts(s):
    try:
        return datetime.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def load(path):
    """One row per API request (deduplicated by requestId) plus compaction markers.

    A subagent's requests live in a subagents/ file or carry isSidechain; both map to chain "sub".
    """
    rows, seen = [], {}
    sub_file = "/subagents/" in path
    with open(path, "rb") as fh:
        for raw in fh:
            try:
                d = json.loads(raw)
            except ValueError:
                continue
            if d.get("type") == "system" and d.get("subtype") == "compact_boundary":
                rows.append({"kind": "compact"})
                continue
            if d.get("type") != "assistant":
                continue
            m = d.get("message") or {}
            u = m.get("usage") or {}
            if not u or m.get("model") == "<synthetic>":  # written locally, no API call: 0 tokens
                continue
            rate = model_prices.rates(BASE, m.get("model"))
            if rate is None:
                UNPRICED[m.get("model") or "?"] += 1
                continue
            rid = d.get("requestId") or m.get("id")
            if rid in seen:
                seen[rid]["u"] = u
                continue
            row = {"kind": "req", "u": u, "ts": ts(d.get("timestamp", "")), "model": m["model"], "rate": rate, "effort": d.get("effort"),
                   "chain": "sub" if sub_file or d.get("isSidechain") else "main"}
            seen[rid] = row
            rows.append(row)
    return rows


def ctx(u):
    return sum(u.get(k) or 0 for k in ("cache_read_input_tokens", "cache_creation_input_tokens", "input_tokens"))


def classify(prev, cur, between, gap):
    if any(r["kind"] == "compact" for r in between):
        return "compaction"
    if cur["model"] != prev["model"]:
        return "model_switch"
    if cur["effort"] != prev["effort"]:
        return "effort_change"
    if gap > 3600:
        return "idle_over_1h"
    if gap > 300:
        return "gap_5m_to_1h"
    return "unexplained_under_5m"



def print_unpriced():
    """Name the served ids skipped for lack of a model_prices: row — never drop them silently."""
    if UNPRICED:
        print("  NOT PRICED (no model_prices: row — add one to model-routing.yaml): "
              + ", ".join(f"{k} x{n}" for k, n in UNPRICED.most_common()))


def main(days):
    cutoff = datetime.datetime.now().timestamp() - days * 86400
    cost = defaultdict(lambda: defaultdict(float))
    breaks, rewritten, effort = Counter(), Counter(), Counter()
    compactions = 0
    for path in glob.glob(ROOT + "/**/*.jsonl", recursive=True):
        if os.path.getmtime(path) < cutoff:
            continue
        rows = load(path)
        prev = prev_i = None
        for i, r in enumerate(rows):
            if r["kind"] == "compact":
                compactions += 1
                continue
            u, model, chain = r["u"], r["model"], r["chain"]
            c = cost[(chain, model)]
            c["n"] += 1
            rin, rout, rread = r["rate"][:3]
            c["read"] += (u.get("cache_read_input_tokens") or 0) * rread / 1e6
            c["write"] += (u.get("cache_creation_input_tokens") or 0) * rin * WRITE_MULT[chain] / 1e6
            c["out"] += (u.get("output_tokens") or 0) * rout / 1e6
            c["in"] += (u.get("input_tokens") or 0) * rin / 1e6
            c["tok"] += ctx(u)
            c["hit"] += u.get("cache_read_input_tokens") or 0
            if r["effort"]:
                effort[(chain, model, r["effort"])] += 1
            if chain == "main" and prev and prev["ts"] and r["ts"]:
                pctx = ctx(prev["u"])
                cr = u.get("cache_read_input_tokens") or 0
                rw = (u.get("cache_creation_input_tokens") or 0) + (u.get("input_tokens") or 0)
                if pctx > 20000 and cr < 0.5 * pctx and rw > 0.5 * pctx:
                    cat = classify(prev, r, rows[prev_i + 1:i], r["ts"] - prev["ts"])
                    breaks[cat] += 1
                    rewritten[cat] += rw
            if chain == "main":
                prev, prev_i = r, i
    print(f"window={days}d\n\n== spend (API-equivalent $) by chain+model ==")
    total = 0.0
    for (chain, model), c in sorted(cost.items(), key=lambda kv: -sum(kv[1][k] for k in ("read", "write", "out", "in"))):
        t = c["read"] + c["write"] + c["out"] + c["in"]
        total += t
        print(f"  {chain:4} {model:28} reqs={int(c['n']):6} hit={c['hit'] / max(c['tok'], 1):6.1%} "
              f"avg_ctx={c['tok'] / c['n'] / 1e3:4.0f}k read=${c['read']:6.0f} write=${c['write']:6.0f} out=${c['out']:5.0f} total=${t:6.0f}")
    print(f"  TOTAL ${total:,.0f}  (${total / days:,.0f}/day)")
    print_unpriced()
    print(f"\n== main-chain cache rewrites by cause (events, tokens rewritten); compactions={compactions} ==")
    for cat, n in breaks.most_common():
        print(f"  {cat:24} n={n:4}  rewritten={rewritten[cat] / 1e6:5.1f}M")
    print("\n== effort mix (requests) ==")
    for (chain, model, lvl), n in sorted(effort.items()):
        print(f"  {chain:4} {model:28} {lvl:7} {n:6}")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 14)
