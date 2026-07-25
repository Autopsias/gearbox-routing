# Plan Spec Schema (Aurora edition)

The build script expects a JSON file with this shape. All fields without "(optional)" are required.

The Aurora edition introduces **dual-layer authoring**: each item and session can carry both a *human-readable* layer (plain English, prominent on screen) and an *agent-readable* layer (technical spec, collapsed by default but always in the DOM for any Claude that opens the file). Every new field is optional; old specs continue to build identically except for the visual refresh.

```json
{
  "title": "Q4 Product Launch Plan",
  "subtitle": "Get the v3.0 release across the line",
  "meta": "(optional) 12 sessions · 28 items · 4 phases",
  "categories": [
    {
      "key": "engineering",
      "label": "Engineering",
      "description": "(optional) one-line context shown in the section header"
    }
  ],
  "items": [
    {
      "id": "eng-01",
      "title": "Spike: validate auth migration approach",
      "category": "engineering",

      "human_summary": "(optional, RECOMMENDED) one or two plain-English sentences about what this is and why it matters in real-world terms. Rendered prominent + readable.",
      "deliverable":   "(optional, RECOMMENDED) one sentence describing the concrete outcome — 'When done, X exists / Y is verifiable.' Rendered as a highlighted callout.",
      "why":           "(optional) one sentence on the underlying problem this solves. Rendered as italic 'Why:' line.",

      "description":  "(optional, agent layer) longer technical description. Surfaced inside the collapsible agent-spec, not in the human layer.",
      "agent_instructions": [
        "(optional, agent layer) numbered bullets describing concrete dev steps Claude should take",
        "or a single string if a paragraph is enough"
      ],
      "schema": {
        "lang":    "json",
        "code":    "{ \"field\": \"...\", \"...\": \"...\" }",
        "caption": "(optional) one-line caption shown below the block"
      },
      "mockup": {
        "svg":     "<svg viewBox='0 0 200 100'>...</svg>",
        "caption": "(optional) caption"
      },
      "code": {
        "lang": "ts",
        "code": "interface InviteRequest { ... }"
      },

      "owner":   "(optional) Person or team",
      "target":  "(optional) Target date e.g. 2026-06-15 or 'end of Q3'",
      "touches": "(optional) Files / areas affected. Surfaced inside the agent-spec.",
      "priority": "P0",
      "effort":   "M",
      "updated":  "(optional) ISO date, defaults to today",

      "tweak_likelihood": "(optional) 'high' | 'medium' | 'low' — how likely this item's call gets revisited (schema choice, judgment call). Items carrying this field surface in a 'Decision hotspots' section above the Session Plan, sorted high->low. Omit if the item isn't a notable judgment call.",
      "alternatives": [
        "(optional, requires tweak_likelihood) the roads not taken — short strings, rendered under the item in the hotspots section"
      ]
    }
  ],
  "sessions": [
    {
      "id":      "s01",
      "title":   "Auth migration spike",
      "model":   "Sonnet",
      "effort":  "~2h",
      "reasoning": "medium",

      "human_summary": "(optional, RECOMMENDED) one or two sentences in plain English about what this session achieves. Rendered in serif at 18px — the most prominent text on the card.",
      "deliverable":   "(optional, RECOMMENDED) one sentence: what concretely exists at session end. Rendered as a highlighted callout.",
      "why_model":     "(optional) Rationale for the model choice",
      "peer_triggers": [],
      "agent_instructions": [
        "(optional, agent layer) any extra notes that should go INSIDE the agent-spec but outside the prompt body itself. Most plans won't need this."
      ],

      "items":  ["eng-01", "eng-02"],
      "prompt": "Run the auth migration spike. Prototype the new flow; don't ship.\n\n(Write only the TASK BODY. build_plan.py wraps it with item scope, a pointer to the per-session context bundle, and the closeout contract — don't include closeout instructions yourself.)",
      "updated": "(optional) ISO date",

      "dispatch": {
        "subagent_type": "general-purpose",
        "parallel_group": null,
        "depends_on": [],
        "requires_human_checkpoint": false,
        "max_retries": 0
      },
      "verify": {
        "gates": ["pytest-fast"],
        "on_fail": "rework",
        "max_rework": 2
      }
    }
  ],
  "infographic": {
    "type":      "phase-journey",
    "title":     "From <em>v2.5</em> to <em>v3.0 production</em>",
    "eyebrow":   "(optional) Plan Achievement · Visual Story",
    "narrative": "(optional) 1-2 sentence story of WHY this plan exists",

    "phases": [
      {"num": 1, "name": "Foundation", "tagline": "auth + observability", "items": ["eng-01", "eng-02"]}
    ],
    "anchor_now":  {"name": "v2.5 brittle", "tagline": "no auth · gap-y observability"},
    "anchor_goal": {"name": "v3.0 ready",   "tagline": "auth · observable · documented"}

    /* see infographic-templates.md for the maturity-ladder / hub-spoke /
       before-after / pillars / custom field shapes */
  }
}
```

