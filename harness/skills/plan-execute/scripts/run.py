#!/usr/bin/env python3
"""/plan-execute orchestration helper (facade).

This is NOT the orchestrator — the orchestrator is Claude in the main
conversation, following SKILL.md. This CLI exposes the deterministic pieces the
orchestrator calls between Task dispatches. It never invokes the Task tool.

Subcommands (all take the plan directory as the first positional arg):

  status DIR                       Read-only state dump (JSON). Surfaces any
                                   HALT_NOTICE.txt banner first.
  status --all                     One-line status for every _plans/*/ under cwd.
  plan   DIR [--resume] [--session SID]
                                   Compute next action (JSON): dispatch |
                                   checkpoint | blocked | complete | halted.
  begin  DIR --sessions S... [--unsafe-lock]
                                   Acquire lock, anchor-preflight, flip the
                                   batch to DOING, record last_batch. Emits the
                                   prompt text (with the reasoning directive
                                   prepended) + model_arg + reasoning tier per
                                   session for the
                                   orchestrator to hand to Task. Refuses on a
                                   networked/sync FS unless --unsafe-lock is
                                   given. (JSON)
  apply  DIR --session SID --output-file F
                                   Consume one closeout file F: extract -> parse
                                   -> schema -> semantic -> persist -> atomically
                                   rewrite PLAN.html block(s). On any failure:
                                   set session BLOCKED + halt. (JSON)
  checkpoint DIR --session SID     Set SID -> AWAITS_REVIEW (human checkpoint).
  ack-checkpoint DIR --session SID [--note N]
                                   APPROVE a POST-SESSION checkpoint: a session
                                   that already closed DONE and was parked at
                                   AWAITS_REVIEW by its closeout's
                                   human_checkpoint_reason. Flips it to DONE and
                                   logs checkpoint_approved. Idempotent. Refuses
                                   on TODO/DOING/PARTIAL and on a PRE-DISPATCH
                                   checkpoint (that one is `--resume`'s job).
  redispatch DIR --session SID --reason R [--allow-stale-dependents]
                                   Deliberately re-run an already-FINISHED
                                   session because new facts invalidated its
                                   output. Resets it to TODO, archives its
                                   closeout/verify/shipping state under a
                                   versioned name, and CASCADES to dependents
                                   whose work derived from it (default).
                                   --allow-stale-dependents keeps them as-is and
                                   records the acceptance in run.ndjson.
  add-session DIR --id SID --title T [--items I] [--new-item 'id|cat|title']
              [--depends-on S] [--model M] [--reasoning R] [--gates G]
              [--require-evidence] [--prompt P] [--infographic-group G]
                                   Add a session (and any new items) to a RUNNING
                                   plan, writing EVERY surface in one journalled
                                   transaction: manifest entry, anchored article
                                   in the correct section, nav-strip chip, header
                                   counts, prompt + context files, change log.
                                   Rejects an item whose category matches no
                                   section, and any dependency cycle.
  amend-session DIR --session SID [--depends-on S] [--prompt P] [--model M]
                [--reasoning R]    Change what a TODO session will be dispatched
                                   with. Refuses on DOING/DONE/terminal — those
                                   were dispatched against the old text.
  retire-session DIR --session SID --reason R [--cascade|--drop-dependency]
                                   Close a session WONTFIX with the reason
                                   recorded as a note. WONTFIX counts as DONE to
                                   the dispatcher, so it REFUSES while live
                                   dependents exist unless you cascade the
                                   retirement or drop the dependency (which also
                                   rewrites those dependents' prompts, same
                                   transaction).
  clear-halt DIR                   Clear the halt flag (operator). (v1.5)
  release DIR                      Release the lock (end of run).

  worktree-status DIR --group G    Read-only view of an isolated parallel group:
                                   pinned base ref, each member's worktree,
                                   branch, commits ahead, dirty state, and the
                                   containment report.
  worktree-cleanup DIR --group G [--force]
                                   Remove the group's member worktrees and
                                   branches. EXPLICIT and LAST (contract §4) —
                                   nothing calls this automatically. PRESERVES
                                   (and exits 1 on) any worktree still holding
                                   uncommitted, untracked or locally-created
                                   ignored content; `--force` overrides that for
                                   the worktree only. Branch deletion is always
                                   `git branch -d`, never `-D`, so a branch with
                                   unmerged commits always survives.
                                   Creation/merge are automatic, inside `begin`.

  --harness {claude,codex}         Which ORCHESTRATOR is driving this run (CP-02;
                                   default claude, accepted everywhere, acted on
                                   by `plan` and `begin`). Under `codex`, `begin`
                                   emits a runnable `codex exec` command per
                                   session — including Claude-pinned ones, which
                                   translate through the SSOT with a printed
                                   receipt — and `plan` discloses the egress
                                   verdict on turn one. The `claude` payload is
                                   unchanged. Contract + evidence:
                                   references/dual-harness-contract.md.

Shipping subcommands (post-session version-control + deploy; see SKILL.md
"Shipping actions"). The deterministic state machine is in shipping.py; the
orchestrator invokes the actual skills.

  ship-begin DIR --session SID [--dry-run] [--resume] [--confirm-stale]
                                   Acquire resource locks, reconcile idempotency
                                   digests, run the guard + staleness gate, and
                                   return the first shipping directive.
  ship-record DIR --session SID --step S --status done|failed [--result-file F]
                                   Record a skill step's outcome; return next.
  ship-run DIR --session SID --step S
                                   Run an argv step here; return next.
  ship-finalize DIR --session SID  Finalize + release shipping locks.
  ship-release DIR                 Release all shipping locks (abort cleanup).
  ship-status DIR --session SID    Read-only computed plan + state (no lock).
  ship-simulate DIR --session SID  Run the whole pipeline producing real events
                                   + state but auto-succeeding every step (no
                                   destructive skill / real command). CI / smoke.

Exit code is 0 on success, 1 on an error the orchestrator must surface.
"""

import argparse
import contextlib
import importlib.util
import json
import os
import re
import shlex
import shutil
import stat as statmod
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import article_block as ab
import closeout_pipeline as cp
import dispatch as dsp
import escalation as esca
import gate_policy as gp
import manifest_io as mio
import outcomes as outc
import parallel_contract as pc
import plan_mutate as pm
import render_verify as rv
import replan as rp
import run_state_io as rsi
import ship_state_io as ssio
import shipping as shp
import stuck_protocol as stuckp
import structural_gate as sg
import verify as vfy
import worktree as wt

PLAN_HTML = "PLAN.html"
_SHADOW_HOOK_MODULE = None


def _shadow_dispatch_description(plan_dir, session, prompt_text):
    """Return an opaque Task-description marker for an eligible shadow sample.

    This is deliberately a description, never a prompt prefix: the primary
    subagent receives byte-identical work instructions whether telemetry is
    enabled or disabled.  The hook validates the one-use marker against the
    registration stored outside the repository.

    Telemetry is observational.  Any import, config, git-snapshot or runtime
    failure returns ``None`` and leaves ordinary plan dispatch untouched.
    """
    global _SHADOW_HOOK_MODULE
    try:
        if _SHADOW_HOOK_MODULE is None:
            hook_path = Path(__file__).resolve().parents[3] / "hooks" / "shadow-sampling.py"
            spec = importlib.util.spec_from_file_location("gearbox_shadow_sampling", hook_path)
            if spec is None or spec.loader is None:
                return None
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            _SHADOW_HOOK_MODULE = module
        gates = _SHADOW_HOOK_MODULE._gate_specs_from_plan(Path(plan_dir), session["id"])
        if gates is None:
            return None
        marker = _SHADOW_HOOK_MODULE.register_dispatch(
            origin="plan-execute",
            workflow_session_id=session["id"],
            prompt=prompt_text,
            gates=gates,
            cwd=Path.cwd(),
        )
        return f"plan-execute:{session['id']} {marker}" if marker else None
    except Exception:
        return None


def _plan_url(plan_dir):
    """Absolute file:// URL for this plan's dashboard (PS-01) — every
    checkpoint/completion message carries this so a session end never leaves
    the operator hunting for the path himself."""
    try:
        return _html_path(plan_dir).resolve().as_uri()
    except (OSError, ValueError):
        return None


def _out(obj, plan_dir=None):
    if plan_dir is not None and isinstance(obj, dict) and "plan_url" not in obj:
        obj = {**obj, "plan_url": _plan_url(plan_dir)}
    print(json.dumps(obj, indent=2, ensure_ascii=False))


# Map a manifest `model` field (free-form, e.g. "Sonnet", "Opus 4.8") to the
# token the Task/Agent tool's `model` parameter accepts, or None when the model
# is unspecified / unrecognized (the orchestrator then omits `model`, letting
# the subagent inherit per its agent definition / the parent).
_MODEL_TOKENS = ("opus", "sonnet", "haiku", "fable")


def _normalize_model(raw):
    if not raw or not isinstance(raw, str):
        return None
    low = raw.strip().lower()
    for token in _MODEL_TOKENS:
        if token in low:
            return token
    return None


# SSOT LOCKSTEP (s04, SKL-03, comment-only — no behavior change): this dict and
# _FALLBACK_LADDER below are CODE-AUTHORITATIVE per ~/.claude/model-routing.yaml's
# header carve-out (1) — the SSOT MIRRORS these constants, not the other way round.
# verify-routing.sh check (d) ast.parses this file (never imports/executes it) and
# fails the drift guard if `_REASONING_DIRECTIVE`'s keys don't cover every SSOT
# reasoning tier, or if `_FALLBACK_LADDER` (below) diverges from the SSOT's
# tier-keyed `providers.<name>.degrade` blocks (translated tier→model via
# `providers.<name>.models` — the ONLY machine-readable degradation source since
# s04). Edit both sides together; the guard is what keeps them honest, not this
# comment.
#
# Reasoning-effort tiers carried per-session in the manifest (plan-builder schema).
# Unlike `model` (a Task parameter), reasoning is prompt content: each tier maps to
# an extended-thinking directive PREPENDED to the session prompt at dispatch time.
_REASONING_DIRECTIVE = {
    "low": "",  # normal generation — no thinking trigger
    "medium": "Think about the approach before you begin.",
    "high": "Think hard about edge cases and trade-offs before you begin.",
    "xhigh": (
        "Think very hard — reason at extended depth across edge cases, trade-offs "
        "and failure modes before acting."
    ),
    "max": (
        "Ultrathink — reason deeply and exhaustively before acting; "
        "this session is high-stakes or hard to reverse."
    ),
}


def _reasoning_tier(raw):
    """Normalize a manifest reasoning value to low|medium|high|xhigh|max, or '' if
    unset/unknown. `extra` is accepted as a synonym for `xhigh` (the Cowork picker
    label). Before xhigh existed here, `xhigh`/`extra` fell through to '' and
    silently produced no thinking directive — that gap is now closed."""
    low = raw.strip().lower() if isinstance(raw, str) else ""
    if low == "extra":
        low = "xhigh"
    return low if low in _REASONING_DIRECTIVE else ""


def _reasoning_directive(raw):
    """Return the extended-thinking directive for a reasoning tier ('' = none)."""
    return _REASONING_DIRECTIVE.get(_reasoning_tier(raw), "")


# --------------------------------------------------------------------------
# TIER AGENTS — the only mechanism that actually BINDS a subagent's effort.
#
# MEASURED against code.claude.com/docs/en (Claude Code 2.1.220, 2026-07-26),
# not assumed: `_REASONING_DIRECTIVE` above is INERT as an effort control.
#   * "Other phrases such as `think`, `think hard`, and `think more` are passed
#     through as ordinary prompt text and are not recognized as keywords."
#   * Only `ultrathink` is recognized, and even it "adds an in-context
#     instruction. The effort level sent to the API is unchanged."
#   * A subagent dispatched with `subagent_type: null` is a FRESH general-purpose
#     agent with no definition file, so no `effort:` frontmatter applies and it
#     inherits the SESSION effort level (settings.json `effortLevel`).
# Net effect before this change: a plan's per-session `reasoning` tier was
# announced in the stream and prepended to the prompt, but every Claude-backed
# session actually ran at whatever the orchestrator's session effort happened to
# be. `model` was honored (it is a real Task parameter); effort was theatre.
#
# The binding mechanisms, in precedence order (docs § "Set the effort level"):
#   1. CLAUDE_CODE_EFFORT_LEVEL env var — beats everything, session-wide
#   2. skill / subagent frontmatter `effort:` — per agent, overrides session  <-- this
#   3. session level (`/effort`, --effort, settings `effortLevel`)
#   4. model default
# Note `effortLevel` in MANAGED settings is explicitly "a starting default, not
# enforcement" — it is not a floor and not a ceiling.
#
# So: resolve the manifest's (model, reasoning) pair to a tier-agent DEFINITION
# carrying both `model:` and `effort:`, and pass it as `subagent_type`. The set
# below is the cross product the SSOT actually resolves (providers.anthropic
# models x effort.map: haiku takes no effort dial; sonnet/opus map
# light|standard|thorough -> low|medium|high). Escalation rungs above `thorough`
# (xhigh dead, max operator-elected) deliberately have NO tier agent: they fall
# back to the prepend path with `effort_enforced: false` so the gap is VISIBLE in
# the member payload instead of silently inheriting.
_TIER_AGENT_DIR = Path.home() / ".claude" / "agents"
_TIER_AGENTS = {
    # Haiku takes NO effort dial (providers.anthropic.effort.map.cheap_fast is all-None,
    # and the SSOT's HAIKU INVARIANT fails any haiku agent carrying an `effort:` key), so
    # one agent serves every haiku tier — there is no dial to bind.
    ("haiku", "low"): "tier-haiku",
    ("haiku", "medium"): "tier-haiku",
    ("haiku", ""): "tier-haiku",
    ("sonnet", "medium"): "tier-sonnet-medium",
    ("sonnet", "high"): "tier-sonnet-high",
    ("opus", "medium"): "tier-opus-medium",
    ("opus", "high"): "tier-opus-high",
    # fable is the named escalation-apex exception (ESC-01, 2026-08-13): reached from
    # opus-high on the SSOT's standing ladder when opus's own rungs aren't enough, so
    # unlike the sonnet/opus xhigh rungs above (dead/operator-elected, deliberately
    # unmapped), fable gets a real tier agent through xhigh. fable.max still has none —
    # it falls back to the prepend path like every other unmapped cell.
    ("fable", "medium"): "tier-fable-medium",
    ("fable", "high"): "tier-fable-high",
    ("fable", "xhigh"): "tier-fable-xhigh",
}


def _tier_agent(model_token, reason_tier, agent_dir=None):
    """Resolve (model, effort) to a tier-agent name whose definition EXISTS on
    disk, else None. Existence is checked, never assumed: a missing definition
    would make Task fail the dispatch outright, so an absent file must degrade to
    the prepend path rather than break the session."""
    name = _TIER_AGENTS.get((model_token, reason_tier))
    if not name:
        return None
    base = Path(agent_dir) if agent_dir else _TIER_AGENT_DIR
    return name if (base / f"{name}.md").is_file() else None


# Reactive degradation ladder (added 2026-07; PROVIDER-SCOPED since s04/DSP-02).
# When a dispatched model is refused for access/entitlement (e.g. Fable
# suspended/paywalled, or `codex exec -m` rejects the model), the orchestrator
# re-dispatches the SAME session on the next model down, at a PER-TARGET reasoning
# tier (recalibrated on our own calibration run, 2026-07-25): fable→opus lands at
# `high` (Opus 5's sweet spot — high→xhigh is a DEAD RUNG on our own calibration
# run: no measurable gain for materially higher cost), and opus→sonnet lands at
# `high` — sonnet high→xhigh is a DEAD RUNG too (a small accuracy gain for
# materially higher cost, per the SSOT), so degrading onto either xhigh would
# land on a rung the SSOT itself outlaws. Floor is the provider's workhorse — never
# auto-drop judgement work to Haiku, never emit Opus @ `max`. The openai chain is
# the v1.15 5.6-ONLY walk: sol→terra@max→NO-CODEX (terra is the SINK — the
# judgement floor; luna is entered only as a baseline or by the upward rescue).
# SSOT LOCKSTEP (s04): these dicts are CODE-AUTHORITATIVE — model-routing.yaml's
# tier-keyed `providers.<name>.degrade` blocks MIRROR them (translated tier→model
# via `providers.<name>.models`), and verify-routing.sh check (d) ast-walks this
# file and fails if either provider's ladder diverges. They MUST stay static
# module-level dict literals (never a comprehension/function-built dict — the
# guard uses ast.literal_eval and would silently break). Edit these dicts, then
# update the SSOT's mirror in the same commit.
_FALLBACK_LADDER = {
    "anthropic": {"fable": "opus", "opus": "sonnet"},
    "openai": {
        # 5.6-ONLY since v1.15 (operator directive 2026-08-13): gpt-5.5 is retired and
        # the `workhorse` tier is removed from providers.openai, so this lane is
        # luna / terra / sol.
        # luna is the BOTTOM tier — a refusal there has nowhere lower to go, so its
        # rung is a RESCUE UPWARD (to terra) rather than a drop (CL-03, 2026-07-28,
        # re-pointed at v1.15 when its old gpt-5.5 target retired). Without it a
        # haiku-pinned session translated to luna had a null fallback, i.e. one
        # refused mechanical session took the whole run to NO-CODEX. resolve_route's
        # degrade() still returns `exhausted` from a cheap_fast current (its floor
        # rule fires first) — the divergence is deliberate and documented beside the
        # SSOT mirror.
        # terra is the SINK: there is deliberately NO terra→luna rung. It would close a
        # terra→luna→terra CYCLE (this dict is a single lookup TODAY, but a ladder-walking
        # consumer would spin), and luna carries only mechanical/standard_build, so the
        # only work that rung could move is judgement work — which providers.openai's
        # escalation invariant forbids from landing on luna. A refused terra is NO-CODEX.
        "gpt-5.6-luna": "gpt-5.6-terra",
        "gpt-5.6-sol": "gpt-5.6-terra",
    },
}
_DEGRADE_EFFORT = {  # keyed by TARGET model, per provider
    "anthropic": {"opus": "high", "sonnet": "high"},
    # terra is effort-steep, never below max. (No luna row: nothing degrades ONTO luna —
    # see the sink note above. If that ever changes, luna's row must come back with the
    # rung, since its effort curve COLLAPSES below max: high 44%, medium 11%.)
    "openai": {"gpt-5.6-terra": "max"},
}


def _fallback_for(token, provider="anthropic", refused=()):
    """Given a concrete model id, return the reactive-degradation target as
    ``(next_model, effort)`` — effort per _DEGRADE_EFFORT[provider] — or ``None``
    when no lower judgement-safe tier exists for that provider (already at/below
    the judgement floor, or an unrecognized/None token).

    THE REFUSED SET IS AUTHORITATIVE OVER THIS EDGE (ESC-03 rework, 2026-08-14).
    `refused` is the session's refused-rung set (`escalation.compute`'s descriptor
    carries it); a cell in it was refused at a real dispatch and is unavailable for
    the rest of the session. This ladder is a STATIC dict that knew nothing about
    that, and the two mechanisms fought one level below the one
    `_refusal_instruction` already settles: a session that stepped DOWN off a
    refused escalated rung lands back at rung 0, where the escalated member's
    `fallback_model: null` suppression no longer applies — so the downward edge
    handed that same refused rung straight back, as a ready-to-run command. The
    refusal wins; the edge is dropped rather than re-proposed."""
    ladder = _FALLBACK_LADDER.get(provider, {})
    nxt = ladder.get(token if isinstance(token, str) else "")
    if not nxt:
        return None
    pair = (nxt, _DEGRADE_EFFORT[provider][nxt])
    if esca.rung_key(*pair) in set(refused or ()):
        # Said out loud: a fallback that silently disappears reads as a bug to the
        # operator who is watching for one.
        print(f"fallback: {esca.rung_key(*pair)} was refused at dispatch, so the downward "
              f"edge from {token} is dropped — a refused rung is never re-proposed.",
              file=sys.stderr)
        return None
    return pair


# --------------------------------------------------------------------------
# Provider-aware dispatch (s04, DSP-02). THE CORE CONSTRAINT: the Task/Agent
# tool only accepts Claude model tokens (_MODEL_TOKENS). A Codex-backed session
# therefore NEVER rides Task's `model` param — it dispatches a codex-WRAPPER
# agent: a fixed Claude wrapper model for Task, plus the concrete Codex model
# embedded in a Bash `codex exec -m <model>` command (two-layer dispatch, shape
# ported from Gearbox resolve_route.py). Tier→model translation reads the SSOT
# (`providers.<name>` via scripts/resolve_route.py) at dispatch time; only the
# degradation ladders above are code-authoritative statics.
# --------------------------------------------------------------------------
_SSOT_ENV = "PLAN_EXECUTE_ROUTING_SSOT"  # test override; default = ~/.claude/model-routing.yaml
_CODEX_DECLARED_RE = re.compile(r"gpt|codex", re.IGNORECASE)


def _declared_codex(raw_model):
    """True when the session's `model` field is an explicit Codex-family pin.

    One definition, three call sites (plus escalation's lane check) — this test
    used to be copy-pasted, and a copy that drifts decides a session's BACKEND."""
    return isinstance(raw_model, str) and bool(_CODEX_DECLARED_RE.search(raw_model))


# Fixed Claude model for the wrapper Task call (fan-out default per the SSOT's
# fanout_policy — never Fable). The Codex model rides the Bash command, not Task.
_CODEX_WRAPPER_MODEL = "sonnet"
# Manifest `reasoning` (Anthropic effort vocabulary) → the provider-neutral
# intent axis (`task_classes` vocabulary, renamed from `task_classes_v2` at s03
# when the legacy flat block retired); each provider's `effort.map`
# translates intent → its native dial. Mirrors providers.anthropic.effort.map
# reversed (low→light, medium→standard, high→thorough).
#
# UNCLAMPED at CL-03 (2026-07-28). This map used to send BOTH `xhigh` and `max`
# to `thorough`, so a session that asked for maximum thinking silently landed on
# whatever the tier's `thorough` cell held — `gpt-5.6-sol @ xhigh`, never `max`.
# That was a clamp invented HERE, not provider policy: `codex debug models` on
# codex-cli 0.145.0 (re-verified 2026-07-28) reports `max` as a real supported
# reasoning level on sol, terra AND luna (`ultra` exists on sol/terra and stays
# forbidden by policy). The intent axis therefore grows two rungs above
# `thorough` so the five manifest tiers map ONE-TO-ONE and every remaining loss
# is the provider's own effort.map talking, where it is visible and calibratable.
# `exhaustive`/`maximal` are ABSTRACT intents (never native dial values) — the
# same naming rule the rest of the axis follows.
#
# SSOT LOCKSTEP: every intent used here must be a key in the tier's
# `providers.<name>.effort.map` cell, because resolve_route's `native_effort()`
# falls back to the cell's `standard` value for an UNKNOWN intent — a missing
# rung would silently DOWNGRADE `max` to the standard effort instead of failing.
# providers.openai carries all five (edited in the same commit); other providers
# must add them before this map may route to them.
_INTENT_FROM_REASONING = {
    "": "standard",
    "low": "light",
    "medium": "standard",
    "high": "thorough",
    "xhigh": "exhaustive",
    "max": "maximal",
}
# Ascending intent order — used ONLY to compute effort fidelity honestly (a rung
# that resolves to the same native effort as a lower rung bought nothing).
_INTENT_ORDER = ("light", "standard", "thorough", "exhaustive", "maximal")


# --------------------------------------------------------------------------
# executor_policy enforcement (s06, EXE-01). The SSOT's `executor_policy:` block
# is POLICY PROSE; THIS is its enforcement point in the dispatch path (the SSOT
# names these functions). All checks FAIL CLOSED:
#   * dial-driven Codex execution (active_provider: openai) requires the session's
#     task_class to be opted into executor_policy.executor_for — otherwise the
#     session dispatches Claude (never Codex);
#   * linchpin / irreversible sessions NEVER auto-dispatch to Codex — dial-driven
#     ones fall back to Claude, explicitly-pinned ones BLOCK loudly (silently
#     rerouting an explicit pin would hide the contradiction);
#   * data_sensitivity_guard egress: a working tree carrying a real secret (or a
#     `.env*` file) is DO-NOT-SEND — the codex command is refused BEFORE
#     construction unless the SSOT carries an unexpired per-repo egress_opt_ins
#     entry for exactly this tree.
# --------------------------------------------------------------------------
_EGRESS_ROOT_ENV = "PLAN_EXECUTE_EGRESS_ROOT"  # test override; default = cwd. The scanned
# root is BOUND into the codex command via `cd <root> &&` so the scanned tree and the
# shipped tree are the same path by construction (adversarial-review 2026-07-10, consensus).
#
# WHAT IS MATCHED (rewritten 2026-07-28 — was filename substrings):
#   1. FILENAME — `.env*`, anywhere in the tree (full walk, symlinks followed).
#      Cheap, near-zero false positives, and a content scanner will NOT catch it:
#      a `.env` full of plain KEY=value trips no secret rule (measured). A `.env`
#      file is a decision, not a guess. Deliberately NOT git-scoped — `.env` is
#      almost always git-ignored, so a tracked-files-only rule would miss it.
#   2. CONTENT — `gitleaks dir` (the same scanner this repo's githooks/pre-commit
#      already runs) over the REVIEWED SURFACE only: git-tracked files plus
#      untracked-but-not-ignored files, minus per-file entries in the SSOT's
#      data_sensitivity_guard.content_scan_allowlist.
# The old `corpus`/`creds`/`credential`/`secret` NAME substrings are gone. They
# were the crying-wolf half: they blocked a repo over an analysis script named
# `migrate_corpus.py` while a live API key in `config.py` sailed straight past,
# because nothing ever read a byte of content. A guard that cries wolf gets
# routed around, and then it protects nothing.
#
# SCOPE (2026-07-28, second pass — the first one shipped rule 2 over the FULL
# tree and was unusable on a real repo). Measured on a 16 GB / 103,727-file plan
# repo: whole-tree = 6 min 5 s and 826 findings, every one of them inside
# git-ignored build output (`dist/` 2.9 GB, `_workspace/` 2.6 GB). Git-scoped =
# 570 files / 6.4 MB / 1.0 s and 1 finding. Build output that git ignores is not
# part of the repo and is not what a reviewer or a `git push` would ship, so it
# is not scanned. `.env` is the one thing that IS shipped-by-upload while being
# git-ignored, which is exactly why rule 1 stays a full-tree walk.
_GITLEAKS_LEAK_EXIT = 7  # distinct from gitleaks' own error exit (1) — a bad path,
# a broken config and "leaks found" all exit 1 under the default, which would make
# a crashed scan indistinguishable from a clean one.
_GITLEAKS_TIMEOUT_S = 300
_GIT_TIMEOUT_S = 60
# gitleaks prints `scanned ~<N> bytes (…) in …` to stderr on every pass. We assert
# it against the byte total of the surface we MEANT to scan. THE TRAP this exists
# for, measured the hard way: `gitleaks dir a b c d` takes ONE path argument — the
# extra three are silently ignored and it scans the CWD tree instead (6 min 40 s
# over 16 GB, vs 1.8 s for the four directories scanned one at a time). A scan
# that silently widens to the whole filesystem is the same defect class as a gate
# that reports green: it looks like it ran, and it did — over the wrong thing.
_SCANNED_BYTES_RE = re.compile(r"scanned ~(\d+) bytes")
# Compiled bytecode is not the reviewed surface. Two of the three findings on the
# real repo above were `__pycache__/*.pyc` copies of ONE Python docstring. Nothing
# a human would read as source is skipped — only build products of source we do
# scan (and in a normal repo git ignores these anyway, so this rarely fires).
_SCAN_SKIP_RE = re.compile(r"(^|/)__pycache__(/|$)|\.pyc$")


def _egress_root():
    return Path(os.environ.get(_EGRESS_ROOT_ENV) or Path.cwd())


def _egress_verdict(ssot_text, root):
    """`{root, restricted_hit, opted_in}` for one tree — the ONE egress scan, shared
    by `begin`'s refusal/verifier stamps and `plan --harness codex`'s turn-one
    disclosure (D4d). `opted_in` is only consulted when there IS a hit."""
    hit = _find_restricted(root, _content_allowlist(ssot_text))
    return {
        "root": str(root),
        "restricted_hit": hit,
        "opted_in": _egress_opt_in(ssot_text, root) if hit else False,
    }


