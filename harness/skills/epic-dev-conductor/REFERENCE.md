# Epic-Dev Conductor — Reference

## Contents
- 1. The two substrates (and why they don't nest)
- 2. Autonomy-mode framework (flags + risk → mode)
- 3. Universal safety rails + the failure modes behind each
- 4. Reading the project build policy (+ safe defaults)
- 5. Verification-Workflow template
- 6. Inter-story CI gate (commit → push → CI → remediate-until-green)
- 7. Throughput & resilience (stall recovery · no redundant verification · per-story telemetry)

---

## 1. The two substrates (and why they don't nest)

| | `/epic-dev` + ralph loop | ultracode / Workflows |
|---|---|---|
| Role | **Drives the build** | Bounded analysis fan-out |
| Execution | Top-level agent + fresh `claude -p` per story (ralph) | Background JS spawning short-lived subagents |
| State | Durable (`sprint-status.yaml`), `--resume`, crash recovery | Single-session, restarts fresh on exit |
| Human input | Yes (`AskUserQuestion` when not headless) | **None mid-run** (only permission prompts pause it) |
| Nesting | Orchestrator + one level of workers | One level only |
| Returns | An advancing build | **One** structured report |

**Consequence:** a build is long, multi-session, and human-gated → only `/epic-dev`+ralph can host it. A Workflow can *produce evidence* a human feeds into a gate, but can never *contain* the build, and `/epic-dev` must never be nested inside a Workflow agent (its `Task` delegation silently no-ops). The only bridge between substrates is the opaque `claude -p` process boundary.

## 2. Autonomy-mode framework (flags + risk → mode)

**Flags** (`/epic-dev <N> --full ...`): `--yolo` = no pause between stories in a live session (escalations still prompt the present human) · `--interactive` = answer prompts live · `--loop N` = fully headless ralph runner (fresh ~200K context per story) · `--force-model` = unattended model-selection that **strips `AskUserQuestion`** · `--end` = epic-exit NFR + trace gate · `--uat` / `/epic-dev-uat <N>` = three-tier hybrid UAT.

**Risk → mode** (the project policy assigns each epic a risk class):

| Risk class | Mode | Rationale |
|---|---|---|
| **High-risk** (wrong gate ⇒ correctness/safety/money loss, or irreversible) | `--interactive` | A human turns every gate. |
| **Correctness** (gated, but recoverable) | `--yolo` in a live session | Automatic *and* reachable for gate escalations. |
| **Plumbing** (scaffold/config; cheap if wrong) | `--yolo`; `--loop` tolerable for the trivial stories | Most autonomous; still re-verify at the boundary. |

Default toward `--yolo`-in-a-live-session: maximally automatic **while safe**. Reserve fully-headless `--loop`/`--force-model` for plumbing only.

## 3. Universal safety rails + the failure modes behind each

These are verified against real `/epic-dev` internals (binary 2.1.x). Treat as low-freedom — do not deviate.

- **Never `--force-model` on a high-risk epic.** *Why:* across the 8 phases there are ~13 `AskUserQuestion` gate/escalation sites and almost none have a `--force-model` branch. The intermediate test-gates "auto-continue" by **shipping failing tests** after 3 iterations; the **final quality gate has no unattended branch at all** — headless (`AskUserQuestion` stripped + `bypassPermissions`) the model may assume "continue", fabricate an answer, or burn the timeout, and can **silently mark a FAIL story `done`**. So `--force-model` does not cleanly auto-advance — it degrades unpredictably.
- **Never nest `/epic-dev`.** *Why:* subagents can't spawn subagents; its `Task` delegation silently no-ops and the 8-phase pipeline collapses to one confused agent with no error. Run it top-level (ralph's fresh `claude -p` is main-thread per iteration, which satisfies this).
- **Warn on headless credit.** *Why:* `--loop` is 100% `claude -p`, which on subscription (Max) plans draws a **separate Agent-SDK credit pool**; when it drains, iterations fail silently and the runner retries as if transient. Check credit + cap iterations before long runs; watch the log for repeated identical-iteration failures.
- **Verify, don't trust, at the boundary.** *Why:* a `--force-model` gate decision is the model's own classification under `bypassPermissions` + per-iteration auto-commit — a wrong "done" is committed before any human sees it. Re-derive each story's gate from evidence (Step 6 Workflow); treat any `--force-model`-gated story as unverified until confirmed. Keep the project's absolute gates human-run.
- **Empirically reproduce HIGH / security / Death-A findings — a green re-review is necessary but not sufficient.** *Why:* across one Epic-1 build, code-review surfaced ~10 HIGH defects that the *passing* test suites missed (a silent-NaN fail-open in the money kernel; serialization byte-identity holes; a vacuous CI gate that silently skipped; enforcement guards that never scanned real source; a secret-redaction filter that leaked the real broker session-token). For any HIGH or security/Death-A finding, the conductor independently **reproduces the issue and confirms its fix** (plant the forbidden import and watch the contract break; probe the redactor with the real credential shapes; re-derive the determinism golden across seeds/TZ) before accepting the fix — do not trust the re-review's "0 HIGH" alone. This caught a residual leak class a green suite had missed.
- **Keep ralph's recursive-spawn design** (fresh context per story) — do **not** switch to an in-session Stop-hook loop, which accumulates context and degrades on a long gated build.

