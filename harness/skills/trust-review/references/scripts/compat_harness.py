#!/usr/bin/env python3
"""Backward-compatibility / migration gate (v3 / FD-03, conventions.md §7.8).

**Prove v3 can read the past before it writes the future.** Every v1/v2 `.trust/<run_id>/`
run already on disk MUST still load under the v3 schema, new v3 optional fields MUST default
safely (absent ⇒ v1/v2 behaviour, never a crash), and a v3 refresh MUST be able to consume a
prior run **read-only** (no mutation of the prior artifacts). New v3 outputs stamp a
`schema_version` + `migration_notes`.

This is pure standard library (it reuses `references/scripts/manifest_gate.py`, also stdlib).
It NEVER writes into a prior run directory; it only reads them, and stamps its result into a
fresh `out_dir` (a new `.trust/<run_id>/`).

CLI:
    python eval/compat_harness.py [--trust-root DIR] [--out DIR] [--quiet]
Exit 0 = all compat assertions hold; non-zero = a regression.
"""
from __future__ import annotations

import argparse
import copy
import datetime as _dt
import glob
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))   # references/scripts/ (shipped runtime home)
if HERE not in sys.path:
    sys.path.insert(0, HERE)                          # manifest_gate is a sibling here

import manifest_gate  # noqa: E402  (stdlib-only; reused, not reimplemented)

# v1-core required keys — what a v3 reader must resolve from EVERY committed run, however old
# or partial. This is the universal minimum present across all committed v1/v2 runs (incl. the
# partial trust-review *loop* manifests, which legitimately predate `created`/`artifact`/`gate`).
# `manifest_version`, `created`, `artifact`, and `gate` were added/standardised later, so a v3
# reader must DEFAULT them safely (see OPTIONAL_DEFAULTED) — never hard-fail an old run for
# lacking a field that did not exist when it was written.
V1_CORE_REQUIRED = ("run_id", "travel_class", "sensitivity", "routing")
OPTIONAL_DEFAULTED = ("manifest_version", "created", "artifact", "gate")
V2_MARKERS = ("verification", "adapter_bindings", "egress")
V3_MARKERS = ("degraded_status", "reviewer_independence", "human_gate_digest", "vault_writeback")


def _sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def detect_schema_version(m: dict) -> str:
    """Classify a manifest by CONTENT, not by the (unreliable / sometimes-missing) version string."""
    keys = set(m)
    if keys & set(V3_MARKERS):
        return "3"
    if keys & set(V2_MARKERS):
        return "2"
    return "1"


def _infer_reviewer_independence(m: dict) -> str:
    """Default-safe inference for the v3 reviewer_independence (§7.2) from a pre-v3 manifest.
    Never overstates independence: an unknown two-context split defaults to 'same-family',
    a local-only/no-reviewer run to 'same-model'."""
    r = m.get("routing") or {}
    if r.get("reviewer_independence") in ("cross-vendor", "named-human-SME", "same-family", "same-model"):
        return r["reviewer_independence"]
    b = (r.get("builder_model") or {}).get("name", "")
    rev = (r.get("reviewer_model") or {}).get("name", "")
    if b and rev and b.strip().lower() != rev.strip().lower():
        return "same-family"   # different model, same vendor (the v1 Agent split) — never claim cross-vendor
    if r.get("two_context"):
        return "same-family"
    return "same-model"


def v3_view(m: dict) -> dict:
    """Return a v3 *view* of any manifest with new optional fields defaulted safely.

    Pure: the input dict is never mutated (operates on a deep copy). Absent v3 fields take their
    documented v1/v2-equivalent default (conventions.md §7.8):
      degraded_status ⇒ []        (no caps recorded ⇒ v1/v2 readiness behaviour)
      reviewer_independence ⇒ inferred, never overstated
      human_gate_digest / vault_writeback ⇒ None (feature absent)
      schema_version ⇒ detected from content
    """
    v = copy.deepcopy(m)
    # pre-v3 / standardised-later fields default to their v1/v2-equivalent (never a crash):
    v.setdefault("manifest_version", "1")           # missing ⇒ treat as the earliest
    v.setdefault("created", "")                      # partial loop manifests predate it
    v.setdefault("artifact", {})                     # ditto — no artifact binding recorded
    v.setdefault("gate", None)                       # `gate` was added after v1
    # genuinely-new v3 fields:
    v.setdefault("degraded_status", [])
    v.setdefault("human_gate_digest", None)
    v.setdefault("vault_writeback", None)
    r = v.setdefault("routing", {})
    if isinstance(r, dict):
        r.setdefault("reviewer_independence", _infer_reviewer_independence(m))
    v["schema_version"] = detect_schema_version(m)
    return v


