# Dual-harness contract — one plan directory, two orchestrators

**Status:** ACCEPTED (2026-07-28, plan `dual-harness-plan-skills-2026-07-28` session S01, item CP-01).
**Scope:** how a single `_plans/<slug>-<date>/` directory (PLAN.html + manifest.json +
`sessions/*`) is built and run from **either** Claude Code **or** Codex CLI.
**Binding on:** CP-02 (`run.py --harness codex`), CP-03/04/05 (the three Codex skill
ports), PI-* (deploy/SSOT), and the lint sessions. Every later session implements
against this file; contradict it only by amending it in the same commit.

**Rule of this document:** every capability claim below cites a probe that was
actually run, with its verbatim output. Nothing here is asserted from memory. The
probe log is § 1; each decision cites probe ids from it. Re-run the whole log before
amending any decision — the CLI moves (this round found behaviour that changed
between `codex-cli` 0.144.1, which `run.py` was hardened against, and 0.145.0).

---

## 1. Probe log

Environment: macOS 25.5.0, `codex-cli 0.145.0` (`~/.npm-global/bin/codex`),
`your-private-harness` @ `ef52264`, SSOT `model-routing.yaml` `active_provider: anthropic`.
Probes P1–P4 are real model calls on `gpt-5.4-mini @ low` (deliberately the cheapest
rung — every one of them tests CLI plumbing, not model quality).

### P0 — `codex exec` flag surface

```
$ codex exec --help
```

Relevant to this contract: `-m/--model`, `-c/--config <key=value>`,
`-s/--sandbox <read-only|workspace-write|danger-full-access>`, `-C/--cd <DIR>`,
`--ignore-user-config`, `--ignore-rules`, `-o/--output-last-message <FILE>`,
`--json`, `--output-schema <FILE>`, `--ephemeral`, and the `resume` / `review`
subcommands. **There is no skills flag and no approvals flag on `exec`** —
`-a/--ask-for-approval` exists only on the interactive `codex` entrypoint
(`codex --help`, values `untrusted | on-request | never`).

### P0b — model catalog and native effort rungs

```
$ codex debug models   # → gpt-5.6-sol entry
"slug": "gpt-5.6-sol", "default_reasoning_level": "low",
"supported_reasoning_levels": [low, medium, high, xhigh, max, ultra]
```

Confirms `max` is a real rung on `gpt-5.6-sol` (not a value the CLI silently clamps),
and that `ultra` exists — which global policy forbids by default.

### P0c — skill discovery, roots and precedence

```
$ codex debug prompt-input "hi"     # renders the model-visible prompt as JSON
<skills_instructions>
### Skill roots
- `r0` = `~/.codex/skills`
- `r1` = `~/.agents/skills`
- `r2` = `~/.codex/skills/.system`
- `r3..r12` = plugin caches
### Available skills
- adversarial-review: … (file: r0/adversarial-review/SKILL.md)
- plan-builder: …      (file: r1/plan-builder/SKILL.md)
- plan-execute: …      (file: r1/plan-execute/SKILL.md)
- plan-harden: …       (file: r1/plan-harden/SKILL.md)
```

Codex discovers skills as `<root>/<name>/SKILL.md` with `name:` + `description:`
frontmatter (optional `metadata.short-description`; **no** `allowed-tools`, **no**
`effort` — those are Claude-only keys). Two roots matter: `~/.codex/skills` (r0) and
`~/.agents/skills` (r1). `codex features list` shows `skill_search  stable  true`.

**`~/.agents/skills` already holds stale auto-imported copies of all three plan
skills** (`~/.agents/.skill-lock.json`, installed 2026-06/07). Their `SKILL.md`
carries a bolted-on preamble — *"`Task` means Codex subagent/multi-agent dispatch
when available and appropriate"* — i.e. exactly the hand-wave this contract replaces.
`~/.agents/skills-disabled/` already exists as the established way to retire one.

### P1 — the dispatch command works on 0.145.0, and skills survive `--ignore-user-config`

```
$ codex exec --ignore-user-config --ignore-rules --sandbox workspace-write \
      -m gpt-5.4-mini -c model_reasoning_effort=low -o p1.last.txt - < p1.txt
EXIT=0   (18.9s)
$ cat p1.last.txt
YES          # a skills list is available
YES          # `adversarial-review` is in it
165          # skills listed
PROBE-1-OK
```

`run.py`'s `_codex_cmd()` shape is intact on 0.145.0, `-o` writes the last agent
message, and **`--ignore-user-config` does not disable skill discovery** — it only
skips `config.toml`. A dispatched session may therefore invoke skills.

### P2 — nested `codex exec` FAILS inside a sandboxed Codex shell ⚠️

Parent `codex exec … --sandbox workspace-write`, asked to run a child
`codex exec … --sandbox workspace-write`:

```
Exit code: 1
/tmp/probe2-inner.txt: MISSING
WARNING: proceeding, even though we could not create PATH aliases: Operation not permitted (os error 1)
Error: failed to initialize in-process app-server client: Operation not permitted (os error 1)
PROBE-2-DONE
```

### P3 — nested `codex exec` WORKS when the parent shell is not sandboxed ✅

Identical child command, parent `--sandbox danger-full-access`:

```
Exit code: 0
/tmp/probe3-inner.txt: INNER-OK
PROBE-3-DONE
```

P2/P3 together are the single most consequential finding in this document: **the
Codex-side orchestrator's dispatch commands must run outside the Seatbelt sandbox.**

### P4 — the only Codex env marker is inverted

```
$ codex sandbox -- /bin/sh -c 'env | grep -iE "^(CODEX|OPENAI|AGENT)"'
CODEX_SANDBOX=seatbelt
CODEX_SANDBOX_NETWORK_DISABLED=1

# same check from inside a NON-sandboxed Codex shell (--sandbox danger-full-access):
CODEX_SANDBOX=UNSET
PROBE-4-DONE
```

### P5 — the SSOT translation `run.py` already performs

> **SUPERSEDED IN PART, 2026-08-13 (SSOT v16, 5.6-ONLY).** The transcript below is a
> real measurement and is left verbatim, but `providers.openai` no longer has a
> `workhorse` tier: gpt-5.5 is retired and the lane is luna / terra / sol. Two
> consequences: the `sonnet @ …` row below now resolves to **nothing** — tier-name
> translation returns `None` and the session halts as unroutable — and the
> `gpt-5.5` cells are dead. That is precisely why dial-driven translation must resolve
> from `(task_class, provider)` via `resolve_route.resolve`, never by tier-name lookup
> across two non-parallel line-ups. The code change lands with the escalation work; this
> banner is the honest interim state, not a fixed one.


```
$ PLAN_EXECUTE_ROUTING_SSOT=./model-routing.yaml python3 -c "…_resolve_codex_dispatch…"
anthropic.models: {cheap_fast: haiku, workhorse: sonnet, frontier_reasoner: opus,  apex_reasoner: fable}
openai.models   : {cheap_fast: gpt-5.6-luna, workhorse: gpt-5.5, frontier_reasoner: gpt-5.6-terra, apex_reasoner: gpt-5.6-sol}

opus   @ low/medium/high/xhigh/max/unset -> ('gpt-5.6-terra', 'max')     # every tier, same cell
haiku  @ low/medium/high/xhigh/max/unset -> ('gpt-5.6-luna',  'max')     # every tier, same cell
sonnet @ low -> ('gpt-5.5','medium')  @ medium -> ('gpt-5.5','high')  @ high/xhigh/max -> ('gpt-5.5','xhigh')
fable  @ low -> ('gpt-5.6-sol','medium') @ medium -> ('gpt-5.6-sol','high') @ high/xhigh/max -> ('gpt-5.6-sol','xhigh')
```

### P6 — the two gates that close the Codex lane today

```
$ _resolve_codex_dispatch("opus", "high", ssot, require_calibrated=True)
RAISED UnroutableCodexSession : active_provider routes to providers.openai whose
calibration.status is 'lane_scoped' (not 'researched') — a lane_scoped/uncalibrated
profile may not serve as the execution default …

$ _executor_for(ssot)
[]                                    # NO task_class is opted into dial-driven Codex

$ _session_barred(...)
{'task_class': 'linchpin'}                    -> 'task_class is linchpin'
{'peer_triggers': ['irreversible_change']}    -> 'peer_triggers declares irreversible_change'
{'dispatch': {'guards_irreversible': True}}   -> 'dispatch.guards_irreversible is set'
{'task_class': 'agentic_build'}               -> None
```

### P7 — the loop primitives are already harness-neutral

Run against a scratch **copy** of this plan directory, with no Claude Code tool in play:

