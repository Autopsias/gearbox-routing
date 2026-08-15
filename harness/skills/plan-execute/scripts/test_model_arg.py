"""Per-session model propagation (regression for the dropped `model` directive).

The manifest declares a per-session `model` (e.g. "Sonnet"). Before this fix the
`begin` output omitted it, so the orchestrator dispatched every subagent on the
inherited (parent) model and the plan's model directive was silently lost.

Covers:
  * `_normalize_model` maps free-form manifest values to the Task `model` token.
  * `cmd_begin` emits both the raw `model` and the normalized `model_arg`.

Run: pytest plan-execute/scripts/test_model_arg.py -q
"""

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import closeout_pipeline as cp  # noqa: E402
import run  # noqa: E402
from test_shipping import make_plan  # noqa: E402  (reuse the plan fixture builder)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Sonnet", "sonnet"),
        ("Opus", "opus"),
        ("Haiku", "haiku"),
        ("Fable", "fable"),
        ("sonnet", "sonnet"),
        ("OPUS", "opus"),
        ("Opus 4.6", "opus"),
        ("Claude Sonnet 4.6", "sonnet"),
        ("Fable 5", "fable"),
        ("claude-fable-5", "fable"),
        ("", None),
        (None, None),
        ("gpt-5", None),
        ("Gemini", None),
        (123, None),
    ],
)
def test_normalize_model(raw, expected):
    assert run._normalize_model(raw) == expected


def test_begin_emits_model_arg(tmp_path, capsys):
    sessions = [
        {"id": "s01", "title": "S1", "items": ["i1"], "model": "Sonnet"},
        {"id": "s02", "title": "S2", "items": ["i2"], "model": "Opus"},
    ]
    plan_dir = make_plan(tmp_path, sessions)

    run.cmd_begin(plan_dir, ["s01", "s02"])
    out = json.loads(capsys.readouterr().out)

    by_id = {m["id"]: m for m in out["batch"]}
    assert by_id["s01"]["model"] == "Sonnet"
    assert by_id["s01"]["model_arg"] == "sonnet"
    assert by_id["s02"]["model"] == "Opus"
    assert by_id["s02"]["model_arg"] == "opus"

    run.cmd_release(plan_dir)


def test_begin_unrecognized_model_arg_is_null(tmp_path, capsys):
    # build_plan requires a `model` field, so a session always carries one; the
    # realistic null path is an unrecognized value (non-Claude, non-Codex / typo).
    # The raw value is preserved for transparency; model_arg is null so Task
    # inherits. (A Codex-LOOKING model, e.g. "gpt-5", no longer lands here — it
    # hard-fails as unroutable; see test_codex_dispatch.py.)
    sessions = [{"id": "s01", "title": "S1", "items": ["i1"], "model": "Gemini"}]
    plan_dir = make_plan(tmp_path, sessions)

    run.cmd_begin(plan_dir, ["s01"])
    out = json.loads(capsys.readouterr().out)

    member = out["batch"][0]
    assert member["model"] == "Gemini"
    assert member["model_arg"] is None
    assert member["backend"] == "claude"

    run.cmd_release(plan_dir)


def test_reasoning_tier_normalizes():
    assert run._reasoning_tier("low") == "low"
    assert run._reasoning_tier("Medium") == "medium"
    assert run._reasoning_tier("HIGH") == "high"
    assert run._reasoning_tier("max") == "max"


def test_reasoning_tier_unknown_or_empty():
    assert run._reasoning_tier("ultra") == ""
    assert run._reasoning_tier("") == ""
    assert run._reasoning_tier(None) == ""


def test_reasoning_directive_mapping():
    assert run._reasoning_directive("low") == ""  # low = normal generation
    assert run._reasoning_directive("") == ""
    assert run._reasoning_directive("medium").startswith("Think about")
    assert "edge cases" in run._reasoning_directive("high")
    assert run._reasoning_directive("max").startswith("Ultrathink")


def test_begin_bakes_reasoning_directive_and_emits_tier(tmp_path, capsys):
    sessions = [
        {"id": "s01", "title": "S1", "items": ["i1"], "model": "Haiku", "reasoning": "low"},
        {"id": "s02", "title": "S2", "items": ["i2"], "model": "Opus", "reasoning": "max"},
    ]
    plan_dir = make_plan(tmp_path, sessions)

    run.cmd_begin(plan_dir, ["s01", "s02"])
    out = json.loads(capsys.readouterr().out)
    by_id = {m["id"]: m for m in out["batch"]}

    # low → no directive prepended; tier still reported
    assert by_id["s01"]["reasoning"] == "low"
    assert not by_id["s01"]["prompt_text"].startswith("Ultrathink")

    # max → directive prepended to the top of the prompt; tier reported
    assert by_id["s02"]["reasoning"] == "max"
    assert by_id["s02"]["prompt_text"].startswith("Ultrathink")

    run.cmd_release(plan_dir)


