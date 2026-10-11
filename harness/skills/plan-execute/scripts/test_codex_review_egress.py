"""Egress fixtures for the CODEX reviewer: what is scanned must be what is sent.

Split from test_codex_review_backend.py at its size bound. The shim, the tree
and plan builders and the autouse fixtures are imported from there, so both
files run the reviewer the same way (importing a fixture registers it here).
"""
# ruff: noqa: F811  (shim and real_text_egress are fixtures imported from test_codex_review_backend)
import os
import shutil
import subprocess

import pytest

import codex_review_backend as crb
import codex_review_egress as cre
import llm_review_gate as g
from test_codex_review_backend import (  # noqa: F401  fixtures register by import
    _REAL_EGRESS,
    _answer,
    _gate,
    _plan,
    _reviews,
    _tree,
    fast_poll,
    no_egress_block,
    real_text_egress,
    shim,
)


def test_the_tree_guard_scans_the_REVIEWED_tree_not_the_env_override(
        tmp_path, monkeypatch):
    """Codex runs in `--cwd`, so `--cwd` is the tree the guard must clear.

    Before: `PLAN_EXECUTE_EGRESS_ROOT` won, so a clean directory named there
    cleared a reviewed tree holding a `.env`. The filename rule needs no gitleaks,
    and the `.env` assertion cannot be met by a missing-scanner refusal.
    """
    tree = _tree(tmp_path)
    (tree / ".env").write_text("SOMETHING=1\n")
    clean = tmp_path / "clean"
    clean.mkdir()
    monkeypatch.setenv("PLAN_EXECUTE_EGRESS_ROOT", str(clean))
    reason = _REAL_EGRESS(str(tree))
    assert reason and ".env" in reason, reason


def _fake_pat():
    # Built at run time so this source file never carries a token-shaped string.
    import secrets
    import string
    return "ghp" + "_" + "".join(secrets.choice(string.ascii_letters + string.digits)
                                 for _ in range(36))


@pytest.mark.parametrize("carrier", ["removed-from-tree diff line", "intent file"])
def test_a_secret_OUTSIDE_the_tree_scan_never_reaches_codex(
        tmp_path, monkeypatch, shim, real_text_egress, capsys, carrier):
    """The tree is clean NOW, but the review would still send a secret.

    A secret committed at the base and deleted by the session is gone from the
    tree, so the tree scan clears it -- and the diff handed to codex carries it
    on a `-` line. The intent file lives in the plan directory, which the tree
    scan never reads. Both are the bytes codex is sent; both must be scanned.
    """
    tree = tmp_path / "tree"
    tree.mkdir()

    def git(*a):
        return subprocess.run(["git", *a], cwd=tree, check=True, capture_output=True,
                              text=True).stdout.strip()
    git("init", "-q")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    secret = _fake_pat()
    seeded = f'TOKEN = "{secret}"\n' if carrier.startswith("removed") else "TOKEN = 1\n"
    (tree / "a.py").write_text(seeded)
    git("add", "-A")
    git("commit", "-qm", "base")
    base = git("rev-parse", "HEAD")
    (tree / "a.py").write_text("TOKEN = None\n")
    intent = tmp_path / "intent.md"
    intent.write_text(f"Rotated the key {secret}.\n" if carrier == "intent file"
                      else "Rotated the key.\n")
    monkeypatch.setenv("CODEX_SHIM_OUT", _answer([], reviewed=("a.py",)))

    def gate(plan):
        return g.main(["--level", "medium", "--reviewer", "codex", "--cwd", str(tree),
                       "--plan-dir", str(plan), "--session", "s01", "--timeout", "30",
                       "--base", base, "--intent-file", str(intent)])

    rc = gate(_plan(tmp_path))
    out = capsys.readouterr().out
    assert rc == g.INDETERMINATE, out
    assert "DEGRADED_FROM: cross_family" in out and "VERIFIER: on_box_human" in out
    assert _reviews(shim) == [], "a secret-bearing review must not start codex"
    assert secret not in out, "the refusal must not print the secret it refused"

    # PAIRED POSITIVE: the same tree and the same base without the secret is sent.
    if shutil.which("gitleaks") is None:
        pytest.skip("clearing clean text needs gitleaks; the refusal half above ran")
    (tree / "a.py").write_text(seeded.replace(secret, "x"))
    git("commit", "-q", "--allow-empty", "-am", "scrub")
    base = git("rev-parse", "HEAD")
    (tree / "a.py").write_text("TOKEN = None\n")
    intent.write_text("Rotated the key.\n")
    rc2 = gate(_plan(tmp_path, name="plan2"))
    assert rc2 == g.PASS, capsys.readouterr().out
    assert len(_reviews(shim)) == 1


