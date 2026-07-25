# /routing-update apply checklist — every surface a model change touches, in order

Run top to bottom after operator approval. Each step names the surface, the edit, and how
it is verified. The authoritative surface SET is the SSOT `consumers:` list + the carve-outs
+ settings.json — if this file and the SSOT `consumers:` list ever disagree, the SSOT wins
and THIS file gets the fix.

| # | Surface | Edit | Verified by |
|---|---|---|---|
| 1 | `~/.claude/model-routing.yaml` | Apply approved diffs to `prices:` / `task_classes:` / `effort_policy:` / `agents:` / `main_session:` / `degradation:`(mirror) / `fanout_policy:`; **bump `version:`**, update `last_reviewed:`; append (never overwrite) a dated `DECISION HISTORY` entry citing the changeset file | guard (a)–(e) downstream |
| 2 | `~/.claude/skills/plan-execute/scripts/run.py` | If the degradation ladder or the set of effort tiers changed: edit `_FALLBACK_LADDER` / `_REASONING_DIRECTIVE` **before or with** step 1 — code is authoritative, SSOT mirrors it | guard check (d) |
| 3 | `~/.claude/epic-dev-assignments.yaml` | If any `epic-*` pin changed: edit here (authoritative), then mirror the SSOT `agents:` rows | `verify-assignments.sh` (guard sub-check f) |
| 4 | `~/.claude/agents/*.md` frontmatter | Re-pin `model:` / `effort:` for every agent row that changed. Invariant: a `model: haiku` agent carries **no** `effort:` key at all | guard checks (a)+(b) |
| 5 | CLAUDE.md digest + `~/.claude/model-routing.digest.md` | Re-render BOTH variants: `python3 ~/.claude/scripts/render-routing-digest.py --variant v0 --install` and `--variant full --install` (use `--check` first to preview). Watch the byte budget — an over-budget digest locks all commits | guard check (e) |
| 6 | Prose stamp consumers | Bump every `<!-- routing-ssot: vN -->` to the new N in each `consumers:` entry with a stamp-checked type (`lint-reads` / `prose-rationale`) — currently `commands/plan-harden.md`, `skills/plan-builder/references/schemas.md`, `skills/plan-builder/references/model-effort-guidance.md`, `skills/plan-builder/SKILL.md` (enumerate from the SSOT, not from this sentence). **A stamp bump asserts the prose agrees** — re-read each surface's model/effort prose against the new SSOT values and fix contradictions before bumping | guard check (c) |
| 7 | `~/.claude/settings.json` | Align `model` with `main_session.advisory_default` family and `effortLevel` with `main_session.default_effort` — **re-confirm with the operator** (user-owned file; changes every future session's default) | guard settings warn-check (full mode) |
| 8 | Hook regexes | ONLY if the `consumers:` set changed: extend `ROUTING_PREFIXES` (`githooks/pre-commit`) and `ROUTING_AFFECTING` (`githooks/commit-msg`) in lockstep | guard surface-list convergence check |
| 9 | Guard | `bash ~/.claude/scripts/verify-routing.sh --full` must PASS. On failure: fix the surface (or the SSOT if it's wrong) and re-run — never loosen the guard, never `--no-verify` | itself |
| 10 | Commit | Stage ONLY the routing surfaces + changeset file; commit message includes `routing-pin-change: approved-by-operator <YYYY-MM-DD>` (checked by commit-msg hook). Hooks stay ON | pre-commit + commit-msg hooks |
| 11 | Deploy | All of steps 1–10 happen in the source worktree `~/your-private-harness`, not in `~/.claude` directly. After the commit (10): push, then `gearbox-deploy` (or `git -C ~/.claude pull --ff-only`) to fast-forward the deploy target, and confirm the guard is green there too | `bash ~/.claude/scripts/verify-routing.sh --full` re-run in `~/.claude` |

Notes:

- **Order matters**: SSOT (1) before renders (5) and stamps (6); code carve-outs (2, 3)
  move with or before the SSOT rows that mirror them; the guard (9) gates the commit (10);
  deploy (11) is always last.
- **Hooks are tracked**: `core.hooksPath=githooks`, so `githooks/pre-commit` and
  `githooks/commit-msg` (step 8) are ordinary repo files — edit and commit them like any
  other surface, no reconstruction needed.
- **Partial-apply recovery**: if interrupted mid-checklist, the guard tells you exactly
  which surfaces still disagree — re-enter at step 9 and fix what it lists.