## Field reference

### Top-level

- **`title`** (string, required) — Plan title in the page header `<h1>` and `<title>`.
- **`subtitle`** (string, optional) — Short tagline shown in the header meta line.
- **`meta`** (string, optional) — Custom meta line. Defaults to `"N sessions · M items"`.
- **`categories`** (array, required) — At least one category. Items must reference one.
- **`items`** (array, required) — Action items. Unique IDs. Categories must exist.
- **`sessions`** (array, required) — Sessions in execution order. Items referenced must exist.
- **`infographic`** (object, required) — One of the 5 plan-achievement template specs (`phase-journey`, `maturity-ladder`, `hub-spoke`, `before-after`, `pillars`) or `custom`.
- **`notify_on_halt`** (object, optional) — Opt-in halt-notification hook. Shape: `{"command": "<string>"}`. When `/plan-execute` sets the halt flag, it runs this command once (best-effort, `shell=False`, ~10s timeout) with env vars `PLAN_DIR`, `PLAN_TITLE`, `HALT_SESSION`, `HALT_REASON`. Failures are swallowed and logged as `notify_failed` in `run.ndjson`; success logs `notify_sent`. The command is your own project script (like a git hook) — e.g. one that posts to Slack, sends a desktop notification, or pings a webhook. Carried into `manifest.json` as immutable dispatch config. (No webhook variant yet — deferred to v2 to keep the network/SSRF surface out.)
- **`notify_on_complete`** (object, optional) — Symmetric companion to `notify_on_halt` for unattended (`--auto`) runs. Shape: `{"command": "<string>"}`. Fires ONCE when `/plan-execute` reports the plan complete (idempotent via a `complete_notified` run-state flag), same safety envelope (`shell=False`, ~10s, swallowed failures), with env vars `PLAN_DIR`, `PLAN_TITLE`, `PLAN_STATUS=complete`. The "you can stop watching now" ping — point it at a Slack/desktop/webhook script.

### Items — the dual-layer surface

**Human layer (prominent on screen):**
- **`id`** (string, required) — Unique. Convention: `cat-prefix-NN` (e.g., `eng-01`, `qw-01`). Lowercase.
- **`title`** (string, required)
- **`category`** (string, required) — Must match a `categories[].key`.
- **`human_summary`** (string, optional, *recommended*) — One or two plain-English sentences. Rendered above the deliverable callout at 14.5px. This is what the human reader sees when scanning the plan.
- **`deliverable`** (string, optional, *recommended*) — Concrete outcome. Rendered as a green-tinted callout with eyebrow label "When done". Makes progress legible at a glance.
- **`why`** (string, optional) — One sentence on the underlying problem. Rendered as italic "Why:" line.
- **`priority`** (`"P0"|"P1"|"P2"|"P3"`) — Defaults to `"P3"`. Drives chip color.
- **`effort`** (`"S"|"M"|"L"|"XL"`) — Defaults to `"M"`. Free-form chip text accepted.
- **`owner`** (string, optional)
- **`target`** (string, optional) — ISO date or relative ("end of Q3").
- **`updated`** (string, optional) — ISO date string. Defaults to today.