```
$ python3 skills/plan-execute/scripts/run.py plan <clone>/dual-harness-…
{"action": "blocked", "message": "no ready sessions but work remains …", "plan_url": "file://…"}

$ python3 … run.py begin <clone>/dual-harness-… --sessions s02
{"action":"dispatch","active_provider":"anthropic","batch":[{"id":"s02",
  "subagent_type":"tier-opus-high","prompt_file":"…/s02.prompt.md","prompt_text":"<6608 chars>",
  "items":["cp-02","cl-03"],"model":"Opus","model_arg":"opus","backend":"claude",
  "reasoning":"high","effort_enforced":true,"effort_mechanism":"tier_agent",
  "fallback_model":"sonnet","fallback_reasoning":"high",
  "executor_family":"anthropic","verifier_family":"openai","verifier_mode":"cross_family"}]}
```

`plan` / `begin` / `apply` / `checkpoint` / `clear-halt` / `release` are pure Python +
filesystem. Nothing in them touches a Claude Code tool. **The plan directory and its
state machine are already the shared substrate; only the dispatch step is
harness-specific.** That is the whole basis of this contract.

### P8 — `~/.codex` is not, and can never be, a `gearbox deploy` target of the usual kind

`scripts/gearbox do_deploy()` is a **git fast-forward of a clone** (`git fetch` →
`merge --ff-only` → `install-hooks.sh` → `verify-routing.sh`). `~/.codex/` holds
`auth.json`, `sessions/`, four sqlite DBs, `plugins/cache/`, `history.jsonl`. It is
not a clone and must never become one. Separately: `git ls-files | grep -i codex` in
both `your-private-harness` and `~/.claude` returns **only** eval artefacts — the existing
`~/.codex/skills/adversarial-review/` port is **untracked and hand-maintained**, with
content already divergent from `commands/adversarial-review.md`. It is a precedent for
the *shape* (`SKILL.md` + `references/` + `agents/openai.yaml`), and a counter-example
for *sourcing*.

---

## 2. Decision D1 — harness detection is an explicit `--harness` flag, never env sniffing

**Decision.** `run.py` gains `--harness {claude,codex}`, default `claude`, accepted on
`plan`, `begin`, `status` and `checkpoint`. There is no environment sniffing, no
auto-detection, and **no `harness` field in `manifest.json` or `PLAN.html`**.

**Why — the evidence forces it, it is not a style preference.**

1. *The only Codex-provided marker is inverted.* P4: `CODEX_SANDBOX=seatbelt` is set
   when the Codex shell **is** sandboxed, and unset when it is not. P2/P3: the
   dispatch loop **only works** when the shell is **not** sandboxed. So the one
   variable available is present exactly in the mode where the Codex harness cannot
   function, and absent exactly in the mode where it can. Sniffing it would be worse
   than useless — it would be backwards.
2. *A flag carries consent; an environment variable cannot.* D4 hangs real policy on
   "the operator deliberately elected Codex for this run". `--harness codex` is that
   election, typed by a human into a command. An inherited env var is an accident of
   process lineage and must never be allowed to waive a safety gate.
3. *The plan directory must stay portable.* Recording the harness in the manifest
   would make it sticky — a plan built in Codex would then want to run in Codex
   forever. The whole point is that the same directory runs from either side. Harness
   is a property of the **run**, not of the **plan**.

**Consequences.**

- `--harness` is recorded as an audit fact, not plan state: `begin` adds
  `harness: "codex"` to the existing `dispatch_started` event in `run.ndjson`, and
  every `begin` JSON payload carries a top-level `"harness"` key. Nothing in
  `PLAN.html` changes.
- **One env check survives, as a negative guard only.** `begin --harness codex`
  refuses when `CODEX_SANDBOX` is set, with the message naming the launch recipe
  (§ D2). Rationale: that condition means the loop is running inside a Seatbelt
  shell, where P2 proves dispatch cannot work. This is a fail-closed guard against a
  proven-broken configuration, not harness detection — do not repurpose it.
- The Claude path must stay **byte-identical** when `--harness` is absent or
  `claude`. CP-02's test suite asserts that explicitly.

**Rejected.** *Sniff `CODEX_SANDBOX` / `CODEX_HOME` / absence of `CLAUDE_*`* — refuted
by P4. *A `harness` field in the manifest* — breaks portability, the plan's stated
goal. *A separate `run_codex.py`* — two copies of a state machine whose closeout,
lock, halt and WAL semantics are the hard part; they would drift within a month.

---

## 3. Decision D2 — the Codex-side dispatch loop: the orchestrator runs the commands itself

**Decision.** Inside Codex there is no Task tool and no wrapper agent. The
orchestrator **is** an interactive `codex` session running the ported `plan-execute`
SKILL.md; it calls the same `run.py` subcommands, runs each session's `codex exec`
command in its own shell, and feeds the resulting last-message file straight back
through `run.py apply`.

### 3.1 Launch recipe (load-bearing — P2/P3)

The orchestrator session **must** be started so its shell commands run unsandboxed:

```
codex --sandbox danger-full-access -C <repo-root>        # PROVEN by P3
```

Do not start it with `--sandbox workspace-write`: P2 shows every dispatch will die
with `failed to initialize in-process app-server client: Operation not permitted`.

*Unproven alternative, offered honestly:* `codex -s workspace-write -a on-request`
plus per-command human approval **may** escalate the approved command out of the
sandbox and work. Approval flows cannot be driven from a non-interactive probe, so
this is **UNVERIFIED**. CP-03 must probe it live before the ported SKILL.md
recommends it; until then the SKILL.md documents only the P3-proven recipe and names
its blast radius plainly (`danger-full-access` disables the sandbox for *every*
command that session runs, not just dispatches).

### 3.2 The loop

| Step | Command | Notes |
|---|---|---|
| 1 | `python3 <SKILLS>/plan-execute/scripts/run.py plan <dir> [--resume] [--session sNN] --harness codex` | Identical JSON actions: `dispatch` / `checkpoint` / `blocked` / `complete` / `halted`. Under `--harness codex` the first payload also carries `egress` (§ D4). |
| 2 | `run.py begin <dir> --harness codex --sessions s01 …` | Every member returns `backend: "codex"` with `dispatch_cmd`, `last_message_file`, `codex_model`, `codex_effort`, `translation` (§ D3), `fallback_cmd`. **No `wrapper_prompt`** — there is no wrapper agent to prompt. |
| 3 | Orchestrator runs each `dispatch_cmd` in the foreground, capturing stdout+stderr to `<plan-dir>/_codex/<sid>.<stamp>.stderr.txt`. | Serial by default (§ 3.4). |
| 4 | `run.py apply <dir> --session sNN --output-file <last_message_file>` | **The `-o` file IS the closeout file.** No relay, no copy, no re-typing. |
| 5–7 | `verify-*`, `ship-*`, `release` | Unchanged. Every one is pure Python (P7). |

**Step 4 is a genuine simplification over the Claude side, and the reason the port is
worth doing at all.** On Claude, a Codex session needs a wrapper agent to relay the
last message through the orchestrator's context, with four failure sentinels
(`CODEX-DISPATCH-FAILED`, `CODEX-TIMEOUT`, `CODEX-NO-CLOSEOUT`) and a standing rule
against synthesising a closeout. Inside Codex the orchestrator holds the file path
directly. The wrapper, its prompt, and three of its four sentinels disappear.

### 3.3 Closeout ingestion and failure classification

The orchestrator classifies from `(exit_code, last_message_file)` — never from the
model's prose:

| Observation | Classification | Action |
|---|---|---|
| exit 0, file contains `<plan-execute-closeout>` | normal | `apply` it |
| exit ≠ 0, file missing/empty | dispatch failure | grep captured stderr for `entitlement \| unavailable \| cli_version_rejected \| quota_exhausted`; re-dispatch **once** on `fallback_cmd`; set `degraded_from` in the closeout handed to `apply`. Null `fallback_cmd`, or `quota_exhausted` ⇒ **NO-CODEX**: surface and STOP. Never re-run the session on a Claude model to "rescue" it. |
| exit ≠ 0, file **contains** a closeout | completed, non-zero tail | `apply` it — the session finished and a later shell step failed |
| exit 0, file present, no closeout block | missing closeout | `apply` it **as-is**. `apply` blocks the session and halts — that path already exists and is correct. |
| timeout / killed | same as missing closeout | `apply` whatever the file holds |

**Never write, repair, or synthesise a closeout.** This rule is inherited verbatim
from the Claude wrapper prompt and is the reason the two harnesses cannot disagree
about what a session claimed.

### 3.4 Batching

Serial by default. A Claude batch fans out N Task calls in one turn; the Codex
equivalent is `cmd1 & cmd2 & wait`, which is expressible but costs the orchestrator
its per-dispatch visibility and interleaves two full agents' output into one shell
buffer. Plan graphs are rarely wide, and `run.py begin` already accepts a multi-session
batch and returns all specs at once — so parallelism is a shell detail the SKILL.md
may adopt later without touching this contract. **Start serial; revisit only with a
plan whose batches are actually wide.**

### 3.5 Checkpoints, halts, `--resume`

