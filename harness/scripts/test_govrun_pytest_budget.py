"""govrun's pytest-worker-count checks, split out of test_govrun.py to stay
under the file-size ratchet.

Everything here is about what govrun decides a payload's worker request IS —
argv, `PYTEST_ADDOPTS`, `-o addopts=`, and a config file's own `addopts`, in
pytest's own precedence order — never about the locking/queueing machinery,
which stays in test_govrun.py. See that file's module docstring for the
suite's overall testing philosophy (real processes, no internal-function
proofs, every check paired with a known negative); it applies here too.

Fixtures and helpers (`state`, `fake_pytest`, `govrun`, `govrun_module`, ...)
are shared via conftest.py.
"""
import os
import shlex
import subprocess
import sys

import pytest

from govrun_errors import Refused

from conftest import BUDGET, EXIT_REFUSED, GOVRUN, govrun, govrun_module

# --- the payload's own worker count -----------------------------------------

@pytest.mark.parametrize("flag", [["-n", "8"], ["-n8"], ["-n=8"],
                                  ["--numprocesses", "8"], ["--numprocesses=8"]])
def test_an_over_budget_worker_count_is_refused_in_every_spelling(
        state, flag, fake_pytest):
    """Every spelling argparse accepts, because the exported env var binds
    `-n auto` only: a wrapped `pytest -n 8` would otherwise run 8 workers in a
    4-worker slot and two slots would reach 16 — the incident, through the
    governor. `-n=8` is in here because argparse strips one attached `=`, so it
    really does reach xdist as 8 (measured, see requested_workers).

    The payload was `true -n 8` until review round 3, which was WRONG twice
    over: the check now (correctly) ignores a non-pytest command's `-n`, and
    the old payload proved refusal of a command that never asked for workers.
    """
    result = govrun(state, "--", "pytest", *flag, env=fake_pytest)
    assert result.returncode == EXIT_REFUSED, result.stderr
    assert "-n auto" in result.stderr
    assert str(BUDGET) in result.stderr


@pytest.mark.parametrize("wrapper", [["env", "-i"], ["nice", "-n", "10"],
                                     ["time", "-p"], ["env", "-u", "HOME"]])
def test_a_wrappers_own_options_do_not_hide_the_payload(
        state, wrapper, fake_pytest):
    """`nice -n 10 pytest -n 8` was admitted: the peel skipped the
    word `nice`, read `-n` as the program, and budgeted nothing."""
    result = govrun(state, "--", *wrapper, "pytest", "-n", "8", env=fake_pytest)
    assert result.returncode == EXIT_REFUSED, result.stderr
    assert str(BUDGET) in result.stderr


def test_env_s_nested_too_deep_is_refused_not_allowed():
    """The fail-closed tail: nesting past the resolve bound refuses, rather
    than peeling to an opaque token that skips the budget.

    A faithful `env -S 'env -S "..."'` grows exponentially with the shell
    quoting, so a realistic 18-deep command is far larger than ARG_MAX and
    cannot be built. This flat, unquoted chain drives the SAME resolve loop,
    one link per `env -S`, past its 16-iteration bound, at a few hundred bytes.
    """
    from govrun_pytest_budget import check_budget
    argv = (("env -S " * 18) + "pytest -n 8").split()
    with pytest.raises(Refused):
        check_budget(argv, BUDGET)


def test_env_s_re_splits_its_string_into_the_payload(state, fake_pytest):
    """`env -S 'pytest -n 8'` runs pytest with 8 workers (macOS and GNU env)."""
    for spelling in (["-S", "pytest -n 8"], ["-iS", "pytest -n 8"], ["-Spytest -n 8"],
                     ["-S", "env -S 'pytest -n 8'"]):
        result = govrun(state, "--", "env", *spelling, env=fake_pytest)
        assert result.returncode == EXIT_REFUSED, (spelling, result.stderr)
        assert str(BUDGET) in result.stderr


@pytest.mark.parametrize("value", ["+8", "1_0", " 8 ", "8\n", "٨", "\xa08",
                                   "0000000008", "08"])
def test_an_over_budget_count_is_refused_however_the_number_is_written(
        state, value, fake_pytest):
    """The second defect this file failed to catch, end to end.

    Two earlier versions decided admission by matching digits, and `+8` and
    `1_0` walked straight past both — xdist read them as 8 and 10 and ran that
    many workers inside a 4-worker slot. Those two are the confirmed misses;
    the rest of this list is the neighbourhood around them — `' 8 '`, `'8\\n'`,
    `'٨'` (unicode decimal digits), `'\\xa08'` (non-breaking space), leading
    zeroes — every one a string `int()` accepts, and every one a plausible
    casualty of the next attempt to "just tighten the pattern".

    The first assertion is the known positive: it proves each value really is
    over budget FROM THE CONSUMER'S POINT OF VIEW, so this test cannot pass by
    probing a value that was never a threat.
    """
    assert xdist_numprocesses(["-n", value]) > BUDGET, "this value is the probe"
    result = govrun(state, "--", "pytest", "-n", value, env=fake_pytest)
    assert result.returncode == EXIT_REFUSED, result.stdout + result.stderr
    assert "-n auto" in result.stderr


