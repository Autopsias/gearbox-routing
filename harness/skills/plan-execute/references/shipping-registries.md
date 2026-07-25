# Shipping registries — deploy targets & eval gates

Post-session shipping (`post_session` / `phase_closer` in a plan spec) keeps all
project-specific knowledge in two **project-local** registries so the global
skills stay project-agnostic:

- `<project>/.claude/deploy-targets.json` — names the deploy actions a plan may invoke.
- `<project>/.claude/eval-gates.json` — names the pre-deploy gates a plan may require.

At resolution time each registry is the skill-bundled default
(`plan-execute/references/{deploy-targets,eval-gates}.default.json`) **merged
under** the project-local file (project wins). A `deploy:` target or
`pre_deploy_gates:` id that resolves in neither is a **build-time error** — the
plan never builds with a dangling shipping reference.

## Two execution kinds

Every target / gate is one of:

- **`skill`** — invoked by the ORCHESTRATOR (Claude) via the Skill tool, because
  a plain Python process cannot call the Skill tool. `/plan-execute` emits a
  directive; the orchestrator runs the skill and records the outcome.
  ```json
  { "kind": "skill", "skill": "deploy-update", "args": "", "probe_flags": [], "timeout": 1800 }
  ```
- **`argv`** — a real executable run by `shipping.py` itself (`shell=False`,
  explicit `cwd`, **allow-listed** env — never the full inherited environment).
  ```json
  { "kind": "argv", "argv": ["./scripts/deploy.sh", "--prod"], "cwd": ".",
    "env_allowlist": ["AWS_PROFILE"], "command_failure_mode": "fail-halt", "timeout": 1800 }
  ```

`command_failure_mode`: `fail-halt` (default for deploy — non-zero exit halts for
the operator) or `best-effort` (10s timeout, exit code ignored, never blocks —
fire-and-forget hooks only).

## Deploy targets (`deploy-targets.json`)

```json
{
  "ec2": { "kind": "skill", "skill": "deploy-update", "args": "", "timeout": 2400 },
  "aws": { "kind": "skill", "skill": "aws-deploy", "args": "", "timeout": 1800 },
  "staging": { "kind": "argv", "argv": ["./scripts/deploy-staging.sh"], "cwd": ".",
               "env_allowlist": ["AWS_PROFILE"], "timeout": 900 }
}
```

A session references a target by name: `"deploy": "ec2"`. The per-session
`deploy_argv` field is an escape hatch that bypasses the registry with an
explicit argv vector (always argv-kind).

## Eval gates (`eval-gates.json`)

```json
{
  "eval-smoke-baseline": { "kind": "skill", "skill": "eval", "args": "--smoke",
                           "probe_flags": ["--smoke"], "timeout": 1200,
                           "success_criteria": "PASS, no metric drop >2%",
                           "fixture_fake": { "returncode": 0, "stdout": "PASS (fixture)" } }
}
```

`fixture_fake` lets `--dry-run` / CI simulate an argv-kind gate deterministically
(returncode + stdout) without running the real gate. `eval-smoke-baseline` ships
as a skill-bundled default, so any project with an `/eval --smoke` skill can use
it without authoring its own registry.

## Why project-local

A deploy target names a project-specific action; baking example-project's
`ec2`/`aws` (or anyone's) into a global-skill enum would couple the shared skill
to one project's vocabulary. The schema stays `none | <opaque-target-name>`; the
meaning of each name lives in the project that owns the deploy.
