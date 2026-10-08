# Ship-Tail CI Loop — dual-path watch-fix-repush (ST-03)

This is the algorithm the ship-tail STEP 5 runs after the PR STAGE pushes. It
watches CI to green and, on red, triggers `/ci-orchestrate` and re-pushes —
looping until green or a bounded ceiling. **It NEVER auto-merges.** Merge is a
human action, full stop.

## Contents

- [INVARIANTS (hard, non-negotiable)](#invariants-hard-non-negotiable)
- [STATE](#state)
- [STEP 5.0 — ATOMIC ENV-DETECT (run exactly once)](#step-50-atomic-env-detect-run-exactly-once)
- [STEP 5.1 — WATCH-FIX-REPUSH LOOP](#step-51-watch-fix-repush-loop)
- [STEP 5.2 — `await_verdict()` per path (identity-gated)](#step-52-awaitverdict-per-path-identity-gated)
- [STEP 5.3 — `re_push()` (guarded)](#step-53-repush-guarded)
- [Transient Retry (per stage)](#transient-retry-per-stage)
- [VERIFY (what the plan's "Verify" checks)](#verify-what-the-plans-verify-checks)


The loop runs in ONE of two paths, chosen once per run by **capability + run
identity**, never by mere reachability:

- **gh path** — when `api.github.com` is reachable AND `gh` can prove auth, repo
  access, check-run visibility, and the EXACT head SHA of the push.
- **runner-log path** — when the gh path cannot be proven (e.g. example-project's
  sandbox blocks `api.github.com`); parse the self-hosted runner logs.

If NEITHER path can prove run identity → **ESCALATE to the human.** Do not
re-push against an unverified or stale failure.

---

## INVARIANTS (hard, non-negotiable)

1. **Never auto-merge.** The loop ends at "green" or "escalate", never at "merged".
2. **Never `--no-verify`.** Re-pushes go through hooks like any other push.
3. **Guarded re-push only.** `git push --force-with-lease=<ref>:<EXPECTED_SHA>`
   with the SHA pinned. Plain `--force-with-lease` (no value) silently degrades
   to `--force` on URL/non-tracking pushes — ALWAYS pin `<ref>:<sha>`.
4. **Atomic env-detect.** Exactly one path per run, decided once, with mutual
   exclusion. A transient `api.github.com` blip must NOT let both paths run
   (split-brain → duplicate re-push).
5. **Bounded.** Hard cycle ceiling `CI_MAX_CYCLES` (default 3). On the ceiling,
   or on an identical recurring failure, ESCALATE — never burn runs forever.
6. **Identity before verdict.** A verdict is only actioned if the run it came
   from is proven to be THIS push (repo + head SHA + run/job correlation) and
   the log/check entry is AFTER the push timestamp.

---

## STATE

```
PUSH_SHA       = git rev-parse HEAD            # the SHA we just pushed
PUSH_REF       = refs/heads/<branch>
PUSH_EPOCH     = <unix time captured immediately BEFORE the push>
CI_PATH        = unset                          # "gh" | "runner-log" — set ONCE
CYCLE          = 0
LAST_FIX_EPOCH = 0                              # when we last applied a fix
LAST_FAIL_SET  = ""                             # sorted set of failing check names
```

---

## STEP 5.0 — ATOMIC ENV-DETECT (run exactly once)

Decide `CI_PATH` ONCE, before the watch loop. Retry the reachability+capability
probe ONCE before falling back, so a single transient blip does not flip paths.
Mutual exclusion: once `CI_PATH` is set, it is frozen for the whole run.

```bash
detect_ci_path() {
  # returns: "gh" | "runner-log" | "none"
  local attempt
  for attempt in 1 2; do            # retry-once-before-fallback
    if gh auth status >/dev/null 2>&1 \
       && curl -sS -m 5 -o /dev/null https://api.github.com 2>/dev/null \
       && gh repo view --json nameWithOwner >/dev/null 2>&1; then
      # CAPABILITY proven so far; identity is checked per-cycle in 5.2.
      echo "gh"; return 0
    fi
    sleep 2
  done
  # gh path unprovable twice → look for a usable self-hosted runner log dir.
  if ls -d "$HOME"/actions-runners/*/_diag 2>/dev/null | grep -q .; then
    echo "runner-log"; return 0
  fi
  echo "none"
}

CI_PATH="$(detect_ci_path)"
echo "[ship-tail][ci-loop] CI_PATH=${CI_PATH} push_sha=${PUSH_SHA} push_ref=${PUSH_REF}"
```

- The `[ship-tail][ci-loop] CI_PATH=...` line is the **deterministic env-detect
  log line**. Verify greps `count>0` for it; the value must read `gh` in a
  gh-reachable run and `runner-log` in a gh-blocked run.
- If `CI_PATH=none` → **ESCALATE**: print
  `[ship-tail][ci-loop] ESCALATE reason=no-provable-path` and STOP. The PR is
  pushed; the human watches CI manually.

---

## STEP 5.1 — WATCH-FIX-REPUSH LOOP

```
while CYCLE < CI_MAX_CYCLES:
    CYCLE += 1
    emit  [ship-tail][ci-loop] cycle=$CYCLE path=$CI_PATH sha=$PUSH_SHA   # deterministic per-cycle line
    verdict, fail_set = await_verdict()           # 5.2 (path-specific, identity-gated)

    if verdict == GREEN:
        emit [ship-tail][ci-loop] GREEN cycle=$CYCLE — CI passed. STOP (no merge).
        STOP — success. (NEVER merge.)

    if verdict in {STALE, WRONG_RUN, UNKNOWN, UNREACHABLE}:
        emit [ship-tail][ci-loop] ESCALATE reason=$verdict cycle=$CYCLE
        STOP — escalate. Do NOT re-push against an unproven/stale failure.

    # verdict == RED, identity proven, entry after PUSH_EPOCH.
    # ---- identical-error short-circuit (race-proof de-dup) ----------------
    if fail_set == LAST_FAIL_SET and not fix_is_stale(fail_set):
        emit [ship-tail][ci-loop] ESCALATE reason=identical-failure-recurred cycle=$CYCLE set="$fail_set"
        STOP — escalate. The same failure survived a fix; do not re-fix.

    LAST_FAIL_SET = fail_set
    /ci-orchestrate --fix-all                       # delegate the fix
    LAST_FIX_EPOCH = now()
    re_push()                                        # 5.3 guarded
    PUSH_SHA = git rev-parse HEAD                    # new head after fix commit(s)
    PUSH_EPOCH = <time just before re_push>

# fell out of the loop → ceiling hit
emit [ship-tail][ci-loop] ESCALATE reason=cycle-ceiling cycles=$CI_MAX_CYCLES
STOP — escalate.
```

### Race-proof de-dup (`fix_is_stale`)

The "already-fixed" memory must be CLEARED when a failing check's `completedAt`
(gh path) or the log's job-finish time (runner-log path) is **newer than
`LAST_FIX_EPOCH`**. A fast CI re-run that completes after our fix is a *fresh*
failure, not the same one — so it is NOT short-circuited; we may fix it once more
(still bounded by `CI_MAX_CYCLES`).

```
fix_is_stale(fail_set):  # returns True if this failure post-dates our last fix
    return verdict_completed_epoch(fail_set) > LAST_FIX_EPOCH
```

So the short-circuit fires only when the SAME set recurs from a run that did NOT
post-date our fix (i.e. we fixed, it came back unchanged from a run no newer than
the fix) — the genuine "unfixable, stop burning runs" case.

---

## STEP 5.2 — `await_verdict()` per path (identity-gated)

### gh path
1. `gh run list --branch <branch> --json headSha,databaseId,status,conclusion,workflowName`
   → select runs whose `headSha == PUSH_SHA` (identity). If none → `UNKNOWN`.
2. `gh run watch <id> --exit-status` to block to completion (per-stage retry on
   transient 5xx/529 — see Transient Retry).
3. Read per-check `conclusion` + `completedAt`. Only entries with
   `completedAt >= PUSH_EPOCH` count. Build `fail_set` = sorted failing check
   names. GREEN iff the required check (`ci-ok` for example-project; the repo's required
   gate generally) concluded `success`.
4. If `gh` starts failing auth/visibility mid-run → do NOT silently switch to the
   runner-log path (that violates atomic env-detect). Treat as `UNREACHABLE` →
   ESCALATE.

### runner-log path
Parse `~/actions-runners/*/_diag/Worker_*.log` (see `sanitize_runner_log.sh` for
the field shapes). Correlation REQUIRED before trusting any verdict:
1. **repo**: the `"k": "repository"` value must equal the repo we pushed.
2. **head SHA**: the `"k": "head_sha"` / `"k": "sha"` value must equal `PUSH_SHA`.
3. **run/job**: capture `run_id` + `jobId` + `jobDisplayName`.
4. **freshness**: only consider log files whose mtime is `>= PUSH_EPOCH`
   (`find ~/actions-runners/*/_diag -name 'Worker_*.log' -newermt "@$PUSH_EPOCH"`).
5. **verdict**: the authoritative line is
   `Job result after all job steps finish: Succeeded|Failed` (LAST occurrence).
   Do NOT trust per-step `"result": "succeeded"` JSON — a job can have green post
   steps yet `Failed` overall. `ci-ok` is the required gate; performance is
   advisory.
6. If correlation cannot be satisfied (no log matches repo+SHA after push) →
   `WRONG_RUN`/`UNKNOWN` → ESCALATE. Never re-push against a stale/foreign log.

The parser logic and these gates are exercised by `test_runner_log_path.sh`
against REAL captured fixtures (`fixtures/Worker_succeeded.log`,
`fixtures/Worker_failed.log`).

---

## STEP 5.3 — `re_push()` (guarded)

```bash
re_push() {
  # PUSH_SHA here = the remote SHA we EXPECT (what we last pushed). The lease
  # protects against a concurrent push having advanced the branch under us.
  git push --force-with-lease="${PUSH_REF}:${EXPECTED_REMOTE_SHA}" origin "$BRANCH"
}
```

- `EXPECTED_REMOTE_SHA` = the SHA we last pushed (the lease ref). If someone else
  advanced the branch, the lease FAILS the push (good — surfaces the race) rather
  than clobbering. NEVER pass a bare `--force-with-lease` and NEVER `--force`.
- After a `/ci-orchestrate` fix, the fixers commit through hooks; the local HEAD
  advances; we re-push that. We do a normal (non-force) push when we are simply
  ahead; the lease form is used when the fix amended/rebased history.

---

## Transient Retry (per stage)

A `529`/`5xx`/transient network error on a single probe (`gh run watch`, a curl,
a log read) must NOT fail the whole run. Wrap each external probe in a small
bounded retry (3 attempts, exponential backoff 2s/4s/8s). Only after the retries
are exhausted does the stage report `UNREACHABLE` → ESCALATE.

---

## VERIFY (what the plan's "Verify" checks)

1. **Forced failure → fix → re-push**: a forced CI red is DETECTED, triggers
   `/ci-orchestrate`, and produces a guarded re-push. (`test_runner_log_path.sh`
   T1 proves the failed fixture parses to `FAILED`, the trigger condition.)
2. **Env-detect log line shows the correct path in BOTH worlds**:
   `grep -c '\[ship-tail\]\[ci-loop\] CI_PATH=gh'` > 0 in a gh-reachable run;
   `grep -c '\[ship-tail\]\[ci-loop\] CI_PATH=runner-log'` > 0 in a gh-blocked
   run. `count==0` means the loop is a no-op.
3. **Per-cycle log line emitted**: `grep -c '\[ship-tail\]\[ci-loop\] cycle='` > 0.
4. **Parser correctness on REAL logs + fixture hygiene**: run
   `references/ship-tail/test_runner_log_path.sh` → all assertions pass
   (verdict extraction Succeeded/Failed, identity+freshness gates, sanitizer
   catches every poisoned redaction rule).
