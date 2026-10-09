#!/usr/bin/env python3
"""governor_parse — how governor-hook.py takes a Bash command apart.

Split out of `governor-hook.py` to get that file under the repo's
500-line ratchet: this module is the parsing half, the hook keeps the DECISION
half (`heavy`, `decide`, `hook_mode`, the output, the entry point). Nothing here
decides anything — every function answers a question about the text.

Latency measured (40 paired `git status` runs): the split runs ~1 ms
FASTER than a one-file rebuild — an imported module is byte-cached, a `__main__`
script is not, and this file imports only stdlib. Plan record.

THE OBJECTION THAT SURVIVES: a split adds a way to disarm the guard — this file
missing, stale, or shadowed on the deploy target. The hook runs as a script, so
`sys.path[0]` is its own directory and `import governor_parse` finds this file
(verified from three working directories). When it does NOT, the hook
FAILS CLOSED four ways: `governor-hook.py` imports inside a `try` and turns
failure into a DENY naming this file; `settings.json`'s wrapper turns any
non-zero hook exit into exit 2, which blocks the call; `govrun --status` reports
ARMED/DISARMED from a decision the hook renders, not a regex on the file; and
`test_governor_parse.py` runs the hook from a deploy-only directory, and again
with this file deleted.

Self-check: hooks/test_governor_parse.py, and hooks/test_governor_hook.py for
everything reached through the hook.
"""
import io
import os
import re
import shlex


# Tokens that END one command and start another. The operator-aware lexer keeps
# a separator inside quotes in its token, so it never splits a segment. NEWLINE
# separates (bash runs `cd /tmp\ndocker build .` as two commands; a CRLF too,
# measured; a BACKSLASH-newline does not). GROUPING separates the same
# way — only a BARE brace token, leaving `${HOME}` and `file{1,2}.txt` alone
# (substitution `echo $(docker build .)` or `` `…` `` still runs a build). The lexer
# glues a RUN of punctuation into one token, so every multi-char operator is
# named: `make |& pytest -n 16` lexes `|&` as one token, an unlisted token is an
# ARGUMENT that hid the pytest; `;;`/`;&`/`;;&` end a `case` arm. Redirections
# (`>&`, `>`, `<`) are NOT here — `numprocesses` stops its scan at them instead.
SEPARATORS = {"&&", "||", ";", "|", "&", "\n", "(", ")", "{", "}",
              "|&", ";;", ";&", ";;&", "`"}
# argparse peels a joined short-option cluster one char at a time, and the first
# char that TAKES A VALUE swallows the rest: `-vn8` is `-v -n 8` and reaches xdist
# as 8 workers. INLINED from govrun_pytest_ini.VALUE_TAKING_SHORT_OPTS (no import
# on the decision path); `test_the_inlined_short_opts_match_govrun` pins the two.
# This is what makes `-rn` NOT an `-n`: `-r` takes a value, so the `n` is it.
VALUE_TAKING_SHORT_OPTS = "Wckmoprn"
# pytest reads options from `PYTEST_ADDOPTS` too, so `PYTEST_ADDOPTS=-n8 pytest`
# runs 8 workers with nothing on argv. The assignment may sit in an EARLIER
# SEGMENT (`export X=-n8; pytest`), so every earlier segment is in scope.
# CONSERVATIVE: a bare `X=…; pytest` and a subshell export are counted anyway
# (over-refuse, documented escape hatch). OUT OF REACH: a variable exported by a
# PREVIOUS, separate Bash call — the hook sees one line, not the environment.
PYTEST_ADDOPTS = "PYTEST_ADDOPTS="
# docker's global options sit BEFORE the subcommand, so `docker --context
# default build .` is a build. The value-taking ones are named (docker CLI
# `--help`, read): a VALUE can look like a subcommand, and `--context
# build` must not read as `build`. Residual gap: a value-taking global flag added
# to docker later and spelled `build`.
DOCKER_GLOBAL_WITH_VALUE = {"-c", "--context", "-H", "--host", "--config",
                            "-l", "--log-level", "--tlscacert", "--tlscert",
                            "--tlskey"}
