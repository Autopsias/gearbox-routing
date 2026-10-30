---
name: tldr
description: Turn one or many long, complex sources (a report, a paper, a contract, a folder of documents, URLs, transcripts, a codebase, knowledge-base notes) into a decision-ready executive TL;DR — a one-page brief, an eli5 visual explainer page, an infographic PNG, a branded deck and, only when asked, a narrated video, all built from one claim ledger so no output can disagree with another. Use when the user types /tldr, says "tldr", "the short version", "summarize this for the exec team / board / my CEO", "what do I need to know from these documents", "explain this so leadership gets it", "make a video that explains this", or drops a stack of long documents and asks what they mean. Not for a one-paragraph summary in chat (answer directly), not for a single how-it-works page with no decision attached (eli5), and not for checking a finished board pack before it travels (pre-send review).
effort: medium  # synthesis and orchestration; eli5, your brand skill and video-creator carry their own rules
---

# tldr

One read, one ledger of claims, one spine. Then every output — brief, page,
poster, deck, video — is rendered from that same spine, so a number or an ask
can never differ between the memo an executive reads and the video they watch.

This skill is a thin orchestrator. It owns three things nobody else does:
the **claim ledger** (what the sources actually say, with provenance), the
**executive brief** (decision first, one page), and the **consistency gate**
across outputs. Everything visual it delegates:

| Output | Delegated to | What tldr hands over |
|---|---|---|
| Explainer page (HTML) | `eli5` skill | ledger, brief, storyboard, audience |
| Branded deck (Marp HTML or PDF) | your house brand skill, Marp mode | brief, storyboard as claim headlines |
| Narrated video (MP4) | `video-creator` plugin | `brief.md`, storyboard as the script arc |
| One-page brief (PDF, DOCX) | `scripts/render.sh` | the brief as HTML and Markdown |
| Infographic poster (PNG) | `scripts/render.sh` + eli5's `svg_text_audit.py` | one poster HTML |

Sources: $ARGUMENTS. With no source given, ask for one and stop.

## Process at a glance

1. Gather and read every source. Chunk only when you must.
2. Write the claim ledger — `references/claim-ledger.md`.
3. One question call: audience, outputs, the ask, does it travel.
4. Write three candidate spines; pick one.
5. Write the brief — `references/brief-template.md`.
6. Storyboard once; render each chosen output from it.
7. Run the consistency gate; review if it travels; deliver.

Copy this checklist into your working notes and tick it as you go:

```
[ ] every source read or extracted; count of words and documents noted
[ ] ledger.md: every claim has id, type, source, locator; conflicts logged
[ ] one question call made (audience / outputs / ask / travels)
[ ] three spines written, one chosen, reason stated
[ ] brief.md body under 300 words by the template's count command; every number carries a ledger id
[ ] storyboard.md: beats, each naming what the reader believes after it
[ ] outputs rendered; each checked by its owner skill's own gate
[ ] ledger_check.py: 0 findings; ask verbatim in every output
[ ] travels → eli5 two-pass review done; verdict recorded
[ ] delivery message: brief first, links, sources block, what is unverified
```

## 1. Gather and read

Put everything under `tldr-out/<slug>/` in the working directory, unless the
user names a folder. Copy or link the sources into `sources/` and list them in
`sources.md` with a stable id (`S1`, `S2`, …), title, date, author, format and
word count. The Read tool opens PDFs directly. For a URL, fetch it; for a
knowledge-base reference, pull the note with that tool and treat the note
as one source. For code, the source is the files you read, named by path.

Read until you can retell each source without looking. A one-shot summary
keeps the topic and drops the point; the figures, dates, exceptions and
conditions appear once each and are the first things compression loses. That
is why extraction (step 2) is a separate step from writing (step 5).

