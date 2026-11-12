"""testvalue.py reports what it claims, on inputs whose answer is known."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import testvalue  # noqa: E402


def test_analyse_separates_owned_duplicate_and_jointly_covered():
    report = testvalue.analyse({
        "t.owner": {("a.py", 1), ("a.py", 2), ("a.py", 3)},   # owns line 3
        "t.duplicate": {("a.py", 1), ("a.py", 2)},            # inside t.owner
        "t.other": {("b.py", 8), ("b.py", 9)},                # owns b.py:8
        "t.joint": {("a.py", 1), ("b.py", 9)},                # covered only by two tests together
        "t.subprocess": set(),                                # no lines: not a lead
    })
    assert report["tests_traced"] == 4
    assert report["source_lines"] == 5
    assert report["single_owner_lines"] == 2
    assert {z["test"]: z["subsumed_by"] for z in report["zero_unique"]} == {
        "t.duplicate": "t.owner", "t.joint": None}


def test_static_signals_count_tests_mocks_and_private_reach(tmp_path):
    (tmp_path / "test_x.py").write_text(
        "from unittest.mock import patch\n"
        "import mod\n"
        "class T:\n"
        "    def test_a(self):\n"
        "        self._helper()\n"            # own helper: not private reach
        "        mod.__name__\n"              # dunder: not private reach
        "        assert mod._internal() == 1\n"
        "async def test_b(monkeypatch):\n"
        "    pass\n")
    (tmp_path / "helper.py").write_text("def test_not_a_test_file(): pass\n")
    [row] = testvalue.static_signals(tmp_path)
    assert (row["file"], row["tests"], row["mock_lines"], row["private_lines"]) == ("test_x.py", 2, 2, 1)
