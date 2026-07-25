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
  clear-halt DIR                   Clear the halt flag (operator). (v1.5)
  release DIR                      Release the lock (end of run).

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
import json
import os
import re
import shlex
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import article_block as ab
import closeout_pipeline as cp
import dispatch as dsp
import gate_policy as gp
import manifest_io as mio
import render_verify as rv
import run_state_io as rsi
import shipping as shp
import structural_gate as sg
import verify as vfy

PLAN_HTML = "PLAN.html"


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
# auto-drop judgement work to Haiku/Luna, never emit Opus @ `max`. The openai
# chain is the v1.5 D3 walk: sol→terra@max→gpt-5.5@xhigh, exhaustion = NO-CODEX.
# SSOT LOCKSTEP (s04): these dicts are CODE-AUTHORITATIVE — model-routing.yaml's
# tier-keyed `providers.<name>.degrade` blocks MIRROR them (translated tier→model
# via `providers.<name>.models`), and verify-routing.sh check (d) ast-walks this
# file and fails if either provider's ladder diverges. They MUST stay static
# module-level dict literals (never a comprehension/function-built dict — the
# guard uses ast.literal_eval and would silently break). Edit these dicts, then
# update the SSOT's mirror in the same commit.
_FALLBACK_LADDER = {
    "anthropic": {"fable": "opus", "opus": "sonnet"},
    "openai": {"gpt-5.6-sol": "gpt-5.6-terra", "gpt-5.6-terra": "gpt-5.5"},
}
_DEGRADE_EFFORT = {  # keyed by TARGET model, per provider
    "anthropic": {"opus": "high", "sonnet": "high"},
    "openai": {"gpt-5.6-terra": "max", "gpt-5.5": "xhigh"},
}


def _fallback_for(token, provider="anthropic"):
    """Given a concrete model id, return the reactive-degradation target as
    ``(next_model, effort)`` — effort per _DEGRADE_EFFORT[provider] — or ``None``
    when no lower judgement-safe tier exists for that provider (already at/below
    the workhorse floor, or an unrecognized/None token)."""
    ladder = _FALLBACK_LADDER.get(provider, {})
    nxt = ladder.get(token if isinstance(token, str) else "")
    return (nxt, _DEGRADE_EFFORT[provider][nxt]) if nxt else None


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
# Fixed Claude model for the wrapper Task call (fan-out default per the SSOT's
# fanout_policy — never Fable). The Codex model rides the Bash command, not Task.
_CODEX_WRAPPER_MODEL = "sonnet"
# Manifest `reasoning` (Anthropic effort vocabulary) → the provider-neutral
# intent axis (`task_classes` vocabulary, renamed from `task_classes_v2` at s03
# when the legacy flat block retired); each provider's `effort.map`
# translates intent → its native dial. Mirrors providers.anthropic.effort.map
# reversed (low→light, medium→standard, high→thorough); xhigh/max clamp to
# thorough — the escalation rungs above `thorough` are provider-native policy.
_INTENT_FROM_REASONING = {
    "": "standard",
    "low": "light",
    "medium": "standard",
    "high": "thorough",
    "xhigh": "thorough",
    "max": "thorough",
}


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
#   * data_sensitivity_guard egress: a working tree containing restricted content
#     (corpus / creds / secrets / .env*) is DO-NOT-SEND — the codex command is
#     refused BEFORE construction unless the SSOT carries an unexpired per-repo
#     egress_opt_ins entry for exactly this tree.
# --------------------------------------------------------------------------
_EGRESS_ROOT_ENV = "PLAN_EXECUTE_EGRESS_ROOT"  # test override; default = cwd. The scanned
# root is BOUND into the codex command via `cd <root> &&` so the scanned tree and the
# shipped tree are the same path by construction (adversarial-review 2026-07-10, consensus).
# Substring matchers — a fail-closed SUPERSET of the SSOT's named segments (over-matching
# just forces an explicit opt-in, the safe direction). `token` deliberately excluded
# (would false-positive on tokenizer/token-count code everywhere); see SSOT matcher spec.
_RESTRICTED_SUBSTRINGS = ("corpus", "creds", "credential", "secret")


