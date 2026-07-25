# /routing-retro judging rubric — flag definitions + evidence bars

Expected values always come from the live SSOT (`claude/model-routing.yaml`,
`providers.<active_provider>`) at run time. This rubric defines the FLAG SHAPES and
what evidence each needs; it deliberately names no model id and no tier as ground
truth — only the abstract vocabulary (`cheap_fast` / `workhorse` /
`frontier_reasoner` tiers; `light` / `standard` / `thorough` effort intents).

## Flags

### over-modeled
Task shape maps to a cheap class (per `task_classes:`) but the session ran
predominantly on a higher tier than that class's default — e.g. mechanical/
standard-build-shaped work on the main session's advisory tier with no escalation
justification in the transcript.
- **Evidence bar**: task shape inferable from prompt/title with reasonable confidence
  AND ≥70% of assistant messages on the higher tier AND no receipt/switch justifying
  it.
- **Caveat**: the MAIN SESSION cannot switch its own model — a main-session mismatch
  is a *missed recommendation* finding (advisory failure), not an agent-pin bug. Say
  which it is.

### under-modeled
Escalation-ladder signature: repeated failures at the same root cause (tool errors /
re-attempts clustered), then a model switch or a receipt showing late escalation —
the task should have STARTED higher per its class default, or escalated after the
SSOT's `escalation.trigger` threshold and didn't.
- **Evidence bar**: the failure→switch sequence visible in the scan (error counts +
  switch commands); quote the receipt or switch line from the transcript for the
  ledger entry.

### receipt-vs-actual mismatch
A routing receipt claims `class -> tier` but the transcript's model mix disagrees
with that tier, or the claimed class is implausible for the task shape.
- **Evidence bar**: the receipt line verbatim + the session's model-mix row.

### cost outlier
Session cost far above peers with similar shape/duration in the same scan window
(rule of thumb: >3x the scan median without a class that justifies it). Common
causes worth naming: fan-out on the most expensive tier (see SSOT `fanout_policy:`
— its `never:` row is the headline check), cache-miss storms (low cache_read vs
input), truncation-retry loops (`max_tokens_truncations` > 0).
- **Evidence bar**: the cost figure + the comparison base (median of this scan) +
  the suspected mechanism.

### degradation-ladder activation
A session shows the reactive fallback (dispatched tier refused → next tier down,
per the active provider's `degrade:` block). Not a misroute by itself — but
repeated activations in one window means the SSOT may be pinning a tier that is
effectively unavailable for the active provider; propose `/routing-update`.

### peer-gate-miss (special — feeds an owner-gate obligation)
A session's work hits the SSOT `codex_peer.lint_keywords` categories
(architecture / irreversible / security) with no adversarial/peer-review gate in
evidence, and it mattered. Class the ledger entry literally as `peer-gate-miss` if
your deployment's promotion process greps for that token.

## Severity ordering for the report

1. Anything arming or near a ledger-count re-run trigger (state the count).
2. **Recurring** over/under-modeling on one task shape (calibration signal →
   /routing-update).
3. One-off misroutes (ledger entries).
4. Cost outliers with a named mechanism.
5. Hygiene: missing receipts, settings/env drift, fan-out policy adherence.

## Honesty rules

- Effort is **inferred, never measured** — label it so in every finding.
- Task-shape inference from a one-line prompt is fallible — mark low-confidence
  inferences and don't propose ledger entries on them (the ledger is append-only; a
  wrong entry pollutes the re-run trigger's counter forever).
- Aggregate verdicts need a per-session look first (no verdict from an aggregate
  alone — the per-item autopsy rule applies to routing verdicts too).
