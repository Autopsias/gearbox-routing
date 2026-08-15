# Example Plan Specs

Five small reference specs, one per infographic template. Use these as starting points when interviewing a user — paste the relevant one, swap in their plan's content.

Each example is a complete, valid spec that the build script will accept.

## Example 1 — Phase Journey (product launch)

See `../../plan-builder-workspace/test-spec.json` (if present) for a full Q4-product-launch plan: 5 sessions, 9 items, 4-phase journey from v2.5 to v3.0.

**Use phase-journey when:** the work is sequential and unlocks downstream work (spike → build → pilot → roll-out).

## Example 2 — Maturity Ladder (observability climb)

```json
{
  "title": "Observability Maturity — Ad-hoc to Continuous",
  "subtitle": "12-week climb across four maturity levels",
  "categories": [
    {"key": "logs",    "label": "Logs"},
    {"key": "metrics", "label": "Metrics"},
    {"key": "traces",  "label": "Traces"},
    {"key": "auto",    "label": "Automation"}
  ],
  "items": [
    {"id": "logs-01", "title": "Adopt structured JSON logs", "category": "logs",    "priority": "P0", "effort": "M"},
    {"id": "logs-02", "title": "Centralize via Loki",        "category": "logs",    "priority": "P1", "effort": "M"},
    {"id": "met-01",  "title": "Wire Prometheus + RED",      "category": "metrics", "priority": "P1", "effort": "L"},
    {"id": "met-02",  "title": "Define service SLOs",        "category": "metrics", "priority": "P1", "effort": "M"},
    {"id": "tr-01",   "title": "Deploy OpenTelemetry",       "category": "traces",  "priority": "P1", "effort": "L"},
    {"id": "tr-02",   "title": "Per-endpoint trace sampling","category": "traces",  "priority": "P2", "effort": "S"},
    {"id": "auto-01", "title": "Auto-rollback on SLO burn",  "category": "auto",    "priority": "P1", "effort": "L"},
    {"id": "auto-02", "title": "Predictive anomaly alerts",  "category": "auto",    "priority": "P2", "effort": "L"}
  ],
  "sessions": [
    {"id": "s01", "title": "Logs foundation",          "model": "Sonnet", "items": ["logs-01", "logs-02"],            "prompt": "..."},
    {"id": "s02", "title": "Metrics + SLOs",           "model": "Sonnet", "items": ["met-01", "met-02"],              "prompt": "..."},
    {"id": "s03", "title": "Tracing rollout",          "model": "Sonnet", "items": ["tr-01", "tr-02"],                "prompt": "..."},
    {"id": "s04", "title": "Automation + alerting",    "model": "Opus",   "items": ["auto-01", "auto-02"],            "prompt": "..."}
  ],
  "infographic": {
    "type": "maturity-ladder",
    "title": "Climbing from <em>ad-hoc</em> to <em>continuous</em>",
    "narrative": "...",
    "levels": [
      {"num": 1, "name": "Ad-hoc",     "tagline": "manual logs only",      "items": ["logs-01"]},
      {"num": 2, "name": "Repeatable", "tagline": "centralized + indexed", "items": ["logs-02", "met-01"]},
      {"num": 3, "name": "Defined",    "tagline": "SLOs + tracing",        "items": ["met-02", "tr-01", "tr-02"]},
      {"num": 4, "name": "Continuous", "tagline": "auto-remediation",      "items": ["auto-01", "auto-02"]}
    ],
    "anchor_bottom": {"name": "Today",      "tagline": "manual ops"},
    "anchor_top":    {"name": "Continuous", "tagline": "self-healing"}
  }
}
```

## Example 3 — Hub & Spoke (cross-team launch)

Skeleton shown — fill in items and prompts:

```json
{
  "title": "v3.0 Cross-Team Launch",
  "infographic": {
    "type": "hub-spoke",
    "title": "Five workstreams converging on <em>v3.0</em>",
    "hub":    {"name": "v3.0 ship", "tagline": "Q3 close · all teams green"},
    "spokes": [
      {"name": "Backend",  "tagline": "auth + perf",   "items": ["be-01", "be-02"]},
      {"name": "Frontend", "tagline": "redesign",      "items": ["fe-01", "fe-02", "fe-03"]},
      {"name": "Mobile",   "tagline": "iOS + Android", "items": ["mo-01", "mo-02"]},
      {"name": "DevOps",   "tagline": "k8s migration", "items": ["op-01"]},
      {"name": "Docs",     "tagline": "API + guides",  "items": ["doc-01", "doc-02"]}
    ]
  }
}
```

