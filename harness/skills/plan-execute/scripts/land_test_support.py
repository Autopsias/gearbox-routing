"""The shared in-process stand-in for `run_cli`, for land / land-ack / finish tests.

`_land_fixture.run_cli` starts a real `python run.py ...` per call. A test that is not
about the command-line boundary (arguments, exit code, printed text) calls `inproc_cli`
instead: the same `run.main()` the CLI calls, in this process, with the same exit code and
the same JSON payload. A test of the boundary, or one that needs its own process (it kills,
signals or waits for a child, or checks recovery after a crash), keeps `run_cli`.

How a test module opts in: import `inproc_cli as run_cli` and `_inproc_session` (an autouse
fixture that hands pytest's monkeypatch to the helper), and import the real one as
`run_cli_subprocess` for the end-to-end tests.
"""
import contextlib
import io
import os
import sys
import tempfile
import traceback
from pathlib import Path

import pytest

from _land_fixture import json_tail, run_cli as run_cli_subprocess  # noqa: F401  (re-exported: the real boundary)

SCRIPTS = Path(__file__).resolve().parent
# Modules whose globals a land / land-ack / finish call could set and forget.
_WATCHED = ("land", "land_phase2", "land_push", "land_steps", "land_state", "land_writers",
            "land_gate", "land_start", "land_reservation", "land_repair", "finish")
_mp = None


@pytest.fixture(autouse=True)
def _inproc_session(monkeypatch):
    global _mp
    _mp = monkeypatch
    yield
    _mp = None


def _snapshot():
    snap = {}
    for name in _WATCHED:
        mod = sys.modules.get(name)
        if mod is None:
            continue
        for k, v in vars(mod).items():
            if k.startswith("__") or callable(v) or isinstance(v, type(sys)):
                continue
            snap[f"{name}.{k}"] = repr(v)
    return snap


def inproc_cli(*argv, cwd=None):
    """`run.py <argv>` in this process: `(exit_code, payload)` like `run_cli`."""
    if _mp is None:      # a module that borrows a helper but did not opt in keeps the real process
        from _land_fixture import run_cli  # noqa: PLC0415
        return run_cli(*argv, cwd=cwd)
    import run  # noqa: PLC0415  (never at import time: the engine reads the environment)
    _mp.chdir(str(cwd or SCRIPTS))
    _mp.setattr(sys, "argv", ["run.py", *[str(a) for a in argv]])
    if not os.environ.get("LAND_HOLD_DIR"):
        _mp.setenv("LAND_HOLD_DIR", tempfile.mkdtemp(prefix="land-holds-"))
    _mp.setenv("PATH", os.environ.get("PATH", ""))   # land_env may prefix PATH: undone at teardown
    if "PYRES_VENV" in os.environ:
        _mp.setenv("PYRES_VENV", os.environ["PYRES_VENV"])
    else:
        _mp.setenv("PYRES_VENV", "")
        _mp.delenv("PYRES_VENV", raising=False)      # land_env may set PYRES_VENV: undone at teardown
    before = _snapshot()
    out, err, code = io.StringIO(), io.StringIO(), 0
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            run.main()
        except SystemExit as e:
            code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
            if isinstance(e.code, str):
                print(e.code, file=err)
        except Exception:  # noqa: BLE001  (a crash is exit 1 with a traceback, as in a child)
            code = 1
            traceback.print_exc(file=err)
    leaked = sorted(k for k, v in _snapshot().items() if k in before and before[k] != v)
    assert not leaked, f"in-process call leaked module state: {leaked}"
    payload = json_tail(out.getvalue())
    if payload is None:
        payload = {"_stdout": out.getvalue()[-2000:], "_stderr": err.getvalue()[-2000:]}
    return code, payload
