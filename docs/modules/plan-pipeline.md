# Plan pipeline (`/plan-builder`, `/plan-execute`, `/plan-harden`)

Turns a large job into a plan of sessions with a dashboard, then runs the plan
session by session through subagents, with checks between sessions and a
human review before anything merges.

| | |
|---|---|
| **Status** | Optional · stable — large, and has its own tests |
| **Platform** | Any with `python3` and `git`. The scripts use the standard library only. |
| **Needs** | Claude Code with the Agent tool. Optional: the Codex CLI (second-model reviews), research MCP servers (prior-art search), [adversarial review](adversarial-review.md) and [grilling](grilling.md) for `/plan-harden`. |

## How it works

1. `/plan-builder` interviews you and writes a plan folder, `_plans/<slug>-<date>/`, in your project: a `PLAN.html` dashboard, a `manifest.json` and one prompt per session.
2. `/plan-execute <plan folder>` sends each ready session to a subagent at the model and effort that the plan asks for, checks the result, and stops at each human checkpoint.
3. When every session is done, `land` merges the plan into your default branch, but only after you approve it with `land-ack`. `finish` then pushes the plan record and reads CI.
4. `/plan-harden` (optional) stress-tests a plan before you run it: research, a grilling round, a two-model review and a premortem.

## What you get

**Required** — the minimum to build and run a plan:

- `harness/skills/plan-builder/` — the builder skill, its scripts (`build_plan.py` and helpers) and the dashboard template.
- `harness/skills/plan-execute/` — the runner skill and `scripts/run.py`, the command-line tool that the skill calls (`plan`, `begin`, `apply`, `verify-*`, `land`, `finish` and others).
- `harness/agents/tier-*.md` and `harness/agents/session-effort-worker.md` — one small subagent per model and effort pair. `plan-execute` picks them by name.
- `harness/model-routing.yaml` — the routing policy. `plan-execute` reads it from `~/.claude/model-routing.yaml`.
- `harness/scripts/resolve_route.py`, `harness/scripts/model_prices.py` — read the policy and the prices.

**Optional add-ons:**

| Add-on | Files | What it adds |
|---|---|---|
| Plan helpers | `harness/scripts/repo-profile-cache.py`, `harness/references/shared/`, `harness/references/plan-harden/` | A cache of the repo profile, shared contracts, and the prompts that `/plan-harden` and the prior-art step use |
| `/plan-harden` | `harness/commands/plan-harden.md` + the plan helpers | Pre-flight review of a plan |
| Guard hooks | `harness/hooks/turnend-guard.py`, `harness/hooks/verify-state-guard.py` | Stop Claude from ending a turn while a plan session it started is still open, and stop subagents from writing the review record |
| Codex lane | `harness/scripts/codex_supervised.py`, `harness/scripts/codex_watchdog.py` | Lets a plan send sessions or reviews to Codex |

`harness/skills/plan-harden/` is **not** the Claude Code version. It holds only
the Codex CLI version. The Claude Code `/plan-harden` is the command file.

## Install

The install paths are fixed: `plan-execute` finds its agents, scripts and
policy by going up three folders from its own location.

```bash
mkdir -p ~/.claude/skills ~/.claude/agents ~/.claude/scripts
cp -R harness/skills/plan-builder harness/skills/plan-execute ~/.claude/skills/
cp harness/agents/tier-*.md harness/agents/session-effort-worker.md ~/.claude/agents/
cp harness/scripts/resolve_route.py harness/scripts/model_prices.py ~/.claude/scripts/
cp -n harness/model-routing.yaml ~/.claude/model-routing.yaml    # -n: never replace your own
```

Add-ons:

```bash
# Plan helpers and /plan-harden
mkdir -p ~/.claude/references ~/.claude/commands
cp harness/scripts/repo-profile-cache.py ~/.claude/scripts/
cp -R harness/references/shared harness/references/plan-harden ~/.claude/references/
cp harness/commands/plan-harden.md ~/.claude/commands/

# Codex lane
cp harness/scripts/codex_supervised.py harness/scripts/codex_watchdog.py ~/.claude/scripts/

# Guard hooks
mkdir -p ~/.claude/hooks
cp harness/hooks/turnend-guard.py harness/hooks/verify-state-guard.py ~/.claude/hooks/
```

For the guard hooks, merge these into the `"hooks"` object of `~/.claude/settings.json`:

```json
"Stop": [
  { "hooks": [ { "type": "command", "command": "p=\"$HOME/.claude/hooks/turnend-guard.py\"; [ -f \"$p\" ] || exit 0; exec python3 \"$p\"", "timeout": 10, "statusMessage": "Turn-end guard: open plan work?" } ] }
],
"PreToolUse": [
  { "matcher": "Bash|Write|Edit|MultiEdit|NotebookEdit", "hooks": [ { "type": "command", "command": "p=\"$HOME/.claude/hooks/verify-state-guard.py\"; [ -f \"$p\" ] || exit 0; exec python3 \"$p\"", "timeout": 10 } ] }
]
```

## Check it works

```bash
python3 ~/.claude/skills/plan-execute/scripts/run.py --help
```

It prints the list of subcommands. Then, in a git repo, ask Claude to "build a
plan for <a task>", and run `/plan-execute _plans/<folder> --status`.

## Remove

```bash
rm -r ~/.claude/skills/plan-builder ~/.claude/skills/plan-execute
rm ~/.claude/agents/tier-*.md ~/.claude/agents/session-effort-worker.md
rm ~/.claude/commands/plan-harden.md ~/.claude/hooks/turnend-guard.py ~/.claude/hooks/verify-state-guard.py
```

Keep `~/.claude/model-routing.yaml` and the scripts if another module uses them.
Delete the hook entries from `settings.json`. Plans you built stay in each
project's `_plans/` folder.

## Cautions

- **It writes to your git repo.** It writes `_plans/` into the project, can
  make `plan/*` branches and worktrees, and `land` pushes to your default
  branch after your approval.
- `--auto` runs a whole plan by itself and dispatches many subagents. That
  costs real tokens. It still stops at human checkpoints.
- The default review gate `code-review-gate` has no portable default and fails
  on purpose. Bind your review gates to the `llm-review-*` gates, or define your
  own in `<project>/.claude/eval-gates.json`.
- The default gate file names an `eval` skill that is not in this repo. A plan
  that uses the `eval-smoke-baseline` gate fails.
- It refuses to run on a folder that iCloud, Dropbox or a network drive syncs,
  unless you pass `--unsafe-lock`.
- Three tier agents (`tier-opus-max`, `tier-sonnet-low`, `tier-sonnet-max`) are
  for a GLM / Z.ai setup. On an Anthropic account they matter only if a plan
  asks for those pairs.
- **Two policy files.** `install.sh` writes the routing policy to
  `~/.claude/claude/model-routing.yaml`; this module reads
  `~/.claude/model-routing.yaml`. If you use both, keep the two files the same.