def check_one(path: str) -> dict:
    """Load + validate one committed run manifest under the v3 reader. Returns a result row."""
    res = {"path": path, "parse_ok": False, "schema_version": None, "missing_required": [],
           "gate_ran": False, "v3_view_ok": False, "no_mutation": False, "errors": []}
    with open(path, "rb") as fh:
        raw = fh.read()
    before = _sha256_bytes(raw)
    try:
        m = json.loads(raw.decode("utf-8"))
        res["parse_ok"] = True
    except Exception as exc:  # noqa: BLE001
        res["errors"].append(f"parse: {exc}")
        return res

    res["schema_version"] = detect_schema_version(m)
    res["missing_required"] = [k for k in V1_CORE_REQUIRED if not manifest_gate._nonempty(m.get(k))]
    res["defaulted"] = [k for k in OPTIONAL_DEFAULTED if k not in m]  # safely supplied by the v3 reader
    # travel_class.effective must resolve to *something* readable (may be None on an old doc run).
    res["travel_class_effective"] = (m.get("travel_class") or {}).get("effective")

    # the v3 reader must run the gate over an old manifest WITHOUT crashing (blocked is fine).
    try:
        g = manifest_gate.gate(m)
        res["gate_ran"] = True
        res["gate_status"] = g["status"]
    except Exception as exc:  # noqa: BLE001
        res["errors"].append(f"gate crashed: {exc}")

    # v3 view defaults safely + does NOT mutate the input.
    snapshot = json.dumps(m, sort_keys=True)
    try:
        v = v3_view(m)
        ok = (v.get("degraded_status") == m.get("degraded_status", [])  # absent ⇒ []
              and isinstance(v.get("degraded_status"), list)
              and v["schema_version"] in ("1", "2", "3")
              and (v.get("routing") or {}).get("reviewer_independence") in
                  ("cross-vendor", "named-human-SME", "same-family", "same-model"))
        res["v3_view_ok"] = bool(ok)
        res["v3_reviewer_independence"] = (v.get("routing") or {}).get("reviewer_independence")
        if json.dumps(m, sort_keys=True) != snapshot:
            res["errors"].append("v3_view mutated the input manifest")
            res["v3_view_ok"] = False
    except Exception as exc:  # noqa: BLE001
        res["errors"].append(f"v3_view crashed: {exc}")

    # on-disk no-mutation: reading must not have touched the file.
    with open(path, "rb") as fh:
        after = _sha256_bytes(fh.read())
    res["no_mutation"] = (before == after)
    if not res["no_mutation"]:
        res["errors"].append("manifest bytes changed during read (mutation!)")
    res["ok"] = (res["parse_ok"] and not res["missing_required"] and res["gate_ran"]
                 and res["v3_view_ok"] and res["no_mutation"] and not res["errors"])
    return res


def discover_manifests(trust_root: str) -> list:
    return sorted(glob.glob(os.path.join(trust_root, "*", "manifest.json")))