### Gate autonomy policy — block vs notify-and-continue (OR-03, added 2026-07-03)

The reliability self-assessment found ~26% of April–May prompts were rubber-stamps (`proceed`/`c`/`y`/menu letters) answered to gates that historically ALWAYS got the same answer. The conductor mirrors plan-execute's per-gate policy field so a rubber-stamp gate default-continues **with a notification** instead of blocking for a poll — but the classification is a **fail-closed, per-gate-TYPE allowlist**, never a content heuristic.

- **Eligible (rubber-stamp) TYPE — default-continue with a one-line notification:** a **BMAD numbered elicitation menu** whose default answer is "proceed" (`bmad_elicitation_menu`). When such a menu fires and its default is "continue", proceed and emit a one-line ping (`PushNotification` / the project's notify hook) instead of waiting; note the auto-continue in the run log.
- **ABSOLUTE — always block, never reclassifiable** (the four conjoined conditions must ALL hold to auto-continue; any miss → block):
  - a **policy-defined human checkpoint** (Step 7) — never crossed autonomously;
  - the **epic-boundary quality gate** (`--end` / `gate-decision.json` CONCERNS/FAIL, Step 6);
  - any **CI-red stop-condition**, tripped **cost/latency/escaped-defect threshold**, or **Death-A** defect (§7 / Step 5a);
  - any gate guarding an **irreversible/destructive** action (deploy, force-push, spend, data-delete);
  - any **`--force-model`-sensitive** gate (safety rail above — headless the model may fabricate "continue"); and
  - any **unknown/new** gate TYPE (defaults to block until a human adds it to the allowlist).
- **A menu that is NOT a pure "proceed" elicitation** (it presents a real branch/decision) is genuine judgment → block, even though it renders as a numbered menu. The allowlist is the gate TYPE + its "proceed" default, not "it looks like a menu".

This never overrides §3's rails or the §7 stop-conditions — it only removes the redundant "shall I acknowledge this routine menu?" pause, exactly as auto-advance (SKILL Step 5b) removes the "shall I start the next story?" pause. Companion mechanism in plan-execute: `dispatch.checkpoint_policy` + `scripts/gate_policy.py`.

## 4. Reading the project build policy (+ safe defaults)

Look in the project's `CLAUDE.md` for a build section (e.g. "Building this repo") or a `.claude/build-policy.md`. Extract:
- **Per-epic mode + risk class** (which epics are high-risk).
- **Human checkpoints** — the epic boundaries where the operator must decide (often UAT gates, go/no-go gates, release sign-offs).
- **Absolute gates** — conditions that must hold regardless of queue order (e.g. a correctness gate before any irreversible step).
- **One-time setup** — sprint-planning, test-framework config, skills to skip.

**Safe defaults when no policy exists:** treat **every** epic as correctness-class (`--yolo` in a live session, never `--force-model`); **stop at every epic boundary** for a human review; run the verification Workflow at each boundary; and offer to write a build policy into the project's `CLAUDE.md` so future runs are precise.

## 5. Verification-Workflow template

At an epic boundary, fire a bounded Workflow that re-derives the gate from evidence and returns one report. Adapt the readers to the project's evidence (test output, coverage/trace matrix, `gate-decision.json`, any domain-specific verdict artifact).

```js
export const meta = {
  name: 'epic-boundary-verify',
  description: 'Re-derive an epic gate decision from evidence, adversarially',
  phases: [{ title: 'Gather' }, { title: 'Verify' }],
}
const VERDICT = {
  type: 'object',
  properties: {
    status: { type: 'string', enum: ['PASS', 'CONCERNS', 'FAIL'] },
    evidence: { type: 'array', items: { type: 'string' } },
    unverifiedStories: { type: 'array', items: { type: 'string' }, description: 'stories that passed a --force-model gate' },
    reasons: { type: 'array', items: { type: 'string' } },
  },
  required: ['status', 'reasons'],
}
phase('Gather')
const dims = ['tests-and-coverage', 'requirements-traceability', 'nfr-evidence', 'recorded-vs-derived-gate']
const findings = await parallel(dims.map((d) => () =>
  agent(`Re-derive the Epic ${args.epic} gate for dimension "${d}" from on-disk evidence only (test output, trace matrix, gate-decision.json). Do not trust recorded verdicts. Return status + reasons.`,
    { label: d, phase: 'Gather', schema: VERDICT })))
phase('Verify')
const decision = await agent(
  `Synthesize one PASS/CONCERNS/FAIL for Epic ${args.epic} from these per-dimension findings; fail closed on any FAIL; list every story that passed a --force-model gate as unverified.\n${JSON.stringify(findings.filter(Boolean), null, 2)}`,
  { label: 'synthesize', phase: 'Verify', schema: VERDICT })
return decision
```

Pass `{ epic: N }` as the Workflow `args`. Present the returned verdict to the operator at the checkpoint; never let the Workflow turn the gate.

## 6. Inter-story CI gate (commit → push → CI → remediate-until-green)

**Standing operator rule (authorized — overrides the default "commit/push only when asked"): after EVERY story reaches `done`, commit + push + run CI, and do not start the next story until CI is fully green.** This keeps `main` continuously green and localizes any breakage to the single story that caused it. Run this between *every* pair of stories, and also before an epic boundary (Step 6).

### Procedure

1. **Confirm the story is actually `done`** in `sprint-status.yaml` and the story file (the conductor set this after Phase 8 PASS). Never commit a story still in `review`/`in-progress` as if complete.
2. **Stage + commit.** Stage the story's artifacts (impl, tests, evidence docs, the status-file updates). Commit with a message that names the story and epic:
   ```
   story <story-key>: <short title> — done (epic <N>)

   <1–3 line summary: what shipped + key verdict/coverage>

   Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
   ```
   (Use the project's required co-author trailer from its git rules.) If the build runs on a branch, that is fine; keep one branch per build unless the project policy says PR-per-story.
3. **Push** the current branch to `origin`. If there is no remote, record "no remote — local commit only" and proceed (CI is then vacuously satisfied; flag it so the operator can add a remote).
4. **Run / await CI.**
   - **CI exists** (`.github/workflows/*` present AND a remote configured): watch the run for the pushed commit — `gh run watch --exit-status` (or `gh pr checks <pr> --watch` when building via PRs). Capture the conclusion.
   - **No CI yet:** many builds stand CI up partway through (in this project, Epic 1 / **Story 1.5** creates the dual-arch determinism CI). Before that story, there is nothing to run — record **"no CI configured yet — commit+push only"** and treat the gate as satisfied. Do not fabricate a CI result.
5. **Decision.**
   - **All checks green →** proceed to the next story (or the epic boundary).
   - **Any check red/failing →** do **NOT** advance. Run the operator's standing remediation: **goal = "all CI green", engine = `/ci-orchestrate`** (the operator's phrasing: `/goal all ci green` → `/ci-orchestrate`). Loop: `/ci-orchestrate` analyzes + dispatches fixers → commit the fixes → push → re-watch CI. Repeat until green. Each remediation cycle is itself committed + pushed (the failure and its fix stay in history, per the git-safety "visible & recoverable" principle). Only a green CI clears the gate.
6. **Escalate, don't loop forever.** If CI stays red after a bounded number of `/ci-orchestrate` cycles (default 3) — or a failure is plainly outside the bot's remit (missing secret, infra outage, a real product/design decision) — **STOP and surface it to the operator** with the failing job + logs. A persistently-red CI is a HALT condition, never a "mark done and move on."

### Notes
- **`/goal` is not a discovered slash-command** in this environment; it is the operator's shorthand for "drive toward the goal *all CI green*." The concrete, available engine is **`/ci-orchestrate`** (the `ci-orchestrate` skill). If a real `/goal` command is later installed, prefer it as the operator wrote it.
- **Interaction with `--loop` (headless ralph):** the per-story CI gate requires the conductor (main thread) to be present between stories. In fully-headless `--loop` mode the conductor is not between stories, so the CI gate **cannot run there** — another reason `--loop` is reserved for trivial plumbing. For any epic where the CI gate matters, drive story-by-story in a live session (`--yolo`/`--interactive`), not `--loop`.
- **Hands-free advance that KEEPS the CI gate — in-session self-paced `/loop` (NOT headless `--loop`).** To run a correctness/Death-A epic hands-free without losing the main-thread CI gate or the present-human-for-escalations safety, launch the conductor under **`/loop continue the build`** (no interval = self-paced) — not headless `--loop`/`--force-model`. The conductor stays on the main thread, so the CI gate runs and any in-phase `AskUserQuestion` escalation still reaches you; after each green story it calls `ScheduleWakeup(~60s, "continue the build")` to re-enter the next story across the turn boundary (SKILL Step 5b). It stops scheduling on any stop-condition or epic completion. This is the supported hands-free path for gated epics; headless `--loop`/`--force-model` stays plumbing-only. Caveat: an in-session loop accumulates context across the epic (same degradation risk as the in-session Stop-hook ralph) — cap to one epic and `--resume` if it gets long.
- **Pre-CI epics still commit+push.** Even when CI doesn't exist yet, the commit+push half of the gate runs every story, so history stays story-granular from the start.
- **Confirm push authorization in PREFLIGHT — the agent cannot self-grant it.** *Why:* the inter-story `git push origin main` can be harness-gated, and an agent writing its own `Bash(git push origin main:*)` allow-rule into `.claude/settings.local.json` is blocked as an Auto-Mode-Bypass — so an unauthorized push stalls **every** story. Before the build, confirm a working push path: either the operator adds the `Bash(git push origin main:*)` allow rule (or uses PR-per-story), or accept that pushes are operator-gated. If gated mid-build, **batch commits locally and surface the rule-add as an explicit operator action — do not retry the push every story.** Commits are recoverable; a stalled push is not a reason to halt the build.

---

## 7. Throughput & resilience (generic — applies to every story, every project)

A single eventful story can run for hours. Two avoidable classes of waste dominate — **stalled delegations** and **redundant verification** — and a third gap (no per-story telemetry) means policy cost/latency thresholds never actually get checked. These are stack-agnostic; apply them on every story regardless of language, test runner, or domain.

### Delegated-phase stall recovery
A delegated phase/subagent can die on the harness **stream watchdog** (~600 s of no output) *without returning a result* — observed when an agent runs a long, quiet step (a slow full test suite whose stdout the runner swallows). A stall is not a failure verdict. On a stall / no-return:
1. **Inspect the actual on-disk state** — VCS status, the artifact the phase was supposed to produce, the test count — to see how far it got. Never assume success *or* failure from the stall alone.
2. **No usable progress** → re-dispatch the same phase with a **tightened, bounded scope** (explicit small steps; "work efficiently, don't over-explore; finish within budget"). A bounded prompt is far less likely to re-stall.
3. **Work completed but it died before returning** (state shows the artifacts exist and gates pass) → accept the work *on the evidence* and author the phase result from it. Do **not** blindly re-run a completed phase — re-running can duplicate edits or double-apply migrations.
4. Escalate to the operator only if a bounded re-dispatch also stalls, or the on-disk state is ambiguous.

### Transport-error auto-retry (OR-01, added 2026-07-03)

Distinct from the no-return **stall** above: a dispatch can also fail
immediately with a **transport-layer** error — connection refused, "failed
to open socket", a 529/overloaded, a request timeout, a connection dropped
with zero output. That's not a stall (nothing ran) and it's not a semantic
failure (the model never got to try) — it's the same class of toil that
motivated 142+ manual "retry" invocations across sessions. The conductor
uses the **same classifier** the `plan-execute` skill uses, so both
orchestration loops agree on what counts as retryable:

1. Classify the error via `~/.claude/skills/plan-execute/scripts/transport.py`'s
   `classify_error(error_text)` — `retryable` (connection-class, no
   response), `ambiguous` (dropped **mid-response** — partial output already
   streamed; treated as non-retryable, the far side may have already
   committed), or `semantic` (the model ran and produced something).
2. Only `retryable` errors get `transport.decide(...)`'s bounded retry: max
   3 attempts, full-jitter exponential backoff, capped at ~90s total elapsed.
   Every attempt/decision is worth a one-line note in the phase's telemetry
   (§ below) — this conductor doesn't have `run.ndjson`, so log it in the
   per-story summary instead: *"phase X hit 1 transport error (connection
   refused), retried once, succeeded."*
