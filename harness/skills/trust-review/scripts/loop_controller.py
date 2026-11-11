#!/usr/bin/env python3
"""
Office Trust Layer — Trust Review · TR-02 (SAFE convergence loop) + TR-03 (model split).

This is the controller that drives the build<->review loop. It is the plugin's
headline differentiator: AI already makes the file LOOK finished; this is the part
that makes it TRUSTWORTHY, by running a hostile reviewer in a fresh context and not
declaring victory until the artifact is verified-clean OR explicitly not-ready.

WHY THE LOOP IS "SAFE" (review-1 finding 3):
  "No new must-fix" is NOT the same as "all must-fix resolved." A repair can fix
  one finding and quietly break another. So EACH ROUND runs BOTH:
     (A) a VERIFY-PRIOR pass  — re-check every finding the builder claimed it
         fixed, against a FRESH look at the artifact. Gone -> verified-fixed;
         still there -> back to open. A claim is never trusted on its word.
     (B) a FRESH FULL pass    — re-run the entire checklist over the whole
         artifact, so a NEW defect introduced by a repair is caught.
  STOP only when there is no unresolved must-fix (open/fixed-claimed/still-open)
  AND no new must-fix was raised this round. If the round cap is reached with
  anything still unresolved, the survivors are marked `still-open` and the verdict
  is `not-ready`. The loop never converges by giving up.

A NAIVE controller would do only pass (A) — "all the findings I was tracking are
fixed, so stop." It never re-scans, so a regression the repair introduced sails
through. Each round log records BOTH what the naive rule would have concluded
(`naive_stop`) and what the SAFE rule concludes (`safe_stop`); when they diverge,
the SAFE rule is the one that caught the regression. The acceptance fixture turns
exactly that divergence into a pass/fail assertion.

TR-03 — MODEL SPLIT: builder and reviewer run on different models via the Agent
`model` override (default: builder=opus, reviewer=sonnet). "Two contexts, not two
vendors": a model reviewing its own family of output has a blind spot; a different
reviewer model blunts it. The pair is recorded in the manifest (routing block +
per-round model), so the split is auditable.

THE GATE IS A PRECONDITION (S02 carry-over): before round 1 the loop calls
references/scripts/manifest_gate.py. If sensitivity + routing + travel-class are
not recorded, the loop returns status `blocked` (NOT `failed`) and runs no model.

Enums (severity / issue_lifecycle / readiness / status) come from
references/conventions.md and are used VERBATIM.

I/O (per the command I/O contract):
  in : artifact + provenance (workbook-verdict.json | evidence-map.json),
       a manifest stub carrying travel_class + sensitivity.
  out: .trust/<run_id>/review-edits.json|md        (round 1 enumeration, TR-01)
       .trust/<run_id>/review-round-<N>.json|md     (per-round delta, TR-02)
       .trust/<run_id>/review-result.json|md        (final verdict + lifecycle)
       .trust/<run_id>/manifest.json                (routing + rounds, TR-01/TR-03)
"""
from __future__ import annotations

import argparse
import copy
import datetime as _dt
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
# scripts/ -> trust-review/: references/scripts/ is the ONE canonical adapter copy.
SKILL_ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(SKILL_ROOT, "references", "scripts"))

import review_contract as rc  # noqa: E402

# NO try/except: swallowing this ImportError skipped the S02 gate silently, which
# is how the never-ran gate hid. If it will not import, the install is broken.
import manifest_gate  # noqa: E402  (S02 precondition gate)
_HAVE_GATE = True

# ---- conventions enums (verbatim) --------------------------------------------
BLOCKING_LIFECYCLE = {"open", "fixed-claimed", "still-open"}   # §1.4 / §1.5
NONTERMINAL = {"open", "fixed-claimed"}                         # still in play
TERMINAL = {"verified-fixed", "waived-by-human", "still-open"}

# TR-03 — Agent `model` override short-names -> recorded name+version.
MODEL_VERSIONS = {
    # ponytail: alias targets as of 2026-09-22 (model-routing.yaml prices.*); re-check when an alias moves
    "opus":   {"name": "Opus",   "version": "5.5"},
    "sonnet": {"name": "Sonnet", "version": "5"},
    "haiku":  {"name": "Haiku",  "version": "4.5"},
}
DEFAULT_BUILDER_MODEL = "opus"     # default split (TR-03)
DEFAULT_REVIEWER_MODEL = "sonnet"


