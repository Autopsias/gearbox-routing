# repo-health check catalog

## Contents
- [Tier rationale](#tier-rationale)
- [Security](#security)
- [Code quality](#code-quality)
- [Tests](#tests)
- [CI workflow](#ci-workflow)
- [AI readiness](#ai-readiness)
- [Hygiene & docs](#hygiene--docs)
- [Scoring](#scoring)

## Tier rationale

The blocking set follows the 2025–26 external consensus (OpenSSF Scorecard
risk tiers, GitHub's post-tj-actions guidance): live secrets, dangerous
workflow patterns, unscoped workflow tokens, unpinned third-party actions,
high/critical vulnerable dependencies with a fix available, and a red test
suite. Everything else is tracked, not gating. Scorecard's OSS-collaboration
checks (Branch-Protection, Code-Review, Signed-Releases, Contributors) are
deliberately absent — low relevance for solo private repos.
Primary sources: https://github.com/ossf/scorecard/blob/main/docs/checks.md ·
https://www.wiz.io/blog/github-actions-security-guide ·
https://code.claude.com/docs/en/best-practices

## Three invariants the collector cannot break

**Every check id is emitted on every run.** A check that does not apply reads
`na` and its detail names what was looked for and not found. Nothing is ever
dropped from the scorecard, because a scorecard that silently shrinks reads
healthier than the repo is — and it reads that way precisely when the shape
detection is wrong, which is the case where you need the truth most. The check
count is therefore constant across repo shapes; only the statuses vary.

**Every fix route is one the skill can dispatch itself.** Routes live as data in
`scripts/routes.py` and `health.py fix-routes` emits them; a route may name a
slash **command file** (a subagent reads it and follows it — probed working) or
an installed **subagent**, never a bare slash command. Probed live:
subagents whose definitions declare the `SlashCommand` tool are not
actually given it, so a bare-command route is silent operator homework. A route's
**arguments** are checked the same way as its file: every flag must appear in the
target command file's `argument-hint:` frontmatter, because a flag the command
does not define is not rejected — it silently lands on a positional and the
dispatch spends a subagent budget changing nothing. The suite checks that against
this repo's `commands/`, which is all a clean CI checkout has; each emitted row
also carries `flag_check`, the same answer read at run time from the **deployed**
`~/.claude/commands/` copy the dispatch will really reach, non-empty whenever the
flags could not be confirmed. It settles **existence first**, so a route that
passes no flags at all still reports a missing command file rather than an empty
string that reads as verified. `fix-routes` also emits a `"dispatch": "probe"` row
for a check still `pending` — unmeasured is not passing, so it is never
filtered out with the ones that passed. Rows are **blocking only** unless
`--advisory` is passed, which is SKILL.md step 5's go-ahead: it ADDS every
advisory check sitting at `warn` or `fail` (most advisory checks top out at
`warn`) with the same pre-built dispatch, and never drops a blocking row.
**Every check in the catalog has a decided route**, and seven of them are
deliberately the operator's: `ai.agents-md` (`/init` is a built-in with no file
to read), `hyg.todo-density` (each marker is work to schedule or a stale line to
delete), `hyg.large-files`, `hyg.activity`, `hyg.unmerged-work`,
`sec.supply-chain` and `sec.vendor-pins` — the reason is on each row. Those are
**not** the same as a check nobody has routed, and the two used to print
identically: both are `"dispatch": "operator"` with an empty `agent`. The row
carries **`routed`** to separate them — `true` on every decided route including
the operator ones, `false` only for an id with no entry in `ROUTES`, which is
the one case that means "go add the route". `test_every_check_the_collector_
emits_has_a_decided_route` keeps the two sets equal, so a check added later
fails the suite until somebody decides who fixes it.

**One manifest predicate, in `shape.py`.** "Does this repo have dependencies?"
is answered in exactly one place. It follows Renovate's `pip_requirements`
basename pattern (`^[\w-]*requirements([-._]\w+)?\.(txt|pip)$`, so
`requirements-ci.txt` and `requirements-dev.pip` count) plus Dependabot's
companion filenames — `package.json`, `setup.py`, `Pipfile`, `setup.cfg` with
`install_requires` (a substring test, so a commented mention counts — it errs
towards more checks, never fewer), `pyproject.toml` with
`[project]`/`[tool.poetry]`.
It is the **basename** that has to match, so `requirements/dev.pip` does not
count and `sub/requirements.txt` does.
A regex-matched requirements file only counts when **every** informative line
is pip syntax, so a prose `functional_requirements.txt` does not (cdxgen #666)
— one sentence or markdown bullet disqualifies the file. A `#` heading
disqualifies nothing: pip reads it as a comment, and it is stripped before the
sniff. "At least one line" was not enough: a bullet list parsed as pip syntax
and the document read as a manifest. The remaining hole is deliberate and named
in `is_requirements`: a file of nothing but bare one-word lines is taken as a
manifest, because bare-name requirements files are real and the safe failure
direction is extra checks, not vanished ones.
Every example in this section is executed against the predicate by
`test_every_manifest_example_in_the_catalog_matches_the_predicate` — one of
them (`requirements/dev.pip`) was written from intent and was never true.

**Two questions, two predicates.** "Is this line evidence the file is a
manifest?" and "is this line an unpinned dependency?" get different answers for
pip's own directives: `-r base.txt` and `--hash=sha256:…` are pip syntax but
they are not dependencies. One predicate answering both made pip-compile output
— the most tightly pinned file a repo can have — report as unpinned. A VCS
reference (`git+https://…@main`) is a dependency the distribution-name regex
cannot match, and it is pinned only by a full 40-char commit sha: `@main` is the
least pinned thing a requirements file can hold.
`vendor/`, `third_party/`, `third-party/` and `node_modules/` path
components are excluded **structurally**, not via
`[tool.claude-quality].exclude`, because that section is absent in every other
repo this vendored script runs in.
Sources: https://docs.renovatebot.com/modules/manager/pip_requirements ·
https://docs.github.com/code-security/dependabot

## Security

**All seven workflow checks read only the files that actually OPENED.** A tracked
workflow that will not open — sparse checkout, interrupted rebase, dangling
symlink — is NOT handed to them as an empty string: it is named in the detail and
demotes a `pass` to `warn`, and if NONE of them opened the check is **`pending`**.
An empty file has no `pull_request_target` and no unpinned `uses:`, so the old
behaviour was two blocking security checks passing over bytes nothing had read.

`pending` and not `na`, here and everywhere else in this catalog: **`na` means the
collector looked and the check does not apply to this repo** — a measured answer,
which `fix-routes` treats as clear and the score ignores. **`pending` means nobody
measured it** — it caps the score, prints as NOT RUN, and always gets a row. Every "could not read / could not reach / the command failed" answer is `pending`
— with ONE deliberate exception, stated in the `ai.lockfiles` row below: a check
that ALREADY found a finding in files it COULD open keeps that `warn`/`fail` and
appends the unread note. Demoting a proven finding to `pending` would drop it out
of the weighted score entirely, so an unreadable file would RAISE the repo's score.
The rule is "never report health you did not measure", not "prefer `pending`".

| id | tier | measured by | note |
|---|---|---|---|
| sec.workflow-permissions | blocking | top-level `permissions:` in every workflow | Scorecard Token-Permissions = High; least-privilege GITHUB_TOKEN |
| sec.action-pinning | blocking | third-party `uses:` refs end in 40-hex SHA | fail = third-party unpinned; warn = first-party (`actions/`, `github/`) unpinned |
| sec.dangerous-workflow | blocking | `pull_request_target` + PR-head checkout; `${{ github.event.* }}` **inside** a `run:` or `script:` body (the key's own line, or its `\|`/`>` block) — the same expression in `env:`, `if:` or `with:` is data, and `env:` is the fix this check prescribes | Scorecard's only Critical check; script-injection class |
| sec.tracked-sensitive | blocking | tracked `.env` / `*.pem` / `id_rsa*` / `*.p12` / `*.pfx` | rotation first, removal second |
| sec.secrets-history | blocking | probe: gitleaks over full history | respects `.gitleaksignore`; verified leak = rotate now |
| sec.dep-vulns | blocking | probe: pip-audit / npm audit | fail only high/critical WITH available fix; below → warn |
| sec.vendor-pins | advisory | probe: `vendor.py verify` (offline tree hash) then `vendor.py check` (upstream) | exit 0 → pass, 1 → fail/warn, **2 → leave it `pending`**: the manifest does not cover every tree in `vendor/`, or `gh` could not reach upstream. Neither is a clean result, both used to exit 0, and neither is `na` — nothing was measured |
| sec.diff-review | advisory | probe: scoped reviewer **agents** over the review window, following Sentry's `security-review` prompt (install getsentry/skills yourself; not vendored here) | the built-in `/security-review` is operator-only — an agent cannot fire it, so naming it here left this probe `pending` run after run. Window = the previous run's commit, else 30 days; partitioned into 2–4 scopes; confirmed findings only (file+line, the input path, the impact), see [SKILL.md](../SKILL.md#reviewing-the-diff-without-the-operator). Empty window with commits present → `pass` "no code changes in window"; no commits at all in 30 days → `na`; `git log` FAILING leaves the probe `pending`, because "no commits in the window" is a claim a failed command cannot make |
| sec.sast | advisory | probe: semgrep `p/default` (+`p/python` on a Python repo; fallback bandit; deep: security-scanner agent) | semgrep replaces bandit — better signal, and `p/default` covers a JS/TS repo, which is why this is `na` only when the repo has no `.py`/`.ts`/`.js` at all; a confirmed high finding escalates the check to fail |
| sec.dep-update-config | advisory | one of `.github/dependabot.yml`, `.github/dependabot.yaml`, `renovate.json` is tracked | acceptable substitute: scheduled dep audit. Renovate also reads `.github/renovate.json` and `.renovaterc*`, which this check does not look for — so it names the filenames it checked rather than claiming none exists |
| sec.supply-chain | advisory | probe: Trail of Bits `supply-chain-risk-auditor`, installed by the user (not vendored here: share-alike licence) | goes past `sec.dep-vulns`: the whole lockfile tree, abandoned or archived upstreams, npm publisher concentration, install-time script execution. Reads registry/advisory metadata only — never dependency source, never installs or builds |
| sec.vendor-pins | advisory | probe: `vendor.py verify` (offline) + `vendor.py check` (upstream) | only when this skill vendors third-party content. Both compare the vendored directory's git **tree** hash, not a commit sha — a commit comparison reports an update on any unrelated upstream push. `verify` red means somebody edited a vendored file in place, which makes the pin a lie; updates are reported, **never auto-applied**, because vendored prompt content is injection surface |

## Code quality

| id | tier | measured by | note |
|---|---|---|---|
| cq.lint | advisory | probe: `ruff check .` on a Python repo, `npx eslint .` on a JS/TS one, both when the repo is both | red configured linter = advisory fail, amber layer. `na` only when the repo has no `.py`/`.ts`/`.js` at all — a pure-TS repo used to read `na` here and take the whole lint layer quiet with it |
| — | — | **scope** | every ruff-based number obeys the repo's own quality ratchet: `[tool.claude-quality].exclude`, mirrored into `[tool.ruff].exclude`. Shape detection reads the same list, so a vendored third-party `pyproject.toml` is not mistaken for this repo's manifest — and excludes `vendor/`, `third_party/`, `third-party/` and `node_modules/` structurally on top, since that section exists only here. Never pass a per-call `--exclude` — a second list drifts, and a linter counting files the ratchet refuses to police makes the report read worse than the repo is |
| cq.complexity | advisory | probe: ruff --select C901 | threshold from repo config, else ruff default; `na` without Python — this rule set is ruff-only |
| cq.file-size | advisory | tracked source files > 500 lines | honors `[tool.claude-quality].exclude` in pyproject.toml, matching each entry the three ways the house ratchet's `is_excluded()` does — a path component, a glob over the relative path (`_plans/*/_evidence`), or a directory prefix — so the two readers of one list agree. Every other check that reads that list matches it the same way. `na` when this repo has no source file in scope at all (a measured "does not apply"), `warn` naming any file that would not open, and `pending` when nothing in scope could be read |
| cq.function-length | advisory | tracked first-party `.py` functions > 100 lines, read from the Python AST | the one size bound the collector never emitted: function length only ever reached the scorecard folded inside `cq.ratchet`, which is `na` on every repo with no `scripts/quality/` of its own — and a check that is never emitted can never fail. `na` (never a bare `pass`) when no first-party `.py` was actually PARSED — no Python at all, or every candidate filtered out as build output — because `ast` reads Python only and a TS repo's 300-line function, or a repo whose only `.py` sits under `build/`, would otherwise report as measured. Raw reading on purpose: the repo's own `.function-length-exceptions` baseline is what `cq.ratchet` honors, exactly as `.file-size-exceptions` is to `cq.file-size` |
| cq.slop | advisory | probe: ruff F401,F841,E722,ERA001,BLE001 | agent-authored-code patterns; fix route: linting-fixer; `na` without Python — ruff-only, like cq.complexity |
| cq.ratchet | advisory | probe: the repo's own quality checkers, when present — in `scripts/quality/` (the source repo) or `tools/` (where `vendor_quality.py` puts them in an adopting repo) | the house gate with committed baselines beats the generic checks — trust it |

## Tests

| id | tier | measured by | note |
|---|---|---|---|
| test.suite | blocking | probe: detected suite command (make test / npm test / pytest) | red suite blocks; no suite at all → na + verify-command finding |
| test.runtime | advisory | probe: suite wall clock (`--durations=15` names the slow tests) | warn over 300 s; the trend matters more than the number. Wall clock is CPU-contention sensitive — record the idle-machine number |
| test.collection-cost | advisory | probe: timed `pytest --collect-only -q` | warn over ~10 s or 15% of the run, fail over 25%. **Collection is paid by every invocation and by every parallel worker**, so an expensive import blocks parallelism rather than being cured by it |
| test.parallel-safety | advisory | probe: random order + `-n 2`, twice each (the collector cannot see whether xdist is installed, so it asks — record `na` with the reason when it is absent) | the isolation proof that must pass BEFORE parallelism; guards against shared state, fixed ports, shared tmp dirs |

Flakiness has no standing check id: on `--deep` a second suite run that
disagrees with the first marks test.suite as warn and hands off to the
flake-detective skill (which keeps its own log at `.claude/flake-detective.log`).

## CI workflow

`ci.timeouts`, `ci.concurrency`, `ci.caching`, `ci.retention` and
`ci.wall-clock` — and the three workflow checks in the security layer — are all
`na` when the repo has no `.github/workflows/` (detail notes git-hook gates when
present). `ci.pre-commit` and `ci.server-side-gate` are measured either way:
they are the two that answer "what gates this repo when there is no CI".

| id | tier | measured by |
|---|---|---|
| ci.timeouts | advisory | `runs-on:` count > `timeout-minutes:` count per file (default job timeout is 360 min) |
| ci.concurrency | advisory | any `concurrency:` group present |
| ci.caching | advisory | `actions/cache` or setup-* `cache:` when lockfiles exist — a repo with no lockfile passes, and the detail says so rather than reading like a failure. `na` when at least one job names a self-hosted runner and none names a GitHub-hosted one: that runner's cache is on its own disk, not in the YAML. A runner taken from `${{ vars.* }}` is counted as unreadable and named in the detail; a `matrix.` runner counts as hosted |
| ci.retention | advisory | `upload-artifact` steps carry `retention-days` |
| ci.pre-commit | advisory | local commit gate present (pre-commit config or githooks dir) — runs even without workflows |
| ci.server-side-gate | advisory | a gate that does not run on the committer's machine: `na` without a git remote, `pass` with workflows, `warn` when a remote exists but nothing re-checks the push. Warn, never fail — see [no-ci.md](no-ci.md) for when CI is actually worth proposing |
| ci.wall-clock | advisory | probe: `gh run list --json durationMs` over recent runs — PR feedback target ≤10 min |

## AI readiness

The layer that makes a repo work well under coding agents (Anthropic/agent
ecosystem guidance, 2025–26): agent instructions at the root, one fast
verification command, deterministic dependencies.

| id | tier | measured by | note |
|---|---|---|---|
| ai.agents-md | advisory | AGENTS.md or CLAUDE.md at root containing runnable commands | fail if neither exists; warn if present without commands |
| ai.verify-command | advisory | detected suite command AND a lint gate (hooks or make check/verify/lint) | the single highest-leverage agent affordance |
| ai.lockfiles | advisory | lockfile committed; every dependency in every detected requirements file names exactly one artifact — `==`, a direct URL, or a full 40-char commit sha for a VCS reference | determinism for agent runs. A lockfile with **no** detected manifest is a `warn`, never silence: either the manifest is untracked or shape detection is wrong, and both are worth a line. **Every** detected manifest is opened — pyproject.toml and setup.cfg included, not only requirements-style files — and one that will not OPEN is named in the detail, because the unread file is exactly the one that could hold the unpinned spec. It is never a `pass` standing over bytes nobody read. What it becomes depends on what the READABLE files already proved: a clean-so-far check drops to `pending` (the unread file could still hold an unpinned spec), but a check that ALREADY found an unpinned dependency, or found no lockfile at all, keeps its `warn` and appends the unread note — the finding was read from somewhere the unread file cannot reach, and demoting a proven finding to `pending` would hide it |

## Hygiene & docs

| id | tier | measured by |
|---|---|---|
| hyg.readme | advisory | README > 20 lines with a fenced runnable quickstart |
| hyg.tracked-junk | advisory | a tracked path with a `node_modules`/`.venv`/`__pycache__`/`dist`/`build` component **at any depth**, or any `*.pyc`/`.DS_Store`; only `[tool.claude-quality].exclude` silences it |
| hyg.large-files | advisory | tracked file > 5 MB, outside dot-directories and `[tool.claude-quality].exclude`; a file whose size cannot be read (dangling symlink, permission wall) is named as **not measured**, never silently skipped |
| hyg.todo-density | advisory | git grep TODO/FIXME/XXX across `*.py`/`*.ts`/`*.js` only; warn > 100 — cluster by subsystem in the detail. `git grep` exit 1 is a real zero; any other failure is `pending`, never a reported count of 0 |
| hyg.activity | advisory | days since last commit; warn > 90; `pending` when `git log` gives no date — the age was never read |
| hyg.dep-unused | advisory | probe: deptry (Python) / knip (TS) — declared-but-unimported and imported-but-undeclared |
| hyg.dep-freshness | advisory | probe: outdated-package count — rot before it becomes a CVE |
| hyg.unmerged-work | advisory | local branches not merged into HEAD's branch + stash count — forgotten solo work. Two INDEPENDENT questions: if one git call fails the other's answer still stands and the unasked one is named; only both failing is `pending` |
| hyg.notebook-outputs | advisory | tracked .ipynb cells with non-empty outputs (stdlib JSON scan); na without notebooks |

## Scoring

Layer color: red = a blocking check failed in it; amber = any
fail/warn/pending; green otherwise (`na` ignored). Verdict: AT RISK on any
blocking fail; NEEDS ATTENTION on any advisory fail or an unrun blocking
probe; HEALTHY otherwise.

Score (0–100, per layer and overall): pass = 1, warn = 0.5, fail = 0;
blocking checks weigh 2×, advisory 1×; `na` and `pending` are excluded from
the average. Gate on top: any blocking fail caps the overall at 59; a
blocking warn or unrun blocking probe caps at 89. Bands: 0–49 red, 50–89
amber, 90–100 green. The formula is monotonic — fixing a check never lowers
the score — and 100 means every applicable check passes with nothing
critical unrun. Design follows Lighthouse (weighted average + bands),
OpenSSF Scorecard (risk weights, n/a exclusion), and SonarQube (gate over
average).
