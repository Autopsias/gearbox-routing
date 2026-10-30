# The executive brief

## Contents
- [Template](#template)
- [Rules](#rules)
- [Worked example (fictional)](#worked-example-fictional)
- [Rendering](#rendering)

One page. The reader is a decision-maker who will act on the output of the
analysis, not read the analysis. The brief answers four questions in order:
what is the situation, what are the three things that matter, what do they
mean for the decision, what should happen next.

## Template

Use this exact order. Keep the headings; drop a section only when the sources
truly give it nothing, and then say "not stated" rather than deleting it.

```
# <Title: the bottom line as a sentence, not a topic>

**Bottom line.** <One sentence. The recommendation and why it matters now.>

**Situation.** <Two sentences at most. What prompted this; what is at stake.>

**What matters**
1. <Finding — so what it means for the decision.> [C01, C04]
2. <Finding — so what.> [C07]
3. <Finding — so what.> [C02, C09]

**Options**
| Option | Trade-off |
|---|---|
| A. <name> | <one line: what you gain, what you give up> |
| B. <name> | <one line> |
| C. <name> | <one line> |

**Recommendation.** <One sentence, active voice, no hedge.> Because <reason>.

**The ask.** <Decision or action>, by <owner>, by <date>.

**Risks.** <Top one or two, each with its mitigation or its owner.>

**If nothing is done.** <One sentence: the cost of waiting.>

**Conflicts in the sources.** <One line, or "none".>

Sources: S1 <title, date> · S2 <title, date> · S3 …
```

## Rules

- **Under 300 words of body.** One count, this command, nothing else:

  ```bash
  sed -e '/^#/d' -e '/^Sources:/d' brief.md | sed -E 's/\[C[0-9][^]]*\]//g' | wc -w
  ```

  The title, the headings and the sources line are out; ledger ids are
  stripped; the table, the bold labels and the conflicts line count. Three
  eval runs each counted a different way, reported 282–329, and
  all three were over 300 by this command. A brief that needs two pages has
  not chosen what matters.
- **Three findings.** More than three and nothing is key. Choose by what the
  reader's decision depends on, not by what interested you.
- **Findings carry implications.** "Acquisition cost rose 40%" is a finding.
  "At current acquisition cost the expansion budget funds half the planned
  growth" is what the executive needs. Write the second; cite the first.
- **No process.** How long the analysis took, how many sources, what you ruled
  out — that serves the writer. Cut it. One sentence of method at most, and
  only when the method changes the confidence.
- **The recommendation is made, not hedged.** "It may be worth considering" is
  a signal you have not finished. The executive can disagree; they need
  something to disagree with. Uncertainty goes in Risks with its size, not in
  the verb.
- **At most three options.** Each with one trade-off line. Do not offer an
  option the sources do not support; do not invent a middle option to look
  balanced.
- **Every number carries its ledger id** in `brief.md`. The rendered PDF and
  DOCX drop the ids and keep the sources line. The gate script checks the
  rendered copies against the ledger.
- **Hedges from the sources stay.** If the source says "expected", the brief
  says "expected".
- **Neutral register.** Not the user's voice, not marketing. Short sentences,
  active voice, plain words, the real names of things.

## Worked example (fictional)

```
# Renew the Orion contract for one year, not three, and run the exit test now

**Bottom line.** Renew Orion for one year and fund the exit test in Q1, because
the three-year price lock saves 6% and the exit test is what makes the saving
optional rather than a trap.

**Situation.** The Orion contract (S2) ends 31 March 2027. Procurement asks for
a decision on the vendor's three-year renewal by 15 November.

**What matters**
1. The three-year offer locks a 6% discount, but it also locks the per-seat
   model that drove cost up 23% in FY25 while usage fell — the discount pays
   back less than one year of that drift. [C01, C05]
2. Nobody has tested whether we can leave: the export format is proprietary
   and the last migration estimate is two years old. [C08, C11]
3. Finance assumes 4% annual growth in seats; Operations' own plan shows flat
   headcount. The two documents disagree. [C03, C12]

**Options**
| Option | Trade-off |
|---|---|
| A. Three-year renewal | 6% saved; exit cost unknown until 2030 |
| B. One-year renewal + exit test | No discount; a real exit number by Q2 |
| C. Do not renew | Forces a migration with no tested plan |

**Recommendation.** Take option B. Because a tested exit price is the only
thing that gives us leverage in 2027 or any year after.

**The ask.** Approve option B and €120k for the exit test, by the CFO, by
15 November.

**Risks.** The exit test may show migration costs above €1M — then A becomes
right, and we will still be in time to take it in 2027. Owner: Head of IT.

**If nothing is done.** The contract auto-renews for three years on
1 April 2027 at list price.

**Conflicts in the sources.** S1 assumes 4% seat growth; S4 plans flat
headcount. The brief uses S4 (newer, operational owner).

Sources: S1 Finance forecast FY26, 2026-09 · S2 Orion MSA, 2024-03 · S4
Operations plan 2027, 2026-10
```

Body word count of the example: 262.

## Rendering

`brief.html` is one A4 page: 20 mm margins, Verdana or the system sans at
10.5 pt body, the title at 18 pt, the table with hairline rules, no colour the
content does not need. No external fonts or scripts; `render.sh` loads the file
from disk. If the PDF spills to page two, cut words; never shrink below 10 pt.
