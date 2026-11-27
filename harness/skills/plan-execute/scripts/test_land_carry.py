"""R6 §17.1 — the land keeps owner answers and review passes across a main move
that only harvested runtime output, and resets on anything else.

Two layers, both on real git:

* ``test_decide_matrix`` — one row per condition of §17.1 invariant 1, against a
  throwaway repo whose candidates are built the way land builds them (sync main
  into the plan branch, then ``--no-ff`` merge onto main);
* ``test_apply_*`` and ``test_land_*`` — what a carry keeps and drops, and the
  whole land run through the real ``run.py`` on ``_land_fixture``.

    pytest skills/plan-execute/scripts/test_land_carry.py -q
"""

import json
import shlex
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import land_carry as lc  # noqa: E402
import land_state as lst  # noqa: E402
from _land_fixture import (_arm_prepush, ack_only, build_repo, isolate, main_side,  # noqa: E402
                           origin_main, verify_only, work)
from land_test_support import _inproc_session, inproc_cli as run_cli, run_cli_subprocess  # noqa: E402,F401
from worktree import git  # noqa: E402

SLUG = "plan-a"
KEY = "gatesetkey"


def _g(root, *a):
    return git(list(a), root, check=True)[1]


def _commit(root, rel, text, msg="c"):
    p = Path(root) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    _g(root, "add", "-A")
    _g(root, "commit", "-q", "-m", msg)
    return _g(root, "rev-parse", "HEAD")


class Repo:
    """main + a plan branch; `candidate()` syncs and merges like land does."""

    def __init__(self, tmp):
        self.root = Path(tmp) / "r"
        self.root.mkdir()
        _g(self.root, "init", "-q", "-b", "main")
        _g(self.root, "config", "user.email", "t@t")
        _g(self.root, "config", "user.name", "t")
        (self.root / "_plans" / SLUG).mkdir(parents=True)
        (self.root / "_plans" / SLUG / "PLAN.html").write_text("x\n")
        (self.root / "settings.json").write_text(json.dumps(
            {"model": "opus", "hooks": {}}, indent=2))
        (self.root / ".claude").mkdir()
        (self.root / ".claude" / "eval-gates.json").write_text("{}\n")
        (self.root / "src").mkdir()
        (self.root / "src" / "b.py").write_text("B = 1\n")    # src/ is never renamed whole
        _commit(self.root, "src/a.py", "A = 1\n", "base")
        _g(self.root, "branch", "plan")
        self.on("plan")
        _commit(self.root, "src/f.py", "def f():\n    return 1\n", "plan work")
        self.on("main")
        self.plan_dir = self.root / "_plans" / SLUG
        self.ctx = {"root": str(self.root), "slug": SLUG}

    def on(self, ref):
        _g(self.root, "checkout", "-q", ref)

    def main_moves(self, rel, text):
        self.on("main")
        return _commit(self.root, rel, text, "harvest")

    def candidate(self, sync=True):
        main = _g(self.root, "rev-parse", "main")
        if sync:
            self.on("plan")
            _g(self.root, "merge", "-q", "--no-edit", "main")
        plan_head = _g(self.root, "rev-parse", "plan")
        _g(self.root, "checkout", "-q", "--detach", main)
        _g(self.root, "merge", "-q", "--no-ff", "-m", "land", "plan")
        merge = _g(self.root, "rev-parse", "HEAD")
        self.on("main")
        return {"expected": main, "plan_head": plan_head, "merge_sha": merge,
                "resolution_required": False}

    def approved(self, key=KEY):
        st = self.candidate()
        st["carry_basis"] = lc.basis(self.plan_dir, self.ctx, st, key)
        return st

    def decide(self, st, key=KEY, sync=True):
        new = {**st, **self.candidate(sync)}
        return lc.decide(self.plan_dir, self.ctx, new, key)


def _settings(**kw):
    return json.dumps({"model": "opus", "hooks": {}, **kw}, indent=2)


def _whitespace_merge(r, st):
    """A candidate whose plan file differs only in whitespace from the plan head,
    with the plan's own commits unchanged: only the blob check can see it."""
    new = r.candidate()
    _g(r.root, "checkout", "-q", "--detach", new["merge_sha"])
    (r.root / "src" / "f.py").write_text("def f():\n        return 1\n")
    _g(r.root, "commit", "-q", "-am", "reindent")
    new["merge_sha"] = _g(r.root, "rev-parse", "HEAD")
    r.on("main")
    return lc.decide(r.plan_dir, r.ctx, {**st, **new}, KEY)


def _harvest(r):
    """A runtime-only main move, so a row reaches the check after `main-unmoved`."""
    return r.main_moves("projects/p/memory/h.md", "h\n")


def _without_outer_registry(r):
    """Approve with no project registry file, then harvest: absent stays absent."""
    _g(r.root, "rm", "-q", ".claude/eval-gates.json")
    _g(r.root, "commit", "-q", "-m", "no project registry")
    st = r.approved()
    _harvest(r)
    return st


def _dangling_outer_registry(r):
    """Approve with no project registry, then a dangling symlink appears there."""
    st = _without_outer_registry(r)
    (r.root / ".claude").mkdir(exist_ok=True)
    (r.root / ".claude" / "eval-gates.json").symlink_to(r.root / "missing.json")
    return st


