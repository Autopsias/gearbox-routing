#!/usr/bin/env python3
"""Self-supervising runner for long `codex exec` reviews.

WHY THIS EXISTS (measured on a large plan-harden run).
Two `/adversarial-review` verify runs at `--effort xhigh` each did ~9-10 minutes of
real work, then went COMPLETELY SILENT. `codex-companion status` kept reporting
`running`; the job log had not grown in 28 and 20 minutes respectively. Both were
hung, not working. The orchestrator's own Bash wrapper had been killed long before,
so nobody was polling `codex_watchdog.py`, and the hang went unnoticed until a human
looked at log mtimes.

Upstream corroboration (openai/codex issues):
  * #31376 — "`codex exec` hangs indefinitely mid-run on a dead pooled connection
    (socket in CLOSE_WAIT, no read timeout fires, no retry)". Worked ~23 min, stalled
    70+. Process alive, 0.00s CPU delta, `-o` file never created, and the documented
    `stream_idle_timeout_ms` (default 300000) never fired on that path.
  * #20919 / #27019 — `codex exec` blocks forever reading stdin when stdin is a
    non-TTY pipe with no writer, or under TERM=dumb.
  * #19945 — silent 0-byte exit when stdio is detached from a TTY and the prompt is long.

THE WRONG FIX is to shrink the prompt or drop the effort tier: that degrades the
review to dodge a transport bug, and it does not even reliably help (our smallest
prompt, 5 KB, hung too). The RIGHT fix is to make a stall RECOVERABLE, so full-fidelity
requests stay full fidelity:

  1. Feed the prompt on stdin with `-` so stdin has a real writer that closes
     (dodges #20919/#27019) and no ARG_MAX ceiling on prompt size.
  2. `--json` so stdout is a JSONL event stream — a high-resolution growth signal.
  3. Supervise growth of that stream IN-PROCESS. No growth for `--idle-timeout`
     seconds ⇒ the run is hung ⇒ kill the whole process group.
  4. RESUME rather than restart (`codex exec resume <thread_id>`, the id read from
     the JSONL `thread.started` event — never `--last`, which grabs whichever
     parallel runner started most recently), so a stall costs the idle window, not
     the work already done. This is what lets `xhigh` stay `xhigh`.
  5. Bound it: `--max-attempts`, `--total-deadline`, and a hard success test that the
     `-o` last-message file exists and is non-empty.

Complements `codex_watchdog.py`, which stays the right tool when the ORCHESTRATOR owns
the poll loop. This script is for when nothing is guaranteed to be watching.

Usage:
    codex_supervised.py --prompt-file P --out O [--effort xhigh] [--model M]
        [--cwd DIR] [--idle-timeout 600] [--max-attempts 3] [--total-deadline 5400]
        [--sandbox read-only] [--log L]

Prints one JSON status object to stdout. Exit 0 = usable output produced.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

POLL_S = 10
GRACE_S = 10  # SIGTERM -> SIGKILL grace


def _spawn(argv, prompt_bytes, log_path, cwd, env):
    """Start codex detached in its own process group, prompt on stdin, JSONL to log."""
    log = open(log_path, "ab", buffering=0)
    proc = subprocess.Popen(
        argv,
        stdin=subprocess.PIPE,
        stdout=log,
        stderr=subprocess.STDOUT,
        cwd=cwd,
        env=env,
        start_new_session=True,  # own process group: we can kill codex AND its children
    )
    try:
        proc.stdin.write(prompt_bytes)
    except BrokenPipeError:
        pass
    finally:
        try:
            proc.stdin.close()  # deliver EOF — the #20919 deadlock is a missing EOF
        except OSError:
            pass
    return proc, log


def _kill_group(proc):
    """SIGTERM the group, then SIGKILL. codex spawns helpers; killing the pid alone leaks them."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(os.getpgid(proc.pid), sig)
        except (ProcessLookupError, PermissionError):
            return
        deadline = time.time() + (GRACE_S if sig == signal.SIGTERM else 5)
        while time.time() < deadline:
            if proc.poll() is not None:
                return
            time.sleep(0.5)


def _supervise(proc, log_path, idle_timeout, hard_deadline):
    """Watch JSONL growth. Returns 'exited' | 'idle' | 'deadline'."""
    last_size, last_growth = -1, time.time()
    while True:
        if proc.poll() is not None:
            return "exited"
        now = time.time()
        if now > hard_deadline:
            _kill_group(proc)
            return "deadline"
        try:
            size = log_path.stat().st_size
        except FileNotFoundError:
            size = 0
        if size > last_size:
            last_size, last_growth = size, now
        elif now - last_growth > idle_timeout:
            # THE #31376 SIGNATURE: alive, zero output growth. Alive != working.
            _kill_group(proc)
            return "idle"
        time.sleep(POLL_S)


