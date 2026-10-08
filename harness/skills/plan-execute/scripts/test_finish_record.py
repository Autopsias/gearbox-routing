"""FIN-01 / FIN-02 (s02) — the record step's confinement, on the real bare-origin
fixture. Split from ``test_finish.py`` to keep each file under 500 lines.

    python3 -m pytest -q test_finish_record.py
"""

import os
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import finish_record as fr  # noqa: E402
from _land_fixture import local_main, origin_main  # noqa: E402
import test_finish as tf  # noqa: E402
from test_finish import _arm, _base, _dirty_record  # noqa: E402
from worktree import git  # noqa: E402

fx, ci = tf.fx, tf.ci                               # the shared fixtures


def _push(fx, msg):
    git(["add", "-A", "-f", "--", "."], fx["root"], check=True)
    git(["commit", "-q", "-m", msg], fx["root"], check=True)
    git(["push", "-q", "origin", "main"], fx["root"], check=True)


def _record(fx, apply=True):
    return fr.record(fx["plan"], _arm(fx), origin_main(fx), apply=apply,
                     lease_ok=lambda: True)[0]


def test_a_tracked_plan_symlink_is_never_written_through(fx, ci, tmp_path):
    victim = tmp_path / "operator.txt"
    victim.write_text("operator's\n")
    link = fx["plan"] / "notes.txt"
    os.symlink(victim, link)
    _push(fx, "plan symlink")
    link.unlink()
    link.write_text("plan copy\n")                 # the local copy is a regular file
    before = origin_main(fx)
    for apply in (False, True):
        rec = _record(fx, apply)
        assert victim.read_text() == "operator's\n"
        assert (rec["status"], rec["park_reason"]) == ("parked", "outside-record")
    assert origin_main(fx) == before


def test_a_commit_hook_that_stages_another_file_parks_before_the_push(fx, ci):
    hook = fx["root"] / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\necho smuggled > shared.txt && git add shared.txt\n")
    hook.chmod(0o755)
    _dirty_record(fx)
    before = origin_main(fx)
    rec = _record(fx)
    assert (rec["status"], rec["park_reason"]) == ("parked", "outside-record")
    assert rec["outside_record"] == ["shared.txt"]
    assert origin_main(fx) == before


def test_an_escaped_trailing_space_in_the_last_ignore_rule_survives(tmp_path, ci):
    fx = _base(tmp_path, amend=False)
    gi = fx["root"] / ".gitignore"
    old = gi.read_bytes() + b"keep\\ "               # last rule, no final newline
    gi.write_bytes(old)
    _push(fx, "escaped space")
    _dirty_record(fx)
    rec = _record(fx)
    assert rec["status"] == "pushed" and ".gitignore" in rec["paths"]
    pushed = git(["show", "origin/main:.gitignore"], fx["root"], strip=False)[1].encode()
    assert pushed.startswith(old + b"\n")


def test_a_tracked_ignored_runtime_file_is_never_staged(fx, ci):
    state = fx["plan"] / "run_state.json"
    state.write_text('{"lease": "old"}\n')
    _push(fx, "tracked runtime state")
    state.write_text('{"lease": "token"}\n')
    _dirty_record(fx)
    rec = _record(fx)
    assert rec["status"] == "pushed"
    assert "_plans/plan-a/run_state.json" not in rec["paths"]
    shown = git(["show", "origin/main:_plans/plan-a/run_state.json"], fx["root"])[1]
    assert shown == '{"lease": "old"}'


def test_check_b_sees_both_sides_of_a_rename(fx):
    git(["mv", "src/a.py", "src/b.py"], fx["root"], check=True)
    assert sorted(fr._porcelain(fx["root"])) == [("A ", "src/b.py"), ("D ", "src/a.py")]


# ------------------------------------------------- links on the path (one class)
# Every test below fails on the pre-fix module (checked by swapping it back in).
def test_lstat_inside_refuses_a_link_on_any_component(tmp_path):
    (tmp_path / "real").mkdir()
    (tmp_path / "real" / "f").write_text("x")
    os.symlink(tmp_path / "real", tmp_path / "dirlink")
    os.symlink(tmp_path / "real" / "f", tmp_path / "filelink")
    assert fr.lstat_inside(tmp_path, "real/f") and fr.lstat_inside(tmp_path, "real")
    assert fr.lstat_inside(tmp_path, "real/absent/deeper") is None
    for bad in ("dirlink/f", "filelink", "dirlink", "real/f/x", "../x"):
        with pytest.raises(fr.Linked):
            fr.lstat_inside(tmp_path, bad)


