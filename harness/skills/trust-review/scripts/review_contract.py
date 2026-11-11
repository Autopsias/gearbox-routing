#!/usr/bin/env python3
"""
Office Trust Layer — Trust Review · TR-01: reviewer contract + independence manifest.

This module is the REVIEWER's half of the two-context build<->review loop. The
reviewer's one job is to ENUMERATE problems against the frozen provenance record
(conventions.md §2) — it never edits the artifact, never proposes prose, never
reasons about the builder's intent. "Two contexts, not two vendors": the reviewer
runs in a fresh context (and, per TR-03, ideally a different model) so it does not
inherit the builder's blind spots.

What lives here (all pure stdlib, so it runs anywhere the harness runs):

  1. EDIT-LIST SCHEMA + VALIDATOR — the fixed shape every reviewer finding takes:
       {id, location, type, severity, evidence, suggested_repair, human_review, status}
     Severity / status come VERBATIM from conventions.md (severity enum,
     issue_lifecycle enum); a finding that omits a required field or uses an
     off-enum value is rejected so a malformed reviewer can't smuggle in a
     finding the loop can't reason about.

  2. THE ENUMERATE-ONLY DIRECTIVE — build_reviewer_prompt() assembles the exact
     text the reviewer subagent is given. It is deterministic (so it hashes
     stably) and it is fed ONLY the artifact + provenance (per the f-02 contract:
     for M1 the Workbook Doctor verdict + source packet; for M2 also the evidence
     map). It is NEVER given the builder's chain-of-thought.

  3. THE INDEPENDENCE MANIFEST (per round) — independence_round() records, for one
     reviewer pass: the model name+version, the timestamp, and the sha256 of the
     artifact, the provenance record, the source packet, and the reviewer prompt
     itself. Independence is then PROVABLE, not asserted: re-hash the inputs and
     the manifest must match; change the artifact and the artifact hash changes.
     The round entry conforms to manifest.schema.json `rounds[]`.

  4. edits_from_verdict() — the deterministic M1 enumeration: turn a Workbook
     Doctor `workbook-verdict.json` (repair plan + must-own) into edit-list
     entries. In production the reviewer subagent produces these from the artifact
     + provenance; this function is the reference enumeration the loop and the
     fixtures drive, and the worked-run reviewer.

Enums are read from references/conventions.md and used verbatim — never redefined.

Usage (library):
    from review_contract import (EDIT_FIELDS, validate_edit_list,
        build_reviewer_prompt, independence_round, edits_from_verdict)
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import sys

# ---- conventions enums (verbatim from references/conventions.md) -------------
SEVERITY = ("must-fix", "should-fix", "polish")                       # §1.2
ISSUE_LIFECYCLE = ("open", "fixed-claimed", "verified-fixed",
                   "waived-by-human", "still-open")                   # §1.5

# The edit-list entry — the reviewer's atomic output. The first seven fields are
# the TR-01 contract (id, location, type, severity, evidence, suggested-repair,
# human-review?); `status` is the issue's lifecycle state inside the loop (§1.5),
# `open` when first raised.
EDIT_FIELDS = ("id", "location", "type", "severity", "evidence",
               "suggested_repair", "human_review", "status")
EDIT_REQUIRED = ("id", "location", "type", "severity", "evidence",
                 "suggested_repair", "human_review")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha256_maybe(path) -> str | None:
    if path and os.path.isfile(path):
        return sha256_file(path)
    return None


# =============================================================================
# 1 + 2. Edit-list schema validation
# =============================================================================
def validate_edit(edit: dict) -> list[str]:
    """Return a list of problems with one edit-list entry; empty == valid."""
    problems: list[str] = []
    if not isinstance(edit, dict):
        return ["edit is not an object"]
    for f in EDIT_REQUIRED:
        if f not in edit:
            problems.append("missing required field %r" % f)
    if edit.get("severity") not in SEVERITY:
        problems.append("severity %r not in %s" % (edit.get("severity"), SEVERITY))
    status = edit.get("status", "open")
    if status not in ISSUE_LIFECYCLE:
        problems.append("status %r not in issue_lifecycle %s" % (status, ISSUE_LIFECYCLE))
    if "human_review" in edit and not isinstance(edit["human_review"], bool):
        problems.append("human_review must be boolean")
    return problems


def validate_edit_list(edits) -> tuple[bool, list[str]]:
    """Validate a whole edit list. Returns (ok, problems[])."""
    if not isinstance(edits, list):
        return False, ["edit list is not an array"]
    problems: list[str] = []
    seen_ids: set[str] = set()
    for i, e in enumerate(edits):
        for p in validate_edit(e):
            problems.append("edit[%d]: %s" % (i, p))
        eid = (e or {}).get("id") if isinstance(e, dict) else None
        if eid in seen_ids:
            problems.append("edit[%d]: duplicate id %r" % (i, eid))
        if eid:
            seen_ids.add(eid)
    return (not problems), problems


def normalize_edit(edit: dict) -> dict:
    """Return a canonical edit dict (fields in fixed order, status defaulted)."""
    out = {k: edit.get(k) for k in EDIT_FIELDS}
    if out.get("status") is None:
        out["status"] = "open"
    out["human_review"] = bool(edit.get("human_review", False))
    # a stable signature for loop bookkeeping: a finding is "the same" finding if
    # it is at the same location with the same type. (Used by the loop to match a
    # prior finding to the current scan — NOT part of the on-the-wire schema.)
    out["sig"] = "%s|%s" % (out.get("location"), out.get("type"))
    return out


# =============================================================================
# 2. The enumerate-only reviewer directive
# =============================================================================
REVIEWER_DIRECTIVE = """\
You are the TRUST-LAYER REVIEWER. You run in a fresh, independent context.