@pytest.mark.parametrize("flag", [["-n", "4"], ["--numprocesses=4"], ["-n=4"],
                                  ["-n", "auto"], ["-nauto"], ["-n=auto"],
                                  ["-n", "logical"], ["-n", "+4"], ["-n", "0"],
                                  ["-n", "8", "-n", "2"], []])
def test_a_worker_count_within_budget_is_admitted(state, flag, fake_pytest):
    """Including the ones the fix must NOT over-refuse. `auto` and `logical`
    are xdist's own words and are the whole point of the exported budget;
    `-n 8 -n 2` really runs 2 workers, because argparse keeps the last
    occurrence — refusing it on the 8 would refuse a command that fits.

    The payload must be NAMED pytest or the check does not apply at all and
    this test admits vacuously — hence the stub."""
    result = govrun(state, "--", "pytest", *flag, env=fake_pytest)
    assert result.returncode == 0, result.stdout + result.stderr


# The ground truth govrun must mirror, computed below by running the SAME two
# pieces of code the real payload runs. Anything govrun reads differently is a
# spelling that slips past the budget (too lax) or a refusal of a command that
# would have run fine (too strict).
#
# The `expected` column is written out by hand ON PURPOSE. `xdist_numprocesses`
# and govrun both end up calling `int()`, so comparing only those two would
# agree even if `int()` were not the right function at all — the hand-written
# column is the independent check that keeps this differential test from going
# vacuous, and it is where the two defects that got past this file live.
ARGPARSE_TRUTH = [
    # the five spellings argparse binds to this option
    (["-n", "8"], 8), (["-n8"], 8), (["-n=8"], 8),
    (["--numprocesses", "8"], 8), (["--numprocesses=8"], 8),
    # exactly ONE attached `=` is stripped, and only when attached
    (["-n==8"], None), (["-n", "=8"], None), (["--numprocesses==8"], None),
    # `auto`/`logical` are xdist's own words: never a count, always admitted
    (["-n", "auto"], None), (["-n=auto"], None), (["-n", "logical"], None),
    # `+8` and `1_0` are the two the digit matcher actually missed; the rest of
    # this block is the space around them that `int()` also accepts, pinned so
    # the next attempt to narrow the check has nowhere quiet to land
    (["-n", "+8"], 8), (["-n", "1_0"], 10), (["-n", " 8 "], 8),
    (["-n", "8\n"], 8), (["-n", "٨"], 8), (["-n", "\xa08"], 8),
    (["-n", "08"], 8), (["-n+8"], 8), (["-n=+8"], 8),
    (["--numprocesses=1_0"], 10),
    # `int()` refuses these, and so does xdist's — pytest dies before spawning
    (["-n", "8.0"], None), (["-n", "0x8"], None), (["-n", "eight"], None),
    # argparse's `store` keeps the LAST occurrence
    (["-n", "8", "-n", "2"], 2), (["-n", "2", "-n", "8"], 8),
    (["-n", "8", "-n", "auto"], None), (["-n", "auto", "-n", "8"], 8),
    # pytest builds its parser with allow_abbrev=False, so this binds nothing
    (["--numproc", "8"], None),
    # argparse stops reading OPTIONS at the first `--`; what follows is
    # positional (file arguments), never a worker count. Reading past it turned
    # `-n 8 -- -n 2` into an admitted 2 — review round 3's high finding.
    (["-n", "8", "--", "-n", "2"], 8), (["-n", "2", "--", "-n", "8"], 2),
    (["--", "-n", "8"], None),
    # argparse peels a JOINED short-option cluster one character at a time, so
    # `-n` hidden behind other flags still binds — review round 5's high
    # finding, which the bare-`-n` scan admitted as None
    (["-vn8"], 8), (["-xsn8"], 8), (["-vvn8"], 8), (["-vn", "8"], 8),
    (["-vn=8"], 8), (["-vn==8"], None), (["-vn", "1_0"], 10),
    (["-vnauto"], None), (["-xsn", "8", "-n", "2"], 2),
    # ...and the first character that TAKES A VALUE swallows the rest of the
    # token, so this is a `-k` expression named "n8", not a worker request.
    # Reading it as one would refuse a command that runs no workers at all.
    (["-kn8"], None), (["-mn8"], None),
]


