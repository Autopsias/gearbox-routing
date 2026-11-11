---
name: evidence-map
description: The Office Trust Layer's cross-app evidence layer for decks — it ties every slide claim to the workbook cell and source that back it and writes the proof into the speaker notes. EM-01 is the evidence-map schema — one record per claim with supporting sources, conflicts, a 0-1 link confidence, and candidates held for approval. EM-02 builds the map and the notes — linking each claim across the workbook cell index and source packet, marking untraceable claims unsupported, and holding weak matches as candidates. EM-03 ranks evidence gaps (no source, undated, untraceable charts, assumptions as facts, conflicts) by risk and feeds Trust Review. EM-04 is the hardened linker — it matches claims to workbook outputs and HOLDS any sub-threshold or incompatible match for human approval rather than asserting false provenance. Use when the user says build the evidence map, prove the deck's numbers, or find a deck's evidence gaps. Not for building the deck argument (deck-architect) or rendering (pptx).
---

# Evidence Map (the deck's proof, before it travels)

You build the layer that makes a forwarded deck **honest**: every slide claim tied to the source(s) that back it, the proof written into the speaker notes so it travels with the file, and the gaps surfaced before anyone forwards a wrong number. This is the M2 counterpart to the M1 Excel trust path — the same trust property (no number without a source), now for slides.

**Read these first — do not redefine anything they own:**
- `../trust-review/references/conventions.md` — the contract hub. `source_status`, `review_status` (`verified` / `needs-review` / `unsupported` / `conflicting`), `severity`, `issue_lifecycle`, the **provenance record** (§2), the **speaker-note / evidence template** (§2.1), the **command I/O contract** + the `{ok, ok-with-warnings, blocked, failed}` status enum. Use these values **verbatim**.
- This skill **consumes the truth layer** — the `office-source-packet` packet (`source-packet.json`), the `deck-architect` slide map (`deck-architecture.json`, which already reserved a §2.1 note per claim and carries `source_ids` + `workbook_refs`), and the Workbook Doctor's backing model / `workbook-map.json`. It never re-asserts what they established; it links across them.

Three items, matching plan **EM-01 / EM-02 / EM-03**. `scripts/evidence_schema.py` (EM-01) is the single source of truth for the record shape; the builder and the gap review both import it.

## EM-01 — the evidence-map schema + speaker-note contract (`scripts/evidence_schema.py`)

The deck instance of the §2 provenance record. One record per claim:

```json
{
  "slide": 7,
  "claim": "Revenue is ahead of plan",
  "supporting": [
    {"source_id": "S3", "locator": "Model.xlsx#Outputs!B14", "confidence": 0.9, "rationale": "exact label + period match"}
  ],
  "candidates": [
    {"source_id": "S7", "locator": "Plan.xlsx#Plan!C12:C24", "confidence": 0.4, "rationale": "candidate; period ambiguous — held for approval"}
  ],
  "conflict": {"with": ["S7"], "note": "S3 actuals vs S7 plan mix"},
  "review_status": "conflicting",
  "tabs": ["Outputs"], "cells": ["Outputs!B14"], "source_ids": ["S3"],
  "calculation": "Outputs!B14 = (actual-plan)/plan",
  "assumptions": ["FY25 figures are current (S3, status=current)"],
  "date_range": "FY25", "owner": "FP&A", "confidence": 0.9
}
```

The discipline lives in three derived invariants `validate_record` enforces, so a record can never imply a source the links don't establish:

1. **A `supporting` link is asserted provenance — it must be at/above `LINK_THRESHOLD`.** A sub-threshold link belongs in `candidates`, **held for human approval**, never silently promoted. The cross-app linker emits candidates with confidence and holds the weak ones (review-1 finding 9).
2. **Zero supporting links == `unsupported`** (a `must-fix` once it travels, §2). Candidates don't count — "some cell holds this number" is not a source.
3. **A non-null `conflict` == `conflicting`.** `review_status` and the headline `confidence` are **derived** from the links, so they can't drift from them.

`speaker_note(rec)` renders the fixed §2.1 template — **Claim / Source IDs / Calculation / Assumptions / Needs review** — the five lines, in order, that `note_is_wellformed` checks.

## EM-02 — the builder + speaker-notes generator (`scripts/evidence_builder.py`)

The honest **cross-app linker**. For each deck claim it combines provenance from two apps:

