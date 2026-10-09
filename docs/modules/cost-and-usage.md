# Cost and usage (`/cost-audit`, `/myusage-self-assessment`)

`/cost-audit` measures what your Claude Code sessions cost, from the local
session transcripts: prompt-cache hit rate, what caused each cache rewrite, the
effort used, and API-equivalent dollars by model.
`/myusage-self-assessment` reads your whole session history and writes a
ranked list of what to build or fix in your setup.

| | |
|---|---|
| **Status** | Optional · experimental |
| **Platform** | Any with `python3`. Both read `~/.claude/projects/`. |
| **Needs** | `/cost-audit`: `~/.claude/scripts/model_prices.py` and a `~/.claude/model-routing.yaml` with a `model_prices:` block. Both: `~/.claude/references/shared/`. The Artifact tool for the one-page reports. |

## What you get

- `harness/skills/cost-audit/` — the skill and `scripts/cache_effort_baseline.py`, the measuring script. The script finds the harness three folders above itself, so it must live at `~/.claude/skills/cost-audit/scripts/`.
- `harness/scripts/model_prices.py` — reads the prices for each model id from the policy file.
- `harness/skills/myusage-self-assessment/` — five phases, a digest script (`digest_sessions.py`) and a safe-append script.

## Install

```bash
mkdir -p ~/.claude/skills ~/.claude/scripts ~/.claude/references
cp -R harness/skills/cost-audit harness/skills/myusage-self-assessment ~/.claude/skills/
cp harness/scripts/model_prices.py ~/.claude/scripts/
cp -R harness/references/shared ~/.claude/references/
cp -n harness/model-routing.yaml ~/.claude/model-routing.yaml    # -n: never replace your own
```

If you already have `~/.claude/model-routing.yaml`, copy only its
`model_prices:` block from `harness/model-routing.yaml`.

## Check it works

```bash
python3 ~/.claude/skills/cost-audit/scripts/cache_effort_baseline.py 14
python3 ~/.claude/skills/myusage-self-assessment/scripts/digest_sessions.py --pretty --out /tmp/digest.json
```

The first prints spend, cache rewrites and the effort mix for session files
changed in the last 14 days. It counts every request in each such file, so a
recently resumed old session also adds its older requests. The second counts your sessions and costs no model calls.

## Remove

```bash
rm -r ~/.claude/skills/cost-audit ~/.claude/skills/myusage-self-assessment
rm ~/.claude/reflection-notes.md      # optional: the self-assessment log
```

## Cautions

- **Check `model_prices:` against the models you use.** The shipped block has
  rows for the current Claude models (prices as of 2026-10-09). A model with
  no row is skipped and listed on a `NOT PRICED` line, so the totals are low.
- The dollar figures are API-equivalent prices, not your bill on a subscription.
- Both read every session transcript, so the reports can contain private text.
- `/myusage-self-assessment` sends many subagents over your history. It is
  expensive. Only you can start it.