def xdist_numprocesses(argv):
    """The worker count the real payload would run with, computed by running
    the real code: argparse configured the way pytest configures it, then
    xdist's own `parse_numprocesses`.

    pytest passes `allow_abbrev=False` (`_pytest/config/argparsing.py:395`) and
    xdist's parser is::

        def parse_numprocesses(s):
            if s in ("auto", "logical"):
                return s
            elif s is not None:
                return int(s)

    A value `int()` rejects returns None here because xdist raises on it while
    pytest is still parsing options — nothing is spawned, so nothing can exceed
    the budget.
    """
    import argparse
    parser = argparse.ArgumentParser(allow_abbrev=False, add_help=False)
    parser.add_argument("-n", "--numprocesses")
    parser.add_argument("-o", "--override-ini", action="append")
    # pytest's other single-dash options, so a JOINED cluster is peeled the way
    # the real parser peels it. Written out here rather than imported from
    # govrun on purpose: if this list came from the code under test, `-kn8`
    # would agree with govrun no matter which of them was wrong.
    for flag in ("-x", "-s", "-l"):
        parser.add_argument(flag, action="store_true")
    for flag in ("-v", "-q"):
        parser.add_argument(flag, action="count")
    for flag in ("-k", "-m", "-c", "-r"):
        parser.add_argument(flag)
    for flag in ("-p", "-W"):
        parser.add_argument(flag, action="append")
    raw = parser.parse_known_args(argv)[0].numprocesses
    if raw is None or raw in ("auto", "logical"):
        return None
    try:
        return int(raw)
    except ValueError:
        return None


@pytest.mark.parametrize("flag,expected", ARGPARSE_TRUTH)
def test_the_real_parsers_read_these_spellings_the_way_we_assume(flag, expected):
    """Pins the assumption itself, so a Python release that moves argparse or
    `int()` turns THIS red rather than quietly making the budget check wrong."""
    assert xdist_numprocesses(flag) == expected


@pytest.mark.parametrize("flag,expected", ARGPARSE_TRUTH)
def test_govrun_reads_a_worker_count_exactly_as_the_real_parsers_do(flag, expected):
    """govrun's admission decision must equal what the payload will really run.

    Reading LOW is a budget bypass; reading HIGH refuses a command that would
    have been fine. Both directions are asserted, on the same table.
    `requested_workers` takes the OPTION STREAM (what follows the pytest
    command), so the table rows are passed as-is.
    """
    assert govrun_module().requested_workers(list(flag)) == expected


def test_the_short_option_table_matches_the_installed_pytest():
    """govrun splits a cluster like `-vn8` using a hardcoded list of which
    single-dash options TAKE A VALUE. That list was read off pytest's own
    parser; this re-derives it, so a pytest release that adds one turns this
    red instead of silently teaching govrun to misread a cluster.

    Skipped rather than failed when the private API moves: govrun does not
    import pytest at runtime and must not start now.
    """
    try:
        from _pytest.config import get_config
        actions = get_config([])._parser.optparser._actions
    except Exception as exc:  # pragma: no cover - depends on pytest internals
        pytest.skip(f"cannot introspect this pytest's parser: {exc}")
    real = {opt[1] for action in actions for opt in action.option_strings
            if len(opt) == 2 and opt.startswith("-") and action.nargs != 0}
    ours = set(govrun_module().VALUE_TAKING_SHORT_OPTS)
    assert real, "the probe found no options at all — it is pointed at nothing"
    assert "n" in ours, "xdist's own -n takes a value"
    assert real <= ours, (
        f"this pytest has value-taking short options govrun does not know: "
        f"{sorted(real - ours)} — a cluster hiding -n behind one of them "
        f"would be misread")


@pytest.mark.parametrize("flag,refused", [
    (["-vn8"], True), (["-xsn8"], True), (["-vn", "8"], True),
    (["-vn=8"], True),
    # the known negatives that keep the cluster split from over-refusing
    (["-vn2"], False), (["-vnauto"], False), (["-kn8"], False),
    (["-vn", "8", "-n", "2"], False),
])
def test_a_joined_short_option_cluster_is_read_end_to_end(
        state, flag, refused, fake_pytest):
    """`pytest -vn8` runs 8 workers — argparse peels the cluster and `-n` takes
    the 8 (pinned in ARGPARSE_TRUTH against argparse itself). Review round 5
    found govrun reading that as "no worker request" and admitting it into a
    4-worker slot.

    `-kn8` is the negative that matters: `-k` swallows the rest of the token,
    so that command asks for NO workers and must run.
    """
    result = govrun(state, "--", "pytest", *flag, env=fake_pytest)
    if refused:
        assert result.returncode == EXIT_REFUSED, result.stdout + result.stderr
        assert "-n auto" in result.stderr
    else:
        assert result.returncode == 0, result.stdout + result.stderr


def test_override_ini_addopts_really_injects_options_into_pytests_own_parse(
        tmp_path):
    """The known positive under the next test: prove the CHANNEL exists, using
    the real pytest.

    xdist is not installed here (the gate runs the bare python3), so `-k`
    stands in for `-n` — the claim being pinned is not about xdist but about
    pytest: that `-o addopts=<text>` reaches the same argparse pass as the
    command line. If a future pytest stops honouring it, this goes red and the
    refusals below become over-refusals rather than staying quietly wrong.
    """
    (tmp_path / "test_probe.py").write_text(
        "def test_one():\n    pass\n\n\ndef test_two():\n    pass\n")
    env = {k: v for k, v in os.environ.items() if k != "PYTEST_ADDOPTS"}

    def collect(*args, **kw):
        return subprocess.run(
            [sys.executable, "-m", "pytest", "--co", "-q",
             "-p", "no:cacheprovider", *args],
            cwd=tmp_path, env={**env, **kw.get("extra_env", {})},
            capture_output=True, text=True, timeout=120).stdout

    assert "test_one" in collect() and "test_two" in collect(), "both collect"
    for spelling in (["-o", "addopts=-k one"], ["-o=addopts=-k one"],
                     ["-so", "addopts=-k one"]):
        out = collect(*spelling)
        assert "test_one" in out and "test_two" not in out, (spelling, out)
    out = collect(extra_env={"PYTEST_ADDOPTS": "-o 'addopts=-k one'"})
    assert "test_one" in out and "test_two" not in out, out
    # last override wins, exactly as govrun assumes
    out = collect("-o", "addopts=-k one", "-o", "addopts=-k two")
    assert "test_two" in out and "test_one" not in out, out


