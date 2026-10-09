# Grilling (`/grill-me`, `/grill-with-docs`)

Interviews you about a plan, one question at a time, until each decision is
settled. Each question comes with a recommended answer.

| | |
|---|---|
| **Status** | Optional · stable |
| **Platform** | Any |
| **Needs** | [Adversarial review](adversarial-review.md) for the last step. Without it, the skill says so and stops there. |

## What you get

- `harness/skills/grill-me/` — the interview. It changes no files. It ends with a table of decisions and a prompt you can paste to start the work.
- `harness/skills/grill-with-docs/` — the same interview. It also writes the terms you agree into `CONTEXT.md` and offers an ADR (a short decision record in `docs/adr/`) for hard-to-reverse decisions. Includes `CONTEXT-FORMAT.md` and `ADR-FORMAT.md`.
- `harness/skills/_shared/grill-adversarial-review.md` — the shared opening and closing text. Both skills read it from `~/.claude/skills/_shared/`.

## Install

```bash
mkdir -p ~/.claude/skills
cp -R harness/skills/grill-me harness/skills/grill-with-docs harness/skills/_shared ~/.claude/skills/
```

## Check it works

Type `/grill-me` and a one-line plan. Claude asks one question at a time, each
with a recommended answer.

## Remove

```bash
rm -r ~/.claude/skills/grill-me ~/.claude/skills/grill-with-docs ~/.claude/skills/_shared
```

## Cautions

- `/grill-with-docs` creates and edits `CONTEXT.md` (and ADR files, when you
  accept one) in your repo.
- If no plan file exists, the last step writes the plan to
  `~/.codex/plans/grill-<slug>-<date>.md`, also when you do not use Codex.
