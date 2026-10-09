# Routing skills (`/routing-update`, `/routing-retro`)

`/routing-update` researches a model change (a new model, a price change, a
deprecation) against live provider docs and updates the routing policy in one
approved change. `/routing-retro` reads past sessions and reports where the
routing did not fit the work. It changes nothing.

| | |
|---|---|
| **Status** | Optional · see "Which copy" |
| **Platform** | Any |
| **Needs** | The routing policy. `/routing-update` needs one research MCP server (Exa, Ref or Perplexity); without one it stops instead of guessing. |

## Which copy

This repo has two copies of each skill:

| | `claude/skills/` (installed by `install.sh`) | `harness/skills/` |
|---|---|---|
| Reads the policy at | `claude/model-routing.yaml`, relative to the folder you start Claude Code in | `~/.claude/model-routing.yaml` |
| `routing-retro` | One scanner script. Writes only to the misroute ledger, after you confirm. | 16 scripts. Also reads the outcome logs that the [plan pipeline](plan-pipeline.md) and the [compaction policy](compaction.md) write. |
| `routing-update` | Stand-alone | Needs the plan pipeline. Some steps assume the original author's deploy tool. |
| Shows up as a slash command after install | **No** (see below) | Yes, if you copy it to `~/.claude/skills/` |

**Known limitation.** `install.sh` puts its copies in `<target>/claude/skills/`.
Claude Code loads personal skills only from `~/.claude/skills/<name>/`, so the
installed copies do not show up as `/routing-update` and `/routing-retro`.

## Install

**If you use the plan pipeline**, take the harness copies:

```bash
mkdir -p ~/.claude/skills
cp -R harness/skills/routing-retro harness/skills/routing-update ~/.claude/skills/
```

**If you use only the routing framework**, copy the `claude/` versions into
your skills folder, and start Claude Code in the folder that holds
`claude/model-routing.yaml` (your install target, or this repo):

```bash
mkdir -p ~/.claude/skills
cp -R claude/skills/routing-retro claude/skills/routing-update ~/.claude/skills/
```

Install one copy or the other, never both: they have the same names.

## Check it works

In a new session, type `/routing-retro`. It reads the policy and reports, and
changes nothing.

## Remove

```bash
rm -r ~/.claude/skills/routing-retro ~/.claude/skills/routing-update
```

## Cautions

- `/routing-update` edits several routing files after one approval. It bumps
  the policy version and writes a changelog entry.
- The shipped model ids and prices are dated examples. See
  [`PROVIDERS.md`](../PROVIDERS.md).
