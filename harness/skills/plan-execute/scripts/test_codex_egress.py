"""data_sensitivity_guard: the content-aware egress scan that gates dispatch.

A working tree carrying a real secret (or a `.env*` file) is DO-NOT-SEND — the
codex command is refused BEFORE construction unless the SSOT carries an
unexpired per-repo opt-in for exactly this tree. Every check FAILS CLOSED, and
every assertion is PAIRED with a control that proves the check can still fail.

Split out of test_codex_dispatch.py. Shared fixtures: codex_helpers.py.

Run: pytest skills/plan-execute/scripts/test_codex_egress.py -q
"""
import json
import os
import re
import subprocess

import pytest

from codex_helpers import (
    _allow_line, _begin, _codex_session, _opt_in_line, _plant_secret,
    egress, make_plan, run,
)

# --------------------------------------------------------------------------
# data_sensitivity_guard, CONTENT-AWARE. The guard used to match
# FILENAME substrings (corpus/creds/credential/secret) and never read a byte:
# it refused a repo over an analysis script called `migrate_corpus.py` while a
# live key in `config.py` sailed past. It now runs `gitleaks` — the scanner
# githooks/pre-commit already uses — over the tree, and keeps only the `.env*`
# filename rule (a `.env` of plain KEY=value trips no content rule).
#
# Every case below is proved in BOTH directions: a plant that must REFUSE and
# the matching control that must PASS. `test_content_scan_can_fail` neuters the
# checker and re-runs the plant, so none of it can go green vacuously.
# --------------------------------------------------------------------------
def _refuses(plan_dir, capsys):
    """Run begin, assert it REFUSED, return the refusal reason for s01."""
    capsys.readouterr()          # drop any earlier command's JSON from the buffer
    with pytest.raises(SystemExit):
        run.cmd_begin(plan_dir, ["s01"])
    out = json.loads(capsys.readouterr().out)
    assert "DO-NOT-SEND" in out["unroutable"]["s01"]
    assert run._statuses(plan_dir)["s01"] == "BLOCKED"
    return out["unroutable"]["s01"]


@pytest.mark.parametrize("plant", ["nested_env", "untracked_secret", "file_symlink",
                                   "dir_symlink", "env_via_symlink"])
def test_egress_guard_refuses_restricted_tree(tmp_path, capsys, ssot, egress_root, plant):
    # PLANT side. Restricted content anywhere in the FULL tree — including
    # git-ignored files and symlink targets, which is exactly what `codex exec`
    # would upload — refuses the dispatch BEFORE any command exists.
    if plant == "nested_env":
        (egress_root / "app" / "config" / ".env.production").parent.mkdir(parents=True)
        (egress_root / "app" / "config" / ".env.production").write_text("KEY=1")
        expect = ".env"
    elif plant == "untracked_secret":
        # NOT a git work tree (pytest's tmp_path never is), so the `.gitignore`
        # sitting here is inert and the FULL-TREE fallback applies — every file
        # is a candidate. The git-scoped behaviour is proved separately, below.
        (egress_root / ".gitignore").write_text("build/\n")
        _plant_secret(egress_root / "build" / "dump.log")
        expect = "dump.log"
    elif plant == "file_symlink":
        outside = _plant_secret(tmp_path / "elsewhere" / "keys.txt")
        (egress_root / "link.txt").symlink_to(outside)
        expect = "link.txt"
    elif plant == "dir_symlink":
        # gitleaks does NOT descend into symlinked directories — run.py walks
        # them itself and hands each out-of-tree target its own scan pass.
        _plant_secret(tmp_path / "elsewhere" / "cleanname" / "config.py")
        (egress_root / "vendored").symlink_to(tmp_path / "elsewhere" / "cleanname",
                                              target_is_directory=True)
        expect = "config.py"
    else:  # env_via_symlink — a benignly-named link whose TARGET is a .env
        (tmp_path / "elsewhere").mkdir(parents=True, exist_ok=True)
        (tmp_path / "elsewhere" / ".env").write_text("K=1")
        (egress_root / "settings").symlink_to(tmp_path / "elsewhere" / ".env")
        expect = ".env"
    ssot("anthropic")
    plan_dir = make_plan(tmp_path, _codex_session())
    reason = _refuses(plan_dir, capsys)
    assert expect in reason, f"refusal must name the offending path: {reason}"


