# Real pages: how to read Stageshow for a page people use

Stageshow is written for faux product scenes. This file says how each part of it reads for a
real page: an app screen, a site, a dashboard, an HTML report or artifact. `$V` is
`~/.claude/skills/ui-craft/vendor/stageshow`. Every `file` § "Heading" below is checked against
the pinned copy by `scripts/check_refs.py`, so a renamed upstream section fails loudly.

## Contents
- Words that change meaning
- craft.md, section by section
- verify.md
- review.md
- Severity
- Design ledger
- What a real page needs that Stageshow does not cover

## Words that change meaning

| Stageshow says | On a real page, read |
|---|---|
| the story, the beat, the payoff | what the page is for, and the next action its reader takes |
| the stage, the crop | the viewport; the first screen is the crop |
| the gold example | the closest good page: an outside reference that a DIRECTION review chose, the project's own best page, or a Stageshow gold when the page shows a product UI |
| `Bar`, placeholder, skeleton, `fidelity` | nothing: a real page has real content, so skip every rule about them |
| film, `film.mjs`, the gate | `scripts/shoot.mjs` frames and `$V/scripts/layout.mjs` |
| `compact` | the phone layout: a second design, not a squeeze |

## craft.md, section by section

Read `$V/references/craft.md` in full first. Then apply it with these readings:

- `craft.md` § "Restraint": applies, except the rules about bars, placeholders, a fictional
  product and its name. Real pages break "Say it once", "One number gets to be big",
  "Simplify before polishing" and "Space follows the story" most (Kuzushi, 2026-10-23:
  progress shown four times on one page).
- `craft.md` § "Type": applies at real sizes: body 16px (15 to 18), small text never under
  12px, prose line height 1.4 to 1.6, prose lines 60 to 75 characters. Reverse "No real
  headings": a real page uses `h1` to `h6` in order, one `h1`.
- `craft.md` § "Spacing and alignment": applies as written.
- `craft.md` § "Icons and logos": applies. "A set chosen on purpose" means the project's own
  set first. `BrandMark` is kit-only; use the project's assets.
- `craft.md` § "Color and looks": applies. "Build once, check every look" means every theme
  the page ships (light and dark): shoot each.
- `craft.md` § "Density": applies; the page works down to 320px wide.
- `craft.md` § "Chrome and devices": skip, except "Columns run the full height of their
  window" (an app sidebar) and "Never draw platform chrome from memory".
- `craft.md` § "A page of demos": only for a page that shows several demos.
- `craft.md` § "Recipes": "Check a fresh load at each width and a resize between" applies;
  the rest is kit CSS.

## verify.md

- `verify.md` § "Gate": replaced by `shoot.mjs` at 1366 and 390 (and any width the brief
  names) and `layout.mjs` at both widths. Exit 2 from either means "not checked", never clean.
- `verify.md` § "Framing": does the first screen show what the page is for and its next
  action? Is anything cut, wrapped badly or under a fixed bar? Any empty band or corner for no
  reason? On the phone: no horizontal scroll (`shoot.mjs` prints it).
- `verify.md` § "Hierarchy and density": applies; skip the lines about bars, avatars or date
  ranges when the page has none.
- `verify.md` § "Motion": only for animated UI.
- `verify.md` § "Polish": applies; "nothing under 10px on the phone" becomes 12px.
- `verify.md` § "What you see, what you do": applies; it is the fix table.
- `verify.md` § "Hand over": skip `REHEARSAL.md`. The hand-over is the marked pictures and
  the finding list (SKILL.md, REVIEW step 7).

## review.md

Use `review.md` § "The reviewer's brief" with "a faux product UI scene" read as "the page",
and the gold read as the closest good page. The first question applies unchanged: could this
layout be any product in its category? Give the reviewer this file too. The brief's whole
ranked list stays: Severity below gives each finding a severity and drops none.

## Severity

Every finding gets one severity. The severity decides the order and when the rounds stop; it
never removes a finding from the report.

| Severity | When | What happens |
|---|---|---|
| blocking | it stops a task, misleads (a wrong count, a promise the page breaks), looks broken (cut, overlapping, unreadable), fails an access rule (contrast, tap size, focus ring), or hides the page's next action | fixed in this review; a new one keeps the rounds open |
| polish | any other rule in `craft.md` or `verify.md`: spacing, alignment, rhythm, type, small hierarchy | reported in its own ranked list; the owner decides; never keeps the rounds open |
| structural | the page's structure does not serve its job: the first screen puts the wrong thing first, one block dominates on every visit, two parts compete for the next action | never put in a fix brief; offer the owner a DIRECTION review |

## Design ledger

One file in the project, `design-ledger.md` at the repository root (or where the project
keeps its design notes), so each review knows what earlier reviews found. It is a working note
for the builders; never ship it with the product. One table row per finding:

| id | date | page | finding (one line) | severity | state |
|---|---|---|---|---|---|
| D-001 | 2026-11-08 | week page, phone | the tutor blocks push the items off the first screen | structural | open |

The state is `open`, `fixed in <PR>`, `decision <where it is recorded>`, or
`rejected <why>`. REVIEW step 6 matches each new finding against the ledger first: a match is
known. Add every finding that survives step 5; change its state when its fix merges.

## What a real page needs that Stageshow does not cover

- Contrast measured, not judged by eye: 4.5:1 for body text, 3:1 for large text and for the
  edges of controls (WCAG 2.2 AA). Read the computed colours in the browser.
- A visible focus ring on every control. A tap target of at least 44px on a phone, never
  under 24px (WCAG 2.2, 2.5.8).
- Real states: empty, loading, error, very long names, the first visit and the hundredth.
  Shoot the states a reader reaches, with seeded data (`shoot.mjs --init`).
- Help text a reader needs once goes behind a fold after the first visit (Kuzushi).
- A project's own design system wins on tokens and components; these rules decide how to use
  them.
