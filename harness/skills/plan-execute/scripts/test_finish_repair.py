"""FIX-02 - land and finish run the repo's whitespace fixers on the record first.

    python3 -m pytest -q test_finish_repair.py
The tool lookup is replaced by a stub fixer; one test uses the real pre-commit.
"""

import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import finish_record as fr  # noqa: E402
import finish_repair as frp  # noqa: E402
import plan_record  # noqa: E402
import test_finish as tf  # noqa: E402
from _land_fixture import origin_main  # noqa: E402
from test_finish import _arm, _dirty_record  # noqa: E402
from test_finish_record import _hook, _push, _record  # noqa: E402
from worktree import git  # noqa: E402

fx, ci = tf.fx, tf.ci
REJECT = ("for f in $(git diff --cached --name-only); do "
          "grep -q ' $' \"$f\" && { echo \"trailing $f\"; exit 1; }; done; exit 0\n")
STUB = textwrap.dedent('''\
    import os, sys
    mode = os.environ.get("STUB_MODE", "fix")
    log = os.environ.get("STUB_LOG")
    if log:
        open(log, "a").write(" ".join(sys.argv[1:]) + "\\n")
    if os.environ.get("STUB_TOUCH"):
        open(os.environ["STUB_TOUCH"], "ab").write(b"edited")
    if mode == "slow":
        import time; time.sleep(30)
    if mode == "code3":
        sys.exit(3)
    changed = 0
    files = sys.argv[sys.argv.index("--files") + 1:]
    for f in files:
        d = open(f, "rb").read()
        n = b"\\n".join(x.rstrip(b" \\t") for x in d.split(b"\\n"))
        if n and not n.endswith(b"\\n"):
            n += b"\\n"
        if n != d:
            open(f, "wb").write(n)
            changed = 1
    sys.exit(changed)
    ''')


@pytest.fixture
def stub(tmp_path, monkeypatch):
    script = tmp_path / "stub_fixer.py"
    script.write_text(STUB)
    monkeypatch.setattr(frp, "find_tool", lambda tree: ([sys.executable, str(script)], None))
    monkeypatch.setenv("STUB_LOG", str(tmp_path / "calls.log"))
    return tmp_path / "calls.log"


def _spaces(fx, name="notes.txt", data=b"a  \nb\t \n"):
    _dirty_record(fx)
    f = fx["plan"] / name
    f.write_bytes(data)
    return f


def _blob(fx, f):
    return fr.blob(fx["root"], origin_main(fx), f"_plans/plan-a/{f}")


@pytest.mark.parametrize("variant", ["no-config", "link-config", "no-hook", "no-tool"])
def test_1_no_hook_setup_leaves_trailing_spaces_and_says_why(fx, ci, variant, monkeypatch):
    cfg = fx["root"] / ".pre-commit-config.yaml"
    if variant == "link-config":
        os.symlink(fx["root"] / "_plans_index.md", cfg)
    elif variant != "no-config":
        cfg.write_text("repos: []\n")
    if variant != "no-config":
        _push(fx, "cfg")
    if variant != "no-hook":
        _hook(fx, REJECT)
    if variant == "no-tool":
        monkeypatch.setattr(frp.shutil, "which", lambda n: None)
    f = _spaces(fx)
    ev = fx["plan"] / "ev.json"
    ev.write_bytes(b'{"a": 1}')
    rec = _record(fx)
    assert f.read_bytes() == b"a  \nb\t \n"
    assert ev.read_bytes() == b'{"a": 1}\n'             # end_with_newline still works
    assert frp.note()
    if variant == "no-hook":
        assert rec["status"] == "pushed", rec
        assert _blob(fx, "notes.txt") == b"a  \nb\t \n"
    else:
        assert (rec["status"], rec["park_reason"]) == ("parked", "push-failed"), rec
        assert "record repair:" in rec["detail"]


