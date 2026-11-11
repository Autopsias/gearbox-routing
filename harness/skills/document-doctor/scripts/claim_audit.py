#!/usr/bin/env python3
# =============================================================================
# Office Trust Layer — Document Doctor · DD-03: claim extraction + provenance audit
#
# The prose analogue of the workbook-doctor's source-packet linkage. It pulls
# every CHECKABLE assertion out of a document, decides what kind of statement
# each is, tags how material it is and whether it is externally or internally
# checkable, and resolves whether it traces to a source — emitting ONE §2
# provenance record per claim.
#
# READ BOUNDARY (review #10) — strictly bounded, never leaks:
#   * Claim EXTRACTION reads ONLY the target document (its DD-01 map) PLUS the
#     source-packet INDEX (the packet's sources + claims metadata).
#   * Provenance RESOLUTION may read approved source-packet excerpts/locators
#     (the packet IS the approved index — packet claim values + source locators).
#   * EXTERNAL / INTERNAL verification (the open web, a vault, memory) runs only
#     LATER, after sensitivity routing + the budget cap (S05/S06). This script
#     NEVER fetches anything off-document; it only resolves against the packet.
#
# Per claim (the DD-03 schema — the v1 §2 record + format + materiality + verifiability):
#   { claim, format, class, review_status, materiality, verifiability,
#     locator, supporting:[{source_id,locator,confidence,rationale}], conflict }
#   - class         : fact | assumption | estimate | interpretation | open-question
#                     (the office-source-packet epistemic split, verbatim)
#   - review_status : verified | needs-review | unsupported | conflicting  (§1.3)
#   - materiality   : the highest-governing tier from §2.3 (f2-01)
#   - verifiability : external-checkable | internal-checkable | none
#   - zero supporting ⇒ unsupported ; a packet conflict ⇒ conflicting (§2).
#
# DEDUPE near-identical claims here so the verifier (S06) inherits a deduped
# list. HOLD sub-threshold provenance links for human review (never silently
# promoted, never silently dropped).
#
# Enums (class split / review_status / materiality / status) come from
# references/conventions.md (+ office-source-packet) and are used VERBATIM.
#
# Output obeys the §3 I/O contract:  <out>/claim-audit.{json,md}
#
#   python claim_audit.py <map.json|document-file> --out .trust/<run_id> \
#       [--run-id <id>] [--source-packet packet.json] [--format auto]
# =============================================================================
import argparse
import copy
import datetime as _dt
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import document_map as dm        # noqa: E402  (DD-01 extractor + map)
import prose_landmines as pl     # noqa: E402  (shared recognisers: DATE_RX, NUM_RX, QUOTE_RX, ATTRIB_RX)

SURFACE = "doc"
SCHEMA_VERSION = "dd-claim-audit/1.0"

# Link thresholds — a match at/above LINK_THRESHOLD joins `supporting`; a match
# in [CANDIDATE_FLOOR, LINK_THRESHOLD) is HELD for human approval (a candidate),
# never silently promoted; below CANDIDATE_FLOOR is no match. Tuneable per run.
LINK_THRESHOLD = 0.60
CANDIDATE_FLOOR = 0.35

# ---- controlled vocab (verbatim) -------------------------------------------
CLASSES = ("fact", "assumption", "estimate", "interpretation", "open-question")
# §2.3 materiality tiers (f2-01). The list is the contract; the ORDER below is a
# tuneable precedence for "the highest governs" — most material first.
MATERIALITY_TIERS = ("numeric", "date", "named-entity", "regulatory", "market",
                     "assumption-as-fact", "headline")
MATERIALITY_PRECEDENCE = ["headline", "regulatory", "market", "assumption-as-fact",
                          "numeric", "date", "named-entity"]

# ---- lexical signals (deterministic, domain-agnostic) ----------------------
ESTIMATE_RX = re.compile(
    r"\b(?:forecast|projected|projection|project|expected|expect|estimat\w*|"
    r"target(?:ing|ed)?|guidance|outlook|plan(?:ned|s)?|anticipat\w*|"
    r"by\s+20\d{2}|will\s+(?:reach|grow|rise|increase|decrease|be)|"
    r"is\s+expected\s+to|we\s+(?:expect|forecast|target|plan|aim))\b", re.I)
