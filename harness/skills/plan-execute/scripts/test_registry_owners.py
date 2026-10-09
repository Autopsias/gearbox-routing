"""REG-01 — branch/worktree OWNERSHIP classification (`registry_owners.py`),
plus the end-to-end `collect()`/`render_table()` smoke test and the
`plans-status` CLI subprocess tests that exercise the whole assembled
registry.

Split out of test_registry.py (was 871 LOC, over the test file-size limit)
along the same seam as registry.py's own split — see that file's docstring
for the git-bookkeeping and plan-lifecycle coverage this file does NOT own.
Imports its fixture helpers (`_make_plan`, `_todo_session`, `_write_lock`,
`_dead_pid`, `RUN_PY`) from `test_registry.py`, the same cross-file pattern
`test_registry.py` already uses for `test_worktree.py`'s `_repo`/`_manifest`.

Run: pytest skills/plan-execute/scripts/test_registry_owners.py -q
"""

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import article_block as ab  # noqa: E402
import registry as reg  # noqa: E402
import ship_state_io as ssio  # noqa: E402
import worktree as wt  # noqa: E402
from test_registry import RUN_PY  # noqa: E402
from test_registry import _dead_pid  # noqa: E402
from test_registry import _make_plan  # noqa: E402
from test_registry import _todo_session  # noqa: E402
from test_registry import _write_lock  # noqa: E402
from test_worktree import _manifest as _wt_manifest  # noqa: E402
from test_worktree import _repo as _wt_repo  # noqa: E402


# --------------------------------------------------------------------------
# branch ownership — THREE classes (ADR-0002), never guessed from name shape
# --------------------------------------------------------------------------
def _group_state(plan_dir, group, root, monkeypatch):
    monkeypatch.setenv(wt.STAGGER_MS_ENV, "0")
    paths = wt.prepare_members(plan_dir, _wt_manifest(), ["s01", "s02"], project_root=root)
    return paths


def test_legacy_group_member_branch_active_never_leftover_when_owner_plan_locked(
    tmp_path, monkeypatch
):
    """(e) — a LEGACY plan/<group>/<sid> branch whose owning plan holds a live
    lock must classify ACTIVE, never LEFTOVER."""
    root = _wt_repo(tmp_path)
    plan_dir = root / "_plans" / "owner-plan"
    plan_dir.mkdir(parents=True)
    _group_state(plan_dir, "g", root, monkeypatch)

    owners = reg.branch_owners(root / "_plans")
    branch = wt.member_branch("g", "s01")
    assert branch in owners
    assert [o["plan"] for o in owners[branch]] == ["owner-plan"]

    rows = {r["branch"]: r for r in reg.classify_branches(
        [branch], owners, {"owner-plan": "active"}
    )}
    assert rows[branch]["class"] == "active"


def test_group_member_branch_leftover_when_owner_plan_stale(tmp_path, monkeypatch):
    root = _wt_repo(tmp_path)
    plan_dir = root / "_plans" / "owner-plan"
    plan_dir.mkdir(parents=True)
    _group_state(plan_dir, "g", root, monkeypatch)
    owners = reg.branch_owners(root / "_plans")
    branch = wt.member_branch("g", "s01")

    rows = {r["branch"]: r for r in reg.classify_branches(
        [branch], owners, {"owner-plan": "stale-lock"}
    )}
    assert rows[branch]["class"] == "leftover"


