---
name: trust-run
description: "One-command orchestrator for the whole Office Trust Layer. Triggers on \"get this ready for the steering group\", \"is this board-ready\", \"run the full trust review\", \"trust-run\", \"prep this board pack\", \"review this memo and deck end to end\", \"do the whole trust workflow\". Give it a memo plus its deck or related files headed to a board, steering group, CEOs, investors or a client; it runs every step in order and returns ONE readiness verdict plus a single prioritised fix-list, reconciling memo and deck against the SAME sources."
disable-model-invocation: true
---

# Trust Run — the one-command Office Trust Layer orchestrator

You are the conductor. The user has one or more related artifacts (typically a **memo + its presentation**) that must be trusted before they go in front of people. Your job is to run the **whole** Trust Layer chain **in the correct order**, invoking the underlying skills as you go, and to return a **single consolidated readiness verdict** — so the user never has to remember the sequence.

**You ORCHESTRATE; you run no mechanical scripts yourself.** Every gate (`manifest_gate`, the egress sanitiser, recalc, verification) executes **inside the delegated skill that owns it**, against that skill's own bundled code — you never invoke those scripts from this skill's directory. **This skill therefore ships no copy of them.** They have ONE canonical home, `skills/trust-review/references/scripts/`; every `references/scripts/…` path in this skill's `references/` docs (which state the shared contract, not this skill's own code) resolves there. A second copy in AUTHORED SOURCE is a drift bug and fails `skills/trust-review/scripts/test_canonical_adapters.py`. That test judges the authored set only — `_plans/**` is excluded as research and prior art, and byte-identical copies of these files do exist there (verified in the 2026-08-20 run). Snapshots under `_plans/**` are not drift and are not imported by anything. Each numbered step is delegated to the skill named in **bold**.

**Canonical definitions — use them verbatim.** Severity, review status, the readiness enum (§1.4/§1.5), the provenance record (§2), the verification + egress rules (§2.2–§2.5), the waiver rule (§4), the frozen release thresholds (§5), and the v3 contract — the degraded-status **caps** (§7.1), **reviewer-independence** by travel class (§7.2), the human-gate **digest invariants** (§7.6), the two-phase **vault write-back** (§7.7), and **schema_version** migration (§7.8) — are all defined in `references/conventions.md`. Do not redefine them here; read those values and use them exactly.

---

## Phase 0 — Memory (before preflight)

Read `$TRUST_MEMORY_DIR/trust-run.md` when that variable is set, else `_skill_memory/trust-run.md` in the workspace folder of the current vault when it exists, else skip silently. ACTIVE entries are **orchestration decisions the owner has corrected** — steps the owner tells the run to skip or add for a pack type, intake assumptions the owner reverses, caps the owner routinely signs off. Use them to sharpen the Step 0b intake proposals; the pipeline order, the gates, and the §7.1 cap ordering never move via memory.

## Step 0 — Preflight + intake (one message, then run)

**0a. Dependency preflight (HARD GATE — a skill cannot detect another skill, so check explicitly).** This orchestrator delegates to eight skills: **travel-class-router, office-source-packet, office-update, document-doctor, deck-architect, evidence-map, workbook-doctor, trust-review.** Before doing anything, confirm each is actually available in this session (it is in the skills list / installed). **If ANY is missing, STOP** and tell the user exactly which ones are absent and that you cannot run end-to-end — **do NOT improvise a missing step from memory** (a fabricated step is the worst failure for a trust tool). The seven sub-skills (everything above except trust-review) install with the same Gearbox module as this one; offer to install the missing bundles, then resume.

