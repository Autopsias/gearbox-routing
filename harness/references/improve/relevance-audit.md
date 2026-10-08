# Relevance audit — score what already exists

`/improve` in its default mode hunts for what is **missing**. This mode does the
opposite: it scores every rule, memory and skill description that already exists
against what recent sessions actually did, and turns the dead and the falsified ones
into gated deletion proposals. It is the only path in `/improve` that proposes a
removal.

Read this file when the run is in audit mode. It replaces the main command's
discovery pipeline (Phases 1–5) but reuses its judgement tables — 4a's
strengthen-versus-hook logic, 4d's categories and confidence levels, and Phase 5's
presentation. **Load Learnings, the Phase 5 evidence gates, Phase 6 Apply Changes and
Save Learnings still apply** — this mode changes what is scored, not the standard of
proof and not how an accepted proposal is applied.

## Contents
- [Step 0 — the sample, and its cap](#step-0--the-sample-and-its-cap)
- [Step 1 — the inventory](#step-1--the-inventory)
- [Step 2 — the verdict](#step-2--the-verdict)
- [Step 3 — failed triggers (skills only)](#step-3--failed-triggers-skills-only)
- [Step 4 — the placement test](#step-4--the-placement-test)
- [Step 5 — presenting](#step-5--presenting) — includes the gate interaction
- [Step 6 — applying what was accepted](#step-6--applying-what-was-accepted)
- [Run summary](#run-summary)

## Step 0 — the sample, and its cap

The audit is bounded on purpose. An unbounded transcript sweep is the failure mode
this cap exists to prevent.

**Cap: the 10 most recent session transcripts, excluding the current one.** List
`~/.claude/projects/<project-path>/*.jsonl` by modification time, take the newest 10,
and work only from those. Extract signals with grep-style filtering — never read a
transcript file in full, and never re-sample per rule: one pass produces the evidence
for every verdict below.

**State the cap and the actual sample in the run summary**, by name and date:

> Sampled 10 of <total on disk> transcripts (cap: 10 most recent), <oldest> → <newest>.

Fewer than 10 on disk is fine — report the real number. Fewer than 3 is too thin for
a never-relevant verdict: say so and downgrade every such verdict to "no sample yet".

## Step 1 — the inventory

Enumerate all three lists completely. These are small, on-disk and cheap; the cap
above is about transcripts, not about the inventory.

| List | Source | One item = |
|---|---|---|
| Memories | every `.md` in `~/.claude/projects/<project-path>/memory/` (and the global memory dir if one exists) | one file |
| Rules | every bullet under **User Preferences**, **Communication style** and **Behavior** in `~/.claude/CLAUDE.md` | one bullet |
| Skill descriptions | the `description` frontmatter of every skill and command, project and global level | one description |

Nothing is sampled out of the inventory. Every item gets a verdict, including the
ones you expect to pass.

## Step 2 — the verdict

Exactly one of four per item.

| Verdict | Means | Required evidence |
|---|---|---|
| **followed** | a sampled session hit a situation this item governs, and the behaviour matched | verbatim quote from that transcript, cited with its session |
| **violated** | a sampled session hit a situation it governs, and the behaviour did not match | verbatim quote showing the miss, cited with its session |
| **wrong** | the item's factual claim is contradicted — a path that no longer exists, a mechanism that behaves differently, a tool that was renamed | the contradicting evidence: a transcript quote, or the config/filesystem check that fails, cited `path:line` plus the command that produced it |
| **never relevant** | no sampled session presented a situation it governs | the negative: the sample list from Step 0 **and the trigger you searched for**, verbatim |

The Phase 5 verbatim-quote gate applies to all four. **Never relevant is the one
verdict whose evidence is an absence**, so it carries the search term instead of a
quote — a never-relevant verdict that does not say what was searched for is not a
verdict, and is dropped. Search for the situation the item governs (the tool, the
file type, the phrase, the decision), not for the item's own wording.

A rule that is **violated** is a strengthening or hook-conversion finding, handled by
4a of the main command — not a deletion candidate. Violation means the rule matters.

## Step 3 — failed triggers (skills only)

A sampled session where an agent visibly lacked knowledge a skill already holds — it
hand-rolled a procedure the skill covers, or asked a question the skill answers, and
the skill was never invoked — is a **FAILED TRIGGER of that skill's description**, not
a gap in the knowledge.

- Propose a **description edit**: add the trigger phrasing the user actually used,
  quoted verbatim from the transcript, to the skill's `description`.
- **NEVER propose copying the skill's content into CLAUDE.md, a memory, or another
  skill.** Duplicating the knowledge leaves two copies to drift and does not fix the
  trigger.
- Cite both sides: the skill's current description (`path:line`) and the transcript
  quote that should have fired it.
- Skill descriptions are read from a budget-pinned listing that refills greedily, so
  do not justify a description edit as a token saving — justify it as a trigger that
  did not fire.

## Step 4 — the placement test

Compute **reach** against the same sample: the number of sampled sessions in which
the situation the item governs actually appeared — not the number where it was
followed. Always state it as a fraction: "3/10 sampled sessions".

**Safety-critical** means the item guards against data loss, credential exposure,
destructive deletion, money, or an irreversible action.

| Item is | Belongs | Proposal |
|---|---|---|
| Broad (reach ≥ 20% of sampled sessions) **or** safety-critical | the always-loaded CLAUDE.md | keep — strengthen it if the verdict was violated |
| Narrow (reach < 20%) **with** a detectable trigger — a phrase, a file type, a tool name, a command | a skill (trigger in its description) or a path-scoped file under `.claude/rules/` | demote: move the text out and leave nothing behind |
| Narrow **with no** detectable trigger | nowhere | deletion candidate |
| Verdict **wrong** | — | correct it if the true version is known; delete it if not |

Safety-critical never demotes on reach alone. A rule that prevents data loss stays in
the always-loaded file at 0/10.

An item younger than 90 days with reach 0/10 is a **hold**, not a deletion — report
it as "no sample yet" and leave it alone. It has not had the chance to be relevant.
Get the age only for items that reached deletion-candidate status, one command each,
against the source repo (`~/your-private-harness`) rather than the deploy target:

```bash
git log --follow --diff-filter=A -1 --format=%as -- <path>   # a memory or skill file
git log -S '<distinctive phrase>' --format=%as -- CLAUDE.md | tail -1   # one rule
```

A memory that records a *fact* rather than an instruction demotes rather than
deletes: facts belong in memory even at low reach; it is instructions that earn their
place in the always-loaded file.

## Step 5 — presenting

Show the audit table once, before any finding:

```
## Relevance audit — <N> memories, <N> rules, <N> skill descriptions
Sample: <N> of <total> transcripts (cap 10), <oldest> → <newest>

| Item | Verdict | Reach | Evidence | Proposal |
|---|---|---|---|---|
| memory/foo.md | never relevant | 0/10 | searched "worktree", "git worktree" | Delete |
| CLAUDE.md: "ALWAYS verify…" | violated | 6/10 | session 2026-08-21: "…" | Strengthen |
```

Then present every deletion, demotion and correction **one at a time via
AskUserQuestion** — Accept / Reject / Modify. Each one keeps its own question even
though the default mode batches; this mode never offers 'Apply all'.
**Nothing in this mode is ever auto-applied.** A deletion is the least reversible
proposal `/improve` makes; it gets the same consent as an addition, and the item's
current text is shown in full in the finding so the user can judge what disappears.

Tiering: **wrong** → Critical. **violated** → Critical or Improvement per 4d.
**never relevant** and demotions → Maintenance. Order them as Phase 5 orders any
findings.

Gate interaction:

- **Gate 1 (verbatim quote)** — applies, per Step 2.
- **Gate 2 (two-session corroboration)** — does **not** apply. It covers findings
  that only add text; every proposal here removes, moves or corrects. The one
  exception is a *replacement* rule proposed alongside a deletion — that half is an
  addition and needs its two sessions.
- **Gate 3 (rejection memory)** — applies in full. A deletion the user already
  refused is not re-proposed without materially new evidence.

## Step 6 — applying what was accepted

Accepted proposals are applied by **the main command's Phase 6**, unchanged: group by
file, follow `apply-changes.md` for every finding type that has a recipe there, then
print the same **Changes Applied** table and ask about committing. A deletion that
removes a file and leaves its index entry behind is the failure this step exists to
prevent, and only the recipe knows about the index.

Every proposal this mode makes maps to a recipe in `apply-changes.md`:

| Proposal | Recipe |
|---|---|
| Delete a memory (Step 4) | **Memory deletions** — file *and* its MEMORY.md line |
| Delete a rule bullet (Step 4) | **Rule deletions** |
| Demote a rule to `.claude/rules/` (Step 4) | **Rule extractions** |
| Demote a rule into a skill (Step 4) | **Rule demotions into a skill** |
| Keep a fact-recording memory instead of deleting it (Step 4) | nothing to apply |
| Correct a **wrong** item (Step 2) | plain edit — no recipe needed |
| Skill description edit (Step 3) | **Skill Description rewrites** |
| A replacement rule proposed with a deletion | plain edit, applied in the same Phase 6 pass |

Two carve-outs on Phase 6's ledger step (step 6), because this mode scores what exists
rather than what is missing:

- **Accepted** — there is no gap sighting to retire. Record nothing, unless the
  proposal carried a replacement rule; that half retires its own gap as normal.
- **Rejected** — record in the ledger as normal (`rejections`, with `evidence_then`).

## Run summary

Report, every time: the sample and its cap; the three inventory counts; the verdict
tally (followed / violated / wrong / never relevant / no sample yet); how many
proposals were presented, and how many were held back by which gate. A silent audit
and an empty one look identical otherwise.
