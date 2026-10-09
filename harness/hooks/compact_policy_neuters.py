#!/usr/bin/env python3
"""The neuter probes for hooks/test_compact_policy.py: (breaker, check, phrase).

A check nobody has seen fail proves nothing, so every behaviour check in the
suite is paired here with a BREAKER that damages the rule it watches, and with
the PHRASE its failure must carry.  The phrase is what makes a probe a receipt
rather than "something raised": a check that fails with an import error or a
fixture error proves nothing about the rule.

Each breaker damages a TABLE, a CONSTANT, a CLOCK or a helper the production
code really reads — never the symbol the check itself calls.  Five of the first
ten probes did the latter and were caught doing it; they proved a stub had been
installed, not that a rule was observed.

The breaker and its check live in one file because they are one statement about
one rule.  (The block-envelope probe is the exception and stays in the test
file: it shares the spec's MIN_HEADROOM pin with the sweep test it guards.)
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil

import compact_policy_testkit as kit
from compact_ctx_neuters import (
    c_carried_forward,
    c_ctx_after_is_measured_after_the_boundary,
    c_three_tuple,
    c_trailing_estimate,
    n_carried_forward,
    n_ctx_after_measured_too_early,
    n_three_tuple,
    n_trailing_estimate,
)

WINDOW_1M = 1_000_000


@contextlib.contextmanager
def shared_modules_restored():
    """Undo any mutation a breaker makes to a SHARED sibling module.

    load_policy() hands back a fresh compact-policy each time, but its siblings
    — compact_store, compact_activation, compact_safepoint, and the testkit
    itself — are ordinary cached imports: a breaker that damages one of those
    stays damaged for every later test in the process — which would disable the very rules the later probes claim to
    check, the exact "check that cannot fail" shape these probes exist to catch.
    """
    import compact_activation
    import compact_reorient
    import compact_safepoint
    import compact_store

    import compact_turn

    saved = [(mod, dict(vars(mod)))
             for mod in (compact_store, compact_activation, compact_safepoint,
                         compact_reorient, compact_turn, kit)]
    try:
        yield
    finally:
        for mod, snapshot in saved:
            for name in [k for k in vars(mod) if k not in snapshot]:
                delattr(mod, name)
            for name, value in snapshot.items():
                setattr(mod, name, value)


# --- breakers --------------------------------------------------------------


def n_classify(m):
    m.PLANNING_COMMANDS = ()  # the TABLE the real classify() reads


def n_sticky(m):
    m.STICKY_TYPES = ()


def n_content(m):
    m.content_kind = lambda _text: None  # the whole-prompt rule classify() calls


def n_expiry(m):
    m.now_ms = lambda: 0  # the CLOCK the real expiry check reads


def n_headroom(m):
    """Remove BOTH self-disarms — see window_trust.py "Neutering these rules"."""
    m.MIN_HEADROOM = 0
    m.corroborated = lambda _ctx, _window: True


def n_window_trust(m):
    """Remove ONLY the corroboration guard. Patch `corroborated`, not the
    constant: it is what rule (f2) calls. See window_trust.py."""
    m.corroborated = lambda _ctx, _window: True


def n_trigger(m):
    real = m.verdict
    m.verdict = lambda t, *rest: real("auto", *rest)


def n_evidence(m):
    m.activation.verify_commit = lambda _t: (True, "neutered")  # a real dependency


def n_duplicate(m):
    m.activation.read_activations = lambda: []


def n_spine(m):
    real = m.ledger

    def dropped(event, session, reason, **f):
        row = real(event, session, reason, **f)
        row.pop("source", None)
        return row

    m.ledger = dropped


def n_planning_word(m):
    """Put a WORD back in the table that only a substring match would have hit."""
    m.PLANNING_WORDS = tuple(m.PLANNING_WORDS) + ("researching",)


def n_policy_lock(m):
    """Take the LOCK away from the read-modify-write, leaving the two separate
    operations that dropped one writer's fields."""
    import compact_store

    compact_store.policy_lock = contextlib.nullcontext


def n_order(m):
    real = m.cmd_prompt

    def switch_first(payload):
        if m.kill_switch():
            return 0
        return real(payload)

    m.cmd_prompt = switch_first


def n_probe_isolation(m):
    """Send a probe's writes back to the LIVE root — the isolation itself."""
    import compact_store

    m.root = compact_store.live_root


def n_worker_guard(m):
    """Empty WORKER_MARKERS, the table env_session() reads to spot a worker."""
    import compact_store

    compact_store.WORKER_MARKERS = ()


