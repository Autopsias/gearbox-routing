# rust-migration-review

Read-only skill that decides whether a repository, or parts of it, should move to Rust. It
names which code units to port first, which to keep, and which cheaper alternatives win, and
it scores benefit, feasibility, risk and effort (engineer-week ranges with numbered
assumptions). It checks its verdicts against current external case studies through Exa.

## Contents

- `SKILL.md` — the 8-step workflow: inventory, goal, measurement, candidates, live
  research, scoring, optional spike, report with a decision card.
- `references/assessment-rubric.md` — scales, evidence tiers (`E0`–`E3`), effort model,
  verdict rules, whole-repository rewrite test, cheaper alternatives, integration patterns
  and parity strategies.
- `references/evidence.md` — durable lessons and dated numbers from published migrations
  and counter-examples, with sources, plus live-research query templates.
- `scripts/rust_inventory.py` — stdlib-only scanner: lines per language and code unit,
  test lines, git churn, existing Rust, runtime dependencies, and `for`/`against` signals
  with `path:line` examples. It never runs target-repository code.
- `scripts/test_rust_inventory.py` — unit tests for the scanner.

## Usage

`install.sh` installs this skill into `<home>/skills/rust-migration-review/` for Claude Code,
OpenAI Codex CLI and Google Antigravity.

Run the scanner on its own against any repository:

```bash
python3 claude/skills/rust-migration-review/scripts/rust_inventory.py /path/to/repo --format markdown
python3 claude/skills/rust-migration-review/scripts/rust_inventory.py /path/to/repo --format json --depth 3
```
