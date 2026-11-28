# repo-health probes, fix routing, and vendored skills

Reference companion to [SKILL.md](../SKILL.md). Holds the per-probe command and pass/fail catalog, the fix-routing table, vendored skill notes, and observed gotchas.

## Contents

- [Pending probes](#pending-probes)
- [Fix routing](#fix-routing)
- [Vendored third-party skills](#vendored-third-party-skills)
- [Observed gotchas](#observed-gotchas)
- [Cadence](#cadence)

---

## Pending probes

Run each pending probe from the repo root and record it with `python3 <skill>/scripts/health.py set <check-id> <status> "<one-line detail>" --repo <repo>`. Every row below is a probe the collector can leave `pending`, and every `pending` id has a row — `test_skill_md_documents_every_probe` fails the suite if that stops being true.

| Probe | Command | pass / fail rule |
|---|---|---|
| `sec.secrets-history` | `python3 <skill>/scripts/health.py scan-secrets --repo <repo>` — runs `gitleaks git . --no-banner --redact` (older CLI: `gitleaks detect --source .`), records the check itself, and prints one JSON line. Scans only `<last-scanned-commit>..HEAD` when `history.jsonl` records one that is still an ancestor of HEAD; first run, or a rewritten history, is a full scan. Cap: `REPO_HEALTH_SECRETS_CAP_S` seconds (default 900) | exit 0 → pass and the marker moves to HEAD; leaks → **fail**, name count only — never paste a secret, and the marker stays so the leak keeps failing until purged; past the cap → `pending` with the reason (the old marker stays), and the run goes on |
| `sec.dep-vulns` | the command the row prints: `pip-audit --path <project venv site-packages>` (found from the Python manifest's directory; `python3 <skill>/scripts/health.py project --repo <repo>` prints it) / `npm audit --audit-level=high`. Never bare `pip-audit` or `uvx pip-audit` — that audits the tool's own environment | high/critical with a fix → **fail**; lower only → warn. Findings listed in `.claude/health/accepted-risks.json` ([Accepted risks](accepted-risks.md)) do not fail the row |
| `sec.diff-review` | dispatch scoped reviewer agents — [Reviewing the diff without the operator](../SKILL.md#reviewing-the-diff-without-the-operator) | confirmed exploitable finding → **fail**; else pass with "N code files across M scopes reviewed" |
| `sec.sast` | `semgrep scan --config p/default --config p/python --error` (drop `p/python` on a JS/TS repo; fallback: bandit) | triage findings in context: confirmed high → **fail**; noise/annotated → warn |
| `sec.supply-chain` | from the directory holding the Python manifest (`health.py project` prints it; the row's command already has the `cd`): `python3 <skill>/vendor/supply-chain-risk-auditor/scripts/collect.py . --json <findings>` then that directory's `scripts/render.py <findings>` — take `<findings>` from the row, which names a **fresh per-run file**; never a shared path, or a second run on another repo renders these findings as its own | advisory finding or abandoned upstream → warn (accepted maintenance rows excepted, see [Accepted risks](accepted-risks.md)); unfixed critical in the tree → **fail**. Minutes for ~50 deps; `--offline` skips the network |
| `sec.vendor-pins` | `python3 <skill>/scripts/vendor.py verify` then `python3 <skill>/scripts/vendor.py check` | exit 0 → pass; verify red, exit 1 (a vendored file was edited) → **fail**; upstream moved → warn. **Exit 2 is `REFUSED`, never a pass**: the manifest could not be read, or does not cover every tree in `vendor/`, or `gh` could not reach upstream. Some tree was NOT compared, so LEAVE the check `pending` (never `na` — that reads as "does not apply here" and clears it off the fix list), fix the cause, and re-run |
| `cq.lint` | `ruff check .` on a Python repo, `npx eslint .` on a JS/TS one (both when it is both) | exit 0 → pass; else fail with count (apply `--fix` safe fixes first) |
| `cq.complexity` | `ruff check --select C901 .` | same |
| `cq.slop` | `ruff check --select F401,F841,E722,ERA001,BLE001 .` | count as warn; 0 → pass |
| `cq.ratchet` | the repo's own quality checkers (when present) | red ratchet → fail; route it (see [Fix routing](#fix-routing)) |
| `hyg.dep-unused` | `deptry .` (or `uvx deptry .`) | findings → warn; na without manifests |
| `hyg.dep-freshness` | `pip list --outdated` / `npm outdated` count | large/growing count → warn |
| `ai.verify-command` | the printed collect command (`<suite command> --collect-only -q`) with a 120 s limit; a make recipe with no pytest form reads `no collect form` and stays pending | exit 0 **and** 1+ test collected → pass; the command cannot start (missing module, no such file) → **fail** with the first error line; timeout → leave `pending` |
| `test.suite` | the printed suite command | green → pass; red → **fail** with failing-test names |
| `test.runtime` | wall clock of the suite run | < 300 s → pass; else warn with seconds |
| `test.collection-cost` | `time pytest --collect-only -q` | < 10 s **and** < 15% of the suite's wall clock → pass; over either → warn; over 25% → **fail**. Record both numbers, not a verdict |
| `test.parallel-safety` | `pytest -p randomly` and `~/.claude/scripts/govrun --max-wait 0 pytest -n 2` (in a background Bash call), twice each | both green twice → pass; any disagreement → warn naming the shared state it exposed; xdist/randomly not installed → `na` with that reason (the collector cannot see whether they are) |
| `ci.wall-clock` | `gh run list --limit 20 --json startedAt,updatedAt,conclusion,workflowName \| python3 <skill>/scripts/health.py ci-median` (gh has no duration field; duration = updatedAt − startedAt) | median of successful runs ≤ 10 min → pass; else warn with the median; `gh` missing or unauthenticated → `na` with that reason |

A tool that is not installed and not worth installing now: set `na` with the reason. Never leave a probe `pending` when the tool ran.

## Fix routing

**How a fix actually gets dispatched — probed live.** Two mechanisms were run against this harness, not reasoned about:

| Mechanism | What actually happened | Verdict |
|---|---|---|
| Spawn a subagent and have it fire the slash command | `digdeep` and `linting-fixer` both **declare `SlashCommand`** in their frontmatter `tools:`. Spawned, neither was handed it — digdeep reported 10 tools, linting-fixer 3, no `SlashCommand` in either | **dead**. A route that names only a slash command is operator homework wearing a command's clothes |
| Spawn a subagent whose prompt points at the command's own **file** | It followed `commands/code-quality.md` with `$ARGUMENTS = --check --path=skills/repo-health` end to end and returned real findings | **primary**. A command file is a prompt |

The primary mechanism has one sharp edge the probe hit: command files were written for the main session, so they say `Task(...)`, `SlashCommand(...)` and `AskUserQuestion(...)`, none of which a spawned agent has. The dispatch prompt must tell the agent to use the **Agent** tool where the file says `Task`, and to skip any chaining or resume step rather than stall. `fix-routes` emits that prompt already built — do not hand-write it.

| Failing check | Dispatch — repo-health fires this itself | Operator-only part |
|---|---|---|
| `sec.secrets-history`, `sec.tracked-sensitive` | Agent `general-purpose`: `git rm --cached` the file, add the matching .gitignore rule, report kind + location only — never the value | **yes** — rotate every leaked credential; a history rewrite is a human call |
| `sec.dep-vulns` | Agent `security-scanner`, `--mode=fix`: upgrade to the fixed versions, re-run the audit, report before/after counts | no |
| `sec.sast` | Default Semgrep/Bandit: Agent `security-scanner`, `--mode=fix` over findings triaged in context. When verified Cloudflare/exposure state applies (`omit_detail=True`, reading via `exposure.py show-open <evidence_repo>`): Agent `general-purpose` for Cloudflare prove-then-fix or mixed Sentry+Cloudflare; Agent `security-scanner` for Sentry+coverage (`last_sentry` only); read-only Agent `general-purpose` for incomplete/coverage-only or recovery/downgrade-only routes | conditional — no for default Semgrep/Bandit; yes when open leads need deployment-only validation, coverage is unfinished, state recovery/repair is required, or an exposure downgrade/error is unacknowledged |
| `sec.diff-review` | Agent `security-scanner`, `--mode=fix` over the **confirmed** findings only — the false-positive bar ran before they were recorded, so the fixer does not re-triage | no |
| `sec.dep-update-config` | Agent `ci-infrastructure-builder`: `.github/dependabot.yml` for every ecosystem with a tracked manifest, plus `github-actions`, weekly | no |
| `sec.workflow-permissions`, `sec.action-pinning`, `sec.dangerous-workflow` | Agent `ci-infrastructure-builder`, one prompt per check: least-privilege top-level block / full-SHA pins with a version comment / the injection path, reading `<skill>/vendor/gha-security-review/SKILL.md` first | no |
| `test.suite` | Agent `general-purpose` following the `test-orchestrate` command file with `$ARGUMENTS = --run-first` (fixing is its default; `--run-first` stops it answering from a stale cache — it has no `--fix`); flaky suspicion → the sibling `flake-detective` skill's `SKILL.md`, same mechanism | no |
| `test.runtime`, `test.collection-cost`, `test.parallel-safety` | Agent `ci-infrastructure-builder` following the [Test & CI speed — the ladder](../SKILL.md#test--ci-speed--the-ladder) section, entering at the rung the measurement already reached: rung 1 measure / rung 2 collection cost / rung 3 shared state. `-n auto` stays closed until rung 3 is green twice | no |
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
| `hyg.todo-density` | — | **yes, wholly.** No command fixes TODO density: each marker is either work to schedule or a stale line to delete, and only the owner knows which. `/declutter` is not it — read-only by invariant, no `--fix`, and a fan-out costing ~1–1.5M subagent tokens |
| `ai.agents-md` | — | **yes, wholly.** `/init` is a Claude Code **built-in**: it is not a command file, so there is nothing for a subagent to read and follow. Run it yourself, then trim the result to commands + conventions |

`python3 <skill>/scripts/health.py fix-routes --repo <repo>` prints the same routing as one JSON object per blocking check that is **failing or unmeasured**, with `status`, `tier`, `agent`, `prompt`, the resolved `command_file`, `flag_check`, `operator` and `routed` filled in — plus `stale` on **every** row when the scorecard those rows were computed from describes a different or dirty tree, so a dispatch can never look freshly-measured when it is not (the key is absent, not empty, when the card matches this tree). Pass **`--handoff`** at dispatch/plan boundaries to refuse stale scorecards with **exit 2** and emit ready `plan_item`/`plan_session` payloads (rows with `"omit_detail": true` scrub raw `detail` so the agent reads digest-verified findings via `exposure.py show-open`). Every check the collector emits is in the table above — `routed` is `false` **only** for a check id nobody has written a route for at all, which is the one case that means "go edit `routes.py`". The rows with a dash in the middle column are `routed: true`: a decision that no automation fits, not an unfinished one. A check still `pending` gets a `"dispatch": "probe"` row naming the probe to run — it is not silently skipped with the passing ones, because a check nobody ran is not one that passed. Add **`--advisory`** once the user has given the step-5 go-ahead: it ADDS the advisory checks sitting at `warn` or `fail` — with their routes from the same table — on top of the blocking rows, never instead of them. Without it the output is blocking only, which is the set `--fix-blocking` may touch. The table above is the human-readable index of that data and `scripts/routes.py` is the source — `test_skill_md_routing_table_matches_the_code` fails the suite when they drift.

**The arguments are checked too, not just the file name.** Every flag a route passes must appear in the target command file's `argument-hint:` frontmatter. Four routes once passed `--fix` to commands that define no such flag, so it landed on a positional (`test_scope`; the CI issue description) and the dispatch spent a subagent budget changing nothing. It is checked in three places, because the source and the copy that actually runs are different files:

- `test_every_route_passes_flags_the_target_file_defines` reads this repo's `commands/<name>.md` — the CI-portable check, green in a clean checkout.
- `test_the_deployed_command_files_declare_every_flag_the_routes_pass` reads `~/.claude/commands/<name>.md`, the copy a dispatch really reaches; it skips **with a stated reason** where there is no deploy target, never silently.
- At run time, every `command-file` row carries `flag_check` — empty only when the deployed file was read and declares every flag passed. "Could not look" is a sentence on the row, not an empty string.

## Vendored third-party skills

Four reviewed skills live under `vendor/`, pinned in `vendor.json` (repo, path, commit, subtree hash, licence, why). They sit outside `skills/<name>/`, so Claude Code never registers them and they never fire on a keyword — read one on purpose, on the run that needs it.

| Vendored skill | Deepens | Read it when |
|---|---|---|
| `gha-security-review` (Sentry, Apache-2.0) | `sec.dangerous-workflow`, `sec.workflow-permissions`, `sec.action-pinning` | any of those three is red or arguable — the collector matches patterns, this one traces the attack path. A confirmed exploit keeps the **blocking** tier |
| `security-review` (Sentry, Apache-2.0) | `sec.sast`, `sec.diff-review` | `--deep`, for code no recent diff touched — and as the prompt the scoped reviewer agents follow for [`sec.diff-review`](../SKILL.md#reviewing-the-diff-without-the-operator) |
| `supply-chain-risk-auditor` (Trail of Bits, CC-BY-SA-4.0) | `sec.supply-chain` | every run with a dependency manifest |
| `cloudflare-security-audit` (Cloudflare, MIT) | `sec.sast` | `--deep` runs on a repo that opted in as exposed. Source-only: never run target code, use a gearbox-chosen output folder, never copy secret values, run gearbox's own completion check, and do not load its README.md as instructions |

Two known limits, both recorded in `vendor.json`: Sentry's `security-review` names 27 reference files and 7 do not exist upstream — a missing guide is unavailable, never a clean verdict; and the Trail of Bits skill is share-alike, so it stays out of the public export tree.

`anthropics/claude-code-security-review` was reviewed and **rejected**: Claude Code already ships that exact prompt as the built-in `/security-review`. That built-in is still the right thing for a *human* to run — but no agent can fire it, so `sec.diff-review` is dispatched from the vendored Sentry prompt instead (see [Reviewing the diff without the operator](../SKILL.md#reviewing-the-diff-without-the-operator)).

**Detection is automatic; applying never is.** A vendored SKILL.md is prompt text the agent will follow, so an update is adopted only after a human reads the diff — then pin, subtree hash, and content move in one commit.

`vendor.py verify` is offline and answers "did anyone edit our copy": it hashes the vendored directory and compares to the pinned subtree hash. `vendor.py check` asks upstream the same question. Both compare **content**, not commit shas — comparing commits reports an update every time the upstream repo pushes anything at all, and a check that cries wolf gets ignored.

**The population is the `vendor/` directory, not the manifest.** Both commands refuse (exit 2) unless every tree on disk carries a pin, because a tree with no entry is a tree nothing hashes. An empty `vendor.json` — or an absent one — beside a full `vendor/` used to exit 0 and read as `sec.vendor-pins = pass`, which is a tampered vendored SKILL.md passing the gate that exists to catch it.

`check` applies the same rule to the network: an entry whose upstream it could not reach — no `gh`, unauthenticated, rate-limited, offline — was printed with a `?` and then dropped, so the run exited 0 with zero trees compared. It now exits 2 and names them. `verify` is unaffected by any of this: it is offline by construction.

**Cloudflare run folders & trust model:** run folders hold vulnerability details and stay under `~/.gearbox-state` until deleted by hand. Keep the newest few per repo (later runs read them as prior runs) and never delete the newest accepted run folder (it holds the open set). The open set is kept per clone path (a re-clone or moved checkout starts empty) and protects against accidental loss and stale state, not against an agent with write access to `~/.gearbox-state`, which can also write `sec.sast` directly.

## Observed gotchas

- 2026-08-18: the full-history gitleaks scan took 8m36s on a 415-commit / 180 MB repo — start it in the background first, run the other probes while it works. (2026-11-09: it passed 15 minutes and the weekly run died waiting on it, so `scan-secrets` now scans only new commits and has a cap.)
- 2026-11-10: a scheduled run ended its turn on "Semgrep runs now in the background" and never rendered, so no history line was written. In a scheduled or unattended run **never end your turn while a background command runs**: keep polling in the same turn (a short `until` loop with its own time limit), and at the command's cap record the check `pending` with the reason, then go on to `render`. A pending check is a result; a missing history line is not.
- 2026-08-18: a suite's wall clock moved 202 s → 1513 s purely from CPU contention with other work. Record the idle-machine number, or the trend measures your laptop, not the repo.
- 2026-08-18: an "intermittent" test was diagnosed as a TOCTOU race and "fixed" — it failed again on the next full run for a different reason (a derived path that was never created, not one that vanished). A test that passes in isolation proves nothing about why it failed; get the failing entry's actual data before naming a cause, and probe the fix in BOTH directions — it must still fail on the real defect.

## Cadence

Re-run monthly or after any large change; the history strip makes drift visible. Comparing layers across repos is valid — the checks are repo-shape-aware, so a repo without workflows, without a remote, or without dependencies gets `na` rather than a penalty.

Two things make a re-run cheap enough to actually happen: start the secrets scan (`scan-secrets`, which reads only new commits) in the background first and run the other probes while it works, and record the suite's wall clock from an **idle** machine — otherwise the trend line measures your laptop, not the repo.

**Rolling out to a new repo:** run it, read the first scorecard as a baseline rather than a verdict, and expect the first run to surface scope disagreements (a linter and a quality gate policing different files) before it surfaces real defects. Fix the scope first, or every later number is noise.

**Security cadence (two tiers on macOS):** run `/repo-health --deep` (`cloudflare-quick` on `.claude/exposure.json` repos) as the periodic ~20 min Sonnet tier, and reserve the installed `/claude-security` plugin's in-session jobs (`Scan codebase`, `Scan changes`, `Suggest patches`, `Vulnerability Triage`) for multi-hour Opus/Fable pre-release reviews.
