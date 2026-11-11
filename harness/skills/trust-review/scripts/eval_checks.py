#!/usr/bin/env python3
"""trust-review E-check — manifest completeness (contract §2.3).

ONE binary check, per the approved 2026-06-04 catalog block (the lightest
treatment in the estate — the two-context loop IS the eval machinery; this
verifies the one thing nothing else re-checks: that independence is provable
from the written manifest). See references/_evals.md.

Usage:
    python3 scripts/eval_checks.py --run-dir .trust/<run_id>

Exit 0 = PASS. Exit 1 = FAIL. Exit 2 = bad args.
"""

import argparse
import json
import re
import sys
from pathlib import Path

ISO_TS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")
REQUIRED_HASHES = ("reviewer_prompt_sha256", "input_artifact_sha256",
                   "provenance_record_sha256")


def check_round(i: int, rnd: dict) -> list:
    problems = []
    model = rnd.get("model") or {}
    if not (model.get("name") or "").strip():
        problems.append("model.name missing")
    if not (str(model.get("version") or "")).strip():
        problems.append("model.version missing")
    ts = rnd.get("timestamp") or ""
    if not ISO_TS.match(str(ts)):
        problems.append(f"timestamp {ts!r} not ISO-8601")
    hashes = rnd.get("prompt_hashes") or {}
    for key in REQUIRED_HASHES:
        if not (str(hashes.get(key) or "")).strip():
            problems.append(f"prompt_hashes.{key} missing")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-dir", required=True, help=".trust/<run_id> directory")
    args = ap.parse_args()

    run_dir = Path(args.run_dir)
    if not run_dir.is_dir():
        print(f"E-CHECKS: run dir {run_dir} not found", file=sys.stderr)
        return 2

    manifest_path = run_dir / "manifest.json"
    if not manifest_path.exists():
        print("- E1 FAIL — manifest.json missing (independence cannot be re-derived; "
              "silence never passes)")
        print("\nE-checks: 0/1 passed, 1 FAIL")
        return 1
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        print(f"- E1 FAIL — manifest.json unparseable: {e}")
        print("\nE-checks: 0/1 passed, 1 FAIL")
        return 1

    rounds = manifest.get("rounds") or []
    if not rounds:
        print("- E1 FAIL — rounds[] empty — the loop wrote no independence records")
        print("\nE-checks: 0/1 passed, 1 FAIL")
        return 1

    bad = []
    for i, rnd in enumerate(rounds):
        problems = check_round(i, rnd)
        if problems:
            bad.append(f"round {rnd.get('round', i)}: " + ", ".join(problems))
    if bad:
        print(f"- E1 FAIL — {len(bad)}/{len(rounds)} round records incomplete — "
              + "; ".join(bad[:3]))
        print("\nE-checks: 0/1 passed, 1 FAIL")
        return 1

    print(f"- E1 PASS — {len(rounds)}/{len(rounds)} rounds carry model+timestamp+3 hashes")
    print("\nE-checks: 1/1 passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
