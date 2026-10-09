# Codex-side critic transport — the inverted ladder

**Precedence.** This file overrides `~/.claude/skills/memo-loop/references/critique.md`
**for transport only** — Step 1 (probe) and Step 3 (dispatch) of that file, rung 1 and
rung 3 specifically. Every other contract in that file and its siblings — the envelope
schema, the findings contract, the discard gate, the pairwise-ranking envelope, the
fault-injection switch — is unchanged and still governs. Read `critique.md` first; this
file only replaces the two sections that assume Claude is the author.

**Why the ladder inverts.** Under this port the *writer* is a GPT model. The independent,
cross-vendor critic is therefore **Claude**, dispatched via `claude -p` — the mirror image
of the Claude-side port, where the writer is Claude and the critic is `codex exec`. Same
principle (`critique.md`'s "whichever harness runs the loop, the *other* provider's model
is the critic"), opposite direction.

## Contents
- [Step 1 (override) — Probe the transport](#step-1-override--probe-the-transport)
- [Step 3 (override) — Dispatch: rung 1, inverted](#step-3-override--dispatch-rung-1-inverted)
- [The mandatory user-visible warning (required, exact text)](#the-mandatory-user-visible-warning-required-exact-text)

## Step 1 (override) — Probe the transport

The Claude-side probe (`critique.md` Step 1) checks `codex` auth. That check is irrelevant
here — the CLI being dispatched is `claude`, and the boundary that can fail is the
**sandbox**, not login, even though a sandbox denial's error text claims otherwise. Measured
in both directions:

| Sandbox config | `claude -p "say OK"` | Reads as |
|---|---|---|
| `workspace-write`, `network_access = true` | exit 0, stdout `OK` | works |
| `workspace-write`, `network_access = false` (Codex's own default) | exit 1, `Not logged in · Please run /login` | **misleading** — this is a sandbox setting, not a login problem |

So: **nested-CLI availability is configuration-dependent, not structurally denied.** The
port probes and classifies the failure reason rather than assuming either "always works"
or "always blocked":

```
probe_codex_side():
  if runtime has no shell (codex-desktop):                        FAIL("no-shell")
  1. `command -v claude`                                  absent  -> FAIL("cli-not-found")
  2. `claude -p "reply with OK"`  (timeout 60s)
       exit 0 AND stdout contains "OK"                             -> PASS
  3. classify the failure BEFORE reporting it:
       a. the active sandbox is `read-only`                        -> FAIL("sandbox-network-denied")
       b. else READ THE FILE $CODEX_HOME/config.toml
          (default ~/.codex/config.toml), inspect the key
          `sandbox_workspace_write.network_access`:
             present and false   -> FAIL("sandbox-network-denied")
             absent               -> FAIL("sandbox-network-denied")   # Codex's own default is OFF
             present and true    -> FAIL("indeterminate")
       c. file unreadable or unparseable                            -> FAIL("indeterminate")
```

**Step 3b reads the config file directly; it never shells out to `codex config`.** That
subcommand does not exist (verified against codex-cli 0.146.0's subcommand list: `exec
review login logout mcp plugin mcp-server app-server remote-control app completion update
doctor sandbox debug apply resume archive delete unarchive fork cloud exec-server features
help`) — a probe that invoked it would fail with an error indistinguishable from the
auth failure this probe exists to stop misreporting.

**`indeterminate` is a real, distinct outcome — never a fallthrough to `unauthenticated`.**
When network access is configured on and the nested CLI still failed, the cause is
genuinely unknown; asserting "unauthenticated" without positive evidence would be a guess.
`unauthenticated` is reachable only via an explicit `claude` auth-status check that reaches
the CLI and reports logged-out — something this probe, running inside the sandbox, cannot
itself obtain.

Record in `run.json`, always:

```json
"runtime_detected": "codex-cli",
"probe": { "rung1": {"result": "fail", "reason": "sandbox-network-denied",
                     "cli": "claude-code 2.1.221"} },
"rung_used": 3,
"reviewer_independence": "same-family",
"residual_bias": "<rung 3's verbatim sentence from critique.md's rung table>",
"demotions": []
```

## Step 3 (override) — Dispatch: rung 1, inverted

### Rung 1 — nested `claude -p`, read-only-equivalent, pinned model

The default whenever `probe_codex_side()` returns PASS.

```bash
claude -p \
  --model opus --effort high \
  --output-format json \
  --json-schema "$RUN_DIR/critic-schema.json" \
  --disallowedTools "Edit,Write,NotebookEdit,Bash,Agent,Artifact" \
  --allowedTools "Read,Grep,Glob" \
  --add-dir "$RUN_DIR" \
  < "$RUN_DIR/critique/critic-prompt.md" \
  > "$RUN_DIR/critique/critic-response.json"
```

Every flag is load-bearing, and each maps onto the same two guarantees `critique.md`
states for the Claude-side rung 1 — obtained by a different mechanism because there is no
`-s read-only` on this CLI:

| Guarantee | Claude→Codex rung 1 (`critique.md`) | Codex→Claude rung 1 (here) |
|---|---|---|
| **Critic cannot edit** | `-s read-only` — the sandbox denies writes, structurally | `--disallowedTools` removes every mutating tool, **and** `--allowedTools "Read,Grep,Glob"` is passed too, making the allow-list authoritative rather than the deny-list. A deny-list alone leaves any tool added in a future Claude Code release enabled by default; the allow-list closes that gap. |
| **Envelope enforced at the transport** | `--output-schema` | `--json-schema` + `--output-format json` |

**Model pin: `opus` at effort `high`, explicit on every dispatch, never inherited** from
the invoking session's own model or effort. This mirrors the Claude-side pin
(`-m gpt-5.6-sol -c model_reasoning_effort=xhigh`) in shape — a fixed, judgment-tier
critic, not the cheapest available and not the top dead rung either — but is a
**separate decision**, made here: the design pins the `sol` side only and leaves the
Claude-side critic model to this port. Grounds, cited rather than assumed: `~/.claude/model-routing.yaml` names
Opus 5 "the standing-default tier for judgment work" and its escalation ladder stops at
`high` — `xhigh` showed no gain in routing tests on Opus 5 at a large extra cost, and `max`
was weak for its cost — so `high` is the ceiling worth paying for, exactly as `xhigh`
is `gpt-5.6-sol`'s analogous ceiling before its own dead rung. Both `--model` and
`--effort` are verified-live flags in the version tested (`claude --help`).

Timeout 900s, matching the Claude-side rung 1. A timeout is a **transport error** per
`critique.md` §4, never a zero-findings read. The finding envelope is extracted from the
`--output-format json` result field before that section's validation runs.

### Rung 2 — unchanged

`critique.md`'s rung 2 (the portable cross-vendor packet, pasted by the user into another
vendor's app) is harness-agnostic already — it performs no model dispatch of its own. Use
it verbatim; nothing here overrides it.

### Rung 3 — same-family fallback, inverted

Last resort, and the routine fallback whenever rung 1 fails and the run should not stall
on a human. A **fresh** `codex exec` dispatch on a **different** GPT model than whichever
model authored the draft — never the same model re-marking its own work, and never
presented as cross-model:

```bash
codex exec \
  -s read-only \
  -m gpt-5.6-terra -c model_reasoning_effort=high \
  -C "$RUN_DIR" --skip-git-repo-check \
  --output-schema "$RUN_DIR/critic-schema.json" \
  -o "$RUN_DIR/critique/critic-response.json" \
  --json \
  - < "$RUN_DIR/critique/critic-prompt.md"
```

`gpt-5.6-terra`, not `gpt-5.6-sol` again — the point of rung 3 is a genuinely different
model, mirroring the Claude-side rung 3's "a different model in the same family." `-s
read-only` still makes "cannot edit" structural here, same as the Claude-side rung 1;
unlike the nested-`claude` case there is no allow/deny-list to reason about because the
sandbox itself is the mechanism.

**`gpt-5.6-terra` is the default, not an unconditional pin — the author model is the
constraint.** Under this port the author is "GPT, whichever model runs this session", which
is not fixed anywhere; if that session *is* `gpt-5.6-terra`, the command above hands the
draft back to its own author and the one rule this rung exists to honour — never let a model
grade its own output — is silently broken on the routine fallback path. So:

1. **Record the author model** in `run.json` as `author_model` at Phase 0, read from the
   running session, alongside `runtime_detected`.
2. **Select rung 3's critic dynamically:** `gpt-5.6-terra` unless it equals `author_model`,
   in which case the next distinct GPT model of judgment tier. Record the choice and why.
3. **Hard guard before dispatch:** if the selected critic model equals `author_model`, do
   **not** dispatch. That is a transport error at rung 3, classified and laddered exactly like
   any other — never a critique that quietly reviews itself.

Record `reviewer_independence: "same-family"` with the mandated `residual_bias` sentence
from `critique.md`'s rung table — never presented as cross-model.

## The mandatory user-visible warning (required, exact text)

On any rung-1 failure — before falling back — print this, verbatim except the bracketed
fills:

> **Cross-vendor critic unavailable.** The nested `claude` CLI did not run under the Codex
> sandbox (reason: `<sandbox-network-denied | cli-not-found | unauthenticated | indeterminate>`).
> If the reason is `sandbox-network-denied`, this is a **sandbox setting, not a login
> problem** — running `/login` will not fix it; enable
> `sandbox_workspace_write.network_access` in `~/.codex/config.toml`, or run the critic
> outside the sandbox.
> Falling back to **\<rung 2 portable packet | rung 3 same-family>**. This run's critique
> is **\<human-mediated cross-vendor | same-family>**, and the convergence report says so.

This warning is never silently skipped. A run that degraded from cross-vendor to
same-family without printing it, or without the same fact appearing in the convergence
report's `residual_bias` line, is a contract violation — the exact silent-degradation
failure mode `critique.md`'s non-negotiable #1 exists to prevent, applied to this port's
own inverted direction.

**Fallback order, Codex side:** rung 2 (portable packet) → rung 3 (fresh `codex exec` on a
different GPT model). Same shape as the Claude-side ladder; only rung 1's mechanism and
failure classification differ, which is why they live in this file and nowhere else.
