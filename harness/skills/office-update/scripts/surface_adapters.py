#!/usr/bin/env python3
"""UP-03 per-surface refresh adapters — the rendered-file boundary for office-update.

Codex v3 finding #1 (HIGH): `office-update` was scoped cross-format but tested only as
a happy path. The CORE (UP-01, `office_update.py`) is deliberately surface-agnostic and
consumes an *already-normalized* claim-set. THIS module is the other half: the
per-surface adapters (UP-03 / S03) that pull a normalized claim-set OUT of a rendered
office file — Excel cells, PowerPoint shapes, the §1.6 document grammar — and hand it to
the CORE, which still owns the diff + carry-forward.

    wrap-don't-rebuild: the Trust Layer owns the DIFF; the skills RENDER.

The adapter never re-implements rendering. It declares which existing skill renders each
surface (`RENDER_SKILLS`) and only owns three things the refresh must get right and that
the happy-path test never exercised:

  1. **Preserve layout / brand / speaker-notes.** The refresh updates *claims + evidence*,
     never the template, theme, or the author's prose. A PDF is **immutable** — it stays a
     `.trust/` sidecar bound to the new run (the `sidecar-only-pdf` cap, conventions §7.1),
     never rewritten.
  2. **Detect layout / brand drift** between the prior and the refreshed artifact and
     surface it as a finding — a silent template/brand swap between periods is a real
     defect a happy-path test would miss.
  3. **Re-point or invalidate evidence / speaker-note locators that no longer resolve.**
     A carried §2.1 evidence note must NEVER be left citing an obsolete cell / slide /
     paragraph: an identity-stable (moved) claim's note is re-pointed to its current
     locator; a dropped / superseded / unresolved note is invalidated, never left dangling.

Built ON the S02 core (it imports `office_update`); it adds NO new identity or diff logic —
`office_update.refresh` / `classify_changes` / `identity_of` own that. Pure standard
library; the lib-backed file parsers (openpyxl / python-pptx / document_map) degrade
cleanly and are kept OUT of the hot path so the negative fixtures stay deterministic.

A *parsed artifact* is the adapter's surface-agnostic input shape (what a real parser, or
a fixture, produces):

    {
      "surface": "excel" | "ppt" | "doc",
      "fmt":     "xlsx"  | "pptx" | "docx" | "md" | "html" | "pdf",
      "claims":  [ { "claim_text", "locator", "sources", "verdict", "slot",
                     "materiality_class", "retrieval_timestamp", "waiver",
                     "unit", "period", "supersedes",
                     "evidence_note": { "locator": "...", "text": "..." } }, ... ],
      "layout":  { surface-specific brand/structure keys — theme, fonts, palette, master,
                   slide_count / sheet_names, named_styles, defined_names / style_names },
      "locators": [ "extra valid locators present in the artifact", ... ]  # optional
    }

CLI:
    python surface_adapters.py --selftest
"""
from __future__ import annotations

import json
import os
import sys
from typing import Dict, List, Optional, Sequence

_HERE = os.path.dirname(os.path.abspath(__file__))            # skills/office-update/scripts
sys.path.insert(0, _HERE)
import office_update as OU       # noqa: E402  (the S02 CORE — diff + carry-forward live here)

SCHEMA_VERSION = OU.SCHEMA_VERSION  # "3"

# Every (surface, format) the adapters cover, and the §1.6 / v1 locator grammar each uses.
SURFACES: Dict[str, Dict[str, str]] = {
    "xlsx": {"surface": "excel", "grammar": "tab!cell[:range]"},
    "pptx": {"surface": "ppt",   "grammar": "slide N/shape"},
    "docx": {"surface": "doc",   "grammar": "page:N|heading:'A>B>C'|para:N|table:R{r}C{c}|figure:ID"},
    "md":   {"surface": "doc",   "grammar": "heading:'A>B>C'|para:N|table:R{r}C{c}|figure:ID"},
    "html": {"surface": "doc",   "grammar": "heading:'A>B>C'|para:N|table:R{r}C{c}|figure:ID"},
    "pdf":  {"surface": "doc",   "grammar": "page:N|table:R{r}C{c}|figure:ID|annot:ID|attachment:'NAME'"},
}

