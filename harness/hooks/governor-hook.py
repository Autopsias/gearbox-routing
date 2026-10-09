#!/usr/bin/env python3
"""governor-hook — PreToolUse(Bash): refuse a heavy command that skipped govrun.

WHY THIS EXISTS. `scripts/govrun.py` caps how many test workers run on this
machine at once, but it only governs jobs that go THROUGH it. A session that
types `pytest -n 8` directly gets the whole machine, which is the incident this
plan exists to stop. This hook is the enforcement half: it sees every Bash tool
call before it runs, and denies a heavy one that is not wrapped, naming the
exact fix so the session can re-issue it.

THE DECISION CONTRACT (docs, https://code.claude.com/docs/en/hooks, verified
2026-08-28). A deny is stdout JSON `hookSpecificOutput.permissionDecision:
"deny"` plus `permissionDecisionReason`, exit 0. An ALLOW here is deliberately
SILENT — exit 0 with no JSON — because a `"deny"`'s opposite, `"allow"`,
"bypasses the permission system entirely" and would auto-approve commands that
should still prompt. Staying silent lets the normal permission flow run.

WHY EVERYTHING SLOW IS OFF THE HOT PATH. The same docs: a command hook that
reaches its timeout "is canceled ... the hook renders no decision", and "don't
count on a stalled hook to act as a gate". A hook that stalls therefore DISARMS
enforcement silently — and it would stall under exactly the swap-thrash this
governor exists to prevent. So: no `sys.path` surgery, no import of govrun, no
subprocess. The worker budget is an INLINED LITERAL below, and the only
filesystem read on the decision path is one small `config.json`.

The ONE import that is not stdlib is `governor_parse`, the sibling this file's
command parsing was extracted into (this file had reached 545
lines against a 500-line ratchet, and the operator chose extraction over an
exceptions entry). It is a sibling in this same directory, imports only stdlib
and reads no file. MEASURED against a monolithic rebuild of the
same code, 40 paired subprocess runs: the split is 1.07 ms FASTER at the paired
median and inside the noise at p95 (see governor_parse's own docstring for why
the sign comes out that way). That is not the class of cost this paragraph
guards against; an unbounded stall is.

THREE ERROR RULES, which are not opposites:
  * config.json unreadable or unparseable is NOT an error. It means the mode is
    unknown, and an unknown mode falls back to the DEFAULT mode, `deny`.
  * `governor_parse` failing to import is NOT a fail-open. Without it this hook
    cannot tell `pytest -n 8` from `git status`, and that is PERMANENT and
    machine-wide until someone restores the file — the opposite of the
    single-command slip the rule below covers. It DENIES every Bash call, says
    why, names the file, and leaves `GOVRUN_BYPASS=1` as the way through. See
    `disarmed`.
  * Any OTHER exception fails OPEN (allow, exit 0) and leaves a trace in
    `<state dir>/hook-errors.log`. A crashing guard must never brick every Bash
    call on the machine; a silently disarmed one must still be visible to
    `govrun --status`, which prints the tail of that log.

WHERE THE COMMAND IS TAKEN APART. Not here, and no longer even in this file:
`governor_parse.py` beside it. Quoting, grouping, operators and comments all
come from `shlex`, the stdlib's shell lexer, because a hand-written scanner
only knows the constructs someone already thought of and every one it misses is
a silent bypass. That module's `CommandText` is the whole delta from shlex.

COVERAGE BOUNDARY. This hook sees Bash TOOL calls only. It does NOT see
plan-execute's verify gates (they run `subprocess.run(shell=False)` from
verify.py, not through a tool), a human typing in Terminal, or a heavy command
hidden inside a wrapper script — or inside a QUOTED string, e.g.
`bash -c 'pytest -n 8'` — whose visible tokens are not `pytest`/`-n`. That is
the unavoidable price of the precision that lets `grep -rn 'docker build' .`
through. Those paths are governed by WRAPPING (s04), not by this hook.

The heavy command has to LEAD its segment, so a wrapper in front of it hides it
unless the wrapper is on the SHORT, EXPLICIT list in `governor_parse`'s
`TRANSPARENT`. The list is deliberately closed, not a general solution.
RESIDUAL GAP, for s04 to document: `bash -c …`, `sh -c …`, `xargs`, `sudo`,
`make test`, `npm run …`, `just`, `tox`, `nohup`, `timeout 600 …`, `stdbuf`,
and any project script that runs pytest or docker internally are all OPAQUE —
the hook renders no decision on them. A transparent wrapper's OWN flags are
skipped (`nice -n 10 pytest -n 8` is seen since 2026-09-08), and env's `-S`,
which re-splits ONE quoted string into the command, is put back into words
(`env -S 'pytest -n 8'` is seen). `bash -c '…'` hands its string to a shell,
not to a word split, and stays opaque: that is where the closed list ends.

WIRING (settings.json, hooks.PreToolUse, matcher "Bash"):

    {"type": "command",
     "command": "p=\"$HOME/.claude/hooks/governor-hook.py\"; [ -f \"$p\" ] || exit 0; python3 \"$p\"; s=$?; [ $s -eq 0 ] || { echo \"governor-hook: present but exited $s without rendering a decision - refusing this Bash call. Fix or remove $p.\" >&2; exit 2; }",
     "timeout": 5}

`timeout` is explicit because a hook TIMEOUT IS AN ALLOW — see above. Five
seconds is ~25x the 200 ms budget this hook's own latency test enforces, so
hitting it means something is very wrong, not merely slow.

The `[ -f "$p" ]` guard stays: a hook that is not installed must not break
Bash. What changed is the case where it IS installed and cannot
run — a syntax error, a missing sibling, no python3. That used to `exec` and
exit non-zero, and the docs (verified) say any non-zero code OTHER
than 2 is a NON-blocking error: "the action proceeds". So the wrapper no longer
execs; it reads the status and re-raises it as exit 2, which "blocks whether or
not you print JSON". Two independent layers now cover the same hole — this one,
and `disarmed` inside the hook — because the wrapper only exists in the
deployed settings.json and the hook is also run directly by `govrun --status`
and by the tests.

Self-check: hooks/test_governor_hook.py, hooks/test_governor_parse.py
"""
import datetime
import json
import os
import sys
from pathlib import Path

