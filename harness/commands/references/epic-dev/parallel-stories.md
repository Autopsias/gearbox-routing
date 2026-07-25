# Parallel stories (`--parallel-stories K`) — opt-in, worktree-isolated

Runs **independent** stories of an epic concurrently — each its own full BMAD pipeline in its own
git worktree — instead of strictly serial. This is the biggest wall-clock lever, but it is **only
safe for genuinely file-disjoint stories**. Default OFF; serial is the correct default. Within a
story, nothing parallelizes (BMAD's TDD pipeline RED→GREEN→review is inherently ordered).

## Contents

- [Hard preconditions — refuse to parallelize unless ALL hold](#hard-preconditions-refuse-to-parallelize-unless-all-hold)
- [Mechanism (orchestrator-driven; manual worktrees for controllable merge-back)](#mechanism-orchestrator-driven-manual-worktrees-for-controllable-merge-back)
- [Safety rails (non-negotiable)](#safety-rails-non-negotiable)
- [When NOT to use (be honest)](#when-not-to-use-be-honest)
- [Status (v1)](#status-v1)


## Hard preconditions — refuse to parallelize unless ALL hold

- **Declared independence (never inferred).** Only stories the operator has EXPLICITLY marked
  independent run in parallel. Resolve the declaration from the first that exists:
  1. a `parallel_group: <name>` field in the story files' metadata, OR
  2. an explicit `--stories S1,S2,…` list passed with `--parallel-stories K`.
  Stories NOT in a declared group run **serially**, as normal. BMAD stories in an epic usually share
  files and have implicit ordering — parallelizing those blind is merge corruption. **Heuristic
  auto-detection is forbidden** (you cannot know a story's file footprint until it is implemented).
- **Clean working tree** at start (`git status --porcelain` empty) — worktrees branch from a known base.
- **`K ≥ 2`**, and the effective concurrency is `min(K, group_size, 4)` (cap 4 — more invites
  resource thrash and merge churn).
- Stories of `story_type` `infrastructure`/`uat` are **excluded** from parallel groups (they need
  manual work) — they stay serial.

## Mechanism (orchestrator-driven; manual worktrees for controllable merge-back)

1. **Base.** Commit/clean the epic branch. `BASE=$(git rev-parse HEAD)`.
2. **Worktree + branch per story** (for the declared group, up to the concurrency cap):
   ```bash
   git worktree add -b "epic{N}/story-{Si}" "{worktree_root}/{Si}" "$BASE"
   ```
   Each worktree is an isolated checkout on its own branch from `BASE`.
2b. **Warm each worktree — provision deps + build cache before its gates run (the cold-checkout fix).**
   `git worktree add` checks out only *tracked* files, so a fresh worktree has **no `.venv` /
   `node_modules` / build cache** (those are gitignored). A story-runner's first `uv run pytest` (or
   equivalent) then either fails outright or pays a cold dependency install. Provision each worktree
   once, right after it is created and before its runner reaches a verification phase:
   - **Resolve the provision command** from project policy first — a `provision_worktree` entry in the
     `CLAUDE.md` build section or `.claude/build-policy.md` (the same place the conductor reads policy;
     this is the treehouse `post_create`-hook idea, declared per project). If undeclared, infer from the
     repo-root lockfile: `uv.lock` → `uv sync` *in the package dir*, `pnpm-lock.yaml` →
     `pnpm install --frozen-lockfile`, `go.mod` → `go build ./...` (warms the build cache). If none can
     be inferred, log `warm: skipped (no provision command)` and let the runner provision itself —
     never guess a destructive command.
     - *example-project:* `cd apps/api && uv sync --all-extras`.
   - **The shared global cache is the whole win.** `uv` (`~/.cache/uv`), `pnpm` (its content-addressable
     store), and `go` (build/mod cache) are **global and shared across all worktrees**, already warm from
     the main checkout — so provisioning a worktree *links from cache in seconds*, no re-download. That
     shared warm cache is exactly what treehouse's pool gives for free, and what makes per-worktree
     provisioning cheap here without adopting the binary.
   - **NEVER copy or symlink a `.venv` / editable install between worktrees.** An editable install records
     the absolute path of the source tree it was synced in — a copied/symlinked venv would import the
     **main checkout's** code, not the runner's worktree, silently breaking isolation (the runner edits its
     worktree but tests would exercise main's code). Always run the provision command *inside* the target
     worktree so its editable install points at its own source.
   - **Run warms concurrently** (one per worktree, same fan-out as the runners) or fold provisioning into
     the runner's first action — either way it is parallel and the shared cache keeps each one cheap.
   - **Provision failure is fail-closed, not best-effort.** Unlike a treehouse lifecycle hook (which
     logs-and-continues), deps here are a precondition for the story's gates: a failed provision makes the
     runner return `blocked` with `blocked_reason: provision failed — <tail>`, never run cold into
     confusing red gates.
3. **Dispatch one story-runner subagent per worktree, concurrently** (cap-at-a-time, in a single
   message so they fan out). Each runner is told to do ALL its work **inside its provisioned worktree
   path** `{worktree_root}/{Si}` (warmed in step 2b) and runs story `Si`'s **full BMAD pipeline there** — the 8 phases + their
   gates, using the epic-* agents at the `epic-dev-assignments.yaml` model/effort, **FAIL-CLOSED**
   (a red gate quarantines the story; it does NOT merge). The runner:
   - works only under its worktree; commits its result on its branch;
   - does NOT touch the shared `sprint-status.yaml` (the orchestrator owns status, applied at merge);
   - returns a structured result: `{story, final_status: done|blocked, gate_decision, branch, blocked_reason}`.
   (For parallel stories the per-phase drive is delegated to the runner — the deliberate trade for
   concurrency. Serial stories keep the orchestrator's per-phase drive.)
4. **Merge back SEQUENTIALLY** (one at a time — serialize the shared tree + status writes):
   for each runner with `final_status=done`, `git merge --no-ff "epic{N}/story-{Si}"` into the epic branch.
   - **Clean merge** → update `sprint-status` story `Si` → `done`; drop the worktree + branch.
   - **Conflict** → **HALT this merge** (NEVER auto-resolve). Mark `Si` blocked
     (`STORY_BLOCKED: merge conflict — stories were not file-disjoint`), keep its worktree/branch for
     the operator, and continue merging the others. A conflict means the independence declaration was
     wrong — surface it loudly.
   - `final_status=blocked` runner → leave its worktree for inspection; mark `Si` blocked; surface.
5. **Post-merge integration gate (REQUIRED).** Each per-worktree gate validated its story in
   ISOLATION; the merged combination must also be green. Run the suite once on the merged epic branch.
   If red → **HALT** (disjoint files but interacting behavior — the operator resolves). This is the
   safety net the isolated gates cannot provide.
6. **Cleanup + report.** Remove merged worktrees/branches (`git worktree remove`, `git branch -d`).
   Report per story: done / blocked / merge-conflicted, with the resume path for any blocked.

## Safety rails (non-negotiable)

- Undeclared stories NEVER parallelize — serial is the default.
- A merge conflict NEVER auto-resolves — it halts + quarantines (the declaration was false).
- Per-worktree gates stay fail-closed — a story with red tests does not merge.
- A post-merge integration suite run catches cross-story interactions the isolated gates miss.
- The orchestrator owns ALL `sprint-status.yaml` writes (applied sequentially at merge) — runners
  never write shared state.
- BMAD workflows (`bmad-*`) are invoked inside each worktree, never reimplemented.
- Worktrees are PROVISIONED (deps/build cache) before their gates run (step 2b) — always provision
  *in-tree* via the shared global cache; NEVER copy/symlink a `.venv`/editable install across worktrees
  (it breaks isolation). A provision failure blocks the story; it does not run cold.
- Composes with `--auto`: an autonomous run dispatches each declared parallel group via this
  mechanism, then resumes the serial loop for the remaining (non-grouped) stories.

## When NOT to use (be honest)

Cohesive epics where stories build on each other or share files — the independence declaration would
be false and every merge would conflict. Serial (the default) is correct there. Parallelism pays off
ONLY for module-separable epics: independent connectors, separate endpoints, isolated modules — where
stories provably touch disjoint files. If you are unsure whether two stories are disjoint, they are
not: run them serially.

## Status (v1)

This is the v1 mechanism with the guardrails above. It has no automated test harness (epic-dev is
orchestration, not code), so it is deliberately conservative — declared-only, conflict-halting,
integration-gated. Start with `K=2` on an epic you KNOW is module-separable before trusting it wider.

**v1.1 (2026-06-22) — warm-worktree provisioning (step 2b).** Adds a per-worktree provision step so
parallel story-runners start from warm deps/build cache instead of a cold checkout — the borrowable
idea from the `treehouse` worktree-pool tool (a `post_create`-style provision hook + reliance on the
shared global package cache), ported in-mechanism rather than by adopting the binary (treehouse's
subshell-bound, no-programmatic-acquire design can't be driven by an orchestrator that fans out
worktrees to Task subagents). The cross-run *pool reuse* treehouse also offers is deliberately NOT
built — these worktrees are still created and destroyed per run.
