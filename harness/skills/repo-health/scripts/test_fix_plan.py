"""The fix plan can be reached after the report, not only from the flag.

A user who forgot `--plan` at the start reaches it two ways: the decision card
on the dashboard names the command, and references/fix-plan.md holds the
procedure. These tests fail when either way goes missing.
"""
import re

from health_render import decision_html
from test_review_goals import SKILL_MD, anchors

FIX_PLAN_MD = SKILL_MD.parent / "references" / "fix-plan.md"
ROW = {"id": "cq.lint", "title": "Lint", "detail": "3 errors"}


def test_the_dashboard_card_offers_the_plan_whenever_there_is_work():
    assert "/repo-health --plan" in decision_html([ROW], [], [], [])   # blocking
    assert "/repo-health --plan" in decision_html([], [ROW], [], [])   # advisory
    assert "/repo-health --plan" in decision_html([], [], [ROW], [])   # warn only


def test_an_all_green_card_offers_no_plan():
    # Known negative: a card that always offered the plan would pass the test above.
    assert "--plan" not in decision_html([], [], [], [])


def test_every_card_keeps_three_options_at_most():
    for args in (([ROW], [], [], []), ([], [ROW], [], []), ([], [], [], [])):
        assert len(re.findall(r"<li>", decision_html(*args))) <= 3


def test_skill_md_points_at_the_fix_plan_procedure():
    assert "--plan" in anchors(SKILL_MD)
    assert "](references/fix-plan.md)" in SKILL_MD.read_text()


def test_fix_plan_md_links_into_skill_md_resolve():
    back = re.findall(r"\]\(\.\./SKILL\.md#([^)]+)\)", FIX_PLAN_MD.read_text())
    assert back, "fix-plan.md lost its links, so this test checks nothing"
    assert not set(back) - anchors(SKILL_MD), set(back) - anchors(SKILL_MD)


def test_plan_builder_sends_a_direct_request_to_the_fix_plan():
    # The third entry: a user who asks plan-builder, not repo-health.
    skills = SKILL_MD.parents[1]
    for path in (skills / "plan-builder" / "SKILL.md",):  # the Codex port is not exported
        assert "repo-health/references/fix-plan.md" in path.read_text(), path
