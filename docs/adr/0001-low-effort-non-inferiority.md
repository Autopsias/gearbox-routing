# ADR 0001 — Before lowering a default effort, test "no worse", not "not better"

- **Status:** accepted as method.
- **Applies to:** any proposal to move a `task_classes` row in
  `claude/model-routing.yaml` to a cheaper effort intent or tier. See
  `effort_policy.lowering_a_default` in that file.

This record comes from a real deployment. Its tasks, scores, costs and outcome
stay private; the question, the method and the limits are what transfer.

## Context

Suppose a deployment runs its frontier tier at a medium effort for
deep-reasoning work, and a downgrade proposal asks to move that default to low
effort. A small pilot runs a handful of hard tasks once per arm and scores each
run on a checkpoint rubric with an LLM judge, two passes in permuted order. If
many tasks tie, that looks like good news for the downgrade.

A natural first design asks whether medium is **better** than low. Under that
question a tie is a wasted task, and a tie-heavy pilot wastes most of them. But the
proposal does not ask whether medium is better. It asks whether low is **good
enough**. That is a non-inferiority question, and under it a tie counts as
evidence.

Flipping the question also flips the direction of every known weakness. Judge
noise, tasks at the ceiling and dropped trials all shrink the observed
difference toward zero. In a superiority test that makes a claim harder. In a
non-inferiority test it makes "no worse" **easier**.

## Decision

1. **Test non-inferiority, one-sided, at alpha 0.05**, on the mean per-task
   margin (medium score minus low score, as a fraction of checkpoints). The
   task is the unit of analysis; checkpoints sit inside trials, trials inside
   tasks.
2. **Fix the tolerance before any data exists,** as a fraction of a task's
   checkpoints (for example 0.15, with 0.10 and 0.20 as alternatives an
   operator could pick instead).
3. **Pick the interval method by simulated coverage, against a floor.** Set a
   coverage floor for the nominal one-sided 95% bound and require it at every
   assumed within-arm spread. A sign-flip permutation bound, a paired t bound
   and a small Bayesian cluster model are candidates. Keep a deliberately
   wrong normal-approximation interval as a control: it must fail, so the
   table still discriminates.
4. **Add a resolvable-difference gate.** Compute the smallest true difference
   the task set detects with 80% power. If that exceeds the tolerance, the
   verdict is INCONCLUSIVE, whatever the interval says.
5. **A task whose margin changes sign between the two judge passes is
   INCONCLUSIVE.**
6. **Add a degenerate-spread guard.** If the realised spread of margins across
   tasks is near zero, the verdict is INCONCLUSIVE. Without it, an all-tied run
   declares "no worse" on zero information: the bound collapses to zero. Ties
   are the normal case in such a pilot, not a corner case.
7. **Never pool pilot trials into the analysis.** They feed the variance
   assumptions only.

## Why the method looks like this

A simulation run before the main measurement can show that its verdict is
already determined, and then the run should not start. Two failures to look for:

- **The task set cannot see.** The smallest difference it detects with 80%
  power can sit far above any tolerance on the menu.
- **The task set leans.** Inject a true difference of exactly zero and read the
  mean margin back. Tasks near the ceiling or the floor clip any real difference
  before it reaches the score, so a portfolio of them can make the cheap arm
  look slightly better when the arms are equal. Then the portfolio, not the
  statistics, is biased toward the downgrade.

The fix is the tasks, not the sample size: more trials of the same tasks
cannot resolve the tolerance, and tasks of middle difficulty can. A tie-heavy
pilot is not evidence that the cheaper effort is safe.

## What to do before you lower a default

1. **Check the task portfolio's estimand first.** Build the task set, inject a
   true difference of zero in a simulation, and read back the mean observed
   margin. A sound portfolio returns zero. Anything else answers a question you
   did not ask, usually in the direction that flatters the cheaper option.
2. **Choose tasks of middle difficulty.** A task the cheap arm always passes,
   or the expensive arm always fails, carries almost no information.
3. **Re-measure interval coverage at your real task count,** across several
   seeds, before you commit the budget. Coverage measured at a handful of
   tasks says nothing about thirty.
4. **Repeat one (task, arm) cell early** and read the within-arm spread. Every
   power figure depends on it, and it is the input most likely to be guessed.
5. **Predeclare everything** — tolerance, method, shortfall rule, trial order —
   in a file written before the first trial runs.

## Limits

- A "no worse" verdict is evidence about one task set at one tolerance. It is
  not proof that the cheaper default is safe across the work a deployment
  actually does.
- An LLM judge disagrees with itself. Permute checkpoint order between passes
  and treat a sign change as INCONCLUSIVE, as rule 5 does.
- Effort semantics change between model generations, so a result is tied to
  the model version it was measured on. Re-run it after a model change.

## References

- Bowyer et al., 2025, arXiv 2503.01747 — small-sample intervals for LLM
  evaluation; non-Bayesian intervals under-cover at small N.
- "Resolution Diagnostics for Paired LLM Evaluation", arXiv 2605.30315 — the
  resolvable-difference gate.
- ICH E10 §1.5 — assay sensitivity in non-inferiority trials.
- Morris, White and Crowther, 2019 — Monte-Carlo error of simulated coverage.
- Gelman, 2006 — priors for variance parameters with few groups.
