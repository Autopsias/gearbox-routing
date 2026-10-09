# Harness modules

`harness/` is a copy of one working Claude Code setup: skills, slash commands,
subagents, hooks and scripts. You do not install it as a whole. You pick the
modules you want, and each module has a card that says what it does, what it
needs, and how to install, check and remove it.

Read [`INSTALL.md`](INSTALL.md) Part 2 first. It has the five rules that apply
to every module: install only what you use, install into `~/.claude`, never
copy `harness/settings.json` or `harness/CLAUDE.md` over your own, copy every
path on the card, and read the cautions.

**Status** on each card means:

- **stable** — used daily in the original setup, and its parts are all in this repo.
- **experimental** — works, but tuned to one person's setup or still changing.
- **author-specific** — needs files or tools that only the original setup has.

## Plan and build

| Module | What it does | Platform |
|---|---|---|
| [Plan pipeline](modules/plan-pipeline.md) | Builds a multi-session plan with a dashboard, runs it through subagents with checks, and merges only after your approval | Any |
| [BMAD and epic builds](modules/bmad.md) | 52 BMAD method commands and `/epic-dev`, which builds an epic story by story | Any — needs BMAD in your project |

## Review and decisions

| Module | What it does | Platform |
|---|---|---|
| [Adversarial review](modules/adversarial-review.md) | Claude and Codex review code or a plan separately; Claude merges the findings | Needs the Codex CLI |
| [Grilling](modules/grilling.md) | Interviews you about a plan until each decision is settled | Any |
| [Investigation](modules/investigation.md) | `/blindspot` finds traps in unfamiliar code; `/diagnose` takes a hard bug to a fix and a test | Any |
| [Decision memos](modules/memo-loop.md) | Critiques or drafts a decision memo with a second model | Any — Codex optional |
| [Outside-idea triage](modules/outside-ideas.md) | Judges what an article or repo proposes against your repo | Any |
| [Explainers](modules/eli5.md) | Turns a subject into a story-style HTML page for a named audience | Needs the Artifact tool |
| [Pre-push gate](modules/no-mistakes.md) | Runs a branch through review, tests, push, pull request and CI, with a stop at each gate | Needs the `no-mistakes` tool |

## Code quality and testing

| Module | What it does | Platform |
|---|---|---|
| [Repo health](modules/repo-health.md) | Scores a repo from 0 to 100 on a dashboard, with BLOCKING and ADVISORY findings | Any |
| [Code quality](modules/code-quality.md) | Finds files, functions and complexity over the limits; audits dead code | Any — best on Python |
| [Engineering skills](modules/engineering-skills.md) | Test-driven development, architecture deepening, per-repo setup | Any |
| [Flake detective](modules/flake-detective.md) | Proves a test is flaky and finds the cause | Pytest projects |
| [Test and CI commands](modules/test-and-ci.md) | Fix failing tests or CI with parallel subagents; commit, push and get CI green | Any — needs `gh` |
| [Support agents](modules/support-agents.md) | The specialist subagents that the commands above use | Any |
| [Safe refactor](modules/safe-refactor.md) | Splits a large file behind a facade, with tests after each step | Any — needs `jq` |

## Session safety and context

| Module | What it does | Platform |
|---|---|---|
| [Git safety guard](modules/git-safety.md) | Blocks commands that throw away uncommitted work | Any |
| [Context compaction policy](modules/compaction.md) | Holds automatic compaction back until a safe moment | macOS, Linux |
| [Status line](modules/statusline.md) | Rate limits, context, cache and model at the bottom of the screen | macOS, Linux — needs `jq` |
| [Small hooks](modules/small-hooks.md) | A reminder for long subagents, and four hooks you can skip | Any |
| [Machine governor](modules/govrun.md) | Makes heavy test runs take turns, and can refuse a heavy run that skips the queue | macOS, Linux |
| [Agent janitor](modules/agent-janitor.md) | Kills leftover agent processes and deletes old temp files, without asking | **macOS only** |

## Cost, routing and authoring

| Module | What it does | Platform |
|---|---|---|
| [Cost and usage](modules/cost-and-usage.md) | Measures what your sessions cost, and what to change in your setup | Any |
| [Routing skills](modules/routing-skills.md) | `/routing-update` and `/routing-retro` for the routing policy | Any |
| [Skill authoring](modules/skill-authoring.md) | Write and score new skills; build mods; share skills with Codex | Any |
| [Utility scripts](modules/utilities.md) | Reports on auto-memory size and MCP server errors | Any |
| [NotebookLM](modules/notebooklm.md) | Drives Google NotebookLM from Claude Code | Any — needs `nlm` |

## Commands and reference

| Module | What it does | Platform |
|---|---|---|
| [General commands](modules/general-commands.md) | `/review`, `/research`, `/wait-what`, `/nextsession`, `/improve`, `/pr`, and what is archived | Any |
| [Reference docs](modules/reference-docs.md) | Background docs, a rule file, the original global instructions, key bindings | Any |

## Not for general use

[The original author's own tooling](modules/author-tooling.md) — deploy
scripts, git hooks for a private repo, and Codex versions of some skills. The
page says what each file is and why it does not work outside that setup.

## Where a module's files come from

Everything under `harness/` comes from a private source repo through an export
script (see [`HARNESS.md`](HARNESS.md)). When the export changes a module, its
card can go out of date. A test in CI (`scripts/test_docs.py`) fails when a
card names a `harness/` path that no longer exists, or when a skill folder has
no card.
