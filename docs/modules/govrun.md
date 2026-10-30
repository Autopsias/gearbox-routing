# Machine governor (`govrun`)

Makes heavy jobs, such as test runs with many workers, take turns, so that two
parallel test suites cannot use all the memory of one laptop. A wrapper,
`govrun`, queues the jobs. An optional hook refuses a heavy command that
Claude runs *without* the wrapper.

| | |
|---|---|
| **Status** | Optional · experimental |
| **Platform** | macOS or Linux (uses `fcntl` record locks) |
| **Needs** | `python3` (standard library only) |

## What you get

- `harness/scripts/govrun` — a two-line shell wrapper that runs `govrun.py`.
- `harness/scripts/govrun.py` — the entry point.
- `harness/scripts/govrun_config.py`, `govrun_errors.py`, `govrun_locks.py`, `govrun_pytest_budget.py`, `govrun_pytest_ini.py`, `govrun_python_m.py`, `govrun_status.py`, `govrun_cores.py`, `govrun_queue.py` — required parts.
- `harness/hooks/governor-hook.py` and `harness/hooks/governor_parse.py` — the optional enforcement hook and the command parser it needs. Copy both, or neither.

## Install

```bash
mkdir -p ~/.claude/scripts
cp harness/scripts/govrun harness/scripts/govrun.py \
   harness/scripts/govrun_{config,errors,locks,pytest_budget,pytest_ini,python_m,status,cores,queue}.py ~/.claude/scripts/
chmod +x ~/.claude/scripts/govrun
```

Optional — the enforcement hook (add `--with-optional`). `./install-module govrun` adds this to `~/.claude/settings.json` for you. By hand: add each entry to the matching list under `"hooks"`, and keep your own entries.

```bash optional
mkdir -p ~/.claude/hooks
cp harness/hooks/governor-hook.py harness/hooks/governor_parse.py ~/.claude/hooks/
```

```json optional
{
  "hooks": {
    "PreToolUse": [
      {"matcher": "Bash", "hooks": [{"type": "command", "command": "p=\"$HOME/.claude/hooks/governor-hook.py\"; [ -f \"$p\" ] || exit 0; in=$(cat); printf '%s' \"$in\" | LC_ALL=C tr -d '\\042\\047\\134' | LC_ALL=C grep -qiF -e pytest -e py.test -e docker || exit 0; printf '%s' \"$in\" | python3 \"$p\"; s=$?; [ $s -eq 0 ] || { echo \"governor-hook: present but exited $s without rendering a decision - refusing this Bash call. Fix or remove $p.\" >&2; exit 2; }", "timeout": 15, "onFailure": "block"}]}
    ]
  }
}
```

Then put the wrapper in front of a heavy command: `~/.claude/scripts/govrun -- pytest -n auto`.

## Check it works

```bash
~/.claude/scripts/govrun --status
```

It prints the state folder, the slot and its holders, and the enforcement
state: `DISARMED` with the wrapper only, `ARMED` once the hook is installed and
wired. With the hook armed, Claude cannot run a parallel pytest without
`govrun`; the same command run through `govrun` is allowed.

## Remove

Run `./install-module --remove govrun` to take the `PreToolUse` entry out of `settings.json`. Then:

```bash
rm ~/.claude/scripts/govrun ~/.claude/scripts/govrun*.py
rm -f ~/.claude/hooks/governor-hook.py ~/.claude/hooks/governor_parse.py
rm -rf ~/.machine-governor
```

## Cautions

- **Copy `governor_parse.py` with the hook.** Without it the hook cannot tell a
  heavy command from a harmless one, so it refuses every Bash command. To run
  one command anyway, start it with `GOVRUN_BYPASS=1`.
- **The hook reads the whole command text.** A command that only *mentions* a
  parallel pytest run, for example inside a heredoc that writes a file, is
  refused too. Write such text with an editor tool instead.
- One shared slot of 4 workers for the whole machine. Change it with
  `GOVRUN_SLOTS` (JSON) or `~/.machine-governor/config.json`. It refuses more
  than 4 in total.
- Every heavy job also takes CPU "core tokens" from one machine-wide budget
  (default 10; set `GOVRUN_CORE_BUDGET`). The tokens are accounting only: a job
  must size its own threads from `GOVRUN_CORES`.
- Waiting jobs start in the order they arrived.
- A second job waits. With `--class interactive` (the default) it waits 120
  seconds and then exits with code `75` ("queue timeout, not a test failure").
  With `--class ci` it waits with no limit.
- It refuses a pytest command that asks for more workers than the slot has
  (exit `2`).
- It keeps its locks in `~/.machine-governor` (or `GOVRUN_STATE_DIR`).
