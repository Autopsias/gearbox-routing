"""RT-02(a) — dispatch construction pins a model for every generic fan-out member.

The construction itself lives in SKILL.md prose: the orchestrating Claude session
IS the thing that builds each Task/`agent()` call at dispatch time — no Python
function does it, so this test checks the two spots in SKILL.md that govern it
(the N-Task path and the Workflow `agent()` path). Both must:
  - name the SSOT's `fanout_policy.default_model` key BY NAME (never a hardcoded
    'sonnet' the SSOT could drift out from under — RECON per s06.prompt.md), and
  - say the omission is illegal for a GENERIC agentType, while a `tier-*` agent
    or `"fork"` is explicitly exempt (each already governs its own model).

PLANT: before this session's fix, both spots said "omit `model` when
`model_arg` is null" with no exception for a generic agentType — the exact
46-of-196 unpinned fan-out measured 2026-08-21 (s06.context.md).

Run: pytest skills/plan-execute/scripts/test_dispatch_construction.py -q
"""
import re
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import run  # noqa: E402

SKILL_MD = SCRIPTS.parent / "SKILL.md"


def _ssot_text():
    return run._routing_ssot_path().read_text(encoding="utf-8")


def _line_starting(text, prefix):
    for line in text.splitlines():
        if line.strip().startswith(prefix):
            return line
    raise AssertionError(f"no line in SKILL.md starts with {prefix!r}")


def _line_containing(text, needle):
    for line in text.splitlines():
        if needle in line:
            return line
    raise AssertionError(f"no line in SKILL.md contains {needle!r}")


def test_ssot_actually_defines_fanout_policy_default_model():
    """Known-positive: the key the SKILL.md text below points at exists for
    real in the SSOT, so a passing text check isn't trusting a typo."""
    text = _ssot_text()
    assert re.search(r"^fanout_policy:\s*$", text, re.M), "fanout_policy: block not found"
    assert re.search(r"^\s+default_model:\s*\w+", text, re.M), "default_model: row not found"


def test_task_path_forbids_omitting_model_for_a_generic_member():
    skill = SKILL_MD.read_text(encoding="utf-8")
    line = _line_starting(skill, "- `model` =")
    assert "fanout_policy.default_model" in line
    assert "ILLEGAL" in line
    assert "GENERIC" in line
    assert "tier-*" in line and '"fork"' in line


def test_workflow_path_forbids_omitting_model_for_a_generic_agent_type():
    skill = SKILL_MD.read_text(encoding="utf-8")
    line = _line_containing(skill, "agent(prompt_text, {label:")
    assert "fanout_policy.default_model" in line
    assert "GENERIC" in line
    assert "tier-*" in line and '"fork"' in line


def test_task_path_never_overrides_an_explicit_pin():
    """ALLOW CONTROL — the substitution is scoped to null+generic only, not a
    blanket 'always resolve the SSOT default' that would clobber an author's
    explicit `model_arg` or a tier-* agent's own frontmatter."""
    skill = SKILL_MD.read_text(encoding="utf-8")
    line = _line_starting(skill, "- `model` =")
    assert "Never override an explicitly pinned" in line
