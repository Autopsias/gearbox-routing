# Routing system — operating runbook

**SSOT:** `claude/model-routing.yaml` (see its own `version:` / `last_reviewed:`
stamps). If a consumer disagrees with the SSOT, fix the consumer — except the
one documented carve-out: `degradation:` (and each provider's `degrade:`
block) mirrors the fallback ladder byte-checked in your harness's dispatcher
(e.g. Claude Code's `plan-execute/run.py::_FALLBACK_LADDER`) — see
`docs/INTEGRATION.md`. "Automatic" is defined per-surface: dispatched work is
automatic BY CONSTRUCTION (agent frontmatter pins + the drift guard); the
main/interactive session is ADVISORY-WITH-OBSERVABILITY (an interactive
session can't switch its own model — classify, recommend `/model`/`/effort`,
log mismatches to `MISROUTES.md`).

**Guard command:** `bash claude/scripts/verify-routing.sh [--core|--full]
[--strict]`. Bare/default mode = `--full`. `--strict` promotes "unknown
agent, no SSOT row" from WARN to FAIL — full mode implies `--strict`.
Sub-checks: (a) agent frontmatter vs SSOT, (b) cheap_fast-tier-has-no-effort
invariant (a tier whose model rejects the dial must carry no effort param),
(c) prose-consumer version stamps (full mode only), (d) harness fallback-ladder
lockstep where a consumer mirrors one (ast-parsed/byte-matched, never
imported or executed), (e) `CLAUDE.md` digest re-render diff, (f) active
runtime-override visibility (informational only — files can be green while
the runtime routes elsewhere via an env override).

## Enforcement point

Wire the guard to **auto-fire on every commit that touches
`claude/model-routing.yaml`** via a pre-commit hook — no manual invocation
needed for routine work. It fails CLOSED on both a policy violation (drift
found) and a tooling crash (missing dep, malformed SSOT, traceback); a broken
guard must never silently become a bypass. Document any recovery escape
hatches inline in the hook itself — never reach for `--no-verify`.

## Operating commands

Two skills operate this runbook's triggers end-to-end: **`/routing-update`**
(`claude/skills/routing-update/`) drives any model-landscape change — new
model / pricing / deprecation / provider version bump — through research →
one operator-approved changeset → every consumer surface regenerated +
guard-verified + committed (it owns the "SSOT change protocol" below).
**`/routing-retro`** (`claude/skills/routing-retro/`) is the manual, nudged
retrospective: a deterministic transcript scan plus a judgment pass over
recent sessions vs `task_classes:`, proposing `MISROUTES.md` entries
(operator-confirmed) and routing calibration proposals back to
`/routing-update`. Retro is read-only; update is the single apply-path.

## Re-run triggers

| Trigger | What to run | Why |
|---|---|---|
| **Every commit touching the SSOT** | Guard fires automatically via the pre-commit hook (no action needed) | Continuous drift detection |
| **Every coding-agent binary/runtime version bump** | Your effort-dial canary/preflight check, if you have one | Effort-dial honoring can be runtime-version-dependent and unobservable post-hoc — a build can silently make the effort param inert |
| **New model GA on your active provider** | Full recalibration pass (`/routing-update`) | A new frontier/tier changes the cost-quality frontier every row in `task_classes:` was tuned against |
| **≥2 entries land in `MISROUTES.md` since `model-routing.yaml`'s `last_reviewed`** | `/routing-retro`, then `/routing-update` on any accepted finding | Two independent misroutes since the last calibration is signal that the table has drifted from reality, not noise |
| **A price change** (any row in `prices:`) | `/routing-update` (at minimum, re-check the $/successful-outcome math against your existing eval data before re-sweeping) | The cost-per-outcome frontier is priced off `prices:`; a stale price silently misprices every routing recommendation |

## Misroute ledger

`claude/evals/routing/MISROUTES.md` — append-only (never edit/delete a past
row). One entry per observed mismatch: date, task shape, expected class →
actual, cost symptom. Your `CLAUDE.md` routing digest's "routing receipt"
line should point here — every delegated/escalated task that diverged from
its `task_classes:` default ends its turn with a receipt, and a real
mismatch gets logged. This ledger is the counter for the "≥2 entries" re-run
trigger above, so entries must stay dated and never be silently pruned.

## SSOT change protocol

1. **Version bump.** Every substantive change to `model-routing.yaml` bumps
   `version:` and updates `last_reviewed:` (see `docs/VERSIONING.md`). A
   content change without a version bump is a guard gap by design intent, not
   a supported path.
2. **Evidence comment.** Every diff cites its source: an eval result, an
   official provider doc (verified via a connected research MCP), or an
   explicit operator decision with a date. Durable rationale goes in
   `CHANGELOG.md` and `docs/METHODOLOGY.md` — not as inline decision-history
   prose blocks inside the SSOT (see the SSOT's own header note on this).
3. **Operator-approved.** No SSOT change is auto-mutated by an eval result.
   `/routing-update` proposes diffs; the operator approves (or rejects/tunes)
   each one before it's applied.
4. **Regenerate every consumer in the same change.** Re-render the `CLAUDE.md`
   routing digest, bump any `<!-- routing-ssot: vN -->`-style stamps in
   consumer files to match (see `model-routing.yaml`'s `consumers:` block for
   the full list), then run the guard (`--core` and `--strict`) as the
   release gate before committing.

## Peer-gate-miss promotion — owner-gate

`model-routing.yaml`'s `codex_peer.lint_keywords` block is an ADVISORY
prose-match (a session's title/summary/deliverable hits an
architecture/irreversible/security keyword without an `adversarial-review`
gate → a Polish-severity flag in a plan-hardening lint, never a plan-killer).
Its documented promotion condition: **any observed peer-gate miss** — a
`MISROUTES.md` entry classed `peer-gate-miss` (a codex-lane trigger fired in
practice with no gate, and it mattered) — **promotes `task_class` detection
to a structured field** in whatever plan/manifest schema your consumer uses,
instead of this prose match. **Owner-gate:** the next routing reviewer who
touches `model-routing.yaml`'s `codex_peer:` block (i.e. whoever runs the
next SSOT change protocol above) is responsible for checking `MISROUTES.md`
for a `peer-gate-miss`-classed entry and executing the promotion if one
exists. This is a manual owner-gate, not a green-checkmark — there's no
on-disk artifact to lint for a runtime prose-match.

## Related files

- `claude/model-routing.yaml` — SSOT
- `claude/scripts/verify-routing.sh`, `claude/scripts/render-routing-digest.py` — guard + renderer
- `claude/scripts/resolve_route.py` — provider-neutral resolve/escalate/degrade (see `docs/INTEGRATION.md`)
- `claude/fixtures/routing-guard/` — seeded-violation fixtures proving the guard catches each failure class
- `claude/evals/routing/MISROUTES.md` — misroute ledger
- **Your own `results/` dir** (not shipped — see `GENERICIZATION.md`): keep sweep
  outputs, eval task registries, and calibration memos wherever your own eval
  harness writes them; point `research_ref` fields at that location once you
  have one.