@pytest.mark.parametrize("argv,env_addopts,refused", [
    (["-s", "-o", "addopts=-n8"], None, True),
    (["-o=addopts=-n8"], None, True),
    (["-so", "addopts=-n 8"], None, True),
    (["--override-ini", "addopts=-n8"], None, True),
    (["--override-ini=addopts=-vn8"], None, True),
    ([], "-o 'addopts=-n 8'", True),
    # negatives: within budget, not a worker option at all, overridden by an
    # explicit command-line count (ini addopts go FIRST, so argv wins), and
    # after the terminator where pytest reads it as a filename
    (["-o", "addopts=-n2"], None, False),
    (["-o", "addopts=-x"], None, False),
    (["-o", "addopts=-n8", "-n", "2"], None, False),
    (["--", "-o", "addopts=-n8"], None, False),
])
def test_a_worker_count_hidden_in_override_ini_addopts_is_refused(
        state, argv, env_addopts, refused, fake_pytest):
    """`pytest -s -o addopts=-n8` runs 8 workers with no `-n` in its argv —
    review round 5's second high finding. This is NOT the documented ini limit:
    the override is a token in the argv govrun already holds, so reading it
    costs no rootdir discovery.

    The test above proves the injection channel is real in the installed
    pytest, so these refusals cannot be passing against an imaginary bypass.
    """
    env = dict(fake_pytest)
    if env_addopts:
        env["PYTEST_ADDOPTS"] = env_addopts
    result = govrun(state, "--", "pytest", *argv, env=env)
    if refused:
        assert result.returncode == EXIT_REFUSED, result.stdout + result.stderr
        assert "-n auto" in result.stderr
    else:
        assert result.returncode == 0, result.stdout + result.stderr


def test_the_overridden_addopts_is_prepended_the_way_pytest_prepends_it(
        monkeypatch):
    """pytest builds `<ini addopts> + $PYTEST_ADDOPTS + <command line>` and
    `-o addopts=` replaces the first term, so the injected options must land in
    FRONT — that is what makes an explicit `-n 2` on the command line win."""
    mod = govrun_module()
    monkeypatch.delenv("PYTEST_ADDOPTS", raising=False)
    assert mod.pytest_option_stream(
        ["pytest", "-o", "addopts=-n 8", "-n", "2"]) == \
        ["-n", "8", "-o", "addopts=-n 8", "-n", "2"]
    assert mod.requested_workers(
        mod.pytest_option_stream(["pytest", "-o", "addopts=-n 8", "-n", "2"])
    ) == 2
    monkeypatch.setenv("PYTEST_ADDOPTS", "-o 'addopts=-n 8'")
    assert mod.pytest_option_stream(["pytest"]) == \
        ["-n", "8", "-o", "addopts=-n 8"]
    # unbalanced quotes inside the override: pytest's own shlex.split raises
    # while it is parsing options, so nothing is spawned — dropped, like the
    # same case in PYTEST_ADDOPTS
    monkeypatch.delenv("PYTEST_ADDOPTS", raising=False)
    assert mod.pytest_option_stream(["pytest", "-o", "addopts='unbalanced"]) \
        == ["-o", "addopts='unbalanced"]


# --- the check is scoped to pytest, and reads what pytest reads --------------

def test_another_commands_dash_n_is_not_a_worker_request(state):
    """govrun wraps ARBITRARY commands. `head -n 10` and `echo -n` are those
    commands' own flags and the payloads must RUN — review round 3 found both
    refused, because the scan assumed every `-n` in every argv was xdist's."""
    listing = govrun(state, "--", "head", "-n", "10", str(GOVRUN))
    assert listing.returncode == 0, listing.stderr
    assert listing.stdout.strip(), "head really ran and produced output"
    echoed = govrun(state, "--", "echo", "-n", "8")
    assert echoed.returncode == 0, echoed.stderr
    assert echoed.stdout == "8", "echo really ran, with -n as ITS flag"


