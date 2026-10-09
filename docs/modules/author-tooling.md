# The original author's own tooling (not for general use)

`harness/` is a copy of one person's working setup, so it also carries the
tools that person uses to deploy that setup. They assume a private git repo, a
specific folder layout and files that this repo does not ship. **Do not
install them.** This page exists so that you know what they are.

| File | What it does in the original setup | Why it does not work for you |
|---|---|---|
| `harness/scripts/gearbox` | `deploy`, `drift` and `harvest` between a private source repo and `~/.claude`. | Needs `~/.claude` to be a clone of that repo, and a `gearbox-codex.py` that is not shipped. `deploy` and `harvest` commit and push. |
| `harness/scripts/gearbox-classify.py`, `deploy.pathspec` | Sort changed paths in `~/.claude` for `gearbox`. | Used only by `gearbox`. |
| `harness/scripts/gearbox-weekly-drift.sh` | A weekly drift check with a macOS notification. | Calls a `doc_check.py` that is not shipped. |
| `harness/scripts/quiesce-check.sh` | Checks that no session writes to `~/.claude` before a deploy. | Used only by `gearbox deploy`. |
| `harness/scripts/verify-assignments.sh` | Checks the model table of the `/epic-dev` command. | Needs files that are not shipped. |
| `harness/scripts/install-hooks.sh`, `harness/githooks/` | Wires git hooks into the private source repo. | The script looks for a top-level `githooks/` folder; here the folder is `harness/githooks/`, so it stops with an error. The hooks also need that repo's files. |
| `harness/hooks/gearbox-drift-warn.sh` | Runs `gearbox drift` at session start. | Expired; see [small hooks](small-hooks.md). |
| `harness/skills/*/codex/` and `*/manifest.toml` | Codex CLI versions of some skills, which `gearbox deploy` copies into `~/.codex/skills/`. | There is no installer for them here. You can copy one `SKILL.md` by hand at your own risk. |

If you only want a secret scan before each commit in your own repos, use
`gitleaks protect --staged` in a `pre-commit` hook instead.