def _egress_root():
    return Path(os.environ.get(_EGRESS_ROOT_ENV) or Path.cwd())


def _segment_restricted(name):
    low = name.lower()
    return low.startswith(".env") or any(s in low for s in _RESTRICTED_SUBSTRINGS)


def _find_restricted(root):
    """First restricted path under `root`, or None. Matcher spec (SSOT
    data_sensitivity_guard): canonical-normalized absolute paths (realpath,
    symlinks resolved AND directory symlinks TRAVERSED, cycle-guarded),
    case-insensitive substring match, FULL tree walk INCLUDING git-ignored
    files, restricted segments matched ANYWHERE (root's own resolved path too).
    ponytail: plain os.walk name-check — O(files) per begin; index/cache it if a
    monorepo ever makes this measurably slow."""
    real_root = Path(os.path.realpath(root))
    if any(_segment_restricted(seg) for seg in real_root.parts):
        return str(real_root)
    seen = set()  # realpath cycle guard for followlinks=True
    for dirpath, dirnames, filenames in os.walk(real_root, followlinks=True):
        rp = os.path.realpath(dirpath)
        if rp in seen:
            dirnames[:] = []  # already traversed via another link — don't descend again
            continue
        seen.add(rp)
        for name in dirnames + filenames:
            if _segment_restricted(name):
                return os.path.join(dirpath, name)
            p = os.path.join(dirpath, name)
            if os.path.islink(p):
                target = os.path.realpath(p)
                if any(_segment_restricted(seg) for seg in Path(target).parts):
                    return f"{p} -> {target}"
    return None


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


# Inline-mapping entries ONLY ({ repo_path: ..., expiry: ... } on one line) —
# block-style entries deliberately DON'T parse (fails closed; the SSOT documents
# the required shape beside egress_opt_ins).
_OPT_IN_RE = re.compile(
    r"-\s*\{[^}]*repo_path:\s*\"?([^\",}]+)\"?[^}]*expiry:\s*\"?(\d{4}-\d{2}-\d{2})\"?[^}]*\}"
)


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


