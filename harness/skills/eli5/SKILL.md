---
name: eli5
description: Explain a complex subject as an engaging, story-driven HTML artifact — big visuals, few words, real names kept and defined — adapted to a named audience, from executives to technical teams to non-technical staff. Use when the user types /eli5 followed by a topic, asks for a picture explainer or visual explainer of how something works, or wants a memo, strategy or proposal retold so a given audience can consume it and still understand it deeply — including his own files or code ("/eli5 how does this module work", "/eli5 this memo for the exec team"). Not for dense reference docs, and not for a plain-prose "explain this simply" request in chat — answer those in chat.
effort: medium  # storyboard + audience question + render-verify loop; was low when the skill was one straight artifact
---

# eli5

Build one HTML artifact that explains the topic as a story, and publish it
with the Artifact tool. Load the `artifact-design` skill first, as with any
artifact.

Topic: $ARGUMENTS. If no topic was given, ask for one and stop.

## Process at a glance

1. **Read the source until you can retell it without looking.** Never draw a
   step you are unsure of.
2. **Establish the audience and confirm the ask** (below) — one question
   call, never an interview.
3. **Pick the page's narrative shape and storyboard the beats** before writing
   any HTML: one line per beat, each naming what the reader believes or feels
   after it, which beat each drawing serves, and — for each drawing — the
   governing question it answers and its native shape from the grammar below.
4. **Build, run the Figure QA gate, run the pre-flight checks, deliver.**
   For a page that will travel beyond the user's own desk, run the two-pass
   adversarial review before it does.

## Who is reading

The default reader is the smartest person you know, on their first day on this
thing. They are under-informed, never unintelligent. Write to that person.
Never write down to anyone: no baby talk, no exclamation marks, no cartoon
mascots, no "don't worry". That register is fixed for every audience below.

### Establish the audience before you storyboard

- If the user named the audience, use it. If they did not, and the page would
  come out differently for different readers — always true for an argument-type
  source, and for anything going beyond the user's own desk — ask **one**
  question before building, with options, e.g.: executive decision-makers /
  the technical team / non-technical staff / mixed-broad. One question call,
  never an interview. Unattended, or for a quick personal explainer: default
  to the smart newcomer and say so in the delivery caption.
- **Confirm the ask in the same question call.** When the source is an
  argument, or the page will end on an ask, add a second question beside the
  audience one: propose the ask you read in the source as the recommended
  default, and let the user confirm, sharpen, or replace it. The ask is the
  page's last beat and its reason to exist — build toward the confirmed one,
  never an assumed one. If the user replaces it, the beats that build to it
  usually shift too: re-storyboard, don't just swap the last block.
- Then apply the **decision filter**: what must this reader DO with the page?
  Decide or approve → they need options, stakes, the gate, and the cost of
  waiting. Build or operate → they need mechanism, trade-offs, and failure
  modes. Live with a change → they need what changes for them, what does not,
  and what they are being asked to watch for. Depth is calibrated to the
  reader's next action, never to the topic's complexity — the topic may hold
  fifty details; this reader needs the ones their action depends on.
- **Start one level simpler than you think this audience needs.** You can add
  depth at the end; you cannot undo losing them in beat one.

### What the audience changes — and the only things it changes

| Lever | Executives / deciders | Technical team | Non-technical staff | Mixed-broad |
|---|---|---|---|---|
| The door (first beat) | The business consequence at stake | The interesting problem or surprise | What changes in their day | The lowest common stake |
| The cut (what survives) | Options, risk, cost, the gate | Mechanism, trade-offs, failure modes | Effects on their work; function over internals | The shared spine; depth deferred |
| Analogy domain | Deals, portfolios, operations | Systems they already run | Their tools and routines | Universally familiar |
| Jargon handling | Define more, keep fewer | Keep more, define less | Function words beside each name | Define like non-technical |
| The ask / close | The decision and its deadline | What to build, review, or challenge | What to do differently, whom to ask | The one thing everyone does |

For a **mixed audience**, write the body to the least technical reader present
and put the depth in the closing note — the drawings carry everyone, the
closing note carries the specialists.

What the audience **never** changes: the truth rules, the real names, the
causal chain, the page budget, the register. Same facts, different door.

## The page tells a story — pick its shape

Every page has a narrative spine; the source decides which one. Name the shape
in your storyboard before writing.

- **Mechanism chain** — "how does X work". The plot is the causal chain; the
  suspense is inevitability. Open on the question the reader already has, or
  the visible outcome that needs explaining; then let each step force the
  next. 3–4 beats.
- **Argument arc** — a strategy, memo, proposal, pitch. Retell the source's
  own storyline (see the dedicated section below). 5–7 beats.
