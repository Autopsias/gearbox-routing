# UI design and review (`ui-craft`)

Designs, builds and reviews any user interface: web apps, sites, dashboards,
HTML reports and Artifact pages, plus product mock-ups and demo animations. It
reads the design rules of [Stageshow](https://github.com/benjitaylor/skills)
(Benji Taylor's skill) in place, checks that text fits its boxes, and has two
blind reviewers (a Claude subagent and Codex) judge screenshots in rounds.

| | |
|---|---|
| **Status** | Optional · stable |
| **Platform** | Any with `git`, `python3`, Node.js and `npm` |
| **Needs** | A one-time download of Stageshow from GitHub (below). The Codex CLI for the second reviewer; without it the skill runs one reviewer and says so. |

## What you get

- `harness/skills/ui-craft/SKILL.md` — the modes: BUILD, REVIEW, DIRECTION (is the structure right), DEMO (mock-ups) and UPSTREAM (take a new Stageshow release).
- `harness/skills/ui-craft/references/` — how to review real pages, the direction review, and the upstream steps.
- `harness/skills/ui-craft/scripts/` — `fetch_vendor.sh` (downloads Stageshow), `stage.sh` (an npm work copy outside your repos), `shoot.mjs` (screenshots and a site crawl), `check_refs.py`, `mark.py` and `codex_review.sh`.
- `harness/skills/ui-craft/vendor.json` — the Stageshow commit the skill is pinned to, and its content hash.

This repo does not ship Stageshow itself. Its code is MIT, but some of its
artwork (app icons and brand marks) is not, so you download it from its own
repo under its own terms.

## Install

```bash
mkdir -p ~/.claude/skills
cp -R harness/skills/ui-craft ~/.claude/skills/
```

Then download Stageshow once:

```bash
bash ~/.claude/skills/ui-craft/scripts/fetch_vendor.sh
```

It fetches the pinned commit into `~/.claude/skills/ui-craft/vendor/stageshow`,
and refuses the download if its git tree hash is not the one in `vendor.json`.

## Check it works

```text
python3 ~/.claude/skills/ui-craft/scripts/check_refs.py
```

It prints `check_refs: all references found`. Then ask Claude to "review the
layout of <a page or URL>": it takes screenshots, and the reviewers answer
with findings.

## Remove

```bash
rm -r ~/.claude/skills/ui-craft
rm -rf ~/.cache/ui-craft    # the npm work copy, if stage.sh built one
```

## Cautions

- `stage.sh` runs `npm ci --ignore-scripts` on Stageshow's locked packages, in
  `~/.cache/ui-craft/`. No package runs code at install, but it does download
  packages from npm.
- The second reviewer sends screenshots to Codex (OpenAI). Do not review a page
  that shows data you may not send there.
- A new Stageshow release is not taken by itself: follow
  `references/upstream.md`, then move the pin in `vendor.json`.
