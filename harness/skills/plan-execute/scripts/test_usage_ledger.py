"""EXE-03 — tokens and cost per attempt, from transcript to ledger to retro report.

Route-at-dispatch contract rule 6. Every fixture below is cut from a REAL file of
its kind on this machine (s07), trimmed to the fields the parser reads:

  * CLAUDE_JSON — evals/routing/s09-fable-effort-canary/results/trial-5-xhigh.json
    (a headless `claude -p --output-format json` result; `result` text shortened).
  * AGENT_PARTIAL — an Agent-tool subagent transcript (subagents/agent-<id>.jsonl):
    three API messages, none with its final `stop_reason` line on disk.
  * WORKFLOW_FINAL — a Workflow member transcript
    (subagents/workflows/<wf>/agent-<id>.jsonl): every message has its final line.
  * CODEX_JSON — a `codex exec --json` stdout log, plain-text log lines included.

Values are asserted EXACTLY, never for presence: a chain that silently degrades
to source "unavailable" must fail here, not read as "no data".

Run: pytest skills/plan-execute/scripts/test_usage_ledger.py -q
"""
import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parents[2]
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "skills" / "routing-retro" / "scripts"))

import attestation  # noqa: E402
import model_prices  # noqa: E402
import outcomes as outc  # noqa: E402
import run  # noqa: E402
import run_state_io as rsi  # noqa: E402
from test_outcomes import SESS, make_plan  # noqa: E402

import aggregate_outcomes as ao  # noqa: E402
import render_report  # noqa: E402
from retro_test_helpers import SSOT_FIXTURE, _rec  # noqa: E402

CLAUDE_JSON = {
    "type": "result", "subtype": "success", "is_error": False, "num_turns": 1,
    "total_cost_usd": 0.04747199999999999, "result": "Light rope A at both ends",
    "usage": {"input_tokens": 2, "cache_creation_input_tokens": 0,
              "cache_read_input_tokens": 33518, "output_tokens": 265,
              "output_tokens_details": {"thinking_tokens": 116}},
    "modelUsage": {
        "claude-haiku-4-5-20251001": {"inputTokens": 579, "outputTokens": 21,
                                      "cacheReadInputTokens": 0, "cacheCreationInputTokens": 0,
                                      "costUSD": 0.0006839999999999999},
        "claude-fable-5": {"inputTokens": 2, "outputTokens": 265, "cacheReadInputTokens": 33518,
                           "cacheCreationInputTokens": 0, "costUSD": 0.046787999999999996},
    },
}


def _msg(mid, stop, out, cread, ccreate, block="tool_use"):
    return {"type": "assistant", "message": {
        "id": mid, "model": "claude-opus-5-5", "stop_reason": stop, "content": [{"type": block}],
        "usage": {"input_tokens": 2, "output_tokens": out, "cache_read_input_tokens": cread,
                  "cache_creation_input_tokens": ccreate}}}


AGENT_PARTIAL = [
    {"type": "user", "message": {"role": "user", "content": "prompt"}},
    _msg("msg_011CfYJucR9SSk6vRfbj5UEv", None, 8, 0, 65087, "thinking"),
    _msg("msg_011CfYJucR9SSk6vRfbj5UEv", None, 8, 0, 65087),
    {"type": "attachment", "attachment": {"type": "hook"}},
    _msg("msg_011CfYJuvzbywpNxtdELhUZg", None, 16, 65087, 23545),
    _msg("msg_011CfYJvEFRXWBfRqUYRyggP", None, 8, 88632, 5893, "thinking"),
    _msg("msg_011CfYJvEFRXWBfRqUYRyggP", None, 8, 88632, 5893),
]
WORKFLOW_FINAL = [
    {"type": "user", "message": {"role": "user", "content": "prompt"}},
    _msg("msg_011CfSjZ2jfwzqk6MYfwobdF", "tool_use", 181, 20830, 22260),
    _msg("msg_011CfSjZH7d2HaSSQccU2Ty2", "tool_use", 116, 43090, 6241),
    _msg("msg_011CfSjZR2cmknCTsTBh4jdL", None, 5, 49331, 24958, "thinking"),
    _msg("msg_011CfSjZR2cmknCTsTBh4jdL", "tool_use", 1944, 49331, 24958),
    _msg("msg_011CfSjZR2cmknCTsTBh4jdL", None, 5, 49331, 24958),  # a late snapshot never wins
]
CODEX_TURN = {"type": "turn.completed", "usage": {
    "input_tokens": 2544789, "cached_input_tokens": 2417664, "cache_write_input_tokens": 0,
    "output_tokens": 21855, "reasoning_output_tokens": 12093}}