Unchanged commands (`checkpoint`, `clear-halt`, `--resume`), unchanged semantics.

In Codex, honouring a human checkpoint means: the orchestrator prints the decision
brief and **ends its turn**. The human answers in the TUI; the next turn runs
`--resume`. No tool, capability or feature flag is required for this — *stopping* is
the mechanism.

**This is why the session's abort condition ("no way to honor a human checkpoint")
does not fire.** Even in the degenerate case — someone drives the loop from
non-interactive `codex exec`, where no human can answer — `plan` returns
`action: "checkpoint"`, nothing is dispatched, and the run halts with the session at
`AWAITS_REVIEW` for a human to find later. A checkpoint that cannot be answered
becomes a stop, never a silent pass. The gate is honoured by construction.

Keep the operator gotcha from `SKILL.md` § "Status semantics": `--resume` correctly
dispatches a **pre-dispatch** `AWAITS_REVIEW` (a `TODO` session gated before it runs —
which is precisely the mechanism D4 uses), but it **re-dispatches** a session parked at
`AWAITS_REVIEW` *after* closing `DONE`. That asymmetry is identical in both harnesses.

### 3.6 Crash recovery — and the one change the WAL needs

Everything reusable is reused: the `.lock` pidfile, `_closeouts/<sid>.json`
write-ahead, `run.ndjson`, `_verify_state/`, `_shipping_state/`. All are filesystem
state, all are harness-neutral (P7).

**One real gap.** `_codex_cmd()` today writes the last-message file to
`/tmp/codex-<sid>-<pid>-<epoch>.last-message.txt`. On the Claude side that is fine —
the wrapper relays the content into the orchestrator's transcript, so it survives.
Inside Codex the orchestrator only ever holds the *path*, in its context. If it dies
between `codex exec` returning and `apply` running, the file still exists but nothing
remembers where — a completed session's work is unreachable, and the session sits in
`DOING` with no closeout, indistinguishable from a crash before dispatch.

**Fix, under `--harness codex` only:**

