# Repo health (`/repo-health`)

Scores a whole repo from 0 to 100 on security, code quality, tests, CI, AI
readiness and hygiene. It keeps the history on an HTML dashboard and splits the
findings into BLOCKING (fix now) and ADVISORY.

It is the one front door for whole-repo reviews. Besides the score, you can pick
four design reviews:

- `refactoring` — shallow modules, one concept spread over many small modules,
  coupling that leaks across seams, and code that is hard to test through its
  interface. It adds the file-size and complexity rows when the score also ran.
- `over-engineering` — a quick scan for what can go. The deep audit is the
  `declutter` skill.
- `trust-ladder` — counts the repo's rules against its checks, and names at most
  five rules that could move to a stronger check.
- `test-value` — which tests earn their place. It counts mocks and reach into
  private names per test file, measures per-test coverage on one suite, and
  judges at most ten tests that own no line of their own.

| | |
|---|---|
| **Status** | Optional · stable — has its own tests |
| **Platform** | Any with `python3` and `git`. The scripts use the standard library only. |
| **Needs** | For the full set of checks: `gitleaks`, `semgrep` (or `bandit`), `ruff`, `pip-audit` or `npm audit`, `deptry`, `gh`. A missing tool skips its check. To fix findings: the [code quality](code-quality.md), [test and CI](test-and-ci.md) and [support agents](support-agents.md) modules. |

## What you get

- `harness/skills/repo-health/SKILL.md` — the flow: pick goals, collect, run checks, render, report with one decision card.
- `harness/skills/repo-health/references/` — the check catalogue and the review procedures.
- `harness/skills/repo-health/scripts/` — the collector (`health.py`), the renderer and the fix routes.
- `harness/skills/repo-health/assets/template.html` — the dashboard template.

Three behaviours worth knowing before the first run:

- **The secrets-history check is incremental.** `health.py scan-secrets --repo <repo>` runs `gitleaks` over the commits since the last scan only. The first run, or a run after a history rewrite, scans everything. It stops after `REPO_HEALTH_SECRETS_CAP_S` seconds (default 900) and records the check as `pending`, not as failed, so the rest of the run goes on. A leak fails the check and reports a count, never the secret. The old marker stays, so the failure repeats until you remove the secret from the history. The scan starts from a record this clone keeps inside `.git` (`repo-health-secrets-marker`), not from the committed `history.jsonl`, so a commit cannot move the start of the range; a fresh clone has no record and falls back to `history.jsonl` for its first run.
- **The verify-command check fails when the test command cannot start.** It runs the printed collect command (`<suite command> --collect-only -q`, 120 s limit). It passes when the command exits 0 and collects at least one test. A missing module or a missing file is a failure, with the first error line.
- **A scheduled or unattended run never ends its turn while a background command runs.** It polls in the same turn. When the command reaches its cap, the check stays `pending` with the reason and the run goes on to the dashboard. A pending check is a result; a missing history line is not.
- `harness/skills/repo-health/vendor/` — a pinned copy of Sentry's `gha-security-review` skill (Apache-2.0) for the GitHub Actions review.

## Install

```bash
mkdir -p ~/.claude/skills
cp -R harness/skills/repo-health ~/.claude/skills/
```

## Check it works

```bash
python3 ~/.claude/skills/repo-health/scripts/health.py --help
```

It lists the subcommands `collect`, `set`, `render` and `fix-routes`. Then
type `/repo-health` in a git repo.

## Remove

```bash
rm -r ~/.claude/skills/repo-health
```

In each repo you scanned, delete `.claude/health/` if you do not want its history.

## Cautions

- It writes `.claude/health/` (a scorecard, `HEALTH.html` and a history file)
  into the repo it scans.
- It runs your test suite as one check. That can take minutes.
- It fixes nothing until you say so. `--fix-blocking` sends fixer subagents to
  the blocking findings.
- Only you can start it (`disable-model-invocation`).
- `--deep` reads two reviewed third-party skills that this repo does not ship,
  because of their licence terms or the owner's choice. Without them, the deep
  pass cannot run. To use `--deep`, put each one in
  `~/.claude/skills/repo-health/vendor/<name>/` yourself:
  - `security-review` from
    [getsentry/skills](https://github.com/getsentry/skills/tree/24fdb833b9e67670a027e3b482189100a69ff7f9/skills/security-review)
    (its references derive from OWASP text under CC BY-SA 4.0).
  - `cloudflare-security-audit` from
    [cloudflare/security-audit-skill](https://github.com/cloudflare/security-audit-skill/tree/c1c8a8c1471069fb0e188eeaff69b8e8db6564a8/skills/security-audit)
    (MIT), for a repo that you mark as exposed. It also needs Node. Without
    it, the exposed-repo audit stops at its first check, with a message about
    the "run contract".

  When you add a copy, also add its row to `vendor.json` (repo, path, pin).
  If you do not, the `sec.vendor-pins` check refuses with exit 2, because the
  manifest no longer covers every folder in `vendor/`.
