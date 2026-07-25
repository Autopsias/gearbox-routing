---
name: routing-update
description: Research a model-landscape change and regenerate every routing surface through one operator-approved changeset. Use when the user says "model update", "new model released", "update model routing", "pricing changed", "model deprecated", "model GA'd", "recalibrate routing for <model>", "Claude Code binary bump" (effort-honoring recheck), or any Anthropic model/pricing/deprecation news that should flow into ~/.claude/model-routing.yaml. Not for judging whether current routing WORKS in practice (that's /routing-retro), and not for per-task model picks (the SSOT digest in CLAUDE.md handles those).
allowed-tools: [Read, Write, Edit, Bash, Glob, Grep, Skill, AskUserQuestion, mcp__exa__web_search_exa, mcp__exa__deep_researcher_start, mcp__exa__deep_researcher_check, mcp__perplexity-ask__perplexity_ask, mcp__ref__ref_search_documentation, mcp__ref__ref_read_url]
effort: high  # SSOT recalibration is judgment-heavy (pricing/tier tradeoffs feed every future dispatch); the mechanical apply is checklist-driven. Verified honored in binary 2.1.170.
---

# /routing-update — model-landscape change → researched, approved, applied everywhere

One command to take a model-landscape change (new model/family, price change, deprecation,
effort-semantics change, Claude Code binary bump) from **research** to **a single
operator-approved changeset** to **every consumer surface regenerated, guard-verified, and
committed**. Replaces the edit-by-hand sweep across ~9 files.

**Prime directive — read, never hardcode.** This skill reads `~/.claude/model-routing.yaml`
(the SSOT) and `~/.claude/evals/routing/README.md` (the runbook) at runtime for every tier
value, price, agent pin, and trigger. Nothing in this skill's own prose carries a model
name, price, or effort tier as ground truth — so the skill never drifts and never joins the
SSOT `consumers:` list. If an instruction here ever disagrees with the SSOT, the SSOT wins.

**Edit in the source worktree, deploy to see it live.** Every `~/.claude/...` path above is
where the SSOT is *read from* (or, for a live `~/.claude`, deployed to) — the actual edits
this skill makes land in the source worktree `~/your-private-harness`, committed and pushed
there, then fast-forwarded into `~/.claude` (`git -C ~/.claude pull --ff-only`). Never hand-edit
routing surfaces directly in `~/.claude`; see `~/.claude/CLAUDE.md` § "This tree is a DEPLOY
TARGET".

**One approval, then everything.** The operator approves the changeset ONCE (step 4); after
that the full apply checklist runs without further per-file questions. Only a `verify-routing.sh`
failure or a genuinely new decision discovered mid-apply comes back to the operator.

## Checklist (all 6 steps)

- [ ] **Snapshot** — read SSOT, runbook, check git status
- [ ] **Research** — parallel fan-out (`/claude-api` first, then external priors, or binary-bump canary)
- [ ] **Impact diff** — map findings onto SSOT blocks + consumer surfaces
- [ ] **Changeset proposal** — write the changeset file, get operator approval (the one gate)
- [ ] **Apply** — run the full checklist top to bottom, guard-verified
- [ ] **Post-apply** — eval re-run scope, nudge retro, owner-gate check, memory pointer

## Flow

### 1. Snapshot

- Read `~/.claude/model-routing.yaml` — note `version:`, `last_reviewed:`, the `prices:`
  block, `task_classes:`, `effort_policy:`, `agents:`, `degradation:`, `main_session:`,
  and the `consumers:` list (this list IS the apply surface set — don't work from a stale
  copy of it in your head).
- Read `~/.claude/evals/routing/README.md` — the re-run triggers table decides what class
  of update this is and what post-apply eval it demands.
- `git -C ~/.claude status --short` — the apply step (5) requires a clean tree for the
  files it touches. Pre-existing unrelated dirt is fine (stage only your files at commit),
  but any pre-existing dirt ON a routing surface must be resolved or explicitly deferred
  by the operator before applying.
- Classify the trigger:
  - **binary bump** → the research step is `evals/routing/harness/preflight.sh --live`
    (effort-canary), not web research. See `references/research-protocol.md` §Binary-bump.
  - **new model / pricing / deprecation / tier-semantics change** → full research fan-out
    (step 2).
  - **retro evidence** (invoked with a `/routing-retro` report as input) → skip web
    research for facts the report already grounds; still verify prices via `/claude-api`
    if any `prices:` row is touched.

### 2. Research (parallel fan-out)

Follow `references/research-protocol.md`. Summary:

- **`/claude-api` skill FIRST** — authoritative model IDs, pricing, deprecations, effort
  availability per model. This is how the SSOT's `prices:` were verified originally
  ("never from memory"). Any `prices:` diff MUST cite this.
- **External priors in parallel** — Exa (`mcp__exa__web_search_exa`, deep researcher for
  a genuinely new tier) + Perplexity for benchmarks/practitioner signal; `mcp__ref` for
  Anthropic docs deltas (effort doc, models overview, Claude Code model-config).
  Precedent artifact shape: `evals/routing/external-priors-vulcanbench-2026-07.md`.
- **Binary bump path** — run the effort-canary preflight instead; its verdict (dial live
  vs inert per model) IS the research output.

### 3. Impact diff

Map research findings onto the SSOT, block by block: which `prices:` rows, `task_classes:`
defaults, `effort_policy:` shapes, `agents:` pins, `degradation:` entries,
`main_session:` values change — and which consumers that touches. Walk
`references/apply-checklist.md`'s surface table top to bottom and mark each surface
**touched / untouched**; explicitly check the three easy-to-miss surfaces:

1. `skills/plan-execute/scripts/run.py` (`_FALLBACK_LADDER` / `_REASONING_DIRECTIVE` —
   code-authoritative carve-out; a ladder/tier change edits CODE first, SSOT mirrors),
2. `epic-dev-assignments.yaml` (authoritative for `epic-*` rows),
3. `~/.claude/settings.json` (`model` + `effortLevel` vs `main_session:` — warn-checked by
   `verify-routing.sh` but user-owned; drift here is a finding even when nothing else changed).

**If the diff is empty** (no landscape change found): report "no model-landscape change
detected", but STILL report any surface drift the walk uncovered (e.g. settings.json vs
`main_session:`) — that is this skill's built-in regression check, not noise.

