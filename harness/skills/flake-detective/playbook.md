# Diagnostic Playbook — 6 steps

Each step has: **what you're testing**, **command(s)**, **how to read the output**, **what to do if inconclusive**. All commands assume `$PYTEST_CMD` resolved per SKILL.md.

**Checklist** (Step 0 triage + 6 reproduction steps — check off as you go):
- [ ] Step 0 — Triage existing log evidence (or explicitly skipped)
- [ ] Step 1 — Worker count differential
- [ ] Step 2 — Worker correlation
- [ ] Step 3 — Distribution mode
- [ ] Step 4 — Fixture scope audit
- [ ] Step 5 — Environment simulation
- [ ] Step 6 — Calibrated reruns

## Preflight (run once)

Before any step, capture environment:

```bash
$PYTEST_CMD --version
$PYTEST_CMD --trace-config        # which conftest.py files load, in order
$PYTEST_CMD --help | grep -A2 xdist  # is xdist installed? what version?
```

Capture `addopts` from `pyproject.toml` (`[tool.pytest.ini_options]`) / `pytest.ini` / `setup.cfg`. Note any `-n auto`, `--dist`, `--reruns`, `addopts = ...` already configured — these affect how the suite runs even when you don't pass flags.

**Always force parser-friendly flags** when output will be parsed:
```
--color=no -vv -rA --tb=short --junitxml=/tmp/flake-detective/run.xml
```
Prefer junitxml for any worker-correlation or pass/fail extraction. Treat grep/awk on stdout as best-effort and emit a `parser-confidence: low` flag if junitxml was unavailable.

---

## Step 0 — Triage existing log evidence

**What you're testing.** Whether log evidence already in hand reveals the class so reproduction (steps 1–6) can be skipped or targeted. Cheap (seconds) compared to a 22-rerun calibration.

**Inputs — use the first available, in priority order:**

1. **Log already in conversation context** — user pasted it; just read it.
2. **Local test-output log** — `pytest --log-file=<path>` output, terminal scrollback, the user's last `pytest -v` run, or a `pytest --last-failed` re-execution.
3. **GitHub Actions** —
   ```bash
   gh run list --branch <branch> --limit 5            # find recent runs
   gh run view <run-id> --log-failed                   # only failed-job log lines
   gh run view <run-id> --log-failed | grep -E '(FAILED|ERROR|Traceback|asyncio|Errno)' -A2
   gh run download <run-id>                            # junitxml/screenshots/artifacts if uploaded
   ```
   If a URL is pasted (`https://github.com/owner/repo/actions/runs/12345`), the run-id is the trailing path segment.
4. **Other CI vendors** — GitLab `glab ci view`, Buildkite/Jenkins web UI export. Same signal extraction; fetch is vendor-specific.
5. **Nothing available** — skip Step 0; start at Step 1 with reproduction. Note "triage skipped" in the hand-off envelope.

**What to extract from the log:**

1. Failing test node id(s).
2. Exception class + first line of the message (top of traceback).
3. Worker assignment if visible — `grep -oE '\[gw[0-9]+\]'`.
4. Last non-test frame in the traceback — names the production code that died.
5. Fixture-chain references — "during fixture setup of X", "in fixture Y" etc.
6. Timing — duration line, or `asyncio.TimeoutError after Ns`.
7. Process-level signals — exit code 137 (OOM), 139 (SIGSEGV), `killed`, `container exited`.
8. Sibling-test ordering — which test ran immediately before the failing one in the same worker.

**How to read it.** Match extracted strings against the **signal → class quick reference table** in `taxonomy.md`. A strong match yields a primary class candidate at **medium confidence** — one log is a hint, not proof. Reproduction in step 6 is what confirms.

**Decision rule (output of Step 0):**

- **Strong class hint** (e.g., `Event loop is closed` → class 6; `Address already in use` → class 2; `UNIQUE constraint` → class 1). **Skip steps 1–3** — when the error type already names the class, worker-count differential / correlation / dist-mode experiments add little. **Jump to step 4** (audit the area implicated by the class — fixture chain for 1/4, resource names for 2, async fixtures for 6) **and step 6** (calibrated reruns to confirm the fix held).
- **Weak / ambiguous class hint** but the failure is genuinely intermittent → run the full playbook from step 1.
- **Single failure with no rerun signal** → not yet a confirmed flake; defer to `/diagnose` per the SKILL.md triage gate.
- **No log available** → skip Step 0; start at step 1.

**Output:** populate the hand-off envelope's `classification.primary` (with confidence) and any `reproduction_matrix_results` already implied by the log (e.g., if log shows `gw0` always failing, set `worker_count: fail/correlated`).

**Inconclusive.** If the log shows only a generic `AssertionError` with no class-discriminating context, do not force a class assignment from the table — proceed to step 1. Premature labelling here corrupts the hand-off envelope.

