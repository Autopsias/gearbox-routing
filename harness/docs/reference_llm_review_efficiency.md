# Reference: LLM review, gate and agent efficiency — the measurements

Backing detail for `~/.claude/rules/llm-review-and-agent-efficiency.md`. Not
loaded per session; read it when tuning a gate or diagnosing a slow agent step.

All figures below were measured during one
plan-execute run, unless stated otherwise. Re-measure
before quoting them — they are a snapshot, not a constant.

---

## 1. What a review gate actually costs

`llm-review-<level>` shells out to a full headless session
(`claude -p <prose review instruction> --output-format json`) which reads the
diff, reasons over it, and runs its own probes — **up to two attempts**, so one
unparseable answer cannot read as a pass.

> **THE TABLE BELOW MEASURES A BROKEN GATE. Do not use it as a budget.**
> Until a later commit the prompt ended with `Invoke the code-review
> skill (Skill tool, skill="code-review", args="<level>")`. That skill fans
> finder agents out over the REPO ROOT, so the scoped diff the gate wrote was
> never opened. These durations time a 12-agent crawl of the whole tree, not a
> review of a diff. Section 4 has the before/after on one surface: two timeouts and
> no verdict became one short attempt and a real verdict.

Derived from `verify_started` → terminal-event timestamps across the verify
passes recorded in that plan's `run.ndjson`: on the BROKEN gate the median pass
took about ten minutes and the longest over twenty.

The binding budget is the ladder in `skills/plan-execute/references/verify-gates.md`
— low 23–31s, medium 41–61s, high 40–165s. A run outside that band is a
regression to diagnose, not a cost to accept. Reading twenty minutes as normal is how
this bug survived.

The deterministic `code-review-gate` is not the cheap half: it runs a large test
suite across many shards plus several other checks, and its registry notes a
measured green run of several minutes. Do not estimate this from a subset — a
small subset's timing led to a wrong "about two minutes" claim.

## 2. The silent retry — the single biggest hidden cost

Measured on one deciding run: the parent `llm_review_gate.py` had been running
about half an hour longer than its child `claude -p ...`.

Two attempts at 1800s each means one gate invocation can burn **60 minutes**
before reporting INDETERMINATE. The child being 30 minutes younger than the
parent is the only visible evidence that attempt 1 died WHILE IT IS RUNNING.

**After the fact there is now a log**: the gate writes a
`gate_completed` event to the plan's `run.ndjson` carrying `duration_ms`, the
per-attempt split `attempt_ms`, the gate name and the outcome. Two entries in
`attempt_ms` where the first sits at the wall limit IS the silent-retry
signature. Read the series with:

    python3 -c "import json;[print(r['gate'],r['outcome'],r['duration_ms'],r['attempt_ms']) \
      for r in map(json.loads,open('<plan>/run.ndjson')) if r['event']=='gate_completed']"

It is written by `gate_timing.log_gate`, from inside `llm_review_gate` rather
than from `verify.py`, so it covers the expensive gate without touching the
verify state machine. Skill-kind gates (`test-orchestrate`, `eval-smoke-baseline`)
are still unmeasured: the orchestrator invokes those, and no start time is
recorded anywhere for them.

`pgrep -fl` prints the full command line, so one call usually yields both the
fact of the retry and its cause.

## 3. Payload discipline — a real fix, but not the biggest one

> Retitled. This was written before the fan-out in section 4 was
> found. Payload discipline is worth having and shipped in a later commit, but it
> could not have fixed the timeouts: the payload was never being read.

What the reviewer was handed on that run: a modest diff **plus several untracked
files** with the instruction "Read each one in full and review it as ADDED code".
Most were the session's own evidence artifacts — **probe logs about twice the size
of the diff**.

They are captured run output, not authored source. A reviewer said so unprompted:
*"captured run output, not authored source — I read them as evidence and raise no
findings against them."*

The untracked-file rule is right in principle (a whole new module can hide from
`git diff HEAD`) but does not distinguish a new source module from a log file the
session was required to produce. On any evidence-bearing session the gate
therefore pays model time, and timeout risk, to deep-read its own receipts.

**Do not simply exclude them.** An executable artifact IS code: one review
found a real data-loss bug in a `run_probes.sh` — a failed backup `cp` plus a
`trap restore EXIT` that could copy a zero-byte file over a real source module.
Discriminate on executable-versus-captured-log.

**This now ships in the gate; no config needed**.
`llm_review_surface.split_untracked` sorts the untracked list into source and
captured output, and `untracked_block` gives each half its own instruction:
source is read in full, captured output is listed by name and size with "DO NOT
read them in full". Nothing is hidden either way. The discriminator is
extension-plus-executable-bit — `.txt/.log/.out/.ndjson/.csv/.diff/.patch` is a
receipt UNLESS the file is executable, and an unknown extension reads as source,
because a misfiled receipt costs some reading while a misfiled module ships a
defect. `surface_size` now counts only the source half, so the detection band
reflects what the reviewer was actually asked to read.

**Check that `--exclude` exists in the tree you are running from.** A deployed copy of the
harness can lag the source repo by a merge; a gate argv carrying the flag then fails with
`unrecognized arguments`. Re-measure rather than quote it.