- **Contrast engine** — a change, a choice, a "why now". Alternate what-is and
  what-could-be, widening the gap each pass, and end on the world with the
  idea adopted — which is the ask. Use when the source's whole point is that
  the present is not acceptable.
- **Cold open on one concrete instance** — when the takeaway needs context
  before it can land, open on a single person, incident, number or document
  and let the general claim grow out of it. If the takeaway is simple, do the
  reverse: state it in the first beat, spend the page earning it.

Story physics, whatever the shape:

- **Open a gap in the first thirty words.** A question, a tension, a stake —
  something the reader now needs closed. Curiosity is a gap between what the
  reader knows and what they can see they don't; the page exists to close the
  gap it opens. Never open on context or history.
- **The reader is the hero; the source is the guide.** Frame beats around what
  the reader can now see, judge, or do — not around the author or the topic.
- **Example before principle.** People learn from the specific to the general;
  show the concrete case, let the pattern surface, then name it.
- **Every story needs friction.** An obstacle, an honest admission, a place
  the plan can fail. A page with no friction reads as advertising; with it,
  it reads as trust.
- **Land the transformation.** The last beat states what the reader should now
  believe, feel, or do: a one-line recap of the chain for a mechanism page,
  the ask for an argument page.

## Rules for the page

- **Pictures carry the explanation; words support the pictures.** Use big
  inline SVG scenes, not decoration. Visual polish is a promise that the
  content is clear — only spend it on a page that is actually clear.
- **Keep the real name for every thing, and define it the first time it
  appears.** The real name is what the reader searches next. Stripping the
  vocabulary out leaves them unable to look anything up.
- **Show the causal chain.** Each step causes the next, in order. Readers
  judge an explanation by whether it makes the outcome feel inevitable — so do
  not cut a mechanism just to save words. Cut detail that carries no cause
  instead.
- **One analogy as the spine, and say where it breaks.** A single unqualified
  analogy is the main way explainers plant lasting wrong ideas: the reader
  reduces the real thing to the analogy. Borrow the analogy from the
  audience's own world, hold it through the page, then name the place it
  stops being true.
- **Say only what is true.** If a step is simplified, mark it as simplified.
  If you are unsure how something works, read the source before you draw it.
- **Budget the page, not the paragraph.** The whole page reads aloud in under
  four minutes — about 600 words of body prose, 800 the hard ceiling for a
  rich source. Check by counting the total, not by feel. Inside that budget,
  spend unevenly: beats are not equal, and a page where every section weighs
  the same has no rhythm. Keep sentences short enough to never need
  re-reading — most under 20 words, none over 30.
- **Sections are beats, and the story sets the count.** A section earns its
  place by changing what the reader believes or feels; if two sections leave
  the reader in the same state, merge them. A one-line paragraph or a
  pull-quote is a beat of its own — the cheapest source of pace on the page.
- Define a term only where the reader cannot follow the chain without it —
  three definitions on a page is plenty.
- **Detail that is not part of the chain goes in one closing note**, not in
  the body. Depth on request, never by default: a reader who wants more will
  ask. For a mixed audience this note may be a native `details`/`summary`
  block ("For the specialists") — the one permitted fold on the page.
- **A list of more than three items never rides inside a sentence.** Put the
  items in the drawing and let the paragraph say why they matter.
- If a picture needs a paragraph to decode, redraw the picture.

- **Motion, sparingly.** CSS-only animation on at most one or two figures
  whose subject is genuinely active flow, fully stilled under
  `prefers-reduced-motion`. Motion never changes the claim.

Do not add navigation, tabs, or scripted interactivity — a single scrolling
page; the optional `details` block above is the only fold.

## Words and pictures — one story, two channels

- **Complementary, never redundant.** The picture says *what*, the words say
  *why*. Never repeat in prose what the picture beside it already shows —
  readers process the two channels simultaneously, and duplication wastes
  both.
- **Each drawing advances the story exactly one beat.** A drawing that only
  decorates its section is cut; a drawing that needs two beats is split.
- **Cast the visuals once, then keep continuity.** Give the page a small
  visual cast — this shape and colour is the company, that one the system,
  that one the risk — and hold it in every drawing. The drawings then read as
  consecutive panels of one story, and the reader learns the vocabulary once.
  Draw the analogy, if you draw it, in the same cast. Every meaning a
  colour carries also gets a label or a distinct shape, and a control is
  drawn as an operational object — a stop RULE card, not a colour blob.
- **Overview first, then zoom.** For anything with parts, the first drawing
  is the map — the whole thing in one scene. Later drawings zoom into one
  part of that map, visibly consistent with it. Never make the reader
  assemble the whole from fragments.
