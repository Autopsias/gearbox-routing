#!/usr/bin/env python3
"""Compaction policy for Claude Code: classify the session, then decide whether
an automatic compaction may proceed.

Subcommands (the hook ones read the hook JSON payload on stdin):

  prompt        UserPromptSubmit  — heartbeat, classify, write the policy file
  pre-compact   PreCompact(auto)  — the veto
  post-compact / session-start   — see compact_reorient.py (PostCompact, SessionStart)
  safe-point    skill / operator  — mark this session safe (or held) to compact
  status        operator          — print the policy and the last 5 decisions
  activation    operator          — record that an intervention went live

One job per file: this one holds the RULES, compact_store.py the state layer,
compact_activation.py the operator bookkeeping, compact_safepoint.py the
safe-point writer, compact_turn.py the turn boundary (rule h),
compact_reorient.py the post-compact/session-start pair,
and context_tokens.py reads the live context out of the transcript tail.

FAIL OPEN, ALWAYS. Every hook-execution path is wrapped: on any exception the
hook prints nothing, writes one `hook-error` ledger line through the single
ledger writer, and exits 0. This repo's other hooks are always-allow observers
that swallow their own failures; this one GATES, so
it has to be the most careful of them.

KILL SWITCH: `GEARBOX_COMPACT_POLICY=off`, or `~/.gearbox-state/compaction/DISABLED`.
Either silences every HOOK-EXECUTION subcommand — but never `activation` or
`status`. The heartbeat is written BEFORE the switch is consulted, so a session
run with the instrument off scores `hook_inactive` rather than `unknown`. The
same seam decides what a BROKEN TREE does: hook execution fails open and
silent, operator bookkeeping fails LOUD with exit 2, because a silent
`activation` would report success for a row it never wrote.

PROBE MODE: `GEARBOX_COMPACT_SOURCE=probe` moves the whole state root to
`~/.gearbox-state/compaction/probe/`, so a hand invocation cannot write to a live
surface — not the ledger, not the heartbeat, not a session's policy file.
"""

from __future__ import annotations

import json
import os
import sys

# FAIL OPEN STARTS AT THE IMPORT. A broken or half-deployed sibling module must
# not put a traceback on the user's screen at every prompt: the hook exits 0
# having printed nothing to stdout, and says why once on stderr.
try:
    import compact_activation as activation
    import compact_reorient as reorient
    from compact_classify import content_kind
    import compact_safepoint as safepoint
    import compact_turn as turn
    from compact_store import (
        append_line, iso_utc, is_probe, kill_switch, ledger, now_ms,
        POLICY_VERSION, read_json, read_policy, root, safe_id, claude_dir,
        take_pending, update_policy, write_pending, write_version_file,
    )
    from context_tokens import context_tokens
    from window_trust import (
        MIN_HEADROOM, _int_or_none, corroborated,
        live_headroom,
    )

    IMPORT_ERROR = ""
except Exception as _exc:  # pragma: no cover - exercised by a dedicated test
    IMPORT_ERROR = "%s: %s" % (type(_exc).__name__, _exc)


# --- the rules, all of them, at the top ------------------------------------

ORCHESTRATOR_COMMANDS = ("/plan-execute", "/epic-dev", "/repo-health")
PLANNING_COMMANDS = (
    "/plan-harden",
    "/plan-builder",
    "/grill-with-docs",
    "/grill-me",
    "/adversarial-review",
    "/routing-retro",
    "/research",  # the explicit override for a research/analysis session
)
# A WORD, not a prefix. `startswith` on the whole prompt classified
# "researching why test_foo is flaky" as planning — and planning is STICKY, so
# one ordinary debugging prompt armed the veto for the rest of the session.
PLANNING_WORDS = ("research",)
WORD_PUNCTUATION = ".,:;!?\"'`*"

