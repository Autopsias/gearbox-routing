# /routing-update apply checklist — every surface a model change touches, in order

Run top to bottom after operator approval. Each step names the surface, the edit, and
how it is verified. The authoritative surface SET is the SSOT `consumers:` list — if
this file and the SSOT `consumers:` list ever disagree, the SSOT wins and THIS file
gets the fix.

| # | Surface | Edit | Verified by |
|---|---|---|---|
| 1 | `claude/model-routing.yaml` | Apply approved diffs to `providers.<name>.models:` / `providers.<name>.effort.map:` / `providers.<name>.escalation:` / `providers.<name>.degrade:` / `prices.<name>:` / `task_classes:` / `agents:` for the provider(s) under research; **bump `version:`** per `docs/VERSIONING.md`, update `last_reviewed:` | guard checks (once `claude/scripts/verify-routing.sh` exists in your deployment) |
| 2 | Any vendored resolver mirror (a consumer's own copy of the escalation/degrade ladder, e.g. `claude/scripts/resolve_route.py` or a project-embedded harness mirror) | If the escalation ladder or the set of effort levels changed for the active provider: edit the mirror **before or with** step 1 — code is authoritative there, the SSOT mirrors it | guard-verified byte/contract check |
| 3 | Any project-owned agent-assignment file that duplicates per-agent tier/effort pins outside this SSOT | If any agent pin changed: edit that file first (it's authoritative for its own rows), then mirror the SSOT `agents:` rows to match | project-local check, if one exists |
| 4 | CHANGELOG.md | Append a dated entry: what moved (old → new), which provider, and the evidence pointer from the changeset. Land in the SAME commit as the policy change, per `docs/VERSIONING.md` | manual review |
| 5 | Any rendered digest (a project's CLAUDE.md routing summary, or equivalent, generated FROM this SSOT) | Re-render if your deployment has a render step; watch any byte/size budget the digest is held to | render script's own check, if one exists |
| 6 | Prose-rationale consumers | Re-read `claude/skills/routing-update/` and `claude/skills/routing-retro/` (both stamped `prose-rationale` in the SSOT `consumers:` list) against the new SSOT values — confirm nothing in their prose contradicts the new tiers/prices. These two skills carry no concrete model/price literals themselves, so this is usually a no-op; only the SSOT-adjacent examples in ARCHITECTURE.md need a look if the schema itself changed | manual re-read |
| 7 | Any local runtime settings file with its own default model/effort | Align with `main_session:` advisory defaults if your deployment has such a file — **re-confirm with the operator** (user-owned file) | guard warn-check, if one exists |
| 8 | Guard | Run your deployment's routing drift-guard (e.g. `bash claude/scripts/verify-routing.sh --full`, once it exists) — must PASS. On failure: fix the surface (or the SSOT if it's wrong) and re-run — never loosen the guard, never bypass it | itself |
| 9 | Commit | Stage ONLY the routing surfaces you touched + the changeset file — never a broad `git add -A`/`git add .` in this repo; commit message includes `routing-pin-change: approved-by-operator <YYYY-MM-DD>` | pre-commit hooks if configured |

Notes:

- **Order matters**: SSOT (1) before renders (5) and prose re-reads (6); resolver/
  agent-file mirrors (2, 3) move with or before the SSOT rows that describe them; the
  guard (8) gates the commit (9).
- **Some rows are aspirational until later port sessions land their scripts**
  (`verify-routing.sh`, `resolve_route.py`, a render script). Where a script doesn't
  exist yet in your deployment, do the equivalent check by hand and note in the
  changeset that automated verification is pending.
- **Partial-apply recovery**: if interrupted mid-checklist, the guard (once it exists)
  tells you exactly which surfaces still disagree — re-enter at step 8 and fix what
  it lists. Without a guard, diff the SSOT against each consumer by hand before
  committing.
