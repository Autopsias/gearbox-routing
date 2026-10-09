# Pre-push gate (`/no-mistakes`) and saved workflows

`/no-mistakes` runs your committed branch through review, tests, lint, docs,
push, pull request and CI before it reaches the remote, and stops at each gate
for your decision. The workflow scripts are templates for multi-agent checks.

| | |
|---|---|
| **Status** | Optional · stable skill. The tool itself is a separate project. |
| **Platform** | Any where the `no-mistakes` tool runs |
| **Needs** | The [`no-mistakes`](https://github.com/kunchenguid/no-mistakes) tool, installed from its own repo. `--rules` and the workflow scripts need Claude Code's Workflow tool. |

## What you get

- `harness/skills/no-mistakes/` — drives the `no-mistakes` pipeline gate by gate. Has a validate-only mode and a task-first mode.
- `harness/workflows/rule-adherence.js` — used by `--rules`: one checker agent per rule in the `## Behavior` section of your `CLAUDE.md`, then a sceptic agent per hit.
- `harness/workflows/adversarial-verify.js` — a template: finder agents per review area, two checkers per finding, majority vote.
- `harness/workflows/README.md` — rules for workflow scripts. The main rule: every `agent()` call sets `opts.model`, or the agent inherits your session's (expensive) model.

## Install

```bash
mkdir -p ~/.claude/skills ~/.claude/workflows
cp -R harness/skills/no-mistakes ~/.claude/skills/
cp harness/workflows/*.js harness/workflows/README.md ~/.claude/workflows/
```

Then install the `no-mistakes` tool from its repo and run `no-mistakes init`
once in each repo.

## Check it works

Run `no-mistakes doctor` in a git repo. Then, on a branch with committed work,
type `/no-mistakes`. It starts a run and stops at the first gate.

## Remove

```bash
rm -r ~/.claude/skills/no-mistakes
rm ~/.claude/workflows/{rule-adherence.js,adversarial-verify.js,README.md}
```

Remove the tool as its own repo says.

## Cautions

- **The pipeline pushes and opens a pull request.** It stops at each gate,
  but `--yes` lets it pass the gates without you.
- Task-first mode (`/no-mistakes <task>`) edits code and commits to a branch.
- `--rules` costs one model call per rule, plus one per hit.