def n_settings_guard(m):
    """Hand back the UNGUARDED registration — `python3 <path>` with nothing
    checking the path is there, which is exactly how it used to be written."""
    kit.hook_commands = lambda: ['python3 "$HOME/.claude/hooks/compact-policy.py" prompt']


def n_hold_inert(m):
    """v2 flip: re-populate the TABLE with the pre-v2 value, so an
    orchestrator's hold is receipted `inert` again — the regression this
    pair now exists to catch."""
    import compact_safepoint

    compact_safepoint.HOLD_IS_INERT_FOR_TYPES = ("orchestrator",)


def n_operator_loud(m):
    """Empty the table main() reads to tell operator bookkeeping from a hook."""
    m.OPERATOR_SUBCOMMANDS = ()


def n_pending_cross_session(m):
    """Swap the per-session pending lookup for 'the last pre-compact line in
    the shared ledger' — the shape the design explicitly forbids, because
    several sessions append to ONE decisions.ndjson and can interleave
    between one session's PreCompact and its own PostCompact call."""
    import compact_store

    def last_ledger_ctx(_session):
        path = os.path.join(compact_store.root(), "decisions.ndjson")
        for line in reversed(compact_store.tail_lines(path, 50)):
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("event") == "pre-compact":
                return {"ctx": row.get("ctx")}
        return None

    m.reorient.take_pending = last_ledger_ctx


# --- checks ----------------------------------------------------------------


def c_classify(m):
    assert m.classify("/plan-harden x") == "planning", "classifier lost /plan-harden"


def c_sticky(m):
    assert m.new_policy("planning", "")["sticky"] is True, "sticky boolean not written"


def c_content(m):
    assert m.classify(
        "Please analyse this article and summarise what applies to us, and what to reuse"
    ) == "planning", "whole-prompt classifier lost the analysis session"
    assert m.classify(
        "Review the diff in the file /tmp/plan-execute-review-1.diff (3 files)"
    ) == "default", "a review-gate dispatch was armed as planning"


def c_expiry(m):
    assert m.effective_safe_point(
        {"safe_point": "hold", "safe_point_expires_ms": 1}
    ) == "unknown", "an expired hold still pins the session open"


def c_headroom(m):
    """The disarm reads the LIVE gap. ctx 150,000 of a 200,000 window leaves
    50,000 — under the margin — even though a ceiling of 190,000 computed from
    the frozen headroom still sits above it."""
    assert m.verdict("auto", {"type": "planning"}, "unknown", 150_000, 200_000,
                     190_000) == ("allow", "no_headroom"), "the veto no longer disarms itself"


def c_trigger(m):
    assert m.verdict("error", {"type": "planning"}, "unknown", 300_000, WINDOW_1M,
                     800_000)[0] == "allow", "an error-triggered compaction was blocked"


def n_turn_boundary(m):
    """Every attempt reads as mid-turn: the start window never opens."""
    m.turn.mid_turn = lambda _policy: True


def c_turn_boundary(m):
    """Rule (h) defers a default session MID-turn and only mid-turn. An
    unstamped policy is the fail-open case: it must never read as mid-turn."""
    fresh = {"type": "default", "turn_start_ms": m.now_ms()}
    stale = {"type": "default", "turn_start_ms": m.now_ms() - 10 * m.turn.TURN_START_MS}
    assert m.turn.defers(stale, "unknown")[0] is True, "a mid-turn attempt was not deferred"
    for policy in (fresh, {"type": "default"}):
        assert m.turn.defers(policy, "unknown")[0] is False, (
            "a default session was blocked at the turn start: %s" % sorted(policy)
        )


def c_planning_word(m):
    assert m.classify("researching why test_foo is flaky") == "default", (
        "a substring match armed the sticky veto on an ordinary session"
    )
    assert m.classify("research how compaction triggers fire") == "planning"


def c_policy_lock(m, home):
    """Two writers for one session must serialise. Without the lock each
    computes its whole-file replace from a read taken before the other landed,
    and the slower one silently discards the faster one's fields — which is how
    a `safe-point --state hold` lost the hold a skill had just asked for."""
    import threading
    import time

    import compact_store

    compact_store.write_policy("race", {"type": "planning"})
    inside = threading.Event()

    def slow(policy):
        inside.set()
        time.sleep(0.25)  # hold the critical section open
        policy["from_slow_writer"] = 1
        return policy

    def fast(policy):
        policy["from_fast_writer"] = 1
        return policy

    worker = threading.Thread(target=compact_store.update_policy, args=("race", slow))
    worker.start()
    inside.wait(timeout=5)
    compact_store.update_policy("race", fast)
    worker.join(timeout=10)
    got = compact_store.read_policy("race") or {}
    assert "from_slow_writer" in got and "from_fast_writer" in got, (
        "an overlapping writer dropped the other's fields: %r" % sorted(got)
    )


