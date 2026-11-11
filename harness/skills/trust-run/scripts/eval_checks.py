#!/usr/bin/env python3
"""trust-run E-checks — deterministic self-eval runner (contract §2.3).

Four binary checks on the orchestration record, per the approved 2026-06-04
catalog block (orchestrators, light treatment). See references/_evals.md.

Usage:
    python3 scripts/eval_checks.py --run-dir .trust/<run_id>

Exit 0 = all PASS. Exit 1 = at least one FAIL. Exit 2 = bad args.
"""

import argparse
import json
import sys
from pathlib import Path

VERDICTS = {"ready", "ready-with-limitations", "blocked", "not-ready",
            "not-ready-for-forwarding"}
# strictness rank: lower = stricter. Summary rank must be <= min(component ranks).
RANK = {"not-ready": 0, "not-ready-for-forwarding": 0, "blocked": 1,
        "ready-with-limitations": 2, "ready": 3}

STEP_ARTIFACTS = [
    ("router", ["travel-class-router.json"]),
    ("refresh", ["refresh-report.json"]),                       # optional step
    ("source-packet", ["source-packet.json"]),
    ("inspect", ["claim-audit.json", "claim-set.json", "workbook-verdict.json",
                 "document-map.json"]),                          # any one suffices
    ("evidence-map", ["evidence-map.json"]),
    ("review", ["manifest.json"]),
    ("human-gate", ["human-gate-digest.json", "human-gate-digest.md",
                    "human-gate-digest.html"]),                  # any one suffices
]
OPTIONAL_STEPS = {"refresh"}


def load_json(path: Path):
    if not path.exists():
        return None, f"{path.name} missing"
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except Exception as e:  # noqa: BLE001
        return None, f"{path.name} unparseable: {e}"


def claimed_steps(summary) -> set:
    """Steps the summary declares; fall back to all-mandatory when undeclared."""
    declared = None
    for key in ("steps_run", "steps", "pipeline"):
        v = (summary or {}).get(key)
        if isinstance(v, list) and v:
            declared = {str(s).lower() for s in v}
            break
    if declared is None:
        return {name for name, _ in STEP_ARTIFACTS} - OPTIONAL_STEPS
    out = set()
    for name, _ in STEP_ARTIFACTS:
        if any(name in d or d in name for d in declared):
            out.add(name)
    # mandatory core can never be silently un-claimed
    out |= {name for name, _ in STEP_ARTIFACTS} - OPTIONAL_STEPS
    return out


def check_e1(summary, serr, run_dir: Path):
    if serr:
        return False, serr
    steps = claimed_steps(summary)
    missing = []
    for name, candidates in STEP_ARTIFACTS:
        if name not in steps:
            continue
        if not any((run_dir / c).exists() for c in candidates):
            missing.append(f"{name} (none of {candidates[0]}…)")
    # review manifest must actually carry rounds
    if "review" in steps and (run_dir / "manifest.json").exists():
        m, merr = load_json(run_dir / "manifest.json")
        if merr or not (m or {}).get("rounds"):
            missing.append("review (manifest has no rounds[])")
    if missing:
        return False, "FABRICATED STEP(S) — claimed but no artefact: " + "; ".join(missing[:4])
    n = len(steps)
    skipped = ", ".join(sorted(OPTIONAL_STEPS - steps)) or "none"
    return True, f"{n}/{n} claimed steps left artefacts (optional skipped: {skipped})"


def component_verdicts(run_dir: Path) -> list:
    out = []
    for name, key in (("workbook-verdict.json", "readiness"),
                      ("document-verdict.json", "readiness"),
                      ("document-map.json", "readiness")):
        p = run_dir / name
        if p.exists():
            obj, err = load_json(p)
            v = (obj or {}).get(key)
            if not err and v in RANK:
                out.append((name, v))
    return out