- **Packet provenance** is carried verbatim (the human-curated S-IDs, locators, confidence, conflict).
- **Workbook cells** — it opens the backing model, builds a `label -> cell` index (`build_cell_index`), and matches each claim. Confidence is computed from label + value + period agreement (`_score_link`). At/above threshold it joins `supporting` (a claim can rest on the packet source **and** the model cell — multi-source provenance); below it is **held as a candidate**.

It refuses to launder an unsourced number: a workbook cell that is itself an **unsourced hardcode** (a literal in a column of formulas) is capped below threshold and surfaced as a candidate with that fact in its rationale — so a claim the packet flags `unsupported` stays `unsupported` even though the number appears in a cell. It also detects **cross-cell conflicts** the packet may miss (the same line in two tabs with different values).

`inject_notes` writes the §2.1 note into each slide's speaker notes (python-pptx) and saves a `-evidenced.pptx` copy — the proof now travels with the file. No python-pptx? It falls back to the `evidence-map.md` memo.

### How the session drives it

```
python scripts/evidence_builder.py \
    --architecture .trust/<run>/deck-architecture.json \
    --source-packet .trust/<run>/source-packet.json \
    --workbook model.xlsx [--workbook-verdict .trust/<run>/workbook-verdict.json] \
    --deck deck.pptx --out .trust/<run>
```

Out: `evidence-map.{json,md}` + `<deck>-evidenced.pptx`. Status `ok` / `ok-with-warnings` (a fallback fired) / `failed`. Surfacing unsupported/conflicting claims is the **job**, not a block — the gate decision belongs to EM-03 + Trust Review.

## EM-03 — the evidence-gap review (`scripts/evidence_gaps.py`)

The skeptic's pass over the populated map. It flags: **unsupported** claims, **undated** numbers, **untraceable** charts, **assumptions presented as fact**, **stale / superseded** sources (and estimates standing in for actuals), **conflicts**, and **candidate-only** claims. Each gap is emitted as a Trust-Review **edit-list finding** — the exact `{id, location, type, severity, evidence, suggested_repair, human_review, status}` shape `review_contract.validate_edit_list` accepts — with `severity`/`status` from the conventions enums and `status` starting at `open`. Gaps are **ranked by risk** (severity weight × scope) and handed to the M2 hostile-review loop.

```
python scripts/evidence_gaps.py --evidence-map .trust/<run>/evidence-map.json --out .trust/<run>
```

## EM-04 — the hardened cross-app linker (`scripts/cross_app_linker.py`)

The signature move — auto-connect a vetted workbook to its deck — made **safe**. review-1 finding 9: semantic matching can manufacture *convincing but false* provenance, and a wrong link makes a deck **more** dangerous, not less. So this linker never asserts a link it cannot stand behind, and it shows its work. It consumes a **workbook outputs index** (a Workbook Doctor `workbook-map.json` carrying `outputs[]`, or the EM-02 cell index adapted, or a normalized `{outputs:[{label,cell,named,value,unit,period,is_hardcode}]}`) and the **deck claims** (the `deck-architecture.json` slide map).

What "hardened" adds over EM-02's linker:

1. **Itemised confidence** — every candidate carries a 0–1 score with a full breakdown (`label · value · unit · period · anchor`), not a black-box number.
2. **Multiple candidates, ranked** — a claim that plausibly matches several outputs keeps them all, each with its own rationale; it never silently collapses to one guess.
3. **Source / date / unit compatibility checks** — a match is checked on **unit** (€M vs % vs headcount) and **period/date** (FY25 vs FY24), not just label+value. An **incompatible unit or period is a hard cap below the assert threshold** — a convincing same-label/same-number match with the wrong unit or year is **held**, never asserted. This is the specific guard against "same label, same number, wrong thing."
4. **Deterministic anchors preferred; hardcodes capped** — a named range / exact single-cell locator scores above a fuzzy label match; an unsourced workbook hardcode is capped below threshold. A zero-label-overlap cell is **never** recorded — the linker does not guess.
5. **Never auto-accept below threshold** — at/above `ASSERT_THRESHOLD` (= EM-01 `LINK_THRESHOLD`) a link is asserted `supporting`; below it the link is **held** in `held_for_approval` for a human to confirm; below `CANDIDATE_FLOOR` it is dropped.

```
python scripts/cross_app_linker.py --workbook-map workbook-map.json \
    --architecture deck-architecture.json [--source-packet source-packet.json] --out .trust/<run>
```

