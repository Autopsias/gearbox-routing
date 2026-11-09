---
name: ui-craft
description: Designs, builds and reviews user interfaces of every kind — web apps, sites, landing pages, dashboards, HTML reports and artifacts, the pages eli5 and plan-builder render, and product mock-ups or animated demo scenes — on Benji Taylor's Stageshow design rules, a frame-only checklist, a box-fit checker, two blind reviewers (Claude and Codex) in rounds, and a direction review that compares an app's structure with outside products, kept current through a pinned vendored copy. Use when building or changing any UI, page or screen, when asked to review a design, UX or layout ("does this look right", "it feels busy", "clean up the layout", "review the UX", "check it on mobile"), when asked whether a page needs a redesign or for design inspiration from other products, when screenshots of a UI need judging, or when asked for a product mock-up, demo frame, hero animation or faux UI. Not for a chart on its own (dataviz).
---

# ui-craft

Design rules and a review method for any UI, taken from Stageshow and read in place, so each
re-pin of upstream brings its improvements. Paths used below:

- `K` = `~/.claude/skills/ui-craft` (this skill)
- `V` = `$K/vendor/stageshow` (the pinned upstream copy; read it, never write in it). If it is
  missing, run `bash $K/scripts/fetch_vendor.sh` once: it downloads the pinned commit and checks its hash
- `W` = the work copy with npm packages: `W=$(bash $K/scripts/stage.sh)` (about 10 s the first
  time per pin, then instant)

Codex reads the same paths: its skill mirror copies `scripts/` and `references/`, not `vendor/`.

## Pick the mode

| The request | Mode |
|---|---|
| Build or change a real page, app screen, HTML report or artifact | BUILD, then REVIEW |
| Judge an existing page, a deployed site or screenshots | REVIEW |
| Is this the right structure: the first review of an app, after a big feature, the owner says a page feels busy or wrong, a structural finding, or about once a quarter | DIRECTION, then BUILD and REVIEW |
| A product mock-up, demo frame, faux UI, hero or loop animation | DEMO |
| "Stageshow has an update", or the Monday notification | UPSTREAM |

A project's own design system wins on tokens and components; these rules decide how to use
them. On a claude.ai Artifact, `artifact-design` owns the page contract (title, theme tokens,
allowed CDNs); this skill owns the craft and the review.

## BUILD

1. Read `$V/references/craft.md` in full, then `$K/references/real-pages.md` (how each rule
   reads on a real page, and what real pages need that Stageshow does not cover).
2. Build. Put the heaviest marks on the page's next action, say each state once, and give
   the phone its own layout.
3. Run REVIEW steps 1 to 3 and fix the largest visible failure first; repeat. Never report
   that a page looks right from its code.

## REVIEW

```
- [ ] 1. Frames: shoot.mjs, every changed page, 1366 and 390, real seeded state
- [ ] 2. Box fit: layout.mjs on each page at both widths
- [ ] 3. Self-check: answer verify.md, as real-pages.md reads it, from the frames
- [ ] 4. Two blind reviewers: a fresh Sonnet agent and Codex get the rules and the frames, nothing else
- [ ] 5. Open the frame for every claim; drop what the frame does not show
- [ ] 6. Mark each surviving finding new or known (the design ledger, earlier reviews, decisions)
- [ ] 7. Show marked pictures first (mark.py), then the list: blocking, then polish
- [ ] 8. Rounds: after the fixes, re-shoot what changed and repeat 4 to 7 until no new blocking finding (at most 3)
```

