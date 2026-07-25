<!--
model-routing.digest.md — SOURCE TEMPLATE for the CLAUDE.md routing digest.

Ported from the source deployment's ~/.claude/model-routing.digest.md (S05, this
port), made PROVIDER-AWARE and re-templated for Gearbox's actual task_classes
(mechanical / standard_build / agentic_build / deep_reasoning / linchpin — see
model-routing.yaml). render-routing-digest.py reads this file, substitutes each
`{{resolve:<task_class>}}` placeholder with that class's tier resolved to
active_provider's real model id + native effort level, substitutes `{{version}}` /
`{{active_provider}}`, and installs the result into CLAUDE.md between the bare
`<!-- BEGIN ROUTING -->` / `<!-- END ROUTING -->` markers.

MARKER CHANGE FROM SOURCE: the marker no longer embeds the config version (the
source's `(model-routing.yaml v4)` suffix required a marker-text rewrite on every
policy bump). The marker is now version-agnostic; the version renders as plain text
in the block body instead.

TWO variants:
  - VARIANT v0 (minimal): task-class table + escalate rule only. No provider-name
    text, no resolved models table extras — safe to publish before a repo has
    finished wiring its own agents/consumers.
  - VARIANT full (advisory): the complete digest, including the resolved-provider
    line and the routing-receipt convention.

Budget: full variant <=40 lines / <=1800 bytes (the renderer enforces the cap).
-->

<!-- ===== VARIANT: v0 (minimal) ===== -->
<!-- BEGIN ROUTING -->
## Task routing — classify before you start

Pick a task class, then set the tier. Authority: `claude/model-routing.yaml`
(v{{version}}, active_provider={{active_provider}}).

| Class | Cues | Resolved tier |
|---|---|---|
| mechanical | rename/format sweep · codemod · doc edit | {{resolve:mechanical}} |
| standard_build | CRUD · wiring · templated feature · test scaffold | {{resolve:standard_build}} |
| agentic_build | multi-file · integration · non-obvious debug | {{resolve:agentic_build}} |
| deep_reasoning | architecture · security · ambiguous · hard root-cause | {{resolve:deep_reasoning}} |

- **Escalate on evidence:** after 2 failed attempts at the same root cause, raise effort one rung, then consult a single frontier advisor, then tier one rung, then pull a second-model peer.
<!-- END ROUTING -->
<!-- ===== END VARIANT v0 ===== -->

<!-- ===== VARIANT: full (advisory) ===== -->
<!-- BEGIN ROUTING -->
## Task routing — classify before you start

Classify the task, set the tier. Authority: `claude/model-routing.yaml`
(v{{version}}, active_provider={{active_provider}}).

| Class | Cues | Resolved tier |
|---|---|---|
| mechanical | rename/format · codemod · doc edit | {{resolve:mechanical}} |
| standard_build | CRUD · wiring · templated feature · scaffold | {{resolve:standard_build}} |
| agentic_build | multi-file · integration · non-obvious debug | {{resolve:agentic_build}} |
| deep_reasoning | architecture · security · ambiguous · hard root-cause | {{resolve:deep_reasoning}} |
| linchpin | one-shot irreversible · plan-foundational call | {{resolve:linchpin}} |

- **Effort = default + escalation, not a ceiling** — never a floor on judgement work.
- **Fan-out pins an explicit tier** — never the frontier tier across N agents (see `fanout_policy`).
- **Escalate on evidence:** after 2 failures at one root cause, raise effort → advisor → tier → second-model peer.
- **Main session advisory:** `main_session.advisory_default_tier`/`_effort` — recommend `/model`/`/effort` on mismatch; cannot self-switch.
- **Routing receipt:** after delegate/escalate, log a one-liner (class → resolved tier) to `claude/evals/routing/MISROUTES.md` on any mismatch.
<!-- END ROUTING -->
<!-- ===== END VARIANT full ===== -->
