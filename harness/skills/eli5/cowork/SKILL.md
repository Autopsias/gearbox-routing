---
name: eli5
description: "Explain a complex subject as an engaging, story-driven HTML page — big visuals, few words, real names kept and defined — adapted to a named audience, from executives to technical teams to non-technical staff. Use when the user types /eli5 followed by a topic, asks for a picture explainer or visual explainer of how something works, or wants a memo, strategy or proposal retold so a given audience can consume it and still understand it deeply — including his own files or code. Not for dense reference docs, and not for a plain-prose explain-this-simply request in chat."
---

# eli5 (Cowork port) — v4.1, complete

Build one self-contained HTML page that explains the topic as a story, write it
to a file, and send it to the user with `SendUserFile` (`display: "render"`).

Topic: whatever the user asked to explain. If no topic was given, ask for one
and stop.

## Process at a glance

1. **Read the source until you can retell it without looking.** Never draw a
   step you are unsure of.
2. **Establish the audience and confirm the ask** (below) — one question
   call, never an interview.
3. **Pick the page's narrative shape and storyboard the beats** before writing
   any HTML: one line per beat, each naming what the reader believes or feels
   after it, which beat each drawing serves, and — for each drawing — the
   governing question it answers and the native shape from the grammar below.
4. **Build, run the Figure QA gate, run the pre-flight checks, deliver.** For a
   page that will travel beyond the user's desk, run the two-pass adversarial
   review before it does.

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
  default, and let the user confirm, sharpen, or replace it. If the user
  replaces it, re-storyboard — don't just swap the last block.
- Then apply the **decision filter**: what must this reader DO with the page?
  Decide or approve → options, stakes, the gate, the cost of waiting. Build or
  operate → mechanism, trade-offs, failure modes. Live with a change → what
  changes for them, what does not, what to watch for. Depth is calibrated to
  the reader's next action, never to the topic's complexity.
- **Start one level simpler than you think this audience needs.**

### What the audience changes — and the only things it changes

| Lever | Executives / deciders | Technical team | Non-technical staff | Mixed-broad |
|---|---|---|---|---|
| The door (first beat) | The business consequence at stake | The interesting problem or surprise | What changes in their day | The lowest common stake |
| The cut (what survives) | Options, risk, cost, the gate | Mechanism, trade-offs, failure modes | Effects on their work; function over internals | The shared spine; depth deferred |
| Analogy domain | Deals, portfolios, operations | Systems they already run | Their tools and routines | Universally familiar |
| Jargon handling | Define more, keep fewer | Keep more, define less | Function words beside each name | Define like non-technical |
| The ask / close | The decision and its deadline | What to build, review, or challenge | What to do differently, whom to ask | The one thing everyone does |

For a **mixed audience**, write the body to the least technical reader present
and put the depth in the closing note. What the audience **never** changes:
the truth rules, the real names, the causal chain, the page budget, the
register.

## The page tells a story — pick its shape

- **Mechanism chain** — "how does X work". The plot is the causal chain. 3–4 beats.
- **Argument arc** — a strategy, memo, proposal, pitch. Retell the source's
  own storyline. 5–7 beats.
- **Contrast engine** — a change, a choice, a "why now". Alternate what-is and
  what-could-be and end on the world with the idea adopted.
- **Cold open on one concrete instance** — when the takeaway needs context
  before it can land.

Story physics: open a gap in the first thirty words; the reader is the hero,
the source is the guide; example before principle; every story needs friction;
land the transformation in the last beat.

## Rules for the page

- **Pictures carry the explanation; words support the pictures.** Big inline
  SVG scenes, not decoration.
- **Keep the real name for every thing, and define it the first time.**
- **Show the causal chain.** Cut detail that carries no cause, never mechanism.
- **Prefer the literal mechanism; use an analogy only when it earns its
  place.** Draw the real thing whenever the source has one. If you use an
  analogy, use ONE, borrow it from the audience's world, hold it through the
  page in prose, and name where it stops being true. Never draw the analogy
  when a decision, count, sequence or structure can be drawn directly.
- **Say only what is true.** Every annotation sentence must be traceable to
  the source; when in doubt, quote the source's own words.