def thread_id_from_log(log_path) -> str | None:
    """First `thread.started` id in the JSONL event log, else None.

    Resume MUST target this id, never `--last`: several runners run in parallel
    (plan-harden targeted mode dispatches one per unit), and `resume --last`
    attaches to whichever session most recently started — another unit's thread.
    A wrong-target resume looks exactly like a successful one. (Found by comparing
    against chaseai-yt/claudex-loop, 2026-08-25; they observed the same.)
    """
    try:
        with open(log_path, "rb") as fh:
            for raw in fh:
                if b"thread.started" not in raw:
                    continue
                try:
                    ev = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(ev, dict) and ev.get("type") == "thread.started":
                    tid = ev.get("thread_id")
                    if isinstance(tid, str) and tid:
                        return tid
    except OSError:
        pass
    return None


def attempt_argv(n, flags, thread_id):
    """argv for attempt n. n==1 or no captured thread id -> fresh exec.

    No id means the prior attempt died before codex even opened a session, so
    there is nothing to resume and `--last` would only grab a stranger's thread.
    """
    if n == 1 or not thread_id:
        return ["codex", "exec"] + flags + ["-"], "fresh"
    return ["codex", "exec", "resume", thread_id] + flags + ["-"], "resume"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt-file", required=True)
    ap.add_argument("--out", required=True, help="codex -o last-message file (success test)")
    ap.add_argument("--log", default=None, help="JSONL event log (default: <out>.jsonl)")
    ap.add_argument("--model", default=None)
    ap.add_argument("--effort", default="xhigh")
    ap.add_argument("--cwd", default=os.getcwd())
    ap.add_argument("--sandbox", default="read-only")
    ap.add_argument("--idle-timeout", type=int, default=600)
    ap.add_argument("--max-attempts", type=int, default=3)
    ap.add_argument("--total-deadline", type=int, default=5400)
    a = ap.parse_args()

    prompt_bytes = Path(a.prompt_file).read_bytes()
    out = Path(a.out)
    log = Path(a.log) if a.log else Path(str(out) + ".jsonl")
    if out.exists():
        out.unlink()
    log.write_bytes(b"")

    env = dict(os.environ)
    # TERM=dumb is a documented trigger for the stdin-read hang (#27019).
    if env.get("TERM", "dumb") == "dumb":
        env["TERM"] = "xterm-256color"

    # `codex exec resume` does NOT accept `--sandbox` (verified against
    # codex-cli: "error: unexpected argument '--sandbox' found"), while it DOES
    # accept -c/-m/-o/--json. Passing the sandbox as a config key keeps ONE flag set that
    # is legal on both the fresh and the resume path — the alternative, two divergent
    # argvs, is what silently broke the recovery path on its first real test.
    flags = ["--json", "--skip-git-repo-check", "-o", str(out),
             "-c", f'sandbox_mode="{a.sandbox}"',
             "-c", f"stream_idle_timeout_ms={a.idle_timeout * 1000}"]
    if a.model:
        flags += ["-m", a.model]
    if a.effort:
        flags += ["-c", f"model_reasoning_effort={a.effort}"]

    started = time.time()
    hard_deadline = started + a.total_deadline
    attempts = []

    for n in range(1, a.max_attempts + 1):
        # RESUME the captured thread, don't restart: the prior attempt's work is
        # still in the session. Never `--last` (see thread_id_from_log).
        argv, mode = attempt_argv(n, flags, thread_id_from_log(log) if n > 1 else None)
        if mode == "fresh":
            stdin_payload = prompt_bytes
        else:
            stdin_payload = (
                b"Continue and FINISH the task from this session. The previous attempt was "
                b"killed by a supervisor after stalling with no output; that is a transport "
                b"fault, not a signal about the task. Do not restart from scratch and do not "
                b"re-explore. Produce the final answer now, in the exact output format the "
                b"original prompt specified.\n"
            )
        t0 = time.time()
        proc, fh = _spawn(argv, stdin_payload, log, a.cwd, env)
        why = _supervise(proc, log, a.idle_timeout, hard_deadline)
        fh.close()
        rc = proc.poll()
        attempts.append({"attempt": n, "mode": mode, "thread_id": thread_id_from_log(log),
                         "outcome": why, "returncode": rc,
                         "seconds": round(time.time() - t0, 1),
                         "log_bytes": log.stat().st_size if log.exists() else 0})

        ok = out.exists() and out.stat().st_size > 0
        if ok:
            print(json.dumps({"status": "completed", "attempts": attempts,
                              "out": str(out), "out_bytes": out.stat().st_size,
                              "seconds": round(time.time() - started, 1)}, indent=2))
            return 0
        if why == "deadline":
            break

    print(json.dumps({"status": "failed", "reason": "no non-empty output produced",
                      "attempts": attempts, "out": str(out), "log": str(log),
                      "seconds": round(time.time() - started, 1)}, indent=2))
    return 1


if __name__ == "__main__":
    sys.exit(main())
