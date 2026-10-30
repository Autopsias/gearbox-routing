"""`gate-indeterminate` when the reviewer read only PART of the land surface.

REGRESSION — review-gate-ground-truth-2026-10-17 ran `land` nine
times. In seven, llm-review-medium attested `REVIEWED_FILES:` 5-15 of 61 (the
surface held ~30 stored defect-sample diffs) and exited undecided. The brief said
"Re-run `run.py land`" every time; each run cost 20-45 minutes and gave the same
answer, because the surface had not changed. The brief must name the two counts
and the levers that shrink the surface instead.

Drives the REAL `llm_review_gate.main` as the land's argv gate; only the
reviewer call is stubbed, so the counts are the gate's own, not retyped here.

    pytest skills/plan-execute/scripts/test_land_short_review.py -q
"""

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

from _land_fixture import build_repo, isolate, run_cli, work  # noqa: E402
from worktree import git  # noqa: E402

GATE = "marker-gate"     # the gate the fixture plan declares
FILES = 20

_SRC = """
import sys
sys.path.insert(0, {scripts!r})
import llm_review_gate as g
g.run_once = lambda prompt, cwd, timeout, **k: ({{"result": {result!r}}}, "")
sys.exit(g.main(["--level", "low", "--cwd", ".", "--session", "land"]))
"""


@pytest.fixture
def fx(tmp_path):
    return build_repo(tmp_path)


def _land(fx, result):
    (fx["root"] / ".claude" / "eval-gates.json").write_text(json.dumps(
        {GATE: {"kind": "argv", "cwd": ".", "timeout": 120, "indeterminate_exit": 2,
                "env_allowlist": ["PLAN_EXECUTE_REVIEW_BASE", "PLAN_EXECUTE_REVIEW_EXCLUDE"],
                "argv": [sys.executable, "-c", _SRC.format(scripts=str(SCRIPTS), result=result)]}}))
    iso = isolate(fx, "plan-a")
    for i in range(FILES):
        (iso["tree"] / f"src/f{i}.py").parent.mkdir(exist_ok=True)
        (iso["tree"] / f"src/f{i}.py").write_text(f"F = {i}\n")
    git(["add", "src"], iso["tree"], check=True)
    work(iso["tree"], "src/last.py", "L = 1\n")
    rc, out = run_cli("land", iso["plan_dir"])
    assert (rc, out.get("kind")) == (1, "gate-indeterminate"), out
    return out


def test_a_short_review_names_both_counts_and_does_not_say_just_rerun(fx):
    out = _land(fx, "REVIEWED_FILES: 7\n```json\n[]\n```")
    brief = out["brief"]
    assert "Re-run `run.py land" not in brief, brief
    claimed, handed = out["short_review"][GATE]
    assert claimed == 7 and handed > FILES, out          # the gate's own count
    assert f"reviewed 7 of {handed} files" in brief, brief
    assert ".review-exclude" in brief and "PLAN_EXECUTE_INTENT_FILE" in brief, brief


def test_a_reviewer_that_never_answers_keeps_the_rerun_advice(fx):
    """The known negative: no attested count is a no-answer, and re-running can
    clear a no-answer."""
    brief = _land(fx, "I looked and it seems fine.")["brief"]
    assert "Re-run `run.py land" in brief, brief
    assert "reviewed " not in brief.split("---")[0], brief
