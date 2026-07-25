# Verdict framework (Phase 3) — cluster → decide

Each cluster gets exactly one verdict. The whole point of the skill is
discrimination: distinguishing a pattern that *justifies building something* from
one that is noise, self-healing, or already covered. Weigh **recurrence** against
**build cost** and **whether the capability already exists**.

## The standing bar

> Only propose a **skill** for something that **actually recurs** AND **isn't
> already covered** by an installed capability.

Most mature setups are skill-saturated, not skill-starved. The default finding is
"fix or automate a thing that already exists," not "build something new." If you
find yourself proposing a new skill, first prove (a) the recurrence count and (b)
that no installed skill/command already covers it — list what you checked.

## The four verdicts

| Verdict | Use when | Typical signal |
|---|---|---|
| **skill** | Recurs, high-leverage, genuinely uncovered. Rare. | A repeated multi-step job no installed skill matches. |
| **automation** | Recurs, mechanical, better unattended. | Polling/babysitting load; no-op-heavy manual checks; long waits the human polls. |
| **fix** | The capability exists but is unreliable / unused / mis-triggered / dead. Most common. | Dead MCP credential, output-schema bug, skill installed but 0 invocations, missing trigger route. |
| **nothing** | Self-healing, model behaviour not config, or external noise. | One-turn-recovering tool errors, auth/login churn, market/model availability churn. |

Record every **nothing** explicitly (an "explicitly NOT proposing" list) with the
reason — so the next run doesn't re-litigate a settled question.

## The "already covered" checklist (run before any skill verdict)

1. `ls ~/.claude/skills/` and the installed command list — does a name match?
2. Search the digest `command_usage` — is there a skill for this that is simply
   **never invoked**? If so the verdict is **fix (routing/pruning)**, not build.
3. Is this model behaviour (e.g. write-before-read) that self-heals in one turn?
   → **nothing**, unless it hits a specific automated template systematically.
4. Is it external infra (API drops, login churn)? → **fix** the infra or
   **nothing**, never a new skill.

## Ranking

Rank clusters by leverage, most first:

```
leverage ≈ recurrence × cost_per_occurrence_to_user / build_cost
```

A month-long dead credential that silently degrades every research call
(low build cost, high recurrence) outranks a rare-but-annoying edge case. Put the
single largest automation cost in the corpus at or near the top even if the fix is
cheap — cheap fix on a huge denominator is maximum leverage.

## Worked examples (from the reference run)

- **Auto security-review fleet firing per-commit on doc/HTML files, with a
  persistent `/findings must be array` schema bug** → **fix** (path-scope the
  trigger, fix the schema, dedupe adjudicated findings, cheaper first-pass model).
  ~700 sessions/month, low build cost = top of the list.
- **User types bare `retry` 142× after API connection drops** → **fix** (bounded
  retry-with-backoff in the orchestration loops). The human is the retry
  mechanism; that is a config gap, not a skill gap.
- **~1,500 pure-babysitting prompts (proceed/check/yes/continue)** →
  **automation** (default-continue-with-notification at gates that always get
  "proceed"; Monitor + push for long runs).
- **Dead Perplexity MCP credential (401 for a month, cross-project)** → **fix
  (trivial)** — renew or remove the server so fallbacks are chosen deliberately.
- **10+ installed commands/skills with zero invocations** → **fix (prune +
  route)** — dead descriptions tax every session's trigger accuracy; reroute the
  recurring prose to the skills that already exist.
- **New skills** → declined. Nothing recurred that wasn't already covered by an
  installed-but-unreliable or installed-but-unused capability. Verdict on the
  setup: "consolidate and harden," not "extend."

## Re-measure (Phase 5) status vocabulary

When a prior run exists, tag each prior candidate:

- **held** — metric now at/under target; the fix stuck.
- **retired** — held for long enough that the rule can be dropped (the "measured
  rule" loop: a rule that provably holds at target no longer needs enforcing).
- **regressed** — metric worsened or the fix was undone; re-raise it.
- **superseded** — replaced by a better-scoped candidate this run.

Key metrics to re-measure: babysit share, retry/interrupt count, automation-fleet
share of sessions, schema-fail rate, and the top error families.
