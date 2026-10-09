---
name: memo-loop
description: Critique a decision memo against a rubric compiled for that memo, or draft one from scratch, from Codex — capped clarifying intake, a binary rubric, an independent Claude critic that only enumerates problems and never rewrites, tool-grounded findings with the unconfirmed ones discarded, and typed terminal states instead of a silent "done". It returns a CRITIQUE, not a rewritten memo. Use when someone wants a memo reviewed, stress-tested or written from scratch — "critique my memo", "what's wrong with this memo", "review this draft", "poke holes in this", "create a memo about X", "write me a decision memo". Not for pre-send verification (that is a separate pre-send review step, not included here).
---

# memo-loop (Codex harness)

Anything can make a memo *look* finished. This loop makes it measurably better, and says so
honestly when it did not: every run scores the memo against a rubric compiled for **this**
memo, hands it to a critic that cannot edit it, and ends in a **typed** terminal state that
distinguishes "nothing material found" from "the critic never ran". It returns a critique, not a
rewritten memo — the automated revision loop was measured against single-pass rewrites, lost twice, and
was descoped.

**You are the author here.** Under this port the drafting model is GPT (this session); the
independent critic is Claude, dispatched via `claude -p`. That is the one thing that inverts
relative to the Claude-side skill — everything else (the rubric, the findings contract, the
discard gate, the terminal states, the run-state layout) is byte-identical and lives in exactly one
place, shared by both harnesses:

Every path below of the form `~/.claude/skills/memo-loop/references/<file>.md` is **shared,
not copied** — reading it from here is reading the same text the Claude-side skill reads.
This port's own SKILL.md restates only what differs.

**Full non-negotiables and phase contracts:** `~/.claude/skills/memo-loop/SKILL.md` — read it
for the eight non-negotiables and the checklist verbatim; they govern this port exactly as
written, no exceptions granted here. This file exists to state the one place they diverge
(critic transport) and the operational facts specific to running from Codex.

---

## No Claude-only tooling in the core loop

This port never issues a Task-tool dispatch, an Agent-tool call, a Skill invocation, or names
any MCP server. Every step is a plain file operation (read/write/hash under the run dir) or a
shell command (`claude -p`, `codex exec`, `command -v`, reading `~/.codex/config.toml`). Where
the shared references describe a "fresh subagent dispatch (Claude Code)" for a drafter or
critic, the Codex-side equivalent is a fresh `codex exec` or `claude -p` invocation — already
stated per-case in `~/.claude/skills/memo-loop/references/council.md` and, for the critic, in this port's own
`references/codex-transport.md`.

## Modes and phases — pointer table

Identical modes (`improve` / `create` / `reflect` / `status`) and identical phase order. Read
each contract when you reach that phase, exactly as the Claude-side skill instructs:

