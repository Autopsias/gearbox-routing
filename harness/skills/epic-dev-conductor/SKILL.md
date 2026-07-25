---
name: epic-dev-conductor
description: Conducts a BMAD /epic-dev build for any project. Reads the project's build policy from its CLAUDE.md, drives /epic-dev at the right autonomy mode per epic, enforces universal safety rails (never --force-model where a wrong gate is costly; never nest /epic-dev; warn on headless Agent-SDK credit), fires bounded verification Workflows at epic boundaries, and halts at the project's human checkpoints. Use when the user says "conduct the build", "continue the build", "run the next epic", "run the next story", "drive epic-dev", or wants to advance a BMAD implementation safely. A bare "develop/run epic N" belongs to /epic-dev itself; this skill is for CONDUCTING the whole build across epics (policy, autonomy modes, checkpoints).
---

# Epic-Dev Conductor

A top-level conductor for **any** BMAD build. It **orchestrates `/epic-dev`** — it never re-implements it — and applies the **project's own build policy** for every risk-specific decision.

## What this is — and is NOT (read once)

- **Driver = `/epic-dev` + the ralph loop.** This skill picks the mode, drives one epic to its next boundary, and stops at the gates. The 8-phase per-story cycle, the DoD, and `sprint-status.yaml` belong to `/epic-dev`.
- **NOT a Workflow; NOT "ultracode-driven".** Workflows cannot span the build's many sessions or pause for human gates, so they cannot host the build. This skill uses a Workflow **only** for bounded verification at epic boundaries (Step 6).
- **Run as the top-level agent.** `/epic-dev`'s `Task` delegation silently no-ops inside a subagent or Workflow agent. If you are not the main thread, STOP and say so.

## Project policy comes first

The risk-specific choices — the autonomy mode per epic, which epics are **high-risk** (a wrong gate decision costs correctness, safety, money, or is irreversible), the human checkpoints, and the absolute gates — are **project-specific**. Read them from the project's `CLAUDE.md` (look for a "Building this repo" / build-policy section) or a `.claude/build-policy.md`. If neither exists, use the **safe defaults** in [REFERENCE.md](REFERENCE.md) and offer to write a policy. On any conflict, the project policy wins.

## Conductor checklist

Copy this into your response and check off each item:

```
Epic-Dev Conductor — Epic N
- [ ] Preflight: top-level agent? sprint-status.yaml exists? test framework configured? headless credit OK?
- [ ] Read project policy (CLAUDE.md build section); note high-risk epics + checkpoints
- [ ] Locate state: current epic, next pending story, last cleared gate
- [ ] Pick mode from policy (REFUSE --force-model on a high-risk epic)
- [ ] Drive STORY-BY-STORY: /epic-dev <N> --full <mode> → one story to `done`
- [ ] Inject the §7(a) **scoped-iterate / full-suite-once-at-end** contract into EVERY code/test-editing phase prompt (dev, fix, expand) — biggest wall-clock saver; turns N internal full-suite runs into 1
- [ ] Gate scope (§7b): production-code phase → independent FULL suite; tests-only phase → changed tests + determinism/golden/arch guards, defer the one authoritative full suite to the pre-commit gate
- [ ] Delegated phase stalls (watchdog ~600s / no return)? Inspect on-disk state → re-dispatch bounded, or accept-on-evidence; don't assume pass/fail (REFERENCE §7)
- [ ] Dispatch errors with a **transport** signature (connection refused, timeout, 529)? Classify + bounded-retry via `transport.py` (shared with plan-execute), NEVER retry once the phase crossed its commit boundary (REFERENCE §7 "Transport-error auto-retry")
- [ ] After EACH story `done`: commit + push + CI gate (Step 5a). CI not green → /ci-orchestrate until green; NEVER start the next story on red CI
- [ ] After the gate: emit per-story telemetry + check the policy's cost/latency/escaped-defect thresholds (REFERENCE §7)
- [ ] More stories left + no stop-condition? AUTO-ADVANCE: begin the next story NOW — do NOT yield to the operator (Step 5b). Stop-conditions are the ONLY exits: CI red after N cycles · checkpoint · tripped threshold/escaped Death-A defect · ambiguous stall · operator interjects
- [ ] Boundary (epic's last story): /epic-dev <N> --end  → fire verification Workflow → read gate-decision.json
- [ ] Checkpoint? STOP, present evidence, operator decides (never cross it)
- [ ] Advance to Epic N+1 (EPIC advance is one-per-invocation unless told to continue; STORY advance within an epic is AUTOMATIC — never pause between two clean stories)
```