**When the pile is large** — more than about eight documents, or more than
about 60,000 words in total — do not read it all into one pass. Write a
per-source extract first (the ledger rows for that source alone), then merge.
This is the meta-summary pattern from Anthropic's own summarization guide, and
it also matches the research finding that hallucination rises with the number
of documents fed to one prompt. The merge step reads ledgers, not prose.

## 2. The claim ledger

Format and rules in `references/claim-ledger.md`. The short version: every
claim gets an id (`C01`), a type (fact, number, quote, assumption, estimate,
open question), the source id, a locator the reader can open (page, section,
timestamp, line), and the words of the source where a number or a ruled
wording is involved. Two sources that disagree get a row in the conflict log,
never an average. Anything the sources do not state is "not stated", not
inferred.

The ledger is the only place a later step is allowed to get a fact from.
Attribute first, then write: the brief, the page, the deck and the script all
cite ledger ids in their working drafts, and the gate in step 7 checks the
numbers against this file.

## 3. One question call

Ask once, with options, never an interview. Four things in the one call:

- **Audience.** Default: executive decision-makers. Other options: the
  technical team, non-technical staff, mixed. The audience changes the door,
  the cut and the analogy domain (eli5 carries that table); it never changes
  the facts. It also picks the video's look (step 6).
- **Outputs.** Checkboxes: brief (PDF and DOCX), explainer page, infographic
  PNG, branded deck, video. Default: the detailed brief, the page and the deck.
  The poster and the video are off by default. Make the video only when the
  user asks for one in the request or ticks it here; when they tick it, say
  the render takes minutes, not seconds. (Owner rulings: 2026-10-17, a run
  that delivered only a 289-word brief and a page was called "appalling" —
  too thin to use; 2026-10-19, a video on every request is not wanted.)
- **The ask.** Propose the decision or action you read in the sources as the
  recommended default; let the user confirm, sharpen or replace it. The ask is
  the last beat of every output. If the user replaces it, re-do the spines.
  When the sources name no owner or date, the owner is the audience and the
  date is the meeting or deadline in the request; write "not stated" only
  when the request gives neither.
- **Travels?** Default yes: the outputs go to other people. Then the page runs
  eli5's two-pass adversarial review and every output ends on a sources block.
  "Just for me" skips the review, keeps the sources block.

Unattended, take every default — brief, page and deck, no video unless the request asks for one — and say so in the delivery message.

## 4. Three spines, pick one

A spine is one sentence: the claim plus the reasoning that carries it. Write
three different spines over the same ledger — for example the cost spine, the
risk spine, the timing spine — then choose one and write one line on why. Three
candidates cost two minutes and stop the first plausible framing from winning
by default. The chosen spine is the bottom line of the brief, the first beat of
the page, the title slide of the deck and the hook of the video.

## 5. The brief

Template, rules and a worked example in `references/brief-template.md`. The
shape: bottom line, situation in two sentences, three findings each with its
"so what", at most three options with one trade-off each, one recommendation
in the active voice, the ask with an owner and a date, the top risks, and what
happens if nothing is done. Under 300 words of body, counted with the one
command in the template — three eval runs each counted their own way, reported
282 to 329, and all three were over by that command. Every number carries its
ledger id in the draft; strip the ids only in the rendered copy and keep them
in `brief.md`.

That one page is the **summary, not the whole brief.** Deliver it as page 1
of `brief_detailed` (3 to 4 pages): the summary, then the tables the reader
acts on — every decision with its decider and date, the gates and dates, the
open questions with their working assumptions, the risks with owners. Render
it with `render.sh` as for the one-pager; a page count over 4 is fine when the
tables need it, but say so in the delivery message.

Write it in a neutral professional register, not in the user's own voice.
When the user wants it as themselves, hand `brief.md` to the `voice` skill
afterwards; do not blend the two.

## 6. Storyboard once, render many

Write `storyboard.md` before any output: the chosen spine, then one line per
beat naming what the reader believes or feels after it and which ledger ids
carry it. Five to seven beats for an argument, three to four for a mechanism.
The brief's bottom line is beat one; the confirmed ask is the last beat. Every
output below is a rendering of this file, not a new act of authorship.