## Example 4 — Before/After (monolith → services)

```json
{
  "title": "Monolith to Services Migration",
  "infographic": {
    "type": "before-after",
    "title": "Migrating from <em>monolith</em> to <em>service mesh</em>",
    "before": {
      "name": "Monolith (today)",
      "bullets": ["Single deployable", "Shared database", "30-min deploys", "Coupled team velocity"]
    },
    "after": {
      "name": "Service mesh (target)",
      "bullets": ["8 deployable services", "Service-owned data", "<5-min deploys", "Independent team velocity"]
    },
    "workstreams": [
      {"name": "Extract auth service",       "items": ["m-01", "m-02"]},
      {"name": "Extract billing",            "items": ["m-03", "m-04"]},
      {"name": "Database split",             "items": ["m-05", "m-06"]}
    ]
  }
}
```

## Example 5 — Pillars (engineering culture)

```json
{
  "title": "Building Defensible Engineering Culture",
  "infographic": {
    "type": "pillars",
    "title": "Building <em>defensible engineering culture</em> on four pillars",
    "roof":       {"name": "Defensible engineering culture"},
    "pillars": [
      {"name": "Code review",     "tagline": "every PR",         "items": ["cul-01", "cul-02"]},
      {"name": "Testing",         "tagline": "coverage > 80%",   "items": ["cul-03", "cul-04"]},
      {"name": "Postmortems",     "tagline": "blameless",        "items": ["cul-05"]},
      {"name": "Documentation",   "tagline": "decision records", "items": ["cul-06", "cul-07"]}
    ],
    "foundation": {"name": "Existing process + people"}
  }
}
```

## Choosing between templates — quick decision guide

1. Does the plan have **distinct sequential phases** (spike → build → roll-out)? → **phase-journey**
2. Does it describe a **maturity climb** (manual → automated, ad-hoc → continuous)? → **maturity-ladder**
3. Are there **multiple parallel teams** converging on one outcome? → **hub-spoke**
4. Is it explicitly a **transformation** with bullets contrasting before/after? → **before-after**
5. Does the plan rest on **N foundational workstreams** that together support a goal? → **pillars**

When unsure, **phase-journey** is the safe default — it accommodates most plans.

## Aurora edition — dual-layer item + session example

Demonstrates the new `human_summary`, `deliverable`, `agent_instructions`, `schema`, and `code` fields. Drop these into any item or session in an existing spec; they all degrade gracefully if omitted.

```json
{
  "items": [
    {
      "id": "be-01",
      "title": "Add invite-by-email endpoint",
      "category": "backend",
      "priority": "P1",
      "effort": "M",
      "human_summary": "Admins can email a teammate an invite link instead of doing the create-user-and-share-password dance. Cuts onboarding from 10 minutes to one click.",
      "deliverable": "POST /api/v1/invites returns a token-bearing magic link; recipient lands on /accept-invite which provisions the account.",
      "why": "Manual user creation is the #1 onboarding complaint in NPS verbatims.",
      "owner": "Backend",
      "target": "End of Q3",
      "touches": "services/auth/invites.go, web/pages/accept-invite.tsx, db/migrations/0042_invites.sql",
      "agent_instructions": [
        "Add the migration for the invites table per the schema below.",
        "Implement POST /api/v1/invites returning 201 with {token, expires_at}.",
        "Wire a worker that emails the magic link via SendGrid (template ID INV-001).",
        "Add /accept-invite page that exchanges token for session and redirects to /dashboard."
      ],
      "schema": {
        "lang": "sql",
        "code": "CREATE TABLE invites (\n  id            UUID PRIMARY KEY,\n  inviter_id    UUID NOT NULL REFERENCES users(id),\n  email         CITEXT NOT NULL,\n  token         TEXT NOT NULL UNIQUE,\n  expires_at    TIMESTAMPTZ NOT NULL,\n  accepted_at   TIMESTAMPTZ,\n  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()\n);\nCREATE INDEX idx_invites_token ON invites(token);"
      },
      "code": {
        "lang": "ts",
        "code": "interface InviteResponse {\n  token: string;\n  expires_at: string;  // ISO 8601\n  invite_url: string;  // e.g. https://app.example.com/accept-invite?t=...\n}"
      }
    }
  ],
  "sessions": [
    {
      "id": "s04",
      "title": "Invite flow end-to-end",
      "model": "Sonnet",
      "effort": "~3h",
      "items": ["be-01", "fe-01"],
      "human_summary": "Stand up the invite-by-email flow end-to-end. Admins click 'Invite' in the dashboard, type an email, the teammate gets a link, clicks it, lands inside.",
      "deliverable": "A real admin can invite a real teammate and watch them join the workspace. CI passes; staging demo recorded.",
      "why_model": "Mechanical CRUD + template wiring — well-defined enough for Sonnet.",
      "prompt": "Execute SESSION S04 — Invite flow end-to-end.\n\nItems: BE-01 (backend endpoint + table) and FE-01 (frontend dashboard button + accept page).\n\n1. Run the migration in db/migrations/0042_invites.sql.\n2. Implement BE-01 following its agent_instructions and schema.\n3. Implement FE-01: add the 'Invite teammate' modal to the admin dashboard, build the /accept-invite page.\n4. Add an integration test that drives the full flow with a real email captured by mailhog in CI.\n5. Record a 30-second screencast for the staging demo channel."
    }
  ]
}
```

