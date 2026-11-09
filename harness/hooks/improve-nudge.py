#!/usr/bin/env python3
"""Nudge the agent to offer /improve at the right moments (NUDGE-01).

Two modes, one line of plain stdout each (plain stdout from UserPromptSubmit and
SessionStart is added as context). Silent otherwise: no output is the all-clear.

  prompt         UserPromptSubmit. Counts pushback prompts per session with a frozen
                 regex; at the third, prints one line, once per session.
  session-start  SessionStart, registered with matcher `startup` only. When the stamp
                 <state>/last-full-scope is older than 30 days and the last reminder is
                 older than 7 days, prints one line. A missing stamp is seeded, silently,
                 so the first reminder comes 30 days after deploy.
  finished       PostToolUse, matcher `Bash`. When the main agent (no `agent_id`) runs a
                 `git push`, `gearbox deploy` or `gh pr merge`, prints one JSON
                 additionalContext line, once per session, and never after a prompt nudge.
                 Owner chose this trigger (docs/worth-adopting/
                 2026-11-08-improve-nudge-early-verdict.md, option B). With a transcript_path,
                 it writes a digest (scripts/improve_digest.py) to <state>/digests/ and asks
                 the agent for a Sonnet semantic preview first (option A of the addendum);
                 it stays silent if /improve already ran in the session.

State: ~/.gearbox-state/improve/ (override IMPROVE_NUDGE_STATE_DIR). Kill switch:
IMPROVE_NUDGE=off or the file <state>/DISABLED. Fails open: any error prints nothing
and exits 0. fired.log (append-only, never pruned) records every print.
Prompts of one session arrive one after another, so the counter takes no lock.
"""

import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Frozen: do not widen or narrow. Bare don't/stop/no words match ordinary instructions.
CORR = re.compile(r"(?i)(^\s*no\b[,.!]|\bwrong\b|i don'?t understand|what do you mean|not what i|why did you|i told you|you said|that'?s not|didn'?t work|still (broken|failing|wrong)|doesn'?t work|you missed|you forgot|don'?t do that|stop doing)")

MAX_PROMPT = 600
THRESHOLD = 3
DAY = 86400
STALE_STAMP = 30 * DAY
THROTTLE = 7 * DAY
PRUNE_AGE = 14 * DAY

PROMPT_LINE = ("improve-nudge: 3 pushback prompts from the owner in this session. "
               "When the current task is done, offer /improve on this conversation in one line. "
               "Do not run it unasked.")
START_LINE = ("improve-nudge: no full-scope /improve in 30+ days. "
              "Offer the owner /improve (historical scope) in one line.")
FINISHED = re.compile(r"\bgit(?:\s+-[cC]\s+\S+|\s+--?\S+)*\s+push\b|\bgearbox deploy\b|\bgh pr merge\b"
                      r"|\bgh release create\b|\b(?:npm|pnpm|yarn|uv|twine) (?:publish|upload)\b"
                      r"|(?:^|[;&|(\n]\s*)(?:\S*(?:python|bash|sh)\S*\s+)?(?:\S*/)?(?:deploy\.sh|publish_public\.py)\b")
# Automated sessions (claude -p / SDK, codex exec, Codex run from Claude Code) get no offer.
AUTOMATED = ("sdk", "codex_exec", "Claude Code")
HOWTO = {"claude": ("/improve", "dispatch ONE subagent (subagent_type general-purpose, model sonnet)"),
         "codex": ("$improve-harness", "spawn ONE subagent (if you cannot spawn one, skip the hints)")}
FINISH_LINE = ("improve-nudge: this session pushed, merged or deployed its work. "
               "In the reply that closes the task, offer {improve} on this conversation in one line. "
               "Do not run it unasked.")
PREVIEW_LINE = ("improve-nudge: this session pushed, merged or deployed its work. Its condensed "
                "transcript is at {digest}. Before the reply that closes the task, {reader} "
                "with the prompt in ~/.claude/references/improve/triage-prompt.md, with <DIGEST> set "
                "to that path. In the closing reply, show its hints (one line each, with the quote), "
                "then offer {improve} on this conversation in one line. Do not run {improve} unasked.")


def state_dir() -> Path:
    return Path(os.environ.get("IMPROVE_NUDGE_STATE_DIR") or Path.home() / ".gearbox-state" / "improve")


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _read_stdin():
    try:
        return json.loads(sys.stdin.read())
    except Exception:
        return None