ENV_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
# Wrappers a heavy command may hide behind and still be seen. CLOSED LIST — see
# the module docstring's residual gap. A runner counts only before `run` (or
# `tool run`), `python*` only before `-m`. INLINED from govrun_pytest_budget (no
# import on the decision path); `test_the_hook_and_the_budget_peel_the_same_
# wrappers` pins the two, so a runner added to one table is added to both or the
# gate reddens. A two-entry table was itself the bypass: `uvx pytest -n 8` and
# `uv run -- pytest -n 8` were ALLOWED while `pytest -n 8` denied.
TRANSPARENT = {"env", "nice", "time"}
# Words that LEAD a segment without being the command — the job follows one word in: reserved
# words (`if true; then pytest -n 8` -> segment `then pytest -n 8`) and command-prefix builtins
# (`coproc`/`nohup`/`command`/`exec pytest -n 8`) both hid a heavy job and `peel` stopped at the
# lead (2026-09-08, ALLOWED). `time` is TRANSPARENT, `{`/`(` split, `--` ends a prefix's options
# (`command -- pytest` ran free). RAW — never a path. Residual: `eval "…"`, `coproc NAME cmd`.
BASH_KEYWORDS = {"if", "then", "elif", "else", "while", "until", "do", "!",
                 "coproc", "command", "exec", "nohup", "builtin"}
RUNNERS = {"uv", "poetry", "pipenv", "pdm", "hatch", "rye"}
# `uvx` is uv's documented alias for `uv tool run` and takes no `run` word.
RUNNERS_ALONE = {"uvx"}
# Shell builtins an assignment may follow and still BE an assignment. Anything
# else in front makes it an ARGUMENT: `echo PYTEST_ADDOPTS=-n8` assigns nothing,
# and denying that would have no way out. Each takes single-letter option
# clusters (`declare -x`, `declare -gx`) that `addopts` skips before the name;
# TRANSPARENT wrappers are handled on their own branch, not here.
BUILTIN_ASSIGNERS = {"export", "declare", "typeset", "readonly"}
# `-n`, `-n8`, `-nauto`, `-n=8`, `--numprocesses`, `--numprocesses=8`. The
# attached value is captured WHOLE and judged by `worker_count`, which runs
# xdist's own `int()`: a digit pattern here was the bypass — `--numprocesses=+8`
# and `--numprocesses=8_0` matched nothing and were ALLOWED while
# xdist ran 8 and 80 workers. `-name` reads as no count: `int("ame")` raises.
NUMPROCESSES = re.compile(r"^(?:-n(.*)|--numprocesses(?:=(.*))?)$")


# A `#` starts a comment only at a WORD BOUNDARY. bash reads a glued one as an
# ordinary character — `curl http://x/#frag && pytest -n 8` runs the pytest
# (measured) — while shlex would call it a comment and eat the `&&`.
COMMENT_BOUNDARY = " \t\r\n;|&()<>"


class CommandText(io.StringIO):
    """The command text, with bash's two comment rules put back.

    shlex consumes a comment with one `instream.readline()` (stdlib shlex.py,
    Python 3.14.6, read), so both divergences are fixable here:

      * It swallows the NEWLINE that ENDS the comment, so `echo ok # c` + newline
        + `pytest -n auto` tokenizes with no separator and the pytest reads as an
        argument of echo. Stop BEFORE the newline instead.
      * It starts a comment at a `#` glued INSIDE a word, which bash reads as an
        ordinary char. Consume nothing there: the `#` is dropped and the word
        splits in two, changing no verdict — a heavy job is known by the token
        LEADING its segment, and dropping a `#` cannot promote one.
    """

    def readline(self):
        text, at = self.getvalue(), self.tell()  # `at` is just past the `#`
        if at >= 2 and text[at - 2] not in COMMENT_BOUNDARY:
            return ""                            # mid-word `#`, not a comment
        end = text.find("\n", at)
        self.seek(len(text) if end < 0 else end)  # leave the newline behind
        return ""