# wrap-don't-rebuild: the adapter delegates RENDERING to these existing skills; it never
# re-implements them. (Recorded in the report so a downstream knows who renders.)
RENDER_SKILLS: Dict[str, List[str]] = {
    "xlsx": ["xlsx"],
    "pptx": ["pptx"],
    "docx": ["docx"],
    "md":   ["document-doctor"],
    "html": ["document-doctor"],
    "pdf":  ["pdf", "document-doctor"],   # read/sidecar only — a PDF is never mutated
}

# A PDF is immutable: its evidence can only live in a .trust sidecar bound to the new run.
IMMUTABLE_FORMATS = {"pdf"}

# Surface-specific layout/brand signature keys (the things a refresh MUST preserve, and
# whose change between periods is drift). Splitting brand-keys out lets a drift be typed.
_BRAND_KEYS = {"theme", "fonts", "palette", "logo", "master", "template"}
_STRUCTURE_KEYS = {
    "excel": ("sheet_names", "named_styles", "defined_names", "sheet_count"),
    "ppt":   ("slide_count", "layout_names", "master"),
    "doc":   ("style_names", "section_count", "heading_paths"),
}


# =============================================================================
# claim extraction (rendered artifact -> normalized claim-set for the CORE)
# =============================================================================
def claim_record(c: dict, surface: str, fmt: str) -> dict:
    """Map one extracted claim-bearing element to the CORE's normalized claim record.

    Keeps the surface + fmt + the surface-grammar locator on the record so the same claim
    text in two grammars cannot collide (the CORE folds surface/fmt into the locator hash).
    Carries the §2.1 `evidence_note` through untouched so the locator-repoint pass can act
    on it after the diff.
    """
    rec = {
        "claim_text": c.get("claim_text", ""),
        "locator": c.get("locator", ""),
        "surface": surface,
        "fmt": fmt,
        "sources": c.get("sources", []),
        "retrieval_timestamp": c.get("retrieval_timestamp", ""),
        "materiality_class": c.get("materiality_class", "numeric"),
        "unit": c.get("unit"),
        "period": c.get("period"),
        "waiver": c.get("waiver"),
        "verdict": c.get("verdict"),
        "slot": c.get("slot"),
    }
    if c.get("supersedes"):
        rec["supersedes"] = c["supersedes"]
    if c.get("evidence_note") is not None:
        rec["evidence_note"] = c["evidence_note"]
    if c.get("supersession_state"):
        rec["supersession_state"] = c["supersession_state"]
    return rec


def extract_claims(parsed: dict) -> List[dict]:
    """Extract the normalized claim-set from a parsed artifact (the UP-03 boundary)."""
    fmt = parsed.get("fmt")
    if fmt not in SURFACES:
        raise ValueError("unsupported format %r (expected one of %s)" % (fmt, sorted(SURFACES)))
    surface = parsed.get("surface") or SURFACES[fmt]["surface"]
    return [claim_record(c, surface, fmt) for c in (parsed.get("claims") or [])]


def locator_index(parsed: dict) -> set:
    """The set of locators that ACTUALLY resolve in the current rendered artifact.

    A re-pointed evidence note must land inside this set; a note that cannot is invalidated.
    """
    idx = set()
    for c in (parsed.get("claims") or []):
        loc = c.get("locator")
        if loc:
            idx.add(loc)
    for loc in (parsed.get("locators") or []):
        idx.add(loc)
    return idx


# =============================================================================
# layout / brand drift (between the prior artifact and the refreshed one)
# =============================================================================
def layout_signature(parsed: dict) -> dict:
    """A canonical signature of the layout/brand a refresh must preserve.

    Surface-aware: brand keys (theme/fonts/palette/logo/master/template) + the structure
    keys that matter for the surface. Used to detect a silent template/brand swap between
    periods.
    """
    surface = parsed.get("surface") or SURFACES.get(parsed.get("fmt", ""), {}).get("surface", "")
    lay = parsed.get("layout") or {}
    sig: Dict[str, object] = {"surface": surface, "fmt": parsed.get("fmt")}
    for k in sorted(_BRAND_KEYS):
        if k in lay:
            sig[k] = lay[k]
    for k in _STRUCTURE_KEYS.get(surface, ()):  # type: ignore[arg-type]
        if k in lay:
            v = lay[k]
            sig[k] = sorted(v) if isinstance(v, (list, set)) else v
    return sig