3. **Commit-boundary gate — do not skip this.** Once a phase has committed a
   side effect (a git commit landed, a file outside its own scratch was
   written, a PR/CI action fired), a SUBSEQUENT transport error for that same
   phase is surfaced, never auto-retried — a retry after a commit risks a
   duplicate side effect with no idempotency key. This mirrors the
   `plan-execute` dispatch loop's `crossed_commit_boundary` rule exactly (see
   `plan-execute/SKILL.md` § Transport-error auto-retry) — the two loops
   share the reasoning, not just the pattern.
4. `ambiguous` (mid-response drop) and `semantic` errors fall straight
   through to the existing stall-recovery flow above: inspect on-disk state,
   re-dispatch bounded or accept-on-evidence, escalate if still unresolved.

Never conflate this with the stall watchdog's re-dispatch — a transport
retry re-runs the SAME prompt unchanged (nothing ran, nothing to tighten);
a stall re-dispatch tightens scope (something ran, partial progress exists).

### Long-delegation hygiene (don't trip the watchdog)
When dispatching any phase that includes a long/quiet step (full suite, large build, broad scan), instruct the agent to: run the long step **in the background and poll** (or with an explicit timeout) instead of blocking silently; **emit periodic progress**; and keep its scope bounded. A quiet multi-minute block with no streamed output is exactly what the watchdog kills.