def _model_nv(short_or_dict):
    if isinstance(short_or_dict, dict):
        return short_or_dict
    return MODEL_VERSIONS.get(short_or_dict, {"name": short_or_dict, "version": "?"})


def _now():
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# =============================================================================
# Finding registry — one record per finding, keyed by stable signature.
# =============================================================================
class Finding:
    __slots__ = ("sig", "edit", "lifecycle", "first_round", "severity", "history")

    def __init__(self, edit: dict, round_no: int):
        self.sig = edit["sig"]
        self.edit = edit
        self.severity = edit["severity"]
        self.lifecycle = "open"
        self.first_round = round_no
        self.history = [(round_no, "open")]

    def set(self, lifecycle: str, round_no: int):
        if lifecycle != self.lifecycle:
            self.lifecycle = lifecycle
            self.history.append((round_no, lifecycle))

    def to_dict(self) -> dict:
        d = {k: self.edit.get(k) for k in rc.EDIT_FIELDS}
        d["status"] = self.lifecycle
        d["first_round"] = self.first_round
        d["history"] = [{"round": r, "lifecycle": lc} for r, lc in self.history]
        return d


# =============================================================================
# The SAFE convergence loop (TR-02)
# =============================================================================
def run_loop(*,
             reviewer,                      # fn(state, provenance, round_no) -> [edit dicts]
             builder=None,                  # fn(state, open_edits, round_no) -> (new_state, claimed_sigs)
             initial_state,                 # the artifact's reviewable state (opaque to the loop)
             provenance,                    # the f-02 provenance object (opaque to the reviewer caller)
             out_dir,
             manifest,                      # dict carrying travel_class + sensitivity (gate checks it)
             builder_model=DEFAULT_BUILDER_MODEL,
             reviewer_model=DEFAULT_REVIEWER_MODEL,
             max_rounds: int = 5,
             run_id: str | None = None,
             artifact_path: str | None = None,
             provenance_path: str | None = None,
             source_packet_path: str | None = None,
             write_files: bool = True,
             resolutions: dict | None = None,
             checklist: str | None = None,
             surface: str = "both",
             fmt: str | None = None,
             # ---- PT-02: reviewer independence by travel class (opt-in; §7.2) -------
             # When ANY of these is provided the loop ENFORCES reviewer independence by
             # travel class (records manifest.routing.reviewer_independence + the §7.1
             # same-family-reviewer cap, and gates readiness). When NONE is provided the
             # behaviour is EXACTLY v1/v2 — no independence block, no cap (backward-compat
             # §, conventions.md: "absent => exactly the v1/v2 behaviour").
             reviewer_vendor: str | None = None,
             reviewer_family: str | None = None,
             builder_vendor: str | None = None,
             builder_family: str | None = None,
             reviewer_independence: str | None = None,   # explicit level override (enum)
             reviewer_provider_id: str | None = None,     # registry-resolved external_reviewer id
             sme: dict | None = None,                     # named-human-SME review record
             independence_signoff: dict | None = None):   # §4 waiver for same-family Board/Reg
    """Drive the build<->review loop to a SAFE verdict. Returns the result dict.

    `surface` selects the Pretty-But-Wrong checklist slice (excel / ppt / doc /
    both). For the DOCUMENT surface (DD-06) pass `surface="doc"` and `fmt` (the
    f2-01 docx/pdf/md/html tag); the format is carried into the (hashed) reviewer
    prompt and recorded on every round + the result, so a per-format limit is
    never folded into one "doc works" verdict (review finding #6). The
    convergence / independence / model-split / §4-waiver / language-pass machinery
    is unchanged across surfaces."""
    run_id = run_id or (_now().replace(":", "").replace("-", "")[:13] + "_review")
    os.makedirs(out_dir, exist_ok=True)
    # TR-04 — the Pretty-But-Wrong standing checklist travels in the (hashed)
    # reviewer prompt every round. Caller may pass a custom checklist; default is
    # the conventions-keyed PRETTY_BUT_WRONG for this surface.
    checklist = checklist if checklist is not None else rc.checklist_text(surface)

    # ---- TR-03: record the model split in the routing block --------------------
    b_nv, r_nv = _model_nv(builder_model), _model_nv(reviewer_model)
    manifest = copy.deepcopy(manifest)
    manifest.setdefault("routing", {})
    manifest["routing"].update({
        "policy": manifest["routing"].get("policy") or "two-context hostile review (trust-review)",
        "two_context": True,
        "builder_model": {**b_nv, "context": "builder", "independent": True},
        "reviewer_model": {**r_nv, "context": "reviewer", "independent": True},
        "independence_note": ("builder=%s %s / reviewer=%s %s via Agent model override; "
                              "reviewer runs in a fresh context with no builder reasoning"
                              % (b_nv["name"], b_nv["version"], r_nv["name"], r_nv["version"])),
    })
    manifest.setdefault("rounds", [])

    # ---- THE GATE IS A PRECONDITION (S02) -------------------------------------
    if _HAVE_GATE:
        verdict = manifest_gate.gate(manifest)
        if verdict["status"] == "blocked":
            result = {
                "run_id": run_id, "skill": "trust-review", "phase": "review-result",
                "status": "blocked",
                "status_reason": "model-run loop gated: " + verdict.get("reason", ""),
                "readiness": "not-ready",
                "readiness_reason": "precondition not met — sensitivity/routing/travel-class "
                                    "must be recorded before any model round (S02 gate).",
                "gate": verdict, "rounds_run": 0, "findings": [], "must_own": [],
            }
            if write_files:
                _write(out_dir, "review-result.json", result)
            return result

    sources_seen = _sources_seen(manifest, provenance_path or artifact_path)
    registry: dict[str, Finding] = {}
    round_logs = []
    converged = False
    stop_round = None

    for k in range(1, max_rounds + 1):
        # ---------- FRESH FULL PASS (B): re-scan the whole artifact -------------
        scan = [rc.normalize_edit(e) for e in reviewer(initial_state, provenance, k)]
        ok, problems = rc.validate_edit_list(
            [{kk: e.get(kk) for kk in rc.EDIT_FIELDS} for e in scan])
        if not ok:
            result = {"run_id": run_id, "skill": "trust-review", "phase": "review-result",
                      "status": "failed",
                      "status_reason": "reviewer produced an invalid edit list: %s" % problems,
                      "readiness": "not-ready", "rounds_run": k, "findings": [], "must_own": []}
            if write_files:
                _write(out_dir, "review-result.json", result)
            return result
        scan_by_sig = {e["sig"]: e for e in scan}

        # ---------- VERIFY-PRIOR PASS (A): re-check claimed/known findings ------
        prior_sigs = set(registry.keys())
        verify_prior = []   # outcomes for findings already tracked
        for sig, f in registry.items():
            if f.lifecycle in TERMINAL and f.lifecycle != "still-open":
                continue
            present = sig in scan_by_sig
            if present:
                f.set("open", k)         # still there — re-open even if claimed
                verify_prior.append({"sig": sig, "id": f.edit["id"],
                                     "outcome": "still-present", "lifecycle": "open"})
            else:
                f.set("verified-fixed", k)   # confirmed gone in a fresh look
                verify_prior.append({"sig": sig, "id": f.edit["id"],
                                     "outcome": "confirmed-gone", "lifecycle": "verified-fixed"})

        # ---------- NEW findings this round (only the fresh pass finds them) ----
        new_findings = []
        for sig, e in scan_by_sig.items():
            if sig not in registry:
                registry[sig] = Finding(e, k)
                new_findings.append(registry[sig])
        new_must_fix = [f for f in new_findings if f.severity == "must-fix"]

        # ---------- the two stop rules, computed and recorded -------------------
        unresolved_must_fix = [f for f in registry.values()
                               if f.severity == "must-fix" and f.lifecycle in BLOCKING_LIFECYCLE]
        # SAFE rule (matches the mockup: no still-open must-fix AND no new must-fix)
        safe_stop = (not unresolved_must_fix) and (not new_must_fix)
        # NAIVE rule (the bug): only the verify-prior view — "everything I was
        # already tracking is resolved" — IGNORING the fresh pass's new findings.
        prior_unresolved = [registry[s] for s in prior_sigs
                            if registry[s].severity == "must-fix"
                            and registry[s].lifecycle in BLOCKING_LIFECYCLE]
        naive_stop = (not prior_unresolved)

        # ---------- independence manifest for THIS reviewer pass (TR-01/TR-03) --
        prompt = rc.build_reviewer_prompt(
            provenance_summary=_provenance_summary(provenance),
            artifact_label=(artifact_path or "artifact (in-memory state)"),
            checklist=checklist, surface=surface, fmt=fmt)
        round_entry = rc.independence_round(
            k, prompt, reviewer_model,
            artifact_path=artifact_path, provenance_path=provenance_path,
            source_packet_path=source_packet_path, sources_seen=sources_seen)
        manifest["rounds"].append(round_entry)

        rlog = {
            "round": k,
            "surface": surface,
            "format": fmt,
            "verify_prior": verify_prior,
            "fresh_full_findings": [{"id": e["id"], "location": e["location"],
                                     "type": e["type"], "severity": e["severity"],
                                     "new": (e["sig"] not in prior_sigs)} for e in scan],
            "new_findings": [{"id": f.edit["id"], "location": f.edit["location"],
                              "severity": f.severity} for f in new_findings],
            "new_must_fix": [f.edit["id"] for f in new_must_fix],
            "unresolved_must_fix": [f.edit["id"] for f in unresolved_must_fix],
            "safe_stop": safe_stop,
            "naive_stop": naive_stop,
            "regression_caught": bool(naive_stop and not safe_stop),
            "model": _model_nv(reviewer_model),
            "timestamp": round_entry["timestamp"],
        }
        round_logs.append(rlog)
        if write_files:
            _write(out_dir, "review-round-%d.json" % k, rlog)
            _write_md(out_dir, "review-round-%d.md" % k, _round_md(rlog, k))
            if k == 1:
                _write(out_dir, "review-edits.json",
                       {"run_id": run_id, "skill": "trust-review", "phase": "review-edits",
                        "manifest": round_entry["manifest"],
                        "edits": [{kk: e.get(kk) for kk in rc.EDIT_FIELDS} for e in scan]})
                _write_md(out_dir, "review-edits.md", _edits_md(scan, round_entry))

        if safe_stop:
            converged = True
            stop_round = k
            break

        # ---------- hand open findings back to the builder ----------------------
        if k == max_rounds:
            break   # cap reached; survivors handled below
        open_edits = [f.edit for f in registry.values() if f.lifecycle == "open"]
        if builder is None:
            break   # enumerate-only run (no auto-builder): single review pass
        initial_state, claimed = builder(initial_state, open_edits, k)
        for sig in (claimed or []):
            if sig in registry and registry[sig].lifecycle == "open":
                registry[sig].set("fixed-claimed", k)

    # ---- apply the human gate resolutions (waivers / verified) -----------------
    # TR-06 — the human gate. Each resolution is validated against §4 (waiver needs
    # owner/decision/due-date/signoff/rationale; Board/Regulatory needs signoff).
    # The persisted record (owner/decision/due/signoff/rationale/limitation) is
    # written into manifest.human_gate so it travels with the file.
    waiver_problems = []
    human_gate_records = []
    gate_travel = (manifest.get("travel_class") or {}).get("effective", "")
    if resolutions:
        waiver_problems, human_gate_records = _apply_resolutions(
            registry, resolutions, max_rounds, gate_travel)
    manifest["human_gate"] = {
        "travel_class": gate_travel,
        "signoff_required": rc.waiver_needs_signoff(gate_travel),
        "records": human_gate_records,
        "limitations": [r["limitation"] for r in human_gate_records
                        if r.get("applied") and r.get("limitation")],
        "problems": waiver_problems,
    }

    # ---- cap reached with anything unresolved -> survivors still-open ----------
    if not converged:
        for f in registry.values():
            if f.lifecycle in NONTERMINAL:
                f.set("still-open", max_rounds)

    # ---- readiness (conventions §1.4 / §4) -------------------------------------
    travel_class = (manifest.get("travel_class") or {}).get("effective", "")
    readiness, reason, must_own = _readiness(registry, travel_class, waiver_problems)

    # ---- PT-02: reviewer independence by travel class (§7.2 + §7.1 cap) ---------
    # OPT-IN: only when the caller supplies independence context. The reviewer is
    # resolved THROUGH the f2-04 adapter registry's `external_reviewer` operation
    # (provider_id passed in by the caller), so a non-Anthropic adapter or a named
    # human-SME gate yields cross-vendor / named-human-SME; the always-on terminal is
    # the same-family Agent split. Board/Regulatory + same-family + no signoff =>
    # BLOCKED; + named-owner signoff => ready-with-limitations (never plain ready);
    # Internal/Travels => same-family accepted (cap note). reviewer_vendor +
    # independence_level + the §7.1 cap are recorded in the manifest.
    independence_enforced = any(x is not None for x in (
        reviewer_vendor, reviewer_family, reviewer_independence, reviewer_provider_id, sme))
    independence_block = None
    independence_blocked = False
    if independence_enforced:
        indep = rc.classify_reviewer_independence(
            builder_model, reviewer_model,
            reviewer_vendor=reviewer_vendor, reviewer_family=reviewer_family,
            builder_vendor=builder_vendor, builder_family=builder_family,
            sme=sme, provider_id=reviewer_provider_id, explicit=reviewer_independence)
        independence_block, indep_cap = rc.reviewer_independence_block(
            indep, travel_class, signoff=independence_signoff)
        manifest["routing"]["reviewer_independence"] = independence_block
        manifest["routing"]["reviewer_vendor"] = indep["reviewer_vendor"]
        manifest["routing"]["independence_level"] = indep["level"]
        if indep_cap:
            manifest.setdefault("degraded_status", []).append(indep_cap)
            capped = rc.apply_readiness_cap(readiness, indep_cap["capped_at"])
            if capped != readiness:
                readiness = capped
                reason = "%s | %s" % (reason, indep_cap["condition"])
            independence_blocked = (not independence_block["acceptable"]
                                    and indep_cap["capped_at"] == "blocked")

    result = {
        "run_id": run_id, "skill": "trust-review", "phase": "review-result",
        "surface": surface, "format": fmt,
        "status": "ok" if converged else "ok-with-warnings",
        "status_reason": ("converged: verified-clean or human-gated"
                          if converged else
                          "round cap (%d) reached with unresolved findings" % max_rounds),
        "readiness": readiness,
        "readiness_reason": reason,
        "converged": converged,
        "stop_round": stop_round,
        "rounds_run": len(round_logs),
        "max_rounds": max_rounds,
        "routing": manifest["routing"],
        "findings": [f.to_dict() for f in registry.values()],
        "must_own": must_own,
        "regression_caught_in_rounds": [r["round"] for r in round_logs if r["regression_caught"]],
        "summary": {
            "must_fix_total": sum(1 for f in registry.values() if f.severity == "must-fix"),
            "must_fix_blocking": [f.edit["id"] for f in registry.values()
                                  if f.severity == "must-fix" and f.lifecycle in BLOCKING_LIFECYCLE],
            "verified_fixed": [f.edit["id"] for f in registry.values()
                               if f.lifecycle == "verified-fixed"],
            "waived": [f.edit["id"] for f in registry.values()
                       if f.lifecycle == "waived-by-human"],
            "still_open": [f.edit["id"] for f in registry.values()
                           if f.lifecycle == "still-open"],
        },
        "waiver_problems": waiver_problems,
        "human_gate": manifest["human_gate"],
        "limitations": manifest["human_gate"]["limitations"],
        "reviewer_independence": independence_block,
        "degraded_status": manifest.get("degraded_status", []),
    }
    # PT-02: a same-family reviewer on a Board/Regulatory file with no named-owner
    # signoff is BLOCKED (§7.2) — readiness is already capped to not-ready above; mark
    # the run status `blocked` (mirrors the S02 precondition-gate path) so the verdict
    # never reads as a converged-clean `ok`.
    if independence_blocked:
        result["status"] = "blocked"
        result["status_reason"] = "reviewer independence: " + independence_block["reason"]
    if write_files:
        _write(out_dir, "review-result.json", result)
        _write_md(out_dir, "review-result.md", _result_md(result))
        _write(out_dir, "manifest.json", manifest)
    return result