# --------------------------------------------------------------------------
# THE CLEAN COPY (2026-10-11, operator decision). Codex reviews a hard-linked
# copy of the reviewed surface with every `.env*` path and symlink left out, so
# a committed `.env.example` (or a real `.env`) never blocks a review and is
# never sent, in any repo. It replaced a placeholder test on templates that
# failed five review rounds.
# --------------------------------------------------------------------------
def _files(root):
    return sorted(os.path.relpath(os.path.join(d, f), root)
                  for d, _, fs in os.walk(root) for f in fs)


def _env_tree(tmp_path):
    tree = _tree(tmp_path)
    (tree / ".gitignore").write_text(".env\nbuild/\n")
    (tree / ".env.example").write_text("API_KEY=\n")
    (tree / "apps").mkdir()
    (tree / "apps" / ".env.sample").write_text("TOKEN=x\n")
    subprocess.run(["git", "add", "-A"], cwd=tree, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "env"],
                   cwd=tree, check=True)
    (tree / ".env").write_text("API_KEY=real\n")                  # ignored, real
    (tree / "build").mkdir()
    (tree / "build" / "out.js").write_text("// built\n")           # ignored output
    (tree / "new.py").write_text("z = 1\n")                        # untracked, not ignored
    (tree / "link.py").symlink_to(tmp_path / "outside.py")
    (tmp_path / "outside.py").write_text("SECRET = 1\n")
    return tree


def _plan_worktree(main, tmp_path):
    """A git worktree of `main` with one edit — what plan-execute reviews. It holds
    tracked files only, never the ignored real `.env`."""
    wt = tmp_path / "wt"
    subprocess.run(["git", "worktree", "add", "-q", "--detach", str(wt)], cwd=main, check=True)
    (wt / "a.py").write_text("x = 3  # the session's edit\n")
    return wt


def test_a_real_env_beside_the_code_refuses_and_a_plan_worktree_passes(tmp_path, ssot):
    """Security review (HIGH): Codex's read-only sandbox reads any file
    (measured), so a real, untracked `.env` in the ORIGINAL tree is one read away from
    the copy. A committed template is tracked and passes; a plan worktree has no
    ignored files, so it passes."""
    if shutil.which("gitleaks") is None:
        pytest.skip("clearing a tree needs gitleaks")
    ssot("anthropic")
    main = _env_tree(tmp_path)
    (main / "link.py").unlink()
    reason = _REAL_EGRESS(cre.clean_copy(str(main)))
    assert reason and "/.env (a real .env* file" in reason, reason
    assert _REAL_EGRESS(cre.clean_copy(str(_plan_worktree(main, tmp_path)))) is None
    # a COMMITTED real secrets file (not a template name) refuses too: the security
    # review's fail-open — tracked was taken to mean harmless
    for name in (".env", ".envrc", ".env.production"):
        wt2 = tmp_path / f"wt-{name}"
        subprocess.run(["git", "worktree", "add", "-q", "--detach", str(wt2)], cwd=main, check=True)
        (wt2 / name).write_text("TOKEN=real\n")
        subprocess.run(["git", "add", "-f", "--", name], cwd=wt2, check=True)
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "c"],
                       cwd=wt2, check=True)
        reason = _REAL_EGRESS(cre.clean_copy(str(wt2)))
        assert reason and f"/{name} (a real .env* file" in reason, (name, reason)
    # a dated per-repo opt-in for the ORIGINAL tree still overrides it
    from codex_helpers import _opt_in_line
    ssot("anthropic", opt_ins=_opt_in_line(str(os.path.realpath(main))))
    assert _REAL_EGRESS(cre.clean_copy(str(main))) is None


def test_the_clean_copy_holds_the_reviewed_surface_and_no_env_file(tmp_path):
    tree = _env_tree(tmp_path)
    (tree / ".envrc.d").mkdir()
    (tree / ".envrc.d" / "hook.py").write_text("H = 1\n")         # a `.env*` DIRECTORY
    copy = cre.clean_copy(str(tree))
    assert _files(copy) == [".gitignore", "a.py", "b.py", "new.py"], _files(copy)
    assert (tmp_path / "tree" / "a.py").read_text() == open(os.path.join(copy, "a.py")).read()
    # CONTROL: the original still holds everything the copy left out
    assert {".env", ".env.example", "link.py"} <= set(os.listdir(tree))


