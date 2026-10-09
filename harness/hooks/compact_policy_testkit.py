#!/usr/bin/env python3
"""Fixtures and process helpers for hooks/test_compact_policy.py.

Kept out of the test file so the assertions stay readable, and so both files
keep room under the repo's size ratchet as a plan session adds its own cases.  The bounds
are scripts/quality/check_file_sizes.py's and they differ: a test file is
capped at `test_limit` (800 LOC), a non-test file at `limit` (500).  An earlier
version of this docstring claimed the split kept the test file "inside the
repo's 500-line ratchet", which was wrong twice over — 500 is not the bound
that applies to it, and the file was already past 500 when the claim was
written.  Nothing here asserts; it only builds inputs and runs the hook the way
Claude Code does.

EVERY helper is HOME-scoped.  No test may touch the real ~/.gearbox-state.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(HERE, "compact-policy.py")

# The ledger spine every decisions.ndjson line must carry, for every subcommand.
SPINE = ("ts", "session", "event", "source", "policy_version", "reason")


def load_policy():
    """Import compact-policy.py under a legal module name (it has a hyphen)."""
    spec = importlib.util.spec_from_file_location("compact_policy_under_test", HOOK)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    sys.path.insert(0, HERE)  # so its own `from context_tokens import ...` resolves
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(HERE)
    return module


def load_context_tokens():
    spec = importlib.util.spec_from_file_location(
        "context_tokens_under_test", os.path.join(HERE, "context_tokens.py")
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# --- running the hook as Claude Code runs it -------------------------------


def hook_env(home, env=None):
    environ = dict(os.environ)
    environ["HOME"] = str(home)
    environ.pop("GEARBOX_COMPACT_POLICY", None)
    environ.pop("GEARBOX_COMPACT_SOURCE", None)
    environ.pop("CLAUDE_CODE_SESSION_ID", None)
    # These tests are themselves usually run FROM a subagent, whose environment
    # carries the worker markers safe-point now refuses on. Strip them, or every
    # run inherits the dispatching agent's identity and the suite tests the
    # harness it happens to run under instead of the hook.
    for marker in ("CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_FORK_SUBAGENT"):
        environ.pop(marker, None)
    environ.update(env or {})
    return environ


def hook_commands():
    """Every compact-policy command settings.json really registers, verbatim.

    Read from the file rather than restated here: a check written against a
    copy of the command would keep passing after the registration changed.
    """
    with open(os.path.join(os.path.dirname(HERE), "settings.json"), encoding="utf-8") as f:
        settings = json.load(f)
    return [hook["command"]
            for groups in settings.get("hooks", {}).values()
            for group in groups
            for hook in group.get("hooks", [])
            if "compact-policy.py" in hook.get("command", "")]


def shell(command, home, stdin=None):
    """Run a registered command the way Claude Code does: through `sh -c`
    (confirmed against the hooks reference)."""
    return subprocess.run(
        command, shell=True, capture_output=True, text=True,
        input=json.dumps(stdin) if stdin is not None else "",
        env=hook_env(home), timeout=60,
    )


def run(home, sub, *args, stdin=None, env=None):
    environ = hook_env(home, env)
    return subprocess.run(
        [sys.executable, HOOK, sub, *args],
        input=json.dumps(stdin) if stdin is not None else "",
        capture_output=True,
        text=True,
        env=environ,
        timeout=60,
    )


def prompt(home, session, text, **kw):
    return run(home, "prompt", stdin={"session_id": session, "prompt": text}, **kw)


def last_event(home, event, session=None):
    """The last ledger row of one event kind. `decisions(home)[-1]` is the wrong
    accessor once a compaction takes two rows: the prompt that MEASURES it
    ledgers its own row afterwards and lands last."""
    rows = [r for r in decisions(home)
            if r.get("event") == event and (session is None or r.get("session") == session)]
    return rows[-1] if rows else None


def settle(home, session, transcript=None, trigger="auto", **kw):
    """post-compact, then the prompt that MEASURES it.

    ctx_after is deferred: PostCompact cannot read it (the compact_boundary
    record lands after that hook), so a later UserPromptSubmit does. A test
    that wants a finished compaction record must drive both halves.
    """
    done = post_compact(home, session, transcript, trigger, **kw)
    prompt(home, session, "carry on")
    return done


def pre_compact(home, session, transcript=None, trigger="auto", **kw):
    payload = {"session_id": session, "trigger": trigger, "transcript_path": transcript}
    return run(home, "pre-compact", stdin=payload, **kw)


def post_compact(home, session, transcript=None, trigger="auto", **kw):
    payload = {"session_id": session, "trigger": trigger, "transcript_path": transcript}
    return run(home, "post-compact", stdin=payload, **kw)


def session_start(home, session, how_started="compact", **kw):
    payload = {"session_id": session, "how_started": how_started}
    return run(home, "session-start", stdin=payload, **kw)


# --- reading what it wrote -------------------------------------------------


def live_root(home, *parts):
    """The LIVE state root under a throwaway HOME."""
    return os.path.join(str(home), ".gearbox-state", "compaction", *parts)


def probe_root(home, *parts):
    """Where a GEARBOX_COMPACT_SOURCE=probe invocation writes instead."""
    return live_root(home, "probe", *parts)


def live_surfaces(home):
    """Every live path a probe must never create. Order matters only for
    legibility; the assertion names whichever ones exist."""
    return [name for name in ("decisions.ndjson", "sessions.ndjson", "policy", "hb")
            if os.path.exists(live_root(home, name))]


def ndjson(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def decisions(home):
    return ndjson(live_root(home, "decisions.ndjson"))


def sessions(home):
    return ndjson(live_root(home, "sessions.ndjson"))


def activations(home):
    return ndjson(live_root(home, "activations.ndjson"))


def policy(home, session):
    path = live_root(home, "policy", session + ".json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


# --- transcript fixtures ---------------------------------------------------


def assistant_line(input_tokens=0, cache_read=0, cache_creation=0, window=None,
                   model=None, filler=0):
    message = {
        "role": "assistant",
        "usage": {
            "input_tokens": input_tokens,
            "cache_read_input_tokens": cache_read,
            "cache_creation_input_tokens": cache_creation,
        },
    }
    if window is not None:
        message["usage"]["contextWindow"] = window
    if model is not None:
        message["model"] = model
    record = {"type": "assistant", "message": message}
    if filler:
        record["padding"] = "x" * filler
    return json.dumps(record, separators=(",", ":"))


def compacted_transcript(path, post_ctx, summary="Summary: did X, did Y.", window=None):
    """A transcript shaped like the tail of one that just compacted: the
    `compact_boundary` record real transcripts carry (measured against a live
    one), the synthetic summary message that follows it, and a
    trailing assistant usage record so ctx_after can be read the normal way."""
    lines = [
        json.dumps({"type": "system", "subtype": "compact_boundary",
                    "content": "Conversation compacted"}, separators=(",", ":")),
        json.dumps({"type": "user", "message": {"role": "user", "content": summary}},
                   separators=(",", ":")),
        assistant_line(input_tokens=post_ctx, window=window),
    ]
    return write_transcript(path, lines)


def tool_result_line(payload_bytes):
    """A record of the kind that lands AFTER an assistant turn and that no
    usage block counts: a tool result, carried by the next request in full."""
    return json.dumps(
        {"type": "user", "message": {"role": "user", "content": "r" * payload_bytes}},
        separators=(",", ":"),
    )


def write_transcript(path, lines, trailing_newline=True):
    body = "\n".join(lines)
    if trailing_newline:
        body += "\n"
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(body)
    return path


def realistic_transcript(path, assistant_ctx=180_000, window=1_000_000, size_kb=512):
    """A transcript shaped like a live one: chatter, then a big last record."""
    lines = [
        json.dumps({"type": "user", "message": {"role": "user", "content": "hello " * 200}})
        for _ in range(40)
    ]
    lines.append(
        assistant_line(
            input_tokens=assistant_ctx // 3,
            cache_read=assistant_ctx // 3,
            cache_creation=assistant_ctx - 2 * (assistant_ctx // 3),
            window=window,
            model="claude-opus-5[1m]",
            filler=size_kb * 1024,
        )
    )
    return write_transcript(path, lines)


# --- a throwaway git repo, for the activation evidence check ---------------


def git_repo(path):
    os.makedirs(path, exist_ok=True)
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    for args in (("init", "-q", "-b", "main"), ("commit", "-q", "--allow-empty", "-m", "seed")):
        subprocess.run(("git", "-C", path) + args, check=True, capture_output=True, env=env)
    head = subprocess.run(
        ("git", "-C", path, "rev-parse", "HEAD"), capture_output=True, text=True, check=True
    ).stdout.strip()
    return head


def git_commit(path):
    """Move the repo's HEAD on — a SECOND deploy, for the same-deploy guard."""
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    subprocess.run(("git", "-C", path, "commit", "-q", "--allow-empty", "-m", "next"),
                   check=True, capture_output=True, env=env)
    return subprocess.run(
        ("git", "-C", path, "rev-parse", "HEAD"), capture_output=True, text=True, check=True
    ).stdout.strip()