# =============================================================================
# Helpers
# =============================================================================
def _readiness(registry, travel_class, waiver_problems):
    blocking = [f for f in registry.values()
                if f.severity == "must-fix" and f.lifecycle in BLOCKING_LIFECYCLE]
    waived = [f for f in registry.values()
              if f.severity == "must-fix" and f.lifecycle == "waived-by-human"]
    must_own = []
    for f in registry.values():
        if f.severity == "must-fix" and f.lifecycle in BLOCKING_LIFECYCLE:
            must_own.append({"kind": "open-must-fix", "ref": f.edit["id"],
                             "what": "%s @ %s" % (f.edit["type"], f.edit["location"])})
        elif f.lifecycle == "waived-by-human":
            must_own.append({"kind": "waived", "ref": f.edit["id"],
                             "what": "%s @ %s (risk accepted by human)"
                             % (f.edit["type"], f.edit["location"])})
        elif f.edit.get("human_review"):
            must_own.append({"kind": "human-review", "ref": f.edit["id"],
                             "what": "%s @ %s needs a human decision"
                             % (f.edit["type"], f.edit["location"])})
    if waiver_problems:
        return ("not-ready",
                "waiver(s) invalid or missing signoff (%s) — finding stays blocking."
                % "; ".join(waiver_problems), must_own)
    if blocking:
        return ("not-ready",
                "%d must-fix unresolved (open/fixed-claimed/still-open) — do not let it travel."
                % len(blocking), must_own)
    if waived:
        return ("ready-with-limitations",
                "%d must-fix waived by a human (§4) — named limitations travel with the file."
                % len(waived), must_own)
    return ("ready", "every must-fix is verified-fixed; no waivers in play.", must_own)