One trap worth the extra second:

* **`git ls-files --exclude-standard` is not this flag.** It is the only
  `--exclude` hit in a tree that lacks the real one, so a careless grep reads as
  a false positive in both directions. Grep for `add_argument("--exclude"`.

Related: the flag is only half the mechanism. A project
`.claude/eval-gates.json` REPLACES the bundled gate entry rather than merging
with it, so an `llm-review-*` entry there must also carry
`PLAN_EXECUTE_REVIEW_EXCLUDE` in its `env_allowlist` or the flag is dead in that
repo — which it was here until a later commit.
Do not add plan-specific paths to `.claude/eval-gates.json` either — a reviewer
correctly flagged that as shared-registry pollution with no expiry, applied to
one review level, leaving the three levels disagreeing.

If a future flag IS added to a gate's argv, put it BEFORE `--timeout` —
`test_shipping.py::test_the_two_gate_registries_agree_on_every_llm_review_timeout`
parses the timeout by splitting the argv string on `--timeout` and taking
everything after it, so a trailing flag becomes part of the value.

## 4. What is already solved — do not rebuild it

- **Review base pinning and scope:** `review_context.gate_env` / `declared_env`.
- **A findings ledger per session:** `_verify_state/<session>.findings.ndjson`.
- **Delta narrowing on rework.** Verbatim from an attempt-2 run: *"surface
  NARROWED to the fix delta — N of M file(s). Everything unchanged since the
  last attempt was reviewed then and is not re-sampled now."* The cost problem is
  NOT that it re-reviews everything each round.
- **Severity floor:** findings in untouched code block only at high severity.
- **A convergence signal** telling the operator to split the session rather than
  redispatch it.
- **The fan-out is gone** (a later commit). The prompt used to end with
  `Invoke the code-review skill (Skill tool, skill="code-review",
  args="<level>")`. That skill dispatches finder agents against the REPO ROOT,
  so the scoped diff the gate had just written was never opened and `--scope`
  stopped meaning anything. One small surface, measured
  both ways: with the skill, both attempts hit their timeout with no verdict, a
  dozen subagents and hundreds of tool calls; without it, one attempt took about
  an eighteenth of the wall time, used no subagents and a handful of tool calls,
  read every file in scope and returned a real finding, on roughly a hundredth of
  the cache-read tokens.

  The tell was not the wall time. Across all the subagents the diff file was
  mentioned ZERO times; their most-read files were `run.py` and
  `skills/plan-execute/scripts/worktree.py`, both outside the scope. `<level>` was that skill call's
  only argument, so the depth it used to buy now comes from
  `llm_review_surface.depth_line`.

## 5. Review verdicts are a sample, not a proof

The same gate over essentially the same code returned **PASSED with zero
findings**, then **FAILED with five, two high severity** — including a check that
was a no-op for every real path in the plan. The runs were not byte-identical (a
cleared ledger, some plan-state files differed), but the code was the same.

> **Superseded explanation.** This was read as sampling variance at a
> large surface. The likelier cause is the section-4 fan-out: a dozen agents crawling
> the repo root sample a different subset of a whole tree each run. Verdicts that
> do not reproduce follow from that far more directly than from surface size. The
> advice below still holds; the diagnosis does not, and this should be re-measured
> against the fixed gate before it is trusted again.

Consequences:
- Never cache a PASS across a state reset or a redispatch. A false fail costs a
  rework; a false pass ships a defect. Encode that asymmetry.
- The gate prints its own detection band, which falls as the surface grows. Treat a pass on a large surface accordingly.
- The cheaper lever may be shrinking the surface, or two passes taking the union
  of findings, rather than caching.

## 6. Open improvements, in priority order

> Items 1 and 2 SHIPPED in a later commit and item 1 was then
> corrected in a later commit after the gate reviewed its own fix. The priority
> order here predates the fan-out finding, which outranked all of them.

1. **Discriminate captured output from source** in the untracked-file list of the
   review prompt (`llm_review_gate.build_prompt`, `llm_review_surface.untracked_files`).
   Keep listing artifacts by name and size; stop instructing a full read.
2. **Record a duration for every gate run.** Nothing does today. `run_state_io.log_event`
   already accepts `**fields`, so no change is needed there — emit from
   `verify._run_gate` and `verify.verify_record`. NOTE: `run_state_io.py` and
   `verify.py` both sit close to their pinned size baselines.
3. **Then, with that data,** decide whether to cache identical-input repeats or to
   run the two gates concurrently. Both are optional and neither is justified by
   evidence yet.

## 7. The subagent watchdog

A dispatched agent is killed after ~600s with no STREAM progress:
`Agent stalled: no progress for 600s (stream watchdog did not recover)`. It
measures output, not liveness — so a long, quiet command kills it in the
foreground and in the background alike.

Measured resume degradation on one agent:

    resume 1   no file changes, killed at the watchdog
    resume 2   real progress, killed mid-edit
    resume 3   almost nothing, killed
    resume 4   nothing at all, killed
    fresh      finished the task in under five minutes

Cost of getting this wrong on that session: five dead attempts.