def test_a_non_git_tree_gets_no_copy_and_keeps_the_strict_rule(tmp_path):
    (tmp_path / "plain").mkdir()
    (tmp_path / "plain" / ".env.example").write_text("A=\n")
    assert cre.clean_copy(str(tmp_path / "plain")) is None
    assert ".env" in _REAL_EGRESS(str(tmp_path / "plain"))


def test_codex_reviews_the_copy_of_a_tree_that_holds_env_files(tmp_path, monkeypatch, shim,
                                                               capsys, ssot):
    """END TO END through the gate, with the REAL tree guard: a plan worktree holding a
    tracked `.env.example` is reviewed, in the copy, with no `.env*` file in it."""
    if shutil.which("gitleaks") is None:
        pytest.skip("the real tree guard needs gitleaks")
    monkeypatch.setattr(crb, "egress_reason", _REAL_EGRESS)
    ssot("anthropic")
    main = _env_tree(tmp_path)
    (main / "link.py").unlink()
    tree = _plan_worktree(main, tmp_path)          # a plan worktree: no ignored real `.env`
    seen, real_spawn = [], crb.spawn

    def spawn(prompt, cwd, *a, **k):
        seen.append((cwd, _files(cwd)))
        return real_spawn(prompt, cwd, *a, **k)
    monkeypatch.setattr(crb, "spawn", spawn)
    monkeypatch.setenv("CODEX_SHIM_OUT", _answer([], reviewed=("a.py",)))
    rc = _gate(tree, _plan(tmp_path))
    cap = capsys.readouterr()
    out = cap.out
    assert "VERIFIER: cross_family" in out and rc == g.PASS, out + cap.err
    cwd, files = seen[0]
    assert os.path.realpath(cwd) != os.path.realpath(tree)
    assert not any(os.path.basename(f).startswith(".env") or f.startswith(".env") for f in files), files
    assert "a.py" in files and len(_reviews(shim)) == 1


def test_a_secret_in_a_reviewed_file_still_blocks_the_copy(tmp_path, monkeypatch, shim, capsys):
    if shutil.which("gitleaks") is None:
        pytest.skip("the content scan needs gitleaks")
    monkeypatch.setattr(crb, "egress_reason", _REAL_EGRESS)
    tree = _env_tree(tmp_path)
    (tree / ".env").unlink()                       # content, not a real `.env`, must refuse
    (tree / "a.py").write_text(f'TOKEN = "{_fake_pat()}"\n')
    rc = _gate(tree, _plan(tmp_path))
    out = capsys.readouterr().out
    assert rc == g.INDETERMINATE and "DEGRADED_FROM: cross_family" in out, out
    assert "a.py" in out and _reviews(shim) == []



# Round-1 review of the clean copy (Codex): three ways an excluded byte could still arrive.
def test_the_copy_is_independent_of_later_writes_to_the_original(tmp_path):
    tree = _env_tree(tmp_path)
    copy = cre.clean_copy(str(tree))
    (tree / "a.py").write_text("x = 'written after the scan'\n")
    assert "after the scan" not in open(os.path.join(copy, "a.py")).read()
    assert os.stat(os.path.join(copy, "a.py")).st_ino != os.stat(tree / "a.py").st_ino


def test_a_symlinked_parent_directory_is_left_out(tmp_path):
    tree = _tree(tmp_path)
    (tree / "config").mkdir()
    (tree / "config" / "settings.txt").write_text("A = 1\n")
    subprocess.run(["git", "add", "-A"], cwd=tree, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "c"],
                   cwd=tree, check=True)
    outside = tmp_path / ".env.production"          # an outside `.env*` directory
    outside.mkdir()
    (outside / "settings.txt").write_text("DB_PASSWORD=spring2026\n")
    shutil.rmtree(tree / "config")
    (tree / "config").symlink_to(outside)           # git still lists config/settings.txt
    copy = cre.clean_copy(str(tree))
    assert "config/settings.txt" not in _files(copy), _files(copy)
    assert "a.py" in _files(copy)                    # CONTROL: the rest is still copied


