---
name: flake-detective
description: Statistically confirms then root-causes flaky pytest tests under xdist parallelism and CI drift. Routes to 6 root-cause classes (fixture leakage, resource contention, test-order dependency, import-time leakage, CI drift, asyncio concurrency). Use when: flaky test, intermittent failure, passes locally fails CI, xdist race.
---

# Flake Detective

Investigate non-determinism before proposing a fix. Confirm flakiness statistically, classify into root-cause class(es), then apply a matched fix pattern. Installed at `~/.claude/skills/flake-detective/`; portable across any pytest project.

## 30-second triage gate

- **Single failure, no rerun signal** → defer to `/diagnose` (deterministic loop). Stop.
- **Confirmed intermittent** (CI history shows pass/fail mix on same SHA, or user says "rerun made it pass") → proceed.
- **Test never passes** → not a flake; defer to `/diagnose` or `/test-orchestrate`.

## Project detection + `PYTEST_CMD` resolution

Resolve once per session, log the chosen command before any test invocation:

1. `FLAKE_PYTEST_CMD` env var (explicit override).
2. Lockfile detection: `uv.lock` → `uv run pytest`; `poetry.lock` → `poetry run pytest`; `pdm.lock` → `pdm run pytest`; `hatch.toml` → `hatch run test`; `tox.ini` → `tox -e py`; `noxfile.py` → `nox -s tests`.
3. `python -m pytest` (venv but no lockfile).
4. Bare `pytest` (last resort).

Preflight once: `$PYTEST_CMD --version`, `$PYTEST_CMD --trace-config`, capture `addopts` from `pyproject.toml`/`pytest.ini`/`setup.cfg`. Detect xdist availability/version. Discover project conventions: `<repo>/.claude/rules/*.md`, conftest chain via `find . -name conftest.py` plus `$PYTEST_CMD --collect-only -q <node>` (closest-conftest, never assume `tests/conftest.py`), `<repo>/scripts/check-*.{sh,py}` validators, and any wait helper / mock factory / settings-patch fixture. **If found, prefer them in fix recommendations.** If absent, library defaults.

## Three calibrated gates

**Evidence gate.** Accepts CI history (parsed failure log + run count) OR local sample. Reports observed flake rate with 95% confidence interval (Wilson score). 0/N = "no evidence at confidence 1−0.95^(1/N); request CI log if rate <(1/N) suspected." Never blocks on absence-of-evidence alone.

**Reproduction matrix.** The 6-step playbook below. Vary ONE dimension at a time (worker count, dist mode, order, fixture scope, environment, timing). Step 6 is dimension-isolation, NOT a duplicate of the evidence gate.

**Validation gate.** Computes target N from minimum-detectable-flake-rate at 95% confidence (5% → N≈59, 10% → N≈22), capped by per-test wall-clock budget (default 5 min). Reports achieved confidence rather than enforcing fixed count. Loadgroup leg only if xdist is installed AND grouping is part of diagnosis or fix.

## Diagnostic playbook (Step 0 triage + 6 reproduction steps)

Full procedure: [playbook.md](playbook.md). Summary:

0. **Triage existing log** — pasted log / `gh run view --log-failed` / local `pytest --log-file=` output. Match against the signal→class table in [taxonomy.md](taxonomy.md). Strong hint → skip 1–3, jump to 4 + 6. No log → start at 1.
1. `$PYTEST_CMD <node> -n0` vs `$PYTEST_CMD <node> -n auto` differential.
2. Worker-assignment correlation (junitxml preferred; grep fallback emits parser-confidence flag).
3. `$PYTEST_CMD <node> --dist loadfile -n auto` (order-dependence-within-file).
4. Fixture scope audit (closest-conftest chain).
5. Docker `--cpus=2 --memory=4g` simulation **with preflight + fallback** to local throttling (`taskset`/`cpulimit`) or CI reproduction.
6. Calibrated reruns via `scripts/flake-rerun.sh -- <node> [N]` (process-level, separate pytest invocations).

## Signal → class → pattern (multi-label routing)

Required discriminators before assigning a primary class: worker-correlation evidence, order-sensitivity evidence, resource-namespace-collision evidence, fixture-scope audit findings, environment delta, import/global-mutation evidence.

Routing returns a **primary class** + matched fix pattern, plus zero-or-more **contributing classes** with composition notes. Six classes and top hybrid recipes: [taxonomy.md](taxonomy.md). Six fix patterns + composition rules + 8 anti-patterns: [fix-patterns.md](fix-patterns.md).

## Hand-off envelope

Whenever this skill hands off to another (e.g. `/diagnose`, `/test-orchestrate`, `/ci-orchestrate`, `/improve-codebase-architecture`), emit this structured block into conversation context so the next skill does not redo the rerun work:

```
[flake-detective hand-off]
classification:
  primary: <class-name> (confidence: <high|medium|low>)
  contributing: [<class-name>, ...]
evidence_gate_stats:
  N: <int>
  pass_rate: <float>
  confidence_interval: [<lo>, <hi>]
reproduction_matrix_results:
  worker_count: <pass|fail|inconclusive>
  dist_mode: <pass|fail|inconclusive>
  order: <pass|fail|inconclusive>
  fixture_scope: <findings>
  environment: <findings>
  timing: <findings>
project_detection:
  pytest_cmd: <resolved cmd>
  conftest_chain: [<path>, ...]
  helpers_found: [<helper-name>, ...]
recommended_skill: <name-or-none>
```

## Anti-patterns (must-avoid)

- No `time.sleep` increases — they hide the race, never fix it.
- No `--no-verify` / hook bypass to ship a "flaky-fix" branch.
- No quarantine / `pytest.skip` without local repro proving the class.
- No blanket `--reruns N` without naming the genuine cold-start cause.

Full eight: [fix-patterns.md](fix-patterns.md).

## When to hand off

- `/diagnose` — deterministic, 1.0 fail-rate over N≥10 → not a flake.
- `/test-orchestrate` — ≥3 unrelated failures, mixed flake/deterministic.
- `/ci-orchestrate` — pipeline-stage triage (lint/type/build also failing).
- `/improve-codebase-architecture` — architectural test-seam issue (no isolatable seam).

## Lifecycle

User-level skill at `~/.claude/skills/flake-detective/`. Single-user; promote to a Claude Code plugin if multi-user adoption needed (out of scope for v1). Update path: `git`-tracked dotfiles or manual copy. Each ref file ends with a `Trigger to revisit:` line. On declaring a fix, append one line to `<repo>/.claude/flake-detective.log` if `<repo>/.claude/` exists: `<ISO-timestamp> <node_id> <primary-class> <flake-rate-before>→<flake-rate-after> <verdict>`. No telemetry leaves the host.