@pytest.mark.parametrize("cmd,expected", [
    (["pytest", "-x"], ["-x"]),
    (["/usr/local/bin/pytest", "-n", "8"], ["-n", "8"]),
    (["py.test"], []),
    (["python3", "-m", "pytest", "-x"], ["-x"]),
    (["python3.14", "-W", "error", "-m", "pytest"], []),
    (["python", "-mpytest", "-x"], ["-x"]),
    # A JOINED SHORT CLUSTER. python peels it one character at a time and `-m`
    # swallows the REST of its token, so every one of these runs pytest. A scan
    # for a bare `-m` missed them and `check_budget` admitted 8 workers through a
    # four-worker budget. The hook splits clusters the same way —
    # `test_the_hook_and_the_budget_split_python_clusters` pins the two.
    (["python3", "-qm", "pytest", "-n", "8"], ["-n", "8"]),
    (["python3", "-qmpytest", "-n", "8"], ["-n", "8"]),
    (["python3", "-Bqmpytest", "-n", "8"], ["-n", "8"]),
    # The known negatives: the module is not pytest, and a terminal option makes
    # python print and exit before `-m` ever runs.
    (["python3", "-qmpyTEST", "-n", "8"], None),
    (["python3", "-qV", "-m", "pytest", "-n", "8"], None),
    # A wrapper govrun is routinely handed. The hook peels the same closed list
    # before it decides, so `uv run pytest -n 16` was denied UNWRAPPED and then
    # walked past this budget once govrun wrapped it.
    (["uv", "run", "pytest", "-n", "8"], ["-n", "8"]),
    # `uv run -m pytest`: the runner forwards `-m` to python. Peeling stopped at
    # the `-m` and read it as the command word, so this was ADMITTED untouched
    #. A value-taking runner flag first (`uv run --with X pytest`)
    # stays a documented residual gap.
    (["uv", "run", "-m", "pytest", "-n", "8"], ["-n", "8"]),
    (["poetry", "run", "pytest", "-x"], ["-x"]),
    (["env", "X=1", "nice", "uv", "run", "pytest"], []),
    (["poetry", "add", "pytest"], None),      # `run` is what makes it a runner
    (["uv", "pip", "install", "pytest"], None),
    (["head", "-n", "10", "file"], None),
    (["echo", "-n", "8"], None),
    (["python3", "script.py", "-n", "8"], None),
    (["python3", "-m", "http.server"], None),
    ([], None),
])
def test_only_pytest_shaped_commands_are_scanned(cmd, expected, monkeypatch,
                                                 tmp_path):
    """None means "not pytest — run it untouched". A list means "these are the
    argv tokens pytest's own argparse will see".

    The stream now begins with any ini `addopts` in scope, so this reads from an
    EMPTY directory rather than from wherever the suite happens to be launched —
    otherwise the expected column would be this repo's own pytest.ini. The first
    assertion proves the directory really is empty of pytest config, so the
    table below cannot be passing against an accidental one.
    """
    monkeypatch.delenv("PYTEST_ADDOPTS", raising=False)
    mod = govrun_module()
    assert mod.ini_addopts([], tmp_path) == [], "the base dir must carry no ini"
    assert mod.pytest_option_stream(cmd, tmp_path) == expected


def test_the_option_terminator_ends_the_scan_exactly_as_argparse_does(
        state, fake_pytest):
    """`pytest -n 8 -- -n 2` runs with 8 workers — measured with the real
    pytest: everything after `--` is a FILE argument. Round 3's check read
    past the `--` and admitted this as a 2."""
    refused = govrun(state, "--", "pytest", "-n", "8", "--", "-n", "2",
                     env=fake_pytest)
    assert refused.returncode == EXIT_REFUSED, refused.stdout + refused.stderr
    admitted = govrun(state, "--", "pytest", "-n", "2", "--", "-n", "8",
                      env=fake_pytest)
    assert admitted.returncode == 0, admitted.stdout + admitted.stderr


def test_pytest_addopts_is_read_because_pytest_reads_it(state, fake_pytest):
    """pytest's effective argv is `<ini addopts> + shlex.split($PYTEST_ADDOPTS)
    + <command line>` in one argparse pass (verified in pytest 9 source and
    by live probe). So `PYTEST_ADDOPTS='-n 8' pytest` runs 8
    workers with a clean argv — round 3's bypass. And because argv comes LAST,
    an explicit `-n 2` on the command line overrides the env's 8: refusing that
    would over-refuse. The third source, ini `addopts`, has its own block
    below — it used to be a documented limit and was a `high` finding."""
    refused = govrun(state, "--", "pytest",
                     env={**fake_pytest, "PYTEST_ADDOPTS": "-n 8"})
    assert refused.returncode == EXIT_REFUSED, refused.stdout + refused.stderr
    assert "PYTEST_ADDOPTS" in refused.stderr

    argv_wins = govrun(state, "--", "pytest", "-n", "2",
                       env={**fake_pytest, "PYTEST_ADDOPTS": "-n 8"})
    assert argv_wins.returncode == 0, argv_wins.stdout + argv_wins.stderr

    not_pytest = govrun(state, "--", "echo", "OK",
                        env={"PYTEST_ADDOPTS": "-n 8"})
    assert not_pytest.returncode == 0, "env only matters to a pytest payload"


