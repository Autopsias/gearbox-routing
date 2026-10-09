#!/usr/bin/env python3
"""Regression checks for lint.py:
- an unknown task_class plus a model override is reported, not a crash;
- a same-model, lower-effort override on a peer-triggered session is `override-below-floor`
  (the escalate walk alone skips it when the effort is not a ladder rung);
- a v8 half override (model without reasoning, or the reverse) is `override-incomplete`;
- a Claude-family override walks the anthropic ladder even when the active provider is openai;
- a cross-provider override (its provider differs from the tree's) skips the floor check;
- a whitespace-only why_model is no reason.
Usage: lint-selftest.py   (run from anywhere)"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]  # parents[3] of this FOLDER is <repo> (codex -> plan-harden -> skills -> repo)


def lint(schema, session, tree="openai"):
    """tree = the tree's provider, set the way run.py reads it (PLAN_EXECUTE_ROUTING_PROVIDER);
    openai by default, matching main()'s Codex-only resolution."""
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CONFIG_DIR"}
    env["PLAN_EXECUTE_ROUTING_PROVIDER"] = tree
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "manifest.json").write_text(json.dumps({"plan_schema_version": schema,
                                                           "sessions": [{"id": "s01", **session}]}))
        r = subprocess.run([sys.executable, str(HERE / "lint.py"), d, str(ROOT / "model-routing.yaml"),
                            str(ROOT / "scripts")], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    return {f["issue"] for f in json.loads(r.stdout)["flags"]}


for schema, issue in ((8, "task-class-missing"), (7, "task-class-unknown")):
    got = lint(schema, {"task_class": "no_such_class", "model": "gpt-5.6-luna", "reasoning": "max", "why_model": "x"})
    assert issue in got, (schema, got)

# openai standard_build defaults to gpt-5.6-luna/max; luna has no `low` ladder rung.
low = {"task_class": "standard_build", "peer_triggers": ["x"], "model": "gpt-5.6-luna", "why_model": "x"}
assert "override-below-floor" in lint(8, {**low, "reasoning": "low"}), "same model, lower effort"
assert "override-below-floor" not in lint(8, {**low, "reasoning": "max"}), "equal to the default"

for half in ({"model": "gpt-5.6-luna"}, {"reasoning": "high"}):
    assert "override-incomplete" in lint(8, {"task_class": "standard_build", "why_model": "x", **half}), half
assert "override-incomplete" not in lint(8, {"task_class": "standard_build", "model": "gpt-5.6-luna",
                                             "reasoning": "max", "why_model": "x"})

# Contract section 4: a cross-provider override (a Codex model on an anthropic tree) skips the
# floor check; a same-provider one still gets it. anthropic standard_build defaults to sonnet/medium.
cross = {"task_class": "standard_build", "peer_triggers": ["x"], "model": "gpt-5.6-luna",
         "reasoning": "low", "why_model": "x"}
assert "override-below-floor" not in lint(8, cross, tree="anthropic"), "cross-provider skips the floor"
same = {**cross, "model": "sonnet"}
assert "override-below-floor" in lint(8, same, tree="anthropic"), "same provider, lower effort"
assert "override-without-reason" in lint(8, {**same, "why_model": "   "}, tree="anthropic"), "blank reason"

# The floor is a RANK, not an escalate() walk (by design): deep_reasoning
# defaults to opus/medium, the walk from sonnet/high enters opus at high and never reaches it,
# but sonnet/high still ranks lower on the anthropic ladder. opus/medium and opus/high pass.
deep = {"task_class": "deep_reasoning", "peer_triggers": ["x"], "model": "sonnet", "reasoning": "high",
        "why_model": "x"}
assert "override-below-floor" in lint(8, deep, tree="anthropic"), "sonnet/high under opus/medium"
for effort in ("medium", "high"):
    assert "override-below-floor" not in lint(8, {**deep, "model": "opus", "reasoning": effort},
                                              tree="anthropic"), effort

# A Claude model keeps its own (anthropic) ladder under an openai active provider;
# only the GLM tree moves it to zai (model-lint.md, the override's OWN provider).
sys.path[:0] = [str(HERE), str(ROOT / "skills" / "plan-execute" / "scripts")]
import provider_lane as pl  # noqa: E402
import lint as lint_mod  # noqa: E402
real = pl.effective_provider
try:
    for active, want in (("openai", "anthropic"), ("zai", "zai"), (None, "anthropic")):
        pl.effective_provider = lambda *a, _v=active, **k: _v
        assert lint_mod._provider("Opus") == want, (active, lint_mod._provider("Opus"))
finally:
    pl.effective_provider = real
print("lint-selftest: PASS")
