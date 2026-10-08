"""Egress fixtures for the CODEX reviewer: what is scanned must be what is sent.

Split from test_codex_review_backend.py at its size bound. The shim, the tree
and plan builders and the autouse fixtures are imported from there, so both
files run the reviewer the same way (importing a fixture registers it here).
"""
# ruff: noqa: F811  (shim and real_text_egress are fixtures imported from test_codex_review_backend)
import shutil
import subprocess

import pytest

import llm_review_gate as g
from test_codex_review_backend import (  # noqa: F401  fixtures register by import
    _REAL_EGRESS,
    _answer,
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
