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

The full explanation, with a diagram, is in [the plan framework](../PLAN-FRAMEWORK.md).


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
| Guard hooks | `harness/hooks/turnend-guard.py`, `harness/hooks/verify-state-guard.py` | Stop Claude from ending a turn while a plan session it started is still open, and stop subagents from writing the review record. `turnend-guard.py` also refuses a reply that says "Needs you: nothing" beside work it names as open ("still open", "not done", "uncommitted"), and a "Needs you" line that asks a decision but names no pick. |
| Codex lane | `harness/scripts/codex_supervised.py`, `harness/scripts/codex_watchdog.py` | Lets a plan send sessions or reviews to Codex. Its runs start with the Ref `Plans` MCP server switched off: the read-only sandbox does not cover MCP servers, and one review run had published repo details as Ref documents. |
| Git hook | `harness/githooks/pre-push` | Makes a push to the default branch wait, at most 300 s, while a `land` pushes it. It never blocks: whatever happens, the push goes on. It only works in a repo laid out like the harness itself (it calls `skills/plan-execute/scripts/land_writers.py` from the folder above the hook), so it is not for a project repo. |

`harness/skills/plan-harden/` is **not** the Claude Code version. It holds only
the Codex CLI version. The Claude Code `/plan-harden` is the command file.

## Install

The install paths are fixed: `plan-execute` finds its agents, scripts and
policy by going up three folders from its own location.

```bash
mkdir -p ~/.claude/skills ~/.claude/agents ~/.claude/scripts
cp -R harness/skills/plan-builder harness/skills/plan-execute ~/.claude/skills/
cp harness/agents/tier-*.md harness/agents/session-effort-worker.md ~/.claude/agents/
cp harness/scripts/resolve_route.py harness/scripts/resolve_route_demo.py harness/scripts/model_prices.py ~/.claude/scripts/
cp -n harness/model-routing.yaml ~/.claude/model-routing.yaml    # -n: never replace your own
```

Optional add-ons:

```bash optional
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

For the guard hooks: `./install-module plan-pipeline` adds this to `~/.claude/settings.json` for you. By hand: add each entry to the matching list under `"hooks"`, and keep your own entries.

```json optional
{
  "hooks": {
    "Stop": [
      {"hooks": [{"type": "command", "command": "p=\"$HOME/.claude/hooks/turnend-guard.py\"; [ -f \"$p\" ] || exit 0; exec python3 \"$p\"", "timeout": 10, "statusMessage": "Turn-end guard: open plan work?"}, {"type": "command", "command": "p=\"$HOME/.claude/hooks/agent-elapsed-nag.py\"; [ -f \"$p\" ] || exit 0; python3 \"$p\" stopbg", "timeout": 5}]}
    ],
    "PreToolUse": [
      {"matcher": "Bash|Write|Edit|MultiEdit|NotebookEdit", "hooks": [{"type": "command", "command": "p=\"$HOME/.claude/hooks/verify-state-guard.py\"; [ -f \"$p\" ] || exit 0; exec python3 \"$p\"", "timeout": 10}]}
    ]
  }
}
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
Run `./install-module --remove plan-pipeline` to take the hook entries out of `settings.json`. Plans you built stay in each
project's `_plans/` folder.

## Halts, answers and landing

These are the rules that decide where a run stops and who answers. They live in `harness/skills/plan-execute/SKILL.md`.