def segments(command):
    """The command split into the separate commands a shell would run.

    The punctuation chars are what make `a&&b` and `pytest -n 8|tail` split
    without spaces, and the lexer keeps quoted text as ONE token — which is the
    whole reason `grep -rn 'docker build' .` is not a docker build. `\\n` is
    moved OUT of whitespace and INTO the punctuation set so a newline splits
    instead of vanishing; `\\r` stays whitespace, so a CRLF is one separator.
    Comments are handled by `CommandText`, not here.
    """
    lexer = shlex.shlex(CommandText(command), posix=True,
                        punctuation_chars="();<>|&\n`")
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    try:
        tokens = list(lexer)
    except ValueError:
        # An unbalanced quote is a broken command, NOT an internal error: bash
        # refuses the whole string (measured — exit 2, `unexpected
        # EOF while looking for matching`), so nothing heavy runs either way.
        # The degraded path must still not be the easy way in, so fall back to a
        # whitespace split rather than allowing. Newlines have to survive it, or
        # the fallback is itself a bypass.
        tokens = command.replace("\n", " ; ").split()
    out, current = [], []
    for token in tokens:
        if token in SEPARATORS:
            out.append(current)
            current = []
        else:
            current.append(token)
    out.append(current)
    return [resplit_env_s(seg) for seg in out if seg]


def runner_words(name, rest):
    """How many tokens the runner front-end spelled `name` occupies, or None.
    The same rule as govrun_pytest_budget.runner_words, which the pin test ties
    this to. RESIDUAL GAP, deliberate: a FLAG between the runner word and the
    payload (`uv run --with pytest-xdist pytest -n 8`) stays opaque, because
    skipping flags blind would read a value-taking one's VALUE as the payload."""
    if name in RUNNERS_ALONE:
        return 1
    if name in RUNNERS:
        if rest[:2] == ["tool", "run"]:
            return 3
        if rest[:1] == ["run"]:
            return 2
    return None


# Options the transparent wrappers take in FRONT of the payload. `env -i`, `nice
# -n 10`, `time -p` are valid, and a peel that skipped only the wrapper WORD
# stopped at the option and never saw the pytest (2026-09-08, all three ALLOWED).
# Value-taking ones need their value skipped too; `--` ends the wrapper's options.
WRAPPER_VALUE_OPTS = {"-u", "--unset", "-C", "--chdir", "-P", "-n",
                      "--adjustment", "-o"}
# The same options as single letters, for CLUSTERS: `env -iu HOME`, `nice -n10`.
# In a cluster the first value letter ends the walk; its value is the rest of the
# token, or the next token when nothing is attached (`env -iS 'pytest -n 8'` and
# `env -S'pytest -n 8'` both run pytest). `S` is env's split-string: resplit_env_s
# turns it into words.
WRAPPER_VALUE_LETTERS = "uCPnoS"


