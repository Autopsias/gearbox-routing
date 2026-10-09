"""govrun's read of a pytest config file's own ``addopts``.

Split out of govrun.py — a large part of what pushed it over the file-size
ratchet — alongside govrun_pytest_budget.py, which uses `ini_addopts` from
here to answer "what would pytest's ini contribute to this run's option
stream". See govrun.py's own module docstring for what govrun is and why.

pytest reads `addopts` from the first of `INI_CONFIG_NAMES` that carries
pytest configuration, in the payload's working directory (or the directories
a path argument moves the search to) and then that directory's parents. This
module owns everything about FINDING and PARSING that file; it does not
decide whether the resulting `-n` is over budget, or how it combines with
`PYTEST_ADDOPTS` and the command line — see govrun_pytest_budget.py for that.
"""
import configparser
import shlex
import tomllib
from pathlib import Path

from govrun_errors import Refused

# Single-dash options that CONSUME a value, so that a cluster like `-vn8` can
# be split the way argparse splits it. Measured, not remembered: read off the
# installed pytest's own parser (pytest 9 —
# `get_config([])._parser.optparser._actions`, the value-takers are
# `-W -c -k -m -o -p -r`), plus xdist's `-n`. The suite's
# `..._short_option_table_matches_the_installed_pytest` re-derives it from that
# same parser and goes red if pytest ever adds one.
#
# ponytail: everything NOT in this string is treated as a zero-argument flag,
# which is a model of a surface plugins can extend. It errs toward REFUSING:
# a plugin's own `-N VALUE` written as `-Nn8` would be read as a worker
# request and refused with a message naming the fix, rather than admitted
# silently. That direction is the safe one for an admission gate; the reverse
# (guessing an unknown char takes a value) would hide a real `-n`.
#
# Lives here (not in govrun_pytest_budget.py, its other user) because
# `_search_starts` below needs it too, and this module has no dependency on
# that one — govrun_pytest_budget.py imports it from here instead.
VALUE_TAKING_SHORT_OPTS = "Wckmoprn"

# pytest's config filenames IN ITS OWN ORDER, read off the installed pytest
# 9.1.1 (`_pytest/config/findpaths.py::locate_config`). Order is
# load-bearing twice over: within a directory the first name that carries pytest
# configuration wins, and a directory is searched before its parents. Note
# `tox.ini` comes BEFORE `setup.cfg`, and `pytest.toml` exists in pytest 9.
INI_CONFIG_NAMES = ("pytest.toml", ".pytest.toml", "pytest.ini", ".pytest.ini",
                    "pyproject.toml", "tox.ini", "setup.cfg")


def _ini_sections(path, strict=False):
    """The pytest config table `path` carries, `{}` for a config file that
    carries none, or None if it is not a pytest config file at all.

    `{}` vs None is load-bearing: a `pytest.ini`/`pytest.toml` is ALWAYS the
    source of configuration even when empty, so it STOPS the upward walk.
    Returning None for it would make us read a grandparent's `addopts` that
    pytest ignores — an over-refusal, but a confusing one.

    A file we cannot parse returns None (keep walking): pytest's own loader
    raises `UsageError` on the same file, so pytest exits while it is still
    finding its config and never spawns a worker — the same reasoning that
    admits an unconvertible `-n` value. `strict` turns that into a refusal
    instead, and is used only for an explicit `-c FILE`, where "keep walking"
    would mean reading a DIFFERENT file than pytest reads.

    ponytail: `RawConfigParser`, so a `%` in an unrelated ini value cannot raise
    where pytest's own (interpolation-free) `iniconfig` would not.
    """
    name = path.name
    try:
        if name.endswith(".toml"):
            data = tomllib.loads(path.read_text(encoding="utf-8"))
            if name in ("pytest.toml", ".pytest.toml"):
                table = data.get("pytest")
                return table if isinstance(table, dict) and table else {}
            tool = data.get("tool", {}).get("pytest", {})
            if not isinstance(tool, dict):
                return None
            native = {k: v for k, v in tool.items() if k != "ini_options"}
            if native:
                return native  # pytest 9's native-TOML mode
            ini = tool.get("ini_options")
            return ini if isinstance(ini, dict) else None
        parser = configparser.RawConfigParser()
        parser.read_string(path.read_text(encoding="utf-8"), str(path))
    except (OSError, ValueError, configparser.Error) as exc:
        if strict:
            raise Refused(
                f"cannot read the pytest config file named by -c ({path}): "
                f"{exc}. govrun refuses rather than guess a worker budget from "
                "a file it cannot parse — fix the file, or pass -n explicitly.")
        return None
    if name.endswith(".cfg"):
        # A bare [pytest] in setup.cfg is a hard error in pytest, so it never
        # runs; only [tool:pytest] is configuration.
        return dict(parser["tool:pytest"]) if parser.has_section("tool:pytest") \
            else None
    if parser.has_section("pytest"):
        return dict(parser["pytest"])
    return {} if name in ("pytest.ini", ".pytest.ini") else None


