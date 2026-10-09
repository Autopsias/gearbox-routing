# Flake detective (`/flake-detective`)

Proves that a pytest test is flaky with a measured failure rate, finds which
of six causes it is, and suggests a matching fix.

| | |
|---|---|
| **Status** | Optional · stable |
| **Platform** | Pytest projects. Docker is optional (CPU-limit simulation). |
| **Needs** | `pytest`, and `pytest-xdist` for the parallel steps |

## What you get

- `harness/skills/flake-detective/SKILL.md` — the triage gate and the hand-off.
- `playbook.md`, `taxonomy.md`, `fix-patterns.md` — the procedure, the six causes and the fixes.
- `scripts/flake-rerun.sh` — runs one test N times as separate pytest processes and gives a verdict.

## Install

```bash
mkdir -p ~/.claude/skills
cp -R harness/skills/flake-detective ~/.claude/skills/
chmod +x ~/.claude/skills/flake-detective/scripts/flake-rerun.sh
```

## Check it works

```bash
bash ~/.claude/skills/flake-detective/scripts/flake-rerun.sh --help
```

Then say "this test passes locally and fails in CI".

## Remove

```bash
rm -r ~/.claude/skills/flake-detective
```

## Cautions

- It runs your tests many times: by default up to 5 minutes per test.
- A suggested fix can change fixtures or test order. Review it before you accept.
