# claude/scripts/

Guard + resolver scripts for `claude/model-routing.yaml`.

| Script | Purpose |
|---|---|
| `verify-routing.sh` | Drift guard. Validates the SSOT's shape (semver `version:`, valid `active_provider:`, every `task_classes` tier resolvable under every `providers.<name>.models`, active provider not `unresearched`), warns on stale `calibration.date`, and (agent-frontmatter-vs-SSOT `AGENTS_DIR` is wired but unpopulated) checks the CLAUDE.md digest re-render in `--full` mode. `--core`/`--full`/`--strict` flags; `CLAUDE_HOME`/`SSOT`/`AGENTS_DIR`/`ROUTING_STALE_DAYS` env overrides. |
| `render-routing-digest.py` | Renders `model-routing.digest.md`'s `{{resolve:<task_class>}}` placeholders to real `active_provider` model ids + native effort levels, and installs the result into `CLAUDE.md` between `<!-- BEGIN ROUTING -->` / `<!-- END ROUTING -->` markers. `--check` for dry-run drift detection, `--install` to bootstrap a marker-less target. Imports `resolve_route.py` (lands in a later session) for tier->model resolution when present; falls back to an inline baseline-only resolver otherwise — see the `TODO(S07 final integration)` comment in the source for the follow-up once that import is guaranteed. |
| `resolve_route.py` | The vendored, provider-neutral stateless resolver (S09). `resolve(task_class, active_provider, current, signal)` is the one normative entry point (ARCHITECTURE.md §3); `escalate()`/`degrade()` are thin convenience wrappers over it. stdlib-only regex parsing of the SSOT (no PyYAML), same house rule as the other two scripts below. `render-routing-digest.py` imports it for tier->model resolution (falls back to an inline baseline-only resolver only if the module is absent). See `claude/fixtures/route-resolver/` for its unit tests. |

Both scripts fail CLOSED: a policy violation and a tooling crash (missing file,
malformed SSOT, marker corruption, budget overflow) are both non-zero exit, never a
silent pass. See `claude/fixtures/routing-guard/` for seeded-violation fixtures each
script is proven against, `claude/fixtures/route-resolver/` for resolve_route.py's
own unit tests, and `docs/VERSIONING.md` / `ARCHITECTURE.md` for the schema these
scripts parse.

Scrub rules: `../../GENERICIZATION.md`.
