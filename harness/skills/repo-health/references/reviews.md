# Reviews beyond the health check

The procedures behind the `refactoring`, `over-engineering`, `performance`
and `trust-ladder` goals of [SKILL.md § Pick what to review](../SKILL.md#pick-what-to-review), and
the one summary every review run ends with. The health check scores what a
tool can measure; these reviews judge what only a reader can see. They are
read-only: they change nothing in the target repo except `.claude/health/REVIEW.md`.

## Contents

- [Before any review](#before-any-review)
- [Evidence rule](#evidence-rule)
- [Refactoring](#refactoring)
- [Over-engineering](#over-engineering)
- [Performance](#performance)
- [Trust ladder](#trust-ladder)
- [Summary](#summary)

## Before any review

1. Run `git -C <repo> status --short` and note uncommitted work. A finding in a
   file that is being edited is stale by the time anyone reads it; name such
   files in the summary instead of reviewing them.
2. Read the project's own guidance: `CLAUDE.md`, `AGENTS.md`, `CONTEXT.md`,
   the ADR folder the guidance names (`docs/adr/`, `docs/adrs/`), and the
   linter, formatter and type-checker configs. Their rules and, above all,
   their ignores are deliberate choices.
3. Write a conventions brief of at most ten lines: the deliberate choices, the
   framework idioms that are not smells, and the paths that are out of scope
   (vendored, generated, build output, lockfiles, `_plans/`, `_evidence/`,
   fixtures). Every review agent gets this brief, so that it does not report a
   deliberate choice as a defect.
4. List the places where the repo already tracks debt: `docs/tech-debt/`,
   `AUDIT.md`, `TODO.md`, issues labelled as debt. A finding that one of them
   already records is reported as `tracked: <path>`, not as new.
5. Pin every review agent to `model: sonnet`, and make it read-only. Give it
   the [evidence rule](#evidence-rule) verbatim. When it starts agents of its
   own, tell it to run them in the foreground and wait for all of them.

## Evidence rule

A finding goes into the summary only when all of these hold:

- It cites `path:line` in the current tree and names the code.
- It states a concrete consequence: a wrong result, time lost on a path that
  runs often, code that can be deleted, or a change that must touch several
  named places.
- A claim that code is dead or unused carries grep proof across source, tests,
  scripts and docs, and allows for dynamic dispatch, framework conventions,
  exported public API and string lookups.
- A claim that code is slow names who calls it and how often (caller proof),
  and carries a label: `measured` (number, command, input size) or
  `suspected — measure with <command>`.

Fewer certain findings beat many plausible ones. Every finding that fails the
rule goes into the summary's dropped list with one line on why.

## Refactoring

The design half comes from the `improve-codebase-architecture` skill beside
this one: shallow modules (an interface nearly as complex as the code behind
it), one concept spread over many small modules, coupling that leaks across
seams, and code that is hard to test through its interface. The measured half
is the health check's `cq.*` rows (file size, function length, complexity),
when the health check ran in the same run.

1. Dispatch one agent (`general-purpose`, `model: sonnet`) with the brief and
   this task: "Read `<skill>/../improve-codebase-architecture/SKILL.md` in full,
   and the files it links. Run its step 1 (Explore) and step 2 (Present
   candidates) on `<repo>`. Stop after step 2: do not ask which candidate to
   explore, and do not start step 3. Run your Explore agents in the
   foreground and wait for every one of them before step 2. Return the
   numbered candidates in the step-2 format, with file paths. Change nothing."
2. Re-open the files that each candidate names. Drop a candidate whose files do
   not show the problem it describes.
3. The candidates go into the summary as options, not as fixes. When the user
   picks one ("explore candidate 2"), read the same `SKILL.md` and follow its
   step 3 on that candidate.

## Over-engineering

The quick scan is the `ponytail:ponytail-audit` skill from the ponytail plugin:
a one-shot, ranked list of what to delete, simplify or replace with the
standard library. It reads a sample of a large repo and gives no proof, so it
produces leads, not findings.

1. Dispatch one agent (`general-purpose`, `model: sonnet`) with the brief and
   this task: "Use the Skill tool to run `ponytail:ponytail-audit` on `<repo>`,
   limited to first-party source. Return its ranked lines unchanged." If the
   skill does not exist (the plugin is not installed), write "quick scan not
   available: ponytail plugin not installed" and go to step 3.
2. Check the top ten lines against the [evidence rule](#evidence-rule): re-open
   each file, and grep for callers of anything tagged `delete:` or `yagni:`.
   Mark each line `checked` or move it to the dropped list.
3. Offer the deep audit, `/declutter`, as an option in the decision card, with
   its cost in its own terms (one subagent per area shard, so a large app is
   expensive). Recommend it when three or more lines
   survive step 2, when the scan was not available, or when the scan says it
   covered only part of the first-party code: a clean sample of a large repo
   is not evidence that the repo is lean. Only the user can start
   `/declutter`, so the summary names the command to type.

## Performance

Runtime speed of the paths that users and jobs actually run. The test suite's
speed is not this review; that is the `test.*` checks and the
[test-speed ladder](../SKILL.md#test--ci-speed--the-ladder).

**No performance claim without a measurement.** A finding is `measured` or
`suspected — measure with <command>`, and the summary never states a speed-up
without a before and an after number.

1. **Read what is already measured.** Benchmark results in the repo
   (`.benchmarks/` from pytest-benchmark, `benchmarks/`, `asv` results),
   tracing or monitoring data that the project's guidance names, measurement
   files that its docs cite (often under a plan's `_evidence/`), and slow-query
   logs. Numbers from real use outrank any code reading. Write each number
   with its date, and check whether a fix landed after it
   (`git log --since=<date> -- <file>`): a number taken before a fix measures
   code that no longer runs. If a source cannot be reached (a tunnel is down,
   a dashboard needs a login), write its name and "not reached", and continue.
2. **Measure the cheap things in the main session.** Run commands that take
   seconds here, never in an agent: an agent that runs a long command stops
   after about 600 s with no output.
   - Python: import time of each entry package,
     `python -X importtime -c "import <package>" 2>&1 | sort -t'|' -k2 -n | tail -15`;
     start-up time of each console script, `/usr/bin/time -p <cmd> --help`,
     three runs, keep the median.
   - JS/TS: build time and bundle size, when the repo has a build script.
   - An existing benchmark suite, when one run takes less than two minutes.
   Record every number with its command and the machine load at the time
   (`sysctl -n vm.loadavg` or `uptime`); a busy machine inflates it.
3. **Map the hot paths.** List the entry points: HTTP routes, CLI commands,
   workers and jobs, MCP tools, scheduled tasks. Pick at most five hot paths:
   code that runs per request or per item, code that step 1 already shows as
   slow, and the flows the project's guidance calls core. Write each as a chain
   from the entry point to the places where it does I/O. Reading the whole repo
   does not scale; following the hot paths does, whatever the repo's size.
4. **Hunt along each hot path**, one agent per path, all in one message. Give
   each agent its chain, the numbers from steps 1–2, the brief, the evidence
   rule, the live values of the feature flags on its path, and this list. Take
   the live values from the deploy config (the production compose file, Helm
   values, `.env.example` names), not from the code defaults:
   - a database or network call inside a loop over results (N+1);
   - file, subprocess or HTTP work repeated per item that could run once or in
     a batch;
   - quadratic work: list membership or nested scans inside a loop over data
     that grows;
   - a blocking call inside async code;
   - unbounded growth: a cache, list or buffer with no limit, or a whole file
     read where streaming fits;
   - expensive setup repeated per call: a client or connection, a compiled
     pattern, a model or an index load;
   - heavy module-level work on the import path of an entry point;
   - a network call with no timeout, or a retry with no backoff;
   - a serialization round trip (dump, then parse) on the hot path.
   The agent reads the code on its path in full, reads outside it only to find
   callers, and runs no benchmark and no test suite.
5. **Verify in the main session.** Re-open every cited line. Drop a finding
   that is not on the named hot path, or that the code already handles (a
   cache, a batch, a documented decision). For the top three `suspected`
   findings, run a micro-measurement when one takes less than a minute
   (`python -m timeit` over a realistic input size, or a timed call against a
   local fixture); a result makes the finding `measured`.
6. **Hand off.** A measured hot spot goes to the sibling `diagnose` skill:
   follow its `SKILL.md`, whose performance branch sets a baseline, bisects
   and fixes it with a regression test. This review fixes nothing.

## Trust ladder

- **Correction** — something a repo has learned not to do, recorded as a rule, a memory note, a revert, a fix commit or a rework finding.
- **Trust ladder** — the four layers that can enforce a Correction, from strongest to weakest.
- **Graduation candidate** — a Correction the review proposes to move one or more layers down the ladder, with its smallest form and path:line evidence.

The four layers, strongest first. The ladder is drawn with the strongest layer
at the bottom, so "down" means toward layer 1:

1. **Shape** — the code's shape makes the mistake impossible (the file, flag or
   path it needs does not exist).
2. **Check** — a lint, test or hook catches the mistake whatever anyone reads.
3. **Guidance** — a rule, skill or memory note advises against it; it works only
   when a model reads it and obeys it.
4. **Review** — a human catches it while reading the diff.

The review asks of each Correction which layer enforces it today, and names the
few that could move down. The numbers come from a script; the placing is a
reader's judgement, checked against the files.

1. **Count.** Run `python3 <skill>/scripts/trust_ladder.py <repo> --json` and
   keep its report as the numbers: prose files and bytes, the check files, the
   NEVER/ALWAYS lines that name a grep-able token, fix and revert commits in 90
   days, doc paths that do not resolve, and the verification base. It finds the
   memory folder as `~/.claude/projects/<slug>/memory`, where the slug is the
   resolved repo path with every character that is not a letter or digit
   replaced by `-`; pass `--memory-dir` to read another. A missing folder reads
   `null`, never 0. TODO density and the one-command verification loop are the
   health rows `hyg.todo-density` and `ai.verify-command`: cite those rows,
   never recount them.
2. **Read the correction record.** `CLAUDE.md` and `AGENTS.md` rules,
   `.claude/rules/`, the project's memory notes, the ADR folder (`docs/adr/`),
   the reverts and fix commits the script counted
   (`git log --since=90.days --format='%h %s'`), and, when the repo has
   plan-execute state, the rework findings in `_plans/*/_verify_state/*.feedback.md`.
3. **Place and propose.** Dispatch ONE read-only agent (`general-purpose`,
   `model: sonnet`). Give it the conventions brief, the
   [evidence rule](#evidence-rule) verbatim, the script's report, the
   paths from step 2, and the places that call the repo's check scripts
   (`.claude/commands/`, deploy and pre-push scripts, CI workflows), and this task: "Place each Correction on the layer that
   enforces it today. Then propose at most five Graduation candidates. For
   each give the layer it sits on, the layer it could move to, the smallest
   form (delete or restructure the thing the mistake needs; else a lint, test
   or hook; else a rule), and the `path:line` evidence for both the Correction
   and the place the new form would live. Prefer the Corrections that recur:
   a rule whose mistake also shows in the fix commits or rework findings.
   Change nothing."
4. **Verify.** Re-open every file a candidate names. Drop a candidate whose
   file does not show the Correction, or whose Correction a check already
   enforces (a candidate on layer 2 that is really on layer 2 moves nowhere).
   Three greps find the checks agents miss: every caller of the check script
   the candidate names (`grep -rn <script> .claude/commands scripts infra .github`);
   `git show --stat <fix commit>` for a regression test the fix already added;
   and the linter's selected rule families (a ruff `select` such as `ASYNC`).
   A recurrence counts only when the commit fixes the same mistake, not the
   same file or area.
5. **Autonomy readiness.** List the gates the repo has: the script's check
   files and verification base, and the health rows `ai.verify-command`,
   `ci.server-side-gate` and `ci.pre-commit` when the health check ran. Then
   the share of pull requests merged in the last 90 days with no human review:
   `gh pr list --state merged --search "merged:>=<date 90 days ago>" --limit 500 --json number,author,reviews`,
   counting a pull request as reviewed when it has a review by someone other
   than its author who is not a bot. When `gh` is missing, unauthenticated or
   the repo has no remote, write "not measured", never 0%.
6. **Summary rows.** In [the summary](#summary), this review's section gives
   the counts from step 1 in one line each, the surviving candidates (layer
   now → layer proposed, smallest form, `path:line`), the autonomy-readiness
   lines, and the dropped candidates with one line on why.

What this review does NOT do: it never designs the top-rung fix, and it changes
nothing in the repo. A candidate is a proposal. It lands through a plan in that
repo, which designs, builds and tests the new form.

## Summary

Every run that includes a review ends here, and gives one summary and one
decision card for everything that ran, including the health check.

1. Write `<repo>/.claude/health/REVIEW.md`, replacing the previous one:
   - a header: date, commit, the reviews that ran, and the health status: this
     run's verdict and score, or the last run's date and score from
     `history.jsonl` marked "not re-run";
   - one section per review, with its findings, each with `path:line` and its
     label, new findings first and `tracked:` ones after them;
   - the dropped list;
   - the decision card;
   - the cost: subagent tokens per review, taken from the agents' completion
     notices, and the wall time.
2. Reply in the chat in this order: the result in one sentence; the health
   status; at most five findings per review, strongest first; one decision card
   with at most three options, a marked recommendation, and what happens if
   the user does nothing; the path of `REVIEW.md`; the dashboard URL when the
   health check published one.
3. Fix nothing without a go-ahead. After it, send health findings through
   [fix routing](../SKILL.md#fix-routing--reuse-never-rebuild), refactoring
   candidates to step 3 of `improve-codebase-architecture`, performance to
   `diagnose`, over-engineering to `/declutter --plan` (the user types
   it), and trust-ladder candidates to a plan in the target repo
   (`plan-builder`). Follow `improve-codebase-architecture` and `diagnose`
   through their `SKILL.md` files, as fix routing does for `flake-detective`. Claude cannot start a
   `user-invocable-only` skill itself, and the file works in every state.

