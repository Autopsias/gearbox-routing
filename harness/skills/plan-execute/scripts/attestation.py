#!/usr/bin/env python3
"""Which model actually served a dispatch, and what it spent, read back off its
transcript.

Split out of outcomes.py, which had grown 16 lines past its size baseline.
Reads a transcript file and returns evidence; only pricing reaches outside the
package (scripts/model_prices.py and the routing file's `model_prices:` block).

PER BACKEND, per the plan's explicit finding: a codex member is a Claude Agent
wrapper around an inner `codex exec`, so attesting the outer Agent says nothing
about the model that actually did the work.

USAGE (route-at-dispatch contract rule 6) — three transcript shapes, each read
off a real file on this machine (s07):
  * `claude-json` — headless `claude -p --output-format json`: a `modelUsage`
    block per model, `inputTokens` already non-cached, `outputTokens` with
    thinking included, and a reported `costUSD`.
  * `subagent-transcript` — an Agent-tool or Workflow-member JSONL
    (`<session>/subagents/[workflows/<wf>/]agent-<agentId>.jsonl`). One API
    message is written as several lines sharing `message.id`; only a line with
    `stop_reason` set carries the final `output_tokens` — the others are
    streaming snapshots (measured: a 4,000-character turn recorded as 8). Input
    and cache counts are complete on every line.
  * `codex-json` — `codex exec --json`: `turn.completed` events whose
    `input_tokens` INCLUDES `cached_input_tokens` (codex-rs `non_cached_input`)
    and whose `output_tokens` includes `reasoning_output_tokens`
    (`total_tokens = input + output`). No served-model field in any event.
"""
import json
import sys
from pathlib import Path

_TOKENS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens")
UNAVAILABLE = {**dict.fromkeys(_TOKENS), "cost_usd": None, "cost_source": "none",
               "source": "unavailable", "scope": "worker"}


def _count(v):
    """A token count as a non-negative int, else None (a bool is not a count)."""
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else None


def _sum(values):
    """Sum of token counts, or None when any one of them is missing or invalid."""
    counts = [_count(v) for v in values]
    return None if any(c is None for c in counts) else sum(counts)


def _usage(source, inp, out, cache_read, cache_creation, cost=None):
    cost = cost if isinstance(cost, (int, float)) and not isinstance(cost, bool) else None
    return {"input_tokens": inp, "output_tokens": out, "cache_read_tokens": cache_read,
            "cache_creation_tokens": cache_creation, "cost_usd": cost,
            "cost_source": "reported" if cost is not None else "none",
            "source": source, "scope": "worker"}


def _served(work):
    """The model that did the work, from {model: token count}, or None.

    Background `claude-haiku-*` title/fast-mode helpers are dropped (see
    evals/routing/harness/lib_common.py `attest_served_model`) unless nothing
    else ran. Several remaining models: the one with the most tokens wins; a
    tied max is genuinely ambiguous and returns None rather than a guess."""
    if not work:
        return None
    non_helper = [k for k in work if "haiku" not in k.lower()]
    if not non_helper:
        return next(iter(work))
    ranked = sorted(non_helper, key=lambda k: work[k] or 0, reverse=True)
    if len(ranked) > 1 and (work[ranked[1]] or 0) == (work[ranked[0]] or 0):
        return None
    return ranked[0]


def _claude_json(data):
    """(attested, usage) from a `claude -p --output-format json` result."""
    mu = data.get("modelUsage")
    if not isinstance(mu, dict) or not mu:
        return None, None
    mu = {k: (v if isinstance(v, dict) else {}) for k, v in mu.items()}
    served = _served({k: (_count(v.get("inputTokens")) or 0) + (_count(v.get("outputTokens")) or 0)
                      for k, v in mu.items()})
    if served is None:
        return None, None
    m = mu[served]
    usage = _usage("claude-json", _count(m.get("inputTokens")), _count(m.get("outputTokens")),
                   _count(m.get("cacheReadInputTokens")), _count(m.get("cacheCreationInputTokens")),
                   m.get("costUSD"))
    return {"model": served, "reasoning": None}, usage