## Steps

1. **Preflight.** Confirm you are the top-level agent. Read `sprint-status.yaml` (in the project's implementation-artifacts dir); if absent, run `bmad-sprint-planning` and re-read. Confirm the test framework is configured for the stack. ⚠️ Headless `--loop` is 100% `claude -p`, which on subscription (Max) plans draws a **separate Agent-SDK credit pool that can drain mid-build and then fail iterations silently** (the runner retries as if transient) — before any long headless run, have the operator check credit and cap iterations.
2. **Read project policy.** Load the project's build section; note the per-epic modes, the high-risk epics, the checkpoints, and the absolute gates. No policy → use [REFERENCE.md](REFERENCE.md) safe defaults.
3. **Locate state.** From `sprint-status.yaml`, find the current epic `N`, next pending story, and whether the prior epic's gate + checkpoint are cleared. State plainly where the build is.
4. **Pick mode.** Apply the policy + the **hard rails — non-negotiable, refuse to break even if asked** ([REFERENCE.md](REFERENCE.md) explains why):
   - **Never `--force-model` on a high-risk epic.** Headless gates degrade unpredictably and can mark a FAIL story `done`.
   - Correctness and plumbing epics → **`--yolo` in this live session** (you stay reachable for gate escalations).
   - High-risk / irreversible epics → **`--interactive`**.
   - Fully headless `--loop` → trivial plumbing only, watching `/tmp/ralph-loop-*.log`.
5. **Drive the epic — STORY-BY-STORY.** Run `/epic-dev <N> --full <mode>` but drive it **one story at a time** so the conductor regains control at every story boundary (don't hand the whole epic to `/epic-dev`'s internal multi-story loop, or the per-story CI gate is skipped). Let `/epic-dev` own each story's 8-phase cycle; after each story reaches `done`, run **Step 5a, then auto-advance per Step 5b (do not pause for the operator between two clean stories)**.
   - **Resilience + throughput (every phase, every story — [REFERENCE.md](REFERENCE.md) §7):** if a delegated phase dies on the harness watchdog (~600 s no output) or returns nothing, **inspect on-disk state** and either re-dispatch with a tightened/bounded scope or accept the work on evidence — never assume pass/fail from a stall. When a phase will run a long, quiet step (full test suite, big build), tell the agent to background-and-poll it and emit progress so it doesn't trip the watchdog. Use the project's fastest/parallel test invocation for any verification you run, run independent checks (typecheck/lint/contracts/tests) concurrently, and don't redundantly re-run an identical just-green suite — this throughput rule never overrides the §3 correctness re-derivations.
5a. **Inter-story CI gate (after EVERY story `done`, before the next story starts).** This is a **hard gate — the next story does not begin until CI is green.** See [REFERENCE.md](REFERENCE.md) §6 for the exact procedure. In short:
   - **Commit + push.** Stage the completed story's changes and commit with a message naming the story (`story <key>: <title> — done`, plus the project's required `Co-Authored-By` trailer); push the current branch to `origin`.
   - **Run CI.** If the repo has CI (`.github/workflows/*` + a remote), watch the run for the pushed commit (`gh run watch` / `gh pr checks`). **If no CI exists yet** (e.g. early in a project, before the story that first stands CI up), record "no CI configured yet — commit+push only" and proceed; the gate is satisfied vacuously.
   - **If CI is not fully green → remediate, don't advance.** Run the operator's standing remediation — **goal: all CI green, via `/ci-orchestrate`** — looping (fix → push → re-check) until CI is green. Do **not** create or start the next story while CI is red.
   - **Only when CI is green (or absent) → proceed** to the next story (or, if this was the epic's last story, to the boundary in Step 6).
   - **Then emit per-story telemetry + check thresholds (REFERENCE §7).** One line: phases run, model per phase, wall-clock, any stalls/retries. If the project policy defines cost/latency/escaped-defect thresholds, evaluate them now — a tripped threshold or a Death-A defect that *escaped* to review/CI is an operator flag, not a silent continue. No baseline yet → record this story as the baseline.
5b. **Auto-advance — the inter-story loop is NON-YIELDING.** (This is the fix for the conductor stopping between stories.) After Step 5a clears, treat the epic as a loop, not a one-shot:

   ```
   WHILE the epic has a pending (not-done) story:
     drive the next story to `done`     (Step 5)
     run the inter-story CI gate         (Step 5a)
     IF a stop-condition fired: STOP, surface it, do NOT advance
     ELSE: begin the NEXT story immediately
   END WHILE  → epic's last story done → go to the boundary (Step 6)
   ```

   - **Do NOT yield to the operator between two clean stories.** A finished story is a natural turn-boundary and the default reflex is to summarize-and-wait — **suppress that reflex.** When the CI gate clears with no stop-condition, begin the next story in the SAME turn; a one-line "story N done → starting story N+1" is the only pause.
   - **The ONLY stop-conditions** (exhaustive — nothing else halts the loop): (a) CI still red after the bounded `/ci-orchestrate` cycles (§6 / Step 5a); (b) a human **checkpoint** the policy defines (Step 7); (c) a tripped cost/latency/escaped-defect **threshold** or a Death-A defect that escaped to review/CI (§7); (d) an **ambiguous stall** a bounded re-dispatch did not resolve (§7); (e) the **operator interjects**. Anything that fires `AskUserQuestion` *inside* a phase still reaches you — auto-advance never suppresses a gate escalation, it only removes the redundant "shall I start the next story?" pause.
   - **Self-paced loop (turn-boundary-surviving) — the hands-free path.** Instructions alone can't beat context/wall-clock limits across a long epic, so for a hands-free run launch the conductor under **`/loop continue the build`** (no interval = self-paced) rather than headless `--loop`. The conductor stays on the main thread (CI gate runs, escalations reach you); after each story's CI gate clears with no stop-condition AND a pending story remains, call **`ScheduleWakeup(delaySeconds≈60, prompt="continue the build")`** so the next story re-fires automatically even if the turn ends. When a stop-condition fires OR the epic completes, **do not schedule another wakeup** — let the loop end. In-session context accumulates across the epic (your CLAUDE.md's in-session-loop degradation caveat applies); if a long epic degrades, the operator restarts with `--resume`, which resumes mid-epic from `sprint-status.yaml`. If you were *not* launched under `/loop`, still auto-advance within the turn per the loop above — you simply lose the cross-turn safety net.
6. **Boundary + verify (feedback loop).** Run `/epic-dev <N> --end`. Then **fire the verification Workflow** ([REFERENCE.md](REFERENCE.md)) to re-derive the gate from evidence — do not trust the recorded verdict for any story that passed a `--force-model` gate. This Workflow is authorized by this instruction (no "ultracode" prefix needed). It is auto-fired, but first state the approximate token cost (~0.4–0.6M subagent tokens) in one line so the spend is never silent. It is the **only** place this skill uses a Workflow — the build itself is always `/epic-dev`, never a Workflow. Read `gate-decision.json`. On CONCERNS/FAIL → HALT, present the failing evidence, do not advance.
7. **Checkpoint.** At each checkpoint the policy defines, STOP, present the gate artifact + Workflow report, and let the operator decide via `AskUserQuestion`. Never cross a checkpoint autonomously.
8. **Advance.** **EPIC** advance is the one human-paced step: on a cleared boundary and checkpoint, offer to proceed to epic `N+1` (return to Step 3) — one epic per invocation unless the operator says continue. **STORY** advance *within* an epic is automatic (Step 5b) — never pause between two clean stories waiting for the operator to say "continue".

See [REFERENCE.md](REFERENCE.md) for the autonomy-mode framework, the universal safety rails and the failure modes behind each, the verification-Workflow template, the safe defaults used when a project has no build policy, and the throughput & resilience playbook (§7: stall recovery, no-redundant-verification, per-story telemetry).