def _outside(tmp_path, name, text):
    out = tmp_path / "outside" / name
    out.parent.mkdir(exist_ok=True)
    out.write_text(text)
    return out


def _link(path, to):
    if path.is_dir() and not path.is_symlink():
        for f in path.iterdir():
            f.unlink()
        path.rmdir()
    elif path.exists() or path.is_symlink():
        path.unlink()
    os.symlink(to, path)


@pytest.mark.parametrize("case", ["origin-index-leaf", "origin-plan-parent",
                                  "local-plan-parent"])
def test_the_record_step_parks_on_a_link_anywhere_on_its_paths(fx, ci, tmp_path, case):
    if case == "origin-index-leaf":                # origin tracks the index as a link
        victim = _outside(tmp_path, "index.md", tf.INDEX)
        _link(fx["root"] / "_plans_index.md", victim)
        _push(fx, "linked index")
        (fx["root"] / "_plans_index.md").unlink()
        (fx["root"] / "_plans_index.md").write_text(tf.INDEX)
        _dirty_record(fx)
    elif case == "origin-plan-parent":             # origin tracks sessions/ as a link
        victim = _outside(tmp_path, "s01.md", "operator's\n")
        _link(fx["plan"] / "sessions", victim.parent)
        _push(fx, "linked sessions")
        (fx["plan"] / "sessions").unlink()         # locally a real dir again
        _dirty_record(fx)
    else:                                          # the local plan dir holds the link
        victim = _outside(tmp_path, "s01.md", "closeout\n")
        _link(fx["plan"] / "sessions", victim.parent)
    before, kept = origin_main(fx), victim.read_bytes()
    for apply in (False, True):
        rec = _record(fx, apply)
        assert (rec["status"], rec["park_reason"]) == ("parked", "outside-record"), rec
        assert victim.read_bytes() == kept
    assert origin_main(fx) == before


def test_a_linked_gitignore_is_never_written_through(tmp_path, ci):
    # .gitignore sits at the tree root, so it has no parent inside the tree; the
    # parent-link case is lstat_inside's, tested above on the same helper.
    fx = _base(tmp_path, amend=False)
    gi = fx["root"] / ".gitignore"
    victim = _outside(tmp_path, "gitignore", gi.read_text())
    _link(gi, victim)
    _push(fx, "linked gitignore")
    _dirty_record(fx)
    before, kept = origin_main(fx), victim.read_bytes()
    for apply in (False, True):
        rec = _record(fx, apply)
        assert (rec["status"], rec["park_reason"]) == ("parked", "outside-record"), rec
        assert rec["outside_record"] == [".gitignore"]
        assert victim.read_bytes() == kept
    assert origin_main(fx) == before


def test_an_ignore_rule_in_a_comment_or_a_longer_pattern_is_still_appended(tmp_path, ci):
    fx = _base(tmp_path, amend=False)
    gi = fx["root"] / ".gitignore"
    gi.write_text(gi.read_text() + "# _plans/*/.lock\n_plans/*/run.ndjson.bak\n"
                  "_plans/*/run_state.json  \n")      # trailing blanks: already there
    _push(fx, "near-miss rules")
    _dirty_record(fx)
    rec = _record(fx)
    assert rec["status"] == "pushed" and ".gitignore" in rec["paths"]
    lines = git(["show", "origin/main:.gitignore"], fx["root"])[1].splitlines()
    for rule in fr._build_plan().GITIGNORE_LINES:
        want = 0 if rule == "_plans/*/run_state.json" else 1
        assert lines.count(rule) == want, (rule, lines)


@pytest.mark.parametrize("case", ["file-became-link", "link-became-file",
                                  "parent-became-link"])
