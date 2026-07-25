#!/usr/bin/env python3
"""Validate a plan spec JSON file. Surfaces errors before build."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
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

if __name__ == "__main__":
    main()