def _resolve_codex_dispatch(raw_model, reasoning, ssot_text, require_calibrated=False):
    """Resolve a Codex-backed session to ``(codex_model, codex_effort)`` against
    `providers.openai` in the SSOT. Raises UnroutableCodexSession on ANY gap —
    never returns a partial/guessed route.

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
    if model is None:
        # Claude-family tier vocabulary under a Codex-focused run: translate the
        # token → tier (via providers.anthropic) → this provider's model.
        token = _normalize_model(raw)
        if token is not None:
            try:
                anthropic = rr._Profile(ssot_text, "anthropic")
            except Exception as e:
                raise UnroutableCodexSession(f"providers.anthropic profile unparseable: {e}")
            tier = anthropic.tier_of(token)
            model = openai.models.get(tier) if tier else None
    if model is None:
        raise UnroutableCodexSession(
            f"session model {raw_model!r} resolves to neither a providers.openai model "
            f"(known: {sorted(openai.models.values())}) nor a translatable Claude tier token"
        )
    try:
        tier = openai.tier_of(model)
        intent = _INTENT_FROM_REASONING.get(_reasoning_tier(reasoning), "standard")
        effort = openai.native_effort(tier, intent)
    except Exception as e:
        raise UnroutableCodexSession(f"no native effort resolvable for {model!r}: {e}")
    return model, effort


def _codex_cmd(model, effort, prompt_file, last_message_file, workdir=None):
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
      * `- < prompt_file` — the session prompt arrives on stdin, verbatim."""
    eff = f" -c model_reasoning_effort={shlex.quote(effort)}" if effort else ""
    # `rm -f` first (adversarial-review 2026-07-10, consensus HIGH): the wrapper
    # relays whatever the -o file contains, so a stale file from a prior attempt
    # must never survive into this invocation.
    cd = f"cd {shlex.quote(str(workdir))} && " if workdir else ""
    return (
        f"{cd}rm -f {shlex.quote(last_message_file)} && "
        "codex exec --ignore-user-config --ignore-rules --sandbox workspace-write "
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
            "next": dsp.next_action(manifest, statuses),
            "shipping": shp.shipping_summary(plan_dir, manifest),
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


def cmd_plan(plan_dir, resume, only_session, auto=False):
    if rsi.is_halted(plan_dir) and not resume:
        state = rsi.load_state(plan_dir)
        _out(
            {
                "action": "halted",
                "halt": state["halt"],
                "hint": "clear with `--clear-halt` after fixing, or pass --resume",
            },
            plan_dir=plan_dir,
        )
        return
    manifest = mio.load_manifest(plan_dir)
    statuses = _statuses(plan_dir)
    _consistency_check(manifest, statuses)
    action = dsp.next_action(manifest, statuses, resume=resume, only_session=only_session)
    # Echo the autonomy posture so the orchestrator (and audit) record it. --auto
    # only affects how the orchestrator self-drives; it NEVER changes the decision
    # here (human checkpoints/blockers still surface). Human gates stay sacrosanct.
    action["auto_mode"] = bool(auto)
    # Plan complete: fire the opt-in notify_on_complete hook once (the "you can stop
    # watching now" ping for an unattended run). Idempotent + best-effort.
    if action.get("action") == "complete":
        action["notify_complete"] = rsi.notify_complete(plan_dir)
    _out(action, plan_dir=plan_dir)


def _consistency_check(manifest, statuses):
    """H4-lite: every manifest session id must exist as an anchored article."""
    missing = [s["id"] for s in manifest["sessions"] if s["id"] not in statuses]
    if missing:
        raise SystemExit(
            f"manifest/HTML mismatch — sessions not found as anchored articles: {missing}"
        )


def cmd_begin(plan_dir, sessions, unsafe_lock=False):
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
            root = _egress_root()
            hit = _find_restricted(root)
            _egress.update(
                root=str(root),
                hit=hit,
                opted_in=_egress_opt_in(ssot_text, root) if hit else False,
            )
        return _egress

    specs, unroutable = {}, {}
    for sid in sessions:
        s = by_id[sid]
        raw = s.get("model")
        declared_codex = isinstance(raw, str) and bool(_CODEX_DECLARED_RE.search(raw))
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
            if eg["hit"] and not eg["opted_in"]:
                raise UnroutableCodexSession(
                    f"data_sensitivity_guard egress: restricted content in the working tree "
                    f"({eg['hit']}) — DO-NOT-SEND to Codex without an unexpired per-repo "
                    f"egress_opt_ins entry in model-routing.yaml for {eg['root']}"
                )
            prompt_file = Path(plan_dir) / s.get("prompt_file", f"sessions/{sid}.prompt.md")
            if not prompt_file.exists():
                raise UnroutableCodexSession(
                    f"prompt file {prompt_file} missing — the codex exec command reads it from stdin"
                )
            codex_model, codex_effort = _resolve_codex_dispatch(
                raw, s.get("reasoning"), ssot_text, require_calibrated=not declared_codex
            )
            # Unique per invocation AND per attempt (adversarial-review 2026-07-10,
            # consensus HIGH): a path keyed only by session id let a stale/foreign
            # closeout from a prior run — or a concurrent plan reusing the same
            # session ids — be relayed as this run's result.
            stamp = f"{os.getpid()}-{int(time.time())}"
            lm_file = f"/tmp/codex-{sid}-{stamp}.last-message.txt"
            fb_lm_file = f"/tmp/codex-{sid}-{stamp}-fb.last-message.txt"
            cmd = _codex_cmd(codex_model, codex_effort, str(prompt_file), lm_file,
                             workdir=eg["root"])
            fb = _fallback_for(codex_model, "openai")
            fb_cmd = (
                _codex_cmd(fb[0], fb[1], str(prompt_file), fb_lm_file, workdir=eg["root"])
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

    if fs_class and unsafe_lock:
        rsi.log_event(plan_dir, "lock_fs_warning", fs_class=fs_class)
    rsi.acquire_lock(plan_dir)
    try:
        for sid in sessions:
            ab.apply_mutation(_html_path(plan_dir), sid, status="DOING", note=None)
        rsi.record_batch(plan_dir, sessions, "DISPATCHED")
        rsi.log_event(plan_dir, "dispatch_started", session_ids=sessions)
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
            if spec["backend"] == "codex":
                # Two-layer dispatch (s04): `prompt_text` here is the WRAPPER
                # prompt for a fixed Claude wrapper model; the session's real
                # prompt stays in prompt_file (codex reads it via stdin). No
                # Anthropic thinking directive is prepended — the reasoning tier
                # rides the `-c model_reasoning_effort=` flag instead.
                batch.append(
                    {
                        "id": sid,
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
                        # v1.5 D3 walk (sol→terra@max→gpt-5.5@xhigh); null = ladder
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
                    }
                )
                continue
            prompt_text = prompt_file.read_text() if prompt_file.exists() else ""
            # Prepend the reasoning directive (if any) so the extended-thinking
            # trigger lands before the task body. The manifest stays immutable and
            # the prompt file on disk is untouched — injection happens here, at
            # dispatch time. `low`/unset prepends nothing.
            reason_line = _reasoning_directive(s.get("reasoning"))
            if reason_line:
                prompt_text = reason_line + "\n\n" + prompt_text
            model_arg = _normalize_model(s.get("model"))
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
            fb = _fallback_for(model_arg, "anthropic")
            eg = egress_state()
            batch.append(
                {
                    "id": sid,
                    "subagent_type": s.get("dispatch", {}).get("subagent_type"),
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
                    # orchestrator to announce; the directive is already baked into
                    # prompt_text.
                    "reasoning": reason_tier,
                    # Reactive-degradation target if this model is refused at dispatch
                    # (Fable→Opus 4.8 → Sonnet 5, floor at Sonnet); null = no lower
                    # judgement-safe tier. Paired with the per-target reasoning
                    # tier from _DEGRADE_EFFORT (opus xhigh, sonnet high).
                    "fallback_model": fb[0] if fb else None,
                    "fallback_reasoning": fb[1] if fb else None,
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
                        "on_box_human" if eg["hit"] and not eg["opted_in"] else "cross_family"
                    ),
                    **(
                        {"executor_policy_note": spec["executor_policy_note"]}
                        if spec.get("executor_policy_note")
                        else {}
                    ),
                }
            )
        _out({"action": "dispatch", "active_provider": provider, "batch": batch}, plan_dir=plan_dir)
    except Exception:
        rsi.release_lock(plan_dir)
        raise


def cmd_apply(plan_dir, session_id, output_file, dry_run_shipping=False):
    manifest = mio.load_manifest(plan_dir)
    by_id = mio.session_by_id(manifest)
    if session_id not in by_id:
        raise SystemExit(f"unknown session {session_id!r}")
    session = by_id[session_id]
    items = session.get("items", [])
    raw = Path(output_file).read_text()

    result = cp.run_pipeline(raw, session_id, items)
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
        rsi.set_halt(plan_dir, f"{session_id}: subagent reported BLOCKED", session_id)

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


def cmd_clear_halt(plan_dir):
    state = rsi.clear_halt(plan_dir)
    _out({"halt": state["halt"]}, plan_dir=plan_dir)


def cmd_release(plan_dir):
    rsi.release_lock(plan_dir)
    _out({"released": True}, plan_dir=plan_dir)


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
    if cmd == "status":
        cmd_status(plan_dir)
    elif cmd == "plan":
        cmd_plan(plan_dir, args.resume, args.session, args.auto)
    elif cmd == "begin":
        cmd_begin(plan_dir, args.sessions, args.unsafe_lock)
    elif cmd == "apply":
        cmd_apply(plan_dir, args.session, args.output_file, args.dry_run_shipping)
    elif cmd == "checkpoint":
        cmd_checkpoint(plan_dir, args.session)
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


if __name__ == "__main__":
    main()
