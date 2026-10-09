# Skills the sync holds back, and why

Every skill here diverges from its Claude source on purpose. The sync reports each one
and refuses to overwrite it. This file is the reason column — a skill held back with no
entry here is an oversight, not a decision.

## Contents

- [Codex editions](#codex-editions) — the mirror is right and the canon is not
- [Reviewed the other way](#reviewed-the-other-way) — canon wins despite divergence
- [Skipped for a different reason](#skipped-for-a-different-reason)
- [How to change a verdict](#how-to-change-a-verdict)

## Codex editions

The mirror says something true of Codex that the Claude source does not say. Syncing
would replace correct content with wrong content.

| Skill | What the mirror has that the canon does not |
|---|---|
| `routing-update` | Names the substitute source for `/claude-api`, which ships inside the Claude Code binary and is unreachable from Codex. |
| `routing-retro` | Reads `~/.codex/history.jsonl` for typed prompts. |
| `myusage-self-assessment` | Mines Codex session history, not Claude's. |
| `improve-harness` | Reads `~/.codex/improve-learnings.md` and `.codex/config.toml`. Renamed from `improve` — see the rename note below. |
| `ci-orchestrate` | Sources `$HOME/.codex/scripts/shared-discovery.sh`. |
| `commit-orchestrate` | Same discovery script. |
| `code-quality` | Runs the quality checkers under `$HOME/.codex/scripts/quality/`. |
| `usertestgates` | Runs `$HOME/.codex/lib/testgates_discovery.py`. |
| `test-orchestrate` | Dispatches Codex subagents by name rather than Claude agents. |
| `epic-dev-conductor`, `review`, `review-adversarial-general`, `ship-tail` | Invoked as `$name` — the Codex form — rather than as a Claude slash command. |
| `flake-detective` | Documents its own promotion path to a Codex plugin. |
| `nlm-skill` | Manages per-tool skill installs, Codex among them. |

## Reviewed the other way

These diverged too, and the divergence was read line by line and judged pure staleness —
zero Codex-specific content — so they sit in `CANON_WINS` and the sync overwrites them.

| Skill | What the divergence turned out to be |
|---|---|
| `coverage` | Numbered lists and "specialist agents" wording that the Claude source has since rewritten. |
| `epic-dev` | Ralph-loop flags and a STEP 1.5 the Claude source gained after the original import. |

## Skipped for a different reason

Not a divergence at all — the sync never considers these.

| Skill | Reason |
|---|---|
| `adversarial-review`, `memo-loop`, `plan-harden` (and any other hand-authored port) | `~/.codex/skills` owns the name. Those are hand-authored Codex ports rendered by `gearbox deploy` from `skills/*/codex/`. |
| `tdd`, `diagnose`, `write-a-skill`, `grill-me`, `grill-with-docs`, `improve-codebase-architecture`, `setup-matt-pocock-skills` | Installed in `~/.agents` and symlinked into `~/.claude`. This tree is their canon. |
| The eight `tier-*` agents | They pin a Claude model and effort for `/plan-execute` dispatch. Codex expresses effort as `-c model_reasoning_effort=`, so a mirror would advertise a tier nothing can dispatch. |

## Renamed on the way in

Codex has no project-over-user precedence: a user-level skill and a project skill
sharing a name are BOTH offered, and the selector picks blind. Where the two do
genuinely different jobs, the sync renames the user-level mirror through `RENAME`.

| Canon name | Mirrored as | Why |
|---|---|---|
| `improve` | `improve-harness` | A vault project's skill of the same name runs a vault retrospective; this one audits the AI harness config. |

Renaming happens on the MIRROR side only, never on the canon. A colliding project
skill keeps its name when it is a shipped product: renaming it would break its slash
command for everyone who installed it.

## How to change a verdict

**A held-back skill should adopt canon.** Read its diff first:

```bash
diff <(sed -n '/## Imported Instructions/,$p' ~/.agents/skills/<name>/SKILL.md) \
     <(sed -n '/^---$/,$p' ~/.claude/skills/<name>/SKILL.md)
```

If nothing Codex-specific is lost, add the name to `CANON_WINS` in the script, move its
row to *Reviewed the other way* with what the divergence was, and re-run the sync.

**A Codex edition needs to keep diverging permanently.** Add it to `PROTECTED` with a
one-line reason and give it a row above. `PROTECTED` and the automatic guard do the same
job; the list exists so the intent is readable without running anything.

**The adaptation is worth having on both sides.** Then it is not an edition — fix the
Claude source through `~/your-private-harness`, deploy, and re-run the sync so both converge.
