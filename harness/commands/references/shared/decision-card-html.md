# Decision-card HTML — the reply-assembling one-pager

A recipe for shipping a report as a single self-contained HTML artifact the
user can act on directly, instead of a wall of chat prose. Companion to
`findings-contract.md` (what a finding IS) — this is how to render findings
when the output is decision-shaped.

## When to use

The report has (a) a small number of comparable options/verdicts and (b) an
action the user will take in response (accept/reject/tweak). Not for pure
narrative summaries — those stay as chat text or a plain doc.

## Shape

One `.html` file, no external requests (fonts/scripts/images all inline or
system fonts — CSP-safe, works offline):

1. **Headline decision card, top of page, at most 3 options.** Mirrors the
   global eval-cycle rule: never a 4th option, never "it depends" prose in
   place of a pick. If there are more than 3 real options, that's the
   headline card's job to have already cut it down.
2. **Per-finding/verdict cards below** — one card per finding, each with three
   chips: **Accept / Reject / Modify**. Clicking a chip is a local JS state
   change (no network), toggling a `data-verdict` attribute and visual state.
   "Modify" reveals an inline text field for a one-line override.
3. **Footer textarea** that assembles a plain-text reply from whatever chips
   are currently selected (accepted findings + modify text), regenerated live
   on every click. **Copy button** next to it (`navigator.clipboard.writeText`,
   no fallback needed for a local artifact).
4. **Data inline** — embed the findings as a JS object/array in a `<script>`
   block, not fetched. The whole file is the payload.
5. **Light + dark** via `@media (prefers-color-scheme: dark)`; optional manual
   toggle is a nice-to-have, not required.

## Vault boundary (your-vault sessions)

HTML artifacts from this pattern **never enter your-vault typed zones**
(`10 People/` … `70 Decisions/`). Legal homes: plan session dirs
(`_plans/<slug>/_evidence/...`), `99 Workspace/` scratch, or `~/.claude/`.
The vault's typed zones are markdown-only knowledge; a decision-card HTML is
a transient interaction surface, not vault content.

## Worked example

`_plans/<your-plan>-<date>/_evidence/s01/articles-to-harness.html`
— built from Thariq's HTML-effectiveness demos (steal/skip chips, resonate
checkboxes, self-filling reply templates). Read it before building a new one;
most of this recipe is already correct there and can be copy-adapted rather
than re-derived.

## Non-goals

Not a framework, not a build step, not a component library. One file, hand-
written per report, under ~150 lines of HTML+CSS+JS total. If a report needs
more structure than 3 headline options + N verdict cards, it's not decision-
shaped — ship it as prose or a table instead.
