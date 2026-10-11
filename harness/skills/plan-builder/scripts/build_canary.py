"""Routing canaries: plan-builder MARKS, plan-execute `begin` PICKS.

A memory note loads in one project only, so a canary armed by a note never reached plans
built in other repos. Config: ../references/build-canaries.json.

Two halves, so the model is still chosen at dispatch (plan schema v8, route at dispatch):
- `apply(spec)` runs on a fresh build (never --rebuild). It writes only the canary tag and
  the climb's fix-round budget. It never writes a model: a session that names one is an
  author override, and route at dispatch would never pick for it again.
- `dispatch_cell(session, provider)` runs inside `begin` (route_at_dispatch._resolve). While
  the canary still wants cohorts it returns the canary cell, which `begin` freezes like any
  class default; once the canary stops it returns None and the session gets its class default.

A session is marked only when nothing the author wrote says otherwise: its task_class
matches, it pins no model or reasoning, carries no routing_experiment, keeps escalation on
(the climb IS the design), has no peer_triggers (a risky session keeps its floor), and has
verify gates (an ungated session yields no evidence). Any doubt about the ledger -> no canary.
PLAN_BUILDER_BUILD_CANARY=off turns both halves off (the test suites set it).
"""
import functools
import json
import os
import sys
from pathlib import Path

import plan_limits as pl
import route_at_dispatch_build as rad

CONFIG = Path(__file__).resolve().parents[1] / "references" / "build-canaries.json"
_RETRO = Path(__file__).resolve().parents[2] / "routing-retro" / "scripts"
#: stages that still want cohorts; None = no cohort yet. smoke-failed and every N>=10 stage stop it.
RUNNING = {None, "smoke-in-progress", "smoke-passed-awaiting-adoption-evidence"}
LEDGER_ENV = "PLAN_EXECUTE_OUTCOME_LEDGER"  # the ledger plan-execute writes, when overridden


def _off():
    return os.environ.get("PLAN_BUILDER_BUILD_CANARY", "").strip().lower() == "off"


def _canaries(config=CONFIG):
    return [c for c in json.loads(Path(config).read_text()).get("canaries", []) if c.get("active")]


@functools.cache
def _ledger():
    """{proposal_id: stage} from the outcome ledger plan-execute writes, or None if unreadable.
    run_deferred, never run(): the aggregator's stamp is not advanced, so this is read-only.
    Cached: one `begin` may ask for several sessions, and the answer cannot change mid-call."""
    try:
        if str(_RETRO) not in sys.path:
            sys.path.insert(0, str(_RETRO))
        import aggregate_outcomes as agg  # noqa: PLC0415 — only a canary needs it

        path = os.environ.get(LEDGER_ENV) or agg.default_ledger_path()
        out, _ = agg.run_deferred(path, rad._resolver()._default_ssot_path())
    except Exception as e:  # noqa: BLE001 — a canary is optional; a build or a begin is not
        print(f"build-canary: outcome ledger unreadable ({e}); no canary", file=sys.stderr)
        return None
    return {c["proposal_id"]: c["stage"] for c in out.get("canaries", [])}


def _running(c, provider, ledger):
    stages = ledger()
    stage = None if stages is None else stages.get(c["proposal_id"])
    ok = stages is not None and provider == c["provider"] and stage in RUNNING
    return ok, stage


def _eligible(s, tc):
    v = s.get("verify") or {}
    return (str(s.get("task_class") or "").strip().lower() == tc
            and not any(str(s.get(k) or "").strip() for k in ("model", "reasoning", "why_model"))
            and "routing_experiment" not in s and s.get("escalation") is not False
            and not s.get("peer_triggers") and bool(v.get("gates")))


def apply(spec, ledger=_ledger, config=CONFIG):
    """Mark a NEW plan's eligible sessions: the tag and the fix-round budget, never a model."""
    if _off():
        return spec
    try:
        provider = rad._tree_provider(Path(rad._resolver()._default_ssot_path()).read_text("utf-8"))
    except Exception as e:  # noqa: BLE001
        print(f"build-canary: routing file unreadable ({e}); no canary", file=sys.stderr)
        return spec
    for c in _canaries(config):
        pid = c["proposal_id"]
        ok, stage = _running(c, provider, ledger)
        if not ok:
            print(f"build-canary: {pid} not applied (provider {provider}, stage {stage})", file=sys.stderr)
            continue
        ids = []
        for s in spec.get("sessions", []):
            if _eligible(s, c["task_class"]):
                s["routing_experiment"] = {"kind": "canary", "proposal_id": pid}
                s["verify"]["max_rework"] = max(s["verify"].get("max_rework", pl.DEFAULT_MAX_REWORK),
                                                c["max_rework"])
                ids.append(s["id"])
        if ids:
            print(f"build-canary: {pid} ({stage or 'first cohort'}) marks {', '.join(ids)}; "
                  f"begin picks {c['model']}@{c['reasoning']} while it runs. "
                  "Pin a model on a session to keep it out.", file=sys.stderr)
    return spec


def dispatch_cell(session, provider, ledger=_ledger, config=CONFIG):
    """`begin`'s half: ``(model, reasoning, proposal_id)`` while the session's canary runs on
    this provider, else None (the session gets its class default)."""
    rx = session.get("routing_experiment") or {}
    if _off() or rx.get("kind") != "canary":
        return None
    for c in _canaries(config):
        if c["proposal_id"] == rx.get("proposal_id") and _running(c, provider, ledger)[0]:
            return c["model"], c["reasoning"], c["proposal_id"]
    return None
