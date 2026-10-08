"""TH-01 — how big the ORCHESTRATOR's own context was when it dispatched.

The retro wants to test, on our own data, whether a big-context orchestrator
produces more rework. That needs the number recorded AT DISPATCH; nothing can
reconstruct it afterwards, because the orchestrator's context has moved on by
the time the session resolves.

OBSERVER, NEVER A GATE. Every path here returns ``None`` rather than raising:
telemetry that can block a dispatch is worse than telemetry that is missing.

WHY THIS MODULE EXISTS AT ALL rather than an import: ``hooks/context_tokens.py``
lives outside the ``skills/plan-execute/scripts`` package and its directory is
not on ``sys.path``, so it is loaded by path through ``importlib``. ``parents[3]``
is the tree root in BOTH trees this ships to (``~/your-private-harness`` and
``~/.claude``) — asserted by ``test_orchestrator_ctx.py``, not assumed.
"""

from __future__ import annotations

import importlib.util
import os
import re
from pathlib import Path

# <root>/skills/plan-execute/scripts/orchestrator_ctx.py -> parents[3] == <root>
TREE_ROOT = Path(__file__).resolve().parents[3]
CONTEXT_TOKENS_PATH = TREE_ROOT / "hooks" / "context_tokens.py"

SESSION_ENV = "CLAUDE_CODE_SESSION_ID"
COMPACT_STORE_PATH = TREE_ROOT / "hooks" / "compact_store.py"


def load_by_path(name, path):
    """Import a module that is not on ``sys.path``, or None for a missing file.

    The ``is_file`` check is not belt-and-braces: ``spec_from_file_location``
    hands back a perfectly good spec for a path that does not exist, and it is
    ``exec_module`` that then raises — so without it a missing module leaves
    here as a FileNotFoundError, and the caller's "returns None" test passes on
    the exception envelope rather than on this branch.
    """
    if not Path(path).is_file():
        return None
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def env_session_id():
    """This CONVERSATION's session id, or None.

    THIS READS ``$CLAUDE_CODE_SESSION_ID`` DIRECTLY, and deliberately does NOT
    delegate to ``compact_store.env_session()`` any more. That call refuses
    whenever ``CLAUDE_CODE_CHILD_SESSION`` or ``CLAUDE_CODE_FORK_SUBAGENT`` is
    set, on the stated premise that a worker sees its OWN id there. MEASURED
    2026-08-23 on this host, both halves of that premise are false:

      * both markers read ``'1'`` inside the ORCHESTRATOR's own tool subprocess,
        not only inside a worker — so they cannot separate the two at all; and
      * a dispatched subagent's subprocess reports the SAME session id as the
        orchestrator (``f527968d-…``, the id naming the main conversation's own
        transcript), not an id of its own.

    The consequence was total and silent: every dispatch refused to identify
    itself, so ``orchestrator_ctx_tokens()`` returned None and the field landed
    ``null`` on 33 of 33 records while the reader underneath it worked fine.

    The refusal in ``compact_store`` is left exactly as it is. It guards a
    WRITE — a safe point landing on the wrong policy file — where refusing
    costs nothing, and it may be right on a Claude Code version this host has
    not run. A refusal that is correct for a write is wrong for a read, which is
    the whole mistake this function used to inherit.

    Residual risk, stated rather than hidden: if a future version DOES give a
    worker its own id, a worker that ran ``run.py`` would record its own
    context. ``run.py`` is orchestrator-only by construction, and
    :func:`transcript_path` still verifies the file exists, so the failure would
    be a missing number rather than a wrong one.
    """
    session_id = os.environ.get(SESSION_ENV)
    return session_id or None


def encode_cwd(path) -> str:
    """Claude Code's own ``~/.claude/projects/`` directory encoding.

    MEASURED 2026-08-22 against all 114 real directories on this host: every
    character outside ``[A-Za-z0-9]`` becomes ``-``, with no collapsing of runs
    (that is why a dotted or hidden segment yields a double dash).

        /Users/x/your-private-harness       -> -Users-x-your-private-harness
        /Users/x/.claude               -> -Users-x--claude
        /Users/x/DeveloperFolder/AWS_setup
                                       -> -Users-x-DeveloperFolder-AWS-setup

    Lossy by construction (``a/b-c`` and ``a-b/c`` encode identically), which is
    exactly why :func:`transcript_path` verifies the session id's own file
    exists instead of trusting the directory name.
    """
    return re.sub(r"[^A-Za-z0-9]", "-", str(path))


def transcript_path():
    """This process's own transcript, or None when it cannot be located.

    The cwd encoding above is the documented fast path. It is not sufficient on
    its own: the transcript directory is keyed on the CLAUDE CODE SESSION's cwd,
    while this helper only ever sees the ``run.py`` PROCESS's cwd, and those
    diverge the moment the orchestrator runs `begin` from anywhere but the
    session's own directory. So a miss falls back to a glob for the same session
    id — the id is a UUID, so a match is the right file wherever it sits.

    The id itself comes from :func:`env_session_id`, which refuses a worker's.
    """
    session_id = env_session_id()
    if not session_id or "/" in session_id or session_id in (".", ".."):
        return None
    projects = Path.home() / ".claude" / "projects"
    direct = projects / encode_cwd(Path.cwd()) / (session_id + ".jsonl")
    if direct.is_file():
        return direct
    for found in projects.glob("*/" + session_id + ".jsonl"):
        if found.is_file():
            return found
    return None


def orchestrator_ctx_tokens():
    """The orchestrator's live context size in tokens, or None. Never raises.

    ``context_tokens()`` returns a THREE-tuple ``(tokens, model_window, source)``.
    A ``source`` starting with ``read`` means the TOKEN COUNT was measured; the
    suffixed variants (``read:no_model_window``, ``read:model_window_contradicted``)
    only say the MODEL WINDOW could not be established, which is a different
    number and not the one recorded here. Anything else is
    ``measurement_unavailable:*`` with ``tokens=None``.

    That distinction is load-bearing rather than pedantic: MEASURED on this host
    2026-08-22, a live transcript returns ``read:no_model_window`` — and
    ``context_tokens``' own docstring records that ZERO of 9,933 transcripts
    carry a window field. An equality test against ``"read"`` would therefore
    have recorded ``null`` for every real dispatch while looking like it worked.
    """
    try:
        path = transcript_path()
        if path is None:
            return None
        module = load_by_path("th01_context_tokens", CONTEXT_TOKENS_PATH)
        if module is None:
            return None
        tokens, _window, source = module.context_tokens(str(path))
        if not str(source).startswith("read"):
            return None
        if isinstance(tokens, bool) or not isinstance(tokens, int):
            return None
        return tokens
    except Exception:  # noqa: BLE001 — telemetry can never block a dispatch
        return None