def test_the_checkout_step_never_restores_across_a_link(fx, ci, tmp_path, case):
    ctx = _arm(fx)
    if case == "link-became-file":                 # the target tracks a link
        link = fx["plan"] / "link"
        os.symlink("PLAN.html", link)
        _push(fx, "plan link")
        link.unlink()
        link.write_text("PLAN.html")               # same bytes as the link's text
        target, path, keep = origin_main(fx), link, lambda: not link.is_symlink()
    else:
        _dirty_record(fx)
        rec, target, _ = fr.record(fx["plan"], ctx, origin_main(fx), apply=True,
                                   lease_ok=lambda: True)
        assert rec["status"] == "pushed"
        name = "PLAN.html" if case == "file-became-link" else "sessions"
        path = fx["plan"] / name
        copy = (_outside(tmp_path, name, path.read_text()) if case == "file-became-link"
                else _outside(tmp_path, "s01.md", "closeout\n").parent)
        _link(path, copy)                          # same bytes, reached through a link
        keep = path.is_symlink
    rel = path.relative_to(fx["root"]).as_posix()
    for apply in (False, True):
        co = fr.checkout(ctx, "_plans/plan-a", target,
                         lambda p: fr.blob(ctx["root"], target, p), apply=apply,
                         lease_ok=lambda: True)
        assert co["status"] == "blocked", co
        assert {"path": rel, "why": "differs-from-origin", "plan": "plan-a"} in co["blocking"]
        assert rel not in co["restored"] and keep()
    assert case == "link-became-file" or local_main(fx) != target


# ------------------------------------------- adversarial-review pass 2 (1-3)
def test_a_commit_hook_that_stages_a_symlink_in_the_record_parks(fx, ci):
    hook = fx["root"] / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\nln -s /etc/hosts _plans/plan-a/evil && "
                    "git add _plans/plan-a/evil\n")
    hook.chmod(0o755)
    _arm(fx)
    _dirty_record(fx)
    before = origin_main(fx)
    out = tf.finish.finish(fx["plan"], timeout_s=0)
    rec = out["record"]
    assert (rec["status"], rec["park_reason"]) == ("parked", "outside-record"), out["brief"]
    assert rec["outside_record"] == ["_plans/plan-a/evil"]
    assert "a commit hook changed the record commit" in out["brief"]
    assert "outside the record: _plans/plan-a/evil" in out["brief"]
    assert origin_main(fx) == before


@pytest.mark.parametrize("old", [b"", b"a\n", b"a\n\n", b"a\r\nb\r\n",
                                 b"# _plans/*/.lock\na\n", b"keep\\ "])
def test_finish_and_plan_builder_write_the_same_gitignore_bytes(tmp_path, old):
    bp, a, b = fr._build_plan(), tmp_path / "builder", tmp_path / "finish"
    for d in (a, b):
        d.mkdir()
        if old:
            (d / ".gitignore").write_bytes(old)
    bp.amend_gitignore(a)
    assert fr._gitignore(b) is True
    got = (b / ".gitignore").read_bytes()
    assert got == (a / ".gitignore").read_bytes()
    assert got.startswith(old) and b"\n_plans/*/.lock\n" in got
    assert bp.amend_gitignore(a) is None and fr._gitignore(b) is False   # idempotent


def test_each_refusal_names_its_cause_and_its_path(tmp_path):
    (tmp_path / "d").mkdir()
    (tmp_path / "f").write_text("x")
    os.symlink(tmp_path / "d", tmp_path / "ln")
    os.mkfifo(tmp_path / "fifo")
    cases = {"ln/x": "symlink at ln", "f/x": "f is a file where a directory is needed",
             "fifo": "fifo is not a regular file", "../x": "'..' or absolute path: ../x"}
    for path, detail in cases.items():
        with pytest.raises(fr.Linked) as e:
            fr.lstat_inside(tmp_path, path)
        assert e.value.detail == detail
    with pytest.raises(fr.Linked) as e:
        fr.read_inside(tmp_path, "d")
    assert e.value.detail == "d is a directory where a file is needed"


