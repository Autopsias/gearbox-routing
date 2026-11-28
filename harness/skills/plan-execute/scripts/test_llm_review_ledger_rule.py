"""F-01: a finding recorded under a blocking rule stops blocking once the rule is removed."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import llm_review_ledger as L  # noqa: E402
from test_llm_review_ledger import _ctx  # noqa: E402


def _rule_tagged_prior(tmp_path, rule):
    ctx = _ctx(tmp_path)
    rec = {"kind": "finding", "fid": "f1", "attempt": 1, "severity": "medium", "status": "open",
           "file": "a.py", "line": 1, "summary": "m", "rule": "1"}
    with open(L.ledger_path(ctx.plan_dir, "s01"), "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"kind": "surface", "attempt": 1, "hashes": ctx.hashes}) + "\n")
        fh.write(json.dumps(rec) + "\n")
    return L.context(ctx.plan_dir, "s01", str(tmp_path), ["a.py"], None, rule)


def test_a_rule_tagged_prior_stops_blocking_when_the_rule_is_removed(tmp_path):
    assert _rule_tagged_prior(tmp_path, "").priors == []


def test_a_rule_tagged_prior_still_blocks_while_the_rule_is_live(tmp_path):
    assert [p["fid"] for p in _rule_tagged_prior(tmp_path, "1. a failure a test could show").priors] == ["f1"]