1. Write the last-message file to `<plan-dir>/_codex/<sid>.<stamp>.last-message.txt`
   (keeping the per-attempt stamp — it exists to stop a stale or foreign closeout from
   a prior run being relayed as this run's result, and that hazard is unchanged).
2. `begin` logs a `codex_dispatch` event to `run.ndjson` carrying `session_id`,
   `last_message_file`, `fallback_last_message_file`, `codex_model`, `codex_effort`
   and `dispatch_cmd`.

Recovery then reads `run.ndjson` for the most recent `codex_dispatch` of a `DOING`
session, checks the file, and either applies it or re-dispatches. No new state file,
no new format — two existing mechanisms, one deterministic path. The `rm -f <file> &&`
prefix already in `_codex_cmd()` keeps a stale file from a previous attempt out of the
way, unchanged: it removes only the one path the current attempt is about to write, so
every earlier attempt's file survives for audit.

### 3.7 The dispatch receipt — `apply` refuses a closeout with no dispatch

**The hole.** Under this harness the orchestrator is an interactive Codex model told,
*in prose*, to run each `dispatch_cmd`. Nothing structural makes it. A model that does
the work inline instead emits a perfectly valid closeout, `apply` takes it, statuses go
`DONE` — and the plan's per-session model selection has silently evaporated: every
session ran on the orchestrator's own model, at whatever effort it was started with.
Nothing on disk distinguishes that run from an honest one, and nothing caught it.

**The check.** `run.py::_missing_dispatch_receipt()` — when a session has a
`codex_dispatch` event, at least one of that event's `-o` paths
(`last_message_file` / `fallback_last_message_file`) must **exist**, or `apply`
refuses: session `BLOCKED`, plan halted, `"failure": "missing_dispatch_receipt"`,
exit 1. Identical treatment to a malformed closeout, and it runs before the closeout
is even read — its contents are not the question.

Design notes, each of which is a way this could have gone wrong:

- **Existence, not contents.** An empty `-o` file means `codex exec` ran and said
  nothing. That is the § 3.3 "missing closeout" row, which already blocks with a
  better-fitting reason. Requiring non-empty would have relabelled it.
- **Most recent event only.** After a re-dispatch, attempt 1's surviving file must not
  vouch for an attempt 2 that never ran.
- **The fallback path counts.** A session degraded onto `fallback_cmd` writes to the
  `-fb` file and the primary path never appears; that is a real dispatch.
- **No replay bypass, and none needed.** `_codex/` is inside the plan directory and
  `rm -f` only clears the path being rewritten, so re-running `apply` — including the
  operator's real recovery of passing the `_codex` last-message file to
  `--output-file` after a loop interruption — finds the same receipt. A
  `_closeouts/<sid>.json`-exists bypass was considered and rejected: it would wave
  through a *re-dispatched* session whose second attempt never ran.
- **Claude harness untouched.** It logs no `codex_dispatch` event, so the check never
  fires — including the two-layer Claude wrapper around a Codex model, whose `-o` file
  is a `/tmp` path relayed through the wrapper's transcript rather than handed to
  `apply` as a file.

**What it proves, stated honestly: that a dispatch happened. Not that the closeout came
out of it.** The orchestrator holds a shell; it could `touch` the path, or write a
closeout into a real `-o` file. The check converts a *silent omission* — the failure
actually observed — into a loud refusal, and leaves deliberate forgery undefended.
Under this harness the orchestrator is inside the trust boundary; the receipt is a
guardrail against drift, not an authentication.

Proved both ways in `test_codex_dispatch.py`: a closeout with its receipt applies (and
re-applies); the same closeout with the receipt absent is refused and the reason names
the file it wanted; a fallback-only receipt is accepted; a stale receipt from an
earlier attempt is not; a Claude-harness closeout is unaffected; and
`test_dispatch_receipt_check_can_fail` swaps the check for a pass-through to prove the
refusal is not vacuous.

*(`_codex/` sits inside the plan directory, which is versioned `live-state` per
`scripts/deploy.pathspec`. Its contents duplicate `_closeouts/` in raw form; that is
acceptable and useful for audit. It does not weaken the H8 sandbox rule — the file is
written by the **CLI** via `-o`, not by the dispatched agent, and the dispatched agent
is still told not to touch plan files.)*

---

## 4. Decision D3 — Claude-pinned sessions translate through the SSOT, with a receipt that names what was lost

**Decision.** Under `--harness codex`, a session pinned to a Claude model runs on the
OpenAI-family equivalent resolved from `model-routing.yaml`, via the **existing**
`run.py::_resolve_codex_dispatch()`. Every translation prints a receipt. No session is
refused merely for naming a Claude model.

### 4.1 Recon result: no new helper is needed, and none should be added

`scripts/resolve_route.py` exposes `_Profile(ssot_text, provider)` with `.models`,
`.tier_of(model_id)` and `.native_effort(tier, intent)`, plus module-level
`resolve/escalate/degrade` (which are *task_class*-driven, not model-token-driven, so
they do not fit). There is no public `translate(anthropic_model, effort) → (openai_model,
effort)` function.

**There should not be one.** `run.py::_resolve_codex_dispatch()` already composes
exactly that translation and is already the enforcement point for the calibration
gate; P5 and P6 are its measured output. CP-02's whole job is to route the
`--harness codex` path through it with `declared_codex=False`. Adding a parallel
helper in `resolve_route.py` would create a second translation surface to keep in
lockstep with the SSOT — the exact drift class `verify-routing.sh` check (d) exists to
police. **Reuse; do not add.**

### 4.2 The mapping — and a correction to this plan's own premise

The plan brief says *"Opus/high → sol/xhigh"*. **That is wrong.** Measured (P5):

| Manifest pin | SSOT tier | Codex model | Effort | Fidelity |
|---|---|---|---|---|
| `haiku` @ any | `cheap_fast` | `gpt-5.6-luna` | `max` | **flat** — every tier maps to `max` |
| ~~`sonnet` @ low / medium / high~~ | ~~`workhorse`~~ | ~~`gpt-5.5`~~ | ~~`medium` / `high` / `xhigh`~~ | **RETIRED 2026-08-13 — no `workhorse` tier; resolves to nothing (unroutable)** |
| ~~`sonnet` @ xhigh / max~~ | ~~`workhorse`~~ | ~~`gpt-5.5`~~ | ~~`xhigh`~~ | **RETIRED 2026-08-13 — same** |
| `opus` @ any | `frontier_reasoner` | **`gpt-5.6-terra`** | `max` | **flat** — every tier maps to `max` |
| `fable` @ low / medium / high | `apex_reasoner` | `gpt-5.6-sol` | `medium` / `high` / `xhigh` | exact |
| `fable` @ xhigh / max | `apex_reasoner` | `gpt-5.6-sol` | `xhigh` | **clamped** |

`gpt-5.6-sol` is the `apex_reasoner` — **Fable's** slot, not Opus's. Two independent
effects degrade fidelity:

- **Flat effort maps.** `providers.openai.effort.map` sets
  `cheap_fast: {light: max, standard: max, thorough: max}` and
  `frontier_reasoner: {light: max, standard: max, thorough: max}` — deliberately
  (luna collapses below `max`; terra is effort-steep). Consequence: for every `opus`-
  or `haiku`-pinned session the declared reasoning tier **does not survive
  translation** at all.
- **Intent clamping.** `run.py::_INTENT_FROM_REASONING` maps `xhigh` and `max` both
  down to `thorough`, so `fable @ max` lands on `sol @ xhigh`, never `sol @ max`.
  That is CL-03's work; this contract only requires that the loss be *visible*.

### 4.3 The receipt

Printed to stderr at `begin`, and carried in each member's `translation` object plus a
`codex_translation` event in `run.ndjson`:

```
harness=codex  translate s02:  Opus · high  ->  gpt-5.6-terra · max
    tier      frontier_reasoner   (providers.anthropic.models -> providers.openai.models)
    effort    high -> intent=thorough -> providers.openai.effort.map.frontier_reasoner.thorough = max
    fidelity  flat_map — this tier maps every intent to `max`; the session's declared
              reasoning tier does NOT survive translation
    profile   providers.openai calibration.status = lane_scoped  (see D4)
```

Machine-readable fields on the member: `translated_from: {"model": "Opus", "reasoning": "high"}`,
`codex_model`, `codex_effort`, and
`effort_fidelity ∈ {exact, clamped, flat_map, raised_by_class, raised_by_escalation}`.

`effort_fidelity` is computed, not asserted: `flat_map` when the tier's `effort.map`
row has one distinct value; `clamped` when `_INTENT_FROM_REASONING` moved the tier
(`xhigh`/`max` → `thorough`); `exact` otherwise — and `raised_by_class` /
`raised_by_escalation` when the session ran ABOVE that row, because its `task_class`
prescribed more or because the upward climb (ESC-03) took it there. The two raises
are reported separately and never conflated: the receipt's `tier`/`effort` lines and
`translation.base` describe the cell the SSOT resolved, `translation.escalated_to`
describes where the climb went, and an escalation-raised rung is never attributed to
a `task_class` row that prescribes nothing of the sort. This mirrors the existing
`effort_enforced` / `effort_mechanism` honesty pair on the Claude path — the house
rule is that a tier is never announced as delivered when nothing delivers it. Project
memory records "gates that cannot fail" as this repo's dominant defect class; a
receipt that always prints the same reassuring line is one of those.

### 4.4 Claude-only dispatch fields under Codex

| Manifest field | Under `--harness codex` |
|---|---|
| `dispatch.subagent_type: "tier-*"` | **Ignored** (tier agents are Claude agent definitions; effort rides `-c model_reasoning_effort=`). Named in the receipt so the drop is visible. |
| `dispatch.subagent_type: <other agent>` | **Ignored**, named in the receipt. The agent's own frontmatter cannot apply. |
| `dispatch.subagent_type: "fork"` | **BLOCKED**, loudly, as `UnroutableCodexSession`. `fork` means "this session needs the orchestrator's live conversation context", which `codex exec` reading a prompt file provably cannot reproduce. Silently downgrading it to a fresh agent would hand the session a context it was authored to depend on and not given. |
| `reasoning` | Rides `-c model_reasoning_effort=<codex_effort>`; no thinking directive is prepended. |
| `model` | Translated per § 4.2. |

---

## 4.5 Decision D3b — a session declares the shell it needs, and the dispatch grants it

**Status:** AMENDMENT, ACCEPTED 2026-07-29. Supersedes nothing above; it fills a hole
§ 4.4 did not know it had.

**The hole.** Every decision above settles *which model* runs a session. None settles
*what the dispatched process may do*, and `_codex_cmd()` hardcoded one answer for every
plan: `--sandbox workspace-write`. Measured on codex-cli 0.145.0 (2026-07-29) from inside
exactly that command:

```
echo probe > ./in-workspace.txt        -> IN_WS_OK
echo probe > "$HOME/.dyno/probe.txt"   -> operation not permitted   (DENIED)
curl https://api.anthropic.com/        -> net=000                   (DENIED)
env | grep CODEX_SANDBOX               -> CODEX_SANDBOX=seatbelt
                                          CODEX_SANDBOX_NETWORK_DISABLED=1
nested `codex exec …`                  -> failed to initialize in-process
                                          app-server client          (DENIED, = P2)
```

So a plan whose sessions write outside the repo, reach the network, or drive a vendor CLI
was **portable in name only**: `--harness codex` accepted it, dispatched it, and failed it
one session at a time — after paying for each. Worse than refusing, and the plan-execute
instance of this repo's "gates that cannot fail" defect class.

**What actually grants each capability** (measured the same day, same CLI):

| Need | Grant | Observed |
|---|---|---|
| write outside the workspace | `-c sandbox_workspace_write.writable_roots=["…"]` | `DYNO_WRITE_OK` |
| network | `-c sandbox_workspace_write.network_access=true` | `net=404` — reached the host |
| nested `codex exec` / vendor CLI | `--sandbox danger-full-access` **only** | P3 ✅; still DENIED under a loosened `workspace-write` |
| declared parent environment variables | `shell_environment_policy.ignore_default_excludes=true` + exact `include_only` names | Codex config reference; default KEY/SECRET/TOKEN filtering otherwise removes them |

Both `-c` overrides apply **through `--ignore-user-config`**, so the determinism that flag
buys is preserved: the dispatch stays fully described by its own command line, never by a
stray `config.toml`.

**Decision.** A session may declare `dispatch.codex_shell`:

```json
"codex_shell": {"writable_roots": ["~/.dyno"], "network": true}
"codex_shell": {"sandbox": "danger-full-access"}
"codex_shell": {"env_include": ["ANTHROPIC_API_KEY", "OPENAI_API_KEY"]}
```

- **Absent, empty, or all-default ⇒ byte-identical to the pre-amendment command**
  (`test_codex_shell_default_is_byte_identical`). Every existing plan is unaffected.
- `writable_roots` are `expanduser().resolve()`d at build time — inside a quoted TOML
  string the shell cannot expand `~`.
- `danger-full-access` **forbids** the narrower keys beside it (it already grants both), so
  the command has exactly one reading.
- `env_include` accepts literal environment-variable names only. The command carries names,
  never values, and its `include_only` policy keeps the child environment to `PATH`, `HOME`,
  `TMPDIR`, and the declared names.
- Surfaced in the § 4.3 receipt as a `shell` line and in the `codex_dispatch` ndjson event
  as `codex_shell_grant`. A widening nobody can see is a widening nobody authorised.

**The invariant: an unsandboxed dispatched agent always has a human in front of it.**
`sandbox: danger-full-access` is legal ONLY on a session something already gates —
`_session_barred()` (linchpin / `irreversible_change` / `guards_irreversible`) or
`requires_human_checkpoint`. Enforced **twice**: `build_plan.py` refuses to build it, and
`run.py::_assert_full_access_gated` refuses to dispatch it (`UngatedFullAccessSession`,
a subclass of `UnroutableCodexSession`, so existing handlers treat it as a loud `BLOCKED`
+ halt). Two enforcement points because a manifest is an editable file, and the single
gate protecting an unsandboxed agent must not be owned only by the tool that wrote it.
`test_codex_shell_full_access_requires_a_human_gate` proves the gate is not vacuous by
running the identical session gated and ungated.

**Why not the alternatives.** *Loosen the sandbox for everyone* — grants every dispatched
session capabilities almost none need, and deletes the reason `--sandbox workspace-write`
was made explicit in the first place. *A run-level operator flag* — the requirement is per
session (mining sessions need a writable root; only the sweep sessions need nested
dispatch), so one flag would grant the maximum to all of them. *Leave it to the prompt* —
prose cannot change a sandbox.

---

## 5. Decision D4 — policy inside the Codex harness

Four gates currently stand between a plan session and a Codex process. Under
`--harness codex` they are not all still doing the job they were designed for.
Settled one at a time, with the reasoning stated rather than implied.

**The governing distinction.** Every one of these gates was written to answer:
*may a **Claude** session, on its own initiative, ship this working tree to OpenAI?*
That is a question about **unsupervised cross-vendor egress**. Under
`--harness codex` the operator has already started an OpenAI process in this tree,
by hand, with an explicit flag. The initiative question is settled before `run.py`
runs. What is *not* settled is data classification and irreversibility — and those
two gates stay.

### D4a — `require_calibrated`: **waived**

`providers.openai.calibration.status` is `lane_scoped`, and P6 proves it raises
`UnroutableCodexSession` on every dial-driven route. Left in place, it would block
100% of sessions under `--harness codex`.

Waived, because the gate's own error text states its purpose: *"a lane_scoped/
uncalibrated profile may not serve as the **execution default**"*. `--harness codex`
is not a default — it is a per-run, per-command election. The gate guards an implicit
choice; there is no implicit choice left to guard.

**Not waived silently:** the receipt prints `calibration.status = lane_scoped` on every
translation (§ 4.3), so the operator sees on turn one that they are running on a
profile transcribed from lane facts rather than freshly calibrated. Calibrating it is
`/routing-update`'s job, tracked elsewhere in this plan.

### D4b — `executor_policy.executor_for`: **waived**

P6 proves the list is **empty** — no `task_class` is opted into dial-driven Codex
execution. Waived for the same reason as D4a, and more strongly: `executor_for` is
explicitly the opt-in list for the **dial** (`active_provider: openai`). A typed
`--harness codex` is a strictly stronger and more deliberate signal than a config
default. Requiring an opt-in list to authorise something the operator just typed is
ceremony, not safety.

### D4c — linchpin / irreversible sessions: **allowed, but checkpoint-forced**

`_session_barred()` (P6) flags `task_class: linchpin`, `peer_triggers` containing
`irreversible_change`, and `dispatch.guards_irreversible`. Under `--harness codex`
these sessions are **allowed to run on Codex**, but never dispatched without a human
saying so first.

**Mechanism — reuse, no new machinery.** `begin --harness codex` on a barred session
does not dispatch it. It flips the session `TODO → AWAITS_REVIEW` and returns
`action: "checkpoint"` with a synthesised brief:

> **s07 is barred from unsupervised Codex execution** (`task_class is linchpin`).
> You elected `--harness codex` for this run, so this session would execute on
> `gpt-5.6-sol · xhigh` in this working tree. That is allowed, but a linchpin session
> is one-shot and hard to reverse, so it does not dispatch without you.
> **Decision:** run s07 on Codex now (`--resume`), or stop and run it from Claude Code
> instead (`/plan-execute <dir> --session s07`, no `--harness`).
> **If you do nothing:** the plan halts here with s07 unstarted; nothing is lost.

`--resume` then dispatches it — this is exactly the **pre-dispatch** `AWAITS_REVIEW`
case that `dispatch.py::ready_sessions(resume=True)` handles correctly (§ 3.5).

**Why not the alternatives.** *Refuse outright* keeps the Claude-harness rule and
kills the feature: the operator chose Codex on purpose, and a plan that stops dead at
its most important session is not portable. *Allow silently* discards the only thing
the bar was ever protecting — a human eye on irreversible work — while keeping none of
its cost. Checkpoint-forcing keeps the eye and drops the veto.

**One thing does not change:** a session that **explicitly pins** a Codex model *and*
is barred still `BLOCKED`s loudly on both harnesses. That combination is a
contradiction inside the plan itself (the author asked for unsupervised Codex on work
declared unsafe for it), and only the plan author can resolve it.

### D4d — the `data_sensitivity_guard` egress check: **kept, now content-aware, with honest scope**

Kept. `_find_restricted()` refuses a Codex dispatch from any tree that trips either
of two rules, unless `model-routing.yaml` clears it:

| # | Rule | Scanned surface | How it is cleared |
|---|---|---|---|
| 1 | **Filename** — a `.env*` entry anywhere, or a symlink resolving into one. | the **full** tree, git-ignored files included, directory symlinks traversed | whole-repo `egress_opt_ins` only |
| 2 | **Content** — `gitleaks dir` (the scanner `githooks/pre-commit` already runs), `--redact`ed. | the **reviewed surface**: git-tracked + untracked-not-ignored, minus `__pycache__`/`*.pyc` | per-file `content_scan_allowlist`, or `egress_opt_ins` |

The scanned tree's own `.gitleaksignore` is honoured — `run.py` matches its
`<repo-relative path>:<rule>:<line>` fingerprints itself. gitleaks' `-i` cannot do it
here: it compares fingerprints built from the path it was *handed*, and `run.py` hands
it absolute paths, while `gitleaks protect --staged` (the commit hook that writes those
pins) produces relative ones. Measured on the harness repo 2026-07-28: `gitleaks dir .`
honours all 16 pins and reports 0 findings; the identical tree scanned by absolute path
honours 0 and reports all 16. Until this was matched in `run.py`, every repo carrying a
`.gitleaksignore` — including the harness repo itself — was silently DO-NOT-SEND, with
no message anywhere saying why, while this document and the SSOT both claimed the file
was honoured.

Everything runs over realpath-resolved absolute paths. gitleaks does not descend into
symlinked directories, so `run.py` walks them itself and gives each out-of-tree target
its own pass (as it does each git submodule). No `gitleaks` on PATH — or a crash,
timeout, garbage output, or a scanned-byte count that does not match the surface
selected — **refuses**, naming the problem; there is deliberately no fallback scanner.
Both SSOT keys require an `expiry` and parse inline mappings only, so a commented-out,
block-style, or out-of-block entry authorizes nothing.

**The two rules have different scopes on purpose (fixed 2026-07-28, second pass).**
Rule 2 first shipped over the full tree and was unusable on a real repo: 16 GB /
103,727 files → **6 min 5 s and 826 findings**, every one of them inside git-ignored
build output (`dist/` 2.9 GB, `_workspace/` 2.6 GB), on a repo whose real secret count
is zero. Scoped to what git considers the repo — 570 files, 6.4 MB — the same scan is
**1.0 s and 1 finding**. Build output git ignores is not part of the repo and is not
what a reviewer reads or a push ships; if a secret really is in it, it is in the source
that generated it, which *is* scanned and *is* the fixable copy. Rule 1 stays
full-tree precisely because `.env` is the counter-example: usually git-ignored, and a
`.env` of plain `KEY=value` trips no content rule at all (measured), so a tracked-only
filename rule would see nothing. A tree that is **not** a git work tree has no ignore
information to scope by, so it is scanned whole, exactly as before.

Mechanism: `run.py` builds a throwaway directory of symlinks to the candidate files
and runs **one** `gitleaks dir` over it with `--follow-symlinks`. `gitleaks dir` takes
**one** path argument, so a 570-path set is otherwise 570 processes; and gitleaks
reports the resolved target in `File`, so a finding names the real repo path with no
mapping back. Every scoped pass asserts gitleaks' own `scanned ~N bytes` line against
the byte total of the candidate set and **refuses on an overshoot** — see the trap
below. Cost after the fix, same 16 GB repo: **2.2 s walk + 1.0 s scan ≈ 3.4 s**
(measured 2026-07-28, gitleaks 8.30.0; 3.1 s end to end for `plan --harness codex`).
Stated honestly the other way: on a repo that ignores almost nothing — the harness
repo itself, 39 MB, a 20 MB candidate set — scoping is about 0.3 s *slower* than the
full-tree scan. The saving is proportional to what a repo ignores, and the repos where
this gate was unusable are exactly the ones that ignore gigabytes.
Not cached across processes, deliberately: the full-tree walk is two thirds of that and
has to run every time for rule 1 anyway, so a cache could only save the ~1 s scan, and
a stale-cache false PASS in a DO-NOT-SEND gate costs more than a second.

**The trap, and why the byte assertion exists.** `gitleaks dir src tools tests docs`
does **not** scan those four directories — `dir` takes one path, the other three are
silently dropped, and it scans the CWD tree instead: 6 min 40 s over the 16 GB tree,
where the four directories cost 1.8 s scanned one at a time. It exits 0, it prints a
report, it looks like it worked. That is the same defect class as a gate that reports
green, and the only defence is asserting what the scan actually *read*
(`test_scan_scope_assertion_catches_a_widened_scan`).

**Changed 2026-07-28 — filename substrings dropped.** The gate used to match
`corpus` / `creds` / `credential` / `secret` as name substrings and never read a byte
of content. It refused a real repo because an analysis script was called
`migrate_corpus.py`, and it would have missed a live API key in `config.py`. The
"over-matching is the safe direction" argument only holds while people still respect
the gate; a guard that cries wolf gets routed around, and then it protects nothing.
Filenames now decide exactly one thing: `.env*`.

Why it survives the D4a/D4b reasoning: those two gates ask *who decided*; this one
asks *what is in the tree*. Electing Codex is consent to send **this project's
source**. It is not consent to send a credential store, and it never was — the
operator who typed `--harness codex` was deciding about a repo, not auditing it for
`.env` files. A false positive now costs one expiring `content_scan_allowlist` line
for that single file, not a whole-repo opt-in.

Both directions are regression-proved in `test_codex_dispatch.py`: planted synthetic
secrets (untracked, behind a file symlink, behind a directory symlink) refuse and the
refusal names the path; the same tree with the secret removed passes; a tree of
innocently-named `migrate_corpus.py` / `creds/README.md` / `secrets_helper.py` passes;
a `.env` refuses on the filename rule alone; `gitleaks` off PATH refuses; and
`test_content_scan_can_fail` neuters the scanner to prove the plants are not vacuous.
The scoping adds its own plant/control pairs in a real `git init` tree: the synthetic
key in a **tracked** file refuses, the same key in a **git-ignored** `dist/` does not,
an untracked-not-ignored file is scanned, a tracked `.pyc` is not while the `.py` it
came from is, a non-git tree still gets the full scan, and a scan forced to widen to
the whole tree refuses on the byte assertion. Each one was falsified against a
reverted implementation before being trusted.

**Its scope is genuinely narrower under `--harness codex`, and the ported SKILL.md
must say so rather than imply protection it no longer provides.** The orchestrator is
itself a Codex process that has already read the tree. The guard can no longer prevent
the *tree* from reaching OpenAI; what it still prevents is a **dispatched session**
shipping it, which is a real but reduced protection. The actual control is human and
belongs in the SKILL.md preconditions, in these words:

> **Do not start a Codex orchestrator in a restricted tree.** By the time `run.py`
> can refuse, your orchestrator has already read it.

To make that landable on turn one rather than at first dispatch, `plan --harness codex`
includes the egress verdict in its **first** payload:

```json
"egress": {"root": "/Users/…/your-private-harness", "restricted_hit": null, "opted_in": false}
```

This reuses `begin`'s existing lazy `egress_state()` — one scan per invocation,
shared by the disclosure and the refusal.

### D4e — verification family: deferred, with a recommendation

Under `--harness codex` every session stamps `executor_family: "openai"`, so the
existing provider-symmetric rule ("the non-executing family verifies") makes
`verifier_family: "anthropic"` for all of them — i.e. verification would require
spawning Claude from the Codex shell, which is the same cross-vendor egress in
reverse, and which an operator who chose Codex may specifically not want.

**Not settled here** — it is a verify-gate design question, not a dispatch-contract
one. Recommended default for CP-02/CP-03, to be confirmed by whichever session
implements verify under Codex: keep the stamp honest (`executor_family: openai`), and
set `verifier_mode: "on_box_human"` for review-class gates unless the operator has
explicitly enabled a Claude verifier (e.g. `claude -p` available and elected).
Deterministic gates (tests, argv gates) are family-neutral and need no change.

---

## 6. Decision D5 — single-source layout and how `gearbox deploy` renders it

**Decision.** Codex skill variants live in `your-private-harness` beside their Claude
originals, at `skills/<name>/codex/`. `gearbox deploy` grows a **render** step —
never a git sync — that copies them into `~/.codex/skills/<name>/`.

### 6.1 Source layout

```
skills/plan-execute/
├── SKILL.md                     # Claude — unchanged, still deployed to ~/.claude
├── scripts/                     # SHARED. run.py et al. Harness-neutral (P7).
├── references/                  # SHARED, including this file
└── codex/
    ├── SKILL.md                 # Codex frontmatter: name + description ONLY
    └── manifest.toml            # what to render, and what to shadow (§ 6.3)
```

`codex/SKILL.md` is a **hand-written sibling**, not a transform of the Claude one.
The two differ in the part that matters — dispatch — and generating one from the
other would mean encoding "replace the Task-tool section with the shell loop" as a
rewrite rule. What is genuinely shared (`scripts/`, `references/`) is shared by
reference, not by copy: the Codex `SKILL.md` points at the same `run.py`.

Frontmatter is a real constraint, not a stylistic one (P0c): Codex reads `name` and
`description` (plus optional `metadata.short-description`) and has no concept of
`allowed-tools` or `effort`. `codex/SKILL.md` carries only the keys Codex reads.

### 6.2 Render, not sync — and why it cannot be otherwise

`gearbox deploy` for `~/.claude` is a git fast-forward of a clone, guarded by
`quiesce-check.sh`, `install-hooks.sh` and `verify-routing.sh` (P8). **None of that
transfers.** `~/.codex/` holds `auth.json`, `sessions/`, four sqlite databases,
`plugins/cache/` and `history.jsonl`; it is not a clone and must never become one.

So the Codex target is an **idempotent, path-scoped render**:

- Copies only the paths a manifest names, into `~/.codex/skills/<name>/`.
- **Never deletes anything it did not write.** `~/.codex/skills/` also holds
  `.system/` (Codex's own bundled skills) and unmanaged user skills like
  `automation-discovery/`. A `--delete` sync there would destroy them.
- Writes a `.gearbox-rendered` stamp per managed skill dir (source commit + render
  timestamp) so drift is detectable and a re-render is a no-op.
- Runs **after** the `~/.claude` fast-forward and reads from the deployed tree, so
  the two targets can never disagree about which revision they carry.
- Failure is **non-fatal to the `~/.claude` deploy**, and loud. Codex is a second
  consumer; it must not be able to abort the primary deploy.

### 6.3 The shadow problem — must be handled in the same step

P0c: `~/.agents/skills/` (root r1) already contains stale auto-imported
`plan-builder`, `plan-execute` and `plan-harden`, whose text tells the model to treat
`Task` as "Codex subagent/multi-agent dispatch when available and appropriate".
Rendering our ports to r0 without retiring those leaves **two same-named skills** in
the roots table, one of which is a June-vintage misdirection.

The render step therefore also moves any shadowed name to
`~/.agents/skills-disabled/<name>/` — the mechanism the operator already used for
exactly this (`skills-disabled/` exists today with two retired entries). This is a
move, not a delete: reversible, inspectable, and it never touches skills we do not
manage. **CP-03 must verify the shadow is gone by re-running
`codex debug prompt-input` and asserting each ported name appears exactly once** —
that is a free, non-model check, and it is the difference between a real gate and a
gate that cannot fail.

### 6.4 The existing `adversarial-review` port is a shape precedent, not a sourcing one

`~/.codex/skills/adversarial-review/` (`SKILL.md` + `references/` + `agents/openai.yaml`)
is the layout to copy. But P8 shows it is **untracked in both repos** and already
divergent from `commands/adversarial-review.md`. It is the drift this decision exists
to prevent, and CP-05 should fold it into the same `skills/<name>/codex/` scheme
rather than treat it as a working model.

---

## 7. Open questions for later sessions

1. **Approval-escalation launch mode (§ 3.1)** — does `codex -s workspace-write
   -a on-request` escalate an approved dispatch out of the Seatbelt sandbox? If yes,
   it is a much better default than `danger-full-access`. **CP-03 must probe it live.**
2. **Verify-gate family under Codex (§ D4e)** — confirm the `on_box_human` default,
   or wire an explicit Claude-verifier opt-in.
3. **`--output-schema` (P0)** — `codex exec` supports a JSON-schema-constrained final
   response, which would eliminate the malformed-closeout failure class outright (the
   Claude side gets this via Workflow's `agent(..., {schema})`). The Claude path
   deliberately avoids `--json --output-schema` because of codex bug #19816
   (schema-valid *intermediate* messages breaking first-match stdout parsing) — but
   that bug is about **stdout parsing**, and we read `-o`, which is last-message by
   definition. Worth a probe in CP-02; potentially removes a whole failure row from
   § 3.3.
4. **`gpt-5.6-luna` as a real tier** — P0b/P5 show `haiku`-pinned sessions land on
   `luna @ max`, and the SSOT itself notes luna has "NO lane role today". CL-02 owns
   whether that cell should exist at all.

## 8. What each downstream session owes this contract

| Session | Owes |
|---|---|
| CP-02 | `--harness {claude,codex}` on `plan`/`begin`/`status`/`checkpoint`; `CODEX_SANDBOX` negative guard (D1); route every session through `_resolve_codex_dispatch` with `require_calibrated=False` (D3, D4a); `translation` + `effort_fidelity` receipt (§ 4.3); `fork` → BLOCKED (§ 4.4); barred → pre-dispatch checkpoint (D4c); egress verdict in `plan` output (D4d); `_codex/` last-message path + `codex_dispatch` ndjson event (§ 3.6); **a test asserting the `--harness claude` payload is byte-identical to today's.** |
| CP-03 | `skills/plan-execute/codex/SKILL.md` implementing § 3 verbatim; probe open question 1; assert no shadowed skill name (§ 6.3). |
| CP-04 / CP-05 | Same layout (§ 6.1); CP-05 additionally folds the untracked `adversarial-review` port into it (§ 6.4). |
| PI-* (deploy) | The render step of § 6.2 — path-scoped, never `--delete`, stamped, non-fatal, after the `~/.claude` fast-forward. |
| CL-02 / CL-03 | The fidelity losses measured in § 4.2 are the problem statement: flat effort maps for `frontier_reasoner`/`cheap_fast`, and the `xhigh`/`max` → `thorough` clamp in `_INTENT_FROM_REASONING`. |

### 8.1 As-built notes — CP-02 + CL-03 (2026-07-28)

Shipped in `run.py` (+ `test_codex_dispatch.py`, + the minimal `providers.openai`
effort-map/degrade extension). Four points where the build had to settle something
this document left open or said twice; **CP-03 writes its SKILL.md against these**.

1. **The `harness` key appears in a `begin`/`plan` payload ONLY under
   `--harness codex`.** § D1 asks for both "every `begin` JSON payload carries a
   top-level `harness` key" and "the Claude path must stay byte-identical"; those
   cannot both hold, and byte-identity is the one § 8 makes a test. The audit fact
   still lands: `dispatch_started` in `run.ndjson` carries `harness: "codex"`, and
   the Claude payload is asserted byte-identical (`test_claude_harness_payload_is_byte_identical`,
   including an exact member key-set).
2. **A barred session checkpoints the WHOLE batch** — nothing dispatches while the
   operator decides, the same all-or-nothing rule the unroutable path already uses.
   The gate's answer is recorded as the session's own `AWAITS_REVIEW` status: a
   second `begin --harness codex` on a session already at `AWAITS_REVIEW` dispatches
   it. No new flag, no new state file.
3. **Member field names under `--harness codex`:** `dispatch_cmd`,
   `last_message_file`, `fallback_cmd`, `fallback_last_message_file`,
   `codex_model`, `codex_effort`, `translated_from`, `effort_fidelity`,
   `translation` (tier / intent / effort_map_row / calibration_status / dropped /
   receipt). **No** `prompt_text`, `model_arg`, `wrapper_prompt`, or `codex_cmd` —
   those belong to the Claude-side two-layer wrapper, which is untouched.
   `verifier_mode` is `on_box_human` (§ D4e's recommended default).
4. **CL-03 needed a five-rung intent axis, not a two-value patch.** The clamp lived
   in `_INTENT_FROM_REASONING`, but removing it required somewhere for the top rungs
   to land: `providers.openai.effort.map` grew `exhaustive` and `maximal` on all four
   tiers, because `resolve_route.native_effort()` silently falls back to a row's
   `standard` cell for an unknown intent — a rung added on the code side alone would
   have DOWNGRADED `max`, not unclamped it (regression-proved both ways in
   `test_intent_rung_missing_from_effort_map_silently_downgrades`). Native ceilings
   are measured, not assumed (`codex debug models`, codex-cli 0.145.0, 2026-07-28):
   sol/terra reach `max`, `gpt-5.5` stopped at `xhigh` — so § 4.2's `workhorse` row
   stayed honestly `clamped` at the top, and only `apex_reasoner.maximal` reached
   native `max`. (SSOT v16, 2026-08-13: gpt-5.5 and the whole `workhorse` tier are
   retired — every model in the 5.6-only lane reaches `max`, so nothing clamps.) `calibration.status` untouched (s07 owns it).

### 8.2 As-built notes — CP-03 + CP-04 (2026-07-28)

Shipped as `skills/plan-execute/codex/` and `skills/plan-builder/codex/`
(`SKILL.md` + `manifest.toml` each), staged by hand into `~/.codex/skills/` and
run end to end. Evidence: `_evidence/s04/skill-discovery.txt`,
`_evidence/s04/dry-run.txt`. Four things the build settled or measured;
**CP-05 writes its port against these.**

1. **A skill `description` is clipped to ~66 characters before the model ever
   sees it.** Measured on this box (169 discovered skills, 54 of them clipped at
   exactly 66): `codex debug prompt-input` renders one line per skill and
   truncates mid-word — no ellipsis, no warning. The budget is shared across every
   discovered skill, so it shrinks as more are installed. **Front-load the entire
   trigger into the first ~60 characters** and treat the rest of the sentence as
   documentation for a human reading the file. This is a much harder constraint
   than "keep it to one sentence", and it is invisible unless probed.
2. **§ 6.3's shadow gate passes.** `~/.agents/skills/{plan-execute,plan-builder}`
   were **moved** (never deleted) to `~/.agents/skills-disabled/`, and each ported
   name now resolves exactly once, from `r0`. `manifest.toml`'s `shadows` list
   names the exact root path so the render step can never touch a skill we do not
   manage. The `*-vista` copies in r1 are NOT shadows — different names, and they
   never entered the skills table at all.
3. **Open question 1 (approval escalation) is unresolvable by probe, not merely
   unprobed.** `-a/--ask-for-approval` exists only on the interactive entrypoint;
   `codex exec` has no approval flag at all, so any automated probe can only
   reproduce P2. The ported SKILL.md documents only the P3-proven
   `--sandbox danger-full-access` recipe, names its blast radius, and marks the
   alternative UNVERIFIED. `skill-discovery.txt` carries a 3-step operator recipe
   to settle it at a TUI. **Do not re-probe it automatically.**
4. **New CLI precondition, not in § 1: `codex exec` refuses to run outside a git
   repository** — `Not inside a trusted directory and --skip-git-repo-check was
   not specified` (0.145.0). It exits 1 with **no** `-o` file, which § 3.3
   classifies as a dispatch failure, so it would burn the fallback rung on a cause
   no model change can fix. Real plan directories live in real repos, so
   `_codex_cmd()` needs no change; it is a stated precondition in the ported
   SKILL.md instead.

### 8.3 As-built notes — PI-01 / the deploy render step (2026-07-28, S06)

Shipped as `scripts/gearbox-codex.py` (`drift` / `render` / `harvest-back`), the
`[codex-target]` + `[codex-render]` sections of `scripts/deploy.pathspec`, three
call sites in `scripts/gearbox`, and `scripts/test_gearbox_codex.py`. Evidence:
`_plans/dual-harness-plan-skills-2026-07-28/_evidence/s06/drift-proof.txt`.
Everything § 6.2 asked for landed unchanged (path-scoped, never `--delete`,
stamped, non-fatal, after the `~/.claude` fast-forward, reading the deployed
tree). Four things the build had to settle:

1. **A content diff alone cannot tell a hand edit from a stale render**, and
   guessing either way is a real hazard: call a stale copy a hotfix and
   `harvest` REVERTS the source. So the `.gearbox-rendered` stamp is
   load-bearing, not decoration — § 6.2 asks for it as a drift *hint*, and it is
   in fact the discriminator. A difference is `stale` (never blocks) only when
   the target still matches the blob at the **stamped revision**; `modified`
   when it does not; `unstamped` when provenance cannot be established at all —
   no stamp, or a stamp naming a revision this tree does not have. All three
   directions are regression-proved.
2. **Drift is classified on BOTH sides of the render root.** A file inside a
   managed skill dir that the source does not render is `extra` and blocks, on
   the same fail-closed rule as `triage_untracked`. `harvest-back` copies it into
   the source tree **and `git add`s it** — otherwise it lands in
   `triage_untracked`, which `gearbox harvest` deliberately never auto-stages,
   and the deploy would be permanently stuck. It keeps being reported until the
   port's `manifest.toml` renders it or it is removed: that is an author's call,
   not something a script should decide.
3. **The named risk — pathspec classes overmatching Codex runtime noise — is
   answered structurally, not by exclusion rules.** No pattern is ever matched
   against the `~/.codex` tree. Every path gearbox reads or writes is
   `<codex-target>/<manifest name>/<manifest render entry>`, all three from
   tracked files in this repo, so `sessions/`, `cache/*.sqlite`, `history.jsonl`,
   `auth.json`, `.system/` and unmanaged skills are not merely ignored — they are
   never enumerated. Pinned by an assertion, not by inspection.
4. **The one-time adoption of the hand-staged ports was resolved by rendering,
   not harvesting.** § 6.4 predicted this: the live `plan-builder` copy was S04's
   hand-stage, which S05 then superseded in source, and it carried no stamp — so
   the gate refused it (`unstamped`) rather than guess. Source was known-newer,
   so it was overwritten. `plan-harden` (never staged at all) rendered fresh and
   its `~/.agents/skills` shadow was retired, closing § 6.3's last open shadow:
   all four ported names now resolve **exactly once**, from `r0`.

The dry-run walked the § 3.2 loop on a two-session fixture with **real**
`codex exec` dispatches: `plan` (egress verdict on turn one) → `begin` (receipt +
`dispatch_cmd`) → the command verbatim → `apply` on the `-o` file → the human
checkpoint held the loop → `--resume` released it → `complete`. `PLAN.html`
statuses and the `dispatch_started` / `codex_translation` / `codex_dispatch` /
`checkpoint_reached` events were all re-read from disk afterwards, not asserted.
It is re-runnable, not a one-off transcript: `skills/plan-execute/codex/fixtures/loop-smoke.sh [workdir]`
builds the two-session fixture in a throwaway git repo and walks the whole loop for two
`gpt-5.6-luna` calls. The committed evidence file IS that script's output.

---

## 9. Decision D6 — the replan, mutation and worktree surface under Codex

**Status:** AMENDMENT, ACCEPTED 2026-08-12 (plan `plan-framework-upgrade-2026-08-12`,
session S12, item SH-01). Between this contract's last amendment (D3b, 2026-07-29)
and this one, six new `run.py` subcommands and one new closeout field shipped on
`main` (sessions S03–S09 of the same plan): `ack-checkpoint`, `redispatch`,
`resolve-replan`, `add-session`, `amend-session`, `retire-session`, `plan_impact`
(+ the REPLAN checkpoint it raises), and the worktree-isolated `parallel_group`
mechanism (`worktree-status` / `worktree-cleanup` + `parallel-group-contract.md`).
This section settles what, if anything, `--harness codex` needs to do differently
for each. Two things NOT covered here were checked and found not yet to exist on
`main` as of this session — named honestly in § 9.4 rather than invented.

### 9.1 The replan/mutation commands need no harness branch — P7 already proves why

`checkpoint`, `ack-checkpoint`, `redispatch`, `resolve-replan`, `add-session`,
`amend-session` and `retire-session` are, like `plan`/`begin`/`apply`/`clear-halt`/
`release` before them, pure Python over `manifest.json` / `PLAN.html` /
`run.ndjson` / `_closeouts/` — none of them shells out to a model, a Task tool, or
anything harness-specific. P7's finding ("the plan directory and its state machine
are already the shared substrate; only the dispatch step is harness-specific")
covers all seven without new code. Verified directly against the shipped
implementation, not re-asserted from the P7-era text: none of `cmd_ack_checkpoint`,
`cmd_resolve_replan`, `cmd_redispatch`, `cmd_add_session`, `cmd_amend_session`, or
`cmd_retire_session` (`skills/plan-execute/scripts/run.py`) branches on `harness` —
only `cmd_plan`/`cmd_begin` do, because dispatch is the one harness-specific step.

**Who runs them under Codex, and how receipts work:** identical to § 3.2 —
the orchestrator (the interactive `codex` session) runs them itself, exactly as
documented for `checkpoint`/`clear-halt`/`release` today. There is no additional
"receipt" mechanism for `add-session`/`amend-session`/`retire-session` beyond what
already exists harness-wide: the journalled transaction (one atomic rename across
six files), the `_changelog.ndjson` line, and the rendered "Plan changes" row on
`PLAN.html` (§ 8's RP-06). None of that machinery reads `harness` either — a
mutation made from a Codex orchestrator session and one made from Claude Code
produce byte-identical `PLAN.html`/`manifest.json` output, because both go through
the same renderer (plan-builder's own, never a second assembler — the invariant
`plan-builder/codex/SKILL.md` § "Operating principles" already states).

**Consequence for the ported `plan-execute/codex/SKILL.md`:** document these seven
commands as directly usable, with the same syntax and semantics as the Claude side
— which is what shipped (verified in this session; see § 9.5).

### 9.2 The REPLAN checkpoint flow under Codex

A `plan_impact` closeout parks the **whole plan** (`halt.kind == "replan"`), not
just the session that raised it — `plan` returns `action: "replan"` with a
three-option decision brief. On a plan stamped `plan_schema_version >= 5` that
brief's `recommendation` slot is not merely empty but OWED: the orchestrator
records its judgement with `recommend-replan` (harness-neutral, exempt from the
halt like the other cure commands, and rendered into `HALT_NOTICE.txt` beside the
options), and `resolve-replan` refuses until it has. Honouring this
under Codex uses the exact mechanism § 3.5 already establishes for an ordinary
human checkpoint: **the orchestrator presents the brief and ends its turn.**
Nothing new is needed because a REPLAN park is, mechanically, another flavor of
"the loop stops and prints something for a human to answer" — the same halt-flag
plumbing `clear-halt` already handles, not a new halt type requiring new dispatch
code. The operator answers by applying their pick through the § 9.1 mutation
commands, then `resolve-replan` records the decision; `resolve-replan`,
`add-session`, `amend-session` and `redispatch` are exempted from the halt (they
are its cure) exactly as they are on the Claude side — one exemption list, not two.

Precedence (unchanged, harness-neutral): a closeout carrying BOTH
`human_checkpoint_reason` and `plan_impact` parks on the human checkpoint first;
`ack-checkpoint` raises the REPLAN afterwards. Plans below `plan_schema_version 3`
ignore `plan_impact` entirely, in both harnesses.

### 9.3 Worktree parallelism is explicitly Claude-harness-only — restated, with its enforcement point named

**This was already the position of record before this session** —
`parallel-group-contract.md` (frozen by session S06, PL-02, *before* S06B/S07
implemented against it) states in its member-rules section: *"Codex harness
boundary — explicitly unchanged. `begin --harness codex` dispatches serially, one
`codex exec` per session, in the shared tree, and creates no worktrees. It
therefore REFUSES an isolated group by name rather than running it unprotected."*
This decision restates that boundary **here**, in the contract § 3.4 already
gestures at ("parallelism is a shell detail... revisit only with a plan whose
batches are actually wide") but never made explicit for the worktree-isolation
case specifically — § 3.4 is about a Codex orchestrator choosing to background
independent `codex exec` calls itself; it is not, and was never, permission to
honour a plan's own `dispatch.isolation: "worktree"` declaration.

**Enforcement point, verified against the shipped code (not re-derived from
memory):** `run.py::_isolation_prep()`, called from `cmd_begin` before the lock and
before any status mutation. When `codex_harness` is true and the batch contains
any isolated member or integration session, it raises `SystemExit` naming the
affected session ids and citing `parallel-group-contract.md` §2 M3, rather than
silently dispatching them into the shared tree. There is no config flag, no
override, and no partial mode — a group that declares isolation is refused
**wholesale** under `--harness codex`; it either runs entirely under Claude Code,
or the plan author removes the declaration.

`worktree-status` and `worktree-cleanup` are the one part of this surface that
**does** work from either harness — they are read-only/cleanup operations over
`.plan-worktrees/<group>/<sid>` and carry no harness branch of their own. An
operator who started a group under Claude Code and is now driving the rest of the
plan from a Codex orchestrator can still inspect or clean up that group's
worktrees with them; what a Codex-harness `begin` cannot do is *create* new ones.

### 9.4 What this session found NOT yet landed, named rather than invented

Two things this session's own task brief named as port targets do not exist
anywhere in this repository as of 2026-08-12 (session `plan-framework-upgrade-
2026-08-12` sessions S08/S10/S11, `PLAN.html` status `TODO` at the time this
amendment was written) — checked by grep, not assumed:

- **The stuck protocol in generated prompts** (RS-03, session S11) and
  **decision-brief validation on BLOCKED closeouts** (RS-04, session S11,
  including the closeout `decision_brief` field). `build_plan.py`'s own
  `PLAN_SCHEMA_VERSION` docstring names it prospectively as *"S11's
  `decision_brief`"* — i.e. the authors of the schema-version history already
  knew it wasn't built yet. **Forward statement, so a later session does not have
  to redesign the plumbing:** when `decision_brief` ships, it needs no new
  Codex-side relay mechanism. § 3.2 step 4 already relays the **entire** closeout
  JSON blob, verbatim, through the `-o` last-message file to `run.py apply` —
  `decision_brief` would arrive as one more key inside that same blob, exactly as
  `plan_impact` does today (§ D3, shipped 2026-07-28, before `decision_brief` was
  even proposed). The only real work at that point is Claude-side/shared-script
  (parsing and presenting the field in `run.py`/`SKILL.md` prose), not a
  dispatch-transport change.
- **The review-gate policy additions** (RV-01 LLM review gate binding, RV-02
  review policy by task class + ultra at plan close, session S10). No
  implementation, and therefore no name, exists yet for either — there is nothing
  concrete to bind a Codex-side reference to.

Both are recorded, with reasons, in
`skills/plan-execute/codex/codex-out-of-scope.md`, checked by
`skills/plan-execute/codex/check_port_coverage.sh` against every subcommand/field
this section documents. **Parallelization-lint re-derivation** (PL-04, session
S08) is a third TODO item at the same repo state; it needs no separate contract
amendment because `plan-harden/codex/SKILL.md` § "4.0b" already points at the
SHARED `parallelization-lint.md` file S08 will edit in place — the Codex port
picks up S08's changes automatically once they land, with no port-side edit,
*provided* S08 keeps that lint's rules in the shared file rather than forking a
Claude-only variant. Also named in `codex-out-of-scope.md`, so a re-run of the
coverage check after S08/S10/S11 land will correctly demand a real port instead of
silently continuing to accept the declaration.

### 9.5 Verification

`skills/plan-execute/codex/check_port_coverage.sh` greps every name in this
section (plus RP-04's containment checks and PL-02/PL-03, which introduced no new
operator-facing name of their own — they hardened existing plumbing) across
`skills/*/codex/`, and cross-checks § 9.4's declared gaps against
`codex-out-of-scope.md`. It exits non-zero on any name that is neither found nor
declared — proved both directions in this session's evidence
(`_plans/plan-framework-upgrade-2026-08-12/_evidence/s12/port-coverage.md`): the
real run exits 0 over all thirteen required names and five declared gaps; a
`--plant` run adding one deliberately-unported name exits 1.