CODEX_JSON = "\n".join([
    "2026-09-03T23:15:19.374147Z ERROR codex_models_manager::manager: failed to load models cache",
    json.dumps({"type": "thread.started", "thread_id": "01a0698e-3c5a-7193-adb1-2414f6f34cb8"}),
    json.dumps({"type": "turn.started"}),
    json.dumps({"type": "item.completed", "item": {"id": "item_0", "type": "error"}}),
    json.dumps(CODEX_TURN),
]) + "\n"

# $/MTok. claude-fable-5 carries the SSOT's rates: priced, the claude-json fixture
# reproduces its own reported costUSD (2*10 + 265*50 + 33518*1.00 = 46,788 µ$).
TABLE = {"claude-fable-5": (10.0, 50.0, 1.0), "claude-opus-5-5": (4.0, 20.0, 0.2, 5.0, 8.0),
         "gpt-codex-test": (1.25, 10.0, 0.125)}


def _jsonl(path, events):
    path.write_text("".join(json.dumps(e) + "\n" for e in events))
    return str(path)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    ledger = tmp_path / "ledger" / "outcomes.ndjson"
    monkeypatch.setenv(outc.LEDGER_PATH_ENV, str(ledger))
    monkeypatch.setattr(attestation, "_price_table", lambda: (TABLE, model_prices.rates))
    return ledger


def _ledger(path):
    return [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()]


def _dispatch_and_record(plan_dir, transcript, backend="claude", agent="agent-1"):
    rsi.log_event(plan_dir, "dispatch_started", session_ids=["s01"])
    run.cmd_record_receipt(plan_dir, "s01", agent, transcript, backend)


# --------------------------------------------------------------------------
# Transcript parsing — one real shape each
# --------------------------------------------------------------------------
def test_claude_json_usage_is_the_served_models_with_its_reported_cost(tmp_path):
    path = tmp_path / "result.json"
    path.write_text(json.dumps(CLAUDE_JSON))
    attested, usage = attestation.read_transcript("claude", str(path))
    assert attested == {"model": "claude-fable-5", "reasoning": None}
    assert usage == {"input_tokens": 2, "output_tokens": 265, "cache_read_tokens": 33518,
                     "cache_creation_tokens": 0, "cost_usd": 0.046787999999999996,
                     "cost_source": "reported", "source": "claude-json", "scope": "worker"}


def test_agent_transcript_with_snapshot_lines_leaves_output_and_cost_null(tmp_path):
    """Measured: a message with no final line records a streaming snapshot of
    output_tokens (8 for a 4,000-character turn). Summing snapshots would price
    a fraction of the output, so output is null, never partial."""
    attested, usage = attestation.read_transcript("claude", _jsonl(tmp_path / "a.jsonl", AGENT_PARTIAL))
    assert attested == {"model": "claude-opus-5-5", "reasoning": None}
    assert usage == {"input_tokens": 6, "output_tokens": None, "cache_read_tokens": 153719,
                     "cache_creation_tokens": 94525, "cost_usd": None, "cost_source": "none",
                     "source": "subagent-transcript", "scope": "worker"}


