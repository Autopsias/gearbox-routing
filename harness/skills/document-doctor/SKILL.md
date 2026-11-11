---
name: document-doctor
description: The Office Trust Layer's prose-document inspector, run before anyone trusts an inherited or AI-written document (.docx / .pdf / .md / .html). It maps the document into ordered sections, paragraphs, tables, figures and footnotes with per-format locators, stamps every record with its format, and reports extraction quality per format, not one blended "doc works" verdict. For PDFs it measures text coverage, image-only pages, table-extraction confidence and OCR availability — a low-coverage scan with no OCR is blocked, not silently passed. For HTML it runs visible-text / semantic-DOM / raw modes and scopes hidden, collapsed and agent-only DOM separately, reporting rendered-vs-source divergence. It then scans prose for landmines — undated numbers, stale dates, tables shipped as images, broken cross-references, unsourced numbers, unattributed quotes. Use for "check this document", "is this report safe to send", or "audit this PDF/Word/HTML file". Not for spreadsheets (workbook-doctor) or decks (deck-architect).
---

# Document Doctor (the document trust path)

You are inspecting a **prose document before anyone builds on it or sends it onward**. The model can already make a report read as finished; it cannot tell you whether the numbers are dated, where the quotes came from, or whether a table survived extraction. This skill is the **prose analogue of the Workbook Doctor**: map the structure, measure what extraction actually recovered, and hunt the quiet prose failures a reader can't see — so the document's risks are visible *before* it travels.