def c_evidence(m):
    assert m.activation.verify_evidence("repo_diet", "no sha here at all")[0] is False, (
        "unverified evidence accepted"
    )


def c_duplicate(m, home):
    """Drives the REAL cmd_activation twice; the second must be refused."""
    m.activation.verify_evidence = lambda *a: (True, "probe")
    argv = ["--intervention", "hooks", "--evidence", "x" * 20, "--source", "operator"]
    m.activation.cmd_activation(list(argv))
    m.activation.cmd_activation(list(argv))
    rows = kit.activations(home)
    assert len(rows) == 1, "a duplicate activation row was written (%d rows)" % len(rows)


def c_spine(m):
    row = m.ledger("prompt", "s", "why")
    missing = [f for f in kit.SPINE if f not in row]
    assert not missing, "ledger spine is missing %s" % missing


def c_order(m, home):
    os.environ["GEARBOX_COMPACT_POLICY"] = "off"
    try:
        m.cmd_prompt({"session_id": "z", "prompt": "hi"})
    finally:
        del os.environ["GEARBOX_COMPACT_POLICY"]
    assert kit.sessions(home), "no heartbeat: the kill switch short-circuited before it"


def c_probe_isolation(m, home):
    """A hand probe must not appear on ANY live surface — the tag alone let a
    probe write a policy file keyed on a real session's id."""
    os.environ["GEARBOX_COMPACT_SOURCE"] = "probe"
    try:
        m.cmd_prompt({"session_id": "iso", "prompt": "/plan-harden go"})
    finally:
        del os.environ["GEARBOX_COMPACT_SOURCE"]
    leaked = kit.live_surfaces(home)
    assert not leaked, "a probe wrote into the LIVE state: %s" % leaked
    assert kit.ndjson(kit.probe_root(home, "decisions.ndjson")), (
        "the probe wrote nothing at all — this check is pointed at nothing"
    )


def c_worker_guard(m, home):
    """Inside a worker the environment's session id is the WORKER's, so
    resolving it would write a safe point onto a different session."""
    os.environ["CLAUDE_CODE_CHILD_SESSION"] = "1"
    os.environ["CLAUDE_CODE_SESSION_ID"] = "worker-sid"
    try:
        m.main(["safe-point", "--state", "hold"])  # the real dispatch path
    finally:
        del os.environ["CLAUDE_CODE_CHILD_SESSION"]
        del os.environ["CLAUDE_CODE_SESSION_ID"]
    assert m.read_policy("worker-sid") is None, (
        "a worker resolved a session id and wrote a safe point onto it"
    )


def c_operator_loud(m):
    """A broken tree must make `activation` REFUSE, never exit 0 having
    recorded nothing: silence there is indistinguishable from success."""
    m.IMPORT_ERROR = "ImportError: simulated half-deployed tree"
    code = m.main(["activation", "--intervention", "hooks", "--evidence", "x" * 20,
                   "--source", "operator"])
    assert code == 2, "activation exited %s on a broken tree, not 2 — a silent no-op" % code


def c_settings_guard(m, home):
    """A half-copied deploy leaves ~/.claude/hooks/compact-policy.py missing.
    The command settings.json registers must still exit 0 there: `python3
    <missing>` exits 2, and on UserPromptSubmit exit 2 REFUSES the prompt — the
    one case "fail open, always" cannot cover from inside the hook."""
    commands = kit.hook_commands()
    assert commands, "settings.json registers no compact-policy command at all"
    for command in commands:
        done = kit.shell(command, home)  # a HOME with no ~/.claude under it
        assert done.returncode == 0, (
            "exit %d when the hook file is missing, from: %s"
            % (done.returncode, command)
        )
    # The other half: an `exit 0` that fired unconditionally would satisfy every
    # assertion above while silently disabling the hook in every session.
    installed = os.path.join(str(home), "installed")
    os.makedirs(os.path.join(installed, ".claude"), exist_ok=True)
    shutil.copytree(kit.HERE, os.path.join(installed, ".claude", "hooks"),
                    ignore=shutil.ignore_patterns("__pycache__"), dirs_exist_ok=True)
    prompt_cmd = next(c for c in commands if c.endswith("prompt"))
    kit.shell(prompt_cmd, installed, stdin={"session_id": "reg", "prompt": "/plan-execute go"})
    assert (kit.policy(installed, "reg") or {}).get("type") == "orchestrator", (
        "the registered command exited 0 without ever running the hook"
    )