**Agent layer (collapsed by default, always in DOM):**
- **`description`** (string, optional) — Longer technical description. Surfaced inside the agent-spec details block, not in the prominent area.
- **`agent_instructions`** (array of string OR single string, optional) — Concrete dev steps for Claude. Rendered as an ordered list inside the agent-spec.
- **`schema`** (string OR object, optional) — Code block. Object form: `{lang, code, caption}`. Shown in a dark inline `code-block` inside the agent-spec.
- **`mockup`** (string OR object, optional) — Visual mockup. Forms accepted:
  - String starting with `<svg`: rendered as inline SVG.
  - String otherwise: rendered as ASCII / plaintext mockup in a `<pre>`.
  - Object: `{svg|img|ascii, caption, alt}`.
- **`code`** (string OR object, optional) — Code excerpt (different from schema). Same form as `schema`.
- **`touches`** (string, optional) — Files / areas affected. Surfaced inside agent-spec when other agent fields are present, otherwise visible in the meta-row.

**Decision hotspots (optional, item-level):**
- **`tweak_likelihood`** (`"high"` | `"medium"` | `"low"`, optional) — Flags an item as a judgment call likely to be revisited (a schema choice, an ambiguous tradeoff). Any item carrying this field is pulled into a static "Decision hotspots" section rendered above the Session Plan, sorted high→low, so the reader sees the plan's riskiest calls before the execution order. Omit entirely for routine items — the section itself disappears from the build when no item uses the field.
- **`alternatives`** (array of strings, optional) — The roads not taken for that decision. Rendered under the item's `why`/description in the hotspots section. Only meaningful alongside `tweak_likelihood`.

### Sessions — same dual-layer pattern

