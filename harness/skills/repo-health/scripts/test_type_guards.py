"""One defect family: guarding the PARSE but not the parsed value's TYPE.

`json.loads` succeeding says nothing about the shape the next line assumes. `[]`
is valid JSON and has no `.get()`; a `cells` key can hold a string; a history
line truncated by an interrupted write parses as nothing at all; `exclude =
"web"` is valid TOML and `tuple()`s into three one-letter excludes that match
nothing. Four live instances were found in this collector — none firing at the time,
all reachable — and point-fixing the family elsewhere left a sibling path open
each time.

So the fix is one guard (`common.typed` / `typed_items` / `parse_json`) that
every reader of parsed data routes through, and this file feeds that guard a
known positive PER SHAPE: not an object, a wrong-typed member, a missing key, a
truncated line. Every test here was confirmed to fail against the pre-fix code —
with AttributeError, JSONDecodeError, KeyError and a silent ('w','e','b')
respectively, which is the point: three of the four crashed, and the fourth
quietly measured the wrong thing.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import common  # noqa: E402
import health  # noqa: E402
import health_render as render  # noqa: E402
import routes  # noqa: E402
import vendor  # noqa: E402
from test_health_shape import card, make_repo  # noqa: E402


# ---------- the guard itself ----------

def test_the_guard_returns_the_default_for_the_wrong_type():
    assert common.typed([], dict, {"a": 1}) == {"a": 1}
    assert common.typed({"a": 1}, dict, {}) == {"a": 1}
    assert common.typed_items([{"a": 1}, "x", None]) == [{"a": 1}]
    assert common.typed_items("not a list") == []
    assert common.typed_items(["a", 1], str) == ["a"]


def test_parse_json_guards_the_type_and_not_only_the_parse():
    assert common.parse_json('{"a": 1}') == {"a": 1}
    assert common.parse_json("[]") is None            # valid JSON, wrong shape
    assert common.parse_json('{"a": 1') is None       # truncated mid-write
    assert common.parse_json("null") is None
    assert common.parse_json("[1]", want=list) == [1]
    assert common.parse_json("[]", want=list, default="x") == []


# ---------- notebooks: valid JSON is not a notebook ----------

def test_a_notebook_that_is_valid_json_but_not_an_object_is_reported_not_fatal(tmp_path):
    """`[]` parses fine and has no .get() — AttributeError killed the whole collect."""
    repo = make_repo(tmp_path, "nb-list", {"nb.ipynb": "[]\n", "a.py": "x = 1\n"})
    health.collect(repo)
    c = next(c for c in card(repo)["checks"] if c["id"] == "hyg.notebook-outputs")
    assert c["status"] == "warn"
    assert "unreadable" in c["detail"]


@pytest.mark.parametrize("cells", [["not a cell", 3], "oops", None, {"a": 1}])
def test_a_notebook_whose_cells_are_not_cells_does_not_crash(tmp_path, cells):
    repo = make_repo(tmp_path, f"nb-{abs(hash(str(cells)))}", {
        "nb.ipynb": json.dumps({"cells": cells}), "a.py": "x = 1\n"})
    health.collect(repo)
    c = next(c for c in card(repo)["checks"] if c["id"] == "hyg.notebook-outputs")
    assert c["status"] == "pass", c["detail"]


# ---------- the scorecard: the one reader, so the one guard ----------

def test_a_scorecard_that_is_valid_json_but_not_an_object_is_refused_legibly(tmp_path):
    repo = make_repo(tmp_path, "card-list", {"a.py": "x = 1\n"})
    health.collect(repo)
    (health.health_dir(repo) / "scorecard.json").write_text("[]\n")
    with pytest.raises(ValueError, match="not a JSON object"):
        health.load_card(repo)
    health.collect(repo)                     # and a re-collect still works
    assert card(repo)["checks"]


def test_a_scorecard_whose_checks_hold_junk_does_not_crash_the_readers(tmp_path):
    """Wrong-typed member and missing key, in the shape every consumer reads."""
    repo = make_repo(tmp_path, "card-junk", {"a.py": "x = 1\n"})
    health.collect(repo)
    f = health.health_dir(repo) / "scorecard.json"
    c = json.loads(f.read_text())
    first = c["checks"][0]
    c["checks"] = [first, "not a check", None, 7, {"id": "x.y"}]
    f.write_text(json.dumps(c))
    got = health.load_card(repo)
    assert [x["id"] for x in got["checks"]] == [first["id"], "x.y"]
    # Unmeasured, never `pass`: a check with no status was never run, and this
    # collector's whole contract is that those two must not read the same.
    assert got["checks"][1]["status"] == "pending"
    render.render(repo)                      # every reader, over the repaired card
    rows = list(health.fix_rows(got, advisory=True))
    assert any(r["id"] == "x.y" for r in rows)


def test_fix_rows_survives_a_card_that_is_not_a_card():
    """A pure function anyone may hand a dict to keeps its own guard."""
    assert list(health.fix_rows([])) == []
    assert list(health.fix_rows({"checks": "nope"})) == []


# ---------- history.jsonl: append-only runtime output ----------

def test_a_truncated_history_line_does_not_crash_render(tmp_path):
    repo = make_repo(tmp_path, "hist-trunc", {"a.py": "x = 1\n"})
    health.collect(repo)
    render.render(repo)
    h = health.health_dir(repo) / "history.jsonl"
    h.write_text(h.read_text() + '{"ts": "2026-08-20", "commi')
    render.render(repo)
    page = (health.health_dir(repo) / "HEALTH.html").read_text()
    assert 'class="run' in page and "NEEDS ATTENTION" in page


def test_a_history_line_missing_its_keys_does_not_crash_render(tmp_path):
    repo = make_repo(tmp_path, "hist-keys", {"a.py": "x = 1\n"})
    health.collect(repo)
    render.render(repo)
    h = health.health_dir(repo) / "history.jsonl"
    h.write_text(h.read_text() + json.dumps({"score": 50}) + "\n"
                 + json.dumps({"ts": "2026-01-01", "layers": "not a dict"}) + "\n")
    render.render(repo)
    page = (health.health_dir(repo) / "HEALTH.html").read_text()
    assert 'class="run' in page and "01-01" in page


# ---------- the repo's own exclude list ----------

def test_a_string_exclude_list_is_not_read_one_character_at_a_time(tmp_path):
    """`tuple("web")` silently became ('w', 'e', 'b') — excluding nothing."""
    repo = make_repo(tmp_path, "excl-str", {
        "pyproject.toml": '[tool.claude-quality]\nexclude = "web"\n', "a.py": "x = 1\n"})
    assert common.quality_excludes(repo) == ()


def test_a_wrong_typed_exclude_entry_is_dropped_not_stringified(tmp_path):
    repo = make_repo(tmp_path, "excl-mixed", {
        "pyproject.toml": '[tool.claude-quality]\nexclude = ["web", 3]\n',
        "a.py": "x = 1\n"})
    assert common.quality_excludes(repo) == ("web",)


# ---------- the vendor manifest ----------

# These two used to assert `load() == []` and a 0 exit — they PINNED the green in
# place. Review caught it: an unreadable manifest read as "vendors
# nothing", `check()` exited 0, and SKILL.md maps that to sec.vendor-pins=pass, so
# a tampered vendored SKILL.md passed the gate that exists to catch it. Absent and
# unreadable are different answers; only absence OVER AN EMPTY vendor/ is clean —
# the second review round (same day) found the sibling that fix left open: an
# EMPTY manifest hashed nothing either, and said so with exit 0.

def vendor_at(monkeypatch, tmp_path, manifest, trees=()):
    """Point vendor.py at a throwaway install: one manifest, N vendored trees.

    Both halves, always. Pinning only MANIFEST and letting VENDOR_DIR fall
    through to the real one is how these tests would pass for the wrong reason —
    and, now that load() compares the two, is the whole thing under test.
    """
    (tmp_path / "vendor").mkdir(exist_ok=True)
    for t in trees:
        (tmp_path / "vendor" / t).mkdir(exist_ok=True)
    monkeypatch.setattr(vendor, "MANIFEST", tmp_path / manifest)
    monkeypatch.setattr(vendor, "VENDOR_DIR", tmp_path / "vendor")
    return tmp_path / manifest


def test_a_vendor_manifest_that_is_not_a_list_REFUSES(tmp_path, monkeypatch):
    """`{"skills": {}}` iterates its KEYS — every entry arrived as a string."""
    vendor_at(monkeypatch, tmp_path, "vendor.json").write_text('{"skills": {}}\n')
    with pytest.raises(vendor.UnusableManifest, match="not a JSON array"):
        vendor.load()


def test_a_vendor_entry_missing_its_keys_REFUSES_rather_than_being_skipped(tmp_path, monkeypatch):
    """A dropped entry is a vendored tree nobody hash-checks."""
    m = vendor_at(monkeypatch, tmp_path, "vendor.json")
    m.write_text(json.dumps([{"name": "x"}, "nope"]) + "\n")
    with pytest.raises(vendor.UnusableManifest, match="not objects"):
        vendor.load()


@pytest.mark.parametrize("body", ['{"skills": {}}', '[{"name": "x"}, "nope"]', '"nope"'])
@pytest.mark.parametrize("cmd", ["verify", "check"])
def test_an_unreadable_manifest_exits_2_never_0(tmp_path, monkeypatch, capsys, body, cmd):
    """2 is "we could not look" — neither clean (0) nor a finding (1)."""
    vendor_at(monkeypatch, tmp_path, "vendor.json").write_text(body + "\n")
    assert vendor.main([cmd]) == 2
    assert "REFUSED" in capsys.readouterr().out


# ---------- the population is the DISK, not the manifest ----------

# The empty-input half of the same family, all three shapes of it. Every one of
# these once exited 0 — "no vendored skills", over trees sitting
# right there — which SKILL.md maps to `sec.vendor-pins = pass`.

@pytest.mark.parametrize("body,why", [
    ("[]", "an EMPTY manifest hashes nothing at all"),
    (json.dumps([{"name": "a", "repo": "o/r", "path": "p", "pin": "c", "tree": "t"}]),
     "a manifest covering SOME of the trees leaves the rest unhashed"),
])
def test_a_manifest_that_does_not_cover_the_vendored_trees_REFUSES(
        tmp_path, monkeypatch, capsys, body, why):
    vendor_at(monkeypatch, tmp_path, "vendor.json", ("a", "b")).write_text(body)
    with pytest.raises(vendor.UnusableManifest, match="no pin in"):
        vendor.load()
    assert vendor.main(["verify"]) == 2, why
    assert "REFUSED" in capsys.readouterr().out


def test_an_ABSENT_manifest_over_vendored_trees_REFUSES(tmp_path, monkeypatch):
    """Absent is not clean either when there is something on disk to hash."""
    vendor_at(monkeypatch, tmp_path, "nope.json", ("a",))
    with pytest.raises(vendor.UnusableManifest, match="no pin in"):
        vendor.load()


def test_a_manifest_that_will_not_OPEN_exits_2_not_1(tmp_path, monkeypatch, capsys):
    """A read that raises is "we could not look", not "we looked and found one"."""
    m = vendor_at(monkeypatch, tmp_path, "vendor.json")
    m.mkdir()                        # a directory where a file is expected
    assert vendor.main(["verify"]) == 2
    assert "REFUSED" in capsys.readouterr().out


def test_an_upstream_nobody_could_REACH_exits_2_never_0(tmp_path, monkeypatch, capsys):
    """`check()` dropped every unreachable entry and returned 0 — and SKILL.md
    maps exit 0 to `sec.vendor-pins = pass`, so an unauthenticated `gh` recorded
    "vendored skills at reviewed pins" with zero trees compared upstream."""
    monkeypatch.setattr(vendor, "gh_json", lambda path: None)   # gh broken/offline
    entries = [{"name": "a", "repo": "o/r", "path": "p", "pin": "c" * 40,
                "tree": "t" * 40}]
    assert vendor.check(entries) == 2
    out = capsys.readouterr().out
    assert "could not reach upstream" in out and "REFUSED" in out


def test_a_reachable_upstream_that_MATCHES_is_still_a_clean_0(monkeypatch):
    """The known negative: the guard must not turn every check run into a refusal."""
    monkeypatch.setattr(vendor, "upstream_tree", lambda e: e["tree"])
    assert vendor.check([{"name": "a", "repo": "o/r", "path": "p",
                          "pin": "c" * 40, "tree": "t" * 40}]) == 0


def test_a_MISSING_gh_binary_is_not_a_tampering_accusation(monkeypatch):
    """FileNotFoundError escaped subprocess.run and killed the process with exit
    1 — which SKILL.md reads as "a vendored file was edited"."""
    def no_such_binary(*a, **k):
        raise FileNotFoundError(2, "No such file or directory: 'gh'")
    monkeypatch.setattr(vendor.subprocess, "run", no_such_binary)
    assert vendor.gh_json("repos/o/r/contents/p") is None


def test_an_ABSENT_manifest_over_an_EMPTY_vendor_dir_is_still_clean(tmp_path, monkeypatch):
    """The one case that legitimately means "vendors nothing": nothing is there."""
    vendor_at(monkeypatch, tmp_path, "nope.json")
    assert vendor.load() == []
    assert vendor.main(["verify"]) == 0


# ---------- fix_rows: the comment promised a guard the code did not have ----------

def test_fix_rows_tolerates_a_check_dict_with_no_id():
    """routes.py:482 claimed fix_rows "repeats the guard ... anyone may hand a
    dict to", then read `c["id"]` twice unguarded. Found by review:
    a written claim about behaviour, contradicted three lines below itself.
    """
    card = {"checks": [{"tier": "blocking", "status": "fail", "title": "no id here"}]}
    rows = list(routes.fix_rows(card))
    assert len(rows) == 1
    assert rows[0]["id"] is None
    assert rows[0]["routed"] is False, "an id-less check cannot claim a fix route"
    assert rows[0]["dispatch"] == "operator", "it falls to a human, and says so"
