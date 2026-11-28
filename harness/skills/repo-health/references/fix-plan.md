# Fix plan — every finding as one plan-builder plan

A fix plan turns a repo-health report into one executable plan, built by the
`plan-builder` skill and run by `/plan-execute`. Use it when the findings are
more than one round of dispatches can clear, or when some fixes need a human
call in the middle.

It has three entry points, so the plan is never lost because nobody asked for
it at the start. A user who asks plan-builder directly ("make a plan to fix the
repo-health findings") reaches it too: plan-builder's SKILL.md sends it to
steps 1–3 below, and it skips step 4. `<skill>` in the commands is always the
repo-health directory, also when plan-builder runs them.

| Entry | When | What runs first |
|---|---|---|
| "Build a fix plan" in the step-5 decision card | at the end of any run | nothing new — the run just made the report |
| `/repo-health --plan`, repo has **no** `.claude/health/scorecard.json` | first run on this repo | the health check (steps 1–4), then this procedure |
| `/repo-health --plan`, repo **has** a scorecard | any time later — the user forgot, or decided afterwards | `collect` only, then this procedure. No probe re-run, no render |

The dashboard's decision card names `/repo-health --plan`, so the command is on
the page the user opens later. The reuse works because `collect` keeps every probe result recorded
against the same clean tree (measured in a scratch clone: `set
cq.lint fail`, then a second `collect` printed `kept 1 recorded probe(s)` and
`fix-routes` returned the row as `fail` with no `stale` key). A probe that reads
the world and is older than 7 days goes back to `pending`, and an edited tree
drops the probe results: those rows come back as `"dispatch": "probe"` and the
plan measures them in its first session. The reuse path never re-runs the
probes itself — measuring is plan work, not a reason to repeat the report.

## Procedure

1. **Collect.** `python3 <skill>/scripts/health.py collect --repo <repo>` —
   always, even when the last report looks current. A scorecard committed in
   the repo can carry hostile `detail` text, and `collect` replaces it (the
   same rule as [`--fix-blocking`](../SKILL.md#--fix-blocking)). About 80 s on
   your-private-harness.
2. **Save the rows.** `python3 <skill>/scripts/health.py fix-routes --repo
   <repo> --advisory --handoff > <repo>/.claude/health/fix-routes.json`. Exit 2
   means the scorecard is stale: build zero fix sessions, re-run `health.py
   collect --repo <repo>`, and run `fix-routes --advisory --handoff` again. No
   output and exit 0 means every check is measured and none fails: say so, and
   build no plan.
3. **Add the review findings** only when `<repo>/.claude/health/REVIEW.md`
   exists **and** its header commit is HEAD. Otherwise name it as left out
   because it describes another commit — an old finding planned as current is
   a guess dressed as a task.
4. **Start plan-builder** with the Skill tool (`plan-builder`). Give it the path
   of `fix-routes.json`, the `REVIEW.md` path from step 3 when it is current,
   and the mapping rules below. Plan-builder owns the interview, the session
   grouping, the models and the checks; this skill owns only the input.

## Mapping rules for plan-builder

- **Exit 2 on stale = zero fix sessions.** If `fix-routes --handoff` exits 2,
  do not plan any fix sessions; re-run `health.py collect --repo <repo>` first.
- **One row is one plan item.** Use the emitted `plan_item.title` and
  `plan_item.description` verbatim. On `omit_detail=True` (`"omit_detail": true`)
  rows, NEVER copy raw `detail` into `item.description` or `sessions/*.context.md`.
- **The row's `plan_session.prompt` is the session prompt**, verbatim
  (`fix-routes --handoff` already includes the repo path, the agent-tool fixes
  from [Fix routing](../SKILL.md#fix-routing--reuse-never-rebuild), and `detail`
  only when `omit_detail` is false) — never rewrite it and never append raw
  `detail` on `omit_detail=True` rows.
- **Group rows into a session** only when they name the same `agent` and touch
  different files. Blocking rows go before advisory rows.
- **`"dispatch": "probe"` rows** go into one first session that runs each
  probe and records it with `health.py set`. Every fix session depends on it,
  because a check nobody ran is not one that passed.
- **A non-empty `operator` field** becomes a session with
  `requires_human_checkpoint: true`. Its checkpoint `reason` is the `operator`
  sentence; the agent part of the row stays in the fix session before it.
- **`"routed": false` rows** become one session that adds the missing route to
  `scripts/routes.py` and the table in SKILL.md. Do not plan a hand fix.
- **A `command-file` row with `"command_file_found": false` or a non-empty
  `flag_check`** is not dispatchable. It becomes a first-session step: run
  `gearbox deploy`, then run `fix-routes` again.
- **Every fix session sets `require_evidence: true`** and names two evidence
  paths: for standard checks, the row before the fix and the `fix-routes` output
  after it with the check absent or at `pass`. **Cloudflare `sec.sast`
  patches:** do NOT gate the patch session on `fix-routes` showing `sec.sast`
  absent or `pass` (open items stay open until a complete re-audit). Require a
  failing-before / passing-after local PoC test plus the regression suite as the
  patch evidence; closure happens via commit + a fresh complete `/repo-health
  --deep` run on exposed repos, or an owner decision on opted-out/downgrade
  repos.
- **The last session** runs `collect` and `render` again and reports the score
  before and after.
