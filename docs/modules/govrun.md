# Machine governor (`govrun`)

A wrapper that makes heavy jobs, such as test runs with many workers, take
turns. Two parallel test suites then cannot use all the memory of one laptop.

| | |
|---|---|
| **Status** | Optional · experimental — only half of the original system |
| **Platform** | macOS or Linux (uses `fcntl` record locks) |
| **Needs** | `python3` (standard library only) |

## What you get

- `harness/scripts/govrun` — a two-line shell wrapper that runs `govrun.py`.
- `harness/scripts/govrun.py` — the entry point.
- `harness/scripts/govrun_config.py`, `govrun_errors.py`, `govrun_locks.py`, `govrun_pytest_budget.py`, `govrun_pytest_ini.py`, `govrun_status.py` — required parts.

The hook that refuses a heavy command run *without* `govrun` is not in this
repo. So nothing forces you to use it; it acts only when you call it.

## Install

```bash
mkdir -p ~/.claude/scripts
cp harness/scripts/govrun harness/scripts/govrun.py \
   harness/scripts/govrun_{config,errors,locks,pytest_budget,pytest_ini,status}.py ~/.claude/scripts/
chmod +x ~/.claude/scripts/govrun
```

Use it in front of a heavy command:

```bash
~/.claude/scripts/govrun -- pytest -n auto
```

## Check it works

```bash
~/.claude/scripts/govrun --status
```

It prints the state folder, the slot and its holders, and
`enforcement: DISARMED (no hook file …)`. `DISARMED` is the expected result,
because the enforcement hook is not in this repo.

## Remove

```bash
rm ~/.claude/scripts/govrun ~/.claude/scripts/govrun*.py
rm -rf ~/.machine-governor
```

## Cautions

- One shared slot of 4 workers for the whole machine. Change it with
  `GOVRUN_SLOTS` (JSON) or `~/.machine-governor/config.json`. It refuses more
  than 4 in total.
- A second job waits. With `--class interactive` (the default) it waits 120
  seconds and then exits with code `75` ("queue timeout, not a test failure").
  With `--class ci` it waits with no limit.
- It refuses a pytest command that asks for more workers than the slot has
  (exit `2`).
- It keeps its locks in `~/.machine-governor` (or `GOVRUN_STATE_DIR`).
