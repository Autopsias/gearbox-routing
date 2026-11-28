"""The review gate's CLI, split out of llm_review_gate.py when that file sat at its
500-line bound and the review policy flags needed room. Argument definitions are not control flow.
"""
import argparse
import os

import untrusted_block as ub


def blocking_rule_block(blocking_rule, max_chars):
    """The plan author's blocking-rule section appended to the review prompt."""
    return (
        "\n\n" + ub.untrusted_block(blocking_rule, label="BLOCKING RULE",
                                     source="the plan author's conditions", max_chars=max_chars,
                                     keep="head") + "\n"
        "Give EVERY finding a `\"rule\"` field: the number of the listed condition it meets, "
        "or `\"none\"` when it meets no condition. A finding that meets no condition stays IN "
        "the array with `\"rule\": \"none\"`; never drop it. The gate blocks on the rule "
        "NUMBER you give; a finding with no `rule` field blocks."
    )


def rule_too_long(args, max_chars):
    """Trim --blocking-rule once (a blank rule is no rule). True when it is over max_chars:
    a cut rule would gate on a partial list, so the gate refuses, never truncates."""
    args.blocking_rule = (args.blocking_rule or "").strip()
    if len(args.blocking_rule) <= max_chars:
        return False
    print(f"[llm-review-gate] blocking rule is over {max_chars} chars; refusing to gate on a "
          "partial rule. Shorten verify.blocking_rule.")
    return True


def parse_args(argv, description, levels):
    ap = argparse.ArgumentParser(description=description)
    ap.add_argument("--level", required=True, choices=levels, help="review level to run")
    ap.add_argument("--reviewer", default="claude", choices=("claude", "codex"),
                    help="which FAMILY reviews. `claude` is the on-box reviewer -- "
                         "the default, and every line of this file's history. "
                         "`codex` hands the SAME prepared surface to a read-only "
                         "`codex exec`, so the code is read by a model that did "
                         "not write it (see codex_review_backend.py).")
    ap.add_argument("--reviewer-model", default="sonnet", choices=("opus", "sonnet"),
                    help="which Claude model reviews when the family is claude. `sonnet` is the "
                         "default from 2026-10-11 (operator decision): on 20 broken + 20 correct "
                         "eval diffs it separated them at least as well as opus at under half "
                         "the cost. `opus` was the default before that date.")
    ap.add_argument("--cwd", default=".", help="directory the review runs in")
    ap.add_argument("--harness", choices=("claude", "codex"),
                    default=os.environ.get("PLAN_EXECUTE_HARNESS", "claude"),
                    help="which family BUILT the code under review (verify.py "
                         "derives it from the session's own dispatch record). Under "
                         "`codex` the cross-family reviewer is CLAUDE, which is "
                         "opt-in: see verifier_park.refusal.")
    ap.add_argument("--timeout", type=int, default=180,
                    help="per-attempt wall limit in seconds (the gate makes at "
                         "most 2 attempts, so budget the registry timeout at "
                         "2x this plus slack)")
    ap.add_argument("--intent-file", default=os.environ.get("PLAN_EXECUTE_INTENT_FILE", ""),
                    help="file holding the session intent the reviewer is told")
    ap.add_argument("--blocking-rule", default=os.environ.get("PLAN_EXECUTE_BLOCKING_RULE", ""),
                    help="the plan author's numbered blocking conditions (the session's "
                         "verify.blocking_rule). With it, a finding blocks only when the reviewer "
                         "ties it to a rule number; `rule: none` is recorded noted. Empty = off.")
    ap.add_argument("--base", default=os.environ.get("PLAN_EXECUTE_REVIEW_BASE", ""),
                    help="git ref the session started from. The reviewed surface "
                         "becomes `git diff <base>` (commits AND working tree). "
                         "Without it the surface is `git diff HEAD`, which is EMPTY "
                         "for any session that committed its work.")
    ap.add_argument("--plan-dir", default=os.environ.get("PLAN_EXECUTE_PLAN_DIR", ""),
                    help="the plan directory this session belongs to. With it, "
                         "untracked files under ANOTHER plan are not this "
                         "session's surface. Unset means the scoping rule cannot "
                         "be evaluated and does not fire.")
    ap.add_argument("--session", default=os.environ.get("PLAN_EXECUTE_SESSION", ""),
                    help="the session id. With it, another session's `_evidence/` "
                         "is not this session's surface either.")
    ap.add_argument("--exclude", default=os.environ.get("PLAN_EXECUTE_REVIEW_EXCLUDE", ""),
                    help="Paths to REMOVE from the surface (comma/colon separated). The "
                         "mirror of --scope: the land re-gate reviews the tree it is about "
                         "to push, which carries the plan's own generated record, and an "
                         "include-list cannot say 'everything but that'.")
    ap.add_argument("--scope", default=os.environ.get("PLAN_EXECUTE_REVIEW_SCOPE", ""),
                    help="comma/colon-separated path prefixes this session owns "
                         "(the session spec's `review_scope`). The surface -- the "
                         "diff AND the untracked list -- is restricted to them, so a "
                         "CONCURRENT session's source cannot raise findings against "
                         "this one. Empty means the whole tree, the old behaviour.")
    ap.add_argument("--blocking-severity",
                    default=os.environ.get("PLAN_EXECUTE_BLOCKING_SEVERITY", ""),
                    help="comma/colon-separated severities that BLOCK. Anything below "
                         "is recorded as advisory `noted` -- kept in the ledger, "
                         "printed, carried forward, and never a reason to re-dispatch "
                         "the session. Empty uses the ledger's floor "
                         "(high,critical,blocker). Pass a longer list (e.g. "
                         "'high,medium') for a session that wants a stricter gate.")
    ap.add_argument("--stop-after-round", type=int, default=0,
                    help="review policy (canary, off at 0): from attempt N+1, a NEW blocking "
                         "finding in the fix delta is recorded `noted` (policy=followup) "
                         "instead of blocking. See llm_review_policy.py.")
    ap.add_argument("--require-repro", action="store_true",
                    help="review policy (canary): ask for `repro` on every blocking finding; "
                         "one without it is labelled policy=unproven and STILL blocks.")
    return ap.parse_args(argv)
