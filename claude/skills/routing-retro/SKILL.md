---
name: routing-retro
description: >-
  Retrospective over recent session transcripts + the misroute ledger to judge whether model/effort routing is actually working — over-modeled, under-modeled, cost outliers, receipt mismatches. Use when the user says "routing retro", "is our model selection working", "analyze model/effort fit", "review routing performance", "are we over/under-modeled", "how much are sessions costing", or after ~1 week / ~30 sessions on a new routing mapping. READ-ONLY analysis - it proposes; the only write it ever offers is an operator-confirmed append to MISROUTES.md. Calibration changes route to /routing-update — this skill never edits the SSOT.
allowed-tools: [Read, Bash, Glob, Grep, Edit, AskUserQuestion]
effort: medium  # judgment over pre-digested per-session stats (the deterministic scanner does the heavy lifting); findings are proposals reviewed by the operator, not applied state.
---

# /routing-retro — is the routing table working in practice?

Manual, nudged retrospective. This skill is the **observability half** of the routing
system: scan recent sessions, compare what SHOULD have run (SSOT `task_classes:` for
whichever provider is `active_provider:`) against what DID run (transcript model mix +
receipts), and turn mismatches into ledger entries and calibration proposals.

**Read-only posture.** This skill writes NOTHING except (after explicit per-entry
operator confirmation) appends to a misroute ledger (e.g. `claude/evals/routing/MISROUTES.md`
once that file exists in your deployment). Calibration-level changes (a class default,
an agent pin, an elasticity, a price row) are handed to `/routing-update` — one
apply-path, one guard, one approval protocol.

**Read, never hardcode.** Expected classes, default pairs, and prices come from
`claude/model-routing.yaml` AT RUN TIME — specifically `prices.<active_provider>` and
`providers.<active_provider>`. This skill's prose carries no tier values, no model
names, and no price numbers.

## Checklist (all 5 steps)

- [ ] **Deterministic scan** — run `retro_scan.py`, read the JSON
- [ ] **Judgment layer** — infer task shape, compare expected vs actual routing
- [ ] **Report** — ranked findings, each with a recommended action
- [ ] **Did-it-stick check** — verify prior changes are still in place
- [ ] **Close** — state the next evidence-tied cadence

## Flow

### 1. Deterministic scan (cheap, no raw-JSONL reading by the model)

```bash
python3 claude/skills/routing-retro/scripts/retro_scan.py \
  --last 20 --exclude-session <CURRENT-SESSION-ID> > /tmp/retro-scan.json
```

- **Always pass the current session's id** to `--exclude-session` (it's in your
  transcript path); the scanner also drops headless/SDK-entrypoint sessions itself.
- Useful variants: `--project <substring>` to scope to one project; `--since
  YYYY-MM-DD`; `--last N`; `--provider <name>` to price against a provider other than
  the SSOT's current `active_provider:` (rare — only for "what would this have cost
  under provider X" questions).
- Output per session: model mix %, token breakdown, **cost against the active
  provider's `prices:` row**, duration, tool/API error counts, `max_tokens`
  truncations, routing receipts (`class -> tier` lines), model/effort switch
  commands, first prompt, AI title.
- Read the JSON output, not the transcripts. Only open a raw transcript when a
  specific finding needs verbatim evidence (quote a receipt, confirm an escalation
  sequence).
- **Path note**: the scanner defaults to a generic transcript-projects directory
  layout (`--projects-dir`, default `~/.claude/projects`). If your deployment stores
  session transcripts elsewhere, pass `--projects-dir` explicitly — there is no
  hardcoded personal path in the script.

### 2. Judgment layer

Read `claude/model-routing.yaml` (`task_classes:`, `main_session:`,
`providers.<active_provider>.effort:`, `providers.<active_provider>.escalation:`) and
`references/judging-rubric.md`, then for each scanned session:

1. **Infer task shape** from `first_prompt` + `ai_title` (and any local prompt-history
   log, if the first prompt is a bare slash command).