### A reviewer's fix is a hypothesis — fence fix scope, don't relay verbatim
The primary defenses live in the agents (`epic-code-reviewer` states the *finding* as authoritative and its *suggestion* as a hypothesis; `epic-implementer`/`epic-test-fixer` self-fence to the story's owned code, blast-radius-check any shared/global/cross-package change, and return `blocked` on a scope cascade rather than thrash). **Backstop for the hand-driven path:** if the conductor ever dispatches a fix itself (outside `/epic-dev`'s own Phase-5 loop), relay the reviewer's **finding**, not its prescription verbatim — and never relay a fix that mutates a **global guard/scanner, a schema, a cross-cutting contract, a golden/parity fixture, or another package's/epic's module** without first doing the 30-second blast-radius check (grep the other callers; does it touch goldens / parity / byte-identity / another package's tests?). A cross-cutting reviewer prescription relayed verbatim is exactly what breaks an unrelated package and ends in a watchdog stall. Carry the scope fence in the fix Task prompt **from iteration 1**, not only after the first attempt thrashes.

### Don't pay for the same verification twice
The 8-phase cycle *and* the conductor can each re-run the **same** full suite many times (every intermediate gate + every dev/fix agent's pre-push + the conductor's own check). The dominant waste observed in practice is **a single fix/expand agent re-running the whole suite after every edit** (e.g. a test-quality fixer that took ~55 min / ~195 tool calls, most of them full-suite re-runs on a 1,200-test suite) — and, secondarily, the conductor running a near-identical full suite at four intermediate gates in a row. Two standing rules kill both, with no loss of the §3 correctness rails:

**(a) Standing agent-dispatch contract — append to EVERY code/test-editing Task prompt (Phases 4, 5-fix, 6, 7-fix, and all gate-fix loops).** Make it part of the prompt the conductor injects, not an ad-hoc afterthought:
> *During your edit loop, scope the test runner to the changed/affected test files only (e.g. `pytest <those files>`) for fast feedback. Run the **full** suite + static gates exactly **once**, at the very end, before you return. Do not re-run the full suite after every edit.*

This is the single biggest saver — it turns N full-suite runs inside one agent into 1. The agent still returns a full-suite-green result, so nothing downstream is weakened.

**(b) Impact-scope the conductor's OWN intermediate gates; one authoritative full-suite per story.** The gate exists to catch what JSON-only output hides — but what it must catch depends on *what changed*:
- A phase that changed **production code** (dev, review-fix) → the conductor runs an **independent full suite** (production change can regress anything). Non-negotiable.
- A phase that changed **tests only** (test-expansion, test-quality) → run the **new/changed tests + the determinism/golden/arch-guard subset** (fast), not the whole suite: a tests-only change cannot regress production behaviour, only the new tests themselves. Defer the one **authoritative full suite to the pre-commit gate** — which feeds the CI run (the ultimate full-suite-on-all-arches check) anyway, so a green pre-commit full suite + green CI is the belt-and-suspenders, not four local full runs.
- Use the **project's fastest equivalent invocation**; keep any policy-marked **single-worker** subset (determinism/parity/state-leak-sensitive) single-worker. Run mutually-independent checks (typecheck, lint, import/arch contracts, tests) **concurrently**, not serially.

