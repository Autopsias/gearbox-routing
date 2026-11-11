# S01 — Foundation (v3): contract, claim identity & migration

**Run:** `2026-05-31T1937_s01-foundation` · schema_version **3** · status **ok** · travel class internal · local-only (no model-run loop).

Lay the v3 contract plus the two properties the adversarial review said must exist before any feature: a **stable claim identity** so a refresh can't reuse `verified` on a claim whose meaning moved, and a **backward-compatibility gate** so v3 reads every existing v1/v2 run before it writes anything new. Wrap-don't-rebuild; additive only; frozen `conventions.md §5` thresholds + §5.1 rule untouched.

## FD-01 — v3 contract extensions + degraded-status caps (findings 3/4/5)

Additive `## 7` block in `references/conventions.md` + the `_v3_additive` mirror in `conventions.json` (v1/v2 keys frozen):

- **§7.1 degraded-status caps** `{sidecar-only-pdf, recalc-unverified, same-family-reviewer, embed-unavailable}` — each **caps** readiness and never yields plain `ready`; `sidecar-only-pdf` also carries `not-ready-for-forwarding`. A cap can only make a verdict worse, never a relaxation; recorded in `manifest.degraded_status[]`.
- **§7.2 reviewer-independence-by-travel-class** `{cross-vendor, named-human-SME, same-family, same-model}` — Internal/Travels accept same-family; Board/Regulatory requires cross-vendor **or** named-human-SME, else `blocked` / `ready-with-limitations` after named-owner signoff.
- **§7.3 checks-tab spec enum** `{tie-outs, formula-consistency, error-scan, stale-date, scenario}` — recalc-gated (the readiness claim is blocked when recalc is unverified, ab-01).
- **§7.4 in-PDF embedded-evidence channel** (`embedded-pdf` vs `sidecar`) + the locator addition `annot:ID | attachment:'NAME'`.
- **§7.6 human-gate-digest invariants** (readiness-from-full-set / no-auto-resolve / hidden-tail-visible / tail-acknowledged).
- **§7.7 two-phase vault-writeback record** (quarantine→promoted, derived-provenance, supersedes/expiry/run-hash/rollback/dedupe, named-human approval).
- Registered **`vault_write`** + **`external_reviewer`** operations in `capability-inventory.md` (DEFERRED-FUNCTIONAL · S01) **and** `references/scripts/adapter_registry.py` (each with an always-on terminal ⇒ never None).

## FD-02 — canonical claim identity + diff (finding 2)

`references/scripts/claim_identity.py` (pure stdlib) + conventions §7.5: `claim_id` (= `C-<12 hex of sha256(normalized_text_hash:source_fingerprint)>`, stable for the life of the claim), normalized-text hash, locator hash, source fingerprint, retrieval timestamp, materiality class, waiver-scope hash, supersession/tombstone state. The **moved-claim diff** (same normalized text + same source fingerprint, new locator ⇒ identity stable, verdict may carry) and the **invalidation classes** that MUST invalidate a carried verdict (text / source / unit-or-period / source-age / waiver-scope / supersession-tombstone / materiality-escalation).

## FD-03 — backward-compat / migration gate (finding 10)

`eval/compat_harness.py` loaded **all 9 committed v1/v2 `.trust` runs** (7 v1, 2 v2) read-only under the v3 reader — `migration.json` + `compat-harness.md` in this run dir. New v3 optional fields default safely (absent ⇒ v1/v2 behaviour, never a crash; partial loop manifests that predate `created`/`artifact`/`gate` are tolerated). A v3 refresh consumes a prior run **read-only** (whole-tree byte-identical); new outputs stamp `schema_version` + `migration_notes` (this run is the first schema_version=3 exemplar).

## Fixtures

One per new surface, wired into `fixtures/run_fixtures.sh` under `[v3 foundation]`: `fixture_v3_contract.py`, `fixture_claim_identity.py`, `fixture_migration_gate.py`; `fixture_adapter_registry.py` extended for the two new ops. Suite kept green (0 failed).