- **At least one drawing carries the stakes**, not just the structure: a
  before/after, a closing window, what walks out the door. Structure informs;
  contrast moves.
- **Headline every figure with its takeaway, as a claim.** "The window closes
  when the walls do", never "Timeline". The caption IS the headline — one
  element, never two. Captions alone must tell the story's spine, and each
  ends on the source's own mechanism or transformed state, never on an
  external failure statistic — put a benchmark beside the beat it evidences.
- **Name the subject inside the artwork.** A reader who sees the drawing
  alone, with no caption, must be able to say what it is about: "safety designed
  in now", not "designed in now".

## Choosing the shape of each figure — the grammar

Before drawing, name the GOVERNING QUESTION the beat answers, then use that
question's native shape. The claim lives in exactly one element — the figure's
caption-headline; annotations are subordinate clarifiers and never introduce a
second conclusion. Subordinate evidence may ride along, but two independent
claims mean two figures or a split beat. Prefer the literal mechanism whenever
the source has one; a visual analogy is for when it demonstrably reduces the
reader's load.

Each mapping is conditional on what the source actually says — never fabricate
an asymmetry, a stack, or an invariant the source does not state.

1. **Bounded proportion** → unit chart of countable marks plus one oversized
   numeral, with a bracket — never a single-cell arrow — spanning the counted
   group. Large counts or precise values → the numeral with context. Close
   comparisons → aligned bars or dots. Use the dashed-threshold-plus-big-"0"
   treatment only when an explicit threshold exists and the count is truly
   zero, and label the threshold at its top.
2. **Choice with consequences** → a neutral fork with matched geometry and
   matched chip templates on both branches. Add an inevitability fact-box,
   cost values, an urgency line, or positive/negative colouring only where the
   source supports each one — never a moral adjective the source did not say.
3. **Precedence ("X before Y")** → a numbered sequence or dependency arrows. An
   ascending staircase is for genuine capability or maturity progression only,
   and then show actors stopping at DIFFERENT steps, each routed to the same
   gate.
4. **Dated commitments with stop rights** → a timeline with gate marks, static
   by default. Mark the continuation ("cleared →") as well as the stop branch.
   Encode stop and continue neutrally unless the source itself frames stopping
   as adverse.
5. **Containment** → nested regions. **Layering** → stacked bands.
   **Hierarchy** → a tree. **Dependency** → a directed node-link diagram.
   Position is the meaning. Three short labels per tile.
6. **One among many, or a scale mismatch** → draw BOTH scales — a bracket over
   a handful, a field of many — then zoom on one. **Magnitude gaps** → aligned
   quantitative scales or proportional areas; equal marks conceal the mismatch.
7. **Flow through a control** → a narrow-waist flow diagram, but only when
   universal passage through one control is a stated invariant. Show
   substitutability explicitly with a switch marker; draw trial branches
   dashed and labelled.
8. **Comparison or benchmark** → aligned dots or bars on a shared baseline.
9. **Before and after** → matched panels with delta callouts.
10. **Risk** → a likelihood-impact matrix with owner and mitigation.
11. **Accountability** → swimlanes or a RACI matrix.
12. **Trade-off** → a neutral spectrum or a two-axis frontier.
13. **Feedback loop** → a signed causal-loop diagram.
14. **Trend** → a line or slope chart.
15. **Uncertainty or scenarios** → an interval band or aligned small multiples.

For a mixed beat, the caption-headline names the governing question and
everything else is subordinate.

**Accessibility.** Every meaning a colour carries also has text or shape
redundancy. Check contrast in both themes.

**Brand styling is not part of this skill.** The page uses a neutral palette
by default. If the brief authorises a house style, read that brand's own skill
for its tokens: the light and dark token set, the visual cast, and the motion budget.

**Count the words in three buckets, and report all three.** Body prose about
600 words, 800 hard ceiling. Captions carry the claims, about 20 words each.
SVG labels about 35 words per figure. Never claim you are inside the budget
from the body count alone.

## When the source is an argument, not a mechanism

A strategy, memo, proposal or pitch is not a mechanism — it is a case someone
is making, and it usually already has a storyline. For these sources, clarity
alone fails the reader: a page that is correct but gives no reason to care
loses them as surely as jargon does. Add these rules on top of the ones above:

- **Retell the source's story; do not flatten it into a taxonomy.** If the
  document has a narrative spine, the page's sections are its beats, in the
  story's order — not a neutral catalogue of its parts. The test: could the
  original author read your page and recognise their own argument's arc?