ASSUME_RX = re.compile(
    r"\b(?:assum\w*|presum\w*|provided\s+that|on\s+the\s+basis\s+that|"
    r"baseline|hypothes\w*)\b", re.I)
INTERP_RX = re.compile(
    r"\b(?:we\s+believe|in\s+our\s+view|suggests?|indicat\w*|demonstrat\w*|"
    r"implies|reflect\w*|shows?\s+that|clearly|notably|impressive|strong(?:ly)?|"
    r"weak(?:ly)?|robust|disappointing|encouraging|remains?\s+the\s+headline|"
    r"underscores?)\b", re.I)
OPENQ_RX = re.compile(r"(?:\?\s*$|\b(?:tbd|to\s+be\s+(?:confirmed|determined)|unclear|"
                      r"unknown|pending|open\s+question)\b)", re.I)
REGULATORY_RX = re.compile(
    r"\b(?:regulat\w*|directive|statut\w*|legislation|complian\w*|GDPR|IFRS|GAAP|"
    r"SOX|MiFID|Basel|article\s+\d+|law|legal\s+(?:requirement|obligation)|"
    r"standard\s+\w+|mandate[ds]?)\b", re.I)
MARKET_RX = re.compile(
    r"\b(?:market\s+(?:share|size|demand|growth)|TAM|SAM|SOM|CAGR|competitor|"
    r"industry|sector\s+(?:growth|demand)|addressable\s+market|share\s+of\s+market)\b",
    re.I)
# a named entity: a Capitalised multiword run, or an org suffix
ENTITY_RX = re.compile(
    r"\b(?:[A-Z][a-zA-Z&.\-]+(?:\s+[A-Z][a-zA-Z&.\-]+)+"          # Proper Noun Phrase
    r"|[A-Z][a-zA-Z]+\s+(?:Inc|Ltd|PLC|plc|GmbH|S\.?A\.?|N\.?V\.?|Corp|Co|AG|SE)\b)")
HEADLINE_SECTION_RX = re.compile(
    r"\b(?:executive\s+summary|summary|highlights?|key\s+(?:takeaways?|messages?)|"
    r"conclusion|headline|at\s+a\s+glance|overview)\b", re.I)

STOPWORDS = set("the a an of to in on for and or but with at by from as is are was were "
                "be been being this that these those it its their our your his her "
                "we they i you he she them us has have had will would could should may "
                "might can do does did not no than then so up out over under into "
                "per year quarter month grew up down vs versus".split())

# A sentence is a CHECKABLE claim only if it carries one of these material signals.
SENT_SPLIT_RX = re.compile(r"(?<=[.!?])\s+(?=[A-Z“\"(\[])")


# ----------------------------------------------------------------------------
# numeric parsing (for value matching against the packet index)
# ----------------------------------------------------------------------------
_SCALE = {"k": 1e3, "m": 1e6, "mn": 1e6, "million": 1e6,
          "bn": 1e9, "b": 1e9, "billion": 1e9, "thousand": 1e3}
_NUMTOK_RX = re.compile(
    r"([€$£]?)\s?(-?\d[\d,\.]*)\s?(%|k|m|mn|bn|b|million|billion|thousand|x)?", re.I)


def _numbers(text):
    """Parse material numbers from a string -> list of dicts {value,is_pct,raw}."""
    out = []
    for m in _NUMTOK_RX.finditer(text or ""):
        cur, body, suf = m.group(1), m.group(2), (m.group(3) or "").lower()
        if not re.search(r"\d", body):
            continue
        # skip bare 4-digit years that are clearly dates (handled as date tier)
        digits = body.replace(",", "").replace(".", "")
        try:
            val = float(body.replace(",", ""))
        except ValueError:
            continue
        is_pct = suf == "%"
        if suf in _SCALE:
            val *= _SCALE[suf]
        # ignore a lone year token with no currency/percent/scale
        if not cur and not suf and re.fullmatch(r"(?:19|20)\d{2}", body):
            continue
        if len(digits) < 2 and not cur and not suf:
            continue
        out.append({"value": val, "is_pct": is_pct, "raw": m.group(0).strip()})
    return out


def _num_match(a_nums, b_nums, tol=0.01):
    """True if any number in a matches any in b within tolerance + pct agreement."""
    for a in a_nums:
        for b in b_nums:
            if a["is_pct"] != b["is_pct"]:
                continue
            hi = max(abs(a["value"]), abs(b["value"]), 1e-9)
            if abs(a["value"] - b["value"]) / hi <= tol:
                return True
    return False


