# Plan Builder — Build Mechanics & Dashboard Architecture

Reference for build script behaviour, schema version stamping, `--rebuild --preserve-state`, the dashboard JS guard, `--harden` hand-off, registration layers, and the Aurora dashboard's operating principles.

## Contents

- [Plan directory layout](#plan-directory-layout)
- [Schema version stamping](#schema-version-stamping)
- [Rebuilding an executed plan (`--preserve-state`)](#rebuilding-an-executed-plan---preserve-state)
- [Dashboard JS guard (ESLint `no-undef`)](#dashboard-js-guard-eslint-no-undef)
- [Hardening hand-off (`--harden`)](#hardening-hand-off---harden)
- [Registration layers and Orca tab](#registration-layers-and-orca-tab)
- [What the dashboard gives the user](#what-the-dashboard-gives-the-user)
- [Operating principles](#operating-principles)

---

## Plan directory layout

`build_plan.py` validates the spec, runs the dashboard JS guard, and emits a plan directory (default `./_plans/<slug>-<today>/`; a legacy `*.html` output path still writes `PLAN.html` beside the other files):

```
<plan-dir>/
├── PLAN.html              # canonical dashboard + per-item/session state
├── spec.json              # authoring input (kept for rebuilds)
├── manifest.json          # immutable dispatch graph
├── run_state.json         # mutable runtime state (halt, last batch)
├── _closeouts/            # write-ahead log written during execution
├── sessions/
│   ├── s01.prompt.md      # subagent invocation prompt
│   ├── s01.context.md     # per-session agent-spec bundle
│   └── ...
├── run.ndjson             # event log (appended during execution)
└── _changelog.ndjson      # mid-run plan amendments (rendered as "Plan changes")
```

## Schema version stamping

A fresh build stamps the spec's `plan_schema_version` when it declares `8` or `9`, and `7` otherwise. `/plan-execute` accepts `>= 2`; every feature is opt-in by stamp so older plans never gain a new refusal mode retroactively:

- **v3:** `plan_impact` closeouts + REPLAN checkpoint + rendered "Plan changes" section.
- **v4:** mandatory per-item prior-art decision (`prior_art` or `research_status: "skipped"|"unavailable"`, machine-checked probe).
- **v5:** mandatory `decision_brief` on a `BLOCKED` closeout (runtime, off manifest stamp) + build-time enforcement of the frozen parallel-group contract (spec stamp >= 5; specs without it get warnings).
- **v6:** upward escalation of repeatedly failing sessions (`sonnet@high → opus@high → opus@xhigh`, then stops — SSOT v39 withdrew automatic fable rungs; a fable-authored session still climbs `fable@medium → fable@high → fable@xhigh`; opt out with `"escalation": false`) + optional `routing_experiment: {kind, proposal_id}`.
- **v7:** plan-level git isolation (own branch + locked worktree at `begin`, `land` protocol to reach the default branch; `plan_version_gate.ISOLATION_MIN_SCHEMA`; `../../plan-execute/references/plan-isolation-contract.md`).
- **v8:** route at dispatch (`task_class` required per session, `touches` required per item, `model`+`reasoning` optional override pair with `why_model`, `verify.locked`).
- **v9:** required `owner_call` on a `BLOCKED` closeout's `decision_brief`.

Full per-version history and rationale: `plan-schema-history.md`.

## Rebuilding an executed plan (`--preserve-state`)

Per-session and per-item status lives ONLY in `PLAN.html`'s `data-status` attributes — `/plan-execute`'s dispatcher and its structural DONE-gate both read them there — and a fresh rebuild renders every article at `TODO`.

- `--rebuild` over an executed plan is **refused** unless you pass `--preserve-state`.
- `--preserve-state` carries recorded state forward: each article's status, accumulated closeout notes, `data-updated` stamp, shipping badge, and `run_state.json`.
- Sessions or items the rebuild adds start at `TODO`; ones it removes stay gone.
- To deliberately restart a plan from scratch, delete `PLAN.html` first.

Carry-over implementation lives in `../../plan-execute/scripts/article_block.py` next to the writers whose fields it must preserve.

## Dashboard JS guard (ESLint `no-undef`)

The dashboard's status nav (session strip, status card, counters, progress pill) is repainted on every page load by the inline `<script>` reading each `<article>`'s `data-status`. A single runtime `ReferenceError` aborts that repaint mid-run and silently freezes the nav at its authored fallback statuses while the page still looks valid (the `isOpus is not defined` bug of 2026-06-06).

`node --check` cannot catch it (runtime error, not syntax), so `build_plan.py` runs ESLint `no-undef` over the assembled inline script (`scripts/dashboard-eslint.config.mjs`) before writing `PLAN.html` and aborts on any undefined identifier. It degrades gracefully (warn + skip) if Node/ESLint are not installed. If it fails with a legitimate new browser or injected-data global, add it to the config's allowlist; otherwise it is a real bug in `assets/base-template.html` or `assets/infographics/*.js`.

## Hardening hand-off (`--harden`)

If the user passed `--harden` (or asked to "harden", "stress-test", or "pressure-test" the plan), the build hands straight off to `/plan-harden <plan-dir>` automatically, with no user prompt. Because `/plan-harden` is a slash command and this skill stays a pure builder that does not invoke other skills itself, the chain runs at the orchestrator level: finish the build, then immediately invoke `/plan-harden <plan-dir>`.

`/plan-harden` runs its adversarial pre-flight (parallel enrichment → `/grill-with-docs` + `/adversarial-review` → Klein premortem → severity-tagged synthesis), applies surviving hardenings into session prompts, **backports them into `spec.json`**, and rebuilds so a later `--rebuild` cannot wipe them.

- **Opt-in by design:** hardening is heavy (the Codex verify loop dominates, ~50–200k tokens). Only chain when `--harden` is present.
- **`--harden --quick`** forwards `--quick` to `/plan-harden` (skips enrichment + premortem, keeps the adversarial verify loop).
- **Automatic, never asked:** run straight through without stopping to confirm the hand-off (genuine human checkpoints inside `/plan-harden` still stop).
- After `/plan-harden` returns, report the plan as **hardened** (revision type + any plan-killers addressed).

## Registration layers and Orca tab

- **Layer 1 — Default location:** `_plans/<slug>-<YYYY-MM-DD>/` under the project root.
- **Layer 2 — Project index + `.gitignore`:** `--register-in <project-root>` updates `<project-root>/_plans_index.md` with a `/plan-execute` run command and amends `.gitignore` so runtime state (`.lock`, `_closeouts/`, `run.ndjson`, `run_state.json`, `HALT_NOTICE.txt`, `LAND_NOTICE.txt`, `_worktrees/`) stays local while `PLAN.html`, `manifest.json`, and `sessions/*` are committed.
- **Layer 3 — Pointer in `CLAUDE.md`:** `--register-in` prints a one-line snippet to paste into the project's `CLAUDE.md`.
- **Orca tab:** when `orca status --json` reports `result.app.running: true`, also open `PLAN.html` in an Orca tab in the plan repository's own workspace:
  ```bash
  orca tab create --url file://<abs PLAN.html> --worktree path:<repo root> --json
  ```
  and tell the user the tab is open. Without `--worktree`, the tab lands in the current session's workspace rather than the plan's.

## What the dashboard gives the user

The Aurora page is state-first (what is done, what runs next, and what needs a decision come before any story):

1. **Editorial page header** — serif title and subtitle (not sticky).
2. **Sticky session navigation strip** — 1 chip per session, color-coded by status, scrolls sideways and centres the next session.
3. **Status card** — big `% of items done`, one status line (sessions, items, blocked, open decisions), next session, and "Open sNN" button (says "Finished" when all closed).
4. **Open decisions + Decision hotspots** — what the owner must still decide, high on the page.
5. **"The plan, explained"** — eli5-derived explainer: causal chain with live status + "You are here", live "Where we are" paragraph, qualified analogy, defined terms, one-line recap.
6. **Plan Achievement infographic** — one of 5 structured SVG templates or `custom` (hidden on phone).
7. **Status + category filter chips and categories status grid** — filter cards; one progress-bar row per category.
8. **Session Plan section** — one card per session (step indicator, title, status pill, model/effort/reasoning chips, `human_summary`, `deliverable`, items in scope, dispatch row, `/plan-execute … --session sNN` command, collapsible agent spec, notes).
9. **Category sections** — one per category with item cards in the same dual-layer pattern.
10. **Fold of finished and later cards** — `DONE`, `WONTFIX`, `DEFERRED` cards and `TODO` sessions after the next one fold to one row; a `BLOCKED` session outranks every other as "next".
11. **Out of scope + Plan changes** — settled boundaries and amendments at the end.
12. **Operating Manual** — collapsed at the end of `<main>`, fully in DOM for any Claude opening the file.
13. **Floating progress pill** — bottom-right on wide screens once scrolled past the status card.
14. **Light + dark theme** — auto via `prefers-color-scheme` + manual cycle button.

## Operating principles

- **Dual-layer principle:** one document, two audiences — prominent serif human layer (`human_summary`, `deliverable`) and visually quiet `<details class="agent-spec">` agent layer always in the DOM. If the human layer cannot stand on its own, the plan will not survive a quarter on the shelf.
- **Operating Manual:** embedded at the end of `<main>` describing `/plan-execute`, the `<!-- ARTICLE:<id> -->` mutation surface, auto-recomputed widgets, and the 8 statuses. Built by `build_plan.py`; do not remove or restructure.
- **Comment anchors:** every `<article>` is wrapped in `<!-- ARTICLE:<id>:BEGIN -->` / `:END -->` placed *between* elements (HTML5 forbids comments inside start tags). Gives the orchestrator a collision-proof target for atomic whole-block replacement. Never move anchors inside `<article>` or remove them.
- **Card ids + layout self-audit:** every session/item `<article>` carries `data-session-id` / `data-item-id`, and `PLAN.html` ships `<script data-layout-audit>` + `#layout-audit-banner` to catch horizontal overflow or clipped text after fonts/layout settle. Automatic; do not remove.
- **Split state:** `PLAN.html` is canonical for per-item/session status (`data-status`, `data-updated`, notes); `manifest.json` is the **immutable** dispatch graph; `run_state.json` holds mutable runtime state (gitignored); `_closeouts/<sid>.json` is the write-ahead log. **NEVER put mutable runtime state in `manifest.json`.**
- **Subagents never edit the plan:** dispatched subagents return a `<plan-execute-closeout>` JSON block; the orchestrator does every write to `PLAN.html`, keeping mutation crash-safe and idempotent.
