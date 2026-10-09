"""The front door's goal table points at procedures that exist.

SKILL.md § "Pick what to review" maps each goal to its procedure by a link, and
references/reviews.md links back into SKILL.md. A renamed heading breaks such a
link silently: the run follows a goal to a section that is not there. These
tests resolve every one of those links.
"""
import re
from pathlib import Path

SKILL_DIR = Path(__file__).parents[1]
SKILL_MD = SKILL_DIR / "SKILL.md"
REVIEWS_MD = SKILL_DIR / "references" / "reviews.md"


def slug(heading):
    """GitHub's heading anchor: lowercase, punctuation dropped, spaces to hyphens."""
    return re.sub(r"[^\w\- ]", "", heading.strip().lower()).replace(" ", "-")


def anchors(path):
    return {slug(h) for h in re.findall(r"^#+ (.+)$", path.read_text(), re.M)}


def goal_rows():
    text = SKILL_MD.read_text()
    section = text.split("\n## Pick what to review\n", 1)[1].split("\n## ", 1)[0]
    return re.findall(r"^\| `([a-z-]+)` \|(.*)$", section, re.M)


def test_the_goal_table_is_parsed():
    # Known positive for the next test: an empty parse would pass it on nothing.
    goals = {g for g, _ in goal_rows()}
    assert {"health", "security", "refactoring", "over-engineering",
            "performance", "all"} <= goals, goals


def test_every_goal_links_to_a_heading_that_exists():
    own, reviews = anchors(SKILL_MD), anchors(REVIEWS_MD)
    for goal, row in goal_rows():
        for target, anchor in re.findall(r"\]\(([^)#]*)#([^)]+)\)", row):
            pool = reviews if target.endswith("reviews.md") else own
            assert anchor in pool, f"{goal}: no heading #{anchor} in {target or 'SKILL.md'}"


def test_reviews_md_links_resolve():
    text = REVIEWS_MD.read_text()
    back = re.findall(r"\]\(\.\./SKILL\.md#([^)]+)\)", text)
    inside = re.findall(r"\]\(#([^)]+)\)", text)
    assert back and inside, "reviews.md lost its links, so this test checks nothing"
    assert not set(back) - anchors(SKILL_MD), set(back) - anchors(SKILL_MD)
    assert not set(inside) - anchors(REVIEWS_MD), set(inside) - anchors(REVIEWS_MD)
