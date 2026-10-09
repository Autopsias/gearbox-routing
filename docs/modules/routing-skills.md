# Routing skills (`/routing-update`, `/routing-retro`)

`/routing-update` researches a model change (a new model, a price change, a
deprecation) against live provider docs and updates the routing policy in one
approved change. `/routing-retro` reads past sessions and reports where the
routing did not fit the work. It never changes the routing policy. It writes
working files (scan output and a report under `/tmp`; the harness version also
keeps a small `.last-aggregated` bookkeeping file next to the outcome log), and
it adds an entry to the misroute ledger only after you confirm it.

| | |
|---|---|
| **Status** | Optional · see "Which copy" |
| **Platform** | Any |
| **Needs** | The routing policy. `/routing-update` needs one research MCP server (Exa, Ref or Perplexity); without one it stops instead of guessing. |

## Which copy

This repo has two copies of each skill. Install one copy or the other, never
both: they have the same names.

| | `claude/skills/` (installed by `install.sh`) | `harness/skills/` |
|---|---|---|
| Reads the policy at | `~/.claude/claude/model-routing.yaml` after `install.sh`; `claude/model-routing.yaml` in a clone of this repo | `~/.claude/model-routing.yaml` |
| `routing-retro` | One scanner script. Works from any project. Writes only to the misroute ledger, after you confirm. | 16 scripts. Also reads the outcome logs that the [plan pipeline](plan-pipeline.md) and the [compaction policy](compaction.md) write. |
| `routing-update` | Run it in your clone of this repo; then run `install.sh` again. | Needs the plan pipeline. Some steps assume the original author's deploy tool. |

## Install

**If you use only the routing framework**, `install.sh` installs these skills
for you ([`INSTALL.md`](../INSTALL.md)). With `--claude-home "$HOME/.claude"`
they land in `~/.claude/skills/`, and Claude Code offers them in the next
session.

**If you use the plan pipeline**, take the harness copies instead:

```bash
mkdir -p ~/.claude/skills
cp -R harness/skills/routing-retro harness/skills/routing-update ~/.claude/skills/
```

## Check it works

In a new session, type `/routing-retro`. It reads the policy and reports. Apart
from its working files under `/tmp` and the bookkeeping file, it writes only to
the misroute ledger, and only if you confirm an entry.

## Remove

```bash
rm -r ~/.claude/skills/routing-retro ~/.claude/skills/routing-update
```

## Cautions

- `/routing-update` edits several routing files after one approval. It bumps
  the policy version and writes a changelog entry.
- The shipped model ids and prices are dated examples. See
  [`PROVIDERS.md`](../PROVIDERS.md).
