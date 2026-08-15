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
      ],

      "prior_art": {
        "decision": "adopt | adapt | build",
        "source":   "what the one-question research pass found (a tool/library name + URL, or a citation that nothing credible exists)",
        "note":     "(required when decision=build AND a credible alternative surfaced) why build won anyway"
      }
      /* MUTUALLY EXCLUSIVE with research_status below — see 'Prior-art decision' */
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
- **`out_of_scope`** (array of strings, optional; 2026-08-01, wayfinder-derived) — work consciously ruled beyond this plan. Rendered as a static "Out of scope" dashboard section and carried into `manifest.json`. The rule that gives it teeth: an entry here **never graduates into a session** — it returns only as a fresh plan if the goal is redrawn. The closing acceptance review reads it to check nothing ruled out got built anyway, and accumulated closeout `deviations` are judged against it.
- **`open_questions`** (array of strings, optional; 2026-08-01) — the fog register: decisions the interview surfaced but could not state sharply enough to resolve or assign to a decision session. Each entry should name the question plus what would sharpen it (e.g. "Which auth provider? — sharpens after the s02 spike"). Rendered as a static "Open decisions" dashboard section and carried into `manifest.json`, where plan-harden's `decision-debt` lint cross-checks it against session prompts. Prefer an explicit decision session over an entry here whenever the question can already be stated precisely — this list is for what genuinely can't be sharpened yet, not a parking lot for avoidable ambiguity.
- **`serial_reason`** (string, optional; 2026-08-12, PL-03) — why this plan is a **chain** rather than a fan-out. Its only effect: it silences the pure-chain warning `validate_spec()` raises when every dependency layer is one session wide (see "Parallel groups" below). Non-empty or omitted — a blank string is refused, because it would silence the warning without answering it. Carried into `manifest.json`, so plan-harden's parallelization lint reads the author's stated reason instead of re-flagging a chain that was already justified. Good reasons: one hand on one interface, a migration that must land in order, a spike whose result reshapes the next session. Not a reason: "it was easier to write".
- **`plan_schema_version`** (integer, optional but **stamp `5` on every new spec**) — the spec's own opt-in. Two build-time requirements key off it, both deliberately unenforced when it is absent so that specs written before the feature existed (and the live plans `plan_mutate` re-validates on every add/amend/retire) keep validating exactly as they did: the mandatory per-item **prior-art decision** at `>= 4` (`PRIOR_ART_MIN_SCHEMA`), and the **parallel-group contract** at `>= 3` (`PARALLEL_CONTRACT_MIN_SCHEMA`). Distinct from the `plan_schema_version` `gen_manifest` stamps on the OUTPUT manifest, which is always `build_plan.PLAN_SCHEMA_VERSION`.
- **`research_env`** (object, **written by the builder — never authored by hand**; RS-06, 2026-08-13) — the build-time research-environment record. `build_plan.build()` stamps it onto `spec.json` (and `gen_manifest` copies it into `manifest.json`) whenever *any* item carries a `research_status`, so the machine's own account of the environment sits beside the author's prose instead of the skip record being purely hand-written. Shape: `{available, signals[], sources[], method, probed_at}` — `signals` names each capability found (`mcp:exa`, `builtin:WebSearch`), `sources` names each config file actually read, `method` restates the probe's limits verbatim. Absent from a spec that makes no skip claim, which is every plan built before 2026-08-13 — that is what keeps `plan_mutate`'s "manifest.json == `gen_manifest(spec.json)`" check passing on existing plans. `gen_manifest` READS it off the spec and never re-probes, so regenerating a manifest on another machine reproduces the same bytes.
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

#### Prior-art decision (item-level — MANDATORY, one of two shapes, 2026-08-12)

<!-- RS-01/RS-02, plan-framework-upgrade-2026-08-12 s09 -->

The interview's prior-art pass (see `SKILL.md` → "Prior-art pass") asks one bounded
research question per item — does a proven solution already cover this scope? — and
records an explicit decision. **The decision itself is not optional**: every item
must carry EITHER `prior_art` (a real decision with a source) OR `research_status:
"skipped"` (an honest degrade, e.g. no research MCP tier was reachable). What's
optional is only which of the two shapes — `validate_spec` (`build_plan.py`) refuses
to build a spec where an item has neither, so a builder can no longer omit the
research step silently while build and harden stay green.