def _break_classifier():
    lc._CLASSIFIER = Path("/nonexistent/gearbox-classify.py")   # restored by the test
    return True


# (id, arrange(repo, st) -> verdict, decision, reason)
ROWS = [
    ("live-state only carries",
     lambda r, st: (r.main_moves("projects/p/memory/n.md", "n\n"), r.decide(st))[1],
     "carry", None),
    ("machine-churn key only carries",
     lambda r, st: (r.main_moves("settings.json", _settings(model="sonnet")), r.decide(st))[1],
     "carry", None),
    ("a plan re-synced onto a runtime move carries (plan_head moved, own commits same)",
     lambda r, st: (r.main_moves("_plans/other/x.md", "x\n"), r.decide(st))[1],
     "carry", None),
    ("a re-sync with no sync of the plan branch carries (land_push._resync shape)",
     lambda r, st: (r.main_moves("projects/p/memory/n.md", "n\n"), r.decide(st, sync=False))[1],
     "carry", None),
    ("a hooks/ file resets",
     lambda r, st: (r.main_moves("hooks/guard.py", "x\n"), r.decide(st))[1],
     "reset", "harness-class"),
    ("a harness key in settings.json resets",
     lambda r, st: (r.main_moves("settings.json", _settings(hooks={"Stop": [1]})), r.decide(st))[1],
     "reset", "harness-class"),
    ("a source change and its revert in the range still reset",
     lambda r, st: (r.main_moves("src/a.py", "A = 2\n"), r.main_moves("src/a.py", "A = 1\n"),
                    r.decide(st))[2],
     "reset", "harness-class"),
    ("this plan's own _plans/<slug>/ touched by main resets",
     lambda r, st: (r.main_moves(f"_plans/{SLUG}/note.md", "n\n"), r.decide(st))[1],
     "reset", "plan-path-touched"),
    ("a changed patch-id resets",
     lambda r, st: (_harvest(r), r.on("plan"),
                    _commit(r.root, "src/f.py", "def f():\n    return 2\n"), r.decide(st))[3],
     "reset", "patch-id"),
    ("a whitespace-only plan commit keeps the patch-id but resets",
     lambda r, st: (_harvest(r), r.on("plan"),
                    _commit(r.root, "src/f.py", "def f():\n        return 1\n"), r.decide(st))[3],
     "reset", "plan-side-changed"),
    ("a whitespace-only change in the merged tree resets on the blob",
     lambda r, st: (_harvest(r), _whitespace_merge(r, st))[1],
     "reset", "blob"),
    ("a hand-resolved candidate resets",
     lambda r, st: (_harvest(r), lc.decide(r.plan_dir, r.ctx, {
         **st, **r.candidate(), "resolution_required": True}, KEY))[1],
     "reset", "plan-side-changed"),
    ("a changed gate set resets",
     lambda r, st: (r.main_moves("projects/p/memory/n.md", "n\n"), r.decide(st, key="other"))[1],
     "reset", "gate-set-key"),
    ("a new candidate with no main move (a rebuilt merge) resets",
     lambda r, st: r.decide(st), "reset", "main-unmoved"),
    ("a changed outer registry resets",
     lambda r, st: (r.main_moves("projects/p/memory/n.md", "n\n"),
                    (r.root / ".claude" / "eval-gates.json").write_text('{"g": {}}\n'),
                    r.decide(st))[2],
     "reset", "registry"),
    ("a malformed outer registry resets",
     lambda r, st: (r.main_moves("projects/p/memory/n.md", "n\n"),
                    (r.root / ".claude" / "eval-gates.json").write_text("{\n"),
                    r.decide(st))[2],
     "reset", "registry"),
    ("a non-object outer registry resets",
     lambda r, st: (r.main_moves("projects/p/memory/n.md", "n\n"),
                    (r.root / ".claude" / "eval-gates.json").write_text("[]\n"),
                    r.decide(st))[2],
     "reset", "registry"),
    ("an outer registry absent before and after still carries",
     lambda r, st: r.decide(_without_outer_registry(r)),
     "carry", None),
    ("an outer registry absent before, a dangling symlink after, resets",
     lambda r, st: r.decide(_dangling_outer_registry(r)),
     "reset", "registry"),
    ("a git error resets",
     lambda r, st: (r.main_moves("projects/p/memory/n.md", "n\n"),
                    r.decide({**st, "carry_basis": {**st["carry_basis"],
                                                    "merge_sha": "f" * 40}}))[1],
     "reset", "git-error"),
    ("an unloadable classifier resets",
     lambda r, st: (_break_classifier(),
                    r.main_moves("projects/p/memory/n.md", "n\n"), r.decide(st))[2],
     "reset", "classify-error"),
    ("a land state from today's code (no carry_basis) resets",
     lambda r, st: r.decide({k: v for k, v in st.items() if k != "carry_basis"}),
     "reset", "legacy-state"),
    ("another carry_version resets",
     lambda r, st: r.decide({**st, "carry_basis": {**st["carry_basis"], "carry_version": 0}}),
     "reset", "legacy-state"),
    ("a basis without one part of the old key resets (its records' stamp is unknown)",
     lambda r, st: (_harvest(r), r.decide({**st, "carry_basis": {
         k: v for k, v in st["carry_basis"].items() if k != "plan_head"}}))[1],
     "reset", "legacy-state"),
]


