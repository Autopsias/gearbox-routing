# The Pretty-But-Wrong checklist (TR-04)

**The reviewer's standing skeptic's reference.** AI can make a workbook or a deck
*look* finished long before it is *right*. This is the checklist the two-context
reviewer runs **every pass**, in addition to judging the artifact against its
provenance record (`conventions.md` §2). It names the defect classes that survive
a casual read — "pretty but wrong" — so they get raised instead of shipped.

**Canonical source.** The machine-readable list lives in
`skills/trust-review/scripts/review_contract.py` as `PRETTY_BUT_WRONG`, rendered
into the reviewer prompt by `checklist_text(surface)` and wired into the loop via
`build_reviewer_prompt(..., checklist=...)`. This file is the human-readable copy;
if the two ever disagree, the code is canonical (the fixture asserts they match).

**Severity is the conventions enum, verbatim** (`must-fix` / `should-fix` /
`polish`, `conventions.md` §1.2). Each item carries a **default severity** that
anchors triage and lets the M1 release benchmark (`hr-00`, S08) score severity
accuracy against a known key; the reviewer still raises the *actual* severity per
finding (a default-`should-fix` undated number on a board figure can be a
`must-fix` in context).

**Surface** says where an item applies: `excel` (workbook), `ppt` (deck/slides),
or `both`. `checklist_text("excel")` returns excel+both items; `("ppt")` returns
ppt+both; the default `("both")` returns all ten.

| id | surface | type | default | what it catches |
|---|---|---|---|---|
| PBW-01 | both | unsupported-claim | **must-fix** | A claim / number stated as fact with **no** supporting source in the provenance. Unsupported once it travels. |
| PBW-02 | both | undated-number | should-fix | A figure with no as-of date / period label — the reader can't tell what it's as of, so it silently goes stale. |
| PBW-03 | ppt | untraceable-chart | **must-fix** | A chart / graphic whose numbers can't be traced to a source or a workbook cell. Looks authoritative, can't be checked. |
| PBW-04 | excel | formula-inconsistency | **must-fix** | A formula inconsistent across a row/column, **or** a roll-forward fault (shape-consistent but consistently *wrong* — the trap a consistency check passes). |
| PBW-05 | excel | hardcode-in-calc-zone | **must-fix** | A hardcoded constant where a formula/reference belongs — a magic number baked into the calc zone that no longer rolls forward. |
| PBW-06 | both | assumption-as-fact | **must-fix** | A load-bearing **assumption** presented as established fact — no owner, no "assumes", no flag that it's a judgement call. |
| PBW-07 | both | stale-date | should-fix | A source / figure whose as-of date is older than the freshness window for the decision — last quarter's number on this quarter's page. |
| PBW-08 | ppt | brand-drift | should-fix | Off-brand fonts / colours / logo / footer — drift from the house visual identity that signals a rushed or copy-pasted slide. |
| PBW-09 | ppt | low-contrast-or-overcrowded | polish | Low-contrast text (fails legibility) or an overcrowded slide (too many points / tiny type) — the message doesn't land. |
| PBW-10 | both | broken-narrative-logic | should-fix | The narrative doesn't follow — a headline its body doesn't support, a conclusion the data doesn't reach, a non-sequitur between slides/tabs. |

These ten are the plan's `tr-04` enumeration: claims without source · numbers
without date · untraceable charts · inconsistent / roll-forward formula faults ·
hardcodes · assumptions-as-facts · stale dates · brand drift · low-contrast /
overcrowded slides · broken narrative logic.