def detect_layout_drift(prev_sig: Optional[dict], curr_sig: dict, *,
                        surface: str, fmt: str) -> Optional[dict]:
    """Compare two layout signatures; return a finding when they diverge, else None.

    A finding follows the review_contract edit-list shape (id/location/type/severity/
    review_status/issue_lifecycle/evidence/status) so Trust Review can consume it verbatim.
    Brand-key changes are typed `brand-drift`; structural changes `layout-drift`. Drift is
    surfaced (a real weakness a reader could trip on), never silently passed.
    """
    if not prev_sig:
        return None
    keys = (set(prev_sig) | set(curr_sig)) - {"surface", "fmt"}
    changed = sorted(k for k in keys if prev_sig.get(k) != curr_sig.get(k))
    if not changed:
        return None
    is_brand = any(k in _BRAND_KEYS for k in changed)
    detail = ", ".join("%s: %r -> %r" % (k, prev_sig.get(k), curr_sig.get(k)) for k in changed)
    return {
        "id": "UP03-DRIFT-01",
        "type": "brand-drift" if is_brand else "layout-drift",
        "severity": "should-fix",
        "review_status": "needs-review",
        "issue_lifecycle": "open",
        "status": "open",
        "surface": surface,
        "format": fmt,
        "location": "artifact layout/brand signature",
        "changed": changed,
        "evidence": ("layout/brand signature changed between the prior run and this refresh "
                     "(%s) — a template/brand swap must be confirmed, not silently inherited" % detail),
        "human_review": ("Confirm the layout/brand change between periods is intended; a silent "
                         "template swap can invalidate carried evidence anchors and brand compliance."),
    }


# =============================================================================
# evidence / speaker-note locator re-point + invalidate
# =============================================================================
def _prior_claims_by_id(prev_dir: Optional[str], prior_parsed: Optional[dict]) -> Dict[str, dict]:
    """Build {claim_id -> prior claim record (carrying its evidence_note)}.

    Prefer the prior parsed artifact (it carries the §2.1 notes); else a prior
    `surface-refresh.json` sidecar; else the CORE `claim-set.json` (no notes). Read-only.
    """
    recs: List[dict] = []
    if prior_parsed:
        recs = extract_claims(prior_parsed)
    elif prev_dir:
        sr = os.path.join(prev_dir, "surface-refresh.json")
        cs = os.path.join(prev_dir, "claim-set.json")
        if os.path.exists(sr):
            with open(sr, encoding="utf-8") as fh:
                recs = (json.load(fh).get("current_claims") or [])
        elif os.path.exists(cs):
            with open(cs, encoding="utf-8") as fh:
                recs = (json.load(fh).get("claims") or [])
    out: Dict[str, dict] = {}
    for r in recs:
        out.setdefault(OU.identity_of(r).claim_id, r)
    return out


def repoint_evidence(change_set: dict, prior_claims_by_id: Dict[str, dict],
                     current_index: set) -> dict:
    """Re-point or invalidate every carried evidence / speaker-note locator.

    The hard rule (UP-03 / finding #1): after this pass, NO surviving evidence note may cite
    a locator that does not resolve in `current_index`.

      * identity-stable claim (unchanged / moved) whose CURRENT locator resolves
        ⇒ the note is RE-POINTED to that current locator.
      * a dropped / superseded / new-identity claim, OR an identity-stable claim whose
        current locator does NOT resolve ⇒ the note is INVALIDATED (the obsolete pointer is
        dropped and the evidence marked `unsupported`), never left dangling.
    """
    by_id = {c["claim_id"]: c for c in change_set.get("changes", [])}
    repointed: List[dict] = []
    invalidated: List[dict] = []
    for cid, prec in prior_claims_by_id.items():
        note = prec.get("evidence_note")
        if not note:
            continue
        old_loc = (note or {}).get("locator") or prec.get("locator")
        ch = by_id.get(cid)
        stable = bool(ch) and ch.get("classification") == "unchanged" and \
            ch.get("relation") in ("unchanged", "moved")
        if stable:
            new_loc = ch.get("locator")
            if new_loc and new_loc in current_index:
                repointed.append({"claim_id": cid, "from": old_loc, "to": new_loc,
                                  "relation": ch.get("relation")})
                continue
            invalidated.append({"claim_id": cid, "locator": old_loc, "review_status": "unsupported",
                                "reason": "identity-stable but current locator does not resolve"})
            continue
        reason = ch.get("classification") if ch else "absent-from-current"
        invalidated.append({"claim_id": cid, "locator": old_loc, "review_status": "unsupported",
                            "reason": "claim %s — obsolete evidence pointer dropped" % reason})
    # post-condition: every surviving (re-pointed) note resolves; nothing dangles.
    dangling = [r for r in repointed if r["to"] not in current_index]
    return {
        "repointed": repointed,
        "invalidated": invalidated,
        "dangling": dangling,                       # MUST be empty — asserted by the guard
        "all_resolve": not dangling,
    }