def test_workflow_member_transcript_counts_each_message_once_from_its_final_line(tmp_path):
    attested, usage = attestation.read_transcript("claude", _jsonl(tmp_path / "w.jsonl", WORKFLOW_FINAL))
    assert attested["model"] == "claude-opus-5-5"
    assert (usage["input_tokens"], usage["output_tokens"], usage["cache_read_tokens"],
            usage["cache_creation_tokens"]) == (6, 2241, 113251, 53459)
    assert usage["source"] == "subagent-transcript"
    # 6*4 + 2241*20 + 113251*0.2 + 53459*5 (all 5m: no TTL split in the transcript) = 334,789.2 µ$
    priced = attestation.price(usage, "claude-opus-5-5", TABLE, model_prices.rates)
    assert (priced["cost_usd"], priced["cost_source"]) == (0.334789, "model_prices")


def test_cache_creation_splits_5m_and_1h_when_the_transcript_reports_it(tmp_path):
    ev = _msg("m1", "end_turn", 100, 0, 1000, block="text")
    ev["message"]["usage"]["cache_creation"] = {"ephemeral_5m_input_tokens": 400,
                                                "ephemeral_1h_input_tokens": 600}
    _, usage = attestation.read_transcript("claude", _jsonl(tmp_path / "s.jsonl", [AGENT_PARTIAL[0], ev]))
    assert usage["cache_creation_tokens"] == 1000 and usage["cache_creation_1h_tokens"] == 600
    priced = attestation.price(usage, "claude-opus-5-5", TABLE, model_prices.rates)
    # 2*4 + 100*20 + 400*5 + 600*8 = 8,808 µ$; all-5m would read 7,008
    assert priced["cost_usd"] == 0.008808
    # a 3-tuple row (no write rates) still refuses to price cache creation
    assert attestation.price(usage, "claude-fable-5", TABLE, model_prices.rates)["cost_usd"] is None


def test_codex_input_is_non_cached_and_turns_are_summed(tmp_path):
    path = tmp_path / "codex.log.jsonl"
    path.write_text(CODEX_JSON)
    attested, usage = attestation.read_transcript("codex", str(path))
    assert attested is None  # no served-model field in any codex event
    assert usage == {"input_tokens": 127125, "output_tokens": 21855, "cache_read_tokens": 2417664,
                     "cache_creation_tokens": 0, "cost_usd": None, "cost_source": "none",
                     "source": "codex-json", "scope": "worker"}
    path.write_text(CODEX_JSON + json.dumps(CODEX_TURN) + "\n")  # a second turn
    _, twice = attestation.read_transcript("codex", str(path))
    assert (twice["input_tokens"], twice["output_tokens"], twice["cache_read_tokens"]) == (
        254250, 43710, 4835328)


def test_codex_priced_cost_counts_cached_tokens_once(tmp_path):
    path = tmp_path / "codex.log.jsonl"
    path.write_text(CODEX_JSON)
    _, usage = attestation.read_transcript("codex", str(path))
    priced = attestation.price(usage, "gpt-codex-test", TABLE, model_prices.rates)
    # 127125*1.25 + 21855*10 + 2417664*0.125 = 679,664.25 µ$. Billing the raw
    # input_tokens as well would read 3,701,744 µ$.
    assert priced["cost_usd"] == 0.679664
    assert priced["cost_source"] == "model_prices"


def test_codex_without_cache_write_count_leaves_input_and_cost_unknown(tmp_path):
    path = tmp_path / "codex.log.jsonl"
    turn = {**CODEX_TURN, "usage": {k: v for k, v in CODEX_TURN["usage"].items()
                                    if k != "cache_write_input_tokens"}}
    path.write_text(CODEX_JSON.replace(json.dumps(CODEX_TURN), json.dumps(turn)))
    _, usage = attestation.read_transcript("codex", str(path))
    # Unknown writes may sit inside input_tokens: never report a guessed non-cached count.
    assert (usage["input_tokens"], usage["cache_creation_tokens"]) == (None, None)
    assert (usage["output_tokens"], usage["cache_read_tokens"]) == (21855, 2417664)
    priced = attestation.price(usage, "gpt-codex-test", TABLE, model_prices.rates)
    assert (priced["cost_usd"], priced["cost_source"]) == (None, "none")