def test_innocent_names_are_not_restricted(tmp_path, capsys, ssot, egress_root):
    # ALLOW CONTROL — the exact false positive that motivated the rewrite. A tree
    # full of scary-SOUNDING filenames with no secret in any of them dispatches.
    # This is a test, not a claim: `migrate_corpus.py` is the file that blocked a
    # real repo under the old substring matcher.
    (egress_root / "migrate_corpus.py").write_text("# rebuild the corpus index\n")
    (egress_root / "creds").mkdir()
    (egress_root / "creds" / "README.md").write_text("How we rotate credentials.\n")
    (egress_root / "secrets_helper.py").write_text("def load_secret(name): ...\n")
    (egress_root / "credential_policy.md").write_text("No secret may be committed.\n")
    ssot("anthropic")
    plan_dir = make_plan(tmp_path, _codex_session())
    by_id, _ = _begin(plan_dir, ["s01"], capsys)
    assert by_id["s01"]["backend"] == "codex"
    run.cmd_release(plan_dir)


def test_removing_the_secret_clears_the_same_tree(tmp_path, capsys, ssot, egress_root):
    # ALLOW CONTROL, same tree, one file different — proves the refusal tracks the
    # CONTENT and not something incidental to the fixture.
    leak = _plant_secret(egress_root / "app" / "config.py")
    ssot("anthropic")
    plan_dir = make_plan(tmp_path, _codex_session())
    assert "config.py" in _refuses(plan_dir, capsys)

    leak.write_text("AWS_ACCESS_KEY_ID = os.environ['AWS_ACCESS_KEY_ID']\n")
    (tmp_path / "p2").mkdir()
    plan_dir2 = make_plan(tmp_path / "p2", _codex_session())
    by_id, _ = _begin(plan_dir2, ["s01"], capsys)
    assert by_id["s01"]["backend"] == "codex"
    run.cmd_release(plan_dir2)