# =============================================================================
# degraded-status caps by surface (conventions §7.1)
# =============================================================================
def surface_caps(surface: str, fmt: str, *, embed_available: bool = True) -> List[dict]:
    """The §7.1 degraded-status caps a surface forces. A PDF is immutable ⇒ sidecar-only-pdf
    (caps readiness AND flags not-ready-for-forwarding — the proof does not travel inside
    the file). An unwritable embedded-evidence channel ⇒ embed-unavailable."""
    caps: List[dict] = []
    if fmt in IMMUTABLE_FORMATS:
        caps.append({
            "cap": "sidecar-only-pdf",
            "condition": ("PDF is immutable; evidence lives ONLY in the .trust sidecar bound "
                          "to this run (the file is never rewritten)"),
            "capped_at": "ready-with-limitations",
            "forwarding": "not-ready-for-forwarding",
            "surface": surface, "format": fmt,
            "note": "proof does not travel inside the file; never a silent 'survives-forward' claim",
        })
    elif not embed_available:
        caps.append({
            "cap": "embed-unavailable",
            "condition": "the surface's embedded-evidence channel could not write",
            "capped_at": "ready-with-limitations",
            "surface": surface, "format": fmt,
        })
    return caps


# =============================================================================
# top-level per-surface refresh (CORE diff + adapter overlay)
# =============================================================================
def refresh_surface(prev_dir: str, current_parsed: dict, *,
                    prior_parsed: Optional[dict] = None,
                    run_id: Optional[str] = None, now: Optional[str] = None,
                    freshness_days: Optional[int] = None, travel_class: str = "internal",
                    out_root: Optional[str] = None) -> dict:
    """Refresh ONE rendered surface: extract its claim-set, run the CORE diff (read-only on
    the prior run), then add the three UP-03 guarantees — layout/brand drift, evidence
    locator re-point/invalidate, and per-surface degraded caps. Returns
    `{"core": <office_update report>, "adapter": <surface overlay>}`.

    Writes are confined to a fresh `out_root/<run_id>/` (never the prior run). The PDF path
    is sidecar-only — the source artifact is never opened for write.
    """
    fmt = current_parsed.get("fmt")
    if fmt not in SURFACES:
        raise ValueError("unsupported format %r" % fmt)
    surface = current_parsed.get("surface") or SURFACES[fmt]["surface"]

    current_claims = extract_claims(current_parsed)
    core = OU.refresh(prev_dir, current_claims, run_id=run_id, now=now,
                      freshness_days=freshness_days, travel_class=travel_class,
                      out_root=out_root)
    run_id = core["run_id"]

    # 2. layout / brand drift (prior signature vs this refresh)
    prev_sig = layout_signature(prior_parsed) if prior_parsed else _load_prior_layout(prev_dir)
    curr_sig = layout_signature(current_parsed)
    drift = detect_layout_drift(prev_sig, curr_sig, surface=surface, fmt=fmt)

    # 3. evidence / speaker-note locator re-point + invalidate
    prior_by_id = _prior_claims_by_id(prev_dir, prior_parsed)
    cur_index = locator_index(current_parsed)
    repoint = repoint_evidence(core, prior_by_id, cur_index)

    caps = surface_caps(surface, fmt)

    adapter = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "skill": "office-update",
        "phase": "surface-refresh",
        "surface": surface,
        "format": fmt,
        "locator_grammar": SURFACES[fmt]["grammar"],
        "render_skills": RENDER_SKILLS.get(fmt, []),
        "preserves": (["immutable-source", "speaker-notes-sidecar"]
                      if fmt in IMMUTABLE_FORMATS else ["layout", "brand", "speaker-notes"]),
        "immutable_source": fmt in IMMUTABLE_FORMATS,
        "layout_signature": curr_sig,
        "prior_layout_signature": prev_sig,
        "layout_drift": drift,
        "locator_repoint": repoint,
        "degraded_status": caps,
        "current_claims": current_claims,   # so next period has the notes for re-pointing
        "core_status": core["status"],
        "core_counts": core["counts"],
    }

    if out_root and core.get("out_dir"):
        out_dir = core["out_dir"]
        _write_json(os.path.join(out_dir, "surface-refresh.json"), adapter)
        _write_text(os.path.join(out_dir, "surface-refresh.md"), render_md(adapter))
        # record the caps in THIS run's manifest (§7.1) — its own new output only.
        man_path = os.path.join(out_dir, "manifest.json")
        if caps and os.path.exists(man_path):
            with open(man_path, encoding="utf-8") as fh:
                man = json.load(fh)
            man["degraded_status"] = caps
            _write_json(man_path, man)
        adapter["out_dir"] = out_dir
    return {"core": core, "adapter": adapter}