# The parsing half, extracted 2026-09-08 (see the docstring). The hook is run as
# a SCRIPT, so `sys.path[0]` is this directory and a plain import finds the
# sibling with no path surgery — verified from three working
# directories, and again from a deploy-shaped directory by
# test_governor_parse.py. The `try` is not defensiveness: an ImportError at
# module level would exit non-zero with ZERO bytes on stdout, and no JSON means
# no decision, which is a machine-wide SILENT ALLOW (measured).
# `fail_open` cannot catch this — it is defined further down this same module,
# so it does not exist yet when the import runs.
try:
    from governor_parse import (ENV_S_UNRESOLVED, docker_subcommand,
                                numprocesses, peel, pytest_args, segments)
except Exception as exc:  # noqa: BLE001 — a broken sibling is not only ImportError
    PARSE_IMPORT_ERROR = exc
else:
    PARSE_IMPORT_ERROR = None

# The per-slot worker budget, INLINED (see the module docstring: importing
# govrun would put an unbounded stall on the decision path). It must equal the
# `workers` in govrun_config.DEFAULT_SLOTS; `test_the_inlined_budget_matches_govrun`
# pins that, and `govrun --status` prints both and marks them AGREE/DIVERGED.
# CONTRACT with scripts/govrun_status.py: this must stay a module-level
# `SLOT_WORKERS = <int>` line — that file reads it back with a regex.
SLOT_WORKERS = 4

REMEDY = (
    "machine worker budget: run via `~/.claude/scripts/govrun <your command>`, "
    "and set this Bash call's timeout to 600000 — govrun may queue behind "
    "another job. Exit 75 means queue timeout, not a test failure."
)
OVER_BUDGET = (
    "machine worker budget: govrun reserves {budget} workers per slot, so a "
    "hardcoded `-n {asked}` overruns it even when wrapped. Use `-n auto` — "
    "govrun sets the worker count itself."
)
BYPASS_TOKEN = "GOVRUN_BYPASS=1"


DISARMED = (
    "governor-hook is DISARMED: its command parser `governor_parse.py` did not "
    "import ({detail}), so it cannot tell a heavy command from a harmless one. "
    "It refuses every Bash call rather than waving all of them through. FIX: "
    "restore hooks/governor_parse.py beside hooks/governor-hook.py — a normal "
    "`gearbox deploy` carries both, since ~/.claude is a clone of the repo. To "
    "run one command anyway, prefix it with "
) + BYPASS_TOKEN + "."

PYTEST_NAMES = {"pytest", "py.test"}


# --- state -------------------------------------------------------------------

def state_dir():
    """govrun's state dir. Never created here — this path only reads."""
    return Path(os.environ.get("GOVRUN_STATE_DIR")
                or Path.home() / ".machine-governor")


# --- decision ----------------------------------------------------------------

def heavy(segment, index, earlier=()):
    """`(what, workers)` if the command at `index` is a heavy job, else None.

    `workers` is an int, `"auto"`, or None (docker). BOTH patterns demand the
    real COMMAND TOKEN, never a token found anywhere in the segment: a looser
    test calls `grep -rn 'docker build' .` a build and `echo pytest -n 8` a test
    run, and refuses them with no way out.
    """
    if index is None:
        return None
    command = os.path.basename(segment[index])
    if command == "docker" and docker_subcommand(segment[index + 1:]) == "build":
        return "`docker build`", None
    if command in PYTEST_NAMES:
        workers = numprocesses(pytest_args(segment, index, earlier))
        if workers is not None:
            return "`pytest` with -n/--numprocesses", workers
    return None


