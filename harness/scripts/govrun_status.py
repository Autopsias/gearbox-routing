"""govrun's ``--status`` report.

Split out of govrun.py — see its module docstring for what govrun is. This
module owns everything ``--status`` prints: each slot's free/held line (using
the same ``_try_lock`` a real acquirer uses, so the answer is truthful rather
than read off possibly-stale holder metadata), and the PreToolUse hook's
wiring and worker-budget agreement, since the governor hook carries the budget as an
inlined literal that can silently desynchronise from an override here.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from govrun_config import _read_json, _read_text, state_dir
from govrun_errors import Refused
from govrun_locks import _try_lock

# The governor's PreToolUse hook carries the worker budget as an INLINED LITERAL (it must
# stay under ~200 ms, so it cannot import or shell out to us). That literal can
# silently desynchronise from an override here, so `--status` prints both and
# marks them AGREE or DIVERGED. CONTRACT for that hook: define the budget as a
# module-level `SLOT_WORKERS = <int>` so this regex can read it.
HOOK_BUDGET_RE = re.compile(r"^\s*SLOT_WORKERS\s*=\s*(\d+)", re.M)

# The regex above reads a LITERAL out of a file. It cannot tell a hook that
# works from a hook that does not even load: once the hook's parsing
# was split into a sibling module, and a missing sibling would have left this
# report entirely green while enforcement was off — the whole machine
# ungoverned, every health check clean. So `--status` also RUNS the hook and
# reports the decisions it actually renders.
#
# TWO probes, not one, and the pair is the point. A hook that refuses the heavy
# command is not necessarily working — a hook whose parser is missing refuses
# EVERYTHING, which passes a deny-only check while enforcement is in fact
# broken (measured: the deny-only version of this function reported
# ARMED against a hook with no `governor_parse.py` beside it). The control
# separates the two, and it does it without keying on any string in the deny
# reason, so nothing here unpins when that wording is edited.
#
# Timeouts are generous: this is `--status`, not the decision path, and a hook
# that hangs here is itself the finding.
HEAVY_PROBE = "pytest -n 8"      # unwrapped AND over budget: must be refused
CONTROL_PROBE = "git status"     # not heavy at all: must be waved through
PROBE_TIMEOUT_SECONDS = 30


def _probe_payload(command):
    """The PreToolUse payload shape from the hooks docs (verified)."""
    return json.dumps({
        "session_id": "govrun-status", "cwd": "/tmp",
        "permission_mode": "default", "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": command, "description": "govrun --status probe",
                       "timeout": 120000, "run_in_background": False},
        "tool_use_id": "toolu_govrun_status"})


# --- status -----------------------------------------------------------------

def pid_alive(pid):
    if not isinstance(pid, int) or pid <= 0:
        return False  # never signal 0 or a negative: that is a process GROUP
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # alive, owned by someone else
    return True


def _holder_line(slot):
    # Trying the lock is the only TRUTHFUL free/held test — the holder file can
    # be stale, the lock cannot. The fd is closed immediately, so `--status`
    # holds the slot for microseconds; a real acquirer that loses that race just
    # polls again 0.5 s later.
    try:
        held_fd = _try_lock(slot)
    except Refused as exc:
        # `_try_lock` refuses a lock whose file was unlinked or replaced under
        # it. For an ACQUIRER that is fatal and should be; for `--status` it is
        # one slot's line, and suppressing the whole report — hook wiring and
        # budget agreement included — over it would be the "clean bill" this
        # command must never give.
        return f"UNKNOWN ({exc})"
    free = held_fd is not None
    if held_fd is not None:
        os.close(held_fd)
    holder = _read_json(state_dir() / f"{slot['name']}.holder.json")
    if not isinstance(holder, dict):
        return "FREE" if free else "HELD (no holder metadata)"
    alive = pid_alive(holder.get("pid"))
    detail = (f"pid={holder.get('pid')} {'alive' if alive else 'dead'} "
              f"class={holder.get('class')} queued={holder.get('queued_seconds')}s "
              f"since={holder.get('started_at')} cmd={holder.get('command')}")
    if free:
        return f"FREE (stale holder: {detail})"
    return f"HELD {detail}"


def _hook_paths():
    settings = Path(os.environ.get("GOVRUN_SETTINGS")
                    or Path.home() / ".claude" / "settings.json")
    hook = Path(os.environ.get("GOVRUN_HOOK")
                or Path.home() / ".claude" / "hooks" / "governor-hook.py")
    return settings, hook


def _hook_wired(settings):
    """'yes' | 'no' | 'unreadable' | 'malformed (...)'.

    `--status` is the ONLY liveness check for a hook that fails open, so it must
    never crash on a bad settings.json — a traceback and a clean bill are the
    same non-answer, and the file is rewritten by another program (the Claude
    Code binary) so bad shapes are reachable without anyone hand-editing it.
    Every level is type-checked, and a shape we cannot read is REPORTED rather
    than silently downgraded to 'no'.
    """
    data = _read_json(settings)
    if data is None:
        return "unreadable" if Path(settings).exists() else "no (no settings.json)"
    if not isinstance(data, dict):
        return f"malformed (settings.json is a {type(data).__name__}, not an object)"
    hooks = data.get("hooks", {})
    if not isinstance(hooks, dict):
        return f"malformed ('hooks' is a {type(hooks).__name__}, not an object)"
    entries = hooks.get("PreToolUse", [])
    if not isinstance(entries, list):
        return f"malformed ('hooks.PreToolUse' is a {type(entries).__name__}, not a list)"
    for entry in entries:
        if not isinstance(entry, dict) or "Bash" not in str(entry.get("matcher", "")):
            continue
        if "governor-hook" in json.dumps(entry.get("hooks", []), default=str):
            return "yes"
    return "no"


def _probe(hook, command):
    """`(verdict, evidence)` for one real run of the hook. `verdict` is
    `"deny"`, `"warn"`, `"silent"` (which is how this hook ALLOWS — a JSON
    `"allow"` would bypass the permission system entirely) or `"broken"`.

    Never raises. `--status` is the one liveness check for a hook that fails
    open, so a traceback here and a clean bill are the same non-answer.
    """
    try:
        proc = subprocess.run([sys.executable, str(hook)],
                              input=_probe_payload(command),
                              capture_output=True, text=True,
                              timeout=PROBE_TIMEOUT_SECONDS)
    except Exception as exc:  # noqa: BLE001 — OSError, timeout, anything
        return "broken", f"the hook would not run: {type(exc).__name__}: {exc}"
    evidence = (f"exit={proc.returncode} stdout={proc.stdout.strip()[:160]!r} "
                f"stderr={proc.stderr.strip()[:240]!r}")
    try:
        rendered = json.loads(proc.stdout or "{}")
    except ValueError:
        return "broken", evidence
    if not isinstance(rendered, dict):
        return "broken", evidence
    specific = rendered.get("hookSpecificOutput")
    specific = specific if isinstance(specific, dict) else {}
    if specific.get("permissionDecision") == "deny":
        return "deny", str(specific.get("permissionDecisionReason", ""))[:240]
    if "would DENY" in str(rendered.get("systemMessage", "")):
        return "warn", evidence
    if proc.returncode == 0 and not proc.stdout.strip():
        return "silent", evidence
    return "broken", evidence


def hook_enforcement(hook):
    """What the hook ACTUALLY decides — the real operation, not a proxy.
    `'ARMED …'` or `'DISARMED …'`, always carrying the evidence.
    """
    if not Path(hook).is_file():
        return "DISARMED (no hook file - nothing enforces the budget)"
    heavy, heavy_why = _probe(hook, HEAVY_PROBE)
    control, control_why = _probe(hook, CONTROL_PROBE)
    if heavy == "broken":
        return f"DISARMED - `{HEAVY_PROBE}` rendered no decision ({heavy_why})"
    if heavy == "silent":
        return f"DISARMED - `{HEAVY_PROBE}` was NOT refused ({heavy_why})"
    if control != "silent":
        # The hook refuses the heavy command AND the harmless one. That is a
        # hook that cannot read commands at all (a missing parser refuses
        # everything), not a hook that is enforcing.
        return (f"DISARMED - it also refuses `{CONTROL_PROBE}`, so it is "
                f"refusing everything rather than enforcing a budget "
                f"({control}: {control_why})")
    # Burn-in mode allows on purpose and says so; that is enforcement OFF but
    # not enforcement BROKEN, and the two must not read the same.
    if heavy == "warn":
        return "ARMED but in warn mode (it reports and allows; see hook_mode)"
    return "ARMED"


def report_hook(out, slots):
    settings, hook = _hook_paths()
    exists = hook.is_file()
    literal = None
    if exists:
        found = HOOK_BUDGET_RE.search(_read_text(hook))
        literal = int(found.group(1)) if found else None
    budgets = {c: max((s["workers"] for s in slots if c in s["classes"]), default=0)
               for c in sorted({c for s in slots for c in s["classes"]})}
    shown = " ".join(f"{c}={w}" for c, w in budgets.items())
    if literal is None:
        verdict = "UNKNOWN (hook absent, or no SLOT_WORKERS literal in it)"
    elif set(budgets.values()) == {literal}:
        verdict = "AGREE"
    else:
        verdict = "DIVERGED"
    # `shown` is the biggest slot EACH CLASS can be admitted to, which is what
    # the hook's SLOT_WORKERS literal is compared against. It is not a total, and
    # under the shipped one-shared-slot table "ci=4 interactive=4" reads exactly
    # like the 8-worker budget this design exists to prevent. Print the machine
    # total beside it so nobody has to guess which number is the ceiling.
    total = sum(s["workers"] for s in slots)
    out(f"budget: {shown} (per class) | machine total={total} workers"
        f" | hook literal={'absent' if literal is None else literal}"
        f" | {verdict}")
    out(f"enforcement: {hook_enforcement(hook)}")
    out(f"hook: wired={_hook_wired(settings)} file={hook} "
        f"exists={'yes' if exists else 'no'} "
        f"executable={'yes' if exists and os.access(hook, os.X_OK) else 'no'} "
        f"settings={settings}")


def report_errors(out):
    """The hook fails OPEN, so this log is the ONLY sign it is misbehaving."""
    log = state_dir() / "hook-errors.log"
    if not log.is_file():
        return
    lines = _read_text(log).splitlines()[-3:]
    out(f"hook-errors.log (last 3 of {log}):")
    for line in lines:
        out(f"  {line}")


def cmd_status(slots):
    out = print
    out("govrun status")
    out(f"state dir: {state_dir()}")
    for slot in slots:
        out(f"slot {slot['name']} workers={slot['workers']} "
            f"classes={','.join(slot['classes'])} {_holder_line(slot)}")
    report_hook(out, slots)
    report_errors(out)
    return 0


