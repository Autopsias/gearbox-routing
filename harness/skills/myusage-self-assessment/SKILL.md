---
name: myusage-self-assessment
description: >-
  Mine your own Claude Code session history to decide what to actually improve in
  your setup. Runs a five-phase method — deterministic digest of every session
  transcript, fan-out mining subagents, signal clustering, a skill/automation/fix/
  nothing verdict per cluster, and a re-measure of the prior run's metrics — then
  appends ranked, evidence-cited verdicts to ~/.claude/reflection-notes.md. Use
  this whenever the user says "assess my claude code usage", "mine my sessions",
  "self-assessment", "review my setup from usage", "what should we improve in my
  setup", "audit my workflow", or asks (even indirectly) which skills/automations/
  fixes their transcript history justifies building. This is the DECISION layer
  over raw usage facts — reach for it even when the user only hints at wanting a
  data-driven verdict on their tooling rather than naming the skill.
---

# Self-assessment: mine your sessions, decide what to build

This skill packages a repeatable diagnosis you run every month or two. It reads
your real session transcripts, has subagents mine them for signals, and produces
a **ranked decision list** — for each recurring pattern, a verdict of build a
**skill**, add an **automation**, apply a **fix**, or do **nothing** — appended to
`~/.claude/reflection-notes.md` with session-id citations so every claim is
checkable.

The governing bar, applied to every candidate: **only propose a skill for
something that actually recurs AND isn't already covered.** Most findings resolve
to "fix or automate a thing that already exists," not "build something new." A
skill-saturated setup is the common case; respect it.

## Model posture

Phase 1 (digest) is pure Python — no model. Phases 2–5 (mining, clustering,
verdicts, write-up) are judgement-heavy: run them at the **strongest available
tier**. Per `~/.claude/model-routing.yaml` this is Fable if available, else Opus
at xhigh effort. Do not run the clustering/verdict reasoning on a cheap tier — the
whole value is discrimination between "recurs and matters" and "noise."

## Complement native /insights — don't duplicate it

Before deriving facts from scratch, probe for the harness's own usage analytics:

1. Check whether a `/insights` command exists and whether `~/.claude/usage-data`
   is present (`ls ~/.claude/usage-data 2>/dev/null`).
2. If facets exist (token totals, tool ratios, per-command counts), **consume
   them** as inputs instead of re-computing. This skill is the *decision layer*:
   it turns facts (whoever produced them) into ranked verdicts and a plan.
3. If they don't exist, the bundled digest produces the denominators you need.

## Checklist (all 5 phases)

- [ ] **Digest** — run `digest_sessions.py`, confirm counts look sane
- [ ] **Mine** — fan out per-project + cross-project miner subagents
- [ ] **Cluster** — merge findings into clusters, rank by leverage
- [ ] **Verdict/Append** — assign skill/automation/fix/nothing per cluster, append to reflection-notes.md
- [ ] **Re-measure** — compare this run's metrics against the prior run

## The five phases

### Phase 1 — Deterministic digest (Python, no model)

Run the bundled script over the real corpus:

```bash
python3 scripts/digest_sessions.py --pretty \
    --out /tmp/self_assessment_digest.json \
    --per-session-out /tmp/self_assessment_sessions.json
```

It walks `~/.claude/projects/*/*.jsonl` (one file per session) and
`~/.claude/history.jsonl` (typed prompts) and emits per-project + aggregate
metrics: session counts, interactive-vs-automation split, model mix, slash-command
usage, tool_errors, api_errors, interrupts, denials, token totals, err_samples,
and the babysit-prompt share. It fixes the historically-flubbed **aiTitle** field
and keeps **sidechain (subagent) records separate** from the main human loop.

Expect **1,500+ session files** on a mature setup (the reference corpus had 1,750
sessions + ~11,600 history prompts = ~13k records, digested in ~18 s). Confirm the
counts look sane before proceeding; a sudden collapse means a path moved.

