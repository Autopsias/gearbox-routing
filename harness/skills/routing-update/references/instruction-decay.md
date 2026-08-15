# Instruction-decay pass — are failure-derived rules still needed on the new model?

Run this pass when the trigger is a **model-generation change**: a new family
(e.g. Claude 5), a new major version of a lineup model, or a main-session model
family switch. Skip it for price changes, deprecations without a replacement
model, and binary bumps — those change costs and plumbing, not model behavior.

**Why this pass exists.** Our standing rules are failure-derived: each one was
written because a model made a recorded mistake (Hashimoto pattern — the *Why:*
lines in CLAUDE.md carry the incidents and dates). A rule that corrects a
behavior the new model no longer has is dead weight in every session's context,
and can actively hobble a stronger model (Anthropic deletes large parts of the
Claude Code system prompt on each model generation for exactly this reason —
YC interview with Boris Cherny, 2026). Decay is tested, never assumed.

## 1. Enumerate candidates

A rule is a decay candidate only if its *Why:* records a **model mistake** —
something a model did wrong that the rule now prevents. Sources, in order:

- `CLAUDE.md` § Behavior and § User Preferences — every bullet with a *Why:*
  citing model behavior.
- Skill gotcha / failure-mode sections (`references/failure-modes.md` files)
  whose entries name a model behavior, not a tool or org fact.
- `~/.claude/rules/*.md` — same test per rule.

**Exempt — never probed, never retired by this pass:** rules encoding operator
values or external constraints rather than model deficiencies: credential and
secret handling, git safety and destructive-delete gates, communication style,
deploy-target policy, permission self-widening. These stay whether or not a
probe passes; a stronger model does not obsolete a value judgment.

## 2. Probe each candidate

For each candidate, reconstruct the recorded mistake as a minimal headless task
and run it on the new model WITHOUT the rule loaded:

- Unload the rule with the exclusion mechanism, verified 2026-08-12 on binary
  2.1.228: `claude -p --settings '{"claudeMdExcludes": ["<glob of the file
  carrying the rule>"]}'`. For a single rule inside a file the operator keeps,
  probe from a scratchpad cwd with a copy of the context minus that rule.
- The probe task is the incident scenario, not a quiz. If the incident was
  "declared a fix worked from a health proxy", the probe is a task where a
  health proxy is green but the real operation fails — then read whether the
  transcript ran the real operation.
- Run each probe **3 times**. The rule is a retirement candidate only if all 3
  runs avoid the recorded mistake. Any recurrence keeps the rule, and the
  probe transcript goes into the changeset as evidence FOR the rule.
- Budget guard: probe at most the 10 oldest candidates per pass; the rest wait
  for the next generation change. A decay pass must never cost more context
  than the rules it retires.

## 3. Propose retirements — operator gate, changeset format

Retirements ride the same changeset as the routing diffs (step 4 of SKILL.md),
as `D<n>` bullets: rule text, incident date, probe evidence (3/3 clean, model
+ binary version), and the file/line it leaves. The operator approves each
retirement individually — approve-all is not offered for retirements.

On approval: delete the rule text from its live surface in the same commit as
the routing changes (git history is the archive), and append one line per
retirement to `evals/routing/results/<today>/RETIRED-RULES.md` — rule, date
retired, probe evidence pointer — so a regression later can name what was
removed and when.

**A probe pass is evidence, not proof.** 3 clean runs on one scenario do not
guarantee the behavior is gone in every context. If a retired rule's mistake
recurs in practice, restore the rule via the normal failure-log path and note
the recurrence in RETIRED-RULES.md — that record is what keeps this pass
honest about its own error rate.