Notice that the **prompt body** doesn't repeat the schema or the dev-step bullets — those live in the items' `agent_instructions` / `schema` / `code` fields, which `build_plan.py` extracts into `sessions/<id>.context.md`. The subagent reads that per-session bundle (~5–15 KB) instead of grepping the full PLAN.html. This keeps the prompt focused on the task and the technical detail right next to its item.

## Dispatch examples — for `/plan-execute`

The `dispatch` block per session drives auto-execution. Three patterns:

### Sequential dependency chain

```json
"sessions": [
  {"id": "s01", "title": "Spike", "model": "Sonnet", "items": ["eng-01"], "prompt": "...",
   "dispatch": {"subagent_type": "general-purpose", "depends_on": []}},
  {"id": "s02", "title": "Build", "model": "Opus", "items": ["eng-02"], "prompt": "...",
   "dispatch": {"subagent_type": "general-purpose", "depends_on": ["s01"]}}
]
```
`/plan-execute` runs s01, waits for its DONE closeout, then runs s02.

### Parallel batch (fan-out)

```json
"sessions": [
  {"id": "s02", "title": "Auth build", "model": "Opus", "items": ["eng-02"], "prompt": "...",
   "dispatch": {"subagent_type": "general-purpose", "parallel_group": "g1", "depends_on": ["s01"]}},
  {"id": "s03", "title": "Sessions migrate", "model": "Sonnet", "items": ["eng-03"], "prompt": "...",
   "dispatch": {"subagent_type": "general-purpose", "parallel_group": "g1", "depends_on": ["s01"]}}
]
```
Once `s01` is DONE, `s02` and `s03` (same `parallel_group`, same `depends_on`)
fan out in ONE assistant turn with two Task calls and run concurrently. Group
members MUST share their `depends_on` set — the validator rejects mismatches.