### 4. Changeset proposal — the one operator gate

Write `evals/routing/results/<today>/ROUTING-v<N+1>-PROPOSED-CHANGESET.md` in the
FINAL-CHANGESET house format (precedent:
`evals/routing/results/2026-07-03/ROUTING-v1.1-FINAL-CHANGESET.md`): a **Governing
principle** paragraph, then **Diffs** as `D1..Dn` bullets — each with the exact
old → new value, the file(s) it lands in, and bracketed evidence (eval cell, `/claude-api`
verification, doc URL, operator decision + date) — then **Digest must reflect** and
**Confidence** sections.

Present a compact summary and ask via AskUserQuestion (lead with a recommendation, marked
`(Recommended)` — never neutral options): approve all / edit specific diffs / abort.
On approval, rename/annotate the file `PROPOSED` → `FINAL` with the approval date. The
runbook's SSOT-change protocol (README §"SSOT change protocol") applies from here on.

### 5. Apply — the full checklist, in order

Run `references/apply-checklist.md` top to bottom. Order matters (SSOT first, renders after,
guard last). Never `--no-verify`; a guard failure is fixed forward, never bypassed. The
commit message carries the literal token `routing-pin-change: approved-by-operator <YYYY-MM-DD>`.

### 6. Post-apply

- **Eval re-run scope** — from the runbook's re-run triggers table: binary bump → canary
  already run; price-only change → re-run `report.py` $/outcome math at minimum; new
  model GA / task_classes semantics change → recommend the full calibration eval
  (evl-01..evl-08 pattern). Recommend, with the runbook row cited; the operator decides
  spend.
- **Nudge the retro**: "run `/routing-retro` after ~1 week of sessions on the new mapping" —
  the update changes the table; the retro is what proves the table works.
- **Owner-gate check** (runbook §"Peer-gate-miss promotion"): while you're the reviewer
  touching the SSOT, check `evals/routing/MISROUTES.md` for any `peer-gate-miss`-classed
  entry; if one exists, surface the promotion obligation to the operator.
- **Memory pointer**: if key facts changed (SSOT version, default pairs, new operating
  commands), update `~/.claude/projects/-Users-USER-DeveloperFolder-example-project/memory/reference_model_effort_routing_system_20260703.md`
  and its MEMORY.md index line.

## Failure modes to refuse

- Editing any consumer surface WITHOUT bumping the SSOT `version:` (stamp checks will
  fail, and rightly so — a content change without a version bump is not a supported path).
- Applying a `prices:` change sourced from memory or a news article alone — `/claude-api`
  verification is mandatory for price rows.
- Bypassing a red `verify-routing.sh` with `--no-verify` or the recovery env escapes —
  those escapes are for hook-documented recovery cases only, never for landing this skill's
  changes.
- Editing `degradation:` in the SSOT without editing `run.py::_FALLBACK_LADDER` FIRST
  (code is authoritative; the SSOT mirrors), or `agents.epic_*` rows without
  `epic-dev-assignments.yaml` (same, sibling-SSOT-authoritative).