def _load_prior_layout(prev_dir: str) -> Optional[dict]:
    """Read the prior run's stored layout signature (from its surface-refresh sidecar)."""
    p = os.path.join(prev_dir, "surface-refresh.json")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as fh:
            return (json.load(fh).get("layout_signature"))
    return None


# =============================================================================
# human surface-refresh memo
# =============================================================================
def render_md(adapter: dict) -> str:
    d = adapter.get("layout_drift")
    rp = adapter.get("locator_repoint", {})
    L = [
        "# Surface refresh — %s (%s) · %s" % (adapter["surface"], adapter["format"], adapter["run_id"]),
        "",
        "wrap-don't-rebuild: the Trust Layer owns the diff; rendering is delegated to **%s**."
        % ", ".join(adapter.get("render_skills") or ["—"]),
        "",
        "- preserves: %s" % ", ".join(adapter.get("preserves") or []),
        "- locator grammar: `%s`" % adapter.get("locator_grammar"),
        "- core status: **%s** · counts: %s" % (adapter.get("core_status"), adapter.get("core_counts")),
        "",
        "## Layout / brand drift",
        ("- **%s** (%s) — %s" % (d["type"], d["severity"], d["evidence"]) if d
         else "- none — layout/brand signature unchanged between periods"),
        "",
        "## Evidence / speaker-note locators",
        "- re-pointed: %d · invalidated (obsolete pointer dropped): %d · all resolve: %s"
        % (len(rp.get("repointed", [])), len(rp.get("invalidated", [])), rp.get("all_resolve")),
    ]
    for r in rp.get("repointed", []):
        L.append("  - `%s` re-pointed `%s` -> `%s` (%s)"
                 % (r["claim_id"], r["from"], r["to"], r.get("relation")))
    for r in rp.get("invalidated", []):
        L.append("  - `%s` invalidated (was `%s`) — %s" % (r["claim_id"], r["locator"], r["reason"]))
    if adapter.get("degraded_status"):
        L += ["", "## Degraded-status caps (§7.1)"]
        for c in adapter["degraded_status"]:
            L.append("- **%s** — caps at `%s`%s" % (c["cap"], c["capped_at"],
                     (" / `%s`" % c["forwarding"]) if c.get("forwarding") else ""))
    L.append("")
    return "\n".join(L)