def c_pending_isolation(m, home):
    """A's PreCompact, then B's PreCompact (interleaving in between A's
    PreCompact and its own PostCompact), then A's PostCompact: ctx_before
    must be A's own, never B's."""
    os.makedirs(str(home), exist_ok=True)
    ta, tb = os.path.join(str(home), "a.jsonl"), os.path.join(str(home), "b.jsonl")
    kit.write_transcript(ta, [kit.assistant_line(111_111, window=WINDOW_1M)])
    kit.write_transcript(tb, [kit.assistant_line(222_222, window=WINDOW_1M)])
    m.cmd_pre_compact({"session_id": "A", "trigger": "auto", "transcript_path": ta})
    m.cmd_pre_compact({"session_id": "B", "trigger": "auto", "transcript_path": tb})
    m.reorient.cmd_post_compact({"session_id": "A", "trigger": "auto", "transcript_path": ta})
    rows = [r for r in kit.decisions(home) if r["event"] == "compacted" and r["session"] == "A"]
    assert rows[-1]["ctx_before"] == 111_111, (
        "session A's ctx_before crossed with session B's: got %r" % rows[-1]["ctx_before"]
    )


def c_hold_inert(m, home):
    """v2: an orchestrator's `hold` is a REAL brake. The receipt
    must say `recorded` (plan-execute reads it as its seam marker working), and
    verdict() must defer an under-ceiling auto compaction while the hold
    stands."""
    m.cmd_prompt({"session_id": "orc", "prompt": "/plan-execute go"})
    m.main(["safe-point", "--state", "hold", "--session", "orc"])
    line = kit.decisions(home)[-1]
    assert line["decision"] == "recorded", (
        "an orchestrator hold was ledgered as %r, not 'recorded'" % line["decision"]
    )
    decision, reason = m.verdict(
        "auto", {"type": "orchestrator", "ceiling_fraction": 0.75}, "hold",
        100_000, 200_000, 150_000,
    )
    assert (decision, reason) == ("block", "deferred"), (
        "an orchestrator hold was not honoured by verdict(): %r" % ((decision, reason),)
    )


# (name, breaker, check, THE PHRASE THE FAILURE MUST CARRY)
NEUTERS = [
    ("classifier", n_classify, c_classify, "classifier lost /plan-harden"),
    ("sticky-boolean", n_sticky, c_sticky, "sticky boolean not written"),
    ("whole-prompt-classifier", n_content, c_content,
     "whole-prompt classifier lost the analysis session"),
    ("safe-point-expiry", n_expiry, c_expiry, "still pins the session open"),
    ("headroom-disarm", n_headroom, c_headroom, "no longer disarms itself"),
    ("error-trigger-never-blocked", n_trigger, c_trigger,
     "error-triggered compaction was blocked"),
    ("activation-evidence", n_evidence, c_evidence, "unverified evidence accepted"),
    ("activation-duplicate", n_duplicate, c_duplicate,
     "duplicate activation row was written"),
    ("ledger-spine", n_spine, c_spine, "ledger spine is missing"),
    ("context-tokens-three-tuple", n_three_tuple, c_three_tuple, "values, not 3"),
    ("heartbeat-before-kill-switch", n_order, c_order, "no heartbeat"),
    ("probe-isolation", n_probe_isolation, c_probe_isolation,
     "probe wrote into the LIVE state"),
    ("safe-point-worker-guard", n_worker_guard, c_worker_guard,
     "worker resolved a session id"),
    ("operator-subcommands-fail-loud", n_operator_loud, c_operator_loud,
     "not 2 — a silent no-op"),
    ("turn-boundary", n_turn_boundary, c_turn_boundary,
     "blocked at the turn start"),
    ("planning-word-boundary", n_planning_word, c_planning_word,
     "substring match armed the sticky veto"),
    ("ctx-carries-output-forward", n_carried_forward, c_carried_forward,
     "output tokens were dropped from ctx"),
    ("ctx-counts-trailing-records", n_trailing_estimate, c_trailing_estimate,
     "records after the last usage block were not counted"),
    ("policy-write-under-lock", n_policy_lock, c_policy_lock,
     "overlapping writer dropped the other's fields"),
    ("registered-command-fails-open", n_settings_guard, c_settings_guard,
     "when the hook file is missing"),
    ("orchestrator-hold-is-real", n_hold_inert, c_hold_inert,
     "an orchestrator hold was ledgered as"),
    ("post-compact-pending-isolation", n_pending_cross_session, c_pending_isolation,
     "crossed with session B's"),
    ("ctx-after-measured-after-the-boundary", n_ctx_after_measured_too_early,
     c_ctx_after_is_measured_after_the_boundary,
     "must be the first turn after the boundary"),
]
