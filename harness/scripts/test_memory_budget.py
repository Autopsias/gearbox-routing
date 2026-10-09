"""memory_budget.py: counts notes, flags the two drift kinds, never writes."""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import memory_budget  # noqa: E402


def test_report_counts_and_drift(tmp_path):
    mem = tmp_path / "proj" / "memory"
    mem.mkdir(parents=True)
    (mem / "MEMORY.md").write_text("- [a](a.md) — x\n- [gone](gone.md) — y\n")
    (mem / "a.md").write_text("A" * 400)
    (mem / "orphan.md").write_text("O" * 4000)
    old = time.time() - 200 * 86400
    os.utime(mem / "a.md", (old, old))
    r = memory_budget.report_project(mem, 90)
    assert r["notes"] == 2 and r["old"] == 1 and r["index_bytes"] > 0
    assert [p.name for p in r["unreachable"]] == ["orphan.md"]
    assert r["dangling"] == ["gone.md"]
    assert r["biggest"][0][0].name == "orphan.md"
    text = memory_budget.render([r], 90)
    assert "proj" in text and "gone.md" in text
    assert sorted(p.name for p in mem.iterdir()) == ["MEMORY.md", "a.md", "orphan.md"]


def test_long_index_lines_counted_report_only(tmp_path):
    mem = tmp_path / "proj2" / "memory"
    mem.mkdir(parents=True)
    short_line = "- [a](a.md) — x\n"
    long_line = "- [b](b.md) — " + ("y" * 200) + "\n"  # well over 200 chars total
    (mem / "MEMORY.md").write_text(short_line + long_line)
    (mem / "a.md").write_text("A")
    (mem / "b.md").write_text("B")
    before = (mem / "MEMORY.md").read_text()

    r = memory_budget.report_project(mem, 90)

    assert r["long_lines"] == 1
    assert r["longest_line"] == len(long_line.rstrip("\n"))
    # report-only: nothing in report_project touches the index file
    assert (mem / "MEMORY.md").read_text() == before

    text = memory_budget.render([r], 90)
    assert ">200c" in text and "longest" in text