def decide(command):
    """`("deny", reason)`, `("bypass", command)`, or None to stay silent."""
    segs = segments(command)
    if not segs:
        return None
    if segs[0][0] == BYPASS_TOKEN:
        return "bypass", command
    for position, segment in enumerate(segs):
        if segment == [ENV_S_UNRESOLVED]:
            return "deny", ("`env -S` nested too deep to resolve; refusing "
                            f"(fail closed, not an opaque allow). {REMEDY}")
        index, govrun_leads = peel(segment)
        hit = heavy(segment, index, segs[:position])
        if hit is None:
            continue
        what, workers = hit
        if not govrun_leads:
            return "deny", f"{what} is a heavy job and is not wrapped. {REMEDY}"
        if isinstance(workers, int) and workers > SLOT_WORKERS:
            return "deny", OVER_BUDGET.format(budget=SLOT_WORKERS, asked=workers)
    return None


def hook_mode():
    """`"deny"` (the default) or `"warn"`, from `<state dir>/config.json`.

    A file we cannot read or parse is not an error — it leaves the mode UNKNOWN,
    and an unknown mode falls back to the default. Only read/parse failures are
    swallowed here; anything else propagates to the fail-open handler.
    """
    try:
        config = json.loads((state_dir() / "config.json").read_text())
    except (OSError, ValueError):
        return "deny"
    if isinstance(config, dict) and config.get("hook_mode") == "warn":
        return "warn"
    return "deny"


# --- output ------------------------------------------------------------------

def emit(payload):
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def deny(reason):
    emit({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                 "permissionDecision": "deny",
                                 "permissionDecisionReason": reason}})


def warn(reason):
    """Burn-in mode: allow, but say the same thing a deny would have said.

    `systemMessage` is a TOP-LEVEL universal field (docs, verified);
    inside `hookSpecificOutput` it is an unknown key and the operator sees
    nothing. stderr is kept only as a debug-log breadcrumb — the same docs say
    an exit-0 hook's stderr never reaches the transcript, so it is not the
    channel that carries the warning.
    """
    message = f"governor (warn mode, would DENY): {reason}"
    emit({"systemMessage": message})
    print(f"governor-hook: {message}", file=sys.stderr)


def log(line):
    """Append to the log `govrun --status` tails. Never raises."""
    stamp = datetime.datetime.now().isoformat(timespec="seconds")
    try:
        path = state_dir()
        path.mkdir(parents=True, exist_ok=True)
        with open(path / "hook-errors.log", "a", encoding="utf-8") as handle:
            handle.write(f"{stamp} {line}\n")
    except Exception:  # a full disk must not turn a fail-open into a crash
        pass


def fail_open(exc):
    """Allow, and leave a trace. An invisible fail-open is indistinguishable
    from a healthy hook, which is how enforcement dies unnoticed."""
    detail = f"fail-open {type(exc).__name__}: {exc}"
    print(f"governor-hook: {detail}", file=sys.stderr)
    log(detail)


# --- entry point -------------------------------------------------------------

def read_command(raw):
    """The Bash command in this hook payload, or None if it isn't one."""
    event = json.loads(raw or "{}")
    if not isinstance(event, dict) or event.get("tool_name") != "Bash":
        return None
    tool_input = event.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if not isinstance(command, str) or not command.strip():
        return None
    return command


def disarmed(command):
    """Refuse, loudly, because the parser is gone. See the docstring's three
    error rules: this is the one failure that is permanent and machine-wide, so
    it is the one that denies instead of failing open. `GOVRUN_BYPASS=1` is
    still honoured — recognising a literal prefix needs no parser, and a guard
    with no way out is a guard someone deletes."""
    detail = f"{type(PARSE_IMPORT_ERROR).__name__}: {PARSE_IMPORT_ERROR}"
    log(f"DISARMED governor_parse did not import - {detail}")
    if command.lstrip().startswith(BYPASS_TOKEN):
        print(f"governor-hook: DISARMED ({detail}) but {BYPASS_TOKEN} leads the "
              "command - allowing it", file=sys.stderr)
        return 0
    print(f"governor-hook: DISARMED - {detail}", file=sys.stderr)
    deny(DISARMED.format(detail=detail))
    return 0


def main():
    try:
        command = read_command(sys.stdin.read())
        if command is None:
            return 0
        if PARSE_IMPORT_ERROR is not None:
            return disarmed(command)
        verdict = decide(command)
        if verdict is None:
            return 0
        action, reason = verdict
        if action == "bypass":
            log(f"bypass {BYPASS_TOKEN}: {reason}")
            print(f"governor-hook: {BYPASS_TOKEN} — heavy-command check skipped",
                  file=sys.stderr)
            return 0
        if hook_mode() == "warn":
            warn(reason)
            return 0
        deny(reason)
    except Exception as exc:  # noqa: BLE001 — deliberate: see fail_open
        fail_open(exc)
    return 0


if __name__ == "__main__":
    sys.exit(main())
