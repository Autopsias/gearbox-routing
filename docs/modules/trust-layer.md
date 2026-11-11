# Office trust layer (`trust-run`, `trust-review` and seven sub-skills)

Checks a memo, deck or workbook before it travels to a board, a client or an
investor. You run one command, `trust-run`. It sends the file through nine
skills in order and returns one readiness verdict with one fix list. A second
model, in a fresh context, tries to find what is wrong. It never edits your file.

Terms used on this page:

- **Gearbox** is this repo: a copy of one working Claude Code setup, split into modules you pick from.
- **Travel class** is how far a file will travel: Internal, Travels, or Board-or-Regulatory. A higher class gets a deeper review.
- **Provider pack** is a small Python file that tells the trust skills which of your own tools can search and read your notes. Without one they use a folder of Markdown files instead.

| | |
|---|---|
| **Status** | Optional · experimental |
| **Platform** | Any with `python3` |
| **Needs** | A second model for the cross-vendor reviewer: the Codex CLI, or a packet you paste into another vendor's chat app. Optional: a web search tool for outside facts, `TRUST_CORPUS_DIR` (a folder of Markdown notes the skills may search), `TRUST_MEMORY_DIR` (a folder for run memory). |

## What you get

All nine skills ship in this one module. Install the module and you have every
skill `trust-run` calls.

- `harness/skills/trust-run/` — the one-command orchestrator. It runs the steps in order and ends at a human gate that waits for you.
- `harness/skills/trust-review/` — the hostile review. The reviewer runs in a fresh context on a different model, lists problems, and never edits. It repeats until no must-fix is open. Its `references/scripts/` folder holds the shared checks.
- `harness/skills/travel-class-router/` — step 1, the front door. Sets the travel class, and raises it when the content looks risky.
- `harness/skills/office-source-packet/` — the truth layer. Lists every source, splits claims into fact, assumption and estimate, and logs conflicts.
- `harness/skills/office-update/` — the refresh branch. Re-checks only the claims that changed when you update a file you already reviewed.
- `harness/skills/document-doctor/` — inspects a memo or HTML document (`.docx`, `.pdf`, `.md`, `.html`).
- `harness/skills/deck-architect/` — builds a deck's argument first, then inspects the deck.
- `harness/skills/evidence-map/` — ties each slide claim to the workbook cell and source behind it.
- `harness/skills/workbook-doctor/` — inspects an Excel workbook for formula and data traps.

The `trust-run` skill starts only when you type it (`/trust-run`). The others start by name or when your request matches.

## Skills they call or recommend

- `trust-run` calls `travel-class-router`, `office-source-packet`, `office-update`, `document-doctor`, `deck-architect`, `evidence-map`, `workbook-doctor` and `trust-review`. All are in this module.
- `trust-review` calls `travel-class-router`, `office-update`, `deck-architect`, `evidence-map`, `workbook-doctor` and `document-doctor`.
- The seven sub-skills also call each other: `travel-class-router`, `office-source-packet`, `office-update`, `document-doctor`, `deck-architect`, `evidence-map`, `workbook-doctor`.
- Optional, not in Gearbox: the `pptx`, `docx`, `xlsx` and `pdf` skills from Anthropic. `workbook-doctor` finds the `xlsx` skill's `recalc.py` through `SKILLS_ROOT` to recalculate a workbook.
- Optional, off unless installed: the finance validators `finance:variance-analysis`, `finance:reconciliation`, `finance:audit-support` and `finance:financial-statements`. `workbook-doctor` recommends only the ones it finds by exact folder name. Set `WD_FINANCE_SKILLS=1` to treat all four as present.

## Install

Copy all nine skill folders. The skills find each other by folder name, so keep them side by side.

```bash
mkdir -p ~/.claude/skills
cp -R harness/skills/trust-run harness/skills/trust-review ~/.claude/skills/
cp -R harness/skills/travel-class-router harness/skills/office-source-packet \
   harness/skills/office-update harness/skills/document-doctor \
   harness/skills/deck-architect harness/skills/evidence-map \
   harness/skills/workbook-doctor ~/.claude/skills/
```

The document and deck inspectors read Office files with `markitdown`,
`python-pptx` and `openpyxl`. Install them with pip if you do not have them.

```bash
python3 -m pip install --user "markitdown[docx,pptx,xlsx,pdf]" python-pptx openpyxl  # optional
```

To let the skills search your own notes, point them at a folder of Markdown
files, and give the builder a place for run memory:

```bash
export TRUST_CORPUS_DIR=~/notes       # optional: notes the skills may search and read
export TRUST_MEMORY_DIR=~/trust-memory  # optional: standing corrections for the builder
```

A folder named `quarantine` inside `TRUST_CORPUS_DIR` is never searched or read.

## Check it works

Ask the registry which tools it can bind. It prints one line per operation
(internal search, web search, reviewer and others): the provider it chose, marked
`(degraded)` when that is a weaker fallback, or `unbound` when none was found.
It ends with the provider packs it loaded and any that failed.

```bash
python3 ~/.claude/skills/trust-review/references/scripts/adapter_registry.py --resolve --tools "TOOL_ONE,TOOL_TWO"
```

Replace `TOOL_ONE,TOOL_TWO` with the names of the tools your session has, comma-separated. With no
`--tools` it assumes no tool servers are connected, so most operations show
`(degraded)` or `unbound`. A degraded or unbound operation is named in the run's verdict.

## Write a provider pack for your own knowledge tool

A pack is one file named `provider_pack_<name>.py`, placed beside
`adapter_registry.py` in `trust-review/references/scripts/`. You can also keep it
anywhere and list its path in `OTL_PROVIDER_PACKS` (paths separated as in `PATH`).
A pack declares data only and imports nothing from the registry:

- `PROVIDERS` — `{operation: [{"id", "kind", "match", "note", "preferred", "before"}]}`. Each entry is inserted before the generic provider whose id is `before`, or at the end of the chain. `kind` is `mcp`, `local` or `builtin`. For `mcp`, `match` is a list of groups of tool-name pieces that must all appear in a tool's name.
- `local_caps()` — optional. Returns the capability ids your pack detects.
- `PARSERS`, `LABELS`, `THEME` — optional hooks for your tool's result rows, chain labels and the digest page colours.

A pack that fails to import, or has one bad provider id, is skipped whole and
listed under the packs that failed. The run then continues on the generic chain.
A pack cannot remove or reorder generic providers. See
`trust-review/references/capability-inventory.md` for the operations and
`test_provider_packs.py` in the same scripts folder for working examples.

## Remove

```bash
rm -r ~/.claude/skills/trust-run ~/.claude/skills/trust-review
rm -r ~/.claude/skills/travel-class-router ~/.claude/skills/office-source-packet \
   ~/.claude/skills/office-update ~/.claude/skills/document-doctor \
   ~/.claude/skills/deck-architect ~/.claude/skills/evidence-map \
   ~/.claude/skills/workbook-doctor
```

## Cautions

- **It sends excerpts of your file to the reviewer model.** What it sends follows the run's sensitivity setting: excerpts are redacted, and whole sensitive files are not sent by default. Check that your data rules allow the reviewer vendor.
- It is experimental. The reviewer model is pinned in the skill; change it to a model you can reach.
- The review costs several model runs per round, and it repeats until no must-fix is open or the round cap is hit. Hitting the cap with anything open gives a not-ready verdict.
- A run with a missing tool (no web search, no notes folder) degrades and says so. It does not pretend the check happened.
- The run ends at a human gate. The verdict is read-only until you acknowledge the full finding set.