# --------------------------------------------------------------------------
# SCOPE (2026-07-28, second pass). Rule 2 first shipped over the FULL tree and
# was unusable on a real repo: 16 GB / 103,727 files → 6 min 5 s and 826
# findings, every one of them inside git-ignored build output. It now scans the
# REVIEWED SURFACE — git-tracked plus untracked-not-ignored — which measured
# 570 files / 6.4 MB / 1.0 s on the same repo. Rule 1 (`.env*`) stays full-tree.
# --------------------------------------------------------------------------
@pytest.fixture
def git_root(egress_root, monkeypatch):
    """`egress_root`, turned into a git work tree — hermetically. The two
    GIT_CONFIG_* overrides matter: without them a developer's global
    `core.excludesFile` decides which planted file `--exclude-standard` hides,
    and the same test passes on one machine and fails on another."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", os.devnull)
    subprocess.run(["git", "init", "-q"], cwd=str(egress_root), check=True)
    return egress_root


def _git_add(root):
    subprocess.run(["git", "add", "-A"], cwd=str(root), check=True)


def _bulk(path, megabytes=2):
    """Enough git-ignored build output that a whole-tree scan cannot be mistaken
    for a scan of the candidate set (the scope assertion's bound is 2× + 1 MiB)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("// bundled output, nothing secret here\n" * (megabytes * 30000))


def test_git_ignored_build_output_is_not_scanned_but_tracked_source_is(
    tmp_path, capsys, ssot, git_root
):
    # THE PLANT + THE ALLOW CONTROL for the scoping, in one tree.
    #
    # ALLOWED, deliberately: the same synthetic key sitting in a git-IGNORED
    # `dist/` is NOT a refusal. Git-ignored build output is not part of the repo
    # and is not what a reviewer reads or a push ships; before this scoping it was
    # 826 of 826 findings on the operator's real repo and the guard was simply
    # routed around. If a secret really lives in build output, it lives in the
    # source that generated it — which IS scanned, and is the fixable copy.
    (git_root / ".gitignore").write_text("dist/\n")
    _bulk(git_root / "dist" / "bundle.js")
    _plant_secret(git_root / "dist" / "leak.js")
    (git_root / "src").mkdir()
    (git_root / "src" / "config.py").write_text("KEY = os.environ['KEY']\n")
    _git_add(git_root)
    ssot("anthropic")
    plan_dir = make_plan(tmp_path, _codex_session())
    by_id, _ = _begin(plan_dir, ["s01"], capsys)
    assert by_id["s01"]["backend"] == "codex"
    run.cmd_release(plan_dir)

    # REFUSED: the same key in a TRACKED source file. One line of difference.
    _plant_secret(git_root / "src" / "config.py")
    _git_add(git_root)
    (tmp_path / "p2").mkdir()
    assert "config.py" in _refuses(make_plan(tmp_path / "p2", _codex_session()), capsys)


def test_untracked_not_ignored_file_is_part_of_the_surface(
    tmp_path, capsys, ssot, git_root
):
    # A file you just wrote and have not committed is exactly what a session is
    # about to ship. `git ls-files --others --exclude-standard` is half the
    # candidate set for that reason — tracked-only would be a hole you could walk
    # a fresh `notes.py` through.
    _plant_secret(git_root / "notes.py")          # never `git add`ed, never ignored
    ssot("anthropic")
    assert "notes.py" in _refuses(make_plan(tmp_path, _codex_session()), capsys)


def test_compiled_bytecode_is_not_scanned(tmp_path, capsys, ssot, git_root):
    # Two of the three findings on the real repo were `__pycache__/*.pyc` copies of
    # ONE Python docstring. Bytecode is a build product of source we DO scan.
    _plant_secret(git_root / "app" / "__pycache__" / "config.cpython-313.pyc")
    (git_root / "app" / "config.py").write_text("KEY = os.environ['KEY']\n")
    _git_add(git_root)                            # tracked on purpose: the skip is
    ssot("anthropic")                             # ours, not git's, in this tree
    plan_dir = make_plan(tmp_path, _codex_session())
    by_id, _ = _begin(plan_dir, ["s01"], capsys)
    assert by_id["s01"]["backend"] == "codex"
    run.cmd_release(plan_dir)

    # ALLOW CONTROL — the .py the bytecode came from is NOT skipped.
    _plant_secret(git_root / "app" / "config.py")
    _git_add(git_root)
    (tmp_path / "p2").mkdir()
    assert "config.py" in _refuses(make_plan(tmp_path / "p2", _codex_session()), capsys)


def test_scan_scope_assertion_catches_a_widened_scan(
    tmp_path, capsys, ssot, git_root, monkeypatch
):
    # THE TRAP, regression-proved. `gitleaks dir a b c d` takes ONE path argument:
    # the other three are silently dropped and it scans the CWD tree instead —
    # measured 6 min 40 s over 16 GB where the four directories cost 1.8 s. The
    # scan still "ran", still reported, still exited 0. That is a gate that cannot
    # fail, and the only defence is asserting the scanned BYTE COUNT against the
    # surface we selected.
    (git_root / ".gitignore").write_text("dist/\n")
    _bulk(git_root / "dist" / "bundle.js")        # 2 MB of git-ignored output
    (git_root / "app.py").write_text("print('hello')\n")
    _git_add(git_root)
    ssot("anthropic")

    # ALLOW CONTROL FIRST — correctly scoped, the same tree dispatches. Without
    # this the assertion below could be firing on every tree and prove nothing.
    plan_dir = make_plan(tmp_path, _codex_session())
    by_id, _ = _begin(plan_dir, ["s01"], capsys)
    assert by_id["s01"]["backend"] == "codex"
    run.cmd_release(plan_dir)

    # Now reproduce the trap's OUTCOME: hand the pass the whole tree while the
    # candidate set says a few hundred bytes.
    real = egress._scoped_link_tree
    monkeypatch.setattr(
        egress, "_scoped_link_tree",
        lambda root, stack: (str(root),) + tuple(real(root, stack)[1:]),
    )
    (tmp_path / "p2").mkdir()
    reason = _refuses(make_plan(tmp_path / "p2", _codex_session()), capsys)
    assert "WIDENED" in reason, reason


def test_non_git_tree_falls_back_to_the_full_scan(tmp_path, capsys, ssot, egress_root):
    # Requirement stated honestly: with no git index there is no ignore
    # information, so there is nothing to scope BY — scan it all. Such trees are
    # small in practice; the 16 GB tree that motivated the scoping is a git repo
    # whose bulk is ignored. (`egress_root` is a bare tmp dir — no `git init`.)
    assert egress._git_candidates(egress_root) is None
    _plant_secret(egress_root / "deep" / "nested" / "anything.py")
    ssot("anthropic")
    assert "anything.py" in _refuses(make_plan(tmp_path, _codex_session()), capsys)


def test_content_scan_allowlist_clears_one_file_without_a_repo_opt_in(
    tmp_path, capsys, ssot, egress_root
):
    # One reviewed file must not force a whole-repo opt-in. Same fail-closed
    # parsing as egress_opt_ins: expired / commented-out / block-style never allow.
    leak = _plant_secret(egress_root / "fixtures" / "sample_key.py")

    def plan(name):
        """A fresh plan dir per case — a refused session is left BLOCKED."""
        (tmp_path / name).mkdir()
        return make_plan(tmp_path / name, _codex_session())

    ssot("anthropic", allowlist=_allow_line(str(leak)))
    plan_dir = make_plan(tmp_path, _codex_session())
    by_id, _ = _begin(plan_dir, ["s01"], capsys)
    assert by_id["s01"]["backend"] == "codex"
    run.cmd_release(plan_dir)

    # Expired entry is dead.
    ssot("anthropic", allowlist=_allow_line(str(leak), expiry="2020-01-01"))
    assert "sample_key.py" in _refuses(plan("p2"), capsys)

    # Commented-out entry never allows.
    ssot("anthropic", allowlist="      # " + _allow_line(str(leak)).strip())
    assert "sample_key.py" in _refuses(plan("p3"), capsys)

    # Block-style entry deliberately does not parse (fails closed).
    ssot("anthropic", allowlist=(f'      - path: "{leak}"\n'
                                 "        approved_by: test-operator\n"
                                 "        expiry: 2099-01-01"))
    assert "sample_key.py" in _refuses(plan("p4"), capsys)

    # And it clears only THAT file — a second leak elsewhere still refuses.
    ssot("anthropic", allowlist=_allow_line(str(leak)))
    _plant_secret(egress_root / "other.py")
    assert "other.py" in _refuses(plan("p5"), capsys)


def test_repo_gitleaksignore_is_actually_honored(tmp_path, capsys, ssot, egress_root):
    # The SSOT and the contract both said the scanned tree's own `.gitleaksignore`
    # is honored via gitleaks' `-i`. It never was: `-i` matches gitleaks' OWN
    # fingerprints, built from the path it was handed, and run.py hands it
    # ABSOLUTE paths — while `gitleaks protect --staged` (the commit hook, the
    # thing that writes those pins) produces repo-RELATIVE ones. Measured on this
    # repo 2026-07-28: `gitleaks dir .` honors all 16 pins and reports 0 findings;
    # the identical tree scanned by absolute path honors 0 and reports all 16. So
    # every repo carrying a `.gitleaksignore` was permanently DO-NOT-SEND, for a
    # reason no message ever mentioned.
    leak = _plant_secret(egress_root / "fixtures" / "sample_key.py")
    ssot("anthropic")

    def plan(name):
        (tmp_path / name).mkdir()
        return make_plan(tmp_path / name, _codex_session())

    reason = _refuses(plan("p1"), capsys)
    rule, line = re.search(r"gitleaks rule (\S+), line (\d+)\)", reason).groups()
    rel = leak.relative_to(egress_root)

    # A pin for the WRONG FILE must not clear it — the match is still anchored to
    # the path and the rule, never the filename alone.
    (egress_root / ".gitleaksignore").write_text(f"# pinned\nelsewhere.py:{rule}:{line}\n")
    assert "sample_key.py" in _refuses(plan("p2"), capsys)

    # The real fingerprint, in the exact form the commit hook writes, clears it.
    (egress_root / ".gitleaksignore").write_text(f"# pinned\n{rel}:{rule}:{line}\n")
    p3 = plan("p3")
    by_id, _ = _begin(p3, ["s01"], capsys)
    assert by_id["s01"]["backend"] == "codex"
    run.cmd_release(p3)

    # And the SAME pin still clears it after the finding MOVES. A gitleaks
    # fingerprint is line-anchored, so before 2026-08-15 any edit above a
    # reviewed false positive silently unpinned it and stranded the whole tree as
    # DO-NOT-SEND — indistinguishable, in the refusal message, from a fresh
    # secret. That is exactly how one project broke: a commit moved a
    # flagged docstring from line 143 to 142.
    leak.write_text("# five new lines above the finding\n" * 5 + leak.read_text(),
                    encoding="utf-8")
    capsys.readouterr()          # drop cmd_release's JSON; _begin reads the next one
    p4 = plan("p4")
    by_id, _ = _begin(p4, ["s01"], capsys)
    assert by_id["s01"]["backend"] == "codex", (
        f"pin {rel}:{rule}:{line} stopped matching once the finding moved off "
        f"line {line}")
    run.cmd_release(p4)


def test_a_pin_forgives_one_finding_not_the_whole_file(tmp_path, capsys, ssot,
                                                       egress_root):
    # Dropping the LINE from the pin key (so a reviewed false positive survives an
    # edit above it) would otherwise widen one pin into "this rule is forgiven
    # anywhere in this file" — and a real key added beside the reviewed line would
    # ride out to Codex on it. The COUNT is the replacement bound: one pin
    # forgives one finding, and the second finding refuses on its own.
    leak = _plant_secret(egress_root / "fixtures" / "sample_key.py")
    ssot("anthropic")

    def plan(name):
        (tmp_path / name).mkdir()
        return make_plan(tmp_path / name, _codex_session())

    reason = _refuses(plan("p1"), capsys)
    rule, line = re.search(r"gitleaks rule (\S+), line (\d+)\)", reason).groups()
    rel = leak.relative_to(egress_root)
    (egress_root / ".gitleaksignore").write_text(f"# pinned\n{rel}:{rule}:{line}\n")

    # One finding, one pin → clear.
    p2 = plan("p2")
    by_id, _ = _begin(p2, ["s01"], capsys)
    assert by_id["s01"]["backend"] == "codex"
    run.cmd_release(p2)

    # A SECOND, DISTINCT secret in the same file under the same rule → still
    # refuses. This is the case the count bound exists to catch: a real key added
    # beside a reviewed false positive must not ride out on its pin.
    leak.write_text(
        leak.read_text() + f"AWS_SECRET_ID = '{'AKIA' + 'JQZ7RTLMWVBK2N5D'}'\n",
        encoding="utf-8")
    assert "sample_key.py" in _refuses(plan("p3"), capsys)

    # Two pins for the pair forgive both.
    (egress_root / ".gitleaksignore").write_text(
        f"# pinned\n{rel}:{rule}:{line}\n{rel}:{rule}:999\n")
    p4 = plan("p4")
    by_id, _ = _begin(p4, ["s01"], capsys)
    assert by_id["s01"]["backend"] == "codex"
    run.cmd_release(p4)


def test_allowlist_never_clears_a_dot_env(tmp_path, capsys, ssot, egress_root):
    # The `.env*` filename rule is not allowlistable — a .env is a decision, not
    # a guess. Only a whole-repo egress_opt_ins entry clears it.
    env = egress_root / ".env"
    env.write_text("FOO=bar\n")
    ssot("anthropic", allowlist=_allow_line(str(env)))
    assert ".env" in _refuses(make_plan(tmp_path, _codex_session()), capsys)


def test_missing_gitleaks_fails_closed(tmp_path, capsys, ssot, egress_root, monkeypatch):
    # DEPENDENCY CONTROL. With no scanner on PATH the guard cannot know whether
    # the tree is clean, so it must REFUSE — never silently pass, never invent a
    # fallback scanner. The refusal has to name the missing dependency.
    (egress_root / "app.py").write_text("print('hello')\n")   # demonstrably clean tree
    monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))
    ssot("anthropic")
    reason = _refuses(make_plan(tmp_path, _codex_session()), capsys)
    assert "gitleaks" in reason and "PATH" in reason, reason
    assert "brew install gitleaks" in reason, "the refusal must say how to fix it"