- **Open on the stakes, not the context.** The first thing on the page names
  what is at risk or newly possible, in the reader's world. Background comes
  second, and only what the stakes require.
- **Keep the author's two or three strongest sentences verbatim** as styled
  pull-quotes, attributed to the source. Simplification should frame the
  author's voice, not replace it — the best lines of a good memo are already
  simpler than any paraphrase.
- **Keep the honest turn.** If the source earns trust by admitting something
  uncomfortable (we are behind, this might fail, the numbers are unset), that
  admission is a load-bearing beat, not detail to trim. Candour is the
  engagement device that never reads as hype.
- **Give the reader a role.** Write in we/you where the source does. End on
  what the reader is being asked to do, decide, or watch for — for an
  argument page, the ask replaces the one-line recap.
- Tension stays honest: name what happens if nothing is done, and where the
  plan is allowed to fail. An argument page with no failure mode reads as
  advertising; with one, it reads as trust.
- **Ruled wordings travel verbatim.** Quote an operative clause exactly —
  a rule, a threshold, a mandate. Compression is where drift enters.

## How to build and deliver

`artifact-design` owns layout, typography, and theme mechanics. On top of it,
two SVG rules learned from a shipped page (2026-08-28, labels near-invisible
on a phone):

- Text inside an SVG scales with the drawing, and on a phone the drawing
  renders at about half its desktop width. So keep every SVG label at
  13 units or larger in a 680-unit-wide viewBox (scale the floor in
  proportion for other widths), and prefer a few large labels over many
  small ones. A label you must shrink below the floor is a label the
  drawing does not need.
- Every fill and stroke in every SVG takes a palette variable — no literal
  colours, so the drawings follow the viewer's theme. For text on an accent
  shape, define an `--on-accent` variable instead of writing white.
- A gradient or filter on an axis-aligned straight stroke silently
  disappears. The stroke has a zero-area object bounding box, and the default
  `objectBoundingBox` units collapse against it — verified with an
  isolating test: the same gradient renders on a rect and vanishes on a
  straight line. Fix it with `gradientUnits="userSpaceOnUse"` (and
  `filterUnits="userSpaceOnUse"`) plus explicit coordinates. Never bend the
  real geometry to work around it.
- Gradient stops are the one place a literal colour is allowed. Everywhere
  else use `var()`.
- A token used as a FILL under lettering needs its own dark value that keeps
  the lettering at 4.5:1 or better. A stroke colour that reads well on the
  page background can be far too light behind white text.
- Text that crosses a border needs a panel-coloured mask rect behind it, at
  least 30 units wider than the text at Verdana widths.

**Label geometry — prevent overflow by construction, not by inspection.**

- A label inside a box clears the box by at least 8 units on each side, measured
  at Verdana widths.
- When it does not fit, wrap to two lines. Never shrink below 12 units.
- Keep every label at least 6 units inside the viewBox edge.
- Never let two labels' bounding boxes touch.
- Never run a straight line through a label.

**See the page before the user does.** Write the page to a local file first
and run the Figure QA gate below. Publish with the Artifact tool only after
the gate is clean, naming the audience in the artifact `description`.

**When the page explains an existing document, name it in the page.** Close
with a source block naming it exactly — title, version, date — so the
retelling never quietly replaces its source. Do not embed the document and do
not add a download button: inside the artifact viewer, page-initiated
downloads are inert, and an embedded PDF will not render. If the user wants
the file to travel with the page, use the artifact assets capability (load
`artifact-capabilities` first), or hand them the file beside the link.

## Figure QA gate — every figure, every time, before publishing

The verifier's fonts are not the reader's fonts. A label that fits in the
sandbox's fallback font can overflow its box on the reader's machine. This
happened on a shipped page: "added once the doors are all hung" fitted DejaVu
and spilled out of its chip in Verdana. A drawing checked only in code is not
checked — SVG text has no wrapping and no overflow warning, so the code always
looks right.

The gate has four layers. Publishing waits for zero findings in the first two
and clean shots in the third.

1. **Font-metric estimator — the primary gate.** Run
   `python3 scripts/svg_text_audit.py <page>.html` from this skill directory.
   It estimates every `<text>` width from Verdana's advance widths, then checks
   viewBox margins, box containment at 8-unit padding, label collisions, and
   straight lines crossing labels. It exits 1 on any finding. Fix each one by
   widening the box, wrapping the label, or moving the element — never by
   shrinking below 12 units — and re-run to zero. Audit at Verdana widths even
   for a page with no brand font: Verdana is the conservative bound for every
   common sans.
