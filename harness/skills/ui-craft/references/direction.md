# Direction review: is this the right structure?

A craft review asks "is each part well made?". A direction review asks "is this the right
page?": what each page is for, what its first screen puts first, and how products that do the
same job solve it. It runs rarely and ends with 2 or 3 directions the owner picks from. It adds
to the craft rules and never replaces them: after the pick, BUILD and REVIEW apply every
Stageshow rule to the new structure.

## Contents
- When to run it
- 1. Jobs
- 2. Frames
- 3. Outside references
- 4. Two blind reviewers, one structural brief
- 5. Directions with mock-ups
- 6. The owner picks

## When to run it

- The first review of an app or a site.
- After a big feature: a new kind of page, a new main path through the app.
- The owner says a page feels busy, confusing or wrong.
- A craft review returns a finding with the severity structural.
- Otherwise about once a quarter. Not on every review: it is the costly part.

## 1. Jobs

For each page in scope, write one line: what the page is for, who reads it, and its one next
action. When the job is not clear from the product, ask the owner. These lines go to every
reviewer; a structure can only be judged against a job.

## 2. Frames

REVIEW step 1, on the key pages only (the home page, the page readers spend most time on, and
any page the owner named), at 1366 and 390 with real seeded state. The reviewers judge the
first screen (`-top` frames) first: it is where structure shows.

## 3. Outside references

Find 3 to 5 products that do the same job for a similar reader. Use the Exa search tool
(`web_search_exa`: describe the page you want, for example "case study of a course app's
weekly lesson page with progress") or web search. Prefer the leaders of the category and case
studies with screenshots. For each reference, write one line: the product, the page, the
pattern it uses for the job, and the link.

Shoot public pages with the in-app browser at 1366 and 390 when a picture helps. Never sign in
to another product. Borrow the pattern, never the look: no copied layout, brand, wording or
illustration (the same rule as `craft.md` § "Icons and logos" for logos).

## 4. Two blind reviewers, one structural brief

Run a fresh Sonnet agent and Codex (`bash $K/scripts/codex_review.sh`) at the same time, on the
same brief and frames. Each gets the jobs, the reference lines, the frame paths and this brief,
and nothing else: not the plan, the code, the earlier reviews or the other reviewer's answer.

> You are judging the structure of these pages, not their details. For each page:
> 1. What does the first screen put first? Is that the page's job?
> 2. What is the one next action, and how many other things compete with it?
> 3. What takes the most space, and does the reader need it on every visit?
> 4. Which reference does this job better, and with which pattern?
> 5. The single biggest structural problem (not spacing, colour or wording), and one
>    direction that fixes it.
> Quote the frame file for every claim. Say "nothing structural" when the structure serves
> the job. Return at most one problem per page, ranked by how much of the job it spoils.

Merge the two answers:
- Both name the same problem: it is strong and leads the list.
- Only one names it: open the frame and judge it; keep it only if the frame shows it.
- Check each against the decisions on record (REVIEW step 6). A problem that a recorded
  decision created goes to the owner as a question, never into a direction as a fact.

In Codex, skip the script: Codex is already one reviewer. Say that the second model was
skipped.

## 5. Directions with mock-ups

Write 2 or 3 directions, never more. Each direction says:
- the problem it solves, with the frames that show it;
- the pattern and the reference it comes from;
- what changes on which pages, and what the reader loses;
- the cost: pages, tests and content that change.

"Keep the structure, fix the details" is a valid direction when the reviewers found nothing
structural. Say which direction you recommend and why.

Make a mock-up of the key page for each direction: a static HTML file in the review folder,
with the project's own CSS tokens and real content, shot with `shoot.mjs` at 1366 and 390. A
mock-up shows the structure; its controls need not work. Send the pictures first (the current
page beside each direction), then the list.

## 6. The owner picks

The owner picks one direction, or none. Record the pick and its reason where the project keeps
decisions (its CLAUDE.md, a decision note, or the design ledger), so a later craft review does
not undo it. Then BUILD the chosen direction and REVIEW it.