def deploy_output(head, changed=True):
    tail = "   nothing (clean no-op)" if not changed else "   M\thooks/compact-policy.py"
    return "-- changed --\n%s\n\ndeploy OK — /somewhere is at %s" % (tail, head[:12])


# --- the exhaustive verdict() sweep ----------------------------------------
# Input generation only. The predicate that decides whether a BLOCK was legal
# lives in the test file, beside the spec it encodes.

WINDOW_1M = 1_000_000

SWEEP_GRID = (
    ["auto", "error", "manual", "", "unknown"],
    [None, {"type": "orchestrator", "ceiling_fraction": None},
     {"type": "planning", "ceiling_fraction": 0.75},
     {"type": "default", "ceiling_fraction": 0.5},
     {"type": "weird", "ceiling_fraction": 0.5}, {}],
    ["ok", "hold", "unknown"],
    # 150_000 sits in the DANGER BAND of the 200k window: close enough to the
    # limit that the live self-disarm must fire, far enough below a ceiling
    # computed from the frozen headroom that the old rule blocked there. Without
    # a ctx in that band the sweep cannot see the defect it exists to catch, and
    # its own neuter probe passes — a check pointed at nothing.
    # 400_000 and 600_000 sit ABOVE WINDOW_TRUST_MIN_CTX and BELOW a 1M
    # session's ceiling, so the sweep still reaches the rules that run
    # after the window-corroboration guard. Without a ctx in that band
    # every block the grid produced was suppressed by the guard, and two
    # neuter probes silently stopped being able to fail.
    [None, 0, 100, 150_000, 200_000, 400_000, 600_000, 900_000, 10 ** 9],
    [None, 200_000, WINDOW_1M],
    [None, 100, 200_000, 900_000],
)


def sweep_blocks(mod):
    """Every input combination on which verdict() says BLOCK."""
    import itertools

    out = []
    for trigger, policy, point, ctx, win, eff in itertools.product(*SWEEP_GRID):
        ceiling, _window_headroom = mod.ceiling_for(policy, eff, win)
        for mid in (False, True):  # rule (h): the turn boundary, both sides
            if mod.verdict(trigger, policy, point, ctx, win, ceiling, mid)[0] == "block":
                out.append((trigger, (policy or {}).get("type"), point, ctx, win,
                            ceiling, mid))
    return out

# The neuter probes — breakers, checks and the phrase each failure must carry —
# live in compact_policy_neuters.py, beside one another.
