#!/usr/bin/env python3
"""Self-check for agent-elapsed-nag.py.

Carries its own controls:
a KNOWN NEGATIVE (nothing dispatched, and a fresh dispatch) must stay silent,
and a KNOWN POSITIVE (a dispatch 31 minutes old) must speak. A check that only
ever asserts silence would pass on a hook that does nothing at all.

Run: python3 hooks/test_agent_elapsed_nag.py
"""

import importlib.util
import io
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(HERE, "agent-elapsed-nag.py")

spec = importlib.util.spec_from_file_location("nag", HOOK)
nag = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nag)

SESSION = "selfcheck-session"


def backdate(root, minutes):
    path = os.path.join(root, SESSION + ".json")
    with open(path, encoding="utf-8") as fh:
        d = json.load(fh)
    d["starts"] = [t - minutes * 60 for t in d["starts"]]
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(d, fh)


def emitted(mode, payload):
    """Drive main() end to end and return what it wrote to stdout."""
    old_in, old_out, old_argv = sys.stdin, sys.stdout, sys.argv
    sys.stdin = io.StringIO(json.dumps(payload))
    sys.stdout = io.StringIO()
    sys.argv = ["agent-elapsed-nag.py", mode]
    try:
        nag.main()
        return sys.stdout.getvalue().strip()
    finally:
        sys.stdin, sys.stdout, sys.argv = old_in, old_out, old_argv


def main():
    with tempfile.TemporaryDirectory() as root:
        os.environ["CLAUDE_AGENT_NAG_ROOT"] = root
        bash = {"session_id": SESSION, "tool_name": "Bash"}
        agent = {"session_id": SESSION, "tool_name": "Agent"}

        # KNOWN NEGATIVE 1: nothing dispatched at all.
        assert nag.run("pre", bash) is None, "spoke with no dispatch open"

        # KNOWN NEGATIVE 2: a dispatch that just started.
        assert nag.run("pre", agent) is None, "the dispatch call itself spoke"
        assert nag.run("pre", bash) is None, "spoke before 30 minutes"

        # KNOWN POSITIVE: the same dispatch, 31 minutes old.
        backdate(root, 31)
        text = nag.run("pre", bash)
        assert text and "31 minutes" in text, f"stayed silent past 30 minutes: {text!r}"

        # No repeat inside the same 30-minute bucket.
        assert nag.run("pre", bash) is None, "nagged twice in one bucket"

        # Speaks again at the next bucket.
        backdate(root, 31)
        text = nag.run("pre", bash)
        assert text and "62 minutes" in text, f"missed the second bucket: {text!r}"

        # SubagentStop clears it; silence returns.
        assert nag.run("stop", {"session_id": SESSION}) is None
        assert nag.run("pre", bash) is None, "still nagging after the agent stopped"

        # Two open dispatches name the count.
        nag.run("pre", agent)
        nag.run("pre", agent)
        backdate(root, 31)
        text = nag.run("pre", bash)
        assert "2 dispatches open" in text, f"lost the dispatch count: {text!r}"

        # main() emits exactly one JSON object, in the shape the docs specify.
        backdate(root, 31)
        out = emitted("pre", bash)
        assert out.count("\n") == 0, "emitted more than one JSON object"
        got = json.loads(out)["hookSpecificOutput"]
        assert got["hookEventName"] == "PreToolUse"
        assert "[cost]" in got["additionalContext"]

        # Silence is EMPTY stdout, not an empty JSON object.
        assert emitted("pre", bash) == "", "spoke inside the same bucket"

        # A bad payload must never raise.
        assert emitted("pre", {}) == ""

    print("agent-elapsed-nag self-check: all assertions passed")


if __name__ == "__main__":
    main()