@pytest.mark.parametrize("arrange,decision,reason", [r[1:] for r in ROWS],
                         ids=[r[0] for r in ROWS])
def test_decide_matrix(tmp_path, monkeypatch, arrange, decision, reason):
    monkeypatch.setattr(lc, "_CLASSIFIER", lc._CLASSIFIER)    # restored after the row
    r = Repo(tmp_path)
    st = r.approved()
    out = arrange(r, st)
    assert (out["decision"], out["reason"]) == (decision, reason), out
    assert out["carry_version"] == 2


def test_a_non_ancestor_main_resets(tmp_path):
    """The approved main was rewritten (a force-push): reset, whatever the paths."""
    r = Repo(tmp_path)
    r.main_moves("projects/p/memory/n.md", "one\n")
    st = r.approved()
    _g(r.root, "reset", "-q", "--hard", "HEAD~1")
    r.main_moves("projects/p/memory/other.md", "two\n")
    out = r.decide(st)
    assert (out["decision"], out["reason"]) == ("reset", "not-ancestor"), out


def test_a_git_error_reading_settings_resets_as_classify_error(tmp_path, monkeypatch):
    """A failing `git show` of settings.json is an error listing the range (§18.2
    check 4), not an absent file and not a harness path."""
    r = Repo(tmp_path)
    st = r.approved()
    r.main_moves("settings.json", _settings(model="sonnet"))    # churn: carries when readable
    real = lc.subprocess.run

    def broken_show(args, *a, **kw):
        if args[:2] == ["git", "show"] and str(args[2]).endswith(":settings.json"):
            return lc.subprocess.CompletedProcess(args, 128, b"", b"fatal: unable to read")
        return real(args, *a, **kw)
    monkeypatch.setattr(lc.subprocess, "run", broken_show)
    out = r.decide(st)
    assert (out["decision"], out["reason"]) == ("reset", "classify-error"), out


def test_a_hooks_file_renamed_into_live_state_resets(tmp_path):
    """`--no-renames`: a rename lists both sides, so the hooks/ side resets."""
    r = Repo(tmp_path)
    r.main_moves("hooks/guard.py", "x = 1\n")
    st = r.approved()
    (r.root / "projects" / "p" / "memory").mkdir(parents=True)
    _g(r.root, "mv", "hooks/guard.py", "projects/p/memory/guard.py")
    _g(r.root, "commit", "-q", "-m", "harvest: rename")
    out = r.decide(st)
    assert (out["decision"], out["reason"]) == ("reset", "harness-class"), out


@pytest.mark.parametrize("rel", ["projects/p/memory/side.md", "hooks/side.py"])
def test_a_merge_into_main_is_diffed_against_every_parent(tmp_path, rel):
    """§17.1 invariant 4 lists every commit with `git log -m`: a merge is diffed
    against each parent. A side branch cut BEFORE an approved hooks/ commit lacks
    it, so even a harvest-only side resets (merges on main are rare)."""
    r = Repo(tmp_path)
    base = _g(r.root, "rev-parse", "main")
    r.main_moves("hooks/approved.py", "x = 1\n")
    st = r.approved()
    _g(r.root, "checkout", "-q", "-b", "side", base)
    _commit(r.root, rel, "side\n", "side")
    r.on("main")
    _g(r.root, "merge", "-q", "--no-ff", "--no-edit", "side")
    out = r.decide(st)
    assert (out["decision"], out["reason"]) == ("reset", "harness-class"), out


def _merge_taking(r, branch, rel):
    """Merge `branch` into main, resolving `rel` to the branch's own copy."""
    r.on("main")
    _g(r.root, "merge", "-q", "--no-ff", "--no-commit", branch)
    _g(r.root, "checkout", branch, "--", rel)
    _g(r.root, "commit", "-q", "--no-edit")


def test_two_merges_that_select_then_restore_a_harness_file_reset(tmp_path):
    """Merge 1 takes an older side's hooks file, merge 2 restores the approved one
    from its own side. Each merge equals one parent on that file (so `--cc` misses
    both) and the net diff is empty; a per-parent diff of every commit sees it."""
    r = Repo(tmp_path)
    r.main_moves("hooks/h.py", "old\n")
    old = _g(r.root, "rev-parse", "main")
    r.main_moves("hooks/h.py", "approved\n")
    st = r.approved()
    _g(r.root, "branch", "side2", "main")
    _g(r.root, "checkout", "-q", "-b", "side1", old)
    _commit(r.root, "projects/p/memory/a.md", "a\n", "side1")
    _merge_taking(r, "side1", "hooks/h.py")
    _g(r.root, "checkout", "-q", "side2")
    _commit(r.root, "projects/p/memory/b.md", "b\n", "side2")
    _merge_taking(r, "side2", "hooks/h.py")
    net = _g(r.root, "diff", "--name-only", st["expected"], "main").split()
    assert net == ["projects/p/memory/a.md", "projects/p/memory/b.md"]
    out = r.decide(st)
    assert (out["decision"], out["reason"]) == ("reset", "harness-class"), out


