# Retro: turn outcomes and overrides into edits of this skill

Run this when the owner types `/worth-adopting retro`, or accepts the offer that step 7 makes.
The retro proposes. The owner approves. Nothing in the skill changes without a yes.

## 1. Read the record

Read all of `<target>/docs/worth-adopting/LEDGER.md`: the table, and the section "Owner
overrides". Work only on rows and overrides after the last row whose source is `retro`.

## 2. Count four things

Give each as "n of m", with the rows behind it.

- **Acted on:** ADOPT and TRIAL verdicts where the owner's decision is "built" or "planned".
  A low share means the bar passes proposals that the owner does not want.
- **Delivered:** built items whose result shows the gain that the report promised. A low
  share means question 1 of the bar accepts pains that are not real, or gains that nobody
  can measure.
- **Overridden:** verdicts, ranks or reaches that the owner changed. Group them by kind.
- **Came back:** DROP or PARK verdicts that the owner, or a later source, raised again.
  This is the only sign that the bar is too strict.

Do not draw a pattern from one event. Two events of the same kind are a pattern worth a line
in the skill. One event stays in the record.

## 3. Propose at most three edits

Order them by how many events stand behind each. For each edit give: the events (date, source,
the owner's words), the exact text to add, change or delete, and the file (`SKILL.md` bar or
gotchas, `references/getting-the-source.md`, `assets/report-template.md`).

Prefer an edit that deletes or sharpens existing text over an edit that adds a rule. The skill
has a size limit (300 lines), and a rule that no event supports any more is a candidate for
deletion. If the counts show nothing, say "no edit", and stop.

A preference of the owner that applies outside this skill ("never change what I set by hand")
belongs in memory as feedback, not in this skill. Propose that in place of a skill edit.

## 4. Apply what the owner approves

Edit the skill in its source repo (`~/your-private-harness/skills/worth-adopting/`), never in the
deployed copy under `~/.claude`. Walk the skill-quality rubric
(`skills/write-a-skill/references/skill-quality-rubric.md`) over the changed file. Run
the repo's skill tests, if it has any. Do not commit; the owner decides that.

## 5. Close the record

Mark each override that an edit absorbed with `absorbed <date>` at the end of its line. Add one
ledger row with the source `retro`, the four counts, and the edits applied. The next retro
starts after this row.