2. **Runtime bounding-box audit — the actual render.** With Playwright, after a
   3-second wait, take `getBBox()` of every `<text>` and repeat the same four
   checks in the browser's real metrics. This catches rotated text, wrapped
   captions, and anything the estimator cannot model. Zero findings.
3. **Read the renders.** Screenshot each `figure` element with its caption, and
   each `svg` element without, at desktop (~1280), phone (~390), dark, and
   desaturated. READ them — hunt for clipped, colliding or escaping lettering,
   colours that vanish, and scribble glyphs. Render the full page too.
4. **Reader-environment render, when available.** If Claude in Chrome is
   connected, open the file in the user's own browser and screenshot each
   figure with the reader's real fonts. Otherwise say in the delivery message
   that the fit rests on the Verdana-metric audit, and ask the user to confirm
   one figure on their screen.

The estimator lives at `scripts/svg_text_audit.py` in this skill. It is a
standalone script — no dependencies beyond the standard library.


## Pre-flight checks — run before publishing

- Count all three word buckets and report them: body prose about 600
  words (800 hard ceiling), captions about 20 words each, SVG labels
  about 35 words per figure.
- Walk the beats: each one changes what the reader believes or feels; merge
  any pair that doesn't. Weights are visibly uneven.
- The first thirty words open a gap; the last beat closes the page on the
  recap or the ask.
- Figure headlines alone still tell the story's spine.
- The analogy's breaking point is named. Definitions: three or fewer.
- Every figure names its governing question, and carries its claim in the
  caption-headline alone.
- Every colour-carried meaning also has a text or shape label; contrast
  checked in both themes.
- Audience test: this reader could say what they are supposed to do next.
- The ask on the page is the one the user confirmed, verbatim in spirit.
- Argument source: the author would recognise their arc; the honest turn and
  the strongest verbatim lines
  and the ruled wordings survived.
- No prose repeats what its neighbouring picture shows; visual cast is
  consistent across drawings; every SVG label meets the size floor.
- Figure QA gate passed: estimator 0 findings, runtime audit 0 findings,
  renders read at desktop, phone, dark and desaturated, reader-environment
  render done or its absence stated in the delivery message.
- Each figure's governing question and native shape named; the subject of
  each claim named inside its artwork; captions alone tell the spine and end
  on the mechanism or transformed state; the opening figure maps the later
  zooms.
- Existing-document source: the closing source block names it exactly.

## Two-pass adversarial review — for any page that will travel

Cross-vendor when possible. Two separate uploads in one chat, so the blind
output is preserved verbatim before the reviewer learns the intent.

- **Pass 1, blind.** Send a PDF of the caption-free artworks only. State no
  purpose. Forbid web search. The reviewer records, per figure, what it reads
  first, second and third, and the claim it infers.
- **Pass 2, informed.** Send a second PDF with the captions, the intended
  claims, and an EVIDENCE DOSSIER quoting the FULL supporting source passage
  for every visual element. Short excerpts produce false "unsupported"
  findings.

Define severity before the reviewer starts. Must-fix: the figure misleads
against the quoted evidence, or the blind read failed, or legibility blocks
comprehension. Should-fix: below professional standard, but comprehension
survives. Polish: aesthetic. Compute the verdict from the reported counts —
zero must-fix means ready; three or more redraws, or any page-level must-fix,
means not ready; anything else is ready only with changes. Require counts,
unique finding ids, a ranking with reasons, and structured provenance.

Triage every finding against the full source before you apply it. When the
reviewer's evidence was incomplete, reject the finding with the verbatim
passage and say so in the closure record.

## Why these rules

Sourced, not invented — the citations behind every rule above live in
`references/why-these-rules.md`. The Figure QA gate exists because a
fallback-font render passed and the reader's Verdana render failed; the
figure grammar and the two-pass review come from a cross-vendor adversarial
review closed. Read the reference before loosening a rule; each one
was added after a measured failure or a published study, and two (the
per-section word quota, dropped 2026-08-28; the SVG label floor, added the
same day) carry dated retrospectives.

## Home & refresh

This file is the canonical source. Derived copy: `cowork/SKILL.md` — the
Claude Desktop (Cowork) port, which swaps the Artifact tool for a
self-contained HTML file sent via SendUserFile, inlines the page mechanics
that `artifact-design` covers here, and keeps the PDF-embed delivery that
the artifact viewer cannot support. Edit here first, mirror content changes
there, then re-package per that file's own Home & refresh section.

The port is written tighter than this file — Cowork has a smaller context
budget. Treat a difference in wording as intended and a difference in a RULE
as drift to reconcile. In Cowork, a saved skill proposal REPLACES the whole
SKILL.md, so always propose the complete file, never a chapter.
