"""govrun's read of the pytest worker count a payload will actually run with.

Split out of govrun.py, which this machinery (plus govrun_pytest_ini.py, its
ini-file-reading half) pushed over the file-size ratchet. See govrun.py's own
module docstring for what govrun is and why.

This module decides whether a payload IS pytest at all, assembles the option
stream pytest will actually parse — command line, `PYTEST_ADDOPTS`,
`-o addopts=`, and (via `govrun_pytest_ini.ini_addopts`) an ini file's own
`addopts`, in pytest's own precedence order — and reads how many workers that
stream asks for. `check_budget` is the one entry point govrun.py calls.
"""
import os
import re
import shlex

from govrun_errors import Refused
from govrun_pytest_ini import VALUE_TAKING_SHORT_OPTS, ini_addopts

# --- the payload's own worker count -----------------------------------------

def worker_count(value):
    """The number xdist will read from `value`, or None if it will not read one.

    THIS RUNS THE CONSUMER'S OWN CONVERSION instead of imitating it. xdist parses
    `-n` with (`src/xdist/plugin.py`, master, checked)::

        def parse_numprocesses(s):
            if s in ("auto", "logical"):
                return s
            elif s is not None:
                return int(s)

    — so anything that is not exactly `auto` or `logical` goes through a bare
    `int()`. `int()` is much wider than any digit pattern: measured here on
    Python, `int('+8')`=8, `int('1_0')`=10 (PEP 515 underscores),
    `int(' 8 ')`=8, `int('8\\n')`=8, `int('٨')`=8 (unicode decimal digits) and
    `int('\\xa08')`=8 (non-breaking space). Two earlier versions of this check
    matched digits instead — first with `str.isdigit()`, then with a widened
    pattern — and each time a spelling walked past it, because a pattern is a
    blocklist against an input space the consumer defines and we do not.
    Calling the consumer's own function has no spelling left to miss, and
    `str.isdigit()` was never even the right predicate: `'²'.isdigit()` is True
    while `int('²')` raises, and `int(' 8 ')` succeeds while `' 8 '.isdigit()`
    is False. Digit-classification and int-conversion are different functions.

    A value `int()` REFUSES is admitted, deliberately. `auto` and `logical` are
    legal and must stay admitted — they are the whole point, since `auto` reads
    the budget we export. Any other unconvertible value makes xdist's own
    `int(s)` raise while pytest is still parsing its options, so it exits
    before starting a single worker and cannot exceed the budget. Admitting it
    costs a slot for the microsecond pytest takes to die; refusing it would
    mean re-deriving "which strings does int() reject", i.e. the same mistake
    in the mirror.
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# Interpreter names that can carry `-m pytest`. `python`, `python3`,
# `python3.14` — a version suffix, nothing else.
PYTHON_RE = re.compile(r"^python[\d.]*$")
def python_m_pytest_args(cmd):
    """pytest's own arguments when `cmd` is `python [opts] -m pytest …`, else None.

    A BARE `-m` SCAN IS NOT ENOUGH: python peels a joined short cluster one char at
    a time, `-m` swallows the REST of its token, and so `-qm pytest`, `-qmpytest`
    and `-Bqmpytest` each ran 8 workers through a four-worker budget."""
    args, index = list(cmd[1:]), 0
    while index < len(args):
        token = args[index]
        if token == "-m":  # the module is the NEXT token
            return list(args[index + 2:]) if args[index + 1:index + 2] == ["pytest"] else None
        if token.startswith("-m") and len(token) > 2:  # `-mpytest`, attached
            return list(args[index + 1:]) if token[2:] == "pytest" else None
        # These exit before `-m`; a bare word is a script and `-c` is code — in
        # every one of them python ITSELF is the job, not pytest.
        if token in ("-V", "--version", "-h", "--help", "--") or \
                not token.startswith("-") or token.startswith("-c"):
            return None
        if token in ("-W", "-X", "--check-hash-based-pycs"):
            index += 2  # a separated value: `-W ignore`
        elif token.startswith("--") or len(token) == 2:
            index += 1  # a long option, or a lone short flag
        else:  # a cluster: `-qm`, `-Bqm`, `-BqW`. The FIRST value letter ends it
            body = token[1:]
            cut = next((p for p, c in enumerate(body) if c in "mcWX"), len(body) - 1)
            args[index:index + 1] = ["-" + c for c in body[:cut + 1]] + (
                [body[cut + 1:]] if body[cut + 1:] else [])
    return None
# Wrappers a pytest run can hide behind, and env assignments that lead it. This
# is the SAME closed list the hook peels (`peel` in hooks/governor-hook.py) and
# the two have to stay in step: govrun is handed a whole payload, so
# `govrun -- uv run pytest -n 16` arrives here as `['uv', 'run', 'pytest', ...]`.
# The program check below reads the FIRST word only, so every wrapped run walked
# past the budget while the hook refused the unwrapped one.
# `test_the_hook_and_the_budget_peel_the_same_wrappers` pins the two tables.
ENV_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
TRANSPARENT = {"env", "nice", "time"}
# Runner front-ends, each of which takes a `run` word before the real payload.
# A table with two entries in it was itself the bypass: `uvx pytest -n 8`,
# `uv run -- pytest -n 8`, `uv tool run pytest -n 8` and the pipenv/pdm/hatch/rye
# spellings of `<runner> run` were all ADMITTED at a 4-worker slot
# while plain `pytest -n 8` was refused. uv is a common runner.
RUNNERS = {"uv", "poetry", "pipenv", "pdm", "hatch", "rye"}
# `uvx` is uv's documented alias for `uv tool run` and takes NO subcommand word.
RUNNERS_ALONE = {"uvx"}
# RESIDUAL GAP, deliberate: a FLAG between the runner word and the payload
# (`uv run --with pytest-xdist pytest -n 8`) stays opaque. Skipping flags blind
# would land on the value of a value-taking one — `--with pytest-xdist` reads as
# the payload — so this peels only the spellings whose length is unambiguous.
PYTEST_ADDOPTS = "PYTEST_ADDOPTS="


def runner_words(name, rest):
    """How many tokens the runner front-end spelled `name` occupies, or None.

    `uv`/`poetry`/… count only in front of `run` (or `tool run`), so
    `poetry add pytest` and `uv pip install pytest` keep their own first word
    and are not read as a pytest run.
    """
    if name in RUNNERS_ALONE:
        return 1
    if name in RUNNERS:
        if rest[:2] == ["tool", "run"]:
            return 3
        if rest[:1] == ["run"]:
            return 2
    return None


# Options the transparent wrappers take in FRONT of the payload. `env -i`,
# `nice -n 10` and `time -p` are all valid, and a peel that skipped only the
# wrapper WORD stopped at the option and never saw the pytest behind it
# (all three ALLOWED). The value-taking ones need their value
# skipped too; `--` ends the wrapper's options, as it does for the runners.
WRAPPER_VALUE_OPTS = {"-u", "--unset", "-C", "--chdir", "-P", "-n",
                      "--adjustment", "-o"}
# The same options as single letters, for CLUSTERS: `env -iu HOME`, `time -ao
# log`, `nice -n10`. In a cluster the first value letter ends the walk and its
# value is the rest of the token, or the next token when nothing is attached
# (`env -iS 'pytest -n 8'` and `env -S'pytest -n 8'` both run pytest,
# measured). `S` is env's split-string: `resplit_env_s` turns it into words.
WRAPPER_VALUE_LETTERS = "uCPnoS"


def option_width(args, index):
    """How many tokens the wrapper option at `args[index]` occupies: 1, or 2
    when it takes a value that is not attached."""
    option = args[index]
    if option.startswith("--"):
        return 2 if option in WRAPPER_VALUE_OPTS and "=" not in option else 1
    for position, letter in enumerate(option[1:], 2):
        if letter in WRAPPER_VALUE_LETTERS:
            return 1 if option[position:] else 2
    return 1


def wrapper_options(args):
    """How many leading tokens of `args` are a transparent wrapper's OWN
    options, so the payload starts after them."""
    index = 0
    while index < len(args) and args[index].startswith("-"):
        if args[index] == "--":
            return index + 1
        index += option_width(args, index)
    return index


ENV_S_UNRESOLVED = "\x00govrun-env-s-unresolved"  # fail-closed marker: env -S nested deeper than resplit resolves


def resplit_env_s(tokens):
    """`env -S 'pytest -n 8'` runs pytest with 8 workers: `-S` re-splits its
    string (macOS and GNU env, measured), and so do `env -iS '…'`
    and the glued `env -S'…'`. Put the words back where the string was, so the
    payload is visible to the same scan as `env pytest`. Only env's OWN option
    slot is read; a `-S` later in the line is not env's. The split can expose
    ANOTHER `env -S` (`env -S 'env -S "pytest -n 8"'` runs pytest too), so it
    repeats until nothing changes, bounded so a pathological line cannot spin."""
    for _ in range(16):
        expanded = _resplit_env_s_once(tokens)
        if expanded == tokens:
            return tokens
        tokens = expanded
    # Deeper env -S nesting than we will resolve. Return the partial tokens and
    # a `pytest` could hide one more layer down, so FAIL CLOSED: a marker the
    # decision path refuses, instead of an opaque allow (review).
    return [ENV_S_UNRESOLVED]


def _resplit_env_s_once(tokens):
    index = 0
    while index < len(tokens):
        if os.path.basename(tokens[index]) != "env":
            index += 1
            continue
        index += 1
        while index < len(tokens) and tokens[index].startswith("-"):
            option = tokens[index]
            if option == "--":
                break
            if option.startswith("--split-string"):
                attached = option[len("--split-string"):]
                string, width = (attached[1:], 1) if attached.startswith("=") else (
                    tokens[index + 1] if index + 1 < len(tokens) else "", 2)
                return tokens[:index] + shlex.split(string) + tokens[index + width:]
            if not option.startswith("--"):
                for position, letter in enumerate(option[1:], 2):
                    if letter == "S":
                        attached = option[position:]
                        string, width = (attached, 1) if attached else (
                            tokens[index + 1] if index + 1 < len(tokens) else "", 2)
                        return tokens[:index] + shlex.split(string) + tokens[index + width:]
                    if letter in WRAPPER_VALUE_LETTERS:
                        break
            index += option_width(tokens, index)
    return tokens


def peel_wrappers(cmd):
    """`(cmd without its leading wrappers, the inline PYTEST_ADDOPTS or None)`.

    A `PYTEST_ADDOPTS=…` written IN FRONT of the command sets that variable for
    this command only, overriding any exported one, and pytest reads it — so
    `PYTEST_ADDOPTS=-n8 pytest` runs 8 workers with an empty command line. It is
    returned rather than dropped, because dropping it is what let that spelling
    through both this budget and the hook.
    """
    index, addopts = 0, None
    cmd = resplit_env_s(list(cmd))
    while index < len(cmd):
        token = cmd[index]
        name = os.path.basename(token)
        if ENV_ASSIGNMENT.match(token):
            if token.startswith(PYTEST_ADDOPTS):
                addopts = token[len(PYTEST_ADDOPTS):]  # a later one wins
            index += 1
            continue
        if name in TRANSPARENT:
            index += 1 + wrapper_options(cmd[index + 1:])
            continue
        step = runner_words(name, cmd[index + 1:])
        if step is None:
            break
        index += step
        # `uv run -- pytest`: the separator ends the RUNNER's own options, so
        # the payload starts after it, not at it.
        if cmd[index:index + 1] == ["--"]:
            index += 1
        # `uv run -m pytest`: the runner forwards `-m` to python, so `pytest`
        # is the payload. A value-taking runner flag before the payload
        # (`uv run --with X pytest`) stays a documented residual gap.
        if cmd[index:index + 1] == ["-m"]:
            index += 1
    return cmd[index:], addopts


def expand_short_clusters(opts):
    """Rewrite joined short options into one token per option, the way
    argparse reads them: `-vn8` -> `-v -n 8`, `-xsn8` -> `-x -s -n 8`.

    This is review round 5's high finding. `pytest -vn8` really does reach
    xdist with 8 workers — argparse peels a cluster one character at a time,
    and the first character that takes a value swallows the REST of the token
    (measured against argparse itself; the differential table pins it). The
    scan before this one only looked at a bare `-n`/`-n8`, so `-vn8` walked
    past the budget.

    Doing it here rather than inside the `-n` scan is what also closes `-o`:
    `pytest -so addopts=-n8` hides an `-o` in a cluster too, and after this
    pass both scans see a plain token. The pass is idempotent — an already
    expanded stream contains no cluster — and it is deliberately blind to
    which option it is expanding, so there is one model of argparse here, not
    two.
    """
    out = []
    for arg in opts:
        if len(arg) > 2 and arg[0] == "-" and arg[1] != "-":
            out.extend(_split_cluster(arg))
        else:
            out.append(arg)
    return out


def _split_cluster(arg):
    out = []
    body = arg[1:]
    for i, char in enumerate(body):
        out.append("-" + char)
        if char in VALUE_TAKING_SHORT_OPTS:
            value = body[i + 1:]
            if value.startswith("="):
                value = value[1:]  # argparse strips exactly one attached `=`
            if value:
                out.append(value)
            break
    return out


def override_addopts(opts):
    """The `addopts` text injected by `-o addopts=...`, or None.

    Review round 5's second high finding. `-o`/`--override-ini` overrides an
    ini value FOR THE RUN, and pytest reads `addopts` back out of the
    overridden config before it parses options, so `pytest -s -o addopts=-n8`
    really runs 8 workers with no `-n` anywhere in its argv. Measured against
    the real pytest 9 with `-k` standing in for `-n` (xdist
    is not installed here): `pytest --co -q -o 'addopts=-k one'` collected 1 of
    2 tests, and so did the same override written `-so 'addopts=...'`, written
    `-o=addopts=...`, and carried in `PYTEST_ADDOPTS`.

    This is NOT the documented ini limit. Reading a pytest.ini would mean
    re-implementing rootdir discovery; reading `-o` means reading an argument
    that is already in the argv in front of us.

    LAST override wins, because `_get_override_ini_value` keeps the last
    matching entry (measured: `-o addopts=-k one -o addopts=-k two` selected
    `two`). STOPS AT `--` for the same reason the worker scan does — measured:
    `pytest --co -q -- -o addopts=-k one` treated `-o` as a filename.
    """
    value = None
    i = 0
    while i < len(opts):
        arg, raw = opts[i], None
        if arg == "--":
            break
        if arg in ("-o", "--override-ini") and i + 1 < len(opts):
            raw = opts[i + 1]
            i += 1
        elif arg.startswith("--override-ini="):
            raw = arg.split("=", 1)[1]
        if raw is not None and raw.startswith("addopts="):
            value = raw.split("=", 1)[1]
        i += 1
    return value


def _most_demanding(candidates, stream):
    """`stream` with the ini `addopts` that asks for the most workers in front.

    Ini `addopts` go FIRST — ahead of `PYTEST_ADDOPTS` and the command line — so
    an explicit `-n 2` still overrides them, which is why this prepends rather
    than refusing on the ini value alone. With the usual single candidate it is
    a plain prepend; with several (only possible via `_search_starts`) it picks
    the one that would refuse, because over-refusing names its own fix.
    """
    best, best_rank = None, -1
    for tokens in candidates:
        candidate = expand_short_clusters(tokens) + stream
        asked = requested_workers(candidate)
        rank = -1 if asked is None else asked
        if best is None or rank > best_rank:
            best, best_rank = candidate, rank
    return stream if best is None else best


def pytest_option_stream(cmd, base=None):
    """The option tokens pytest will actually parse, or None if this payload
    is not pytest at all.

    `base` is the directory ini discovery starts from; it defaults to the
    working directory, which is what the payload will inherit through `execvp`.

    TWO decisions live here, and each closed a class of review finding.

    WHICH COMMANDS: `-n` means "xdist workers" only when the command IS pytest
    — `pytest ...`, `py.test ...`, or `python[3[.X]] ... -m pytest ...`. Any
    other command's `-n` is that command's own flag (`head -n 10`,
    `echo -n 8`) and govrun, which wraps arbitrary payloads, must run it
    untouched. Earlier versions scanned every argv and refused `head -n 10`.

    WHICH SOURCES: pytest reads options from THREE places and concatenates
    them into one argv before one argparse pass — verified in the installed
    pytest 9 (`_pytest/config/__init__.py::Config.parse`, read)
    and by a live probe with `--maxfail` from all three sources at once::

        args = <ini addopts> + shlex.split($PYTEST_ADDOPTS) + <command line>

    Last occurrence wins ACROSS sources (measured: argv beat env beat ini), so
    reading argv alone both misses `PYTEST_ADDOPTS='-n 8' pytest` (bypass) and
    cannot know that `pytest -n 2` OVERRIDES an env `-n 8` (over-refusal).
    This returns env + argv in pytest's own order, with short-option clusters
    split, and lets the caller's last-wins scan do the rest.

    The `<ini addopts>` term is not always the ini's: a `-o addopts=...` on the
    command line (or in PYTEST_ADDOPTS) REPLACES it, and that override is
    itself an argv token, so it is read here — see `override_addopts`. When
    there is no such override the term IS the ini's, and `ini_addopts` reads it
    from disk. That used to be a documented limit; it was a `high` finding, and
    a repo whose `pytest.ini` said `-n 8` walked straight through the budget.
    """
    cmd, inline_addopts = peel_wrappers(cmd)
    program = os.path.basename(cmd[0]) if cmd else ""
    rest = None
    if program in ("pytest", "py.test"):
        rest = list(cmd[1:])
    elif PYTHON_RE.match(program):
        rest = python_m_pytest_args(cmd)
    if rest is None:
        return None
    # An INLINE assignment replaces the exported one for this command, which is
    # bash's own rule, so it is read INSTEAD of the environment, not as well as.
    env_addopts = (os.environ.get("PYTEST_ADDOPTS", "")
                   if inline_addopts is None else inline_addopts)
    try:
        stream = shlex.split(env_addopts) + rest
    except ValueError:
        # Unbalanced quotes: pytest's own shlex.split raises the same error
        # while it is still parsing options, so it exits before spawning a
        # worker — same reasoning as an unconvertible `-n` value.
        stream = rest
    stream = expand_short_clusters(stream)
    injected = override_addopts(stream)
    if injected is None:
        return _most_demanding(ini_addopts(stream, base), stream)
    try:
        # `-o addopts=` REPLACES the ini value (so the file is not read at all
        # here), and ini addopts go FIRST — in front of PYTEST_ADDOPTS and the
        # command line, so an explicit `-n 2` still overrides it (measured, see
        # override_addopts).
        return expand_short_clusters(shlex.split(injected)) + stream
    except ValueError:
        return stream


def requested_workers(opts):
    """The worker count pytest will actually run with, or None. `opts` is the
    combined option stream from `pytest_option_stream`.

    Spellings: `-n K`, `-nK`, `-n=K`, `--numprocesses K`, `--numprocesses=K`,
    and any of those with `-n` joined to preceding short flags (`-vn8`,
    `-xsn 8`). That set is not a guess — it is every form argparse binds to
    this option, verified against argparse itself (see the differential test,
    which fails if a Python release ever moves argparse). The attached-value
    forms arrive here already split by `expand_short_clusters`, which is where
    argparse's rule that exactly one leading `=` is stripped from an attached
    value lives, so `-n=8` reaches xdist as 8 while `-n==8` reaches it as '=8'.
    And pytest constructs its parser with `allow_abbrev=False`
    (`_pytest/config/argparsing.py:395`, checked), so there is no
    `--numproc 8` abbreviation to catch — argparse leaves it unparsed.

    STOPS AT `--`, because argparse does: everything after the first `--` is
    positional, so `pytest -n 8 -- -n 2` runs with 8 workers (measured with the
    real pytest — the trailing `-n 2` becomes two file arguments). Reading past
    it turned the 8 into an admitted 2.

    LAST ONE WINS, because argparse's `store` action keeps the last occurrence:
    `-n 8 -n 2` reaches xdist as 2 (measured), so refusing it on the 8 would
    refuse a command that runs 2 workers. `-n 8 -n auto` likewise ends as
    `auto` and is admitted — the assignment below overwrites unconditionally,
    including with None.

    This cannot be left to the governor hook. The exported env var binds `-n auto`
    only, so a wrapped `pytest -n 8` otherwise runs 8 workers inside a 4-worker
    slot and two slots reach 16 — the incident, reproduced THROUGH the
    governor. Nor can the hook catch it: the highest-risk callers are
    subprocesses, not Bash tool calls. govrun is the only layer that sees every
    wrapped payload.
    """
    opts = expand_short_clusters(opts)
    asked = None
    i = 0
    while i < len(opts):
        arg, value = opts[i], None
        if arg == "--":
            break
        if arg in ("-n", "--numprocesses") and i + 1 < len(opts):
            value = opts[i + 1]
            i += 1
        elif arg.startswith("--numprocesses="):
            value = arg.split("=", 1)[1]
        if value is not None:
            asked = worker_count(value)
        i += 1
    return asked


def check_budget(cmd, workers):
    peeled, _ = peel_wrappers(list(cmd))
    if peeled == [ENV_S_UNRESOLVED]:
        raise Refused(
            "env -S nested too deep to resolve; refusing (fail closed). "
            "Unwrap the command or run it through -n auto.")
    opts = pytest_option_stream(cmd)
    if opts is None:
        return  # not pytest: its `-n` is its own business
    asked = requested_workers(opts)
    if asked is not None and asked > workers:
        raise Refused(
            f"payload asks for {asked} workers (from -n/--numprocesses on the "
            f"command line, in PYTEST_ADDOPTS — exported or assigned in front "
            f"of the command — via -o addopts=, or in the "
            f"addopts of a pytest.ini / pyproject.toml / tox.ini / setup.cfg) "
            f"but this slot budgets "
            f"{workers}. Use -n auto (it reads PYTEST_XDIST_AUTO_NUM_WORKERS, "
            f"which govrun sets to {workers}), or -n {workers}.")