def test_a_plan_rename_source_that_main_touches_resets(tmp_path, monkeypatch):
    """`--no-renames` on the plan side: the plan renames a.md to b.md, main edits
    then restores a.md. Rename detection would list only b.md and carry. Every git
    call runs with diff.renames=true, whatever the user's global config says."""
    for k, v in {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "diff.renames",
                 "GIT_CONFIG_VALUE_0": "true"}.items():
        monkeypatch.setenv(k, v)
    r = Repo(tmp_path)
    r.main_moves("projects/p/memory/a.md", "same\n" * 5)
    r.on("plan")
    _g(r.root, "merge", "-q", "--no-edit", "main")
    _g(r.root, "mv", "projects/p/memory/a.md", "projects/p/memory/b.md")
    _g(r.root, "commit", "-q", "-m", "plan rename")
    r.on("main")
    st = r.approved()
    r.main_moves("projects/p/memory/a.md", "edited\n")
    r.main_moves("projects/p/memory/a.md", "same\n" * 5)
    out = r.decide(st)
    assert (out["decision"], out["reason"]) == ("reset", "plan-path-touched"), out


def test_units_list_an_unrelated_root_commit_like_git_log_m(tmp_path):
    """A merged-in unrelated history brings a root commit: `_units` lists it as
    `git log -m --no-renames --name-only` does, not as an empty unit."""
    r = Repo(tmp_path)
    old = _g(r.root, "rev-parse", "main")
    _g(r.root, "checkout", "-q", "--orphan", "orphan")
    _g(r.root, "rm", "-rq", "--cached", ".")
    _g(r.root, "clean", "-fdq")
    _commit(r.root, "projects/p/memory/o.md", "o\n", "orphan root")
    r.on("main")
    _g(r.root, "merge", "-q", "--no-ff", "--no-edit", "--allow-unrelated-histories",
       "orphan")
    log = _g(r.root, "log", "-m", "--root", "--no-renames", "--name-only", "--format=@%H",
             f"{old}..main")
    want = sorted((c, tuple(sorted(ps))) for c, *ps in
                  (b.split() for b in log.split("@") if b.strip()))
    got = sorted((c, tuple(sorted(ps))) for ps, _, c in lc._units(str(r.root), old, "main"))
    assert got == want


def test_a_plan_changed_runtime_path_resets(tmp_path):
    """The plan itself edits a live-state file; a harvest of that same file resets
    (the two edits touch different lines, so the sync merges cleanly)."""
    r = Repo(tmp_path)
    r.main_moves("projects/p/memory/n.md", "1\n2\n3\n4\n5\n6\n")
    r.on("plan")
    _g(r.root, "merge", "-q", "--no-edit", "main")
    _commit(r.root, "projects/p/memory/n.md", "plan\n2\n3\n4\n5\n6\n")
    r.on("main")
    st = r.approved()
    r.main_moves("projects/p/memory/n.md", "1\n2\n3\n4\n5\nharvest\n")
    out = r.decide(st)
    assert (out["decision"], out["reason"]) == ("reset", "plan-path-touched"), out


def test_carry_classes_count_paths(tmp_path):
    r = Repo(tmp_path)
    st = r.approved()
    r.main_moves("projects/p/memory/n.md", "n\n")
    r.main_moves("settings.json", _settings(model="sonnet"))
    out = r.decide(st)
    assert out["decision"] == "carry" and out["classes"] == {"live_state": 1, "churn": 1}, out


# --------------------------------------------------------------------------
# apply — what a carry keeps
# --------------------------------------------------------------------------
REVIEW = {"kind": "argv", "argv": ["python3", "scripts/llm_review_gate.py"]}
TEST = {"kind": "argv", "argv": ["pytest"]}
TEST_INDET = {"kind": "argv", "argv": ["pytest"], "indeterminate_exit": 2}
SKILL = {"kind": "skill", "skill": "code-review"}


OLD = "M1:P1:X1:G"      # the key land stamped on the basis candidate's records
#: `st["gate_key"]` as each path leaves it: `land_push._resync` keeps the old key,
#: and `step_worktree` pops it when main moved before this `land` (the common case).
PATHS = pytest.mark.parametrize("gate_key", [OLD, None], ids=["resync", "rebuilt-worktree"])


def _state(gate_key=OLD):
    def rec(gid, outcome):
        return {"gate_id": gid, "outcome": outcome, "gate_key": OLD, "findings_count": 0}
    return {
        "gate_key": gate_key, "expected": "M2", "plan_head": "P2", "merge_sha": "X2",
        "skill_gates": {"skill-ok": rec("skill-ok", "pass"), "skill-red": rec("skill-red", "fail")},
        "argv_gates": {"review": rec("review", "pass"), "tests": rec("tests", "pass"),
                       "tests-indet": rec("tests-indet", "pass")},
        "on_box_gates": {"review-box": rec("review-box", "verified-on-box"),
                         "review-blocked": rec("review-blocked", "blocked"),
                         "review-undecided": rec("review-undecided", "on-box")},
        "ack": {"plan_head": "P1", "main_head": "M1", "merge_sha": "X1", "gate_digest": "D"},
        "carry_basis": {"carry_version": lc.CARRY_VERSION, "main_head": "M1", "plan_head": "P1",
                        "merge_sha": "X1", "gate_set_key": "G"},
    }


