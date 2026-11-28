# Plan Execute — Dispatch, Routing, Degradation & Telemetry

Deep dispatch rules for steps 2–4 of `SKILL.md`: `executor_policy`, egress scanning, effort enforcement, `compact-policy.py` safe-point holds, Codex wrapper dispatch, reactive degradation, GLM (`zai`) translation, Workflow dispatch, `record-receipt` telemetry, and schema v8 route-at-dispatch.

## Contents

- [Executor policy and data sensitivity egress guard](#executor-policy-and-data-sensitivity-egress-guard)
- [Effort enforcement (`effort_mechanism`)](#effort-enforcement-effort_mechanism)
- [Compact-policy safe-point holds](#compact-policy-safe-point-holds)
- [Codex-backed members (two-layer wrapper dispatch)](#codex-backed-members-two-layer-wrapper-dispatch)
- [Reactive model degradation](#reactive-model-degradation)
- [GLM tree (`active_provider: zai`)](#glm-tree-active_provider-zai)
- [Workflow dispatch and seam rule](#workflow-dispatch-and-seam-rule)
- [Checkpoint answer injection and Codex egress](#checkpoint-answer-injection-and-codex-egress)
- [Background wakes and `record-receipt` (TEL-01)](#background-wakes-and-record-receipt-tel-01)
- [Route at dispatch (plan schema v8)](#route-at-dispatch-plan-schema-v8)

---

## Executor policy and data sensitivity egress guard

`begin` enforces the SSOT's `executor_policy` fail-closed:
- Under `active_provider: openai`, only a session whose manifest `task_class` is opted into `executor_for` rides the dial to Codex (others dispatch Claude, with an `executor_policy_note`).
- `linchpin` / irreversible sessions never auto-dispatch to Codex (dial-driven → Claude; an explicit Codex pin on barred work → `BLOCKED`).
- A Codex-declared session whose route cannot be constructed makes `begin` exit 1 with the session `BLOCKED` and the plan halted (never silently falls back to Claude).

### Data sensitivity egress guard (`gitleaks` + `.env*`)

Before any Codex process exists, `begin` refuses any working tree that trips:
1. Its **`.env*` filename rule** — scanned over the **whole tree**, git-ignored files included (realpath + symlink-resolved).
2. Its **content scan** (`gitleaks dir`, same scanner as the commit hook) — scanned over the **reviewed surface** only: git-tracked plus untracked-not-ignored, minus `__pycache__`/`*.pyc` (non-git trees are scanned whole). Git-ignored build output is excluded by design (measured on a 16 GB repo: 6 min 5 s / 826 build-artifact findings full-tree vs ~3 s scoped).

Fail-closed rules:
- Missing `gitleaks` on `PATH` refuses and names the missing dependency (no fallback scanner).
- A scan whose scanned-byte count overshoots its selected surface fails closed.
- Two SSOT keys clear a hit, both requiring an `expiry`: `content_scan_allowlist` (one reviewed file) and `egress_opt_ins` (whole repo).
- Filename substrings (`corpus`/`creds`/`credential`/`secret`) were dropped 2026-07-28.

### Provider-symmetric verification stamp

Each member carries `executor_family`, `verifier_family`, and `verifier_mode`: the non-executing family verifies. `on_box_human` means the tree is restricted, so verification is human/on-box at the checkpoint (record `VERIFIED-ON-BOX` or `BLOCKED`; never start a Codex process to verify it). This same disposition backs `cross-family-review-low|medium|high` (`verify-gates.md` → "Cross-family review").

## Effort enforcement (`effort_mechanism`)

*(Corrected 2026-07-26.)* Prompt text is **not** an effort control (`ultrathink` only adds an in-context instruction; `think` / `think hard` / `think more` are ordinary text). `subagent_type` frontmatter is what binds effort. `begin` resolves each `(model, reasoning)` pair to a tier agent (`tier-<model>-<effort>`, e.g. `tier-opus-high`) and returns `effort_enforced` + `effort_mechanism`:

- **`tier_agent`** — genuinely bound by the resolved agent's `effort:` frontmatter. Announce as authoritative. Includes `tier-fable-xhigh` (ESC-01), `tier-opus-xhigh` (v1.20), and `tier-opus-low` (v25).
- **`agent_definition`** — the manifest named its own `subagent_type`; that agent's frontmatter governs. Announce the manifest's declared tier, not a claim about what bound it.
- **`prompt_directive_advisory`** — **nothing binds the effort.** No tier agent covers the pair (e.g. sonnet `xhigh` and every `max`). `begin` names `session-effort-worker` (no `effort:` key, SSOT v26) so the subagent inherits the orchestrator's session effort, and adds advisory prose to `prompt_text`. Announce honestly (*"S05 declares max; no tier agent, so it inherits the session effort"*).

Precedence (`CLAUDE_CODE_EFFORT_LEVEL` > frontmatter > session > model default) lives in `model-routing.yaml` § "WHAT ACTUALLY BINDS AN EFFORT LEVEL".

### Task `subagent_type` & `description` rules

- Pass `member.subagent_type` as returned (`tier-*`, `session-effort-worker`, or `"fork"`). Omit `subagent_type` only when `null` (missing agent file on disk → fresh `general-purpose`, which pins effort medium since SSOT v26).
- Pass `prompt` = `member.prompt_text` verbatim without adding or stripping thinking lines.
- Pass `description` = `member.dispatch_description` verbatim when non-null (opaque shadow-eligible marker, deliberately outside `prompt_text`; never copy into the prompt or reuse on retry), otherwise use a concise session label.
- Announce every dispatch **name-first**: `Dispatching '<title>' (<sid>) on <model> · reasoning: <reasoning>`.

## Compact-policy safe-point holds

From the **MAIN CONVERSATION** (never inside a dispatched worker or Workflow agent), bracket every batch:
- Before dispatch (step 3):
  ```bash
  [ -f ~/.claude/hooks/compact-policy.py ] && python3 ~/.claude/hooks/compact-policy.py safe-point --session "$CLAUDE_CODE_SESSION_ID" --state hold --plan-dir <dir> --phase batch-<n> || true
  ```
- After batch closeouts + verify + ship, before `release` (step 7):
  ```bash
  [ -f ~/.claude/hooks/compact-policy.py ] && python3 ~/.claude/hooks/compact-policy.py safe-point --session "$CLAUDE_CODE_SESSION_ID" --state ok --plan-dir <dir> --phase batch-<n> || true
  ```

**`--session "$CLAUDE_CODE_SESSION_ID"` is mandatory on both calls.** `safe-point` never guesses a session id and refuses whenever worker markers (`CLAUDE_CODE_CHILD_SESSION` / `CLAUDE_CODE_FORK_SUBAGENT`) are set — which also read `1` in the main conversation's Bash subprocess (measured and 2026-09-04). Without `--session`, the call prints `Nothing written.`, exits 0, and leaves the brake unarmed.

## Codex-backed members (two-layer wrapper dispatch)

For `backend: "codex"`, the Task tool only accepts Claude tokens, so `begin` returns a short wrapper `prompt_text` instructing a fresh Claude agent to run `codex_cmd` in the foreground (`codex exec --ignore-user-config --ignore-rules --sandbox workspace-write -c project_doc_max_bytes=262144 -m <codex_model> -c model_reasoning_effort=<codex_effort> -o <last-message file>`) and relay Codex's final message verbatim.

- Dispatch with `model` = `model_arg` (fixed Claude wrapper model), `prompt` = `prompt_text`, omit `subagent_type` when `null`. Announce with the Codex tier: `Dispatching '<title>' (<sid>) via Codex wrapper — <codex_model> · reasoning_effort: <codex_effort>`.
- **Degradation:** a wrapper reply of `CODEX-DISPATCH-FAILED: <signal> — …` (`entitlement` | `unavailable` | `cli_version_rejected` | `quota_exhausted`) triggers ONE re-dispatch using `fallback_prompt_text` (chain `sol → terra@max → NO-CODEX`, plus `luna → terra@max` rescue; nothing degrades onto `luna`) and records `degraded_from` in the closeout handed to `apply`. `quota_exhausted` or null `fallback_model`/`fallback_prompt_text` means **NO-CODEX**: surface and STOP — never re-run on Claude.
- `CODEX-NO-CLOSEOUT` or `CODEX-TIMEOUT` is **not** a dispatch failure — apply as-is (blocks for review).
- Never edit `-m`/`--sandbox` flags or strip the wrapper to run inline.
- **Verify-gate rework:** `begin` already appends live `_verify_state/<sid>.feedback.md` once to the prompt file `codex_cmd` reads (`--- BEGIN CURRENT VERIFICATION FEEDBACK ---`; archived `r<N>` feedback is never carried). Re-dispatch `codex_cmd` unchanged — never pipe feedback yourself or append it to the wrapper prompt.
- **Reasoning mapping:** manifest tiers map 1:1 to native Codex effort (5.6-only lane reaches `max`; `ultra` is never emitted).
- **Unroutable recovery:** fix manifest model or SSOT, run `--clear-halt`, re-run; next `begin` overwrites `BLOCKED`.

## Reactive model degradation

If a dispatched `Task` fails or is refused for **model access/entitlement**, immediately re-dispatch the same session on `fallback_model` at `fallback_reasoning` (`high` for both Opus and Sonnet — `xhigh` is never a degrade landing; Claude ladder: **Fable → Opus → Sonnet**, floor at Sonnet; never auto-drop judgement work to Haiku or emit Opus @ `max`).

- **PRECEDENCE — an ESCALATED member is not degraded.** When `member.on_dispatch_refusal` is present (ESC-02 raised its rung above authored), `fallback_model` is `null` by construction: run `PYBP record-refusal` and re-run `plan` instead of degrading.
- **Announce and record:** announce *"S02 requested Fable — unavailable; degraded to Opus · high"* and add `degraded_from: {"requested":"<token>","ran":"<fallback>","reasoning":"high"}` to the closeout passed to `apply`.
- A `null` `fallback_model` (Sonnet/Haiku/unrecognized) means no lower judgement-safe tier: surface the failure and STOP.
- **Observational note:** Claude Code may silently inherit the orchestrator's model on an entitlement refusal without returning an error; `PreToolUse(Agent)` (`~/.gearbox-state/routing/dispatch-audit.ndjson`) logs requested vs actual, and `settings.json`'s `fallbackModel` handles session-level availability failover. See `model-routing.yaml` § degradation.

## GLM tree (`active_provider: zai`)

When `begin` reports `active_provider: zai` (`~/.claude-glm` or `PLAN_EXECUTE_ROUTING_PROVIDER=zai`):
- `model_arg`/`reasoning` are already translated to the ZAI cell (`Sonnet`→`sonnet`@max-or-low, `Opus`→`opus`@max, `Haiku`/`Fable` collapsed onto `sonnet`/`opus`), and `subagent_type` names a zai tier agent (`tier-sonnet-low`, `tier-sonnet-max`, `tier-opus-max`).
- Degradation keeps `max` (`opus → sonnet@max`). Escalation has no apex and tops out at `opus@max`; null fallback on `sonnet` stops.
- Under the GLM env the tokens are `glm-5.3-flash` / `glm-5.4` (since routing v46, 2026-11-27; `glm-5.3` before) — announce with the wire name when known (e.g. *“Dispatching ‘Build govrun’ (s01) on glm-5.4 · reasoning: max”*).

## Workflow dispatch and seam rule

**Default for any `backend: "claude"` batch of ≥ 2 members** (codex members always use the plain Task wrapper path), when the Workflow tool is available and no member's prompt invokes skills mid-session.

- **Why default (th-02):** N Task calls dump every worker's full prose into orchestrator context and trigger mid-run compaction. A schema-forced Workflow return keeps worker prose out of context and eliminates missing/malformed closeouts at source.
- Run `parallel()` over members with one `agent()` each, using a schema mirroring `closeout-contract.md` (`session` pinned `const` to member id, `result` enum `[DONE, PARTIAL, BLOCKED]`).
- On return, wrap each member's validated JSON in `<plan-execute-closeout>...</plan-execute-closeout>`, write it as the temp file, and proceed to `apply` unchanged.
- If a member returns no validated result (agent error, budget exhaustion), write NO closeout file — it stays `DOING` for normal crash recovery (`--session sNN` via Task). Never synthesize a closeout.
- Reactive degradation applies identically inside Workflow (`fallback_model` @ `fallback_reasoning`, setting `degraded_from`).
- On Workflow unavailability or hard error, fall back to parallel `Task` calls in a single assistant message.
- **Workflow seam rule:** human gates (`checkpoint`, `--resume`, shipping confirms) and all `run.py` calls (`begin`/`apply`/`release`/`ship-*`) stay in the main conversation. The H8 sandbox binds workflow agents identically.

## Checkpoint answer injection and Codex egress

`begin` prepends the owner's answer recorded by `plan --resume --answer-file` to the member's dispatched prompt (`prompt_text` for Claude; stdin prompt file for Codex) via `findings_digest.digest(plan_dir, session_id)`. On the Codex lane, `codex_command._refuse_restricted_text` runs `gitleaks` over that injected text and rework feedback (fail-closed without `gitleaks`; cleared by `egress_opt_ins`). The cross-session findings digest was removed 2026-10-13 after re-measurement showed no drop in repeated findings.

## Background wakes and `record-receipt` (TEL-01)

- **Background wake behaviour (measured):** a single Agent dispatch wakes after a long background command, so its first message may carry no closeout. A Workflow member returns without waking and its background command is killed — tell Workflow agents to wait inside their turn with `Monitor` and an `until`-loop (`until <check>; do echo waiting; sleep 30; done`), never `sleep N` followed by a check.
- A notification saying the agent "has not reported yet" or "stopped with background work of its own still running" is NOT a return: wait for the final message.
- Write the full final message (or tail from the last unfenced `<plan-execute-closeout>`, or last ~2,000 chars if missing) to `/tmp/closeout-sNN.txt`.
- **`record-receipt` (AFTER Task returns, BEFORE `apply`):**
  ```bash
  PYBP record-receipt <dir> --session sNN --agent-id <id> [--transcript <path>] [--backend claude|codex]
  ```
  - Claude Agent-tool: pass returned `agentId` and `~/.claude/projects/<project>/<parent-session-id>/subagents/agent-<agentId>.jsonl`.
  - Workflow member: `.../subagents/workflows/<wf_id>/agent-<agentId>.jsonl` (ids in `journal.jsonl`).
  - `claude -p --output-format json`: pass that result JSON (attests `modelUsage`).
  - Codex: pass `--backend codex` and `--transcript <last_message_file>.events.jsonl` when `codex exec --json` wrote one; otherwise omit `--transcript` (usage lands `unavailable`, `model_ran_source: "requested"`).
  - Best-effort and non-blocking; if skipped on Claude, `scripts/transcript_find.py` searches `~/.claude/projects` and attests only on a unique matching transcript.
- **`missing_dispatch_receipt`:** cannot occur on the Claude harness (it is a Codex-orchestrator check under `--harness codex`, contract § 3.7).

## Route at dispatch (plan schema v8)

Authority: `route-at-dispatch-contract.md` (wins on any disagreement). Plans below v8 dispatch as before.

- **Who picks the model:** `begin` resolves an unpinned v8 session from `model-routing.yaml` for the active provider, freezes that class default for the escalation generation, and prints `effort_mechanism` (`agent_definition`, `tier_agent`, `prompt_directive_advisory`, or `codex_cli`).
- **Overrides:** an authored `model`/`reasoning` pair on v8 must set both or neither with `why_model` (`routing_provenance: pinned_override`). A below-floor override is refused for `peer_triggers` or `task_class: linchpin`.
- **Locked checks:** `verify.locked` paths are hashed at `begin`; `verify-begin` re-hashes them plus the protected test-config set (pytest config, `conftest.py`, `sitecustomize`, `*.pth`, etc.) before any gate. Any change fails as `LOCKED_CHECK_EDITED`.
- **Usage & cost:** recorded from the transcript passed to `record-receipt` (`null`, never `0`, when absent). `claude -p` records reported cost; Agent/Workflow dispatches compute real cost (`cost_source: model_prices`) using `cache_write_5m` / `cache_write_1h` rates (`model-routing.yaml` v31+); Codex attempts record token counts from `--json` with `cost_usd: null`.
- **Rollback switch:** `begin --no-route-at-dispatch` or `PLAN_EXECUTE_ROUTE_AT_DISPATCH=0` (refuses unpinned v8 sessions rather than inheriting your model).
- **GLM tree guard:** never run a v8 plan under `~/.claude-glm` until that copy has `SUPPORTED_MAX_SCHEMA >= 8` (`gearbox deploy` does not update it).