def test_cross_plan_branch_collision_reports_conflict_never_leftover(tmp_path):
    """Defect 1 (HIGH), granted-attempt fix — VERIFIED live in this repo, not
    hypothetical: `pg-prep` is declared as a `parallel_group` in BOTH
    `_plans/example-plan-a-2026-07-27` and `_plans/example-plan-b-2026-07-27`,
    which both produce the SAME `plan/pg-prep/<sid>` branch name via
    `worktree.member_branch`. `branch_owners` used to key ownership by branch
    name alone and merge with `owners.update(partial)`, so the LAST plan
    scanned silently won. If the loser was LIVE and the winner was DONE,
    `classify_branches` had a definitive "done" determination and reported
    the LIVE plan's branch LEFTOVER — safe to reap — exactly the false
    positive ADR-0002 (docs/adr/0002-...) exists to prevent. This test fails
    without the fix: it plants that exact shape (one live plan, one done
    plan, same branch name) and asserts the branch is never LEFTOVER and is
    instead reported as a CONFLICT naming both claimants."""
    root = _wt_repo(tmp_path)
    branch = wt.member_branch("pg-prep", "s01")

    live_plan_dir = _make_plan(root, "live-plan", _todo_session())
    done_plan_dir = _make_plan(root, "done-plan", _todo_session())
    ab.apply_mutation(done_plan_dir / "PLAN.html", "s01", status="DONE")

    for plan_dir, name in ((live_plan_dir, "live-plan"), (done_plan_dir, "done-plan")):
        state_path = plan_dir / "_worktrees" / "pg-prep.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        ssio.durable_write_json(state_path, {
            "group": "pg-prep", "repo_root": str(root),
            "members": {"s01": {"path": str(tmp_path / f"wt-{name}"), "branch": branch}},
        })

    owners = reg.branch_owners(root / "_plans")
    assert branch in owners
    assert {o["plan"] for o in owners[branch]} == {"live-plan", "done-plan"}

    plan_lifecycle_by_name = {"live-plan": "active", "done-plan": "done"}
    rows = {r["branch"]: r for r in reg.classify_branches(
        [branch], owners, plan_lifecycle_by_name
    )}
    assert rows[branch]["class"] == "conflict"
    assert rows[branch]["class"] != "leftover"  # the live plan's branch is never reapable
    assert {o["plan"] for o in rows[branch]["owners"]} == {"live-plan", "done-plan"}


def test_single_owner_branch_classifies_exactly_as_before_no_regression(tmp_path, monkeypatch):
    """(plan, branch) keying must not change behavior for the common case —
    exactly one plan claims a branch — covering both ACTIVE and LEFTOVER."""
    root = _wt_repo(tmp_path)
    plan_dir = root / "_plans" / "owner-plan"
    plan_dir.mkdir(parents=True)
    _group_state(plan_dir, "g", root, monkeypatch)

    owners = reg.branch_owners(root / "_plans")
    branch = wt.member_branch("g", "s01")
    assert branch in owners
    assert len(owners[branch]) == 1
    assert owners[branch][0]["plan"] == "owner-plan"

    active_rows = {r["branch"]: r for r in reg.classify_branches(
        [branch], owners, {"owner-plan": "active"}
    )}
    assert active_rows[branch]["class"] == "active"
    assert active_rows[branch]["owner"]["plan"] == "owner-plan"

    leftover_rows = {r["branch"]: r for r in reg.classify_branches(
        [branch], owners, {"owner-plan": "done"}
    )}
    assert leftover_rows[branch]["class"] == "leftover"
    assert leftover_rows[branch]["owner"]["plan"] == "owner-plan"


def test_orphan_branch_with_no_worktree_is_unknown(tmp_path):
    """(c) — a bare `plan/*` branch (never checked out, never recorded in any
    `_worktrees/<group>.json`) is a QUESTION, not garbage."""
    root = _wt_repo(tmp_path)
    wt.git(["branch", "plan/nobody/s99"], root, check=True)
    branches = reg.list_plan_branches(root)
    assert "plan/nobody/s99" in branches
    rows = {r["branch"]: r for r in reg.classify_branches(branches, {}, {})}
    assert rows["plan/nobody/s99"]["class"] == "unknown"


def test_branch_leftover_only_on_definitive_stale_never_on_undeterminable_lifecycle(
    tmp_path, monkeypatch
):
    """Rework fix (HIGH, gate llm-review-low attempt 1): a plan whose lifecycle
    cannot be determined — manifest.json missing/corrupt — must NOT make its
    LIVE `plan/*` branches report LEFTOVER (i.e. "safe to reap"). Before the
    fix, classify_branches treated anything other than exactly "active" as
    leftover, so lifecycle None/"unknown" fell straight through to LEFTOVER —
    disagreeing with classify_worktrees, which already treats an
    undeterminable lifecycle as NOT stale. This test fails without the fix."""
    root = _wt_repo(tmp_path)
    plan_dir = root / "_plans" / "owner-plan"
    plan_dir.mkdir(parents=True)
    _group_state(plan_dir, "g", root, monkeypatch)
    # No manifest.json written at all — this plan's lifecycle is undeterminable.
    assert not (plan_dir / "manifest.json").exists()

    owners = reg.branch_owners(root / "_plans")
    branch = wt.member_branch("g", "s01")
    assert branch in owners

    # Both the "missing" (key absent) and the explicit "unknown" shapes
    # classify_plan can produce must be covered.
    for lifecycle_map in ({}, {"owner-plan": "unknown"}, {"owner-plan": None}):
        rows = {r["branch"]: r for r in reg.classify_branches([branch], owners, lifecycle_map)}
        assert rows[branch]["class"] != "leftover", lifecycle_map