This is a v2 **read path**. It is **inspect-only** — it never edits the document. A document is **not one thing**: it is one of four `format`s (`docx`/`pdf`/`md`/`html`) with materially different extraction fidelity, mutation surface and locator semantics, so **every record carries `surface=doc` plus its `format`**, and limits are reported **per format**, never averaged into one verdict (review finding #6).

It runs in three phases plus an early capability spike:

- **DD-01** — extractor + structure map → `document-map.{json,md}`
- **DD-07** — PDF extraction-quality metrics (coverage / image-only / table-confidence / OCR / locator-fidelity), folded into the same map
- **DD-02** — prose landmine detectors → `prose-landmines.{json,md}`
- **dd-08** — the anchored-Word-comment round-trip spike that *gates* the S04 mutation path (proven this session)

**Read these first — do not redefine anything they own:**

- `../trust-review/references/conventions.md` — the contract hub. Use **verbatim**: `severity` (`must-fix`/`should-fix`/`polish`), `review_status` (`verified`/`needs-review`/`unsupported`/`conflicting`), `issue_lifecycle` (`open`/…), `readiness` (`not-ready`/`ready-with-limitations`/`ready`), the `{ok, ok-with-warnings, blocked, failed}` status enum, the **command I/O contract** (§3), the **`surface`/`format`** vocabulary and the **document locator grammar** (§1.6), and the **doc Pretty-But-Wrong taxonomy** (§1.7, `DOC-PBW-01…07`). Findings carry the source-ID scheme `S<n>` when traced to a packet.
- `../trust-review/references/capability-inventory.md` — the proven reader/writer contracts and their **f2-02 fallback chains**: `docx_read` (python-docx → markitdown → raw-XML), `pdf_read` (pdf-text → markitdown → OCR), `html_read` (semantic-DOM → visible-text → raw), `md_read` (parser → plain-text), and `docx_comment_write` (anchored comments → appendix + sidecar). The graceful-degradation behaviour for each absent backend lives there.
- `../trust-review/references/sensitivity-routing-policy.md` — the document is opened, mapped and hashed **locally**; a model never needs the whole file. Honour the run's travel class / sensitivity.

The deterministic engineering lives in two scripts; this SKILL.md tells you how to run them and how to read what they return.

```
skills/document-doctor/scripts/
  document_map.py     # DD-01 extractor + structure map  ·  DD-07 PDF quality/OCR
  prose_landmines.py  # DD-02 prose landmine detectors
```

---

## Inputs (declared up front, per the I/O contract)
- `document` — path to the `.docx` / `.pdf` / `.md` / `.html` (required).
- `format` — `auto|docx|pdf|md|html`. `auto` infers from the extension. The **`format` is REQUIRED on every record and is never inferred at read time** (§1.6) — it is fixed once, here, and stamped everywhere downstream.
- `source_packet` — optional `source-packet.json` from `office-source-packet`. When present, numbers and quotes can be traced to claims + `S<n>` (the linkage itself lands with the document evidence map in a later session; DD-02 surfaces the candidates).
- `run_id` — `<ISO-timestamp>_<short-slug>`. Auto-generated if absent; outputs go to `.trust/<run_id>/` next to the document.

---

## Phase 1 — Extractor + structure map (DD-01) → `.trust/<run_id>/document-map.{json,md}`

Run the per-type extractor adapter (inspect-only):

```bash
python skills/document-doctor/scripts/document_map.py <document> \
    --out .trust/<run_id> [--format auto|docx|pdf|md|html] \
    [--source-packet <packet.json>] --run-id <run_id> \
    [--coverage-threshold 0.10] [--assume-no-ocr]
```

It selects the adapter for the `format` and follows that format's **f2-02 fallback chain**, recording the chosen binding in `adapter` (e.g. `docx_read=python-docx@1.2.0`, `fallback:false`) so the manifest can see which path ran. It then emits, each stamped `surface=doc` + `format` and each with a **§1.6 locator**:

| Record | What it captures | Locator grammar |
|---|---|---|
| `sections` | ordered headings with the full **heading path** (`A>B>C`) | `heading:'A>B>C'` |
| `paragraphs` | body paragraphs in document order | `para:N` (docx/md/html) · `page:N` (pdf) |
| `tables` | tables with a **cell grid** (`{r,c,text,locator}`) where the format allows | `table:T{n}` / `table:T{n}R{r}C{c}` |
| `figures` | images, embedded SVGs, charts | `figure:ID` |
| `footnotes` | foot/endnotes | `footnote:ID` |

**HTML modes + hidden / agent-only scoping (review finding #11).** For `html`, the reader runs **three modes** — `semantic_dom` (structure from the *visible* tree), `visible_text`, and `raw` (a byte signature) — and records which ran in `html_modes.ran`. Crucially it scopes **separately**, never merging into the visible claim set:

- `hidden_blocks` — `display:none` / `visibility:hidden` / `hidden` / `aria-hidden` / collapsed `<details>`, each `in_scope:false` (`DOC-PBW-05`).
- `agent_only_blocks` — agent-only DOM (e.g. `<details class="agent-spec">` or `data-agent-*`), each `in_scope:false`.
- `nav_blocks` — navigation chrome, recorded out of the claim set.
- `rendered_vs_source` — the **divergence report**: tokens present in the source markup but **not** in the visible render, with a sample, so a claim hidden from the reader can't masquerade as rendered content.

**Status.** `ok` when clean; `ok-with-warnings` when a fallback adapter ran or hidden/agent-only content was found; `blocked` for an unreadable/below-threshold document (see DD-07); `failed` only on a hard read error. Every structural record's `review_status` is effectively `needs-review` — DD-01 surfaces structure, a human (and DD-02) confirm.

## Phase 2 — PDF extraction-quality metrics (DD-07), folded into the map

For a `pdf`, the same run computes and records a `pdf_quality` block (the prose analogue of "the checks tab is a smoke alarm" — a clean-looking PDF can be a picture):

- **text coverage** — share of pages with a real text layer; **per-page** breakdown kept.
- **image-only page count** — pages with an image and no extractable text (a scan).
- **table-extraction confidence** — share of detected tables that are rectangular and populated (via `pdfplumber` when present); a table baked into a scanned image stays low — **never fabricated**.
- **OCR availability** — whether an OCR engine (`tesseract` / `pytesseract`) is present.
- **locator fidelity** — `page` for PDFs (anchors are page-level, not paragraph-level), recorded so downstream consumers don't over-trust a PDF locator.

**The block rule (review finding #5).** When **text coverage is below `--coverage-threshold` AND no OCR is available**, the run returns `status: blocked`, `readiness: not-ready`, raises a **`DOC-PBW-01` extraction-loss `must-fix`**, and emits a **human-review item** ("provide an OCR'd copy or review manually"). A below-threshold scan is a **verdict, not a silent empty map** — a partial extract is never presented as complete. When OCR **is** available, the verdict is `ocr-recommended` and the `ocr` block records the **engine, the image-only pages to OCR, and the provenance note** (confidence is filled when a downstream pass actually OCRs). `--assume-no-ocr` certifies the block branch deterministically even where `tesseract` happens to be installed. The exit code is `3` on `blocked` (mirroring the workbook `manifest_gate` convention).

## Phase 3 — Prose landmine detectors (DD-02) → `.trust/<run_id>/prose-landmines.{json,md}`

Runs over the DD-01 map (or a raw document, which it maps first) and flags the quiet prose failures keyed to the §1.7 doc Pretty-But-Wrong taxonomy. Each finding carries `surface=doc`, its `format`, a §1.6 `locator`, a `severity`, a `review_status`, `issue_lifecycle:open`, a `type`, and concrete `evidence`.

```bash
python skills/document-doctor/scripts/prose_landmines.py <map.json|document> \
    --out .trust/<run_id> --run-id <run_id> \
    [--today YYYY-MM-DD] [--stale-days 365]
```

| Detector (`type`) | What it catches | Default severity | §1.7 |
|---|---|---|---|
| `undated-number` | a material figure (currency / %, magnitude, ratio) with **no date** in the same passage | should-fix | undated-number (both) |
| `stale-date` | an "as of / as at / updated / effective" date past the staleness window | should-fix (must-fix if very old) | stale-date (both) |
| `table-as-image` | a table or chart shipped as a **picture** (untraceable numbers) — incl. image-only PDF pages | should-fix / must-fix | DOC-PBW-02 |
| `broken-cross-ref` | "see Table 4 / Appendix B" with **no matching referent** in the map | should-fix | broken-narrative-logic |
| `numeric-token-candidate` | a material number in prose — a **claim to source / verify** | polish | undated/unsupported feed |
| `unattributed-quote` | a quotation with **no nearby attribution / citation** | should-fix | DOC-PBW-03 |

Status is `ok` when clean, `ok-with-warnings` otherwise. DD-02 **surfaces candidates** with locators and severities; it never blends, never invents a source, and leaves the resolve-to-source step to the document evidence map + the two-context review in later sessions.

## dd-08 — anchored Word comment round-trip spike (capability gate, proven this session)

S04/dd-05 wants to write trust findings back into the `.docx` as **anchored comments**. That only works if anchored comments survive a real Word/LibreOffice round-trip — so per the "no downstream task may assume a tool until its contract is proven" rule, it is proven **now**, before S04 depends on it:

```bash
python fixtures/fixture_dd08_comment_roundtrip.py   # writes fixtures/_dd08_comment_roundtrip_proof.txt
```

**Result of 2026-05-30: PASS.** `python-docx 1.2.0` exposes `Document.add_comment(runs=…, text, author, initials)`; the written `.docx` carries `commentRangeStart` / `commentRangeEnd` / `commentReference` + `word/comments.xml`, and a **LibreOffice `--convert-to docx` round-trip preserves the anchor and the comment text** (rc 0, reload count 1). ⇒ **S04/dd-05 may use anchored comments + an appendix.** If a future host returns `PARTIAL`/`FAIL` (LibreOffice drops the anchor), the recorded fallback is **appendix + a `.trust/<run>/` sidecar** so no evidence is lost — the path is in `capability-inventory.md` (`docx_comment_write`).

---

## Acceptance criteria (binary)
- DD-01 emits `document-map.{json,md}` for **each** of `docx`/`pdf`/`md`/`html`; JSON parses; **every** sections/paragraphs/tables/figures/footnotes record carries `surface=doc` + the correct `format` + a §1.6 locator.
- Tables are emitted with a **cell grid** where the format allows; sections carry the nested **heading path**.
- HTML runs **visible-text / semantic-DOM / raw** modes and records hidden, collapsed and **agent-only** DOM **separately** with `in_scope:false`, plus a **rendered-vs-source** divergence report — hidden/agent tokens never leak into the visible paragraph set.
- DD-07 reports text-coverage, image-only count, table-extraction confidence, OCR availability and locator fidelity; an **image-only PDF with no OCR is `blocked` / `not-ready`** with a `DOC-PBW-01` must-fix + a human-review item; with OCR present it records engine + page provenance.
- DD-02 detects undated-number, stale-date, table-as-image, broken-cross-ref, numeric-token-candidate and unattributed-quote, **each** with format + locator + severity + evidence.
- dd-08 has a recorded verdict; on `PASS` the comment path is enabled, otherwise the appendix+sidecar fallback is recorded in `capability-inventory.md`.
- Per-format limits are reported **per format** (`format_reports`), never folded into one combined "doc works" claim.

## Failure modes (graceful degradation, per capability-inventory.md)
- **python-docx absent** → `docx_read` falls back to markitdown, then raw-XML strip; structure fidelity drops and is recorded (`adapter.fallback:true`), never a silent empty map.
- **pdf text layer below threshold + no OCR** → `blocked`/`not-ready` + `DOC-PBW-01` (the scan case), not a partial extract presented as complete.
- **pdfplumber absent** → table-extraction confidence stays `0.0` (unknown), reported honestly; the rest of the map still runs.
- **bs4 absent** → `html_read` falls back to markitdown then raw tag-strip; DOM scoping is lost and the note records it.
- **Document unreadable by every adapter** → `status: failed` with the error (distinct from `blocked`).
- **No source packet** → numeric/quote candidates are surfaced but not traced to `S<n>`; the gap is noted, never invented.

## Fixtures
- `fixtures/fixture_document_doctor.py` builds a tiny self-made file of **each** format under `fixtures/document_doctor/` (`tiny.md`, `tiny.docx`, `tiny_text.pdf`, `tiny.html`), a **scanned/image-only PDF** (`scanned.pdf`, which **must block** with OCR forced off and record OCR provenance with OCR on), and an **HTML file with hidden text + a nav + an embedded SVG + agent-only `<details>`**. It runs `document_map.py` + `prose_landmines.py` and asserts the full DD-01/DD-07/DD-02 contract (41 assertions). Wired into `fixtures/run_fixtures.sh`.
- `fixtures/fixture_dd08_comment_roundtrip.py` is the dd-08 spike; its verdict is recorded in `fixtures/_dd08_comment_roundtrip_proof.txt` and wired into `run_fixtures.sh`.
- Keep generic — these are format-and-domain-agnostic defect classes, not tied to any house template or client.
