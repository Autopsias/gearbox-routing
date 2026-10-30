# Gearbox

**The right gear for every task.**

[![verify](https://github.com/Autopsias/gearbox-routing/actions/workflows/verify.yml/badge.svg)](https://github.com/Autopsias/gearbox-routing/actions/workflows/verify.yml)

Gearbox is a toolkit for running AI coding agents well. It picks the model and
the effort level each task needs, runs large jobs as checked sessions, reviews
work with two different models, keeps repos healthy, and guards your sessions.
It is not tied to one vendor: the routing policy ships profiles for Anthropic,
OpenAI, Gemini and Z.ai, and the toolkit runs on Claude Code and on OpenAI's
Codex CLI. You take only the parts you want.

## Is Gearbox for you?

**A good fit if you:**

- use Claude Code every day and want a setup that someone has already tested
  on daily work: reviews, plans, guards and cost checks;
- spend real money on models and want each task to get the cheapest model
  that does it well, with a record of why;
- run jobs too large for one session and want them split into sessions that
  each must pass their checks before the next one starts;
- want a second model (OpenAI's Codex) to review your code and plans.

**Not a fit if you:**

- want one package that installs everything. Apart from the routing
  framework, you copy each part by hand, and each part has its own steps;
- need support or a stable API. `harness/` is a copy of one person's working
  setup, and it changes when that setup changes;
- do not use Claude Code. Only the routing policy and a few skills work
  without it (see [Works with](#works-with)).

**What it costs you to use it:**

- **Context.** Every skill, command and subagent that Claude can start on its
  own adds its name and description to every turn. Install only what you use.
- **Setup work.** The routing framework is one command. Each module is a few
  copy commands and a check, all on its card.
- **Trust.** Hooks run on your machine on every tool call they watch. Read a
  hook before you wire it in.

## What is in the box

| Part | What it is | How you install it |
|---|---|---|
| **Routing framework** (`claude/`) | One policy file that maps each kind of task to a model and an effort level, plus the scripts that check it and apply it | `install.sh`: one command, with backups and an uninstall |
| **Harness** (`harness/`) | Skills, slash commands, subagents, hooks and scripts, grouped into modules | By hand, one module at a time. Each module has a card with every file it needs and how to install, check and remove it |

- **[The catalogue](docs/CATALOG.md)** lists every skill, command, subagent
  and hook, with one line on what it does, how you start it, and its module
  card. A script builds it from the files, so it always matches this repo.
- **[The module index](docs/MODULES.md)** groups the parts into modules by
  what you want to do.

## What Gearbox gives you

### Model and effort routing
- One policy file maps five task classes — from a mechanical rename to a one-shot irreversible call — to a model tier and an effort level.
- Provider profiles for Anthropic, OpenAI, Gemini and Z.ai. To change provider, you edit one line; the task classes stay the same.
- After two failures at the same root cause, the next attempt gets more effort, then a stronger model. Nothing starts on the expensive model just because a task feels hard.
- `/routing-update` updates the policy when a model or a price changes; `/routing-retro` reads past sessions and reports where the routing did not fit.

[Routing at a glance ↓](#routing-at-a-glance) · [Architecture →](ARCHITECTURE.md)

### Plan framework
- `/plan-builder` turns a large job into a plan of sessions with a dashboard; `/plan-harden` stress-tests the plan.
- `/plan-execute` runs each session in a fresh subagent on the model its task class needs, and counts it as done only when its checks pass (tests, an LLM review, a second model).
- It stops at human checkpoints with a short brief, and merges into your main branch only after you approve.

[The plan framework →](docs/PLAN-FRAMEWORK.md)

### Review and decisions
- `/adversarial-review`: Claude and OpenAI's Codex review the same code or plan separately; the findings are then merged.
- `/grill-me` interviews you about a design until each decision is settled; `/blindspot` finds the traps in unfamiliar code; `/diagnose` takes a hard bug to a fix and a test.

[Modules →](docs/MODULES.md#review-and-decisions)

### Repo health and quality
- `/repo-health` scores a repo from 0 to 100 on a dashboard, splits findings into BLOCKING and ADVISORY, and keeps the history.
- `/code-quality` holds file-size, function-length and complexity limits as a ratchet that only lets debt go down.
- Test and CI fixers sort failures and send specialist subagents; `/ship-tail` takes a branch to a pull request with green CI, and never merges.

[Modules →](docs/MODULES.md#code-quality-and-testing)

### Session safety
- Hooks block `git reset --hard` on uncommitted work, hold automatic compaction back until a safe moment, and queue heavy test runs so they do not all run at once.
- On macOS, a janitor cleans up leftover agent processes. A status line shows rate limits, context use and cache state.

[Modules →](docs/MODULES.md#session-safety-and-context)

### Cost and learning
- `/cost-audit` measures what your sessions cost from the local transcripts: cache hits, cache rewrites, effort used and dollars per model.
- `/improve` looks back over your sessions and proposes rules, skills and memory. Plans write their lessons to project memory, and the next plan reads them.

[Modules →](docs/MODULES.md#cost-routing-and-authoring)

There are also 52 [BMAD method](https://github.com/bmad-code-org/BMAD-METHOD)
commands and an epic builder for teams that use BMAD. Every part has a
[module card](docs/MODULES.md) with what it needs and how to install, check and
remove it.

## Works with

**Model providers.** The routing policy has a profile for each provider:

| Provider | Cheap tier | Workhorse tier | Strongest tier | Effort control |
|---|---|---|---|---|
| Anthropic | Claude Haiku 5.5 | Claude Sonnet 5.5 | Claude Opus 5.5 (Fable 5.1 as escalation-only top) | `effort` |
| OpenAI | `gpt-6-luna` | `gpt-6.1-sol` | `gpt-6-astra` | `reasoning.effort` |
| Gemini | `gemini-3.5-flash-lite` | `gemini-3.8-flash` | `gemini-3.1-pro-preview` | `thinking_level` |
| Z.ai | `glm-5.3-flash` | `glm-5.3-flash` | `glm-5.3` | `reasoning_effort` |

The model ids and prices were checked against each vendor's own pages on
2026-10-09. They are examples: check them before you rely on them
([`docs/PROVIDERS.md`](docs/PROVIDERS.md)).

**Agent harnesses.**

| Harness | What works |
|---|---|
| **Claude Code** | Everything. Gearbox is built and used daily on Claude Code. Hooks, the status line and subagent files are Claude Code features, so those parts work only here. |
| **OpenAI Codex CLI** | The plan runner can run a plan from Codex (`--harness codex`). `/adversarial-review`, `/plan-harden` and the memo critic ship Codex versions. A sync script mirrors the other skills into Codex's skill folder, and another script renders your global rules into Codex's `AGENTS.md`. |
| **Claude Code on other models** | The plan runner has a lane that runs plans on Z.ai's GLM models from a separate Claude Code config. A guide shows how to run Claude Code against a Gemini model through a proxy (experimental). |
| **Any other agent or CI job** | The routing policy is a plain YAML file and the resolver is a standard-library Python function, so any tool can read the policy or ask it for a route ([`docs/INTEGRATION.md`](docs/INTEGRATION.md)). The routing table also renders into any `CLAUDE.md`-style instruction file. |

## Routing at a glance

Each task class resolves to a model and an effort level for the active
provider. With the Anthropic example profile:

| Task class | Typical work | Model · effort |
|---|---|---|
| `mechanical` | rename, format, codemod, doc edit | Claude Haiku 5.5 · low |
| `standard_build` | CRUD, wiring, templated feature | Claude Sonnet 5.5 · medium |
| `agentic_build` | multi-file change, integration, non-obvious bug | Claude Sonnet 5.5 · high |
| `deep_reasoning` | architecture, security, hard root cause | Claude Opus 5.5 · high |
| `linchpin` | a one-shot call the rest of a plan rests on | Claude Opus 5.5 · high |

The same five classes resolve to OpenAI, Gemini or Z.ai models when you switch
`active_provider:`. The effort controls differ in kind between vendors, so each
profile translates the intent into its own vendor's control; Gearbox never
assumes one vendor's "high" equals another's. To measure the right tier for
your own task mix, see [`docs/METHODOLOGY.md`](docs/METHODOLOGY.md).

## Get started

You need `git`, `bash` and Python 3. Each step is optional; take the ones you want.

**1. Get the repo.**

```bash
git clone https://github.com/Autopsias/gearbox-routing.git
cd gearbox-routing
```

**2. The routing framework.** Try it first. This command installs into a new
`gearbox-install-<time>` folder here and never touches your real setup:

```bash
./install.sh --accept-example-profile
```

The last lines show `guard: PASS`. When you are happy, install it for real.
The long flag is on purpose: the installer writes to your real `~/.claude` only
when you say so twice.

```bash
./install.sh --claude-home "$HOME/.claude" --i-understand-this-mutates-live-claude --accept-example-profile
```

It backs up every file it changes, and `--uninstall` puts them back
([`docs/INSTALL.md`](docs/INSTALL.md)). Add `--provider openai`, `gemini` or
`zai` for another provider.

**3. Harness modules.** Pick modules by name. `./install-module` copies a
module's files into `~/.claude` and adds its hooks to your `settings.json`
(after a backup). It runs the steps on the module's card, and it prints any
step it will not run for you, such as installing an outside tool.

```bash
./install-module --list                       # every module, one line each
./install-module --dry-run git-safety         # see what it would do
./install-module git-safety statusline        # install
./install-module --remove git-safety          # undo its settings.json entries
```

Then start a new Claude Code session. Install only what you will use: every
skill, command and subagent that Claude can start on its own adds its name and
description to the agent's context on every turn. The cards give Claude Code
paths (`~/.claude/…`); the [Codex row above](#works-with) says what runs in
Codex. Good places to start:

| If you want… | Run `./install-module` with |
|---|---|
| The right model for each task | step 2 above; it also installs [`routing-skills`](docs/modules/routing-skills.md) |
| A health check and quality pass on a repo | [`repo-health`](docs/modules/repo-health.md) [`code-quality`](docs/modules/code-quality.md) [`test-and-ci`](docs/modules/test-and-ci.md) [`support-agents`](docs/modules/support-agents.md) |
| Second-model review of code and plans | [`adversarial-review`](docs/modules/adversarial-review.md) [`grilling`](docs/modules/grilling.md) [`investigation`](docs/modules/investigation.md) |
| Large jobs run as checked sessions | [`plan-pipeline`](docs/modules/plan-pipeline.md), plus `adversarial-review` and `grilling` for `/plan-harden` |
| Small, safe wins | [`git-safety`](docs/modules/git-safety.md) [`statusline`](docs/modules/statusline.md) |
| To see what your sessions cost | [`cost-and-usage`](docs/modules/cost-and-usage.md) |
| Long sources turned into a one-page brief for a decision | [`tldr`](docs/modules/tldr.md) [`eli5`](docs/modules/eli5.md) |

## What using it looks like

After you install a module, you use it inside a normal Claude Code session.
Each example below comes from the "Check it works" part of its card.

| You do this | You get this |
|---|---|
| Ask Claude to run `git reset --hard` while you have uncommitted work | The [git safety guard](docs/modules/git-safety.md) refuses it with a `BLOCKED … would discard N tracked modification(s)` message |
| Type `/repo-health` in a git repo | [Repo health](docs/modules/repo-health.md) scores the repo from 0 to 100 on a dashboard, with BLOCKING and ADVISORY findings |
| Type `/adversarial-review` with a small uncommitted change | [Adversarial review](docs/modules/adversarial-review.md) writes one review from Claude and one from Codex, then shows one merged list of findings |
| Ask Claude to "build a plan for <a task>", then run `/plan-execute _plans/<folder>` | The [plan pipeline](docs/modules/plan-pipeline.md) builds a plan with a dashboard, runs each session in a fresh subagent, and stops at your checkpoints |
| Start a new session with the status line installed | The [status line](docs/modules/statusline.md) shows rate limits, context use, cache state, the model and its effort |
| Type `/tldr <path to a long PDF>` | [Executive summaries](docs/modules/tldr.md) writes a one-page brief that puts the decision first, and checks that every number in it comes from its list of claims taken from the sources |

## Documentation

| I want to… | Read |
|---|---|
| See every skill, command, subagent and hook, one line each | [`docs/CATALOG.md`](docs/CATALOG.md) |
| Understand the plan framework | [`docs/PLAN-FRAMEWORK.md`](docs/PLAN-FRAMEWORK.md) |
| See every module | [`docs/MODULES.md`](docs/MODULES.md) |
| Install, update or remove | [`docs/INSTALL.md`](docs/INSTALL.md) |
| Understand the routing model | [`ARCHITECTURE.md`](ARCHITECTURE.md) |
| Calibrate the policy for my own models | [`docs/METHODOLOGY.md`](docs/METHODOLOGY.md) |
| Choose or switch a provider | [`docs/PROVIDERS.md`](docs/PROVIDERS.md) |
| Call the resolver from my own tool or CI | [`docs/INTEGRATION.md`](docs/INTEGRATION.md) |
| Know what changed | [`CHANGELOG.md`](CHANGELOG.md) · versioning rules: [`docs/VERSIONING.md`](docs/VERSIONING.md) |
| Contribute | [`CONTRIBUTING.md`](CONTRIBUTING.md) |
| Report a security issue | [`SECURITY.md`](SECURITY.md) |
| Understand how `harness/` is exported | [`docs/HARNESS.md`](docs/HARNESS.md) · [`GENERICIZATION.md`](GENERICIZATION.md) |

## Requirements

- **An agent harness.** Claude Code runs every part; OpenAI's Codex CLI runs
  the parts listed under [Works with](#works-with); the routing policy works
  with any agent or CI job.
- **bash**, **git** and **Python 3**. The routing scripts use only the Python standard
  library. Some harness scripts need more (for example, the Codex skill sync
  needs PyYAML); each module card lists its own needs. CI runs them on Python 3.12.
- **Optional:** the Codex CLI for two-model reviews; one research MCP server
  (Exa, Ref or Perplexity) for `/routing-update`. Each module card lists its own
  needs.

## Status

- **Routing framework:** policy version 2.7.1. CI runs the drift check, the
  unit tests, a docs check and a scratch install on every pull request and on
  every push to `master`.
- **Harness modules:** a copy of one person's daily setup, refreshed from time
  to time. CI does not run the harness tests, because many of them expect a live
  `~/.claude`, macOS, or tools a CI runner does not have. Each module card says
  whether its module is stable, experimental or author-specific.

## About this repo

`harness/` is generated. An export script copies it from a private source
repo, removes personal data, and scans the result
([`docs/HARNESS.md`](docs/HARNESS.md)). A change made directly in `harness/`
is lost at the next export. Everything else — the docs, `claude/`,
`install.sh` and `scripts/` — is maintained here.

If you clone this repo to push changes, run `git config core.hooksPath .githooks`
first. See [`CONTRIBUTING.md`](CONTRIBUTING.md).

## Credits

- Several harness skills (`grill-me`, `grill-with-docs`, `tdd`,
  `improve-codebase-architecture`, `setup-matt-pocock-skills`) are adapted from
  [mattpocock/skills](https://github.com/mattpocock/skills) (MIT).
- `harness/scripts/repo-profile-cache.py` is adapted from
  [everyinc/compound-engineering-plugin](https://github.com/everyinc/compound-engineering-plugin)
  (MIT).
- `harness/skills/repo-health/vendor/` holds a pinned copy of Sentry's
  `gha-security-review` skill (Apache-2.0; its licence ships beside it).

## License

[MIT](LICENSE)
