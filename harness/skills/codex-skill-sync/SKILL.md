---
name: codex-skill-sync
description: |-
  Mirror the canonical Claude skills, agents, commands and enabled-plugin skills into
  ~/.agents/skills so Codex loads the same set. Use when a skill was added, edited or
  removed on the Claude side and Codex should see it, or when the user says "sync my
  Codex skills", "does Codex have that skill", "update the Codex mirror", "why is
  Codex running an old version of this skill", or asks whether Codex and Claude are
  in step. Not for authoring a new skill (write-a-skill) and not for the
  hand-authored Codex ports under ~/.codex/skills, which this never touches.
disable-model-invocation: true
---

# Codex skill sync

Claude is canonical. Codex reads its own roots, so the two drift the moment a skill
changes — this makes the mirror a generated artifact instead of a copy someone has to
remember to update.

`gearbox deploy` runs the apply form automatically after it successfully renders the
Gearbox-owned Codex ports. Use the commands directly for diagnosis or an immediate
manual refresh:

```bash
python3 ~/.claude/skills/codex-skill-sync/scripts/sync_from_claude.py          # report only
python3 ~/.claude/skills/codex-skill-sync/scripts/sync_from_claude.py --apply  # write
```

Always read the dry run before applying. The script is idempotent: a second run with
nothing changed reports zero writes, so any non-zero number is real drift.

## What it mirrors

| Source | Into |
|---|---|
| `~/.claude/skills/*/SKILL.md` | `~/.agents/skills/<name>/` |
| `~/.claude/agents/*.md` | same |
| `~/.claude/commands/*.md` (BMAD prefixes stripped) | same |
| skills of every **enabled** plugin in `installed_plugins.json` | same |

Supporting `references/`, `assets/` and `scripts/` directories travel with the skill,
so a mirrored skill can open the files it cites.

## Five rules it will not break

1. **A Gearbox Codex port wins.** Any name `~/.codex/skills` already owns is skipped.
   Those are hand-authored ports, and that tree is a deploy target this script must
   never write to.
2. **A skill Claude symlinks back to is skipped.** Seven live in `~/.agents` and are
   linked into `~/.claude`; for those the arrow runs the other way, and writing one
   edits the Claude side through the link.
3. **Nothing is overwritten if content would be lost.** A mirror is replaced only when
   the incoming text accounts for every line it already has. Otherwise the script
   reports it and moves on.
4. **Claude-isms are translated.** `CLAUDE.md` → `AGENTS.md`, `Skill(skill='bmad-bmm-x')`
   → `$x`, Claude attribution dropped. Copying them verbatim would describe the wrong
   harness.
5. **Only generated mirrors are removed.** When a source disappears, becomes Claude-only,
   or gains a Gearbox Codex port, the old mirror is deleted only when its `SKILL.md`
   carries this sync's provenance marker. Manual, protected, and reverse-symlinked skills
   are never removed.

## Reading the report

- **new / updated** — written (or would be, on a dry run).
- **removed** — a generated mirror no longer has a mirrorable Claude source.
- **skipped** — rules 1, 2, and the eight `tier-*` agents, which pin a Claude
  model+effort pair that Codex expresses as `-c model_reasoning_effort=`.
- **protected** — held back by rule 3. Each is a deliberate **Codex edition**: the
  mirror says something true of Codex the Claude source does not say. The roster and
  the reason per skill are in `references/held-back-skills.md`.

To adopt canon for one of them after reading its diff, add the name to `CANON_WINS`
in the script. Do not edit the mirror by hand — the next sync overwrites it.

## Gotchas

Every one of these was observed, not imagined.

- **Writing through a symlink edited the Claude canon.** Several skills
  (`tdd`, `diagnose`, `write-a-skill`, `grill-me`, `grill-with-docs`,
  `improve-codebase-architecture`, `setup-matt-pocock-skills`) are installed in
  `~/.agents` and symlinked *into* `~/.claude/skills`. Mirroring them wrapped the
  Claude-side file. Rule 2 exists because of this. Symptom: the sync never settles,
  reporting the same handful of skills as changed on every run.
- **Hand-rolled YAML frontmatter broke many skills in one pass.**
  Descriptions carry colons, quotes and `>`. The script emits frontmatter through
  PyYAML for that reason. An unparseable skill is an absent skill, so validate after
  any change to `render()`:
  `python3 -c "import yaml,pathlib; [yaml.safe_load(p.read_text().split('---')[1]) for p in pathlib.Path.home().glob('.agents/skills/*/SKILL.md')]"`
- **Codex truncates descriptions to roughly the first eight words.** It
  warns when it does. Put the verb and the object in the opening sentence, and prune
  skills you do not use: fewer skills means longer visible descriptions.
- **A blanket "Claude"→"Codex" replace corrupted many filesystem paths.**
  It produced `~/.Codex/`, which exists nowhere, and identifiers like `Codex-ai`. Never
  bulk-replace the vendor name; the translation table above is deliberately narrow.
- **A stale mirror instructed `git commit --no-verify`** where the current
  source forbids it. Staleness is not cosmetic; it inverts safety rules.
- **Removed Claude skills stayed installed in Codex.** The sync updated and
  added mirrors but never retired one, despite documenting removal. Cleanup now requires
  the generated provenance marker; name matching alone is never permission to delete.
- **A destination symlink redirected a mirror write outside the skill root.**
  The sync now refuses a skill or supporting-file destination containing a nested symlink,
  validates every destination before its first write, and honours deploy root overrides.

## Verifying it worked

Read the mirror as Codex sees it, never as a file on disk:

```bash
codex exec --sandbox read-only -m gpt-5.6-luna \
  "List every skill available to you and quote the description you see for <name>."
```
