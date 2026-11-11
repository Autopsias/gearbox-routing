---
name: trust-review
description: "The Office Trust Layer's two-context build-then-review loop. Use after the Workbook Doctor verdict or the evidence map, for 'review this before the board', 'run the hostile review', or 'is this safe to send'. A hostile reviewer in a fresh independent context (a different model than the builder) enumerates problems against the provenance and never edits; each round re-verifies prior fixes AND does a fresh full pass, and stops only when no must-fix is open or newly raised. Not for building or editing the file \u2014 the reviewer only enumerates."
---

# Trust Review (the two-context build↔review loop)

This is the heart of the plugin. The model can already make a workbook or deck **look** finished; it cannot tell you whether the numbers survive a hostile read. Trust Review runs a **reviewer in a fresh context** whose only job is to **enumerate problems** against the provenance, then drives a **builder↔reviewer loop** that does not stop until the artifact is verified-clean or is explicitly returned **not-ready**.

Two ideas make it work, and both come from the review hardening (review-1):

1. **Two contexts, not two vendors.** A model reviewing its own family of output has a blind spot. The reviewer runs in a **fresh context** with **no access to the builder's reasoning**, and — by default — on a **different model** (builder = Opus, reviewer = Sonnet). It **enumerates, never edits**.
2. **A fix can break something else.** "No new must-fix" is **not** "all must-fix resolved." So every round runs **both** a verify-prior pass **and** a fresh full pass; a regression a repair introduces is caught instead of shipped.

**Read these first — do not redefine anything they own:**

- `references/conventions.md` — the contract hub. Use **verbatim**: `severity` (`must-fix`/`should-fix`/`polish`), `review_status`, `issue_lifecycle` (`open`/`fixed-claimed`/`verified-fixed`/`waived-by-human`/`still-open`), `readiness` (`not-ready`/`ready-with-limitations`/`ready`), the **command I/O contract**, the `{ok, ok-with-warnings, blocked, failed}` status enum, and the **§4 waiver rule**. §1.5 defines the loop stop condition; §2 is the provenance record the reviewer judges against.
- `references/sensitivity-routing-policy.md` + `references/manifest.schema.json` — the per-run manifest the loop writes (routing + rounds). The reviewer sees redacted excerpts per the run's sensitivity, never whole sensitive files by default.
- `references/scripts/manifest_gate.py` — **the precondition.** The loop calls it before round 1 and returns `blocked` (not `failed`) if sensitivity + routing + travel-class are not recorded.

**Phase 0 — memory (BUILDER side only — never the reviewer):** read `$TRUST_MEMORY_DIR/trust-review.md` when that variable is set, else `_skill_memory/trust-review.md` in the workspace folder of the current vault when it exists, else skip silently. ACTIVE entries are **reviewer finding types the owner has deemed noise**. The guard that matters: **memory never reaches the reviewer context** — feeding the reviewer a "stop flagging these" list is exactly the contamination the two-context split exists to prevent. Apply entries only when the builder/orchestrator triages findings: contest a known-noise type through the normal §4 waiver path or annotate it in the human-gate digest, visibly, with the memory note as rationale. The reviewer keeps flagging; the triage stops re-litigating.

The deterministic engineering lives in two scripts (plus the self-eval runner); this SKILL.md tells you how to run them and how the session drives the live loop.

```
skills/trust-review/scripts/
  review_contract.py   # TR-01 — enumerate-only directive, edit-list schema + validator,
                       #         independence manifest (per round), verdict->edits enumeration
                       # TR-04 — the Pretty-But-Wrong checklist (PRETTY_BUT_WRONG + checklist_text)
                       # TR-06 — the human-gate record (waiver validation + persisted record)
  loop_controller.py   # TR-02 — issue lifecycle + dual-pass SAFE loop + stop/cap + round logs
                       # TR-03 — builder/reviewer model split, recorded in the manifest
                       # TR-06 — applies the human gate (resolutions) -> manifest.human_gate
  language_pass.py     # TR-05 — the language pass (voice CHECK) over the converged review prose
  eval_checks.py       # self-eval — ONE E-check: per-round manifest completeness (provable independence)
```

---

## What it consumes (the f-02 contract)

The reviewer judges the artifact against its **provenance record** (`conventions.md` §2). In **M1** that provenance is the **Workbook Doctor verdict** (`workbook-verdict.json`) plus the **source packet** (`source-packet.json`); in **M2** it additionally includes the **evidence map**. On the **document surface** (v2, DD-06) the provenance is the **Document Doctor verdict** (`document-verdict.json`) + the **doc evidence map** (`evidence-map.json` / `evidence-gaps.json` from DD-05) + the source packet, and the loop runs with `surface="doc"` and the document `fmt`. The reviewer is fed **artifact + provenance only** — never the builder's chain-of-thought.