2. **Expected class** per SSOT `task_classes:` → expected (tier, effort-intent) pair,
   then resolved to the active provider's native model id / effort level via
   `providers.<active_provider>.models:` / `.effort.map:`.
3. **Compare vs actual** model mix + receipts + switches. Flag per the rubric:
   over-modeled, under-modeled (escalation-ladder signatures: repeated failures then a
   model switch), receipt-vs-actual mismatch, cost outlier, degradation-ladder
   activation.
4. **Prescriptive-prompt friction (frontier-reasoner-pinned agents only).** Some
   frontier reasoning models are documented by their providers as sensitive to overly
   prescriptive prompts written for earlier model generations — the instructions can
   fight the model's own reasoning process and degrade output. For sessions that
   dispatched an agent pinned to the `frontier_reasoner` tier (per SSOT `agents:`),
   check the transcript for the agent fighting its instructions: skipping mandated
   procedure steps, restating why a step doesn't apply, or output-quality complaints.
   If seen, propose trimming that agent's prompt via `/routing-update` — never edit
   here.

**Effort blind spot — handle explicitly.** Transcripts do NOT record the effort level
actually applied. Report effort as *inferred* (from any local runtime settings file,
an environment-variable override, `/effort`-style switches seen in transcripts, or
receipts) and label it "inferred" in every finding — never claim effort as measured.

### 3. Report — biggest wins first, every finding gets a recommended action

Short report, ranked by impact (lead with a recommendation, never neutral):

- **Misroute instances** → propose appending to the misroute ledger (e.g.
  `claude/evals/routing/MISROUTES.md`) in its documented schema (`### YYYY-MM-DD —
  <task shape>` / expected class / actual / cost symptom / notes). **Confirm each
  append with the operator individually**, stating the consequence: *the ledger is
  append-only, and enough entries since the SSOT's `last_reviewed` should prompt a
  full-calibration-eval re-run.* Only append what the operator confirms.
- **Calibration-level proposals** (change a class default, an agent pin, an
  elasticity, a price row) → recommend running **`/routing-update`** with this retro
  report as input evidence. Never edit the SSOT or any consumer from here.
- **Trigger check**: after any appends, count ledger entries dated since the SSOT's
  `last_reviewed:` — if there are multiple, surface a full-eval re-run recommendation
  explicitly.
- When the findings boil down to a short decision-shaped set (accept/reject a
  misroute append, run `/routing-update` or not), the report may instead be shipped
  as a compact decision-card one-pager instead of a long prose report.

### 4. "Did it stick" check

If a prior `/routing-update` or retro applied changes (check the SSOT's `version:` /
`last_reviewed:` history and CHANGELOG.md), verify they're still in place:

- Guard-covered surfaces: run your deployment's routing drift-guard (e.g. `bash
  claude/scripts/verify-routing.sh --full`, once it exists), read-only — green means
  SSOT/agents/digest still agree.
- NOT guard-blocking: any local runtime settings file (the guard, if present, only
  warns) and *behavioral* adherence (are receipts actually being emitted? are
  fan-outs pinning tiers per `fanout_policy:` rather than inheriting the main-session
  default?) — check both here, from the scan data.

### 5. Close

End with an evidence-tied next cadence, not a calendar default: **"re-run
/routing-retro after ~30 new sessions on this mapping, or immediately after any
ledger entry or `/routing-update` apply"** — adjust the session count to how fast
sessions actually accumulate in the scan window you just measured.

## Note on the fallback-ladder binding

If your deployment has a vendored resolver or a harness-embedded fallback ladder
(e.g. a `run.py`-style consumer that mirrors the SSOT's escalation/degrade shape in
code), that binding is **out-of-tree, code-authoritative behavior** — this skill only
*observes* its effects in transcripts (receipts, degradation-ladder activation
signatures). Gearbox's skills do not own or enforce that degrade behavior; they
document it as a consumer to check for drift (`references:` in the SSOT), never as
something `/routing-retro` or `/routing-update` control directly.