@pytest.mark.parametrize("case", ["local-link", "file-where-dir"])
def test_a_record_park_reports_the_repo_relative_path(fx, ci, tmp_path, case):
    if case == "local-link":                       # plan-relative link -> repo-relative
        _link(fx["plan"] / "sessions", _outside(tmp_path, "s01.md", "x\n").parent)
        at, detail = "_plans/plan-a/sessions", "symlink at _plans/plan-a/sessions"
    else:                                          # origin: a file; local: a directory
        (fx["plan"] / "sessions").write_text("a file\n")
        _push(fx, "sessions as a file")
        (fx["plan"] / "sessions").unlink()
        _dirty_record(fx)
        at = "_plans/plan-a/sessions"
        detail = f"{at} is a file where a directory is needed"
    before = origin_main(fx)
    rec = fr.record(fx["plan"], _arm(fx), before, apply=True, lease_ok=lambda: True)[0]
    assert (rec["park_reason"], rec["outside_record"], rec["detail"]) == \
        ("outside-record", [at], detail)
    assert origin_main(fx) == before


# ------------------------------------------- adversarial-review pass 3 (1-2)
def test_a_hook_staged_gitlink_parks_even_when_config_hides_submodules(fx, ci):
    git(["config", "diff.ignoreSubmodules", "all"], fx["root"], check=True)
    hook = fx["root"] / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\ngit update-index --add --cacheinfo "
                    "160000,$(git rev-parse HEAD),_plans/plan-a/sub\n")
    hook.chmod(0o755)
    _dirty_record(fx)
    before = origin_main(fx)
    rec = _record(fx)
    assert (rec["status"], rec["park_reason"]) == ("parked", "outside-record")
    assert rec["outside_record"] == ["_plans/plan-a/sub"]
    assert origin_main(fx) == before


def test_a_hook_that_adds_an_extra_commit_parks_before_the_push(fx, ci):
    hook = fx["root"] / ".git" / "hooks" / "post-commit"
    hook.write_text("#!/bin/sh\ngit update-ref HEAD "
                    "$(git commit-tree HEAD^{tree} -p HEAD -m extra)\n")
    hook.chmod(0o755)
    _dirty_record(fx)
    before = origin_main(fx)
    rec = _record(fx)
    assert (rec["status"], rec["park_reason"]) == ("parked", "outside-record")
    assert "a commit hook changed the record commit" in rec["detail"]
    assert "not one commit" in rec["detail"]
    assert origin_main(fx) == before


# ------------------------------------------- one rule: the commit is the staged tree
def _hook(fx, body, name="pre-commit"):
    hook = fx["root"] / ".git" / "hooks" / name
    hook.write_text("#!/bin/sh\n" + body)
    hook.chmod(0o755)


def test_a_hook_that_stages_an_extra_plan_file_parks(fx, ci):
    _hook(fx, "echo extra > _plans/plan-a/extra.txt && git add _plans/plan-a/extra.txt\n")
    _dirty_record(fx)
    before = origin_main(fx)
    rec = _record(fx)
    assert (rec["status"], rec["park_reason"]) == ("parked", "outside-record"), rec
    assert rec["outside_record"] == ["_plans/plan-a/extra.txt"]
    assert "a commit hook changed the record commit" in rec["detail"]
    assert origin_main(fx) == before


def test_a_hook_that_rewrites_and_restages_a_record_file_parks(fx, ci):
    f = "_plans/plan-a/PLAN.html"
    _hook(fx, f"echo hooked >> {f} && git add {f}\n")   # the commit differs from the tree
    _dirty_record(fx)
    before = origin_main(fx)
    rec = _record(fx)
    assert (rec["status"], rec["park_reason"], rec["outside_record"]) == \
        ("parked", "outside-record", [f]), rec
    assert origin_main(fx) == before


# ------------------------------------------- clean filters and failing hooks
def test_a_clean_filter_that_rewrites_another_plans_row_parks(fx, ci):
    (fx["root"] / ".gitattributes").write_text("_plans_index.md filter=x\n")
    _push(fx, "attributes")
    git(["config", "filter.x.clean", "sed s/O,/O-hijacked,/"], fx["root"], check=True)
    _dirty_record(fx)
    before = origin_main(fx)
    rec = _record(fx)
    assert (rec["status"], rec["park_reason"], rec["outside_record"]) == \
        ("parked", "outside-record", ["_plans_index.md"]), rec
    assert origin_main(fx) == before