def _tokens(text):
    toks = re.findall(r"[A-Za-z][A-Za-z\-]+", (text or "").lower())
    return {t for t in toks if t not in STOPWORDS and len(t) > 2}


def _jaccard(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


# ----------------------------------------------------------------------------
# claim extraction (target document ONLY — the read boundary)
# ----------------------------------------------------------------------------
def _split_sentences(text):
    text = (text or "").strip()
    if not text:
        return []
    parts = SENT_SPLIT_RX.split(text)
    return [p.strip() for p in parts if p.strip()]


def _material_signals(sent, in_headline_section):
    """Return the set of §2.3 materiality tiers a sentence carries (may be empty)."""
    tiers = set()
    nums = _numbers(sent)
    has_num = bool(nums)
    # a number that is NOT only a bare date counts as numeric
    if has_num:
        tiers.add("numeric")
    if pl.DATE_RX.search(sent):
        tiers.add("date")
    if ENTITY_RX.search(sent):
        tiers.add("named-entity")
    if REGULATORY_RX.search(sent):
        tiers.add("regulatory")
    if MARKET_RX.search(sent):
        tiers.add("market")
    if pl.QUOTE_RX.search(sent):
        tiers.add("named-entity")  # an attributed/quoted statement is entity-bearing
    if in_headline_section and (has_num or tiers):
        tiers.add("headline")
    return tiers, nums


def _classify(sent, tiers):
    """Epistemic class — the office-source-packet split, verbatim labels."""
    if OPENQ_RX.search(sent):
        return "open-question"
    if INTERP_RX.search(sent):
        return "interpretation"
    if ESTIMATE_RX.search(sent):
        return "estimate"
    if ASSUME_RX.search(sent):
        return "assumption"
    return "fact"


def _materiality(tiers, klass):
    """The single highest-governing tier (§2.3 'the highest governs')."""
    tiers = set(tiers)
    # a load-bearing assumption/estimate stated declaratively is assumption-as-fact
    if klass in ("assumption", "estimate") and ("numeric" in tiers or "market" in tiers):
        tiers.add("assumption-as-fact")
    for t in MATERIALITY_PRECEDENCE:
        if t in tiers:
            return t, sorted(tiers)
    return "named-entity", sorted(tiers) if tiers else ("numeric", [])


def _verifiability(tiers, klass):
    """external-checkable | internal-checkable | none."""
    if klass in ("interpretation", "open-question"):
        # an opinion/known-unknown is not externally settle-able by itself …
        return "none"
    if {"regulatory", "market", "named-entity"} & set(tiers):
        return "external-checkable"   # a public fact about the world
    if {"numeric", "date", "assumption-as-fact", "headline"} & set(tiers):
        return "internal-checkable"   # should trace to the packet / internal data
    return "none"


def extract_claims(doc):
    """Pull checkable claim candidates from the document's prose ONLY.

    Source = paragraphs + footnotes (+ heading-anchored context). Tables are
    structured and covered by DD-02 (table-as-image) + the structure map; we do
    not mine table cells here to keep the candidate set bounded & deterministic.
    """
    fmt = doc.get("format")
    section_paths = [s.get("heading_path", "") for s in doc.get("sections", [])]
    headline_blob = " | ".join(section_paths)
    candidates = []

    def _emit(text, base_locator, heading_path):
        in_head = bool(HEADLINE_SECTION_RX.search(heading_path or "")) or \
            bool(HEADLINE_SECTION_RX.search(headline_blob)) and not heading_path
        for sent in _split_sentences(text):
            tiers, nums = _material_signals(sent, in_head)
            if not tiers:
                continue  # not a checkable assertion — skip (bounds the set)
            klass = _classify(sent, tiers)
            mat, mat_all = _materiality(tiers, klass)
            verif = _verifiability(tiers, klass)
            loc = base_locator
            if heading_path:
                loc = "heading:'%s' | %s" % (heading_path, base_locator)
            candidates.append({
                "surface": SURFACE, "format": fmt,
                "claim": sent.strip(),
                "class": klass,
                "review_status": "needs-review",   # set during resolution
                "materiality": mat,
                "materiality_all": mat_all,
                "verifiability": verif,
                "locator": loc,
                "_nums": nums,
                "supporting": [],
                "candidates": [],
                "conflict": None,
            })

    for p in doc.get("paragraphs", []):
        _emit(p.get("text", ""), p.get("locator", "para:?"), p.get("heading_path", ""))
    for fn in doc.get("footnotes", []):
        _emit(fn.get("text", ""), fn.get("locator", "footnote:?"), "")
    return candidates


# ----------------------------------------------------------------------------
# dedupe near-identical claims (so S06 inherits a deduped list)
# ----------------------------------------------------------------------------
def _norm_key(claim):
    toks = sorted(_tokens(claim))
    return " ".join(toks)


def dedupe(claims):
    kept = []
    merged = 0
    for c in claims:
        key = _norm_key(c["claim"])
        ck_tokens = _tokens(c["claim"])
        dup_of = None
        for k in kept:
            if k["_key"] == key and key:
                dup_of = k
                break
            if _jaccard(ck_tokens, k["_tokens"]) >= 0.9 and \
                    c["materiality"] == k["materiality"]:
                dup_of = k
                break
        if dup_of is not None:
            merged += 1
            dup_of.setdefault("duplicate_locators", []).append(c["locator"])
        else:
            c["_key"] = key
            c["_tokens"] = ck_tokens
            kept.append(c)
    return kept, merged


# ----------------------------------------------------------------------------
# provenance resolution against the source-packet INDEX (approved excerpts only)
# ----------------------------------------------------------------------------
def _packet_index(packet):
    """Build the resolution index from the packet (sources + claims metadata)."""
    if not packet:
        return None
    srcs = {s.get("id"): s for s in (packet.get("sources") or [])}
    pclaims = []
    for cl in (packet.get("claims") or []):
        prov = cl.get("provenance") or {}
        label = " ".join(str(x) for x in (cl.get("metric"), prov.get("claim"),
                                          cl.get("value"), cl.get("period")) if x)
        pclaims.append({
            "id": cl.get("id"),
            "tokens": _tokens(label) | _tokens(prov.get("claim", "")),
            "nums": _numbers(str(cl.get("value", "")) + " " + str(prov.get("claim", ""))),
            "supporting": prov.get("supporting") or [],
            "conflict": prov.get("conflict"),
            "review_status": (cl.get("review_status") or "").lower(),
            "value": cl.get("value"),
            "metric": cl.get("metric"),
        })
    return {"sources": srcs, "claims": pclaims}


def _score(claim, pclaim):
    """Link confidence that this packet claim backs this document claim."""
    num_ok = _num_match(claim.get("_nums", []), pclaim["nums"])
    tok = _jaccard(claim["_tokens"], pclaim["tokens"])
    score = (0.6 if num_ok else 0.0) + 0.4 * tok
    # a strong label match with no numbers on either side can still link
    if not claim.get("_nums") and not pclaim["nums"]:
        score = max(score, tok)
    return round(min(score, 1.0), 3)


def resolve(claim, index, src_status_of):
    """Resolve one claim against the packet index. Sets supporting / conflict /
    candidates / review_status. Zero supporting ⇒ unsupported; conflict ⇒ conflicting.
    Sub-threshold matches are HELD as candidates, never promoted."""
    if index is None:
        # No packet — provenance cannot be RESOLVED (we won't fabricate either an
        # unsupported verdict or a source). Surfaced as needs-review with a note.
        claim["review_status"] = "needs-review"
        claim["provenance_note"] = "no source packet — provenance not resolved"
        return claim

    best = None
    best_score = 0.0
    for pc in index["claims"]:
        s = _score(claim, pc)
        if s > best_score:
            best, best_score = pc, s

    if best is not None and best_score >= LINK_THRESHOLD:
        # carry the packet claim's §2 supporting + conflict VERBATIM (provenance
        # resolution reads the approved packet excerpts/locators only)
        claim["supporting"] = [
            {"source_id": s.get("source_id"), "locator": s.get("locator"),
             "confidence": s.get("confidence", best_score),
             "rationale": "matched packet claim %s (link=%.2f): %s"
                          % (best["id"], best_score, (s.get("rationale") or "")[:120])}
            for s in best["supporting"]
        ]
        claim["conflict"] = copy.deepcopy(best["conflict"])
        claim["matched_packet_claim"] = best["id"]
        claim["link_confidence"] = best_score
    elif best is not None and best_score >= CANDIDATE_FLOOR:
        # HOLD for human approval — a sub-threshold link is never promoted
        claim["candidates"] = [{
            "source_id": (best["supporting"][0].get("source_id") if best["supporting"] else None),
            "locator": (best["supporting"][0].get("locator") if best["supporting"] else None),
            "confidence": best_score,
            "rationale": "sub-threshold match to packet claim %s — HELD for human approval"
                         % best["id"],
            "held_for_approval": True,
        }]
        claim["matched_packet_claim"] = best["id"]
        claim["link_confidence"] = best_score

    # derive review_status (§1.3) strictly from the resolved provenance
    if claim["conflict"]:
        claim["review_status"] = "conflicting"
    elif claim["supporting"]:
        cur = any(src_status_of(s.get("source_id")) == "current" for s in claim["supporting"])
        hi = (claim.get("link_confidence", 0) >= 0.9)
        if cur and hi and claim["class"] == "fact":
            claim["review_status"] = "verified"
        else:
            claim["review_status"] = "needs-review"
    else:
        # zero supporting against a real packet ⇒ unsupported (§2)
        claim["review_status"] = "unsupported"
    return claim


# ----------------------------------------------------------------------------
# orchestration + output (§3 I/O contract)
# ----------------------------------------------------------------------------
def load_doc(path, fmt, max_text, coverage_threshold):
    if path.lower().endswith(".json"):
        with open(path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        if "format" in doc and "paragraphs" in doc:
            return doc
    real_fmt = dm.detect_format(path, fmt)
    return dm.build_map(path, real_fmt, max_text, coverage_threshold)


def audit(doc, packet):
    """Run the full DD-03 audit over a DD-01 map + an optional packet index."""
    fmt = doc.get("format")
    # honour a DD-07 block: a scanned/below-threshold PDF has no reliable text to
    # mine — do not fabricate claims from an empty/garbled extract.
    if doc.get("status") == "blocked":
        return {
            "status": "blocked",
            "status_reason": "document-map is blocked (e.g. scanned/below-threshold "
                             "PDF with no OCR) — claims cannot be extracted from an "
                             "unreliable extract; resolve the extraction block first.",
            "format": fmt, "claims": [], "held_for_approval": [],
            "blocked_findings": doc.get("findings", []),
            "human_review": doc.get("human_review", []),
        }

    index = _packet_index(packet)
    src_status_of = (lambda sid: (index["sources"].get(sid, {}).get("source_status")
                                  if index and sid else None))

    raw = extract_claims(doc)
    deduped, merged = dedupe(raw)
    for c in deduped:
        resolve(c, index, src_status_of)

    held = []
    for c in deduped:
        # tidy internal fields, collect held candidates
        if c.get("candidates"):
            held.append({"claim": c["claim"], "locator": c["locator"],
                         "candidates": c["candidates"]})
        for k in ("_nums", "_tokens", "_key"):
            c.pop(k, None)

    by = lambda key: {v: sum(1 for c in deduped if c.get(key) == v)
                      for v in sorted({c.get(key) for c in deduped})}
    unsupported = [c for c in deduped if c["review_status"] == "unsupported"]
    conflicting = [c for c in deduped if c["review_status"] == "conflicting"]
    status = "ok"
    if unsupported or conflicting or held:
        status = "ok-with-warnings"
    return {
        "status": status,
        "status_reason": ("%d unsupported, %d conflicting, %d held for approval"
                          % (len(unsupported), len(conflicting), len(held))
                          if status != "ok" else "every claim traces or is explicitly classified"),
        "format": fmt,
        "summary": {
            "total_claims": len(deduped),
            "deduped_merged": merged,
            "by_class": by("class"),
            "by_review_status": by("review_status"),
            "by_materiality": by("materiality"),
            "by_verifiability": by("verifiability"),
            "unsupported": len(unsupported),
            "conflicting": len(conflicting),
            "held_for_approval": len(held),
        },
        "claims": deduped,
        "held_for_approval": held,
        "read_boundary": ("claims extracted from the target document + the source-packet "
                          "INDEX only; external/internal verification deferred to S05/S06"),
    }


def write_outputs(report, out_dir, input_path, run_id, packet_path):
    os.makedirs(out_dir, exist_ok=True)
    doc = {"schema": SCHEMA_VERSION, "surface": SURFACE, "run_id": run_id,
           "generated": dm._now_iso(), "input": os.path.abspath(input_path),
           "source_packet": (os.path.basename(packet_path) if packet_path else None),
           **report}
    with open(os.path.join(out_dir, "claim-audit.json"), "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, ensure_ascii=False, default=str)
    with open(os.path.join(out_dir, "claim-audit.md"), "w", encoding="utf-8") as fh:
        fh.write(_markdown(doc))
    return doc


_RS_BADGE = {"verified": "VERIFIED", "needs-review": "NEEDS-REVIEW",
             "unsupported": "UNSUPPORTED", "conflicting": "CONFLICTING"}


def _markdown(doc):
    L = ["# Claim audit — `%s` (format: **%s**, surface: doc)" %
         (os.path.basename(doc["input"]), doc.get("format")), ""]
    L.append("- **status:** `%s` — %s" % (doc["status"], doc.get("status_reason", "")))
    if doc["status"] == "blocked":
        L.append("")
        L.append("> %s" % doc.get("status_reason"))
        return "\n".join(L) + "\n"
    s = doc["summary"]
    L.append("- **claims:** %d (deduped: %d merged)  ·  **held for approval:** %d"
             % (s["total_claims"], s["deduped_merged"], s["held_for_approval"]))
    L.append("- **by review_status:** %s" % json.dumps(s["by_review_status"]))
    L.append("- **by class:** %s" % json.dumps(s["by_class"]))
    L.append("- **by materiality:** %s" % json.dumps(s["by_materiality"]))
    L.append("")
    L.append("> Read boundary: %s" % doc.get("read_boundary"))
    L.append("")
    L.append("## Claims")
    L.append("")
    L.append("| Review | Class | Materiality | Verifiability | Claim | Locator | Sources |")
    L.append("|---|---|---|---|---|---|---|")
    for c in doc["claims"]:
        srcs = ", ".join(s.get("source_id") or "?" for s in c["supporting"]) or \
            ("(held)" if c.get("candidates") else "none")
        L.append("| %s | %s | %s | %s | %s | %s | %s |" % (
            _RS_BADGE.get(c["review_status"], c["review_status"]), c["class"],
            c["materiality"], c["verifiability"], c["claim"][:70].replace("|", "\\|"),
            (c["locator"] or "")[:40], srcs))
    L.append("")
    if doc["held_for_approval"]:
        L.append("## Held for human approval (sub-threshold links — never promoted)")
        L.append("")
        for h in doc["held_for_approval"]:
            L.append("- “%s…” _[%s]_ → %s" % (h["claim"][:70], h["locator"],
                     h["candidates"][0]["rationale"]))
        L.append("")
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Document Doctor — DD-03 claim audit")
    ap.add_argument("input", help="document-map.json OR a .docx/.pdf/.md/.html file")
    ap.add_argument("--out", help=".trust/<run_id> output dir")
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--source-packet", default=None, help="source-packet.json (the INDEX)")
    ap.add_argument("--format", default="auto")
    ap.add_argument("--coverage-threshold", type=float, default=0.10)
    ap.add_argument("--max-text", type=int, default=4000)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    if not os.path.isfile(args.input):
        print(json.dumps({"status": "failed", "error": "no such file: %s" % args.input}))
        return 2
    packet = None
    if args.source_packet:
        if not os.path.isfile(args.source_packet):
            print(json.dumps({"status": "failed", "error": "no packet: %s" % args.source_packet}))
            return 2
        with open(args.source_packet, "r", encoding="utf-8") as fh:
            packet = json.load(fh)

    doc = load_doc(args.input, args.format, args.max_text, args.coverage_threshold)
    report = audit(doc, packet)

    run_id = args.run_id or (_dt.datetime.now().strftime("%Y-%m-%dT%H%M") + "_claim-audit")
    out_dir = args.out or os.path.join(os.path.dirname(os.path.abspath(args.input)),
                                       ".trust", run_id)
    out = write_outputs(report, out_dir, args.input, run_id, args.source_packet)
    if not args.quiet:
        print(json.dumps({"status": out["status"], "format": out.get("format"),
                          "summary": out.get("summary", {}), "out": out_dir}, default=str))
    # exit 0 on a clean/with-warnings run; blocked -> 3 (mirrors the gate convention)
    return 0 if out["status"] in ("ok", "ok-with-warnings") else 3


if __name__ == "__main__":
    sys.exit(main())