The digest carries the external benchmark thresholds the miners check against:
**Read:Edit ratio > 6** (good), **edits-without-prior-Read < 10%**, **frustration
< 6%**. Read:Edit and edit-without-Read need per-tool counts the miners derive from
raw transcripts only when a cluster warrants the drill-down; the digest surfaces
the denominators.

### Phase 2 — Fan-out mining subagents

Dispatch **one miner per project** (the big projects at least) plus **one
cross-project miner**, each reading the digest (and per-session array) — never the
raw 1 GB of transcripts. Give every miner the exact signal taxonomy so their
output is mergeable:

- **recurring jobs** — the same task done by hand repeatedly
- **boilerplate** — repeated multi-step sequences ripe for a script
- **friction** — corrections, frustration, re-explanations (quote the user)
- **infra failures** — dead MCPs, auth errors, broken hooks, API drops
- **workflow health** — Read:Edit ratio, edit-without-Read, model fit
- **interrupts / denials** — where the human halts or the classifier blocks
- **waste** — automation firing on no-ops, re-derived scripts, redundant runs

The exact miner prompt template lives in `references/mining-prompts.md`. Load it
and instantiate one dispatch per miner. Each miner returns findings tagged with
taxonomy label, recurrence count, representative **session-id citations**, and
verbatim user quotes for friction.

### Phase 3 — Cluster and decide

Merge all miner findings into clusters (same root cause across projects = one
cluster). For each cluster assign exactly one verdict, weighing **recurrence
against build cost**:

- **skill** — recurs, high-leverage, and *not already covered* by an installed
  capability. Rare. Check installed skills/commands first.
- **automation** — recurs, mechanical, better handled unattended (scheduled task,
  push notification, default-continue gate).
- **fix** — the capability exists but is unreliable, unused, mis-triggered, or
  dead (bad credential, schema bug, missing route). The most common verdict.
- **nothing** — self-healing, model behaviour not configuration, or external
  noise. Record it as explicitly declined so the next run doesn't re-litigate.

The full decision rubric, the "already covered" checklist, and worked examples are
in `references/verdict-framework.md`. Rank clusters by leverage (recurrence ×
cost-to-user ÷ build-cost), most leverage first.

### Phase 4 — Append to reflection-notes (safe write)

Append a new dated section to `~/.claude/reflection-notes.md` — **never
overwrite**. The file is a running log; each run adds one section. Use the safe
appender so a concurrent writer can't corrupt it:

```bash
python3 scripts/append_reflection.py \
    --file ~/.claude/reflection-notes.md \
    --section /tmp/self_assessment_section.md
```

It takes an advisory `flock` and appends atomically. If the script is
unavailable, append with `>>` in a single shell redirect (atomic for a lone
writer) — never read-then-rewrite the whole file. Structure the section: date +
method line (corpus size, digest record count), corpus baseline (interactive vs
automation denominator), a headline, the ranked candidates with verdict tags and
citations, an "explicitly NOT proposing" list, and cross-cutting observations.

When the ranked verdicts are decision-shaped (a small set of skill/automation/
fix/nothing calls the user will accept/reject), also emit them as a decision-card
one-pager per `~/.claude/commands/references/shared/decision-card-html.md`.

### Phase 5 — Re-measure the prior run

If a previous section exists in `reflection-notes.md`, add a **re-measure block**
comparing this run's key metrics against the last: **babysit share**, **retry /
interrupt count**, **automation-fleet share of sessions**, **schema-fail rate**,
and the top **error families**. This is the "measured rule" loop — a rule or fix
proposed last time whose metric now sits at target can **retire**; one that
regressed gets re-raised. State each prior candidate's status: held / regressed /
retired / superseded.

## Output contract

The run is done when `~/.claude/reflection-notes.md` carries a new dated section
with: ranked verdicts (each tagged skill/automation/fix/nothing), session-id
citations on every material claim, the re-measure block (if a prior run exists),
and an explicit not-proposing list. Diagnosis only — this skill decides and
records; it does not build, edit configs, or install anything.
