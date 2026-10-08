"""`codex_review_backend.availability()` — the probe that decides, before any
process is spawned, whether the cross-family reviewer can run at all.

It gets its own file because it needs NONE of the gate machinery: no ledger, no
plan dir, no supervisor poll, no review answer queue. Stand a `codex` on PATH,
call one function, read a two-tuple. Everything here is a KNOWN NEGATIVE or its
paired positive, because a probe that cannot say "no" is not a probe.

The bug this file was written for: `availability()` tested `"logged in" in
status` — and the phrase "not logged in" CONTAINS "logged in", so a box that
said it was logged OUT read as usable. Measured `(True, '')`. The gate would
then spend a review that cannot authenticate and return INDETERMINATE, where
the honest answer is a clean degrade to `on_box_human`.
"""
import os
import shutil
import subprocess

import pytest

import codex_review_backend as crb

#: `login status` answers on STDERR and exits 0 — measured, codex-cli 0.147.0.
#: `--version` answers on stdout. A shim that prints both to stdout is why the
#: stdout-only read survived a green suite for as long as it did.
_SHIM = '''#!/usr/bin/env python3
import sys
argv = sys.argv[1:]
if argv[:1] == ["--version"]:
    print("codex-cli 0.147.0-shim"); raise SystemExit(0)
if argv[:2] == ["login", "status"]:
    {login}
raise SystemExit(0)
'''

_SAYS = {
    # what the shim does for `login status`      -> is the box usable?
    "logged in, on stderr, exit 0":
        ('print("Logged in using ChatGPT", file=sys.stderr); raise SystemExit(0)', True),
    "logged OUT, on stderr, exit 0":
        ('print("Not logged in", file=sys.stderr); raise SystemExit(0)', False),
    "logged OUT the way the CLI phrases it, exit 0":
        ('print("Not logged in \\u00b7 Please run /login", file=sys.stderr); '
         'raise SystemExit(0)', False),
    "silent on both streams, exit 0":
        ('raise SystemExit(0)', False),
    "logged OUT, exit 1":
        ('print("Not logged in", file=sys.stderr); raise SystemExit(1)', False),
}


@pytest.fixture
def codex_says(tmp_path, monkeypatch):
    """-> a callable that stands a `codex` on PATH answering `login status` a
    given way, with SUPERVISOR and SCHEMA left alone so they are never the
    reason a case comes back False."""
    bindir = tmp_path / "bin"
    bindir.mkdir()

    def stand(login_body):
        exe = bindir / "codex"
        exe.write_text(_SHIM.format(login=login_body))
        exe.chmod(0o755)
        crb._version.cache_clear()
        monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
        return crb.availability()

    return stand


@pytest.mark.parametrize("case", list(_SAYS))
def test_availability_believes_the_box_only_when_it_says_it_is_LOGGED_IN(case, codex_says):
    login_body, expected = _SAYS[case]
    ok, why = codex_says(login_body)
    assert ok is expected, f"{case}: availability() said {(ok, why)!r}"
    if not expected:
        assert "not logged in" in why, f"{case}: unhelpful reason {why!r}"


def test_the_substring_trap_is_pinned_and_the_pin_can_FAIL(codex_says):
    """The regression guard, stated as the thing that actually went wrong.

    `"not logged in".lower()` contains `"logged in"`, so a positive-only test
    accepts a logged-OUT box. The first assert keeps this test from going
    VACUOUS: if the phrasing ever changes so that the trap no longer exists,
    the second would pass for a reason unrelated to the fix, and this file
    would report green over a predicate nobody is checking. The other half is
    the neuter probe in _evidence/s01b/neuter-probe.txt, which restores the
    broken predicate and records that this test then FAILS.
    """
    assert "logged in" in "not logged in", "the trap is gone; this test is vacuous"
    ok, why = codex_says(_SAYS["logged OUT, on stderr, exit 0"][0])
    assert not ok, "a box saying 'Not logged in' on stderr, exit 0, read as AVAILABLE"
    assert "'not logged in'" in why, why


def test_the_real_codex_on_this_box_answers_login_status_on_STDERR():
    """Not a shim: the claim the whole stderr read rests on, checked against the
    binary. Skipped where `codex` is absent — never silently assumed."""
    exe = shutil.which("codex")
    if exe is None:
        pytest.skip("no `codex` on PATH")
    p = subprocess.run([exe, "login", "status"], capture_output=True, text=True,
                       timeout=30)
    assert p.returncode == 0, p.stderr
    assert not p.stdout.strip(), f"stdout is no longer empty: {p.stdout!r}"
    assert p.stderr.strip(), "login status answered on NEITHER stream"