def _apply_resolutions(registry, resolutions, round_no, gate_travel=""):
    """Move findings to verified-fixed / waived-by-human per the human gate (§4),
    and emit one persisted human-gate record per resolution.

    Returns (problems[], records[]). A waiver that fails §4 validation does NOT
    move its finding — the finding stays blocking, a problem is logged, and the
    record is persisted with applied=False + the reason. The travel class used for
    §4.3 signoff is the run's effective travel class (from the manifest); a
    resolutions-level travel_class may override only upward."""
    problems = []
    records = []
    # the effective travel class for signoff: the higher of the run's class and any
    # class the human gate declares (a waiver can't dodge signoff by claiming a
    # lower class than the file actually travels at).
    res_travel = resolutions.get("travel_class", "")
    travel = res_travel or gate_travel
    if rc.waiver_needs_signoff(gate_travel):
        travel = gate_travel  # the run's class wins if it already demands signoff
    for r in resolutions.get("resolutions", []):
        fid = r.get("finding_id")
        target = r.get("lifecycle")
        f = next((x for x in registry.values() if x.edit["id"] == fid), None)
        if f is None:
            continue
        finding = {k: f.edit.get(k) for k in rc.EDIT_FIELDS}
        if target == "verified-fixed":
            f.set("verified-fixed", round_no)
            records.append(rc.human_gate_record(
                fid, lifecycle="verified-fixed", waiver=None,
                travel_class=travel, applied=True, finding=finding))
        elif target == "waived-by-human":
            w = r.get("waiver") or {}
            wp = rc.validate_waiver(w, travel)
            if wp:
                problems.append("%s: %s" % (fid, "; ".join(wp)))
                records.append(rc.human_gate_record(
                    fid, lifecycle="waived-by-human", waiver=w,
                    travel_class=travel, applied=False, problems=wp, finding=finding))
                continue
            f.set("waived-by-human", round_no)
            records.append(rc.human_gate_record(
                fid, lifecycle="waived-by-human", waiver=w,
                travel_class=travel, applied=True, finding=finding))
    return problems, records