def test_2_fixed_bytes_reach_folder_and_commit(fx, ci, stub):
    _hook(fx, REJECT)
    f = _spaces(fx)
    os.chmod(f, 0o644)
    rec = _record(fx)
    assert rec["status"] == "pushed", rec
    assert f.read_bytes() == b"a\nb\n" == _blob(fx, "notes.txt")
    assert f.stat().st_mode & 0o777 == 0o644
    assert not [p for p in fx["plan"].parent.iterdir() if p.name.startswith(".repair-")]


def _mini(tmp_path, with_hook=True):
    """A repo with plan _plans/p (dirty) and a second checkout of it as target."""
    root = tmp_path / "mini"
    root.mkdir(parents=True)
    git(["init", "-q", "-b", "main"], root, check=True)
    (root / "_plans" / "p").mkdir(parents=True)
    (root / "_plans" / "p" / "manifest.json").write_text("{}\n")
    git(["add", "-A"], root, check=True)
    git(["-c", "user.email=a@b", "-c", "user.name=n", "commit", "-q", "-m", "i"], root, check=True)
    if with_hook:
        h = root / ".git" / "hooks" / "pre-commit"
        h.write_text("#!/bin/sh\n" + REJECT)
        h.chmod(0o755)
    git(["config", "user.email", "a@b"], root, check=True)
    git(["config", "user.name", "n"], root, check=True)
    target = tmp_path / "target"
    git(["worktree", "add", "-q", "--detach", str(target)], root, check=True)
    (root / "_plans" / "p" / "n.txt").write_bytes(b"x  \n")
    return root, target


def test_2b_record_plan_pushes_fixed_bytes(tmp_path, stub):
    root, target = _mini(tmp_path)
    res = plan_record.record_plan(target, root / "_plans" / "p")
    assert res["status"] == "recorded", res
    assert (root / "_plans" / "p" / "n.txt").read_bytes() == b"x\n"
    assert git(["show", "HEAD:_plans/p/n.txt"], target, strip=False)[1] == "x\n"


def test_3_other_exit_or_timeout_changes_nothing(fx, ci, stub, monkeypatch):
    _hook(fx, REJECT)
    f = _spaces(fx)
    for mode in ("code3", "slow"):
        monkeypatch.setenv("STUB_MODE", mode)
        monkeypatch.setattr(frp, "TIMEOUT", 1)
        rec = _record(fx)
        assert (rec["status"], rec["park_reason"]) == ("parked", "push-failed"), rec
        assert f.read_bytes() == b"a  \nb\t \n"
        assert "record repair:" in rec["detail"]


def test_4_report_only_does_not_run_the_tool(fx, ci, stub):
    f = _spaces(fx)
    assert _record(fx, apply=False)["status"] == "would-push"
    assert not stub.exists() and f.read_bytes() == b"a  \nb\t \n"


def test_5_special_files_are_never_passed(fx, ci, stub, tmp_path):
    _hook(fx, REJECT)
    _spaces(fx)
    (fx["plan"] / "LAND_NOTICE.txt").write_bytes(b"z  \n")
    (fx["plan"] / "run.ndjson").write_bytes(b"z  \n")        # ignored runtime file
    assert _record(fx)["status"] == "pushed"
    called = stub.read_text()
    assert "notes.txt" in called and "LAND_NOTICE" not in called and "run.ndjson" not in called
    root = tmp_path / "unit"
    plan, tree = root / "plan", root / "tree"
    for d in (plan, tree):
        (d / "p").mkdir(parents=True)
        git(["init", "-q"], d, check=True)
    for n in ("manifest.json", "a.txt"):
        (tree / "p" / n).write_bytes(b"q  \n")
        (plan / "p" / n).write_bytes(b"q  \n")
    os.symlink("a.txt", tree / "p" / "lnk")
    stub.unlink()
    got = frp.repair_record(tree, plan / "p", "p", ["p/manifest.json", "p/lnk", "p/a.txt"])
    assert got == ["p/a.txt"]
    assert "manifest" not in stub.read_text() and "lnk" not in stub.read_text()
    assert (plan / "p" / "manifest.json").read_bytes() == b"q  \n"


