#!/usr/bin/env python3
"""The `safe-point` subcommand: a skill telling the policy when this session is
safe to compact (`ok`) or must not be (`hold`).

Split out of compact-policy.py, which had reached its 500-line bound. The seam
is real rather than convenient: this is the only subcommand with its own
argument parser, and it is the only WRITER that is not a hook. The rule that
READS a safe point — `effective_safe_point`, with its expiry and phase checks —
stays with the other rules, and `new_policy` is passed in rather than imported,
because compact-policy.py's name has a hyphen and cannot be imported back.
"""

from __future__ import annotations

import argparse
import sys

from compact_store import (
    env_session,
    kill_switch,
    ledger,
    now_ms,
    update_policy,
    write_version_file,
)

SAFE_POINT_TTL_MINUTES = 30.0

# Session types whose `hold` is receipted inert. A `hold` written on one of these
# is recorded, and nothing will ever read it as a reason to defer — and saying so
# out loud is the whole point: a skill holding the session otherwise gets
# `recorded` and exit 0, indistinguishable from a hold that works.
# v2: EMPTY — orchestrator holds became real brakes when compact-policy
# retired its unconditional rule (c) allow. Kept as a seam (with its inert branch)
# so a future type can opt out without re-plumbing the writer.
HOLD_IS_INERT_FOR_TYPES = ()


def parse(argv):
    parser = argparse.ArgumentParser(prog="compact-policy.py safe-point")
    parser.add_argument("--state", choices=("ok", "hold"))
    parser.add_argument("--session")
    parser.add_argument("--plan-dir")
    parser.add_argument("--phase")
    parser.add_argument("--note")
    parser.add_argument("--ttl-minutes", type=float, default=SAFE_POINT_TTL_MINUTES)
    return parser.parse_args(argv)


def cmd_safe_point(argv, new_policy):
    """Called only from a MAIN CONVERSATION, never from a worker or a Workflow
    agent: a worker's session id is not the orchestrator's, so a safe point
    written there would land on the wrong policy file.

    That is ENFORCED rather than documented — env_session() refuses to resolve a
    session id inside a worker, because a rule nothing checks is a rule a skill
    step will break silently. `--session` still works everywhere: the caller
    that knows which session it means may always say so.
    """
    args = parse(argv)
    if kill_switch():
        return 0
    session = args.session
    if not session:
        session, refusal = env_session()
        if not session:
            sys.stderr.write(
                "compact-policy safe-point: %s. Nothing written.\n" % refusal
            )
            return 0  # never guess a session id — a worker's is a guess too

    def mutate(policy):
        # Read-modify-write INSIDE the lock: this command routinely runs while
        # the session's own UserPromptSubmit hook is writing the same file, and
        # a whole-file replace computed from an earlier read drops one side's
        # fields — silently discarding the hold a skill just asked for.
        policy = policy or new_policy()
        if args.phase is not None:
            policy["current_phase"] = args.phase
        if args.state:
            policy["safe_point"] = args.state
            policy["safe_point_phase"] = policy.get("current_phase")
            policy["safe_point_expires_ms"] = now_ms() + int(args.ttl_minutes * 60_000)
        if args.plan_dir:
            policy["plan_dir"] = args.plan_dir
        if args.note is not None:
            policy["safe_point_note"] = args.note
        policy["updated_ms"] = now_ms()
        return policy

    policy = update_policy(session, mutate)
    write_version_file()
    kind = policy.get("type")
    inert = args.state == "hold" and kind in HOLD_IS_INERT_FOR_TYPES
    if inert:
        sys.stderr.write(
            "compact-policy safe-point: hold is INERT on this session (type: %s) "
            "— this type is opted out of the veto. "
            "Recorded, but nothing will defer.\n" % kind
        )
    ledger(
        "safe-point", session,
        "hold_inert:%s" % kind if inert else "safe_point:%s" % (args.state or "phase-only"),
        type=kind, decision="inert" if inert else "recorded",
        safe_point=args.state, phase=policy.get("current_phase"),
    )
    return 0