# --- the third source: a config file's own addopts ---------------------------
#
# Every file pytest will take `addopts` from, with the body that puts a given
# option list into each. Written as a table so the known-positive test below
# proves each one is a REAL channel in the installed pytest, and the refusal
# test proves govrun closes the same list. A pytest release that drops one turns
# the known positive red rather than leaving a quiet over-refusal behind.
#
# `pytest.toml` and `[tool.pytest]` are pytest 9's native-TOML mode, where
# `addopts` is a LIST, not a string; the other TOML table is the older
# ini-in-TOML mode where it is a string. Both spellings are here because govrun
# has to read both.

def _ini_body(section):
    return lambda opts: "[%s]\naddopts = %s\n" % (section, " ".join(opts))


def _toml_string_body(table):
    return lambda opts: '[%s]\naddopts = "%s"\n' % (table, " ".join(opts))


def _toml_list_body(table):
    return lambda opts: "[%s]\naddopts = [%s]\n" % (
        table, ", ".join('"%s"' % opt for opt in opts))


INI_ADDOPTS_FILES = [
    pytest.param("pytest.ini", _ini_body("pytest"), id="pytest.ini"),
    pytest.param(".pytest.ini", _ini_body("pytest"), id="dot-pytest.ini"),
    pytest.param("tox.ini", _ini_body("pytest"), id="tox.ini"),
    pytest.param("setup.cfg", _ini_body("tool:pytest"), id="setup.cfg"),
    pytest.param("pyproject.toml", _toml_string_body("tool.pytest.ini_options"),
                 id="pyproject-ini_options"),
    pytest.param("pyproject.toml", _toml_list_body("tool.pytest"),
                 id="pyproject-native-toml"),
    pytest.param("pytest.toml", _toml_list_body("pytest"), id="pytest.toml"),
]


@pytest.mark.parametrize("filename,body", INI_ADDOPTS_FILES)
def test_an_ini_addopts_really_reaches_pytests_own_parse(tmp_path, filename,
                                                         body):
    """The known positive under the refusals below: prove the CHANNEL exists,
    file by file, using the real pytest.

    xdist is not installed here (the gate runs the bare python3), so `-k` stands
    in for `-n` — the claim being pinned is not about xdist but about pytest:
    that a config file's `addopts` reaches the same argparse pass as the command
    line. The first assertion collects BOTH tests with no config file present,
    so a refusal test below cannot be passing against a channel that was already
    closed.
    """
    (tmp_path / "test_probe.py").write_text(
        "def test_one():\n    pass\n\n\ndef test_two():\n    pass\n")
    env = {k: v for k, v in os.environ.items() if k != "PYTEST_ADDOPTS"}

    def collect():
        return subprocess.run(
            [sys.executable, "-m", "pytest", "--co", "-q",
             "-p", "no:cacheprovider"],
            cwd=tmp_path, env=env, capture_output=True, text=True,
            timeout=120).stdout

    before = collect()
    assert "test_one" in before and "test_two" in before, before

    (tmp_path / filename).write_text(body(["-k", "one"]))
    after = collect()
    assert "test_one" in after and "test_two" not in after, (filename, after)


@pytest.mark.parametrize("filename,body", INI_ADDOPTS_FILES)
def test_a_worker_count_in_a_config_files_addopts_is_refused(
        state, tmp_path, fake_pytest, filename, body):
    """`pytest.ini` with `addopts = -n 8` runs 8 workers with a clean argv and
    an empty environment — review round 6's high finding, and previously a
    DOCUMENTED LIMIT here on the grounds that reading it meant re-implementing
    pytest's rootdir discovery. It does not: rootdir discovery answers "where is
    the project", while the budget only needs "which config file does pytest
    read", which is a fixed list of filenames checked in one directory and then
    its parents.

    The payload's working directory is a throwaway repo, so this cannot be
    answered by the pytest.ini of the repo the suite itself runs in.
    """
    (tmp_path / filename).write_text(body(["-n", "8"]))
    result = govrun(state, "--", "pytest", env=fake_pytest, cwd=tmp_path)
    assert result.returncode == EXIT_REFUSED, result.stdout + result.stderr
    assert "-n auto" in result.stderr


def test_a_config_files_addopts_does_not_over_refuse(state, tmp_path,
                                                     fake_pytest):
    """The negatives that keep the ini read from refusing what would have run.
    Ini `addopts` go FIRST, so an explicit `-n 2` still wins — refusing on the
    ini's 8 there would refuse a command that runs 2 workers."""
    ini = tmp_path / "pytest.ini"

    ini.write_text("[pytest]\naddopts = -n 2\n")
    within = govrun(state, "--", "pytest", env=fake_pytest, cwd=tmp_path)
    assert within.returncode == 0, within.stdout + within.stderr

    ini.write_text("[pytest]\naddopts = -q --strict-markers\n")
    unrelated = govrun(state, "--", "pytest", env=fake_pytest, cwd=tmp_path)
    assert unrelated.returncode == 0, unrelated.stdout + unrelated.stderr

    ini.write_text("[pytest]\naddopts = -n 8\n")
    argv_wins = govrun(state, "--", "pytest", "-n", "2", env=fake_pytest,
                       cwd=tmp_path)
    assert argv_wins.returncode == 0, argv_wins.stdout + argv_wins.stderr

    not_pytest = govrun(state, "--", "echo", "OK", cwd=tmp_path)
    assert not_pytest.returncode == 0, "an ini nearby is not echo's business"

    # ...and the known positive for this whole block: the same directory DOES
    # refuse a payload that reads that ini. Without this the negatives above
    # could all be passing because the ini was never read at all.
    refused = govrun(state, "--", "pytest", env=fake_pytest, cwd=tmp_path)
    assert refused.returncode == EXIT_REFUSED, refused.stdout + refused.stderr