def test_content_scan_can_fail(tmp_path, capsys, ssot, egress_root, monkeypatch):
    # FALSIFICATION CONTROL (house pattern: test_schema_hardening.py § 13). Swap
    # the content scan for a pass-through and the SAME planted tree must stop
    # refusing. If it still refuses, the plant test above proves nothing.
    _plant_secret(egress_root / "config.py")
    ssot("anthropic")
    monkeypatch.setattr(egress, "_content_scan", lambda *a, **kw: None)
    plan_dir = make_plan(tmp_path, _codex_session())
    by_id, _ = _begin(plan_dir, ["s01"], capsys)
    assert by_id["s01"]["backend"] == "codex", (
        "with the content scan neutered the planted tree must dispatch — it did not, "
        "so the plant test is not proving the content scan"
    )
    run.cmd_release(plan_dir)


def test_commented_or_out_of_block_opt_in_never_authorizes(tmp_path, capsys, ssot, egress_root):
    # adversarial-review (Codex HIGH): a commented-out entry, or one
    # outside the egress_opt_ins block, previously matched the raw-text regex.
    (egress_root / ".env").write_text("K=1")
    commented = "      # " + _opt_in_line(str(egress_root)).strip()
    ssot("anthropic", opt_ins=commented)
    sessions = [{"id": "s01", "title": "S1", "items": ["i1"], "model": "gpt-5.6-sol"}]
    plan_dir = make_plan(tmp_path, sessions)
    with pytest.raises(SystemExit):
        run.cmd_begin(plan_dir, ["s01"])
    capsys.readouterr()
    assert run._statuses(plan_dir)["s01"] == "BLOCKED"

    # Out-of-block: the same entry appended at the END of the SSOT (under an
    # unrelated top-level key) must not authorize either.
    p = ssot("anthropic")
    p.write_text(p.read_text() + "\nunrelated_block:\n" + _opt_in_line(str(egress_root)) + "\n")
    (tmp_path / "p2").mkdir()
    plan_dir2 = make_plan(tmp_path / "p2", sessions)
    with pytest.raises(SystemExit):
        run.cmd_begin(plan_dir2, ["s01"])
    capsys.readouterr()
    assert run._statuses(plan_dir2)["s01"] == "BLOCKED"