---

## Step 1 — Worker count differential

**What you're testing.** Whether parallelism itself triggers the failure.

**Commands.**
```bash
$PYTEST_CMD <node> -n0 --color=no -vv --junitxml=/tmp/flake-detective/n0.xml
$PYTEST_CMD <node> -n auto --color=no -vv --junitxml=/tmp/flake-detective/nauto.xml
```

**Reading the output.** Compare exit codes AND timing. `-n0` runs xdist with 0 distributed workers (still goes through xdist's collection); plain non-xdist execution is `$PYTEST_CMD <node>` with no `-n` at all. Document the comparison being made — they are not identical.

- `-n0` PASS, `-n auto` FAIL → parallelism is the trigger; proceed to step 2.
- Both PASS → not reproducible alone; flake rate may be low. Go to step 6 with a higher N.
- Both FAIL → not a flake; hand off to `/diagnose`.
- `-n0` SLOW, `-n auto` FAST but FAIL → races are masked by single-process serialization; proceed.

**Inconclusive.** If the run itself is fast and flake rate <10%, you may need 5+ runs of each to see the differential. Loop:
```bash
for i in 1 2 3 4 5; do $PYTEST_CMD <node> -n auto --junitxml=/tmp/flake-detective/auto-$i.xml; done
```

---

## Step 2 — Worker correlation

**What you're testing.** Whether the failure tracks a specific worker (or always the first/last) — fingerprint of class 1 (fixture scope leakage) and class 2 (shared resource).

**Commands (preferred — junitxml).**
```bash
$PYTEST_CMD <node> -n auto --junitxml=/tmp/flake-detective/wc.xml --color=no -vv
xmllint --xpath '//testcase' /tmp/flake-detective/wc.xml | grep -oP '(?<=name=")[^"]+|(?<=classname=")[^"]+'
```
Worker assignment is in the junitxml `<testcase>` `name`/`classname` attributes when xdist is active (`gw0`, `gw1`, …).

**Commands (fallback — grep, parser-confidence: low).**
```bash
$PYTEST_CMD <node> -n auto --color=no -vv 2>&1 | tee /tmp/flake-detective/run.log
grep -E 'PASSED|FAILED' /tmp/flake-detective/run.log
```
Each line typically begins with `[gw0]` / `[gw1]` and ends with the node id. **Do NOT use `awk '{print $NF}'`** — `$NF` is the node id, not the worker. Use `grep -oE '\[gw[0-9]+\]'` against the line, then pair with the trailing PASS/FAIL.

**Reading the output.**
- Failures cluster on a single worker (e.g., always `gw0`) → fixture scope leakage (class 1) — that worker initialized the shared fixture first and stamped state others now collide with.
- Failures distribute evenly across workers → shared resource contention (class 2) — every worker hits the same hardcoded port/path.
- Failures correlate with a specific NEIGHBOR test in the same worker → order dependency (class 3).

**Inconclusive.** Run with `-n 4` explicitly (deterministic worker count) instead of `-n auto`. Repeat 3–5 times to build a worker-failure histogram.

---

## Step 3 — Distribution mode

**What you're testing.** Whether the failure depends on which tests share a worker.

**Commands.** First verify xdist supports `loadfile`/`loadgroup` (preflight):
```bash
$PYTEST_CMD <node> --dist loadfile -n auto --junitxml=/tmp/flake-detective/loadfile.xml
$PYTEST_CMD <node> --dist loadgroup -n auto --junitxml=/tmp/flake-detective/loadgroup.xml
```

`loadfile` keeps tests within a file on the same worker (preserves intra-file order). `loadgroup` groups by `xdist_group` marker.

**Reading the output.**
- `--dist loadfile` PASS → the issue is across-file interaction. This is class 1 (session fixture across files) or class 4 (broad monkeypatch).
- `--dist loadgroup` PASS for tests sharing a custom group name → class 2 (shared resource); the group serializes access. Strongest discriminator vs class 1.
- Both still FAIL → order dependency within file (class 3) or async/loop issue (class 6).

**Inconclusive.** If your tests aren't yet marked with `xdist_group`, you can't test loadgroup directly. Add a temporary marker and try again — that experiment IS the diagnostic.

---

## Step 4 — Fixture scope audit

**What you're testing.** Which fixtures in the closest-conftest chain mutate shared state.

**Commands.** Walk the conftest chain — never assume `tests/conftest.py`:
```bash
find . -name conftest.py -not -path '*/node_modules/*' -not -path '*/.venv/*'
$PYTEST_CMD --collect-only -q <node>      # shows the node's collection path
$PYTEST_CMD --setup-show <node>           # fixture trace at the node
```
The collection output reveals which conftest.py files apply (pytest walks UP from the test file). Map every fixture in those files; flag any with `scope="session"` or `scope="module"` that:

- Inserts/updates DB rows
- Writes files outside `tmp_path_factory`
- Mutates `os.environ`
- Modifies `sys.modules`, `sys.path`, or any class attribute
- Calls `monkeypatch.setattr` (which inherits the fixture's scope)
- Starts a long-running task / process / thread

**Reading the output.** Each flagged fixture is a class 1 / class 4 candidate. Cross-reference with worker-correlation evidence from step 2 — the fixture that runs FIRST on the suspect worker is the most likely culprit.

**Inconclusive.** Add a `print(f"[FIXTURE] {worker_id} setup")` at the top of suspect fixtures (use a tagged debug prefix per `/diagnose`). Re-run and watch the order; the fixture that prints multiple times across workers is shared-state-leaking.

---

## Step 5 — Environment simulation

**What you're testing.** Whether CI hardware constraints reproduce the failure locally.

**Preflight before mandating Docker.**
```bash
docker info 2>&1 | head -3   # is the daemon running and accessible?
id -nG | grep -q docker || echo "DOCKER_NEEDS_SUDO"
```
If docker is unavailable, fall back BEFORE failing the step.

**Commands (Docker — preferred when available).**
```bash
docker run --rm --cpus=2 --memory=4g \
  -v "$(pwd)":/app -w /app \
  python:3.11 \
  bash -c "$PYTEST_CMD <node> -n auto"
```
Caveats: image pull on first run; UID/GID mount mismatches on Linux can break write paths; Docker Desktop on macOS has its own resource limits that may cap the constraint.

**Fallback 1 — local CPU throttling (Linux).**
```bash
taskset -c 0,1 $PYTEST_CMD <node> -n auto    # pin to 2 cores
# OR
cpulimit -l 50 -- $PYTEST_CMD <node> -n auto # 50% CPU cap
```

**Fallback 2 — CI reproduction.**
Push the branch with a temporary commit that runs the failing node 5 times in a CI step:
```yaml
- run: for i in 1 2 3 4 5; do $PYTEST_CMD <node> -n auto; done
```

**Reading the output.** If failure rate jumps from <1% locally to >10% under throttling → class 5 (CI environment drift). The mechanism is widening race windows enough for the underlying class 1/2/3/6 issue to surface — so this step rarely terminates the investigation; it points at WHICH other class is real.

**Inconclusive.** Do not mandate Docker if the preflight fails. Document which fallback you used and proceed; if NO environment differential is reproducible, class 5 is unlikely to be primary.

---

## Step 6 — Calibrated reruns

**What you're testing.** Statistical confirmation that a fix actually works (validation gate) — NOT initial flake confirmation (that was the evidence gate).

**Commands.**
```bash
scripts/flake-rerun.sh -- <node> 22       # 22 runs ≈ 95% confidence to detect 10%-rate flake
# or for higher confidence:
scripts/flake-rerun.sh -- <node> 59       # 59 runs ≈ 95% confidence to detect 5%-rate flake
```
The helper runs each invocation as a separate `$PYTEST_CMD` process (process-level reruns). This is the right semantics for confirming a flake is gone — a fresh process is what CI runs.

**Note on `pytest-repeat`.** `$PYTEST_CMD <node> --count=N` repeats within a single pytest session (intra-session). NOT equivalent — fixtures and modules are reused. Useful for investigating intra-session order/timing issues but NOT for replacing process-level reruns.

**Reading the output.** The helper emits a verdict from the taxonomy in `scripts/flake-rerun.sh`:
- `flaky` → at least one PASS and one FAIL across runs (you reproduced).
- `deterministic-pass` → all runs PASS (fix held, OR rate is below the gate's detection threshold; check the reported confidence interval).
- `deterministic-fail` → all runs FAIL (not a flake; hand off to `/diagnose`).
- `infrastructure-error` / `usage-error` / `no-tests-collected` / `collection-error` → script issue, fix and rerun.
- `inconclusive` → too many infra errors; fewer than `max(3, ceil(N/3))` valid runs.

The output ALSO includes a 95% Wilson confidence interval for the observed pass-rate and the minimum-detectable-flake-rate at this N (`1 − 0.95^(1/N)`), so you can judge what the result actually proves.

**Inconclusive.** Increase N and re-run. If wall-clock budget is exceeded before achieving target confidence, document the achieved confidence and the rate at which a flake is still possible.

---

Closing reminders: **record duration variance, not just pass/fail.** Increasing variance across runs is itself a signal — usually class 6 (async cleanup hanging on a dying loop) or class 5 (CI scheduler jitter). **Always emit the hand-off envelope** (per SKILL.md) before passing to another skill so the rerun work is not redone.

Trigger to revisit: when pytest-xdist majors past v3, when `--dist loadgroup` semantics change, or when pytest stdout format introduces a fourth column that breaks junitxml fallback parsers.
