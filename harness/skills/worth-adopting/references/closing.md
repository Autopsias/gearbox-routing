# Closing a run: report, ledger row, reply, and what the owner does next

Read this file at step 7 of `SKILL.md`, and again when the owner answers the decision card.

## Contents
- Report and ledger row
- Learn from what the owner does next
- Gotchas (from real runs)

## Report and ledger row

Copy `assets/report-template.md` to `<target>/docs/worth-adopting/<YYYY-MM-DD>-<slug>.md` and
fill it. Add one row with `wa_ledger.py add-row < row.txt` (create the file from
`assets/ledger-header.md` on the first run): it puts the row at the end of the table, above
the overrides, and refuses a row without nine cells or one whose report skips the big picture. Do not commit before the owner answers
the decision card. After the answer (at once when there is no card), put this run's record in
one pull request from the step 1 worktree: commit the record there first (a rebase refuses uncommitted files), then rebase it right before the push with `git -C <target> fetch -q && git -C <target> rebase origin/main` (on a LEDGER.md conflict keep both rows; 2026-11-20: a same-day row on main refused the merge): the report, this run's ledger lines and
any measure script, and nothing of another run (2026-11-17: two runs left their lines
uncommitted in one ledger, and the main checkout could not fast-forward).

Keep the report to one page. Each bullet is one or two lines, a DROP is one line, and a point
appears once: the first test runs wrote 1,100 words for one TRIAL, and the length was most of
the extra run time. The report is a record for the next run, not an essay for the owner.

A row with no ADOPT and no TRIAL gets `none needed` as its decision and its result.

**Close open outcomes.** A verdict teaches nothing until its outcome is on record. After the
report, take at most three older rows, the oldest first, whose decision or result is `open`,
or whose `due` date is past (`wa_ledger.py open` lists them). A due stated as "after the next N plans, scans or sessions" is
past when the count is met or 14 days have gone by. For each, do only the cheap checks: does the path exist, is the
part wired, did the measurement run (a log, a saved number, a scheduled job). Write what you
find into the row, with the date (`wa_ledger.py note '<date or source>' "Checked <date>: <…>"`). If a measurement needs real work, do not do it now. Owed
once, the chat reply names it; already marked owed, it becomes a decision-card option.

Then reply in the chat, in plain words, in about 250 words when there is something to decide
and in a few lines when there is not, in this order:

1. One sentence with the result: how many proposals, how many worth action, and the largest one.
2. Each ADOPT and TRIAL in two or three lines: what gets better, the smallest form, the carry cost.
3. One line that counts the PARK and DROP verdicts, says what the drop challenge changed, and
   gives the report path. Then the principles, one line each: the stand, with its number, and
   the direction.
4. A decision card, only if an ADOPT, a TRIAL or a twice-owed measurement exists. Use the
   briefing format of `CLAUDE.md` (the template has its four parts): three options at most, and only
   options that you checked are executable.
5. One last line for each measurement owed for the first time ("Owed: the compaction re-measure
   from 2026-09-19 has not run").

Do not paste the report into the chat. Write for a reader who did not see the source and does
not know the file names: say the effect first ("sessions stop ending with work half done"),
and give the mechanism only where it changes the decision.

## Learn from what the owner does next

The skill improves only through what is written down, so write down two things.

- **The decision.** When the owner answers the decision card, put the answer in the row
  (`wa_ledger.py note '<date or source>' "planned (<plan>)" --field decision`):
  `built`, `planned`, `declined`. A later run closes what stays `open` (step 1). ALWAYS run the
  skill a picked option names (for example `/grill-with-docs`): the owner chose that method, and
  output you write yourself in its place is not what he approved; if you cannot run it, say so
  before you build (2026-11-21: a glossary written without the promised question pass).
- **An override.** When the owner changes or questions a verdict, a rank, or the reach of a
  proposal, add one line under "Owner overrides" (`wa_ledger.py add-override '<line>'`): what
  the run said in a few words, then the owner's own words. Nothing else goes in that line. Do this at once, before you act. The
  words are the evidence, and a paraphrase loses them. An override often asks for one more
  check (step 6), not for a new verdict: do the check, then keep or change the verdict, and
  say which in the reply.

Do not edit this skill during a run. When the ledger has eight or more rows after the last
`retro` row, or three or more overrides that are not marked `absorbed` (`wa_ledger.py retro-due`
counts both), add one line to the chat reply: "Enough outcomes for a retro: `/worth-adopting retro`." The retro turns the record
into at most three proposed edits of this skill, and the owner approves each one. Its steps
are in `references/retro.md`; read that file only for a retro.

## Gotchas (from real runs)

- **2026-09-19, model-choice video.** The proposal reached past the pain: it changed the
  effort of sessions that the owner sets by hand, and the owner wanted only the defaults for
  spawned agents changed. Keep the reach of a proposal inside the pain it answers, and never
  override a choice that the owner makes by hand.
- **2026-09-21, first test runs.** A harness guard refuses a subagent's `Write` of a file
  named as a report ("Subagents should return findings as text"). Run this skill in the main
  session. An agent that an orchestrator dispatches returns the report as text, and the
  orchestrator saves it. Do not write the file another way to get past the guard.
- **2026-09-19, paywalled blog post.** Search results carried a block of text written as an
  automated reminder to the agent. The run reported it and did not act on it. The same run
  named the paywall and marked which parts came from summaries by other people.
