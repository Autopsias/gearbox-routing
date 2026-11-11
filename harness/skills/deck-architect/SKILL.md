---
name: deck-architect
description: The Office Trust Layer's PowerPoint front door — it builds the deck's ARGUMENT before any slide exists, then renders and validates it. DA-01 turns a source packet into a deck architecture — audience, the decision asked, a one-sentence spine, and a slide map where every headline is a CLAIM sentence, not a topic, tied to its sources. It is a HARD GATE — no rendering until approved and every claim names a source or is flagged unsupported. DA-02 reads any .pptx template into render rules, marking what it cannot determine as unclear rather than inventing it. DA-03 renders ONLY from the approved architecture, preserving claim headlines verbatim and carrying evidence into the speaker notes. DA-04 is the deck variant of Trust Review — brand AND accuracy checks inside the review loop. Use when the user says build the deck architecture, what each slide should claim, or render or validate the deck. Not for pure restyling or brand polish (pptx) or Excel auditing (workbook-doctor).
---

# Deck Architect (the deck's argument, before its slides)

You are at the **front of the M2 PowerPoint path**. The rule, from Nate and from review-1 finding 11: **structure before slides.** AI renders a beautiful deck in seconds; polish then *hides* a weak argument. So you build the argument first — and you teach the harness to read any template's brand rules — and only then does a render step run.

**Read these first — do not redefine anything they own:**
- `../trust-review/references/conventions.md` — the contract hub. `source_status`, `review_status` (`verified` / `needs-review` / `unsupported` / `conflicting`), `severity`, `readiness`, the **provenance record** (§2), the **speaker-note / evidence template** (§2.1), the **command I/O contract** + the `{ok, ok-with-warnings, blocked, failed}` status enum. Use these values **verbatim**.
- This skill **consumes the truth layer** — the `office-source-packet` skill's `source-packet.json` (+ `file-spec.json`). It never re-reads raw sources; it inherits the S-IDs, statuses, and conflict log intact.

Two phases, matching plan items **DA-01** (architecture + spine gate) and **DA-02** (brand interpreter). They are independent — DA-01 structures content, DA-02 reads a template — and a later render step (M2, EM/HR) consumes both.

---

## Phase 1 — Deck Architecture + narrative-spine GATE (DA-01)

**Script:** `scripts/deck_architecture.py`
**In:** `source-packet.json` (required) + `file-spec.json` (optional; its deck slide-map is the structural skeleton).
**Out:** `.trust/<run_id>/deck-architecture.{json,md}`.

```
python deck_architecture.py --source-packet source-packet.json \
    --file-spec file-spec.json [--audience board-only] [--decision "..."] [--approve]
```

It emits two things and one gate:

