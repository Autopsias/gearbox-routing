# Recon toolkit — deterministic pre-audit recipes

## Contents
- [Size map](#size-map)
- [Dead-code tooling by language](#dead-code-tooling-by-language)
- [Unreferenced-module scan](#unreferenced-module-scan)
- [Flag cross-check](#flag-cross-check)
- [Test-value probe](#test-value-probe)
- [Test removal gates](#test-removal-gates)
- [Reachability rules that decide dead vs alive](#reachability-rules-that-decide-dead-vs-alive)
- [Output handling](#output-handling)

## Size map

```bash
for d in <src-root>/*/; do
  echo "$(find "$d" -name '*.<ext>' -exec cat {} + 2>/dev/null | wc -l | tr -d ' ') $d"
done | sort -rn
```

Run once for app code, once for tests, once for scripts. Record the three
totals — every later number in the report is stated against them. Expected
observation: a handful of dominant packages; those become shard seeds.

## Dead-code tooling by language

Tool output is **candidate input** for the shards, never a finding by itself.
Every tool below over- and under-triggers; the shard re-verifies.

| Language | Tool | Invocation | Notes |
|---|---|---|---|
| Python | vulture | `uv run --with vulture vulture <src> --min-confidence 90` | High-confidence unused vars/imports; identical dead blocks copied across files are a tell of a copied dead interface |
| Python | ruff | `ruff check --select F401,F811,F841 <src>` | Unused imports/redefinitions; usually pre-commit-clean already |
| JS/TS | knip | `npx knip` | Unused files, exports, deps; needs its config to know entry points |
| JS/TS | ts-prune | `npx ts-prune` | Unused exports only; noisy on barrel files |
| Any | git staleness | `git log -1 --format=%ci -- <path>` per candidate | Age alone proves nothing — pair with reachability |

## Unreferenced-module scan

For each module file, check whether its import path (or basename in an import
statement) appears anywhere in app code, scripts, or tests other than itself.
Python pattern (adapt paths/extensions per repo):

```python
# For each module: skip if its dotted path appears in the corpus of all other
# files, or an import statement references its basename. Survivors are
# unreferenced-module candidates, sorted by size.
```

Known blind spots — the shard must check these before believing a candidate:
dynamic imports/registries, entry points declared in config (routers, CLI
entry points, plugin tables), and re-exports via `__init__`/barrel files.

## Flag cross-check

The feature-flag graveyard is its own debt class (industrial precedent: Uber's
Piranha exists solely to delete stale-flag code). Recipe:

1. Inventory: grep the settings/config surface for the flag naming pattern
   (`*_ENABLED`, `feature_*`, config-map keys). Record each flag's default.
2. Runtime value: resolve each flag against what production actually loads
   (compose file, env template, deploy config — read the real file, not the
   example).
3. Readers: for each flag, grep app code excluding the config files. Zero
   readers = dead flag (candidate). Readers exist but the flag is OFF at
   default AND in prod = parked mechanism (candidate for OPERATOR-GATED, never
   SAFE-DELETE without a recorded ruling).
4. Report three lists: dead flags, OFF-everywhere flags with the LOC they
   gate, ON-in-prod flags whose documentation says OFF (record-drift —
   WIRING-BUG class).

## Test-value probe

The probe and its procedure belong to `/repo-health`. Follow steps 2 to 4 of
`~/.claude/skills/repo-health/references/reviews.md` § Test value, once per
suite, and skip its ten-test limit: here every lead goes to the tests shard.
The script is `~/.claude/skills/repo-health/scripts/testvalue.py`. A test that
owns no line is a candidate, never a finding: two tests can run the same
lines and assert different things.

## Test removal gates

For a cleanup session that removes or merges tests. Run both before the
change and after it, in the session's worktree, and keep the two outputs as
evidence.

1. **Coverage does not drop.** Run the suite under the project's coverage
   command and compare the total and the per-file numbers of the source files
   the removed tests ran.
2. **The mutation result does not get worse.** A mutation tool changes the
   source in small ways and counts the changes no test notices (`survived`).
   For Python use mutmut, limited to those source files, in `pyproject.toml`:

   ```toml
   [tool.mutmut]
   source_paths = ["src/pkg/module.py"]
   pytest_add_cli_args_test_selection = ["tests/"]
   ```

   Then `uv run --with mutmut mutmut run` and `mutmut results --all true`.
   Count the `survived` lines. More after than before means a removed test
   checked something: restore it. mutmut writes a `mutants/` folder in the
   project; do not commit it or the config block.

Measured on a three-test fixture with mutmut 3.8.1, 2026-11-10: with a
duplicate test removed, 4 of 4 mutants stayed killed; with the real test
removed too, 4 of 4 survived. For other languages use the project's own
mutation tool (Stryker for JS/TS); with none, gate 1 alone is not enough for
SAFE-DELETE, so class the removal OPERATOR-GATED.

## Reachability rules that decide dead vs alive

These three rules come from Google's Sensenmann (automated dead-code deletion
at monorepo scale) and are the audit's ground truth:

1. **Reachability from real entry points**, not text presence. A symbol
   mentioned in comments, docs, or its own tests is not reached.
2. **A module and its own tests are one unit.** Code whose only importers are
   its own tests is dead — the tests die with it.
3. **Looks-dead is not is-dead for rare-but-critical paths.** Incident
   tooling, year-end jobs, audit hooks, "break glass" code fire rarely and
   legitimately. Anything matching that shape is OPERATOR-GATED, never
   SAFE-DELETE — the operator's block-list decision, not the audit's.

## Output handling

Write recon outputs to the session scratchpad, one file per recipe. The shard
prompts embed only the candidates in each shard's scope, not the full dumps.
Recon numbers quoted in the final report must be re-derivable from these files.
