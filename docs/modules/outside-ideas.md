# Outside-idea triage (`/worth-adopting`, `/jev-fit-scan`)

`/worth-adopting` reads an outside source (an article, a video transcript, a
post, a repo) and judges each idea in it against your repo: ADOPT, TRIAL, PARK
or DROP. `/jev-fit-scan` finds places in a repo where one vendor's
verdict-only model (TypeSafe's Jev) could replace a model call.

| | |
|---|---|
| **Status** | Optional. `worth-adopting`: stable. `jev-fit-scan`: niche — useful only if you consider that vendor. |
| **Platform** | Any with `python3` and web access |
| **Needs** | A web fetch tool for URLs. `jev-fit-scan --deep` also needs Claude Code's Workflow tool, `gh` and `curl`. |

## What you get

- `harness/skills/worth-adopting/` — a 7-step checklist, one verdict per idea, a report template and a ledger template. It also looks for where an idea applies above the target repo, keeps the source's main principles (three at most) apart from its concrete proposals, and checks the three weakest drops before it reports.
- `harness/skills/worth-adopting/scripts/wa_ledger.py` — the only way the skill reads or writes the ledger. `find '<author>|<tool>|<slug>'` prints one short line per matching row; `row '<date or source>'` prints one row, or one cell with `--field result`. A ledger row is 1 to 5 KB, so reading the whole file overflows the tool output and a cut cell reads as a fact. `scripts/youtube_source.py <url> <out-dir>` saves a YouTube video as one text source: the captions, with a marker at each moment the picture changes (a slide, a screen, a demo).
- `harness/skills/jev-fit-scan/` — the scan, the fit rules, a dated snapshot of the vendor docs, and scripts (`jev_docs.py`, `scan_sites.py`, `site_signals.py`, `deep_scan.workflow.js`). The `evals/` folder holds test fixtures only.

## Install

```bash
mkdir -p ~/.claude/skills
cp -R harness/skills/worth-adopting harness/skills/jev-fit-scan ~/.claude/skills/
rm -r ~/.claude/skills/jev-fit-scan/evals     # optional: test fixtures
```

## Check it works

```bash
python3 ~/.claude/skills/jev-fit-scan/scripts/jev_docs.py --self-test
```

It prints `self-test ok`. For the other skill, type `/worth-adopting <a blog post URL>`:
you get one verdict per idea, or "nothing here".

## Remove

```bash
rm -r ~/.claude/skills/worth-adopting ~/.claude/skills/jev-fit-scan
```

## Cautions

- `/worth-adopting` writes a report and a ledger file
  (`docs/worth-adopting/LEDGER.md`) in the target repo. It builds nothing.
  Its step 1 makes a worktree from `origin/main` and works there, so a local
  checkout that is behind cannot hide an earlier run from the ledger.
  Read and change the ledger with `wa_ledger.py`, never with `cat`, `grep` or
  an editor: a cell cut by hand is not a fact.
- It mentions a placeholder repo name (`~/your-private-harness`) from the
  original setup. Name your target repo, or pass `--repo <path>`.
- `/jev-fit-scan` sends no repo content out. In deep mode, its agents read web
  pages, which it treats as untrusted data. Keep permission prompts on.
- `/jev-fit-scan` can only be started by you (`disable-model-invocation`).
- Its vendor snapshot is dated. It goes stale.
