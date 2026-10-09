---
name: cost-audit
description: Measures what Claude Code sessions actually cost and why, from the local transcripts — prompt-cache hit rate, what caused each cache rewrite, the effort level used per model and per request, and API-equivalent dollars by chain and model — then compares against the saved baseline and ends with a one-pager and a decision card. Use when the user says "cost audit", "how much are sessions costing", "is caching working", "cache hit rate", "are we over-efforting", "effort audit", "re-run the cost baseline", "token spend this week", or after any effort, model-default or cache-TTL change. Not for judging model fit per task class (that's /routing-retro) and not for changing the routing SSOT (that's /routing-update).
allowed-tools: [Bash, Read, Artifact]
effort: low  # one deterministic script; the judgment is a comparison against a saved baseline
---

# /cost-audit — what did sessions cost, and which lever moves it?

Anthropic's cost playbook (claude.com/blog, "Reducing cost and improving performance") names
four levers: a stable cached prefix, no mid-session model or effort switches (an effort switch
keeps the cache on Opus 5.5 and Fable 5.1, see §3), prompt anti-patterns, and effort
calibration. This skill measures the first, second and fourth from
`~/.claude/projects/**/*.jsonl` and says which one is out of line. Run the real script every
time; never report numbers from this file or from memory.

## Checklist

- [ ] 1. Run the script for the window asked (default 14 days)
- [ ] 2. Compare against the saved baseline
- [ ] 3. Name the lever that moved, with its setting
- [ ] 4. Ship one page + one decision card

## 1. Measure

```bash
python3 ~/.claude/skills/cost-audit/scripts/cache_effort_baseline.py 14
```

Three blocks come back: spend by chain and model (with hit rate and average context per
request), main-conversation cache rewrites by cause, and the effort mix per request. The dollar figures are API-equivalent: on a subscription
they are usage-limit weight, not an invoice. Rates come per served model id from the
`model_prices:` block of `model-routing.yaml`; a model with no row there is skipped and named
on a `NOT PRICED` line — add the row before you read the totals (a hardcoded table once
silently dropped every request for a newer model). The write multipliers (main 2×, subagent 1.25×)
assume the default TTL buckets; confirm the main bucket when in doubt:

```bash
claude -p "reply ok" --model claude-haiku-4-5 --output-format json | python3 -c "import json,sys;print(json.load(sys.stdin)['usage']['cache_creation'])"
```

`ephemeral_1h_input_tokens` > 0 means the one-hour cache is in effect.

## 2. Compare

The baseline lives in your own baseline file: the first audit's numbers, saved as a dated note
(for example in your project memory). On a first run there is none yet, so save this run as it.
Read it, then put the two runs side by side on four numbers: hit rate per model, $/day,
share of Opus main requests at high or above (Opus 5 in the baseline, Opus 5.5 after it), share of Fable 5.1 main requests at xhigh.
A hit rate under 90% on any model with more than 1,000 requests, or a rewrite category that
doubled, is a finding; a $/day change alone is not, because volume moves it.

## 3. Name the lever

| Signal in the output | Lever | Where it lives |
|---|---|---|
| MAIN-chain effort mix above the routing SSOT | Nothing | The operator picks main-session model and effort by hand. Report it as a price tag; never propose or make a change to `modelSettings.<model>.effortLevel` or `maxEffortLevel` for it |
| SUB-chain requests at an effort no agent file pins (built-in `general-purpose`, `Explore`, `fork`) | Agent definition | A subagent with no `effort:` frontmatter inherits the session effort; the Agent tool takes `model` but no effort. A user-level agent file of the same name replaces the built-in (probed: session high, replaced `general-purpose` ran low). Route the change through /routing-update |
| `model_switch` or `effort_change` rewrites growing | Session discipline | Pick the model at session start: a model switch is a full re-read. An effort change is a full re-read on most models, Opus 5 included, but keeps the cache on Opus 5.5 and Fable 5.1 (docs `prompt-caching`), so an `effort_change` rewrite on those two points at another cause — the script tests effort before the time gap |
| `idle_over_1h` large | Nothing | Expected; resume-from-summary handles the big ones |
| `unexplained_under_5m` growing | Read the cause live | Statusline shows `Cache:N% warm,miss:<cause>`; `/usage` shows the same |
| Subagent expiries | Nothing global | A 1-hour subagent TTL costs more in writes than it saves; fix the long quiet command instead |

Routing SSOT values come from `~/.claude/model-routing.yaml`; do not restate them here.

## 4. Report

One rendered page (Artifact) with the three blocks as tables and the baseline delta, then a
decision card of at most three options; the house shape is in
`~/.claude/references/shared/decision-card-html.md`. Save any adopted change back into
the baseline memory note with the date.

## Gotchas (observed)

- **lines are not requests.** One API request spans several transcript lines,
  each carrying the same `usage` and `effort`. Counting lines doubled every effort figure in
  the first audit. The script deduplicates by `requestId`; keep it that way.
- **MCP trimming is not a lever.** MCP server definitions add a small fixed cost
  to the base context (measure it with `--strict-mcp-config --mcp-config '{"mcpServers":{}}'`).
- **hand edits to the live settings file block deploy.** A key deletion in
  `~/.claude/settings.json` classes as harness drift; commit it in your harness source,
  restore the live file, then redeploy.
