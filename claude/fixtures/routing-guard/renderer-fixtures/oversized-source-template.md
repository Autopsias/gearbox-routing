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
padding line 0 xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
padding line 1 xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
padding line 2 xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
padding line 3 xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
padding line 4 xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
padding line 5 xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
padding line 6 xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
padding line 7 xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
padding line 8 xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
padding line 9 xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
padding line 10 xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
padding line 11 xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
padding line 12 xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
padding line 13 xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
padding line 14 xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
padding line 15 xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
padding line 16 xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
padding line 17 xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
padding line 18 xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
padding line 19 xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
padding line 20 xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
padding line 21 xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
padding line 22 xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
padding line 23 xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
padding line 24 xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
padding line 25 xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
padding line 26 xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
padding line 27 xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
padding line 28 xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
padding line 29 xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
padding line 30 xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
padding line 31 xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
padding line 32 xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
padding line 33 xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
padding line 34 xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
padding line 35 xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
padding line 36 xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
padding line 37 xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
padding line 38 xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
padding line 39 xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
padding line 40 xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
padding line 41 xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
padding line 42 xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
padding line 43 xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
padding line 44 xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
padding line 45 xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
padding line 46 xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
padding line 47 xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
padding line 48 xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
padding line 49 xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
padding line 50 xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
padding line 51 xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
padding line 52 xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
padding line 53 xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
padding line 54 xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
padding line 55 xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
padding line 56 xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
padding line 57 xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
padding line 58 xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
padding line 59 xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
padding line 60 xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
padding line 61 xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
padding line 62 xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
padding line 63 xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
padding line 64 xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
padding line 65 xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
padding line 66 xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
padding line 67 xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
padding line 68 xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
padding line 69 xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
padding line 70 xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
padding line 71 xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
padding line 72 xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
padding line 73 xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
padding line 74 xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
padding line 75 xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
padding line 76 xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
padding line 77 xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
padding line 78 xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
padding line 79 xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

## Task routing — classify before you start

Pick a task class, then set the tier. Authority: `claude/model-routing.yaml`
(v{{version}}, active_provider={{active_provider}}).

| Class | Cues | Resolved tier |
|---|---|---|
| mechanical | rename/format sweep · codemod · doc edit | {{resolve:mechanical}} |
| standard_build | CRUD · wiring · templated feature · test scaffold | {{resolve:standard_build}} |
| agentic_build | multi-file · integration · non-obvious debug | {{resolve:agentic_build}} |
| deep_reasoning | architecture · security · ambiguous · hard root-cause | {{resolve:deep_reasoning}} |

- **Escalate on evidence:** after 2 failed attempts at the same root cause, raise effort one rung, then tier one rung, then pull a second-model peer.
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
- **Escalate on evidence:** after 2 failures at one root cause, raise effort → tier → second-model peer.
- **Main session advisory:** `main_session.advisory_default_tier`/`_effort` — recommend `/model`/`/effort` on mismatch; cannot self-switch.
- **Routing receipt:** after delegate/escalate, log a one-liner (class → resolved tier) to `claude/evals/routing/MISROUTES.md` on any mismatch.
<!-- END ROUTING -->
<!-- ===== END VARIANT full ===== -->
