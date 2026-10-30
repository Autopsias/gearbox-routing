#!/usr/bin/env python3
"""dispatch-audit — PreToolUse(Agent): record what model + effort every subagent
dispatch actually asked for, so tier drift is VISIBLE instead of silent.

WHY THIS EXISTS. Two silent-drift paths have no error to catch:

  1. An Agent call that OMITS `model` and names no agent silently inherits the
     orchestrator's model. This is the mechanism behind the 2026-07-02 fan-out
     incident (~33 agents on claude-fable-5, 406 model calls) — see
     model-routing.yaml fanout_policy §workflow_agent_model_inheritance.
  2. A subagent `model` override that is blocked or unresolvable "falls back to the
     inherited or default model rather than failing the request" (Claude Code docs,
     model-config §Restrict model selection). So plan-execute's reactive-degradation
     path, which waits for a dispatch ERROR, never fires — the session just runs on
     the wrong model. Detection has to be observational, which is this hook.

It is an OBSERVER, never a gate: it always allows, and any failure inside it is
swallowed. A broken audit log must never block a dispatch. It records what was
REQUESTED (all a PreToolUse hook can see) — hooks cannot read the effort a subagent
will actually run at, and cannot change model or effort at all.

WHY PURE PYTHON, not a shell wrapper around `python3 -c '...'`: the first draft was
that, and a single apostrophe in a CODE COMMENT ("the definition's frontmatter")
terminated the shell string and broke the whole hook — silently, because stderr was
sent to /dev/null. One language, no nested quoting, no such failure mode.

Wired from settings.json hooks.PreToolUse matcher "Agent".
Self-check: hooks/test_dispatch_audit.sh
"""

import datetime
import json
import os
import sys

ALLOW = {
    "hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "allow",
    }
}

# Default OUTSIDE both versioned trees, for two reasons measured the hard way:
#   (1) `gearbox deploy` is fail-closed on unclassified paths in the deploy target. An
#       earlier draft defaulted to ~/.claude/evals/routing/, which is versioned, so the
#       log itself ABORTED the next deploy as a triage_untracked path. An observer that
#       can block a deploy is exactly what this hook must not be.
#   (2) Records carry `cwd` and the dispatch `description` — task-derived text. Under the
#       transcript-containment rule (evals/dyno/RUNTIME-ROOT.md) that must never land in a
#       versioned path; only redacted metadata may be committed.
# Reuses the ONE established runtime root rather than inventing a second one.
DEFAULT_LOG = os.path.join(os.path.expanduser("~"), ".gearbox-state", "routing", "dispatch-audit.ndjson")


def effort_level(v):
    """Normalize the payload effort to a plain level string. MEASURED: it arrives as an
    OBJECT ({"level": "high"}), not a string — an earlier draft logged the raw dict,
    which no downstream grep could match. Tolerate a bare string too; anything else
    becomes None rather than a dict nobody can query."""
    if isinstance(v, str):
        return v
    if isinstance(v, dict):
        lvl = v.get("level")
        return lvl if isinstance(lvl, str) else None
    return None


def drift_class(model, sub):
    """Name the drift class at write time, so the log is greppable without re-deriving
    the rule later.

    NOTE the no-model + named-agent case is NOT drift: the agent definition supplies
    both model: and effort: frontmatter, which is the intended way to pin a tier. An
    earlier draft labelled it "inherits_model" and so cried drift on the CORRECT
    pattern — a tier-agent dispatch legitimately omits `model`.
    """
    if model is None and sub is None:
        return "inherits_both"        # both levers unset: model AND effort inherit
    if model is None:
        return "definition_governs"   # the named agent frontmatter supplies model+effort
    if sub is None:
        return "inherits_effort"      # model pinned, but no definition to carry effort:
    return "pinned"                   # model on the call, effort from the named definition


# RT-02(b) — the SAME "generic agentType" set plan-execute's SKILL.md now refuses
# to leave unpinned at construction: unset, or one of the built-in fan-out shapes
# that carry no model/effort frontmatter of their own. A tier-* agent or "fork"
# is NEVER generic — each already pins (or deliberately ignores) a model.
_GENERIC_SUBAGENT_TYPES = {None, "general-purpose", "Explore", "Plan"}


def is_unpinned_generic(model, sub):
    """True for exactly the ad-hoc fan-out this hook cannot fix (Claude Code
    silently ignores a hook's attempt to change an Agent's model/subagent_type):
    no model given, and no named agent whose OWN frontmatter would supply one."""
    return model is None and sub in _GENERIC_SUBAGENT_TYPES


def build_record(ev):
    ti = ev.get("tool_input")
    if not isinstance(ti, dict):
        return None
    model = ti.get("model")
    sub = ti.get("subagent_type")
    desc = ti.get("description")
    rec = {
        "ts": datetime.datetime.now(datetime.timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        "session_id": ev.get("session_id"),
        "cwd": ev.get("cwd"),
        "requested_model": model,
        "subagent_type": sub,
        "drift": drift_class(model, sub),
        # Session-level effort as the hook sees it — NOT the effort the subagent will
        # run at. A named-agent effort: frontmatter overrides this and is invisible here.
        "session_effort": effort_level(ev.get("effort")) or os.environ.get("CLAUDE_EFFORT"),
        "description": (desc or "")[:120] if isinstance(desc, str) else "",
    }
    if is_unpinned_generic(model, sub):
        # Present ONLY when true — a scan of the ledger must be able to grep this
        # key at all and get the honest remaining count, never a field that reads
        # "false" everywhere and looks like a clean bill of health.
        rec["unpinned_generic"] = True
    return rec


def main():
    # Emit the allow decision FIRST and flush it, so nothing below can prevent the
    # dispatch — not an exception, not a full disk, not a bad payload.
    sys.stdout.write(json.dumps(ALLOW) + "\n")
    sys.stdout.flush()
    try:
        ev = json.loads(sys.stdin.read() or "{}")
        if not isinstance(ev, dict):
            return
        rec = build_record(ev)
        if rec is None:
            return
        if rec.get("unpinned_generic"):
            print(
                f"dispatch-audit: unpinned generic dispatch (subagent_type="
                f"{rec['subagent_type']!r}) — no model given, silently inherits the "
                "orchestrator's model.",
                file=sys.stderr,
            )
        path = os.environ.get("CLAUDE_DISPATCH_AUDIT_LOG") or DEFAULT_LOG
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass  # an observer never fails the dispatch it observes


if __name__ == "__main__":
    main()
