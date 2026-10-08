"""Shared fixtures and helpers for the ESC-02 escalation tests.

Extracted when test_escalation.py was split: both halves need the same plan
stamping, the same armed-stuck state and the same SESS shape, and a second copy
would drift from the first.
"""
import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
BUILD_PLAN = SCRIPTS.parent.parent / "plan-builder" / "scripts"
sys.path.insert(0, str(BUILD_PLAN))

import article_block as ab  # noqa: E402,F401
import build_plan  # noqa: E402,F401
import dispatch as dsp  # noqa: E402,F401
import escalation as esca  # noqa: E402
import manifest_io as mio  # noqa: E402,F401
import egress  # noqa: E402,F401
import run  # noqa: E402
import run_state_io as rsi  # noqa: E402
import stuck_protocol as sp  # noqa: E402,F401
import verify as vfy  # noqa: E402,F401
from test_shipping import make_plan  # noqa: E402,F401

FIXTURES = SCRIPTS.parent / "fixtures"
REPO = SCRIPTS.parents[2]


def _resolver():
    return run._import_resolver()


def _begin(plan_dir, sessions, capsys, **kw):
    capsys.readouterr()                      # drain whatever an earlier command printed
    run.cmd_begin(plan_dir, sessions, **kw)
    out = json.loads(capsys.readouterr().out)
    return {m["id"]: m for m in out.get("batch", [])}, out


def _stamp(plan_dir, version):
    """Re-stamp a fixture plan's manifest — the ONLY way a test opts a plan into
    (or out of) the escalation gate, since the stamp is the gate."""
    m = Path(plan_dir) / "manifest.json"
    man = json.loads(m.read_text())
    man["plan_schema_version"] = version
    m.write_text(json.dumps(man, indent=2, ensure_ascii=False))


def _arm(plan_dir, session_id, *, consecutive, reworks):
    """Put the session in the state a real rework loop would have left: N
    consecutive same-signature failures recorded, N reworks spent, and the
    DISPATCH HISTORY those failures imply.

    The history is not decoration. The climb ACCUMULATES from the rung the session
    is standing on (so a second root cause climbs from there rather than restarting
    at the authored cell), which makes the rung path-dependent — a hand-written
    counter with no dispatch behind it describes a session that never ran."""
    state = rsi.load_state(plan_dir)
    state.setdefault("stuck", {})[session_id] = {
        "sig": "deadbeef", "class": "AssertionError", "locus": "boom",
        "consecutive": consecutive, "attempts": consecutive, "previous_sig": None,
    }
    # ...as of the PREVIOUS dispatch: one rung lower, one failure earlier.
    state.setdefault(esca.STATE_KEY, {}).setdefault(session_id, {}).update(
        {"climb": max(0, consecutive - 2), "last_rung": max(0, consecutive - 2),
         "last_attempts": max(0, consecutive - 1)}
    )
    rsi.save_state(plan_dir, state)
    vs = Path(plan_dir) / "_verify_state"
    vs.mkdir(exist_ok=True)
    (vs / f"{session_id}.json").write_text(json.dumps({
        "session_id": session_id, "rework_count": reworks, "max_rework": 9,
        "gates": [], "gate_status": {}, "on_fail": "rework", "outcome": "rework",
    }))


SESS = {"id": "s01", "title": "S1", "items": ["it-1"], "model": "Sonnet",
        "reasoning": "high", "task_class": "agentic_build", "prompt": "work"}


def _record_refusal(plan_dir, sid, model, reasoning, capsys):
    capsys.readouterr()                      # drain whatever an earlier command printed
    run.cmd_record_refusal(plan_dir, sid, model, reasoning,
                           reason="observed: model unavailable at dispatch",
                           source="dispatch_error")
    return json.loads(capsys.readouterr().out)
