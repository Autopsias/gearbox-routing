# The original author's own tooling (not for general use)

`harness/` is a copy of one person's working setup, so it also carries the
tools that person uses to deploy that setup. They assume a private git repo, a
specific folder layout and files that this repo does not ship. **Do not
install them.** This page exists so that you know what they are.

| File | What it does in the original setup | Why it does not work for you |
|---|---|---|
| `harness/scripts/gearbox`, `gearbox-codex.py` | `deploy`, `drift` and `harvest` between a private source repo and `~/.claude`, and the Codex skill copies. | Needs `~/.claude` to be a clone of that repo. `deploy` and `harvest` commit and push. |
| `harness/scripts/gearbox-classify.py`, `deploy.pathspec` | Sort changed paths in `~/.claude` for `gearbox`. | Used by `gearbox` and `gearbox-codex.py`. |
| `harness/scripts/gearbox-weekly-drift.sh`, `doc_check.py` | A weekly drift check and a check of the claims in the docs, with a macOS notification. | Checks the layout of that private repo. |
| `harness/scripts/quiesce-check.sh` | Checks that no session writes to `~/.claude` before a deploy. | Used only by `gearbox deploy`. |
| `harness/scripts/verify-assignments.sh`, `harness/epic-dev-assignments.yaml` | Checks the model and effort of each `/epic-dev` subagent against one table. | Works only on a home laid out like the original one. |
| `harness/scripts/install-hooks.sh`, `harness/githooks/` | Wires git hooks into the private source repo. | The script looks for a top-level `githooks/` folder; here the folder is `harness/githooks/`, so it stops with an error. The hooks also need that repo's files. |
| `harness/hooks/gearbox-drift-warn.sh` | Runs `gearbox drift` at session start. | Expired; see [small hooks](small-hooks.md). |
| `harness/skills/*/codex/` and `*/manifest.toml` | Codex CLI versions of some skills, which `gearbox deploy` copies into `~/.codex/skills/`. | There is no turnkey installer (no `install.sh` path) for them here. `gearbox-codex.py render` can copy the manifest-named files, and it takes `--claude-dir`, `--pathspec` and `--codex-skills` to point elsewhere. It is written for the original layout, so use it at your own risk. You can also copy one `SKILL.md` by hand. |

If you only want a secret scan before each commit in your own repos, use
`gitleaks protect --staged` in a `pre-commit` hook instead.