def _unit(tmp_path):
    plan, tree = tmp_path / "plan" / "p", tmp_path / "tree" / "p"
    for d in (plan, tree):
        d.mkdir(parents=True)
    git(["init", "-q"], tree.parent, check=True)
    for d in (plan, tree):
        (d / "a.txt").write_bytes(b"q  \n")
    return plan, tree.parent


def test_6_races_lease_and_two_processes(fx, ci, stub, tmp_path, monkeypatch):
    plan, tree = _unit(tmp_path)
    monkeypatch.setenv("STUB_TOUCH", str(plan / "a.txt"))        # an editor writes mid-run
    with pytest.raises(frp.Park) as e:
        frp.repair_record(tree, plan, "p", ["p/a.txt"])
    assert e.value.reason == "outside-record" and (plan / "a.txt").read_bytes() .startswith(b"q  \nedited")
    monkeypatch.delenv("STUB_TOUCH")
    (plan / "a.txt").write_bytes(b"q  \n")
    (tree / "p" / "a.txt").write_bytes(b"q  \n")
    with pytest.raises(frp.Park) as e:
        frp.repair_record(tree, plan, "p", ["p/a.txt"], lease_ok=lambda: False)
    assert e.value.reason == "lease-lost" and (plan / "a.txt").read_bytes() == b"q  \n"
    # two real processes of the same plan through record_plan, copy included
    root, target = _mini(tmp_path / "two")
    second = tmp_path / "two" / "target2"
    git(["worktree", "add", "-q", "--detach", str(second)], root, check=True)
    code = ("import sys;sys.path.insert(0,%r);import plan_record as p;"
            "r=p.record_plan(sys.argv[1],%r);print(r['status'])" % (str(SCRIPTS), str(root / "_plans" / "p")))
    env = {**os.environ, "STUB_MODE": "fix"}
    monkeypatch.undo()
    stubdir = tmp_path / "s"
    stubdir.mkdir()
    (stubdir / "sitecustomize.py").write_text(
        "import finish_repair as f, sys\nf.find_tool=lambda t:([sys.executable,%r],None)\n" % str(tmp_path / "stub_fixer.py"))
    env["PYTHONPATH"] = f"{stubdir}{os.pathsep}{SCRIPTS}"
    ps = [subprocess.Popen([sys.executable, "-c", code, str(t)], env=env, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, text=True) for t in (target, second)]
    outs = [p.communicate()[0].strip() for p in ps]
    assert (root / "_plans" / "p" / "n.txt").read_bytes() == b"x\n", outs
    assert all(o.endswith("recorded") for o in outs), outs


def test_6b_two_finish_record_steps_serialise(fx, ci, tmp_path):
    """Two real processes run finish's own locked record step on one plan."""
    _hook(fx, REJECT)
    f = _spaces(fx)
    _arm(fx)
    origin = origin_main(fx)
    script = tmp_path / "stub_fixer.py"
    script.write_text(STUB)
    stubdir = tmp_path / "s"
    stubdir.mkdir()
    (stubdir / "sitecustomize.py").write_text(
        "import finish_repair as f, sys\nf.find_tool=lambda t:([sys.executable,%r],None)\n" % str(script))
    code = ("import sys;sys.path.insert(0,%r);import finish;c=finish.context(sys.argv[1]);"
            "r=finish._locked_record(sys.argv[1],c,sys.argv[2],True,lambda:True)[0];print(r['status'])"
            % str(SCRIPTS))
    env = {**os.environ, "STUB_MODE": "fix", "PYTHONPATH": f"{stubdir}{os.pathsep}{SCRIPTS}"}
    ps = [subprocess.Popen([sys.executable, "-c", code, str(fx["plan"]), origin], env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True) for _ in range(2)]
    raw = [p.communicate()[0].strip() for p in ps]
    outs = sorted(r.splitlines()[-1] for r in raw)
    log = git(["log", "--oneline", "origin/main", "--", "_plans/plan-a/notes.txt"], fx["root"])[1]
    assert len(log.splitlines()) == 1, (raw, log)      # the later process added no commit
    assert outs == ["already-recorded", "pushed"], raw
    assert f.read_bytes() == b"a\nb\n" == _blob(fx, "notes.txt")


