#!/usr/bin/env python3
"""FD-03 functional proof: the backward-compatibility / migration gate (conventions.md §7.8).

Drives `eval/compat_harness.py` over the REAL committed `.trust/<run_id>/` runs AND a set of
synthetic v1/v2/v3 manifests, asserting:
  1. Every committed run parses and loads under the v3 reader; the gate covers ≥1 v1 AND ≥1 v2.
  2. New v3 optional fields default safely — absent ⇒ v1/v2 behaviour, never a crash
     (degraded_status ⇒ [], reviewer_independence inferred-not-overstated, schema_version detected).
  3. A v3 refresh consumes a prior run READ-ONLY — the whole .trust tree is byte-identical before
     and after, and the gate writes its stamp ONLY into a fresh out_dir, never a prior run dir.
  4. New output is stamped schema_version=3 + migration_notes.
  5. v3_view never mutates its input; schema-version detection is content-based.

Run via fixtures/run_fixtures.sh, or directly:  python fixtures/fixture_migration_gate.py
Exit 0 = PASS, non-zero = FAIL.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, os.path.join(ROOT, "skills", "trust-review", "references", "scripts"))  # compat_harness ships here now

import compat_harness as CH  # noqa: E402

FAILURES: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("  PASS " if cond else "  FAIL ") + msg)
    if not cond:
        FAILURES.append(msg)


def _tree_fingerprint(root: str) -> dict:
    fp = {}
    for dp, _dn, fns in os.walk(root):
        for fn in fns:
            p = os.path.join(dp, fn)
            try:
                with open(p, "rb") as fh:
                    fp[p] = hashlib.sha256(fh.read()).hexdigest()
            except OSError:
                fp[p] = "unreadable"
    return fp


def main() -> int:
    print("FD-03 migration / backward-compat gate (conventions.md §7.8)")
    print("-" * 70)

    trust_root = os.path.join(HERE, "trust_runs")

    # 3 (setup). Whole-tree fingerprint of .trust BEFORE the gate runs.
    before = _tree_fingerprint(trust_root)

    out_dir = tempfile.mkdtemp(prefix="otl_compat_")  # honours TMPDIR; OUTSIDE .trust
    result = CH.run_compat(trust_root, out_dir=out_dir)

    # 1. all committed runs load; range covers v1 + v2
    check(result["pass"], "the migration gate PASSES over the committed .trust runs")
    check(result["runs_total"] >= 2, f"≥2 committed runs discovered (got {result['runs_total']})")
    check(result["by_schema_version"]["1"] >= 1 and result["by_schema_version"]["2"] >= 1,
          f"range covers ≥1 v1 AND ≥1 v2 (got {result['by_schema_version']})")
    check(all(r.get("ok") for r in result["rows"]),
          "every committed run resolves its core required fields, runs the gate, and v3-views cleanly")
    check(all(r["no_mutation"] for r in result["rows"]),
          "every committed manifest is byte-identical after the read (per-file no-mutation)")

    # 3. READ-ONLY: the whole .trust tree is unchanged and nothing was written into a prior run dir.
    after = _tree_fingerprint(trust_root)
    check(before == after, "the entire .trust tree is byte-identical before/after (a v3 refresh is read-only)")
    check(not result["prior_runs_mutated"], "the gate reports zero prior-run mutations")
    check(os.path.commonpath([out_dir, trust_root]) != trust_root or not out_dir.startswith(trust_root),
          "the gate's stamp out_dir is OUTSIDE any prior .trust run dir")

    # 4. the new output is stamped schema_version=3 + migration notes
    mig = json.load(open(os.path.join(out_dir, "migration.json"), encoding="utf-8"))
    check(mig.get("schema_version") == "3", "new output is stamped schema_version=3")
    check(bool(mig.get("migration_notes")), "new output carries migration_notes")
    check(os.path.exists(os.path.join(out_dir, "compat-harness.md")), "a human-readable compat-harness.md is written")

    # 2 + 5. default-safe behaviour on SYNTHETIC manifests (independent of which runs are committed)
    # v1-shaped: no manifest_version, no gate, no v2/v3 blocks
    v1 = {"run_id": "r1", "travel_class": {"effective": "internal"},
          "sensitivity": {"run_max": "internal", "sources": [{"source_id": "S1", "sensitivity": "internal"}]},
          "routing": {"policy": "local", "two_context": True,
                      "builder_model": {"name": "Opus"}, "reviewer_model": {"name": "Sonnet"}}}
    snap = json.dumps(v1, sort_keys=True)
    view = CH.v3_view(v1)
    check(json.dumps(v1, sort_keys=True) == snap, "v3_view does NOT mutate its input (v1)")
    check(view["degraded_status"] == [], "absent degraded_status ⇒ [] (v1/v2 readiness behaviour)")
    check(view["routing"]["reviewer_independence"] == "same-family",
          "a different-model two-context split infers 'same-family' (never overstated to cross-vendor)")
    check(CH.detect_schema_version(v1) == "1", "a v1-shaped manifest is detected as schema 1")

    # local-only / no reviewer ⇒ same-model (no independence claimed)
    v1b = {"run_id": "r1b", "travel_class": {"effective": "internal"},
           "sensitivity": {"run_max": "internal", "sources": [{"source_id": "S1", "sensitivity": "internal"}]},
           "routing": {"policy": "local-only", "local_only": True}}
    check(CH.v3_view(v1b)["routing"]["reviewer_independence"] == "same-model",
          "a local-only run with no reviewer infers 'same-model' (no independence overstated)")

    # v2-shaped: has a verification block ⇒ detected as schema 2
    v2 = copy.deepcopy(v1); v2["verification"] = {"results": []}; v2["adapter_bindings"] = {"bindings": {}}
    check(CH.detect_schema_version(v2) == "2", "a manifest with a verification/adapter_bindings block is schema 2")

    # v3-shaped: has degraded_status ⇒ detected as schema 3, and the existing value is preserved
    v3 = copy.deepcopy(v1); v3["degraded_status"] = [{"cap": "recalc-unverified"}]
    check(CH.detect_schema_version(v3) == "3", "a manifest with degraded_status is schema 3")
    check(CH.v3_view(v3)["degraded_status"] == [{"cap": "recalc-unverified"}],
          "an EXISTING degraded_status is preserved (not overwritten by the default)")

    # never crashes on a partial loop manifest (no created/artifact) — the real dr10/dr11 shape
    partial = {"run_id": "loop", "travel_class": {"effective": "travels"},
               "sensitivity": {"run_max": "confidential", "sources": [{"source_id": "S1", "sensitivity": "confidential"}]},
               "routing": {"policy": "two-context", "two_context": True}, "rounds": []}
    pv = CH.v3_view(partial)
    check(pv.get("created") == "" and pv.get("artifact") == {} and pv.get("gate") is None,
          "a partial loop manifest (no created/artifact/gate) defaults those safely, never a crash")

    print("-" * 70)
    if FAILURES:
        print(f"RESULT: FAIL ({len(FAILURES)} check(s) failed)")
        for f in FAILURES:
            print("   - " + f)
        return 1
    print("RESULT: PASS — every committed v1/v2 run loads under the v3 reader, new optional fields "
          "default safely (never a crash), a v3 refresh consumes prior runs READ-ONLY (whole-tree "
          "byte-identical), and new output is stamped schema_version=3 + migration notes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