GATES = [("review", REVIEW), ("review-blocked", REVIEW), ("review-box", REVIEW),
         ("review-undecided", REVIEW), ("skill-ok", SKILL), ("skill-red", SKILL),
         ("tests", TEST), ("tests-indet", TEST_INDET)]


@PATHS
def test_apply_carry_keeps_settled_review_passes_and_answers(tmp_path, monkeypatch, gate_key):
    fx = build_repo(tmp_path)
    iso = isolate(fx, SLUG)
    ctx = lst.context(iso["plan_dir"])
    st = _state(gate_key)
    saves, real_save = [], lc.save
    monkeypatch.setattr(lc, "save", lambda *a: (saves.append(dict(a[2])), real_save(*a))[1])
    lc.apply(iso["plan_dir"], ctx, st, GATES, "new",
             {"decision": "carry", "reason": None, "classes": {"harness": 1},
              "basis": {"carry_version": 1, "main_head": "M2"},
              "route": "code", "moved_paths": ["src/other.py"]})
    # F9: ONE save, holding the new key and the carried record together
    assert len(saves) == 1 and saves[0]["gate_key"] == "new"
    assert saves[0]["carried_gates"] == ["review", "review-box", "skill-ok"]
    assert saves[0]["ci_only_gates"] == []             # F3: only review passes were copied
    assert set(st["skill_gates"]) == {"skill-ok"}       # a fail, a block or an undecided
    assert set(st["argv_gates"]) == {"review"}          # test gates re-run, even with
    assert set(st["on_box_gates"]) == {"review-box"}     # indeterminate_exit declared
    for field in ("skill_gates", "argv_gates", "on_box_gates"):
        assert all(r["gate_key"] == "new" for r in st[field].values())
    assert st["on_box_gates"]["review-box"]["outcome"] == "verified-on-box"
    assert (st["ack"]["plan_head"], st["ack"]["main_head"], st["ack"]["merge_sha"],
            st["ack"]["gate_digest"]) == ("P2", "M2", "X2", "D")   # digest never re-bound
    assert len(st["ack"]["carries"]) == 1
    assert (st["ack"]["carries"][-1]["route"], st["ack"]["carries"][-1]["moved_paths"]) == (
        "code", ["src/other.py"])                       # §18.9
    assert st["moved_since_ack"] == ["src/other.py"]
    saved = lst.load(iso["plan_dir"])                    # saved before any park
    assert saved["gate_key"] == "new" and saved["carry_basis"]["main_head"] == "M2"
    ev = _events(iso["plan_dir"])[-1]
    assert (ev["decision"], ev["carry_version"]) == ("carry", 2)
    assert (ev["from_key"], ev["to_key"]) == (OLD, "new")    # the old key, popped or not
    assert st["ack"]["carries"][-1]["from_key"] == OLD
    assert ev["carried"] == ["review", "review-box", "skill-ok"]
    assert "tests" in ev["rerun"] and "tests-indet" in ev["rerun"]
    assert (ev["route"], ev["moved_paths"]) == ("code", ["src/other.py"])


def test_apply_reset_empties_everything_and_moves_the_ack(tmp_path):
    fx = build_repo(tmp_path)
    iso = isolate(fx, SLUG)
    ctx = lst.context(iso["plan_dir"])
    st = _state()
    lc.apply(iso["plan_dir"], ctx, st, GATES, "new",
             {"decision": "reset", "reason": "harness-class", "classes": {"harness": 1},
              "basis": None})
    assert (st["skill_gates"], st["argv_gates"], st["on_box_gates"]) == ({}, {}, {})
    assert st["ack"] is None and st["invalidated_from"]["main_head"] == "M1"
    assert "carry_basis" not in st
    ev = _events(iso["plan_dir"])[-1]
    assert (ev["decision"], ev["reason"], ev["carried"]) == ("reset", "harness-class", [])


@PATHS
@pytest.mark.parametrize("field,gid", [("skill_gates", "skill-ok"), ("argv_gates", "review"),
                                       ("on_box_gates", "review-box")])
def test_apply_carry_drops_a_record_with_a_stale_key(tmp_path, field, gid, gate_key):
    """A record from an older candidate is dropped, never re-keyed, on both paths."""
    fx = build_repo(tmp_path)
    iso = isolate(fx, SLUG)
    st = _state(gate_key)
    st[field][gid]["gate_key"] = "M0:P0:X0:G"            # passed under an earlier key
    lc.apply(iso["plan_dir"], lst.context(iso["plan_dir"]), st, GATES, "new",
             {"decision": "carry", "reason": None, "classes": {},
              "basis": {"carry_version": 1, "main_head": "M2"}})
    assert gid not in st[field]
    kept = {g for f in ("skill_gates", "argv_gates", "on_box_gates") for g in st[f]}
    assert kept == {"skill-ok", "review", "review-box"} - {gid}     # the rest still carry


