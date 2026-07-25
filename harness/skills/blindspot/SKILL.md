---
name: blindspot
description: Runs a "blindspot pass" over an unfamiliar code/vault area before you touch it — digs through git history and current files to surface unknown unknowns (landmines, unwritten conventions, reverted attempts, missing concepts) and hands back one assembled improved prompt to actually use. Trigger on "blindspot pass", "I've never touched this module", "what am I walking into", "unknown unknowns in this codebase/area", "scan this unfamiliar area before I start", or any request to scan an unfamiliar directory/module before making changes there.
---

# Blindspot pass

A read-only reconnaissance scan of a target path (directory or module) that a
task is about to touch for the first time. The point: git history and code
structure already contain the landmines a first-timer would otherwise hit —
this surfaces them before the edit, not after the incident.

## Scan method (run all five, skip only if genuinely inapplicable)

- [ ] **Git archaeology.** `git log --oneline -- <path>`, then look for reverts
   (`git log --all --grep="[Rr]evert" -- <path>`), stalled migrations (a
   commit that renames/introduces a pattern that siblings never adopted), and
   TODO age (`git log -S"TODO" --oneline -- <path>` + `git blame` on any TODO
   line still present — old TODOs are usually dead ends, not backlog).
- [ ] **Config/flag divergence.** Grep the target for env-gated branches
   (`if.*env`, feature flags, `NODE_ENV`, `DEBUG`, config file overrides) and
   check whether dev defaults actually match what prod loads.
- [ ] **Invisible wiring.** Find files in the target that are never imported
   anywhere obvious — registries, plugin loaders, `__init__.py` exports,
   dependency-injection containers. A file with no visible caller is either
   dead or wired through a mechanism the interface doesn't show.
- [ ] **Sibling-template trap.** List files of the same shape (e.g. all
   handlers, all routes) sorted by last-modified. Diff the newest against the
   median-age one. The newest is not automatically the right one to copy —
   check if it was itself a one-off fix, an in-progress migration, or an
   abandoned experiment (git log tells you which).
- [ ] **Unwritten conventions.** Diff naming/structure across 3+ sibling files
   to spot a pattern no doc states (e.g. every handler validates input except
   one; every module exports a `default` except this one).

Classify every finding as one of: **Landmine** (will actively break something
if touched naively), **Convention** (unwritten rule the code silently
enforces), **History** (a past attempt/revert/stall worth knowing about),
**Missing concept** (something the interface implies exists but doesn't, or
vice versa).

## Output

Produce ONE self-contained HTML file (no external requests, no CDN links,
works in light+dark via `prefers-color-scheme`): a scan-stats header
(files/commits scanned, findings count by category), one card per finding
(what / why it bites / a copyable prompt-fix snippet with a copy button —
`navigator.clipboard.writeText`), and a footer that concatenates every
prompt-fix into one assembled "improved implementation prompt" with its own
copy button. Load `references/design.md` before writing the HTML for the
concrete markup/CSS/JS shape to reuse.

If there is no surface to render HTML (no Artifact tool, no file the user can
open), fall back to markdown with the same structure: `## Scan stats`, one
`### [Category] <title>` section per finding with **What** / **Why it
bites** / **Prompt fix**, then `## Assembled prompt` at the end.

Findings must be concrete — named files, real commit hashes/dates, actual
line numbers — never generic advice ("write more tests", "add docs"). If a
draft comes out generic, redo the git/grep digging before writing it up;
iterate at most once.