def test_price_never_uses_an_unknown_model_or_a_missing_count():
    usage = dict(attestation.UNAVAILABLE, input_tokens=2, output_tokens=265, cache_read_tokens=33518,
                 cache_creation_tokens=0, source="claude-json")
    assert attestation.price(usage, None, TABLE, model_prices.rates)["cost_source"] == "none"
    assert attestation.price(usage, "sonnet", TABLE, model_prices.rates)["cost_usd"] is None  # alias
    assert attestation.price(dict(usage, output_tokens=None), "claude-fable-5", TABLE,
                             model_prices.rates)["cost_usd"] is None
    assert attestation.price(usage, "claude-fable-5", TABLE, model_prices.rates)["cost_usd"] == 0.046788


# --------------------------------------------------------------------------
# Receipt -> ledger (record-receipt, then the real outcomes.write)
# --------------------------------------------------------------------------
def test_claude_json_usage_reaches_the_ledger(tmp_path, isolated):
    plan_dir = make_plan(tmp_path, [SESS])
    path = tmp_path / "result.json"
    path.write_text(json.dumps(CLAUDE_JSON))
    _dispatch_and_record(plan_dir, str(path))
    outc.write(plan_dir, "s01", resolution="unit", result="done_unverified", verified=False)
    rec = _ledger(isolated)[0]
    assert rec["usage"] == {"input_tokens": 2, "output_tokens": 265, "cache_read_tokens": 33518,
                            "cache_creation_tokens": 0, "cost_usd": 0.046787999999999996,
                            "cost_source": "reported", "source": "claude-json", "scope": "worker"}


def test_unreported_cost_is_priced_against_the_served_id(tmp_path, isolated):
    data = json.loads(json.dumps(CLAUDE_JSON))
    del data["modelUsage"]["claude-fable-5"]["costUSD"]
    plan_dir = make_plan(tmp_path, [SESS])  # authored alias: sonnet
    path = tmp_path / "result.json"
    path.write_text(json.dumps(data))
    _dispatch_and_record(plan_dir, str(path))
    outc.write(plan_dir, "s01", resolution="unit", result="done_unverified", verified=False)
    usage = _ledger(isolated)[0]["usage"]
    assert (usage["cost_usd"], usage["cost_source"]) == (0.046788, "model_prices")


def test_agent_and_codex_tokens_reach_the_ledger(tmp_path, isolated):
    plan_dir = make_plan(tmp_path, [SESS])
    _dispatch_and_record(plan_dir, _jsonl(tmp_path / "w.jsonl", WORKFLOW_FINAL))
    outc.write(plan_dir, "s01", resolution="a", result="rework", verified=False, attempt=1)
    codex = tmp_path / "codex.log.jsonl"
    codex.write_text(CODEX_JSON)
    _dispatch_and_record(plan_dir, str(codex), backend="codex", agent="agent-2")
    outc.write(plan_dir, "s01", resolution="b", result="done_unverified", verified=False, attempt=2)
    first, second = (r["usage"] for r in _ledger(isolated))
    assert (first["source"], first["output_tokens"], first["cache_creation_tokens"],
            first["cost_source"]) == ("subagent-transcript", 2241, 53459, "model_prices")
    assert (second["source"], second["input_tokens"], second["cache_read_tokens"],
            second["cost_usd"]) == ("codex-json", 127125, 2417664, None)


def test_no_receipt_and_stale_receipt_both_read_unavailable_with_null_counts(tmp_path, isolated):
    plan_dir = make_plan(tmp_path, [SESS])
    outc.write(plan_dir, "s01", resolution="a", result="rework", verified=False, attempt=1)
    path = tmp_path / "result.json"
    path.write_text(json.dumps(CLAUDE_JSON))
    _dispatch_and_record(plan_dir, str(path))
    rsi.log_event(plan_dir, "dispatch_started", session_ids=["s01"])  # re-dispatch, no receipt
    outc.write(plan_dir, "s01", resolution="b", result="done_unverified", verified=False, attempt=2)
    for rec in _ledger(isolated):
        assert rec["usage"] == attestation.UNAVAILABLE


