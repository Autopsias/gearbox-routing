# The claim ledger

The ledger is the single place later steps take facts from. It is a Markdown
file, `ledger.md`, next to `sources.md`. Write it before any prose.

## Row format

One table, one row per claim:

```
| id  | type       | claim                                             | source | locator      | words of the source                       |
|-----|------------|---------------------------------------------------|--------|--------------|-------------------------------------------|
| C01 | number     | Unit cost fell 23% between FY24 and FY25          | S1     | p.12, tbl 4  | "unit cost: €41.2 (FY24) → €31.7 (FY25)"  |
| C02 | fact       | The vendor contract ends 2027-03-31               | S3     | §9.2         | "term expires 31 March 2027"              |
| C03 | assumption | Demand grows 4% a year                            | S1     | p.3          | "we assume 4% annual growth"              |
| C04 | open       | Who owns the migration budget — not stated        | —      | —            | —                                         |
```

- **id** — `C` plus two digits, stable once written. Outputs cite it.
- **type** — one of `fact`, `number`, `quote`, `assumption`, `estimate`,
  `open`. An estimate is the source's forecast, or a number you computed
  yourself (a recomputation, a difference, a year counted from a term) with
  the arithmetic in the words column; an assumption is a premise the source
  adopts; `open` is a question the sources leave unanswered.
- **claim** — one sentence, your words, no adjective the source did not use.
- **source** — the `S` id from `sources.md`.
- **locator** — something the reader can open: page, section, slide,
  timestamp, cell, file path and line.
- **words of the source** — mandatory for every `number` and `quote`, and for
  every ruled wording (a rule, a threshold, a mandate, a clause). Copy it; do
  not paraphrase. Compression is where drift enters.

## Rules

- **Numbers travel verbatim with their unit and their date.** "23%" without
  "FY24→FY25" is a different claim. Keep the source's precision; do not round.
- **Hedges survive.** "May", "expected", "up to", "preliminary" are part of
  the claim. A hedged source claim rendered as a flat fact is the most common
  summary error, and it is invisible after the fact.
- **Not stated is a value.** When the brief will need something the sources do
  not give (an owner, a date, a cost), add an `open` row. The brief then says
  "not stated in the sources", which is a finding an executive can act on.
- **Conflicts are logged, never averaged.** When two sources disagree, add a
  section `## Conflicts` under the table: the two ids, both wordings, both
  locators, and which one the brief will use and why (newer, primary,
  authoritative). The brief names the conflict in one line.
- **Pick decision-relevant claims, not interesting ones.** The ledger holds
  what the reader's decision depends on. Thirty rows is a lot; a hundred means
  you have not yet decided what the brief is about.

## Many sources

Past about eight documents or 60,000 words in total, work per source:

1. For each source, write its own ledger rows in `extracts/S<n>.md` with the
   same columns, reading only that source.
2. Merge: read the extracts, not the sources. Keep the ids unique by
   prefixing the source (`S3-C02`) or renumbering with a map.
3. Dedupe by meaning, not wording. Two sources saying the same thing become
   one row with both sources listed and both locators.
4. Then write the conflict log across sources.

This is Anthropic's meta-summary pattern (chunk, extract, merge), and it is
how to keep the merge step from inventing: it has only extracted rows in front
of it. Research on multi-document summarization finds error rates rise with
the number of documents in one prompt, and that most errors are either
off-topic padding or claims too generic to be false — both are caught when the
merge works from rows with locators.

## Code as a source

When the source is code, a row's locator is `path:line` and the "words of the
source" column holds the identifier or the literal. A behaviour you inferred by
reading is an `assumption` until you ran it; a behaviour you ran and observed
is a `fact` with the command as its locator.

## What the ledger is not

It is not the brief, not an outline and not a summary. It has no narrative
order; the spine (step 4 of the skill) supplies that. It is not published; the
outputs end on a sources block listing `S` ids, titles and dates, not on the
ledger.