# A FRACTION of the headroom, never an absolute token count: the real threshold
# depends on the model window this session happens to run at.
# v2 (operator decision: auto-compaction, never stop): orchestrator was
# None + an unconditional pre-compact allow — compaction could land MID-BATCH.
# Now it defers via the safe-point regime: plan-execute already writes hold
# (in-batch) and ok (between batches), so auto-compaction lands at the seam.
CEILING_FRACTION = {"orchestrator": 0.75, "planning": 0.75, "default": 0.5}
STICKY_TYPES = ("orchestrator", "planning")
PROVISIONAL_TYPE = "default"


# --- the policy file -------------------------------------------------------


def new_policy(kind, text):
    return {
        "type": kind,
        "ceiling_fraction": CEILING_FRACTION[kind],
        "sticky": kind in STICKY_TYPES,
        "source_prompt": (text or "")[:120],
        "safe_point": "unknown",
        "plan_dir": None,
        "updated_ms": now_ms(),
    }


def first_word(text):
    """The command, or the first WORD, that starts the prompt.

    Matching is on this word and nothing else. The earlier form tested
    `stripped.lower().startswith(PLANNING_WORDS)` — a substring match at
    position zero — so "researching why test_foo is flaky" classified as
    planning, and planning never downgrades.
    """
    stripped = (text or "").strip()
    if not stripped:
        return ""
    return stripped.split(None, 1)[0].strip(WORD_PUNCTUATION).lower()


def classify(text):
    """orchestrator / planning / default, from the command that starts the work."""
    word = first_word(text)
    if not word:
        return PROVISIONAL_TYPE
    if word in ORCHESTRATOR_COMMANDS:
        return "orchestrator"
    if word in PLANNING_COMMANDS or word in PLANNING_WORDS:
        return "planning"
    # v3: the first word marked only a small share of sessions. The whole
    # prompt decides next — rules and measurement in compact_classify.py.
    return content_kind(text) or PROVISIONAL_TYPE


def apply_classification(existing, kind, text):
    """(policy_to_write_or_None, action).

    ONE UPGRADE RULE: orchestrator/planning is STICKY and never downgraded;
    `default` is PROVISIONAL and any later sticky classification replaces it.
    Nothing ever moves a session back to default.
    """
    if existing is None:
        return new_policy(kind, text), "classified:" + kind
    current = existing.get("type")
    if current in STICKY_TYPES:
        return None, "sticky:" + str(current)
    if kind in STICKY_TYPES:
        existing["type"] = kind
        existing["ceiling_fraction"] = CEILING_FRACTION[kind]
        existing["sticky"] = True
        existing["source_prompt"] = (text or "")[:120]
        existing["updated_ms"] = now_ms()
        return existing, "upgraded:%s->%s" % (current, kind)
    return None, "provisional:" + PROVISIONAL_TYPE


# --- safe points -----------------------------------------------------------


def effective_safe_point(policy):
    """The safe point as pre-compact must read it.

    EVERY SAFE POINT EXPIRES: a skill that crashed between `hold` and `ok`
    cannot pin a session open until the model limit — the worst case becomes
    'compacts 30 minutes later than ideal', not 'session dies'. A `hold`
    written in a different phase than the session's current one is stale for
    the same reason and also reads as 'unknown'.
    """
    if not isinstance(policy, dict):
        return "unknown"
    if policy.get("safe_point") not in ("ok", "hold"):
        return "unknown"
    expires = policy.get("safe_point_expires_ms")
    if not isinstance(expires, (int, float)) or now_ms() >= expires:
        return "unknown"
    if policy.get("safe_point_phase") != policy.get("current_phase"):
        return "unknown"
    return policy["safe_point"]


# --- pre-compact -----------------------------------------------------------