@PATHS
@pytest.mark.parametrize("part", ["plan_head", "main_head", "merge_sha"])
def test_apply_carry_never_rebinds_an_ack_for_another_candidate(tmp_path, part, gate_key):
    """The ack is re-bound only from the candidate `decide` compared (the basis).
    One bound to anything else moves to `invalidated_from`; the records still carry."""
    fx = build_repo(tmp_path)
    iso = isolate(fx, SLUG)
    st = _state(gate_key)
    st["ack"][part] = "other"
    lc.apply(iso["plan_dir"], lst.context(iso["plan_dir"]), st, GATES, "new",
             {"decision": "carry", "reason": None, "classes": {},
              "basis": {"carry_version": 1, "main_head": "M2"}})
    assert st["ack"] is None and st["invalidated_from"][part] == "other"
    assert "carries" not in st["invalidated_from"]
    assert set(st["argv_gates"]) == {"review"} and set(st["on_box_gates"]) == {"review-box"}


@pytest.mark.parametrize("bad", ["M1", ["M1"], 7])
@pytest.mark.parametrize("decision", ["carry", "reset"])
def test_apply_a_non_dict_carry_basis_resets_without_crashing(tmp_path, bad, decision):
    fx = build_repo(tmp_path)
    iso = isolate(fx, SLUG)
    st = {**_state(), "carry_basis": bad}
    lc.apply(iso["plan_dir"], lst.context(iso["plan_dir"]), st, GATES, "new",
             {"decision": decision, "reason": None, "classes": {}, "basis": None})
    assert (st["skill_gates"], st["argv_gates"], st["on_box_gates"]) == ({}, {}, {})
    assert st["ack"] is None and "carry_basis" not in st
    ev = _events(iso["plan_dir"])[-1]
    assert (ev["decision"], ev["from_main"]) == ("reset", None)


def _events(plan_dir, name="land_carry"):
    p = Path(plan_dir) / "run.ndjson"
    rows = [json.loads(ln) for ln in p.read_text().splitlines() if ln.strip()]
    return [r for r in rows if r.get("event") == name]


# --------------------------------------------------------------------------
# The whole land, through run.py
# --------------------------------------------------------------------------
def _acked(tmp_path, stop_file="projects/p/memory/STOP"):
    """A land waiting on the push with a recorded ack. One TEST gate that reads a
    live-state path, so a harvest can turn it red."""
    fx = build_repo(tmp_path, gates={"marker-gate": {
        "kind": "argv", "cwd": ".", "timeout": 60,
        "argv": ["/bin/sh", "-c", f"test ! -f {stop_file}"]}})
    iso = isolate(fx, SLUG)
    work(iso["tree"], "src/f.py", "F = 1\n")
    rc, out = run_cli("land", iso["plan_dir"])
    assert out["action"] == "land-awaits-review", out
    rc, out = ack_only(iso["plan_dir"], "ok")
    assert out["action"] == "land-acked", out
    return fx, iso


def test_land_carries_the_ack_across_a_harvest_and_lands(tmp_path):
    fx, iso = _acked(tmp_path)
    moved = main_side(fx, "projects/p/memory/note.md", "harvested\n", "harvest: 1 live-state")
    rc, out = run_cli("land", iso["plan_dir"])
    assert out["action"] == "landed", out
    assert origin_main(fx) != moved
    ev = _events(iso["plan_dir"])[-1]
    assert (ev["decision"], ev["to_main"]) == ("carry", moved), ev
    assert ev["rerun"] == ["marker-gate"]                # the test gate re-ran


def test_land_resets_on_a_hooks_change(tmp_path):
    fx, iso = _acked(tmp_path)
    moved = main_side(fx, "hooks/guard.py", "x = 1\n")
    rc, out = run_cli("land", iso["plan_dir"])
    assert out["action"] == "land-awaits-review", out
    assert origin_main(fx) == moved
    assert lst.load(iso["plan_dir"])["ack"] is None
    ev = _events(iso["plan_dir"])[-1]
    assert (ev["decision"], ev["reason"]) == ("reset", "harness-class"), ev


def test_land_a_rerun_test_gate_that_fails_never_pushes(tmp_path):
    fx, iso = _acked(tmp_path)
    moved = main_side(fx, "projects/p/memory/STOP", "stop\n", "harvest: 1 live-state")
    rc, out = run_cli("land", iso["plan_dir"])
    assert out["action"] == "land-parked" and out["kind"] == "gate-failed", out
    assert origin_main(fx) == moved
    st = lst.load(iso["plan_dir"])                      # the carried ack does not
    assert st["ack"] is None and st["invalidated_from"]["carries"]   # outlive the red
    ev = _events(iso["plan_dir"])
    assert ev[-2]["decision"] == "carry"
    assert (ev[-1]["decision"], ev[-1]["reason"], ev[-1]["gates"]) == (
        "reset", "gate-result", ["marker-gate"]), ev[-1]


def test_step_ack_logs_a_reset_when_a_carried_ack_no_longer_matches(tmp_path):
    """§17.1 invariant 13: a carried ack whose result digest moved is dropped, and
    the reset is logged with reason gate-result."""
    import land_steps  # noqa: PLC0415
    fx, iso = _acked(tmp_path)
    main_side(fx, "projects/p/memory/note.md", "harvested\n")
    ctx = lst.context(iso["plan_dir"])
    st = lst.load(iso["plan_dir"])
    st["ack"]["carries"] = [{"at": "t"}]
    st["gate_digest"] = "changed"
    out = land_steps.step_ack(iso["plan_dir"], ctx, st)
    assert out["action"] == "land-awaits-review"
    assert st["ack"] is None and st["invalidated_from"]["gate_digest"] != "changed"
    ev = _events(iso["plan_dir"])[-1]
    assert (ev["decision"], ev["reason"]) == ("reset", "gate-result"), ev