**Human layer:**
- **`id`** (string, required) — Convention: `sNN` (e.g., `s01`).
- **`title`** (string, required)
- **`model`** (`"Haiku"|"Sonnet"|"Opus"|"Fable"`, required) — Drives the model chip color (teal / blue / amber / violet). Free-form tolerant at render time: any value gets a chip, but only Haiku/Sonnet/Opus/Fable have a dedicated color (unknown names fall back to Sonnet's) — and `build_plan.py` warns at build time when a value won't normalize to a dispatchable token (fable/opus/sonnet/haiku), since `/plan-execute` would then omit `model` and the subagent inherits the orchestrator's model.
- **`effort`** (string, optional) — Free-form **size / wall-clock** estimate, e.g. `"S"`, `"~2h"`, `"half day"`. This is *time weight*, NOT cognitive depth — for depth use `reasoning`.
- **`reasoning`** (`"low"|"medium"|"high"|"xhigh"|"max"`, optional) — **Cognitive-effort tier**, orthogonal to `effort`. Renders as a `◐`-prefixed chip (grey / blue / amber / orange / pink) and is carried into `manifest.json` so the runner can size the dispatched model's thinking budget. Signals how much thinking depth the session needs: mechanical → `low`; standard build → `medium`; coding / agentic / integration → `high` (**the coding/agentic default — on Sonnet, `xhigh` is a dead rung on our own calibration run (a small accuracy gain for materially higher cost); from `high`, escalate MODEL, not effort**); ambiguous design tradeoffs / deep reasoning → `Fable` at `low`; reserve `max` for open-ended or irreversible calls (never on Opus — see the rubric). `xhigh`/`extra` are synonyms; `Haiku` rejects the reasoning dial entirely (pair it with `low`).
- **`human_summary`** (string, optional, *strongly recommended*) — One or two sentences in plain English about what this session achieves. Rendered in serif at 18px — the most prominent text on the card. The human reader reads this first.
- **`deliverable`** (string, optional, *recommended*) — Concrete outcome at session end. Rendered as a green callout.
- **`why_model`** (string, optional) — One sentence rationale for the model choice. Rendered as italic line.
- **`peer_triggers`** (array of `"architecture_decision"|"irreversible_change"|"security_sensitive"`, optional) — **Structured second-model-review gate.** Declares that this session does work matching one or more `codex_peer` triggers (SSOT `codex_peer.triggers`). When non-empty, the session MUST carry an `adversarial-review` entry in `verify.gates` (or mention `/adversarial-review` in its `prompt`) — the §4.0 model-lint promotes a missing gate here from advisory to a 🔴 **plan-killer** (the one deterministic model-lint flag that can block). This is the structured successor to the old keyword-heuristic `codex-trigger-no-gate` scan: declare the trigger explicitly and the gate is enforced, instead of guessing from title text. Omit (or `[]`) for sessions that don't touch architecture/irreversibility/security — the keyword scan still runs as a soft 🟡 "you may have forgotten to declare this" net. Carried into `manifest.json`. (Distinct from `task_class` below — the model-routing class, which became a real optional session field at s06.)
- **`task_class`** (string, optional; s06 EXE-01) — the session's model-routing class (`mechanical|standard_build|agentic_build|deep_reasoning|linchpin`, per the SSOT `task_classes:` vocabulary). Consumed by `/plan-execute`'s `executor_policy` enforcement: under a codex-focused dial (`active_provider: openai`), ONLY a session whose `task_class` is opted into the SSOT's `executor_policy.executor_for` may auto-dispatch to Codex; unset/unknown FAILS CLOSED to Claude execution, and `linchpin` (or any irreversible session — `peer_triggers: [irreversible_change]` / `dispatch.guards_irreversible`) is permanently barred from unsupervised Codex execution. Normalized to lowercase and carried into `manifest.json` (empty string when unset).

#### Model + reasoning rubric — steer by session KIND

<!-- routing-ssot: vN (stamp with your own SSOT's revision) -->
> **Canonical source:** `~/.claude/model-routing.yaml` (`task_classes:`) is the
> AUTHORITY for the defaults below — this table is the prose rationale behind
> those rows, not the source of truth itself. If this table and the SSOT ever
> disagree, the SSOT wins; update this table to match (`verify-routing.sh --full`
> check (c) enforces the version stamp above stays in lockstep with the SSOT).

Don't default every session to Sonnet / medium. Match the tier to the work. The skill *steers* with this rubric during the interview; it never silently auto-fills.

| Session kind | `model` | `reasoning` | `effort` | Why |
|---|---|---|---|---|
| Mechanical (rename sweep, formatting, codemod, doc edits) | `Haiku` | `low` | S | Well-specified, no judgement — fast + cheap wins. Haiku rejects the reasoning dial; keep it at `low`. |
| Standard build (CRUD, wiring, templated features, test scaffolds) | `Sonnet` | `medium` | M | **Sonnet 5 (2026-06-30) is the broad workhorse** — near-Opus on coding/agentic at ~40% less cost. Defined scope at `medium` is its sweet spot. |
| Integration / multi-file refactor / non-obvious debugging | `Opus` | `high` | M–L | On our own calibration run, **`Opus 5`·`high` dominates `Sonnet`·`high`** on the hard-agentic distribution — cheaper AND materially more accurate. `Opus`·`medium` is the cost-saver rung; `Sonnet`·`high` remains reasonable for LIGHTER integration work (the easy distribution isn't covered by the same run). Sonnet's `high`→`xhigh` stays a dead rung. |
| Architecture, ambiguous tradeoffs, security-sensitive design, hard root-cause | `Opus` | `medium` | L–XL | On our own calibration run, **`Opus 5`·`medium` dominates the old `Fable`·`low` pin on both axes** — Opus 5 (GA 2026-07-24, same $5/$25 as 4.8) inverts the earlier dominance direction. Escalate `medium`→`high` on the two named triggers. **Do not pair Opus with `xhigh`/`max`** — `high`→`xhigh` is a DEAD RUNG (no measurable gain for materially higher cost) and `max` weak (marginal at best); `max` is operator-elected only. |
| Hard/frontier work — the plan's linchpin session (cross-cutting architecture spanning domains, one-shot irreversible adjudication, synthesis the rest of the plan rests on) | `Opus` | `high` | L–XL | On our own calibration run, `Opus 5`·`high` is the sweet-spot rung (`Fable`·`xhigh` is dominated). **Fable is escalation-apex only now** — reach it via raise-model from `Opus`·`high` when the two named triggers persist (unsolved in prior rounds; whole-codebase/large-context synthesis where 1M context matters); it still draws usage credits, is intermittently paywalled, and a failed Fable dispatch **auto-degrades to Opus @ `high`** (plan-execute, per-target degrade). |
| Spike / research (throwaway prototype to learn) | `Sonnet` | `medium` / `high` | S–M | Value is in the thinking, not the polish. |

**Plan / execute by session KIND — Anthropic's own `opusplan` pattern.** Claude Code ships an `opusplan` alias that *"uses `opus` during plan mode, then switches to `sonnet` for execution."* Apply the same split here: **design / architecture / adjudication / synthesis sessions → Opus 5 · `medium` (deep-reasoning default; `high` for linchpin/one-shot-irreversible); standard build / wiring sessions → Sonnet 5; hard multi-file/agentic build → Opus 5 · `high`.** Route by what the session *does*, not by how hard the overall plan feels. **Fable 5 is the escalation apex, not a standing pick** — Opus 5 dominates it at every rung on our own calibration run; reach Fable only when the two named triggers persist past Opus·`high`.

**Two dials, two directions — tune `effort` before switching `model`, but know when a switch wins both.** `reasoning`/effort applies to *all* the tokens a session emits — text, tool calls, and thinking — and every one is billed at the **output** rate, so effort is a real cost multiplier, not a free quality knob. **Raise `reasoning` before upgrading `model` — but only up to each model's live ceiling**: Sonnet stops at `high` (`high`→`xhigh` dead rung on our own calibration run — a small accuracy gain for materially higher cost) and **Opus 5 stops at `high` too** (`high`→`xhigh` is a dead rung — no measurable gain for materially higher cost — and `max` weak, marginal at best). **But the economics run both ways.** Because effort multiplies output-rate tokens, on a genuinely hard task a **stronger model at *lower* effort** can be both better and cheaper than a weaker model cranked hot — the canonical example from our own calibration run: `Opus 5`·`medium` beats `Sonnet`·`high` AND the old `Fable`·`low` pin on both axes. **Never pair Opus with `max` as a routine rung** (operator-elected only, for exceptional one-shots), and treat **Fable as the escalation apex, not a standing pick** — Opus 5 dominates every Fable rung on our own calibration run; Fable stays credit-metered/paywalled, and a failed Fable dispatch auto-degrades to **Opus @ `high`** (plan-execute). Keep `effort` about wall-clock and `reasoning` about depth: a session can be `effort: XL` / `reasoning: low` (lots of mechanical edits) or `effort: S` / `reasoning: high` (one hard decision). *(Pricing 2026-07-25, /claude-api-verified: Sonnet 5 $3/$15 per MTok — intro $2/$10 through 2026-08-31; Opus 5 $5/$25 — same price as 4.8, GA 2026-07-24; Fable 5 $10/$50 + usage credits, paywalled. On typical sessions the model gaps compress, which is why the Haiku/Sonnet rows optimise for cost.)*

**Agent layer:**
- **`items`** (array of item IDs, required) — Items completed in this session.
- **`prompt`** (string, required) — The task body. `build_plan.py` writes it to `sessions/<id>.prompt.md`, wrapped with the item scope, a pointer to `sessions/<id>.context.md`, and the closeout contract. **Write only the task body — don't include closeout instructions yourself.** The subagent reads the generated prompt file; it is never JSON-escaped into the HTML.

  **Wargame contract (blind executability).** A dispatched session runs on a cheaper model with no one to ask — the prompt must leave it no judgment calls. For every non-trivial session prompt:
  - each move states its **expected observation** — exactly what the executor should see if the move worked;
  - each move names its **most likely failure**, what that failure signals, and the **counter-move**;
  - every fork carries a **trigger** — "if you observe X, take route B" — never "use judgment";
  - any assumption planning could not settle is marked **RECON NEEDED** with the exact check that settles it;
  - the prompt ends with **abort conditions**: the states where the executor stops and flags via closeout instead of improvising.

  The grade: could a mid-tier model run this session end to end without asking a single question? If not, the plan is banking the wrong things. (End-state verification still belongs in `verify.gates`/`deliverable`; this contract makes each *step* self-checking on the way there.)
- **`agent_instructions`** (array OR string, optional) — Extra agent-only notes. Rendered in the agent-spec and included in the generated context bundle.
- **`updated`** (string, optional) — ISO date.

**Dispatch block (`dispatch`, optional — drives `/plan-execute`):**
- **`subagent_type`** (string \| null) — The agent type `/plan-execute` dispatches. `null` (or omitted) = a **fresh `general-purpose` agent**: clean isolated context, and the per-session `model` IS honored. This is the right default for almost every session. A named type (`"Plan"`, `"Explore"`, `"code-reviewer"`, `"epic-implementer"`, …) is also a fresh agent with that type's tools/prompt. The literal `"fork"` is a true fork that inherits the orchestrator's context **and runs the orchestrator's model (the per-session `model` is ignored — build warns)**; reserve it for the rare session that needs the live conversation context. *(Earlier docs called `null` "a fork" — incorrect; omitting `subagent_type` yields a fresh agent, not a fork.)*
- **`parallel_group`** (string \| null) — Sessions sharing a group whose deps are all satisfied dispatch concurrently in one batch. Members of a group MUST share the same `depends_on` set (validated).
- **`depends_on`** (array of session ids) — These sessions must be DONE/terminal before this one is dispatched. No cycles, no dangling refs (validated).
- **`requires_human_checkpoint`** (bool) — If true, the loop sets this session to `AWAITS_REVIEW` and halts before dispatching it; the human continues with `/plan-execute … --resume`. **Requires the `checkpoint` decision brief below — build error without it.**
- **`checkpoint`** (object — **required when `requires_human_checkpoint` is true**, forbidden keys otherwise validated) — the decision brief `/plan-execute` presents when the gate parks the plan. Fields: **`reason`** (string, required) — why a human must look: what is irreversible or judgment-laden here, written in plain language for a reader with no context; **`decision`** (string, required) — the specific question the human answers; **`options`** (array of strings, optional) — the 2-4 concrete answers. If no real decision exists (the answer would always be "proceed"), don't declare the gate: use a `verify` gate or `checkpoint_policy: "notify-and-continue"`. Carried verbatim into `manifest.json`; `/plan-execute`'s `checkpoint` action and `PYBP checkpoint` output surface it as `checkpoint_brief`.
- **`max_retries`** (int 0–5) — Reserved for transient-error retry (v1.5). Semantic failures never retry.
- **`depends_on_policy`** (`"all"｜"completed_or_terminal"`, optional, default `"all"`) — how the session treats an upstream dep that **terminally failed**. `"all"` (today's behaviour) is a hard AND: every dep must complete or the session never dispatches. `"completed_or_terminal"` lets the session dispatch over the **completed subset** even when a dep terminally failed — for a capstone/synthesis session that should degrade rather than be stranded by one upstream stumble. Carried into `manifest.json` for `/plan-execute` to honour; a session using it should say in its prompt how it degrades (which items become `no-data`). *(Encodes the hardening that a prompt clause alone cannot: without this key the DAG silently strands the capstone.)*
- **`model_fallbacks`** (array of model-name strings, optional) — machine-readable degrade ladder the runner applies **before dispatch** if the primary `model` is unavailable (e.g. `["Opus"]` for a `Fable` session that must degrade to Opus 4.8 when Fable is paywalled). Beats prompt-text "degrade to X" prose, which is never read if allocation fails before the prompt runs.
- **`reasoning_fallbacks`** (array of reasoning-tier strings, optional) — the reasoning tier to pair with each `model_fallbacks` entry (e.g. `["xhigh"]`), so the degrade preserves an appropriate thinking budget.

If `dispatch` is omitted, defaults apply: `subagent_type=null`, `parallel_group=null`, `depends_on=[]`, `depends_on_policy="all"`, `requires_human_checkpoint=false`, `max_retries=0`, `model_fallbacks=[]`, `reasoning_fallbacks=[]`.

**Verify block (`verify`, optional — the "don't ship trash" gate):**

Declares automated gates that MUST pass before a session's self-reported `DONE`
actually becomes `DONE`. `/plan-execute` runs them at the apply boundary, BEFORE
shipping; absent → today's behaviour (DONE is taken at face value).

```json
"verify": {
  "gates": ["pytest-fast", "code-review-gate"],
  "on_fail": "rework",
  "max_rework": 2,
  "require_evidence": true
}
```

- **`gates`** (non-empty array; may be **omitted** when `require_evidence` is set) —
  ordered gate ids resolved through the SAME registry as
  `post_session.pre_deploy_gates`: `.claude/eval-gates.json` merged over the
  skill-bundled defaults (`eval-smoke-baseline`, `code-review-gate`). An unknown
  gate id is a **build-time error**. Gates run sequentially; all must pass.
- **`on_fail`** — `rework` (default) or `halt`. `rework` re-dispatches the session
  with the gate's (redacted) output appended as feedback; `halt` stops for a human.
- **`max_rework`** (int 0–5, default 1) — the bound on the rework loop. After it's
  exhausted, the session goes `BLOCKED` + halt.
- **`require_evidence`** (bool, default `false` — **Vista ③**) — when `true`, the
  session's closeout MUST carry an `evidence` array (paths to proof-of-engagement
  artifacts: eval JSON, a `grep -c <log-event>` count > 0, a screenshot, a
  transcript). `/plan-execute` refuses `DONE` at `verify-finalize` until every
  listed path exists and is non-empty — a miss reworks/halts like a failed gate. May
  stand alone (no `gates`). Steer it onto sessions where a metric could move without
  the mechanism truly engaging.
- **`checks`** (array of objects, optional) — **named evidence-artifact contracts** that
  turn a load-bearing hardening from prompt prose into a machine-readable requirement.
  Each item is `{"name": <str>, "evidence_path": <str>, "assert": <str, optional>}`:
  `evidence_path` is the artifact the session MUST produce (the runner asserts it exists +
  is non-empty at `verify-finalize`, via the same enforcement as `require_evidence`);
  `assert` is a human-readable description of what the artifact must show. `checks` renders
  as an **Evidence contracts** list in the session prompt and seeds the closeout `evidence`
  example with these paths. Composes with `require_evidence` (pair them so the existence
  gate fires); may also stand alone as a valid verify block. Use it when a hardening clause
  ("build over the completed subset", "log a commit-boundary event", "diff the structural
  status") would otherwise be un-checkable prose — name the artifact that proves it ran.
  Example:
  ```json
  "checks": [
    {"name": "coverage-map", "evidence_path": "_evidence/s09/coverage-map.json",
     "assert": "each upstream dep marked shipped|no-data"}
  ]
  ```

Phase-level default: put `verify` on a `phases[]` entry; a session with `"phase":
"pN"` inherits it, and its own `verify` overrides. **Author a verify gate on every
session whose `deliverable` is checkable** — tests for code, `/eval --smoke` for
search/scoring, `code-review-gate` for ship-ready work. A verify gate is how a plan
self-drives safely: it turns "did the agent really finish?" from a human read into
an automatic check. See `../plan-execute/references/verify-gates.md`.

**Shipping block (`post_session`, optional — drives version-control + deploy posture):**

Declares what `/plan-execute` does AFTER a session's closeout is applied — commit, push, open a PR, run pre-deploy gates, deploy. Plan-declared shipping is **pre-authorized** (example-project's CLAUDE.md "plan-enumerated destructive calls are pre-authorized" rule). Absent → today's behaviour (nothing fires; shipping is opt-in).

```json
"post_session": {
  "git": "none | commit | commit-push | commit-push-pr",
  "deploy": "none | <target-name>",
  "deploy_argv": ["explicit", "argv", "vector"],
  "pre_deploy_gates": ["eval-smoke-baseline"],
  "rollback_hint": "<command or note surfaced if this deploy is later invalidated>",
  "skip_if_partial": true,
  "command_failure_mode": "fail-halt | best-effort",
  "kind": "ship-ready | spike",
  "env_allowlist": ["AWS_PROFILE"]
}
```

- **`git`** — `none` (default) / `commit` (`/commit-orchestrate`) / `commit-push` (+ `--push-after`) / `commit-push-pr` (+ `/pr create`). Each value expands to ordered, resumable sub-steps (commit → push → pr).
- **`deploy`** — `none` (default) or an **opaque target name** resolved through the project-local registry `.claude/deploy-targets.json` (see `plan-execute/references/shipping-registries.md`). The schema is project-agnostic: example-project's `example-project-ec2` / `example-project-aws` live in example-project's repo, not here. An unknown target → **build-time error**.
- **`deploy_argv`** — escape hatch for non-registry deploys: an explicit argv vector run `shell=False`, with an **allow-listed env** (never inherited `os.environ`). NEVER a shell string. Relative-path executables are rejected.
- **`pre_deploy_gates`** — gate ids resolved through `.claude/eval-gates.json` (or the skill-bundled `eval-smoke-baseline` default). Each gate has a defined pass/fail contract; a gate failure halts before any deploy.
- **`rollback_hint`** — **mandatory for any deploy-bearing session.** Surfaced on a later-invalidation halt so the operator has a starting point. Rollback execution itself stays manual.
- **`skip_if_partial`** — if true, ALL shipping is skipped unless the closeout `result` is `DONE`.
- **`command_failure_mode`** — `fail-halt` (default for deploy; non-zero exit halts) or `best-effort` (10s timeout, exit ignored, never blocks — fire-and-forget hooks only).
- **`kind`** — `ship-ready` (recommended `git: commit-push`) or `spike` (recommend `commit` or `none` — pushing throwaway commits pollutes shared history). Steers the authoring default; the skill never auto-fills.

**Top-level `phases` (optional) + per-session `phase` — phase-default shipping:**

```json
"phases": [
  { "id": "p1", "phase_closer": { "git": "commit-push-pr", "deploy": "example-project-ec2",
      "pre_deploy_gates": ["eval-smoke-baseline"], "rollback_hint": "…",
      "require_human_checkpoint": false } }
]
```

A `phase_closer` is a `post_session` shape plus `require_human_checkpoint` (a phase-level checkpoint that ALWAYS outranks shipping). A session sets `"phase": "p1"` to inherit that phase's `phase_closer` as defaults; its own `post_session` overrides per key. Resolution happens at **build time** — `/plan-execute` reads the already-merged block and never re-merges. **Prefer authoring deploy-bearing sessions at phase end**, after the gates that would invalidate the deploy have passed.

### Changelog — relation to ADR-027

This `post_session` shape **extends** the per-session pattern ADR-027 introduced (example-project, 2026-05-20). Additions over ADR-027: the `deploy:` enum with a project-detection guard, the `deploy_argv` escape hatch with `command_failure_mode`, first-class `pre_deploy_gates`, `skip_if_partial`, and phase-level `phase_closer` inheritance. Schema evolution is **additive** — ADR-027-shaped manifests remain valid input. Do not author a competing ADR; cite ADR-027.

### Session / item IDs (validated, P10)

Both must match `^[a-z][a-z0-9-]{1,63}$` — a lowercase letter first, then lowercase letters / digits / hyphens, 2–64 chars. No underscores, no uppercase, no Windows-reserved names (`con`, `prn`, `nul`, `lpt0`–`lpt9`, …). IDs become filenames (`sessions/<id>.prompt.md`) so they must be filesystem-safe.

### Statuses (8)

`TODO` · `DOING` (subagent in-flight) · `DONE` · `PARTIAL` (some items done, needs continuation) · `AWAITS_REVIEW` (human checkpoint) · `BLOCKED` · `DEFERRED` · `WONTFIX`. The orchestrator sets `DOING`/`DONE`/`PARTIAL`/`BLOCKED`/`AWAITS_REVIEW`; `DEFERRED`/`WONTFIX` are terminal and skipped.

### Authoring guidance

When interviewing the user, the highest-leverage fields to draft *for them* are `human_summary` and `deliverable` — these turn the plan from "a list of tickets" into "a story the principal can follow at a glance." The author rarely volunteers them, but Claude can draft both from the title and existing description and ask the user to refine.

Bias toward writing `human_summary` in the voice of someone explaining the work to a peer in the hallway — short, concrete, no jargon-for-jargon's-sake. Save acronyms, code, file paths and dev steps for `agent_instructions` and `description`, where they belong.

When ordering items within a session prompt or a category, lead with the decisions most likely to change — data-model shapes, type interfaces, user-facing behavior — and put mechanical work (renames, formatting, boilerplate wiring) last. Steer any such judgment call onto `tweak_likelihood` + `alternatives` (see "Decision hotspots" above) rather than leaving it implicit, so it surfaces in the hotspots section instead of getting buried in execution order.

### Infographics

See `infographic-templates.md` for descriptions and which template fits which plan archetype.
