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

- `harness/skills/worth-adopting/` — a 7-step checklist, one verdict per idea, a report template and a ledger template.
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
- It mentions a placeholder repo name (`~/your-private-harness`) from the
  original setup. Name your target repo, or pass `--repo <path>`.
- `/jev-fit-scan` sends no repo content out. In deep mode, its agents read web
  pages, which it treats as untrusted data. Keep permission prompts on.
- `/jev-fit-scan` can only be started by you (`disable-model-invocation`).
- Its vendor snapshot is dated. It goes stale.