## Inputs (declared up front, per the I/O contract)
- `artifact` — the workbook/deck under review.
- `provenance` — `workbook-verdict.json` (M1) or `evidence-map.json` (M2), plus `source-packet.json`.
- `manifest` — a stub already carrying `travel_class` + `sensitivity` (from the router / source packet). The loop fills in `routing` + `rounds`.
- `run_id` — `<ISO-timestamp>_<short-slug>`; outputs go to `.trust/<run_id>/`.

## Outputs (per the I/O contract)
- `.trust/<run_id>/review-edits.{json,md}` — the round-1 enumeration (the edit list).
- `.trust/<run_id>/review-round-<N>.{json,md}` — the per-round delta (verify-prior + fresh-full + the stop decision).
- `.trust/<run_id>/review-result.{json,md}` — the final readiness verdict + per-finding lifecycle + `must_own`.
- `.trust/<run_id>/manifest.json` — routing (the model split) + the per-round independence records.

---

## TR-01 — the reviewer contract + the independence manifest

**The enumerate-only directive** (`review_contract.REVIEWER_DIRECTIVE`, assembled by `build_reviewer_prompt`) is the exact text the reviewer subagent receives. It is deterministic (so it hashes stably) and it is fed only the artifact label + a provenance summary. It forbids editing, rewriting, or asking for the builder's reasoning.

**The edit-list schema** — every reviewer finding is exactly:

```json
{"id":"R-01","location":"Board!B5","type":"unsupported-number","severity":"must-fix",
 "evidence":"claim C6 has no source (source packet)","suggested_repair":"source it or drop it",
 "human_review":true,"status":"open"}
```

`severity` and `status` come **verbatim** from the conventions enums; `validate_edit_list` **rejects** an entry that omits a required field or uses an off-enum value, so a malformed reviewer cannot smuggle in a finding the loop can't reason about.

**The independence manifest** (`independence_round`) records, per reviewer pass: the **model name + version**, the **timestamp**, and the **sha256** of the artifact, the provenance record, the source packet, and the reviewer prompt itself. Independence is then **provable, not asserted**: re-hash the same inputs and the record reproduces; change the artifact and `input_artifact_sha256` changes. The round entry conforms to `manifest.schema.json` `rounds[]`.

---

## TR-02 — the SAFE convergence loop

`loop_controller.run_loop(...)` drives the loop. Each round, for the current artifact state:

```
round k:
  [fresh full pass]   re-scan the whole artifact  → all findings now visible
  [verify-prior pass] for each finding the builder claimed fixed:
                          gone in the fresh look → verified-fixed
                          still present          → back to open
  [new findings]      any finding the fresh pass sees that wasn't tracked → new (open)
  stop IF  no must-fix is open/fixed-claimed/still-open  AND  no new must-fix this round
  else     hand the open findings back to the builder
  cap hit with anything open → mark survivors still-open → verdict not-ready
```

The two stop rules are **both computed and logged** every round:
- `safe_stop` — no unresolved must-fix **and** no new must-fix (the rule we use).
- `naive_stop` — "everything I was already tracking is fixed" (verify-only; **ignores** the fresh pass).

When they **diverge** (`regression_caught: true`), a naive controller would have **stopped and shipped** — the SAFE loop saw a new must-fix the repair introduced and **continued**. That divergence is the acceptance gate (`fixtures/fixture_trust_review.py`).

**Readiness** is computed per `conventions.md` §1.4 / §4: any must-fix `open`/`fixed-claimed`/`still-open` → `not-ready`; a `waived-by-human` must-fix (valid §4 waiver record; Board/Regulatory additionally needs signoff) → `ready-with-limitations`; all must-fix `verified-fixed` → `ready`. Always paired with the explicit `must_own` list.

**The human gate** is an input: `resolutions` moves a finding to `verified-fixed` or `waived-by-human` (the §4 record — owner/decision/due-date/signoff/rationale). See **TR-06** below.

---

## TR-04 — the Pretty-But-Wrong checklist