def run_compat(trust_root: str, out_dir: str | None = None) -> dict:
    """Run the full migration gate over every committed run under `trust_root`.

    Read-only over the prior runs (proven by a whole-tree byte-hash before/after); stamps a
    `migration.json` + `compat-harness.md` into `out_dir` (a fresh .trust/<run_id>/) if given.
    """
    manifests = discover_manifests(trust_root)
    # whole-tree no-mutation fingerprint (every manifest under the root), before.
    tree_before = {p: _sha256_bytes(open(p, "rb").read()) for p in manifests}

    rows = [check_one(p) for p in manifests]

    tree_after = {p: _sha256_bytes(open(p, "rb").read()) for p in manifests}
    mutated = [p for p in manifests if tree_before.get(p) != tree_after.get(p)]

    by_ver = {"1": 0, "2": 0, "3": 0}
    for r in rows:
        by_ver[r.get("schema_version", "1")] = by_ver.get(r.get("schema_version", "1"), 0) + 1

    all_ok = bool(rows) and all(r.get("ok") for r in rows) and not mutated
    # a meaningful migration gate needs at least one v1 AND one v2 run to prove the range.
    range_ok = by_ver["1"] >= 1 and by_ver["2"] >= 1

    result = {
        "trust_root": trust_root,
        "checked_at": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "schema_version": "3",
        "runs_total": len(rows),
        "by_schema_version": by_ver,
        "range_covered_v1_and_v2": range_ok,
        "prior_runs_mutated": mutated,
        "read_only_verified": not mutated,
        "all_runs_load_under_v3": all_ok,
        "rows": rows,
        "migration_notes": (
            "v3 reader loaded every committed v1/v2 run; new v3 optional fields "
            "(degraded_status, reviewer_independence, human_gate_digest, vault_writeback) "
            "defaulted safely (absent ⇒ v1/v2 behaviour); reviewer_independence inferred without "
            "overstating; a v3 refresh consumed each prior run READ-ONLY (whole-tree byte-hash "
            "unchanged). New outputs stamp schema_version=3."
        ),
        "pass": all_ok and range_ok,
    }

    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, "migration.json"), "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=2)
            fh.write("\n")
        _write_md(os.path.join(out_dir, "compat-harness.md"), result)
    return result


def _write_md(path: str, r: dict) -> None:
    lines = [
        "# Migration / backward-compatibility gate (v3 / FD-03)",
        "",
        f"- Checked: {r['checked_at']}",
        f"- Trust root: `{r['trust_root']}`",
        f"- Runs checked: **{r['runs_total']}** "
        f"(v1: {r['by_schema_version']['1']}, v2: {r['by_schema_version']['2']}, v3: {r['by_schema_version']['3']})",
        f"- Range covered (≥1 v1 **and** ≥1 v2): **{r['range_covered_v1_and_v2']}**",
        f"- Prior runs read-only (no mutation): **{r['read_only_verified']}**",
        f"- All runs load under the v3 reader: **{r['all_runs_load_under_v3']}**",
        f"- **VERDICT: {'PASS' if r['pass'] else 'FAIL'}**",
        "",
        "## Migration notes",
        "",
        r["migration_notes"],
        "",
        "## Per-run",
        "",
        "| run | schema | required-ok | gate ran | v3-view ok | no-mutation | reviewer_independence |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in r["rows"]:
        run = os.path.basename(os.path.dirname(row["path"]))
        lines.append(
            f"| `{run}` | {row.get('schema_version')} | "
            f"{not row.get('missing_required')} | {row.get('gate_ran')} | "
            f"{row.get('v3_view_ok')} | {row.get('no_mutation')} | {row.get('v3_reviewer_independence','-')} |"
        )
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def main(argv: list) -> int:
    # argparse, not a split-on-"=" dict: the documented `--trust-root DIR` form
    # used to parse as {"--trust-root": True} and crash in os.path.join.
    ap = argparse.ArgumentParser(prog="compat_harness.py")
    ap.add_argument("--trust-root", default=os.path.join(os.getcwd(), ".trust"))
    ap.add_argument("--out")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)
    r = run_compat(args.trust_root, out_dir=args.out)
    if not args.quiet:
        print(json.dumps({k: v for k, v in r.items() if k != "rows"}, indent=2))
    print(f"COMPAT GATE: {'PASS' if r['pass'] else 'FAIL'} "
          f"({r['runs_total']} runs; v1={r['by_schema_version']['1']} v2={r['by_schema_version']['2']}; "
          f"read-only={r['read_only_verified']})")
    return 0 if r["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