YOUR ONE JOB: enumerate problems. You are a hostile, careful skeptic.

HARD RULES (non-negotiable):
  * ENUMERATE ONLY. Do NOT edit the artifact. Do NOT rewrite text. Do NOT
    propose finished prose. You LIST findings; the builder fixes them.
  * Judge the artifact against the PROVENANCE you are given (claims -> sources),
    not against your own guess of what is true.
  * You were NOT given the builder's reasoning, and you must not ask for it.
    Independence is the point: you evaluate the OUTPUT, cold.
  * Use the controlled vocabulary verbatim: severity in {must-fix, should-fix,
    polish}; a number/claim with no supporting source is a must-fix once it
    travels; a conflict between sources is a must-fix until reconciled.

OUTPUT: a JSON array of edit-list entries, each EXACTLY:
  {"id","location","type","severity","evidence","suggested_repair","human_review"}
  - id            short stable handle, e.g. "R-01"
  - location      where it is: "Sheet!Cell" / "slide N / shape"
  - type          the defect class, e.g. "unsupported-number", "actuals-plan-mix"
  - severity      must-fix | should-fix | polish
  - evidence      what proves it is a problem (cite the provenance/source id)
  - suggested_repair   what WOULD fix it (a description, not an edit you applied)
  - human_review  true if a human must decide (conflict, waiver, judgement call)

