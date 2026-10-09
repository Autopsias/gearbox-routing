# Explainers (`/eli5`)

Turns a subject, a memo or a piece of code into a story-style HTML page with
large visuals and few words, written for an audience that you name.

| | |
|---|---|
| **Status** | Optional · stable |
| **Platform** | Claude Code with the Artifact tool |
| **Needs** | `python3` for the text-width check |

## What you get

- `harness/skills/eli5/SKILL.md` — the process: read, pick the audience, storyboard, build, check, publish.
- `harness/skills/eli5/scripts/svg_text_audit.py` — finds SVG text that overflows its box.
- `harness/skills/eli5/references/why-these-rules.md` — the reasons behind the rules.
- `harness/skills/eli5/cowork/` — a version for Claude Desktop. Not needed for Claude Code.

## Install

```bash
mkdir -p ~/.claude/skills
cp -R harness/skills/eli5 ~/.claude/skills/
```

## Check it works

Type `/eli5 how a git rebase works, for a non-technical manager`. Claude asks
about the audience, then publishes a page.

## Remove

```bash
rm -r ~/.claude/skills/eli5
```

## Cautions

- It publishes an Artifact page. Artifacts are private by default; check the
  page before you share it.
- The skill file is long (about 27 KB), so it uses much context when it loads.