def test_a_clean_filter_that_rewrites_a_record_file_parks(fx, ci):
    (fx["root"] / ".gitattributes").write_text("*.html filter=x\n")
    _push(fx, "attributes")
    git(["config", "filter.x.clean", "sed s/done/filtered/"], fx["root"], check=True)
    _dirty_record(fx)
    before = origin_main(fx)
    rec = _record(fx)
    assert (rec["park_reason"], rec["outside_record"]) == \
        ("outside-record", ["_plans/plan-a/PLAN.html"]), rec
    assert origin_main(fx) == before


_STRIP = ("f=_plans/plan-a/PLAN.html\n"
          "grep -q ' $' $f || exit 0\n"
          "perl -pi -e 's/ +$//' $f && echo 'fixed trailing whitespace' >&2\n")


def _trailing(fx):
    _dirty_record(fx)
    (fx["plan"] / "PLAN.html").write_text("<html>done</html>   \n")


def test_a_hook_that_fails_once_parks_push_failed_and_is_never_retried(fx, ci):
    _hook(fx, _STRIP + "echo 'still failing' >&2\nexit 1\n")
    _trailing(fx)
    before = origin_main(fx)
    rec = _record(fx)
    assert (rec["status"], rec["park_reason"]) == ("parked", "push-failed"), rec
    assert "still failing" in rec["detail"]
    assert origin_main(fx) == before


def test_a_formatter_that_also_stages_an_extra_file_parks(fx, ci):
    _hook(fx, _STRIP.replace("&& echo", "&& echo x > _plans/plan-a/extra.txt && "
                             "git add _plans/plan-a/extra.txt && echo"))
    _trailing(fx)
    before = origin_main(fx)
    rec = _record(fx)
    assert (rec["status"], rec["park_reason"], rec["outside_record"]) == \
        ("parked", "outside-record", ["_plans/plan-a/extra.txt"]), rec
    assert "a commit hook changed the record commit" in rec["detail"]
    assert origin_main(fx) == before


_DELETE = "f=_plans/plan-a/PLAN.html\n[ -e $f ] || exit 0\nrm $f\n"


@pytest.mark.parametrize("tail,reason", [("git add $f\n", "outside-record"),
                                         ("exit 1\n", "push-failed")])
def test_a_hook_that_deletes_a_record_file_parks(fx, ci, tail, reason):
    # The exit-1 case is the old formatter retry's hole: the retry re-staged the
    # same path NAMES, so a modify turned into a delete was accepted and pushed.
    _hook(fx, _DELETE + tail)
    _dirty_record(fx)
    before = origin_main(fx)
    rec = _record(fx)
    assert (rec["status"], rec["park_reason"]) == ("parked", reason), rec.get("detail")
    assert origin_main(fx) == before
    assert git(["cat-file", "-e", f"{before}:_plans/plan-a/PLAN.html"], fx["root"])[0] == 0


@pytest.mark.parametrize("hook", [None, "echo checked >&2\n"])
def test_a_run_with_no_or_a_harmless_hook_commits_and_pushes(fx, ci, hook):
    if hook:
        _hook(fx, hook)
    _dirty_record(fx)
    rec = _record(fx)
    assert rec["status"] == "pushed", rec
    assert rec["sha"] == origin_main(fx)
    assert git(["show", f"{rec['sha']}:_plans/plan-a/PLAN.html"], fx["root"])[1] == \
        "<html>done</html>"


def test_a_fifo_in_the_plan_folder_parks_and_pushes_nothing(fx, ci):
    _dirty_record(fx)
    os.mkfifo(fx["plan"] / "sessions" / "pipe")
    os.mkfifo(fx["plan"] / "top.fifo")
    before = origin_main(fx)
    for apply in (False, True):
        rec = _record(fx, apply)
        assert (rec["status"], rec["park_reason"]) == ("parked", "record-special-file")
        assert sorted(rec["outside_record"]) == ["sessions/pipe", "top.fifo"]
    assert origin_main(fx) == before


def test_nested_ordinary_directories_record_normally(fx, ci):
    _dirty_record(fx)
    for d in ("_closeouts", "_evidence/deep/er"):
        (fx["plan"] / d).mkdir(parents=True)
    (fx["plan"] / "_evidence/deep/er/x.txt").write_text("x\n")
    rec = _record(fx)
    assert rec["status"] == "pushed", rec
    assert "_plans/plan-a/_evidence/deep/er/x.txt" in rec["paths"]