def test_codex_cmd_pins_scanned_root(tmp_path, capsys, ssot, egress_root):
    # The scanned egress root is bound into the command (`cd <root> && ...`) so
    # the scanned tree and the shipped tree are the same path by construction.
    ssot("anthropic")
    sessions = [{"id": "s01", "title": "S1", "items": ["i1"], "model": "gpt-5.6-sol"}]
    plan_dir = make_plan(tmp_path, sessions)
    by_id, _ = _begin(plan_dir, ["s01"], capsys)
    assert by_id["s01"]["codex_cmd"].startswith(f"cd {egress_root} && ")
    run.cmd_release(plan_dir)


def test_egress_opt_in_allows_and_expiry_is_enforced(tmp_path, capsys, ssot, egress_root):
    (egress_root / ".env").write_text("KEY=1")
    sessions = [{"id": "s01", "title": "S1", "items": ["i1"], "model": "gpt-5.6-sol"}]

    # Unexpired per-repo opt-in → dispatches.
    ssot("anthropic", opt_ins=_opt_in_line(str(egress_root)))
    plan_dir = make_plan(tmp_path, sessions)
    by_id, _ = _begin(plan_dir, ["s01"], capsys)
    assert by_id["s01"]["backend"] == "codex"
    run.cmd_release(plan_dir)

    # Expired opt-in → refused again (no env-var bypass exists).
    ssot("anthropic", opt_ins=_opt_in_line(str(egress_root), expiry="2020-01-01"))
    (tmp_path / "p2").mkdir()
    plan_dir2 = make_plan(tmp_path / "p2", sessions)
    with pytest.raises(SystemExit):
        run.cmd_begin(plan_dir2, ["s01"])
    capsys.readouterr()
    assert run._statuses(plan_dir2)["s01"] == "BLOCKED"


def test_restricted_tree_claude_executor_gets_on_box_verification(
    tmp_path, capsys, ssot, egress_root
):
    # Restricted repo whose ACTUAL executor is Claude: no Codex process may start
    # even for VERIFICATION — the on-box disposition is stamped instead.
    _plant_secret(egress_root / "config.py")
    ssot("openai", executor_for="standard_build")
    sessions = [{"id": "s01", "title": "S1", "items": ["i1"], "model": "Sonnet",
                 "task_class": "mechanical"}]  # not opted in → Claude executes
    plan_dir = make_plan(tmp_path, sessions)
    by_id, _ = _begin(plan_dir, ["s01"], capsys)
    m = by_id["s01"]
    assert m["backend"] == "claude"
    assert "codex_cmd" not in m                 # no Codex process, executor OR verifier
    assert m["verifier_family"] == "openai"
    assert m["verifier_mode"] == "on_box_human"
    run.cmd_release(plan_dir)