def option_width(args, index):
    """Tokens the wrapper option at `args[index]` occupies: 1, or 2 for an unattached value."""
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
    string (macOS and GNU env, measured), and so do `env -iS '…'` and
    the glued `env -S'…'`. Put the words back where the string was, so the payload
    meets the same scan as `env pytest`. Only env's OWN option slot is read. The
    split can expose ANOTHER `env -S` (`env -S 'env -S "pytest -n 8"'` runs pytest
    too), so it repeats until stable, bounded so a pathological line cannot spin."""
    for _ in range(16):
        expanded = _resplit_env_s_once(tokens)
        if expanded == tokens:
            return tokens
        tokens = expanded
    # Deeper env -S nesting than we resolve: a `pytest` could hide one layer
    # down, so FAIL CLOSED with a marker the decision path refuses.
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


def _split_python_cluster(token):
    """A python short-option cluster -> its own tokens, argparse's reading.
    `-qm` -> `-q -m`, `-qmpytest` -> `-q -m pytest`, `-qWignore` -> `-q -W
    ignore`. `m`/`c` end the token (the module or code is the rest); `W`/`X` take
    the rest as a value; else it is a flag."""
    out, body = [], token[1:]
    for pos, char in enumerate(body):
        out.append("-" + char)
        if char in "mcWX":
            rest = body[pos + 1:]
            if rest:
                out.append(rest)
            break
    return out


# python options that PRINT and EXIT, so a following `-m` never runs: `-V` /
# `--version`, `-h` / `--help`, and `--` (python reads the next token as a script
# path — `python -- -m pytest` looks for a file `-m`). Verified:
# pytest did not run, so denying them was a FALSE denial. `-qV` splits to `-q -V`.
PYTHON_TERMINAL = {"-V", "--version", "-h", "--help", "--"}


def python_m_token(segment, python_at):
    """Index of the token that spells python's `-m` / `-mMODULE`, or None for a
    script, a `-c CODE` string, no module, or a PYTHON_TERMINAL option that exits
    first. Reaches the `-m` behind python's OWN options, INCLUDING in a cluster
    (`-qm pytest`, `-qW ignore -m pytest`): a short cluster is split in place so
    its terminal `-m`/`-c` or value-taking `-W`/`-X` is its own token. `-c` starts
    code; `-W`/`-X`/`--check-hash-based-pycs` take a value, attached or next."""
    index = python_at + 1
    while index < len(segment):
        token = segment[index]
        if token == "-m" or (token.startswith("-m") and len(token) > 2):
            return index
        if token in PYTHON_TERMINAL:  # python prints and exits before -m runs
            return None
        if not token.startswith("-") or token.startswith("-c"):
            return None
        if token in ("-W", "-X", "--check-hash-based-pycs"):
            index += 2  # a separated value: `-W ignore`
            continue
        if token.startswith("--") or len(token) == 2:
            index += 1  # a long option, or a lone short flag
            continue
        segment[index:index + 1] = _split_python_cluster(token)  # `-qm`, `-BqW`
    return None


def peel_python(segment, python_at):
    """Advance past `python [opts] -m` to the module (the real command); return
    the next index to scan, or None when python ITSELF is the command — a script,
    a `-c CODE` string, or a terminal option that exits before `-m`. An attached
    `-mMODULE` is split in place, so the module is its own token."""
    pos = python_m_token(segment, python_at)
    if pos is None:
        return None
    if len(segment[pos]) > 2:
        segment[pos:pos + 1] = ["-m", segment[pos][2:]]
    return pos + 1 if pos + 1 < len(segment) else None


def peel(segment):
    """`(index of the real command word, whether govrun leads this segment)`.

    Leading `NAME=value` assignments are skipped (`DOCKER_BUILDKIT=1 docker build
    .` is a build; `X=govrun pytest -n 8` is NOT wrapped), and so are `TRANSPARENT`
    wrappers. `govrun` is peeled the same way AND reported, because wrapping is
    PER-SEGMENT: in `govrun -- echo ok && pytest -n auto` the pytest runs outside
    govrun, so the leading govrun must not exempt it.
    """
    # `len(segment)` re-read every pass, NOT cached: peel_python expands a
    # clustered `-m` in place (`-qqmpytest` -> `-q -q -m pytest`), and a cached
    # bound let the grown tail hide the module (`-qqmpytest -n 8` ran).
    index, leads = 0, False
    while index < len(segment):
        token = segment[index]
        name = os.path.basename(token)
        if ENV_ASSIGNMENT.match(token) or token in BASH_KEYWORDS or token == "--":
            index += 1  # a `NAME=value`, or a keyword like `then`/`do`/`!`
            continue
        if name in TRANSPARENT:
            index += 1 + wrapper_options(segment[index + 1:])
            continue
        if name == "govrun":
            leads = True
            # `govrun --class ci -- pytest …`: govrun's own flags end at `--`.
            index = segment.index("--", index) + 1 if "--" in segment[index:] else index + 1
            continue
        if name.startswith("python"):
            nxt = peel_python(segment, index)  # `python [opts] -m pytest`
            if nxt is None:
                return index, leads
            index = nxt
            continue
        step = runner_words(name, segment[index + 1:])
        if step is None:
            return index, leads
        index += step
        # `uv run -- pytest`: the separator ends the RUNNER's own options, so
        # the payload starts after it, not at it.
        if segment[index:index + 1] == ["--"]:
            index += 1
        # `uv run -m pytest`: the runner forwards `-m` to python, so the module
        # is the real command. `uv run --with X pytest` is the residual gap above.
        if segment[index:index + 1] == ["-m"]:
            index += 1
    return None, leads


def numprocesses(args):
    """The xdist worker count in `args`: an int, `"auto"`, or None if absent.
    Scanning stops at the first redirect, so `-n` has to be a pytest ARGUMENT.
    Pipes and `;` already ended the segment, so `pytest -q 2>&1 | tail -n 40`
    never reaches the `tail -n 40`.
    """
    count = None
    for index, token in enumerate(args):
        if token[:1] in "<>" or token[:2] in ("2>", "1>"):
            break
        found = NUMPROCESSES.match(token)
        if not found:
            continue
        value = found.group(1) or found.group(2)
        if not value:  # `-n 8` — the value is the next word
            value = args[index + 1] if index + 1 < len(args) else ""
        elif token[1] == "n" and value[0] == "=":
            value = value[1:]  # argparse strips exactly one attached `=`
        # LAST one wins, as argparse's `store` does: `-n 2 -n 8` runs 8
        # workers (measured, xdist 3.8.0), and returning the first 2 let a
        # wrapped `govrun -- pytest -n 2 -n 8` past the budget.
        count = worker_count(value)
    return count


def worker_count(value):
    """What xdist makes of `value`: an int, `"auto"`, or None when its own
    `int()` would raise and pytest dies before a worker starts. The SAME rule
    as govrun_pytest_budget.worker_count, restated because nothing is imported
    on the decision path."""
    if value in ("auto", "logical"):
        return "auto"
    try:
        return int(value)
    except ValueError:
        return None


def split_cluster(token):
    """`-vn8` -> `['-v', '-n', '8']`, argparse's own reading (see
    VALUE_TAKING_SHORT_OPTS). A token that is not a cluster comes back whole."""
    if len(token) <= 2 or token[0] != "-" or token[1] == "-":
        return [token]
    out, body = [], token[1:]
    for position, char in enumerate(body):
        out.append("-" + char)
        if char in VALUE_TAKING_SHORT_OPTS:
            value = body[position + 1:]
            if value.startswith("="):
                value = value[1:]  # argparse strips exactly one attached `=`
            if value:
                out.append(value)
            break
    return out


def pytest_args(segment, index, earlier=()):
    """The option tokens pytest will parse, in the order pytest parses them.
    `earlier` is the segments that ran before this one.

    pytest PREPENDS `PYTEST_ADDOPTS` to the command line (docs read)
    and argparse keeps the LAST value, so env options come first and argv after:
    `PYTEST_ADDOPTS='-n 8' pytest -n 2` runs 2 (measured, xdist 3.8.0). Only the
    NEAREST assignment counts — an inline `X=-n2 pytest` replaces an exported
    `-n 8` from an earlier segment. Argv stops at a redirect and at `--` (argparse
    reads everything after `--` as a file name: `pytest -n 3 -- -n 2` runs 3).
    """
    args = []
    for tokens in (segment[:index], *reversed(list(earlier))):
        options = addopts(tokens)
        if options:
            for option in options:
                args.extend(split_cluster(option))
            break
    for token in segment[index + 1:]:
        if token == "--" or token[:1] in "<>" or token[:2] in ("2>", "1>"):
            break
        args.extend(split_cluster(token))
    return args


def addopts(tokens):
    """The `PYTEST_ADDOPTS` options `tokens` assign, split into words.

    Called on every segment ahead of the pytest AND on the pytest segment's own
    prefix, so one rule covers `X=-n8 pytest`, `export X=-n8; pytest` and `env
    X=-n8 pytest`. Position makes it an assignment: the scan stops at the first
    token that is neither an assignment nor a leader.
    """
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token.startswith(PYTEST_ADDOPTS):
            value = token[len(PYTEST_ADDOPTS):]
            try:
                return shlex.split(value)
            except ValueError:  # unbalanced quote: pytest's own split raises too
                return value.split()
        name = os.path.basename(token)
        if name in TRANSPARENT:  # `env -i X=1`: its options do not end the scan
            index += 1 + wrapper_options(tokens[index + 1:])
            continue
        if name in BUILTIN_ASSIGNERS:
            # `declare -x PYTEST_ADDOPTS=…`: skip the builtin AND its options.
            # declare/typeset/export/readonly options are single-letter flag
            # clusters (-x, -gx, …) that never consume a word — skip leading `-`.
            index += 1
            while index < len(tokens) and tokens[index].startswith("-"):
                index += 1
            continue
        if not ENV_ASSIGNMENT.match(token):
            break
        index += 1
    return []


def docker_subcommand(args):
    """The first word after docker's global options, or None."""
    index = 0
    while index < len(args):
        token = args[index]
        if not token.startswith("-"):
            return token
        # `--context=default` carries its value in the token itself.
        index += 2 if token in DOCKER_GLOBAL_WITH_VALUE else 1
    return None
