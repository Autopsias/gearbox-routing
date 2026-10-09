# Skill authoring (`/write-a-skill`, `/mods`, `/codex-skill-sync`)

`/write-a-skill` helps you write a new skill and scores it against a
17-criterion quality rubric. `/mods` decides whether a Claude Code mod (a
plugin with TypeScript event handlers) is worth building, then creates or
reviews one. `/codex-skill-sync` copies your Claude Code skills into
`~/.agents/skills` so that OpenAI Codex sees them too.

| | |
|---|---|
| **Status** | Optional. `write-a-skill`: stable. `mods`: experimental. `codex-skill-sync`: author-specific. |
| **Platform** | Any. `codex-skill-sync` needs the Codex CLI and PyYAML. `mods` needs a Claude Code version with mod support and `claude plugin validate`. |
| **Needs** | Nothing else for `write-a-skill` |

## What you get

- `harness/skills/write-a-skill/` — the process, a template and the rubric (`references/skill-quality-rubric.md`).
- `harness/skills/mods/` — modes `list`, `scout`, `source`, `create`, `review`, `improve`, and a read-only inventory script.
- `harness/skills/codex-skill-sync/` — `scripts/sync_from_claude.py`. It only reports by default; `--apply` writes.

## Install

```bash
mkdir -p ~/.claude/skills
cp -R harness/skills/write-a-skill ~/.claude/skills/
cp -R harness/skills/mods ~/.claude/skills/                 # optional
cp -R harness/skills/codex-skill-sync ~/.claude/skills/     # optional, Codex users only
```

## Check it works

Ask Claude to "write a skill for <a task>". It asks about the use cases before
it drafts, and scores the draft against the rubric.

```bash
python3 ~/.claude/skills/mods/scripts/mod_inventory.py
```

lists your installed plugins and mods, and writes nothing.

## Remove

```bash
rm -r ~/.claude/skills/write-a-skill ~/.claude/skills/mods ~/.claude/skills/codex-skill-sync
```

## Cautions

- Some text tells you to write skills in the original author's private repo
  and deploy them with `gearbox deploy`. Ignore it and edit
  `~/.claude/skills/` directly.
- The rubric asks you to run `~/.claude/skills/.s08-verify-dangling-refs.sh`.
  That gate checks the original author's command layout: it needs
  `~/.claude/SKILL-UNIFICATION-ROUTING.md` and about twenty specific commands
  and agents, so it fails on any other setup. Skip that rubric line unless you
  installed the whole harness.
- `codex-skill-sync --apply` writes into `~/.agents/skills` and can delete old
  copies that it made. Its lists of protected skills are the original author's.
- `mod_inventory.py` runs `claude plugin validate` on each installed plugin.
