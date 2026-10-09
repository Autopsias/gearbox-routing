#!/usr/bin/env python3
"""The activation ledger and the status readout — OPERATOR bookkeeping.

Split from compact-policy.py along the seam the kill switch already draws:
the hook subcommands go silent when the operator rolls the veto back, and
these two never do. If the switch gagged `activation`, a real deploy made
while the veto was rolled back would record nothing, and every downstream
session would then report `no_exposure` for an intervention that IS live.

`~/.gearbox-state/compaction/activations.ndjson` is a plan session's SOLE authority on when each
intervention went live. Nothing else in this plan may append to it, and a row
is written at the moment an intervention actually goes live — never
reconstructed afterwards from a git log, because a commit date is not a deploy
date.
"""

from __future__ import annotations

import json
import os
import sys

from compact_store import (
    append_line,
    iso_utc,
    kill_switch,
    ledger,
    POLICY_VERSION,
    read_policy,
    root,
    claude_dir,
    repo_dir,
    tail_lines,
)

INTERVENTIONS = ("hooks", "base_context", "routing", "repo_diet", "compact_window")

# How each intervention actually goes live, and therefore what its evidence has
# to prove. `repo_diet` needs no deploy — those repositories are read directly,
# so it is live the moment the commit lands.
CLAIM = {
    "hooks": "deploy",
    "base_context": "deploy",
    "routing": "deploy",
    "repo_diet": "commit",
    "compact_window": "deploy",
}
DEFAULT_SCOPE = {
    "hooks": "compact-policy.py + safe-point hooks live in ~/.claude",
    "base_context": "always-loaded context (CLAUDE.md and friends) in ~/.claude",
    "routing": "model-routing.yaml and the routing surfaces in ~/.claude",
    "repo_diet": "per-repo CLAUDE.md diet",
    "compact_window": "settings.json autoCompactWindow 300000 (retuned from 130000: a sub-200k trigger sits below the window-trust bound and disarms every per-type deferral) + orchestrator defer-to-seam (compact-policy v2) + model-window rows for the full lineup — ONE bundled intervention, one cohort",
}

# `gearbox deploy`'s own final line, and the line it prints when the sync moved
# nothing. Both are the script's, not this file's invention — scripts/gearbox.
DEPLOY_OK_MARKER = "deploy OK"
DEPLOY_NOOP_MARKER = "nothing (clean no-op)"