- **Budget the page in three buckets and report all three:** body prose about
  600 words (800 hard ceiling); captions ≤ ~20 words each; SVG labels ≤ ~35
  words per figure. Sentences mostly under 20 words, none over 30.
- **Sections are beats, and the story sets the count.**
- Three definitions on a page is plenty.
- **Detail not part of the chain goes in one closing note** — for a mixed
  audience a `details`/`summary` block, the one permitted fold.
- **A list of more than three items never rides inside a sentence.**
- If a picture needs a paragraph to decode, redraw the picture.
- **Motion, sparingly.** No navigation, tabs, or scripted interactivity. CSS-only
  animation on at most one or two figures whose subject is genuinely active
  flow, fully stilled under `prefers-reduced-motion`. Motion never changes
  the claim.

## Words and pictures — one story, two channels

- **Complementary, never redundant.** Never repeat in prose what the picture
  beside it shows.
- **Each drawing advances the story exactly one beat.**
- **Cast the visuals once, then keep continuity.** Every colour-carried
  meaning also gets a label or a distinct shape; controls are drawn as
  operational objects (a stop RULE card), never colour blobs.
- **Overview first, then zoom.**
- **At least one drawing carries the stakes.**
- **Headline every figure with its takeaway, as a claim.** The caption IS the
  headline — one element. Captions alone must tell the spine, ending on the
  source's mechanism or transformed state, never on an external failure
  statistic (put a benchmark beside the beat it evidences).