def test_shadow_marker_is_emitted_only_as_task_description(tmp_path, capsys, monkeypatch):
    """Telemetry must never alter the primary work instructions.

    The hook marker is intentionally metadata for the outer Agent call.  This
    regression checks both the optional payload shape and the stronger invariant
    that the byte string sent as ``prompt_text`` is unchanged.
    """
    sessions = [{"id": "s01", "title": "S1", "items": ["i1"], "model": "Sonnet"}]
    plan_dir = make_plan(tmp_path, sessions)
    original_prompt = (plan_dir / "sessions" / "s01.prompt.md").read_text()
    marker = "plan-execute:s01 dyno-shadow-v1:abcdefghijklmnopqrstuvwxyz012345"
    monkeypatch.setattr(run, "_shadow_dispatch_description", lambda *_: marker)

    run.cmd_begin(plan_dir, ["s01"])
    member = json.loads(capsys.readouterr().out)["batch"][0]

    assert member["dispatch_description"] == marker
    assert member["prompt_text"] == original_prompt
    assert marker not in member["prompt_text"]
    run.cmd_release(plan_dir)


# --------------------------------------------------------------------------
# Reactive degradation (added 2026-07): xhigh tier + fallback ladder + audit record
# --------------------------------------------------------------------------
def test_reasoning_xhigh_directive_and_extra_synonym():
    # Regression: before xhigh existed here, "xhigh"/"extra" silently produced NO
    # directive. Both must now normalize + yield a real thinking directive.
    assert run._reasoning_tier("xhigh") == "xhigh"
    assert run._reasoning_tier("Extra") == "xhigh"  # Cowork picker label synonym
    d = run._reasoning_directive("xhigh")
    assert d and d != run._reasoning_directive("max")  # non-empty, distinct from max
    assert run._reasoning_directive("extra") == d


@pytest.mark.parametrize(
    "token,expected",
    [
        # PER-TARGET effort: fable→opus lands at HIGH too (opus high→xhigh is a
        # dead rung on our own calibration run) — stale at xhigh until 2026-07-26.
        ("fable", ("opus", "high")),
        # PER-TARGET effort: opus→sonnet lands at HIGH (sonnet high→xhigh is
        # a dead rung) — this expectation was stale at xhigh until s04.
        ("opus", ("sonnet", "high")),
        ("sonnet", None),   # floor — never auto-drop judgement work to Haiku
        ("haiku", None),
        (None, None),
        ("gpt-5", None),    # not in the ANTHROPIC ladder (default provider)
    ],
)
def test_fallback_for(token, expected):
    assert run._fallback_for(token) == expected


@pytest.mark.parametrize(
    "token,expected",
    [
        # v1.15 5.6-ONLY lane: gpt-5.5 and the whole `workhorse` tier are retired.
        ("gpt-5.6-sol", ("gpt-5.6-terra", "max")),
        # terra is the SINK: nothing degrades onto the mechanical-only luna (v1.15
        # judgement floor), and that is also what keeps the rescue below from closing
        # a terra->luna->terra cycle. A refused terra means NO-CODEX.
        ("gpt-5.6-terra", None),
        ("gpt-5.6-luna", ("gpt-5.6-terra", "max")),   # bottom tier — an UPWARD rescue, not a drop (CL-03, re-pointed v1.15)
        ("gpt-5.5", None),  # RETIRED — no longer in the ladder at all
        ("fable", None),    # not in the openai ladder
    ],
)
def test_fallback_for_openai(token, expected):
    assert run._fallback_for(token, "openai") == expected


def test_begin_emits_fallback_model(tmp_path, capsys):
    sessions = [
        {"id": "s01", "title": "S1", "items": ["i1"], "model": "Fable"},
        {"id": "s02", "title": "S2", "items": ["i2"], "model": "Opus"},
        {"id": "s03", "title": "S3", "items": ["i3"], "model": "Sonnet"},
    ]
    plan_dir = make_plan(tmp_path, sessions)

    run.cmd_begin(plan_dir, ["s01", "s02", "s03"])
    out = json.loads(capsys.readouterr().out)
    by_id = {m["id"]: m for m in out["batch"]}

    # Fable → Opus @ HIGH; Opus → Sonnet @ HIGH (per-target — sonnet's
    # xhigh is a dead rung); Sonnet is the floor (no fallback).
    assert by_id["s01"]["fallback_model"] == "opus"
    assert by_id["s01"]["fallback_reasoning"] == "high"
    assert by_id["s02"]["fallback_model"] == "sonnet"
    assert by_id["s02"]["fallback_reasoning"] == "high"
    assert by_id["s03"]["fallback_model"] is None
    assert by_id["s03"]["fallback_reasoning"] is None

    run.cmd_release(plan_dir)


def test_degraded_from_persists_and_is_digest_excluded(tmp_path):
    # The orchestrator records a substitution as `degraded_from`; it must survive
    # persist (audit trail) but NOT change the closeout digest (no false state-drift).
    base = {"session": "s01", "result": "DONE", "items_completed": ["i1"],
            "items_blocked": [], "notes": {"i1": "done"}}
    degraded = {**base, "degraded_from": {"requested": "fable", "ran": "opus",
                                          "reasoning": "xhigh"}}

    cp.persist(str(tmp_path), "s01", degraded)
    loaded = cp.load_closeout(str(tmp_path), "s01")
    assert loaded["degraded_from"] == {"requested": "fable", "ran": "opus",
                                       "reasoning": "xhigh"}
    # digest identical with/without the audit field → a degraded re-apply is not drift
    assert cp.closeout_digest(base) == cp.closeout_digest(degraded)