The reviewer's **standing skeptic's reference** (`review_contract.PRETTY_BUT_WRONG`, rendered by `checklist_text(surface)`): the ten defect classes an AI ships in a file that **looks** finished — claims without source, numbers without date, untraceable charts, inconsistent / roll-forward formula faults, hardcodes, assumptions-as-facts, stale dates, brand drift, low-contrast / overcrowded slides, broken narrative logic. Each is tagged a **default severity** (verbatim from the conventions enum) and a **surface** (`excel` / `ppt` / `doc` / `both`). It is wired into the reviewer via `build_reviewer_prompt(..., checklist=...)` and the loop carries it in **every round's hashed reviewer prompt** by default (`run_loop(..., checklist=, surface=)`). Full table + rationale: `references/pretty_but_wrong_checklist.md` (the code is canonical). The default severities give the M1 benchmark (S08) a known key to score severity accuracy against.

The **document surface** adds the seven `doc` rows from `conventions.md` §1.7 — `DOC-PBW-01` extraction-loss, `DOC-PBW-02` untraceable-figure-or-table, `DOC-PBW-03` quote-or-citation-misattribution, `DOC-PBW-04` contradiction-across-sections, `DOC-PBW-05` hidden-or-agent-only-content, `DOC-PBW-06` verification-contradiction-unresolved, `DOC-PBW-07` per-format-limit-hidden — each at its §1.2 severity. `checklist_text("doc")` returns these **plus** the `both`-surface v1 items (which already apply to a document and are not re-listed); the `excel`/`ppt` slices are unchanged.

## TR-06 — the human-gate record (waiver semantics, §4)

The last word is a human's. The gate is an **input** (`run_loop(..., resolutions=...)`) and a **persisted record** (`manifest.human_gate`). Each resolution either verifies a fix or **waives** a must-fix. A waiver is validated by `review_contract.validate_waiver` against **conventions §4**:

- **No `ready` while any must-fix is `open`/`fixed-claimed`/`still-open`** (§4.1).
- **Any waived must-fix caps the verdict at `ready-with-limitations`** — never plain `ready`. The waiver requires **owner, decision, due date, signoff, rationale**; all five are persisted, and the **named limitation text travels with the file** (§4.2, in `review-result.md` and `manifest.human_gate.limitations`).
- **A Board/Regulatory waiver additionally requires named-owner signoff** (`signoff == "approved"`); without it the finding stays blocking and the verdict stays `not-ready` (§4.3).

An invalid waiver does **not** move its finding — it's recorded with `applied: false` + the reason. The three S08-bound fixtures (`fixtures/fixture_human_gate.py`, pack → `fixtures/human_gate_fixtures.json`) prove (a) an open must-fix blocks `ready`, (b) a valid waiver caps at `ready-with-limitations`, (c) a Board/Regulatory waiver needs signoff — and that a **planted unsourced claim cannot reach `ready` by any path.**

## TR-05 — the language pass (voice CHECK)