def record_effective_window(session, ctx):
    """(policy, effective_window), freezing the window on the FIRST call.

    effective_window = ctx at the FIRST PreCompact call of this session,
    DERIVED EMPIRICALLY and never resolved from config: the hook cannot see
    which window this launch runs at (the --autocompact flag, the setting and
    CLAUDE_CODE_AUTO_COMPACT_WINDOW have different precedence and only the last
    is visible in the environment), and it does not need to — Claude Code has
    already decided to compact by the time PreCompact fires, so the context at
    this call IS the effective threshold. A recorded run measured the first trigger
    landing at ~0.75x the CONFIGURED window, which is one more reason not to read
    the configured number.

    Read and write happen inside one lock, so this cannot clobber a safe point
    a skill writes at the same moment.
    """
    def mutate(policy):
        if policy is None or _int_or_none(policy.get("effective_window")) is not None:
            return None  # unclassified, or already frozen — write nothing
        if _int_or_none(ctx) is None:
            return None
        policy["effective_window"] = ctx
        policy["updated_ms"] = now_ms()
        return policy

    try:
        policy = update_policy(session, mutate)
    except OSError:
        policy = read_policy(session)
    return policy, _int_or_none((policy or {}).get("effective_window"))


def ceiling_for(policy, effective, model_window):
    """(ceiling, window_headroom) — both None when either input is unknown.

    The ceiling is this session type's share of the window's headroom, CLAMPED
    so it can never sit inside the live safety margin. Unclamped it is computed
    entirely from numbers frozen at the first PreCompact call, which is how
    the recorded run produced a ceiling of 168,758 on a 200,000-token model — above
    the 167,281 at which that run died. `model_window - MIN_HEADROOM` is the
    highest context this hook will ever defer past, whatever the fraction says.
    """
    effective = _int_or_none(effective)
    model_window = _int_or_none(model_window)
    if policy is None or effective is None or model_window is None:
        return None, None
    window_headroom = model_window - effective
    fraction = policy.get("ceiling_fraction")
    if not isinstance(fraction, (int, float)) or isinstance(fraction, bool):
        return None, window_headroom
    share = effective + fraction * window_headroom
    return int(min(share, model_window - MIN_HEADROOM)), window_headroom


def verdict(trigger, policy, point, ctx, model_window, ceiling, mid_turn=False):
    """THE SAFE DEFAULT IS ALLOW. (decision, reason), rules (a)-(h) in order.

    Never lets `None >= ceiling` raise: every comparison happens only after
    both values are known ints.
    """
    kind = (policy or {}).get("type")
    # (a) Anything that is not an automatic compaction goes through untouched.
    # trigger == 'error' means Claude Code is recovering from a context-limit
    # error the API already returned, and blocking there makes the underlying
    # error surface and the request FAIL.
    if trigger != "auto":
        return "allow", "trigger:%s" % trigger
    if policy is None:
        return "allow", "no_policy"  # (b) unclassified behaves exactly as today
    # (c) RETIRED (v2): orchestrator no longer bypasses the veto — it
    # defers on a live `hold` like everyone else, released by the between-batches
    # `ok`. Every fail-open below (expired hold, unreadable ctx, no headroom,
    # uncorroborated window, at-ceiling) still applies, so the worst case remains
    # "compacts mid-batch exactly as before v2", never "session dies".
    if point == "ok":
        return "allow", "safe_point_ok"  # (d)
    headroom = live_headroom(ctx, model_window)
    if headroom is None:
        return "allow", "unreadable_context"  # (e)
    if headroom < MIN_HEADROOM:
        return "allow", "no_headroom"  # (f) nowhere left to defer TO, RIGHT NOW
    # (f2) The window is an assertion until the session corroborates it — see
    # window_trust.py for the measurements behind the bound.
    if not corroborated(ctx, model_window):
        return "allow", "window_uncorroborated"
    if not isinstance(ceiling, int) or ctx >= ceiling:
        return "allow", "at_ceiling"  # (g)
    if kind == "planning" or point == "hold":
        return "block", "deferred"
    if kind == "default" and mid_turn:
        return "block", "deferred_to_turn_end"  # (h) v4 — see compact_turn.py
    return "allow", "default_type_not_blocked"


