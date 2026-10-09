#!/usr/bin/env python3
"""How long each repo's gates take, from the `gate_run` events.

    python3 gate_durations.py ~/DeveloperFolder/example-project ~/your-private-harness --since 2026-10-05

Reads `<repo>/_plans/*/run.ndjson` and prints, per repo and gate: runs, median
and longest seconds, and total minutes. `verify` and `land*` phases are summed
together. Written so the "do test gates share the machine?" decision rests on
measured times, not guesses.
"""

import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path


def runs(repo, since=""):
    out = defaultdict(list)
    for f in sorted(Path(repo).expanduser().glob("_plans/*/run.ndjson")):
        for line in f.read_text(errors="replace").splitlines():
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if (isinstance(e, dict) and e.get("event") == "gate_run"
                    and isinstance(e.get("seconds"), (int, float)) and e.get("ts", "") >= since):
                out[e.get("gate")].append(float(e["seconds"]))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("repos", nargs="+")
    ap.add_argument("--since", default="", help="ISO date; older events are skipped")
    a = ap.parse_args(argv)
    print(f"{'repo':<18} {'gate':<34} {'runs':>5} {'median s':>9} {'max s':>8} {'total min':>10}")
    for repo in a.repos:
        found = runs(repo, a.since)
        if not found:
            print(f"{Path(repo).name:<18} (no gate_run events)")
        for gate, s in sorted(found.items(), key=lambda kv: -sum(kv[1])):
            print(f"{Path(repo).name:<18} {str(gate):<34} {len(s):>5} {statistics.median(s):>9.0f}"
                  f" {max(s):>8.0f} {sum(s) / 60:>10.1f}")


if __name__ == "__main__":
    main()
