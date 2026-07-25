---
name: routing-update
description: Research a model-landscape change and regenerate every routing surface through one operator-approved changeset. Use when the user says "model update", "new model released", "update model routing", "pricing changed", "model deprecated", "model GA'd", "recalibrate routing for <model>", "runtime binary bump" (effort-honoring recheck), or any provider model/pricing/deprecation news that should flow into `claude/model-routing.yaml`. Not for judging whether current routing WORKS in practice (that's /routing-retro), and not for per-task model picks (the SSOT digest handles those).
allowed-tools: [Read, Write, Edit, Bash, Glob, Grep, Skill, AskUserQuestion]
effort: high  # SSOT recalibration is judgment-heavy (pricing/tier tradeoffs feed every future dispatch); the mechanical apply is checklist-driven.
---

# /routing-update — model-landscape change → researched, approved, applied everywhere

One command to take a model-landscape change (new model/family, price change, deprecation,
effort-semantics change, agent-runtime bump) from **research** to **a single
operator-approved changeset** to **every consumer surface regenerated, guard-verified, and
committed** — for whichever provider is currently `active_provider:` in the SSOT.

**Prerequisite — a research tool MUST be connected.** This skill verifies model
lineups, prices, and effort-dial semantics from CURRENT provider docs, never from
memory — that is a hard project rule, not a style preference. Before starting, confirm
at least ONE of the following is connected: **Exa** (`web_search_exa` +
`deep_researcher_start`/`_check`), **Ref** (`ref_search_documentation` /
`ref_read_url`), or **Perplexity**. Any one suffices. **If none is connected, STOP**
after the snapshot step and ask the operator to supply the researched numbers
directly — do not assert a model id, price, or effort-dial fact unverified, and do not
silently fall back to training-data knowledge of "what model X probably costs."

**Prime directive — read, never hardcode.** This skill reads `claude/model-routing.yaml`
(the SSOT) at runtime for every tier value, price, agent pin, and trigger. Nothing in
this skill's own prose carries a model name, price, or effort tier as ground truth — so
the skill never drifts and never joins the SSOT `consumers:` list as a stamp-checked
prose surface beyond its own rationale stamp. If an instruction here ever disagrees with
the SSOT, the SSOT wins.

**One approval, then everything.** The operator approves the changeset ONCE (step 4);
after that the full apply checklist runs without further per-file questions. Only a
guard-verify failure or a genuinely new decision discovered mid-apply comes back to the
operator.

**Human-gated, always.** This skill never auto-applies. Every changeset — however small
— stops for an explicit operator approval before any file is edited. There is no
"trivial change" fast path that skips the gate.

## Checklist (all 6 steps)

- [ ] **Snapshot** — read SSOT, confirm a research tool is connected, check git status
- [ ] **Research** — parallel fan-out against the ACTIVE provider's docs (or runtime-bump canary)
- [ ] **Impact diff** — map findings onto SSOT blocks + consumer surfaces
- [ ] **Changeset proposal** — write the changeset file, get operator approval (the one gate)
- [ ] **Apply** — run the full checklist top to bottom, guard-verified
- [ ] **Post-apply** — eval re-run scope, nudge retro, version/changelog sanity check

## Flow

### 1. Snapshot

- Read `claude/model-routing.yaml` — note `version:`, `last_reviewed:`, `active_provider:`,
  the `providers.<active_provider>` block (`calibration:`, `models:`, `effort:`,
  `escalation:`, `degrade:`), the `prices.<active_provider>` block, `task_classes:`,
  `agents:`, and the `consumers:` list (this list IS the apply surface set — don't work
  from a stale copy of it in your head).
- Confirm the research prerequisite: check which of Exa / Ref / Perplexity is connected.
  If none, STOP here — report which providers you checked and ask the operator to supply
  the numbers (model ids, prices, effort-dial behavior) directly, citing their source.
  Do not proceed to step 2 on memory alone.
- `git status --short` (repo root) — the apply step (5) requires a clean tree for the
  files it touches. Pre-existing unrelated dirt is fine (stage only your files at
  commit), but any pre-existing dirt ON a routing surface must be resolved or explicitly
  deferred by the operator before applying. **Never** stage or commit files outside the
  routing surface set you were asked to touch — a concurrent session may be editing
  unrelated parts of this repo.
- Classify the trigger:
  - **runtime/binary bump** → the research step is a live effort-canary probe against
    the active provider's runtime (see `references/research-protocol.md`
    §Binary-bump), not web research.
  - **new model / pricing / deprecation / tier-semantics change** → full research
    fan-out (step 2), scoped to whichever provider the change is about. If that
    provider is not `active_provider:`, you are updating a non-active profile — the
    changeset still applies (steps 3-5), but the "bump version + apply everywhere"
    urgency is lower since no consumer currently resolves against it.
  - **retro evidence** (invoked with a `/routing-retro` report as input) → skip web
    research for facts the report already grounds; still verify prices via live
    research if any `prices:` row is touched.

### 2. Research (parallel fan-out)

Follow `references/research-protocol.md`. Summary:

- Research against the CURRENT provider's own documentation and pricing pages first —
  authoritative model IDs, pricing, deprecations, effort-dial availability per model.
  This is how the SSOT's `models:`/`effort:`/`prices:` rows are verified
  ("never from memory").