def test_land_a_code_race_inside_the_push_window_goes_back_to_review(tmp_path):
    """§17.3: a main move by code during the push is not re-synced unattended."""
    fx, iso = _acked(tmp_path)
    _arm_prepush(fx, times=1, race_dir="")
    rc, out = run_cli("land", iso["plan_dir"])
    assert out["action"] == "land-awaits-review", out
    ev = _events(iso["plan_dir"])[-1]
    assert (ev["decision"], ev["reason"]) == ("reset", "harness-class"), ev


# --------------------------------------------------------------------------
# The whole land with THREE gates, each run counted: a review gate, a review gate
# only the owner can answer (on-box), and a test gate. Every case runs: land, the
# owner's answers, a main move, land again.
# --------------------------------------------------------------------------
_STUB = """import sys
from pathlib import Path
with Path(sys.argv[1]).open("a") as fh:      # one byte per real run
    fh.write("x")
if sys.argv[2] == "refuse":                  # the policy refusal `verifier_park` reads
    print("VERIFIER: on_box_human")
    print("DEGRADED_FROM: codex_harness_no_claude_verifier")
    sys.exit(2)
"""
N_MD = "1\n2\n3\n4\n5\n6\n"
ONCE = {"review": 1, "review-box": 1, "tests": 1}
KEPT = {"review": 1, "review-box": 1, "tests": 2}      # only the test gate ran again
ALL = {"review": 2, "review-box": 2, "tests": 2}


class Land3:
    """An ACKED land of a plan that changes `src/f.py` and line 1 of the live-state
    file `projects/p/memory/n.md`. The review stub is a file named
    `llm_review_gate.py`, which is what `verify_pass.is_review_gate` keys on."""

    def __init__(self, tmp_path):
        self.counts = tmp_path / "counts"
        self.counts.mkdir()
        stub = tmp_path / "gates" / "llm_review_gate.py"
        stub.parent.mkdir()
        stub.write_text(_STUB)

        def review(gid, mode, **kw):
            return {"kind": "argv", "cwd": ".", "timeout": 60, **kw,
                    "argv": [sys.executable, str(stub), str(self.counts / gid), mode]}
        gates = {"review": review("review", "pass"),
                 "review-box": review("review-box", "refuse", indeterminate_exit=2),
                 "tests": {"kind": "argv", "cwd": ".", "timeout": 60, "argv": [
                     "/bin/sh", "-c", f"printf x >> {shlex.quote(str(self.counts / 'tests'))}; "
                                      "test ! -f projects/p/memory/STOP"]}}
        self.fx = fx = build_repo(tmp_path, gates=gates, plan_gates=sorted(gates))
        (fx["root"] / "settings.json").write_text(_settings())
        _commit(fx["root"], "projects/p/memory/n.md", N_MD, "seed")
        _g(fx["root"], "push", "-q", "origin", "main")
        self.iso = iso = isolate(fx, SLUG)
        self.plan_dir = iso["plan_dir"]
        work(iso["tree"], "src/f.py", "F = 1\n")
        work(iso["tree"], "projects/p/memory/n.md", "plan\n" + N_MD[2:])
        out = self.cli("land", want="land-parked")
        assert (out["kind"], out["on_box"]) == ("gate-on-box-verification", ["review-box"]), out
        assert verify_only(self.plan_dir, "verified-on-box", "read it")[1]["action"] == (
            "land-verified")
        self.cli("land", want="land-awaits-review")
        assert ack_only(self.plan_dir, "ok")[1]["action"] == "land-acked"
        assert self.runs() == ONCE
        st = self.state()                # the key format `_basis_key` rebuilds
        assert st["gate_key"] == lc._basis_key(st["carry_basis"])
        self.key = st["gate_key"]

    def cli(self, cmd, *args, want):
        rc, out = run_cli(cmd, self.plan_dir, *args)
        assert out.get("action") == want, out
        return out

    def runs(self):
        return {g: len((self.counts / g).read_text()) if (self.counts / g).exists() else 0
                for g in ONCE}

    def state(self):
        return lst.load(self.plan_dir)

    def last(self):
        return _events(self.plan_dir)[-1]


CARRIES = [("a live-state path", "projects/p/memory/note.md", "harvested\n"),
           ("a machine-churn settings key only", "settings.json", _settings(model="sonnet"))]


@pytest.mark.parametrize("rel,text", [c[1:] for c in CARRIES], ids=[c[0] for c in CARRIES])
def test_land3_a_runtime_only_move_keeps_the_review_and_the_owner_answer(tmp_path, rel, text):
    """Main moved before `land` ran again, so `step_worktree` popped `gate_key`:
    the review pass and the on-box answer still carry, and only the test re-runs."""
    ld = Land3(tmp_path)
    moved = main_side(ld.fx, rel, text, "harvest")
    out = ld.cli("land", want="landed")
    assert origin_main(ld.fx) not in (moved, None) and out["merge_sha"]
    assert ld.runs() == KEPT
    ev = ld.last()
    assert (ev["decision"], ev["reason"], ev["from_key"]) == ("carry", None, ld.key), ev
    assert (ev["carried"], ev["rerun"]) == (["review", "review-box"], ["tests"]), ev
    assert ev["to_main"] == moved and ev["to_key"] != ld.key