def _addopts_tokens(cfg):
    """`addopts` as pytest will splice it, or None if there is none.

    pytest declares `addopts` with ini type "args": a string is `shlex.split`,
    and in pytest 9's native-TOML mode it may already be a list.
    """
    if not isinstance(cfg, dict):
        return None
    value = cfg.get("addopts")
    if isinstance(value, list):
        return [str(v) for v in value]
    if isinstance(value, str):
        try:
            return shlex.split(value)
        except ValueError:
            return []  # pytest's own split raises here too; nothing spawns
    return None


def _config_file_arg(opts):
    """The value of the last `-c` / `--config-file`, or None. Stops at `--`."""
    value = None
    i = 0
    while i < len(opts):
        arg = opts[i]
        if arg == "--":
            break
        if arg in ("-c", "--config-file") and i + 1 < len(opts):
            value, i = opts[i + 1], i + 1
        elif arg.startswith("--config-file="):
            value = arg.split("=", 1)[1]
        i += 1
    return value


def _search_starts(opts, base):
    """Directories to search upward from, in order, `base` first.

    pytest does NOT always start at the working directory: it starts at the
    common ancestor of the path arguments. Measured with the real
    pytest 9 — with `pytest.ini` in both a directory and its `sub/`, plain
    `pytest` read the top one and `pytest sub/` read `sub/pytest.ini`. Searching
    only the working directory would therefore have missed a real `addopts`.

    ponytail: this collects a UNION (working dir plus every argument that is a
    real path) instead of computing pytest's common ancestor, which would mean
    re-implementing `get_dirs_from_args`/`get_common_ancestor` — the
    consumer-imitation this file has paid for twice. The union can only find
    configs pytest might not read, and the caller resolves a disagreement by
    taking the LARGEST worker request: an over-refusal names its own fix, a
    bypass is silent. It also means an option VALUE that happens to be a
    directory name (`-k tests`) can add a search start; same harmless direction.
    """
    starts, seen = [], set()

    def add(path):
        if path not in seen:
            seen.add(path)
            starts.append(path)

    add(base)
    skip = positional = False
    for arg in opts:
        if skip:
            skip = False
            continue
        if arg == "--":
            positional = True  # everything after is a path argument to pytest
            continue
        if not positional and arg.startswith("-") and arg != "-":
            if len(arg) == 2 and arg[1] in VALUE_TAKING_SHORT_OPTS:
                skip = True
            continue
        try:
            path = base / arg.split("::", 1)[0]  # `file.py::test_x` node ids
            if path.is_dir():
                add(path)
            elif path.exists():
                add(path.parent)
        except (OSError, ValueError):
            pass
    return starts


def ini_addopts(opts, base=None):
    """Every `addopts` an ini file could inject into this pytest run.

    A list, because a path argument can move pytest's search (see
    `_search_starts`); normally it has one entry or none.

    THE BOUNDED READ, and where it deliberately stops. pytest's `addopts` is the
    third source of options (`<ini addopts> + $PYTEST_ADDOPTS + <command line>`),
    and `pytest.ini` with `addopts = -n 8` really does run 8 workers with a clean
    argv — review round 6's high finding, and previously a documented limit here
    on the grounds that reading it meant re-implementing rootdir discovery. It
    does not: rootdir discovery answers "where is the project", while all this
    needs is "which config file does pytest read", which is the much smaller
    `locate_config` — a fixed list of filenames, checked in one directory then
    its parents. That is what this does, with `INI_CONFIG_NAMES` and
    `_ini_sections` transcribed from pytest's own loader.

    Where it cannot match pytest, it takes the conservative branch and says so:

    * `-c FILE` / `--config-file FILE` names the config exactly (measured: it
      overrides the located one), so that file is read INSTEAD of any search.
      A file we cannot parse is a refusal, not a shrug — see `_ini_sections`.
    * `--rootdir` does NOT move the config file. Read in
      `findpaths.determine_setup` and measured: `--rootdir=<top> sub/` still
      read `sub/pytest.ini`. So it is deliberately ignored, not handled.
    * Several candidate configs disagreeing -> the largest worker request wins
      (`_most_demanding`).
    * An `addopts` that itself contains `-o addopts=...` is not re-expanded;
      pytest reads its ini `addopts` once, and so does this.
    * Wrappers that launch pytest indirectly (`uv run pytest`, `tox`, `make
      test`) remain out of scope: govrun sees the wrapper's name, not pytest.
    """
    base = Path(base) if base is not None else Path.cwd()
    explicit = _config_file_arg(opts)
    if explicit is not None:
        path = base / explicit
        if not path.is_file():
            return []  # pytest errors on a missing -c file; nothing runs
        tokens = _addopts_tokens(_ini_sections(path, strict=True))
        return [tokens] if tokens else []
    found, seen = [], set()
    for start in _search_starts(opts, base):
        path = _locate_config(start)
        if path is None or path in seen:
            continue
        seen.add(path)
        tokens = _addopts_tokens(_ini_sections(path))
        if tokens:
            found.append(tokens)
    return found


def _locate_config(start):
    """The config file pytest would read starting at `start`: the first name in
    `INI_CONFIG_NAMES` that carries pytest configuration, in `start` and then in
    each of its parents. Bounded by the filesystem root."""
    for directory in (start, *start.parents):
        for name in INI_CONFIG_NAMES:
            path = directory / name
            if path.is_file() and _ini_sections(path) is not None:
                return path
    return None