def check_e2(summary, serr, run_dir: Path):
    if serr:
        return False, serr
    verdict_keys = [k for k in ("verdict", "readiness", "headline_verdict") if k in summary]
    if not verdict_keys:
        return False, "summary carries no verdict"
    if len(verdict_keys) > 1:
        vals = {summary[k] for k in verdict_keys}
        if len(vals) > 1:
            return False, f"MULTIPLE conflicting verdicts: { {k: summary[k] for k in verdict_keys} }"
    verdict = summary[verdict_keys[0]]
    if verdict not in VERDICTS:
        return False, f"verdict {verdict!r} not in the §1.4+§7.1 enum"
    comps = component_verdicts(run_dir)
    softer = [(n, v) for n, v in comps if RANK[verdict] > RANK[v]]
    if softer:
        return False, (f"verdict {verdict!r} is SOFTER than component(s) "
                       + "; ".join(f"{n}={v}" for n, v in softer[:3])
                       + " — a weak document was averaged away")
    note = f"; strictest component {min(comps, key=lambda t: RANK[t[1]])[1]!r}" if comps else ""
    return True, f"one verdict {verdict!r}{note} — consistent"


def check_e3(summary, serr):
    if serr:
        return False, serr
    fix_list = None
    for key in ("fix_list", "fixes", "prioritised_fix_list", "fixlist"):
        if isinstance(summary.get(key), list):
            fix_list = summary[key]
            break
    if fix_list is None:
        return False, "summary carries no fix-list"
    must = [(i, f) for i, f in enumerate(fix_list)
            if str(f.get("severity", "")).lower() == "must-fix"]
    bad = []
    for i, f in must:
        if not (str(f.get("file") or f.get("artifact") or "").strip()):
            bad.append(f"item {i}: must-fix names no file")
        if not (str(f.get("locator") or f.get("location") or "").strip()):
            bad.append(f"item {i}: must-fix names no locator")
    if must:
        first_non_must = next((i for i, f in enumerate(fix_list)
                               if str(f.get("severity", "")).lower() != "must-fix"), None)
        if first_non_must is not None and any(i > first_non_must for i, _ in must):
            bad.append("must-fix items are not first — list is not prioritised")
    if bad:
        return False, "; ".join(bad[:4])
    return True, f"{len(fix_list)}-item fix-list, {len(must)} must-fix first, all name file+locator"


def check_e4(summary, serr):
    if serr:
        return False, serr
    indep = summary.get("reviewer_independence")
    if not indep:
        return False, "reviewer_independence not recorded in the summary"
    tc = str(summary.get("travel_class") or summary.get("effective_class") or "").lower()
    verdict = summary.get("verdict") or summary.get("readiness")
    if "board" in tc or "regulatory" in tc:
        if str(indep).lower() in ("same-family", "same_family"):
            signoff = summary.get("signoff") or summary.get("named_owner_signoff")
            if verdict == "ready":
                return False, ("Board/Regulatory + same-family reviewer but verdict 'ready' — "
                               "the §7.1 cap was silently bypassed")
            if verdict == "ready-with-limitations" and not signoff:
                return False, ("same-family at ready-with-limitations without a recorded "
                               "named-owner signoff — caps at blocked")
    return True, f"reviewer_independence={indep!r} recorded; cap honored"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-dir", required=True, help=".trust/<run_id> directory")
    args = ap.parse_args()

    run_dir = Path(args.run_dir)
    if not run_dir.is_dir():
        print(f"E-CHECKS: run dir {run_dir} not found", file=sys.stderr)
        return 2

    summary, serr = load_json(run_dir / "trust-run-summary.json")

    results = [
        ("E1", *check_e1(summary, serr, run_dir)),
        ("E2", *check_e2(summary, serr, run_dir)),
        ("E3", *check_e3(summary, serr)),
        ("E4", *check_e4(summary, serr)),
    ]

    n_fail = sum(1 for _, ok, _ in results if ok is False)
    n_pass = sum(1 for _, ok, _ in results if ok is True)
    for cid, ok, evidence in results:
        print(f"- {cid} {'PASS' if ok else 'FAIL'} — {evidence}")
    print(f"\nE-checks: {n_pass}/{len(results)} passed" + (f", {n_fail} FAIL" if n_fail else ""))
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
