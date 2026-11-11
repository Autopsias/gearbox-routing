#!/usr/bin/env python3
"""S02 functional proof: content-signal scanner (F-03) + manifest gate (F-05).

Proves, with the real tools and no network:
  1. content_signals.py escalates an 'internal'-declared workbook to board-regulatory
     when it contains formulas + financial + board/investor terms + an external email.
  2. manifest_gate.py BLOCKS a model-run loop when sensitivity/routing are not recorded,
     and passes (ok) once they are.
  3. The complete manifest validates against manifest.schema.json (jsonschema).

Run via fixtures/run_fixtures.sh, or directly:
    python fixtures/fixture_s02_policy.py
Exit 0 = PASS, non-zero = FAIL.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))  # plugin root: office-trust-layer/
sys.path.insert(0, os.path.join(ROOT, "skills", "travel-class-router", "scripts"))
sys.path.insert(0, os.path.join(ROOT, "skills", "trust-review", "references", "scripts"))

import content_signals  # noqa: E402
import manifest_gate     # noqa: E402

FAILURES: list[str] = []


def check(cond: bool, msg: str):
    print(("  PASS " if cond else "  FAIL ") + msg)
    if not cond:
        FAILURES.append(msg)


def build_xlsx(path: str):
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["A1"] = "Q2 Board Pack — EBITDA margin forecast"
    ws["A2"] = "Revenue"
    ws["B2"] = 1000
    ws["A3"] = "Costs"
    ws["B3"] = 600
    ws["A4"] = "EBITDA"
    ws["B4"] = "=B2-B3"            # live formula
    ws["A5"] = "Margin %"
    ws["B5"] = "=B4/B2"           # live formula
    ws["A6"] = "Prepared for the board of directors and investor due diligence"
    ws["A7"] = "Send to analyst@externalcap.com for review"
    ws["B8"] = 12.5
    ws["B9"] = 9.0
    ws["B10"] = 42
    wb.save(path)


def main() -> int:
    print("S02 policy fixture — content scanner (F-03) + manifest gate (F-05)")
    with tempfile.TemporaryDirectory() as tmp:
        xlsx = os.path.join(tmp, "board_pack.xlsx")
        build_xlsx(xlsx)

        print("\n[1] content_signals.scan() on an 'internal'-declared board workbook")
        r = content_signals.scan(xlsx, declared="internal", internal_domain="mycompany.com")
        print(json.dumps({k: r[k] for k in ("declared_class", "effective_class", "escalated", "signals")}, indent=2))
        check(r["scanned"] is True, "file was scanned locally")
        check("formula" in r["signals"], "detected live formulas")
        check("financial-metric" in r["signals"], "detected financial metrics")
        check("investor-board-client-term" in r["signals"], "detected board/investor terms")
        check("external-recipient" in r["signals"], "detected external recipient")
        check(r["effective_class"] == "board-regulatory", "escalated internal -> board-regulatory")
        check(r["escalated"] is True, "escalation flag set")

        print("\n[2] manifest_gate.gate() BLOCKS when sensitivity/routing missing")
        incomplete = {
            "manifest_version": "1.0",
            "run_id": "2026-05-29T1430_test",
            "created": "2026-05-29T14:30:00Z",
            "artifact": {"path": "board_pack.xlsx", "sha256": "deadbeef"},
            "travel_class": {"effective": "board-regulatory"},
            # no sensitivity, no routing
        }
        v = manifest_gate.gate(incomplete)
        print(json.dumps(v, indent=2))
        check(v["status"] == "blocked", "incomplete manifest -> blocked")
        check(v["sensitivity_recorded"] is False, "sensitivity flagged as not recorded")
        check(v["routing_recorded"] is False, "routing flagged as not recorded")
        check(any("sensitivity" in m for m in v["missing"]), "missing list names sensitivity")
        check(any("routing" in m for m in v["missing"]), "missing list names routing")

        print("\n[3] manifest_gate.gate() PASSES when both recorded")
        complete = {
            "manifest_version": "1.0",
            "run_id": "2026-05-29T1430_boardpack",
            "created": "2026-05-29T14:30:05Z",
            "artifact": {"path": "board_pack.xlsx", "sha256": "abc123", "kind": "workbook"},
            "travel_class": {"value": "board-regulatory", "declared": "internal",
                             "effective": "board-regulatory", "source": "escalated",
                             "escalation_signals": ["formula", "financial-metric",
                                                    "investor-board-client-term", "external-recipient"],
                             "waiver": None},
            "sensitivity": {"run_max": "restricted",
                            "sources": [{"source_id": "S1", "sensitivity": "restricted", "redaction": "mask"}],
                            "redaction_default": "mask", "mask_map_sha256": "m1"},
            "routing": {"policy": "board-regulatory/restricted", "local_only": False, "two_context": True,
                        "builder_model": {"name": "model-x", "version": "1", "context": "builder"},
                        "reviewer_model": {"name": "model-y", "version": "1", "context": "reviewer", "independent": True},
                        "independence_note": None},
            "rounds": [{"round": 1, "role": "reviewer",
                        "model": {"name": "model-y", "version": "1"},
                        "timestamp": "2026-05-29T14:31:10Z",
                        "inputs_seen": [{"source_id": "S1", "exposure": "masked-excerpt",
                                         "locator": "Outputs!B4:B5", "sha256": "h1"}],
                        "prompt_hashes": {"reviewer_prompt_sha256": "p1", "source_packet_sha256": "p2",
                                          "input_artifact_sha256": "p3", "provenance_record_sha256": "p4"}}],
            "retention": {"purge_after": "2026-06-05", "retain_excerpts": False},
            "gate": {"sensitivity_recorded": True, "routing_recorded": True, "status": "ok"},
        }
        v2 = manifest_gate.gate(complete)
        print(json.dumps(v2, indent=2))
        check(v2["status"] == "ok", "complete manifest -> ok (model-run loop may start)")
        check(v2["missing"] == [], "no missing keys")

        print("\n[4] complete manifest validates against manifest.schema.json")
        try:
            import jsonschema
            with open(os.path.join(ROOT, "skills", "trust-review", "references", "manifest.schema.json"), encoding="utf-8") as fh:
                schema = json.load(fh)
            jsonschema.validate(complete, schema)
            check(True, "jsonschema validation passed")
        except Exception as exc:  # noqa: BLE001
            check(False, f"jsonschema validation: {exc}")

        print("\n[5] gate --write round-trips through a real file")
        mpath = os.path.join(tmp, "manifest.json")
        with open(mpath, "w") as fh:
            json.dump(incomplete, fh)
        rc = manifest_gate.main([mpath, "--write"])
        with open(mpath) as fh:
            written = json.load(fh)
        check(rc == 3, "CLI exit code 3 on blocked")
        check(written["gate"]["status"] == "blocked", "gate{} written back to file")

    print()
    if FAILURES:
        print(f"FIXTURE RESULT: FAIL ({len(FAILURES)} assertion(s))")
        return 1
    print("FIXTURE RESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