def test_the_config_file_is_searched_the_way_pytest_searches_for_it(
        state, tmp_path, fake_pytest):
    """Two things pytest does that a working-directory-only read would miss.

    A config in a PARENT applies to a run started deeper. And a path ARGUMENT
    moves the search: measured against the real pytest 9 — with a
    `pytest.ini` in both a directory and its `sub/`, plain `pytest` read the top
    one and `pytest sub/` read `sub/pytest.ini`.

    The `[pytest]` file with no `addopts` in `proj/` is not decoration: an empty
    `pytest.ini` is still the source of configuration for pytest and STOPS the
    upward walk, which is what makes the first `proj/` run admit.
    """
    deep = tmp_path / "parent" / "a" / "b"
    deep.mkdir(parents=True)
    (tmp_path / "parent" / "pytest.ini").write_text("[pytest]\naddopts = -n 8\n")
    from_below = govrun(state, "--", "pytest", env=fake_pytest, cwd=deep)
    assert from_below.returncode == EXIT_REFUSED, (
        from_below.stdout + from_below.stderr)

    root = tmp_path / "proj"
    sub = root / "sub"
    sub.mkdir(parents=True)
    (root / "pytest.ini").write_text("[pytest]\n")
    (sub / "pytest.ini").write_text("[pytest]\naddopts = -n 8\n")

    whole = govrun(state, "--", "pytest", env=fake_pytest, cwd=root)
    assert whole.returncode == 0, whole.stdout + whole.stderr
    scoped = govrun(state, "--", "pytest", "sub", env=fake_pytest, cwd=root)
    assert scoped.returncode == EXIT_REFUSED, scoped.stdout + scoped.stderr


def test_an_explicit_config_file_replaces_the_located_one(state, tmp_path,
                                                          fake_pytest):
    """`-c FILE` names the config exactly — measured against the real pytest:
    `-c alt.ini sub/` used alt.ini's `addopts`, not the located file's. So it is
    read INSTEAD of the search, in both directions: it can introduce an
    over-budget count, and it can clear one."""
    (tmp_path / "pytest.ini").write_text("[pytest]\naddopts = -n 8\n")
    (tmp_path / "light.ini").write_text("[pytest]\naddopts = -q\n")
    (tmp_path / "heavy.ini").write_text("[pytest]\naddopts = -n 8\n")

    located = govrun(state, "--", "pytest", env=fake_pytest, cwd=tmp_path)
    assert located.returncode == EXIT_REFUSED, located.stdout + located.stderr

    replaced = govrun(state, "--", "pytest", "-c", "light.ini", env=fake_pytest,
                      cwd=tmp_path)
    assert replaced.returncode == 0, replaced.stdout + replaced.stderr

    (tmp_path / "pytest.ini").write_text("[pytest]\naddopts = -q\n")
    introduced = govrun(state, "--", "pytest", "-c", "heavy.ini",
                        env=fake_pytest, cwd=tmp_path)
    assert introduced.returncode == EXIT_REFUSED, (
        introduced.stdout + introduced.stderr)


def test_an_unparseable_explicit_config_file_is_refused_not_guessed(
        state, tmp_path, fake_pytest):
    """The conservative branch. Everywhere else a file govrun cannot parse is
    skipped, because pytest's own loader raises on it and pytest exits before
    spawning a worker. `-c` is different: it names the ONE file pytest will
    read, so "skip it" would mean admitting a payload whose worker count govrun
    never saw. Over-refusing names its own fix; a bypass is silent."""
    (tmp_path / "broken.toml").write_text("this is not = = toml\n")
    result = govrun(state, "--", "pytest", "-c", "broken.toml", env=fake_pytest,
                    cwd=tmp_path)
    assert result.returncode == EXIT_REFUSED, result.stdout + result.stderr
    assert "broken.toml" in result.stderr


def test_rootdir_does_not_move_the_config_file(state, tmp_path, fake_pytest):
    """`--rootdir` is deliberately ignored, not handled. Read in pytest's
    `findpaths.determine_setup` (it sets rootdir at the END, after the config is
    located) and measured: `--rootdir=<top> sub/` still read `sub/pytest.ini`.
    So a `--rootdir` pointing somewhere else must NOT let an over-budget ini
    through."""
    (tmp_path / "pytest.ini").write_text("[pytest]\naddopts = -n 8\n")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    result = govrun(state, "--", "pytest", "--rootdir", str(elsewhere),
                    env=fake_pytest, cwd=tmp_path)
    assert result.returncode == EXIT_REFUSED, result.stdout + result.stderr


