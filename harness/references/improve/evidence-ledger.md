# The evidence ledger — `~/.claude/improve-ledger.json`

This file is the memory that makes `/improve` evidence-bound instead of
impression-bound. It is read at the start of every run and written at the end of
every run, whether or not any change was applied.

Three gates depend on it. All three are enforced in Phase 5, **before** a finding
is ever shown to the user:

1. **Verbatim quote** — every finding carries a word-for-word, cited excerpt: a
   transcript/conversation quote for a conversation-derived finding, or the config text
   itself (`path:line`) for a finding produced by reading config. No excerpt, no
   finding.
2. **Two-session corroboration** — a finding that proposes a *brand-new rule* is
   presented only once two distinct sessions have been seen saying so, this run's
   sighting included.
3. **Rejection memory** — an edit the user already rejected is not proposed again
   unless materially new evidence has arrived.

Gate 1 needs no stored state. Gates 2 and 3 are the reason this file exists.

## Contents
- [Schema](#schema) — field-by-field meanings
- [Lifecycle](#lifecycle) — [read](#read--start-of-run-load-learnings-phase) · [matching a finding to a gap key](#matching-a-finding-to-a-gap-key) · [recording a sighting](#recording-a-sighting) · [gate 2](#gate-2--two-session-corroboration) · [gate 3](#gate-3--rejection-check) · [retiring a gap](#retiring-a-gap) · [recording a rejection](#recording-a-rejection) · [write](#write--end-of-run-save-learnings-phase)
- [Failure handling](#failure-handling) — what counts as an empty ledger
- [Session identity](#session-identity) — why the date fallback under-counts
- [Deployment](#deployment) — how the file gets versioned

## Schema

```json
{
  "gaps": [
    {
      "key": "<kebab-slug>",
      "sightings": [
        {"session": "<id-or-date>", "quote": "...", "seen": "YYYY-MM-DD"}
      ],
      "retired": null
    }
  ],
  "rejections": [
    {
      "edit": "<summary>",
      "rejected": "YYYY-MM-DD",
      "evidence_then": ["..."],
      "reason": "..."
    }
  ]
}
```

Both top-level keys are always present, both are always arrays. An empty ledger is
`{"gaps": [], "rejections": []}`.

### Field meanings

| Field | Meaning |
|---|---|
| `gaps[].key` | Stable kebab-case slug naming the *gap*, not the proposed fix — e.g. `no-quote-on-findings`. The identity of a gap across runs. Never renamed once written; a rename splits one gap into two and resets its count. Reuse an existing key rather than minting a near-duplicate — see "Matching a finding to a gap key". |
| `gaps[].sightings[].session` | The session this sighting came from. Prefer the real Claude Code session id. Fall back to the run date (`YYYY-MM-DD`) only when no id is available — see "Session identity" below, because the fallback can under-count. |
| `gaps[].sightings[].quote` | The verbatim excerpt that evidenced the gap in that session — a transcript quote, or the cited config text (`path:line`) for a file-derived finding. Word-for-word, not paraphrased. This is what gate 1 produced; storing it is what lets gate 3 later judge "materially new". |
| `gaps[].sightings[].seen` | Date the sighting was recorded, `YYYY-MM-DD`. Drives the 90-day expiry. |
| `gaps[].retired` | `null` while the gap is live. Set to `YYYY-MM-DD` once a rule covers it. A retired gap is never presented again. |
| `rejections[].edit` | One-line summary of the exact edit the user rejected, naming the target file. Specific enough that a later run can tell whether it is proposing the same thing. |
| `rejections[].rejected` | Date of the rejection, `YYYY-MM-DD`. |
| `rejections[].evidence_then` | The verbatim quotes that supported the edit *at the moment it was rejected*. This is the baseline "materially new evidence" is measured against. |
| `rejections[].reason` | Why the user rejected it, in their words where available. Recorded so a later run can tell a taste rejection from a "not yet" rejection. |

## Lifecycle

### Read — start of run (Load Learnings phase)

Read the file. Then, in this order:

1. Drop every sighting whose `seen` is more than 90 days before today.
2. Drop every gap that now has zero sightings **and** is not retired — nothing is
   left to corroborate.
3. Keep retired gaps regardless of sighting age; they are the record of what is
   already covered.

The result is the **live ledger** the run works against. Everything below refers to
it.

### Matching a finding to a gap key

Do this **before** recording anything. `key` equality is what gate 2 counts, so two
runs that word the same gap differently never corroborate each other — the count
resets and the gate holds forever.

1. Read the live ledger's existing `gaps[].key` values, live and retired.
2. Ask of each: *does this key name the same underlying gap as my finding?* Judge the
   gap, not the wording, and not the proposed fix — `no-quote-on-findings` and
   `findings-lack-quotes` are one gap; `findings-lack-quotes` and
   `findings-lack-session-ids` are two.
3. On a match, **reuse that key verbatim**, including its wording. A retired match
   means the gap is already covered — drop the finding rather than reopening it.
   Exception: for a key that starts `standing-default-`, `retired` means 'adopted',
   not 'covered'. A PASS in the `--after <that date>` question-class run is presented
   as a 'Standing default' finding of kind 'Ignored default' (the adopted text, its
   target file, three post-adoption member questions), never dropped.
4. Only with no match, mint a new kebab-case slug naming the gap.

When it is genuinely ambiguous, reuse the existing key. Merging two gaps into one
under-counts by at most a run; splitting one gap into two stalls it indefinitely.

### Recording a sighting

When the run finds evidence of a gap — with a verbatim quote, per gate 1 — record a
sighting against that gap's `key`, resolved by the matching step above:

- If no gap with that key exists, create one with `retired: null`.
- If a sighting for that gap already carries this session's `session` value,
  **do not append a second one**. One session is one sighting per gap, forever. A
  gap mentioned five times in one conversation is still one session's worth of
  evidence.
- Otherwise append `{"session", "quote", "seen"}`.

### Gate 2 — two-session corroboration

A finding **proposes a new rule** when its edit only *adds* text — a new rule, a new
memory file, a new skill, a new section. Findings that strengthen, reword, move or
delete existing text are not new rules and this gate does not apply to them.

For a new-rule finding: **record this run's sighting first** (per "Recording a
sighting"), **then** count the **distinct `session` values** among the live sightings
for its gap key — this run's included.

Record-then-count, in that order. Counting first would mean two *prior* sessions plus
the current one, so a gap would take three runs to graduate instead of two.

- **2 or more** → present the finding normally.
- **Fewer than 2** (i.e. this run is the only session on record) → do **not** present
  it. The sighting is already recorded; say so in the run summary, naming the gap and
  the count, e.g.
  *"Recorded 1st sighting of `no-quote-on-findings` — needs a second independent
  session before it can be proposed."*

This is a hold, not a rejection. The sighting persists, so a gap seen today and
again next month graduates on the later run without anyone remembering it.

### Gate 3 — rejection check

Before presenting **any** finding, new-rule or not, compare it against
`rejections`. It is a **match** when it targets the same file and proposes
substantially the same edit as a recorded `edit` summary.

On a match, the finding may be presented **only** with materially new evidence.
Evidence is materially new when it includes at least one verbatim quote that is
both:

- not already present in that rejection's `evidence_then`, **and**
- from a session not represented in `evidence_then`.

A rewording of the same argument, a second reading of the same transcript, or a
stronger tone is **not** materially new evidence. Without it, drop the finding
silently — do not soften it and present it anyway.

When it does clear the bar, say so explicitly in the finding, e.g.
*"Re-proposing an edit rejected 2026-05-04 ('too strict for style rules'). New
evidence since: <quote> (session 2026-08-19)."* The user needs to see that this was
already declined once.

### Retiring a gap

When a finding is **accepted and applied**, set that gap's `retired` to today's
date. The rule now covers it; further sightings are noise. Do not delete the gap —
the retirement date is the record of when it was closed. For a `standing-default-`
key the date is the adoption date: it restarts that class's count (`--after`), and
a class that passes the bar again after it is an 'Ignored default' finding.

### Recording a rejection

When the user **rejects** a finding, append to `rejections`:

- `edit` — one line naming the target file and the change.
- `rejected` — today.
- `evidence_then` — every verbatim quote that supported the finding in this run.
- `reason` — the user's stated reason; if they gave none, say so
  (`"no reason given"`), never invent one.

**Apply none** is not a rejection: a set declined with 'Apply none' writes no entry to `rejections`, and its gap sightings stay. A number excluded from 'Apply all' by name is a rejection with the reason `"excluded by number, no reason given"`.

A **Modify** is not a rejection. The user accepted the substance and changed the
wording — apply it and retire the gap as an acceptance.

### Write — end of run (Save Learnings phase)

Write the live ledger back, including this run's new sightings, retirements and
rejections. Write it even when no change was applied — the sightings recorded by
gate 2 are the whole point, and they are lost if the run only writes on success.

## Failure handling

Treat all of these as **an empty ledger** — `{"gaps": [], "rejections": []}` — and
say so out loud in the run summary, naming which case it was:

- file does not exist (first run — normal, announce once);
- file does not parse as JSON;
- it parses but is not an object, or `gaps` / `rejections` are missing or are not
  arrays.

Check the parsed value's **shape**, not just that parsing succeeded — a file
holding `[]` or `null` parses cleanly and will otherwise fail on first use.

Never silently continue on a malformed ledger: with an empty ledger, gate 2 holds
back every new-rule finding and gate 3 lets through every previously-rejected one.
The user has to know that is what happened.

Skip individual entries that are malformed (a sighting with no `quote`, a gap with
no `key`) rather than discarding the whole file, and report how many were skipped.

## Session identity

`session` is what gate 2 counts, so its accuracy is the gate. Prefer the real
Claude Code session id. The `YYYY-MM-DD` fallback is lossy in one specific way:
**two runs on the same day collapse into one session** and will never corroborate
each other. That is the conservative direction — it under-counts rather than
graduating a rule on a single session's evidence — but say so when a gap is held
back and both its sightings carry the same date.

Sightings harvested from *past* session transcripts by the History Scan agent use
that transcript's session id or date, not today's — they are evidence from those
sessions, not this one.

## Deployment

The ledger is runtime output: it is written by a `/improve` run in the live harness
(`~/.claude`) and can only be produced there. Two files make it versionable, and both
are required:

- **`.gitignore`** negates it (`!/improve-ledger.json`). The repo is default-deny at
  the top level (`/*`), so without the negation git never reports the file at all —
  it shows only under `git status --ignored` as `!!`, the classifier never sees it,
  and the pathspec entry below is inert.
- **`scripts/deploy.pathspec`** registers it under `[live-state]`, so a run that
  rewrites it is routine — `gearbox deploy` auto-harvests it with provenance instead
  of aborting on it as unclassified drift. See that file for the policy.

Versioning it means the stored quotes are committed. That is the same bargain
`improve-learnings.md` already makes, and the same gitleaks pre-commit hook covers
both — but a quote is transcript text, so do not store one you would not commit.

One wrinkle on the very first run: until the file is tracked it classifies as
`live_untracked`, and deploy's routine harvest stages tracked modifications only. It
blocks nothing, but an explicit `gearbox harvest` is what first commits it.

`improve-learnings.md` moved into `[live-state]` in the same decision (2026-08-23).
The Save Learnings step writes both files on every run, but only the ledger was new;
the learnings file had been classified as harness source, so each `/improve` run left
behind drift that aborted the next deploy.