def cmd_pre_compact(payload):
    session = payload.get("session_id")
    trigger = str(payload.get("trigger") or "unknown")
    if kill_switch():
        return 0
    write_version_file()
    ctx, model_window, ctx_source = context_tokens(payload.get("transcript_path"))
    policy, effective = record_effective_window(session, ctx)
    point = effective_safe_point(policy)
    mid, bounded = turn.defers(policy, point)
    ceiling, window_headroom = ceiling_for(bounded, effective, model_window)
    decision, reason = verdict(trigger, policy, point, ctx, model_window, ceiling, mid)
    if decision == "block":
        # No compaction is coming, so there is no "before" to remember — and
        # any record a PRIOR (allowed) call left behind must go too, or the
        # staleness this guards against just moves one pre-compact call back.
        take_pending(session)
    else:
        write_pending(session, {"ctx": ctx})  # a plan session's ctx_before
    headroom = live_headroom(ctx, model_window)
    if reason == "unreadable_context":
        reason = "unreadable_context:" + ctx_source
    kind = (policy or {}).get("type")
    ledger(
        "pre-compact", session, reason, type=kind, trigger=trigger, ctx=ctx,
        model_window=model_window, effective_window=effective, ceiling=ceiling,
        headroom=headroom, window_headroom=window_headroom, safe_point=point,
        decision=decision, ctx_source=ctx_source,
    )
    if decision == "block":
        sys.stdout.write(
            json.dumps(
                {"decision": "block",
                 "reason": "%s, ctx %s/%s, safe_point %s" % (kind, ctx, ceiling, point)},
                separators=(",", ":"),
            )
        )
    return 0


# --- prompt (UserPromptSubmit) ---------------------------------------------


def hooks_registered():
    """OBSERVED from the deployed settings.json, not assumed from 'I am running'."""
    blob = read_json(os.path.join(claude_dir(), "settings.json"))
    hooks = (blob or {}).get("hooks")
    if not isinstance(hooks, dict):
        return False
    return all(
        "compact-policy.py" in json.dumps(hooks.get(event) or [])
        for event in ("UserPromptSubmit", "PreCompact")
    )


