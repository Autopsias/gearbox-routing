"""Shared fixture data and record builders for the aggregate_outcomes tests.

Extracted when test_aggregate_outcomes.py was split at the apex-revisit
boundary: both halves build the same ledger records against the same synthetic
SSOT, and a second copy would drift from the first.

The SSOT fixture is SYNTHETIC on purpose — never the live repo file — so class
defaults and prices stay pinned and independent of future SSOT edits.
"""
import hashlib
import json


import aggregate_outcomes as ao

SSOT_FIXTURE = """version: 99
active_provider: anthropic

prices:
  fable:   { in: 10.00, out: 50.00 }
  opus:    { in:  5.00, out: 25.00 }
  sonnet:  { in:  3.00, out: 15.00 }
  haiku:   { in:  1.00, out:  5.00 }

task_classes:
  mechanical:     { tier: cheap_fast,      effort: light    }
  standard_build: { tier: workhorse,       effort: standard }
  agentic_build:  { tier: frontier_reasoner, effort: thorough }
  deep_reasoning: { tier: frontier_reasoner, effort: standard }
  linchpin:       { tier: frontier_reasoner, effort: thorough }

providers:
  anthropic:
    models:
      cheap_fast:        haiku
      workhorse:         sonnet
      frontier_reasoner: opus
      apex_reasoner:     fable
    effort:
      control: effort
      map:
        cheap_fast:        { light: null, standard: null,   thorough: null }
        workhorse:         { light: low,  standard: medium, thorough: high }
        frontier_reasoner: { light: low,  standard: medium, thorough: high }
        apex_reasoner:     { light: low,  standard: medium, thorough: high }
"""

EPOCH = 99




def _rid(project, plan, session, generation, attempt, resolution):
    blob = "|".join(str(x) for x in (project, plan, session, generation, attempt, resolution))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:24]


def _rec(**kw):
    base = dict(
        ts="2026-08-01T00:00:00+00:00",
        project="proj", plan="planA", session="s01", generation=0,
        task_class="agentic_build", backend="claude",
        model_authored="opus", reasoning_authored="high", tier_authored="opus@high",
        model_ran="opus", reasoning_ran="high", tier_ran="opus@high",
        model_ran_source="attested", effort_mechanism="tier_agent",
        degraded_from=None, escalated_from=None, routing_experiment=None,
        routing_provenance="default_resolved", ssot_version_ran=EPOCH,
        attempt=1, rework_count=0, stuck_armed=False, gates_failed=[],
        result="passed", verified=True,
    )
    base.update(kw)
    resolution = base.pop("resolution", "unit")
    base["record_id"] = _rid(base["project"], base["plan"], base["session"],
                              base["generation"], base["attempt"], resolution)
    return base


def _cohort(session, result="passed", attempts=1, **overrides):
    """attempts-1 rework records followed by one terminal record. `overrides`
    always wins over the computed defaults (e.g. an explicit `verified=`)."""
    recs = []
    for i in range(1, attempts):
        kw = {"session": session, "attempt": i, "result": "rework", "verified": False}
        kw.update(overrides)
        recs.append(_rec(**kw))
    kw = {"session": session, "attempt": attempts, "result": result, "verified": (result == "passed")}
    kw.update(overrides)
    recs.append(_rec(**kw))
    return recs


def _write(path, records):
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def _run(tmp_path, ssot_path, records, **kw):
    ledger = tmp_path / "outcomes.ndjson"
    _write(ledger, records)
    return ao.run(ledger, ssot_path, **kw)


# Both halves build canary cohorts; keyed by proposal_id, never inferred.
def _canary(session, result="passed", ts="2026-08-01T00:00:00+00:00", proposal_id="pidX", **kw):
    base = {
        "ts": ts, "model_authored": "sonnet", "reasoning_authored": "high",
        "routing_provenance": "experimental",
        "routing_experiment": {"kind": "canary", "proposal_id": proposal_id},
    }
    base.update(kw)
    return _cohort(session, result=result, **base)


# --------------------------------------------------------------------------
# Compaction-ledger fixture builders — shared by test_compaction_retro.py and
# test_compaction_report.py (extracted when the former crossed its size bound;
# a second copy would drift from the first).
# --------------------------------------------------------------------------
def act_row(iv, ts, seq=1, **over):
    row = {"schema": 1, "event_id": f"{iv}#{seq}", "ts_utc": ts, "intervention": iv,
           "scope": "s", "evidence": "e", "source": "session"}
    row.update(over)
    return row


def dec_row(session, ts, **over):
    row = {"ts": ts, "session": session, "event": "prompt", "source": "live",
           "policy_version": 1, "reason": "classified:default", "decision": "allow"}
    row.update(over)
    return row


def hb_row(session, ts, **over):
    row = {"ts": ts, "session": session, "type": "default", "policy_version": 1,
           "kill_switch": "off", "hooks_registered": True}
    row.update(over)
    return row


def write_ndjson(path, rows):
    # A row may be a dict OR a raw string — the latter is how tests plant
    # deliberately malformed ledger lines.
    path.write_text("".join(json.dumps(r) + "\n" if isinstance(r, dict) else r + "\n"
                            for r in rows))


def dyno_root(tmp_path, activations=(), decisions=(), sessions=(), version=1, policies=None):
    root = tmp_path / "dyno"
    (root / "policy").mkdir(parents=True)
    write_ndjson(root / "activations.ndjson", activations)
    write_ndjson(root / "decisions.ndjson", decisions)
    write_ndjson(root / "sessions.ndjson", sessions)
    if version is not None:
        (root / "policy" / "VERSION.json").write_text(json.dumps({"policy_version": version}))
    for sid, typ in (policies or {}).items():
        (root / "policy" / f"{sid}.json").write_text(json.dumps({"type": typ}))
    return root


def scan_of(sessions):
    return {"sessions": sessions}


def sess_row(sid, started, ended, cost=1.0, **over):
    row = {"session_id": sid, "started": started, "ended": ended, "cost_usd": cost,
           "base_context_tokens": 1000, "context_peak_tokens": 2000,
           "reread_cost_pct": 10.0, "compactions": 0, "agent_dispatches": [],
           "first_prompt": "hello"}
    row.update(over)
    return row
