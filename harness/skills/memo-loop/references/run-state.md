# The run-state directory — every file, with its template

**Files are the only state.** Every pass reads disk and runs in a fresh context; nothing is carried in
conversation history. That is what makes the fresh-context critic provable instead of asserted, and it
is why this file exists: one home for the shape of every artifact a run writes.

**Location:** `<memo-dir>/.memo-loop/<run_id>/`, where `<memo-dir>` holds the memo (`improve`) or is the
working directory (`create`), and `run_id` is `<ISO-timestamp>_<short-slug>` — a timestamp first, so
runs sort naturally. It is a separate namespace from any pre-send review tool's run directories, so
neither ever meets the other's files.

## Contents
- [The tree](#the-tree)
- [`run.json`](#runjson)
- [`manifest.json`](#manifestjson)
- [`anchor-scores.json` — the frozen original's score](#anchor-scoresjson--the-frozen-originals-score)
- [`trust-handoff.json`](#trust-handoffjson)
- [Where the rest live](#where-the-rest-live)

## The tree

```
<memo-dir>/.memo-loop/<run_id>/
  run.json                  # THE run log
  manifest.json             # critic independence record
  original.md               # the frozen anchor. Never rewritten, and never edited.
  rubric.json  rubric.md    # the compiled instance rubric        -> intake-and-rubric.md
  dependency-map.json       # claim -> section/source/claim edges  -> intake-and-rubric.md
  calibration-exemplar.md   # the pair handed to the critic        -> intake-and-rubric.md
  critic-schema.json        # the envelope JSON Schema             -> critique.md
  anchor-scores.json        # the frozen original's score          -> intake-and-rubric.md
  critique/
    critic-prompt.md        # the exact dispatched text, hashed into manifest.json
    critic-response.json    # raw envelope, exactly as returned
    critic-events.jsonl     # the transport's own event stream
    findings.json           # validated, tool-grounded findings     -> critique.md
    discarded.json          # tool-unconfirmed findings, PUBLISHED  -> critique.md
    citations.json          # present / verified / flagged          -> critique-report.md
  council/                  # `create` with council only            -> council.md
    outline.md  outline-score.json  evidence-gate.json
    drafts/  rankings/  label-map.json  blinded-scores.json  merge.md
  cross-vendor-review-request.md   # rung 2 only                    -> critique.md
  critique-report.md        # the human-facing deliverable          -> critique-report.md
  trust-handoff.json        # pre-send-review-compatible finding set
  lessons-entries.jsonl     # this run's entries, as appended       -> reflect.md
```

Files marked `->` are specified in the reference named; they are listed here so the tree is complete in
one place, not restated. The rest are below.

## `run.json`

**Every field is REQUIRED.** A run whose `run.json` is missing any of them is itself a defect — this is
memo-loop's analogue of a manifest-completeness check. Optional-looking keys
(`fault_injection`, `awaiting_external_review`) are written as `null` when they do not apply; absent is
not the same as null.

```json
{
  "run_id": "2026-08-04T1830_mobile-checkout",
  "mode": "improve",
  "schema_version": "1",

  "runtime_detected": "claude-code",
  "probe": {"rung1": {"result": "pass", "reason": null,
                      "cli": "codex-cli 0.146.0", "auth": "ok"}},
  "rung_used": 1,
  "reviewer_independence": "cross-vendor",
  "residual_bias": "<the rung's verbatim sentence from critique.md>",
  "demotions": [],

  "backends": {
    "home_dir":         "/Users/<you>/.claude/memo-loop",
    "lessons_file":     "/Users/<you>/.claude/memo-loop/lessons.jsonl",
    "calibration_dir":  "/Users/<you>/.claude/memo-loop/calibration",
    "promotion_dir":    "/Users/<you>/.claude/memo-loop/learned",
    "resolved_by":      "global",
    "test_backend":     false
  },

  "anchor_origin": "human",
  "anchor_sha256": "7b41…",
  "drafting_path": null,
  "exemplar_source": "bootstrap",
  "intake": {"decision": "…", "audience": "…", "questions": []},

  "fault_injection": null,
  "test_run": false,
  "lessons_suppressed": null,

  "run_state": "terminated",
  "awaiting_external_review": null,

  "anchor_score": {"pass": 4, "of": 10},
  "findings_summary": {"blocking": 1, "material": 3, "minor": 2, "discarded": 1},
  "critique": {"completed": true, "reason": null},
  "unresolved_count": 0,
  "citations": {"present": 3, "verified": 2, "flagged": 1},

  "terminal_state": "FINDINGS_RAISED",
  "terminal_reason": null
}
```

Field notes, only where the value is not obvious:

| Field | Values | Note |
|---|---|---|
| `anchor_origin` | `human` \| `generated` | `generated` in `create`; it suppresses voice-drift findings for the whole run. |
| `drafting_path` | `null` \| `"council"` \| `"single-writer (council unavailable: <reason>)"` | `create` only. A degradation is never silent — it is also stated in the convergence report. |
| `resolved_by` | `env` \| `vault` \| `global` \| `sidecar` | `sidecar` is the degraded last rung and is reported out loud. |
| `run_state` | `running` \| `awaiting-external-review` \| `terminated` | `terminal_state` is **`null`** until `terminated`. |
| `findings_summary` | counts, or **all `null`** | `null` on `ABORTED` — zero findings is a result a completed critique produces, and `0` here would make a failed dispatch indistinguishable from a clean memo. |
| `terminal_state` | `CLEAN` \| `CLEAN_UNVERIFIED` \| `FINDINGS_RAISED` \| `ABORTED` | `null` until `run_state == "terminated"`. `CLEAN` means zero blocking/material findings — never that the memo is good. |
| `terminal_reason` | `null` \| `critic-transport-exhausted` \| `critic-schema-invalid` \| `input-integrity-failed` \| `write-boundary-violated` \| `anchor-score-failed` \| `deliverable-write-failed` | Non-null exactly when `terminal_state` is `ABORTED`, which is the terminal class for **every** incomplete run. `critic-schema-invalid` is the transport rejecting **our** schema (`critique.md` Step 4) — it never demotes and never retries. |

## `manifest.json`

```json
{ "schema_version": "1", "run_id": "2026-08-04T1830_mobile-checkout",
  "dispatches": [
    { "purpose": "critique",
      "model": {"name": "gpt-5.6-sol", "version": "2026-06-11"},
      "timestamp": "2026-08-04T18:31:07Z",
      "memo_sha256": "7b41…", "rubric_sha256": "41ab…", "critic_prompt_sha256": "d0e7…",
      "rung": 1, "runtime": "claude-code", "reviewer_independence": "cross-vendor",
      "schema_enforced": "transport" } ] }
```

One entry per model dispatch the run made — the critique, the anchor grading, and in `create` mode each
council ranking, blinded grading and chairman call. `dispatches[]` being incomplete makes the review's
own provenance untrustworthy however clean the memo scored — re-hashing the same inputs must reproduce
the record.

`schema_enforced` is `"transport"` when `--output-schema` / `--json-schema` carried the envelope, and
`"read-only"` when the supervised path was used and only post-read validation applied.

## `anchor-scores.json` — the frozen original's score

```json
{ "memo": "original.md", "memo_sha256": "c19f…",
  "rubric_sha256": "41ab…", "item_count": 10,
  "grader": {"model_name": "gpt-5.6-sol", "model_version": "2026-06-11",
             "timestamp": "2026-08-04T18:44:02Z", "grader_prompt_sha256": "b7a2…"},
  "pass_count": 4, "unresolved_count": 0, "score": "4/10",
  "items": [
    {"rubric_item_id": "R-01", "verdict": "PASS", "settled_by": "orchestrator tool check"},
    {"rubric_item_id": "R-06", "verdict": "FAIL", "settled_by": "critic item_verdict"},
    {"rubric_item_id": "R-08", "verdict": "UNRESOLVED", "settled_by": "orchestrator tool check"}] }
```

`verdict` is one of `PASS` / `FAIL` / `UNRESOLVED`; ranks are `PASS=1`, `FAIL=0`, `UNRESOLVED=0`.

**There is exactly one scored artifact per run**, because there is exactly one memo — the frozen
original. Nothing is re-scored, because nothing is edited.

`grader` is **required**: it is what shows the scorer was not the model that wrote the memo
(`intake-and-rubric.md` Step 8). `[checkable]` items are settled by the orchestrator's own tool runs and
carry no model verdict; the block describes the grader of the `[judgment]` items.

## `trust-handoff.json`

Written in Phase 3, in trust's own vocabulary, so a board-bound memo enters pre-send review without
translation. The impact → severity mapping is in `critique.md` and is applied here, never left to the
reader.

```json
{ "schema": "memo-loop.trust-handoff.v1",
  "run_id": "2026-08-04T1830_mobile-checkout",
  "memo_path": "…/mobile-checkout.md", "memo_sha256": "c19f…",
  "readiness": "critiqued-not-remediated",
  "handoff_applies_to": "pre-rewrite-original",
  "reviewer_independence": "cross-vendor",
  "findings": [
    {"severity": "should-fix", "title": "No named objection is answered",
     "location": "S4", "review_status": "open", "issue_lifecycle": "raised",
     "source_finding_id": "F-08"}],
  "claims": [
    {"claim_id": "C1", "text": "Mobile conversion is 2.1% against 3.4% on desktop.",
     "review_status": "supported", "sources": ["src:funnel-2026Q1.csv#row14"],
     "verified_at": "2026-08-04T18:44:02Z"}],
  "waivers": [] }
```

`readiness` is always **`critiqued-not-remediated`** under a critic-only skill. The old value
`improved-not-verified` was retired because it asserted the opposite of what happened: the
memo was **not** improved — nothing edited it — and a trust consumer reading "improved" could treat an
untouched, still-deficient memo as having been worked on. **pre-send review** is what decides whether a memo
may be sent, and memo-loop never asserts that verdict on its behalf.

> **This handoff describes the PRE-REWRITE text, and the workflow guarantees it goes stale.** memo-loop
> criticises `original.md` and then tells the author to rewrite; every per-claim `review_status` and
> `verified_at` above refers to sentences that may no longer exist. Two fields make that checkable
> instead of assumed:
>
> - `handoff_applies_to: "pre-rewrite-original"` — always, under a critic-only skill.
> - `memo_sha256` — the hash of the exact text these claims were checked against.
>
> **A consumer MUST compare `memo_sha256` against the file it is given and refuse to inherit any
> `review_status` on a mismatch.** Without that rule the send-gate can award verification credit to text
> that an unconstrained rewriter has since changed — uncontained churn,
> laundered into a machine-readable `"supported"`. `readiness` does not cover this: it
> says what *kind* of review happened, never *which bytes* were reviewed.

## Where the rest live

| Artifact | Owner |
|---|---|
| `rubric.json` · `rubric.md` · `dependency-map.json` · `calibration-exemplar.md` · `anchor-scores.json` | `intake-and-rubric.md` |
| `critic-schema.json` · `findings.json` · `discarded.json` · `cross-vendor-review-request.md` · ranking + blinded-score records | `critique.md` |
| `citations.json` · `critique-report.md` · `trust-handoff.json` | `critique-report.md` |
| `council/` | `council.md` |
| `lessons-entries.jsonl`, the resolved lessons store and `reflect-state.json` | `reflect.md` |

**Nothing in this tree is ever written inside an installed skill directory**, in either harness. The
run dir sits beside the memo; the lessons home, calibration archive and promotion target resolve through
the backend ladder. All four are asserted absolute and symlink-resolved before any write, on every
invocation of every mode.