- **Name the subject inside the artwork.** A blind reader of the drawing alone
  must be able to say what it is about ("safety designed in now", not "designed
  in now").

## Translating ideas into figures — the grammar

Name the beat's GOVERNING QUESTION, then use its native shape. One governing
question per figure; subordinate evidence may ride along; a second independent
claim means splitting the beat. Each mapping is conditional on what the source
actually says.

1. **Bounded proportion** → unit chart + oversized numeral, a bracket (never a
   single-cell arrow) spanning the counted group. Large counts → numeral with
   context. Close comparisons → aligned bars/dots. Threshold-plus-big-"0"
   ONLY when an explicit threshold exists and the count is truly zero; label
   the threshold at its top.
2. **Choice with consequences** → a neutral fork, matched geometry, matched
   chip templates. Inevitability box, cost values, urgency line,
   positive/negative colouring only when the source supports each — never a
   moral adjective the source did not say.
3. **Precedence ("X before Y")** → numbered sequence or dependency arrows. A
   staircase ONLY for genuine maturity progression, and then show actors
   stopping at DIFFERENT steps, each routed to the same gate.
4. **Dated commitments with stop rights** → timeline with gate marks; mark
   the continuation ("cleared →") as well as the stop branch; stop encoded
   neutrally unless the source frames it as adverse.
5. **Containment** → nested regions · **layering** → stacked bands ·
   **hierarchy** → a tree · **dependency** → directed node-link. Position is
   the meaning; never assert stacking or containment the source does not
   state; three short labels per tile.
6. **One-among-many / scale mismatch** → draw BOTH scales (a bracket over a
   handful, a field of many), then zoom on one. Magnitude gaps → aligned
   scales or proportional areas.
7. **Flow through a control** → narrow waist ONLY for a stated invariant;
   show substitutability explicitly (a switch marker); trial branches dashed
   and labelled.
8. **Comparison / benchmark** → aligned dots or bars on a shared baseline.
9. **Before / after** → matched panels with delta callouts.
10. **Risk** → likelihood–impact matrix with owner and mitigation.
11. **Accountability** → swimlanes or a RACI matrix.
12. **Trade-off** → neutral spectrum or two-axis frontier.
13. **Feedback loop** → signed causal-loop diagram.
14. **Trend** → line or slope chart.
15. **Uncertainty / scenarios** → interval band or small multiples.

## When the source is an argument, not a mechanism

Retell the source's story, not a taxonomy; open on the stakes; keep the
author's two or three strongest sentences verbatim as pull-quotes; keep the
honest turn; give the reader a role; keep the tension honest. **Ruled wordings
travel verbatim** — quote the operative clause exactly; compression is where
drift enters.

## How to build the file

1. ONE complete HTML file — `<!doctype html>` through `</html>` — as
   `eli5-<topic-slug>.html`.
2. **Self-contained, no network.** One `<style>` block; every picture an
   inline `<svg>`; system font stacks only.
3. **Readable on any screen.** One column, `max-width` ~720px; every `<svg>`
   has a `viewBox` and `width:100%; height:auto`; every SVG label ≥ 13 units
   in a 680-wide viewBox.
4. **Both themes.** Light palette on `:root`, dark redefinitions in
   `@media (prefers-color-scheme: dark)`; explicit `body` background and
   colour; every SVG fill/stroke via `var(--…)` except gradient stops (literal
   brand values allowed). A token used as a FILL under lettering needs its
   own dark value keeping the lettering at 4.5:1 or better.
5. A short `<title>` — a name, not a summary.
6. **SVG mechanics that bite.** Axis-aligned straight strokes have a zero-area
   object bounding box: gradients AND filters with default objectBoundingBox
   units collapse silently — use `gradientUnits`/`filterUnits="userSpaceOnUse"`
   with explicit coordinates; never bend real geometry. Text crossing a
   border needs a panel-coloured mask rect at least 30 units wider than the
   text at Verdana widths.
7. **Label geometry rules (prevent overflow by construction):** a label inside
   a box must clear the box by ≥ 8 units on each side AT VERDANA WIDTHS; when
   it does not, wrap to two lines (never shrink below 12 units); keep 6 units
   inside the viewBox edge; never let two labels' boxes touch; never run a
   straight line through a label.
8. **When the page explains an existing document, put the document IN the
   page:** closing source block naming title, version, date; the PDF embedded
   as a base64 `data:` URI in a download anchor and an `object` viewer inside a
   `details` fold (embed under ~10MB). Never invent a URL.
9. Deliver with `SendUserFile` (`display: "render"`) and a one-line caption
   naming topic and audience. Confidential sources stay file-only — never a
   hosted page.

## Figure QA gate — every figure, every time, before delivery

The verifier's fonts are not the reader's fonts. A label that fits in the
sandbox's fallback font can overflow its box on the reader's machine (this
happened: "added once the doors are all hung" fit DejaVu and spilled out of its
chip in Verdana). So the gate has four layers, and delivery waits for zero
findings in the first two and clean shots in the third:

1. **Font-metric estimator (font-agnostic, the primary gate).** Run the audit
   script below on the page; it estimates every `<text>` width from Verdana's
   advance widths and checks viewBox margins, box containment (8-unit
   padding), label collisions, and straight lines crossing labels. Fix every
   finding by widening the box, wrapping the label, or moving the element —
   never by shrinking below 12 units — and re-run to zero. Audit at Verdana widths
   whatever font the page uses: it is the conservative bound for every
   common sans.
2. **Runtime bounding-box audit (actual render).** With Playwright, after a
   3-second wait, take `getBBox()` of every `<text>` and repeat the same four
   checks in the browser's real metrics — catches rotated text, wrapped
   captions, and anything the estimator cannot model. Zero findings.
3. **Read the renders.** Screenshot each `figure` element (with caption) and
   each `svg` element (without) at desktop, phone, dark and desaturated, and
   READ them for clipped, colliding or escaping lettering, vanished colours,
   and scribble glyphs. Also render the full page.
4. **Reader-environment render (when available).** If the user's browser is
   reachable (Claude in Chrome connected), open the delivered file there and
   screenshot each figure with the reader's real fonts; otherwise say in the
   delivery caption that the fit is guaranteed by the Verdana-metric audit
   and ask the user to confirm one figure on their screen.

Estimator — write to `svg_text_audit.py` and run `python3 svg_text_audit.py page.html`
(exit 1 on any finding):

```python
import re, sys, html as htmlmod
VERDANA = {' ':.352,'a':.599,'b':.631,'c':.524,'d':.631,'e':.594,'f':.351,'g':.631,'h':.635,'i':.274,'j':.326,
 'k':.575,'l':.274,'m':.973,'n':.635,'o':.611,'p':.631,'q':.631,'r':.421,'s':.508,'t':.389,'u':.635,'v':.592,
 'w':.817,'x':.592,'y':.592,'z':.524,'A':.684,'B':.686,'C':.698,'D':.770,'E':.632,'F':.575,'G':.775,'H':.752,
 'I':.420,'J':.455,'K':.693,'L':.562,'M':.855,'N':.752,'O':.787,'P':.603,'Q':.787,'R':.695,'S':.684,'T':.616,
 'U':.732,'V':.684,'W':.989,'X':.684,'Y':.616,'Z':.684,'0':.636,'1':.636,'2':.636,'3':.636,'4':.636,'5':.636,
 '6':.636,'7':.636,'8':.636,'9':.636,'.':.363,',':.363,':':.454,';':.454,'-':.454,'–':.636,'—':1.0,'·':.363,
 '(':.454,')':.454,"'":.268,'’':.268,'"':.459,'€':.636,'×':.838,'→':1.0,'⇅':.9,'↻':.9,'/':.454,'?':.545,
 '&':.726,'%':1.076,'+':.838,'=':.838,'…':1.0,'✓':.8,'◆':.8}
BOLD, ASC, DESC = 1.08, 0.76, 0.22
def attr(t, n, d=None):
    m = re.search(r'\b'+n+r'="([^"]*)"', t); return m.group(1) if m else d
def audit(path):
    src = open(path).read(); out = []
    for fi, svg in enumerate(re.finditer(r'<svg\b[^>]*viewBox="([^"]+)"[^>]*>(.*?)</svg>', src, flags=re.S), 1):
        vb = [float(v) for v in svg.group(1).split()]; body = svg.group(2)
        rb = re.sub(r'<g\b[^>]*transform=[^>]*>.*?</g>', '', body, flags=re.S)
        rects = [tuple(float(attr(t,k,0)) for k in ('x','y','width','height')) for t in re.findall(r'<rect\b[^>]*/?>', rb) if 'transform=' not in t]
        lines = [tuple(float(attr(t,k,0)) for k in ('x1','y1','x2','y2')) for t in re.findall(r'<line\b[^>]*/?>', body)]
        texts = []
        for m in re.finditer(r'<text\b([^>]*)>([^<]*)</text>', body):
            t, s = m.group(1), htmlmod.unescape(m.group(2)).strip()
            if not s: continue
            if 'transform=' in t: out.append((fi,'rotated text (not audited)',s)); continue
            x, y, size = float(attr(t,'x',0)), float(attr(t,'y',0)), float(attr(t,'font-size',13))
            w = sum(VERDANA.get(c,.62) for c in s)*size*(BOLD if attr(t,'font-weight','')=='700' else 1)
            a = attr(t,'text-anchor','start'); x0 = x-w/2 if a=='middle' else (x-w if a=='end' else x)
            texts.append((s,(x0,y-ASC*size,x0+w,y+DESC*size),(x,y)))
        for s,(x0,y0,x1,y1),(ax,ay) in texts:
            if x0 < vb[0]+6 or x1 > vb[0]+vb[2]-6: out.append((fi,'exceeds viewBox',s))
            c = [r for r in rects if r[0]<=ax<=r[0]+r[2] and r[1]<=ay<=r[1]+r[3] and r[2] < vb[2]*0.95]
            if c:
                r = min(c, key=lambda r: r[2]*r[3]); over = max(r[0]+8-x0, x1-(r[0]+r[2]-8), 0)
                if over > 0: out.append((fi, f'overflows its box by {over:.0f}', s))
            for lx1,ly1,lx2,ly2 in lines:
                if abs(lx1-lx2)<1 and x0<lx1<x1 and min(ly1,ly2)<y1 and max(ly1,ly2)>y0: out.append((fi,'crossed by vertical line',s))
                if abs(ly1-ly2)<1 and y0<ly1<y1 and min(lx1,lx2)<x1 and max(lx1,lx2)>x0: out.append((fi,'crossed by horizontal line',s))
        for i in range(len(texts)):
            for j in range(i+1,len(texts)):
                a,b = texts[i][1],texts[j][1]
                if a[0]<b[2] and b[0]<a[2] and a[1]<b[3] and b[1]<a[3]: out.append((fi,'collides with '+texts[j][0],texts[i][0]))
    return out
if __name__ == '__main__':
    f = audit(sys.argv[1]); [print(f'figure {a}: {b} :: "{c}"') for a,b,c in f]
    print(f'--- {len(f)} finding(s)'); sys.exit(1 if f else 0)
```

Runtime audit — in Playwright, after `waitForTimeout(3200)`, for each
`figure svg`: collect non-transformed `rect`s as containers, `getBBox()` every
`text`, and apply the same viewBox / containment / collision checks; print and
exit 1 on findings.

## Pre-flight checks — run before sending

- Figure QA gate passed: estimator 0 findings, runtime audit 0 findings,
  renders read (desktop, phone, dark, desaturated), reader-environment render
  done or its absence stated in the caption.
- Three word buckets counted and reported.
- Beats walked: each changes what the reader believes; each figure's governing
  question and native shape named; the subject of each claim named in its
  artwork; captions alone tell the spine and end on the mechanism or
  transformed state; the opening figure maps the later zooms.
- Analogy (if any): one, breaking point named. Definitions ≤ three.
- Audience test; the ask is the one the user confirmed.
- Argument source: arc recognisable; honest turn kept; strongest lines and
  ruled wordings verbatim.
- No prose repeats its picture; cast consistent; every colour meaning
  labelled; every SVG label meets the size floor.
- Existing-document source: source block names it; PDF embedded and clickable.

## Two-pass adversarial review — for any page that will travel

Cross-vendor when possible. **Pass 1, blind:** a PDF of caption-free artworks
only, no purpose stated, web search forbidden; the reviewer records what it
reads first/second/third and the claim it infers, per figure. **Pass 2,
informed:** a second PDF with captions, intended claims and an EVIDENCE DOSSIER
quoting the FULL supporting source passage for every visual element — short
excerpts produce false "unsupported" findings. Two separate uploads in one
chat so the blind output is preserved verbatim. Severity defined (must-fix =
misleads against quoted evidence, or blind read failed, or legibility blocks
comprehension; should-fix = below professional standard but comprehension
survives; polish = aesthetic); verdict computed from reported counts (zero
must-fix → ready; ≥3 redraws or any page-level must-fix → not ready;
otherwise only-with-changes); invariants on counts, unique ids, ranking with
reasons, structured provenance. Triage every finding against the full source
before applying; reject with the verbatim passage when the reviewer's evidence
was incomplete, and say so in the closure record.

## Why these rules

Sourced 2026-08-23 to 2026-09-01, not invented: write to a smart newcomer
(Storytelling with Data); keep searchable jargon (Julia Evans); a single
unqualified analogy induces misconceptions (Spiro et al. 1989); explanations
rate higher with more causal mechanism (Zemla et al. 2017); depth calibrated to
the next action (Wellspoken 2026; MoltedOpus 2026); narrative transportation
(Green & Brock 2000; Dahlstrom 2014; Heath & Heath 2007); curiosity as an
information gap (Loewenstein 1994); Duarte's Sparkline (HBR 2012); open on one
concrete instance, units and scale carry emotion (The Pudding); multimedia and
redundancy principles, details-on-demand (Hohman et al., Distill 2020); match
the visual to the question type (Dan Roam, 6×6 codex); a graphic is a
cognitive tool, truthful first, layered, never decorated (Alberto Cairo);
action titles and one proving number (MBB exhibit practice); plain language is
not dumbing down (NIH; ISO 24495-1:2023). The Figure QA gate exists because a
fallback-font render passed and the reader's Verdana render failed.

## Home & refresh

Canonical source: `skills/eli5/SKILL.md` in this harness. This is
the derived Cowork port. Edit the canonical file first, then mirror and
re-package:

```bash
rm -rf /tmp/eli5stage && mkdir -p /tmp/eli5stage/eli5 && \
  cp ~/.claude/skills/eli5/cowork/SKILL.md /tmp/eli5stage/eli5/ && \
  (cd /tmp/eli5stage && zip -rqD ~/Downloads/eli5.skill eli5)
```

The staging folder MUST be named `eli5`; the description must not contain
angle brackets. Then upload `~/Downloads/eli5.skill` in Cowork → Customize →
Skills. A saved skill proposal REPLACES the whole SKILL.md — always propose
the complete file, never a chapter.