Return ONLY the JSON array. No preamble, no rewritten artifact.
"""


def build_reviewer_prompt(provenance_summary: str,
                          artifact_label: str,
                          checklist: str | None = None,
                          surface: str | None = None,
                          fmt: str | None = None) -> str:
    """Assemble the exact (deterministic) text the reviewer subagent receives.

    Fed ONLY the artifact label + a provenance summary (and, S07+, the
    Pretty-But-Wrong checklist). NEVER the builder's chain-of-thought.

    For the DOCUMENT surface (DD-06) the reviewer CARRIES THE FORMAT: a doc is one
    of four formats with materially different extraction fidelity, mutation surface
    and locator semantics (§1.6), so the reviewer is told which one it is judging
    and is reminded that a per-format limit must never be folded into one "doc
    works" verdict (review finding #6 / DOC-PBW-07). The line is deterministic, so
    the prompt still hashes stably for the independence manifest.
    """
    parts = [REVIEWER_DIRECTIVE,
             "\n--- ARTIFACT UNDER REVIEW ---\n%s" % artifact_label]
    # Carry the SURFACE / FORMAT only when a `fmt` is supplied (the DD-06 document
    # path). The v1 excel / ppt / both calls pass no fmt, so their reviewer prompt
    # is byte-identical to before this change (additive — no v1 hash drift).
    if fmt:
        parts.append("\n--- SURFACE / FORMAT ---\nsurface: %s | format: %s\n"
                     "(Judge against THIS format's extraction + locator semantics "
                     "(§1.6); a per-format limit must be reported per format, never "
                     "folded into one 'doc works' verdict.)" % (surface or "doc", fmt))
    parts.append("\n--- PROVENANCE (claims -> sources; the f-02 record) ---\n%s"
                 % provenance_summary)
    if checklist:
        parts.append("\n--- STANDING CHECKLIST (run every item) ---\n%s" % checklist)
    return "\n".join(parts).strip() + "\n"


# =============================================================================
# 2b. TR-04 — the Pretty-But-Wrong checklist (the reviewer's standing skeptic ref)
# =============================================================================
# The standing checklist the reviewer runs EVERY pass, in addition to judging the
# artifact against its provenance. Each item is a defect class AI routinely ships
# in a file that LOOKS finished — "pretty but wrong" — tagged with a DEFAULT
# severity (verbatim from the SEVERITY enum). `surface` says where it applies:
# "excel" (workbook), "ppt" (deck/slides), or "both". The reviewer raises the
# *actual* severity per finding, but the default anchors triage and lets the M1
# benchmark (S08) score severity accuracy against a known key.
#
# Covers the ten failure modes the plan (tr-04) enumerates:
#   claims without source · numbers without date · untraceable charts ·
#   inconsistent / roll-forward formula faults · hardcodes · assumptions-as-facts ·
#   stale dates · brand drift · low-contrast / overcrowded slides · broken
#   narrative logic.
PRETTY_BUT_WRONG = (
    {"id": "PBW-01", "surface": "both", "type": "unsupported-claim",
     "default_severity": "must-fix",
     "check": "A claim / number stated as fact with NO supporting source in the "
              "provenance record. Unsupported once it travels."},
    {"id": "PBW-02", "surface": "both", "type": "undated-number",
     "default_severity": "should-fix",
     "check": "A figure with no as-of date / period label — the reader can't tell "
              "what it is as of, so it silently goes stale."},
    {"id": "PBW-03", "surface": "ppt", "type": "untraceable-chart",
     "default_severity": "must-fix",
     "check": "A chart / graphic whose underlying numbers can't be traced to a "
              "source or a workbook cell. Looks authoritative, can't be checked."},
    {"id": "PBW-04", "surface": "excel", "type": "formula-inconsistency",
     "default_severity": "must-fix",
     "check": "A formula inconsistent across a row/column, OR a roll-forward fault "
              "(a formula shape-consistent but consistently WRONG across the range "
              "— the trap a consistency check passes)."},
    {"id": "PBW-05", "surface": "excel", "type": "hardcode-in-calc-zone",
     "default_severity": "must-fix",
     "check": "A hardcoded constant where a formula/reference belongs — a magic "
              "number baked into the calc zone that no longer rolls forward."},
    {"id": "PBW-06", "surface": "both", "type": "assumption-as-fact",
     "default_severity": "must-fix",
     "check": "A load-bearing ASSUMPTION presented as established fact — no owner, "
              "no 'assumes', no flag that it is a judgement call."},
    {"id": "PBW-07", "surface": "both", "type": "stale-date",
     "default_severity": "should-fix",
     "check": "A source / figure whose as-of date is older than the freshness "
              "window for the decision — last quarter's number on this quarter's page."},
    {"id": "PBW-08", "surface": "ppt", "type": "brand-drift",
     "default_severity": "should-fix",
     "check": "Off-brand fonts / colours / logo / footer — drift from the house "
              "visual identity that signals a rushed or copy-pasted slide."},
    {"id": "PBW-09", "surface": "ppt", "type": "low-contrast-or-overcrowded",
     "default_severity": "polish",
     "check": "Low-contrast text (fails legibility) or an overcrowded slide "
              "(too many points / tiny type) — the message doesn't land."},
    {"id": "PBW-10", "surface": "both", "type": "broken-narrative-logic",
     "default_severity": "should-fix",
     "check": "The narrative doesn't follow — a headline its body doesn't support, "
              "a conclusion the data doesn't reach, a non-sequitur between slides/tabs."},
    # ---- DD-06 — the DOCUMENT-surface defect rows (conventions §1.7, verbatim) ----
    # The `both`-surface items above (unsupported-claim, undated-number,
    # assumption-as-fact, stale-date, broken-narrative-logic) ALREADY apply to a doc
    # and are not re-listed (§1.7). These are the doc-specific failure modes; severity
    # is the §1.2 enum verbatim.
    {"id": "DOC-PBW-01", "surface": "doc", "type": "extraction-loss",
     "default_severity": "must-fix",
     "check": "A number, table, or figure dropped or garbled by extraction (an unparsed "
              "PDF table, low-confidence OCR, a stripped HTML block) presented as if "
              "complete — the reader can't tell content was lost. Below-threshold PDF "
              "extraction with no OCR ⇒ blocked/not-ready + a human-review item."},
    {"id": "DOC-PBW-02", "surface": "doc", "type": "untraceable-figure-or-table",
     "default_severity": "must-fix",
     "check": "A table or embedded figure whose numbers can't be traced to a source + a "
              "doc locator — the prose analogue of the deck's untraceable chart."},
    {"id": "DOC-PBW-03", "surface": "doc", "type": "quote-or-citation-misattribution",
     "default_severity": "must-fix",
     "check": "A quotation, statistic, or citation attributed to a source that does not "
              "support it (or is fabricated)."},
    {"id": "DOC-PBW-04", "surface": "doc", "type": "contradiction-across-sections",
     "default_severity": "should-fix",
     "check": "The document asserts X in one section and not-X in another, or a figure "
              "that disagrees with its own table / appendix."},
    {"id": "DOC-PBW-05", "surface": "doc", "type": "hidden-or-agent-only-content",
     "default_severity": "should-fix",
     "check": "Collapsed/hidden/agent-only DOM (e.g. HTML <details>), tracked changes, or "
              "comments carrying claims absent from the visible render — counted "
              "separately and scoped explicitly (finding #11), never silently merged "
              "into the visible-text claim set."},
    {"id": "DOC-PBW-06", "surface": "doc", "type": "verification-contradiction-unresolved",
     "default_severity": "must-fix",
     "check": "A `contradicts` verification verdict (§2.2) that was blended/averaged away "
              "instead of preserved as a conflict."},
    {"id": "DOC-PBW-07", "surface": "doc", "type": "per-format-limit-hidden",
     "default_severity": "should-fix",
     "check": "A per-format extraction/mutation limit (§1.6) folded into a single 'doc "
              "works' verdict instead of reported per format."},
)

# every default severity must be a real conventions severity (guard against drift)
assert all(it["default_severity"] in SEVERITY for it in PRETTY_BUT_WRONG)
_PBW_SURFACES = ("both", "excel", "ppt", "doc")
assert all(it["surface"] in _PBW_SURFACES for it in PRETTY_BUT_WRONG)


def checklist_items(surface: str = "both") -> list[dict]:
    """The Pretty-But-Wrong items that apply to a surface. surface in
    {both, excel, ppt, doc}: 'excel' returns excel+both, 'ppt' returns ppt+both,
    'doc' returns doc+both (the v1 both-surface items already apply to a document,
    §1.7), 'both' (default) returns everything."""
    if surface == "both":
        return list(PRETTY_BUT_WRONG)
    return [it for it in PRETTY_BUT_WRONG if it["surface"] in ("both", surface)]


def checklist_text(surface: str = "both") -> str:
    """Render the standing checklist as the text fed to build_reviewer_prompt(
    ..., checklist=...). Deterministic, so the reviewer prompt still hashes stably."""
    items = checklist_items(surface)
    lines = ["The PRETTY-BUT-WRONG checklist — run EVERY item against the artifact. "
             "These are defects an AI ships in a file that LOOKS finished. The "
             "default severity anchors triage; raise the real severity per finding.",
             ""]
    for it in items:
        lines.append("- [%s] (%s, default %s) %s — %s"
                     % (it["id"], it["surface"], it["default_severity"],
                        it["type"], it["check"]))
    return "\n".join(lines) + "\n"


# =============================================================================
# 3. The independence manifest (per round)
# =============================================================================
# Agent `model` override short-names -> recorded name+version (mirrors
# loop_controller.MODEL_VERSIONS; kept here so independence_round is callable
# stand-alone with a short name like "sonnet").
_MODEL_VERSIONS = {
    "opus":   {"name": "Opus",   "version": "5.5"},
    "sonnet": {"name": "Sonnet", "version": "5"},
    "haiku":  {"name": "Haiku",  "version": "4.5"},
}


def _model_ref(model, role) -> dict:
    """model may be a short name ("sonnet"), a (name, version) tuple, or a
    {name, version} dict."""
    if isinstance(model, dict):
        name, version = model.get("name"), model.get("version")
    elif isinstance(model, str):
        nv = _MODEL_VERSIONS.get(model, {"name": model, "version": "?"})
        name, version = nv["name"], nv["version"]
    else:
        name, version = model
    return {"name": name, "version": version, "context": role, "independent": True}


def independence_round(round_no: int,
                       reviewer_prompt: str,
                       model,
                       *,
                       artifact_path: str | None = None,
                       provenance_path: str | None = None,
                       source_packet_path: str | None = None,
                       sources_seen: list[dict] | None = None,
                       role: str = "reviewer",
                       timestamp: str | None = None) -> dict:
    """One reviewer pass's independence record, conformant to manifest.schema.json
    `rounds[]`. The hashes make independence PROVABLE: re-hash the same inputs and
    the record reproduces; change the artifact and `input_artifact_sha256` changes.

    Required by the schema: round, model{name,version}, timestamp, inputs_seen[],
    prompt_hashes{reviewer_prompt_sha256, source_packet_sha256, input_artifact_sha256}.
    """
    ts = timestamp or _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    artifact_hash = _sha256_maybe(artifact_path) or sha256_text(artifact_path or "")
    provenance_hash = _sha256_maybe(provenance_path) or sha256_text(provenance_path or "")
    packet_hash = _sha256_maybe(source_packet_path) or sha256_text(source_packet_path or "")
    prompt_hash = sha256_text(reviewer_prompt)

    # inputs_seen: the sources the reviewer actually saw (schema wants S<n> ids).
    inputs_seen = sources_seen or [{
        "source_id": "S1", "exposure": "excerpt",
        "locator": provenance_path or artifact_label_default(),
        "sha256": provenance_hash}]

    return {
        "round": round_no,
        "role": role,
        "model": _model_ref(model, role),
        "timestamp": ts,
        "inputs_seen": inputs_seen,
        "prompt_hashes": {
            "reviewer_prompt_sha256": prompt_hash,
            "source_packet_sha256": packet_hash,
            "input_artifact_sha256": artifact_hash,
            "provenance_record_sha256": provenance_hash,
        },
        # friendly alias matching the TR-01 schema example in the plan
        "manifest": {
            "artifact_hash": artifact_hash,
            "provenance_hash": provenance_hash,
            "reviewer_prompt_hash": prompt_hash,
            "model": "%s %s" % (_model_ref(model, role)["name"],
                                _model_ref(model, role)["version"]),
            "ts": ts,
        },
    }


def artifact_label_default() -> str:
    return "(artifact)"


# =============================================================================
# 3b. TR-06 — the human-gate record (waiver semantics, conventions §4)
# =============================================================================
# §4 waiver rule, verbatim:
#   1. No artifact reaches `ready` while any must-fix is open/fixed-claimed/still-open.
#   2. Any waived must-fix caps the verdict at `ready-with-limitations` — never plain
#      `ready`. A waiver requires a NAMED OWNER, the REQUIRED DECISION, a DUE DATE, a
#      SIGNOFF status, and a written RATIONALE, all persisted in the manifest.
#   3. A Board/Regulatory travel-class waived must-fix ADDITIONALLY requires
#      named-owner signoff recorded before the verdict may be returned; without it
#      the verdict stays `not-ready`.
# The waived LIMITATION TEXT travels with the file so a downstream reader sees
# exactly what was accepted and by whom.
WAIVER_REQUIRED_FIELDS = ("owner", "decision", "due_date", "signoff", "rationale")
SIGNOFF_STATES = ("approved", "pending", "rejected")
# travel classes that require named-owner signoff on a waiver (§4.3)
SIGNOFF_REQUIRED_CLASSES = ("board", "regulatory", "board-regulatory")


def waiver_needs_signoff(travel_class) -> bool:
    """True if this travel class requires named-owner signoff on a waiver (§4.3)."""
    tc = str(travel_class or "").lower()
    return tc.startswith(("board", "regulatory")) or tc in SIGNOFF_REQUIRED_CLASSES


def validate_waiver(waiver: dict, travel_class: str = "") -> list[str]:
    """Return the problems with one waiver record; empty == a valid §4 waiver.

    Enforces §4.2 (all five fields present + non-empty) and §4.3 (Board/Regulatory
    additionally requires signoff == 'approved' by the named owner). A waiver that
    fails validation does NOT move its finding to waived-by-human — the finding
    stays blocking and the verdict stays not-ready."""
    problems: list[str] = []
    if not isinstance(waiver, dict):
        return ["waiver is not an object"]
    missing = [k for k in WAIVER_REQUIRED_FIELDS if not waiver.get(k)]
    if missing:
        problems.append("missing %s" % ",".join(missing))
    so = waiver.get("signoff")
    if so is not None and so not in SIGNOFF_STATES:
        problems.append("signoff %r not in %s" % (so, SIGNOFF_STATES))
    if waiver_needs_signoff(travel_class):
        if waiver.get("signoff") != "approved":
            problems.append("Board/Regulatory waiver requires named-owner signoff "
                            "(signoff must be 'approved'); got %r" % waiver.get("signoff"))
        if not waiver.get("owner"):
            problems.append("Board/Regulatory waiver requires a named owner")
    return problems


def limitation_text(finding_id: str, waiver: dict, finding: dict | None = None) -> str:
    """The one-line limitation that travels WITH the file (§4): what was accepted,
    by whom, and why. A downstream reader sees the risk without opening the manifest."""
    what = ""
    if finding:
        what = " — %s @ %s" % (finding.get("type", finding_id), finding.get("location", ""))
    return ("LIMITATION [%s]%s: %s. Risk accepted by %s (decision: %s; due %s; "
            "signoff: %s). Rationale: %s"
            % (finding_id, what, (waiver.get("decision") or "must-fix waived"),
               waiver.get("owner", "?"), waiver.get("decision", "?"),
               waiver.get("due_date", "?"), waiver.get("signoff", "?"),
               waiver.get("rationale", "?")))


def human_gate_record(finding_id: str, *, lifecycle: str, waiver: dict | None,
                      travel_class: str, applied: bool,
                      problems: list[str] | None = None,
                      finding: dict | None = None) -> dict:
    """One persisted human-gate decision for the manifest (`manifest.human_gate.records[]`).
    Captures owner / required decision / due date / signoff status / rationale /
    limitation text + whether it was accepted (and why not, if rejected)."""
    w = waiver or {}
    rec = {
        "finding_id": finding_id,
        "resolution": lifecycle,                 # verified-fixed | waived-by-human
        "owner": w.get("owner"),
        "required_decision": w.get("decision"),
        "due_date": w.get("due_date"),
        "signoff": w.get("signoff"),
        "rationale": w.get("rationale"),
        "travel_class": travel_class,
        "applied": bool(applied),
    }
    if lifecycle == "waived-by-human":
        rec["limitation"] = limitation_text(finding_id, w, finding)
    if problems:
        rec["rejected_reason"] = "; ".join(problems)
    return rec


# =============================================================================
# 3c. PT-02 — reviewer independence by travel class (conventions §7.2 + §7.1 cap)
# =============================================================================
# §7.2: the two-context review's independence is no longer a free-text note — it is a
#   VALUE gated by travel class (the §2.3 order internal < travels < board-regulatory):
#     cross-vendor       reviewer is a DIFFERENT VENDOR's model than the builder.
#     named-human-SME    a named human subject-matter expert performed/owns the review.
#     same-family        a different model, SAME family/vendor (the v1 Agent split).
#     same-model         the reviewer shares the builder's model (no independence).
# §7.1: a same-family / same-model reviewer triggers the `same-family-reviewer` degraded
#   cap — internal/travels => ready-with-limitations; board-regulatory => BLOCKED unless a
#   named-owner signoff (the §4 waiver shape) is recorded, then ready-with-limitations.
# A cross-vendor model or a named human SME is the strongest independence and is accepted
# at every travel class. Levels are the conventions enum, used VERBATIM.
INDEPENDENCE_LEVELS = ("cross-vendor", "named-human-SME", "same-family", "same-model")
SAME_FAMILY_LEVELS = ("same-family", "same-model")

# Known model short-names -> vendor/family. The v1 Agent split (opus/sonnet/haiku) is ALL
# one vendor + family (Anthropic / claude): a different MODEL, the SAME FAMILY — the
# `same-family` level. A reviewer from another vendor is `cross-vendor`.
MODEL_VENDORS = {
    "opus":   {"vendor": "anthropic", "family": "claude"},
    "sonnet": {"vendor": "anthropic", "family": "claude"},
    "haiku":  {"vendor": "anthropic", "family": "claude"},
}

# Registry (adapter_registry external_reviewer) provider_id -> independence level.
REVIEWER_PROVIDER_INDEPENDENCE = {
    "cross_vendor_model": "cross-vendor",
    "named_human_sme": "named-human-SME",
    "same_family_agent": "same-family",
}

# §1.4 readiness order + the §7.1 cap targets, as one rank so a cap can only LOWER a
# verdict, never raise it. 'blocked' is a cap target that maps to readiness not-ready.
_READINESS_RANK = {"not-ready": 0, "blocked": 0, "ready-with-limitations": 1, "ready": 2}


def _vendor_family(model, vendor=None, family=None):
    """(vendor, family) for a builder/reviewer model. Explicit vendor/family win; else the
    short-name table; else an unknown vendor string is its own single-member vendor+family."""
    if vendor or family:
        return (vendor or family or "unknown", family or vendor or "unknown")
    if isinstance(model, dict):
        return (model.get("vendor", "unknown"),
                model.get("family", model.get("vendor", "unknown")))
    nv = MODEL_VENDORS.get(str(model).lower())
    if nv:
        return (nv["vendor"], nv["family"])
    return (str(model or "unknown"), str(model or "unknown"))


def named_sme_ok(sme) -> bool:
    """A valid named-human-SME review record: a named owner + an 'approved' signoff
    (reuses the §4 signoff vocabulary). Anything less is not a human-independence claim."""
    if not isinstance(sme, dict):
        return False
    return bool(sme.get("owner")) and sme.get("signoff") == "approved"


def classify_reviewer_independence(builder_model, reviewer_model, *,
                                   reviewer_vendor=None, reviewer_family=None,
                                   builder_vendor=None, builder_family=None,
                                   sme=None, provider_id=None, explicit=None) -> dict:
    """Return the §7.2 reviewer-independence record:
       {level, reviewer_vendor, builder_vendor, reviewer_family, builder_family, sme, problems}.

    Resolution order: an `explicit` enum value wins; else a valid named-human-SME record
    (the human owns the review); else a registry `provider_id`
    (cross_vendor_model / named_human_sme / same_family_agent) fixes the level; else the
    level is DERIVED from the builder vs reviewer vendor/family. A provider_id of
    named_human_sme WITHOUT a valid SME record cannot claim human independence — it logs a
    problem and degrades to same-family (never overstated)."""
    bvend, bfam = _vendor_family(builder_model, builder_vendor, builder_family)
    rvend, rfam = _vendor_family(reviewer_model, reviewer_vendor, reviewer_family)
    problems: list[str] = []
    if explicit in INDEPENDENCE_LEVELS:
        level = explicit
    elif named_sme_ok(sme):
        level = "named-human-SME"
    elif provider_id == "cross_vendor_model":
        level = "cross-vendor"
    elif provider_id == "named_human_sme":
        if named_sme_ok(sme):
            level = "named-human-SME"
        else:
            problems.append("named-human-SME reviewer requires a named owner + approved signoff")
            level = "same-family"
    else:
        if str(reviewer_model).lower() == str(builder_model).lower() and not (reviewer_vendor or reviewer_family):
            level = "same-model"
        elif rvend != bvend:
            level = "cross-vendor"
        else:
            level = "same-family"
    return {"level": level, "reviewer_vendor": rvend, "builder_vendor": bvend,
            "reviewer_family": rfam, "builder_family": bfam,
            "sme": named_sme_ok(sme), "problems": problems}


def independence_acceptable(level, travel_class, *, signoff=None):
    """§7.2 gate. Returns (acceptable, cap_id_or_None, capped_at_or_None, reason).

      cross-vendor / named-human-SME => acceptable everywhere; NO cap.
      same-family / same-model:
        internal/travels  => acceptable WITH the same-family-reviewer cap (=> RWL).
        board-regulatory  => NOT acceptable unless a valid named-owner signoff (the §4
                             waiver shape) is recorded: no signoff => cap target 'blocked';
                             a valid signoff => the cap is recorded but capped at RWL."""
    if level in ("cross-vendor", "named-human-SME"):
        return (True, None, None,
                "%s reviewer — independent at every travel class (§7.2)" % level)
    if not waiver_needs_signoff(travel_class):
        return (True, "same-family-reviewer", "ready-with-limitations",
                "same-family reviewer accepted for %s; same-family-reviewer cap => "
                "ready-with-limitations (§7.1)" % (travel_class or "internal"))
    # board-regulatory: must be cross-vendor / named-SME, else a named-owner signoff
    so_problems = validate_waiver(signoff, travel_class) if signoff else ["no named-owner signoff"]
    if signoff and not so_problems:
        return (True, "same-family-reviewer", "ready-with-limitations",
                "same-family reviewer on Board/Regulatory accepted ONLY via a named-owner "
                "signoff (§7.2) — same-family-reviewer cap => ready-with-limitations")
    return (False, "same-family-reviewer", "blocked",
            "Board/Regulatory requires a cross-vendor or named-human-SME reviewer; a "
            "same-family reviewer is BLOCKED without a named-owner signoff (§7.2): %s"
            % "; ".join(so_problems))


def reviewer_independence_block(indep: dict, travel_class, *, signoff=None):
    """Build the manifest.routing.reviewer_independence block + the §7.1 degraded cap
    record (when one applies). Returns (block, cap_record_or_None)."""
    acceptable, cap, capped_at, reason = independence_acceptable(
        indep["level"], travel_class, signoff=signoff)
    block = {
        "level": indep["level"],
        "reviewer_vendor": indep["reviewer_vendor"],
        "builder_vendor": indep["builder_vendor"],
        "travel_class": travel_class,
        "acceptable": acceptable,
        "signoff_required": (waiver_needs_signoff(travel_class)
                             and indep["level"] in SAME_FAMILY_LEVELS),
        "signoff_state": (signoff or {}).get("signoff"),
        "signoff_owner": (signoff or {}).get("owner"),
        "reason": reason,
        "problems": list(indep.get("problems", [])),
    }
    cap_record = None
    if cap:
        cap_record = {"cap": cap, "condition": reason, "capped_at": capped_at,
                      "surface": "all",
                      "note": "reviewer independence by travel class (§7.1/§7.2)"}
    return block, cap_record


def apply_readiness_cap(readiness, capped_at):
    """Lower `readiness` to a §7.1 cap target if the cap is STRICTER (a cap can only lower,
    never raise). 'blocked' maps to not-ready. Returns the (possibly) lowered readiness."""
    target = "not-ready" if capped_at == "blocked" else capped_at
    if target not in _READINESS_RANK:
        return readiness
    return (readiness if _READINESS_RANK.get(readiness, 0) <= _READINESS_RANK[target]
            else target)


# =============================================================================
# 4. Deterministic M1 enumeration — verdict -> edit list
# =============================================================================
def edits_from_verdict(verdict: dict) -> list[dict]:
    """Turn a Workbook Doctor workbook-verdict.json into reviewer edit-list
    entries (the reference M1 enumeration). Every unresolved repair-plan finding
    becomes one edit, keyed to the conventions enums. human_review is set when the
    finding needs a human call (a source/conflict decision)."""
    edits: list[dict] = []
    n = 0
    for f in verdict.get("repair_plan", []):
        n += 1
        check = f.get("check", "")
        sheet = f.get("sheet", "")
        loc = f.get("locator", "")
        location = ("%s!%s" % (sheet, loc)) if sheet else loc
        # a finding needs human review if it is a sourcing / reconciliation call
        human = check in ("source_packet_link",) or f.get("severity") == "must-fix" \
            and check in ("source_packet_link",)
        # conflict / unsupported sourcing always needs a human; recompute/hardcode
        # has a mechanical repair the builder can do.
        human = check in ("source_packet_link",)
        edits.append(normalize_edit({
            "id": "R-%02d" % n,
            "location": location,
            "type": check or "finding",
            "severity": f.get("severity", "should-fix"),
            "evidence": f.get("action", "") or f.get("note", ""),
            "suggested_repair": f.get("action", ""),
            "human_review": human,
            "status": f.get("lifecycle", "open"),
        }))
    return edits


# =============================================================================
# CLI — emit the reviewer prompt and the edit list from a verdict file
# =============================================================================
def _main(argv: list[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="TR-01 reviewer contract helpers")
    ap.add_argument("--verdict", help="workbook-verdict.json (M1 provenance)")
    ap.add_argument("--print-directive", action="store_true",
                    help="print the enumerate-only reviewer directive and exit")
    args = ap.parse_args(argv)
    if args.print_directive:
        print(REVIEWER_DIRECTIVE)
        return 0
    if args.verdict:
        with open(args.verdict, encoding="utf-8") as fh:
            verdict = json.load(fh)
        edits = edits_from_verdict(verdict)
        ok, problems = validate_edit_list(edits)
        print(json.dumps({"ok": ok, "problems": problems,
                          "edits": [{k: e[k] for k in EDIT_FIELDS} for e in edits]},
                         indent=2))
        return 0 if ok else 1
    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