def _sources_seen(manifest, fallback_path):
    srcs = ((manifest.get("sensitivity") or {}).get("sources")) or []
    seen = []
    for s in srcs:
        seen.append({"source_id": s.get("source_id", "S1"),
                     "exposure": s.get("redaction", "excerpt") if s.get("redaction") in
                     ("none", "excerpt", "mask", "local-only") else "excerpt",
                     "locator": fallback_path or "(provenance)",
                     "sha256": rc.sha256_text(s.get("source_id", "S1"))})
    # schema wants exposure in {full,excerpt,masked-excerpt,redacted}
    norm = {"none": "full", "excerpt": "excerpt", "mask": "masked-excerpt", "local-only": "redacted"}
    for s in seen:
        s["exposure"] = norm.get(s["exposure"], "excerpt")
    if not seen:
        seen = [{"source_id": "S1", "exposure": "excerpt",
                 "locator": fallback_path or "(provenance)",
                 "sha256": rc.sha256_text("S1")}]
    return seen


def _provenance_summary(provenance) -> str:
    if isinstance(provenance, dict):
        rp = provenance.get("repair_plan")
        if rp is not None:
            return ("Workbook Doctor verdict: readiness=%s; %d repair-plan finding(s); "
                    "%d assumption(s)." % (provenance.get("readiness", "?"),
                    len(rp), len(provenance.get("assumptions", []))))
        return json.dumps(provenance)[:400]
    return str(provenance)[:400]


