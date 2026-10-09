---
name: repo-health
description: The one front door for whole-repo reviews. Scores repo health — security, code quality, tests, CI workflow, AI readiness, hygiene — on a persistent dashboard with run history at .claude/health/HEALTH.html, split into BLOCKING (fix now) vs ADVISORY; on request also reviews refactoring (module design), over-engineering (a quick scan that names /declutter for the deep audit), performance (measured hot paths) and the trust ladder (which of the repo's own rules and lessons could become a lint, test or code change), and ends with one summary and one decision card. Routes each fix to the existing specialist skill. Use when the user says "repo health", "review this repo", "health check on this repo", "does this repo need refactoring", "is this repo over-engineered", "make this repo more secure or faster", or "run repo-health". Not for reviewing a diff (/review) or fixing one known-failing area — route those directly (/code-quality, /test-orchestrate, /ci-orchestrate, security-scanner agent).
disable-model-invocation: true
---

# repo-health

A health-check run produces three things in the target repo's `.claude/health/`:
`scorecard.json` (this run's raw results), `HEALTH.html` (the standing
one-pager dashboard — regenerated in place every run), and `history.jsonl`
(one line per run, drives the trend dots). A run that includes a review also
writes `REVIEW.md` there ([Pick what to review](#pick-what-to-review)). The check
catalog, tiers, and the external sources behind them:
[references/checks.md](references/checks.md).

Tier meanings — **blocking**: fix immediately (live secrets, dangerous
workflow patterns, vulnerable dependencies, red test suite). **advisory**:
improves the repo, never gates. Verdict: any blocking fail → AT RISK; any
advisory fail or unrun blocking probe → NEEDS ATTENTION; else HEALTHY.
The dashboard also carries a 0–100 score (aim: 100); formula and caps:
[references/checks.md](references/checks.md#scoring).

`<skill>` below is the directory this SKILL.md sits in — Claude Code hands the
skill its own base directory at invocation, so use that. Never spell it out as
the deploy path under `~/.claude`: that is the **deploy target**, so a run from
a source checkout would silently execute the stale deployed copy of every
script and report on code that is not the code under review.

## Pick what to review

`/repo-health` is the one front door for whole-repo reviews. Its arguments name
the goals. With none, ask ONE `AskUserQuestion` call holding TWO multi-select
questions — the tool shows at most four options per question, so a single
question silently drops goals:

- **Checks:** `health` (first, marked recommended), `security`, `performance`.
- **Design reviews:** `refactoring`, `over-engineering`, `trust-ladder`.

Every goal selected across both questions is the goal set; all six selected is
`all`. Nothing selected in either question means `health`. When no question
tool is available (a headless run), run `health` only.

| Goal | What runs | Procedure |
|---|---|---|
| `health` | the health check: steps 1–5 of the workflow | [Workflow](#workflow) |
| `security` | the health check, plus the first `--deep` pass: the whole-repo security review | [`--deep`](#--deep-optional-slower) |
| `refactoring` | a design review, plus the `cq.*` rows when the health check also ran | [reviews.md § Refactoring](references/reviews.md#refactoring) |
| `over-engineering` | a quick scan; the deep audit stays `/declutter` | [reviews.md § Over-engineering](references/reviews.md#over-engineering) |
| `performance` | measurements, then a hunt along at most five hot paths | [reviews.md § Performance](references/reviews.md#performance) |
| `trust-ladder` | counts the repo's rules against its checks, then places each correction on the layer that enforces it and names at most five that could move to a stronger one | [reviews.md § Trust ladder](references/reviews.md#trust-ladder) |
| `all` | every row above | — |

`--deep` and `--fix-blocking` keep their meaning, and both imply `health`.

**Only the user can start `/declutter`.** Claude Code blocks a model call to a
skill that sets `disable-model-invocation: true`, and tells the model not to
copy its steps another way (docs: skills, "Control who invokes a skill"). So
this skill never runs it; the over-engineering review names it in the decision
card, with its cost.

**Render only when the health check ran.** `render` appends a line to
`history.jsonl`, and a run that measured nothing would enter the trend as a
data point. A run that includes any review ends with
[the summary](references/reviews.md#summary): one summary and one decision card
for everything that ran.

## Workflow

Steps 1–5 are the health check. Run them when the goals include `health`,
`security` or `all`; otherwise go straight to the reviews. Copy this checklist
and tick it as you go:

```
[ ] 0. goals picked   [ ] 1. collect   [ ] 2. probes recorded   [ ] 3. render
[ ] 4. dashboard delivered   [ ] 5. verdict + decision reported
[ ] 6. reviews run + summary written (only when a review was picked)
```

1. **Collect.** Target repo = cwd unless the user names another.
   `python3 <skill>/scripts/health.py collect --repo <repo>`
   This runs every deterministic check and prints the pending probes.
   Re-running is safe: probe results already recorded against this exact clean
   tree are kept (the run says how many it kept). `--fresh` discards them.
   Four probes read the **world**, not the tree — `sec.dep-vulns`,
   `hyg.dep-freshness`, `sec.vendor-pins`, `sec.supply-chain` — so an unchanged
   tree is no evidence they still hold (a CVE lands, upstream moves) and they
   are kept only for 7 days before going back to `pending`.
   Every check id appears on every run — one that does not apply reads `na`
   with the reason, so a shrinking scorecard is never mistaken for health.
2. **Run each pending probe** from the repo root and record it with
   `python3 <skill>/scripts/health.py set <check-id> <status> "<one-line detail>" --repo <repo>`.
   Every row below is a probe the collector can leave `pending`, and every
   `pending` id has a row — `test_skill_md_documents_every_probe` fails the
   suite if that stops being true, because a pending check with no instruction
   is a check nobody runs.

   | Probe | Command | pass / fail rule |
   |---|---|---|
   | `sec.secrets-history` | `gitleaks git . --no-banner --redact` (older CLI: `gitleaks detect --source .`) | exit 0 → pass; leaks → **fail**, name count only — never paste a secret |
   | `sec.dep-vulns` | `pip-audit` (or `uvx pip-audit`) / `npm audit --audit-level=high` | high/critical with a fix → **fail**; lower only → warn |
   | `sec.diff-review` | dispatch scoped reviewer agents — [Reviewing the diff without the operator](#reviewing-the-diff-without-the-operator) | confirmed exploitable finding → **fail**; else pass with "N code files across M scopes reviewed" |
   | `sec.sast` | `semgrep scan --config p/default --config p/python --error` (drop `p/python` on a JS/TS repo; fallback: bandit) | triage findings in context: confirmed high → **fail**; noise/annotated → warn |
   | `sec.supply-chain` | Trail of Bits' `supply-chain-risk-auditor` from [trailofbits/skills](https://github.com/trailofbits/skills), installed by you (it is not vendored here: CC-BY-SA-4.0 is share-alike): its `scripts/collect.py . --json <findings>` then its `scripts/render.py <findings>` — take `<findings>` from the row, which names a **fresh per-run file**; never a shared path, or a second run on another repo renders these findings as its own | advisory finding or abandoned upstream → warn; unfixed critical in the tree → **fail**. Minutes for ~50 deps; `--offline` skips the network |
   | `sec.vendor-pins` | `python3 <skill>/scripts/vendor.py verify` then `python3 <skill>/scripts/vendor.py check` | exit 0 → pass; verify red, exit 1 (a vendored file was edited) → **fail**; upstream moved → warn. **Exit 2 is `REFUSED`, never a pass**: the manifest could not be read, or does not cover every tree in `vendor/`, or `gh` could not reach upstream. Some tree was NOT compared, so LEAVE the check `pending` (never `na` — that reads as "does not apply here" and clears it off the fix list), fix the cause, and re-run |
   | `cq.lint` | `ruff check .` on a Python repo, `npx eslint .` on a JS/TS one (both when it is both) | exit 0 → pass; else fail with count (apply `--fix` safe fixes first) |
   | `cq.complexity` | `ruff check --select C901 .` | same |
   | `cq.slop` | `ruff check --select F401,F841,E722,ERA001,BLE001 .` | count as warn; 0 → pass |
   | `cq.ratchet` | the repo's own quality checkers (when present) | red ratchet → fail; route it (see [Fix routing](#fix-routing--reuse-never-rebuild)) |
   | `hyg.dep-unused` | `deptry .` (or `uvx deptry .`) | findings → warn; na without manifests |
   | `hyg.dep-freshness` | `pip list --outdated` / `npm outdated` count | large/growing count → warn |
   | `test.suite` | the printed suite command | green → pass; red → **fail** with failing-test names |
   | `test.runtime` | wall clock of the suite run | < 300 s → pass; else warn with seconds |
   | `test.collection-cost` | `time pytest --collect-only -q` | < 10 s **and** < 15% of the suite's wall clock → pass; over either → warn; over 25% → **fail**. Record both numbers, not a verdict |
   | `test.parallel-safety` | `pytest -p randomly` and `pytest -n 2`, twice each | both green twice → pass; any disagreement → warn naming the shared state it exposed; xdist/randomly not installed → `na` with that reason (the collector cannot see whether they are) |
   | `ci.wall-clock` | `gh run list --limit 20 --json durationMs,conclusion,workflowName` | median of successful runs ≤ 10 min → pass; else warn with the median; `gh` missing or unauthenticated → `na` with that reason |

   A tool that is not installed and not worth installing now: set `na` with
   the reason. Never leave a probe `pending` when the tool ran.
3. **Render.** `python3 <skill>/scripts/health.py render --repo <repo>` — prints
   the verdict and writes `HEALTH.html` (appends the history line).
4. **Deliver the dashboard.** Send `HEALTH.html` to the user (render view).
   If `.claude/health/artifact-url.txt` exists, republish the Artifact to
   that URL; otherwise publish a new Artifact and save its URL to that file
   — one stable URL per repo across runs.
5. **Report and decide.** Lead with the verdict. List blocking items with their
   fix routes (from `fix-routes`, below). End with the dashboard's decision card
   (max 3 options); when a review also ran, fold it into the summary's one
   decision card instead. Fix nothing without a go-ahead — except under
   [`--fix-blocking`](#--fix-blocking). **Once the go-ahead comes**, run
   `fix-routes --repo <repo> --advisory` and dispatch those rows the same way
   (steps 3–5 of `--fix-blocking`); never hand-write a dispatch prompt the tool
   already emits.

## Fix routing — reuse, never rebuild

**How a fix actually gets dispatched — probed live.** Two mechanisms
were run against this harness, not reasoned about:

| Mechanism | What actually happened | Verdict |
|---|---|---|
| Spawn a subagent and have it fire the slash command | `digdeep` and `linting-fixer` both **declare `SlashCommand`** in their frontmatter `tools:`. Spawned, neither was handed it — digdeep reported 10 tools, linting-fixer 3, no `SlashCommand` in either | **dead**. A route that names only a slash command is operator homework wearing a command's clothes |
| Spawn a subagent whose prompt points at the command's own **file** | It followed `commands/code-quality.md` with `$ARGUMENTS = --check --path=skills/repo-health` end to end and returned real findings | **primary**. A command file is a prompt |

The primary mechanism has one sharp edge the probe hit: command files were
written for the main session, so they say `Task(...)`, `SlashCommand(...)` and
`AskUserQuestion(...)`, none of which a spawned agent has. The dispatch prompt
must tell the agent to use the **Agent** tool where the file says `Task`, and to
skip any chaining or resume step rather than stall. `fix-routes` emits that
prompt already built — do not hand-write it.

| Failing check | Dispatch — repo-health fires this itself | Operator-only part |
|---|---|---|
| `sec.secrets-history`, `sec.tracked-sensitive` | Agent `general-purpose`: `git rm --cached` the file, add the matching .gitignore rule, report kind + location only — never the value | **yes** — rotate every leaked credential; a history rewrite is a human call |
| `sec.dep-vulns` | Agent `security-scanner`, `--mode=fix`: upgrade to the fixed versions, re-run the audit, report before/after counts | no |
| `sec.sast` | Agent `security-scanner`, `--mode=fix` over findings triaged in context | no |
| `sec.diff-review` | Agent `security-scanner`, `--mode=fix` over the **confirmed** findings only — the false-positive bar ran before they were recorded, so the fixer does not re-triage | no |
| `sec.dep-update-config` | Agent `ci-infrastructure-builder`: `.github/dependabot.yml` for every ecosystem with a tracked manifest, plus `github-actions`, weekly | no |
| `sec.workflow-permissions`, `sec.action-pinning`, `sec.dangerous-workflow` | Agent `ci-infrastructure-builder`, one prompt per check: least-privilege top-level block / full-SHA pins with a version comment / the injection path, reading `<skill>/vendor/gha-security-review/SKILL.md` first | no |
| `test.suite` | Agent `general-purpose` following the `test-orchestrate` command file with `$ARGUMENTS = --run-first` (fixing is its default; `--run-first` stops it answering from a stale cache — it has no `--fix`); flaky suspicion → the sibling `flake-detective` skill's `SKILL.md`, same mechanism | no |
| `test.runtime`, `test.collection-cost`, `test.parallel-safety` | Agent `ci-infrastructure-builder` following the **Test & CI speed — the ladder** section below, entering at the rung the measurement already reached: rung 1 measure / rung 2 collection cost / rung 3 shared state. `-n auto` stays closed until rung 3 is green twice | no |
| `cq.file-size`, `cq.function-length`, `cq.complexity`, `cq.ratchet` | Agent `general-purpose` following the `code-quality` command file with `--fix`, `--fix --focus=function-length`, `--fix --focus=complexity`, `--adopt` respectively | no |
| `cq.lint`, `cq.slop` | Agent `linting-fixer` | no |
| `ci.timeouts`, `ci.concurrency`, `ci.caching`, `ci.retention`, `ci.wall-clock` | Agent `general-purpose` following the `ci-orchestrate` command file — the workflow-config four get the gap as the `issue` positional plus `--check-actions --fix-all` (its fix switch is `--fix-all`, not `--fix`); wall-clock gets `--strategic` | no |
| `ci.server-side-gate`, `ci.pre-commit` | Agent `ci-infrastructure-builder`: one workflow (or one hook config) calling the SAME contract the local gate runs, never a second copy of the commands | no |
| `hyg.tracked-junk` | Agent `general-purpose`: `git rm --cached` + extend .gitignore, never delete the working copy | no |
| `hyg.readme` | Agent `general-purpose`: a quickstart whose setup/run/test commands are copied out of the Makefile, `package.json` or CI — and **run** before they are written down | no |
| `hyg.notebook-outputs` | Agent `general-purpose`: nbstripout the named notebooks, wire it into the existing hook config, touch no source cell | no — but the agent is told to say so if an output held a credential, and rotating that is yours |
| `hyg.dep-unused` | Agent `general-purpose`: drop each unused dependency, **declare** each undeclared one (a transitive import is a break waiting to happen), re-run deptry/knip and the suite | no |
| `hyg.dep-freshness` | Agent `general-purpose`: upgrade inside the manifest's declared constraints, refresh the lockfile with the repo's own tool, run the suite, report before/after | **yes, in part** — it lists the major-version bumps and takes none; those break APIs and are your call |
| `ai.verify-command` | Agent `ci-infrastructure-builder`: ONE `make check` calling the lint/type/test commands that already exist — never a second copy of them — then named in AGENTS.md | no |
| `ai.lockfiles` | Agent `general-purpose`: generate the lockfile with the repo's own tool (never by hand), or pin the named unpinned requirements, then run the suite | no |
| `hyg.large-files` | — | **yes, wholly.** A >5 MB tracked file is as likely to be a needed fixture as a mistake, and LFS / release attachment / delete each change how the repo is cloned. The blob stays in history until somebody rewrites it |
| `hyg.activity` | — | **yes, wholly.** The date of the last commit is a fact about the project, not a defect in the tree; no edit moves it. Archive, hand over, or read the row as the reminder it is |
| `hyg.unmerged-work` | — | **yes, wholly.** Merging or deleting a branch publishes or destroys work only its author can value, and a dropped stash is unrecoverable |
| `sec.supply-chain` | — | **yes, wholly.** Every remedy is a dependency decision — drop, replace, vendor, or accept. An agent picking the replacement library is how an unvetted dependency gets in |
| `sec.vendor-pins` | — | **yes, wholly.** Read `vendor.py diff <name>`, then re-vendor and move the pin. Applying a vendored update is always a human decision — which is why `vendor.py` has `verify`, `check`, `diff` and no `update` |
| `hyg.todo-density` | — | **yes, wholly.** No command fixes TODO density: each marker is either work to schedule or a stale line to delete, and only the owner knows which. `/declutter` is not it — read-only by invariant, no `--fix`, and an expensive fan-out |
| `ai.agents-md` | — | **yes, wholly.** `/init` is a Claude Code **built-in**: it is not a command file, so there is nothing for a subagent to read and follow. Run it yourself, then trim the result to commands + conventions |

`python3 <skill>/scripts/health.py fix-routes --repo <repo>` prints the same
routing as one JSON object per blocking check that is **failing or unmeasured**,
with `status`, `tier`, `agent`, `prompt`, the resolved `command_file`,
`flag_check`, `operator` and `routed` filled in — plus `stale` on **every** row
when the scorecard those rows were computed from describes a different or dirty
tree, so a dispatch can never look freshly-measured when it is not (the key is
absent, not empty, when the card matches this tree). Every check the collector emits
is in the table above — `routed` is `false` **only** for a check id nobody has
written a route for at all, which is the one case that means "go edit
`routes.py`". The rows with a dash in the middle column are `routed: true`: a
decision that no automation fits, not an unfinished one. A check still `pending`
gets a `"dispatch": "probe"` row naming the probe to run — it is not silently
skipped with the passing ones, because a check nobody ran is not one that passed.
Add **`--advisory`** once the user has given the step-5 go-ahead: it ADDS the
advisory checks sitting at `warn` or `fail` — with their routes from the same
table — on top of the blocking rows, never instead of them. Without it the
output is blocking only, which is the set `--fix-blocking` may touch. The table
above is the human-readable index of that data and `scripts/routes.py` is the
source — `test_skill_md_routing_table_matches_the_code` fails the suite when
they drift.

**The arguments are checked too, not just the file name.** Every flag a route
passes must appear in the target command file's `argument-hint:` frontmatter.
Four routes once passed `--fix` to commands that define no such flag, so it
landed on a positional (`test_scope`; the CI issue description) and the dispatch
spent a subagent budget changing nothing. It is checked in three places, because
the source and the copy that actually runs are different files:

- `test_every_route_passes_flags_the_target_file_defines` reads this repo's
  `commands/<name>.md` — the CI-portable check, green in a clean checkout.
- `test_the_deployed_command_files_declare_every_flag_the_routes_pass` reads
  `~/.claude/commands/<name>.md`, the copy a dispatch really reaches; it skips
  **with a stated reason** where there is no deploy target, never silently.
- At run time, every `command-file` row carries `flag_check` — empty only when
  the deployed file was read and declares every flag passed. "Could not look" is
  a sentence on the row, not an empty string.

## `--fix-blocking`

Only when the user invoked the skill with this flag. It fixes the failing
**blocking** checks and nothing else; advisory findings still wait for a
go-ahead.

Run `collect` before `fix-routes`: a scorecard committed in the repo can carry
hostile `detail` text, and `collect` replaces it. Do not run `--fix-blocking` on
a repo you do not trust.

1. `python3 <skill>/scripts/health.py fix-routes --repo <repo>`. Every line is a
   finding, and each carries its own `status`. No output **and exit 0** means
   every blocking check has been **measured** and none is failing — say so and
   stop. Both ways of not knowing print instead of staying silent: a repo that
   was never collected exits non-zero and says to run `collect` first, and a
   blocking check still `pending` gets its own line (step 2), so neither an
   uncollected nor an unprobed repo can be read as a clean one.
2. `"dispatch": "probe"` (`"status": "pending"`) — **this check was never run.
   Unmeasured is not passing.** Run the command in `detail` from the repo root
   (step 2 of the main flow has the pass/fail rule), record it with `health.py
   set`, then re-run `fix-routes`: a `fail` comes back with its fix route
   attached. Do this before reporting anything as clean.
3. For each line whose `dispatch` is `agent` or `command-file`: spawn the
   subagent named in `agent`, with `prompt` as its prompt plus the repo path and
   the check's `detail`. Put independent dispatches in ONE message so they run
   in parallel.
4. A `command-file` line is dispatchable only when `"command_file_found": true`
   **and** `flag_check` is empty. `"command_file_found": false` means the file
   is missing on this machine; a non-empty `flag_check` means the deployed copy
   does not declare a flag the route passes (so the argument would land on a
   positional and the run would change nothing) or could not be read at all.
   Report either as operator work — run `gearbox deploy` — and never improvise a
   substitute.
5. A non-empty `operator` field is the part no agent may do. Dispatch the rest,
   then hand that part to the user in one plain sentence. A `"dispatch":
   "operator"` line with `"routed": true` is a check where that part is the
   *whole* fix — somebody decided no automation fits, and the sentence says why.
   Hand it over and **do not** go write a route for it.
6. `"routed": false` is the opposite and the only one that means a gap: nobody
   has decided who fixes this check. Name it, and add its route to
   `scripts/routes.py`; do not quietly hand-fix it, or the next run repeats the
   same gap. Read this field, never the row's shape — a deliberate operator row
   and an undecided one both have `"dispatch": "operator"` and an empty `agent`,
   which is exactly how `hyg.todo-density` used to read as an unfinished route.
7. Afterwards re-run `collect` (the fixes changed the tree, so the preserved
   probe results are gone) and `render`.

## Reviewing the diff without the operator

`/security-review` is a Claude Code **built-in**, so there is no command file for
a subagent to read and only the operator can fire it — which is exactly why
`sec.diff-review` sat `pending` run after run. Dispatch the review instead:

1. **Derive the window.** Base = the `commit` field on the *last line* of
   `.claude/health/history.jsonl` whose commit is not HEAD, when
   `git cat-file -e <base>^{commit}` resolves it. Otherwise, 30 days.
   `health_render.py` appends this run's entry at render time, which is *after* the
   probes — so while you are running this probe the newest line is still the
   previous run, and reaching one line further back would skip a whole run and
   re-review its commits. The not-HEAD condition covers the other order: if the
   operator rendered before running the probes, the newest line *is* this run.
2. **List the changed code files.**
   `git diff --name-only <base>..HEAD -- '*.py' '*.ts' '*.tsx' '*.js' '*.jsx'`
   (no base: `git log --since=30.days --name-only --pretty= -- <same globs> | sort -u`).
   An empty list → record `pass`, detail "no code changes in window", and stop.
   (The collector already records `na` when the repo has no commits at all in
   30 days, so these two silences never get confused.)
3. **Partition into 2–4 scopes** by top-level directory, balanced by file count.
   Never one agent for everything — it reads shallowly; never one per file — it
   loses the cross-file path that makes a finding real.
4. **Dispatch one reviewer agent per scope, in a single message** so they run in
   parallel. Each gets its scope's file list, the base sha, and: "Read
   Sentry's `security-review` SKILL.md in full and review the changes to these
   files by it. Read-only — make no edits." That skill is not vendored here
   (its references derive from CC BY-SA material): install
   [getsentry/skills](https://github.com/getsentry/skills) yourself and give the
   agents its path.
5. **Apply the false-positive bar** to what comes back. A finding is confirmed
   only when the agent names (a) file and line, (b) the path by which
   attacker-controlled input reaches it, and (c) the concrete impact. Drop
   everything else: "potential", "consider", missing defence-in-depth, style,
   and anything inside a test fixture or a vendored tree.
6. **Record confirmed findings only.** Any confirmed exploitable finding →
   `fail`; otherwise `pass` with "N code files across M scopes reviewed, 0
   confirmed". Never paste raw agent output into the detail.

## `--deep` (optional, slower)

Add three passes before render. Read Sentry's `security-review` SKILL.md
(install [getsentry/skills](https://github.com/getsentry/skills) yourself; it is
not vendored here) and review the **whole repo** by it, recording confirmed findings into
`sec.sast` — `sec.diff-review` only ever sees the review window, so this is the
pass that reaches code no recent commit touched. Run the test suite a second
time and diff the results (a new intermittent failure → `test.suite` warn
"flaky — hand to flake-detective"). And dispatch an agent to follow the
`code-quality` command file with `$ARGUMENTS = --check` for the full
size/complexity report — same mechanism as every other route, since a bare
slash command is not dispatchable (see [Fix routing](#fix-routing--reuse-never-rebuild)).

## Test & CI speed — the ladder

Recommend speed work in this order and never skip a rung. Robustness first:
a faster suite that flakes is worse than a slow one.

1. **Measure.** Time the suite (`--durations=15`) and collection separately
   (`time pytest --collect-only -q`). Record both. A suite is only "slow" once
   you know which of the two is slow.
2. **Static wins first** — they need no new dependency and cannot add flakiness:
   fix expensive module-level work in test files (it is paid on every import),
   set `testpaths`, disable plugin autoload in the fast profile, drop unused
   heavy imports.
3. **Prove isolation before parallelising.** Random order and `-n 2`, twice each.
   Both green twice, or the suite is not parallel-safe — fix the shared state
   (per-worker tmp dirs, port 0, per-worker DB) first.
4. **Then parallelise** (`-n auto`), and only when serial runtime justifies it.
5. **Then shard** in CI, only when one parallel job still exceeds ~5 min.

**Collection cost gates parallelism.** Every xdist worker re-imports every test
module, so an expensive import is multiplied by the worker count, not divided.
Fix rung 2 before recommending rung 4 — otherwise parallelism makes it slower.

Two profiles are the goal: a seconds-fast loop agents can run per change, and a
full suite (target ≤10 min) for the gate.

## Vendored third-party skills

One reviewed skill lives under `vendor/`, pinned in `vendor.json` (repo, path,
commit, subtree hash, licence, why). It sits outside `skills/<name>/`, so Claude
Code never registers it and it never fires on a keyword — read it on purpose,
on the run that needs it.

| Vendored skill | Deepens | Read it when |
|---|---|---|
| `gha-security-review` (Sentry, Apache-2.0) | `sec.dangerous-workflow`, `sec.workflow-permissions`, `sec.action-pinning` | any of those three is red or arguable — the collector matches patterns, this one traces the attack path. A confirmed exploit keeps the **blocking** tier |

Sentry's `security-review` (from the same upstream) deepens `sec.sast` and
`sec.diff-review`, but it is **not vendored here**: its reference files derive
from the OWASP Cheat Sheet Series under CC BY-SA 4.0, which is share-alike.
Install [getsentry/skills](https://github.com/getsentry/skills) yourself and
point the `--deep` pass and the scoped reviewer agents at its `SKILL.md`. One
known limit upstream: it names 27 reference files and 7 do not exist — a
missing guide is unavailable, never a clean verdict.

`anthropics/claude-code-security-review` was reviewed and **rejected**: Claude
Code already ships that exact prompt as the built-in `/security-review`. That
built-in is still the right thing for a *human* to run — but no agent can fire
it, so `sec.diff-review` is dispatched from Sentry's prompt instead
(see [Reviewing the diff without the operator](#reviewing-the-diff-without-the-operator)).

**Detection is automatic; applying never is.** A vendored SKILL.md is prompt text
the agent will follow, so an update is adopted only after a human reads the diff —
then pin, subtree hash, and content move in one commit.

`vendor.py verify` is offline and answers "did anyone edit our copy": it hashes the
vendored directory and compares to the pinned subtree hash. `vendor.py check` asks
upstream the same question. Both compare **content**, not commit shas — comparing
commits reports an update every time the upstream repo pushes anything at all, and
a check that cries wolf gets ignored.

**The population is the `vendor/` directory, not the manifest.** Both commands
refuse (exit 2) unless every tree on disk carries a pin, because a tree with no
entry is a tree nothing hashes. An empty `vendor.json` — or an absent one — beside
a full `vendor/` used to exit 0 and read as `sec.vendor-pins = pass`, which is a
tampered vendored SKILL.md passing the gate that exists to catch it.

`check` applies the same rule to the network: an entry whose upstream it could not
reach — no `gh`, unauthenticated, rate-limited, offline — was printed with a `?`
and then dropped, so the run exited 0 with zero trees compared. It now exits 2 and
names them. `verify` is unaffected by any of this: it is offline by construction.

## Can every check still refuse?

A check that reports clean because it never really looked reads exactly like a
check that passed. So every check id here is fed the defect it exists to catch:

```
python3 <skill>/scripts/probe_checks.py --tools    # + external tools, writes a transcript
python3 -m pytest <skill>/scripts/test_probe_checks.py   # same probes, runs in CI
```

`probe_cases.py` holds the corpus — a base fixture where everything passes, plus
the smallest override that plants ONE defect per case. `probe_checks.py` runs
each pair and judges it: refusal counts only when the check's OWN violation
message matches the case's expected pattern, a plant that changes nothing is
`CANNOT FAIL`, and a crash is `ERROR` — never "refused". **Adding or rewriting a
check means adding its case**; `test_every_emitted_check_id_was_actually_probed`
fails the suite otherwise.

**There is no fast subset.** Both lines above run every deterministic probe;
`--tools` only ADDS the eight external-tool probes. A subset once existed and dropped the only probes two check ids had, so neutering the 5 MB
threshold to 5 GB left CI green. The guards now assert against the records a run
produced, never against the case table — a case that leaves the run fails them.

**Exit code is three-valued, because "we did not look" is not "clean":**

| code | meaning |
|---|---|
| `0` | every probe ran, and every check refused its known positive |
| `1` | a check could **not** refuse (`CANNOT FAIL`), or a probe errored — outranks `2` |
| `2` | nothing broke, but a probe was **skipped** — its tool is not installed, or it could not reach the feed it reads (an offline runner: `uvx` has no index, `semgrep` no rulesets), or `--tools` was not passed at all, so some check's real plant never ran |

`2` is the one that matters: without it, `--tools` on a box with no `gitleaks`
exits green while `sec.secrets-history`'s only real plant never ran — and a run
with no `--tools` at all exits green having skipped all eight.

**The pytest line runs the deterministic set only** (`run_all(tools=False)`), so
the eight external-tool probes do NOT run on a plain `pytest` CI job. Exit `2`
cannot fire there, because pytest never calls `main()`. To cover them, CI needs a
job that installs gitleaks / semgrep / ruff / make / uvx and runs the `--tools`
line above, and that job must treat exit `2` as a failure.

Two of those eight close the gaps the audit could not: `sec.dep-vulns`
and `hyg.dep-unused` are probed through `uvx --with-requirements` and `uvx
deptry`, because `pip-audit -r <file>` builds a throwaway venv whose `ensurepip`
aborts in the sandbox. Both need the network.

## Gotchas (observed)

- A full-history gitleaks scan can take many minutes on a large repo — start
  it in the background first, run the other probes while it works.
- A suite's wall clock can grow several-fold from CPU contention alone. Record
  the idle-machine number, or the trend measures the machine, not the repo.
- An "intermittent" test was diagnosed as a TOCTOU race and
  "fixed" — it failed again on the next full run for a different reason
  (a derived path that was never created, not one that vanished). A test that
  passes in isolation proves nothing about why it failed; get the failing
  entry's actual data before naming a cause, and probe the fix in BOTH
  directions — it must still fail on the real defect.

## Cadence

Re-run monthly or after any large change; the history strip makes drift visible.
Comparing layers across repos is valid — the checks are repo-shape-aware, so a
repo without workflows, without a remote, or without dependencies gets `na`
rather than a penalty.

Two things make a re-run cheap enough to actually happen: start the full-history
`gitleaks` scan in the background first and run the other probes while it works,
and record the suite's wall clock from an **idle** machine — otherwise the trend
line measures the machine, not the repo.

**Rolling out to a new repo:** run it, read the first scorecard as a baseline
rather than a verdict, and expect the first run to surface scope disagreements
(a linter and a quality gate policing different files) before it surfaces real
defects. Fix the scope first, or every later number is noise.