Out: `evidence-map.{json,md}` with `records[]` (each carrying `supporting` / `candidates` / `held_for_approval` + the per-link compatibility breakdown) and a `gate` (`no_silent_link_below_threshold`, `every_link_has_compatibility_breakdown`, asserted/held counts). The records reuse the EM-01 schema, so EM-03 and DA-04 consume them unchanged.

## DD-05 — the document surface (per-format output channel)

The evidence layer **generalises to documents** (`scripts/doc_evidence.py`), so a forwarded prose document carries its own proof exactly as a deck does — reusing this skill's machinery verbatim, adding only a `doc` branch (the v1 deck path is untouched).

- **The record.** A doc record IS the EM-01/§2 record + the §2.1 note, extended with the f2-01 `format` and the §1.6 document `locator` (`page:N | heading:'A>B>C' | para:N | table:R{r}C{c} | figure:ID`). It is built straight from the **Document Doctor claim audit** (`claim-audit.json`, DD-03), which already resolved each claim against the source-packet **index** with the HOLD discipline; `doc_evidence.py` carries that into `evidence_schema.evidence_record` and **re-validates the §2 invariants** (a supporting link clears `LINK_THRESHOLD`; zero supporting ⇒ `unsupported`; a non-null conflict ⇒ `conflicting`). EM-04's HOLD semantics are reused — a sub-threshold / unresolved link is **held for approval**, never asserted.
- **The output channel — BY FORMAT** (review finding #6; the dd-08 spike PASSED): a `.trust/<run>/` **sidecar** (`evidence-map.{json,md}` + `evidence-gaps.{json,md}`) is **always** written, then — **docx** ⇒ anchored Word comments on each claim's run **+** an Evidence appendix in a `-evidenced.docx` copy (fall back to appendix + sidecar if comments are unavailable); **pdf** ⇒ **capability-gated, viewer-proven** (AB-02): a plain readable PDF gets an `-evidenced.pdf` via the **`embedded-pdf`** channel — an appended provenance appendix (claim → source ID → verdict) + named-destination anchors + the sidecar attached as an embedded file, the author's pages byte-faithful (append-only); a signed / form / encrypted / unreadable / no-writer PDF is withheld ⇒ **sidecar** + the `sidecar-only-pdf` cap + `not-ready-for-forwarding`, never a silent survives-the-forward; **md** ⇒ sidecar + an appended provenance block in a `-evidenced.md` copy; **html** ⇒ sidecar + an appended `<section>` in a `-evidenced.html` copy. **The author's prose is never silently rewritten** — only comments and an *appended* section are added.
- **The gap review.** EM-03 (`evidence_gaps.review_gaps`) is run **verbatim** over the doc map; its `review_contract`-valid edit list (with doc-aware `location`s) feeds Trust Review (DD-06).

```
python scripts/doc_evidence.py --claim-audit .trust/<run>/claim-audit.json \
    [--source-packet source-packet.json] [--document board.docx] \
    [--verdict document-verdict.json] --out .trust/<run>
```

Proven in `fixtures/fixture_doc_evidence_map.py`: every doc claim is traced, conflicted, or explicitly unsupported — none silently sourced; a held link is never promoted; the §2.1 note round-trips; and each format's output channel behaves (docx anchored comments + appendix, pdf embedded-pdf when embeddable / sidecar-gated (sidecar-only-pdf + not-ready-for-forwarding) otherwise, md/html appended-section, the author's prose intact).

## The acceptance property

A fixture deck + workbook yields a map where **every claim is traced, conflicted, or explicitly unsupported — none silently sourced.** The unsupported claim appears flagged with zero sources (not dropped, not given a fake link); a hardcoded cell that holds the number is held as a candidate, not asserted; the gap review ranks the unsupported claim as the top must-fix and hands a valid edit list to Trust Review. Proven by `fixtures/fixture_evidence_map.py`.

For **EM-04**, the hardened linker's acceptance property is sharper still: a **low-confidence match is held for human approval, not silently linked.** A workbook output with the *same label and the same number* as a claim but an **incompatible unit (€M vs %)** or an **incompatible period (FY25 vs FY24)** is held in `held_for_approval` — never asserted as a supporting source — and a claim whose only cell match is such a trap stays `unsupported`. Proven by `fixtures/fixture_render_validate.py`.