def _is_env_name(name):
    return name.lower().startswith(".env")


def _under(path, root):
    root = str(root)
    return path == root or path.startswith(root + os.sep)


def _walk_tree(real_root):
    """ONE traversal, two products: `(env_hit, extra_roots)`.

    `env_hit` is the first `.env*` path found — matched on the entry name AND on
    any symlink's resolved target, over the FULL tree including git-ignored files,
    traversing directory symlinks (cycle-guarded). `extra_roots` are the resolved
    targets of directory symlinks that land OUTSIDE the tree: gitleaks does NOT
    descend into symlinked directories (measured, v8.30), so without a separate
    pass a `vendored -> ~/private` link would be a content blind spot this walk
    can already see through.

    os.scandir rather than os.walk, and an (st_dev, st_ino) cycle guard rather
    than a per-directory realpath: 2.2 s instead of 4.8 s over the 103k-file repo
    measured above, because realpath lstat()s every path component of every
    directory. The identity pair is also the stronger guard — it catches a cycle
    through a bind mount, which realpath does not."""
    real_root = str(real_root)
    extra_roots, seen, stack = [], set(), [real_root]
    while stack:
        d = stack.pop()
        try:
            st = os.stat(d)
        except OSError:
            continue                       # vanished / unreadable — nothing to inspect
        ident = (st.st_dev, st.st_ino)
        if ident in seen:
            continue                       # already traversed via another link
        seen.add(ident)
        try:
            entries = list(os.scandir(d))
        except OSError:
            continue
        for e in entries:
            if _is_env_name(e.name):
                return f"{e.path} (.env* filename rule)", []
            try:
                is_link = e.is_symlink()
                is_dir = e.is_dir()        # follows the link, which is what we want
            except OSError:
                continue
            if is_link:
                target = os.path.realpath(e.path)
                if any(_is_env_name(seg) for seg in Path(target).parts):
                    return f"{e.path} -> {target} (.env* filename rule)", []
                if is_dir and not _under(target, real_root):
                    extra_roots.append(target)
            if is_dir:
                stack.append(e.path)
    return None, sorted(set(extra_roots))


def _git_candidates(real_root):
    """Repo-relative paths git considers part of `real_root`'s subtree — tracked
    files plus untracked-but-not-ignored files — or None when `real_root` is not
    inside a git work tree (or git is unusable, or the listing fails).

    None means "fall back to the full walk", which is the safe direction: it
    over-scans rather than under-scans. Non-git trees are small in practice; the
    16 GB tree that motivated the scoping is a git repo whose bulk is ignored."""
    git = shutil.which("git")
    if git is None:
        return None
    out = set()
    for extra in ([], ["--others", "--exclude-standard"]):
        try:
            proc = subprocess.run([git, "-C", str(real_root), "ls-files", "-z"] + extra,
                                  capture_output=True, text=True, timeout=_GIT_TIMEOUT_S)
        except (OSError, subprocess.SubprocessError):
            return None
        if proc.returncode != 0:           # not a work tree (128), or a broken index
            return None
        out.update(p for p in proc.stdout.split("\0") if p)
    return out


def _scoped_link_tree(real_root, stack):
    """`(link_root, expected_bytes, submodule_roots)` — a throwaway directory of
    symlinks mirroring the git-tracked + untracked-not-ignored files under
    `real_root`, or None when the tree is not git-scoped.

    Why a link tree and not something cleverer: `gitleaks dir` takes exactly ONE
    path, so a 570-path candidate set is either 570 processes (~30 s of process
    startup) or one process over one directory. Symlinks cost 0.19 s to lay down
    for that set, `--follow-symlinks` makes gitleaks read the targets, and — the
    part that makes this work at all — gitleaks reports the RESOLVED target in
    `File`, so a finding names the real repo path with no mapping back (probed,
    v8.30.0). `stack` is an ExitStack; the tree is removed when it unwinds."""
    rels = _git_candidates(real_root)
    if rels is None:
        return None
    link_root = stack.enter_context(tempfile.TemporaryDirectory(prefix="plan-egress-scan-"))
    expected, submodules = 0, []
    for rel in sorted(rels):
        if _SCAN_SKIP_RE.search(rel):
            continue
        src = os.path.join(str(real_root), rel)
        try:
            st = os.stat(src)              # follows links: a tracked symlink is
        except OSError:                    # scanned as whatever it points at
            continue                       # tracked-but-deleted, or dangling
        if statmod.S_ISDIR(st.st_mode):
            # A gitlink (submodule): `git ls-files` names the directory, not the
            # files inside it, and the inner repo has its own index. Hand it a
            # full pass of its own rather than leave a hole in the scan.
            submodules.append(src)
            continue
        if not statmod.S_ISREG(st.st_mode):
            continue                       # fifo/socket/device — nothing to scan
        dst = os.path.join(link_root, rel)
        try:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            os.symlink(src, dst)
        except OSError:
            return None                    # can't build it — fall back to the full walk
        expected += st.st_size
    return link_root, expected, submodules


def _gitleaks_fingerprints(real_root):
    """Fingerprints pinned in the scanned tree's own `.gitleaksignore`, in the
    form `githooks/pre-commit` produces them: `<repo-relative path>:<rule>:<line>`.

    We match these OURSELVES because gitleaks' `-i` structurally cannot, here. It
    compares its own fingerprints, which are built from the path it was handed —
    and we hand it ABSOLUTE paths, so it produces absolute fingerprints that can
    never equal the relative ones `gitleaks protect --staged` writes. Measured on
    this repo 2026-07-28: scanning `.` honours all 16 pinned false positives and
    reports 0 findings; scanning the identical tree by absolute path honours 0 and
    reports all 16. That silently made every repo carrying a `.gitleaksignore`
    permanently DO-NOT-SEND — including this one — while both the SSOT and the
    contract stated the file was honoured. `-i` stays passed for the case where
    the path forms do line up; this is the belt that actually fits."""
    try:
        text = (Path(real_root) / ".gitleaksignore").read_text(encoding="utf-8")
    except OSError:
        return frozenset()
    return frozenset(
        ln.strip() for ln in text.splitlines()
        if ln.strip() and not ln.lstrip().startswith("#")
    )


def _gitleaks_pass(exe, target, ignore_root, expected_bytes, link_root, allow,
                   pinned=frozenset()):
    """One `gitleaks dir` process. Returns a display string for the first
    non-allowlisted finding (or for any degraded outcome — see `_content_scan`),
    or None when the target is clean."""
    cmd = [
        exe, "dir", target,               # ONE path argument. See _SCANNED_BYTES_RE.
        "--follow-symlinks",              # file symlinks; dir symlinks come in as extra roots
        "--no-banner", "--redact",
        "--report-format", "json", "--report-path", "-",
        # Honor the SCANNED tree's own .gitleaksignore (not the orchestrator's
        # cwd, which gitleaks defaults to) — same false-positive workflow the
        # pre-commit hook documents. The SSOT allowlist below is the auditable,
        # EXPIRING key; this one is the in-repo, permanent one.
        "--gitleaks-ignore-path", str(ignore_root),
        "--exit-code", str(_GITLEAKS_LEAK_EXIT),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=_GITLEAKS_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired) as e:
        return (f"gitleaks could not scan {target} ({type(e).__name__}) — this tree "
                "cannot be cleared for egress (fail-closed)")
    if proc.returncode not in (0, _GITLEAKS_LEAK_EXIT):
        tail = (proc.stderr or "").strip().splitlines()[-1:] or ["(no stderr)"]
        return (f"gitleaks failed on {target} (exit {proc.returncode}: {tail[0]}) — "
                "this tree cannot be cleared for egress (fail-closed)")
    if expected_bytes is not None:
        # SCOPE ASSERTION — the scan must have read the surface we selected and
        # not silently widened past it. Fails closed both ways: no byte line at
        # all (a gitleaks output-format change) is as unclearable as an overshoot.
        m = _SCANNED_BYTES_RE.search(proc.stderr or "")
        if m is None:
            return ("gitleaks did not report a scanned-byte count, so the scan SCOPE "
                    "cannot be confirmed — this tree cannot be cleared for egress "
                    "(fail-closed; check the gitleaks version, tested against 8.30.0)")
        scanned = int(m.group(1))
        # Generous bound on purpose: the failure it must catch is a scan that fell
        # back to the whole tree (16 GB vs 6.4 MB — three orders of magnitude), not
        # a few KB of gitleaks' own base64/archive re-decoding.
        if scanned > 2 * expected_bytes + (1 << 20):
            return (f"gitleaks scanned {scanned} bytes for a {expected_bytes}-byte "
                    f"candidate set — the scan WIDENED beyond the reviewed surface, so "
                    f"this tree cannot be cleared for egress (fail-closed)")
    try:
        findings = json.loads(proc.stdout or "[]")
    except ValueError:
        return (f"gitleaks produced unparseable output for {target} — this tree "
                "cannot be cleared for egress (fail-closed)")
    for f in findings:
        path = f.get("File") or target
        alias = f.get("SymlinkFile") or ""
        # `File` is the resolved target, `SymlinkFile` the link that reached it.
        # Name whichever the operator can act on: an IN-TREE symlink (`link.txt ->
        # ~/keys.txt`) is the thing they'd delete; a link inside our own scratch
        # tree is an implementation detail they should never see.
        if alias and not (link_root and _under(alias, link_root)):
            path = alias
        if os.path.realpath(path).lower() in allow:
            continue
        if pinned and _pins_finding(pinned, ignore_root, f, path):
            continue
        return (f"{path} (gitleaks rule {f.get('RuleID', '?')}, "
                f"line {f.get('StartLine', '?')})")
    return None


def _pins_finding(pinned, real_root, finding, display_path):
    """True iff `.gitleaksignore` carries this finding's repo-relative
    fingerprint. Both the resolved file and the link that reached it are tried,
    since either may be the in-repo path the commit hook would have pinned."""
    tail = f":{finding.get('RuleID', '?')}:{finding.get('StartLine', '?')}"
    for p in {finding.get("File") or "", finding.get("SymlinkFile") or "",
              display_path}:
        if not p or not _under(os.path.realpath(p), str(real_root)):
            continue
        if os.path.relpath(os.path.realpath(p), str(real_root)) + tail in pinned:
            return True
    return False


def _content_scan(real_root, extra_roots, allow):
    """First non-allowlisted gitleaks finding on the REVIEWED SURFACE of
    `real_root` (and under any out-of-tree symlinked directory), as a display
    string naming the offending path; None if the content is clean.

    The reviewed surface is the git-scoped candidate set — tracked plus
    untracked-not-ignored — scanned through a throwaway symlink tree. When
    `real_root` is not a git work tree there is no ignore information to use, so
    the whole tree is scanned exactly as before.

    FAILS CLOSED in every degraded case — a missing `gitleaks`, a scan crash, a
    timeout, unparseable output, a scanned-byte count that does not match the
    surface we selected — by returning a hit that names the problem. An unrunnable
    or mis-scoped scanner must never read as "nothing found"; the only way past is
    the operator's own unexpired egress_opt_ins entry for this tree.

    Findings are `--redact`ed: the refusal names the file, rule and line, never
    the secret itself (the message lands in run.ndjson and the orchestrator's
    transcript)."""
    exe = shutil.which("gitleaks")
    if exe is None:
        return (
            "gitleaks is NOT on PATH — the data_sensitivity_guard content scan cannot "
            "run, so this tree cannot be cleared for egress (fail-closed; there is no "
            "fallback scanner by design). Install it: `brew install gitleaks`"
        )
    with contextlib.ExitStack() as stack:
        scoped = _scoped_link_tree(real_root, stack)
        if scoped is None:
            # (target, expected_bytes, link_root) — no ignore data, scan it all.
            passes = [(str(real_root), None, None)]
        else:
            link_root, expected, submodules = scoped
            passes = [(link_root, expected, link_root)]
            extra_roots = list(extra_roots) + submodules
        passes += [(t, None, None) for t in extra_roots]
        pinned = _gitleaks_fingerprints(real_root)
        for target, expected, link_root in passes:
            hit = _gitleaks_pass(exe, target, real_root, expected, link_root, allow,
                                 pinned)
            if hit:
                return hit
    return None


def _find_restricted(root, allow=frozenset()):
    """First restricted path under `root`, or None — the filename rule first
    (it rides the full-tree walk that also finds out-of-tree symlinked
    directories), then the content scan over the git-scoped surface. `allow` is
    the SSOT's per-file content allowlist; it never clears a `.env*`, which only a
    whole-repo egress_opt_ins entry can.

    Measured 2026-07-28 on the 16 GB / 103,727-file plan repo, gitleaks 8.30.0:
    2.2 s walk + 1.0 s scan ≈ 3.4 s, against 6 min 5 s before the scoping.

    ponytail: NOT cached across processes, deliberately. The walk is 2/3 of the
    cost and has to run every time anyway (rule 1 is the whole tree), so a cache
    could only save the ~1 s gitleaks pass — and the only honest key for it is
    (path, size, mtime) over the same candidate set the scan already stats. A
    stale-cache false PASS in a DO-NOT-SEND gate costs more than a second."""
    real_root = Path(os.path.realpath(root))
    env_hit, extra_roots = _walk_tree(real_root)
    return env_hit or _content_scan(real_root, extra_roots, allow)


def _ssot_block(ssot_text, key):
    """Comment-stripped body of the YAML block under the first non-commented
    `key:` line (the following lines indented deeper than the key line). The
    regex matchers below run ONLY inside their owning block, so commented-out
    or unrelated-block text can never manufacture policy (adversarial-review
    2026-07-10, Codex HIGH: a commented-out egress opt-in previously matched)."""
    lines = (ssot_text or "").splitlines()
    out, key_indent = [], None
    for ln in lines:
        stripped = ln.strip()
        if key_indent is None:
            m = re.match(rf"^(\s*){re.escape(key)}:", ln)
            if m and not stripped.startswith("#"):
                key_indent = len(m.group(1))
            continue
        if stripped and not stripped.startswith("#"):
            if len(ln) - len(ln.lstrip()) <= key_indent:
                break
            out.append(ln)
    return "\n".join(out)


# Inline-mapping entries ONLY ({ <path_key>: ..., expiry: ... } on one line) —
# block-style entries deliberately DON'T parse (fails closed; the SSOT documents
# the required shape beside each block). `(?<!\w)` keeps the `path:` matcher off
# `repo_path:`, so the two blocks can never read each other's entries.
def _inline_entry_re(path_key):
    return re.compile(
        rf"-\s*\{{[^}}]*(?<!\w){path_key}:\s*\"?([^\",}}]+)\"?[^}}]*"
        rf"expiry:\s*\"?(\d{{4}}-\d{{2}}-\d{{2}})\"?[^}}]*\}}"
    )


_OPT_IN_RE = _inline_entry_re("repo_path")
_ALLOWLIST_RE = _inline_entry_re("path")


def _content_allowlist(ssot_text):
    """Realpath'd, lowercased set of FILES with an UNEXPIRED entry in
    data_sensitivity_guard.content_scan_allowlist — a reviewed finding that no
    longer has to force a whole-repo egress opt-in.

    Same shape and the same fail-closed parsing as egress_opt_ins: inline
    mappings only, parsed from inside that block with comments stripped, so a
    block-style, commented-out or out-of-block entry never allows anything. It
    only ever silences a CONTENT finding — a `.env*` file is not allowlistable."""
    today = time.strftime("%Y-%m-%d")
    return {
        os.path.realpath(os.path.expanduser(m.group(1).strip())).lower()
        for m in _ALLOWLIST_RE.finditer(_ssot_block(ssot_text, "content_scan_allowlist"))
        if m.group(2) >= today
    }


def _egress_opt_in(ssot_text, root):
    """True iff data_sensitivity_guard.egress_opt_ins (that block ONLY, comments
    stripped) carries an UNEXPIRED entry whose repo_path realpath-matches `root`
    (case-insensitive). No env-var bypass exists by design — the SSOT entry is
    the only key."""
    block = _ssot_block(ssot_text, "egress_opt_ins")
    if not block:
        return False
    real_root = os.path.realpath(str(root)).lower()
    today = time.strftime("%Y-%m-%d")
    for m in _OPT_IN_RE.finditer(block):
        repo_path, expiry = m.group(1).strip(), m.group(2)
        if os.path.realpath(os.path.expanduser(repo_path)).lower() == real_root and expiry >= today:
            return True
    return False


_EXECUTOR_FOR_RE = re.compile(r"^\s*executor_for:\s*\[([^\]]*)\]", re.M)


def _executor_for(ssot_text):
    """Set of task_class names opted into dial-driven Codex execution — parsed
    ONLY from inside the `executor_policy:` block (comments stripped). Absent
    block / empty or unparseable list = opted into NOTHING (fail closed)."""
    m = _EXECUTOR_FOR_RE.search(_ssot_block(ssot_text, "executor_policy"))
    if not m:
        return frozenset()
    return frozenset(t.strip().lower() for t in m.group(1).split(",") if t.strip())


def _session_barred(session):
    """Reason string when this session is barred from unsupervised Codex
    execution (linchpin class or irreversible work), else None.
    security_sensitive is deliberately NOT barred (SSOT executor_policy)."""
    if (session.get("task_class") or "").strip().lower() == "linchpin":
        return "task_class is linchpin"
    if "irreversible_change" in (session.get("peer_triggers") or []):
        return "peer_triggers declares irreversible_change"
    if (session.get("dispatch") or {}).get("guards_irreversible"):
        return "dispatch.guards_irreversible is set"
    return None


class UnroutableCodexSession(Exception):
    """A session declared/required to run on Codex whose Codex dispatch cannot be
    constructed. HARD-FAIL INVARIANT (s04 edge-case #2): this must surface as a
    loud BLOCKED, never fall through to the silent None→inherit-Claude path —
    a mis-routed Codex session quietly executing on Claude is the exact opposite
    of the feature."""


def _routing_ssot_path():
    override = os.environ.get(_SSOT_ENV)
    if override:
        return Path(override)
    # run.py lives at ~/.claude/skills/plan-execute/scripts/run.py → parents[3] = ~/.claude
    return Path(__file__).resolve().parents[3] / "model-routing.yaml"


def _load_routing():
    """(active_provider, ssot_text) — best-effort. (None, None) when the SSOT is
    unreadable: the anthropic default path must keep working without it (backward
    compat is mandatory); only a Codex-declared session hard-fails on it."""
    try:
        text = _routing_ssot_path().read_text(encoding="utf-8")
    except OSError:
        return None, None
    m = re.search(r"^active_provider:\s*([\w-]+)", text, re.M)
    return (m.group(1) if m else None), text