1. `node $K/scripts/shoot.mjs --stage "$W" <url|file> --out <dir> --name <page> [--init seed.js]`.
   It writes `<page>-<width>.png` (full page) and `<page>-<width>-top.png` (first screen), and
   prints screens, words, horizontal overflow and fixed parts per width (also in
   `<dir>/shots.json`). For a whole app, `--crawl` follows every same-origin link from the start
   page and shoots each screen (`--list` prints them first); `--pages "#a,#b"` names them. Menus,
   pop-ups and toggles need the in-app browser: serve the build with `preview_start` (it cannot
   open `file://`), on `127.0.0.1` when `localhost` holds someone's saved state. A page with stored
   state (a reader's progress, a signed-in view) gets an `--init` script that seeds realistic
   data: placeholder state hides overlap and clipping. Never sign in with real credentials;
   build the page locally instead.
2. `cd "$W" && node scripts/layout.mjs <url> --width 1366`, then `--width 390`. It checks three
   things only: a control shorter than its text and padding, text against one edge of a
   control, and content eating a clipped container's bottom padding. "no-issues-detected" is
   not visual approval; exit 2 means "not checked".
3. Answer `$V/references/verify.md` from the frames, read through `real-pages.md`. Stop
   self-checking when the frame stops changing and go to step 4: a fresh eye.
4. Dispatch one fresh agent (Sonnet). Its prompt names: `$V/references/review.md` (its
   brief), `$V/references/craft.md`, `$V/references/verify.md`, `$K/references/real-pages.md`,
   the frame paths, and one line on what the page is for and who reads it. It never gets the
   builder's plan, reasons, code or earlier reviews. Over about 40 frames, split the app into
   areas, one reviewer each, all dispatched at once. Ask for every finding the frames show,
   ranked, each with the frame file, the place in frame pixels, the rule quoted, a one-line
   fix, its severity (blocking, polish or structural: the Severity section of
   `real-pages.md`), and what it could not check. A cap on the count only moves the rest to
   the next review. At the same time, give Codex the same prompt and frames:
   `bash $K/scripts/codex_review.sh <brief.md> <answer.md> <frame.png>...`. A finding both
   models report is strong; one that only one reports gets a closer look in step 5. In Codex,
   skip the script and say that the second model was skipped.
5. Open every frame a finding cites and look at the place. Keep a finding only if the frame
   shows it.
6. Match each finding against the project's design ledger first (the Design ledger section
   of `real-pages.md`; create it on the first review), then read the project's earlier design
   reviews (its memory notes, docs or issues) and mark each finding new or known. Report the
   known ones as one line, not as news. Then check each new finding against the decisions on
   record (the project's CLAUDE.md, decision notes, and a
   test that asserts the very behaviour): a finding that contradicts one goes to the owner
   as a question, never into a fix brief.