**Explainer page.** Load the `eli5` skill and follow it in full. Give it the
storyboard as its beats, the ledger as its source, and the audience from
step 3. eli5 owns the figure grammar, the SVG label rules, the Figure QA gate
and the two-pass review; do not restate them here. The page closes on the
sources block from `sources.md`.

**Brief as PDF and DOCX.** Write `brief.html` (one A4 page, the template's
layout, system fonts, no external assets) and render:

```bash
bash scripts/render.sh pdf  tldr-out/<slug>/brief.html tldr-out/<slug>/brief.pdf
bash scripts/render.sh docx tldr-out/<slug>/brief.md   tldr-out/<slug>/brief.docx
```

Open the PDF and read it: one page, nothing clipped. A two-page brief has
failed; cut, do not shrink the font.

**Infographic poster.** One `poster.html` at 1200×1600: the spine as the
headline claim, the three findings as three figures drawn with eli5's figure
grammar and label rules, the ask as the footer. Audit the SVG text with eli5's
`scripts/svg_text_audit.py poster.html` to zero findings, then:

```bash
bash scripts/render.sh png tldr-out/<slug>/poster.html tldr-out/<slug>/poster.png 1200x1600
```

Read the PNG before you deliver it, bottom edge included: the screenshot
stops at 1600 px and anything below that line is cut without a warning.

**Branded deck.** Load your house brand skill and use its Marp mode; with no
brand skill, write plain Marp. Five to eight slides. Every slide headline
is a claim sentence from the storyboard, never a topic word; the evidence and
its ledger ids go in the speaker notes. The brand skill owns the brand and its QA.
Marp yields HTML, PDF and slide PNGs, with no Marp-to-PPTX
converter, so say in the delivery message that there is no editable
PowerPoint unless the user asked for one and you built it.

**Video (only when the user asked for one).** Load the `video-creator` skill. Pick its theme from the audience
before `lv.py init`, because the theme sets the colours, the font and the logo
in every frame:

| Audience | Theme |
|---|---|
| Your brand's audience | `--theme <path to your brand's video-theme CSS>` |
| A Kuzushi course or lesson | `--theme kuzushi` |
| Anyone else, or no brand named | `--theme neutral` |

Name the theme in the delivery message. Write its `brief.md` from yours:
the one takeaway is the spine, the key facts are ledger rows copied verbatim
with their source, the constraints include "say nothing the ledger does not
hold". Its scene arc (hook → problem → evidence → principle → why here → how
to use → recap → close) maps onto your beats. Run the render in the background
and report elapsed time; it takes minutes. video-creator owns lint, QA and
the visual checklist.

## 7. Consistency gate, review, deliver

Run the gate over every rendered output. Name only the outputs you built: the
gate reports a file that does not exist as `MISSING`.

```bash
python3 scripts/ledger_check.py tldr-out/<slug>/ledger.md \
  --ask "<the confirmed ask>" \
  tldr-out/<slug>/brief.md tldr-out/<slug>/page.html tldr-out/<slug>/poster.html \
  tldr-out/<slug>/deck.md tldr-out/<slug>/video/script.json