- **A halted session takes your answer in a file.** Write your reply, word for word, to `<plan folder>/_decisions/<session>.halt-<n>.md`. Then run `run.py clear-halt <plan folder> --answer-file <that file>`. The session's next attempt starts with your words, once. A checkpoint works the same way: write `<plan folder>/_decisions/<session>.md`, then run `run.py plan <plan folder> --resume --answer-file <that file>`. A bare `--resume` past a checkpoint refuses.
- **The tool counts the clears.** The second clear of the same session's halt needs `--cost-report '<elapsed, passes, what changed, the cut>'`. The third clear of a review halt takes only `--exit accept|split|escalate`; one more review round is not an exit. `harness/skills/plan-execute/references/verify-gates.md` has the three exits.
- **Landing on a plan branch** (plans with `plan_schema_version` 7 or higher, or `begin --isolate`): `land` merges the default branch into the plan branch, merges the plan into a separate land worktree, and waits for your `land-ack`. `references/plan-isolation-contract.md` is the full rule. §17 says that your answers and the review passes carry over when every new commit on the default branch changes only live-state or machine-churn paths, none of them a file the plan changes, and the plan's diff is unchanged; that an owner stop leaves a reservation and not a lease; that the push runs in the same step as `land-ack`; and that a running land renews its lease, so a live holder is never taken over. §18 splits a land into a long phase with no hold and a short phase around the push, and takes gate classes from a registry, never from a duration.
- **A closeout can arrive as bare JSON** with no tags, or HTML-escaped. `plan-execute` reads both.
- **Write `$HOME`, never `~`, inside a shell variable** that holds the `run.py` command (`P="python $HOME/.claude/..."`). A `~` inside quotes does not expand, and the command fails.

## Cautions

- **It writes to your git repo.** It writes `_plans/` into the project, can
  make `plan/*` branches and worktrees, and `land` pushes to your default
  branch after your approval.
- `--auto` runs a whole plan by itself and dispatches many subagents. That
  costs real tokens. It still stops at human checkpoints.
- The default review gate `code-review-gate` has no portable default and fails
  on purpose. Bind your review gates to the `llm-review-*` gates, or define your
  own in `<project>/.claude/eval-gates.json`.
- The default gate file has an `eval-smoke-baseline` gate for a project that
  has its own `eval` skill. This repo does not ship one, so do not name that
  gate in a plan unless your project has the skill.
- It refuses to run on a folder that iCloud, Dropbox or a network drive syncs,
  unless you pass `--unsafe-lock`.
- Three tier agents (`tier-opus-max`, `tier-sonnet-low`, `tier-sonnet-max`) are
  for a GLM / Z.ai setup. On an Anthropic account they matter only if a plan
  asks for those pairs.
- The `Stop` entry above holds two hooks, `turnend-guard.py` and `agent-elapsed-nag.py`, because the harness wires them as one entry. The installer adds it once, whichever of the two modules you install first. `./install-module --remove` for either module removes the whole entry, so run `--remove` for one and re-add the other by hand if you keep it. Each command skips itself when its file is missing.
- **A retired Codex model is refused, not rerouted.** A session whose `model` is
  `gpt-5.6-sol` (or `gpt-5.5...`) stops with a message that names the fix: re-pin it
  with `plan-execute amend-session <PLAN_DIR> --session <SID> --model <M>`. Pick
  `gpt-5.6-luna` for mechanical or standard builds, `gpt-5.6-terra` for an escalated
  build, or `gpt-6.2-sol` for judgement work (the lane default). The ids are the
  author's setup; use the ids your Codex plan offers.
- **Write an accept file after the closeout.** When you accept a review finding
  on record (`_verify_state/<session>.accepted.json`, see the review-halt exit above),
  the gate warns on its convergence line if the file is older than the session's last
  closeout, because a dispatched session could have written it. The warning does not block.
- **Two policy files.** `install.sh` writes the routing policy to
  `~/.claude/claude/model-routing.yaml`; this module reads
  `~/.claude/model-routing.yaml`. They are meant to differ in one way:
  `harness/model-routing.yaml` writes the Anthropic and Z.ai models as Claude
  Code tokens (`haiku`, `sonnet`, `opus`, `fable`), because plan-execute
  dispatches and escalates by token. Keep every other value the same. To
  refresh it, copy `harness/model-routing.yaml` again; do not copy
  `claude/model-routing.yaml` over it, or escalation stops working.