| Phase | Contract | Note for this port |
|---|---|---|
| 0 · Intake + rubric | `~/.claude/skills/memo-loop/references/intake-and-rubric.md` | Unchanged. Its Step 1 write-target assertion already checks **both** `~/.claude/skills/memo-loop/` and `~/.codex/skills/memo-loop/` — see "No runtime writes" below. |
| 1 · Critique | `~/.claude/skills/memo-loop/references/critique.md` | **Transport (Step 1 + Step 3, rungs 1 and 3) is overridden by `references/codex-transport.md`** — the one Codex-only file this port renders. Everything else in `critique.md` (the envelope schema, the findings contract, the discard gate, the failure ladder's demote/abort logic, the pairwise-ranking envelope, the fault-injection switch) is unchanged and governs here exactly as written. |
| 2 · Deliverable | `~/.claude/skills/memo-loop/references/critique-report.md` | Unchanged. |
| 3 · Handoff + lessons | `~/.claude/skills/memo-loop/references/critique-report.md`, then `~/.claude/skills/memo-loop/references/reflect.md` §2 | Unchanged. |
| `create` only | `~/.claude/skills/memo-loop/references/council.md` | Unchanged — already states the Codex-side drafter dispatch (`codex exec`) inline per step. |
| `reflect` only | `~/.claude/skills/memo-loop/references/reflect.md` | Unchanged. `reflect` remains explicit-invocation-only in this port too. |

Supporting references, same pointers as the Claude-side skill: `run-state.md` (every run-dir
file and its template), `rubric-backbone.md` (the candidate-item pool),
`calibration-exemplar.md` (the shipped bootstrap exemplar).

## Critic transport — the one inversion

`references/codex-transport.md` — **the only Codex-specific reference this port renders** —
holds the inverted critic ladder (rung 1: nested `claude -p`, pinned `opus`/`high`; rung 3: a
fresh `codex exec` on a *different* GPT model) and the sandbox caveat: Codex's
`workspace-write` sandbox does **not** unconditionally block a nested `claude -p` call —
availability is configuration-dependent on `sandbox_workspace_write.network_access`, and a
network denial there surfaces as a **misleading login error**, not a sandbox error. The port
probes and classifies the real reason before ever telling the user to run `/login`. Read that
file before Phase 1's first dispatch under this port; it states its own precedence over
`critique.md` explicitly, and nowhere else needs to.

**Never silently degrade.** A rung-1 failure prints the mandated warning (verbatim in
`codex-transport.md`) and states the fallback and the resulting `reviewer_independence`
label in the critique report. A same-family (same-provider) fallback that reaches
`SATISFIED` without that warning having been shown is a contract violation.

## No runtime writes under `~/.codex/skills`

`~/.codex/skills/memo-loop/` — where this port is installed by `gearbox deploy` — is a
**deploy target**, not a working directory (the `[codex-render]` section of
`scripts/deploy.pathspec`). Any runtime file that appears there — a run dir, a lessons file, a calibration
archive — is classified **HARNESS-CODE** and **aborts the next `gearbox deploy`** until
`gearbox harvest` carries it back into the source repo, which is never the intent for a
memo's transient run state.

This is already enforced by the shared reference, not restated here as a separate rule:
`intake-and-rubric.md` Step 1 resolves all four write targets (run-state dir, lessons file,
calibration dir, promotion dir) to absolute, symlink-resolved paths and hard-aborts if any
resolves under `~/.claude/skills/memo-loop/` **or** `~/.codex/skills/memo-loop/` — the
dual-harness form of the same assertion, already written to cover this port. Nothing in this
file needs to duplicate it; only the *consequence* (a deploy-blocking hotfix, not just "your
work vanishes on redeploy") is stated here because it is Codex-deploy-specific.

## Interoperable run-state, both directions

State files, budgets, stop rules and the lessons schema are **identical paths and formats**
in both harnesses (`run-state.md`, `reflect.md`) — a memo improved from Codex and then
reopened from Claude Code (or vice versa) reads the same `run.json`, the same
`lessons-entries.jsonl` shape, the same resolved lessons store. Nothing in this port
namespaces state by harness; `runtime_detected` records which harness ran *that* pass
(`"codex-cli"` or `"codex-desktop"`, alongside the existing `"claude-code"` /
`"claude-desktop"` values), not which harness owns the file.

## Runtime discovery — Codex CLI and the Codex desktop app

Both facts below are cited, not assumed; re-check either before a future render if the
surrounding layout changes.

- **`~/.codex/skills/<name>/SKILL.md` is confirmed live** for both the CLI
  and the desktop app's App Server: OpenAI's
  own App Server documentation — which is what backs the desktop app, not just the CLI —
  gives skill paths in exactly this shape (`/Users/me/.codex/skills/skill-creator/SKILL.md`,
  used by its `skills/list` and `skills/config/write` calls). Source:
  https://learn.chatgpt.com/docs/app-server#skills . **No separate desktop install step is needed beyond the same
  `gearbox deploy` render** that installs the CLI copy — both read the same tree.
- **A second, separate root exists and is already accounted for by this repo's shadow
  convention:** OpenAI's "Build skills" docs (same fetch,
  https://learn.chatgpt.com/docs/build-skills#where-codex-loads-local-skills) name
  `$HOME/.agents/skills` as the documented USER-scope location, alongside per-repo
  `.agents/skills` scopes. This repo's other Codex ports already guard against a same-named
  duplicate appearing there (see `~/.claude/skills/plan-harden/codex/manifest.toml`'s `shadows` field) — the
  same mechanism this port would use if one ever appeared. If `~/.agents/skills` has no `memo-loop` entry, this port's `manifest.toml` declares
  `shadows = []`. Re-check before a future render if a `memo-loop` skill is ever added to
  `~/.agents/skills` by another source.

## Not ported

The Agent-tool `model` override (Claude-only — `~/.claude/skills/memo-loop/references/council.md` already states the
Codex-side drafter dispatch is a fresh `codex exec` invocation instead) and the ralph-loop
plugin. This port runs plain sequential phases; a single critique pass needs no iteration engine.

## Boundary with pre-send review

Unchanged from the Claude-side skill: memo-loop improves and creates; deciding whether a
finished memo may be sent belongs to a separate pre-send review step, not included here.
`trust-handoff.json` is still written in Phase 3, so a board-bound memo improved from this
port can hand off to such a step without translation.
