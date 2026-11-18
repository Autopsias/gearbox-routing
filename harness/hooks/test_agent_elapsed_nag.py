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
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(HERE, "agent-elapsed-nag.py")

spec = importlib.util.spec_from_file_location("nag", HOOK)
assert spec is not None and spec.loader is not None
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


def bg_age(root, minutes):
    path = os.path.join(root, "bg-session.bash.json")
    with open(path, encoding="utf-8") as fh:
        d = json.load(fh)
    for v in d.values():
        v["t"] -= minutes * 60
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(d, fh)


def test_background_bash():
    with tempfile.TemporaryDirectory() as root:
        os.environ["CLAUDE_AGENT_NAG_ROOT"] = root
        sid = "bg-session"

        def mk(**ti):
            return {"session_id": sid, "tool_name": "Bash", "tool_use_id": "t1", "tool_input": ti}

        other = {"session_id": sid, "tool_name": "Read", "tool_input": {}}
        # foreground Bash (flag absent or false) never arms or tracks
        assert nag.run("pre", mk(command="ls")) is None
        assert nag.run("pre", mk(command="ls", run_in_background=False)) is None
        assert not os.path.exists(os.path.join(root, sid + ".bash.json"))
        # start returns the wake-up context
        text = nag.run("pre", mk(command="sleep 9999", run_in_background=True))
        assert text and "CronCreate" in text and "30 minutes" in text
        assert nag.run("pre", mk(command="sleep 1", run_in_background=True)) is None, \
            "the arm-a-timer text repeated in one session"
        nag.run("stopbg", {"session_id": sid, "background_tasks": [{"status": "running", "command": "sleep 9999"}]})
        assert nag.run("pre", other) is None, "spoke at 0 minutes"
        bg_age(root, 5)
        assert nag.run("pre", other) is None, "spoke for a 5-minute task"
        bg_age(root, 26)
        text = nag.run("pre", other)
        assert text and "31 minutes" in text and "sleep 9999" in text
        assert nag.run("pre", other) is None, "nagged twice in one bucket"
        # Stop snapshot still lists it: kept
        run_ = {"session_id": sid, "background_tasks": [{"status": "running", "command": "sleep 9999"}]}
        nag.run("stopbg", run_)
        bg_age(root, 31)
        assert nag.run("pre", other), "dropped a task that is still running"
        # gone from the Stop list: silent after
        bg_age(root, 31)
        nag.run("stopbg", {"session_id": sid, "background_tasks": []})
        assert nag.run("pre", other) is None, "nagged about a finished task"
        # a Stop payload without the field leaves entries alone
        nag.run("pre", mk(command="x", run_in_background=True))
        bg_age(root, 31)
        nag.run("stopbg", {"session_id": sid})
        assert nag.run("pre", other), "dropped entry without a background_tasks field"
        # older than 24 h: dropped, silent
        bg_age(root, 25 * 60)
        assert nag.run("pre", other) is None, "nagged about a 24h+ task"
        # same command twice: Stop keeps as many entries as it shows running, newest first.
        # A re-run after a failure is the common twin, and the ended one is the older; its
        # start time must not date the reminder (2026-11-17: 63 minutes said, 20 real).
        def bgs(tid, cmd):
            return {"session_id": sid, "tool_name": "Bash", "tool_use_id": tid,
                    "tool_input": {"command": cmd, "run_in_background": True}}

        def kept():
            with open(os.path.join(root, sid + ".bash.json"), encoding="utf-8") as fh:
                return sorted(json.load(fh))
        one = {"status": "running", "command": "pytest"}
        nag.run("pre", bgs("p1", "pytest"))
        bg_age(root, 60)
        nag.run("pre", bgs("p2", "pytest"))
        nag.run("stopbg", {"session_id": sid, "background_tasks": [one, one]})
        assert kept() == ["p1", "p2"], "dropped a twin while both run"
        nag.run("stopbg", {"session_id": sid, "background_tasks": [one]})
        assert kept() == ["p2"], "kept the ended twin's start time"
        assert nag.run("pre", other) is None, "reminded with the ended twin's 60 minutes"
        bg_age(root, 31)
        text = nag.run("pre", other)
        assert text and "31 minutes" in text, f"newer running twin lost its reminder: {text}"
        nag.run("stopbg", {"session_id": sid, "background_tasks": []})
        assert nag.run("pre", other) is None, "nagged after every twin ended"
        # parallel starts: the lock keeps every task
        nag.run("stopbg", {"session_id": sid, "background_tasks": []})
        threads = [threading.Thread(target=nag.run, args=("pre", bgs(f"c{i}", f"job {i}"))) for i in range(8)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        with open(os.path.join(root, sid + ".bash.json"), encoding="utf-8") as fh:
            assert len(json.load(fh)) == 8, "a parallel start lost a task"
        # the agent FIFO is untouched by all of this
        assert nag.run("pre", {"session_id": sid, "tool_name": "Agent", "tool_input": {"run_in_background": False}}) is None
        # malformed input exits clean with no output
        for raw in ("", "not json", "[]", '{"tool_input": 5}'):
            old_in, old_out, old_argv = sys.stdin, sys.stdout, sys.argv
            sys.stdin, sys.stdout, sys.argv = io.StringIO(raw), io.StringIO(), ["x", "pre"]
            try:
                nag.main()
                assert sys.stdout.getvalue() == ""
            finally:
                sys.stdin, sys.stdout, sys.argv = old_in, old_out, old_argv


def main():
    test_background_bash()
    with tempfile.TemporaryDirectory() as root:
        os.environ["CLAUDE_AGENT_NAG_ROOT"] = root
        bash = {"session_id": SESSION, "tool_name": "Bash"}
        agent = {"session_id": SESSION, "tool_name": "Agent",
                 "tool_input": {"run_in_background": False}}
        bg_agent = {"session_id": SESSION, "tool_name": "Agent"}

        # KNOWN NEGATIVE 1: nothing dispatched at all.
        assert nag.run("pre", bash) is None, "spoke with no dispatch open"

        # KNOWN NEGATIVE 2: a foreground dispatch that just started.
        assert nag.run("pre", agent) is None, "a foreground dispatch call spoke"
        nag.run("stop", {"session_id": SESSION})

        # KNOWN POSITIVE: a background dispatch (the default) asks for a +30 min timer,
        # because an idle orchestrator runs no tools for this hook to speak through.
        text = nag.run("pre", bg_agent)
        assert text and "CronCreate" in text and "30 minutes" in text, f"no timer prompt: {text!r}"
        nag.run("stop", {"session_id": SESSION})
        assert nag.run("pre", bg_agent) is None, "the arm-a-timer text repeated in one session"
        nag.run("stop", {"session_id": SESSION})
        assert nag.run("pre", agent) is None
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