1. **The narrative spine** — `audience` · `decision` (the single ask) · `what_they_know` · `what_they_must_believe` · a one-sentence `spine` · the `primary_misread_risk`. The spine is a **judgement proposal**, emitted `review_status: needs-review` — a human owns the argument; the harness structures it. The **misread risk is computed from the packet** (an estimate that could read as an actual, a `human-decision` conflict's display value, an unsupported claim still slotted to render).

2. **The slide map** — one row per slide, the DA-01 schema exactly:
   `slide · claim_headline · role · source_ids · workbook_refs · chart · assumptions · open_questions · note_requirement · review_status`.
   - **Claims, not topics.** The headline is the claim **sentence** (a full assertion — "Margin recovered to 14% on lower clinker cost"), never a topic label ("Margin"). A file-spec topic label is **rewritten** to the claim sentence and the original kept in `source_headline` so the transform is auditable.
   - **Every number names a source.** `source_ids` come from the claim's provenance; the `note_requirement` reserves the §2.1 evidence speaker note (Claim / Source IDs / Calculation / Assumptions / Needs review) the M2 evidence map fills in.

3. **The GATE** — a **hard stop**. Status `blocked` until **both**: (a) every claim names a source **or** is explicitly flagged `unsupported` (the invariant), and (b) a named human **approves** the spine + slide map (`--approve`). An unsourced *and* unflagged number, or an `unsupported` claim still slotted to render, is a `must-fix` **render-blocker**: the architecture cannot reach a renderable state. Sections, titles, and asks that assert no figure need no source.

**Acceptance (binary):** every slide-map row asserting a claim either names ≥1 source or is flagged `unsupported`; headlines are sentences; the architecture is `blocked` until approved; a planted unsourced number cannot render.

---

## Phase 2 — Brand-System interpreter (DA-02)

**Script:** `scripts/brand_interpreter.py`
**In:** any `.pptx` template path. **Out:** `.trust/<run_id>/brand-rules.{json,md}`.

```
python brand_interpreter.py --template template.pptx [--brand-doc brand-system.md]
```

This reads **any template the user gives**, including ones we do not pre-own, instead of relying on a hand-authored spec for one company. It extracts the practical render rules: **masters/layouts** (names + placeholder types, slide size), **typography** (theme fonts, sizes seen), **palette + usage** (theme `clrScheme` + inferred roles + chart sequence), **chart/table style**, **logo/legal/footer** (master images, footer/slide-number/date placeholders), **density**, **banned moves**, **accessibility** (computed WCAG contrast of theme pairs, min font size).

**Never invent.** Every rule carries a `determination`:
- `extracted` — read straight from the file's XML. Authoritative.
- `inferred` — derived by a **stated heuristic** (carried with the rule, so a human can overrule).
- `unclear` — **not in the binary** (logo usage rules, legal/footer wording, banned moves, the *intended* role of each colour, semantic status colours, table style). Emitted as `unclear` **with a reason** and value `null` — never guessed.

A template named `brand_template.pptx` that ships the stock Office theme is reported as the stock theme; the interpreter reports **what is in the file, not what the filename implies.** Brand is an *input constraint* on rendering — the `unclear` items are exactly what the render step must get from the brand owner.

**Acceptance (binary):** ≥1 rule `extracted`, ≥1 `unclear`; **no `unclear` rule carries a fabricated value**; the canonical known-unknowns (banned moves, footer/legal text) are `unclear`.

---

## Phase 3 — Render-from-spec (DA-03)

**Script:** `scripts/render_deck.py`
**In:** `deck-architecture.json` (APPROVED, required) + `brand-rules.json` (DA-02, optional) + `evidence-map.json` (EM-02/EM-04, optional).
**Out:** `.trust/<run_id>/deck.pptx` + `render-report.{json,md}`.

```
python render_deck.py --architecture deck-architecture.json \
    --brand-rules brand-rules.json --evidence-map evidence-map.json
```

The render step is the **last place the deck could quietly invent its own story**, so it is **constrained**, and it hands real brand polish to the public `pptx` skill, with the rules extracted from the user's template. It does three things and **reports the rest**:

1. **Preserves the claim headlines** — every slide title is the architecture's `claim_headline` **verbatim**. The renderer never re-headlines or pulls a number itself.
2. **Carries the evidence into speaker notes** — each slide gets the §2.1 note (Claim / Source IDs / Calculation / Assumptions / Needs review) from the evidence map, so the proof survives the forward.
3. **Keeps charts traceable** — a chart whose numbers cannot be traced to a source/cell is stamped `source: UNTRACEABLE` **and** flagged a `must-fix` deviation.

…and the **render report** is a slide-by-slide record of every place the render could not follow the spec — an `unclear` brand rule that forced a documented fall-back, a missing evidence note, an untraceable chart, an unsupported claim still slotted. It **inherits the DA-01 gate**: an unapproved or render-blocked architecture is **not** rendered (the report returns `blocked`).

**Acceptance (binary):** an unapproved architecture is not rendered; an approved one renders with titles == claim headlines verbatim and the §2.1 note in each slide's notes; an untraceable chart and an `unclear` brand rule are both reported, never silently papered over.

---

## Phase 4 — Brand + accuracy validator (DA-04)

**Script:** `scripts/deck_validator.py`
**In:** any subset of `deck-architecture.json` · `evidence-map.json` · `render-report.json` · `brand-rules.json`.
**Out:** `.trust/<run_id>/deck-validation.{json,md}`, and (in the loop) the standard `review-result.json`.

```
python deck_validator.py --architecture deck-architecture.json \
    --evidence-map evidence-map.json --render-report render-report.json --brand-rules brand-rules.json
```

This is **not a second review engine** — it is the M1 `trust-review` loop run with a deck-specialised **enumerator**. It emits the exact `review_contract` edit-list shape (`{id, location, type, severity, evidence, suggested_repair, human_review, status}`), so `deck_reviewer(...)` drops straight into `loop_controller.run_loop(reviewer=…, surface="ppt")`. It runs nine deck-specific checks against the truth layer — **claim specificity · source attribution · numbers match · charts traceable · assumptions labelled · brand/template compliance · hierarchy/readability · legal/sensitivity · narrative logic** — each mapped to a Pretty-But-Wrong class (TR-04) and keyed to a conventions §1.2 severity. **Legal/sensitivity issues are surfaced explicitly** (`human_review: true`).

**Acceptance (binary):** the edit list validates against `review_contract`; an unsupported claim, an untraceable chart, and a sensitivity-flagged slide are all caught; run inside the loop, a deck whose only must-fix is an untraceable chart converges to `ready` once a builder traces it.

---

## How this fits the pipeline
`office-source-packet` (packet + file-spec, ends at a human gate) → **Deck Architect DA-01/DA-02** (architecture gate + brand rules) → M2 evidence map (EM-02/EM-04) populates the per-claim provenance/speaker notes → **Deck Architect DA-03** renders the deck from the approved spec → **DA-04** runs as the deck variant inside the two-context `trust-review` loop + human gate. A `blocked` architecture does **not** render and a deck with an open must-fix does **not** travel. The fixtures and worked runs live under `fixtures/fixture_deck_architect.py`, `fixtures/fixture_render_validate.py`, and `.trust/`.
