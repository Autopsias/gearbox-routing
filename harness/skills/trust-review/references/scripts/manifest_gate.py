#!/usr/bin/env python3
"""Acceptance gate for the Trust Layer (Office Trust Layer plugin, F-05 / S02).

No model-run loop may start until the run's manifest records BOTH a sensitivity
classification AND a model-routing decision. This module is the mechanical check
behind that rule (sensitivity-routing-policy.md §8.1; conventions.md §3.2 + status
enum). Skills call `gate(manifest)` before their first model round; a `blocked`
result means: do NOT invoke a model — return status `blocked` with this reason.

Pure standard library: no third-party dependency, so it runs anywhere the harness
runs (local-first). An optional `--full` flag validates the whole manifest against
manifest.schema.json IF `jsonschema` happens to be installed; the gate itself never
needs it.

Usage:
    python manifest_gate.py <manifest.json>          # check; exit 0 ok / 3 blocked
    python manifest_gate.py <manifest.json> --write   # also write gate{} back
    python manifest_gate.py <manifest.json> --full     # + schema validation if available
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import sys

TRAVEL_CLASSES = {"internal", "travels", "board-regulatory"}
SENSITIVITY_CLASSES = {"public", "internal", "confidential", "restricted"}


def _nonempty(x) -> bool:
    return x is not None and x != "" and x != [] and x != {}


def check_sensitivity(m: dict) -> tuple[bool, list[str]]:
    """Sensitivity is recorded when run_max is set and >=1 source carries a class."""
    missing: list[str] = []
    s = m.get("sensitivity") or {}
    if s.get("run_max") not in SENSITIVITY_CLASSES:
        missing.append("sensitivity.run_max")
    sources = s.get("sources") or []
    classed = [x for x in sources if (x or {}).get("sensitivity") in SENSITIVITY_CLASSES]
    if not classed:
        missing.append("sensitivity.sources[].sensitivity")
    return (not missing), missing


def check_routing(m: dict) -> tuple[bool, list[str]]:
    """Routing is recorded when policy is set AND a model decision exists:
    either local_only==True, or builder_model/reviewer_model is populated."""
    missing: list[str] = []
    r = m.get("routing") or {}
    if not _nonempty(r.get("policy")):
        missing.append("routing.policy")
    decided = bool(r.get("local_only")) or _nonempty(r.get("builder_model")) or _nonempty(r.get("reviewer_model"))
    if not decided:
        missing.append("routing.{local_only|builder_model|reviewer_model}")
    return (not missing), missing


def check_travel_class(m: dict) -> tuple[bool, list[str]]:
    tc = (m.get("travel_class") or {}).get("effective")
    return (tc in TRAVEL_CLASSES), ([] if tc in TRAVEL_CLASSES else ["travel_class.effective"])


def gate(manifest: dict) -> dict:
    """Return the gate verdict for a manifest dict.

    {status: 'ok'|'blocked', sensitivity_recorded, routing_recorded,
     travel_class_recorded, missing: [...], checked_at}
    """
    sens_ok, sens_missing = check_sensitivity(manifest)
    route_ok, route_missing = check_routing(manifest)
    tc_ok, tc_missing = check_travel_class(manifest)
    missing = sens_missing + route_missing + tc_missing
    status = "ok" if (sens_ok and route_ok and tc_ok) else "blocked"
    return {
        "status": status,
        "sensitivity_recorded": sens_ok,
        "routing_recorded": route_ok,
        "travel_class_recorded": tc_ok,
        "missing": missing,
        "checked_at": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "reason": "" if status == "ok" else ("model-run loop blocked: missing " + ", ".join(missing)),
    }


def _maybe_schema_validate(manifest: dict, schema_path: str) -> str | None:
    try:
        import jsonschema  # type: ignore
    except Exception:
        return "jsonschema not installed; skipped full schema validation (gate is independent of it)"
    try:
        with open(schema_path, encoding="utf-8") as fh:
            schema = json.load(fh)
        jsonschema.validate(manifest, schema)
        return "schema: valid"
    except Exception as exc:  # noqa: BLE001
        return f"schema: INVALID — {exc}"


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("--")]
    flags = {a for a in argv if a.startswith("--")}
    if not args:
        print("usage: manifest_gate.py <manifest.json> [--write] [--full]", file=sys.stderr)
        return 2
    path = args[0]
    with open(path, encoding="utf-8") as fh:
        manifest = json.load(fh)

    verdict = gate(manifest)

    if "--full" in flags:
        schema_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(path))), "manifest.schema.json")
        # also try the canonical references/ location relative to this script
        if not os.path.exists(schema_path):
            schema_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "manifest.schema.json")
        verdict["schema_check"] = _maybe_schema_validate(manifest, schema_path)

    if "--write" in flags:
        manifest["gate"] = {
            "sensitivity_recorded": verdict["sensitivity_recorded"],
            "routing_recorded": verdict["routing_recorded"],
            "status": verdict["status"],
            "missing": verdict["missing"],
            "checked_at": verdict["checked_at"],
        }
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, indent=2)
            fh.write("\n")

    print(json.dumps(verdict, indent=2))
    return 0 if verdict["status"] == "ok" else 3


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
