# Trust Layer — Conventions (the contract hub)

**This is the single file every Trust Layer component reads from.** Skills, commands, and the two-context reviewer reference these definitions; they never redefine them locally. Defining the vocabulary, the provenance record, the I/O contract, the waiver rule, and the release thresholds **once** is what keeps the components composable and prevents implementation drift as the plugin grows across sessions and milestones.

A machine-readable mirror of everything below lives in [`conventions.json`](conventions.json). When code needs these values, it reads the JSON; humans read this file.

> **Stability contract.** The *abstract* shapes here (especially the provenance record and the command I/O contract) are frozen interfaces. The M1 reviewer codes against them even though the M2 deck **evidence-map builder** that fully populates a provenance record does not land until M2. The contract precedes the implementation; only implementation is deferred.

---

## 1. Enumerations (controlled vocabulary)

### 1.1 Source identifiers & source status

A **source** is any input a claim can rest on: an uploaded file, a system export, a prior deliverable, a verbal figure, an external benchmark.

- **Source ID** — every source in a run's source packet gets a stable ID of the form `S<n>` (`S1`, `S2`, …), assigned in the source packet and **never reused or renumbered** for the life of the artifact. All downstream references (locators, evidence map, speaker notes) point at the source ID, not at a filename, so renames and re-imports don't break the trail.
- **Source status** — one of:

  | Value | Meaning |
  |---|---|
  | `current` | The live, authoritative version. Safe to build on. |
  | `superseded` | A newer source replaced it; kept only for audit. A claim resting on a `superseded` source is a finding. |
  | `estimate` | A modeled / projected / judgement figure, not an observed fact. |
  | `raw` | Unprocessed uploaded data, preserved faithfully (source ID, source date, import date, cleaning notes). Never mixed with calculations. |

### 1.2 Severity — `severity`

How badly a finding must be dealt with before the file can travel.

| Value | Meaning |
|---|---|
| `must-fix` | A correctness/traceability defect that can mislead a reader or a decision. **Blocks `ready`** until resolved or formally waived. |
| `should-fix` | A real weakness that a competent reader could trip on; fix unless there's a reason not to. |
| `polish` | Cosmetic / stylistic; does not affect trust. |

### 1.3 Review status — `review_status`

The standing of a single claim or number after review.

| Value | Meaning |
|---|---|
| `verified` | Traces to a `current` source with a sound calculation/derivation. |
| `needs-review` | Plausible but not yet traced; a human must confirm. |
| `unsupported` | No source backs it; stated as if it were fact. |
| `conflicting` | Two or more sources disagree, or the source disagrees with the file. |

### 1.4 Readiness — `readiness`

The artifact-level verdict the harness returns. Always paired with the explicit list of claims/numbers/assumptions a human must still own.

| Value | Meaning |
|---|---|
| `not-ready` | At least one `must-fix` is `open`, `fixed-claimed` (not yet verified), or `still-open`. Do not let it travel. |
| `ready-with-limitations` | No open `must-fix`, **but** at least one `must-fix` was resolved by a human **waiver** (see §4). The named limitations travel with the file. |
| `ready` | Every `must-fix` is `verified-fixed`; no waivers in play. |

### 1.5 Issue lifecycle — `issue_lifecycle`

The state of a single finding inside the build↔review loop. "No new must-fix" is **not** the same as "resolved" — a finding only leaves the loop when it reaches a terminal verified/waived state.

| Value | Meaning |
|---|---|
| `open` | Raised by the reviewer; not yet addressed. |
| `fixed-claimed` | The builder says it fixed it; **not yet re-verified**. Still blocks `ready`. |
| `verified-fixed` | The reviewer confirmed the fix against the original finding in a fresh pass. Terminal (resolved). |
| `waived-by-human` | A named human accepted the risk per §4. Terminal, but caps readiness (see §4). |
| `still-open` | Survived the loop's stop condition unresolved (e.g. round cap hit). Forces `not-ready`. |

**Loop stop condition (used by `trust-review`, M1):** each round runs **both** a verify-prior pass (re-check every `fixed-claimed` finding) **and** a fresh full pass. Stop only when there is **no `open`/`fixed-claimed`/`still-open` finding AND no new `must-fix` was raised this round.** If the round cap is reached with anything unresolved, mark the survivors `still-open` → `not-ready`.

### 1.6 Surface & format — `surface`, `format` (v2 additive)