def _import_resolver():
    """Import scripts/resolve_route.py (the vendored provider-neutral resolver)."""
    scripts_dir = str(Path(__file__).resolve().parents[3] / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    import resolve_route  # noqa: PLC0415 — lazy: only Codex-backed dispatch needs it

    return resolve_route


def _apply_attempt(plan_dir, session_id):
    """1-based count of how many times THIS session has already been through
    `cmd_apply`'s `batch_completed` marker — i.e. the real attempt number for
    the resolution about to be recorded.

    TEL-01 finding 3: `apply_terminal` and `administrative_retire` used to
    hard-code `attempt=1` for every write, so a session re-applied within the
    SAME escalation generation (no intervening `redispatch`/`amend`, e.g. an
    orchestrator retry after a malformed first closeout, or a corrected
    resubmission) produced an IDENTICAL `record_id`
    (project, plan, session, generation, attempt, resolution) to its own prior
    write — `_append_dedup` then silently treats the second, genuinely
    different resolution as a duplicate and drops it, corrupting
    attempts-per-success. `batch_completed` is logged once per `cmd_apply`
    call regardless of result (DONE/PARTIAL/BLOCKED), so counting PRIOR
    occurrences for this session and adding 1 gives the real ordinal —
    monotonically increasing within a generation exactly like verify.py's
    `rework_count`-derived `attempt`, and reset to 1 by the generation bump a
    real redispatch always performs (mirroring how `esca.session_state`'s
    `generation` already resets that trail)."""
    try:
        lines = (Path(plan_dir) / "run.ndjson").read_text().splitlines()
    except OSError:
        return 1
    # PER-GENERATION, as the docstring above promises. Counting across ALL
    # generations was the defect: the ordinal then kept climbing through a
    # redispatch instead of resetting, and disagreed with verify.py's
    # rework_count-derived attempt — two yardsticks in one metric. An
    # `escalation_reset` event is what bumps the generation (escalation.py's
    # reset_ladder), so only `batch_completed` events AFTER the last reset for
    # this session belong to the current cohort.
    prior = 0
    for ln in lines:
        try:
            rec = json.loads(ln)
        except ValueError:
            continue
        if session_id not in (rec.get("session_ids") or []):
            continue
        if rec.get("event") == "escalation_reset":
            prior = 0          # new generation, new cohort: the count restarts
        elif rec.get("event") == "batch_completed":
            prior += 1
    return prior + 1


def _last_dispatch_harness(plan_dir, session_id):
    """Which harness this session's MOST RECENT `begin` ran under ("claude" /
    "codex"), or None when it has never been dispatched. Read-only.

    `dispatch_started` is written by every `begin` on both harnesses and carries
    `harness: "codex"` only on the codex one, so the newest such event naming this
    session is the whole answer — and, unlike a one-way `codex_dispatch` marker,
    it moves back when the session is next run on Claude."""
    try:
        lines = (Path(plan_dir) / "run.ndjson").read_text().splitlines()
    except OSError:
        return None
    seen = None
    for ln in lines:
        try:
            rec = json.loads(ln)
        except ValueError:
            continue
        if rec.get("event") == "dispatch_started" and session_id in (rec.get("session_ids") or []):
            seen = rec.get("harness") or "claude"
    return seen


def _dispatch_lane(session, provider, ssot_text, harness="claude", plan_dir=None):
    """"claude" or "codex" — the lane `cmd_begin`'s spec pre-pass will pick.

    The same rules that pre-pass applies, in the same order, so a caller that has
    not reached the pre-pass yet (verify.py, announcing a climb in the rework
    feedback file) reads the answer it will get.

    `plan_dir` is how a caller that does not know the harness recovers it: under
    `--harness codex` EVERY session takes the codex branch (the dial and the
    executor_policy opt-in are both waived), and every `begin` writes a
    `dispatch_started` event stamped with the harness it ran under. The LAST such
    event for this session is the answer — a statement about the last dispatch,
    not a promise about the next, but declining to announce a climb is the safe
    direction and announcing one the codex branch will never bind is not.

    LAST, not ANY. Keying off the presence of a `codex_dispatch` event pinned the
    lane to `codex` FOREVER once a session had run under that harness even one
    time: run.ndjson is append-only and no redispatch clears it, so a session
    later run on the Claude harness silently stopped being told about a climb that
    `cmd_begin` was in fact applying."""
    if harness == "codex":
        return "codex"
    if plan_dir is not None and _last_dispatch_harness(plan_dir, session["id"]) == "codex":
        return "codex"
    if _declared_codex(session.get("model")):
        return "codex"
    if provider == "anthropic":
        return "claude"
    if provider != "openai":
        # No wrapper exists; the pre-pass blocks this as unroutable. Not a climb
        # either way — call it codex so nothing announces one.
        return "codex"
    # executor_policy: dial-driven Codex execution is OPT-IN per task_class and
    # never for barred work, and an ineligible session FAILS CLOSED to Claude —
    # where the climb does bind.
    task_class = (session.get("task_class") or "").strip().lower()
    if _session_barred(session) or not (task_class and task_class in _executor_for(ssot_text)):
        return "claude"
    return "codex"


# The ladder a climb walks is THE LANE'S provider profile — NOT the run-level
# `active_provider` dial. The two differ exactly when the dial is `openai` and a
# session fails closed to Claude execution, and feeding the openai profile a
# `sonnet` cell makes the resolver raise ("current model_id 'sonnet' is not in
# provider 'openai' models", measured 2026-08-14). That raise was caught and
# degraded to "no climb", so under an openai dial the rework loop re-dispatched the
# SAME rung forever with nothing in the log to say why.
#
# ESC-03 (s04): the codex lane is now wired too, onto `providers.openai` — the SAME
# `resolve_route.escalate()` funnel, never a second table. Its climb starts from
# the RESOLVED (codex_model, codex_effort) pair, which is why the caller passes the
# cell in rather than this module deriving it from the manifest.
_ESCALATION_PROVIDER = {"claude": "anthropic", "codex": "openai"}


def _escalation_declined(session, lane="claude"):
    """Why a computed climb will NOT BIND for this session — or None.

    ONE predicate, two consumers, deliberately: `cmd_begin` ACTS on it, and
    verify.py's rework path ANNOUNCES the climb in the feedback file the next
    attempt reads. Two copies would drift, and the drift IS the defect — a
    subagent told it was escalated while it ran on exactly the tier it did last
    time is worse than one that was never told anything.

    LANE-SCOPED, because the one reason a climb cannot bind is Claude-specific: on
    the codex lane a `dispatch.subagent_type` is a Claude AGENT DEFINITION that
    cannot apply at all (it is dropped with a named receipt — see
    `_codex_harness_spec`), and effort rides `-c model_reasoning_effort=` instead.
    Declining a codex climb because of it would refuse the climb over a field that
    is not the effort mechanism there."""
    if lane != "claude":
        return None
    agent = (session.get("dispatch") or {}).get("subagent_type")
    if agent and not str(agent).startswith("tier-"):
        # A FUNCTIONAL agent brings its own tools and instructions; its frontmatter
        # governs model+effort. Swapping it for a tier agent to make the climb bind
        # would silently discard what the author asked for.
        return "declared_subagent_type"
    return None


def _escalation_descriptor(plan_dir, manifest, session, model_arg, reason_tier,
                           steps=None, lane="claude"):
    """ESC-02/ESC-03 — this session's computed rung, or None when it does not escalate.

    `model_arg`/`reason_tier` are THE CELL THE CLIMB STARTS FROM, and which cell
    that is depends on the lane: the normalized Claude token + reasoning tier on
    the `claude` lane, the RESOLVED `(codex_model, codex_effort)` pair on the
    `codex` one. The caller resolves it, because on the codex lane the manifest's
    own `model` may be a Claude token that translation turned into a gpt-5.6 model
    — and walking the openai ladder from `sonnet` raises rather than resolves.

    `steps` overrides the derived climb. Only verify.py's HALT path passes it, to
    name the rung the attempt that just failed actually ran on. Dispatch never
    overrides.

    Returns None — no climb, nothing announced — for a session whose authored cell
    is not a ladder cell, or on a lane with no ladder. A climb that cannot BIND for
    any other reason is flattened HERE to rung 0 carrying `declined` +
    `would_have`, so every consumer sees the same answer: `rung > 0` means "this
    really is the cell that will be dispatched", for the announcer as much as for
    the dispatcher.

    Thin adapter otherwise: `escalation.compute` owns the arithmetic and
    `resolve_route.escalate` owns every rung. Kept BEST-EFFORT on the resolver
    import so a box without a readable SSOT dispatches exactly as it did before
    the feature existed — escalation is an optimisation of a failing rework, and
    losing it must never take the dispatch down with it."""
    provider = _ESCALATION_PROVIDER.get(lane)
    if not esca.enabled(manifest, session) or provider is None:
        return None
    if not esca.dispatchable_cell(model_arg, reason_tier):
        # Said out loud, but ONLY once the failure history actually asked for a
        # climb — otherwise every first dispatch of every unpinned session prints it.
        if esca.climb_steps(plan_dir, session["id"]) > 0:
            print(
                f"escalation: session {session['id']} would climb, but its authored cell "
                f"({model_arg or 'inherit'}@{reason_tier or 'unset'}) is not a ladder cell — "
                "a session needs BOTH `model` and `reasoning` to have a rung to climb from. "
                "Dispatching as authored.",
                file=sys.stderr,
            )
        return None
    try:
        rr = _import_resolver()
    except Exception as e:  # noqa: BLE001 — any import gap degrades to no-climb
        print(f"escalation: resolver unavailable ({e}) — dispatching as authored.",
              file=sys.stderr)
        return None
    try:
        desc = esca.compute(
            plan_dir, manifest, session, provider, rr,
            authored_model=model_arg, authored_reasoning=reason_tier,
            task_class=(session.get("task_class") or None),
            ssot_path=str(_routing_ssot_path()),
            steps=steps,
        )
    except Exception as e:  # noqa: BLE001 — a malformed ladder must not block work
        print(f"escalation: could not compute a rung for {session['id']} ({e}) — "
              "dispatching as authored.", file=sys.stderr)
        return None
    if desc and desc["rung"] > 0:
        why = _escalation_declined(session, lane)
        if why:
            return {**desc, "rung": 0, "ran": dict(desc["authored"]),
                    "declined": why, "would_have": desc["ran"]}
    return desc


def _codex_climb(plan_dir, manifest, session, model, effort):
    """ESC-03 — the codex-lane climb, applied to a RESOLVED codex cell.

    Returns `(model, effort, descriptor)`: the escalated pair when the failure
    history bought a rung, the pair it was handed otherwise. The descriptor rides
    along so the dispatcher can stamp `escalated_from` and record where the
    dispatch settled — the same two things the Claude lane does with it.

    ONE call site's worth of logic in a function because there are TWO codex
    dispatch paths (the `--harness codex` spec builder and the Claude-wrapper
    branch of `cmd_begin`) and a climb wired into only one of them is the defect
    this plan exists to close, one lane down."""
    desc = _escalation_descriptor(plan_dir, manifest, session, model, effort,
                                  lane="codex")
    if desc and desc["rung"] > 0:
        # Only `-m` and `-c model_reasoning_effort=` move: the caller re-derives the
        # command from this pair through the same `_codex_cmd`, so every other flag
        # stays byte-identical by construction rather than by inspection.
        return desc["ran"]["model"], desc["ran"]["reasoning"], desc
    return model, effort, desc


def _refused(desc):
    """The rungs this session has had refused at dispatch, from the escalation
    descriptor that already carries them. Empty when the session does not escalate
    at all (version gate / opt-out) — which is also the only honest answer there:
    no rung of such a session can have been recorded refused, since
    `record-refusal` refuses to record one."""
    return (desc or {}).get("refused") or ()


def _record_escalation(plan_dir, sid, desc, member):
    """Record where one dispatch settled on the ladder, and stamp the member with
    the honesty pair. THE one place both lanes do this — Claude and codex members
    carry identical `escalated_from`/`model_ran`/`model_ran_source` fields, and the
    same `escalation_applied` event, because they go through this function.

    `model_ran_source` is deliberately "requested", never "attested": nothing here
    proves which model actually served the dispatch — a blocked override can
    silently inherit another model with no error at all, and a dispatch-audit
    cross-check cannot catch it either (that hook records the REQUESTED
    configuration too). The attestation handoff is s05's."""
    if not desc:
        return
    # OBSERVATION of where this dispatch settled. BOTH numbers: the rung that BOUND
    # (what the BLOCKED brief must name if this attempt is the last one) and the
    # ladder position ASKED FOR (the base of the next climb). They differ when a
    # refused rung forced a step-down, and collapsing them capped the ladder at the
    # refused rung forever.
    esca.record_dispatch(plan_dir, sid, desc["rung"], climb=desc["climb_requested"])
    if desc["rung"] <= 0:
        return
    member["escalated_from"] = {
        "authored": desc["authored"],
        "ran": desc["ran"],
        "attempt": desc["attempt"],
        "rung": desc["rung"],
        "generation": desc["generation"],
    }
    member["model_ran"] = desc["ran"]["model"]
    member["reasoning_ran"] = desc["ran"]["reasoning"]
    member["model_ran_source"] = desc["model_ran_source"]
    rsi.log_event(
        plan_dir, "escalation_applied", session_ids=[sid],
        authored=desc["authored"], ran=desc["ran"],
        rung=desc["rung"], climb_requested=desc["climb_requested"],
        refused_skipped=desc["refused_skipped"],
        ladder_exhausted=desc["ladder_exhausted"],
        remaining=desc["remaining"], generation=desc["generation"],
        model_ran_source=desc["model_ran_source"],
    )
    print(
        f"escalation: session {sid} climbs to rung {desc['rung']} — "
        f"{desc['authored']['model']}@{desc['authored']['reasoning'] or 'unset'} -> "
        f"{desc['ran']['model']}@{desc['ran']['reasoning'] or 'unset'} "
        "(requested, not attested)",
        file=sys.stderr,
    )


def _refusal_instruction(sid, model, reasoning):
    """What to do INSTEAD of degrading when an ESCALATED dispatch is refused.

    Two instructions would otherwise fire on the SAME observed event — "degrade to
    fallback_model" and "report it with record-refusal" — and the older one wins by
    seniority, which is the wrong answer twice over: the downward ladder from an
    escalated cell lands AT OR BELOW the authored tier (the floor invariant forbids
    exactly that), and skipping `record-refusal` leaves the refused rung in the
    ladder, so the next rework re-proposes it and the whole max_rework budget burns
    on a model that will never run. The caller makes `fallback_model` null so the
    choice is removed rather than ranked."""
    return (
        "record-refusal — do NOT degrade. This member runs on an ESCALATED rung, "
        "and the downward fallback ladder from it ends at or below the tier the "
        "plan author chose. Run `plan-execute record-refusal <PLAN_DIR> --session "
        f"{sid} --model {model} --reasoning {reasoning} "
        "--reason \"<what you observed>\"`, then re-run `plan`: the session comes "
        "back on the PREVIOUS rung, the refusal costs no rework budget, and that "
        "rung is never proposed again."
    )


def _resolve_codex_dispatch(raw_model, reasoning, ssot_text, require_calibrated=False,
                            task_class=None):
    """Resolve a Codex-backed session to ``(codex_model, codex_effort)`` against
    `providers.openai` in the SSOT. Raises UnroutableCodexSession on ANY gap —
    never returns a partial/guessed route.

    ``task_class`` drives the DIAL-DRIVEN translation (v1.15): a session that does
    not pin a Codex model resolves through ``resolve_route.resolve(task_class,
    "openai")`` rather than by matching Claude tier NAMES against OpenAI tier
    names — see the comment at the translation branch for the measured reason.
    An explicitly pinned Codex model never consults it.

    ``require_calibrated`` (adversarial-review 2026-07-10, Codex HIGH): a route
    driven by the RUN-LEVEL dial (`active_provider: openai`, translating Claude
    tier vocabulary) must not dispatch through a profile whose
    `calibration.status` isn't `researched` — the SSOT marks openai `lane_scoped`
    (peer-review-only cells, never calibrated as execution defaults). A session
    that EXPLICITLY pins a Codex model is exempt (deliberate per-session opt-in)."""
    if not ssot_text:
        raise UnroutableCodexSession(
            f"model-routing.yaml unreadable at {_routing_ssot_path()} — cannot resolve a Codex model"
        )
    try:
        rr = _import_resolver()
    except Exception as e:
        raise UnroutableCodexSession(f"scripts/resolve_route.py unavailable ({e})")
    try:
        openai = rr._Profile(ssot_text, "openai")
    except Exception as e:
        raise UnroutableCodexSession(f"providers.openai profile unparseable: {e}")
    if require_calibrated:
        try:
            pblock = rr._parse_provider_block(ssot_text, "openai")
            calib = rr._sub_block(pblock, "calibration", 4) or ""
            status = rr._find_scalar(calib, "status")
        except Exception as e:
            raise UnroutableCodexSession(f"providers.openai calibration unreadable: {e}")
        if status != "researched":
            raise UnroutableCodexSession(
                f"active_provider routes to providers.openai whose calibration.status is "
                f"{status!r} (not 'researched') — a lane_scoped/uncalibrated profile may not "
                "serve as the execution default; run /routing-update to calibrate, or pin the "
                "session's Codex model explicitly"
            )

    raw = raw_model.strip() if isinstance(raw_model, str) else ""
    by_lower = {m.lower(): m for m in openai.models.values()}
    model = by_lower.get(raw.lower())
    class_cell = None          # the {model_id, native_effort} the task_class prescribes
    if model is None and raw.lower().startswith("gpt-5.5"):
        # RETIRED-MODEL PIN (v1.15) — an already-built manifest can still carry a
        # `gpt-5.5` pin the operator retired on 2026-08-13. BLOCKED-WITH-GUIDANCE:
        # never silently rerouted onto a 5.6 model (the plan author did not choose
        # it) and never run on 5.5. Silence is the one forbidden outcome.
        #
        # CHECKED BEFORE THE TRANSLATION BRANCH, deliberately (s03): once
        # translation resolves from `task_class`, an explicit `gpt-5.5` pin on a
        # session that also carries a task_class WOULD have resolved to a 5.6 model
        # and dispatched — turning a loud block into exactly the silent reroute the
        # directive forbids. Ordering is the whole guarantee here.
        raise UnroutableCodexSession(
            f"session model {raw_model!r} is RETIRED (operator directive 2026-08-13: this lane is "
            "gpt-5.6-only) and is never silently rerouted. Re-pin the session explicitly with "
            "`plan-execute amend-session <PLAN_DIR> --session <SID> --model <M>`, choosing the 5.6 "
            "replacement for the work: gpt-5.6-luna (mechanical / standard_build), gpt-5.6-terra "
            "(escalated build), gpt-5.6-sol (judgement work — the lane default)."
        )
    if model is None:
        # TRANSLATING A CLAUDE TOKEN (v1.15, s03). TWO independent signals, and
        # the route is the STRONGER of the two:
        #
        #   1. the session's OWN model → providers.anthropic tier → openai.models[tier]
        #   2. (task_class, openai) through resolve_route.resolve
        #
        # WHY NOT EITHER ONE ALONE — both directions were measured (2026-08-14):
        #   * class only: `model: Opus` + `task_class: standard_build` resolved to
        #     gpt-5.6-LUNA, while the SAME session with no task_class resolved to
        #     gpt-5.6-terra. The author's pin was silently downgraded by the
        #     presence of an unrelated field, and terra became unreachable by
        #     translation entirely — no task_class maps to frontier_reasoner.
        #   * model only: `model: Opus` + `task_class: linchpin` resolved to terra,
        #     downgrading the plan's most consequential class off the apex model.
        #
        # Neither signal dominates, so neither may be dropped: the pin and the
        # class are two independent statements of how much capability the work
        # needs, and taking the stronger cannot under-serve either. It is also why
        # tier-name symmetry between the line-ups is never ASSUMED — where it does
        # not exist (since the 5.6-only collapse retired `workhorse`, a SONNET
        # session resolves to nothing at all and used to halt as unroutable) the
        # class simply carries the route on its own, which is the gap the SSOT's
        # "resolve from (task_class, provider)" policy exists to close.
        #
        # The GARBAGE-MODEL GUARD survives, and is scoped to what it is actually
        # for: a session that WROTE a model field which resolves to nothing
        # (`model: "Gemini"`) is an authoring error and must BLOCK loudly rather
        # than quietly route on its task_class as though the field were absent. A
        # session that wrote NO model at all is a different thing entirely — it has
        # exactly one signal, and refusing to use it blocked sessions whose
        # task_class resolves perfectly well.
        token = _normalize_model(raw)
        unrecognised_pin = bool(raw) and token is None
        if not unrecognised_pin:
            candidates = []
            if token is not None:
                try:
                    anthropic = rr._Profile(ssot_text, "anthropic")
                except Exception as e:
                    raise UnroutableCodexSession(f"providers.anthropic profile unparseable: {e}")
                tier = anthropic.tier_of(token)
                if tier and openai.models.get(tier):
                    candidates.append(openai.models[tier])
            if task_class:
                try:
                    # SAME SSOT the rest of this function reads — never the live
                    # file by default, or a test/override run would silently
                    # resolve against a different lineup than it dispatches with.
                    routed = rr.resolve(task_class, "openai",
                                        ssot_path=str(_routing_ssot_path()))
                except Exception as e:  # noqa: BLE001
                    # A class this SSOT does not declare is ONE SIGNAL MISSING, not
                    # a dispatch failure. Nothing validates the `task_class`
                    # vocabulary at build time, so a typo reaches dispatch — and
                    # raising here took the WHOLE `begin` batch down and halted the
                    # plan over a session whose own model token resolved perfectly.
                    # Say it loudly, drop the signal, and let the model route stand;
                    # if the model route is empty too, the block below still fires
                    # and now names the class as a suspect.
                    print(f"WARNING: session task_class {task_class!r} does not resolve under "
                          f"providers.openai ({e}) — routing on the session's model alone. "
                          "The neutral `task_classes:` block owns the class vocabulary; a typo "
                          "here silently loses the class signal.", file=sys.stderr)
                    routed = None
                if isinstance(routed, dict) and routed.get("model_id"):
                    candidates.append(routed["model_id"])
                    class_cell = routed
            if candidates:
                # Ranked in the OPENAI profile's own declared tier order, which the
                # SSOT contracts to be ascending capability.
                model = max(candidates, key=lambda m: openai.tier_rank(openai.tier_of(m)))
    if model is None:
        hint = (
            " — this session declares no `task_class`, and since the lane collapsed to "
            "three 5.6 tiers a Claude tier NAME no longer has a guaranteed counterpart "
            "here (`workhorse` is gone with gpt-5.5). Declare a task_class on the "
            "session so the route resolves from (task_class, openai), or pin a "
            "providers.openai model explicitly."
            if not task_class and _normalize_model(raw) is not None
            else ""
        )
        raise UnroutableCodexSession(
            f"session model {raw_model!r} resolves to neither a providers.openai model "
            f"(known: {sorted(openai.models.values())}) nor a translatable Claude tier "
            f"token{hint}"
        )
    try:
        tier = openai.tier_of(model)
        intent = _INTENT_FROM_REASONING.get(_reasoning_tier(reasoning), "standard")
        effort = openai.native_effort(tier, intent)
    except Exception as e:
        raise UnroutableCodexSession(f"no native effort resolvable for {model!r}: {e}")
    # ...and the EFFORT obeys the same "stronger of both signals" rule as the model.
    # `resolve(task_class, openai)` prescribes a whole CELL, not just a model, and
    # taking only its model_id dropped the effort half: `linchpin` (SSOT row
    # sol·MAX, one-shot irreversible work) dispatched at sol@xhigh, and the row the
    # SSOT actually prescribes was unreachable unless the session happened to pin
    # `reasoning: max` (measured 2026-08-14). Only applies when the class actually
    # named this model — a session routed by its own pin keeps its own effort.
    if class_cell and class_cell.get("model_id") == model:
        effort = _stronger_effort(openai, tier, effort, class_cell.get("native_effort"))
    return model, effort


def _effort_levels(profile, tier):
    """This tier's native effort levels, ASCENDING, in the provider's OWN order.

    Built by walking `effort.map` through the intent axis from the weakest
    reasoning tier to the strongest, so the ordering is the provider's calibration
    rather than a hard-coded one — and it covers every level the map can emit, not
    only the ones the ESCALATION ladder happens to walk. Ranking against the
    escalation ladder alone was the bug: `apex_reasoner`'s ladder is
    [high, xhigh, max], so a session at `reasoning: low` (native `medium`, a real
    level the map emits and the ladder never mentions) was unrankable and the
    comparison silently kept the WEAKER value."""
    levels = []
    for reasoning in ("low", "medium", "high", "xhigh", "max"):
        try:
            lvl = profile.native_effort(tier, _INTENT_FROM_REASONING.get(reasoning, "standard"))
        except Exception:  # noqa: BLE001 — a gap in the map is not fatal to the order
            continue
        if lvl and lvl not in levels:
            levels.append(lvl)
    # The ESCALATION ladder is deliberately NOT merged in. Its rungs carry no
    # position relative to the intent map, so appending them would rank a rung that
    # sits BELOW every mapped level as the strongest one and invert the comparison.
    # A level the map never emits is simply unrankable, and `_stronger_effort`
    # already refuses to let an unrankable value win.
    return levels


def _stronger_effort(profile, tier, a, b):
    """The higher of two native effort levels for `tier`. An UNRANKABLE level never
    wins by default: if exactly one of the two is rankable, that one is taken; if
    neither is, the session's own value (`a`) stands."""
    levels = _effort_levels(profile, tier)
    ra = levels.index(a) if a in levels else None
    rb = levels.index(b) if b in levels else None
    if ra is None:
        return b if rb is not None else a
    if rb is None:
        return a
    return b if rb > ra else a


class UngatedFullAccessSession(UnroutableCodexSession):
    """`codex_shell.sandbox: danger-full-access` on a session no human gates.
    Raised at DISPATCH time as well as build time on purpose: a manifest is a
    file, and the one invariant protecting an unsandboxed agent must not be
    enforceable only by the tool that wrote it. Subclasses
    UnroutableCodexSession so every existing handler treats it as what it is —
    a loud BLOCKED + halt, never a silent fall-through to Claude."""


_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_CODEX_CORE_ENV = ("PATH", "HOME", "TMPDIR")


def _codex_shell_grant(session):
    """Normalise a session's declared `dispatch.codex_shell` into the capability
    grant its `codex exec` dispatch needs — or `None` for today's default.

    WHY THIS EXISTS. `codex exec --sandbox workspace-write` (the only shape this
    runner used to emit) denies three things a real plan session routinely needs,
    all MEASURED on codex-cli 0.145.0, 2026-07-29:

      * a write outside the repo workspace -> `operation not permitted`
      * any network access -> `CODEX_SANDBOX_NETWORK_DISABLED=1`
      * a nested `codex exec` / vendor CLI -> `failed to initialize in-process
        app-server client: Operation not permitted` (contract probe P2)

    A plan whose sessions need those could be launched under `--harness codex`
    and then failed session by session, which is the "portable in name only"
    outcome. So a session DECLARES what it needs and the dispatch grants it:

      writable_roots  ->  -c sandbox_workspace_write.writable_roots=[...]   (measured: grants the write)
      network         ->  -c sandbox_workspace_write.network_access=true    (measured: grants the network)
      sandbox         ->  --sandbox danger-full-access                      (the ONLY thing that grants nested dispatch)

    The first two are measured to survive `--ignore-user-config`, so the
    determinism that flag buys is preserved: the dispatch is still fully
    described by this one command line.

    Absent, empty, or all-default -> returns None and the emitted command stays
    BYTE-IDENTICAL to the pre-2026-07-29 form (asserted by
    test_codex_shell_default_is_byte_identical)."""
    shell = ((session.get("dispatch") or {}).get("codex_shell")) or {}
    if not isinstance(shell, dict) or not shell:
        return None
    mode = (shell.get("sandbox") or "workspace-write").strip()
    roots = [
        str(Path(r).expanduser().resolve())
        for r in (shell.get("writable_roots") or [])
        if str(r).strip()
    ]
    network = bool(shell.get("network"))
    raw_env = shell.get("env_include") or []
    if not isinstance(raw_env, list) or any(
        not isinstance(name, str) or not _ENV_NAME_RE.fullmatch(name)
        for name in raw_env
    ):
        raise UnroutableCodexSession(
            "dispatch.codex_shell.env_include must be a list of environment variable names"
        )
    env_include = list(dict.fromkeys(name for name in raw_env if name not in _CODEX_CORE_ENV))
    if mode == "danger-full-access":
        # Full access already grants both, and carrying the narrower keys beside
        # it would leave two readings of one command. build_plan.py refuses the
        # combination; here we simply drop them and say so in the receipt.
        return {"sandbox": mode, "writable_roots": [], "network": True,
                "env_include": env_include, "supersedes": bool(roots) or network}
    if mode != "workspace-write":
        raise UnroutableCodexSession(
            f"dispatch.codex_shell.sandbox {mode!r} is not a codex sandbox mode this "
            "runner emits — use 'workspace-write' (the default) or 'danger-full-access'"
        )
    if not roots and not network and not env_include:
        return None
    return {"sandbox": mode, "writable_roots": roots, "network": network,
            "env_include": env_include, "supersedes": False}


def _assert_full_access_gated(sid, session, grant):
    """An UNSANDBOXED dispatched agent never happens without a human in the loop.

    `danger-full-access` disables the sandbox for every command the dispatched
    session runs. The only sessions allowed to ask for it are ones a human
    already has to clear before dispatch: barred work (`_session_barred` —
    linchpin / `irreversible_change` / `guards_irreversible`), or an explicit
    `requires_human_checkpoint`. Enforced at build time by build_plan.py AND
    here, because a gate enforced in one place is a gate that can be edited
    around (project memory: "gates that cannot fail")."""
    if not grant or grant.get("sandbox") != "danger-full-access":
        return
    if _session_barred(session):
        return
    if (session.get("dispatch") or {}).get("requires_human_checkpoint"):
        return
    raise UngatedFullAccessSession(
        f"{sid} asks for dispatch.codex_shell.sandbox 'danger-full-access' (an "
        "UNSANDBOXED dispatched agent) but no human gates it. Add "
        "dispatch.guards_irreversible: true (or requires_human_checkpoint with its "
        "checkpoint brief), or drop to 'workspace-write' plus the narrower "
        "writable_roots/network grants."
    )


def _codex_cmd(model, effort, prompt_file, last_message_file, workdir=None, grant=None):
    """The exact foreground command the wrapper agent must run. Contract verified
    against codex-cli 0.144.1 (s04 hardening r1):
      * `cd <workdir> &&` prefix (s06, adversarial-review consensus) — pins codex
        exec's working tree to the EXACT root the egress guard scanned, instead of
        inheriting the wrapper agent's incidental cwd; scanned tree == shipped tree
        by construction.
      * `--sandbox workspace-write` EXPLICIT — executor dispatches must edit files;
        `codex exec` defaults to a READ-ONLY sandbox, so omitting the flag makes
        the run silently produce no edits while reporting success.
      * `--ignore-user-config --ignore-rules` — the dispatched model/sandbox is
        fully determined by THIS command, never by a stray $CODEX_HOME/config.toml
        or .rules file.
      * `-o <file>` (--output-last-message) — the LAST agent message lands in a
        file. We deliberately avoid `--json --output-schema`: open codex bug
        #19816 makes it emit schema-valid INTERMEDIATE messages, so first-match
        stdout parsing intermittently returns a wrong early message; `-o` is
        last-message by definition.
      * `- < prompt_file` — the session prompt arrives on stdin, verbatim.
      * `grant` (optional, from `_codex_shell_grant`) — the session's DECLARED
        capability grant. `None` keeps this command byte-identical to the form
        that shipped before 2026-07-29; a grant widens the sandbox exactly as far
        as the session said it needs and no further."""
    eff = f" -c model_reasoning_effort={shlex.quote(effort)}" if effort else ""
    # `rm -f` first (adversarial-review 2026-07-10, consensus HIGH): the wrapper
    # relays whatever the -o file contains, so a stale file from a prior attempt
    # must never survive into this invocation.
    cd = f"cd {shlex.quote(str(workdir))} && " if workdir else ""
    sandbox = (grant or {}).get("sandbox") or "workspace-write"
    caps = ""
    if (grant or {}).get("writable_roots"):
        toml_arr = "[" + ",".join(f'"{r}"' for r in grant["writable_roots"]) + "]"
        caps += " -c " + shlex.quote(f"sandbox_workspace_write.writable_roots={toml_arr}")
    if sandbox == "workspace-write" and (grant or {}).get("network"):
        caps += " -c " + shlex.quote("sandbox_workspace_write.network_access=true")
    if (grant or {}).get("env_include"):
        env_names = [*_CODEX_CORE_ENV, *grant["env_include"]]
        toml_arr = "[" + ",".join(f'"{name}"' for name in env_names) + "]"
        caps += " -c " + shlex.quote("shell_environment_policy.inherit=all")
        caps += " -c " + shlex.quote("shell_environment_policy.ignore_default_excludes=true")
        caps += " -c " + shlex.quote(f"shell_environment_policy.include_only={toml_arr}")
    return (
        f"{cd}rm -f {shlex.quote(last_message_file)} && "
        f"codex exec --ignore-user-config --ignore-rules --sandbox {sandbox}{caps} "
        f"-m {shlex.quote(model)}{eff} "
        f"-o {shlex.quote(last_message_file)} - < {shlex.quote(prompt_file)}"
    )


def _codex_wrapper_prompt(sid, cmd, last_message_file):
    """The full prompt for the Claude wrapper agent (this IS the Task prompt for a
    Codex-backed member). Foreground/fresh contract — deliberately NOT codex:rescue,
    which calls `task`, may go background, and returns nothing on failure."""
    return f"""You are the Codex dispatch wrapper for plan-execute session {sid}. Do NOT do the session's work yourself and do NOT edit any file — your ONLY job is to run the Codex CLI once, in the FOREGROUND, and relay its final message.

1. Run EXACTLY this command via the Bash tool (foreground — never run_in_background; set timeout 600000):

   {cmd}

   Do not alter the -m model, the --sandbox mode, or any other flag. Never use `codex exec resume`.

2. If the command fails BEFORE doing any work — non-zero exit with a model-access error (entitlement/paywall, model not found/unavailable, CLI version rejects the model, quota exhausted) — do NOT retry and do NOT produce a closeout. Your entire final message must be one line:
   CODEX-DISPATCH-FAILED: <signal> — <first error line>
   where <signal> is one of: entitlement | unavailable | cli_version_rejected | quota_exhausted.

3. If the command times out, relay whatever {last_message_file} contains (if anything) and append the single line: CODEX-TIMEOUT.

4. Otherwise read {last_message_file} (Codex's LAST agent message, written via -o — always this file, never an earlier intermediate stdout message) and reply with its full contents VERBATIM as your final message. It should end with the <plan-execute-closeout> block the session prompt requires. NEVER write, repair, or synthesize a closeout yourself: if the file is missing or carries no closeout block, relay what exists and append the single line: CODEX-NO-CLOSEOUT."""


# --------------------------------------------------------------------------
# HARNESS MODE (CP-02, 2026-07-28). One plan directory, two orchestrators — the
# full contract is skills/plan-execute/references/dual-harness-contract.md.
#
#   --harness claude  (default)  the orchestrator is Claude in the main
#                                conversation and dispatches via the Task tool.
#                                This path is BYTE-IDENTICAL to what it was
#                                before this flag existed; the flag is not read.
#   --harness codex              the orchestrator is an interactive `codex`
#                                session that runs each dispatch command in its
#                                own shell and feeds the `-o` file back through
#                                `apply`. EVERY session gets a runnable command:
#                                Codex-pinned ones pass through, Claude-pinned
#                                ones translate through the SSOT with a receipt.
#
# Harness is a property of the RUN, never of the plan: nothing is written to
# manifest.json or PLAN.html, and there is NO environment sniffing (D1 — the one
# marker Codex sets, CODEX_SANDBOX, is present exactly in the mode where this
# loop cannot work, so sniffing it would be backwards).
# --------------------------------------------------------------------------
_HARNESSES = ("claude", "codex")
# NEGATIVE GUARD ONLY, never detection (D1): CODEX_SANDBOX is set inside a
# Seatbelt-sandboxed Codex shell, and a nested `codex exec` provably dies there
# ("failed to initialize in-process app-server client: Operation not permitted",
# contract probe P2 vs P3). Fail closed against a configuration measured broken.
_CODEX_SANDBOX_ENV = "CODEX_SANDBOX"
_CODEX_LAUNCH_RECIPE = "codex --sandbox danger-full-access -C <repo-root>"


def _effort_fidelity(row, intent):
    """How much of the session's declared reasoning tier actually SURVIVES into
    the native dial — computed from the tier's `effort.map` row, never asserted
    (§ 4.3). `exact` is not the default: it has to be earned.

      flat_map  the row maps every intent to one value — the declared tier is
                discarded wholesale (terra and luna are deliberately like this).
      clamped   some LOWER intent resolves to the same native effort, so the
                higher tier bought nothing.
      exact     this intent has a native effort no lower intent shares.

    The house rule (and this repo's dominant defect class) is that a receipt
    which always prints the same reassuring line is a gate that cannot fail."""
    if not row:
        return "unknown"
    if len(set(row.values())) <= 1:
        return "flat_map"
    here = row.get(intent, row.get("standard"))
    for lower in _INTENT_ORDER:
        if lower == intent:
            break
        if lower in row and row[lower] == here:
            return "clamped"
    return "exact"


_FIDELITY_BLURB = {
    "flat_map": (
        "this tier maps EVERY intent to `{effort}`; the session's declared "
        "reasoning tier does NOT survive translation"
    ),
    "clamped": (
        "a lower reasoning tier also resolves to `{effort}` on this tier — the "
        "declared tier buys no extra depth"
    ),
    "exact": "the declared reasoning tier maps to a native effort of its own",
    "raised_by_escalation": (
        "the ESCALATION climb dispatched this session on `{ran}`, ABOVE the cell the SSOT "
        "resolved — so the declared reasoning tier does not describe what ran, and the "
        "tier/effort rows above describe the rung the climb started FROM"
    ),
    "raised_by_class": (
        "the session's task_class row prescribes a HIGHER effort than its declared "
        "reasoning tier maps to, and translation never routes below either signal — "
        "so it runs at `{effort}`, above what the reasoning tier alone would buy"
    ),
    "unknown": "effort.map row unreadable — fidelity could not be computed",
}


def _codex_translation(sid, session, codex_model, codex_effort, ssot_text, dropped, grant=None,
                       escalation=None):
    """The translation receipt for one session under `--harness codex` (D3 § 4.3):
    a machine-readable dict plus the exact stderr text. Every field is derived
    from the SSOT the dispatch actually resolved through — there is no second
    mapping surface to drift.

    `codex_model`/`codex_effort` are the BASE rung — the cell the SSOT resolved,
    BEFORE any escalation climb — and `escalation` is that climb's descriptor.
    That split is load-bearing (ESC-03 rework, 2026-08-14): built from the
    POST-climb pair, this receipt attributed the climb's own raise to whatever
    else was to hand, and persisted the false cause as `effort_fidelity` on the
    batch member and in the `codex_translation` event — an escalated session read
    as `raised_by_class` (a task_class row that prescribes no such thing), and a
    climb that changed the MODEL read as a passthrough of a model the session
    never pinned. A receipt that states the wrong cause confidently is worse than
    no receipt, so the SSOT cell is described as itself and the climb is reported
    as its own explicitly-labelled raise on top."""
    raw_model = session.get("model")
    declared_codex = _declared_codex(raw_model)
    reason_tier = _reasoning_tier(session.get("reasoning"))
    intent = _INTENT_FROM_REASONING.get(reason_tier, "standard")
    tier, row, calibration = None, {}, None
    try:
        rr = _import_resolver()
        openai = rr._Profile(ssot_text, "openai")
        tier = openai.tier_of(codex_model)
        row = openai.effort_map.get(tier) or {}
        pblock = rr._parse_provider_block(ssot_text, "openai")
        calibration = rr._find_scalar(rr._sub_block(pblock, "calibration", 4) or "", "status")
    except Exception:  # pragma: no cover - the same parse already succeeded upstream
        pass
    fidelity = _effort_fidelity(row, intent)
    # `fidelity` describes the effort.map ROW — how much of the declared reasoning
    # tier survives the intent lookup. When the session's task_class RAISED the
    # effort past that lookup, the row's verdict no longer describes what ran: a
    # session dispatched at its tier's top cell was persisted as `clamped` ("the
    # declared tier buys no extra depth") when in fact it bought more. Report the
    # raise as its own fidelity value rather than mislabelling it.
    mapped_effort = row.get(intent) if isinstance(row, dict) else None
    if mapped_effort is not None and mapped_effort != codex_effort:
        fidelity = "raised_by_class"
    # THE CLIMB IS ITS OWN CAUSE. `raised_by_class` above is a claim about the
    # SSOT's task_classes row; an escalated dispatch runs where it runs because the
    # session kept failing, which no row prescribes. Reported last because it is
    # the OUTERMOST cause of the cell that actually ran, and never conflated with
    # the row verdict it would otherwise overwrite silently.
    climbed = (escalation or {}).get("ran") if (escalation or {}).get("rung", 0) > 0 else None
    if climbed:
        fidelity = "raised_by_escalation"
    translated_from = (
        None if declared_codex else {"model": raw_model, "reasoning": reason_tier}
    )
    head = (
        f"harness=codex  passthrough {sid}:  {codex_model} · {codex_effort}"
        "   (session pins a providers.openai model — no tier translation)"
        if declared_codex
        else f"harness=codex  translate {sid}:  {raw_model} · {reason_tier or 'unset'}"
        f"  ->  {codex_model} · {codex_effort}"
    )
    lines = [head]
    if tier:
        origin = (
            "(providers.openai.models)"
            if declared_codex
            # v1.15: translation takes the STRONGER of the session's own model tier
            # and the (task_class, openai) route.
            else "(stronger of: model tier, task_class -> providers.openai.models)"
        )
        lines.append(f"    tier      {tier}   {origin}")
        # HONEST DERIVATION. `codex_effort` is the stronger of the intent-mapped
        # level and the level the task_class row prescribes, so printing it as
        # `effort.map.<tier>.<intent> = <it>` claims a row the SSOT does not
        # contain whenever the class won (e.g. apex_reasoner.standard reads `high`,
        # and the line printed `= xhigh`). Name the row's real value, then the
        # raise, separately.
        mapped = row.get(intent) if isinstance(row, dict) else None
        lines.append(
            f"    effort    {reason_tier or 'unset'} -> intent={intent} -> "
            f"providers.openai.effort.map.{tier}.{intent} = {mapped}"
        )
        if mapped != codex_effort:
            lines.append(
                f"    effort    RAISED to {codex_effort} by the session's task_class "
                f"({(session.get('task_class') or '').strip().lower() or 'unset'}), whose "
                "own task_classes row prescribes it — translation never routes BELOW "
                "either signal"
            )
    if climbed:
        ran_cell = f"{climbed['model']} · {climbed['reasoning'] or 'unset'}"
        lines.append(
            f"    escalated RAISED to {ran_cell} by the ESCALATION climb "
            f"(rung {escalation['rung']}, dispatch attempt {escalation['attempt']}) — NOT by "
            "the rows above, which describe the cell this dispatch climbed FROM"
        )
    lines.append(
        f"    fidelity  {fidelity} — "
        + _FIDELITY_BLURB[fidelity].format(
            effort=codex_effort,
            ran=(f"{climbed['model']} · {climbed['reasoning'] or 'unset'}" if climbed else ""),
        )
    )
    lines.append(
        f"    profile   providers.openai calibration.status = {calibration} "
        "(require_calibrated waived by the typed --harness codex election; see "
        "dual-harness-contract.md D4a)"
    )
    # The capability grant is part of the receipt, not a silent widening: an
    # operator reading turn one must see exactly how far this session's sandbox
    # was opened, and on whose say-so.
    if grant:
        if grant.get("sandbox") == "danger-full-access":
            lines.append(
                "    shell     sandbox=danger-full-access — this dispatched session runs "
                "UNSANDBOXED (writes anywhere, network, nested `codex exec`/vendor CLIs). "
                "Declared by dispatch.codex_shell; a human gate is REQUIRED and was verified"
            )
            if grant.get("supersedes"):
                lines.append(
                    "    shell     writable_roots/network dropped — full access already grants both"
                )
            if grant.get("env_include"):
                lines.append(
                    "    shell     env_include=" + ",".join(grant["env_include"])
                    + " (names only; values stay outside the command and receipt)"
                )
        else:
            granted = []
            if grant.get("writable_roots"):
                granted.append("writable_roots=" + ",".join(grant["writable_roots"]))
            if grant.get("network"):
                granted.append("network_access=true")
            if grant.get("env_include"):
                granted.append("env_include=" + ",".join(grant["env_include"]))
            lines.append(
                "    shell     sandbox=workspace-write + " + "; ".join(granted)
                + "  (declared by dispatch.codex_shell; measured to survive --ignore-user-config)"
            )
    for d in dropped:
        lines.append(f"    dropped   {d}")
    return {
        "codex_shell_grant": grant,
        "translated_from": translated_from,
        # THE CELL THE SSOT RESOLVED, named as such — `tier`, `intent`,
        # `effort_map_row` and the non-escalation half of `effort_fidelity` all
        # describe THIS pair, not necessarily the one the command runs.
        "base": {"model": codex_model, "effort": codex_effort},
        # …and where the climb took it, or None. The consumer never has to infer
        # which of the two a field is about.
        "escalated_to": (
            {"model": climbed["model"], "effort": climbed["reasoning"],
             "rung": escalation["rung"]}
            if climbed else None
        ),
        "tier": tier,
        "intent": intent,
        "effort_map_row": row,
        "effort_fidelity": fidelity,
        "calibration_status": calibration,
        "dropped": list(dropped),
        "receipt": "\n".join(lines),
    }


def _codex_harness_spec(plan_dir, sid, session, ssot_text, egress_state, manifest):
    """One session's runnable `codex exec` dispatch spec under `--harness codex`.

    EVERY ready session gets one — that is the point of the mode. A session
    pinned to a Claude model is translated through the SSOT via the EXISTING
    `_resolve_codex_dispatch` (the single translation surface; adding a second
    one in resolve_route.py is exactly the drift class check (d) polices), with
    `require_calibrated=False` because a typed `--harness codex` is a per-run
    election, not the implicit execution default that gate guards (D4a).

    Raises UnroutableCodexSession on any gap — never a partial or guessed route."""
    raw = session.get("model")
    declared_codex = _declared_codex(raw)
    dispatch = session.get("dispatch") or {}
    subagent = dispatch.get("subagent_type")

    # `fork` is BLOCKED, loudly (§ 4.4): it means "this session needs the
    # orchestrator's live conversation context", which `codex exec` reading a
    # prompt file provably cannot reproduce. Silently downgrading it to a fresh
    # agent would hand the session a dependency it was authored to rely on.
    if subagent == "fork":
        raise UnroutableCodexSession(
            "dispatch.subagent_type is 'fork' — a fork needs the orchestrator's LIVE "
            "conversation context, which `codex exec` (prompt file on stdin) cannot "
            "reproduce; run this session from Claude Code (no --harness), or drop the fork pin"
        )
    # An EXPLICIT Codex pin on barred work stays a loud BLOCK on BOTH harnesses:
    # the plan itself contradicts itself (unsupervised Codex asked for on work
    # declared unsafe for it) and only the plan author can resolve that. Barred
    # work WITHOUT such a pin is handled by the caller as a checkpoint (D4c).
    barred = _session_barred(session)
    if barred and declared_codex:
        raise UnroutableCodexSession(
            f"session is barred from unsupervised Codex execution ({barred}) but explicitly "
            "pins a Codex model — remove the pin or run supervised"
        )
    # data_sensitivity_guard (D4d) — KEPT under this harness with its scope stated
    # honestly: it can no longer stop the TREE reaching OpenAI (the orchestrator is
    # itself a Codex process that has read it), but it still stops a DISPATCHED
    # session shipping it. The real control is "don't start a Codex orchestrator in
    # a restricted tree", which `plan --harness codex` discloses on turn one.
    eg = egress_state()
    if eg["restricted_hit"] and not eg["opted_in"]:
        raise UnroutableCodexSession(
            f"data_sensitivity_guard egress: restricted content in the working tree "
            f"({eg['restricted_hit']}) — DO-NOT-SEND to Codex. Clear it in "
            f"model-routing.yaml: an unexpired content_scan_allowlist entry for that one "
            f"reviewed file, or an unexpired per-repo egress_opt_ins entry for {eg['root']}"
        )
    prompt_file = Path(plan_dir) / session.get("prompt_file", f"sessions/{sid}.prompt.md")
    if not prompt_file.exists():
        raise UnroutableCodexSession(
            f"prompt file {prompt_file} missing — the codex exec command reads it from stdin"
        )
    # The BASE rung — the cell the SSOT resolves for this session, kept under its
    # own name because the receipt below has to describe it after the climb has
    # moved on from it.
    base_model, base_effort = _resolve_codex_dispatch(
        raw, session.get("reasoning"), ssot_text, require_calibrated=False,
        task_class=(session.get("task_class") or "").strip().lower() or None,
    )
    # ESC-03 — the climb, applied to the RESOLVED cell and before the command is
    # built, so the escalated rung is what the emitted `codex exec` actually runs.
    # `manifest` is REQUIRED (not defaulted) because it carries the version gate: a
    # caller that forgot it would silently dispatch a plan that should have climbed.
    codex_model, codex_effort, esc_desc = _codex_climb(
        plan_dir, manifest, session, base_model, base_effort)
    escalated = bool(esc_desc and esc_desc["rung"] > 0)
    # CRASH RECOVERY (§ 3.6): inside Codex the orchestrator holds only the PATH,
    # in its context — a /tmp path is unrecoverable if it dies between `codex exec`
    # returning and `apply` running. Write into the plan directory instead (still
    # per-attempt stamped: a stale or foreign closeout from a prior run must never
    # be relayed as this run's result) and log the path to run.ndjson, so recovery
    # is "read the last codex_dispatch event for this DOING session".
    codex_dir = Path(plan_dir) / "_codex"
    codex_dir.mkdir(exist_ok=True)
    stamp = f"{os.getpid()}-{int(time.time())}"
    lm_file = str(codex_dir / f"{sid}.{stamp}.last-message.txt")
    fb_lm_file = str(codex_dir / f"{sid}.{stamp}-fb.last-message.txt")
    # The session's DECLARED shell capabilities (writes outside the workspace,
    # network, nested dispatch). Resolved and gate-checked BEFORE the command is
    # built, so an ungated full-access request never becomes a runnable string.
    grant = _codex_shell_grant(session)
    _assert_full_access_gated(sid, session, grant)
    dispatch_cmd = _codex_cmd(codex_model, codex_effort, str(prompt_file), lm_file,
                              workdir=eg["root"], grant=grant)
    # SUPPRESSED ON AN ESCALATED MEMBER (ESC-03, same rule as the Claude lane) —
    # see `_refusal_instruction` for why a refused escalated rung is reported, not
    # degraded around.
    fb = None if escalated else _fallback_for(codex_model, "openai", refused=_refused(esc_desc))
    fb_lm = fb_lm_file if fb else None
    fb_cmd = (
        _codex_cmd(fb[0], fb[1], str(prompt_file), fb_lm_file, workdir=eg["root"], grant=grant)
        if fb else None
    )
    # Claude-only dispatch fields under Codex (§ 4.4): a tier agent is a Claude
    # AGENT DEFINITION and cannot apply here — effort rides `-c
    # model_reasoning_effort=` instead. Dropping it silently would be the same
    # dishonesty the effort_enforced/effort_mechanism pair exists to prevent, so
    # the drop is named in the receipt.
    dropped = []
    if subagent:
        dropped.append(
            f"dispatch.subagent_type {subagent!r} — a Claude agent definition; it cannot "
            f"apply here, effort rides `-c model_reasoning_effort={codex_effort}` instead"
        )
    # BASE pair in, climb descriptor alongside: the receipt describes the cell the
    # SSOT resolved and reports the climb as its own raise (see its docstring).
    translation = _codex_translation(sid, session, base_model, base_effort, ssot_text, dropped,
                                     grant=grant, escalation=esc_desc)
    return {
        "backend": "codex",
        "codex_model": codex_model,
        "codex_effort": codex_effort,
        "codex_shell_grant": grant,
        "dispatch_cmd": dispatch_cmd,
        "last_message_file": lm_file,
        "fallback_model": fb[0] if fb else None,
        "fallback_reasoning": fb[1] if fb else None,
        "fallback_cmd": fb_cmd,
        "fallback_last_message_file": fb_lm,
        "translation": translation,
        "escalation": esc_desc,
        **({"on_dispatch_refusal": _refusal_instruction(sid, codex_model, codex_effort)}
           if escalated else {}),
    }


def _barred_checkpoint_brief(sid, plan_dir, reason, spec):
    """The decision brief for a barred session under `--harness codex` (D4c).
    Allowed to run on Codex — never dispatched without a human saying so first.
    Plain language, and it names the actual model the session would run on."""
    return (
        f"**{sid} is barred from unsupervised Codex execution** ({reason}).\n"
        f"You elected `--harness codex` for this run, so this session would execute on "
        f"`{spec['codex_model']} · {spec['codex_effort']}` in this working tree. That is "
        f"allowed, but this session is one-shot and hard to reverse, so it does not "
        f"dispatch without you.\n"
        f"**Decision:** run {sid} on Codex now (re-run `plan --resume`, then `begin "
        f"--harness codex --sessions {sid}`), or stop and run it from Claude Code instead "
        f"(`/plan-execute {plan_dir} --session {sid}`, no `--harness`).\n"
        f"**If you do nothing:** the plan halts here with {sid} unstarted; nothing is lost."
    )


def _resolve_plan_dir(path):
    """Accept either the plan directory or a PLAN.html path."""
    p = Path(path)
    if p.is_file() and p.name == PLAN_HTML:
        return p.parent
    return p


# --------------------------------------------------------------------------
# QW-02 (2026-07-03 reliability sweep): cwd-robust plan resolution.
#
# Root cause (raw session 960d2926): an earlier `cd` in the same session
# shifted cwd into the plan dir itself, so a subsequent relative `<plan-dir>`
# argument (still typed relative to the ORIGINAL cwd) no longer resolved and
# the run burned a turn on "plan directory not found". Separately, ~26
# sessions/22 launches pasted a full `file:///...PLAN.html` URI because there
# was no "just run the latest/canonical plan" default despite
# `_plans_index.md` already existing. Both gaps close here:
#   1. `_search_upward_for_plan` — if the literal arg doesn't resolve from
#      cwd, walk up the directory tree (cwd is often already inside or
#      beside the plan dir after an earlier `cd`) looking for a match.
#   2. `_find_plans_index` / `_pick_default_plan` — a bare invocation (no
#      plan-dir argument at all) reads `_plans_index.md` (found by the same
#      upward search) and defaults to the CANONICAL-tagged row, or the
#      highest `created YYYY-MM-DD` row if none is tagged CANONICAL.
_UPWARD_SEARCH_MAX_LEVELS = 12

_INDEX_ROW_RE = re.compile(
    r"\[.*?\]\(([^)]+?/PLAN\.html)\).*?created\s+(\d{4}-\d{2}-\d{2})",
    re.IGNORECASE,
)


def _search_upward_for_plan(path_arg, start=None):
    """Best-effort recovery when `path_arg` (as given) does not exist relative
    to cwd. Walks up from `start` (default cwd) checking, at each level:
      (a) <level>/<path_arg> exists and looks like a plan dir,
      (b) <level> itself IS the target plan dir (cwd already inside it),
      (c) <level>/_plans/<basename(path_arg)> exists.
    Returns a Path or None."""
    cur = Path(start or Path.cwd()).resolve()
    name = Path(path_arg).name
    for _ in range(_UPWARD_SEARCH_MAX_LEVELS):
        for candidate in (cur / path_arg, cur if cur.name == name else None, cur / "_plans" / name):
            if candidate is not None and candidate.is_dir() and (candidate / "manifest.json").exists():
                return candidate
        if cur.parent == cur:
            break
        cur = cur.parent
    return None


def _find_plans_index(start=None):
    """Search upward from `start` (default cwd) for `_plans_index.md`."""
    cur = Path(start or Path.cwd()).resolve()
    for _ in range(_UPWARD_SEARCH_MAX_LEVELS):
        candidate = cur / "_plans_index.md"
        if candidate.is_file():
            return candidate
        if cur.parent == cur:
            break
        cur = cur.parent
    return None


def _parse_plans_index(index_path):
    """Parse `_plans_index.md` rows into dicts with html_path/date/canonical/line.
    Tolerant of drift in the surrounding prose — matches only the
    `[title](.../PLAN.html) ... created YYYY-MM-DD` shape plan-builder emits."""
    rows = []
    for line in Path(index_path).read_text().splitlines():
        m = _INDEX_ROW_RE.search(line)
        if not m:
            continue
        html_path, date = m.groups()
        rows.append(
            {
                "html_path": html_path,
                "date": date,
                "canonical": "canonical" in line.lower(),
                "line": line.strip(),
            }
        )
    return rows


def _pick_default_plan(index_path):
    """Pick the CANONICAL-tagged row (or, absent one, the highest-dated row)
    from `_plans_index.md` and return its plan directory. None if the index
    has no parseable rows."""
    rows = _parse_plans_index(index_path)
    if not rows:
        return None
    canonical_rows = [r for r in rows if r["canonical"]]
    pool = canonical_rows if canonical_rows else rows
    pool = sorted(pool, key=lambda r: r["date"])
    chosen = pool[-1]
    return (Path(index_path).parent / chosen["html_path"]).parent


def _resolve_bare_invocation():
    """No plan-dir argument given: find `_plans_index.md` (upward search) and
    default to its canonical/latest plan. Raises SystemExit with a clear
    message on any failure — never silently guesses."""
    index_path = _find_plans_index()
    if index_path is None:
        raise SystemExit(
            "no plan directory given and no _plans_index.md found searching "
            f"upward from {Path.cwd()} — pass a plan directory explicitly."
        )
    plan_dir = _pick_default_plan(index_path)
    if plan_dir is None:
        raise SystemExit(f"_plans_index.md at {index_path} has no parseable plan rows.")
    print(f"(no plan directory given — defaulted to {plan_dir} via {index_path})", file=sys.stderr)
    return plan_dir


def _html_path(plan_dir):
    return Path(plan_dir) / PLAN_HTML


def _statuses(plan_dir):
    return ab.read_all_statuses(_html_path(plan_dir).read_text())


# --------------------------------------------------------------------------
# AWAITS_REVIEW has two flavors, and telling them apart is what makes both
# `--resume` and `ack-checkpoint` safe (RP-01):
#
#   PRE-DISPATCH  — the manifest's `requires_human_checkpoint` gate on a session
#                   that has NOT run. `checkpoint` set it. `--resume` dispatches
#                   it for the first time. Correct, unchanged.
#   POST-SESSION  — the session RAN, closed DONE, and `apply`/`verify-finalize`
#                   parked it on its closeout's `human_checkpoint_reason`.
#                   Re-dispatching redoes paid work and re-trips the same gate
#                   forever; `ack-checkpoint` resolves it instead.
#
# The discriminator is RECORDED STATE, never prose: a persisted closeout that
# carried result DONE. If no closeout is on disk, the session cannot be proven to
# have finished, so it is treated as PRE-DISPATCH — the safe default is to never
# silently ack work whose record is missing.
# --------------------------------------------------------------------------
def _awaits_review_kind(plan_dir, session_id):
    """``"post_session"`` | ``"pre_dispatch"`` for a session at AWAITS_REVIEW."""
    try:
        co = cp.load_closeout(plan_dir, session_id)
    except (OSError, ValueError):
        co = None
    return "post_session" if co and co.get("result") == "DONE" else "pre_dispatch"


def _post_session_parked(plan_dir, statuses):
    """Session ids at AWAITS_REVIEW whose park is the POST-SESSION flavor."""
    return [
        sid
        for sid, st in sorted(statuses.items())
        if st == "AWAITS_REVIEW" and _awaits_review_kind(plan_dir, sid) == "post_session"
    ]


def _refuse_if_halted(plan_dir, what, escape=None, allow_kinds=()):
    """Guard every state-mutating entry point behind the halt flag.

    Historically ONLY `cmd_plan` checked it, so `begin` (and anything added
    later) happily mutated a halted plan — the flag stopped the loop's front
    door while the side doors stayed open.

    ``allow_kinds`` names the halt FLAVORS this entry point is the cure for. A
    REPLAN halt (RP-05) exists to be resolved by restructuring the plan, so the
    mutation commands and `redispatch` pass ``("replan",)`` — refusing them
    would deadlock the operator against the very gate they are answering.
    Dispatch (`plan` / `begin`) never passes it: advancing is exactly what a
    REPLAN park stops.
    """
    if not rsi.is_halted(plan_dir):
        return
    halt = rsi.load_state(plan_dir).get("halt", {})
    if halt.get("kind") and halt.get("kind") in allow_kinds:
        return
    raise SystemExit(
        f"refusing to {what}: this plan is HALTED — {halt.get('reason') or '(no reason recorded)'} "
        f"(set by {halt.get('by_session') or 'unknown'} at {halt.get('at')}). "
        f"Fix the cause, then clear it: run.py clear-halt {plan_dir}"
        + (f" — or {escape}." if escape else ".")
    )


# --------------------------------------------------------------------------
def cmd_status(plan_dir):
    # P8: surface the HALT_NOTICE banner at the TOP, before the JSON dump.
    notice = Path(plan_dir) / "HALT_NOTICE.txt"
    if notice.exists():
        print("=" * 72)
        print(notice.read_text().rstrip())
        print("=" * 72)
    manifest = mio.load_manifest(plan_dir)
    statuses = _statuses(plan_dir)
    state = rsi.load_state(plan_dir)
    by_status = {}
    for s in manifest["sessions"]:
        st = statuses.get(s["id"], "TODO")
        by_status.setdefault(st, []).append(s["id"])
    _out(
        {
            "title": manifest.get("title"),
            "halt": state.get("halt"),
            "last_batch": state.get("last_batch"),
            "sessions_by_status": by_status,
            "item_statuses": {k: v for k, v in statuses.items() if k in mio.all_item_ids(manifest)},
            "next": dsp.next_action(
                manifest, statuses, post_session_parked=_post_session_parked(plan_dir, statuses)
            ),
            # RP-04 — page/manifest coherence. Reported, never blocking: `status`
            # answers "how is this plan", and a page surface that drifted is part
            # of that answer. 25 ms on a 228 KB page (measured 2026-08-12).
            "containment": sg.check_containment(plan_dir),
            "shipping": shp.shipping_summary(plan_dir, manifest),
            "replan_pending": [p["brief"] for p in rp.pending(plan_dir)],
        },
        plan_dir=plan_dir,
    )


def cmd_status_all():
    """Scan every _plans/*/ under cwd and print a one-line status per plan."""
    plans_root = Path.cwd() / "_plans"
    if not plans_root.is_dir():
        print(f"No _plans/ directory under {Path.cwd()}.")
        return
    plan_dirs = sorted(d for d in plans_root.iterdir() if (d / "manifest.json").exists())
    if not plan_dirs:
        print(f"No plans found under {plans_root}.")
        return
    for d in plan_dirs:
        print(_one_line_status(d))


def _one_line_status(plan_dir):
    try:
        manifest = mio.load_manifest(plan_dir)
    except mio.ManifestError as e:
        return f"{plan_dir.name}: (unreadable manifest: {e})"
    try:
        statuses = _statuses(plan_dir)
    except (OSError, ValueError):
        statuses = {}
    state = rsi.load_state(plan_dir)
    counts = {}
    for s in manifest["sessions"]:
        st = statuses.get(s["id"], "TODO")
        counts[st] = counts.get(st, 0) + 1
    counts_str = " ".join(f"{k}:{v}" for k, v in sorted(counts.items()))
    halt_str = " [HALTED]" if state.get("halt", {}).get("set") else ""
    lb = state.get("last_batch")
    last_str = ""
    if lb:
        sids = ",".join(lb.get("session_ids", []))
        last_str = f" · last: {sids} ({lb.get('result', '')})"
    title = manifest.get("title", plan_dir.name)
    ship = shp.shipping_summary(plan_dir, manifest)
    ship_str = ""
    if ship:
        badges = ",".join(f"{sid}:{b}" for sid, b in ship["sessions"].items())
        ship_str = f" · ship: {badges}"
    return f"{plan_dir.name}{halt_str} — {title} — {counts_str}{last_str}{ship_str}"


def cmd_plan(plan_dir, resume, only_session, auto=False, harness="claude"):
    if rsi.is_halted(plan_dir) and not resume:
        state = rsi.load_state(plan_dir)
        out = {
            "action": "halted",
            "halt": state["halt"],
            "hint": "clear with `--clear-halt` after fixing, or pass --resume",
        }
        # RP-05: a REPLAN halt is a DECISION, not a failure — the operator needs
        # the brief, not a "something went wrong" line. Carry it here so the stop
        # reads like the checkpoint it is.
        parked = rp.pending(plan_dir)
        if parked:
            out["action"] = "replan"
            out["replan"] = [p["brief"] for p in parked]
            # RP-08 — the brief as the operator should SEE it: reason, invalidated
            # sessions, the three options, and the recommendation (or a loud note
            # that it is still owed) in one block that can be presented verbatim.
            out["replan_text"] = "\n\n".join(rp.format_brief(p["brief"]) for p in parked)
            out["hint"] = (
                "answer the brief: `run.py resolve-replan <plan-dir> --session "
                "<sid> --decision amend|retire|proceed --reason '…'` (apply the "
                "amendment through add/amend/retire-session or redispatch FIRST)"
            )
            owed = [p["session"] for p in parked if rp.needs_recommendation(p)]
            if owed:
                out["replan_needs_recommendation"] = owed
                out["hint"] = (
                    f"FIRST record your recommendation for {', '.join(owed)}: `run.py "
                    "recommend-replan <plan-dir> --session <sid> --recommendation "
                    "'<which option you would take, and why>'` — resolve-replan refuses "
                    "until it is recorded, because an operator should not have to decide "
                    "without it. Then " + out["hint"]
                )
        _out(out, plan_dir=plan_dir)
        return
    manifest = mio.load_manifest(plan_dir)
    statuses = _statuses(plan_dir)
    _consistency_check(manifest, statuses)
    action = dsp.next_action(
        manifest,
        statuses,
        resume=resume,
        only_session=only_session,
        post_session_parked=_post_session_parked(plan_dir, statuses),
    )
    # Echo the autonomy posture so the orchestrator (and audit) record it. --auto
    # only affects how the orchestrator self-drives; it NEVER changes the decision
    # here (human checkpoints/blockers still surface). Human gates stay sacrosanct.
    action["auto_mode"] = bool(auto)
    # Plan complete: fire the opt-in notify_on_complete hook once (the "you can stop
    # watching now" ping for an unattended run). Idempotent + best-effort.
    if action.get("action") == "complete":
        action["notify_complete"] = rsi.notify_complete(plan_dir)
    if harness == "codex":
        # D4d: land the egress verdict on TURN ONE, not at first dispatch. Under
        # this harness the orchestrator is ITSELF a Codex process that has already
        # read the tree, so `begin`'s refusal can no longer protect the tree — only
        # a human who sees this before starting can. The rule the ported SKILL.md
        # states: do not start a Codex orchestrator in a restricted tree.
        action["harness"] = "codex"
        _, ssot_text = _load_routing()
        action["egress"] = _egress_verdict(ssot_text, _egress_root())
    _out(action, plan_dir=plan_dir)


# --------------------------------------------------------------------------
# PL-01 — worktree-isolated dispatch + the integration merge.
# Contract: references/parallel-group-contract.md §2 M3 (mechanism), §3 rules
# 5-7 (integration), §4 (lifecycle). Mechanism lives in worktree.py.
# --------------------------------------------------------------------------
def _halt_and_exit(plan_dir, session_id, note, payload):
    """Mark BLOCKED + halt under the lock, emit the payload, exit 1.

    Same all-or-nothing shape as the unroutable-Codex path above: nothing in the
    batch dispatches and no session was ever flipped to DOING.
    """
    rsi.acquire_lock(plan_dir)
    try:
        ab.apply_mutation(_html_path(plan_dir), session_id, status="BLOCKED", note=note)
        rsi.set_halt(plan_dir, f"{session_id}: {note}", session_id)
    finally:
        rsi.release_lock(plan_dir)
    _out(payload, plan_dir=plan_dir)
    sys.exit(1)


def _isolation_prep(plan_dir, manifest, by_id, sessions, statuses, codex_harness):
    """Create member worktrees / run the integration merge BEFORE any dispatch.

    Returns ``{session_id: worktree_path}`` for isolated members in this batch.

    The merge is triggered STRUCTURALLY — by the integration session's own
    dispatch — rather than by a documented orchestrator step, because a step
    described only in prose is a step that can be skipped. The integration agent
    therefore never starts on an unmerged tree, and a conflict refuses its
    dispatch instead of handing it a half-merged one.
    """
    members = [sid for sid in sessions if wt.is_isolated_member(by_id[sid])]
    integrators = [
        sid for sid in sessions
        if (g := wt.integrates_group(by_id[sid])) and wt.isolated_group(manifest, g)
    ]
    if not members and not integrators:
        return {}
    if codex_harness:
        # The Codex harness dispatches SERIALLY, one `codex exec` per session,
        # and has no worktree lifecycle. Refuse rather than silently run an
        # isolated group in the shared tree — accepting an inert isolation flag
        # is the exact failure the contract was written to end (§2 M3).
        raise SystemExit(
            f"refusing `begin --harness codex` for {sorted(set(members) | set(integrators))}: "
            f"dispatch.isolation={pc.ISOLATION_WORKTREE!r} is honoured only by the Claude "
            "harness. The Codex harness runs sessions serially in the shared tree and "
            "creates no worktrees, so the declaration cannot be met. Run this group under "
            f"the Claude harness, or remove the isolation declaration. See {pc.CONTRACT_DOC} "
            "§2 M3."
        )
    project_root = shp.find_project_root(plan_dir)

    for sid in integrators:
        group = wt.integrates_group(by_id[sid])
        # Contract §4 — "A member that ends non-DONE blocks integration." This
        # OVERRIDES depends_on_policy: "completed_or_terminal" for integration
        # sessions specifically. Merging a half-finished member's branch is
        # exactly the "partial output mixed with successful peer output" failure.
        unfinished = {
            m["id"]: statuses.get(m["id"], "TODO")
            for m in wt.group_members(manifest, group)
            if statuses.get(m["id"]) != "DONE"
        }
        if unfinished:
            _halt_and_exit(
                plan_dir, sid,
                f"integration withheld: members of parallel_group {group!r} are not all DONE "
                f"({unfinished})",
                {"action": "blocked", "integration": {"group": group, "session": sid,
                                                      "unfinished_members": unfinished}},
            )
        try:
            res = wt.merge_group(plan_dir, manifest, group, integration_session=sid)
        except wt.WorktreeError as e:
            _halt_and_exit(plan_dir, sid, f"integration merge failed: {e}",
                           {"action": "blocked", "integration": {"group": group, "error": str(e)}})
        if res["status"] != "merged":
            # LOUD, and every member branch is intact — never an automated
            # resolution (contract §3 rule 5; S02 §7 measured the loud failure).
            note = {
                "conflict": (
                    f"integration merge CONFLICT on member {res.get('session')} "
                    f"(branch {res.get('branch')}): {', '.join(res.get('files') or []) or 'see log'}"
                ),
                "containment": (
                    "integration refused: members wrote OUTSIDE their worktrees — "
                    + "; ".join(
                        f"{s['session']} → {', '.join(s['paths'])}"
                        for s in res["containment"]["stray"]
                    )
                ),
                "missing-member": (
                    f"integration refused: no worktree recorded for member {res.get('session')}"
                ),
            }.get(res["status"], f"integration refused: {res['status']}")
            res["recovery"] = (
                f"every member branch is intact ({sorted((res.get('branches') or {}).values())}). "
                f"Resolve by hand, then re-dispatch {sid}. To undo the merges that DID land: "
                f"git -C {project_root} reset --hard {res.get('base_ref', '')[:12]}"
            )
            _halt_and_exit(plan_dir, sid, note,
                           {"action": "blocked", "integration": res})
        rsi.log_event(plan_dir, "worktree_integration_merged", session_ids=[sid],
                      group=group, order=res["order"], merged=res["merged"])
        print(
            f"integration: merged {len(res['merged'])} member branch(es) producer-first "
            f"({' → '.join(res['merged']) or 'nothing new'}) for parallel_group {group!r}. "
            "The full gate set now re-runs on the MERGED tree — a clean textual merge does "
            "not imply a working tree.",
            file=sys.stderr,
        )

    try:
        return wt.prepare_members(plan_dir, manifest, members, project_root=project_root)
    except wt.WorktreeError as e:
        raise SystemExit(f"worktree isolation failed: {e}") from e


def _consistency_check(manifest, statuses):
    """H4-lite: every manifest session id must exist as an anchored article."""
    missing = [s["id"] for s in manifest["sessions"] if s["id"] not in statuses]
    if missing:
        raise SystemExit(
            f"manifest/HTML mismatch — sessions not found as anchored articles: {missing}"
        )


def cmd_begin(plan_dir, sessions, unsafe_lock=False, harness="claude", resume=False):
    # Inherited halt hole (closed 2026-08-12): `plan` refused on a halted plan but
    # `begin` did not, so the loop's own next step could dispatch — and mutate
    # TODO→DOING — straight through a halt. `--resume` is the same deliberate
    # override `plan --resume` already is, and nothing else opens this door.
    if not resume:
        _refuse_if_halted(
            plan_dir,
            f"dispatch {', '.join(sessions)}",
            escape="pass --resume to dispatch past it deliberately (as `plan --resume` does)",
        )
    codex_harness = harness == "codex"
    # D1 negative guard — NOT harness detection. A Seatbelt-sandboxed Codex shell
    # cannot spawn a nested `codex exec` at all (contract P2), so proceeding would
    # fail every dispatch with an opaque app-server error.
    if codex_harness and os.environ.get(_CODEX_SANDBOX_ENV):
        raise SystemExit(
            f"refusing `begin --harness codex`: {_CODEX_SANDBOX_ENV}="
            f"{os.environ[_CODEX_SANDBOX_ENV]!r} means this shell is inside the Codex "
            "Seatbelt sandbox, where a nested `codex exec` dies with 'failed to initialize "
            "in-process app-server client: Operation not permitted'. Restart the "
            f"orchestrator unsandboxed: {_CODEX_LAUNCH_RECIPE} (this disables the sandbox "
            "for EVERY command that session runs — see dual-harness-contract.md § 3.1)."
        )
    # P5: refuse to lock on a networked/sync FS where the pidfile lock is
    # unreliable, unless the operator overrides with --unsafe-lock.
    fs_class = rsi.check_lock_fs(plan_dir)
    if fs_class and not unsafe_lock:
        raise SystemExit(
            f"refusing to acquire the plan lock: {plan_dir} is on {fs_class}. "
            "The .lock pidfile (like flock) is reliable only on a local POSIX "
            "filesystem; on a networked or cloud-sync filesystem two runs can "
            "each believe they hold the lock and race PLAN.html into corruption. "
            "Move the plan to local disk, or pass --unsafe-lock to proceed anyway."
        )

    manifest = mio.load_manifest(plan_dir)

    # PL-02 — parallel-group contract, DISPATCH-TIME half. The build-time half
    # lives in plan-builder's `validate_spec.py`; this one is not redundant with
    # it, because the manifest reaching us here can have been hand-edited,
    # produced by an older builder, or mutated in flight by add/amend-session.
    # The gate protecting the tree must not live only in the tool that wrote the
    # manifest. Fires BEFORE the lock and before any TODO→DOING mutation, and is
    # keyed on manifest membership rather than on this batch, so a member
    # dispatched alone via --only-session is still checked as a member.
    pc_refusals, pc_warnings = pc.check(
        manifest, schema_version=manifest.get("plan_schema_version")
    )
    if pc_refusals:
        raise SystemExit(pc.refusal_text(pc_refusals))

    by_id = mio.session_by_id(manifest)
    html = _html_path(plan_dir).read_text()

    # Anchor preflight for every batch member BEFORE any mutation (H7-lite).
    for sid in sessions:
        if sid not in by_id:
            raise SystemExit(f"unknown session {sid!r}")
        ab.preflight(html, sid)
        for iid in by_id[sid].get("items", []):
            ab.preflight(html, iid)

    # Provider-aware dispatch resolution — PRE-LOCK, PRE-MUTATION (s04). The
    # exception path below releases the lock but does NOT restore DOING→TODO, so
    # any resolution failure must fire BEFORE a session is flipped to DOING. An
    # unroutable Codex session is marked BLOCKED atomically (with a halt reason)
    # instead — it must NEVER fall through to the silent inherit-Claude path.
    active_provider, ssot_text = _load_routing()
    provider = active_provider or "anthropic"
    # data_sensitivity_guard egress state — scanned lazily, at most once per begin
    # (used for BOTH the executor egress refusal and the verifier on-box stamp).
    _egress = {}

    def egress_state():
        if not _egress:
            _egress.update(_egress_verdict(ssot_text, _egress_root()))
        return _egress

    # Read once, from the same HTML the preflight above validated. Barred Codex
    # sessions need it to tell "not yet gated" from "the human already resumed
    # past the gate" (D4c); the integration gate needs it for contract §4's
    # "a member that ends non-DONE blocks integration".
    statuses = ab.read_all_statuses(html)

    # PL-01 — worktrees + the integration merge, BEFORE the lock and before any
    # TODO→DOING mutation, so a conflict or a containment violation refuses the
    # whole batch with nothing half-dispatched.
    worktrees = _isolation_prep(plan_dir, manifest, by_id, sessions, statuses, codex_harness)

    specs, unroutable, barred_pending = {}, {}, {}
    for sid in sessions:
        s = by_id[sid]
        raw = s.get("model")
        declared_codex = _declared_codex(raw)
        if codex_harness:
            # THE HARNESS BRANCH: every session resolves to a runnable codex exec
            # command. The dial (`active_provider`) and executor_policy's opt-in
            # list are both WAIVED here (D4a/D4b) — they exist to gate an IMPLICIT
            # choice, and a typed `--harness codex` is an explicit per-run
            # election by the operator. Reversibility (D4c) and data
            # classification (D4d) still gate, because those ask a different
            # question than "who decided".
            try:
                specs[sid] = _codex_harness_spec(plan_dir, sid, s, ssot_text, egress_state,
                                                 manifest=manifest)
            except UnroutableCodexSession as e:
                unroutable[sid] = str(e)
                continue
            barred = _session_barred(s)
            if barred and statuses.get(sid, "TODO") != "AWAITS_REVIEW":
                # Allowed on Codex, but not without a human: park it at
                # AWAITS_REVIEW with a brief. `--resume` re-offers it, and the
                # AWAITS_REVIEW status IS the record that the gate was answered.
                barred_pending[sid] = barred
            continue
        if not declared_codex and provider == "anthropic":
            specs[sid] = {"backend": "claude"}
            continue
        # executor_policy (s06, EXE-01) — dial-driven Codex execution is OPT-IN
        # per task_class and NEVER for linchpin/irreversible work. Ineligible
        # sessions FAIL CLOSED to Claude execution (the safe family), loudly noted.
        if not declared_codex and provider == "openai":
            barred = _session_barred(s)
            task_class = (s.get("task_class") or "").strip().lower()
            eligible = task_class and task_class in _executor_for(ssot_text)
            if barred or not eligible:
                why = (
                    f"barred from unsupervised Codex execution ({barred})"
                    if barred
                    else f"task_class {task_class or '(unset)'} not opted into executor_policy.executor_for"
                )
                print(
                    f"executor_policy: session {sid} dispatches Claude under "
                    f"active_provider=openai — {why}.",
                    file=sys.stderr,
                )
                specs[sid] = {"backend": "claude", "executor_policy_note": why}
                continue
        # Codex-backed dispatch: session-declared Codex model, or a non-Anthropic
        # active_provider (run-level dial). Resolve + validate the FULL wrapper
        # command now; any gap blocks loudly here.
        try:
            if not declared_codex and provider != "openai":
                raise UnroutableCodexSession(
                    f"active_provider {provider!r} has no dispatch wrapper (only the "
                    "codex CLI lane exists) — flip active_provider or pin a session model"
                )
            if declared_codex:
                barred = _session_barred(s)
                if barred:
                    # An EXPLICIT Codex pin on barred work is a contradiction the
                    # plan author must resolve — block loudly, never silently
                    # reroute an explicit pin (executor_policy, s06).
                    raise UnroutableCodexSession(
                        f"session is barred from unsupervised Codex execution ({barred}) "
                        "but explicitly pins a Codex model — remove the pin or run supervised"
                    )
            # data_sensitivity_guard egress (s06): refuse BEFORE any codex command
            # is constructed — codex exec ships the working tree to OpenAI.
            eg = egress_state()
            if eg["restricted_hit"] and not eg["opted_in"]:
                raise UnroutableCodexSession(
                    f"data_sensitivity_guard egress: restricted content in the working tree "
                    f"({eg['restricted_hit']}) — DO-NOT-SEND to Codex. Clear it in "
                    f"model-routing.yaml: an unexpired content_scan_allowlist entry for that "
                    f"one reviewed file, or an unexpired per-repo egress_opt_ins entry for "
                    f"{eg['root']}"
                )
            prompt_file = Path(plan_dir) / s.get("prompt_file", f"sessions/{sid}.prompt.md")
            if not prompt_file.exists():
                raise UnroutableCodexSession(
                    f"prompt file {prompt_file} missing — the codex exec command reads it from stdin"
                )
            codex_model, codex_effort = _resolve_codex_dispatch(
                raw, s.get("reasoning"), ssot_text, require_calibrated=not declared_codex,
                task_class=(s.get("task_class") or "").strip().lower() or None,
            )
            # ESC-03 — the climb, on the RESOLVED cell, before the command is built.
            # The same funnel and the same rules as the Claude lane below; only the
            # `-m` and `-c model_reasoning_effort=` flags move.
            codex_model, codex_effort, esc_desc = _codex_climb(
                plan_dir, manifest, s, codex_model, codex_effort)
            escalated = bool(esc_desc and esc_desc["rung"] > 0)
            # Unique per invocation AND per attempt (adversarial-review 2026-07-10,
            # consensus HIGH): a path keyed only by session id let a stale/foreign
            # closeout from a prior run — or a concurrent plan reusing the same
            # session ids — be relayed as this run's result.
            stamp = f"{os.getpid()}-{int(time.time())}"
            lm_file = f"/tmp/codex-{sid}-{stamp}.last-message.txt"
            fb_lm_file = f"/tmp/codex-{sid}-{stamp}-fb.last-message.txt"
            # Same declared capability grant as the codex harness (§ 4.5): a
            # Codex-backed session needs the same shell whichever orchestrator
            # relays it, so this path must not build a narrower command.
            grant = _codex_shell_grant(s)
            _assert_full_access_gated(sid, s, grant)
            cmd = _codex_cmd(codex_model, codex_effort, str(prompt_file), lm_file,
                             workdir=eg["root"], grant=grant)
            # SUPPRESSED ON AN ESCALATED MEMBER (ESC-03) — see `_refusal_instruction`.
            fb = None if escalated else _fallback_for(codex_model, "openai",
                                                      refused=_refused(esc_desc))
            fb_cmd = (
                _codex_cmd(fb[0], fb[1], str(prompt_file), fb_lm_file, workdir=eg["root"],
                           grant=grant)
                if fb else None
            )
            specs[sid] = {
                "backend": "codex",
                "codex_model": codex_model,
                "codex_effort": codex_effort,
                "codex_cmd": cmd,
                "wrapper_prompt": _codex_wrapper_prompt(sid, cmd, lm_file),
                "fallback_model": fb[0] if fb else None,
                "fallback_reasoning": fb[1] if fb else None,
                "fallback_prompt_text": _codex_wrapper_prompt(sid, fb_cmd, fb_lm_file) if fb else None,
                "escalation": esc_desc,
                **({"on_dispatch_refusal": _refusal_instruction(sid, codex_model, codex_effort)}
                   if escalated else {}),
            }
        except UnroutableCodexSession as e:
            unroutable[sid] = str(e)
    if unroutable:
        # Mark BLOCKED + halt under the lock, then exit 1. No session in the batch
        # is dispatched (and none was ever flipped to DOING).
        rsi.acquire_lock(plan_dir)
        try:
            for sid, why in sorted(unroutable.items()):
                ab.apply_mutation(
                    _html_path(plan_dir), sid, status="BLOCKED",
                    note=f"unroutable Codex dispatch: {why}",
                )
            first = sorted(unroutable)[0]
            rsi.set_halt(
                plan_dir, f"{first}: unroutable Codex dispatch — {unroutable[first]}", first
            )
            rsi.log_event(plan_dir, "codex_unroutable", session_ids=sorted(unroutable),
                          reasons=unroutable)
        finally:
            rsi.release_lock(plan_dir)
        _out({"action": "blocked", "unroutable": unroutable}, plan_dir=plan_dir)
        sys.exit(1)

    if barred_pending:
        # D4c — barred work is ALLOWED on Codex but never dispatched unsupervised.
        # Refusing outright would kill the feature at the plan's most important
        # session; allowing silently would discard the only thing the bar ever
        # protected (a human eye on irreversible work). Checkpoint-forcing keeps
        # the eye and drops the veto. NOTHING in the batch dispatches — same
        # all-or-nothing rule as the unroutable path above, so a mixed batch can
        # never half-run while the operator is deciding.
        rsi.acquire_lock(plan_dir)
        try:
            briefs = {}
            for sid, why in sorted(barred_pending.items()):
                briefs[sid] = _barred_checkpoint_brief(sid, plan_dir, why, specs[sid])
                print(specs[sid]["translation"]["receipt"], file=sys.stderr)
                ab.apply_mutation(
                    _html_path(plan_dir), sid, status="AWAITS_REVIEW",
                    note=f"barred from unsupervised Codex execution ({why}) — "
                         "human decision required before dispatch",
                )
                rsi.log_event(plan_dir, "checkpoint_reached", session_ids=[sid],
                              reason=why, harness="codex")
        finally:
            rsi.release_lock(plan_dir)
        _out(
            {
                "action": "checkpoint",
                "harness": "codex",
                "sessions": sorted(barred_pending),
                "checkpoint_briefs": briefs,
                "resume_with": f"/plan-execute {plan_dir} --resume",
            },
            plan_dir=plan_dir,
        )
        return

    if fs_class and unsafe_lock:
        rsi.log_event(plan_dir, "lock_fs_warning", fs_class=fs_class)
    rsi.acquire_lock(plan_dir)
    try:
        for sid in sessions:
            ab.apply_mutation(_html_path(plan_dir), sid, status="DOING", note=None)
        rsi.record_batch(plan_dir, sessions, "DISPATCHED")
        # `harness` is an AUDIT FACT of this run, never plan state (D1): it is
        # logged here and echoed in the payload, and nothing writes it to
        # manifest.json or PLAN.html — the same directory must stay runnable from
        # either side. Absent on the Claude path so that payload stays identical.
        rsi.log_event(
            plan_dir, "dispatch_started", session_ids=sessions,
            **({"harness": "codex"} if codex_harness else {}),
        )
        # DURABLE executor-family record (s06, adversarial-review Claude HIGH):
        # begin output is ephemeral, so the actual-executor family each session
        # resolved to is persisted to run.ndjson — the verifier-selection rule
        # (non-executing family verifies) derives from THIS record + any
        # closeout `degraded_from`, never from the global dial.
        rsi.log_event(
            plan_dir, "dispatch_families", session_ids=sessions,
            families={
                sid: ("openai" if specs[sid]["backend"] == "codex" else "anthropic")
                for sid in sessions
            },
        )
        batch = []
        for sid in sessions:
            s = by_id[sid]
            spec = specs[sid]
            prompt_file = Path(plan_dir) / s.get("prompt_file", f"sessions/{sid}.prompt.md")
            reason_tier = _reasoning_tier(s.get("reasoning"))
            if codex_harness:
                # ONE-LAYER dispatch (D2): the Codex orchestrator runs this command
                # in its own shell and feeds `last_message_file` straight back
                # through `apply` — the `-o` file IS the closeout file. There is no
                # wrapper agent, so no `wrapper_prompt`, no `model_arg`, and no
                # relay to lose the closeout in; three of the Claude wrapper's four
                # failure sentinels disappear with it.
                tr = spec["translation"]
                print(tr["receipt"], file=sys.stderr)
                rsi.log_event(
                    plan_dir, "codex_translation", session_ids=[sid],
                    translated_from=tr["translated_from"], codex_model=spec["codex_model"],
                    codex_effort=spec["codex_effort"], effort_fidelity=tr["effort_fidelity"],
                    # `codex_model`/`codex_effort` are what RAN; `tier`/`intent`/
                    # `effort_fidelity` describe `base`. Both pairs are logged so a
                    # reader of this event never has to guess which one a field is
                    # about — they differ exactly when the climb moved.
                    base=tr["base"], escalated_to=tr["escalated_to"],
                    tier=tr["tier"], intent=tr["intent"],
                    calibration_status=tr["calibration_status"], dropped=tr["dropped"],
                )
                # Crash-recovery breadcrumb (§ 3.6): the last-message path lives in
                # the orchestrator's context only, so persist it — recovery reads
                # the most recent codex_dispatch for a DOING session.
                rsi.log_event(
                    plan_dir, "codex_dispatch", session_ids=[sid],
                    last_message_file=spec["last_message_file"],
                    # Also the DISPATCH RECEIPT `apply` checks (see
                    # _missing_dispatch_receipt). The fallback path has to be here
                    # too: a session degraded onto `fallback_cmd` writes its output
                    # to the -fb file, and a receipt check that only knew the
                    # primary path would refuse a real, successful dispatch.
                    fallback_last_message_file=spec["fallback_last_message_file"],
                    codex_model=spec["codex_model"], codex_effort=spec["codex_effort"],
                    dispatch_cmd=spec["dispatch_cmd"],
                    # How far this session's sandbox was opened, and on whose
                    # declaration — an audit fact, logged where the dispatch is.
                    codex_shell_grant=spec.get("codex_shell_grant"),
                )
                member = {
                        "id": sid,
                        "title": s.get("title", sid),
                        "prompt_file": str(prompt_file),
                        "items": s.get("items", []),
                        "model": s.get("model"),
                        "reasoning": reason_tier,
                        "backend": "codex",
                        "codex_model": spec["codex_model"],
                        "codex_effort": spec["codex_effort"],
                        # How far this session's sandbox was opened (null = the
                        # default workspace-write, nothing added).
                        "codex_shell_grant": spec.get("codex_shell_grant"),
                        "dispatch_cmd": spec["dispatch_cmd"],
                        "last_message_file": spec["last_message_file"],
                        # Ladder walk on a dispatch-time refusal; null = exhausted
                        # = NO-CODEX (surface + stop, never rescue on Claude).
                        "fallback_model": spec["fallback_model"],
                        "fallback_reasoning": spec["fallback_reasoning"],
                        "fallback_cmd": spec["fallback_cmd"],
                        "fallback_last_message_file": spec["fallback_last_message_file"],
                        # HONESTY PAIR, the Codex-side counterpart of
                        # effort_enforced/effort_mechanism: what the manifest asked
                        # for, and how much of it survived translation.
                        "translated_from": tr["translated_from"],
                        "effort_fidelity": tr["effort_fidelity"],
                        "translation": tr,
                        # D4e (deferred, recommended default): the stamp stays
                        # honest — OpenAI executed — but the cross-family verifier
                        # would mean spawning Claude from the Codex shell, i.e. the
                        # same cross-vendor egress in reverse, which an operator who
                        # chose Codex may specifically not want. Review-class gates
                        # are on-box/human until a Claude verifier is elected;
                        # deterministic gates (tests, argv) are family-neutral.
                        "executor_family": "openai",
                        "verifier_family": "anthropic",
                        "verifier_mode": "on_box_human",
                        **({"on_dispatch_refusal": spec["on_dispatch_refusal"]}
                           if spec.get("on_dispatch_refusal") else {}),
                }
                # ESC-03 — same stamping as the Claude lane, same function.
                _record_escalation(plan_dir, sid, spec.get("escalation"), member)
                batch.append(member)
                continue
            if spec["backend"] == "codex":
                # Two-layer dispatch (s04): `prompt_text` here is the WRAPPER
                # prompt for a fixed Claude wrapper model; the session's real
                # prompt stays in prompt_file (codex reads it via stdin). No
                # Anthropic thinking directive is prepended — the reasoning tier
                # rides the `-c model_reasoning_effort=` flag instead.
                member = {
                        "id": sid,
                        "title": s.get("title", sid),
                        "subagent_type": s.get("dispatch", {}).get("subagent_type"),
                        "prompt_file": str(prompt_file),
                        "prompt_text": spec["wrapper_prompt"],
                        "items": s.get("items", []),
                        "model": s.get("model"),
                        "model_arg": _CODEX_WRAPPER_MODEL,
                        "backend": "codex",
                        "codex_model": spec["codex_model"],
                        "codex_effort": spec["codex_effort"],
                        "codex_cmd": spec["codex_cmd"],
                        "reasoning": reason_tier,
                        # v1.15 5.6-only walk (sol→terra@max→NO-CODEX); null = ladder
                        # exhausted = NO-CODEX (surface + stop, never inherit Claude).
                        "fallback_model": spec["fallback_model"],
                        "fallback_reasoning": spec["fallback_reasoning"],
                        # Ready-made wrapper prompt for the fallback re-dispatch.
                        "fallback_prompt_text": spec["fallback_prompt_text"],
                        # PROVIDER-SYMMETRIC verification (s06): the NON-executing
                        # family verifies. Degradation is within-family (openai
                        # ladder stays openai; NO-CODEX = stop, never Claude), so
                        # the family stamped here holds for the actual executor;
                        # the closeout's backend/degraded_from stays authoritative.
                        "executor_family": "openai",
                        "verifier_family": "anthropic",
                        "verifier_mode": "cross_family",
                        **({"on_dispatch_refusal": spec["on_dispatch_refusal"]}
                           if spec.get("on_dispatch_refusal") else {}),
                }
                # ESC-03 — same stamping as the Claude lane, same function.
                _record_escalation(plan_dir, sid, spec.get("escalation"), member)
                batch.append(member)
                continue
            prompt_text = prompt_file.read_text() if prompt_file.exists() else ""
            # PL-01 — ADVISORY containment (contract §Verdict). The member is TOLD
            # its worktree; nothing in the harness confines it there, because S06
            # measured `EnterWorktree(path=…)` being REFUSED from a freshly
            # dispatched subagent. The integration session checks whether it
            # obeyed (§3 rule 6) rather than assuming it did.
            wt_path = worktrees.get(sid)
            if wt_path:
                group = wt.group_of(s)
                st = wt.load_state(plan_dir, group) or {}
                prompt_text = (
                    wt.prompt_preamble(wt_path, wt.member_branch(group, sid),
                                       st.get("base_ref", ""))
                    + "\n---\n\n"
                    + prompt_text
                )
            model_arg = _normalize_model(s.get("model"))
            # ESC-02 — UPWARD ESCALATION. The rung is COMPUTED here, from
            # (rework_count, stuck state, refused-rungs set), never read from a
            # pre-decided "next rung" someone wrote earlier: a crash between this
            # dispatch and `apply` re-derives the SAME rung on resume instead of
            # climbing twice. Every rung comes from resolve_route.escalate() — the
            # shared funnel — so this adds no second ladder table. Gated on the
            # manifest stamp (escalation.ESCALATION_MIN_SCHEMA), so a plan built
            # before the feature dispatches byte-identically.
            esc_desc = _escalation_descriptor(plan_dir, manifest, s, model_arg,
                                              reason_tier, lane="claude")
            declared_agent = s.get("dispatch", {}).get("subagent_type")
            if esc_desc and esc_desc.get("declined"):
                # Said OUT LOUD. An inert escalation announced as a real one is the
                # failure class this repo keeps closing, so the decline is a warning
                # and an event — never a silent shrug.
                would = esc_desc["would_have"]
                why = {
                    "declared_subagent_type":
                        f"it pins dispatch.subagent_type={declared_agent!r}, a non-tier "
                        "agent whose own frontmatter governs its model and effort",
                }.get(esc_desc["declined"], esc_desc["declined"])
                print(
                    f"WARNING: session {sid} would escalate to "
                    f"{would['model']}@{would['reasoning'] or 'unset'} but {why} — "
                    "dispatching AS AUTHORED, because escalation cannot bind here.",
                    file=sys.stderr,
                )
                rsi.log_event(plan_dir, "escalation_declined", session_ids=[sid],
                              reason=esc_desc["declined"],
                              subagent_type=declared_agent, would_have=would)
            elif esc_desc and esc_desc["rung"] > 0:
                # A tier agent IS the effort mechanism, so the escalated cell
                # replaces it — that is the climb, not a loss of author intent.
                model_arg = esc_desc["ran"]["model"]
                reason_tier = esc_desc["ran"]["reasoning"]
                declared_agent = None
            # Settled BEFORE the effort block, which reads it: a climb that binds
            # changes what the effort-mechanism warning has to say.
            escalated = bool(esc_desc and esc_desc["rung"] > 0)
            # EFFORT ENFORCEMENT (see _TIER_AGENTS above). Prefer a tier-agent
            # definition, which carries `effort:` frontmatter and is the only
            # mechanism that actually binds a subagent's effort level. An explicit
            # manifest `dispatch.subagent_type` always wins — the author asked for
            # a specific agent, and that agent's own frontmatter governs its tier.
            subagent_type = declared_agent
            effort_enforced = False
            if declared_agent:
                # A named agent brings its own model+effort; we cannot know them
                # from here, so report the tier as declared-by-agent, not enforced.
                effort_mechanism = "agent_definition"
            else:
                tier = _tier_agent(model_arg, reason_tier)
                if tier:
                    subagent_type = tier
                    effort_enforced = True
                    effort_mechanism = "tier_agent"
                else:
                    effort_mechanism = "prompt_directive_advisory"
            if not effort_enforced and effort_mechanism == "prompt_directive_advisory":
                # Fallback path: prepend the directive so the INTENT still reaches
                # the subagent as prose, but do not pretend it sets the effort dial.
                # `low`/unset prepends nothing. Keyed on the EFFECTIVE tier
                # (`reason_tier`), not the manifest's raw value: after an ESC-02
                # climb those differ, and prose naming the authored tier on an
                # escalated attempt would be advisory text that is also wrong.
                reason_line = _reasoning_directive(reason_tier)
                if reason_line:
                    prompt_text = reason_line + "\n\n" + prompt_text
                if escalated:
                    # An ESCALATED cell with no tier agent is a climb that does not
                    # bind, and the `low` suppression below would hide it entirely
                    # (haiku@unset climbs to sonnet@LOW, which has no tier agent).
                    # A silent inert escalation is the failure class this repo keeps
                    # closing, so say it whatever the tier.
                    print(
                        f"WARNING: session {sid} escalated to "
                        f"{model_arg or '(inherit)'}@{reason_tier or 'unset'}, which has NO "
                        "tier agent — the climb changes the requested model but NOT the effort "
                        "dial, which the subagent inherits from the orchestrator. The rung is "
                        "still recorded as requested, never as attested.",
                        file=sys.stderr,
                    )
                elif reason_tier not in ("", "low"):
                    print(
                        f"WARNING: session {sid} declares reasoning {reason_tier!r} on model "
                        f"{model_arg or '(inherit)'!r}, which has no tier agent — the subagent "
                        "will INHERIT the orchestrator's session effort. The prepended thinking "
                        "directive is advisory prose, not an effort control "
                        "(only `ultrathink` is a recognized keyword, and it does not change the "
                        "effort sent to the API). Add a tier agent, or set "
                        "CLAUDE_CODE_EFFORT_LEVEL for the run.",
                        file=sys.stderr,
                    )
            # Unrecognized (non-Claude / typo) model → inherit, but WARN (today it
            # was silent): the subagent falls back to the orchestrator's model and
            # the plan's per-session model directive is quietly lost. (A model that
            # LOOKS Codex-declared never reaches here — it hard-fails pre-lock.)
            if model_arg is None and str(s.get("model") or "").strip():
                print(
                    f"WARNING: session {sid} model {s.get('model')!r} did not normalize to a "
                    "dispatchable token (fable/opus/sonnet/haiku) — the subagent will inherit "
                    "the orchestrator's model.",
                    file=sys.stderr,
                )
            fb = _fallback_for(model_arg, "anthropic", refused=_refused(esc_desc))
            eg = egress_state()
            dispatch_description = _shadow_dispatch_description(plan_dir, s, prompt_text)
            member = {
                    "id": sid,
                    # Session title so the orchestrator can announce by NAME
                    # ("Dispatching 'Migrate corpus schema' (s03)…") — a wall of
                    # bare sNN ids is illegible in the stream.
                    "title": s.get("title", sid),
                    "subagent_type": subagent_type,
                    "prompt_file": str(prompt_file),
                    "prompt_text": prompt_text,
                    "items": s.get("items", []),
                    # `model` is the raw manifest value (transparency); `model_arg`
                    # is the normalized token to pass to Task's `model` param, or
                    # null to inherit. The orchestrator MUST honor `model_arg`.
                    "model": s.get("model"),
                    "model_arg": model_arg,
                    "backend": "claude",
                    # reasoning tier (low|medium|high|xhigh|max or "") for the
                    # orchestrator to announce.
                    "reasoning": reason_tier,
                    # HOW that tier is (or is not) actually applied. `tier_agent`
                    # = bound by the resolved subagent's `effort:` frontmatter;
                    # `agent_definition` = the manifest named an agent, whose own
                    # frontmatter governs; `prompt_directive_advisory` = NOTHING
                    # binds it, the subagent inherits the session effort and the
                    # prepended directive is prose only. The orchestrator MUST
                    # announce the honest mechanism, never claim an unenforced tier.
                    "effort_enforced": effort_enforced,
                    "effort_mechanism": effort_mechanism,
                    # Reactive-degradation target if this model is refused at dispatch
                    # (Fable→Opus 4.8 → Sonnet 5, floor at Sonnet); null = no lower
                    # judgement-safe tier. Paired with the per-target reasoning
                    # tier from _DEGRADE_EFFORT (opus xhigh, sonnet high).
                    #
                    # SUPPRESSED ON AN ESCALATED MEMBER (ESC-02) — the field is made
                    # null so the choice is removed rather than ranked; see
                    # `_refusal_instruction` for the full reasoning.
                    "fallback_model": None if escalated else (fb[0] if fb else None),
                    "fallback_reasoning": None if escalated else (fb[1] if fb else None),
                    **({"on_dispatch_refusal": _refusal_instruction(
                        sid, model_arg, reason_tier)} if escalated else {}),
                    # PROVIDER-SYMMETRIC verification (s06): a Claude-executed
                    # session is verified by the OpenAI family — derived from the
                    # ACTUAL resolved executor (this backend), never from the
                    # global active_provider. When the working tree is restricted
                    # without an egress opt-in, Codex verification would itself be
                    # forbidden egress: the disposition is on_box_human (human/
                    # on-box review at the checkpoint, recorded VERIFIED-ON-BOX or
                    # BLOCKED) and NO Codex process may start for verification.
                    "executor_family": "anthropic",
                    "verifier_family": "openai",
                    "verifier_mode": (
                        "on_box_human"
                        if eg["restricted_hit"] and not eg["opted_in"]
                        else "cross_family"
                    ),
                    **(
                        {"executor_policy_note": spec["executor_policy_note"]}
                        if spec.get("executor_policy_note")
                        else {}
                    ),
                }
            # ESC-02 — the honesty pair for the climb, shaped like `degraded_from`
            # (which records a DOWNWARD substitution) so a consumer reading the two
            # sees the same fields for the same kind of fact. SAME function as the
            # codex lane (ESC-03), so the two payloads cannot drift apart.
            _record_escalation(plan_dir, sid, esc_desc, member)
            if wt_path:
                # The orchestrator MUST pass this to the Task call's `cwd`-shaped
                # instruction (it is already in `prompt_text`); it is surfaced
                # structurally too so the payload is machine-checkable.
                member["worktree"] = wt_path
                member["worktree_branch"] = wt.member_branch(wt.group_of(s), sid)
                member["isolation"] = pc.ISOLATION_WORKTREE
            # Preserve the established Claude-harness payload byte-for-byte
            # while telemetry is disabled or a session is ineligible. The
            # optional field appears only for a real, one-use marker.
            if dispatch_description:
                # The Task description is not the subagent prompt. The opaque
                # marker is passed verbatim by SKILL.md, never prepended to the
                # primary work instructions.
                member["dispatch_description"] = dispatch_description
            batch.append(member)
        payload = {"action": "dispatch", "active_provider": provider, "batch": batch}
        if pc_warnings:
            payload["parallel_group_warnings"] = pc_warnings
        if codex_harness:
            payload["harness"] = "codex"
        _out(payload, plan_dir=plan_dir)
    except Exception:
        rsi.release_lock(plan_dir)
        raise


# --------------------------------------------------------------------------
# DISPATCH RECEIPT (2026-07-28). Under `--harness codex` the orchestrator is an
# interactive Codex model told, IN PROSE, to run each session's `dispatch_cmd`.
# Nothing structural made it. A model that instead did the work inline produced a
# perfectly valid closeout, `apply` took it, the session went DONE — and the
# plan's per-session model selection silently evaporated: every session ran on
# the orchestrator's own model. That is the failure this check catches.
#
# WHAT IT PROVES, exactly: that a Codex process ran for this session and left the
# file `codex exec -o` writes. It does NOT prove the closeout came out of that
# process — an orchestrator with a shell can `touch` the path. It converts a
# silent omission (a model that just skipped the dispatch) into a refusal, and
# leaves deliberate forgery undefended. Say it that way; do not overclaim.
#
# Claude-harness sessions log no `codex_dispatch` event, so this never fires for
# them — including the two-layer Claude wrapper around a Codex model, whose -o
# file lives in /tmp and is relayed through the wrapper's transcript.
# --------------------------------------------------------------------------
def _last_codex_dispatch(plan_dir, session_id):
    """The MOST RECENT `codex_dispatch` event for `session_id`, or None when the
    session was never dispatched under `--harness codex`.

    Most recent, not any: after a re-dispatch it is the newest attempt that has
    to have produced output. An older attempt's file still sitting on disk must
    not vouch for a newer one that never ran."""
    try:
        lines = (Path(plan_dir) / "run.ndjson").read_text().splitlines()
    except OSError:
        return None
    last = None
    for ln in lines:
        try:
            rec = json.loads(ln)
        except ValueError:
            continue
        if rec.get("event") == "codex_dispatch" and session_id in (rec.get("session_ids") or []):
            last = rec
    return last


def _missing_dispatch_receipt(plan_dir, session_id):
    """Plain-language refusal reason when this session's closeout has no
    corresponding Codex dispatch, or None to accept it.

    The receipt is the EXISTENCE of the `-o` last-message file named by the
    session's most recent `codex_dispatch` event (or its fallback twin). Not its
    contents: an empty file means `codex exec` ran and said nothing, which is the
    contract's "missing closeout" row — that already blocks the session, with a
    better reason than this check could give.

    No replay bypass is needed and none exists: `_codex/` lives inside the plan
    directory and only ever removes the ONE file the next attempt is about to
    overwrite, so re-running `apply` finds the same receipt. Deleting `_codex/`
    by hand costs you the ability to re-apply — deliberately, since after that
    nothing on disk says the dispatch happened."""
    ev = _last_codex_dispatch(plan_dir, session_id)
    if ev is None:
        return None                                  # not a Codex-harness session
    paths = [p for p in (ev.get("last_message_file"),
                         ev.get("fallback_last_message_file")) if p]
    # The event records the path exactly as `begin` spelled it, which is RELATIVE
    # whenever the plan dir was given relatively (the shipped loop-smoke fixture
    # does exactly that). Also look each one up under THIS plan dir's `_codex/`,
    # where it always lives, so `apply` from a different cwd than `begin` is a
    # miss on the literal path and still not a refusal.
    codex_dir = Path(plan_dir) / "_codex"
    if any(os.path.exists(p) or (codex_dir / os.path.basename(p)).exists()
           for p in paths):
        return None
    return (
        f"no Codex dispatch receipt for {session_id}. `begin --harness codex` emitted a "
        f"dispatch command for this session, but the file `codex exec` writes with `-o` "
        f"({', '.join(paths) or 'none recorded'}) does not exist — so no Codex process "
        f"ran for it, and this closeout cannot have come from the model the plan chose. "
        f"Run the session's `dispatch_cmd` (in run.ndjson's last `codex_dispatch` event) "
        f"and apply its `-o` file. Never write, repair or synthesise a closeout, and "
        f"never do the session's work inline — that silently runs the whole plan on the "
        f"orchestrator's own model."
    )


def _crash_point(label):
    """Test-only crash injection, same shape as `plan_mutate._crash_point`.

    `PLAN_APPLY_CRASH=<label>` kills the process there with a REAL `os._exit`,
    so the crash-replay tests exercise the same recovery path a power cut would
    rather than an exception a `finally` could tidy up after.
    """
    if os.environ.get("PLAN_APPLY_CRASH") != label:
        return
    sys.stderr.write(f"[crash-injection] dying at apply:{label}\n")
    sys.stderr.flush()
    os._exit(70)


def cmd_apply(plan_dir, session_id, output_file, dry_run_shipping=False):
    manifest = mio.load_manifest(plan_dir)
    by_id = mio.session_by_id(manifest)
    if session_id not in by_id:
        raise SystemExit(f"unknown session {session_id!r}")
    session = by_id[session_id]
    items = session.get("items", [])

    # Same fail-closed treatment as a malformed closeout: BLOCKED + halt, never a
    # silent DONE. Checked BEFORE the closeout is even read — its content is not
    # the question here.
    no_receipt = _missing_dispatch_receipt(plan_dir, session_id)
    if no_receipt:
        ab.apply_mutation(
            _html_path(plan_dir), session_id, status="BLOCKED",
            note="closeout missing_dispatch_receipt",
        )
        rsi.set_halt(plan_dir, f"{session_id}: missing_dispatch_receipt — {no_receipt}",
                     session_id)
        _out({"applied": False, "failure": "missing_dispatch_receipt", "reason": no_receipt},
             plan_dir=plan_dir)
        sys.exit(1)

    raw = Path(output_file).read_text()

    # `plan_impact` names OTHER sessions, so its check needs the whole graph —
    # and it only applies on plans stamped at the schema version that introduced
    # it, so a plan built before this feature never gains a new refusal mode.
    result = cp.run_pipeline(
        raw, session_id, items,
        all_session_ids=mio.all_session_ids(manifest),
        plan_schema_version=manifest.get("plan_schema_version"),
    )
    if result["status"] != "ok":
        # Closeout invalid in some way -> BLOCK the session + halt.
        reason = _failure_reason(result)
        ab.apply_mutation(
            _html_path(plan_dir),
            session_id,
            status="BLOCKED",
            note=f"closeout {result['status']}: {reason}",
        )
        rsi.set_halt(plan_dir, f"{session_id}: {result['status']} — {reason}", session_id)
        _out(
            {"applied": False, "failure": result["status"], "reason": reason, "diagnostics": result},
            plan_dir=plan_dir,
        )
        sys.exit(1)

    parsed = result["parsed"]
    # P3: persist BEFORE touching HTML.
    cp.persist(plan_dir, session_id, parsed)

    # Cost/audit transparency (added 2026-07): if the orchestrator substituted a
    # lower model after a dispatch failure (e.g. Fable unavailable → Opus @ xhigh),
    # the closeout carries `degraded_from`. Surface it in the event log so the run's
    # real cost + capability are legible, not a silently-inherited mystery model.
    degraded_from = parsed.get("degraded_from")
    if degraded_from:
        rsi.log_event(plan_dir, "model_degraded", session_ids=[session_id],
                      degraded_from=degraded_from)

    # ESC-02 — the same transparency for an UPWARD substitution. `cp.persist`
    # above already carried the field into `_closeouts/<sid>.json` verbatim (the
    # closeout digest ignores optional audit fields, so it never reads as
    # state-drift); this is the event-log half, so the run's real cost and
    # capability are legible from run.ndjson alone.
    escalated_from = parsed.get("escalated_from")
    if escalated_from:
        rsi.log_event(plan_dir, "escalation_applied", session_ids=[session_id],
                      phase="closeout", escalated_from=escalated_from,
                      model_ran_source=parsed.get("model_ran_source", esca.SOURCE_REQUESTED))

    res = parsed["result"]
    completed = parsed["items_completed"]
    blocked = parsed["items_blocked"]
    notes = parsed.get("notes", {})

    # Apply item-level statuses.
    for iid in completed:
        ab.apply_mutation(
            _html_path(plan_dir), iid, status="DONE", note=notes.get(iid, "completed")
        )
    for iid in blocked:
        ab.apply_mutation(
            _html_path(plan_dir), iid, status="BLOCKED", note=notes.get(iid, "blocked")
        )

    # PL-01 — commit an isolated member's worktree onto ITS OWN BRANCH.
    #
    # Contract M1 bars the MEMBER from committing; it does not bar the
    # orchestrator, and something has to produce the commits the integration
    # merge consumes. Doing it here means exactly ONE committer, running one
    # session at a time as closeouts are applied — so the `.git/index.lock`
    # contention the capability ledger leaves OPEN is never entered.
    #
    # Runs on PARTIAL too, deliberately: a partial member's branch is never
    # merged (contract §4), but its work belongs on a ref rather than loose in a
    # worktree, which is the S02 §6 lesson about git-invisible output.
    worktree_commit = None
    if res in ("DONE", "PARTIAL"):
        try:
            worktree_commit = wt.commit_member(
                plan_dir, manifest, session_id,
                f"plan({wt.group_of(session)}): {session_id} — {res.lower()}",
            )
        except wt.WorktreeError as e:
            ab.apply_mutation(_html_path(plan_dir), session_id, status="BLOCKED",
                              note=f"worktree commit failed: {e}")
            rsi.set_halt(plan_dir, f"{session_id}: worktree commit failed — {e}", session_id)
            _out({"applied": False, "failure": "worktree_commit_failed", "reason": str(e)},
                 plan_dir=plan_dir)
            sys.exit(1)

    # Verify gates (the "don't ship trash" boundary): a self-reported DONE with a
    # declared `verify` block is NOT finalized here — the session stays DOING and
    # the orchestrator runs the verify sub-loop (verify-begin → … → verify-finalize)
    # which flips DOING→DONE only after every gate passes (or →PARTIAL on rework /
    # →BLOCKED on exhaustion). See verify.py.
    verify_block = session.get("verify")
    verify_pending = bool(verify_block) and res == "DONE"

    # Apply session-level status.
    session_note = notes.get(session_id)
    # OPTIONAL, backward-compatible: fold plan-vs-reality drift into the note so
    # it's visible on the session card, not only in _closeouts/<sid>.json. Absent
    # in older/routine closeouts — nothing changes when the key is missing (SG-02).
    deviations = parsed.get("deviations") or []
    if deviations:
        dev_text = "deviations: " + "; ".join(str(d) for d in deviations)
        session_note = f"{session_note} — {dev_text}" if session_note else dev_text
    if res == "DONE":
        if verify_pending:
            # Leave the DOING status `begin` set; verify-finalize finalizes it.
            rsi.log_event(plan_dir, "verify_pending", session_ids=[session_id],
                          gates=verify_block.get("gates", []))
        else:
            ab.apply_mutation(
                _html_path(plan_dir), session_id, status="DONE",
                note=session_note or "session complete",
            )
    elif res == "PARTIAL":
        ab.apply_mutation(
            _html_path(plan_dir),
            session_id,
            status="PARTIAL",
            note=session_note or "partial — needs continuation",
        )
    elif res == "BLOCKED":
        ab.apply_mutation(
            _html_path(plan_dir),
            session_id,
            status="BLOCKED",
            note=session_note or "session blocked",
        )
        # RS-04 — the decision brief IS the halt notice. A blocked plan is read by
        # an operator who wants to know what to DO, and making them open
        # `_closeouts/<sid>.json` for the options and the recommendation is how a
        # brief becomes a file nobody reads.
        rsi.set_halt(plan_dir, f"{session_id}: subagent reported BLOCKED", session_id,
                     detail=cp.format_decision_brief(parsed.get("decision_brief")))

    # TEL-01 — a TERMINAL closeout with no verify block is its own resolution
    # moment: DONE here never gets a `verify_finalize("passed")` write because
    # no verify block exists, so it must be recorded as `done_unverified`
    # instead of silently never appearing in the ledger at all. A verify-pending
    # DONE is recorded later, by verify.py, once its gates actually resolve —
    # never here, or a gated session would double-count as both.
    # PARTIAL is DELIBERATELY excluded here, and that exclusion is the whole
    # reason this condition names its two members explicitly. PARTIAL is not a
    # resolution — it is the session saying "dispatch me again", and the loop
    # does. Its real resolution arrives on a later apply or through verify, and
    # THAT is the row the ledger wants; writing one row per intermediate PARTIAL
    # would inflate the attempt count that attempts-per-success divides by.
    # The asymmetry the reviewer flagged is intentional: `batch_completed` IS
    # logged for a PARTIAL (so `_apply_attempt` advances, because an apply did
    # happen), while no ledger row is written (because nothing resolved).
    # KNOWN GAP, accepted: a plan ABANDONED after a PARTIAL never resolves, so
    # it leaves no ledger row at all. Recording abandonment needs a signal the
    # harness does not have — nothing distinguishes "abandoned" from "not
    # finished yet" — so inventing a row here would guess. `retire-session`
    # already covers the case where abandonment is DECLARED (result=wontfix).
    if not verify_pending and res in ("DONE", "BLOCKED"):
        # TEL-01 finding 3 — the real attempt ordinal, not a hardcoded 1: see
        # `_apply_attempt`'s docstring for why a constant here silently drops a
        # re-applied resolution.
        _attempt = _apply_attempt(plan_dir, session_id)
        outc.write(
            plan_dir, session_id,
            resolution="apply_terminal",
            result="done_unverified" if res == "DONE" else "blocked",
            # rework_count is 0 BY DEFINITION here, never `_attempt - 1`:
            # this branch fires only when the session has NO verify block, so
            # no gate ever ran and no gate rework ever happened. Deriving it
            # from the apply ordinal recorded a re-application as a gate
            # rework and mixed these rows with verify.py's real counts in any
            # aggregate over the ledger.
            verified=False, attempt=_attempt, rework_count=0,
        )

    cp.mark_replayed(plan_dir, session_id)
    rsi.log_event(plan_dir, "batch_completed", session_ids=[session_id], result=res)

    # A subagent may also request a human checkpoint via human_checkpoint_reason.
    # When verify is pending, the checkpoint is applied by verify-finalize AFTER
    # the gates pass — never spend human attention on work that fails its gates.
    hc = parsed.get("human_checkpoint_reason")
    # OR-03: a post-session `human_checkpoint_reason` is the "AWAITS_REVIEW ack"
    # gate TYPE. Resolve its per-gate policy (fail-closed default = block). Only a
    # session whose dispatch opts in — eligible type, NOT requires_human_checkpoint,
    # guards nothing irreversible — auto-continues; everything else still blocks
    # for review exactly as before. This never fires for verify_pending (the
    # checkpoint is applied by verify-finalize AFTER the gates pass).
    hc_auto_continue = (
        bool(hc)
        and res != "BLOCKED"
        and not verify_pending
        and gp.is_notify_and_continue("session_review_ack", session.get("dispatch"))
    )
    if hc and res != "BLOCKED" and not verify_pending:
        if hc_auto_continue:
            # Rubber-stamp gate: proceed without waiting. Record the auto-continue
            # in run.ndjson (the authoritative trail) and push a notification so the
            # operator keeps visibility instead of having to poll.
            rsi.log_event(
                plan_dir, "gate_auto_continue", session_ids=[session_id],
                gate_type="session_review_ack", reason=hc, policy="notify-and-continue",
            )
            notify = rsi.notify_gate_continue(plan_dir, session_id, "session_review_ack", hc)
            rsi.log_event(
                plan_dir, "gate_notify", session_ids=[session_id],
                gate_type="session_review_ack", notify=notify.get("action"),
            )
        else:
            ab.apply_mutation(
                _html_path(plan_dir), session_id, status="AWAITS_REVIEW", note=f"checkpoint: {hc}"
            )
            rsi.log_event(plan_dir, "checkpoint_reached", session_ids=[session_id], reason=hc)

    # RP-05 — the REPLAN park. Deferred, never dropped, behind two other gates:
    #   * a PENDING VERIFY block — verify-finalize parks it once the gates pass
    #     (never spend an operator's attention on work that fails its gates);
    #   * an UNACKED HUMAN CHECKPOINT — `ack-checkpoint` parks it the moment the
    #     human gate clears (precedence is an order, not a discard).
    # A BLOCKED session already halts the plan on its own reason, and that reason
    # is the one the operator needs first.
    parked_on_checkpoint = bool(hc) and res != "BLOCKED" and not hc_auto_continue
    plan_impact = rp.closeout_impact(parsed, manifest)
    replan = None
    if plan_impact and res != "BLOCKED" and not verify_pending and not parked_on_checkpoint:
        _crash_point("park")
        replan = rp.park(plan_dir, session_id, plan_impact, manifest)

    # PS-01 structural DONE-gate (PRIMARY, browser-free). The #1 recurring
    # correction across three weeks of sessions — escalating to profanity —
    # was "you didn't update the plan HTML," and it regressed TWICE after
    # being "fixed" with instruction/prompt guidance alone. Made structural
    # here: re-read PLAN.html from disk (never trust the in-memory string a
    # mutation just wrote) and confirm every status this closeout claims
    # ACTUALLY landed, plus that the dashboard's repaint script still parses
    # (no-undef). A mismatch overrides whatever status was just set — the
    # session is BLOCKED + halted with the concrete mismatch attached, not
    # silently left looking DONE while the dashboard disagrees.
    expected = {iid: "DONE" for iid in completed}
    expected.update({iid: "BLOCKED" for iid in blocked})
    if not verify_pending:
        if hc and res != "BLOCKED" and not hc_auto_continue:
            expected[session_id] = "AWAITS_REVIEW"
        elif res in ("DONE", "PARTIAL", "BLOCKED"):
            # An auto-continued rubber-stamp gate falls through here: the session
            # keeps its self-reported result (DONE/PARTIAL) rather than parking in
            # AWAITS_REVIEW, so the structural gate expects that terminal status.
            expected[session_id] = res

    gate = sg.run_gate(plan_dir, expected)
    if gate["status"] == "failed":
        reason = (
            "PLAN.html structural gate failed — the dashboard did not actually "
            "update to match this closeout: " + "; ".join(gate["reasons"])
        )
        ab.apply_mutation(_html_path(plan_dir), session_id, status="BLOCKED", note=reason)
        rsi.set_halt(plan_dir, f"{session_id}: structural gate failed — {reason}", session_id)
        rsi.log_event(
            plan_dir, "structural_gate_failed", session_ids=[session_id], reasons=gate["reasons"]
        )
        _out(
            {
                "applied": True,
                "session": session_id,
                "result": "BLOCKED",
                "structural_gate": gate,
                "halted": True,
                "reason": reason,
            },
            plan_dir=plan_dir,
        )
        sys.exit(1)

    # Surface the resolved verify + post_session blocks so the orchestrator knows
    # whether a verify sub-loop and/or a shipping pipeline is pending after this
    # closeout (read from the manifest — the subagent never overrides them).
    post_session = session.get("post_session")
    out = {
        "applied": True,
        "session": session_id,
        "result": res,
        "items_completed": completed,
        "items_blocked": blocked,
        "halted": rsi.is_halted(plan_dir),
        "verify_pending": verify_pending,
        "verify": verify_block,
        "post_session": post_session,
        "structural_gate": gate,
    }
    # RS-04 — surface the brief on BOTH parking surfaces: a BLOCKED halt (rendered
    # above into HALT_NOTICE.txt) and a human checkpoint (where the brief is
    # recommended, not required — it is what makes the checkpoint answerable).
    brief = parsed.get("decision_brief")
    if brief:
        out["decision_brief"] = brief
        out["decision_brief_text"] = cp.format_decision_brief(brief)
    elif hc:
        # Not a refusal: `decision_brief` is only REQUIRED on BLOCKED. But an
        # unbriefed checkpoint is the "please review" anti-pattern the contract
        # names, so it leaves a trace instead of passing unremarked.
        rsi.log_event(plan_dir, "decision_brief_missing", session_ids=[session_id],
                      gate_type="session_review_ack")
    if worktree_commit:
        out["worktree_commit"] = worktree_commit
        out["worktree_branch"] = wt.member_branch(wt.group_of(session), session_id)
    if replan:
        # The brief IS the output — the orchestrator presents it to the operator
        # verbatim, exactly as it does a pre-dispatch checkpoint brief. RP-08:
        # `brief_text` is that presentation, and `next` names the recommendation
        # step when the brief still owes one.
        out["replan"] = _replan_out(replan)
        out["next"] = _replan_next_step(plan_dir, session_id, replan)
    elif plan_impact:
        out["replan_deferred"] = {
            "invalidates": plan_impact.get("invalidates"),
            "reason": plan_impact.get("reason"),
            "until": "verify-finalize" if verify_pending else "ack-checkpoint",
        }
    if not verify_pending:
        # SECONDARY, best-effort visual confirmation (PS-01) — only meaningful
        # once the session has actually finalized here (verify-pending sessions
        # get their own render-verify pass at verify-finalize). Never allowed to
        # raise: an environment with no headless Chrome must still finish.
        try:
            out["render_verify"] = rv.check(plan_dir)
        except Exception as e:  # pragma: no cover - defensive
            out["render_verify"] = {"status": "unavailable", "reason": f"render-verify errored: {e}"}
    if dry_run_shipping and post_session:
        out["shipping_dry_run"] = shp.ship_begin(plan_dir, session_id, dry_run=True)
    _out(out, plan_dir=plan_dir)


def _failure_reason(result):
    if "violations" in result:
        return "; ".join(result["violations"])
    return result.get("error", result["status"])


def cmd_checkpoint(plan_dir, session_id):
    # Surface the author's decision brief (reason/decision/options) so the
    # operator sees WHAT they are deciding, not just "review sNN". None on
    # legacy (pre-brief) manifests — the orchestrator then derives context
    # from the session's human_summary/deliverable.
    brief = None
    try:
        manifest = mio.load_manifest(plan_dir)
        sess = mio.session_by_id(manifest).get(session_id) or {}
        brief = sess.get("dispatch", {}).get("checkpoint")
    except mio.ManifestError:
        pass
    note = "awaiting human review before dispatch"
    if brief and brief.get("decision"):
        note = f"awaiting human decision: {brief['decision']}"
    ab.apply_mutation(
        _html_path(plan_dir),
        session_id,
        status="AWAITS_REVIEW",
        note=note,
    )
    rsi.log_event(
        plan_dir,
        "checkpoint_reached",
        session_ids=[session_id],
        reason=(brief or {}).get("decision"),
    )
    _out(
        {
            "checkpoint": session_id,
            "checkpoint_brief": brief,
            "resume_with": f"/plan-execute {plan_dir} --resume",
        },
        plan_dir=plan_dir,
    )


def cmd_ack_checkpoint(plan_dir, session_id, note=None):
    """RP-01 — approve a POST-SESSION checkpoint.

    The gap this closes: a session that closed DONE and then parked at
    AWAITS_REVIEW had NO approve path. `--resume` re-dispatched it (redoing paid
    work, re-tripping the same gate, looping), and the documented workaround was
    to hand-call `article_block.apply_mutation` from a Python REPL.
    """
    _refuse_if_halted(plan_dir, f"ack {session_id}")
    manifest = mio.load_manifest(plan_dir)
    if session_id not in mio.session_by_id(manifest):
        raise SystemExit(f"unknown session {session_id!r}")

    statuses = _statuses(plan_dir)
    status = statuses.get(session_id, "TODO")

    if status == "DONE":
        # Idempotent: acking an already-acked (or never-parked) session is a
        # no-op success, not an error — a retried command must not halt a run.
        _out(
            {"acked": False, "session": session_id, "status": "DONE",
             "already": True, "message": f"{session_id} is already DONE — nothing to ack"},
            plan_dir=plan_dir,
        )
        return

    if status != "AWAITS_REVIEW":
        raise SystemExit(
            f"refusing to ack {session_id}: it is {status}, not AWAITS_REVIEW. "
            "ack-checkpoint approves a FINISHED session parked for review; it is not a way "
            "to mark unfinished work complete. A TODO/DOING/PARTIAL session is the dispatch "
            "loop's job (`plan`), and a BLOCKED one needs investigation (`clear-halt`, then "
            "`redispatch --reason ...` if it should re-run)."
        )

    try:
        co = cp.load_closeout(plan_dir, session_id) or {}
    except (OSError, ValueError):
        co = {}
    if _awaits_review_kind(plan_dir, session_id) != "post_session":
        found = (
            f"its closeout recorded result={co.get('result')!r}, not DONE — the session has not "
            "finished, so this park is a continuation point, not a result to approve"
            if co
            else f"no closeout exists at _closeouts/{session_id}.json, so nothing on disk says "
            "this session has run"
        )
        raise SystemExit(
            f"refusing to ack {session_id}: this is a PRE-DISPATCH checkpoint — {found}. The "
            "gate is asking whether to START (or continue) the session: dispatch it with "
            "`plan --resume`. (If it DID finish and its closeout was lost, there is no ack path "
            "by design — the record is what ack trusts.)"
        )
    reason = co.get("human_checkpoint_reason")
    ab.apply_mutation(
        _html_path(plan_dir),
        session_id,
        status="DONE",
        note=note or f"checkpoint approved: {reason or 'operator ack'}",
    )
    rsi.log_event(
        plan_dir, "checkpoint_approved", session_ids=[session_id],
        reason=reason, note=note,
    )

    # Never report a mutation from the in-memory string that wrote it — re-read
    # PLAN.html from disk and confirm the flip actually landed (PS-01).
    gate = sg.run_gate(plan_dir, {session_id: "DONE"})
    if gate["status"] == "failed":
        rsi.set_halt(plan_dir, f"{session_id}: ack structural gate failed", session_id)
        _out({"acked": False, "session": session_id, "structural_gate": gate, "halted": True},
             plan_dir=plan_dir)
        sys.exit(1)

    # RP-05 precedence: a closeout carrying BOTH a human checkpoint and
    # `plan_impact` parked on the human gate FIRST. Now that gate is cleared, the
    # REPLAN park lands — the discovery is deferred by the checkpoint, never
    # discarded by it.
    out = {"acked": True, "session": session_id, "status": "DONE",
           "checkpoint_reason": reason, "structural_gate": gate,
           "next": "run `plan` (no --resume) to dispatch the next session"}
    plan_impact = rp.closeout_impact(co, manifest)
    if plan_impact:
        out["replan"] = _replan_out(rp.park(plan_dir, session_id, plan_impact, manifest))
        out["next"] = _replan_next_step(plan_dir, session_id, out["replan"])
    _out(out, plan_dir=plan_dir)


# --------------------------------------------------------------------------
# RP-05 — plan_impact -> the REPLAN checkpoint. The machinery lives in
# replan.py (shared with verify.py, which parks a verify-gated session's
# discovery only after its gates pass); this is the CLI surface.
# --------------------------------------------------------------------------
def _replan_out(parked):
    """A park, plus its brief rendered as presentable text (RP-08)."""
    if parked.get("brief"):
        parked["brief_text"] = rp.format_brief(parked["brief"])
    return parked


def _replan_next_step(plan_dir, session_id, parked):
    """The command to run next after a REPLAN park. RP-08: when the brief still
    owes a recommendation, that step comes FIRST — `resolve-replan` refuses
    until it is recorded, and finding that out by being refused is worse than
    being told here."""
    resolve_cmd = (
        f"`run.py resolve-replan {plan_dir} --session {session_id} "
        "--decision amend|retire|proceed --reason '…'`"
    )
    if parked.get("needs_recommendation"):
        return (
            f"REPLAN raised by {session_id} — record your recommendation FIRST: "
            f"`run.py recommend-replan {plan_dir} --session {session_id} "
            "--recommendation '<which option you would take, and why>'`, then "
            f"decide with {resolve_cmd}"
        )
    return f"REPLAN raised by {session_id} — decide with {resolve_cmd}"


def cmd_resolve_replan(plan_dir, session_id, decision, reason):
    try:
        result = rp.resolve(plan_dir, session_id, decision, reason)
    except rp.ResolveError as e:
        raise SystemExit(str(e)) from e
    _out(result, plan_dir=plan_dir)


# RP-08 — the brief's recommendation. A separate verb from `resolve-replan`
# because it is a separate act: the orchestrator's judgement is what the
# operator READS before deciding, not a field filled in alongside their answer.
def cmd_recommend_replan(plan_dir, session_id, recommendation):
    try:
        result = rp.record_recommendation(plan_dir, session_id, recommendation)
    except rp.ResolveError as e:
        raise SystemExit(str(e)) from e
    result["brief_text"] = rp.format_brief(result["brief"])
    _out(result, plan_dir=plan_dir)


# --------------------------------------------------------------------------
# RP-02 — redispatch
# --------------------------------------------------------------------------
# Sessions whose output is FINISHED: there is a result on the board that a
# redispatch would replace. Everything else is the dispatch loop's business.
REDISPATCHABLE = {"DONE", "WONTFIX", "DEFERRED", "BLOCKED"}
# Dependents whose recorded output was DERIVED from the producer, so a producer
# re-run makes them stale. DOING is deliberately absent (never yank an in-flight
# dispatch); WONTFIX/DEFERRED are deliberately absent (they are operator
# decisions not to do the work, not stale results); TODO/PARTIAL re-dispatch on
# their own anyway.
CASCADE_RESETTABLE = {"DONE", "AWAITS_REVIEW"}
_ARCHIVED_STATE_DIRS = ("_closeouts", "_verify_state", "_shipping_state")
_ARCHIVE_RE = re.compile(r"^r\d+\.")


def _archive_version(plan_dir, session_id):
    """Next free redispatch round number for this session (1-based)."""
    used = [0]
    for d in _ARCHIVED_STATE_DIRS:
        dpath = Path(plan_dir) / d
        if not dpath.is_dir():
            continue
        for f in dpath.glob(f"{session_id}.r*"):
            m = re.match(rf"^{re.escape(session_id)}\.r(\d+)\.", f.name)
            if m:
                used.append(int(m.group(1)))
    return max(used) + 1


def _archive_session_state(plan_dir, session_id, version):
    """Move this session's recorded state aside under a versioned name.

    Renamed, not copied — deliberately. Leaving the live `_closeouts/<sid>.json`
    in place would keep `_awaits_review_kind` reading the session as an
    already-finished post-session park, and a stale `_verify_state/<sid>.json`
    with ``outcome: passed`` makes `verify-begin` answer "already-verified" so the
    re-run's gates never execute. The archive is the history; the live slot must
    be empty for the re-run.
    """
    moved = []
    for d in _ARCHIVED_STATE_DIRS:
        dpath = Path(plan_dir) / d
        if not dpath.is_dir():
            continue
        for f in sorted(dpath.iterdir()):
            if not f.is_file() or not f.name.startswith(session_id + "."):
                continue
            rest = f.name[len(session_id) + 1 :]
            if _ARCHIVE_RE.match(rest):  # already archived by an earlier round
                continue
            f.rename(dpath / f"{session_id}.r{version}.{rest}")
            moved.append(f"{d}/{session_id}.r{version}.{rest}")
    return moved


def _dependents(manifest, session_ids):
    """Transitive closure of sessions depending (directly or not) on any of
    ``session_ids``, excluding the roots themselves."""
    out, frontier = set(), set(session_ids)
    while frontier:
        nxt = set()
        for s in manifest["sessions"]:
            sid = s["id"]
            if sid in out or sid in session_ids:
                continue
            deps = set((s.get("dispatch", {}) or {}).get("depends_on", []) or [])
            if deps & frontier:
                nxt.add(sid)
        out |= nxt
        frontier = nxt
    return [s["id"] for s in manifest["sessions"] if s["id"] in out]


def _exclusive_items(manifest, session_ids):
    """Items owned ONLY by ``session_ids`` — safe to reset with them. An item
    shared with a session that is NOT being reset keeps its status."""
    owners = {}
    for s in manifest["sessions"]:
        for iid in s.get("items", []) or []:
            owners.setdefault(iid, set()).add(s["id"])
    ids = set(session_ids)
    return sorted(i for i, own in owners.items() if own and own <= ids)


def cmd_redispatch(plan_dir, session_id, reason, allow_stale_dependents=False):
    """RP-02 — deliberately re-run a finished session, with the reason recorded."""
    # Allowed through a REPLAN halt for the same reason the mutation commands
    # are: re-running a session whose output the plan just declared invalid is
    # one of the three answers the REPLAN brief offers.
    _refuse_if_halted(plan_dir, f"redispatch {session_id}", allow_kinds=("replan",))
    if not (reason or "").strip():
        raise SystemExit(
            "refusing to redispatch without --reason: re-running a finished session throws "
            "away a recorded result, and the plan's history is the only place the WHY can "
            "live. State it in one line (what new fact invalidated the output)."
        )
    manifest = mio.load_manifest(plan_dir)
    by_id = mio.session_by_id(manifest)
    if session_id not in by_id:
        raise SystemExit(f"unknown session {session_id!r}")

    statuses = _statuses(plan_dir)
    status = statuses.get(session_id, "TODO")
    if status not in REDISPATCHABLE:
        raise SystemExit(
            f"refusing to redispatch {session_id}: it is {status}, which is not a finished "
            f"state ({'/'.join(sorted(REDISPATCHABLE))}). There is no completed result to "
            "replace — the dispatch loop still owns this session (`plan`), and a session "
            "parked at AWAITS_REVIEW is resolved with `ack-checkpoint` first."
        )

    # Dependents consumed this session's output. Because the session is being
    # re-run precisely BECAUSE that output no longer holds, their recorded
    # results are stale by construction — so cascade is the DEFAULT, and keeping
    # them is the choice that has to be stated and recorded.
    dependents = _dependents(manifest, {session_id})
    stale = [d for d in dependents if statuses.get(d, "TODO") in CASCADE_RESETTABLE]
    reset = [session_id] if allow_stale_dependents else [session_id, *stale]
    # Every dependent NOT reset, with the status it kept — including the ones the
    # operator deliberately kept via --allow-stale-dependents (those also appear
    # under accepted_stale). "Untouched" must mean untouched.
    untouched = {d: statuses.get(d, "TODO") for d in dependents if d not in set(reset)}

    archived, events = {}, []
    for sid in reset:
        version = _archive_version(plan_dir, sid)
        archived[sid] = _archive_session_state(plan_dir, sid, version)
        # ESC-02 — a redispatch is a FRESH SESSION, so it gets a fresh ladder:
        # the refused-rungs set is cleared and the generation counter bumps. The
        # bump matters to the outcome ledger, not just to dispatch: records from
        # before a deliberate re-run describe a different experiment and must not
        # be pooled with records from after it. `_archive_session_state` moves the
        # verify state aside (so rework_count restarts) but run_state is a single
        # shared file it cannot rename — hence this explicit call.
        esca.reset(plan_dir, sid, why=f"redispatch r{version}: {reason}")
        stuckp.clear(plan_dir, sid)
        note = (
            f"redispatched (r{version}): {reason}"
            if sid == session_id
            else f"redispatched (r{version}): input from {session_id} was redispatched — {reason}"
        )
        ab.apply_mutation(_html_path(plan_dir), sid, status="TODO", note=note)
        events.append(sid)

    # Items whose only producer is being re-run go back to TODO too: leaving them
    # DONE overstates progress on work the plan just declared invalid.
    items = [
        iid
        for iid in _exclusive_items(manifest, reset)
        if statuses.get(iid) in ("DONE", "BLOCKED", "PARTIAL", "AWAITS_REVIEW")
    ]
    for iid in items:
        ab.apply_mutation(
            _html_path(plan_dir), iid, status="TODO",
            note=f"redispatched with {session_id}: {reason}",
        )

    rsi.log_event(
        plan_dir, "redispatch", session_ids=events, reason=reason,
        from_status=status, cascaded=[s for s in events if s != session_id],
        items_reset=items, archived=archived,
    )
    if allow_stale_dependents and stale:
        # The operator chose to keep derived work that is stale by construction.
        # That is a legitimate call (the change may not affect them) but it must
        # be auditable rather than silent.
        rsi.log_event(
            plan_dir, "stale_dependents_accepted", session_ids=stale,
            because_of=session_id, reason=reason,
            statuses={d: statuses.get(d) for d in stale},
        )

    # RP-06 — a redispatch changes the plan mid-run, so it belongs in the
    # rendered change log alongside add/amend/retire-session. (Those three write
    # their entry inside the journalled generation; this path has no generation
    # to stage, so it uses the same atomic writer directly.)
    pm.record_change(
        plan_dir, op="redispatch", session=session_id,
        summary=f"re-ran {session_id} (was {status}): {reason}"
                + (f"; cascaded to {', '.join(s for s in events if s != session_id)}"
                   if len(events) > 1 else ""),
        sessions_touched=events, items_touched=items,
    )

    expected = {sid: "TODO" for sid in events}
    expected.update({iid: "TODO" for iid in items})
    gate = sg.run_gate(plan_dir, expected)
    if gate["status"] == "failed":
        rsi.set_halt(plan_dir, f"{session_id}: redispatch structural gate failed", session_id)
        _out({"redispatched": False, "session": session_id, "structural_gate": gate,
              "halted": True}, plan_dir=plan_dir)
        sys.exit(1)

    _out(
        {
            "redispatched": True,
            "session": session_id,
            "from_status": status,
            "reason": reason,
            "sessions_reset": events,
            "items_reset": items,
            "cascaded": [s for s in events if s != session_id],
            "accepted_stale": stale if allow_stale_dependents else [],
            "dependents_untouched": untouched,
            "archived": archived,
            "structural_gate": gate,
            "next": "run `plan` to dispatch it again",
        },
        plan_dir=plan_dir,
    )


def _assert_real_rung(plan_dir, manifest, session, model, reasoning):
    """Raise unless (model, reasoning) is a rung ABOVE this session's authored cell.

    The ladder is re-derived from the SAME `escalation`/`resolve_route` funnel the
    dispatcher walks, so "a rung the refusal set will actually match" and "a rung
    this session can be dispatched on" are the same question, asked once.

    LANE-AWARE (ESC-03): a codex-backed session's ladder is `providers.openai`'s,
    walked from the RESOLVED codex cell — which is what `begin` dispatched and
    therefore what the orchestrator will pass back here. Validating it against the
    Claude ladder would reject every real codex refusal."""
    sid = session["id"]
    provider, ssot_text = _load_routing()
    lane = _dispatch_lane(session, provider or "anthropic", ssot_text, plan_dir=plan_dir)
    if lane == "codex":
        try:
            authored_model, authored_reasoning = _resolve_codex_dispatch(
                session.get("model"), session.get("reasoning"), ssot_text,
                task_class=(session.get("task_class") or "").strip().lower() or None,
            )
        except UnroutableCodexSession as e:
            raise SystemExit(
                f"cannot validate the refused rung for {sid}: this session dispatches on the "
                f"Codex lane and its cell no longer resolves ({e})."
            )
    else:
        authored_model = _normalize_model(session.get("model"))
        authored_reasoning = _reasoning_tier(session.get("reasoning"))
    if not esca.enabled(manifest, session):
        raise SystemExit(
            f"session {sid} does not escalate (manifest plan_schema_version < "
            f"{esca.ESCALATION_MIN_SCHEMA}, or the session sets escalation: false), so it "
            "has no escalated rung to refuse."
        )
    if not esca.dispatchable_cell(authored_model, authored_reasoning):
        raise SystemExit(
            f"session {sid} declares no ladder cell (model={authored_model or 'inherit'}, "
            f"reasoning={authored_reasoning or 'unset'}) — it never escalates, so it has no "
            "escalated rung to refuse."
        )
    try:
        rr = _import_resolver()
        valid = esca.ladder_keys(rr, session.get("task_class") or "agentic_build",
                                 _ESCALATION_PROVIDER[lane], authored_model,
                                 authored_reasoning,
                                 ssot_path=str(_routing_ssot_path()))
    except Exception as e:  # noqa: BLE001
        raise SystemExit(
            f"cannot validate the refused rung for {sid}: the routing ladder is unreadable "
            f"({e}). Recording it unchecked would silently record a rung that matches nothing."
        )
    key = esca.rung_key(model, reasoning)
    if key not in valid:
        raise SystemExit(
            f"{key!r} is not an escalated rung of session {sid}'s ladder. Its rungs above "
            f"{esca.rung_key(authored_model, authored_reasoning)} are: {', '.join(valid) or '(none)'}. "
            "Pass --model and --reasoning EXACTLY as `begin` dispatched them. For a CLAUDE "
            "member that is the batch member's model_arg/reasoning. For a CODEX member it is "
            "codex_model/codex_effort — model_arg there is the FIXED Claude wrapper model and "
            "reasoning is the manifest tier, so neither names the rung that was actually "
            "refused (SKILL.md:162). Either way the pair is in the escalation_applied event."
        )


def cmd_record_refusal(plan_dir, session_id, model, reasoning, reason, source):
    """ESC-02 — THE refusal interface. Nothing infers a refusal; the orchestrator
    calls this when it OBSERVES one.

    WHAT COUNTS (and nothing else): a real dispatch error the orchestrator saw, or
    a `CODEX-DISPATCH-FAILED: <signal>` wrapper reply. A blocked Claude model
    override that SILENTLY inherits another model raises nothing at all and is
    explicitly OUT OF SCOPE here — it is caught by the served-model attestation
    handoff, not by this set (see escalation.py's module docstring).

    Effects: the cell is unavailable for the rest of this session, the session
    re-dispatches at the PREVIOUS rung (floored at the authored cell — never onto
    the downward fallback ladder), and the refusal is NOT charged against
    max_rework, because nothing was attempted."""
    manifest = mio.load_manifest(plan_dir)
    session = mio.session_by_id(manifest).get(session_id)
    if session is None:
        raise SystemExit(f"unknown session {session_id!r}")
    if not (reason or "").strip():
        raise SystemExit(
            "refusing to record a refusal without --reason: an unexplained rung "
            "disappearing from a session's ladder is exactly the silent behaviour "
            "this interface exists to prevent. State what you OBSERVED in one line."
        )
    # THE RUNG MUST BE A REAL RUNG OF THIS SESSION'S LADDER. Without this check
    # `--model fable` with no `--reasoning` recorded `fable@unset`, printed
    # `"recorded": true`, and the very next dispatch sent `fable@medium` again —
    # the exact rung just refused (measured 2026-08-14). A refusal that reads as
    # accepted and changes nothing is worse than one that is rejected, so a key
    # that names no rung is a hard error that lists the keys that do.
    _assert_real_rung(plan_dir, manifest, session, model, reasoning)
    rec = esca.record_refusal(plan_dir, session_id, model, reasoning, reason, source)
    # BACK TO TODO. A refused dispatch never ran, so leaving the session at DOING
    # made the documented recovery ("then re-run `plan`") answer `blocked` — the
    # loop this command exists to unblock could not actually be walked. Only from
    # DOING: a session parked at AWAITS_REVIEW or already finished is not this
    # command's to move.
    status = _statuses(plan_dir).get(session_id)
    if status == "DOING":
        ab.apply_mutation(
            _html_path(plan_dir), session_id, status="TODO",
            note=f"escalated rung {esca.rung_key(model, reasoning)} refused at dispatch: {reason}",
        )
    _out(
        {
            "recorded": True,
            "session": session_id,
            "rung": esca.rung_key(model, reasoning),
            "refused": rec["refused"],
            "reason": reason,
            "source": source,
            "charged_against_max_rework": False,
            "status": _statuses(plan_dir).get(session_id),
            "next": "run `plan` to re-dispatch — this rung is now unavailable and the "
                    "session falls back to the previous rung, never below the authored one",
        },
        plan_dir=plan_dir,
    )


def cmd_record_receipt(plan_dir, session_id, agent_id, transcript, backend):
    """TEL-01 — the served-model attestation HANDOFF. The orchestrator calls this
    exactly once per dispatch, AFTER the Agent tool call returns and BEFORE
    apply/verify resolution — the earliest point an Agent ID exists at all, so
    it cannot run any earlier (`begin` precedes the Agent call). The receipt is
    what lets `outcomes.py` write `model_ran_source="attested"` instead of
    downgrading every real outcome to `"unknown"`; see outcomes.py's module
    docstring for the full per-backend rule.

    TEL-01 finding 1 — stamps the receipt with the (generation, dispatch
    ordinal) it belongs to, so a resolution recorded later can tell whether
    this receipt is actually ABOUT it — see `outcomes._model_ran`."""
    if not (agent_id or "").strip():
        raise SystemExit("refusing to record a receipt without --agent-id: the receipt IS the "
                          "correlation between this dispatch and an Agent ID — an empty one "
                          "proves nothing.")
    attested = outc.attest_from_transcript(backend, transcript) if transcript else None
    generation = esca.session_state(plan_dir, session_id)["generation"]
    dispatch_ordinal = outc._dispatch_ordinal(plan_dir, session_id)  # noqa: SLF001 — same module family
    rec = rsi.record_receipt(plan_dir, session_id, agent_id=agent_id, transcript=transcript,
                             backend=backend, attested=attested,
                             generation=generation, attempt=dispatch_ordinal)
    _out(
        {
            "recorded": True, "session": session_id, "backend": backend,
            "attested": bool(attested),
            "model_ran_source": outc.SOURCE_ATTESTED if attested else outc.SOURCE_REQUESTED,
            "receipt": rec,
        },
        plan_dir=plan_dir,
    )


def cmd_clear_halt(plan_dir):
    state = rsi.clear_halt(plan_dir)
    _out({"halt": state["halt"]}, plan_dir=plan_dir)


def cmd_release(plan_dir):
    rsi.release_lock(plan_dir)
    _out({"released": True}, plan_dir=plan_dir)


# --------------------------------------------------------------------------
# RP-03 — structural mutation: add / amend / retire a session.
#
# These are the ONLY supported way to change a running plan's shape. The engine
# lives in plan_mutate.py (one renderer, one journalled multi-file transaction);
# these wrappers own the refusals that belong to the RUN — halt flag and a live
# dispatch lock — and the JSON the orchestrator prints.
# --------------------------------------------------------------------------
def _refuse_if_dispatch_in_flight(plan_dir, what):
    holder = rsi.lock_holder(plan_dir)
    if holder:
        raise SystemExit(
            f"refusing to {what}: a /plan-execute batch holds this plan's lock "
            f"(pid={holder.get('pid')}, host={holder.get('host')}, "
            f"started={holder.get('started_at')}). Restructuring re-renders PLAN.html, "
            "which would clobber the status writes that batch is about to make. Let it "
            f"finish (or `run.py release {plan_dir}` if it died), then re-run."
        )


def _unfinished_shipping(plan_dir):
    """Sessions with a shipping run recorded but not complete. `_shipping_state`
    binds to the manifest DIGEST, so mutating the manifest under one makes its
    resume refuse as `state-drift` and HALT the plan — paid work stranded by a
    restructuring that had nothing to do with it."""
    out = []
    for p in sorted((Path(plan_dir) / "_shipping_state").glob("*.json")):
        parts = p.name.split(".")
        if len(parts) > 2 and re.fullmatch(r"r\d+", parts[1]):
            continue  # `<sid>.rN.json` — archived by a redispatch round
        sid = parts[0]
        try:
            state = json.loads(p.read_text())
            if not shp._all_done(state):
                out.append(sid)
        except (OSError, ValueError, KeyError):
            out.append(sid)
    return out


def _mutation_guard(plan_dir, what):
    # A REPLAN halt (RP-05) is resolved BY restructuring the plan, so these
    # commands are its cure, not something it should block.
    _refuse_if_halted(plan_dir, what, allow_kinds=("replan",))
    _refuse_if_dispatch_in_flight(plan_dir, what)
    unfinished = _unfinished_shipping(plan_dir)
    locks = ssio.held_ship_locks(plan_dir)
    if unfinished or locks:
        raise SystemExit(
            f"refusing to {what}: shipping is mid-flight "
            f"(unfinished: {unfinished or 'none'}; locks held: {locks or 'none'}). "
            "Shipping state binds to the manifest digest, so mutating the plan now would "
            "make those runs refuse as `state-drift` and halt the plan. Finish them "
            "(`ship-status` / `ship-finalize`) or release them (`ship-release`), then re-run."
        )


def _mutate(plan_dir, fn):
    """Run one mutation, turning a refusal into a clean non-zero exit."""
    try:
        result = fn()
    except pm.MutationError as e:
        raise SystemExit(str(e)) from e
    rsi.log_event(plan_dir, result["op"].replace("-", "_"), **{
        k: v for k, v in result.items() if k in
        ("txn", "session", "sessions_touched", "items_touched", "retired",
         "dependents_amended", "files_written", "verify_state_migrated")
    })
    gate = sg.run_gate(plan_dir, result.pop("expected_statuses", {}))
    result["structural_gate"] = gate
    result["next"] = "run `plan` to dispatch from the updated graph"
    _out(result, plan_dir=plan_dir)
    if gate["status"] == "failed":
        sys.exit(1)
    return result


def cmd_add_session(plan_dir, args):
    _mutation_guard(plan_dir, f"add session {args.id}")
    _mutate(plan_dir, lambda: pm.add_session(
        plan_dir, sid=args.id, title=args.title,
        items=_csv(args.items), new_items=args.new_item or [],
        depends_on=_csv(args.depends_on), model=args.model, reasoning=args.reasoning,
        gates=_csv(args.gates), require_evidence=args.require_evidence,
        prompt=args.prompt, human_summary=args.human_summary,
        parallel_group=args.parallel_group,
        infographic_group=args.infographic_group,
        allow_builder_drift=args.allow_builder_drift,
    ))


def cmd_amend_session(plan_dir, args):
    _mutation_guard(plan_dir, f"amend session {args.session}")
    _mutate(plan_dir, lambda: pm.amend_session(
        plan_dir, args.session,
        depends_on=(_csv(args.depends_on) if args.depends_on is not None else None),
        prompt=args.prompt, model=args.model, reasoning=args.reasoning,
        allow_builder_drift=args.allow_builder_drift,
    ))
    # ESC-02 — an amendment that moves the AUTHORED cell invalidates the climb
    # computed against the old one: rungs are positions relative to the authored
    # rung, so keeping a refused-rungs set (or a generation) across the change
    # would let a pre-amend execution share a cohort with a post-amend one. Fires
    # only for --model/--reasoning; a prompt or depends_on edit leaves the ladder
    # alone. Runs AFTER the mutation so a refused mutation resets nothing.
    if args.model or args.reasoning:
        esca.reset(plan_dir, args.session,
                   why=f"amend-session model={args.model!r} reasoning={args.reasoning!r}")
        stuckp.clear(plan_dir, args.session)


def cmd_retire_session(plan_dir, args):
    _mutation_guard(plan_dir, f"retire session {args.session}")
    result = _mutate(plan_dir, lambda: pm.retire_session(
        plan_dir, args.session, reason=args.reason, cascade=args.cascade,
        drop_dependency=args.drop_dependency,
        allow_builder_drift=args.allow_builder_drift,
    ))
    # TEL-01 — retirement is the ONE path that produces `result=wontfix`: closeouts
    # only ever carry DONE/PARTIAL/BLOCKED, so no apply/verify resolution can ever
    # write it. Reached only when `_mutate` did not already `sys.exit(1)` on a
    # failed structural gate. One record per session actually retired — cascade
    # can retire more than the one named on the command line.
    for sid in result.get("retired", []) or []:
        # TEL-01 finding 3 — same real-attempt fix as `apply_terminal` (see
        # `_apply_attempt`): a hardcoded 1 here would collide with a prior
        # `apply_terminal`/`administrative_retire` write for the same session
        # at the same generation only if the resolution strings also matched,
        # but deriving the real ordinal keeps the field honest either way.
        _attempt = _apply_attempt(plan_dir, sid)
        outc.write(plan_dir, sid, resolution="administrative_retire", result="wontfix",
                  # Same as apply_terminal: retirement is an ADMINISTRATIVE
                  # resolution, not a gate outcome, so it has zero reworks.
                  verified=False, attempt=_attempt, rework_count=0)


def _csv(raw):
    """`--items a,b` or `--items a --items b` -> ['a','b']. Empty string -> []."""
    if raw is None:
        return []
    parts = raw if isinstance(raw, list) else [raw]
    return [x.strip() for p in parts for x in str(p).split(",") if x.strip()]


# --------------------------------------------------------------------------
# Shipping subcommands (post-session actions). The deterministic state machine
# lives in shipping.py; the orchestrator invokes the actual skills.
# --------------------------------------------------------------------------
def cmd_ship_begin(plan_dir, session_id, dry_run, resume, confirm_stale):
    _out(
        shp.ship_begin(
            plan_dir, session_id, dry_run=dry_run, resume=resume, confirm_stale=confirm_stale
        ),
        plan_dir=plan_dir,
    )


def cmd_ship_record(plan_dir, session_id, step, status, result_file):
    _out(shp.ship_record(plan_dir, session_id, step, status, result_file), plan_dir=plan_dir)


def cmd_ship_run(plan_dir, session_id, step):
    _out(shp.ship_run_argv(plan_dir, session_id, step), plan_dir=plan_dir)


def cmd_ship_finalize(plan_dir, session_id):
    _out(shp.ship_finalize(plan_dir, session_id), plan_dir=plan_dir)


def cmd_ship_release(plan_dir):
    _out(shp.ship_release(plan_dir), plan_dir=plan_dir)


def cmd_ship_status(plan_dir, session_id):
    _out(shp.ship_status(plan_dir, session_id), plan_dir=plan_dir)


def cmd_ship_simulate(plan_dir, session_id):
    _out(shp.ship_simulate(plan_dir, session_id), plan_dir=plan_dir)


# --------------------------------------------------------------------------
# Verify subcommands (session verification gates). The deterministic state
# machine lives in verify.py; the orchestrator invokes the actual gate skills.
# --------------------------------------------------------------------------
def cmd_verify_begin(plan_dir, session_id, dry_run, resume):
    _out(vfy.verify_begin(plan_dir, session_id, dry_run=dry_run, resume=resume), plan_dir=plan_dir)


def cmd_verify_record(plan_dir, session_id, gate, status, result_file):
    _out(
        vfy.verify_record(plan_dir, session_id, gate, status, result_file),
        plan_dir=plan_dir,
    )


def cmd_verify_run(plan_dir, session_id, gate):
    _out(vfy.verify_run_argv(plan_dir, session_id, gate), plan_dir=plan_dir)


def cmd_verify_finalize(plan_dir, session_id):
    _out(vfy.verify_finalize(plan_dir, session_id), plan_dir=plan_dir)


def cmd_verify_status(plan_dir, session_id):
    _out(vfy.verify_status(plan_dir, session_id), plan_dir=plan_dir)


def cmd_verify_simulate(plan_dir, session_id):
    _out(vfy.verify_simulate(plan_dir, session_id), plan_dir=plan_dir)


# --------------------------------------------------------------------------
def main():
    p = argparse.ArgumentParser(description="plan-execute orchestration helper")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_dir(sp, optional=True):
        # QW-02: plan_dir is optional on every subcommand (not just `status
        # --all`) — omit it to default to the CANONICAL/latest plan in
        # _plans_index.md (see `_resolve_bare_invocation`).
        if optional:
            sp.add_argument("plan_dir", nargs="?", help="Plan directory (or PLAN.html path)")
        else:
            sp.add_argument("plan_dir", help="Plan directory (or PLAN.html path)")
        # Global flag: only `begin` acts on it; other subcommands accept-and-ignore.
        sp.add_argument(
            "--unsafe-lock",
            action="store_true",
            help="begin: lock even on a networked/sync FS where the lock is "
            "unreliable. Accepted and ignored by other subcommands.",
        )
        # Which ORCHESTRATOR is driving this run (CP-02). Only `plan` and `begin`
        # act on it; `status`/`checkpoint` (and the ship-*/verify-* subcommands)
        # accept-and-ignore it so a Codex orchestrator can pass it uniformly.
        # NEVER auto-detected and never persisted — see the HARNESS MODE block.
        sp.add_argument(
            "--harness",
            choices=_HARNESSES,
            default="claude",
            help="Which orchestrator is running this loop. `claude` (default) is "
            "unchanged Task-tool dispatch. `codex` emits a runnable `codex exec` "
            "command per session for a Codex orchestrator to run itself.",
        )

    def _add_drift_flag(sp):
        sp.add_argument(
            "--allow-builder-drift",
            action="store_true",
            help="Accept the page changes a NEWER plan-builder would make to this "
            "plan alongside your mutation. Without it, a plan built by an older "
            "builder is refused rather than silently upgraded.",
        )

    s = sub.add_parser("status")
    add_dir(s, optional=True)
    s.add_argument(
        "--all",
        action="store_true",
        help="Scan every _plans/*/ under cwd; print one line per plan.",
    )
    s = sub.add_parser("plan")
    add_dir(s)
    s.add_argument("--resume", action="store_true")
    s.add_argument("--session", default=None)
    s.add_argument(
        "--auto",
        action="store_true",
        help="Autonomous mode: the orchestrator self-drives through verify-rework "
        "loops and `dispatch_next:false` hints, halting only on human checkpoints, "
        "hard blockers, and stale-deploy confirms. Echoed in the action so the "
        "orchestrator (and run.ndjson) record the posture; human gates stay sacrosanct.",
    )
    s = sub.add_parser("begin")
    add_dir(s)
    s.add_argument("--sessions", nargs="+", required=True)
    s.add_argument(
        "--resume",
        action="store_true",
        help="Dispatch even though the plan is HALTED — the same deliberate override "
        "`plan --resume` is. Without it, `begin` refuses on a halted plan.",
    )
    s = sub.add_parser("apply")
    add_dir(s)
    s.add_argument("--session", required=True)
    s.add_argument("--output-file", required=True)
    s.add_argument(
        "--dry-run-shipping",
        action="store_true",
        help="After applying, also print the (non-executing) shipping plan for the session.",
    )
    s = sub.add_parser("checkpoint")
    add_dir(s)
    s.add_argument("--session", required=True)
    s = sub.add_parser("ack-checkpoint")
    add_dir(s)
    s.add_argument("--session", required=True)
    s.add_argument("--note", default=None,
                   help="Note to record on the session card (default: the checkpoint reason).")
    s = sub.add_parser("redispatch")
    add_dir(s)
    s.add_argument("--session", required=True)
    s.add_argument("--reason", required=True,
                   help="Why the recorded result no longer holds. Required — it is the only "
                        "place the WHY of a discarded result survives.")
    s.add_argument(
        "--allow-stale-dependents",
        action="store_true",
        help="Do NOT reset dependents that consumed this session's output. Their results "
        "are stale by construction, so this acknowledgement is recorded in run.ndjson "
        "as stale_dependents_accepted.",
    )
    s = sub.add_parser(
        "record-refusal",
        help="ESC-02: report an OBSERVED dispatch refusal of one escalated rung, so "
             "the session falls back to the previous rung instead of retrying it",
    )
    add_dir(s)
    s.add_argument("--session", required=True)
    s.add_argument("--model", required=True,
                   help="The model token that was refused, EXACTLY as `begin` "
                        "dispatched it: batch member model_arg on the Claude lane, "
                        "codex_model on the codex lane (model_arg there is the fixed "
                        "Claude wrapper model, not the refused rung).")
    s.add_argument("--reasoning", default="",
                   help="The effort tier that was refused, EXACTLY as `begin` "
                        "dispatched it: batch member reasoning on the Claude lane, "
                        "codex_effort on the codex lane. The (model, "
                        "reasoning) pair must name a real rung of this session's "
                        "ladder or the command is REFUSED — a mismatched key would "
                        "record a refusal that matches nothing and re-dispatch the "
                        "same rung.")
    s.add_argument("--reason", required=True,
                   help="What you OBSERVED — the dispatch error text, or the "
                        "CODEX-DISPATCH-FAILED signal. Never an inference.")
    s.add_argument("--source", default="dispatch_error",
                   choices=("dispatch_error", "codex_wrapper_signal"),
                   help="Which observable channel reported it. There is no third "
                        "channel: a silently-inherited model raises nothing and is "
                        "out of scope for this interface.")
    s = sub.add_parser(
        "record-receipt",
        help="TEL-01: correlate a returned Agent-tool dispatch with a served-model "
             "attestation. Call AFTER the Agent call returns and BEFORE apply/verify "
             "resolution — the earliest point an Agent ID exists.",
    )
    add_dir(s)
    s.add_argument("--session", required=True)
    s.add_argument("--agent-id", required=True, help="The Agent tool's returned agent id.")
    s.add_argument(
        "--transcript", default=None,
        help="Path to served-model evidence, when any exists. CLAUDE backend: a headless "
        "`claude -p --output-format json` result file (the proven `modelUsage` source). "
        "CODEX backend: accepted for symmetry but never yields an attestation — the "
        "`codex exec --json` stream carries no served-model field (measured, "
        "evals/routing/graders/judge.py:8).",
    )
    s.add_argument("--backend", default="claude", choices=("claude", "codex"))
    s = sub.add_parser(
        "resolve-replan",
        help="Answer a REPLAN park raised by a closeout's plan_impact",
    )
    add_dir(s)
    s.add_argument("--session", required=True,
                   help="The session whose closeout raised the REPLAN")
    s.add_argument("--decision", required=True, choices=list(rp.REPLAN_DECISIONS),
                   help="amend / retire the invalidated sessions, or proceed unchanged. "
                        "amend and retire are only accepted once the change log shows the "
                        "amendment actually landed.")
    s.add_argument("--reason", required=True,
                   help="Why — one line. It is what the plan's change log keeps.")

    s = sub.add_parser(
        "recommend-replan",
        help="Record YOUR recommendation onto a parked REPLAN brief (required before "
             "resolve-replan on schema v5+ plans)",
    )
    add_dir(s)
    s.add_argument("--session", required=True,
                   help="The session whose closeout raised the REPLAN")
    s.add_argument("--recommendation", required=True,
                   help="Which of the three options you would take and why — one line. "
                        "It is rendered into HALT_NOTICE.txt beside the options, so the "
                        "operator decides with your judgement in front of them.")

    # RP-03 — structural mutation of a running plan.
    s = sub.add_parser("add-session", help="Add a session (and its items) to a running plan")
    add_dir(s)
    s.add_argument("--id", required=True, help="New session id, e.g. s21")
    s.add_argument("--title", required=True)
    s.add_argument("--items", action="append", default=None,
                   help="Existing item ids for this session (comma-separated, repeatable)")
    s.add_argument("--new-item", action="append", default=None, metavar="'id|category|title'",
                   help="Declare a NEW item: 'id|category|title[|one-line summary]'. "
                        "The category must match an existing section.")
    s.add_argument("--depends-on", action="append", default=None,
                   help="Session ids this one waits for (comma-separated, repeatable)")
    s.add_argument("--model", default="Sonnet")
    s.add_argument("--reasoning", default=None, choices=["low", "medium", "high", "xhigh", "max"])
    s.add_argument("--gates", action="append", default=None,
                   help="Verify gate ids (comma-separated, repeatable)")
    s.add_argument("--require-evidence", action="store_true")
    s.add_argument("--prompt", default=None, help="Session prompt body")
    s.add_argument("--human-summary", default=None)
    s.add_argument("--parallel-group", default=None)
    s.add_argument("--infographic-group", default=None,
                   help="Plan Achievement group the new item(s) belong to (pillar / "
                        "phase / spoke name). Defaults to the group named after the "
                        "item's category; when nothing matches, the item is left out "
                        "of that section AND you are told so.")
    _add_drift_flag(s)

    s = sub.add_parser("amend-session", help="Change a TODO session's deps / prompt / model")
    add_dir(s)
    s.add_argument("--session", required=True)
    s.add_argument("--depends-on", action="append", default=None,
                   help="REPLACES depends_on (comma-separated, repeatable; "
                        "pass '' to clear)")
    s.add_argument("--prompt", default=None)
    s.add_argument("--model", default=None)
    s.add_argument("--reasoning", default=None, choices=["low", "medium", "high", "xhigh", "max"])
    _add_drift_flag(s)

    s = sub.add_parser("retire-session", help="Close a session WONTFIX with a recorded reason")
    add_dir(s)
    s.add_argument("--session", required=True)
    s.add_argument("--reason", required=True, help="Why this work is not being done (one line)")
    s.add_argument("--cascade", action="store_true",
                   help="Also retire every live session that depends on it")
    s.add_argument("--drop-dependency", action="store_true",
                   help="Keep live dependents: drop this session from their depends_on and "
                        "warn them in their prompt, in the same transaction")
    _add_drift_flag(s)

    s = sub.add_parser("clear-halt")
    add_dir(s)
    s = sub.add_parser("release")
    add_dir(s)

    # Shipping subcommands.
    s = sub.add_parser("ship-begin")
    add_dir(s)
    s.add_argument("--session", required=True)
    s.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the shipping plan without locking or executing.",
    )
    s.add_argument(
        "--resume",
        action="store_true",
        help="Ship past a human checkpoint / a stale-auth confirm (operator OK).",
    )
    s.add_argument(
        "--confirm-stale",
        action="store_true",
        help="Proceed with a deploy whose authorization digest went stale.",
    )
    s = sub.add_parser("ship-record")
    add_dir(s)
    s.add_argument("--session", required=True)
    s.add_argument("--step", required=True)
    s.add_argument("--status", required=True, choices=["done", "failed"])
    s.add_argument("--result-file", default=None)
    s = sub.add_parser("ship-run")
    add_dir(s)
    s.add_argument("--session", required=True)
    s.add_argument("--step", required=True)
    s = sub.add_parser("ship-finalize")
    add_dir(s)
    s.add_argument("--session", required=True)
    s = sub.add_parser("ship-release")
    add_dir(s)
    s = sub.add_parser("ship-status")
    add_dir(s)
    s.add_argument("--session", required=True)
    s = sub.add_parser("ship-simulate")
    add_dir(s)
    s.add_argument("--session", required=True)

    # Verify subcommands (session verification gates).
    s = sub.add_parser("verify-begin")
    add_dir(s)
    s.add_argument("--session", required=True)
    s.add_argument("--dry-run", action="store_true",
                   help="Compute the gate plan without running real gates.")
    s.add_argument("--resume", action="store_true",
                   help="Resume a verify sub-loop after a crash (DOING + pending state).")
    s = sub.add_parser("verify-record")
    add_dir(s)
    s.add_argument("--session", required=True)
    s.add_argument("--gate", required=True)
    s.add_argument("--status", required=True, choices=["done", "failed"])
    s.add_argument("--result-file", default=None)
    s = sub.add_parser("verify-run")
    add_dir(s)
    s.add_argument("--session", required=True)
    s.add_argument("--gate", required=True)
    s = sub.add_parser("verify-finalize")
    add_dir(s)
    s.add_argument("--session", required=True)
    s = sub.add_parser("verify-status")
    add_dir(s)
    s.add_argument("--session", required=True)
    s = sub.add_parser("verify-simulate")
    add_dir(s)
    s.add_argument("--session", required=True)
    # PL-01 — worktree isolation. `worktree-status` is read-only; cleanup is the
    # EXPLICIT, LAST step of a group's lifecycle (contract §4) and has no
    # automatic caller anywhere in this file, on purpose.
    s = sub.add_parser("worktree-status")
    add_dir(s)
    s.add_argument("--group", required=True)
    s = sub.add_parser("worktree-cleanup")
    add_dir(s)
    s.add_argument("--group", required=True)
    s.add_argument(
        "--force", action="store_true",
        help="remove a member worktree even though it still holds uncommitted, untracked "
             "or locally-created ignored content. The branch is still deleted only with "
             "`git branch -d`, never -D, so unmerged commits always survive.",
    )

    args = p.parse_args()

    # `status --all` scans cwd/_plans and needs no specific plan dir.
    if args.cmd == "status" and getattr(args, "all", False):
        cmd_status_all()
        return

    if args.plan_dir is None:
        # QW-02 bare-invocation default: no arg -> CANONICAL/latest plan from
        # _plans_index.md (found via upward search from cwd).
        plan_dir = _resolve_bare_invocation()
    else:
        plan_dir = _resolve_plan_dir(args.plan_dir)
        if not Path(plan_dir).is_dir():
            # QW-02 cwd-robust resolution: the literal arg didn't resolve —
            # likely an earlier `cd` shifted cwd relative to it. Search
            # upward before giving up.
            found = _search_upward_for_plan(args.plan_dir)
            if found is not None:
                print(
                    f"(plan directory {plan_dir} not found from cwd — resolved via "
                    f"upward search to {found})",
                    file=sys.stderr,
                )
                plan_dir = found
    if not Path(plan_dir).is_dir():
        raise SystemExit(f"plan directory not found: {plan_dir}")

    _dispatch(args.cmd, plan_dir, args)