def test_a_change_to_a_env_file_is_never_sent(tmp_path, monkeypatch, shim, capsys):
    """The copy leaves `.env.example` out, but its diff would still carry the lines."""
    tree = _env_tree(tmp_path)
    (tree / "link.py").unlink()
    (tree / ".env.example").write_text("API_KEY=\nDB_PASSWORD=spring2026\n")
    monkeypatch.setenv("CODEX_SHIM_OUT", _answer([], reviewed=("a.py", "b.py", "new.py")))
    rc = _gate(tree, _plan(tmp_path))
    out = capsys.readouterr().out
    assert rc == g.INDETERMINATE and "DEGRADED_FROM: cross_family" in out, out
    assert ".env.example" in out and _reviews(shim) == []
    assert "spring2026" not in out
    # PAIRED POSITIVE: the same tree with the template back as committed is reviewed
    (tree / ".env.example").write_text("API_KEY=\n")
    assert _gate(tree, _plan(tmp_path, name="plan2")) == g.PASS
    assert len(_reviews(shim)) == 1


def test_a_git_quoted_env_name_is_still_refused(tmp_path, monkeypatch, shim, capsys):
    """Round 2 (Codex): git C-quotes an unusual name (`".env\\tproduction"`), and the
    quote hid the `.env` prefix from the check, so the file's diff reached Codex."""
    tree = _tree(tmp_path)
    odd = tree / ".env\tproduction"
    odd.write_text("A=1\n")
    subprocess.run(["git", "add", "--", odd.name], cwd=tree, check=True)   # a.py/b.py stay the diff
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "c"],
                   cwd=tree, check=True)
    odd.write_text("DB_PASSWORD=spring2026\n")
    monkeypatch.setenv("CODEX_SHIM_OUT", _answer([]))
    rc = _gate(tree, _plan(tmp_path))
    out = capsys.readouterr().out
    assert rc == g.INDETERMINATE and "DEGRADED_FROM: cross_family" in out, out
    assert _reviews(shim) == [] and "spring2026" not in out
    # PAIRED POSITIVE: with that file back as committed, the review runs
    odd.write_text("A=1\n")
    assert _gate(tree, _plan(tmp_path, name="plan2")) == g.PASS
    assert len(_reviews(shim)) == 1


@pytest.mark.parametrize("swap", ["left_in_place", "swapped_back_after_open"])
def test_a_parent_swapped_for_a_link_during_the_copy_is_left_out(tmp_path, monkeypatch, swap):
    """Round 3 (Codex): a race. Between the check and the copy, another process swaps
    a parent directory for a link to an outside `.env*` directory."""
    tree = _tree(tmp_path)
    (tree / "config").mkdir()
    (tree / "config" / "settings.txt").write_text("A = 1\n")
    subprocess.run(["git", "add", "--", "config"], cwd=tree, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "c"],
                   cwd=tree, check=True)
    outside = tmp_path / ".env.production"
    outside.mkdir()
    (outside / "settings.txt").write_text("DB_PASSWORD=spring2026\n")
    real_open = os.open

    def racing_open(path, *a, **k):
        if str(path).endswith("config/settings.txt"):
            os.rename(tree / "config", tree / "config.real")
            os.symlink(outside, tree / "config")              # the swap lands before the open
            try:
                return real_open(path, *a, **k)
            finally:
                if swap == "swapped_back_after_open":         # and is undone right after it
                    os.unlink(tree / "config")
                    os.rename(tree / "config.real", tree / "config")
        return real_open(path, *a, **k)
    monkeypatch.setattr(cre.os, "open", racing_open)
    copy = cre.clean_copy(str(tree))
    assert "config/settings.txt" not in _files(copy), (swap, _files(copy))
    assert "a.py" in _files(copy)                              # CONTROL: the rest is copied
    assert not any("spring2026" in open(os.path.join(copy, f)).read() for f in _files(copy))


def _committed_tree(tmp_path, files):
    tree = _tree(tmp_path)
    for rel, text in files.items():
        (tree / rel).parent.mkdir(parents=True, exist_ok=True)
        (tree / rel).write_text(text)
    subprocess.run(["git", "add", "-f", "--", *files], cwd=tree, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "c"],
                   cwd=tree, check=True)
    return tree


def test_a_template_holding_a_detectable_key_is_real(tmp_path, ssot):
    """Second security review (MEDIUM): the copy leaves `.env.example` out, so the tree
    scan never read it, yet Codex can read the original."""
    if shutil.which("gitleaks") is None:
        pytest.skip("the template scan needs gitleaks")
    ssot("anthropic")
    tree = _committed_tree(tmp_path, {".env.example": f'TOKEN="{_fake_pat()}"\n'})
    assert cre.real_env(str(tree)).endswith(".env.example")
    (tree / ".env.example").write_text("TOKEN=\n")                  # CONTROL: placeholder
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "c"],
                   cwd=tree, check=True)
    assert cre.real_env(str(tree)) is None


