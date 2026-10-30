"""`python [opts] -m pytest` recognition, split out of govrun_pytest_budget.py
(2026-10-11) at its 500-line bound, moved verbatim. Re-exported there.
"""
import re


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
