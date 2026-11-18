"""classifier_report counts a refusal once, pairs it with its command, and matches the digest."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import classifier_report as cr  # noqa: E402
import digest_sessions as ds  # noqa: E402
from test_digest_sessions import _desktop_session  # noqa: E402


def test_refusal_stored_twice_counts_once_with_command(tmp_path):
    p = _desktop_session(tmp_path / "projects" / "-Users-me-repo" / "abc.jsonl")
    lines = [json.loads(x) for x in p.read_text().splitlines()]
    lines[-1]["toolUseResult"] = lines[-1]["message"]["content"][0]["content"]  # the second copy
    p.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    rows = cr.collect([p])
    assert len(rows) == 1
    assert rows[0][:3] == ("Merge Without Review", "Bash", "gh pr merge 1")
    assert len(rows) == sum(ds.digest_session(p, 5)["classifier_denials"].values())


def test_render_has_totals_and_command(tmp_path):
    p = _desktop_session(tmp_path / "-Users-me-repo" / "abc.jsonl")
    md = cr.render(cr.collect([p]), 1, 60)
    assert "| Merge Without Review | 1 | 1 |" in md and "`gh pr merge 1`" in md
