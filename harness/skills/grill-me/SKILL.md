---
name: grill-me
description: Interview the user relentlessly about a plan or design until reaching shared understanding, resolving each branch of the decision tree. Use when user wants to stress-test a plan, get grilled on their design, or mentions "grill me". Use when no domain docs need updating; if the repo has CONTEXT.md/ADRs that should capture the decisions, use grill-with-docs instead.
---

Same interview discipline and mandatory post-interview adversarial-review protocol as
`/grill-with-docs` — read `~/.claude/skills/_shared/grill-adversarial-review.md` for the
full shared wording (interview opening + "After the session — adversarial review").
This skill's only delta on top of that shell: no CONTEXT.md/ADR side effects (that's
`/grill-with-docs`'s job when you want the interview to also sharpen and persist domain
language).

Order questions by architectural blast radius — ask the ones whose answer would reshape the design (data model, service boundaries, sync-vs-async, build-vs-buy) before the ones that only tune a detail within an already-settled shape. A wrong early guess on a low-radius question costs a redo; a wrong guess on a high-radius one costs a rewrite.

## When we reach shared understanding — close it out

Before handing off to adversarial review, consolidate the resolved decision tree into two things and show them to me:

1. **Decisions table** — one row per question: `Question | Decision | Rationale`.
2. **Ready-to-paste implementation prompt** — a self-contained prompt block (in a fenced code block) that hands an implementer everything needed to build this: the decisions above, constraints, and scope, with no need to re-read the interview transcript.
