# Failure modes & recovery + Token budget

Read this when something in a `/plan-harden` run has actually gone wrong (a fork
failed, a phase hard-errored, the user Ctrl-C'd) and you need to know the documented
recovery behavior — or when you need the rough token-budget numbers for planning. The
happy path never needs this file.

## Failure modes & recovery

| Failure | Behavior |
|---|---|
| Workflow path: a fork's schema validation fails after retries, or its agent dies | That source is `{"status": "error", "errors": ["fork failed"]}`. Proceed with available results. |
| Workflow path: the whole Workflow call hard-errors | Fall back to the Agent-fork path for the enabled forks; if that also fails, Phase 0 marked ⊘. |
| Agent-fork path: a fork lags far behind its peers (~2-3 min) | Proceed without it; record `{"status": "error", "errors": ["timeout"]}`. |
| Agent-fork path: fork returns unparseable JSON | Treat as error. Empty findings for that source. Logged in summary. |
| All Phase 0 forks error/skip | Phase 0 marked ⊘. Phase 1 runs without enrichment print block. Continue. |
| `/adversarial-review` errors or both reviewers fail | Phase 2 ⊘. Skip `[HARDENED:...]` expectation. Continue to Phase 3 + 4. User can manually re-run later. |
| `/adversarial-review` runs >5min | Do NOT kill (would corrupt /tmp artifacts and the verify loop). Wait up to 15min (the verify loop adds up to 3 Codex rounds — see §2.2). Warn user, then proceed. |
| User Ctrl-C mid-run | Write a `## /plan-harden Status` section listing completed phases. Plan may have partial `[HARDENED:...]` tags applied. User can resume with `--from-phase N`. |
| `PLAN_FILE` deleted mid-run | Re-read fails in Phase 4 → error out cleanly. Print phase outputs to conversation as fallback. |
| Plan structure changed between Phase 2 and Phase 4 | Phase 4 still appends a summary; prepends the WARNING described in 4.2. |
| §4.0b parallel lint errors (unreadable manifest, malformed touches) | `parallel-lint ⊘ (error: <reason>)` in the summary. Advisory-only, so never blocks; continue to 4.1. |

### Resumption with `--from-phase N`

`--from-phase N` skips earlier phases. Required preconditions per N:

- `--from-phase 1`: requires PLAN_FILE exists. ENRICHMENT_FINDINGS will be empty → Phase 1 print block shows `(no enrichment — resumed mid-run)`.
- `--from-phase 2`: same as above, plus skip Phase 1.
- `--from-phase 3`: skip 0,1,2. Premortem runs without prior context — note in summary.
- `--from-phase 4`: skip 0-3. Synthesis runs with whatever's in PLAN_FILE (essentially: just append/refresh the summary section).

---

## Token budget (rough)

Phase 0 ≤24k (+~6k if Fork D runs) · Phase 2 50-200k (the Codex verify loop dominates) · Phases 3-4 ≤6k (the S.3 early model-lint and §4.0b parallel lint are deterministic, ≤2k combined) · Phase 1 interactive/uncapped. Roughly ~80k beyond a baseline grill+adversarial chain. The verify loop now runs Codex at `--effort xhigh` (deeper per round) but the **convergence gate** typically caps it at 2 rounds instead of 3 — better findings, fewer wasted rounds. **Note `--quick` does NOT shorten the verify loop** (it skips only Phases 0+3); the dominant Phase-2 cost is structural to `/adversarial-review`, so `--quick` mainly saves enrichment+premortem, not the Codex rounds. If a run subjectively far exceeds budget, the lever is the convergence gate (already automatic), not `--quick`.
