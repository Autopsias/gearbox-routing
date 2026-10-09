# Adversarial review (`/adversarial-review`)

Reviews code or a plan with two models: Claude and OpenAI's Codex review it
separately, then Claude merges the findings. For a plan, it also writes a
hardened version and has Codex check it again.

| | |
|---|---|
| **Status** | Optional · stable |
| **Platform** | Any with `bash` and `python3` |
| **Needs** | The Codex CLI (`codex` on your `PATH`). For a review of a git diff, also the Codex plugin for Claude Code (the command calls `~/.claude/plugins/cache/openai-codex/codex/*/scripts/codex-companion.mjs`). |

## What you get

- `harness/commands/adversarial-review.md` — the command. It finds the target, runs the two reviews in parallel, merges them, and for a plan runs up to 3 Codex check rounds.
- `harness/references/adversarial-review/plan-revision-and-verify-loop.md` — the plan phase. The command reads it only for a plan.
- `harness/scripts/codex_supervised.py` — runs `codex exec` and restarts it if its output stops. Used for plans and documents.
- `harness/scripts/codex_watchdog.py` — watches the Codex output file on the git-diff path.

`harness/skills/adversarial-review/` is **not** a Claude Code skill. It holds
only the Codex CLI version (`codex/`). The Claude Code entry point is the command.

## Install

```bash
mkdir -p ~/.claude/commands ~/.claude/references ~/.claude/scripts
cp harness/commands/adversarial-review.md ~/.claude/commands/
cp -R harness/references/adversarial-review ~/.claude/references/
cp harness/scripts/codex_supervised.py harness/scripts/codex_watchdog.py ~/.claude/scripts/
npm install -g @openai/codex@latest      # the Codex CLI, if you do not have it
```

No `settings.json` change.

## Check it works

```bash
python3 ~/.claude/scripts/codex_supervised.py --help
codex --version
```

Then, in a repo with a small uncommitted change, type `/adversarial-review`.
It names the target it found, writes two review files
(`/tmp/adversarial-review-*-claude.md` and `-codex.md`), and shows one merged
list of findings.

## Remove

```bash
rm ~/.claude/commands/adversarial-review.md
rm -r ~/.claude/references/adversarial-review
rm ~/.claude/scripts/codex_supervised.py ~/.claude/scripts/codex_watchdog.py
```

Keep the two scripts if you use the [plan pipeline](plan-pipeline.md) or
[memo-loop](memo-loop.md); they call them too.

## Cautions

- **It sends your code or plan to OpenAI through Codex.** Check that your
  project allows this.
- It costs two model runs per review, and up to 3 more Codex runs for a plan.
- The command pins a Codex model id. Model ids change; if your Codex CLI is
  older than the pinned model, Codex returns an HTTP 400 error. Update the CLI
  or change the pin.
- For a plan, it edits the plan file (it marks each change `[HARDENED]`) and
  adds a `*-REVIEW-LOG.md` file next to it, without asking first.
- If Codex fails, it reports Claude's findings alone and says so.