def _dispatch(cmd, plan_dir, args):
    # An interrupted structural mutation (add/amend/retire) left a journal: finish
    # it before anything reads the plan, so a crashed mutation heals on the next
    # command instead of leaving a mixed-generation plan to be discovered later.
    try:
        recovered = pm.recover(plan_dir)
    except pm.MutationError as e:
        raise SystemExit(str(e)) from e
    for rec in recovered:
        print(f"(recovered interrupted mutation {rec['txn']}: {rec['action']})", file=sys.stderr)

    if cmd == "status":
        cmd_status(plan_dir)
    elif cmd == "plan":
        cmd_plan(plan_dir, args.resume, args.session, args.auto, args.harness)
    elif cmd == "begin":
        cmd_begin(plan_dir, args.sessions, args.unsafe_lock, args.harness, args.resume)
    elif cmd == "apply":
        cmd_apply(plan_dir, args.session, args.output_file, args.dry_run_shipping)
    elif cmd == "checkpoint":
        cmd_checkpoint(plan_dir, args.session)
    elif cmd == "ack-checkpoint":
        cmd_ack_checkpoint(plan_dir, args.session, args.note)
    elif cmd == "redispatch":
        cmd_redispatch(plan_dir, args.session, args.reason, args.allow_stale_dependents)
    elif cmd == "record-refusal":
        cmd_record_refusal(plan_dir, args.session, args.model, args.reasoning,
                           args.reason, args.source)
    elif cmd == "record-receipt":
        cmd_record_receipt(plan_dir, args.session, args.agent_id, args.transcript, args.backend)
    elif cmd == "resolve-replan":
        cmd_resolve_replan(plan_dir, args.session, args.decision, args.reason)
    elif cmd == "recommend-replan":
        cmd_recommend_replan(plan_dir, args.session, args.recommendation)
    elif cmd == "add-session":
        cmd_add_session(plan_dir, args)
    elif cmd == "amend-session":
        cmd_amend_session(plan_dir, args)
    elif cmd == "retire-session":
        cmd_retire_session(plan_dir, args)
    elif cmd == "clear-halt":
        cmd_clear_halt(plan_dir)
    elif cmd == "release":
        cmd_release(plan_dir)
    elif cmd == "ship-begin":
        cmd_ship_begin(plan_dir, args.session, args.dry_run, args.resume, args.confirm_stale)
    elif cmd == "ship-record":
        cmd_ship_record(plan_dir, args.session, args.step, args.status, args.result_file)
    elif cmd == "ship-run":
        cmd_ship_run(plan_dir, args.session, args.step)
    elif cmd == "ship-finalize":
        cmd_ship_finalize(plan_dir, args.session)
    elif cmd == "ship-release":
        cmd_ship_release(plan_dir)
    elif cmd == "ship-status":
        cmd_ship_status(plan_dir, args.session)
    elif cmd == "ship-simulate":
        cmd_ship_simulate(plan_dir, args.session)
    elif cmd == "verify-begin":
        cmd_verify_begin(plan_dir, args.session, args.dry_run, args.resume)
    elif cmd == "verify-record":
        cmd_verify_record(plan_dir, args.session, args.gate, args.status, args.result_file)
    elif cmd == "verify-run":
        cmd_verify_run(plan_dir, args.session, args.gate)
    elif cmd == "verify-finalize":
        cmd_verify_finalize(plan_dir, args.session)
    elif cmd == "verify-status":
        cmd_verify_status(plan_dir, args.session)
    elif cmd == "verify-simulate":
        cmd_verify_simulate(plan_dir, args.session)
    elif cmd == "worktree-status":
        _out(wt.group_status(plan_dir, mio.load_manifest(plan_dir), args.group),
             plan_dir=plan_dir)
    elif cmd == "worktree-cleanup":
        res = wt.cleanup_group(plan_dir, args.group, force=args.force)
        _out(res, plan_dir=plan_dir)
        if res.get("preserved"):
            # A preserved worktree is a REFUSAL, not a partial success: content
            # that would have been destroyed is still on disk. Exit 1 so a script
            # cannot read "cleaned" out of a run that deliberately kept things.
            sys.exit(1)


if __name__ == "__main__":
    main()