def test_branch_with_a_live_worktree_but_no_state_record_is_unknown(tmp_path):
    """(f) — even a branch WITH a real, currently-checked-out worktree is
    UNKNOWN (never LEFTOVER, never guessed ACTIVE) when no `_worktrees/*.json`
    anywhere claims it."""
    root = _wt_repo(tmp_path)
    wt.git(["branch", "plan/mystery/s01"], root, check=True)
    path = tmp_path / "mystery-wt"
    wt.git(["worktree", "add", "-q", str(path), "plan/mystery/s01"], root, check=True)

    (root / "_plans").mkdir(exist_ok=True)
    owners = reg.branch_owners(root / "_plans")  # nothing recorded anywhere
    rows = {r["branch"]: r for r in reg.classify_branches(
        ["plan/mystery/s01"], owners, {}
    )}
    assert rows["plan/mystery/s01"]["class"] == "unknown"


def test_branch_owners_degrades_corrupt_group_state_with_no_bak(tmp_path):
    """Rework fix (gate llm-review-low, attempt 2) — the reported bug:
    `registry.py:265` called `ssio.read_json_with_bak()` for a
    `_worktrees/<group>.json` and let `ShipStateError` propagate straight out
    when the file was corrupt and had no usable `.bak`. Nothing between that
    call and the `plans-status` CLI caught it, so one truncated group-state
    file aborted the whole read-only registry with a traceback. This test
    fails without the fix: `branch_owners` must record the failure in
    `errors` and keep going, exactly like `lock_info` already degrades a
    corrupt `.lock` file."""
    root = _wt_repo(tmp_path)
    plan_dir = root / "_plans" / "broken-plan"
    state_path = plan_dir / "_worktrees" / "g.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text("{not valid json at all")  # corrupt, no .bak

    errors = {}
    owners = reg.branch_owners(root / "_plans", errors)
    assert owners == {}  # nothing crashed, nothing was silently invented
    assert "broken-plan" in errors
    assert "g.json" in errors["broken-plan"][0]


def test_branch_owners_degrades_group_state_that_is_not_a_json_object(tmp_path):
    """Second malformed-input path the sweep found: a `_worktrees/<group>.json`
    that parses (valid JSON) but is the wrong shape — a JSON list, not an
    object — used to raise a raw AttributeError from `state.get(...)`."""
    root = _wt_repo(tmp_path)
    plan_dir = root / "_plans" / "broken-plan"
    state_path = plan_dir / "_worktrees" / "g.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text("[]")  # valid JSON, wrong shape

    errors = {}
    owners = reg.branch_owners(root / "_plans", errors)
    assert owners == {}
    assert "broken-plan" in errors


def test_branch_owners_degrades_when_members_field_is_wrong_type(tmp_path):
    """Field-level known positive (coordinator refinement): the group-state
    file itself is a valid JSON object — `state` passes the whole-value
    `isinstance(state, dict)` check the previous fix added — but its
    `members` FIELD is a list, not an object. `_owners_from_state_file`
    already guards this with `isinstance(members, dict)`; this test proves
    it degrades to no owners for that group rather than raising when
    `members.items()` is called on a list."""
    root = _wt_repo(tmp_path)
    plan_dir = root / "_plans" / "owner-plan"
    state_path = plan_dir / "_worktrees" / "g.json"
    state_path.parent.mkdir(parents=True)
    ssio.durable_write_json(state_path, {"group": "g", "members": ["not", "a", "dict"]})

    owners = reg.branch_owners(root / "_plans")
    assert owners == {}  # no crash, nothing invented


def test_branch_owners_skips_a_member_entry_that_is_not_a_dict(tmp_path):
    """Field-level known positive: one MEMBER value inside an otherwise valid
    `members` object is a bare string instead of an object (e.g. `"s01":
    "corrupt"`). `_owners_from_state_file` already guards each entry with
    `isinstance(entry, dict)`; this proves the malformed member is skipped
    while a healthy sibling member in the same file is still recorded."""
    root = _wt_repo(tmp_path)
    plan_dir = root / "_plans" / "owner-plan"
    state_path = plan_dir / "_worktrees" / "g.json"
    state_path.parent.mkdir(parents=True)
    good_branch = wt.member_branch("g", "s02")
    ssio.durable_write_json(state_path, {
        "group": "g",
        "members": {
            "s01": "corrupt-not-a-dict",
            "s02": {"path": str(tmp_path / "wt-s02"), "branch": good_branch},
        },
    })

    owners = reg.branch_owners(root / "_plans")
    assert good_branch in owners
    assert owners[good_branch][0]["plan"] == "owner-plan"


def test_branch_owners_degrades_an_unhashable_branch_field_instead_of_raising(tmp_path):
    """Field-level known positive: a member's `branch` value is a list, not a
    string (e.g. corruption from a bad merge of two JSON values). Using it
    as a dict key (`out[branch] = ...`) raises `TypeError: unhashable type`
    — inside `_DEGRADE_ERRORS`, so `_guarded` already catches it — this
    proves the whole run survives it and degrades that one plan's ownership
    rather than crashing `branch_owners` for every plan."""
    root = _wt_repo(tmp_path)
    plan_dir = root / "_plans" / "owner-plan"
    state_path = plan_dir / "_worktrees" / "g.json"
    state_path.parent.mkdir(parents=True)
    ssio.durable_write_json(state_path, {
        "group": "g",
        "members": {"s01": {"path": str(tmp_path / "wt-s01"), "branch": ["not", "hashable"]}},
    })

    errors = {}
    owners = reg.branch_owners(root / "_plans", errors)
    assert owners == {}
    assert "owner-plan" in errors


# --------------------------------------------------------------------------
# worktree staleness — foreign trusts git's `prunable`; OWNED derives
# staleness from the owning plan's record, because a locked worktree is
# structurally never `prunable` (constraint 1)
# --------------------------------------------------------------------------
def test_unowned_prunable_worktree_reported_stale(tmp_path):
    """(d)."""
    root = _wt_repo(tmp_path)
    path = tmp_path / "wt-prune"
    wt.git(["worktree", "add", "-q", "-b", "prunbr", str(path), "main"], root, check=True)
    import shutil
    shutil.rmtree(path)

    worktrees = reg.list_worktrees(root)
    rows = reg.classify_worktrees(worktrees, {}, {})
    row = next(r for r in rows if r["branch"] == "prunbr")
    assert row["owned"] is False
    assert row["stale"] is True  # trusts git's own prunable flag for a foreign worktree


def test_owned_locked_worktree_reported_stale_via_registry_not_prunable(tmp_path):
    """(g) — the measured constraint this whole design turns on: `git worktree
    prune` skips locked worktrees BY DESIGN, so git will never mark this one
    `prunable`. The registry must still say STALE, because it knows the
    owning plan's lock has gone dead-and-old."""
    root = _wt_repo(tmp_path)
    wt.git(["branch", "feature-a"], root, check=True)
    path = tmp_path / "wt-locked-owned"
    branch = "plan/g/s01"
    wt.git(["worktree", "add", "-q", "--lock", "-b", branch, str(path), "feature-a"],
           root, check=True)

    plan_dir = root / "_plans" / "owner-plan"
    plan_dir.mkdir(parents=True)
    state_path = plan_dir / "_worktrees" / "g.json"
    state_path.parent.mkdir(parents=True)
    ssio.durable_write_json(state_path, {
        "group": "g", "repo_root": str(root),
        "members": {"s01": {"path": str(path), "branch": branch}},
    })

    worktrees = reg.list_worktrees(root)
    owned_row = next(w for w in worktrees if w["branch"] == branch)
    assert owned_row["locked"] is True
    assert owned_row["prunable"] is False  # git structurally cannot see this one as stale

    owners = reg.branch_owners(root / "_plans")
    rows = reg.classify_worktrees(worktrees, owners, {"owner-plan": "stale-lock"})
    row = next(r for r in rows if r["branch"] == branch)
    assert row["owned"] is True
    assert row["stale"] is True  # ...but the registry catches it anyway


def test_cross_plan_worktree_path_collision_reports_conflict_never_stale(tmp_path):
    """Rework fix — the worktree-side mirror of
    `test_cross_plan_branch_collision_reports_conflict_never_leftover`.
    `worktree.py`'s `.plan-worktrees/<group>/<sid>` path (worktree.py:323)
    carries no plan identity, exactly like the `plan/<group>/<sid>` branch
    name: `root / WORKTREE_DIRNAME / group / sid` is derived purely from
    project_root + group + session id. VERIFIED live in this repo: `pg-prep`
    s01/s02 are declared as a `parallel_group` in BOTH
    `_plans/example-plan-a-2026-07-27` and `_plans/example-plan-b-2026-07-27`
    — same project_root, same group, same session id — which resolves to the
    IDENTICAL worktree path under both.

    `classify_worktrees` used to resolve this last-write-wins (a plain dict
    comprehension over every owner record, keyed only by resolved path), so a
    DONE plan could mark a LIVE plan's worktree stale — exactly the false-reap
    signal ADR-0002 exists to prevent, previously fixed for branches but not
    for worktrees. This test fails without the fix: it plants two plans (one
    live, one done) sharing a resolved worktree path via the same group+sid,
    and asserts the row is reported CONFLICT — owned, but `owner` is None,
    `stale` is False, and both claimants are named — never resolved to a
    single winner."""
    root = _wt_repo(tmp_path)
    wt.git(["branch", "feature-a"], root, check=True)
    shared_path = tmp_path / "wt-shared"
    branch = wt.member_branch("pg-prep", "s01")
    wt.git(["worktree", "add", "-q", "--lock", "-b", branch, str(shared_path), "feature-a"],
           root, check=True)

    live_plan_dir = _make_plan(root, "live-plan", _todo_session())
    done_plan_dir = _make_plan(root, "done-plan", _todo_session())
    ab.apply_mutation(done_plan_dir / "PLAN.html", "s01", status="DONE")

    for plan_dir in (live_plan_dir, done_plan_dir):
        state_path = plan_dir / "_worktrees" / "pg-prep.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        ssio.durable_write_json(state_path, {
            "group": "pg-prep", "repo_root": str(root),
            "members": {"s01": {"path": str(shared_path), "branch": branch}},
        })

    owners = reg.branch_owners(root / "_plans")
    assert {o["plan"] for o in owners[branch]} == {"live-plan", "done-plan"}

    worktrees = reg.list_worktrees(root)
    plan_lifecycle_by_name = {"live-plan": "active", "done-plan": "done"}
    rows = reg.classify_worktrees(worktrees, owners, plan_lifecycle_by_name)
    row = next(r for r in rows if r["branch"] == branch)

    assert row["stale"] is False  # the live plan's worktree is never reapable
    assert row.get("class") == "conflict"
    assert row["owner"] is None  # never resolved to a single winner
    assert {o["plan"] for o in row.get("owners", [])} == {"live-plan", "done-plan"}