def git_out(cwd, *args):
    import subprocess

    try:
        done = subprocess.run(
            ("git", "-C", cwd) + args, capture_output=True, text=True, timeout=20
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        return ""  # TimeoutExpired lands here: a hung git REFUSES, never crashes
    return done.stdout.strip() if done.returncode == 0 else ""


def hex_tokens(text):
    out = []
    for raw in text.replace(",", " ").replace(":", " ").split():
        token = raw.strip("().;'\"[]<>")
        if 7 <= len(token) <= 40 and all(c in "0123456789abcdefABCDEF" for c in token):
            out.append(token)
    return out


def verify_evidence(intervention, evidence):
    """(ok, why). REFUSE rather than write an unverified activation row."""
    text = (evidence or "").strip()
    if len(text) < 8:
        return False, "evidence is empty or too short to verify"
    if CLAIM.get(intervention) == "deploy":
        return verify_deploy(text)
    return verify_commit(text)


def verify_deploy(text):
    """A deploy claim needs `gearbox deploy` exit 0 AND a changed ~/.claude tree.

    Both facts are in the deploy output itself: `deploy OK — <dir> is at <sha>`
    is only printed on the success path, and `nothing (clean no-op)` is what
    the changed-files section prints when the tree did not move. The sha is
    then checked against ~/.claude's CURRENT HEAD, so a stale paste from an
    earlier deploy cannot pass.
    """
    if DEPLOY_OK_MARKER not in text:
        return False, "deploy evidence carries no `gearbox deploy` success line (%r)" % (
            DEPLOY_OK_MARKER,
        )
    if DEPLOY_NOOP_MARKER in text:
        return False, "gearbox deploy reported a clean no-op: the ~/.claude tree did not change"
    live = git_out(claude_dir(), "rev-parse", "HEAD")
    if not live:
        return False, "cannot read %s HEAD, so the deploy claim cannot be verified" % claude_dir()
    if live not in text and live[:7] not in text:
        return False, "the deploy output does not name %s's current HEAD (%s) — stale evidence" % (
            claude_dir(),
            live[:12],
        )
    return True, "deploy verified against %s HEAD %s" % (claude_dir(), live[:12])


def verify_commit(text):
    for token in hex_tokens(text):
        if git_out(repo_dir(), "rev-parse", "--verify", "--quiet", token + "^{commit}"):
            return True, "commit %s exists in %s" % (token, repo_dir())
    return False, "no commit sha in the evidence resolves in %s" % repo_dir()


def cites_sha(evidence, sha):
    """Does this evidence name `sha`? Compared as whole hex TOKENS, either of
    which may be the abbreviated form — never as a substring, because a 7-hex
    prefix genuinely occurs inside an unrelated 40-hex sha."""
    for token in hex_tokens(evidence or ""):
        lo, hi = sorted((token.lower(), sha.lower()), key=len)
        if hi.startswith(lo):
            return True
    return False


def same_deploy_conflict(intervention, evidence):
    """(other_event_id, sha) when this activation rides a deploy ANOTHER
    intervention already claimed, else (None, None).

    This is the confound: two interventions citing the same deploy sha share
    one cohort by construction, so their cohorts are identical and no amount of further
    data can ever tell them apart —
    compaction_retro.verdict_for is forced to return `confounded` for both,
    forever. The reader says so honestly; nothing stopped the WRITER creating it.

    Time is not the test. Two rows written an hour apart off one deploy are just
    as confounded as two written a second apart, and the deploy sha is the fact
    that says so. Only a `deploy` claim has one; a `commit` claim (repo_diet)
    goes live per repository and is not gated here.
    """
    if CLAIM.get(intervention) != "deploy":
        return None, None
    live = git_out(claude_dir(), "rev-parse", "HEAD")
    if not live or not cites_sha(evidence, live):
        return None, None      # verify_deploy refuses this case on its own
    for row in read_activations():
        if row.get("intervention") == intervention:
            continue
        if cites_sha(row.get("evidence"), live):
            return row.get("event_id"), live[:12]
    return None, None


def read_activations():
    rows = []
    try:
        with open(os.path.join(root(), "activations.ndjson"), encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    rows.append(row)
    except OSError:
        return []
    return rows


def cmd_activation(argv):
    import argparse

    parser = argparse.ArgumentParser(prog="compact-policy.py activation")
    parser.add_argument("--intervention", choices=INTERVENTIONS, required=True)
    parser.add_argument("--evidence", required=True)
    parser.add_argument("--source", choices=("operator", "session"), required=True)
    parser.add_argument("--scope")
    parser.add_argument("--restage", action="store_true")
    args = parser.parse_args(argv)
    # Deliberately NOT gagged by the kill switch — see this module's docstring.
    mine = [r for r in read_activations() if r.get("intervention") == args.intervention]
    if mine and not args.restage:
        print("REFUSED (duplicate): %s already has an activation row:" % args.intervention)
        print(json.dumps(mine[-1], separators=(",", ":")))
        return 0  # a plain re-run is a no-op, never a failure
    ok, why = verify_evidence(args.intervention, args.evidence)
    if not ok:
        sys.stderr.write("REFUSED (evidence): %s\n" % why)
        return 2
    peer, sha = same_deploy_conflict(args.intervention, args.evidence)
    if peer:
        sys.stderr.write(
            "REFUSED (same deploy): %s and %s would both go live at %s, so their\n"
            "cohorts are identical by construction and neither can ever be measured.\n"
            "Either deploy the two surfaces separately, or record them as ONE\n"
            "intervention whose --scope names both.\n"
            % (args.intervention, peer, sha)
        )
        return 2
    row = {
        "schema": 1,
        "event_id": "%s#%d" % (args.intervention, len(mine) + 1),
        "ts_utc": iso_utc(),
        "intervention": args.intervention,
        "scope": args.scope or DEFAULT_SCOPE[args.intervention],
        "evidence": args.evidence,
        "source": args.source,
    }
    append_line(os.path.join(root(), "activations.ndjson"), row)
    print(json.dumps(row, separators=(",", ":")))
    ledger(
        "activation",
        os.environ.get("CLAUDE_CODE_SESSION_ID") or "operator",
        "activation:%s (%s)" % (row["event_id"], why),
        intervention=args.intervention,
        decision="recorded",
    )
    return 0


def cmd_status(argv, safe_point_reader):
    import argparse

    parser = argparse.ArgumentParser(prog="compact-policy.py status")
    parser.add_argument("--session")
    args = parser.parse_args(argv)
    session = args.session or os.environ.get("CLAUDE_CODE_SESSION_ID") or "unknown"
    policy = read_policy(session)
    print("session: %s" % session)
    # Printed FIRST-ish and always: under GEARBOX_COMPACT_SOURCE=probe this is the
    # probe subtree, and an operator reading a status must never mistake probe
    # state for live state.
    print("state_root: %s" % root())
    print("policy: %s" % (json.dumps(policy, separators=(",", ":")) if policy else "(none)"))
    print("effective_safe_point: %s" % safe_point_reader(policy))
    print("kill_switch: %s" % (kill_switch() or "off"))
    print("policy_version: %d" % POLICY_VERSION)
    print("last 5 decisions:")
    for line in tail_lines(os.path.join(root(), "decisions.ndjson"), 5) or ["(none)"]:
        print("  " + line)
    return 0