def _subagent(events):
    """(attested, usage) from an Agent-tool / Workflow-member JSONL transcript."""
    final = {}  # message.id -> (model, usage, has_stop_reason)
    for ev in events:
        msg = ev.get("message") if ev.get("type") == "assistant" else None
        if not isinstance(msg, dict) or not msg.get("id") or msg.get("model") in (None, "<synthetic>"):
            continue
        prev = final.get(msg["id"])
        if prev is None or not prev[2]:  # a line with stop_reason is final; else the last line wins
            final[msg["id"]] = (msg["model"], msg.get("usage") or {}, bool(msg.get("stop_reason")))
    by_model = {}
    for model, u, done in final.values():
        by_model.setdefault(model, []).append((u, done))
    work = {m: sum((_count(u.get(k)) or 0) for u, _ in rows for k in
                   ("input_tokens", "output_tokens", "cache_read_input_tokens",
                    "cache_creation_input_tokens")) for m, rows in by_model.items()}
    served = _served(work)
    if served is None:
        return None, None
    rows = by_model[served]
    # A snapshot's output_tokens undercounts, so the attempt's output is unknown
    # unless every message has its final line: null, never a partial sum.
    out = _sum(u.get("output_tokens") for u, _ in rows) if all(d for _, d in rows) else None
    usage = _usage("subagent-transcript", _sum(u.get("input_tokens") for u, _ in rows), out,
                   _sum(u.get("cache_read_input_tokens") for u, _ in rows),
                   _sum(u.get("cache_creation_input_tokens") for u, _ in rows))
    # The TTL split rides along only when some 1-hour write exists (else all is 5m).
    h1 = sum(_count((u.get("cache_creation") or {}).get("ephemeral_1h_input_tokens")) or 0
             for u, _ in rows if isinstance(u.get("cache_creation"), dict))
    if h1:
        usage["cache_creation_1h_tokens"] = h1
    return {"model": served, "reasoning": None}, usage


def _codex(events):
    """usage from a `codex exec --json` stream: the sum of its turn.completed events.

    Categories are kept disjoint like Anthropic's: cached and cache-write tokens
    come out of `input_tokens`, which OpenAI reports inclusive of both. A stream
    without `cache_write_input_tokens` leaves that count, and so the non-cached
    input and the cost, null: the categories could overlap, so never guess."""
    turns = [ev["usage"] for ev in events
             if ev.get("type") == "turn.completed" and isinstance(ev.get("usage"), dict)]
    if not turns:
        return None
    inp, cached = _sum(t.get("input_tokens") for t in turns), _sum(t.get("cached_input_tokens", 0) for t in turns)
    writes = (_sum(t.get("cache_write_input_tokens") for t in turns)
              if all("cache_write_input_tokens" in t for t in turns) else None)
    non_cached = None if None in (inp, cached, writes) else inp - cached - writes
    if non_cached is not None and non_cached < 0:
        non_cached = None
    return _usage("codex-json", non_cached, _sum(t.get("output_tokens") for t in turns), cached, writes)


def _load(path):
    """(json object, None) for a single-object file, else (None, [line objects])."""
    text = Path(path).read_text(encoding="utf-8")
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data, None
    except ValueError:
        pass
    events = []
    for line in text.splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue  # codex interleaves plain-text lines; a torn tail line is skipped
        if isinstance(ev, dict):
            events.append(ev)
    return None, events


