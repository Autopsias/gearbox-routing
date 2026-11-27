"""One parsed, surface-attested reviewer answer, judged against the ledger (split out of the gate at its 500-line bound)."""
from __future__ import annotations

import sys

import llm_review_ledger as ledger
import llm_review_policy as policy


def judge_round(args, led, intent, data, attempt, verdict, count, why, claimed, expected, untagged):
    """Print the round, record it in the ledger, and return True when it FAILS the gate."""
    print(f"[llm-review-gate] level={args.level} verdict={verdict} "
          f"findings={count} shape={why!r} attempt={attempt} "
          f"reviewed_files={claimed}/{expected}")
    print(f"[intent-into-review] intent_block={'present' if intent else 'absent'} "
          f"source=lane-b-verify findings_classified={count}")
    parsed = ledger.findings_array(data.get("result"))
    if args.blocking_rule:   # an untagged finding blocks AND is recorded (I1, I2)
        parsed += untagged(data.get("result"))
    fail, counts, recs = ledger.judge(led, parsed, ledger.parse_floor(args.blocking_severity),
                                      bool(args.blocking_rule))
    if args.blocking_rule:
        print(ledger.not_blocking_block(parsed).lstrip("\n"))
    fail, recs, moved = policy.apply(args, led, parsed, recs, fail)
    print(policy.report(moved), end="")
    if led:
        ledger.record(led, recs)
        print(ledger.convergence_line(counts) + ledger.accepted_block(recs))
    # `not fail` ALONE: an empty answer must not clear an open prior ("omission is not a fix").
    if not fail:
        if count:
            print(f"\n--- reviewer findings ({count}), none blocking ---\n{data.get('result')}")
        return False
    print(f"\n--- reviewer findings ({count}) ---\n{data.get('result')}")
    print(f"\nllm-review-{args.level}: FAILED with {count} finding(s). "
          f"Fix them, or (for a deliberate choice) say so in the session's "
          f"## Work text so the reviewer classifies it ask-user.", file=sys.stderr)
    return True