def test_7_failed_replace_leaves_original_and_no_temp(tmp_path, stub, monkeypatch):
    plan, tree = _unit(tmp_path)
    monkeypatch.setattr(frp.os, "replace", lambda *a: (_ for _ in ()).throw(OSError("boom")))
    with pytest.raises(frp.Park):
        frp.repair_record(tree, plan, "p", ["p/a.txt"])
    assert (plan / "a.txt").read_bytes() == b"q  \n"
    assert sorted(x.name for x in plan.parent.iterdir()) == ["p"] and \
        sorted(x.name for x in plan.iterdir()) == ["a.txt"]


@pytest.mark.parametrize("mode", ["code3", "fix"])
def test_9_outside_change_is_rolled_back(tmp_path, stub, monkeypatch, mode):
    plan, tree = _unit(tmp_path)
    (tree / "other.txt").write_text("keep\n")
    git(["add", "other.txt"], tree, check=True)
    git(["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "x"], tree, check=True)
    monkeypatch.setenv("STUB_MODE", mode)
    monkeypatch.setenv("STUB_TOUCH", str(tree / "other.txt"))
    if mode == "code3":
        assert frp.repair_record(tree, plan, "p", ["p/a.txt"]) == []
    else:
        with pytest.raises(frp.Park):
            frp.repair_record(tree, plan, "p", ["p/a.txt"])
    assert (tree / "other.txt").read_bytes() == b"keep\n"
    assert (tree / "p" / "a.txt").read_bytes() == b"q  \n" == (plan / "a.txt").read_bytes()
    assert "other.txt" not in git(["status", "--porcelain"], tree)[1]


@pytest.mark.parametrize("mode", ["code3", "fix"])
def test_9b_file_dirty_before_the_fixers_is_restored(tmp_path, stub, monkeypatch, mode):
    plan, tree = _unit(tmp_path)
    (tree / "other.txt").write_text("keep\n")
    git(["add", "other.txt"], tree, check=True)
    git(["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "x"], tree, check=True)
    (tree / "other.txt").write_bytes(b"dirty\n")
    monkeypatch.setenv("STUB_MODE", mode)
    monkeypatch.setenv("STUB_TOUCH", str(tree / "other.txt"))
    if mode == "code3":
        assert frp.repair_record(tree, plan, "p", ["p/a.txt"]) == []
    else:
        with pytest.raises(frp.Park):
            frp.repair_record(tree, plan, "p", ["p/a.txt"])
    assert (tree / "other.txt").read_bytes() == b"dirty\n"


def test_8_second_run_changes_nothing(tmp_path, stub):
    plan, tree = _unit(tmp_path)
    assert frp.repair_record(tree, plan, "p", ["p/a.txt"]) == ["p/a.txt"]
    ino = (plan / "a.txt").stat().st_ino
    assert frp.repair_record(tree, plan, "p", ["p/a.txt"]) == []
    assert (plan / "a.txt").stat().st_ino == ino


@pytest.mark.skipif(not shutil.which("pre-commit"), reason="pre-commit is not installed")
def test_real_tool_with_the_real_hook(fx, ci):
    (fx["root"] / ".pre-commit-config.yaml").write_text(
        "repos:\n- repo: https://github.com/pre-commit/pre-commit-hooks\n  rev: v4.5.0\n"
        "  hooks:\n  - id: trailing-whitespace\n  - id: end-of-file-fixer\n")
    _push(fx, "cfg")
    subprocess.run(["pre-commit", "install"], cwd=fx["root"], check=True, capture_output=True)
    f = _spaces(fx, data=b"a  \r\nb\n\n\n")
    crlf = fx["plan"] / "crlf.txt"
    crlf.write_bytes(b"x \r\ny\r\n")
    nul = fx["plan"] / "bin.dat"
    nul.write_bytes(b"\0ab  \n")
    rec = _record(fx)
    # detail first: a 2026-11-08 full-suite failure (push-failed, not reproduced alone)
    # lost its cause when pytest truncated the whole record
    assert rec["status"] == "pushed", (rec.get("park_reason"), rec.get("detail"))
    assert f.read_bytes() == _blob(fx, "notes.txt")
    assert f.read_bytes().endswith(b"b\n") and b" \r" not in f.read_bytes()
    assert nul.read_bytes() == b"\0ab  \n" == _blob(fx, "bin.dat")


def test_10_second_write_failure_restores_the_first(tmp_path, stub, monkeypatch):
    plan, tree = _unit(tmp_path)
    for d in (plan, tree / "p"):
        (d / "b.txt").write_bytes(b"z  \n")
    real, n = frp.os.replace, []

    def flaky(a, b):
        n.append(1)
        if len(n) == 2:
            raise OSError("boom")
        return real(a, b)
    monkeypatch.setattr(frp.os, "replace", flaky)
    with pytest.raises(frp.Park):
        frp.repair_record(tree, plan, "p", ["p/a.txt", "p/b.txt"])
    assert (plan / "a.txt").read_bytes() == b"q  \n" and (plan / "b.txt").read_bytes() == b"z  \n"


def test_11_quoted_install_python_with_spaces(tmp_path, monkeypatch):
    py = tmp_path / "a b" / "python"
    py.parent.mkdir()
    py.write_text("#!/bin/sh\n")
    py.chmod(0o755)
    tree = tmp_path / "r"
    (tree / ".git" / "hooks").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(tree)], check=True)
    (tree / ".pre-commit-config.yaml").write_text("repos: []\n")
    (tree / ".git" / "hooks" / "pre-commit").write_text(f"INSTALL_PYTHON='{py}'\n")
    argv, why = frp.find_tool(tree)
    assert argv == [str(py), "-m", "pre_commit"], why


def test_12_generated_index_row_is_fixed_before_the_hook(fx, ci, stub):
    """The commit also stages the index, which has no copy in the plan folder."""
    idx = fx["root"] / fr.INDEX
    idx.write_text(idx.read_text() + "- [other](_plans/other/PLAN.html) \n")
    _push(fx, "another plan's row ends in a space")
    _hook(fx, REJECT)
    _dirty_record(fx)
    mark = fr.marker("plan-a")
    idx.write_text("".join(ln.rstrip("\n") + "  \n" if mark in ln else ln
                           for ln in idx.read_text().splitlines(keepends=True)))
    ctx = _arm(fx)
    rec = fr.record(fx["plan"], ctx, origin_main(fx), apply=True, lease_ok=lambda: True)[0]
    assert rec["status"] == "pushed", rec
    target = origin_main(fx)
    got = fr.blob(fx["root"], target, fr.INDEX)
    assert mark.encode() in got and b"  \n" not in got and b"other](_plans/other/PLAN.html)\n" in got
    co = fr.checkout(ctx, "_plans/plan-a", target, lambda p: fr.blob(ctx["root"], target, p),
                     apply=True, lease_ok=lambda: True)
    assert co["status"] == "fast-forwarded", co       # the owner's copy with the space blocks nothing
    assert idx.read_bytes() == got


def test_12b_only_line_and_file_ends_count_as_the_same_index():
    assert frp.only_ends_differ(b"a  \nb\n\n", b"a\nb\n")
    assert not frp.only_ends_differ(b"a b\n", b"ab\n")
    assert not frp.only_ends_differ(None, b"a\n")