RESETS = [("a hooks/ file", "hooks/guard.py", "x = 1\n", "harness-class"),
          ("a file the plan changes", "projects/p/memory/n.md", N_MD[:-2] + "harvest\n",
           "plan-path-touched")]


@pytest.mark.parametrize("rel,text,reason", [c[1:] for c in RESETS], ids=[c[0] for c in RESETS])
def test_land3_any_other_move_resets_and_every_gate_runs_again(tmp_path, rel, text, reason):
    ld = Land3(tmp_path)
    moved = main_side(ld.fx, rel, text)
    out = ld.cli("land", want="land-parked")             # the owner is asked again
    assert (out["kind"], out["on_box"]) == ("gate-on-box-verification", ["review-box"]), out
    assert origin_main(ld.fx) == moved                   # nothing pushed
    assert ld.runs() == ALL
    st = ld.state()
    assert st["ack"] is None and st["on_box_gates"] == {}
    ev = ld.last()
    assert (ev["decision"], ev["reason"], ev["carried"]) == ("reset", reason, []), ev
    assert ev["rerun"] == ["review", "review-box", "tests"] and ev["from_key"] == ld.key


def test_land3_a_rerun_test_that_fails_drops_the_carried_approval(tmp_path):
    ld = Land3(tmp_path)
    moved = main_side(ld.fx, "projects/p/memory/STOP", "stop\n", "harvest")
    out = ld.cli("land", want="land-parked")
    assert (out["kind"], out["failed"]) == ("gate-failed", ["tests"]), out
    assert origin_main(ld.fx) == moved                   # nothing pushed
    assert ld.runs() == KEPT                             # the carry itself was right
    st = ld.state()
    assert st["ack"] is None and st["invalidated_from"]["carries"]
    carry, reset = _events(ld.plan_dir)[-2:]
    assert (carry["decision"], carry["carried"]) == ("carry", ["review", "review-box"]), carry
    assert (reset["decision"], reset["reason"], reset["gates"]) == (
        "reset", "gate-result", ["tests"]), reset


def test_land3_an_answer_from_an_older_candidate_is_asked_again(tmp_path):
    """The stale-key protection on the rebuilt-worktree path, where the old key is
    rebuilt from the basis: a record stamped for an older candidate is dropped by
    the carry, never re-keyed, so the owner is asked again and nothing pushes."""
    ld = Land3(tmp_path)
    st = ld.state()
    st["on_box_gates"]["review-box"]["gate_key"] = "M0:P0:X0:G"
    lst.save(ld.plan_dir, lst.context(ld.plan_dir), st)
    moved = main_side(ld.fx, "projects/p/memory/note.md", "harvested\n", "harvest")
    out = ld.cli("land", want="land-parked")
    assert (out["kind"], out["on_box"]) == ("gate-on-box-verification", ["review-box"]), out
    assert origin_main(ld.fx) == moved
    assert ld.runs() == {"review": 1, "review-box": 2, "tests": 2}
    carry = _events(ld.plan_dir)[-2]
    assert (carry["decision"], carry["carried"], carry["rerun"]) == (
        "carry", ["review"], ["review-box", "tests"]), carry
    assert ld.state()["ack"] is None                     # the carried ack went with it


def test_land3_a_harvest_inside_the_push_window_carries(tmp_path):
    """The `land_push._resync` path: `gate_key` is still in the state, and the
    carry is the same one. The race commit is a live-state file (a harvest)."""
    ld = Land3(tmp_path)
    before = origin_main(ld.fx)
    _arm_prepush(ld.fx, times=1)
    out = ld.cli("land", want="landed")
    assert out["merge_sha"] and origin_main(ld.fx) != before
    assert ld.runs() == KEPT
    ev = ld.last()
    assert (ev["decision"], ev["from_key"], ev["from_main"]) == ("carry", ld.key, before), ev
    assert (ev["carried"], ev["rerun"]) == (["review", "review-box"], ["tests"]), ev
    assert len(ld.state()["ack"]["carries"]) == 1


def test_a_reset_decided_in_an_open_phase2_window_closes_it_before_any_gate_runs(tmp_path, monkeypatch):
    import land_phase2 as lp2
    iso = isolate(build_repo(tmp_path), SLUG)
    ctx, st = lst.context(iso["plan_dir"]), {**_state(), "phase": "2", "phase2": {"mode": "on"}}
    left = []
    monkeypatch.setattr(lp2, "is_open", lambda s: s.get("phase") == "2")
    monkeypatch.setattr(lp2, "_unmark", lambda: left.append("unmarked"))
    monkeypatch.setattr(lc, "rsi", type("R", (), {"log_event": staticmethod(lambda *a, **k: left.append("logged"))}))
    lc.apply(iso["plan_dir"], ctx, st, GATES, "new",
             {"decision": "reset", "reason": "harness-class", "classes": {}, "basis": None})
    assert left == ["unmarked", "logged"] and "phase" not in st and "phase2" not in st
    assert "phase" not in lst.load(iso["plan_dir"])