def read_transcript(backend, transcript_path):
    """(attested, usage) for one dispatch's transcript. Never raises.

    `attested` is `{"model", "reasoning"}` or None; `usage` is the rule-6 block
    (cost filled only when the transcript REPORTS one) or None."""
    if not transcript_path:
        return None, None
    try:
        data, events = _load(transcript_path)
        if backend == "codex":
            return None, _codex(events if events is not None else [data])
        if data is not None:
            return _claude_json(data)
        return _subagent(events)
    except (OSError, ValueError, TypeError, AttributeError):
        return None, None


# --------------------------------------------------------------------------
# Served-model attestation from a dispatch transcript — PER BACKEND, per the
# plan's explicit finding: a codex member is a Claude Agent wrapper around an
# inner `codex exec`, so attesting the outer Agent says nothing about the model
# that actually did the work.
# --------------------------------------------------------------------------
def attest_from_transcript(backend, transcript_path):
    """Served-model evidence from `transcript_path`, or None. Never raises.

    CLAUDE backend: headless `claude -p --output-format json`'s `modelUsage`
    block, or an Agent-tool / Workflow-member JSONL transcript's
    `message.model` (both proven on real files, s09 and s07).

    CODEX backend: the `codex exec --json` event stream carries usage but NO
    served-model field in any envelope (evals/routing/graders/judge.py:8; the
    codex-rs event schema, checked again s07) — this always returns None for
    codex, so codex cohorts stay at `model_ran_source="requested"`.

    More than one non-helper model: the one that did the most work is the
    served model; a tied max returns None rather than a guess."""
    return None if backend == "codex" else read_transcript(backend, transcript_path)[0]


# --------------------------------------------------------------------------
# Cost — rule 6: a reported cost, or model_prices against the SERVED id.
# --------------------------------------------------------------------------
def _price_table():
    """{model_id: (in, out, cache_read, write_5m, write_1h)} from the routing file this process reads."""
    scripts = str(Path(__file__).resolve().parents[3] / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    import model_prices  # noqa: PLC0415 — harness scripts/, reached only when pricing
    import provider_lane  # noqa: PLC0415

    return model_prices.load(str(provider_lane.routing_ssot_path())), model_prices.rates


def price(usage, served_model, table, rates):
    """`usage` with cost filled from `rates(table, served_model)`, never guessed.

    A reported cost is kept. No served id, no rate row, a missing token count, or
    any cache-creation tokens (model_prices has no rate for them) all leave
    cost_usd null with cost_source none — a subset of the spend is never priced.
    Cache-creation tokens bill at the 5m write rate, except the `cache_creation_1h_tokens`
    share (when the transcript split them) at the 1h rate. A rate row without write
    rates (3-tuple) cannot price non-zero cache creation."""
    if usage.get("cost_source") == "reported":
        return dict(usage)
    counts = [usage.get(k) for k in _TOKENS]
    rate = rates(table, served_model) if served_model else None
    if rate is None or any(c is None for c in counts) or (counts[3] and len(rate) < 5):
        return dict(usage, cost_usd=None, cost_source="none")
    cost = counts[0] * rate[0] + counts[1] * rate[1] + counts[2] * rate[2]
    if counts[3]:
        h1 = min(_count(usage.get("cache_creation_1h_tokens")) or 0, counts[3])
        cost += (counts[3] - h1) * rate[3] + h1 * rate[4]
    cost /= 1_000_000
    return dict(usage, cost_usd=round(cost, 6), cost_source="model_prices")


def ledger_usage(receipt):
    """The rule-6 usage block for one outcome record, from THIS dispatch's
    receipt (None when there is none or it is stale). Never raises."""
    usage = (receipt or {}).get("usage")
    if not isinstance(usage, dict) or usage.get("source") in (None, "unavailable"):
        return dict(UNAVAILABLE)
    served = (receipt.get("attested") or {}).get("model")
    try:
        if usage.get("cost_source") == "reported":
            return dict(usage)
        return price(usage, served, *_price_table())
    except Exception:  # noqa: BLE001 — an observer never fails the ledger write
        return dict(usage, cost_usd=None, cost_source="none")
