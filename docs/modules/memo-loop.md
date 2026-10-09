# Decision memos (`memo-loop`)

Critiques a decision memo against a rubric that it builds for that memo, or
drafts a new memo. It returns a critique; it never rewrites your memo.

| | |
|---|---|
| **Status** | Optional · experimental |
| **Platform** | Any with `python3` |
| **Needs** | The Codex CLI for the second-model critic. Without Codex, it makes a packet that you paste into another vendor's chat app, and then it reads the reply back (`--ingest`). |

## What you get

- `harness/skills/memo-loop/SKILL.md` — modes `improve`, `create`, `reflect` and `status`.
- `harness/skills/memo-loop/references/` — one file per phase (intake and rubric, critique, report, council, reflect, run state and others).
- `harness/skills/memo-loop/codex/` — the Codex CLI version. Not needed for Claude Code.
- It also uses `harness/scripts/codex_supervised.py` and one file of the Codex review contract (below).

## Install

```bash
mkdir -p ~/.claude/skills ~/.claude/scripts ~/.claude/skills/adversarial-review/codex/references
cp -R harness/skills/memo-loop ~/.claude/skills/
rm -r ~/.claude/skills/memo-loop/codex
cp harness/scripts/codex_supervised.py ~/.claude/scripts/
cp harness/skills/adversarial-review/codex/references/finding-contract.md \
   ~/.claude/skills/adversarial-review/codex/references/
```

## Check it works

Type `memo-loop status`. It reads its state, writes nothing, and lists the
critic backends it found.

## Remove

```bash
rm -r ~/.claude/skills/memo-loop ~/.claude/skills/adversarial-review/codex
rm -r ~/.claude/memo-loop      # optional: run history and lessons
```

## Cautions

- It sends the memo to the critic model (Codex or another vendor). Check that
  your data rules allow this.
- It keeps run state and lessons in `~/.claude/memo-loop/`.
- The critic's Codex model id is pinned in `references/critique.md`. Change it
  to a model your Codex CLI accepts.
- Only `reflect` writes standing rules, and only the ones you name.