**0b. Intake — ask in ONE short message, don't interrogate:**
1. **The files** — the memo and the presentation paths (+ any other related artifacts). Note each file's surface/format (`docx` memo, `html` or `pptx` deck, `xlsx` model).
2. **Audience / how far it travels** — if the user said board, steering group, CEOs, investors, regulators, or client, the travel class is **Board / Regulatory** (the front door may raise it further, never lower it without a logged waiver). **Because Board/Regulatory requires an independent cross-vendor reviewer (see Step 6), also ask up front: is a cross-vendor reviewer available (Codex authenticated, or you'll paste into another LLM), or should I record a named-owner signoff?** — so the run does not land `blocked` by surprise.
3. **Sources** — read internal sources DIRECTLY from the user's own knowledge source (no upload, no egress) and get external facts from a web search provider. Do not guess which tools exist: run the resolve command with the tool names you can see in this session, `python3 skills/trust-review/references/scripts/adapter_registry.py --resolve --tools <comma-separated tool names>`, and use the providers it prints, in that order. With no shell, follow the order in `references/capability-inventory.md`, which lists provider-pack providers first. The generic chain for internal sources is a knowledge MCP supplied through a provider pack, then Obsidian, then the folder named by `TRUST_CORPUS_DIR`; for external facts it is the first available web search provider. Confirm both are connected; if one is absent the run **degrades** (see Operating rules) and says so. Also fold in any loose files (a model, an export).
4. **Is this a refresh of a prior version?** — if yes, Step 2 runs the refresh branch first.

Then run the rest without re-asking intake questions, **pausing only at the mandatory human gates** (a deck-architecture approval if a deck is actually (re)built, and the final human gate).

## Step 1 — Front door (**travel-class-router**)
Route **each** file. Record the declared class, the content-escalated effective class, and the routing; it writes the `travel_class` / `sensitivity` / `routing` manifest blocks and its own `manifest_gate` must pass before any model step. CEO/steering/board ⇒ expect **Board / Regulatory**.

## Step 2 — Refresh branch (**office-update**, ONLY if Step 0b.4 = yes)
On a refresh, run office-update **FIRST** — before building any packet. It loads the prior `.trust` run **read-only** (never mutates it, §7.8), diffs this period's claim-set against it by stable claim identity, classifies every claim unchanged / updated / new / dropped / superseded, **carries prior verdicts forward only on identity-stable unchanged claims**, and tombstones dropped ones. **Only the changed/new claims proceed to Steps 3–6**; everything unchanged keeps its carried verdict, and the new output stamps `schema_version` + `migration_notes`. (First-time pack: skip this step entirely.)

## Step 3 — Truth layer (**office-source-packet**)
Build **one** source packet for the whole pack (on a refresh, only for the changed/new claims from Step 2): every source gets a stable ID + status; every claim is split fact / assumption / estimate / interpretation / open-question; disagreements go in a conflict log, never averaged. Sources include notes from the user's internal knowledge source (internal chain) and anything corroborated by the external web search provider, plus loose files. **This single packet is shared by BOTH the memo and the deck** — that is what lets Step 5 reconcile them. If there is genuinely nothing to verify against, record `packet = structure-only` (claims can still be flagged unsupported, just not traced).

## Step 4 — Inspect each artifact, by surface and format (emit the claim audit)
Run the right inspector per artifact and **make sure each emits its claim-level output, not just a readiness verdict** (Step 5 consumes it):
- **Memo (`docx` / `pdf` / `md` / `html`)** and **a deck that is `html`** → **document-doctor**, per format (never one blurred "doc" pass): structure map, extraction-quality / OCR gate, the prose landmines (undated or stale numbers, claims with no source, internal contradictions, broken cross-references, tables shipped as images, unattributed quotes), and crucially the **claim audit (`claim-audit.json`)** + the per-file readiness verdict that Step 5 needs.
- **A deck that is `pptx`** → this is an **already-built** file, so do **NOT** send it to deck-architect's build-and-render gate. Extract its claim-set from the rendered slides via **office-update**'s UP-03 **surface adapter** (the per-surface claim extractor), producing the deck's claim/slide map. (Use **deck-architect** only if the user is *building* a new deck from the packet; use **workbook-doctor** for any `xlsx` model behind the deck.)
- **An `xlsx` model** behind either → **workbook-doctor** (recalc-gated; a `recalc-unverified` cap applies if no live recalc — carry it into Step 7).
Collect every finding and every claim audit / claim-set.

## Step 5 — Cross-document evidence map (**evidence-map**)
Build the evidence map for **the whole pack against the one Step-3 packet**, consuming the Step-4 outputs (the memo's `claim-audit.json`; the deck's extracted claim/slide map): tie each claim in the memo AND each claim in the deck to its backing source; mark anything untraceable **unsupported** (never invent a source); hold sub-threshold matches for approval (EM-04). **Reconcile the two documents** — flag any figure or claim that appears in both but disagrees (the classic "memo says €412m, deck says €410m"), and any number with no source. Emit the evidence notes: for `docx`, an `-evidenced.docx` (anchored Word comments + evidence appendix, prose untouched); for `html`/`md`, a sidecar + appended evidence section. Rank the gaps by risk and hand a `review_contract`-valid edit list to Step 6.

## Step 6 — Cross-vendor two-context hostile review + human gate (**trust-review**)
Run the Board/Regulatory depth: a fresh, independent reviewer enumerates every problem against the evidence and never edits; each round re-checks prior fixes AND does a full fresh pass, looping until **no must-fix is open and none was newly raised**.

**6a. Reviewer independence is gated by travel class (§7.2) — this is where most board runs silently fail, so handle it explicitly.** trust-review's *default* is builder = Opus, reviewer = Sonnet — **both the same model family**. On **Board / Regulatory**, conventions §7.2 + the §7.1 `same-family-reviewer` cap mean a same-family reviewer ⇒ **`blocked`**, lifting to `ready-with-limitations` ONLY with a named-owner signoff. To reach a clean verdict you need a genuinely **cross-vendor** reviewer. Resolve it in this order:
   1. **Codex reviewer (preferred, real cross-vendor).** If Codex is authenticated, run **`codex:adversarial-review`** (Codex / GPT — a different vendor from Claude) as the independent reviewer and record `reviewer_independence = cross-vendor`. This legitimately clears the §7.2 bar.
   2. **Manual cross-vendor degradation (when the Codex CLI is unavailable / unauthenticated / errors — never silently fall back to same-family).** Do **not** quietly downgrade and claim `ready`. Instead **write a portable review packet** to `.trust/<run_id>/cross-vendor-review-request.md` containing: a ready-to-paste **reviewer prompt** (the hostile-review instructions), and the **evidence bundle** it needs — the claim set, each claim's sources + provenance, the reconciliation flags, and the must-fix candidates. Tell the user plainly: *"Paste this into Codex / ChatGPT / Gemini / your preferred engine and bring back its response."* When the user returns the external review, **ingest it as a named-human-mediated cross-vendor review** (the user vouches for the vendor + the run — the §7.2 `named-human-SME` / cross-vendor path), record the reviewer + that it was human-mediated, and lift the cap accordingly. **Until that response comes back, the verdict stays capped** — `blocked` on Board/Regulatory (or `ready-with-limitations` if a valid named-owner signoff was recorded at Step 0b.2).
   3. **Named-owner signoff fallback.** If neither cross-vendor path is taken, a same-family review caps at `ready-with-limitations` **only** with a logged named-owner signoff (owner + decision + scope + expiry + signoff + rationale); otherwise it is `blocked`. State this; do not present it as plain `ready`.

**6b. Human gate.** The run ends at the **human gate** — trust-review's `human_gate_digest` (§7.6): a one-screen digest produced from the **FULL** finding set (`readiness_from_full_set`), ranked by materiality × travel class, near-duplicates collapsed **for display only** (the full set still governs), the **hidden must-fix tail counted and shown**, strictly **read-only** (no digest action auto-resolves a finding), and requiring the owner's **full-set acknowledgement** before the verdict may be acted on.

## Step 7 — Return ONE verdict (the only thing the user must read)
Write `trust-run-summary.{json,md}` to the run dir and report back, in plain language:
- **The headline verdict for the pack**, drawn from the **full §1.4 + §7.1 enum** — `ready` / `ready-with-limitations` / **`blocked`** / `not-ready`, plus the forwarding qualifier **`not-ready-for-forwarding`** — taken from the **strictest** file and the **strictest active cap** (the §7.1 ordering: `not-ready-for-forwarding` < `blocked` < `ready-with-limitations`; a single weak document or one active cap is never hidden behind an average). Name every active cap and the `reviewer_independence` value in the summary.
- **The prioritised fix-list** — must-fix first, each naming the file, the locator, and the source gap, so the user acts without opening six reports.
- **The memo↔deck reconciliation result** — agree, or the exact disagreements.
- **Where the proof lives** — the `-evidenced.docx`, the evidence map, the human-gate digest, and (if used) the `cross-vendor-review-request.md`, all under `.trust/<run_id>/`.
- **What was NOT checked / capped** — degraded coverage stated honestly, never silently dropped (see below).

## Step 7.5 — Self-eval (E-checks) before the verdict is reported

After `trust-run-summary.json` is written — before reporting to the user —
run the four binary checks in `references/_evals.md`:

```bash
python3 skills/trust-run/scripts/eval_checks.py --run-dir .trust/<run_id>
```

E1 every claimed step left its artefact (a claimed step with no artefact is
a **fabricated step** — Step 0a's worst failure) · E2 exactly ONE verdict
from the enum, never softer than the strictest component
(`action_required` on fail — re-derive from the components and restate;
never patch the summary to match the components) · E3 one prioritised
fix-list, must-fix first, each naming file+locator · E4
reviewer_independence recorded and the Board/Regulatory same-family cap
honored.

Max 2 repair rounds on record defects, then ACTION REQUIRED. Append the 🧪
block to the run summary:

```markdown
## 🧪 Self-eval — E-checks (4/4 passed, 0 repair rounds)

- E1 PASS — 6/6 claimed steps left artefacts (refresh not claimed)
- E2 PASS — one verdict "blocked"; strictest component blocked — consistent
- E3 PASS — 5-item fix-list, 2 must-fix first, all name file+locator
- E4 PASS — reviewer_independence=cross-vendor recorded
```

**Memory append (event-driven).** If the owner corrected an orchestration
decision this run: append 2–3 sentences to
the memory file above (newest first, cap 20) with pack
type + direction. Two recurrences → graduate per the contract §3.4.

## Operating rules
- **Degrade, don't fail — with the CORRECT vocabulary.** A missing backend never blocks the run and never fabricates a verdict; it lowers *stated* coverage. **The preferred internal provider absent** ⇒ the chain falls to the next one (a knowledge MCP from a provider pack, Obsidian, a memory graph, the `TRUST_CORPUS_DIR` folder or a local-vault grep), then packet/session fallback, with internal coverage marked degraded at whichever rung answered. **No web search provider** ⇒ internal-only; eligible external claims recorded **`not-checked-budget`** (the §2.4 status — not a bare `needs-review` verdict) and a readiness cap applied. **Both absent** ⇒ the full structural + provenance review still runs, verification marked degraded. **Codex absent** ⇒ the Step 6a manual cross-vendor packet (never a silent same-family pass).
- **Egress is gated (matters for board material).** Internal Obsidian reads never leave the machine. Before anything reaches the web search provider, the web-egress guard sanitises it — confidential/restricted **claim text is never sent out**, only a sanitised query; a restricted Board/Regulatory artifact defaults to `skipped-egress` unless a named-owner waiver with signoff is recorded.
- **Never relax the gate.** Readiness is computed from the FULL finding set; a waived or capped item never reads as plain `ready`. Thresholds (§5) are frozen.
- **Don't poison the vault (§7.7).** Any provenance written back to the user's knowledge base lands in a **quarantine** zone and is promoted to a citable note **only on named human approval** — a derived note is never treated as a primary fact until promoted. Stamp `schema_version` on the run dir (§7.8) so next period's refresh can read it.
- **One run dir, one verdict.** Everything for this pack lands in a single `.trust/<run_id>/`; the user gets one consolidated answer + one fix-list.
- **Honest automation.** This runs the chain in order without re-asking intake questions, but it is **not** unattended — it pauses at the mandatory human gates (deck-architecture approval if a deck is built; the final human gate) and at the manual cross-vendor step when Codex is unavailable.

This skill is the front-to-back automation of `preflight → travel-class-router → (office-update if a refresh) → office-source-packet → document-doctor / office-update-surface-adapter / workbook-doctor → evidence-map → trust-review (cross-vendor) → human gate → one verdict`. Invoke it whenever the user wants the whole thing done in the right order without naming the steps.