7. Write a spec (format in the script's docstring) and run
   `uv run -q --with pillow python -I $K/scripts/mark.py <spec.json> <out dir>`. Send the
   pictures (SendUserFile, display render) before the text. Each picture: one finding, a red
   box on the fault, green dashes where it should align. List the blocking findings first,
   then the polish list (still reported, ranked; the owner decides). A structural finding is
   never put in a fix brief: offer the owner a DIRECTION review for it. Add every new finding
   to the ledger.
8. After the fixes land, re-shoot the changed pages and the pages that share their parts
   (step 1), and run steps 4 to 7 on them with fresh reviewers. A fix often makes a new fault;
   this round is where it shows. Stop when a round adds no new blocking finding, or after 3
   rounds, and report what is still open in the ledger. Polish findings never keep the rounds
   open.

## DIRECTION

It asks the question the craft review does not ask: is this the right structure for the
page's job? It keeps every craft rule: after the pick, BUILD and REVIEW the chosen direction.
Read `$K/references/direction.md` for each step.

```
- [ ] 1. Jobs: one line per page: what it is for, who reads it, its one next action
- [ ] 2. Frames: REVIEW step 1 on the key pages, 1366 and 390
- [ ] 3. References: 3 to 5 outside products that do the same job (Exa or web search, public pages only)
- [ ] 4. Two blind reviewers: the structural brief to a fresh Sonnet agent and to Codex at the same time
- [ ] 5. Directions: 2 or 3, each with a mock-up of the key page at both widths
- [ ] 6. The owner picks; record the pick as a decision; then BUILD and REVIEW
```

## DEMO

Stageshow itself is the method: read `$V/SKILL.md` and follow it, with two changes. Work in
`$W`, never in `$V` (upstream's `npm install`, `npm run dev` and its `examples/<yours>/`
scratch folder all write into the folder they run in). Serve the preview with
`cd "$W" && node node_modules/vite/bin/vite.js playground --config playground/vite.config.ts --port 5199 --strictPort`
in the background (upstream's `npm run dev` goes through `portless`, a local proxy), then film
from `$W`: `node scripts/film.mjs "http://127.0.0.1:5199/?capture=<example>" --select '#example' --still --gate --out <png>`.
Stop the server when done.

## UPSTREAM

Follow `$K/references/upstream.md`. Never apply an update in the middle of a task; finish on
the pinned copy and report the update.

## Gotchas

- **2026-11-07, Kuzushi trial.** The blind reviewer was right on 12 of 15 claims. Three failed
  the frame check: it guessed a phone inset (12px called "cut tight"), called a quote bigger
  than the title, and read a sticky sidebar in a full-page shot as a seam. Step 5 exists for
  this. A full-page shot draws fixed and sticky parts once; judge them on `-top` frames.
- **2026-11-07, Kuzushi fixes.** "The executive page is missing from the menu" was a
  deliberate October decision, guarded by a UAT case. The fix reversed it and edited the test
  to match; the owner caught it at merge time. Step 6 now checks decisions, not only reviews.
- **2026-11-07, Kuzushi full review.** 3 reviewers, 40 claims: 12 new findings confirmed,
  about 20 already known, 1 wrong (a phone frame's 2x pixels read as CSS pixels: "28px text"
  was 16px), 3 too small to check. Measure what a frame cannot show in the browser:
  table overflow, tap sizes and contrast each took one script there.
- **2026-11-07, Kuzushi trial.** 9 of the 15 findings repeated the project's earlier reviews.
  Without step 6 the owner gets old news as new.
- **2026-11-07, Kuzushi trial.** The owner asked for pictures, not prose: "you need to explain
  all these points visually, show me instead of telling me". Step 7 is not optional.
- **2026-11-07, build.** Auto mode refused Stageshow scripts run from a raw download. From the
  work copy it refused `node $W/scripts/layout.mjs <url> --report <file>`, the same command
  with `| grep` after it, and a heredoc plus `node` in one call; it ran
  `cd "$W" && node scripts/layout.mjs <url> --width <n>` and a plain `node shoot.mjs ...`.
  If a run is refused, say so and give the owner the exact command; do not hunt shapes.
- **2026-11-07, build.** The first planted faults (a 2px-padded card, a clipped button)
  passed `layout.mjs`: they are outside its three rules. A clean run proves only those rules.
  It also has no `--init`, so on a page with stored state (Kuzushi) it checks the
  first-visit view, not the seeded one in the frames.
- **2026-11-08, Kuzushi third run.** 4 reviewers, 60 claims: every reviewer filled the old
  cap of 15, and 10 claims were new. Five craft runs (Kuzushi PRs #198 to #202) made 39 fixes
  and no structural change; the structural changes came from the owner's own complaint. Two
  of the ten fixes in the third run made a new fault. Steps 4 and 8 and DIRECTION exist for
  this.
- **2026-11-08, two-model test.** On one phone frame, asked the structural question, Codex
  and a fresh Sonnet agent named the same problem (the tutor blocks above the week's items)
  and the same pattern (a Duolingo-style path). The question found it, not the model; the
  second model confirms it.
- **2026-11-08, Codex.** `--image` takes several files: a prompt after it is read as a file
  name, and Codex stops with "No prompt provided via stdin". `codex_review.sh` puts the
  prompt first. It prints nothing while it works: 299 s on a 3-frame craft brief, 85 s on a
  7-frame structural brief. Run it with `run_in_background` and read the answer file.
- **2026-11-08, first Kuzushi DIRECTION run.** Codex and Sonnet named the same structural
  problem on all three pages. Both also caught a brief error: it said "white belt" for a Home
  frame that showed a 2nd dan reader. Name the seeded state of each frame in the brief.
