# /routing-update apply checklist — every surface a model change touches, in order

Run top to bottom after operator approval. Each step names the surface, the edit, and how
it is verified. The authoritative surface SET is the SSOT `consumers:` list + the carve-outs
+ settings.json — if this file and the SSOT `consumers:` list ever disagree, the SSOT wins
and THIS file gets the fix.

| # | Surface | Edit | Verified by |
|---|---|---|---|
| 1 | `~/.claude/model-routing.yaml` | Apply approved diffs to `prices:` / `task_classes:` / `effort_policy:` / `agents:` / `main_session:` / `degradation:`(mirror) / `fanout_policy:`; **bump `version:`**, update `last_reviewed:`; append (never overwrite) a dated `DECISION HISTORY` entry citing the changeset file | guard (a)–(e) downstream |
| 2 | `~/.claude/skills/plan-execute/scripts/run.py` | If the degradation ladder or the set of effort tiers changed: edit `_FALLBACK_LADDER` / `_REASONING_DIRECTIVE` **before or with** step 1 — code is authoritative, SSOT mirrors it | guard check (d) |
| 3 | `~/.claude/epic-dev-assignments.yaml` | If any `epic-*` pin changed: edit here (authoritative), then mirror the SSOT `agents:` rows | `verify-assignments.sh` (guard sub-check f) |
| 4 | `~/.claude/agents/*.md` frontmatter | Re-pin `model:` / `effort:` for every agent row that changed. Invariant: a `model: haiku` agent carries **no** `effort:` key at all | guard checks (a)+(b) |
| 5 | CLAUDE.md digest + `~/.claude/model-routing.digest.md` | Re-render BOTH variants: `python3 ~/.claude/scripts/render-routing-digest.py --variant v0 --install` and `--variant full --install` (use `--check` first to preview). Watch the byte budget — an over-budget digest locks all commits | guard check (e) |
| 6 | Prose stamp consumers | Bump every `<!-- routing-ssot: vN -->` to the new N in each `consumers:` entry with a stamp-checked type (`lint-reads` / `prose-rationale`) — enumerate from the SSOT, not from this sentence. **Plus the known UNREGISTERED stamped file `skills/plan-builder/codex/SKILL.md`** (stamped but absent from `consumers:`, so check (c) has never seen it — it sat at v12 against SSOT v14, found 2026-07-31). **A stamp bump asserts the prose agrees** — step 7 is what makes that assertion true | guard check (c) |
| 7 | **Prose-CONTENT propagation — the three consumer skills** | **The gap this step exists to close:** guard check (c) compares only the stamp *version*; it never diffs the prose's recommended models, efforts, prices or costs against the SSOT. So a changeset can bump every stamp, go green, and leave every skill recommending superseded values. See §"Step 7 in full" below | `scripts/check_stale_values.py` + the per-skill read |
| 8 | `~/.claude/settings.json` | Align `model` with `main_session.advisory_default` family and `effortLevel` with `main_session.default_effort` — **re-confirm with the operator** (user-owned file; changes every future session's default) | guard settings warn-check (full mode) |
| 9 | Hook regexes | ONLY if the `consumers:` set changed: extend `ROUTING_PREFIXES` (`githooks/pre-commit`) and `ROUTING_AFFECTING` (`githooks/commit-msg`) in lockstep | guard surface-list convergence check |
| 10 | Guard | `bash ~/.claude/scripts/verify-routing.sh --full` must PASS. On failure: fix the surface (or the SSOT if it's wrong) and re-run — never loosen the guard, never `--no-verify` | itself |
| 11 | Commit | Stage ONLY the routing surfaces + changeset file; commit message includes `routing-pin-change: approved-by-operator <YYYY-MM-DD>` (checked by commit-msg hook). Hooks stay ON | pre-commit + commit-msg hooks |
| 12 | Deploy | All of steps 1–11 happen in the source worktree `~/your-private-harness`, not in `~/.claude` directly. After the commit: push, then `gearbox-deploy` (or `git -C ~/.claude pull --ff-only`) to fast-forward the deploy target, and confirm the guard is green there too | `bash ~/.claude/scripts/verify-routing.sh --full` re-run in `~/.claude` |

## Step 7 in full — prose-content propagation

Three skills carry routing *advice* in prose, and all three route work to **both** the
Anthropic and the OpenAI lineup. Prose is not stamp-checked for content, so it is where
this system rots. Do both halves.

### 7a. Mechanical — the stale-value scan

From the approved changeset, collect every **superseded literal** (old price, old cost, old
model id, old effort rung, old tier name) and run:

```bash
python3 ~/.claude/skills/routing-update/scripts/check_stale_values.py '$4.95' '$3.03' 'v12'
```

It scans the live consumer surfaces (SSOT `consumers:` + the carve-outs), skips the
append-only historical record (`DECISION HISTORY` blocks, `evals/routing/results/**`,
`_plans/**`, `fixtures/**`), and exits non-zero naming `file:line` for every surviving hit.
**A clean exit is the evidence that step 6's stamp bump was honest.** Paste its output into
the apply log.

### 7b. Judgement — read each skill's routing prose against the new SSOT

The scanner catches values that changed. It cannot catch prose that is *wrong in a way the
changeset did not touch* — a retired premise, a self-contradiction, a claim the SSOT has
since flagged as stale. Read these, per skill.

**The three plan skills are DUAL-HARNESS — both provider halves are live.** Run from
Claude Code they route to Anthropic models; run from Codex CLI (via each skill's `codex/`
port, which `gearbox deploy` renders into `~/.codex/skills`) they route to OpenAI models.
So an OpenAI-side change is NOT "documentation on an inert profile": the Codex rubric rows
are the live authoring guidance for every plan built or executed under Codex. Check both
halves of every rubric on every pass, and remember the deployed `~/.codex` copies stay
stale until the next `gearbox deploy` — a source-only fix isn't live for Codex until then
(and a hand edit in `~/.codex` is a hotfix that blocks deploy; fix source, then deploy).

- **`/plan-builder`** — `references/schemas.md` (the rubric table, authority),
  `references/model-effort-guidance.md` (archetype playbook; **carries TWO stamps**, lines
  ~3 and ~139 — the guard reads only the first, so the second must be bumped by hand),
  `SKILL.md` (inline rubric prose), `codex/SKILL.md` (the rendered Codex port — unregistered,
  see step 6). Check: does every model/effort/cost cell match the SSOT's resolved pins for
  **both** providers?
- **`/plan-harden`** — `commands/references/plan-harden/model-lint.md`. This file's rules
  are what re-model a plan, so a stale rule silently re-models every future plan. Check:
  does each rule still resolve through `resolve_route.resolve()` rather than a hardcoded
  tier, and does its prose rationale cite a premise the SSOT still holds? (Precedent: its
  "dominated by Fable" premise was retired at v1.11 and the rule text had to be inverted.)
- **`/plan-execute`** — `SKILL.md` (dispatch-loop prose: degrade ladder, effort rungs,
  model-availability claims) and `scripts/run.py` (code-authoritative — step 2). Check the
  SKILL.md prose against **both** the SSOT and *itself*: this file has held a degrade rung
  stated two different ways in three adjacent paragraphs.

**Two failure shapes to look for specifically**, both found live on 2026-07-31:

1. **A stale availability/deprecation claim quoted as fact.** `plan-execute/SKILL.md` still
   read *"Fable is paywalled/suspended as of 2026-07"* — a claim `model-routing.yaml`'s own
   `prices.fable` note explicitly flags as stale *and* as having been quoted back at the
   operator as fact. Grep every consumer for availability language whenever the SSOT
   carries a "re-confirm before relying on this" note.
2. **Intra-file contradiction.** The same file said degradation lands on `high` because
   *"`xhigh` is a DEAD RUNG on both"*, then instructed degrading to `xhigh` twice further
   down. Read the whole routing section of a file, not just the line the scanner hit.

Anything you fix here goes in the same commit as the stamp bump — never as an owed
follow-up. If it genuinely cannot ship in this commit, it is named explicitly in the apply
log with an owner, not left silent.

## Notes

- **Order matters**: SSOT (1) before renders (5), stamps (6) and content (7); code
  carve-outs (2, 3) move with or before the SSOT rows that mirror them; the guard (10)
  gates the commit (11); deploy (12) is always last.
- **Hooks are tracked**: `core.hooksPath=githooks`, so `githooks/pre-commit` and
  `githooks/commit-msg` (step 9) are ordinary repo files — edit and commit them like any
  other surface, no reconstruction needed.
- **Partial-apply recovery**: if interrupted mid-checklist, the guard tells you exactly
  which surfaces still disagree — re-enter at step 10 and fix what it lists. The guard will
  NOT tell you about step 7 content drift; re-run 7a explicitly.
