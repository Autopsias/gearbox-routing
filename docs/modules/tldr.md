# Executive summaries (`/tldr`)

Turns one or many long sources (reports, contracts, a folder of documents,
transcripts, a codebase) into a one-page brief that puts the decision first.
It can also make an explainer page, a poster, a deck and a video. All of them
come from one list of claims, so a number can never differ between two outputs.

| | |
|---|---|
| **Status** | Optional · new |
| **Platform** | Claude Code |
| **Needs** | `python3`; Google Chrome for PDF and PNG; `pandoc` for DOCX; the [Explainers](eli5.md) module for the page |

## What you get

- `harness/skills/tldr/SKILL.md` — the process: read, build the claim list, write the brief, render the outputs, check them.
- `harness/skills/tldr/scripts/ledger_check.py` — fails when an output has a number that is not in the claim list, or does not state the ask word for word.
- `harness/skills/tldr/scripts/render.sh` — renders the brief to PDF, PNG or DOCX with tools already on the machine.
- `harness/skills/tldr/references/` — the brief template, the claim-list format and the reasons behind the rules.

## Install

```bash
mkdir -p ~/.claude/skills
cp -R harness/skills/tldr ~/.claude/skills/
```

## Check it works

Type `/tldr <path to a long PDF>`. Claude asks about the audience and the
outputs, then writes them under `tldr-out/` in the working directory.

## Remove

```bash
rm -r ~/.claude/skills/tldr
```

## Cautions

- `render.sh` looks for Chrome at the macOS path. On another system, set
  `TLDR_CHROME` to your Chrome or Chromium binary.
- The deck uses your own brand skill if you have one; with none, it is plain
  Marp. The video needs the `video-creator` plugin. Both are optional.