def test_reviewing_a_subdirectory_still_sees_the_root_env(tmp_path):
    tree = _committed_tree(tmp_path, {"sub/c.py": "C = 1\n"})
    (tree / ".env").write_text("TOKEN=real\n")                    # in the repo root
    assert cre.real_env(str(tree / "sub")).endswith("/.env")
    (tree / ".env").unlink()
    assert cre.real_env(str(tree / "sub")) is None                 # CONTROL


def test_an_unreadable_directory_refuses(tmp_path):
    tree = _tree(tmp_path)
    locked = tree / "secrets"
    locked.mkdir()
    (locked / "x").write_text("1\n")
    locked.chmod(0o111)                                            # enter, but not list
    try:
        assert "(unreadable directory)" in cre.real_env(str(tree))
    finally:
        locked.chmod(0o755)
    assert cre.real_env(str(tree)) is None                         # CONTROL: readable again


def test_a_change_to_the_pin_file_is_never_sent(tmp_path, monkeypatch, shim, capsys):
    """Operator decision: the tree scan forgives what `.gitleaksignore` pins, so a
    session that edits it could pin its own way past the scan. A person reviews that change."""
    tree = _committed_tree(tmp_path, {".gitleaksignore": "# pins\n"})
    (tree / "a.py").write_text("x = 9\n")
    (tree / ".gitleaksignore").write_text("# pins\na.py:generic-api-key:1\n")
    monkeypatch.setenv("CODEX_SHIM_OUT", _answer([], reviewed=("a.py", "b.py")))
    rc = _gate(tree, _plan(tmp_path))
    out = capsys.readouterr().out
    assert rc == g.INDETERMINATE and "DEGRADED_FROM: cross_family" in out, out
    assert ".gitleaksignore" in out and _reviews(shim) == []
    # PAIRED POSITIVE: the same change without the pin edit is reviewed
    (tree / ".gitleaksignore").write_text("# pins\n")
    assert _gate(tree, _plan(tmp_path, name="plan2")) == g.PASS
    assert len(_reviews(shim)) == 1


def test_scanner_suppression_cannot_come_from_the_change(tmp_path, monkeypatch, shim, capsys):
    """Third security review: a session could silence the scan by editing the repo's
    `.gitleaks.toml` (refused like `.gitleaksignore`) or by an inline `gitleaks:allow`
    (the scan now passes --ignore-gitleaks-allow)."""
    tree = _committed_tree(tmp_path, {"c.py": "C = 1\n"})
    (tree / ".gitleaks.toml").write_text('[extend]\nuseDefault = true\n[allowlist]\npaths = ["a.py"]\n')
    monkeypatch.setenv("CODEX_SHIM_OUT", _answer([]))
    assert _gate(tree, _plan(tmp_path)) == g.INDETERMINATE
    out = capsys.readouterr().out
    assert ".gitleaks.toml" in out and _reviews(shim) == []
    (tree / ".gitleaks.toml").unlink()
    if shutil.which("gitleaks") is None:
        pytest.skip("the inline half needs gitleaks")
    (tree / "a.py").write_text(f'T = "{_fake_pat()}"  # gitleaks:allow\n')
    reason = _REAL_EGRESS(cre.clean_copy(str(tree)))
    assert reason and "a.py" in reason, reason       # the inline allow is not honoured


def test_a_scan_config_that_extends_another_file_refuses(tmp_path):
    """Fourth security review (parser differential): gitleaks loads `[extend] path`, a
    file of any name that a change could edit past the `.gitleaks*` name check."""
    import egress
    tree = _committed_tree(tmp_path, {".gitleaks.toml": '[extend]\nuseDefault = true\n'})
    assert egress._config_extends(str(tree)) is None                # CONTROL
    (tree / ".gitleaks.toml").write_text('[extend]\npath = "conf/extra.toml"\n')
    assert "extends another file" in egress._config_extends(str(tree))
    assert "extends another file" in (egress._find_restricted(str(tree)) or "")   # wired in
    for cfg in ('[Extend]\nPath = "x.toml"\n', '[EXTEND]\nURL = "https://x"\n'):  # case, as gitleaks
        (tree / ".gitleaks.toml").write_text(cfg)
        assert "extends another file" in (egress._config_extends(str(tree)) or ""), cfg
    (tree / ".gitleaks.toml").write_text('[extend\n')                # does not parse
    assert "cannot be read" in egress._config_extends(str(tree))
