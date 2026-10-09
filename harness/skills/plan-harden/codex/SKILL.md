---
name: plan-harden
description: Harden a plan-builder plan from Codex before it ships — Codex-only adversarial review, premortem, model + parallelization lints, severity-tagged synthesis, applied and backported to spec.json; not the Claude+Codex dual-model round (that's the Claude-side /plan-harden).
---

# Plan Harden (Codex harness — Codex-only review)

You are the **hardening orchestrator** — the interactive `codex` session the
operator is typing into. You harden one `spec.json`-backed plan **directory**
(`PLAN.html` + `manifest.json` + `spec.json` + `sessions/*.prompt.md`) — the
same directory `plan-execute`/`plan-builder` produce and run. This port has no
concept of Claude Code's freeform "plan mode" plan file; that stays the
Claude-side `/plan-harden`'s territory.

Settled contract, with a cited probe behind every claim:
`~/.claude/skills/plan-execute/references/dual-harness-contract.md` (§ 6.4
names this port explicitly). It wins over this file on any disagreement.

**Explicitly OUT OF SCOPE: the dual-model Claude+Codex round.** The Claude-side
`/plan-harden` runs `/adversarial-review` as a Claude fork *and* a background
Codex pass, then a Codex verify loop that checks the Claude fork's fixes. This
port runs the Codex reviewer **once**, Codex-only — there is no second model to
cross-check the first, so there is no verify loop either. If the operator wants
cross-model verification, hand the plan directory to Claude Code and run
`/plan-harden` there — say so, don't improvise a substitute.

There is **no Task tool and no Workflow tool here**. Every enrichment step
that the Claude version spawns as a parallel fork, you do yourself, inline,
sequentially — the same "you are the worker" shape `plan-execute`/codex
already establishes for session dispatch.

---

## Preconditions

1. **Launch mode.** Phase 2 nests `codex exec` (dispatching the reviewer), so
   this session's shell commands must run **unsandboxed** — identical
   constraint to `plan-execute`/codex, same proof (contract P2/P3):

   ```
   codex --sandbox danger-full-access -C <repo-root>
   ```

2. **The plan must be a plan-builder directory.** A sibling `spec.json` +
   `manifest.json` + `sessions/*.prompt.md` must exist. If not, this port
   cannot run — it has nothing to backport into. Refuse and say so.

3. **`codex exec` refuses to run outside a git repository** (measured,
   codex-cli 0.145.0) — the plan's working tree must be a real repo.

---

## Phase 0 — Enrichment (inline, sequential — no forks)

Two of the Claude version's four forks apply here; two don't, and you say why
in the summary rather than silently dropping them:

| Fork | This port |
|---|---|
| A — project memory | **Kept.** `~/.claude/projects/<cwd-slug>/memory/MEMORY.md` is a plain markdown file, not a Claude-tool dependency — read it yourself (`cat` + grep for 3–7 keywords from the plan's title/sessions). If it doesn't exist, note the path you looked at and move on. |
| B — external research | **Dropped.** Perplexity/Exa are Claude Code MCP servers not present in this harness. Note: `research: n/a (Claude-only MCPs)`. |
| C — edge cases | **Kept, inline only.** No `bmad-review-edge-case-hunter` skill exists here — use the **inline edge-case prompt** template verbatim from `~/.claude/references/plan-harden/fork-prompts.md` § Fork C, but run it as your own reasoning (no subagent to spawn it to). ≤5 cases, each with boundary / failure path / severity. |
| D — territory blindspot | **Dropped.** `blindspot` is a Claude-only skill. Note: `blindspot: n/a (Claude-only skill)`. |

Read the plan (`PLAN.html` + `spec.json`) once before either step.

**Early model-lint pass (advisory)**: run § 4.0's model-lint NOW as
well — it is deterministic and near-free, and this port's expensive step is the
`xhigh` reviewer dispatch. A 🔴-class hit (`peer-gate-missing`) discovered only at
Phase 4 arrives AFTER that spend; discovered here, it shapes the review prompt and
can be fixed first. The § 4.0 pass remains the AUTHORITATIVE one — never skip it
because this pass ran.

On a schema-v8 plan (`manifest.json` `plan_schema_version` >= 8) the lint checks the class and
risk declarations, not the model pick (see § 4.0).

---

## Phase 1 — SKIPPED (structurally, not conditionally)

There is no Codex port of `/grill-with-docs`. This is not `--quick` mode's
skip (which is conditional and user-chosen) — it is a permanent absence in
this port. Record it in the summary as `grill: n/a (no Codex port)`, never as
`⊘ skipped`.

---

## Phase 2 — Adversarial review (Codex-only, single pass)

### 2.1 Dispatch the reviewer as a nested, isolated `codex exec`

Same isolation pattern `plan-execute`/codex uses for session dispatch: a fresh
process, not your own context, so the review isn't anchored to whatever you
were just reasoning about.

**Model/effort: `gpt-5.6-sol` · `xhigh`** — the Codex rubric's "integration /
multi-file / non-obvious" rung (the `openai` ladder in
`~/.claude/model-routing.yaml`). A plan review is exactly that shape: cross-session, cross-file,
consequence-heavy. Don't crank to `max` by default — that's the linchpin rung,
reserved for one-shot irreversible work, not a standing review setting.

Command shape (mirrors `run.py::_codex_cmd`, the same flags `plan-execute`
already proved — `--ignore-user-config --ignore-rules --sandbox
workspace-write`, `-c project_doc_max_bytes=262144` so the repo's whole
AGENTS.md loads rather than Codex's default first 32 KiB, `rm -f` the output file first, `-o` for last-message):

```bash
cd <repo-root> && \
  rm -f <plan-dir>/_codex/adversarial-review.<stamp>.last-message.txt && \
  codex exec --ignore-user-config --ignore-rules --sandbox workspace-write \
  -c project_doc_max_bytes=262144 \
  -m gpt-5.6-sol -c model_reasoning_effort=xhigh \
  -o <plan-dir>/_codex/adversarial-review.<stamp>.last-message.txt - <<'PROMPT'
Use $adversarial-review to stress-test the plan at <plan-dir>/PLAN.html
(spec of record: <plan-dir>/spec.json). This is a plan-builder execution
plan: sessions, dependencies, verify gates, checkpoints, shipping actions.
Supported decision: whether to run this plan as-is. Review-only — do not
edit any file.
PROMPT
```

Capture stdout+stderr to `<plan-dir>/_codex/adversarial-review.<stamp>.stderr.txt`.
Classify from `(exit code, output file)` exactly like a session dispatch: exit
0 + a `## Findings` section present is normal; anything else is a failed
review — note it in the summary as `adversarial ⊘ failed: <reason>` and still
proceed to Phase 3/4 (same non-halting rule the Claude version uses).

### 2.2 The seam — adapt here, never edit the reviewer

`~/.claude/skills/adversarial-review/codex/SKILL.md` (the render source for
`~/.codex/skills/adversarial-review/`) is a **general-purpose** review skill.
Its Mandatory Final Format is a `## Findings` block with `blocking | material
| minor` impact labels and a `## Claude Handoff` section — **not** the
Claude-side `/adversarial-review`'s `[HARDENED:...]`-tagged auto-edit
contract. Two things follow:

1. **Never ask it to revise.** Its own contract states it "remains in
   review-only mode unless the user explicitly asks to revise" — and asking it
   to revise would trade a documented, parseable Findings format for an
   undocumented one. Keep the dispatch prompt review-only (§ 2.1). *You* apply
   hardenings, in Phase 4, using its Findings as input.
2. **You own the severity translation.** Map its labels onto this port's
   buckets — this mapping is the entire adaptation, and it lives here, not in
   the reviewer:

   | Reviewer `impact` label | plan-harden severity |
   |---|---|
   | `blocking` | 🔴 plan-killer |
   | `material` | 🟡 polish |
   | `minor` | 🟣 known-debt |

If a future update to the reviewer skill changes its output shape, fix the
parse in this file. **Never edit `skills/adversarial-review/codex/` to make it
match what this port expects** — that skill has other callers, and § 4.5's
abort rule forbids it outright.

---

## Phase 3 — Klein premortem (inline reasoning, no model call)

Identical to the Claude version's § 3 — this is *your* reasoning, not
delegation. Self-prompt:

> Ship date + 6 months. The plan at `<plan-dir>` was implemented and shipped.
> Today, it broke. Walk back from the failure: what was the most likely root
> cause? Be specific. Do NOT name "general complexity" alone.

Weigh it against any Fork A/C findings that name a concrete landmine before
finalizing. Produce a 3–5 sentence hypothesis + one classification tag from
the Claude version's list (`assumption_drift`, `missing_dependency`,
`scope_creep`, `production_constraint`, `vendor_change`, `team_handoff`,
`monitoring_blind_spot`, `rollback_failure`).

**Independence timing**: you MAY form the hypothesis before or
while the Phase 2 reviewer runs — Klein independence holds as long as it is
committed BEFORE you read the reviewer's Findings block. The final reconciliation
(and any revision of the class tag) happens after Phase 2 lands and may weigh its
findings the same way it weighs Fork A/C's.

---

## Phase 4 — Synthesis, apply, backport (idempotent)

### 4.0 Model-selection sanity lint — unchanged, reused verbatim

Scripts + prose, not model-bound, so nothing about this harness changes it.
Data source order and full rule set:
`~/.claude/references/plan-harden/model-lint.md` — read it fresh,
it is harness-neutral. Data source: `~/.claude/model-routing.yaml`
(`task_classes:`) when it parses; else
`~/.claude/skills/plan-builder/references/schemas.md`. Note
`model-lint source=ssot|schemas-fallback` in the summary. The 🔴-capable
flags (`peer-gate-missing`, plus the three v8 rules above) still block here exactly as it does on the Claude
side.

**Schema v8 (route-at-dispatch):** the executor picks the model from `task_class`, so
the lint no longer grades the author's model pick. Rules that compare a declared `model`
with the rubric run on a v8 session only when it declares an override. Four v8 rules are
🔴 and deterministic: `task-class-missing`, `override-without-reason`,
`override-incomplete` (only one of `model`/`reasoning` set), and
`override-below-floor` (session has `peer_triggers` or is `linchpin`; the override's cell
ranks lower than the class default's on the provider's ladder, per
`resolve_route.below_floor` — never a hand-written ladder; an override whose provider
differs from the tree's provider is cross-provider and skips it, then a model not on the
ladder skips). `locked-check-suggested` 🟡: the
session depends on a session whose deliverable is a test file and declares no
`verify.locked`. Below v8 nothing changes. Full text: `model-lint.md` → "Schema v8".

### 4.0b Parallelization-opportunity lint — unchanged, reused verbatim

Same reference as the Claude side, harness-neutral:
`~/.claude/references/plan-harden/parallelization-lint.md` — the
write-conflict matrix, the five deterministic blockers, the semantic-ordering
suppressor, the session-split patterns, and the verdict + decision-card contract.
Runs HERE, after Phase 2's hardenings are known, because they can change the DAG.
Hold `PARALLEL_LINT = {verdict, blockers, options}`. Port-specific notes: the
≤3-option decision card is presented to the operator **in this interactive
session** (there is no separate chat surface); **never auto-apply**
`parallel_group` — the operator elects, and an elected change lands via the
normal spec-edit → rebuild path (§ 4.4). Also honour the reference's caveat that
the concurrency-safety facts cite `plan-execute`'s Claude-side dispatch machinery
— on this harness `plan-execute`/codex dispatches a batch **sequentially by
default** (parallelism is a shell detail the operator can
adopt later), so grouping bought here pays out under the
Claude harness or only if the operator deliberately backgrounds the batch; say
that in the option trade-offs.

### 4.1 Build the summary block

Same severity rule as the Claude version (§ 4.1 there): 🔴 requires quoted
evidence or downgrades to 🟡. Pull findings from Phase 0 (memory + inline edge
cases), Phase 2's `## Findings` (translated per § 2.2), Phase 3's premortem,
Phase 4.0's model-lint, and Phase 4.0b's parallel-lint (verdict in the
Phases-run line; any `data-dependency-in-prose` hit under 🟡; the operator's
election recorded).

### 4.2 Write the sidecar — `<plan-dir>/PLAN-HARDEN.md`

A plan-builder plan directory has no single freeform "plan file" to append a
summary section to (`PLAN.html` is generated, `spec.json` is structured JSON).
The established convention already in this repo (`_plans/*/PLAN-HARDEN.md`) is
the sidecar: **not** regenerated by `build_plan.py`, so it survives a rebuild.

Idempotency rule identical to the Claude version's § 4.2: search for an
existing `## /plan-harden Summary` heading in `PLAN-HARDEN.md` and REPLACE it;
if the file or heading doesn't exist, create/append. This port's sidecar has
no `## Grill auto-accept log` section (Phase 1 doesn't exist here) — don't
fabricate one.

```markdown
# /plan-harden run artifacts (Codex-only port)

Sidecar for the Codex `plan-harden` port's output on this plan-builder plan.
The plan-of-record is `spec.json` (rendered to `PLAN.html` +
`sessions/*.prompt.md`); harden edits are applied to `sessions/*.prompt.md`
and backported to `spec.json` in Phase 4. This file is NOT regenerated by
`build_plan.py`, so it survives a rebuild.

## /plan-harden Summary

**Run metadata**: timestamp <ISO8601>, harness codex, reviewer gpt-5.6-sol·xhigh
**Phases run**: enrichment <✓ N hits> (memory: <n>, research: n/a, edge-cases: <n> inline, blindspot: n/a), grill n/a (no Codex port), adversarial <✓ N findings translated | ⊘ failed: <reason>>, premortem ✓, model-lint <✓ N flags | clean> (source=<ssot|schemas-fallback>), parallel-lint <linear-optimal | opportunities (elected: <choice>) | ⊘ (error: <reason>)>

**🔴 Plan-killers**:
- ...

**🟡 Polish**:
- ...

**🟣 Known-debt**:
- ...

**Premortem (6mo failure prediction)**: <paragraph> [class: <tag>]

**Hand-off envelope**:
plan-harden:
  hardenings_applied: <n>
  blockers_remaining: <n>
  premortem_class: <tag>
  recommend_exit_now: <yes|no>
```

### 4.3 Apply surviving 🔴/🟡 hardenings into session prompts

For each affected session, `Read <plan-dir>/sessions/sNN.prompt.md`, find the
body between the `## Work` heading and the first of `## Verification gates` /
`## Post-session actions` / `## Closeout`, and edit it to fold in the
hardening (same target Claude's § 4.2b names).

### 4.4 BACKPORT to `spec.json` — mandatory, never skip

This is the whole reason the discipline exists: `PLAN.html` and
`sessions/*.prompt.md` are **generated** from `spec.json`. A later
`build_plan.py --rebuild` silently wipes anything applied only to the
generated files.

1. For each edited session, set `spec.json`'s session `prompt` field to the
   current prompt-file body you just edited (same slice: between `## Work` and
   the first of the three headings, stripped).
2. Write `spec.json` back: `json.dump(..., indent=2, ensure_ascii=False)` +
   trailing newline (preserve the existing shape).
3. **Verify the round-trip.** Rebuild to a **temp** directory — never the live
   plan dir:
   ```bash
   python3 ~/.claude/skills/plan-builder/scripts/build_plan.py <plan-dir>/spec.json /tmp/<x>
   ```
   Diff the regenerated `sessions/*.prompt.md` bodies against the live ones. They
   must match **after normalising the plan-directory path** — `build_plan.py`
   stamps the output directory into a header line (*"The plan dashboard is at the
   sibling `PLAN.html` in this directory: `<dir>/`"*), so a temp-dir build ALWAYS
   differs there and a literal byte-for-byte demand can never be met (measured). Normalise that one line, then require byte equality on everything
   else. Report the result in the summary.

### 4.4b Contradiction sweep — deterministic, after EVERY batch of edits

The highest-yield check in the whole command, and it needs no model call. Every
hardening you apply claims to REPLACE something; assert that what it replaced is
actually gone, across the WHOLE `spec.json`:

0. **Read the builder's own advisories first** — a `build_plan.py` rebuild prints
   three, free and deterministic: a session too wide to review, PROSE naming a
   session OR ITEM id the plan does not have (a session that was split, or an item
   dropped or renumbered, whose old id survives in a sibling's text — this sweep's
   class, caught mechanically), and a program embedded in a session prompt.
1. **Deleted-phrase sweep** — grep for the superseded rule's distinctive phrasing.
   A survivor outside the new text is a finding at the original hardening's
   severity (allow-list deliberate negations like *"there is no X"*).
2. **Item-vs-prompt sweep** — no `items[].deliverable` / `human_summary` / `why`
   may contradict the session prompt that consumes it. The common shape is a
   prompt made branch-conditional while its item still demands the work
   unconditionally.
3. **Field-vs-prose sweep** — every `deliverable`, `verify.checks[].assert` and
   `dispatch.checkpoint` must be satisfiable on EVERY branch the prompt permits.
4. **Schema-vs-writer sweep** — where a record shape is declared in one place and
   written in another, the field lists must match exactly.

Probe any all-clear with a known positive before trusting it: a sweep pointed at
the wrong string reports "clean" and "I did not look" identically. Record
`sweep ✓ N classes clean` (or the survivors) in the sidecar summary.

*Why (measured over repeated hardening passes on one large plan): a fix
applied in one place while the text it replaces survives elsewhere was the
majority defect in every single round, including two contradictions the hardening
patches themselves introduced. An ad-hoc version of this sweep caught findings
that neither reviewer model found.*

### 4.4c Reviewability — flag a session too wide to converge

The ONLY signal with no measured counter-example is that a session's findings did not FALL
between two review passes. Flag that. `build_plan.py`'s wide-session warning (p90 of 6 files,
because one review pass samples a surface that size at low recall) and a long brief are HINTS
worth a look, not flags: on one measured plan a longer session writing more files CONVERGED
while a slightly shorter one writing fewer did not, so both proxies were falsified on the
same plan. If findings concentrate on a code snippet inside the prompt, the remedy is to MOVE the
code to whoever owns its tests, not to split the session.

Never split automatically: splitting changes the DAG, `depends_on` and gate placement. Offer at
most three options — split at a named item boundary, extract the untestable part to the session
that owns its tests, or accept with the human checkpoint as the control — and record the
operator's election in the sidecar.

### 4.5 Abort condition

If the only way to land a hardening is editing `manifest.json` directly
instead of through `spec.json` + rebuild — **STOP**. `manifest.json` is the
immutable dispatch graph; only `build_plan.py` may regenerate it, and only
from `spec.json`. Surface the blocked hardening as a finding for the human
instead of hand-editing it.

---

## What differs from the Claude harness — summary

- No Phase 1 (grill) — structurally absent, not conditionally skipped.
- Single-pass Codex-only review — no dual-model round, no Codex verify loop
  (nothing to cross-check the reviewer against).
- Enrichment forks run inline, sequentially, by you — no Task tool, no
  Workflow tool.
- Output lands in a `PLAN-HARDEN.md` sidecar, not a mutated freeform plan
  file — this port only targets plan-builder plan directories.
- Backport-to-`spec.json` + rebuild-and-diff is **not optional** here (it is
  conditional in the Claude version, gated on "if this run applied hardenings
  only to generated files") — this port has no other kind of plan file, so
  every applied hardening is backported, every time.

## References (shared — read, do not copy)

- `~/.claude/skills/plan-execute/references/dual-harness-contract.md` — the settled contract, § 6.4 names this port.
- `~/.claude/references/plan-harden/model-lint.md` — the full lint rule set (§ 4.0).
- `~/.claude/references/plan-harden/parallelization-lint.md` — the parallel-lint rule set (§ 4.0b).
- `~/.claude/references/plan-harden/fork-prompts.md` — Fork A/C prompt templates (inline variant reused; Fork B/D not applicable here).
- `~/.claude/skills/adversarial-review/codex/SKILL.md` — the review engine this port dispatches (Phase 2).
- `~/.claude/skills/plan-builder/scripts/build_plan.py` — the backport target (§ 4.4).
- `~/.claude/skills/plan-builder/references/schemas.md` — model-lint fallback data source.