**Accuracy first, language last.** Once the loop converges (readiness `ready` / `ready-with-limitations`), `language_pass.language_pass(result, ...)` runs a **CHECK** over the review prose (readiness reason, `must_own`, limitations, each finding's evidence + suggested-repair) for **AI tells** (a faithful subset of the `voice` skill §4 banned list). It is **gated**: `skipped` while `not-ready` — accuracy is never traded for prose. If tells are found it **offers a voice REWRITE** (run the `voice` skill in REWRITE mode in-session); it never rewrites itself. Emits `.trust/<run>/language-pass.{json,md}`.

---

## TR-03 — the model split (Agent override)

Builder and reviewer run on **different models** via the Agent `model` override. The default is **builder = `opus`, reviewer = `sonnet`** (Agent aliases — Opus 5.5 and Sonnet 5 as of 2026-09-22); pass `reviewer_model=`/`builder_model=` to change it. The pair is recorded in `manifest.routing.{builder_model,reviewer_model}` (name + version) and per round in `manifest.rounds[].model`. The live mechanism is proven in `fixtures/_trust_review_modelsplit_proof.txt` (a real Sonnet reviewer subagent enumerating cold) and `fixtures/_agent_modelsplit_proof.txt` (the S01 probe).

**Cost trade-off.** Two contexts + a second model cost more than a single self-review. The reviewer is the cheaper model (Sonnet) and sees only redacted excerpts; the expensive builder context is reused. For `internal` travel class a single-model split (or `local_only`) is acceptable; **Board/Regulatory should always use the two-model split** — the blind-spot risk dominates the token cost.

### How the session drives the live loop

The scripts own the **contract + bookkeeping + verdict**; the **session (you) runs the live reviewer and builder via the Agent tool**:

1. **Reviewer pass** — spawn the reviewer in a fresh context on the reviewer model:
   `Agent(subagent_type="general-purpose", model="sonnet", prompt=build_reviewer_prompt(provenance_summary, artifact_label))`. Parse its JSON edit list; validate with `validate_edit_list`; feed it to `run_loop` as the round's scan.
2. **Builder pass** — in the builder context (Opus), apply the repairs, then report which finding signatures you claim fixed. The loop marks them `fixed-claimed` (never trusted until the next verify-prior pass).
3. The controller records the independence manifest, runs the dual passes, and decides stop / continue / cap.

For an **offline / deterministic** run (the worked run + fixtures), `review_contract.edits_from_verdict` is the reference reviewer (it enumerates straight from the Workbook Doctor verdict) and an injected builder stands in for the live one.

```bash
# enumerate-only worked run straight from a Workbook Doctor verdict
python skills/trust-review/scripts/loop_controller.py \
    --verdict .trust/<run>/workbook-verdict.json \
    --out .trust/<run> --run-id <run> --max-rounds 5
```

---

## DD-06 — the document surface (`surface="doc"`)

The two-context loop is **surface-agnostic** — the same `run_loop` that reviews a workbook (M1) or a deck (M2) reviews a **document** (v2). The doc branch is purely additive:

- **Carry the format.** `run_loop(..., surface="doc", fmt="docx"|"pdf"|"md"|"html")` carries the f2-01 `format` into the **(hashed) reviewer prompt** via `build_reviewer_prompt(..., surface=, fmt=)`, and records `surface` + `format` on every round and the result. A reviewer is told *which* format it is judging and reminded that a per-format limit must be **reported per format, never folded into one "doc works" verdict** (review finding #6 / `DOC-PBW-07`). The v1 (`excel`/`ppt`) prompt is byte-identical — the format block appears **only** when a `fmt` is supplied.
- **Provenance the reviewer reads.** the **Document Doctor verdict** (`document-verdict.json`, DD-04 — its `repair_plan` enumerates straight to the edit list via `edits_from_verdict`) + the **doc evidence map / gaps** (DD-05, whose `evidence-gaps.json` is already a `validate_edit_list`-valid edit list).
- **Everything else is unchanged.** the dual-pass **SAFE stop** (verify-prior + fresh-full; a repair-introduced regression is caught where a naive rule ships), the **Opus/Sonnet model split**, the **per-round independence manifest** (hashes are provable), the **§4 waiver / human gate**, and the **TR-05 language pass** all work identically — accuracy first, the human owns the last word, and a `not-ready` document does not travel.

Proven in `fixtures/fixture_doc_trust_review.py`: `surface="doc"` carries the format into the hashed prompt, the doc Pretty-But-Wrong rows are present, the loop catches a planted document regression a naive rule would ship and then converges, and a Board/Regulatory waiver caps the verdict at `ready-with-limitations`.

---

## AV-01 — the human-gate DIGEST (`human_gate_digest.py`, conventions §7.6)

A one-screen digest is an *interface* improvement; left unguarded it becomes a way to rubber-stamp the unranked tail. `human_gate_digest.build_digest(review_result, evidence_gaps=…, travel_class=…, top_n=5, owner_ack=…)` reads a `run_loop` result (and, optionally, an evidence-map gap list) and produces a **ranked** digest — materiality × travel class — with near-duplicates **collapsed for DISPLAY only** (a `+N similar` on the representative; the collapsed members stay in the set). Four invariants are non-negotiable and recorded:

- **`readiness_from_full_set`** — readiness is derived from the **full** finding set, never the displayed top set.
- **`no_digest_action_auto_resolves`** — the digest is strictly **read-only**; it mutates no finding, so no action here resolves/fixes/waives anything.
- **`hidden_tail_visible`** — the count of must-fix **not shown** (`hidden_tail_count`) is always surfaced and enumerable.
- **`tail_acknowledged`** — a valid **full-set** `owner_ack` is required before the digest's verdict may be acted on; a top-set-only ack does not count.

A digest that cannot satisfy all four returns the safe default `not-ready` (`digest_verdict`), never a prettied-up `ready`. `write_digest(d, out_dir)` emits a **themed one-screen** `human-gate-digest.{json,md,html}` with per-finding `.trust` deep links. Proven by `fixtures/fixture_human_gate_digest.py` (a 199-finding input ⇒ 5 ranked + deduped shown, 27 hidden tail counted, readiness still reflects all 32 must-fix, the ack gate real).

## AV-02 — two-phase vault WRITE-BACK (`vault_writeback.py`, conventions §7.7)

Writing a finished run's provenance back into the user's corpus is how the layer compounds — but a naive auto-write **poisons** it (next quarter cites the Trust Layer's own artifact as a primary fact). So `vault_writeback.two_phase_vault_write(run_result, out_dir=…, travel_class=…, vault_path=…)` is **two-phase**:

- **Phase 1 — quarantine.** A derived note lands in a **quarantine zone** stamped `source_authority=derived-provenance`, `schema_version`, `supersedes`, `expiry`, a deterministic `run_hash`, a `rollback_pointer` into `.trust/<run>/`, and a `dedupe_key` over artifact/run/claim IDs (`approved_by/at = null`). Resolved **through the f2-04 registry** `vault_write` op (provider-pack providers → Obsidian → memory → local-vault → the always-on `.trust` quarantine sidecar terminal); no backend ⇒ a **stated** sidecar, never a silent corpus write. The note is classified `internal` (minimal excerpts + hashes, internal-as-sensitive). Write-back is **opt-in by travel class**.
- **Phase 2 — promotion.** `promote(record, approver=…)` requires a **named human**; without one the note stays quarantined and non-citable. Only promotion moves it to the citable zone (`phase=promoted`, approver + timestamp).

The internal-corpus verifier (`internal_corpus_adapter.py`) treats a **quarantined** derived note as **audit metadata, not a primary fact** — it will not cite it as corroboration until promoted (`is_quarantined_derived` / `is_citable`). Proven by `fixtures/fixture_vault_writeback.py`.

## Router → trust-review handoff

The travel-class router (`commands/travel-class-router.md`) escalates **Board/Regulatory** files to the **full workflow + two-context loop + human gate**. After the Workbook Doctor produces its verdict, the router invokes `trust-review` with `artifact + workbook-verdict.json + source-packet.json` and the manifest stub (travel class + sensitivity already recorded). `trust-review` calls the gate first, runs the loop, and returns `readiness` + `must_own`. A `not-ready` result must not travel.

## Acceptance gate (this skill)
`fixtures/fixture_trust_review.py` builds a synthetic artifact whose **repair introduces a regression** and proves the loop **catches it** (the SAFE rule continues where the naive rule would ship), then converges to `ready`; a regression-every-round scenario hits the cap → `still-open` → `not-ready`. It also asserts the edit-list schema is enforced, the directive is enumerate-only and carries no builder reasoning, the independence-manifest hashes are provable, the Opus/Sonnet split is recorded and overridable, and the S02 gate blocks a loop with no sensitivity/routing. S07 adds three more fixtures — `fixture_checklist.py` (TR-04), `fixture_human_gate.py` (TR-06, pack handed to S08), `fixture_language_pass.py` (TR-05). Wired into `fixtures/run_fixtures.sh` (suite **22/0 GATE PASS**).

## Self-eval (E-check) — provable independence, after the loop

The loop is its own eval — the dual-pass SAFE rule, the model split, and the
manifest gate already verify the review. The one property nothing re-checks
afterwards is the claim that **independence is provable from the written
manifest**. So this skill carries exactly ONE E-check (`references/_evals.md`,
the approved catalog block):

```bash
python3 skills/trust-review/scripts/eval_checks.py --run-dir .trust/<run_id>
```

E1: `rounds[]` non-empty and every round record complete — `model.name` +
`model.version`, an ISO-8601 `timestamp`, and all three independence hashes
(`reviewer_prompt_sha256`, `input_artifact_sha256`,
`provenance_record_sha256`). A missing manifest is itself a FAIL. Run it
after the loop concludes (converged or capped). FAIL → repair the manifest
defect → re-run; max 2 repair rounds, then ACTION REQUIRED naming the round
and missing fields — an incomplete independence record makes the review's
provenance `not-ready` regardless of how clean the artifact reviewed.
Append the 🧪 block to the run summary:

```markdown
## 🧪 Self-eval — E-checks (1/1 passed, 0 repair rounds)

- E1 PASS — 3/3 rounds carry model+timestamp+3 hashes
```

**Memory append (event-driven).** If the owner deemed a reviewer finding type
noise this run (or an E1 FAIL/repair happened): append 2–3 sentences to
the memory file above (newest first, cap 20). Builder
side only — see Phase 0 for the independence guard. Two recurrences →
graduate per the contract §3.4.

## Failure modes
- **Gate not satisfied** → status `blocked` (not `failed`); record which key is missing; run no model.
- **Reviewer returns an invalid edit list** → status `failed`; do not advance the loop on an unparseable review.
- **Reviewer model rejected by the Agent override** → fall back to the parent model and record **reduced independence** in `manifest.routing.independence_note`.
- **Round cap reached with anything open** → survivors `still-open` → `not-ready`; never converge by giving up.