```

It lists every number in an output that the ledger does not hold, and every
output where the ask does not appear verbatim. Fix the output or the ledger —
never silence the finding. A number you computed yourself (a recomputation,
a difference, a year counted from a contract term) goes into the ledger as an
`estimate` row with the arithmetic in the words column before it appears
anywhere; an axis tick is a number too, so tick axes at ledger values. Never
delete a true figure or a tick to make the gate pass — one eval run dropped a
"4%" tick that way and the page lost its scale. Exit 0 means zero findings.
The gate matches a number by value only: "16×" passes when any "16" is in the
ledger, so read a changed multiplier or ratio yourself. Spelled-out numbers in
video narration are not checked; the `visual` and caption fields are.

When the outputs travel, run eli5's two-pass adversarial review on the page
and record the verdict in `review.md`. The brief and the deck inherit the
page's verdict only when they changed no number and no wording of the ask;
otherwise say they are unreviewed. When no page was built, the brief is the
reviewed object instead: pass 1 gets `brief.pdf` alone, pass 2 gets it with
the ledger and the sources, same severity scale, same record. A board brief
that nobody but its author has read is the thing this skill exists to
prevent.

**Deliver in one message.** The brief's bottom line as the first sentence.
Then the links or paths, one line each. Then the sources block. Then what you
could not verify — a render you did not open, a review you skipped, a dub you
did not listen to. Report the three word counts eli5 asks for when a page was
built. End with the state line.

## Gotchas — observed, dated

- **2026-10-17 — Chrome headless prints noise.** `render.sh` uses Google
  Chrome's `--headless=new`. It writes "Trying to load the allocator multiple
  times" and two `task_policy_set` errors to stderr on every run and still
  produces the file. Judge by the output file, not the log. About 7 s a render.
- **2026-10-17 — system Python has no Playwright.** video-creator's `lv.py`
  imports Playwright. On this Mac the system `python3` lacks it; the plugin's
  own environment at `~/.cache/video-creator/venv/bin/python` has it and the
  voice models. Run `lv.py` with that interpreter.
- **2026-10-17 — the poster footer fell off the page.** The first poster
  put the ask below 1600 px; the audit and the gate both passed and the PNG
  had no footer. Only reading the PNG caught it. Tighten spacing, never the
  canvas: a taller image breaks the 3:4 preview on a phone.
- **2026-10-18 — themes need video-creator 1.2.0.** Older copies have no
  `--theme` and render every video in the Kuzushi look with belt dots. When
  `lv.py` with no command prints no `--theme`, refresh the plugin marketplace
  first, or say in the delivery message that the video carries the Kuzushi look.
- **2026-10-17 — Marp is not on PATH.** Marp mode needs `marp`;
  only an npx cache copy exists (`~/.npm/_npx/*/node_modules/.bin/marp`,
  4.1.2). Run it with `--no-stdin`, or it waits on standard input and hangs
  until the tool timeout. Its PDF export timed out too: render `deck.html`
  with `render.sh pdf` instead. Set `CHROME_PATH` to Google Chrome.
- **2026-10-17 — Homebrew ffmpeg has no `drawtext` and no `subtitles`
  filter.** `lv.py stills` writes every frame, then crashes building the
  labelled contact sheets; `lv.py render` writes `video.mp4` (64–99 s for a
  two-minute video) and then fails on `video-captioned.mp4`. Build the
  contact sheets with `xstack` and no labels, deliver `video.mp4`, and say
  that captions were not burned in (the `.srt` and `.vtt` sit beside it).
  `lv.py qa` crashes the same way before it transcribes anything; until the
  plugin is fixed, run it with a shim `ffmpeg` first on `PATH` that strips
  `,drawtext=…` from its arguments and execs the real binary.
- **2026-10-17 — `ledger_check.py` on a `.srt` reports every timestamp.**
  Check the caption TEXT (drop the `-->` lines and the cue numbers), and
  expect the ask to fail there: captions split it across cues, so check it
  on the final scene's frame instead.
- **2026-10-17 — NotebookLM is not in the chain.** `nlm login --check` fails
  when logged out, its video has one fixed style, no branding and no
  line-by-line script edit, and the free tier allows three videos a day. Those
  limits are why the video route is video-creator. Reach for `nlm-skill` only
  when the user asks for NotebookLM by name.

## Why these rules

Sourced in `references/why-these-rules.md`: the one-page decision-first brief
shape, extraction before writing, hallucination rising with document count,
attribute-first generation, the three-spines habit and the word budget that
fails the build. Read it before loosening a rule.
