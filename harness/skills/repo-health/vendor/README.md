# Vendored third-party skills — do not edit in place

Every file below this directory is a verbatim copy of somebody else's work at a
reviewed commit. The pins, the licences, and the reason each one is here live in
[`../vendor.json`](../vendor.json).

**Do not hand-edit these files.** A local edit makes the pin a lie, and the next
`vendor.py check` cannot tell your change from an upstream one. To take an
upstream update: read the diff (`vendor.py diff <name>`), then bump the pin and
re-copy the tree in one commit.

**These are not installed as live skills.** They sit under `repo-health/vendor/`,
not `skills/<name>/`, so Claude Code does not register them and they never fire
on their own. `repo-health` reads them deliberately, on the run that needs them.
That is the containment: a vendored `SKILL.md` is prompt text an agent will
follow, so it must be reached on purpose, never by keyword match.

## What is here

| Directory | Upstream | Licence |
|---|---|---|
| `gha-security-review/` | [getsentry/skills](https://github.com/getsentry/skills) `skills/gha-security-review` | Apache-2.0 |

Sentry's `security-review` from the same upstream is **not vendored**: its
reference files derive from the OWASP Cheat Sheet Series under CC BY-SA 4.0,
which is share-alike. Install [getsentry/skills](https://github.com/getsentry/skills)
yourself if you want it.

## Not vendored, and why

`anthropics/claude-code-security-review` was reviewed and rejected. Claude Code
already ships that exact prompt as the built-in `/security-review`. A vendored
copy would duplicate what already runs, and would rot while the built-in tracks
the binary. It is still the right thing for a *human* to run — but **no agent can fire
it**: a subagent is never handed `SlashCommand`, which is why `sec.diff-review`
sat `pending` run after run. So the
probe is NOT recorded from the built-in. It is recorded from scoped reviewer
agents following Sentry's `security-review` prompt (installed by you); the
method is
[SKILL.md § Reviewing the diff without the operator](../SKILL.md#reviewing-the-diff-without-the-operator)
and `references/checks.md` carries the same rule.