def _session_id(data) -> str:
    sid = data.get("session_id") if isinstance(data, dict) else None
    return sid if isinstance(sid, str) else ""


def _write_atomic(path: Path, text: str) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def _log(sd: Path, mode: str, sid_hash: str) -> None:
    line = f"{datetime.now(timezone.utc).isoformat()}\t{mode}\t{sid_hash}\t{os.getcwd()}\n"
    with open(sd / "fired.log", "a") as f:
        f.write(line)


def _prune(sessions: Path) -> None:
    cutoff = time.time() - PRUNE_AGE
    for p in sessions.iterdir():
        try:
            if p.stat().st_mtime < cutoff:
                p.unlink()
        except OSError:
            pass


def _is_pushback(prompt: str) -> bool:
    if len(prompt) > MAX_PROMPT or prompt.startswith("<"):
        return False
    return bool(CORR.search(prompt.replace("’", "'")))


def mode_prompt(sd: Path) -> None:
    data = _read_stdin()
    sid = _session_id(data)
    prompt = data.get("prompt") if isinstance(data, dict) else None
    if not sid or not isinstance(prompt, str):
        return
    sessions = sd / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    _prune(sessions)
    if not _is_pushback(prompt):
        return
    h = _sha(sid)
    f = sessions / h
    try:
        n, nudged = f.read_text().split()
        n, nudged = int(n), nudged == "1"
    except (OSError, ValueError):
        n, nudged = 0, False
    n += 1
    if n >= THRESHOLD and not nudged:
        _log(sd, "prompt", h)
        _write_atomic(f, f"{n} 1")
        print(PROMPT_LINE)
    else:
        _write_atomic(f, f"{n} {int(nudged)}")


def mode_session_start(sd: Path) -> None:
    data = _read_stdin()
    sid = _session_id(data)
    h = _sha(sid) if sid else "-"
    sd.mkdir(parents=True, exist_ok=True)
    sessions = sd / "sessions"
    if sessions.is_dir():
        _prune(sessions)
    stamp, last = sd / "last-full-scope", sd / "last-start-nudge"
    now = time.time()
    try:
        stamp_age = now - stamp.stat().st_mtime
    except OSError:
        stamp.touch()  # first run: seed, stay silent
        return
    if stamp_age <= STALE_STAMP:
        return
    try:
        if now - last.stat().st_mtime <= THROTTLE:
            return
    except OSError:
        pass
    _log(sd, "session-start", h)
    last.touch()
    print(START_LINE)


def mode_finished(sd: Path) -> None:
    data = _read_stdin()
    sid = _session_id(data)
    if not sid or data.get("agent_id"):
        return
    ti = data.get("tool_input")
    if not isinstance(ti, dict):
        return
    cmd = ti.get("command")
    if not isinstance(cmd, str):
        cmd = json.dumps(ti)  # shortcut: Codex exec input shape is undocumented, search it whole
    if not FINISHED.search(cmd):
        return
    sessions = sd / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    h = _sha(sid)
    done = sessions / f"{h}.finished"
    if done.exists():
        return
    done.touch()
    try:
        if (sessions / h).read_text().split()[1] == "1":
            return  # the prompt nudge already asked for the same offer
    except (OSError, IndexError):
        pass
    improve, reader = HOWTO["codex" if "turn_id" in data else "claude"]
    line = FINISH_LINE.format(improve=improve)
    tp = data.get("transcript_path")
    if isinstance(tp, str) and tp:
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
            from improve_digest import condense
            digest = condense(os.path.expanduser(tp))
            if digest.improve_ran or digest.origin.startswith(AUTOMATED):
                return  # /improve already ran, or nobody is there to read the offer
            ddir = sd / "digests"
            ddir.mkdir(parents=True, exist_ok=True)
            _prune(ddir)
            out = ddir / f"{h}.txt"
            out.write_text(digest.text)
            line = PREVIEW_LINE.format(digest=out, reader=reader, improve=improve)
        except Exception:
            pass  # no preview, plain offer
    _log(sd, "finished", h)
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                             "additionalContext": line}}))


def main() -> int:
    try:
        if os.environ.get("IMPROVE_NUDGE") == "off":
            return 0
        sd = state_dir()
        if (sd / "DISABLED").exists():
            return 0
        mode = sys.argv[1] if len(sys.argv) > 1 else ""
        if mode == "prompt":
            sd.mkdir(parents=True, exist_ok=True)
            mode_prompt(sd)
        elif mode == "session-start":
            mode_session_start(sd)
        elif mode == "finished":
            mode_finished(sd)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