Note the shape: `s01` is the **contract-first** session — it freezes whatever
`s02` and `s03` both consume — and the two members write **disjoint files**
(their items' `touches` must not overlap, or the build refuses them). Neither
member commits: shipping is the group's act, not a member's.

### Contract-first fan-out with worktree isolation

```json
"items": [
  {"id": "eng-02", "title": "Auth build", "category": "eng", "touches": "src/auth/, tests/auth/"},
  {"id": "eng-03", "title": "Sessions migrate", "category": "eng", "touches": "src/sessions/, tests/sessions/"}
],
"sessions": [
  {"id": "s02", "title": "Auth build", "model": "Opus", "items": ["eng-02"], "prompt": "...",
   "verify": {"gates": ["code-review-gate"]},
   "dispatch": {"parallel_group": "g1", "isolation": "worktree", "depends_on": ["s01"]}},
  {"id": "s03", "title": "Sessions migrate", "model": "Sonnet", "items": ["eng-03"], "prompt": "...",
   "verify": {"gates": ["test-orchestrate"]},
   "dispatch": {"parallel_group": "g1", "isolation": "worktree", "depends_on": ["s01"]}}
]
```
Each member runs in its own orchestrator-managed git worktree, so the two never
share a working tree. **You do not write the integration session** —
`build_plan.py` emits one (`dispatch.integrates_group: "g1"`, `depends_on:
["s02","s03"]`, `verify.gates: ["code-review-gate","test-orchestrate"]`,
`post_session.git: "commit"`) whose prompt carries the merge protocol: baseline
first, containment check, base-ref confirmation, producer-first merge, gates
re-run on the merged tree, then ship. Declare your own session with
`integrates_group` only if you want to control it yourself.

Every rule that is refused here — no member commits, no member touches a
lockfile, every member item declares `touches`, no two members write the same
path — is in `../plan-execute/references/parallel-group-contract.md`, checked at
build time and again at dispatch by the same code.

### Human checkpoint

```json
{"id": "s04", "title": "Publish public release notes", "model": "Sonnet", "items": ["docs-01"], "prompt": "...",
 "dispatch": {"subagent_type": null, "depends_on": ["s02", "s03"], "requires_human_checkpoint": true,
  "checkpoint": {
    "reason": "The release notes go out publicly and cannot be unsent — wording, tone, and what we disclose about the incident are judgment calls only the owner can make.",
    "decision": "Approve the drafted release notes for publication as-is, request edits, or hold the release.",
    "options": ["Publish as drafted", "Edit first (say what to change)", "Hold — do not publish yet"]}}}
```
After `s02`+`s03` complete, `/plan-execute` sets `s04` to `AWAITS_REVIEW` and
halts, presenting the `checkpoint` brief — why the gate exists and exactly what
you're deciding. You answer, then run `/plan-execute <dir> --resume` to dispatch
it. The brief is **mandatory** for every human gate (build error without it): a
gate that can't name its decision shouldn't exist — use a `verify` gate or
`checkpoint_policy: "notify-and-continue"` instead.
`subagent_type: null` runs the session as a fresh `general-purpose` agent.

## Model + reasoning tiering — match the tier to the work

`model` picks the engine; `reasoning` picks the thinking depth; `effort` is wall-clock. They're independent — set each from the work's actual demand (full rubric in `schemas.md` → "Model + reasoning rubric").

```json
"sessions": [
  {"id": "s01", "title": "Rename sweep across modules", "model": "Haiku", "reasoning": "low", "effort": "S",
   "items": ["mech-01"], "why_model": "Mechanical codemod — well-specified, no judgement.", "prompt": "..."},
  {"id": "s02", "title": "Build invite CRUD + tests", "model": "Sonnet", "reasoning": "medium", "effort": "M",
   "items": ["be-01"], "why_model": "Defined scope; Sonnet 5 is the broad workhorse.", "prompt": "..."},
  {"id": "s03", "title": "Refactor auth across 9 modules + chase the flaky-session bug", "model": "Sonnet", "reasoning": "xhigh", "effort": "L",
   "items": ["int-01"], "why_model": "Multi-file surface + non-obvious debugging — Sonnet 5 @ xhigh absorbs what used to escalate to Opus.", "prompt": "..."},
  {"id": "s04", "title": "Resolve the consistency-vs-latency tradeoff", "model": "Opus", "reasoning": "xhigh", "effort": "M",
   "items": ["arch-01"], "why_model": "One irreversible architecture call — Opus 4.8 @ xhigh (never Opus @ max; it overthinks past its peak).", "prompt": "..."},
  {"id": "s05", "title": "Design the cross-domain event schema the whole rollout rests on", "model": "Fable", "reasoning": "high", "effort": "XL",
   "items": ["arch-02"], "why_model": "The plan's linchpin — cross-cutting + irreversible; Fable earns its credit cost here. Auto-degrades to Opus 4.8 @ xhigh if Fable is paywalled/unavailable.", "prompt": "..."}
]
```

Note `s04` is `effort: M` but `reasoning: xhigh` — a single hard decision, not much typing. And `s01` could be `effort: XL` (hundreds of files) yet stay `reasoning: low`. Size and depth are orthogonal. `s03` shows the current default: **`xhigh` is the coding/agentic sweet spot**, and Sonnet 5 at `xhigh` now covers most integration/debugging work that used to need Opus. `s05` is the one Fable session — reserve it for a genuine linchpin; it degrades to Opus 4.8 @ `xhigh` automatically if Fable is unavailable (credit-metered / paywalled as of 2026-07). The **design (s04 Opus / s05 Fable) vs build (s02–s03 Sonnet) split above IS Anthropic's own `opusplan` pattern** — Opus during plan mode, Sonnet for execution. **No session pairs Opus with `max`.**
