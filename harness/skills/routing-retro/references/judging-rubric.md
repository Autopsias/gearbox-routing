# /routing-retro judging rubric — flag definitions + evidence bars

Expected values always come from the live SSOT (`~/.claude/model-routing.yaml`) at run
time. This rubric defines the FLAG SHAPES and what evidence each needs; it deliberately
names no model or tier.

## Flags

### over-modeled
Task shape maps to a cheap class (per `task_classes:`) but the session ran predominantly
on a higher tier than that class's default — e.g. mechanical/standard-build-shaped work on
the main session's advisory tier with no escalation justification in the transcript.
- **Evidence bar**: task shape inferable from prompt/title with reasonable confidence AND
  ≥70% of assistant messages on the higher tier AND no receipt/switch justifying it.
- **Caveat**: the MAIN SESSION cannot switch its own model (SSOT SCOPE (b)) — a
  main-session mismatch is a *missed /model-recommendation* finding (advisory failure),
  not an agent-pin bug. Say which it is.

### under-modeled
Escalation-ladder signature: repeated failures at the same root cause (tool errors /
re-attempts clustered), then a `/model` switch or a receipt showing late escalation — the
task should have STARTED higher per its class default, or escalated after 2 failures per
`escalation:` and didn't.
- **Evidence bar**: the failure→switch sequence visible in the scan (error counts + switch
  commands); quote the receipt or switch line from the transcript for the ledger entry.

### receipt-vs-actual mismatch
A routing receipt claims `class -> tier` but the transcript's model mix disagrees with
that tier, or the claimed class is implausible for the task shape.
- **Evidence bar**: the receipt line verbatim + the session's model-mix row.

### cost outlier
Session cost far above peers with similar shape/duration in the same scan window (rule of
thumb: >3x the scan median without a class that justifies it). Common causes worth naming:
fan-out on the most expensive tier (see SSOT `fanout_policy:` — its `never:` row is the
headline check), cache-miss storms (low cache_read vs input), truncation-retry loops
(`max_tokens_truncations` > 0).
- **Evidence bar**: the cost figure + the comparison base (median of this scan) + the
  suspected mechanism.

### context-bloat (should-have-forked)
A session carried a large context across many turns while the WORK DIVERGED — later turns
paid to re-read history belonging to an earlier, finished task. The remedy is dispatch
hygiene (finish the task, then start a clean session/worker carrying only the accepted
result), never a tier or effort change.
- **Scan signals**: high `context_peak_tokens` (outlier vs this scan's median) AND a high
  `reread_cost_pct` AND enough `assistant_messages` for the carrying to have repeated.
- **Evidence bar — the divergence is the finding, not the token count.** You MUST cite ≥2
  distinct, unrelated task shapes inside the one session (from `first_prompt`, `ai_title`,
  and a transcript look at where the second topic starts) before flagging. A single long
  hard task with a large context is CORRECT behaviour and must not be flagged.
- **Why the count alone proves nothing**: cache reads bill at a fraction of fresh input
  (see the scanner's `CACHE_READ_X`), so a high re-read share is the *cheap* outcome — the
  expensive alternative is the same context arriving as fresh input. Never report a token
  count or a cache-read share as a saving opportunity on its own; quantify in dollars
  (`reread_cost_usd`) and only against demonstrated irrelevance.
- **Not this flag**: low `cache_read` against high fresh input is the opposite problem (a
  cache-miss storm) and belongs to *cost outlier* above.
- **Sidechain caveat**: a subagent's context lands in the SAME transcript, so a fan-out can
  set `context_peak_tokens` on a sidechain turn the main thread never carried. Check
  `sidechain_messages` before attributing a peak to the main conversation — a big
  sidechain peak is a `fanout_policy` question (worker scope), not a fork-hygiene one.

### degradation-ladder activation
A session shows the reactive fallback (dispatched tier refused → next tier down, per SSOT
`degradation:`). Not a misroute by itself — but ≥2 activations in one window means the
SSOT may be pinning a tier that is effectively unavailable; propose `/routing-update`.

### peer-gate-miss (special — feeds the runbook owner-gate)
A session's work hits `codex_peer.lint_keywords` (architecture / irreversible / security)
with no adversarial-review gate in evidence, and it mattered. Class the MISROUTES entry
literally as `peer-gate-miss` — the runbook's promotion owner-gate greps for that token.

## Severity ordering for the report

1. Anything arming or near the **≥2-MISROUTES re-run trigger** (state the count).
2. **Recurring** over/under-modeling on one task shape (calibration signal → /routing-update).
3. One-off misroutes (ledger entries).
4. Cost outliers with a named mechanism.
5. Hygiene: missing receipts, settings/env drift, fan-out policy adherence, context-bloat
   (dispatch hygiene — no SSOT change; it never routes to `/routing-update`).

## Honesty rules

- Effort is **inferred, never measured** — label it so in every finding.
- Task-shape inference from a one-line prompt is fallible — mark low-confidence inferences
  and don't propose ledger entries on them (the ledger is append-only; a wrong entry
  pollutes the re-run trigger's counter forever).
- Aggregate verdicts need a per-session look first (no NO-GO from an aggregate — the
  per-item autopsy rule applies to routing verdicts too).
