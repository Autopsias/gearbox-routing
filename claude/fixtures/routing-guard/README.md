# fixtures/routing-guard/ — seeded-violation cases for verify-routing.sh + render-routing-digest.py

Ported from the source deployment's `~/.claude/fixtures/routing-guard/` (S05, this
port), re-seeded for the PROVIDER-AWARE checks this repo's guard actually runs.
DROPPED on port: the `broken-tree/`, `unknown-agent-only/`, `parse-robustness.yaml`,
and `semantic-drift/` fixtures — those exercised the source guard's epic-dev
cross-check, agent-frontmatter-drift-in-a-live-deployment, and prose-consumer-stamp
checks, none of which this port carries (see GENERICIZATION.md "Drops" and
`claude/scripts/verify-routing.sh`'s own header comment for what was removed and
why). What ships here proves the checks THIS guard actually has.

Every fixture below is a full copy of the real `claude/model-routing.yaml` with
exactly one seeded violation — run the guard with `SSOT=<fixture>` to isolate it.

## YAML-level fixtures (`verify-routing.sh --core`)

| Fixture | Seeds | Expected |
|---|---|---|
| `control.yaml` | Unmodified copy — no violation | `exit 0` (PASS) |
| `missing-tier-in-provider.yaml` | Deletes `providers.gemini.models.frontier_reasoner` — a task_classes tier (`deep_reasoning`/`linchpin` both use `frontier_reasoner`) has no resolvable model under `gemini` | `exit 1` — "tier <-> provider-map completeness" DRIFT naming the missing tier |
| `bare-integer-version.yaml` | `version: "2.0.0"` -> `version: 4` (the OLD pre-semver-migration shape) | `exit 2` (tooling crash, not policy drift) — the guard hard-rejects a bare integer rather than silently accepting it as an alternate format |
| `invalid-active-provider.yaml` | `active_provider: anthropic` -> `active_provider: anthropic_typo` (not a key under `providers:`) | `exit 1` — "active_provider is a valid providers: key" DRIFT |
| `unresearched-active-provider.yaml` | `providers.anthropic.calibration.status: researched` -> `unresearched`, while it's still the `active_provider` | `exit 1` — "active_provider calibration.status != unresearched" DRIFT (ARCHITECTURE.md §3: never dispatch against an unresearched active profile) |

Run any of these:
```
SSOT=claude/fixtures/routing-guard/<fixture>.yaml bash claude/scripts/verify-routing.sh --core
```

## renderer-fixtures/ (`render-routing-digest.py`)

Standalone target/source files proving the renderer refuses to write on corruption
and enforces its safety invariants:

| Fixture | Seeds | Expected |
|---|---|---|
| `missing-markers.md` | Zero `<!-- BEGIN/END ROUTING -->` markers | Default (no `--install`): `exit 2`, refuses. `--install` would bootstrap it (not seeded here — see the repo's real `CLAUDE.md` install). |
| `duplicate-markers.md` | Two full marker pairs (corruption) | `exit 2` under BOTH default and `--install` — corruption is never a valid bootstrap target |
| `old-integer-marker.md` | The OLD source-deployment marker shape `<!-- BEGIN ROUTING (model-routing.yaml v4) -->` | `exit 2` even with `--install` — the grep-gate (`_assert_no_old_markers_anywhere`) rejects any surviving pre-migration marker text, decoupled-marker invariant |
| `prose-marker-mention.md` | ONE real marker pair PLUS a sentence mentioning the marker text inline (not on its own line) | `--check` reports `exit 1` (content DRIFT, since the placeholder body doesn't match a real render) — NOT a "duplicate marker" crash. Proves the marker scan is line-anchored, not a bare substring scan. |
| `oversized-source-template.md` | A copy of `model-routing.digest.md` with ~3.5KB of padding injected into the v0 variant body | `exit 2` — "rendered digest EXCEEDS budget" (40 lines / 1800 bytes), refuses to write a lossy/truncated block |

Run any of these, e.g.:
```
python3 claude/scripts/render-routing-digest.py --variant v0 --target claude/fixtures/routing-guard/renderer-fixtures/missing-markers.md
python3 claude/scripts/render-routing-digest.py --variant v0 --source claude/fixtures/routing-guard/renderer-fixtures/oversized-source-template.md --target /tmp/fresh.md
```

## Regenerating

These are point-in-time snapshots of `claude/model-routing.yaml` / `model-routing.digest.md`
with one deliberate edit each — re-copy the real files and re-apply the same edit if the
real SSOT's shape changes structurally (e.g. a block gets renamed) and a fixture stops
parsing.