# --------------------------------------------------------------------------
# end-to-end collect() + render_table() smoke
# --------------------------------------------------------------------------
def test_collect_joins_plans_branches_and_worktrees(tmp_path, monkeypatch):
    root = _wt_repo(tmp_path)
    # A real plan (manifest + PLAN.html, non-terminal) that ALSO ran a worktree
    # group — collect() has to join the same plan across all three sources.
    plan_dir = _make_plan(root, "owner-plan", _todo_session())
    _group_state(plan_dir, "g", root, monkeypatch)
    _write_lock(plan_dir, pid=__import__("os").getpid())

    data = reg.collect(root, root / "_plans")
    assert {p["plan"] for p in data["plans"]} == {"owner-plan"}
    assert next(p for p in data["plans"] if p["plan"] == "owner-plan")["lifecycle"] == "active"
    branch = wt.member_branch("g", "s01")
    branch_row = next(b for b in data["branches"] if b["branch"] == branch)
    assert branch_row["class"] == "active"
    wt_row = next(w for w in data["worktrees"] if w["branch"] == branch)
    assert wt_row["owned"] is True
    table = reg.render_table(data)
    assert "owner-plan" in table


# --------------------------------------------------------------------------
# `plans-status` CLI end-to-end (subprocess) — exercises the full assembled
# registry (collect() + render_table()/JSON) the way an operator actually
# invokes it.
# --------------------------------------------------------------------------
def test_cli_plans_status_survives_naive_started_at_lock(tmp_path):
    """Rework fix (MEDIUM) end-to-end: `run.py plans-status --json` run as a
    real subprocess against a repo whose plan has a naive (no tzinfo)
    `started_at` in its `.lock` must still exit 0 and report `age_s: null`
    for that plan — not crash the whole read-only run with an uncaught
    TypeError."""
    root = _wt_repo(tmp_path)
    plan_dir = _make_plan(root, "fixture", _todo_session())
    (plan_dir / ".lock").write_text(json.dumps({
        "pid": _dead_pid(),
        "started_at": datetime.now().isoformat(),  # naive: no tzinfo
        "host": "test-host",
    }))

    result = subprocess.run(
        [sys.executable, str(RUN_PY), "plans-status", "--json"],
        cwd=str(root), capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr

    data = json.loads(result.stdout)
    row = next(p for p in data["plans"] if p["plan"] == "fixture")
    assert row["lock"]["age_s"] is None


def test_cli_plans_status_survives_corrupt_worktree_group_state(tmp_path, monkeypatch):
    """Rework fix (gate llm-review-low, attempt 2) end-to-end — the exact
    scenario the finding named: one plan's `_worktrees/<group>.json` is
    corrupt with no `.bak`. `plans-status --json` must still exit 0, still
    report the OTHER (healthy) plan correctly, and mark the broken plan with
    an explicit unreadable/unknown marker rather than aborting for both."""
    root = _wt_repo(tmp_path)
    good_dir = _make_plan(root, "good-plan", _todo_session())
    _write_lock(good_dir, pid=__import__("os").getpid())

    broken_dir = root / "_plans" / "broken-plan"
    state_path = broken_dir / "_worktrees" / "g.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text("{not valid json at all")  # corrupt, no .bak

    result = subprocess.run(
        [sys.executable, str(RUN_PY), "plans-status", "--json"],
        cwd=str(root), capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr

    data = json.loads(result.stdout)
    good_row = next(p for p in data["plans"] if p["plan"] == "good-plan")
    assert good_row["lifecycle"] == "active"
    assert good_row["errors"] == []

    assert "broken-plan" in data["errors"]
    assert any("g.json" in msg for msg in data["errors"]["broken-plan"])


@pytest.mark.parametrize("bad_lock_contents", ["[]", '"x"', "3"])
def test_cli_plans_status_survives_a_malformed_lock_value(tmp_path, bad_lock_contents):
    """Rework fix (gate llm-review-low, attempt 3) end-to-end — the exact
    scenario the finding named: `lock_info` caught a JSON PARSE error but not
    a JSON value of the wrong TYPE (`[]`, a bare string, a number). A `.lock`
    holding any of those parsed fine and then `info.get("pid")` raised
    AttributeError straight out of `classify_plan`, which is mapped over
    every plan by `plans-status` with no surrounding guard — one hand-edited
    or truncated pidfile used to abort the whole read-only run for every
    OTHER plan too. This test fails without the fix: `plans-status --json`
    must still exit 0, still report the healthy plan correctly, and still
    mark the affected plan's row (not vanish it) instead of crashing."""
    root = _wt_repo(tmp_path)
    good_dir = _make_plan(root, "good-plan", _todo_session())
    _write_lock(good_dir, pid=__import__("os").getpid())

    bad_dir = _make_plan(root, "bad-lock-plan", _todo_session())
    (bad_dir / ".lock").write_text(bad_lock_contents)

    result = subprocess.run(
        [sys.executable, str(RUN_PY), "plans-status", "--json"],
        cwd=str(root), capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr

    data = json.loads(result.stdout)
    good_row = next(p for p in data["plans"] if p["plan"] == "good-plan")
    assert good_row["lifecycle"] == "active"
    assert good_row["lock"]["alive"] is True

    bad_row = next(p for p in data["plans"] if p["plan"] == "bad-lock-plan")
    assert bad_row["lifecycle"] == "active"  # manifest/PLAN.html still readable
    assert bad_row["lock"] == {
        "pid": None, "started_at": None, "host": None, "alive": False, "age_s": None,
    }  # marked as a degraded lock, not silently dropped from the report