- **`prior_art`** (object, one of two mandatory shapes) — `{decision, source, note}`:
  - **`decision`** (`"adopt"|"adapt"|"build"`, required) — `adopt`: an existing
    tool/library covers the scope as-is, use it. `adapt`: an existing solution covers
    most of it; note what still needs custom work. `build`: nothing credible covers
    it, or nothing found actually fits.
  - **`source`** (string, required, non-empty) — what the one-question research pass
    found: a tool/library name + URL for `adopt`/`adapt`, or a citation/note that
    nothing credible turned up for a clean `build`. **A `build` decision with an
    empty `source` is a build-time error** — "we didn't look" is not a decision.
  - **`note`** (string, optional at build time — not `validate_spec`-enforced) —
    free text. **Required in spirit whenever `decision: "build"` and a credible
    alternative DID surface** — state why build won anyway. This is what the P8
    interview step means by "a build decision where credible prior art surfaced
    requires a stated reason"; `build_plan.py` cannot itself judge whether an
    alternative "surfaced" (that's a research outcome, not a schema shape), so the
    real enforcement is `/plan-harden`'s Fork B prior-art challenge (RS-02): it
    re-researches every `build` item adversarially and surfaces any credible
    alternative it finds as a decision card, `note` or not — an empty `note` on a
    real alternative gets caught there, one layer later, not at build time. An item
    with `note` set on a `build` decision is
    additionally pulled into the "Decision hotspots" section (above), even without
    `tweak_likelihood` — a build call made despite a known alternative is inherently
    a judgment call worth surfacing.
  - Rendered inside the item's agent-spec as a "Prior art" block, always in the DOM
    (see "Why the Operating Manual matters" — the agent layer, not the human layer).
- **`research_status`** (string, one of two mandatory shapes) — two legal values:
  - **`"skipped"`** — the author chose not to run the pass. Pairs with
    **`research_reason`** (string, required, non-empty) — why (the item is too small
    to be worth the question, the answer is already known, etc.). This is the
    explicit "skipped-with-notice" path: it must never be indistinguishable from a
    silently omitted `prior_art`. Rendered inside the agent-spec as a
    "Prior art — research skipped" note.
  - **`"unavailable"`** *(RS-06, 2026-08-13)* — **the research tooling was not there.**
    This is the ONE claim the builder adjudicates instead of accepting: at build time
    `validate_spec` runs `probe_research_tools()` and **refuses the spec** if that probe
    can see research capability configured in this environment, naming exactly what it
    found. `research_reason` is OPTIONAL here — the system supplies the reason. Rendered
    as "Prior art — no research tooling available (verified at build time)".
    **What the probe actually is, stated plainly: a CONFIGURATION probe, not a
    reachability probe.** `build_plan.py` is a plain script; it does not hold the
    research tools (the authoring agent does, through the harness), so it cannot ask
    whether Perplexity answered. It reads only: configured MCP server names
    (`~/.claude.json` → `mcpServers`, this project's entry, `<project>/.mcp.json`) and
    `permissions.deny` in the user/project `settings*.json`. No network call, no
    credential read, no server VALUES — names and permission entries only. It proves
    "configured and permitted", never "reachable". On a stock Claude Code install the
    built-in `WebSearch`/`WebFetch` alone make the verdict *available*, so
    `"unavailable"` is nearly always a false claim — which is the point of checking it.
    Fails permissive: when no Claude Code configuration exists at all (a bare CI
    checkout), the probe reports nothing found and the claim is accepted.
    **Second stated limit:** it describes the **Claude Code** configuration on the
    machine running `build_plan.py`, which is not necessarily the harness the
    authoring agent runs in. Under `/plan-builder` in the Codex lane — no MCP research
    tier there, but a fully configured `~/.claude.json` here — the honest shape is
    `"skipped"` with the reason stated, not `"unavailable"`.
    **No version gate, and none is needed:** `"unavailable"` was an illegal value before
    this change, so no spec that has ever built can carry it — every new refusal fires
    only on specs that could not previously build at all. Measured across all 713
    `spec.json` files under all 31 `_plans/` trees on disk: zero verdict changes, zero
    `gen_manifest`-consistency changes.
- Mutually exclusive in practice (a build-time error covers the omit-both case;
  supplying both is not itself rejected, but author one, not both).

### Sessions — same dual-layer pattern

**Human layer:**
- **`id`** (string, required) — Convention: `sNN` (e.g., `s01`).
- **`title`** (string, required)
- **`model`** (`"Haiku"|"Sonnet"|"Opus"|"Fable"` — or a Codex token `"gpt-5.6-sol"|"gpt-5.6-terra"|"gpt-5.6-luna"`, required) — Drives the model chip color (teal / blue / amber / violet — Codex tokens all share one slate "Codex" color). Free-form tolerant at render time: any value gets a chip, but only these seven tokens have a dedicated color (unknown names fall back to Sonnet's) — and `build_plan.py` warns at build time when a value won't normalize to a dispatchable token (fable/opus/sonnet/haiku/gpt-5.6-sol/gpt-5.6-terra/gpt-5.6-luna), since `/plan-execute` would then omit `model` and the subagent inherits the orchestrator's model. A session pinned to a Codex token dispatches under `/plan-execute --harness codex` (Codex CLI) instead of the Cowork picker — see "Codex rubric" below for the model-per-kind map.
- **`effort`** (string, optional) — Free-form **size / wall-clock** estimate, e.g. `"S"`, `"~2h"`, `"half day"`. This is *time weight*, NOT cognitive depth — for depth use `reasoning`.
- **`reasoning`** (`"low"|"medium"|"high"|"xhigh"|"max"`, optional) — **Cognitive-effort tier**, orthogonal to `effort`. Renders as a `◐`-prefixed chip (grey / blue / amber / orange / pink) and is carried into `manifest.json` so the runner can size the dispatched model's thinking budget. Signals how much thinking depth the session needs: mechanical → `low`; standard build → `medium`; coding / agentic / integration → `high` (**the coding/agentic default — on Sonnet, `xhigh` is a dead rung on our own calibration run (a small accuracy gain for materially higher cost); from `high`, escalate MODEL, not effort**); ambiguous design tradeoffs / deep reasoning → `Fable` at `low`; reserve `max` for open-ended or irreversible calls (never on Opus — see the rubric). `xhigh`/`extra` are synonyms; `Haiku` rejects the reasoning dial entirely (pair it with `low`).
- **`human_summary`** (string, optional, *strongly recommended*) — One or two sentences in plain English about what this session achieves. Rendered in serif at 18px — the most prominent text on the card. The human reader reads this first.
- **`deliverable`** (string, optional, *recommended*) — Concrete outcome at session end. Rendered as a green callout.
- **`why_model`** (string, optional) — One sentence rationale for the model choice. Rendered as italic line.
- **`peer_triggers`** (array of `"architecture_decision"|"irreversible_change"|"security_sensitive"`, optional) — **Structured second-model-review gate.** Declares that this session does work matching one or more `codex_peer` triggers (SSOT `codex_peer.triggers`). When non-empty, the session MUST carry an `adversarial-review` entry in `verify.gates` (or mention `/adversarial-review` in its `prompt`) — the §4.0 model-lint promotes a missing gate here from advisory to a 🔴 **plan-killer** (the one deterministic model-lint flag that can block). This is the structured successor to the old keyword-heuristic `codex-trigger-no-gate` scan: declare the trigger explicitly and the gate is enforced, instead of guessing from title text. Omit (or `[]`) for sessions that don't touch architecture/irreversibility/security — the keyword scan still runs as a soft 🟡 "you may have forgotten to declare this" net. Carried into `manifest.json`. (Distinct from `task_class` below — the model-routing class, which became a real optional session field at s06.)
- **`acceptance_review`** (bool, optional; 2026-07-26) — marks THE closing plan-level acceptance-review session. At most one per plan. Full contract + the authoring template: "Closing acceptance review" below.
- **`escalation`** (bool, optional; ESC-02, 2026-08-13, `plan_schema_version` 6+) — the per-session opt-OUT of the upward rework climb. Default (omitted) is **on**: when this session's verify gate fails twice at the SAME normalised root cause, the next re-dispatch runs one rung UP the SSOT's ladder (`sonnet@high → opus@high → fable@medium → fable@high → fable@xhigh`, then it stops). Set `false` to pin the session to its authored model however often it fails — appropriate when the session is deliberately calibrating a specific cell, or when a bigger model would change what is being measured. Only `false` is emitted into `manifest.json`, so a spec that omits it produces a byte-identical manifest. `true` is the default and writing it changes nothing; a non-bool (including the string `"false"`, which is truthy) is a **build error**, not a surprise at the third rework. A session that declares `model` but no `reasoning` never escalates either, whatever this field says — an unset effort is not a ladder rung, so there is no rung above it to name; `/plan-execute` says so on stderr the first time such a session would have climbed.

- **`routing_experiment`** (object, optional; 2026-08-13, `plan_schema_version` 6+) — tags this session as belonging to a named routing canary, so the routing outcome ledger can separate its records from the general population instead of averaging a deliberate experiment into the baseline. Shape: `{"kind": "<experiment family, e.g. effort_canary>", "proposal_id": "<the proposal this session tests>"}`. Both keys are REQUIRED when the block is present (an untagged canary record is indistinguishable from an ordinary one, which defeats the point) and any other key is refused. Carried into `manifest.json` verbatim, and ONLY when declared — the ledger reads it back from there and never re-derives it.

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

#### Codex rubric — the `gpt-5.6-*` lane (dual-harness; 5.6-ONLY since 2026-08-13)

A session can be pinned to a **Codex model** instead of a Claude one. `build_plan.py`
validates and renders `gpt-5.6-sol` / `gpt-5.6-terra` / `gpt-5.6-luna`
exactly like the four Claude tokens above (no warning, a dedicated chip color) — the
only difference is the chip is slate "Codex" rather than teal/blue/amber/violet, and
`/plan-execute --harness codex` dispatches the session through the Codex CLI instead of
the Cowork picker. Evidence: DeepSWE 2026-07-27
(`evals/routing/external-priors-gpt56-2026-07.md`) — the same numbers back
`model-routing.yaml`'s `providers.openai`.

| Session kind | `model` | `reasoning` | Why |
|---|---|---|---|
| Mechanical (rename sweep, formatting, codemod, doc edits) | `gpt-5.6-luna` | `max` | $0.61, 67% DeepSWE — luna's ONLY usable rung; see the collapse warning below. |
| Standard build (CRUD, wiring, templated features) | `gpt-5.6-luna` | `max` | $0.61, 67% DeepSWE — moved off terra at v1.14 (operator-elected on price: 3 points for 6.5×). `gpt-5.6-terra`/`max` ($3.96, 70%) is the escalation, not the default. |
| Integration / multi-file refactor / non-obvious debugging (agentic build) | `gpt-5.6-sol` | `xhigh` | $4.70, 71% DeepSWE — sol's proven escalation rung (matches `adversarial-review`'s own on-disk default). |
| Architecture, ambiguous tradeoffs, hard root-cause (deep reasoning) | `gpt-5.6-sol` | `xhigh` | Same cell as agentic build — the lane has no cheaper dedicated deep-reasoning rung. |
| Linchpin (one-shot irreversible, plan-foundational) | `gpt-5.6-sol` | `max` | $8.39, 73% DeepSWE — sol's ceiling; reserve for the rung that earns it. |

**⚠ Luna collapses below `max`.** `gpt-5.6-luna`'s DeepSWE accuracy falls off a cliff
as effort drops — `max` 67% → `high` 44% → `medium` 11%. Never pair `gpt-5.6-luna`
with any `reasoning` other than `max`; a "cheap" luna session run at a lower tier
isn't cheaper, it's broken.

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
- **`parallel_group`** (string \| null) — Sessions sharing a group whose deps are all satisfied dispatch concurrently in one batch. Members of a group MUST share the same `depends_on` set (validated). A member is bound by the frozen parallel-group contract — see "Parallel groups" below before using it.
- **`isolation`** (`"worktree"` \| null; 2026-08-12, contract M3) — declares that this group's members run in **separate orchestrator-managed git worktrees** (`git worktree add`). Group-level: every member must carry the same value, and a half-isolated group is refused. `"worktree"` is the ONLY legal value — a typo is refused, never quietly read as "no isolation" — and it never means the Agent tool's own `isolation: "worktree"` parameter, which is barred by name (measured to destroy untracked agent output). Absent/`null` = a shared-tree group, which stays legal.
- **`integrates_group`** (string, optional; contract §3) — this session is the named group's **integration session**: it depends on every member, re-runs the union of their gates on the merged tree, and owns the group's single git action. Declared, never derived. For a **worktree-isolated** group you do not write it — `build_plan.py` emits it (see "Parallel groups" below) — but an author-written one is left untouched.
- **`depends_on`** (array of session ids) — These sessions must be DONE/terminal before this one is dispatched. No cycles, no dangling refs (validated).
- **`requires_human_checkpoint`** (bool) — If true, the loop sets this session to `AWAITS_REVIEW` and halts before dispatching it; the human continues with `/plan-execute … --resume`. **Requires the `checkpoint` decision brief below — build error without it.**
- **`checkpoint`** (object — **required when `requires_human_checkpoint` is true**, forbidden keys otherwise validated) — the decision brief `/plan-execute` presents when the gate parks the plan. Fields: **`reason`** (string, required) — why a human must look: what is irreversible or judgment-laden here, written in plain language for a reader with no context; **`decision`** (string, required) — the specific question the human answers; **`options`** (array of strings, optional) — the 2-4 concrete answers. If no real decision exists (the answer would always be "proceed"), don't declare the gate: use a `verify` gate or `checkpoint_policy: "notify-and-continue"`. Carried verbatim into `manifest.json`; `/plan-execute`'s `checkpoint` action and `PYBP checkpoint` output surface it as `checkpoint_brief`.
- **`max_retries`** (int 0–5) — Reserved for transient-error retry (v1.5). Semantic failures never retry.
- **`depends_on_policy`** (`"all"｜"completed_or_terminal"`, optional, default `"all"`) — how the session treats an upstream dep that **terminally failed**. `"all"` (today's behaviour) is a hard AND: every dep must complete or the session never dispatches. `"completed_or_terminal"` lets the session dispatch over the **completed subset** even when a dep terminally failed — for a capstone/synthesis session that should degrade rather than be stranded by one upstream stumble. Carried into `manifest.json` for `/plan-execute` to honour; a session using it should say in its prompt how it degrades (which items become `no-data`). *(Encodes the hardening that a prompt clause alone cannot: without this key the DAG silently strands the capstone.)*
- **`codex_shell`** (object, optional; 2026-07-29) — **the shell capabilities this session needs when a `codex exec` process runs it** (under `/plan-execute --harness codex`, or a Codex-pinned session on the Claude harness). Omit for work confined to the repo. The default dispatch is `--sandbox workspace-write`, MEASURED on codex-cli 0.145.0 to deny **writes outside the repo workspace**, **all network** (`CODEX_SANDBOX_NETWORK_DISABLED=1`), and **nested `codex exec` / vendor CLIs** (`failed to initialize in-process app-server client`). A session needing any of those must declare it, or a Codex run dispatches it and fails it after paying for it. Fields:
  - **`writable_roots`** (array of paths) — extra roots the session may write, e.g. `["~/.dyno"]` when the plan's artifacts live outside the repo. `~` is expanded at build time (inside a quoted TOML string the shell cannot). Renders `-c sandbox_workspace_write.writable_roots=[…]`.
  - **`network`** (bool) — live network for the dispatched session. Renders `-c sandbox_workspace_write.network_access=true`.
  - **`env_include`** (array of environment-variable names) — explicitly forward only these parent variables to commands spawned by Codex. Credential values never enter the plan, command, receipt, or log. The runner adds `PATH`, `HOME`, and `TMPDIR`, then renders `shell_environment_policy.inherit=all`, `ignore_default_excludes=true`, and the exact `include_only` allowlist. Wildcards are rejected.
  - **`sandbox`** (`"workspace-write"` default | `"danger-full-access"`) — `danger-full-access` is the ONLY thing that grants **nested agent dispatch** (a session shelling out to `codex exec` / `claude -p` / another vendor CLI), and it hands that session an **unsandboxed** shell. Two build-time rules: it **forbids** `writable_roots`/`network` beside it (already granted; the command must have one reading), and it is legal ONLY on a session a human already gates — `dispatch.guards_irreversible`, `requires_human_checkpoint`, `task_class: linchpin`, or `peer_triggers: [irreversible_change]`. `build_plan.py` refuses an ungated one and `run.py` refuses again at dispatch (`UngatedFullAccessSession` → `BLOCKED` + halt): the gate protecting an unsandboxed agent must not be enforced only by the tool that wrote the manifest.

  Absent/empty ⇒ the emitted command is byte-identical to the pre-2026-07-29 form, so existing plans are unaffected. The grant is disclosed in the `begin` translation receipt (`shell` line), each member's `codex_shell_grant`, and the `codex_dispatch` ndjson event. Full contract, probe by probe: `../plan-execute/references/dual-harness-contract.md` § 4.5.
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

#### LLM review level by task class (s10 RV-02 — steering, never silent auto-fill)

Three bundled argv gates run a **headless `/code-review`** over the session's
working-tree diff in a **fresh context**, and turn its findings block into an exit
code (`0` no findings · `1` findings · `2` INDETERMINATE — see
`../plan-execute/references/verify-gates.md` → "LLM review gates"). Propose one per
**ship-ready** session from its `task_class`:

| `task_class` | Gate id | Why this depth |
|---|---|---|
| `mechanical`, `standard_build` | `llm-review-low` | Few, high-confidence findings. A rename sweep or a templated CRUD wire-up doesn't need a reviewer speculating; it needs the one real mistake caught cheaply (~30 s). |
| `agentic_build` | `llm-review-medium` | Multi-file integration is where non-obvious breakage lives. Medium verifies candidate findings by running the code before reporting (measured), so the extra minute buys confirmed bugs, not guesses. |
| `deep_reasoning`, `linchpin` | `llm-review-high` | Broader coverage, and explicitly allowed to raise **uncertain** findings. On architecture / security / one-shot-irreversible work an uncertain finding is worth a human minute; on a rename it is noise. |

**The level is a spend decision too.** Measured on a ten-line diff: low ~$0.53,
medium $0.58–0.72, high $0.75–3.40 per run — and a gate re-runs on every rework
attempt. A ~6× spread is why the level follows `task_class` instead of defaulting
to high.

Rules that keep this honest:

- **Ship-ready sessions only.** A spike, a research session, a decision session or a
  docs-only session gets no review gate — there is no diff worth a reviewer's fresh
  context, and a review gate on a session that ships nothing is a tax with no catch.
- **Propose, don't auto-fill.** Name the level and the reason ("agentic_build →
  `llm-review-medium`"); the author confirms, drops it, or moves a level. Silent
  insertion is how plans acquire gates nobody understands and everybody overrides.
- **Stacks with `adversarial-review`, doesn't replace it.** A session with
  `peer_triggers` still needs its `adversarial-review` gate: that is a *second model*
  arguing about the design; this is a *fresh context* reading the diff. Different
  reviewer, different question — declare both.
- **Not `code-review-gate`.** Despite the name, that id is the project's
  DETERMINISTIC test/lint gate (see `.claude/eval-gates.json`). Never overload it.
- **Order matters.** Put the deterministic gates first (`gates: ["code-review-gate",
  "llm-review-medium"]`) — gates run sequentially and stop at the first failure, so a
  broken build should fail on the cheap test gate, not after paying for a review.

```json
"verify": {
  "gates": ["code-review-gate", "llm-review-medium"],
  "on_fail": "rework", "max_rework": 2
}
```

#### Closing acceptance review (`acceptance_review`, optional — the plan-level gate)

<!-- added 2026-07-26. Verify gates check the PARTS; this checks the WHOLE. -->

- **`acceptance_review`** (bool, default `false`) — marks THE closing session that
  validates the **plan's** objectives, not one session's. At most one session per plan
  may set it (a second is a build error). Carried into `manifest.json`; read by
  `/plan-execute` (it surfaces this session's verdict at `complete` instead of a bare
  "success") and by plan-harden's `acceptance-review-missing` lint. `build_plan.py`
  warns at build time when a plan of ≥4 sessions declares none.

**Why it exists.** Verify gates, `require_evidence` and the structural DONE-gate all
answer *"did THIS session finish?"*. Nothing answered *"did the PLAN achieve what it
set out to?"* — and a plan whose every session closed `DONE` can still miss its
objectives: `deviations` accumulate session by session, items get parked
`DEFERRED`/`WONTFIX`, and each local gate passes while the integrated outcome goes
unexamined. That is the verification/validation split, and this session is the
validation half. (`epic-dev` has had the equivalent for a while: its Phase-8
requirements-traceability quality gate, run by a deliberately isolated agent.)

**Author it as a normal session** — it needs no new runtime machinery, only these
choices:

| Field | Value | Why |
|---|---|---|
| `dispatch.subagent_type` | `null` (fresh agent) | Isolation is the point. The orchestrator that drove the plan is the *last* reader who should judge it — it shares every optimistic assumption the closeouts made. Never `"fork"`. |
| `dispatch.depends_on` | every other terminal session | Makes it structurally last; the graph, not prose, enforces the ordering. |
| `task_class` | `deep_reasoning` | Cross-session synthesis over ambiguous evidence. Resolves to `Opus`/`medium` under `active_provider: anthropic` — keep `model`/`reasoning` in step with the resolver, or plan-harden's `task-class-model-mismatch` fires. Escalate the MODEL to `Fable` only for a very large plan whose closeout+repo corpus is a genuine large-context read. |
| `items` | `[]`, or one tracking item | Its deliverable is a judgement, not a work item. |
| `verify.require_evidence` | `true` | The verdict must exist as a file, not as chat narration. Pair with a `checks` entry naming the report path so the artifact contract is machine-checked. |
| `dispatch.requires_human_checkpoint` | `false` | That gate fires *before* dispatch. The decision comes from the closeout instead — see below. |

**The verdict drives the checkpoint, conditionally.** Tell the session in its `prompt`:
set `human_checkpoint_reason` **only when the review finds gaps**, phrased as the
decision the human actually has to make (accept the gap as-is · dispatch a
gap-closing session · descope it explicitly) — the harness's own rule is that a gate
whose only sane answer is "proceed" should not exist. A clean `ACHIEVED` verdict
therefore lets the plan complete without a halt, with the verdict recorded in the
session's notes and its report artifact. When it *does* park the plan, the operator
acks it via the post-session-checkpoint procedure in
`../plan-execute/SKILL.md` § "Operator gotcha" (`--resume` would re-dispatch it).

**Prompt shape.** The acceptance criteria live verbatim in this session's `prompt`
(numbered, one checkable statement each — 3–7 is the useful range) plus a one-line
summary in its `deliverable`; there is no separate plan-level criteria field. The task
body should tell it to:

1. Read the criteria below, then `_closeouts/*.json` for every session — `notes`,
   `deviations`, `items_blocked`, `learnings`, `degraded_from` — and PLAN.html for any
   `DEFERRED`/`WONTFIX` item. When the manifest carries `out_of_scope`, also check the
   scope boundary both ways: nothing ruled out got built anyway, and no accumulated
   deviation quietly descoped work the criteria depend on.
2. **Verify each criterion against the repository/system itself**, not against the
   closeouts' claims. A closeout is a report; the criterion is about reality. Quote
   `file:line`, a command's output, or a test result per criterion.
3. Return a verdict per criterion (`ACHIEVED` / `GAP` with the evidence) plus one
   overall verdict, and write it to the report path named in `verify.checks`.
4. Ship it in the eval-deliverable shape `/plan-execute` § PS-02 requires — one
   rendered one-pager with the data inline, plus a decision card of **at most three**
   options. Never a narration wall, never a multi-page HTML maze.
5. **For a plan that shipped substantial code**, put one line on the decision card
   RECOMMENDING the operator run `claude ultrareview` over the plan's accumulated diff
   before it merges — the cloud multi-agent review, which reads the whole change at
   once rather than session by session. **The session never launches it**: it bills
   **$5–25** per run, so it is operator-elected, always. Recommend it, name the target
   (branch or PR), and stop. Skip the line entirely for a docs/config plan — the
   recommendation only earns its price against real code.

```json
{
  "id": "s12", "title": "Acceptance review", "model": "Opus", "reasoning": "medium",
  "effort": "~45m", "task_class": "deep_reasoning", "acceptance_review": true,
  "items": [],
  "human_summary": "Independent check that the plan actually delivered what it promised — not that each session said it was done.",
  "deliverable": "A one-page acceptance report scoring all 5 plan criteria ACHIEVED or GAP, each with evidence, plus a decision card if any gap remains.",
  "why_model": "Cross-session synthesis over ambiguous evidence (deep_reasoning -> Opus/medium); a fresh agent so it doesn't inherit the orchestrator's optimism.",
  "prompt": "Validate this plan against its acceptance criteria. ...\n\nCriteria:\n1. ...\n2. ...",
  "dispatch": {"subagent_type": null, "depends_on": ["s09", "s10", "s11"], "requires_human_checkpoint": false},
  "verify": {
    "require_evidence": true,
    "checks": [{"name": "acceptance-report", "evidence_path": "_evidence/acceptance-review.md",
                "assert": "one verdict line per criterion, each citing file:line, a command output, or a test result"}]
  }
}
```

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

### Parallel groups — the frozen contract, and what the builder does about it

The authority is `../plan-execute/references/parallel-group-contract.md` (contract **v1**,
FROZEN 2026-08-12). It changes by decision, never by drift from an implementation that found it
inconvenient; this section says what plan-builder *does*, not what the rules *are*.

**The interview question** (SKILL.md → "Parallelism"): *"which of these sessions could run at the
same time, if they couldn't conflict?"* Then: freeze shared interfaces in an early sequential
session, fan out file-disjoint consumers, name the integration point, and ask for `touches` on
every write-heavy item.

**Enforced at build time** — `validate_spec()` calls the SAME checker `/plan-execute` calls at
dispatch (`skills/plan-execute/scripts/parallel_contract.py`), so the two gates cannot drift.
On a spec that opts in (`"plan_schema_version": >= 3`) each of these is a **build error**:

| Rule | What is refused |
|---|---|
| §1 | a `parallel_group` present but not a non-empty string; a half-isolated group |
| R1 | members of one group with different `depends_on` sets (**every** version, ungated) |
| M1 | a member declaring `post_session.git` other than `none` — shipping is the group's act |
| M2 | a member item whose `touches` names a dependency manifest or lockfile |
| M2a | a member item with **no `touches`** — fails closed, because M2 and M5 are computed from it |
| M3 | `dispatch.isolation` with any value but `"worktree"`, or a mechanism this build can't honour |
| M5 | overlapping writes between members — refused on a shared tree, a warning under isolation |
| §3 | zero (isolated) or two integration sessions; one that misses a member or a member's gate |

Below the opt-in version the same problems are **printed as warnings** naming the rule, never
refused: `validate_spec()` is shared plumbing (`plan_mutate` re-validates the spec of every LIVE
plan on each add/amend/retire), and measured on 2026-08-12, enforcing unconditionally would have
stranded 5 of this repo's 10 existing plans on rules their manifests are grandfathered out of.

**Emitted into `manifest.json`** — `items[].touches` (the M2a/M2/M5 input; before this, 307
manifest items in this repo carried it 0 times, so both tree-protecting rules were keyed on a
field that was never written), plus `dispatch.isolation` and `dispatch.integrates_group`, each
only when declared.

**The integration session is auto-emitted** for every **worktree-isolated** group with no
author-written integrator (`synthesize_integration_sessions`, idempotent under `--rebuild`):
`depends_on` every member, `verify.gates` = the union of the members' gates, one
`post_session.git: "commit"`, `model: Opus` / `reasoning: high` / `task_class: agentic_build`, and
a prompt carrying the §3 merge protocol — baseline `HEAD` + dirty state first, containment check
(it is advisory, so it must be *checked*), confirm the base ref, merge producer-first, halt loudly
on conflict, re-run the gates on the merged tree, then ship. A **shared-tree** group is NOT
auto-emitted: §3 makes its integrator optional (a plan that never commits is legitimate) and the
missing one is already a warning; auto-committing on behalf of an author who chose not to ship
would be the worse default.

**The pure-chain warning** (opted-in specs only, same threshold) — `validate_spec()` computes each session's dependency layer, reports
the max layer width and the serialization ratio (sessions ÷ critical-path length; `1.0` = a pure
chain), and when every layer is one session wide it warns, **naming the file-disjoint session
pairs that could have shared a group**. `serial_reason` (top-level) silences it. Pair detection
reads `touches` as **paths**: a session whose `touches` is prose, or which owns an item with none,
is treated as conflicting with everything — a false conflict costs one missed suggestion, a false
disjointness costs two agents writing the same file. `python3 scripts/validate_spec.py <spec>`
prints the report on every run.

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