# =============================================================================
# lib-backed file parsers (degrade cleanly; kept OFF the deterministic hot path)
# =============================================================================
def parse_artifact_file(path: str, fmt: str) -> dict:
    """Best-effort parse of a real rendered file into a parsed-artifact dict.

    openpyxl (xlsx) / python-pptx (pptx) / document_map (docx/pdf/md/html) when present;
    otherwise returns a minimal structure with a recorded limitation. The deterministic
    fixtures DO NOT use this — they build the parsed structure directly — so the suite never
    depends on a heavy library being installed.
    """
    if fmt == "xlsx":
        return _parse_xlsx_file(path)
    if fmt == "pptx":
        return _parse_pptx_file(path)
    return _parse_doc_file(path, fmt)


def _parse_xlsx_file(path: str) -> dict:
    parsed = {"surface": "excel", "fmt": "xlsx", "claims": [], "layout": {}, "notes": []}
    try:
        import openpyxl
        wb = openpyxl.load_workbook(path, data_only=True)
        parsed["layout"] = {"sheet_names": list(wb.sheetnames), "sheet_count": len(wb.sheetnames),
                            "defined_names": sorted(wb.defined_names.keys())
                            if hasattr(wb, "defined_names") else []}
    except Exception as e:                                       # noqa: BLE001
        parsed["notes"].append("openpyxl unavailable/failed (%s) — structure only" % e.__class__.__name__)
    return parsed


def _parse_pptx_file(path: str) -> dict:
    parsed = {"surface": "ppt", "fmt": "pptx", "claims": [], "layout": {}, "notes": []}
    try:
        from pptx import Presentation
        prs = Presentation(path)
        parsed["layout"] = {"slide_count": len(prs.slides.__iter__.__self__._sldIdLst)
                            if hasattr(prs, "slides") else len(list(prs.slides))}
    except Exception as e:                                       # noqa: BLE001
        parsed["notes"].append("python-pptx unavailable/failed (%s)" % e.__class__.__name__)
    return parsed


def _parse_doc_file(path: str, fmt: str) -> dict:
    parsed = {"surface": "doc", "fmt": fmt, "claims": [], "layout": {}, "notes": []}
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(_HERE)),
                                        "document-doctor", "scripts"))
        import document_map as DM
        m = DM.build_map(path, fmt, 4000, 0.10)
        heads = [s.get("heading_path") for s in m.get("sections", [])]
        parsed["layout"] = {"style_names": [], "section_count": len(m.get("sections", [])),
                            "heading_paths": heads}
        parsed["locators"] = [p.get("locator") for p in m.get("paragraphs", []) if p.get("locator")]
    except Exception as e:                                       # noqa: BLE001
        parsed["notes"].append("document_map unavailable/failed (%s)" % e.__class__.__name__)
    return parsed


# =============================================================================
# IO helpers
# =============================================================================
def _write_json(path: str, obj) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def _write_text(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
        if not text.endswith("\n"):
            fh.write("\n")


# =============================================================================
# self-test (smoke; the full negative battery is fixtures/fixture_surface_adapters.py)
# =============================================================================
def _selftest() -> int:
    parsed_prev = {
        "surface": "ppt", "fmt": "pptx",
        "layout": {"theme": "BrandDark", "fonts": ["PingL"], "slide_count": 8},
        "claims": [{"claim_text": "Q3 revenue = EUR 48.2M", "locator": "slide 2/title",
                    "sources": [{"source_id": "S1", "source_status": "current",
                                 "content_hash": "h1", "as_of": "2026-03-31"}],
                    "verdict": "verified", "slot": "q3-rev",
                    "evidence_note": {"locator": "slide 2/notes", "text": "from S1"}}],
    }
    sig = layout_signature(parsed_prev)
    sig2 = dict(sig); sig2["theme"] = "BrandLight"
    assert detect_layout_drift(sig, sig2, surface="ppt", fmt="pptx")["type"] == "brand-drift"
    assert detect_layout_drift(sig, sig, surface="ppt", fmt="pptx") is None
    assert surface_caps("doc", "pdf")[0]["cap"] == "sidecar-only-pdf"
    assert extract_claims(parsed_prev)[0]["surface"] == "ppt"
    print("surface_adapters selftest: PASS")
    return 0


def main(argv: Sequence[str]) -> int:
    if "--selftest" in argv:
        return _selftest()
    print("usage: surface_adapters.py --selftest", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