def heartbeat(session, kind, switch):
    """One line per session — the exposure DENOMINATOR, not the authority on type.

    Written before the kill switch is consulted: without it, a session that
    never compacted, one where the hook was switched off, and one the hook
    never saw are indistinguishable later — and the better the veto works, the
    thinner the compaction ledger gets. Its `type` is the FIRST classification,
    which a later prompt may upgrade; anything needing the session's type reads
    the policy file, which the upgrade rewrites.
    """
    # ponytail: one empty marker file per session is the whole once-per-session
    # guard (O_EXCL makes the create itself the lock). If ~/.gearbox-state/compaction/hb/
    # ever grows enough to matter, prune it by mtime — nothing reads it.
    marker = os.path.join(root(), "hb", safe_id(session))
    try:
        os.makedirs(os.path.dirname(marker), exist_ok=True)
        os.close(os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
    except OSError:
        return False  # FileExistsError: this session already has its one line
    try:
        append_line(
            os.path.join(root(), "sessions.ndjson"),
            {"ts": iso_utc(), "session": str(session or "unknown"), "type": kind,
             "policy_version": POLICY_VERSION, "kill_switch": "on" if switch else "off",
             "hooks_registered": hooks_registered()},
        )
    except OSError:
        # The marker must not outlive a failed write, or this session is lost
        # from the denominator forever — the one thing the heartbeat prevents.
        try:
            os.unlink(marker)
        except OSError:
            pass
        return False
    return True


def cmd_prompt(payload):
    session = payload.get("session_id")
    text = payload.get("prompt") or payload.get("user_prompt") or ""
    kind = classify(text)
    switch = kill_switch()
    heartbeat(session, kind, switch)  # FIRST — before anything else
    if switch:
        return 0
    write_version_file()
    actions = []

    def mutate(existing):
        policy, action = apply_classification(existing, kind, text)
        actions.append(action)
        return turn.stamp(policy or existing)  # every prompt starts a turn

    update_policy(session, mutate)  # read-modify-write under one lock
    # A compaction this session already had may still owe a ctx_after: the
    # number only becomes readable once a turn lands after the boundary.
    reorient.measure_deferred(session)
    ledger("prompt", session, actions[-1], type=kind, decision="allow", classified=kind)
    return 0


# --- dispatch --------------------------------------------------------------

USAGE = "usage: compact-policy.py {prompt|pre-compact|post-compact|session-start|" \
    "safe-point|status|activation}\n"

# OPERATOR bookkeeping, on the same seam the kill switch already draws: these
# two never go silent, because silence from them is indistinguishable from
# success and they are what downstream sessions read.
OPERATOR_SUBCOMMANDS = ("status", "activation")
STDIN_SUBCOMMANDS = ("prompt", "pre-compact", "post-compact", "session-start")


def read_payload():
    try:
        raw = sys.stdin.read()
    except (OSError, ValueError):
        return {}
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def run_stdin_hook(sub):
    """Every JSON-stdin subcommand, fail-open wrapper around all of them."""
    handlers = {"prompt": cmd_prompt, "pre-compact": cmd_pre_compact,
                "post-compact": reorient.cmd_post_compact,
                "session-start": reorient.cmd_session_start}
    session = "unknown"
    try:
        payload = read_payload()
        session = payload.get("session_id") or "unknown"
        return handlers[sub](payload)
    except Exception as exc:  # FAIL OPEN, ALWAYS
        ledger(sub, session, "hook-error", decision="allow", error=type(exc).__name__)
        return 0


def broken_tree_exit(sub):
    """What a half-deployed tree does — and it is NOT the same for both halves.

    HOOK EXECUTION fails open and silent: compaction proceeds normally, which
    is the whole fail-open contract. OPERATOR BOOKKEEPING FAILS LOUD, because
    `activation` is the sole authority on whether an intervention went live: an
    exit 0 that wrote no row reports success for a row that does not exist, and
    every downstream session would then score that intervention `no_exposure`
    forever.
    """
    if sub in OPERATOR_SUBCOMMANDS:
        sys.stderr.write(
            "compact-policy %s: REFUSED — a module failed to import (%s). "
            "NOTHING was recorded. Fix the tree and re-run.\n" % (sub, IMPORT_ERROR)
        )
        return 2
    sys.stderr.write(
        "compact-policy: disabled, a module failed to import (%s). "
        "Compaction proceeds normally.\n" % IMPORT_ERROR
    )
    return 0


def main(argv):
    sub, rest = (argv[0], argv[1:]) if argv else ("", [])
    if IMPORT_ERROR:
        return broken_tree_exit(sub)
    if not argv:
        sys.stderr.write(USAGE)
        return 0
    if is_probe():
        sys.stderr.write(
            "compact-policy: PROBE MODE — all state under %s, nothing live is "
            "touched.\n" % root()
        )
    if sub in STDIN_SUBCOMMANDS:
        return run_stdin_hook(sub)
    if sub == "safe-point":
        try:
            return safepoint.cmd_safe_point(rest, lambda: new_policy(PROVISIONAL_TYPE, ""))
        except SystemExit:
            raise
        except Exception as exc:  # FAIL OPEN, ALWAYS
            ledger(sub, "unknown", "hook-error", decision="allow", error=type(exc).__name__)
            return 0
    if sub == "status":
        return activation.cmd_status(rest, effective_safe_point)
    if sub == "activation":
        return activation.cmd_activation(rest)
    sys.stderr.write("compact-policy: unknown subcommand %r\n" % sub)
    sys.stderr.write(USAGE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