def test_the_stream_is_ini_then_env_then_argv_in_pytests_own_order(
        monkeypatch, tmp_path):
    """pytest concatenates `<ini addopts> + $PYTEST_ADDOPTS + <command line>`
    into ONE argparse pass, so all three appear here in that order and the
    later ones win. This test used to assert the two-term stream, which was
    wrong in exactly the way review round 6 found: the ini term was missing."""
    (tmp_path / "pytest.ini").write_text("[pytest]\naddopts = -n 16\n")
    monkeypatch.setenv("PYTEST_ADDOPTS", "-n 8")
    mod = govrun_module()
    assert mod.pytest_option_stream(["pytest", "-n", "2"], tmp_path) == \
        ["-n", "16", "-n", "8", "-n", "2"]
    assert mod.requested_workers(
        mod.pytest_option_stream(["pytest", "-n", "2"], tmp_path)) == 2
    # unbalanced quotes: pytest's own shlex.split raises during option parsing,
    # so pytest exits before spawning a worker — the env part is dropped here
    # for the same reason an unconvertible -n value is admitted
    monkeypatch.setenv("PYTEST_ADDOPTS", "'unbalanced")
    assert mod.pytest_option_stream(["pytest"], tmp_path) == ["-n", "16"]


# --- a wrapped or env-carried worker count ------------------------------------
def refused(command, budget=BUDGET):
    """Whether `check_budget` refuses `command` at `budget` workers. Written
    against the SHELL SPELLING and split here, because the finding this closes
    was reported with a string where a token list is required — which makes
    `pytest_option_stream` return None for every input, including a plain
    `pytest -n 8`, and turns any repro built on it into noise."""
    from govrun_pytest_budget import check_budget

    try:
        check_budget(shlex.split(command), budget)
    except Refused:
        return True
    return False


@pytest.mark.parametrize("wrapper", [
    "",                    # the plain control: no wrapper at all
    "uv run ",
    "uv run -- ",          # the separator spelling, which uv documents
    "uvx ",                # uv's alias for `uv tool run`, no `run` word
    "uv tool run ",
    "poetry run ",
    "pipenv run ",
    "pdm run ",
    "hatch run ",
    "rye run ",
    "env FOO=1 nice ",
])
def test_a_wrapped_pytest_is_budgeted_like_a_bare_one(wrapper):
    """govrun is handed a whole payload, so a runner in front of pytest is the
    normal case, not an exotic one — a real project's ci.yml spells it `uv run`
    throughout. Every wrapper here ADMITTED `-n 8` at a 4-worker slot
    while the bare form was refused.

    Each form carries its own known negative: the same wrapper asking for `-n 4`
    must be ADMITTED. Without that half, a peel that simply refused everything
    it did not recognise would pass this test."""
    assert refused(f"{wrapper}pytest -n 8"), f"{wrapper!r} walked past the budget"
    assert not refused(f"{wrapper}pytest -n 4"), f"{wrapper!r} over-refuses"


@pytest.mark.parametrize("command,expected", [
    ("pytest -n 8", True),                      # control: the plain spelling
    ("PYTEST_ADDOPTS=-n8 pytest", True),        # runs 8 with an empty argv
    ("PYTEST_ADDOPTS='-n 8' pytest", True),
    ("env PYTEST_ADDOPTS=-n8 pytest", True),    # the assignment can trail a wrapper
    ("uv run -- pytest -vn8", True),            # a cluster behind a runner
    ("PYTEST_ADDOPTS=-n2 pytest", False),       # the VALUE is read, not the name
    ("PYTEST_ADDOPTS=-n8 pytest -n 2", False),  # argv is later, and pytest keeps the last
])
def test_an_inline_pytest_addopts_is_read_like_an_exported_one(command, expected):
    """`PYTEST_ADDOPTS=-n8 pytest` sets the variable for that one command, and
    pytest reads options out of it — so it runs 8 workers with nothing on the
    command line. This layer read the variable only out of its OWN environment,
    so the assignment written in front of the command was invisible and the
    refusal message's claim to cover PYTEST_ADDOPTS was half true.

    The last two rows are the known negatives: a `-n 2` in the assignment and an
    argv `-n 2` that OVERRIDES an assigned `-n 8` (pytest concatenates ini + env
    + argv and keeps the last) both have to stay admitted, or this closes a
    bypass by refusing commands that never overran anything."""
    assert refused(command) is expected


def test_an_inline_assignment_replaces_the_exported_one(monkeypatch):
    """bash's own rule, and the reason the inline value is read INSTEAD of the
    environment rather than as well as it. The control is the same process
    environment with no assignment in front, which must still refuse."""
    monkeypatch.setenv("PYTEST_ADDOPTS", "-n 8")
    assert refused("pytest"), "control: the exported value alone is over budget"
    assert not refused("PYTEST_ADDOPTS=-n2 pytest")