def _write(out_dir, name, obj):
    with open(os.path.join(out_dir, name), "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2)
        fh.write("\n")


def _write_md(out_dir, name, text):
    with open(os.path.join(out_dir, name), "w", encoding="utf-8") as fh:
        fh.write(text)


def _edits_md(scan, round_entry):
    lines = ["# Trust Review — edit list (enumerate-only)\n",
             "Reviewer model: **%s**  ·  prompt hash `%s`\n"
             % (round_entry["manifest"]["model"],
                round_entry["manifest"]["reviewer_prompt_hash"][:12]),
             "\n| id | location | type | severity | human-review | suggested repair |",
             "|---|---|---|---|---|---|"]
    for e in scan:
        lines.append("| %s | %s | %s | %s | %s | %s |" % (
            e["id"], e["location"], e["type"], e["severity"],
            "yes" if e["human_review"] else "no",
            (e["suggested_repair"] or "")[:60]))
    return "\n".join(lines) + "\n"


def _round_md(rlog, k):
    lines = ["# Trust Review — round %d\n" % k,
             "- verify-prior: %d finding(s) re-checked" % len(rlog["verify_prior"]),
             "- fresh-full: %d finding(s) seen, %d new" % (
                 len(rlog["fresh_full_findings"]), len(rlog["new_findings"])),
             "- new must-fix this round: %s" % (rlog["new_must_fix"] or "none"),
             "- unresolved must-fix: %s" % (rlog["unresolved_must_fix"] or "none"),
             "- **SAFE stop:** %s   ·   naive (verify-only) stop: %s"
             % (rlog["safe_stop"], rlog["naive_stop"])]
    if rlog["regression_caught"]:
        lines.append("\n> ⚠️ **Regression caught:** a naive 'no-new-must-fix / verify-only' "
                     "rule would have STOPPED here and shipped, but the fresh full pass found "
                     "a new must-fix the repair introduced. The SAFE loop continues.")
    return "\n".join(lines) + "\n"


def _result_md(result):
    s = result["summary"]
    lines = ["# Trust Review — result\n",
             "**Readiness: %s** — %s\n" % (result["readiness"].upper(), result["readiness_reason"]),
             "- status: `%s` — %s" % (result["status"], result["status_reason"]),
             "- rounds run: %d / %d  ·  converged: %s  ·  stop round: %s"
             % (result["rounds_run"], result["max_rounds"], result["converged"], result["stop_round"]),
             "- model split: builder **%s %s** / reviewer **%s %s**"
             % (result["routing"]["builder_model"]["name"], result["routing"]["builder_model"]["version"],
                result["routing"]["reviewer_model"]["name"], result["routing"]["reviewer_model"]["version"]),
             "- must-fix: %d total · %d blocking · %d verified-fixed · %d waived · %d still-open"
             % (s["must_fix_total"], len(s["must_fix_blocking"]), len(s["verified_fixed"]),
                len(s["waived"]), len(s["still_open"]))]
    if result["regression_caught_in_rounds"]:
        lines.append("- **regression(s) caught in round(s): %s**"
                     % result["regression_caught_in_rounds"])
    if result["must_own"]:
        lines.append("\n## A human must still own")
        for m in result["must_own"]:
            lines.append("- [%s] %s — %s" % (m["kind"], m["ref"], m["what"]))
    if result.get("limitations"):
        lines.append("\n## Limitations that travel with the file (§4)")
        for lim in result["limitations"]:
            lines.append("- %s" % lim)
    hg = result.get("human_gate") or {}
    if hg.get("problems"):
        lines.append("\n## Human-gate problems (waiver rejected — finding stays blocking)")
        for p in hg["problems"]:
            lines.append("- %s" % p)
    return "\n".join(lines) + "\n"


# =============================================================================
# CLI — run an enumerate-only review pass from a verdict file (the worked-run path)
# =============================================================================
def _verdict_reviewer(verdict):
    """A reviewer that enumerates from a Workbook Doctor verdict (deterministic)."""
    edits = rc.edits_from_verdict(verdict)

    def _r(state, provenance, round_no):
        return edits
    return _r


def _main(argv):
    ap = argparse.ArgumentParser(description="TR-02/TR-03 trust-review loop (enumerate-only CLI)")
    ap.add_argument("--verdict", required=True, help="workbook-verdict.json (M1 provenance)")
    ap.add_argument("--manifest", help="manifest stub (travel_class + sensitivity); else a minimal stub")
    ap.add_argument("--out", required=True)
    ap.add_argument("--run-id")
    ap.add_argument("--builder-model", default=DEFAULT_BUILDER_MODEL)
    ap.add_argument("--reviewer-model", default=DEFAULT_REVIEWER_MODEL)
    ap.add_argument("--max-rounds", type=int, default=5)
    ap.add_argument("--surface", default=None,
                    help="excel | ppt | doc | both. Default: read from the verdict "
                         "(document-verdict.json carries surface), else 'both'.")
    ap.add_argument("--format", default=None,
                    help="docx | pdf | md | html (document surface only). Default: read "
                         "from the verdict. Carried into the per-round reviewer prompt + "
                         "recorded per round (per-format reporting, DD-06 / review #6).")
    args = ap.parse_args(argv)

    with open(args.verdict, encoding="utf-8") as fh:
        verdict = json.load(fh)
    # DR-06: the document Trust Review loop must run per-format. The verdict
    # carries surface/format (DD-04 propagates them); honour them so driving the
    # CLI on a document-verdict reviews surface="doc" + fmt, not the "both"
    # default. Workbook verdicts have no surface key -> stays "both" (unchanged).
    surface = args.surface or verdict.get("surface") or "both"
    fmt = args.format or verdict.get("format")
    if args.manifest and os.path.isfile(args.manifest):
        with open(args.manifest, encoding="utf-8") as fh:
            manifest = json.load(fh)
    else:
        manifest = _minimal_manifest(verdict)

    result = run_loop(
        reviewer=_verdict_reviewer(verdict),
        builder=None,                       # enumerate-only worked run (human gate at S07)
        initial_state={"from": "verdict"},
        provenance=verdict,
        out_dir=args.out,
        manifest=manifest,
        builder_model=args.builder_model,
        reviewer_model=args.reviewer_model,
        max_rounds=args.max_rounds,
        run_id=args.run_id,
        artifact_path=args.verdict,
        provenance_path=args.verdict,
        surface=surface,
        fmt=fmt,
    )
    print(json.dumps({k: result[k] for k in ("status", "readiness", "rounds_run",
          "readiness_reason") if k in result}, indent=2))
    return 0 if result["status"] in ("ok", "ok-with-warnings") else 3


def _minimal_manifest(verdict):
    tc = (verdict.get("inputs") or {}).get("travel_class") or "Travels"
    eff = {"board": "board-regulatory", "regulatory": "board-regulatory",
           "travels": "travels", "internal": "internal"}.get(str(tc).lower(), "travels")
    return {
        "manifest_version": "1", "run_id": verdict.get("run_id", "review"),
        "created": _now(),
        "artifact": {"path": (verdict.get("inputs") or {}).get("workbook", "artifact"),
                     "sha256": "0" * 64, "kind": "workbook"},
        "travel_class": {"effective": eff},
        "sensitivity": {"run_max": "confidential",
                        "sources": [{"source_id": "S1", "sensitivity": "confidential"}]},
        "routing": {"policy": "two-context hostile review (trust-review)"},
    }


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
