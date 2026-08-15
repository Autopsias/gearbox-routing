#!/usr/bin/env python3
"""Validate a plan spec JSON file. Surfaces errors before build."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_plan
from build_plan import validate_spec

def main():
    if len(sys.argv) != 2:
        print("Usage: validate_spec.py <spec.json>", file=sys.stderr)
        sys.exit(1)
    spec_path = Path(sys.argv[1])
    spec = json.loads(spec_path.read_text())
    try:
        validate_spec(spec)
    except ValueError as e:
        print(f"INVALID: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"OK: {spec_path} — {len(spec['categories'])} categories, "
          f"{len(spec['items'])} items, {len(spec['sessions'])} sessions, "
          f"infographic={spec['infographic']['type']}")
    # How parallel the dispatch graph actually is (S07 / PL-03). Reported here
    # unconditionally — this is the command an author runs — while
    # `validate_spec()` itself stays quiet unless the plan is a pure chain, so
    # the dozens of library callers that only want validation get no new noise.
    built = build_plan.with_integration_sessions(spec)
    rep = build_plan.parallelism_report(built)
    print(f"    parallelism: {build_plan.format_parallelism_report(rep)}")
    # Only worth listing when the plan is a CHAIN: on an already-parallel plan
    # every disjoint pair is noise, not a finding.
    if rep["max_width"] == 1 and rep["groupable"]:
        pairs = "; ".join(f"{a}+{b}" for a, b, _wa, _wb in rep["groupable"][:8])
        more = f" (+{len(rep['groupable']) - 8} more)" if len(rep["groupable"]) > 8 else ""
        print(f"    file-disjoint pairs that could share a parallel_group: {pairs}{more}")
    if rep["opaque"]:
        print(f"    no parseable `touches` (treated as conflicting with everything): "
              f"{', '.join(rep['opaque'])}")

if __name__ == "__main__":
    main()
