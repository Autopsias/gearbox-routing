# Changing the plan mid-run — mutation rules

Moved from SKILL.md "Changing the plan mid-run". Read it before `add-session`, `amend-session` or `retire-session`, or when one of them refuses.

A new item is passed as **JSON**, not `id|category|title`: every plan the builder stamps
today requires `prior_art` (decision + source) or `research_status` on every item, and the
pipe form has nowhere to put them — so it fails validation every time (measured).
The pipe form still works on plans built before that gate.

`--task-class` is **optional** — it never blocks the add. Omit it and `add-session` prints a
stderr warning naming the consequence: this session's outcomes are excluded from routing
proposals until amended, because `task_class` is how a routing retro groups sessions by kind
of work.

**One renderer.** These commands own no HTML assembler. They mutate `spec.json` and re-run
*plan-builder's own* renderer, carrying recorded state forward with
`article_block.carry_over_state` — so article containment, the nav strip, header counts,
"Session N of M" steps and the category sections are correct by construction. A second
assembler is exactly how the hand edits diverged. `add-session` refuses an item whose
category matches no section, refuses to take an item another session owns, and refuses any
dependency cycle (`amend-session` can create one; it is refused the same way).

**One transaction.** A mutation writes six files. `temp+rename` is atomic for *one* file,
so the write is journalled into `_mutation/<txn>/`: staging first, then a single atomic
rename of `journal.json` as the commit point, then the individual renames. A crash before
the commit point leaves the plan untouched; a crash after it leaves a journal that the
**next `run.py` command of any kind replays** — rolling forward to one complete generation,
never a mix. Replay is idempotent.

**`amend-session` is TODO-only.** A `DOING`/`DONE`/terminal session was dispatched against
the old text, so amending it would rewrite history rather than change the future. Use
`redispatch` to re-run it, or `retire-session` to drop it.

**`retire-session` has a required dependent policy.** `WONTFIX` is a member of the
executor's `DONE_STATES`, so retiring a producer *satisfies* its consumers' dependencies —
they would dispatch without the input they were written to consume. So retirement refuses
while any live (non-terminal) dependent exists, direct **or** transitive, and names both
sets. Two escapes, both recorded: `--cascade` retires the whole live sub-tree, and
`--drop-dependency` keeps the dependents, removing the retired session from their
`depends_on` **and** appending a warning to their prompts in the same transaction.

**One containment gate.** "Correct by construction" is a claim about the renderer, and a
claim is not a check — so every mutation runs `structural_gate.containment_report` over the
generation it has COMPUTED, *before* the commit point, and refuses on any problem the plan
did not already have (pre-existing ones ride out as warnings, because a defect this mutation
did not cause must not strand the plan). It checks the six surfaces the 2026-08-02 hand
edits broke — item `data-cat` vs its enclosing `<section data-cat>`, the `session-strip`
chip set, the `N sessions · M items` blurb, the `const WORKSTREAMS`/`PILLARS`/… array the
progress bar counts through, session document order (what the "Up next" panel actually
scans), and the header totals — plus manifest⇄article membership both ways and
`parallel_group` depends_on symmetry (asymmetric deps make peers ready at different times,
so the batch silently degrades to sequential dispatch). Same report rides along as WARNINGS
on every `apply` / `verify-finalize`, where it is deliberately non-blocking.

**Refusals you will meet.** The halt flag; a live **dispatch lock** (a batch is in flight —
restructuring would clobber its status writes); **shipping mid-flight** (`_shipping_state`
binds to the manifest digest, so mutating now would make that run refuse as `state-drift`
and halt the plan); **recorded state mid-flight** — an unsettled `_verify_state/<sid>.json`
(no outcome yet, or `rework` awaiting the re-dispatch) or a persisted-but-unreplayed
`_closeouts/<sid>.json`, for the same digest reason; and **`builder_drift`** — if re-rendering with today's plan-builder
would change the page even with *no* mutation applied, the command prints the diff and
requires `--allow-builder-drift`, so a builder-version upgrade can never ride in
unannounced. A plan with no `spec.json` is refused outright: there is no second renderer.

Every mutation appends one line to `_changelog.ndjson` (`{at, op, session, summary,
sessions_touched, items_touched}`) **and one dated row to the rendered "Plan changes" section
at the bottom of `PLAN.html`** — so the plan stays a self-contained record of why it changed
instead of pointing at a sibling file. `redispatch` and a REPLAN resolution write there too.
Plans built before the section exists have no anchors for it; every writer is a tolerant
no-op there, and the record still lands in `_changelog.ndjson`.
A new item is also placed in a Plan Achievement group (defaulting to the group named after
its category, `--infographic-group` to override); when nothing matches, the containment gate
REFUSES the mutation and names the flag to re-run with, rather than shipping a progress bar
that counts a short denominator (the 2026-08-02 bar read 92% · 12-of-13 against a real
70% · 19-of-27).

Settled verify state (`passed`/`halted`) is **migrated forward onto the new manifest digest
in the same transaction, `rework_count` preserved** — otherwise the operator's only way past
the resulting `state-drift` refusal is to delete the file, which hands a session that had
already exhausted `max_rework` a silent fresh budget.