Every record that **maps, locates, claims, evidences, reviews, benchmarks, or dogfoods** a deliverable carries a **`surface`**. v1 had two; v2 adds the **document** surface. This is an *addition* to the controlled vocabulary — the v1 `excel`/`ppt` surfaces are unchanged.

| `surface` | Covers |
|---|---|
| `excel` | Workbooks (`.xlsx`) — the M1 path. |
| `ppt` | Decks (`.pptx`) — the M2 path. |
| `doc` | Prose documents — the v2 path. |

`doc` is **not** one thing. A document is one of four formats with materially different extraction fidelity, mutation surface, and locator semantics, so **every `doc`-surface record MUST also carry a `format`** (a REQUIRED field, never inferred at read time):

| `format` | Reader / writer family | Why it differs |
|---|---|---|
| `docx` | Word — structured XML | Anchored comments are possible (see `capability-inventory.md`); high locator fidelity. |
| `pdf`  | Fixed-layout | Extraction quality varies; may need OCR. A below-threshold scan is a *verdict*, not a silent pass. |
| `md`   | Markdown — plain text | High fidelity; headings/tables/links preserved. |
| `html` | Markup | Multiple extraction modes (visible-text / semantic-DOM / raw); hidden/agent-only DOM is scoped separately. |

**Rule (v2 review finding #6):** a single `doc` verdict must **never** hide a per-format limit. The map, locator, claim, evidence record, review finding, benchmark score and dogfood report are each reported **per `format`** as well as combined. A harness that can read `md` cleanly but loses a `pdf` table must say so per format, not average it into one "doc works" claim.

**Document locator grammar.** A §2 `locator` into a `doc`-surface source uses this grammar so a reviewer lands on the exact evidence:

```
page:N | heading:'A>B>C' | para:N | table:R{r}C{c} | figure:ID
```

`tab!cell[:range]` remains the workbook form and `slide N / shape` the deck form; a locator's grammar follows its source's `surface` + `format`.

### 1.7 Document Pretty-But-Wrong defect list (`doc` surface, v2 additive)

The v1 reviewer's Pretty-But-Wrong checklist (ten Excel/PPT failure modes) is canonical in `skills/trust-review/scripts/review_contract.py` (`PRETTY_BUT_WRONG`), mirrored in `references/pretty_but_wrong_checklist.md`. The **doc-surface** taxonomy below is the contract the document trust path's reviewer implements; the machine-readable `doc` rows are added to `review_contract.PRETTY_BUT_WRONG` when that reviewer is built (the contract precedes the implementation, per the stability note above). Severity is the §1.2 enum verbatim; the `both`-surface v1 items (unsupported-claim, undated-number, assumption-as-fact, stale-date, broken-narrative-logic) already apply to `doc` and are not re-listed.

| id | surface | type | default | what it catches |
|---|---|---|---|---|
| DOC-PBW-01 | doc | extraction-loss | **must-fix** | A number, table, or figure dropped or garbled by extraction (an unparsed PDF table, low-confidence OCR, a stripped HTML block) presented as if complete — the reader can't tell content was lost. Below-threshold PDF extraction with no OCR ⇒ `blocked`/`not-ready` + a human-review item. |
| DOC-PBW-02 | doc | untraceable-figure-or-table | **must-fix** | A table or embedded figure whose numbers can't be traced to a source + `doc_locator` — the prose analogue of the deck's untraceable-chart. |
| DOC-PBW-03 | doc | quote-or-citation-misattribution | **must-fix** | A quotation, statistic, or citation attributed to a source that does not support it (or is fabricated). |
| DOC-PBW-04 | doc | contradiction-across-sections | should-fix | The document asserts X in one section and not-X in another, or a figure that disagrees with its own table / appendix. |
| DOC-PBW-05 | doc | hidden-or-agent-only-content | should-fix | Collapsed/hidden/agent-only DOM (e.g. HTML `<details>`), tracked changes, or comments carrying claims absent from the visible render — counted separately and scoped explicitly (finding #11), never silently merged into the visible-text claim set. |
| DOC-PBW-06 | doc | verification-contradiction-unresolved | **must-fix** | A `contradicts` verification verdict (§2.2) that was blended/averaged away instead of preserved as a `conflict`. |
| DOC-PBW-07 | doc | per-format-limit-hidden | should-fix | A per-format extraction/mutation limit (§1.6) folded into a single "doc works" verdict instead of reported per format. |

Keep generic — these are format-and-domain-agnostic defect classes, not tied to any house template or client.

---

## 2. The abstract provenance record

The frozen shape that ties a **claim** to the **sources** that support it, with confidence and conflict made explicit. The M1 reviewer evaluates claims against this shape; the M2 `evidence-map` builder produces it for decks. One record per claim.

```json
{
  "claim": "Revenue grew 12% YoY in FY25",
  "supporting": [
    {
      "source_id": "S1",
      "locator": "Model.xlsx#Outputs!B14",
      "confidence": 0.0,
      "rationale": "why this source backs the claim (and the calc, if any)"
    }
  ],
  "conflict": null
}
```

Field rules:

- **`claim`** — one statement that could be true or false. A headline, a board number, a stated assumption.
- **`supporting`** — zero or more sources. A claim with **zero** supporting entries is `unsupported` (a `must-fix` once it travels). Multiple entries are allowed and expected — a single claim may rest on several sources.
- **`locator`** — points *into* a source: `tab!cell[:range]` for workbooks, `slide N / shape` for decks, page/section for documents. Specific enough that a reviewer can land on the exact evidence.
- **`confidence`** — `0.0`–`1.0`, the *link* confidence that this source actually supports this claim (not the source's own reliability). The cross-app linker (M2) emits candidates with confidence and **holds sub-threshold matches for human approval** rather than asserting false provenance.
- **`conflict`** — `null`, or an object describing the disagreement: `{ "with": ["S2"], "note": "S1 says 12%, S2 says 9%" }`. A non-null conflict sets the claim's `review_status` to `conflicting`.

### 2.1 Speaker-note / evidence template (per claim)

Where a claim surfaces in a deliverable (a slide headline, a board figure), the evidence layer is written in this fixed template so it is audit-ready and machine-checkable:

```
Claim:        <the claim, in plain English>
Source IDs:   <S1, S3 …>
Calculation:  <the formula/derivation, or "direct from source">
Assumptions:  <load-bearing assumptions, with their source/owner/status>
Needs review: <what a human must still confirm, or "none">
```

For decks (M2) this becomes the slide's speaker notes; for workbooks (M1) it is recorded in the run's evidence artifact.

### 2.2 Source verification records (verify against the world, v2 additive)

v1 checks a claim against the sources **uploaded with the file**. v2 may additionally check a claim against sources **outside the packet** — the open web and the user's own internal corpus — and folds the result back into the **same** provenance record (§2). It does not invent a parallel record type: a verification result is just another source.

A verification is **provider-agnostic.** Its `source_type` is one of:

| `source_type` | What it is | `locator` |
|---|---|---|
| `external-web` | A public web source retrieved by a search/fetch provider. | the URL |
| `internal-vault` | A note / document from the user's own corpus (e.g. a knowledge-base vault, an Obsidian vault, a configured local vault, repo/project docs). | note-path + the §1.6 `doc_locator` |
| `internal-memory` | A fact from a project-memory / knowledge-graph backend. | the memory-record id |

`provider` is **metadata only** — it records *which backend answered* (e.g. `provider: exa`, `provider: knowledge-mcp`, `provider: obsidian`, `provider: local-vault`) and is **never part of the source type and never baked into the schema.** Which provider serves a `source_type` is decided at runtime by the adapter registry (F2-04); the contract names operations and source types, never a tool.

**Provider packs.** A user's own search, read and write tools reach the registry through a provider pack (see `capability-inventory.md`, *Provider packs*). A pack adds providers; it changes no source type, no verdict and no cap.

A verification returns exactly one **`verdict`**:

| `verdict` | Meaning |
|---|---|
| `corroborates` | The verification source supports the claim. |
| `contradicts` | It disagrees with the claim. |
| `not-found` | No source addressed the claim. |
| `not-checked-budget` | The claim was **eligible** but not checked — the materiality/budget cap or a timeout stopped it (§2.3). Recorded explicitly, **never silently dropped.** |

Two rules keep verification honest and composable:

- **A result becomes a source.** A `corroborates`/`contradicts` source is admitted to the run's source packet as a **new `S<n>`** (URL or note-path `locator`), assigned its own `source_status` and `sensitivity`, and entered into the claim's `supporting[]` / `conflict` exactly like any uploaded source. The verification doesn't sit in a side-channel; it joins the provenance trail.
- **Contradiction ⇒ conflict, never blended.** A `contradicts` verdict sets the claim's `review_status` to `conflicting` and writes a §2 `conflict` object (`{ "with": ["S7"], "note": "claim says 12%, S7 (external-web) says 9%" }`). The harness **never averages** the claim with a contradicting source, never splits the difference, never silently overwrites — the disagreement is preserved for a human, identical to the v1 source-vs-source conflict rule.

### 2.3 Claim materiality & per-travel-class verification budget (v2 additive, finding #4)

Not every claim is worth an external round-trip; unbounded auto-verification is slow, leaks context, and burns rate limit. Verification is **prioritised by materiality** and **bounded by a per-travel-class cap.**

**Materiality tiers** — which claims are eligible first (a claim may sit in several; the highest governs):

| tier | example |
|---|---|
| `numeric` | a figure, amount, count, percentage |
| `date` | an as-of date / period label |
| `named-entity` | an org / person / product / place |
| `regulatory` | a statute, standard, or legal obligation |
| `market` | a market-size / share / competitor claim |
| `assumption-as-fact` | a load-bearing assumption stated as established fact |
| `headline` | a headline / executive-summary assertion (the claims a reader most trusts) |

**Per-travel-class caps** (the order on travel class is F-03's `internal < travels < board-regulatory`):

| travel class | verification budget |
|---|---|
| **Internal** | opt-in — 0 by default. |
| **Travels** | a bounded cap over the top material claims. |
| **Board / Regulatory** | a higher cap; the `headline` + executive-summary set is **always in scope**. |

The **contract fixes the ordering and the existence of a cap**, not the number: concrete N, timeout, and dedupe parameters are tuneable per run and **recorded in the manifest**. Near-identical claims are de-duplicated and verified once. Any claim that is eligible but unverified because the cap or a timeout was hit is recorded **`not-checked-budget`** (§2.2) — eligible-but-skipped is a *stated outcome*, never an invisible omission.

### 2.4 External source-quality policy (v2 additive, finding #7)

A verification is only as good as the source that answered. **`external-web`** sources are admitted under an **authority order**:

1. **Official / regulatory / primary filings** — the entity itself, a regulator, an audited filing.
2. **Reputable primary reporting** — the primary author of the fact.
3. **Reputable secondary** — an established outlet summarising a primary.

Rules:

- **Reject low-authority for `must-fix`.** Content farms, unattributed aggregators, and SEO chaff may be noted as weak corroboration but can **never** clear a `must-fix` claim.
- **Retrieval date required.** Every external source records the as-of of its fetch; an undated retrieval is treated as stale.
- **High-confidence corroboration = ≥2 independent sources OR 1 primary/official source.** A lone secondary source is `needs-review`, not `verified`.
- **Stale / contradictory sources are preserved as conflicts (§2.2)** — never discarded to make a claim look clean.

### 2.5 Web-egress rule (v2 additive, finding #8)

Verifying a claim against the web sends *something* out, so the contract bounds what may leave:

- **Confidential / restricted claim text is never egressed.** Only a **sanitised / abstracted query** — named entities masked, monetary magnitudes coarsened, source labels removed — may leave the machine, and the manifest records the **raw-claim-vs-egressed-query diff.**
- **Restricted Board/Regulatory defaults to `skipped-egress`** — the external check does not run (recorded as `not-checked-budget`) **unless a named-owner waiver** (the §4 / policy §6 waiver shape) explicitly permits a sanitised query. Internal verification (`internal-vault` / `internal-memory`, no egress) still runs.

This section fixes the **contract** (confidential/restricted ⇒ sanitise-or-skip; restricted-Board ⇒ skipped-egress without a signed waiver). The **mechanism** — the deterministic egress sanitizer, the gate, and the manifest diff — lives in `sensitivity-routing-policy.md`.

---

## 3. Standard command I/O contract

**Every** Trust Layer skill and command obeys this contract, so they compose into a pipeline and any one of them can be run, re-run, or chained without bespoke glue.

| Aspect | Contract |
|---|---|
| **Inputs** | Declared up front as explicit artifact paths (the workbook, deck, source files). A skill never reaches for an undeclared input. |
| **Output dir** | `.trust/<run_id>/` next to the deliverable. `<run_id>` = `<ISO-timestamp>_<short-slug>` (e.g. `2026-05-29T1430_boardpack`). |
| **Outputs** | Two files per skill: `<skill>.json` (machine-readable: status, findings keyed by the enums above, provenance records, links) **and** `<skill>.md` (human memo). |
| **Status** | The skill returns exactly one status from the enum below. |

### 3.1 Status enum — `status`

| Value | Meaning |
|---|---|
| `ok` | Ran fully; nothing that needs a human's attention beyond the normal verdict. |
| `ok-with-warnings` | Ran fully, but with caveats the human should read (e.g. a capability fell back — "recalc unverified"). |
| `blocked` | Could not run because a precondition is unmet (e.g. source sensitivity/model routing not recorded before a model-run loop; a required input missing). Distinct from `failed` — the work is well-defined but **gated**. |
| `failed` | Attempted and errored (tool crash, unreadable file). |

`blocked` vs `failed` matters: a `blocked` run tells the orchestrator *which gate to satisfy*; a `failed` run is an error to debug.

### 3.2 The per-run manifest

Each run writes `manifest.json` into `.trust/<run_id>/` recording what happened — and, for any model-run loop, **what each model saw**: input artifact + source-packet + reviewer-prompt hashes, model name + version, and timestamp per round. The full sensitivity/redaction/routing policy that governs the manifest is defined in `sensitivity-routing-policy.md` (S02); this contract reserves the manifest's required keys so M1 skills emit a compatible stub.

---

## 4. Readiness & waiver semantics

The human gate is a feature, not an apology — a model reviewing its own family of output has a blind spot, so the last word is a human's. The waiver rule makes that explicit and un-gameable:

1. **No artifact reaches `ready` while any `must-fix` is `open`, `fixed-claimed`, or `still-open`.**
2. **Any waived `must-fix` caps the verdict at `ready-with-limitations`** — never plain `ready`. A waiver requires a **named owner, the required decision, a due date, a signoff status, and a written rationale**, all persisted in the manifest (the human-gate record, `tr-06`, M1/S07).
3. **A `Board`/`Regulatory` travel-class waived `must-fix` additionally requires named-owner signoff** recorded in the manifest before the verdict may be returned. Without signoff the verdict stays `not-ready`.

The waived limitation text travels *with* the file, so a downstream reader sees exactly what was accepted and by whom.

---

## 5. Release thresholds (concrete, change-controlled)

The benchmark (`hr-00` for M1, `hr-04` for M2) must clear these hard numbers. The soft "ship if green or gaps are listed" criterion is **not** a release criterion.

| Metric | Threshold | Meaning |
|---|---|---|
| `recall_must_fix` | **≥ 0.90** | Of the seeded real `must-fix` defects, the harness must catch at least 90%. Missing real defects is the cardinal failure. |
| `false_positive_rate_max` | **≤ 0.10** | At most 10% of raised findings may be spurious. Too many false alarms → harness fatigue → people route around it. |
| `severity_accuracy` | **≥ 0.80** | At least 80% of findings are assigned the correct severity (`must-fix`/`should-fix`/`polish`). |
| `runtime_max_min` | **≤ 10 min** | A full run on the benchmark artifact completes within 10 minutes, or the workflow won't be used. |

These are **placeholder targets — revisable.** They are deliberately explicit so they can be argued with.

### 5.1 Threshold-change rule

Thresholds may be revised **only via a logged rationale recorded BEFORE the release benchmark runs.** They may **never** be relaxed after seeing failures. A post-hoc change (loosening the bar to pass a failing benchmark) **blocks release** at `hr-00`/`hr-04`. This closes the loophole of softening the gate to get a green light.

---

## 6. How components reference this file

Every `SKILL.md` and command in this plugin must, in its body, point here for shared definitions — e.g. *"Severity, review status, readiness, and the issue lifecycle are defined in `references/conventions.md`; use those values verbatim."* No component redefines an enum, the provenance record, the I/O contract, the waiver rule, or the thresholds. If a definition needs to change, it changes **here** (thresholds additionally per §5.1), and every component inherits the change.

---

## 7. v3 contract extensions (additive — frozen v1/v2 above unchanged)

> **Additivity contract (v3).** Everything in §7 is an *addition*. The §1–§6 enums, the abstract provenance record (§2), the command I/O contract (§3), the waiver rule (§4), and **the §5 release thresholds + the §5.1 threshold-change rule are FROZEN and unchanged.** v3 introduces the recurring-update primitive on a stable claim identity, turns three "best-effort" properties into readiness *caps* (never a relaxation — a cap can only make a verdict *worse*), and proves backward-compatibility before it writes. A v3 cap NEVER promotes a verdict; it only ever holds it down. The machine-readable mirror is the `_v3_additive` block in [`conventions.json`](conventions.json). The findings referenced (2/3/4/5/7/8/10) are the accepted items in `office-trust-layer-v3-review-disposition.md`.

### 7.1 Degraded-status caps — `degraded_status` (findings 3/4/5)

A **degraded-status cap** is a named condition that, when present, **caps the artifact-level `readiness` (§1.4) — it can never yield plain `ready`.** This closes the "degrade silently with a footnote" loophole: a sidecar-only PDF, an unverified-recalc workbook, a same-family reviewer, or an unwritable embedded-evidence channel each *cap* the verdict instead of passing with a note.

| cap id | condition | caps readiness at | surface(s) |
|---|---|---|---|
| `sidecar-only-pdf` | the in-PDF embedded-evidence channel (§7.4) was unavailable; the evidence lives **only** in a `.trust/` sidecar | `ready-with-limitations` — and **`not-ready-for-forwarding`** for the *survives-the-forward* property (the evidence does not travel inside the file) | `doc`/`pdf` |
| `recalc-unverified` | live recalc (LibreOffice) was unavailable/failed; only static formula assertions ran | `ready-with-limitations`; **the checks-tab readiness *claim* is blocked** (the smoke alarm was never powered — ab-01/§7.3) | `excel` |
| `same-family-reviewer` | the reviewer ran in the **same model family** as the builder (no cross-vendor reviewer, no named human SME) | by travel class (§7.2): Internal/Travels ⇒ `ready-with-limitations`; **Board/Regulatory ⇒ `blocked`** unless a named-owner signoff is recorded, then `ready-with-limitations` | all |
| `embed-unavailable` | the surface's embedded-evidence channel could not write (e.g. anchored `docx` comments did not round-trip; PDF annotation/attachment unsupported) | `ready-with-limitations` | `doc` |

Rules:

- **A cap never produces plain `ready`.** The strictest cap governs when several apply (`not-ready-for-forwarding` < `blocked` < `ready-with-limitations`). A cap is **orthogonal to and stacks with** the §4 waiver rule — a waiver caps at `ready-with-limitations`; a cap can lower it further, never raise it.
- **`not-ready-for-forwarding`** is a v3 *forwarding* qualifier on `ready-with-limitations`/`not-ready` (§1.4 unchanged): the file may be usable in place but its proof does **not** travel inside it. It is recorded on the verdict, never silently dropped.
- Each active cap is recorded in the manifest as `degraded_status[] = { cap, condition, capped_at, surface, note }`. Absent caps ⇒ v1/v2 behaviour (the field is optional and defaults to `[]`).

### 7.2 Reviewer independence by travel class — `reviewer_independence` (finding 5)

The two-context review's independence is no longer a free-text note; it is a **value gated by travel class** (the §2.3 order `internal < travels < board-regulatory`).

| `reviewer_independence` | meaning |
|---|---|
| `cross-vendor` | reviewer is a different vendor's model than the builder (strongest automated independence). |
| `named-human-SME` | a named human subject-matter expert performed/owns the review. |
| `same-family` | reviewer is a different model but the **same family/vendor** as the builder (the v1 Agent model-split). |
| `same-model` | reviewer shares the builder's model (no independence; never acceptable on its own for a travelling file). |

Requirement by travel class:

- **Internal / Travels** — a `same-family` reviewer is **accepted**; record the `same-family-reviewer` cap note (§7.1) but readiness is not gated beyond it.
- **Board / Regulatory** — requires `cross-vendor` **or** `named-human-SME`. A `same-family` (or `same-model`) fallback yields **`blocked`**, or **`ready-with-limitations` only after an explicit named-owner signoff** (the §4 waiver shape: owner + decision + due date + signoff + rationale, persisted in the manifest). The "same-family note" path is allowed for Internal/Travels **only**.

The chosen value + the travel class + (if applicable) the signoff are recorded in `manifest.routing.reviewer_independence`.

### 7.3 Checks-tab spec enum — `checks_tab` (finding 4 / ab-01)

The workbook "checks tab" is the *smoke alarm* (`workbook-doctor`). Its check categories are a controlled enum so a builder cannot pass a checks-tab readiness claim with an ad-hoc or empty set:

| `checks_tab` category | what it asserts |
|---|---|
| `tie-outs` | totals/subtotals reconcile to their components and to stated control figures. |
| `formula-consistency` | a formula is consistent across the row/column it spans (catches the "one cell wrong across a whole row" trap a naive consistency check passes). |
| `error-scan` | no Excel error values (`#REF!`, `#DIV/0!`, `#VALUE!`, …) survive a recalc. |
| `stale-date` | as-of / period dates are current and consistent across the workbook. |
| `scenario` | base/scenario toggles recompute and stay internally consistent. |

**Recalc dependency (ab-01):** the checks-tab readiness *claim* is only valid when a **live recalc was verified**. If recalc is unavailable/failed, the `recalc-unverified` cap (§7.1) applies and **the checks-tab readiness claim is blocked** — static formula assertions are a *fallback*, explicitly **not equivalent** to recalc.

### 7.4 In-PDF embedded-evidence channel + locator addition (finding 3 / ab-02)

v2's evidence output channels per format (`capability-inventory.md`) are extended with an **embedded-PDF** evidence channel so a forwarded PDF can carry its own proof:

| evidence channel | surface/format | travels inside the file? |
|---|---|---|
| `embedded-pdf` | `doc`/`pdf` | **yes** — evidence embedded as PDF annotations / file attachments, preserving the author's pages and not breaking signatures/forms/tagged-PDF. |
| `sidecar` | any | no — evidence in `.trust/<run_id>/` only ⇒ triggers the `sidecar-only-pdf` cap (§7.1). |

**Locator addition (extends the §1.6 doc locator grammar — additive, §1.6 text unchanged).** A `pdf`-format embedded-evidence anchor uses:

```
annot:ID | attachment:'NAME'
```

alongside the existing `page:N | table:R{r}C{c} | figure:ID` forms, so a reviewer can land on the embedded note itself. When `embedded-pdf` is unavailable the channel degrades to `sidecar` and the `sidecar-only-pdf` cap is recorded — **never a silent "survives forward" claim.**

### 7.5 Canonical claim identity + moved-claim diff (finding 2)

A refresh must be able to tell *"this is the same claim, moved"* from *"this claim's meaning changed"* — otherwise it becomes a trust **copier** that reuses `verified` on a claim whose source/period/unit quietly shifted. The canonical identity is computed by `references/scripts/claim_identity.py` (pure stdlib) and consumed by `office-update` (S02/S03) and verification.

**Identity fields** (one per claim):

| field | how it is computed |
|---|---|
| `claim_id` | minted once as `C-<first-12 hex of sha256(normalized_text_hash + ':' + source_fingerprint)>`; **stable for the life of the claim**, persists across moves. |
| `normalized_text_hash` | `sha256` of the claim text after normalization: lowercase, Unicode-NFKC, collapse whitespace, strip surrounding punctuation, normalize number formatting (thousands separators / trailing zeros) **without** changing magnitudes or units. |
| `locator_hash` | `sha256` of the canonical locator string; **surface+format specific** (`tab!cell` / `slide N/shape` / the §1.6 doc grammar incl. §7.4 anchors). Excluded from `claim_id` so a move does not change identity. |
| `source_fingerprint` | `sha256` over the sorted backing-source tuples `(source_id, source_status, content_or_excerpt_hash, as_of_date)`. Identity of *what backs the claim*. |
| `retrieval_timestamp` | ISO-8601 as-of of the verification/extraction that produced the current state (drives source-age staleness, §7.5 invalidation). |
| `materiality_class` | the governing §2.3 materiality tier (highest of numeric/date/named-entity/regulatory/market/assumption-as-fact/headline). |
| `waiver_scope_hash` | `sha256` over the waiver that covers this claim (owner + decision + scope + travel class), or `null` if unwaived. |
| `supersession_state` | `active` \| `superseded` \| `tombstoned` (a dropped claim is tombstoned, never silently deleted). |

**Moved-claim diff.** Given a prior identity P and a current identity C:

- **same `normalized_text_hash` AND same `source_fingerprint` AND different `locator_hash` ⇒ `moved` — identity STABLE.** `claim_id` is carried, `locator_hash`/`retrieval_timestamp` updated, and a prior verdict **may** be carried (the move alone does not invalidate it).
- same on all of text+source+locator ⇒ `unchanged`.
- any change to `normalized_text_hash` or `source_fingerprint` ⇒ a **new identity** (a new `claim_id`); the new claim **starts at `needs-review` and cannot inherit a prior verdict** (§7.5 below).

**Identity-change classes that MUST invalidate a carried verdict** (feed the up-02 invalidation matrix; any one forces re-review — the carried `verified`/`ready` does not transfer):

1. `text-changed` — `normalized_text_hash` differs.
2. `source-changed` — `source_fingerprint` differs (different source, content changed, or the backing source went `superseded`).
3. `unit-or-period-changed` — a semantic shift in unit/period even if the surface text looks similar (a sub-class of source/text change; called out because it is the most common silent drift).
4. `source-stale` — `retrieval_timestamp` is older than the freshness window for the claim's materiality/travel class.
5. `waiver-scope-changed` — `waiver_scope_hash` differs or the covering waiver expired/owner changed.
6. `superseded-or-tombstoned` — `supersession_state` left `active`.
7. `materiality-escalated` — `materiality_class` rose (a higher bar; a verdict earned at a lower tier cannot cover the higher one).

A `moved` claim with **none** of the above is the only case that carries a verdict; everything else re-enters review at `needs-review`.

### 7.6 Human-gate-digest invariant schema (finding 7 / av-01)

When the human gate summarises findings as a *digest*, prioritisation must never hide an unresolved `must-fix`. The digest carries non-negotiable **invariants**:

```json
{
  "full_set_readiness": "not-ready | ready-with-limitations | ready",
  "must_fix_total": 0,
  "must_fix_open": 0,
  "displayed": ["finding-id …"],
  "hidden_tail_count": 0,
  "owner_ack": { "owner": "name", "ack_at": "ISO-8601", "ack_scope": "full-set" },
  "invariants": {
    "readiness_from_full_set": true,
    "no_digest_action_auto_resolves": true,
    "hidden_tail_visible": true,
    "tail_acknowledged": true
  }
}
```

Invariants (all required):

- **`readiness_from_full_set`** — readiness is computed from the **full** finding set, never the displayed/top set.
- **every `must-fix` stays blocking** until it reaches a §1.5 terminal (`verified-fixed`/`waived-by-human`); **no digest action auto-resolves** a finding.
- **`hidden_tail_visible`** — the count of must-fix findings not shown (`hidden_tail_count`) is always surfaced.
- **`tail_acknowledged`** — the owner must acknowledge the hidden tail (`owner_ack`) before the digest's verdict is acted on.

A digest that cannot satisfy all four returns `not-ready` (the safe default), never a prettied-up `ready`.

### 7.7 Two-phase vault-writeback record (finding 8 / av-02)

Writing provenance back to the user's internal corpus must not **poison** it. A vault write-back is **two-phase**: a derived note is written to `quarantine` and **promoted only after explicit human approval.** Until promoted, the verifier treats it as **audit metadata, not a primary fact.**

```json
{
  "phase": "quarantine | promoted",
  "source_authority": "derived-provenance",
  "schema_version": "3",
  "supersedes": ["note-id …"],
  "expiry": "ISO-8601 | staleness-policy",
  "run_hash": "sha256 of the producing run",
  "rollback_pointer": ".trust/<run_id>/ or a prior note revision",
  "dedupe_key": "artifact:<id>|run:<id>|claim:<claim_id>",
  "approved_by": "name | null",
  "approved_at": "ISO-8601 | null"
}
```

Rules:

- **`quarantine` → `promoted` requires a named human approval** (`approved_by` + `approved_at`); an unapproved note is never promoted and never treated as a primary fact.
- `source_authority` is always `derived-provenance` — a Trust-Layer-derived note never masquerades as an authoritative primary source.
- `supersedes` / `dedupe_key` prevent duplicate or stale corpus entries; `rollback_pointer` + `run_hash` make every promotion reversible and auditable.

### 7.8 Schema versioning + migration (finding 10 / fd-03)

The `.trust/<run_id>/` run schema has a **`schema_version` lineage**, additive at each step:

| `schema_version` | adds (over the prior) |
|---|---|
| `1` (v1) | the base manifest: artifact, travel_class, sensitivity, routing, rounds, gate. |
| `2` (v2) | `verification`, `egress`, `adapter_bindings`, `retention` (document + source-verification surfaces). |
| `3` (v3) | `degraded_status` (§7.1), `reviewer_independence` (§7.2), the human-gate-digest invariants (§7.6), the two-phase vault-writeback record (§7.7), and claim-identity stamps (§7.5). |

**Backward-compatibility rule (the migration gate, `eval/compat_harness.py`):**

- A v3 reader **MUST load every committed v1/v2 run.** Old manifests stay readable; required keys still resolve.
- **New v3 optional fields default safely** — absent ⇒ exactly the v1/v2 behaviour, **never a crash** (`degraded_status` ⇒ `[]`, `reviewer_independence` ⇒ inferred from `routing` else `same-family`, claim identity ⇒ recomputed on demand).
- **A v3 refresh consuming a prior run is READ-ONLY** — it never mutates the prior artifacts; it stamps `schema_version` + `migration_notes` onto its **own new output only**.

New v3 outputs stamp `schema_version: "3"` and a `migration_notes` string recording what was carried forward and what was re-reviewed.