- External priors in parallel for a genuinely new model/tier — benchmarks,
  practitioner reports, head-to-head evals — via whichever of Exa/Ref/Perplexity is
  connected. Disagreement between sources is signal to keep digging, not to average.
- **Runtime-bump path** — run the effort-canary probe instead; its verdict (dial live
  vs inert per model) IS the research output.

### 3. Impact diff

Map research findings onto the SSOT, block by block, for the provider under research:
which `providers.<name>.models:` rows, `providers.<name>.effort.map:` rows,
`providers.<name>.escalation:`/`degrade:` ladders, `prices.<name>:` rows, `task_classes:`
defaults, or `agents:` pins change — and which consumers that touches. Walk
`references/apply-checklist.md`'s surface table top to bottom and mark each surface
**touched / untouched**; explicitly check the easy-to-miss surfaces:

1. Any vendored resolver mirror outside this repo (a consumer's own copy of the
   escalation/degrade ladder — code-authoritative for its own runtime; the SSOT
   mirrors it, never the reverse). If your deployment has such a mirror, edit it
   FIRST or WITH the SSOT rows that describe it.
2. Any project-owned agent-assignment file that pins per-agent tier/effort outside
   this SSOT's `agents:` block (that file is authoritative for its own rows; mirror
   the SSOT `agents:` rows to match, not the other way round).
3. Any local runtime settings file that carries its own default model/effort (warn-
   checked by the guard but user-owned) — drift here is a finding even when nothing
   else changed.

**If the diff is empty** (no landscape change found): report "no model-landscape
change detected", but STILL report any surface drift the walk uncovered — that is this
skill's built-in regression check, not noise.

### 4. Changeset proposal — the one operator gate

Write a changeset file (suggested location: `claude/evals/routing/results/<today>/ROUTING-v<N+1>-PROPOSED-CHANGESET.md`,
once that directory exists in your deployment — otherwise anywhere durable under
version control) in this house format: a **Governing principle** paragraph, then
**Diffs** as `D1..Dn` bullets — each with the exact old → new value, the file(s) it
lands in, and bracketed evidence (a live research citation, a canary-probe result, or
an operator decision + date) — then **Digest must reflect** and **Confidence**
sections.

Present a compact summary and ask via AskUserQuestion (lead with a recommendation,
marked `(Recommended)` — never neutral options): approve all / edit specific diffs /
abort. On approval, rename/annotate the file `PROPOSED` → `FINAL` with the approval
date.

### 5. Apply — the full checklist, in order

Run `references/apply-checklist.md` top to bottom. Order matters (SSOT first, renders
after, guard last). Never bypass a guard failure with a skip flag; a guard failure is
fixed forward, never bypassed. The commit message carries the literal token
`routing-pin-change: approved-by-operator <YYYY-MM-DD>`. Stage ONLY the routing
surfaces you touched plus the changeset file — never a broad `git add -A` / `git add .`
in this repo, since other work may be in flight elsewhere in the tree.

### 6. Post-apply

- **Eval re-run scope** — price-only change: re-run cost math against the new
  `prices.<provider>:` rows at minimum; new model GA / task_classes semantics change:
  recommend a fuller calibration eval pass. Recommend, don't spend the operator's
  budget without asking.
- **Nudge the retro**: "run `/routing-retro` after ~1 week of sessions on the new
  mapping" — the update changes the table; the retro is what proves the table works.
- **Version/changelog sanity check**: confirm `claude/model-routing.yaml`'s `version:`
  was bumped per `docs/VERSIONING.md` (schema/vocabulary change = MAJOR, provider
  calibration change = MINOR, no-behavior-change = PATCH) and that `CHANGELOG.md` has a
  matching dated entry landed in the SAME commit. A content change without a version
  bump is not a supported path — fix before considering step 5 done, not after.
- **Memory pointer (generic)**: if key facts changed (SSOT version, default pairs, new
  operating commands) and your deployment keeps a durable notes/memory store outside
  this repo, update that store's routing-summary entry now — this skill does not
  assume any particular memory tool or path; use whatever your setup already has for
  "durable facts I want future sessions to know without re-reading the SSOT."

## Failure modes to refuse

- Editing any consumer surface WITHOUT bumping the SSOT `version:` (guard/stamp checks
  will fail, and rightly so — a content change without a version bump is not a
  supported path).
- Applying a `prices:` or `models:` change sourced from memory or a single news
  article alone — a live documentation/pricing check is mandatory for those rows.
- Bypassing a red guard check with a `--no-verify`-style escape — those escapes are
  for hook-documented recovery cases only, never for landing this skill's changes.
- Editing a provider's `escalation:`/`degrade:` ladder in the SSOT without editing any
  vendored resolver mirror FIRST (code is authoritative there; the SSOT mirrors it),
  or editing `agents:` rows without also updating any project-owned agent-assignment
  file that duplicates them.
- Auto-applying any diff without the operator's explicit approval — there is no
  unattended path through this skill, regardless of how small the diff looks.
- Running the research/apply flow with zero research tools connected — stop and ask
  the operator for verified numbers instead (see Prerequisite above).