# --------------------------------------------------------------------------
# Ledger -> /routing-retro aggregate -> rendered report
# --------------------------------------------------------------------------
def _usage(cost):
    return dict(attestation.UNAVAILABLE, input_tokens=1, output_tokens=1, cache_read_tokens=0,
                cache_creation_tokens=0, cost_usd=cost, cost_source="reported", source="claude-json")


def test_ledger_to_report_shows_cost_per_attempt_and_per_completed_session(tmp_path, isolated):
    plan_dir = make_plan(tmp_path, [SESS])
    path = tmp_path / "result.json"
    path.write_text(json.dumps(CLAUDE_JSON))
    _dispatch_and_record(plan_dir, str(path))
    outc.write(plan_dir, "s01", resolution="a", result="rework", verified=False, attempt=1)
    _dispatch_and_record(plan_dir, str(path), agent="agent-2")
    outc.write(plan_dir, "s01", resolution="b", result="passed", verified=True, attempt=2)
    ssot = tmp_path / "model-routing.yaml"
    ssot.write_text(SSOT_FIXTURE)
    agg = ao.run(isolated, ssot)
    assert agg["cost_cells"] == [{
        "task_class": "standard_build", "model_ran": "claude-fable-5", "reasoning_ran": "high",
        "attempts": 2, "attempts_with_usage": 2, "attempts_with_cost": 2,
        "median_cost_per_attempt": 0.046788, "completed_sessions": 1,
        "median_cost_per_completed_session": 0.093576, "incomplete_cost_sessions": 0}]
    html = render_report.render(agg)
    assert "0.046788 USD" in html and "0.093576 USD" in html


def test_cheap_cell_that_escalates_costs_more_per_completed_session(tmp_path):
    """Operator decision 2026-09-29: a cheap cell that fails and escalates is not
    cheap. Sonnet@medium fails at $0.50 and escalates to Opus@high at $3.00; a
    session that starts on Opus@high passes first time at $2.00. Per attempt
    Sonnet looks cheaper; per completed session it is the dearer cell."""
    cheap = dict(task_class="standard_build", model_ran="sonnet", reasoning_ran="medium")
    dear = dict(task_class="standard_build", model_ran="opus", reasoning_ran="high")
    records = [
        _rec(session="s01", attempt=1, result="rework", usage=_usage(0.5), **cheap),
        _rec(session="s01", attempt=2, result="passed", usage=_usage(3.0), **dear),
        _rec(session="s02", attempt=1, result="passed", usage=_usage(2.0), **dear),
        # incomplete-cost: one attempt carries no cost, so the session stays out
        _rec(session="s03", attempt=1, result="rework", usage=attestation.UNAVAILABLE, **cheap),
        _rec(session="s03", attempt=2, result="passed", usage=_usage(1.0), **cheap),
    ]
    ledger = tmp_path / "outcomes.ndjson"
    ledger.write_text("".join(json.dumps(r) + "\n" for r in records))
    ssot = tmp_path / "model-routing.yaml"
    ssot.write_text(SSOT_FIXTURE)
    cells = {(c["model_ran"], c["reasoning_ran"]): c for c in ao.run(ledger, ssot)["cost_cells"]}
    s, o = cells[("sonnet", "medium")], cells[("opus", "high")]
    assert (s["median_cost_per_attempt"], o["median_cost_per_attempt"]) == (0.75, 2.5)
    assert (s["median_cost_per_completed_session"], s["completed_sessions"],
            s["incomplete_cost_sessions"]) == (3.5, 1, 1)
    assert (o["median_cost_per_completed_session"], o["completed_sessions"]) == (2.0, 1)
    assert s["median_cost_per_completed_session"] > o["median_cost_per_completed_session"]
    assert (s["attempts"], s["attempts_with_usage"], s["attempts_with_cost"]) == (3, 2, 2)
