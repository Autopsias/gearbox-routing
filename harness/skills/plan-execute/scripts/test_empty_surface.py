"""(f) A session that SHIPPED THROUGH MERGED PRs (kuzushi-belt-lock s02,
2026-10-14), driven through the registry gate the way `verify.py` does. Its work
is already committed, so the surface is empty: that blocks AT ONCE naming
`review_base` -- never three "transport" retries -- and with `review_base` in the
closeout the same tree gets a real review. Split out of test_verifier_wiring.py
for its size bound; the fixtures are that file's.
"""
# ruff: noqa: F811  (shim and deployed_home are fixtures imported from test_verifier_wiring)
import json
import subprocess

import review_context as rvs

from test_verifier_wiring import (  # noqa: F401 — shim/deployed_home are fixtures
    SID, _fixture_tree, _git, _reviews, _run_the_registry_gate, _state, deployed_home, shim)


def _pr_shipped(tmp_path):
    plan_dir = _fixture_tree(tmp_path, restricted=False)
    root = plan_dir.parent.parent
    base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True,
                          capture_output=True, text=True).stdout.strip()
    _git(root, "commit", "-qam", "the session's merged PR")
    return plan_dir, base


def test_an_empty_surface_blocks_once_and_names_review_base(tmp_path, shim, deployed_home):
    plan_dir, _ = _pr_shipped(tmp_path)
    out = _run_the_registry_gate(plan_dir)

    assert _reviews(shim) == [], "nothing to review: no reviewer may be spawned"
    assert out["action"] == "halted" and out.get("empty_surface") is True, out
    assert "transport" not in out, out
    assert "review_base" in out["reason"] and "could not RUN" not in out["reason"]
    assert _state(plan_dir)["rework_count"] == 0
    assert _state(plan_dir).get("transport_indeterminate", {}) == {}, "not on the transport budget"


def test_review_base_in_the_closeout_gives_a_pr_shipped_session_a_real_review(
        tmp_path, shim, deployed_home):
    plan_dir, base = _pr_shipped(tmp_path)
    rvs.record_base(str(plan_dir), SID, base)   # what the runner caches at dispatch
    co = plan_dir / "_closeouts" / f"{SID}.json"
    rec = json.loads(co.read_text())
    digest = rec["_closeout_digest"]
    rec["review_base"] = base
    co.write_text(json.dumps(rec))
    out = _run_the_registry_gate(plan_dir)

    assert len(_reviews(shim)) == 1, _reviews(shim)
    assert out["action"] == "passed", out
    assert json.loads(co.read_text())["_closeout_digest"] == digest
    events = [json.loads(ln) for ln in (plan_dir / "run.ndjson").read_text().splitlines()]
    assert any(e.get("event") == "review_base_declared" and e.get("resolved") == base
               for e in events), "the accepted base is logged for the operator"