**This is a throughput rule only.** It does NOT relax the correctness rails in §3: every story still gets ≥1 authoritative full-suite run before its commit, the inter-story CI gate (§6) still must go green, the boundary re-derivation (Step 6 Workflow) and the empirical reproduction of HIGH / security / Death-A findings remain mandatory — those *re-derive from evidence by design*, they are not redundant repeats of a green run.

### Per-story telemetry → operationalize the policy's thresholds
If the project policy defines economic/quality guardrails (cost or latency ceilings, an "escaped-defect" criterion, a rollback trigger), the conductor must actually **check them** — they are worthless if never evaluated. After each inter-story gate:
- Capture the story's **wall-clock**, the **model per phase**, and (where the harness exposes it) **token/cost**, plus any **stalls/retries**. Emit a one-line per-story telemetry summary.
- Compare against the policy thresholds. If one trips — or a Death-A/critical defect **escaped** an earlier phase and was only caught at review/CI — surface it to the operator with the policy's remediation (e.g. the revert path), don't silently continue. Escaped-defect signals worth flagging explicitly (each observed costing ~50+ min on a single story): **green-but-broken (fake-input)** — a defect that passed the dev phase's green suite because the tests exercised a fabricated input shape, caught only at code-review (root cause: ATDD/impl isolated from the real dependency contract — see the `epic-atdd-writer`/`epic-implementer` real-boundary rules); **cross-package cascade** — a fix (or relayed reviewer prescription) that broke another package / golden / byte-identity and forced a revert or stalled a fix agent (root cause: a cross-cutting fix relayed/applied without a blast-radius check); **green-but-broken (oracle-gaming)** — the dev/fix agent hit a *broken ATDD oracle* (a test helper that swallows or mis-routes the very exception/value it asserts) and, instead of fail-fasting, shipped a **production hack whose only purpose is to thread a value through the test harness** (a side-effecting `__format__`/`__str__`, a sentinel exploiting a helper's catch/re-raise, an attribute the helper reads) — a landmine behind a passing suite (real case: an `_EscapingTypeError.__format__` re-raise; ~1600 tests green, a crash-on-format error shipped). Fix the oracle, not the production; see the `epic-implementer`/`epic-test-fixer` oracle-gaming rules. **false-fix-claim** — a dev/fix agent reports findings/tests fixed that it never actually applied or never observed pass. **The latter two are why the conductor must EMPIRICALLY RE-VERIFY every claimed fix with its own probe** (not trust the agent's self-report or the green suite) before accepting a gate — the §3 verify-don't-trust discipline applied per-fix, not just at the epic boundary. Recurrence of any of these is a signal to tighten the upstream agent rule, not just to re-run.
- If no baseline exists yet, record this story's numbers **as** the baseline so later stories have something to compare against.
- A useful signal for tuning the policy's model map: note which phases dominate wall-clock and whether the expensive (e.g. opus) phases actually *caught* something — feed that back to the operator rather than assuming the current model/effort assignment is optimal.
