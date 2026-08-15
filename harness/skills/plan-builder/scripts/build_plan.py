#!/usr/bin/env python3
"""
Build a self-contained HTML plan dashboard from a JSON spec (Aurora edition).

Usage:
    python build_plan.py <spec.json> <output.html> [--register-in <project-root>]

The script reads a plan spec JSON, validates it, then assembles the final HTML
by injecting generated fragments into the base template's marker comments.

The Aurora edition emits a DUAL-LAYER card structure:
  * HUMAN layer (prominent): id, title, status pill, plain-English `human_summary`,
    `deliverable` callout, "Why this matters" italic line.
  * AGENT layer (collapsible <details class="agent-spec">): description, agent
    instructions, schema, mockup, code excerpt, owner / target / touches.

All new spec fields are OPTIONAL — old specs continue to build identically
except for the visual refresh.

See ../references/schemas.md for the spec schema.
"""

import hashlib
import html
import json
import re
import os
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
TEMPLATE_PATH = SKILL_DIR / "assets" / "base-template.html"
DASHBOARD_ESLINT_CONFIG = SCRIPT_DIR / "dashboard-eslint.config.mjs"

# Single-source the shipping-adapter contract (git enum values + the capability
# probe) from the co-located plan-execute skill so build-time validation and
# runtime execution agree on one definition. Guarded: if plan-execute isn't
# present, shipping validation degrades to structural checks only.
_PE_SCRIPTS = SKILL_DIR.parent / "plan-execute" / "scripts"
try:
    if _PE_SCRIPTS.is_dir():
        sys.path.insert(0, str(_PE_SCRIPTS))
    import shipping_adapter as _ship_adapter
except ImportError:
    _ship_adapter = None

# Same single-sourcing for per-article runtime state: article_block.py defines
# what /plan-execute writes into a PLAN.html article AND how a --rebuild carries
# it forward (carry_over_state), so the two can never drift apart.
try:
    import article_block as _article_block
except ImportError:
    _article_block = None

# The FROZEN parallel-group contract's single shared checker (S06 / PL-02,
# ../../plan-execute/references/parallel-group-contract.md). Contract §5 puts the
# same rules at TWO call sites — here at BUILD time over spec.json, and in
# run.py at DISPATCH time over manifest.json — and forbids either side from
# reimplementing them, so both import this one module.
try:
    import parallel_contract as _pcon
except ImportError:
    _pcon = None

# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------
ID_REGEX = re.compile(r"^[a-z][a-z0-9-]{1,63}$")
# Cross-platform reserved filename roots (Windows + DOS legacy)
RESERVED_IDS = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{i}" for i in range(0, 10)),
    *(f"lpt{i}" for i in range(0, 10)),
}
VALID_STATUSES = {
    "TODO",
    "DOING",
    "DONE",
    "BLOCKED",
    "DEFERRED",
    "WONTFIX",
    "AWAITS_REVIEW",
    "PARTIAL",
}
VALID_INFOGRAPHIC_TYPES = [
    "phase-journey",
    "maturity-ladder",
    "hub-spoke",
    "before-after",
    "pillars",
    "custom",
]

INFOGRAPHIC_GROUP_KEYS = {
    "phase-journey": "phases",
    "maturity-ladder": "levels",
    "hub-spoke": "spokes",
    "before-after": "workstreams",
    "pillars": "pillars",
    "custom": "groups",
}
"""Where each Plan-Achievement shape keeps its list of item-bearing groups.

ONE definition on purpose: the progress bar counts through this list, the
mutation engine places new items into it (`plan_mutate.attach_to_infographic`
imports this name), and `validate_spec` refuses a spec that leaves an item out
of it. A second copy is how the renderer and the gate drift apart.
"""

# Dispatchable model tokens. CLAUDE_MODEL_TOKENS drive the classic Cowork-picker
# chip; CODEX_MODEL_TOKENS are the gpt-5.6-* family — first-class since 2026-07-28.
# 5.6-ONLY since 2026-08-13 (operator directive): gpt-5.5 was REMOVED from this list,
# so a new plan pinning it now warns at BUILD time instead of only failing at dispatch.
# (dual-harness S03, CL-01): a session pinned to one of these runs under
# `/plan-execute --harness codex` instead of the Cowork picker. Both families
# validate identically; only the dashboard chip color/CSS class differs (every
# Codex token shares one "codex" class — see _model_css_class below, since a raw
# token like "gpt-5.6-sol" contains dots that are invalid in a bare CSS class
# selector).
CLAUDE_MODEL_TOKENS = ("fable", "opus", "sonnet", "haiku")
CODEX_MODEL_TOKENS = ("gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna")
# RETIRED Codex tokens — NOT dispatchable (a spec pinning one is refused at build,
# see validate_spec) but still RENDERABLE: an already-built plan pinned to a retired
# model can be re-rendered at any time, and its chip needs a dot-free CSS class
# exactly as much as a live one. Dropping a token from CODEX_MODEL_TOKENS without
# landing it here emits `class="chip model-gpt-5.5"` — a class the dashboard CSS
# cannot select, which is the precise breakage the mapping exists to prevent.
RETIRED_CODEX_MODEL_TOKENS = ("gpt-5.5",)


def _is_codex_model(model_low):
    """True when a (lowered) model string names one of the live Codex tokens."""
    return any(t in model_low for t in CODEX_MODEL_TOKENS)


def _model_css_class(model_low):
    """CSS-safe chip class for a (lowered) model string. Codex tokens — live AND
    retired — all map to 'codex'; Claude tokens keep the pre-existing behavior (the
    lowered model string itself), so an all-Claude spec renders byte-identical chips
    to before this change."""
    codex = any(t in model_low for t in CODEX_MODEL_TOKENS + RETIRED_CODEX_MODEL_TOKENS)
    return "codex" if codex else model_low


# --------------------------------------------------------------------------
# Shipping (post_session / phase_closer) — schema, inheritance, build guards
# --------------------------------------------------------------------------
VALID_GIT = {"none", "commit", "commit-push", "commit-push-pr"}
VALID_FAILURE_MODES = {"fail-halt", "best-effort"}
VALID_SESSION_KINDS = {"ship-ready", "spike"}
# Keys a per-session post_session block may carry.
POST_SESSION_KEYS = {
    "git", "deploy", "deploy_argv", "pre_deploy_gates", "rollback_hint",
    "skip_if_partial", "command_failure_mode", "kind", "env_allowlist",
}
# A phase_closer is a post_session shape plus a phase-level checkpoint gate.
PHASE_CLOSER_KEYS = POST_SESSION_KEYS | {"require_human_checkpoint", "checkpoint"}

CHECKPOINT_BRIEF_KEYS = {"reason", "decision", "options"}


def _validate_checkpoint_brief(brief, where):
    """A human gate must ship its decision brief: `reason` (why a human must
    look — what is irreversible or judgment-laden here, plain language) and
    `decision` (the specific question the human answers when the plan parks).
    Optional `options`: the 2-4 concrete answers. A gate whose answer would
    always be "proceed" has no decision and should not exist — use a `verify`
    gate or `checkpoint_policy: "notify-and-continue"` instead."""
    if not isinstance(brief, dict):
        raise ValueError(
            f"{where} declares a human checkpoint but no checkpoint brief. Add "
            f'{where}.checkpoint = {{"reason": "<why a human must look — what is '
            f'irreversible or judgment-laden>", "decision": "<the specific question '
            f'the human answers>"}} (optional "options": [<the concrete answers>]). '
            f"If there is no real decision — the answer would always be 'proceed' — "
            f"drop the gate: use a verify gate or checkpoint_policy: "
            f"'notify-and-continue' instead."
        )
    unknown = set(brief) - CHECKPOINT_BRIEF_KEYS
    if unknown:
        raise ValueError(
            f"{where}.checkpoint has unknown keys: {sorted(unknown)} "
            f"(allowed: {sorted(CHECKPOINT_BRIEF_KEYS)})"
        )
    for k in ("reason", "decision"):
        v = brief.get(k)
        if not isinstance(v, str) or not v.strip():
            raise ValueError(
                f"{where}.checkpoint.{k} must be a non-empty plain-language string"
            )
    opts = brief.get("options")
    if opts is not None and (
        not isinstance(opts, list)
        or not opts
        or not all(isinstance(o, str) and o.strip() for o in opts)
    ):
        raise ValueError(
            f"{where}.checkpoint.options must be a non-empty list of strings"
        )

# Verify (session/phase verification gates) — the "don't ship trash" boundary.
# `require_evidence` (Vista ③) asserts the closeout carries an `evidence` list of
# paths that exist + are non-empty before DOING→DONE — a structural form of the
# "verify mechanism engagement" discipline.
VERIFY_KEYS = {"gates", "on_fail", "max_rework", "require_evidence", "checks"}
VALID_ON_FAIL = {"rework", "halt"}
# dispatch.depends_on_policy — how a session treats an upstream that terminally
# failed. "all" (default) = today's hard-AND: every dep must complete or the
# session never dispatches. "completed_or_terminal" = dispatch over the completed
# subset even if a dep terminally failed (a capstone that shouldn't be stranded).
VALID_DEPENDS_ON_POLICY = {"all", "completed_or_terminal"}
# verify.checks[] item keys — named evidence-artifact contracts the runner can
# assert (path exists + non-empty) at the apply boundary. Composes with
# require_evidence (which triggers the existence/non-empty enforcement).
VERIFY_CHECK_KEYS = {"name", "evidence_path", "assert"}


def _deploy_present(block):
    return bool(block.get("deploy_argv")) or (block.get("deploy", "none") not in (None, "none"))


def _validate_post_session_block(block, where, *, allow_checkpoint):
    """Structural validation of a post_session / phase_closer block. Registry
    resolution + the adapter probe happen later (they need the project root)."""
    if not isinstance(block, dict):
        raise ValueError(f"{where} must be an object")
    allowed = PHASE_CLOSER_KEYS if allow_checkpoint else POST_SESSION_KEYS
    unknown = set(block) - allowed
    if unknown:
        raise ValueError(f"{where} has unknown keys: {sorted(unknown)} (allowed: {sorted(allowed)})")

    git = block.get("git", "none")
    if git not in VALID_GIT:
        raise ValueError(f"{where}.git must be one of {sorted(VALID_GIT)}, got {git!r}")
    if "deploy" in block and not isinstance(block["deploy"], str):
        raise ValueError(f"{where}.deploy must be a string (target name or 'none')")
    argv = block.get("deploy_argv")
    if argv is not None and (not isinstance(argv, list) or not all(isinstance(a, str) for a in argv)):
        raise ValueError(f"{where}.deploy_argv must be a list of strings")
    gates = block.get("pre_deploy_gates")
    if gates is not None and (not isinstance(gates, list) or not all(isinstance(g, str) for g in gates)):
        raise ValueError(f"{where}.pre_deploy_gates must be a list of gate-id strings")
    cfm = block.get("command_failure_mode", "fail-halt")
    if cfm not in VALID_FAILURE_MODES:
        raise ValueError(f"{where}.command_failure_mode must be one of {sorted(VALID_FAILURE_MODES)}")
    if "skip_if_partial" in block and not isinstance(block["skip_if_partial"], bool):
        raise ValueError(f"{where}.skip_if_partial must be bool")
    if "rollback_hint" in block and not isinstance(block["rollback_hint"], str):
        raise ValueError(f"{where}.rollback_hint must be a string")
    if "kind" in block and block["kind"] not in VALID_SESSION_KINDS:
        raise ValueError(f"{where}.kind must be one of {sorted(VALID_SESSION_KINDS)}")
    ev = block.get("env_allowlist")
    if ev is not None and (not isinstance(ev, list) or not all(isinstance(x, str) for x in ev)):
        raise ValueError(f"{where}.env_allowlist must be a list of strings")
    if allow_checkpoint and "require_human_checkpoint" in block \
            and not isinstance(block["require_human_checkpoint"], bool):
        raise ValueError(f"{where}.require_human_checkpoint must be bool")
    if allow_checkpoint and block.get("require_human_checkpoint"):
        _validate_checkpoint_brief(block.get("checkpoint"), where)


def resolve_post_session(session, phases_by_id):
    """Merge a session's phase_closer defaults with its per-session override.

    phase_closer provides defaults; per-session post_session overrides per key.
    Returns the resolved block, or None when nothing actionable is declared
    (today's behaviour — shipping is opt-in)."""
    base = {}
    phase_id = session.get("phase")
    if phase_id and phase_id in phases_by_id:
        base = dict(phases_by_id[phase_id].get("phase_closer") or {})
    override = session.get("post_session") or {}
    if not base and not override:
        return None
    merged = dict(base)
    merged.update(override)
    merged.setdefault("git", "none")
    merged.setdefault("command_failure_mode", "fail-halt")
    actionable = (
        merged.get("git", "none") != "none"
        or merged.get("pre_deploy_gates")
        or _deploy_present(merged)
    )
    return merged if actionable else None


def _validate_verify_block(block, where):
    """Structural validation of a verify block (gate-registry resolution is a
    later guard — it needs the project root)."""
    if not isinstance(block, dict):
        raise ValueError(f"{where} must be an object")
    unknown = set(block) - VERIFY_KEYS
    if unknown:
        raise ValueError(f"{where} has unknown keys: {sorted(unknown)} (allowed: {sorted(VERIFY_KEYS)})")
    require_evidence = block.get("require_evidence", False)
    if not isinstance(require_evidence, bool):
        raise ValueError(f"{where}.require_evidence must be a bool, got {require_evidence!r}")
    gates = block.get("gates")
    # gates may be omitted when require_evidence carries the block on its own; but
    # an empty block (neither gates nor require_evidence) is meaningless.
    if gates is None and not require_evidence and not block.get("checks"):
        raise ValueError(f"{where} must declare `gates` (non-empty), `require_evidence: true`, "
                         f"and/or `checks` (non-empty)")
    if gates is not None and (not isinstance(gates, list) or not gates
                              or not all(isinstance(g, str) for g in gates)):
        raise ValueError(f"{where}.gates must be a non-empty list of gate-id strings")
    on_fail = block.get("on_fail", "rework")
    if on_fail not in VALID_ON_FAIL:
        raise ValueError(f"{where}.on_fail must be one of {sorted(VALID_ON_FAIL)}, got {on_fail!r}")
    mr = block.get("max_rework", 1)
    if not isinstance(mr, int) or mr < 0 or mr > 5:
        raise ValueError(f"{where}.max_rework must be an int 0..5, got {mr!r}")
    # verify.checks[] — named evidence-artifact contracts (optional). Each item
    # names an artifact the runner asserts exists + is non-empty; `assert` is a
    # human-readable description of what the artifact must show.
    checks = block.get("checks")
    if checks is not None:
        if not isinstance(checks, list) or not checks:
            raise ValueError(f"{where}.checks must be a non-empty list of check objects")
        for i, c in enumerate(checks):
            if not isinstance(c, dict):
                raise ValueError(f"{where}.checks[{i}] must be an object")
            unknown_ck = set(c) - VERIFY_CHECK_KEYS
            if unknown_ck:
                raise ValueError(f"{where}.checks[{i}] has unknown keys: {sorted(unknown_ck)} "
                                 f"(allowed: {sorted(VERIFY_CHECK_KEYS)})")
            for req in ("name", "evidence_path"):
                if not isinstance(c.get(req), str) or not c[req].strip():
                    raise ValueError(f"{where}.checks[{i}].{req} is required and must be a "
                                     f"non-empty string")
            if "assert" in c and not isinstance(c["assert"], str):
                raise ValueError(f"{where}.checks[{i}].assert must be a string")


def resolve_verify(session, phases_by_id):
    """Merge a phase-level verify default with the session's verify override.

    Mirrors ``resolve_post_session``: the phase provides defaults, the session
    overrides per key. Returns the resolved block, or None when no gates are
    declared (verification is opt-in — absent => today's behaviour)."""
    base = {}
    phase_id = session.get("phase")
    if phase_id and phase_id in phases_by_id:
        base = dict(phases_by_id[phase_id].get("verify") or {})
    override = session.get("verify") or {}
    if not base and not override:
        return None
    merged = dict(base)
    merged.update(override)
    if not merged.get("gates") and not merged.get("require_evidence") and not merged.get("checks"):
        return None
    merged.setdefault("on_fail", "rework")
    merged.setdefault("max_rework", 1)
    return merged


_PEER_TRIGGERS = frozenset(
    {"architecture_decision", "irreversible_change", "security_sensitive"}
)


def validate_peer_triggers(spec):
    """Build-time guard for the structured second-model-review gate (Layer 1,
    2026-07-10). Two checks, both raised as ValueError before manifest.json is
    written so a mis-declared plan never lands:

      1. Every value in a session's `peer_triggers` must be one of the three
         SSOT `codex_peer.triggers` names (typos fail loud, not silently).
      2. A session that declares ANY peer trigger MUST carry an
         `adversarial-review` verify gate (or reference `/adversarial-review`
         in its prompt / agent_instructions). This is the STRUCTURED,
         deterministic successor to the §4.0 `codex-trigger-no-gate` keyword
         heuristic — declared here, enforced here. Operator-directed promotion
         2026-07-10 (overrides the SSOT's "wait for an observed peer-gate miss"
         auto-trigger).
    """
    phases_by_id = {p["id"]: p for p in spec.get("phases", []) if "id" in p}
    for s in spec["sessions"]:
        triggers = s.get("peer_triggers") or []
        if not isinstance(triggers, list):
            raise ValueError(
                f"Session {s['id']} peer_triggers must be a list, got {type(triggers).__name__}."
            )
        bad = [t for t in triggers if t not in _PEER_TRIGGERS]
        if bad:
            raise ValueError(
                f"Session {s['id']} peer_triggers has unknown value(s) {bad!r}; "
                f"allowed: {sorted(_PEER_TRIGGERS)}."
            )
        if not triggers:
            continue
        # Gate must be present. Check the resolved verify block first, then a
        # prose escape hatch in the prompt / agent_instructions.
        vb = resolve_verify(s, phases_by_id) or {}
        has_gate = "adversarial-review" in (vb.get("gates") or [])
        if not has_gate:
            prose = str(s.get("prompt", ""))
            ai = s.get("agent_instructions") or []
            prose += " " + (" ".join(ai) if isinstance(ai, list) else str(ai))
            has_gate = "/adversarial-review" in prose or "adversarial-review" in prose
        if not has_gate:
            raise ValueError(
                f"Session {s['id']} declares peer_triggers {triggers!r} but has no "
                f"'adversarial-review' verify gate and does not reference "
                f"/adversarial-review in its prompt. Add the gate, or drop the "
                f"trigger if it genuinely doesn't apply (see schemas.md → peer_triggers)."
            )


def validate_verify_resolves(spec, project_root):
    """Build-time guard: every verify gate id must resolve in the project's
    eval-gates registry or the skill-bundled defaults (same registry as
    pre_deploy_gates). For skill-kind gates, probe_flags are also asserted
    against the live skill file so a drifted flag fails loud.
    Raises ValueError before manifest.json is written."""
    phases_by_id = {p["id"]: p for p in spec.get("phases", []) if "id" in p}
    reg = load_project_registries(project_root)
    bundled_gates = _bundled_gate_ids()
    full_reg = _merged_registries(project_root) if _ship_adapter is not None else None

    probe_roots = None
    if project_root:
        probe_roots = [str(Path(project_root) / ".claude" / "commands"),
                       str(Path(project_root) / ".claude" / "skills")]

    for s in spec["sessions"]:
        vb = resolve_verify(s, phases_by_id)
        if not vb:
            continue
        for gate in vb.get("gates", []):
            merged_gates = full_reg["gates"] if full_reg else reg["gates"]
            if gate not in merged_gates and gate not in bundled_gates:
                raise ValueError(
                    f"Session {s['id']} verify gate {gate!r} is not in the project's "
                    f".claude/eval-gates.json nor the skill-bundled defaults "
                    f"{sorted(bundled_gates)}."
                )
            # Probe probe_flags of skill-kind verify gates — fails loud on drift.
            if _ship_adapter is not None and gate in merged_gates:
                entry = merged_gates[gate]
                res = _ship_adapter.probe_registry_entry(gate, entry, probe_roots)
                if not res["ok"]:
                    raise ValueError(
                        f"Session {s['id']} verify gate {gate!r} probe_flags "
                        f"{res.get('missing', [])} are not present in skill "
                        f"{res.get('skill')!r} (adapter-contract-drift)."
                    )


def _transitive_upstream_items(session_id, sessions_by_id):
    """Declared item ids of a session + all transitive depends_on upstreams."""
    items, seen, stack = set(), set(), [session_id]
    while stack:
        sid = stack.pop()
        s = sessions_by_id.get(sid)
        if not s:
            continue
        items.update(s.get("items", []))
        for dep in s.get("dispatch", {}).get("depends_on", []) or []:
            if dep not in seen:
                seen.add(dep)
                stack.append(dep)
    return items


def deploy_auth_digest(session_id, sessions_by_id):
    """Build-time authorization digest binding a deploy to the DECLARED inputs it
    ships (this session's items + transitive upstreams'). At runtime /plan-execute
    recomputes over ACTUALLY-completed items; a divergence downgrades the
    pre-authorization to surface-and-confirm (the Terraform stale-plan class)."""
    items = _transitive_upstream_items(session_id, sessions_by_id)
    blob = json.dumps(sorted(items), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def load_project_registries(project_root):
    """Load .claude/{deploy-targets,eval-gates}.json from the project (if any)."""
    reg = {"deploy": {}, "gates": {}}
    if not project_root:
        return reg
    base = Path(project_root) / ".claude"
    for key, fname in (("deploy", "deploy-targets.json"), ("gates", "eval-gates.json")):
        p = base / fname
        if p.is_file():
            try:
                reg[key] = json.loads(p.read_text())
            except (json.JSONDecodeError, OSError) as e:
                raise ValueError(f"{p} is not valid JSON: {e}") from e
    return reg


def validate_shipping_resolves(spec, project_root):
    """Build-time guard: every declared deploy target / gate must resolve in the
    project registries, and every git step's adapter flags must still exist.
    For skill-kind registry entries, probe_flags are also asserted against the
    live skill file so a drifted/renamed flag fails loud rather than silently
    mis-routing at execution time.
    Raises ValueError (before manifest.json is written) on any miss."""
    phases_by_id = {p["id"]: p for p in spec.get("phases", []) if "id" in p}
    reg = load_project_registries(project_root)
    bundled_gates = _bundled_gate_ids()
    # Merged registries (bundled defaults + project-local) used for probe access.
    full_reg = _merged_registries(project_root) if _ship_adapter is not None else None

    probe_roots = None
    if project_root:
        probe_roots = [str(Path(project_root) / ".claude" / "commands"),
                       str(Path(project_root) / ".claude" / "skills")]

    for s in spec["sessions"]:
        ps = resolve_post_session(s, phases_by_id)
        if not ps:
            continue
        where = f"Session {s['id']} post_session"
        # git adapter probe (single-sourced from plan-execute).
        if _ship_adapter is not None and ps.get("git", "none") != "none":
            for step, res in _ship_adapter.probe_git_value(ps["git"], probe_roots):
                if not res["ok"]:
                    raise ValueError(
                        f"{where}: git={ps['git']!r} sub-step {step!r} references "
                        f"skill {res['skill']!r} flags {res['missing']} that are not present "
                        f"in its current interface (adapter-contract-drift). Update "
                        f"plan-execute/scripts/shipping_adapter.py or the skill."
                    )
        # deploy target must resolve in the merged registry (bundled defaults + project-local).
        if ps.get("deploy", "none") not in (None, "none") and not ps.get("deploy_argv"):
            merged_deploy = full_reg["deploy"] if full_reg else reg["deploy"]
            if ps["deploy"] not in merged_deploy:
                raise ValueError(
                    f"{where}: deploy target {ps['deploy']!r} is not defined in "
                    f"{Path(project_root or '.') / '.claude' / 'deploy-targets.json'} "
                    f"nor the skill-bundled deploy-targets.default.json. "
                    f"Declared targets: {sorted(merged_deploy)}"
                )
            # Probe probe_flags of skill-kind deploy targets — fails loud on drift.
            if _ship_adapter is not None:
                entry = merged_deploy[ps["deploy"]]
                res = _ship_adapter.probe_registry_entry(ps["deploy"], entry, probe_roots)
                if not res["ok"]:
                    raise ValueError(
                        f"{where}: deploy target {ps['deploy']!r} probe_flags "
                        f"{res.get('missing', [])} are not present in skill "
                        f"{res.get('skill')!r} (adapter-contract-drift). "
                        f"The registered probe_flags must exist in the live skill interface."
                    )
        # deploy-bearing sessions MUST declare a rollback_hint (mandatory, not optional).
        if _deploy_present(ps) and not ps.get("rollback_hint"):
            raise ValueError(
                f"{where}: a deploy-bearing session MUST declare a 'rollback_hint' "
                f"(command or note surfaced on later-invalidation)."
            )
        # pre_deploy_gates must resolve (project registry or skill-bundled defaults).
        for gate in ps.get("pre_deploy_gates", []) or []:
            merged_gates = full_reg["gates"] if full_reg else reg["gates"]
            if gate not in merged_gates and gate not in bundled_gates:
                raise ValueError(
                    f"{where}: pre_deploy_gate {gate!r} is not in the project's "
                    f".claude/eval-gates.json nor the skill-bundled defaults "
                    f"{sorted(bundled_gates)}."
                )
            # Probe probe_flags of skill-kind gates — fails loud on drift.
            if _ship_adapter is not None and gate in merged_gates:
                entry = merged_gates[gate]
                res = _ship_adapter.probe_registry_entry(gate, entry, probe_roots)
                if not res["ok"]:
                    raise ValueError(
                        f"{where}: pre_deploy_gate {gate!r} probe_flags "
                        f"{res.get('missing', [])} are not present in skill "
                        f"{res.get('skill')!r} (adapter-contract-drift)."
                    )


def _bundled_gate_ids():
    p = _PE_SCRIPTS.parent / "references" / "eval-gates.default.json"
    if p.is_file():
        try:
            return set(json.loads(p.read_text()))
        except (json.JSONDecodeError, OSError):
            return set()
    return set()


def _bundled_registries():
    """Load the skill-bundled default registries (deploy-targets + eval-gates).

    Mirrors the merge order in plan-execute/scripts/shipping.py: bundled defaults
    are the floor; project-local files win when present (but here we only load the
    bundled floor for probe-flag access during build-time validation).
    """
    result = {"deploy": {}, "gates": {}}
    base = _PE_SCRIPTS.parent / "references"
    for key, fname in (("deploy", "deploy-targets.default.json"),
                       ("gates", "eval-gates.default.json")):
        p = base / fname
        if p.is_file():
            try:
                result[key] = json.loads(p.read_text())
            except (json.JSONDecodeError, OSError):
                pass
    return result


def _merged_registries(project_root):
    """Project-local registries merged OVER the bundled defaults (project wins)."""
    merged = _bundled_registries()
    local = load_project_registries(project_root)
    merged["deploy"].update(local["deploy"])
    merged["gates"].update(local["gates"])
    return merged


def _check_id(kind, value):
    if not isinstance(value, str):
        raise ValueError(f"{kind} id must be a string, got {type(value).__name__}: {value!r}")
    if not ID_REGEX.match(value):
        raise ValueError(
            f"{kind} id {value!r} is invalid — must match ^[a-z][a-z0-9-]{{1,63}}$ "
            f"(lowercase letter first; lowercase/digits/hyphens; 2–64 chars)"
        )
    if value.lower() in RESERVED_IDS:
        raise ValueError(f"{kind} id {value!r} is reserved on Windows/DOS — pick another")


def _validate_dispatch_graph(sessions):
    """Detect cycles + check parallel-group depends_on consistency."""
    by_id = {s["id"]: s for s in sessions}
    # Topological sort to detect cycles
    visiting, visited = set(), set()

    def visit(sid, stack):
        if sid in visited:
            return
        if sid in visiting:
            cycle = " → ".join(stack + [sid])
            raise ValueError(f"Dispatch cycle detected: {cycle}")
        visiting.add(sid)
        deps = by_id[sid].get("dispatch", {}).get("depends_on", []) or []
        for dep in deps:
            if dep not in by_id:
                raise ValueError(f"Session {sid!r} depends_on unknown session {dep!r}")
            visit(dep, stack + [sid])
        visiting.discard(sid)
        visited.add(sid)

    for s in sessions:
        visit(s["id"], [])

    # parallel_group members must share the same depends_on set
    groups = {}
    for s in sessions:
        d = s.get("dispatch", {}) or {}
        g = d.get("parallel_group")
        if not g:
            continue
        deps = tuple(sorted(d.get("depends_on", []) or []))
        groups.setdefault(g, []).append((s["id"], deps))
    for g, members in groups.items():
        dep_sets = {deps for _, deps in members}
        if len(dep_sets) > 1:
            ids = ", ".join(m[0] for m in members)
            raise ValueError(
                f"parallel_group {g!r} members ({ids}) have mismatched depends_on sets: {dep_sets}"
            )


# --------------------------------------------------------------------------
# Parallel-group contract (S06 / PL-02) + parallelism shaping (S07 / PL-03)
# --------------------------------------------------------------------------
# The contract is FROZEN: ../../plan-execute/references/parallel-group-contract.md.
# Rule IDs below (R1, M1, M2, M2a, M3, M5, §1, §3) are that document's, and the
# refusals themselves come from the shared checker, never from a second
# implementation here.

PARALLEL_CONTRACT_MIN_SCHEMA = 3
"""The spec's OWN top-level ``plan_schema_version`` at which the v3+ rules of the
parallel-group contract are ENFORCED at build time (they are warnings below it).

Contract §6 gates those rules on the MANIFEST's version. At build time there is
no manifest yet, so the only honest input-side reading is the spec's own stamp —
exactly the shape ``PRIOR_ART_MIN_SCHEMA`` uses one field over, and for the same
measured reason. `validate_spec()` is shared plumbing: `plan_mutate._validate`
calls it on the spec of a LIVE plan for every add/amend/retire, and
`build_plan --rebuild` calls it on specs written months ago. Measured across this
repo's `_plans/*/spec.json` on 2026-08-12: enforcing unconditionally would refuse
5 of the 10 existing plans (M1 shipping members in
`claude-code-reliability-selfassessment`, `skill-quality-audit`; M2a missing
`touches` in `gearbox-source-of-truth`, `skill-unification`; M5 overlap in
`kun-chen-harness-adaptations`) — i.e. strand five running plans' mutation
engine on a rule their manifests are grandfathered out of anyway. Below the
threshold the refusals are still PRINTED as warnings (naming each rule), so the
diagnostic reaches the author instead of going silent.

R1 (dependency symmetry) is NOT gated by this, matching contract §6: it stays
hard for every version in `_validate_dispatch_graph`, exactly as before.
"""

# Path-shaped token test for `touches`. The field is free-form by schema, so
# prose like "the auth module and its tests" parses into ONE junk token that
# overlaps nothing — which would read as "file-disjoint" and produce a
# confidently wrong "these two could have run in parallel". So a token that is
# not a bare path makes the whole session UNPARSEABLE, and an unparseable
# session is treated as conflicting with EVERY other session. Conservative on
# purpose: a false conflict costs one missed suggestion; a false disjointness
# costs two agents writing the same file.
_PATHISH = re.compile(r"^[A-Za-z0-9_.~@+*-][A-Za-z0-9_./~@+*-]*$")


def _declared_writes(spec):
    """``{session_id: [path, ...] | None}`` — None = "conflicts with everything".

    None means the session's declared writes cannot be trusted as a conflict
    matrix input: it owns an item with no `touches`, or a `touches` token that
    is not a path (see `_PATHISH`). Normalisation is `parallel_contract`'s, so
    the pair suggestions here and the M5 refusals there read paths identically.
    """
    touches_by_item = {it.get("id"): it.get("touches") for it in spec.get("items") or []}
    out = {}
    for s in spec.get("sessions") or []:
        paths, usable = [], bool(s.get("items"))
        for iid in s.get("items") or []:
            raw = touches_by_item.get(iid)
            if not (raw and str(raw).strip()):
                usable = False
                continue
            for p in _pcon._touch_paths(raw):
                if not _PATHISH.match(p):
                    usable = False
                paths.append(p)
        out[s["id"]] = paths if (usable and paths) else None
    return out


def _session_layers(sessions):
    """``{session_id: depth}`` — depth 0 = no dependencies. Graph is acyclic by
    the time this runs (`_validate_dispatch_graph` refuses cycles first)."""
    by_id = {s["id"]: s for s in sessions}
    depth = {}
    walking = set()

    def d(sid):
        if sid in walking:
            return 0  # a cycle — `_validate_dispatch_graph` reports it properly
        if sid not in depth:
            walking.add(sid)
            deps = [
                x for x in ((by_id[sid].get("dispatch") or {}).get("depends_on") or [])
                if x in by_id and x != sid
            ]
            depth[sid] = 1 + max((d(x) for x in deps), default=-1)
            walking.discard(sid)
        return depth[sid]

    for s in sessions:
        d(s["id"])
    return depth


def parallelism_report(spec):
    """How parallel this plan's dispatch graph actually is (S07 / PL-03).

    Returns ``{sessions, critical_path, max_width, widths, ratio, groupable,
    opaque}``. `ratio` is sessions / critical-path length: 1.0 is a pure chain,
    N is a single fully parallel wave. `groupable` names pairs whose declared
    writes are disjoint — the sessions that COULD have shared a `parallel_group`
    — and `opaque` names the sessions excluded from that matrix because their
    `touches` is missing or unparseable (see `_declared_writes`).
    """
    sessions = list(spec.get("sessions") or [])
    depth = _session_layers(sessions)
    widths = {}
    for lvl in depth.values():
        widths[lvl] = widths.get(lvl, 0) + 1
    critical = (max(depth.values()) + 1) if depth else 0
    writes = _declared_writes(spec) if _pcon else {}

    # A closing acceptance review depends on everything by definition; suggesting
    # it as a parallel peer would be noise, not a finding.
    eligible = [
        s["id"] for s in sessions
        if not s.get("acceptance_review")
        and not (s.get("dispatch") or {}).get("integrates_group")
    ]
    grouped = {
        s["id"]: (s.get("dispatch") or {}).get("parallel_group") for s in sessions
    }
    groupable = []
    for i, a in enumerate(eligible):
        for b in eligible[i + 1:]:
            if grouped.get(a) and grouped.get(a) == grouped.get(b):
                continue  # already parallel with each other
            pa, pb = writes.get(a), writes.get(b)
            if not pa or not pb:
                continue
            if any(_pcon._overlaps(x, y) for x in pa for y in pb):
                continue
            groupable.append((a, b, ", ".join(sorted(pa)), ", ".join(sorted(pb))))
    return {
        "sessions": len(sessions),
        "critical_path": critical,
        "max_width": max(widths.values()) if widths else 0,
        "widths": widths,
        "ratio": round(len(sessions) / critical, 2) if critical else 0.0,
        "groupable": groupable,
        "opaque": sorted(sid for sid, p in writes.items() if not p),
    }


def format_parallelism_report(rep):
    """One line, for the `validate_spec.py` CLI and the width warning."""
    return (
        f"{rep['sessions']} sessions, critical path {rep['critical_path']}, "
        f"max layer width {rep['max_width']}, serialization ratio {rep['ratio']} "
        f"(1.0 = pure chain)"
    )


_MAX_LISTED_PAIRS = 8


def _width_warning(spec, rep):
    """Contract-first shaping (PL-03): a plan that is a pure chain must SAY why.

    Fires only when every dependency layer is one session wide and the spec
    carries no `serial_reason`. Names the file-disjoint pairs so the author sees
    the concrete alternative instead of an abstract nag.
    """
    if rep["max_width"] != 1 or rep["sessions"] < 2:
        return None
    if str(spec.get("serial_reason") or "").strip():
        return None
    pairs = rep["groupable"]
    if pairs:
        shown = "; ".join(f"{a}+{b} ({wa} | {wb})" for a, b, wa, wb in pairs[:_MAX_LISTED_PAIRS])
        more = f" (+{len(pairs) - _MAX_LISTED_PAIRS} more pair(s))" if len(pairs) > _MAX_LISTED_PAIRS else ""
        pair_text = (
            f" Sessions whose declared `touches` are file-disjoint, so they could have "
            f"shared a parallel_group: {shown}{more}."
        )
    else:
        pair_text = (
            " No file-disjoint session pair was found, so the chain may well be real — "
            "but nothing recorded that."
        )
    opaque = rep["opaque"]
    opaque_text = (
        f" {len(opaque)} session(s) ({', '.join(opaque[:8])}) declare no parseable `touches` "
        "and are treated as conflicting with everything (conservative) — declare path-shaped "
        "`touches` on write-heavy items to get a real conflict matrix."
        if opaque else ""
    )
    return (
        f"WARNING: this plan is a PURE CHAIN — {format_parallelism_report(rep)}. Every session "
        f"waits for the one before it.{pair_text}{opaque_text} If the chain is deliberate "
        '(one hand on one interface, a migration that must land in order, a spike whose result '
        'reshapes the next session), declare top-level "serial_reason": "<why>" and this '
        "warning goes quiet. See references/schemas.md -> 'serial_reason'."
    )


# CALIBRATED, not guessed (2026-08-14). Measured across 207 sessions in 25 plans
# on disk: p50 = 2,486 chars of `prompt`, p75 = 4,133, p90 = 6,409, p95 = 8,088.
# The threshold is p90, so this fires on roughly one session in ten.
#
# Why it is worth a warning at all: on the adaptive-routing plan, s02 (1,486
# chars) finished in 26 minutes and s03 (7,708) took five and a half hours
# across two attempts. Both declared ONE item, so nothing else in the builder
# could tell them apart — item count is the progress unit and it does not track
# work at all.
#
# Why it WARNS and never refuses: size predicts cost only loosely. s09 on that
# same plan sat at p84 and finished in six minutes, because most of its time was
# spent waiting on trials rather than writing code. This flags a card worth a
# second look; it does not know how long the work takes. A deliberately large
# session is a legitimate choice, so there is no flag to silence it — the plan
# author reads the line and decides.
_SESSION_PROMPT_P90 = 6400


def _oversized_session_warning(spec):
    """Warn on any session whose `prompt` exceeds the corpus p90 (see above)."""
    big = [
        (s["id"], len(s.get("prompt") or ""))
        for s in spec.get("sessions", [])
        if len(s.get("prompt") or "") > _SESSION_PROMPT_P90
    ]
    if not big:
        return
    listed = ", ".join(f"{sid} ({n:,} chars)" for sid, n in sorted(big, key=lambda x: -x[1]))
    print(
        f"WARNING: {len(big)} session(s) carry a work brief above the {_SESSION_PROMPT_P90:,}-char "
        f"p90 of every plan measured on this machine: {listed}. A brief this size usually means "
        "several sessions' worth of work on one card — the largest measured case ran 12x longer "
        "than the smallest, and both declared one item, so item count cannot warn you. Consider "
        "splitting at an item boundary. This is advisory: a deliberately large session is fine.",
        file=sys.stderr,
    )


def _integration_session_id(pg, taken):
    """A stable, ID_REGEX-legal id for an auto-emitted integration session."""
    base = (slugify(f"{pg}-integration") or "integration")[:64].rstrip("-")
    if not ID_REGEX.match(base):
        base = f"g-{base}"[:64].rstrip("-")
    sid, n = base, 2
    while sid in taken:
        sid = f"{base[:60].rstrip('-')}-{n}"
        n += 1
    return sid


def _integration_prompt(pg, member_ids, gates):
    """The task body for an auto-emitted integration session (contract §3)."""
    gate_line = ", ".join(f"`{g}`" for g in gates) if gates else "(no member declared gates)"
    return f"""Integrate the worktree-isolated parallel group `{pg}` and ship it.

This session was emitted by `plan-builder` from the frozen parallel-group contract
(`skills/plan-execute/references/parallel-group-contract.md` §3) — the group's members are
barred from committing (M1), so this session owns the group's single shipping action.

Members: {', '.join(member_ids)}.

Do this in order, and stop loudly rather than improvising:

1. **Record the baseline BEFORE touching anything** (§3 rule 7): the repository `HEAD`, the
   current branch, and the pre-existing dirty state (`git status --porcelain`). You stage only
   paths this group owns — never sweep up edits that were already in the tree.
2. **Verify containment** (§3 rule 6): each member's changes must be present in that member's own
   worktree/branch, and the shared tree must NOT have been written by a member. Containment is
   ADVISORY in this contract — nothing in the harness confines a member to its worktree — so this
   check is the only thing that detects a member that ignored it. A member whose work landed in
   the shared tree, or a stray member commit, is a HALT: report it, do not merge around it.
3. **Confirm the base ref** every member worktree was created from is the one you are merging
   into. `git worktree add -b <b>` with no start-point bases on local `HEAD`, not
   `origin/<default>` (measured, S02 §4) — if `HEAD` moved under the group, say so instead of
   silently merging onto a different base.
4. **Merge member branches producer-first** (§3 rule 5). A conflict HALTS with every member
   branch intact. Never let an agent "resolve" a merge — that is how work disappears.
5. **Re-run the full gate set on the merged tree**: {gate_line}. A clean textual merge does not
   imply a working tree — member A renaming a symbol and member B adding a caller of the old name
   merge without complaint and are caught only here.
6. **Then ship**, per this session's declared post-session action.

Two limits worth knowing before you debug something else: a fresh worktree is tracked-files-only
(untracked and gitignored files NEVER carry over — measured, S02 §5), so a member that needed a
local `.env`, a build cache, or any per-worktree setup had to create it inside its own worktree;
and no member may have changed a dependency manifest or lockfile (M2), so if you are staring at a
lockfile diff, something violated the contract — report it rather than resolving it.
"""


def synthesize_integration_sessions(spec):
    """Contract §3 — auto-emit the integration session for every WORKTREE-ISOLATED
    parallel group that does not already declare one. Returns a NEW sessions list.

    Idempotent: a group that already has a session carrying
    `dispatch.integrates_group: "<pg>"` is left exactly as the author wrote it.
    The author is never asked to write this session (PL-03) — but the emitted
    session is a plainly DECLARED one, so the executor still reads it as declared
    rather than deriving anything (contract §3: "declared, not derived").

    SHARED-TREE groups are deliberately NOT auto-emitted: §3 makes their
    integration session optional (a plan that never commits is legitimate), and
    the shared checker already WARNS when one is missing. Auto-emitting a commit
    into a plan whose author chose not to ship would be the worse default.
    """
    sessions = list(spec.get("sessions") or [])
    if not _pcon:
        return sessions
    declared = {
        (s.get("dispatch") or {}).get("integrates_group")
        for s in sessions
    }
    taken = {s["id"] for s in sessions}
    for pg, members in sorted(_pcon.groups({"sessions": sessions}).items()):
        if pg in declared:
            continue
        if not any(
            (s.get("dispatch") or {}).get("isolation") == _pcon.ISOLATION_WORKTREE
            for s in members
        ):
            continue
        member_ids = sorted(s["id"] for s in members)
        gates = sorted({g for s in members for g in ((s.get("verify") or {}).get("gates") or [])})
        sid = _integration_session_id(pg, taken)
        taken.add(sid)
        integ = {
            "id": sid,
            "title": f"Integrate parallel group {pg}",
            # Merging N isolated branches, detecting containment violations and
            # re-running the whole gate set on the merged tree is multi-file
            # integration work with a real judgment surface — agentic_build.
            "model": "Opus",
            "reasoning": "high",
            "effort": "~1h",
            "task_class": "agentic_build",
            "human_summary": (
                f"Bring the parallel work from {', '.join(member_ids)} back together, check it "
                "still works as one, and commit it."
            ),
            "deliverable": (
                f"The {pg} member branches are merged producer-first, the group's full gate set "
                "passes on the merged tree, and the group's work is committed once."
            ),
            "why_model": (
                "Auto-emitted integration session: a merge conflict or a semantic break across "
                "two members' work is exactly the non-obvious multi-file case Opus/high is for."
            ),
            "items": [],
            "prompt": _integration_prompt(pg, member_ids, gates),
            "dispatch": {
                "subagent_type": None,
                "parallel_group": None,
                "integrates_group": pg,
                # Contract §4: a member that ends non-DONE BLOCKS integration —
                # so the default "all" policy is the required one here. Never
                # completed_or_terminal: merging a half-finished member's branch
                # is the exact "partial mixed with successful peer output" failure.
                "depends_on": member_ids,
                "requires_human_checkpoint": False,
            },
            # §3 rule 4 — the group's SINGLE git action. `commit` rather than
            # `commit-push`: pushing is the author's call, and a conservative
            # default here is a local commit the operator can inspect.
            "post_session": {"git": "commit", "kind": "ship-ready"},
        }
        if gates:
            # §3 rule 3 — a SUPERSET of the union of the members' gates, so
            # "re-run the full gate set on the merged tree" is checkable.
            integ["verify"] = {"gates": gates, "on_fail": "rework", "max_rework": 1}
        last = max(i for i, s in enumerate(sessions) if s["id"] in set(member_ids))
        sessions.insert(last + 1, integ)
    return sessions


def with_integration_sessions(spec):
    """`spec` with the emitted integration sessions folded in.

    Returns the spec UNTOUCHED when `sessions` is missing or not a list — the
    synthesis must never manufacture a `sessions` key, or a malformed spec would
    build an empty plan instead of failing its required-field check.
    """
    if not isinstance(spec.get("sessions"), list):
        return spec
    return dict(spec, sessions=synthesize_integration_sessions(spec))


def _check_parallel_contract(spec):
    """Run the shared checker over the spec (contract §5, build-time call site).

    Refusals are hard on a spec that opts in (see `PARALLEL_CONTRACT_MIN_SCHEMA`)
    and printed as warnings below that threshold. Raises ValueError; prints
    warnings to stderr.
    """
    if _pcon is None:
        print(
            "WARNING: the co-located plan-execute skill is missing "
            "(scripts/parallel_contract.py), so the frozen parallel-group contract is NOT "
            "checked at build time. The dispatch-time gate still refuses violations.",
            file=sys.stderr,
        )
        return
    # The checker must see the plan as it will be BUILT — including the
    # integration sessions this builder emits — or a worktree group would be
    # refused under §3 for lacking the very session the builder is about to write.
    checked = with_integration_sessions(spec)
    refusals, warnings = _pcon.check(checked, schema_version=PLAN_SCHEMA_VERSION)
    enforced = (spec.get("plan_schema_version") or 0) >= PARALLEL_CONTRACT_MIN_SCHEMA
    if refusals and enforced:
        raise ValueError(_pcon.refusal_text(refusals))
    for r in refusals:
        print(
            f"WARNING: [would be REFUSED at dispatch] {r} (this spec declares no "
            f'"plan_schema_version": {PARALLEL_CONTRACT_MIN_SCHEMA}+, so the contract is not '
            "enforced at build time — but the manifest this build writes is stamped "
            f"{PLAN_SCHEMA_VERSION}, and /plan-execute WILL refuse it)",
            file=sys.stderr,
        )
    for w in warnings:
        print(f"WARNING: {w}", file=sys.stderr)
    if enforced:
        msg = _width_warning(spec, parallelism_report(checked))
        if msg:
            print(msg, file=sys.stderr)


# --------------------------------------------------------------------------
# Research-tool availability probe (RS-06, 2026-08-13)
# --------------------------------------------------------------------------
# HONEST NAME FIRST. This is a *configuration* probe, NOT a reachability probe.
# build_plan.py is a plain Python script: it does not hold the research tools,
# the authoring agent does, and it calls them through the harness. So the script
# cannot ask "did Perplexity answer?" — it can only read, from configuration it
# genuinely has on disk, whether this environment OFFERS the authoring agent any
# research capability at all. It makes no network call, tests no credential, and
# reads no secret material (never `.env*`, never a keychain, never a credential
# store, and never the VALUES inside an MCP server block — only the server
# NAMES). What it can prove is therefore narrow and stated plainly everywhere it
# is documented: "configured and permitted", never "reachable".
#
# What it buys, exactly: the sentence "the research tools were absent, so the
# pass was skipped" stops being an author assertion the builder takes on trust.
# An item that makes that claim structurally (`research_status: "unavailable"`)
# is REFUSED at build time when the probe can see research capability sitting
# right there. And any item that makes a skip claim of either kind gets the
# machine's own account of the environment stamped into the plan next to the
# author's prose (see `research_env_record`).
RESEARCH_MCP_TOKENS = frozenset({
    "exa", "perplexity", "tavily", "brave", "firecrawl", "context7",
    "ref", "grep", "kagi", "serper", "searxng", "websearch", "search",
})
"""Name tokens that mark a configured MCP server as research-capable. Matched
against the server name split on non-alphanumerics (so `claude_ai_Exa` -> the
token `exa` hits, and a server merely CONTAINING the letters "ref" does not).
Deliberately a small, boring allowlist: a name we do not recognize is simply
not counted as a research signal, which fails toward ACCEPTING an
`unavailable` claim rather than toward refusing a legitimate one."""

BUILTIN_RESEARCH_TOOLS = ("WebSearch", "WebFetch")
"""Claude Code's own research tools. Present on a stock install — the practical
consequence being that on a normal machine the probe reports research capability
AVAILABLE, and an `unavailable` claim is therefore nearly always false. That is
the point of the check, not a defect in it."""


def _read_json_quietly(path):
    """Parse a JSON config file; None on any failure (missing, unreadable,
    malformed). A probe that raises would turn an unrelated broken config into a
    build failure, so every read here is best-effort."""
    try:
        return json.loads(Path(path).read_text())
    except Exception:
        return None


def _is_research_server(name):
    return bool(set(re.split(r"[^a-z0-9]+", str(name).lower())) & RESEARCH_MCP_TOKENS)


def probe_research_tools(project_root=None, home=None):
    """Observe — from CONFIGURATION ONLY — whether this environment offers the
    authoring agent research capability.

    Returns ``{"available": bool, "signals": [...], "sources": [...],
    "method": str}``. `signals` names each capability found (`mcp:<server>` or
    `builtin:WebSearch`); `sources` names each config file actually read, so the
    record is auditable after the fact.

    Read (config files only, names/permission entries only, never values):
      * ``~/.claude.json`` -> ``mcpServers`` (user level) and
        ``projects[<project_root>].mcpServers`` (this project only — NOT every
        project on the machine, which would report capability this build does
        not have).
      * ``<project_root>/.mcp.json`` -> ``mcpServers``.
      * ``permissions.deny`` in ``~/.claude/settings*.json`` and
        ``<project_root>/.claude/settings*.json`` — a bare ``"WebSearch"`` /
        ``"WebFetch"`` deny entry removes that built-in signal. A scoped deny
        (``WebFetch(domain:…)``) is a partial restriction, not an absence, and
        is deliberately NOT treated as removing the capability.

    Fails PERMISSIVE by construction: when no Claude Code configuration exists
    at all (a bare CI checkout, `python3 build_plan.py` on a stranger's box) the
    probe reports ``available: False`` — it genuinely observed nothing — which
    ACCEPTS an `unavailable` claim rather than breaking someone's build on the
    strength of a file that isn't there.
    """
    home = Path(home) if home else Path.home()
    proj = Path(project_root) if project_root else Path.cwd()
    signals, sources = [], []

    claude_json = home / ".claude.json"
    data = _read_json_quietly(claude_json)
    if isinstance(data, dict):
        sources.append(str(claude_json))
        names = set(data.get("mcpServers") or {})
        projects = data.get("projects")
        if isinstance(projects, dict):
            entry = projects.get(str(proj))
            if isinstance(entry, dict):
                names |= set(entry.get("mcpServers") or {})
        signals += [f"mcp:{n}" for n in sorted(names) if _is_research_server(n)]

    mcp_json = proj / ".mcp.json"
    data = _read_json_quietly(mcp_json)
    if isinstance(data, dict):
        sources.append(str(mcp_json))
        signals += [
            f"mcp:{n}" for n in sorted(data.get("mcpServers") or {}) if _is_research_server(n)
        ]

    # Built-in web research: available unless a settings file denies it outright.
    # Only claimed when a Claude Code config actually exists — otherwise we have
    # no evidence the harness is present and must not invent a signal.
    settings_paths = [
        home / ".claude" / "settings.json",
        home / ".claude" / "settings.local.json",
        proj / ".claude" / "settings.json",
        proj / ".claude" / "settings.local.json",
    ]
    denied, harness_seen = set(), claude_json.exists() or (home / ".claude").is_dir()
    for sp in settings_paths:
        data = _read_json_quietly(sp)
        if not isinstance(data, dict):
            continue
        sources.append(str(sp))
        harness_seen = True
        for entry in (data.get("permissions") or {}).get("deny") or []:
            denied.add(str(entry).strip())
    if harness_seen:
        signals += [f"builtin:{t}" for t in BUILTIN_RESEARCH_TOOLS if t not in denied]

    return {
        "available": bool(signals),
        "signals": sorted(set(signals)),
        "sources": sources,
        "method": (
            "configuration-only: configured MCP server names + permission deny-lists. "
            "No network call, no credential read, no reachability test."
        ),
    }


def research_env_record(spec):
    """The build-time environment record stamped onto a spec that makes ANY
    research-skip claim, or None when it makes none.

    None is the load-bearing case: a spec with no `research_status` anywhere —
    which is every plan built before 2026-08-13 — gets no new key, so its
    spec.json and manifest.json stay byte-identical and `plan_mutate`'s
    "manifest.json matches gen_manifest(spec.json)" consistency check keeps
    passing. The record is read back OUT of spec.json by `gen_manifest`, never
    re-probed there, so re-generating a manifest on another machine reproduces
    the same bytes.
    """
    if not any((it.get("research_status") or "").strip() for it in spec.get("items") or []):
        return None
    rec = dict(probe_research_tools())
    rec["probed_at"] = today_iso()
    return rec


def validate_spec(spec):
    """Raise ValueError with a clear message if spec is invalid."""
    required_top = ["title", "categories", "items", "sessions", "infographic"]
    for k in required_top:
        if k not in spec:
            raise ValueError(f"Missing required top-level field: {k!r}")

    if not isinstance(spec["categories"], list) or not spec["categories"]:
        raise ValueError("'categories' must be a non-empty list")

    # Scope boundary + fog register (2026-08-01, wayfinder-derived). Both optional,
    # both flat lists of strings — carried into manifest.json and rendered as a
    # static dashboard section. out_of_scope entries never graduate into work;
    # open_questions entries are decisions not yet sharp enough to assign.
    for f in ("out_of_scope", "open_questions"):
        v = spec.get(f)
        if v is not None:
            if not isinstance(v, list) or any(
                not isinstance(e, str) or not e.strip() for e in v
            ):
                raise ValueError(f"{f!r} must be a list of non-empty strings")

    # serial_reason (optional; S07 / PL-03) — the author's stated reason this plan
    # is a chain rather than a fan-out. Its ONLY effect is to silence the
    # pure-chain warning, so an empty string would silence it while saying
    # nothing: refuse that shape rather than accept a hollow justification.
    sr = spec.get("serial_reason")
    if sr is not None and (not isinstance(sr, str) or not sr.strip()):
        raise ValueError(
            "'serial_reason' must be a non-empty string explaining WHY this plan is a "
            "chain (it exists to silence the pure-chain warning; an empty one silences "
            "the warning without answering it)"
        )

    cat_keys = {c["key"] for c in spec["categories"]}
    for c in spec["categories"]:
        for f in ["key", "label"]:
            if f not in c:
                raise ValueError(f"Category missing '{f}': {c}")

    # Prior-art enforcement gate (RS-01/RS-02, 2026-08-12 hardening; reworked
    # 2026-08-12 after breaking 11/15 plan-execute test shards + the mutation
    # engine on every pre-existing plan -- validate_spec() is a shared,
    # general-purpose utility dozens of unrelated callers reuse to materialize
    # a throwaway spec, not exclusively plan-builder's own authoring path, so
    # the requirement CANNOT be unconditional or version-implicit (there is no
    # such thing as "the input spec's version" -- plan_schema_version has only
    # ever been an OUTPUT stamp gen_manifest assigns, never something read back
    # off spec.json). Mirrors closeout_pipeline.PLAN_IMPACT_MIN_SCHEMA exactly,
    # one layer earlier: a spec OPTS IN by declaring a top-level
    # `"plan_schema_version": 4` (which plan-builder's own interview flow now
    # stamps on every spec it compiles -- see SKILL.md); a spec that omits the
    # field -- EVERY pre-existing spec.json, EVERY unrelated test fixture --
    # validates exactly as it did before this feature existed. Conservative
    # reading, deliberately: unset/unknown version = pre-bump = unenforced.
    _prior_art_enforced = (spec.get("plan_schema_version") or 0) >= PRIOR_ART_MIN_SCHEMA

    item_ids = set()
    for it in spec["items"]:
        for f in ["id", "title", "category"]:
            if f not in it:
                raise ValueError(f"Item missing '{f}': {it}")
        _check_id("Item", it["id"])
        if it["category"] not in cat_keys:
            raise ValueError(f"Item {it['id']} references unknown category {it['category']!r}")
        if it["id"] in item_ids:
            raise ValueError(f"Duplicate item id: {it['id']}")
        item_ids.add(it["id"])

        # Prior-art decision (RS-01/RS-02, 2026-08-12 hardening). The P8 research
        # step asks one question per item -- does a proven solution already cover
        # this scope? Optional AT THE SHAPE LEVEL (either shape below satisfies
        # it), but the PRESENCE of a decision is mandatory ON A SPEC THAT OPTS IN
        # (see `_prior_art_enforced` above, gated on the spec's own top-level
        # `plan_schema_version` -- mirrors closeout_pipeline.PLAN_IMPACT_MIN_SCHEMA
        # exactly: a spec that never declares its version is untouched, never
        # retroactively refused). Shape checks below run whenever the fields are
        # PRESENT regardless of the gate -- harmless for a pre-bump spec, which
        # never carries them.
        pa = it.get("prior_art")
        rs = it.get("research_status")
        if pa is not None:
            if not isinstance(pa, dict):
                raise ValueError(f"Item {it['id']}.prior_art must be an object")
            unknown = set(pa) - {"decision", "source", "note"}
            if unknown:
                raise ValueError(
                    f"Item {it['id']}.prior_art has unknown key(s) {sorted(unknown)}; "
                    "allowed: decision, source, note"
                )
            dec = pa.get("decision")
            if dec not in ("adopt", "adapt", "build"):
                raise ValueError(
                    f"Item {it['id']}.prior_art.decision must be adopt|adapt|build, got {dec!r}"
                )
            src = pa.get("source")
            if not isinstance(src, str) or not src.strip():
                raise ValueError(
                    f"Item {it['id']}.prior_art.source is required (non-empty string) -- "
                    f"a {dec!r} decision with no source is unenforceable (2026-08-12 hardening)"
                )
            note = pa.get("note")
            if note is not None and not isinstance(note, str):
                raise ValueError(f"Item {it['id']}.prior_art.note must be a string")
        if rs is not None:
            if rs not in ("skipped", "unavailable"):
                raise ValueError(
                    f"Item {it['id']}.research_status must be 'skipped' or 'unavailable' "
                    f"(or omitted), got {rs!r}"
                )
            reason = it.get("research_reason")
            if rs == "skipped":
                if not isinstance(reason, str) or not reason.strip():
                    raise ValueError(
                        f"Item {it['id']}.research_reason is required (non-empty string) "
                        "when research_status is 'skipped'"
                    )
            else:
                # research_status: "unavailable" (RS-06, 2026-08-13) — the ONE
                # value that asserts the tooling was absent, and the one claim
                # the builder can adjudicate for itself instead of taking on
                # trust. NOT version-gated, and it needs no gate: the value was
                # ILLEGAL until this change (the branch above refused anything
                # but "skipped"), so no spec on disk can carry it and still have
                # built. Every new refusal below therefore fires only on specs
                # that could not previously build at all — measured across every
                # spec.json under every `_plans/` tree on this machine, zero
                # newly refused. See `probe_research_tools` for what the probe
                # can and cannot see; `research_reason` is OPTIONAL here because
                # the system supplies the reason.
                if reason is not None and (not isinstance(reason, str) or not reason.strip()):
                    raise ValueError(
                        f"Item {it['id']}.research_reason, when present alongside "
                        "research_status: 'unavailable', must be a non-empty string"
                    )
                probe = probe_research_tools()
                if probe["available"]:
                    raise ValueError(
                        f"Item {it['id']}.research_status is 'unavailable', but the build-time "
                        "probe found research capability configured in this environment: "
                        + ", ".join(probe["signals"])
                        + " (read from " + ", ".join(probe["sources"]) + "). "
                        "'The research tools were absent' is a claim the builder checks, not "
                        "one it takes from the author (RS-06). Either run the prior-art pass, "
                        "or — if you chose not to research this item for some other reason — "
                        "say so with research_status: 'skipped' plus a research_reason. "
                        "NOTE the probe reads configuration only (MCP server names + "
                        "permission deny-lists); it cannot tell whether a configured tool "
                        "actually answered."
                    )
        if pa is None and rs is None and _prior_art_enforced:
            raise ValueError(
                f"Item {it['id']} must carry either prior_art (decision+source) or "
                "research_status: 'skipped' with research_reason (or 'unavailable', which "
                "the build-time probe must corroborate) -- the prior-art pass "
                "(RS-01) must be explicit, never silently absent (2026-08-12 hardening). "
                "See references/schemas.md -> 'Prior-art decision'."
            )

    session_ids = set()
    for s in spec["sessions"]:
        for f in ["id", "title", "model", "items"]:
            if f not in s:
                raise ValueError(f"Session missing '{f}': {s}")
        _check_id("Session", s["id"])
        if s["id"] in session_ids:
            raise ValueError(f"Duplicate session id: {s['id']}")
        session_ids.add(s["id"])
        for iid in s["items"]:
            if iid not in item_ids:
                raise ValueError(f"Session {s['id']} references unknown item {iid!r}")

        # Model/reasoning tiers: a typo here silently drops the deliberately-chosen
        # tier at dispatch (run.py omits `model` for unrecognized values, and an
        # unknown reasoning tier maps to no thinking directive). Validate at build.
        r = s.get("reasoning")
        if r is not None and str(r).strip():  # None/'' = unset (run.py: no directive)
            if str(r).strip().lower() not in {"low", "medium", "high", "xhigh", "max"}:
                raise ValueError(
                    f"Session {s['id']}.reasoning must be low|medium|high|xhigh|max (or omitted), got {r!r}"
                )
        model_low = str(s["model"]).strip().lower()
        if not any(t in model_low for t in CLAUDE_MODEL_TOKENS) and not _is_codex_model(model_low):
            print(
                f"WARNING: Session {s['id']}.model {s['model']!r} will not normalize to a "
                "dispatchable model token (fable/opus/sonnet/haiku/gpt-5.6-sol/"
                "gpt-5.6-terra/gpt-5.6-luna) — /plan-execute will "
                "omit `model` and the subagent inherits the orchestrator's model.",
                file=sys.stderr,
            )

        # escalation (optional, ESC-02, 2026-08-13) — the per-session opt-OUT of
        # the upward rework climb. Only `false` is meaningful; `true` is the
        # default and writing it changes nothing. Validated as a strict bool so a
        # string "false" (which is truthy, and would silently leave escalation ON)
        # is a build error rather than a surprise at the third rework.
        if "escalation" in s and not isinstance(s["escalation"], bool):
            raise ValueError(
                f"Session {s['id']}.escalation must be true or false (bool), got "
                f"{s['escalation']!r}. Omit it for the default (escalation ON, from "
                f"plan_schema_version {PLAN_SCHEMA_VERSION}); set it to false to pin "
                "this session to its authored model however often it fails."
            )

        # routing_experiment (optional, 2026-08-13) — tags a session as belonging
        # to a named routing canary so the outcome ledger can separate its records
        # from the general population. The BUILDER owns validation + propagation;
        # the ledger only READS it back off the manifest, which is why it is
        # defined here rather than in the consumer.
        rx = s.get("routing_experiment")
        if rx is not None:
            if not isinstance(rx, dict):
                raise ValueError(f"Session {s['id']}.routing_experiment must be an object")
            unknown = sorted(set(rx) - {"kind", "proposal_id"})
            if unknown:
                raise ValueError(
                    f"Session {s['id']}.routing_experiment has unknown key(s) {unknown}; "
                    "allowed: kind, proposal_id"
                )
            for f in ("kind", "proposal_id"):
                if f not in rx:
                    raise ValueError(
                        f"Session {s['id']}.routing_experiment.{f} is required — an "
                        "untagged canary record is indistinguishable from an ordinary "
                        "one in the ledger, which defeats the point of tagging it"
                    )
                if not isinstance(rx[f], str) or not rx[f].strip():
                    raise ValueError(
                        f"Session {s['id']}.routing_experiment.{f} must be a non-empty string"
                    )

        # Dispatch block (optional but if present, validate)
        d = s.get("dispatch")
        if d is not None:
            if not isinstance(d, dict):
                raise ValueError(f"Session {s['id']}.dispatch must be an object")
            for f in (
                "subagent_type",
                "parallel_group",
                "isolation",
                "integrates_group",
                "depends_on",
                "requires_human_checkpoint",
                "max_retries",
            ):
                if f in d and d[f] is not None:
                    if f in ("subagent_type", "parallel_group", "isolation",
                             "integrates_group") and not isinstance(d[f], str):
                        raise ValueError(f"Session {s['id']}.dispatch.{f} must be a string or null")
                    if f == "depends_on" and not isinstance(d[f], list):
                        raise ValueError(f"Session {s['id']}.dispatch.depends_on must be a list")
                    if f == "requires_human_checkpoint" and not isinstance(d[f], bool):
                        raise ValueError(
                            f"Session {s['id']}.dispatch.requires_human_checkpoint must be bool"
                        )
                    if f == "max_retries" and not isinstance(d[f], int):
                        raise ValueError(f"Session {s['id']}.dispatch.max_retries must be int")
            if d.get("max_retries", 0) < 0 or d.get("max_retries", 0) > 5:
                raise ValueError(f"Session {s['id']}.dispatch.max_retries must be 0..5")
            # A human gate without its decision brief is a build error (owner
            # policy 2026-07-11): the operator who hits the gate weeks later must
            # see why it exists and what they are deciding, not just "review s07".
            if d.get("requires_human_checkpoint"):
                _validate_checkpoint_brief(
                    d.get("checkpoint"), f"Session {s['id']}.dispatch"
                )
            # checkpoint_policy (optional) — OR-03 per-gate autonomy for the
            # AWAITS_REVIEW-ack gate. Only "block" | "notify-and-continue".
            cpol = d.get("checkpoint_policy")
            if cpol is not None and cpol not in ("block", "notify-and-continue"):
                raise ValueError(
                    f"Session {s['id']}.dispatch.checkpoint_policy must be "
                    f"'block' or 'notify-and-continue', got {cpol!r}"
                )
            if d.get("guards_irreversible") is not None and not isinstance(
                d.get("guards_irreversible"), bool
            ):
                raise ValueError(
                    f"Session {s['id']}.dispatch.guards_irreversible must be bool"
                )
            # codex_shell (optional, 2026-07-29) — the shell capabilities this
            # session needs from a `codex exec` dispatch. Absent = today's
            # `--sandbox workspace-write` with nothing added, which MEASURABLY
            # denies writes outside the repo, all network, and nested
            # `codex exec`/vendor CLIs. A session that needs any of those must
            # SAY SO, or a `--harness codex` run fails it one session at a time.
            sh = d.get("codex_shell")
            if sh is not None:
                if not isinstance(sh, dict):
                    raise ValueError(
                        f"Session {s['id']}.dispatch.codex_shell must be an object with keys "
                        "writable_roots / network / sandbox / env_include"
                    )
                unknown = set(sh) - {"writable_roots", "network", "sandbox", "env_include"}
                if unknown:
                    raise ValueError(
                        f"Session {s['id']}.dispatch.codex_shell has unknown key(s) "
                        f"{sorted(unknown)}; allowed: writable_roots, network, sandbox, env_include"
                    )
                wr = sh.get("writable_roots")
                if wr is not None and (
                    not isinstance(wr, list)
                    or not all(isinstance(x, str) and x.strip() for x in wr)
                ):
                    raise ValueError(
                        f"Session {s['id']}.dispatch.codex_shell.writable_roots must be a "
                        "list of non-empty path strings (e.g. [\"~/.dyno\"])"
                    )
                if sh.get("network") is not None and not isinstance(sh.get("network"), bool):
                    raise ValueError(
                        f"Session {s['id']}.dispatch.codex_shell.network must be bool"
                    )
                env_include = sh.get("env_include")
                if env_include is not None and (
                    not isinstance(env_include, list)
                    or not all(
                        isinstance(name, str)
                        and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name)
                        for name in env_include
                    )
                ):
                    raise ValueError(
                        f"Session {s['id']}.dispatch.codex_shell.env_include must be a "
                        "list of environment variable names"
                    )
                sbx = sh.get("sandbox")
                if sbx is not None and sbx not in ("workspace-write", "danger-full-access"):
                    raise ValueError(
                        f"Session {s['id']}.dispatch.codex_shell.sandbox must be "
                        "'workspace-write' (the default) or 'danger-full-access', got {!r}".format(sbx)
                    )
                if sbx == "danger-full-access":
                    # Only nested `codex exec` / vendor-CLI dispatch needs this, and
                    # it hands the dispatched agent an UNSANDBOXED shell. Two rules,
                    # both build-time: no redundant narrower keys beside it, and a
                    # human must gate the session. run.py re-checks the gate at
                    # dispatch (a manifest is editable; the invariant must not be).
                    if wr or sh.get("network"):
                        raise ValueError(
                            f"Session {s['id']}.dispatch.codex_shell: sandbox "
                            "'danger-full-access' already grants writes anywhere and network — "
                            "drop writable_roots/network so the command has one reading"
                        )
                    gated = bool(
                        d.get("guards_irreversible")
                        or d.get("requires_human_checkpoint")
                        or (s.get("task_class") or "").strip().lower() == "linchpin"
                        or "irreversible_change" in (s.get("peer_triggers") or [])
                    )
                    if not gated:
                        raise ValueError(
                            f"Session {s['id']} asks for codex_shell.sandbox "
                            "'danger-full-access' (an UNSANDBOXED dispatched agent) but no "
                            "human gates it. Add dispatch.guards_irreversible: true (or "
                            "requires_human_checkpoint with its checkpoint brief), or drop to "
                            "'workspace-write' with writable_roots/network instead."
                        )
            # depends_on_policy (optional) — how to treat a terminally-failed dep.
            pol = d.get("depends_on_policy")
            if pol is not None and pol not in VALID_DEPENDS_ON_POLICY:
                raise ValueError(
                    f"Session {s['id']}.dispatch.depends_on_policy must be one of "
                    f"{sorted(VALID_DEPENDS_ON_POLICY)}, got {pol!r}"
                )
            # model_fallbacks / reasoning_fallbacks (optional) — machine-readable
            # degrade ladder the runner applies BEFORE dispatch (not prompt prose).
            for fb in ("model_fallbacks", "reasoning_fallbacks"):
                v = d.get(fb)
                if v is not None and (not isinstance(v, list)
                                      or not all(isinstance(x, str) for x in v)):
                    raise ValueError(
                        f"Session {s['id']}.dispatch.{fb} must be a list of strings"
                    )

        # Post-session shipping block (optional). Registry/probe resolution is a
        # later guard (needs the project root) — here we only check structure.
        if s.get("post_session") is not None:
            _validate_post_session_block(
                s["post_session"], f"Session {s['id']}.post_session", allow_checkpoint=False
            )
        # Verify block (optional). Gate-registry resolution is a later guard.
        if s.get("verify") is not None:
            _validate_verify_block(s["verify"], f"Session {s['id']}.verify")
        # P4 — fork ignores the per-session model: a true fork (subagent_type
        # "fork") inherits the orchestrator's context AND always runs the
        # orchestrator's model. Pairing it with a dispatchable model is a
        # contradiction the runner can't honor.
        _fork_model_low = str(s.get("model", "")).strip().lower()
        if d is not None and d.get("subagent_type") == "fork" and (
            any(t in _fork_model_low for t in CLAUDE_MODEL_TOKENS) or _is_codex_model(_fork_model_low)
        ):
            print(
                f"WARNING: Session {s['id']}.dispatch.subagent_type is 'fork' but model is "
                f"{s.get('model')!r}. A fork inherits the orchestrator's context and ALWAYS runs "
                "the orchestrator's model — the per-session model is ignored. Use a fresh agent "
                "(omit subagent_type, or set a typed agent) to honor the model.",
                file=sys.stderr,
            )
        if "phase" in s and not isinstance(s["phase"], str):
            raise ValueError(f"Session {s['id']}.phase must be a string (phase id)")
        if "acceptance_review" in s and not isinstance(s["acceptance_review"], bool):
            raise ValueError(
                f"Session {s['id']}.acceptance_review must be a boolean (true marks THE "
                "closing plan-level acceptance review; omit otherwise)"
            )

    # Closing acceptance review (2026-07-26): the plan-level validation session.
    # Session verify gates check the PARTS; nothing checked the WHOLE — a plan
    # where every session closed DONE can still miss its objectives through
    # accumulated deviations and descoped items. Exactly one session may carry
    # the marker, and a plan big enough to drift should have one.
    acceptance = [s["id"] for s in spec["sessions"] if s.get("acceptance_review")]
    if len(acceptance) > 1:
        raise ValueError(
            f"Sessions {acceptance} all set acceptance_review — a plan has exactly ONE "
            "closing acceptance review (it validates every other session's outcome)"
        )
    # ponytail: 4 is the "big enough to drift" line; raise it if small plans nag.
    if not acceptance and len(spec["sessions"]) >= 4:
        print(
            f"WARNING: {len(spec['sessions'])}-session plan has no closing acceptance-review "
            "session (no session sets acceptance_review: true). Session verify gates check "
            "each part; nothing will check whether the PLAN's objectives were achieved. See "
            "references/schemas.md -> 'Closing acceptance review'.",
            file=sys.stderr,
        )

    _oversized_session_warning(spec)

    # Optional top-level phases carrying phase_closer defaults that sessions
    # inherit (phase-default + per-session override).
    phase_ids = set()
    for p in spec.get("phases", []) or []:
        if "id" not in p:
            raise ValueError(f"phase missing 'id': {p}")
        _check_id("Phase", p["id"])
        if p["id"] in phase_ids:
            raise ValueError(f"Duplicate phase id: {p['id']}")
        phase_ids.add(p["id"])
        if p.get("phase_closer") is not None:
            _validate_post_session_block(
                p["phase_closer"], f"Phase {p['id']}.phase_closer", allow_checkpoint=True
            )
        if p.get("verify") is not None:
            _validate_verify_block(p["verify"], f"Phase {p['id']}.verify")
    for s in spec["sessions"]:
        if s.get("phase") and s["phase"] not in phase_ids:
            raise ValueError(f"Session {s['id']}.phase references unknown phase {s['phase']!r}")

    _validate_dispatch_graph(spec["sessions"])
    # The frozen parallel-group contract (§5, build-time call site) + the
    # pure-chain warning. AFTER the graph check on purpose: R1 (dependency
    # symmetry) stays enforced there for every schema version, so the contract's
    # own R1 refusal never has to fire ungated from here.
    _check_parallel_contract(spec)

    info = spec["infographic"]
    if info.get("type") not in VALID_INFOGRAPHIC_TYPES:
        raise ValueError(
            f"infographic.type must be one of {VALID_INFOGRAPHIC_TYPES}, got {info.get('type')!r}"
        )
    if info.get("type") == "custom":
        if "svg_inline" not in info and "svg_inline_file" not in info:
            raise ValueError(
                "custom infographic requires 'svg_inline' (raw SVG markup) or 'svg_inline_file' (path)"
            )
        if "groups" not in info:
            raise ValueError(
                "custom infographic requires 'groups' (list of {id, items[]} for data binding)"
            )
        for g in info["groups"]:
            for f in ["id", "items"]:
                if f not in g:
                    raise ValueError(f"custom group missing '{f}': {g}")

    # Every item the progress bar should count must actually be in a group.
    # Only fires when this infographic binds items at all: a shape with no
    # bindings drives the bar from the item cards instead, and has no
    # denominator to leave anything out of. Same rule the executor's
    # containment gate applies as a WARNING at run time — enforced HERE, where
    # it is still cheap to fix, because by run time the plan is already built.
    if (spec.get("plan_schema_version") or 0) >= INFOGRAPHIC_COVERAGE_MIN_SCHEMA:
        groups = info.get(INFOGRAPHIC_GROUP_KEYS.get(info.get("type"), ""), None)
        if isinstance(groups, list) and groups:
            placed = {i for g in groups if isinstance(g, dict) for i in (g.get("items") or [])}
            if placed:
                unplaced = [it["id"] for it in spec["items"] if it["id"] not in placed]
                if unplaced:
                    names = [str(g.get("name") or g.get("id")) for g in groups
                             if isinstance(g, dict)]
                    raise ValueError(
                        f"infographic: item(s) {', '.join(unplaced)} are in no Plan "
                        f"Achievement group, so the progress bar would count "
                        f"{len(placed & {it['id'] for it in spec['items']})} of "
                        f"{len(spec['items'])} items and report progress over a "
                        f"subset while looking like it covers the whole plan. "
                        f"Add each item to a group, or add a group for its "
                        f"category. Groups: {names}"
                    )

    # Optional halt/complete notification hooks (opt-in command run when
    # /plan-execute halts or finishes).
    for hook in ("notify_on_halt", "notify_on_complete"):
        notify = spec.get(hook)
        if notify is not None:
            if not isinstance(notify, dict):
                raise ValueError(f"{hook} must be an object with a 'command' string")
            cmd = notify.get("command")
            if not isinstance(cmd, str) or not cmd.strip():
                raise ValueError(f"{hook}.command must be a non-empty string")

    return True


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def esc(s):
    return html.escape(str(s), quote=False) if s is not None else ""


def attr(s):
    return html.escape(str(s), quote=True) if s is not None else ""


def encode_pre(s):
    """Encode text for safe embedding inside a <pre> block."""
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def today_iso():
    return date.today().isoformat()


# --------------------------------------------------------------------------
# Strip + filter chip + category fragments
# --------------------------------------------------------------------------
def gen_session_strip(sessions):
    chips = []
    for s in sessions:
        sid = s["id"]
        title_text = f"{sid.upper()} — {s['title']} · {s.get('model', 'Sonnet')}"
        chips.append(
            f'  <a href="#{attr(sid)}" class="strip-chip" data-session="{attr(sid)}" '
            f'data-status="TODO" title="{attr(title_text)}">{esc(sid.upper())}</a>'
        )
    return (
        '<nav class="session-strip" aria-label="Session navigation" data-role="auto-render">\n'
        '  <span class="strip-label">Sessions</span>\n' + "\n".join(chips) + "\n"
        "</nav>"
    )


def gen_category_filter_chips(categories):
    chips = [
        '<div class="cat-filter active" data-cat="all">All</div>',
        '<div class="cat-filter" data-cat="sessions">Session plan</div>',
    ]
    for c in categories:
        chips.append(f'<div class="cat-filter" data-cat="{attr(c["key"])}">{esc(c["label"])}</div>')
    return "\n        ".join(chips)


def gen_cats_array_js(categories):
    lines = ["[", "        { key: 'sessions', label: 'Session Plan', type: 'session' },"]
    for c in categories:
        lines.append(
            f"        {{ key: '{c['key']}', label: \"{esc(c['label'])}\", type: 'item' }},"
        )
    lines.append("      ]")
    return "\n".join(lines)


# Palette rotation for category accent colors. Skip 'sessions' which always uses session/terra.
ACCENT_VARS = [
    "var(--accent)",
    "var(--terra)",
    "var(--sonnet)",
    "var(--doing)",
    "var(--done)",
    "var(--p1)",
    "var(--p2)",
    "var(--opus)",
    "var(--deferred)",
]


def gen_category_colors_css(categories):
    lines = ['  .cat-row[data-cat="sessions"] { --cat-accent: var(--session); }']
    for i, c in enumerate(categories):
        accent = ACCENT_VARS[i % len(ACCENT_VARS)]
        lines.append(f'  .cat-row[data-cat="{c["key"]}"] {{ --cat-accent: {accent}; }}')
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Dual-layer agent-spec content blocks
# --------------------------------------------------------------------------
def gen_code_block(content_field, label):
    """Render a code/schema/mockup code excerpt as a dark code-block.
    `content_field` may be a string (raw code) or a dict with optional keys:
      - code (str): the actual code
      - lang (str): language hint shown in the header (e.g. 'json', 'sql', 'ts')
      - caption (str): optional caption line
    Returns empty string if no usable content.
    """
    if not content_field:
        return ""
    if isinstance(content_field, str):
        code, lang, caption = content_field, "", ""
    elif isinstance(content_field, dict):
        code = content_field.get("code", "")
        lang = content_field.get("lang", "")
        caption = content_field.get("caption", "")
    else:
        return ""
    if not code:
        return ""
    lang_label = lang.upper() if lang else label.upper()
    caption_html = (
        f'<figcaption style="color:var(--text-muted);font-size:11.5px;font-style:italic;margin-top:6px;">{esc(caption)}</figcaption>'
        if caption
        else ""
    )
    return f"""<div class="code-block">
      <div class="code-block-head"><span class="agent-tag">{esc(label)}</span><span>{esc(lang_label)}</span></div>
      <pre>{encode_pre(code)}</pre>
    </div>{caption_html}"""


def gen_mockup_block(mockup):
    """Render a mockup as <figure class="mockup">.
    `mockup` may be a string (raw SVG/HTML/ASCII) or a dict:
      - svg (str): inline SVG markup
      - ascii (str): ASCII / text mockup, rendered in <pre>
      - img (str): image URL
      - caption (str): optional caption
    """
    if not mockup:
        return ""
    if isinstance(mockup, str):
        # Detect SVG by leading tag
        m = mockup.lstrip()
        if m.startswith("<svg") or m.startswith("<?xml"):
            inner = mockup
            return f'<figure class="mockup"><span class="agent-tag" style="float:left">MOCKUP</span>{inner}</figure>'
        else:
            return f'<figure class="mockup"><span class="agent-tag" style="float:left">MOCKUP · ASCII</span><pre>{encode_pre(mockup)}</pre></figure>'
    if isinstance(mockup, dict):
        caption = mockup.get("caption", "")
        cap_html = f"<figcaption>{esc(caption)}</figcaption>" if caption else ""
        if mockup.get("svg"):
            return f'<figure class="mockup"><span class="agent-tag" style="float:left">MOCKUP</span>{mockup["svg"]}{cap_html}</figure>'
        if mockup.get("img"):
            alt = attr(mockup.get("alt", "mockup"))
            return f'<figure class="mockup"><span class="agent-tag" style="float:left">MOCKUP</span><img src="{attr(mockup["img"])}" alt="{alt}"/>{cap_html}</figure>'
        if mockup.get("ascii"):
            return f'<figure class="mockup"><span class="agent-tag" style="float:left">MOCKUP · ASCII</span><pre>{encode_pre(mockup["ascii"])}</pre>{cap_html}</figure>'
    return ""


def gen_agent_instructions(instr):
    """Render agent_instructions as an ordered list. Accepts list[str] or str."""
    if not instr:
        return ""
    if isinstance(instr, str):
        return f"<p>{esc(instr)}</p>"
    if isinstance(instr, list):
        lis = "\n        ".join(f"<li>{esc(x)}</li>" for x in instr)
        return f"<ol>\n        {lis}\n      </ol>"
    return ""


# --------------------------------------------------------------------------
# Item card — dual layer
# --------------------------------------------------------------------------
def gen_item_article(item, item_to_session):
    iid = item["id"]
    sid = item_to_session.get(iid)
    session_link = (
        f'<span class="session-link"><strong>Session:</strong> <a href="#{attr(sid)}">{esc(sid.upper())}</a></span>'
        if sid
        else '<span class="session-link"><strong>Session:</strong> <em style="color:var(--text-subtle);font-style:italic;">trigger-deferred</em></span>'
    )
    pri = item.get("priority", "P3")
    eff = item.get("effort", "M")
    title = item["title"]
    human_summary = item.get("human_summary", "")
    deliverable = item.get("deliverable", "")
    desc = item.get("description", "")
    why = item.get("why", "")
    owner = item.get("owner", "")
    target = item.get("target", "")
    touches = item.get("touches", "")
    updated = item.get("updated", today_iso())

    # AGENT spec body — only render if at least one technical field is present
    agent_pieces = []
    if desc and not human_summary:
        # If there's no human_summary but a description, surface description in human layer instead — see below.
        pass
    elif desc:
        agent_pieces.append(f"<h4>Detail</h4><p>{esc(desc)}</p>")
    # Prior-art decision (RS-01/RS-02, 2026-08-12) -- rendered early in the agent
    # spec since it's a decision the reader should see before dev steps. Exactly
    # one of the two shapes is present (validate_spec enforces it), but render
    # defensively either way.
    pa = item.get("prior_art")
    if pa:
        pa_lines = [f"<strong>Decision:</strong> {esc(pa.get('decision', ''))}",
                    f"<strong>Source:</strong> {esc(pa.get('source', ''))}"]
        if pa.get("note"):
            pa_lines.append(f"<strong>Note:</strong> {esc(pa['note'])}")
        agent_pieces.append("<h4>Prior art</h4><p>" + "<br>".join(pa_lines) + "</p>")
    elif item.get("research_status") == "skipped":
        agent_pieces.append(
            "<h4>Prior art — research skipped</h4><p>"
            f"{esc(item.get('research_reason', ''))}</p>"
        )
    elif item.get("research_status") == "unavailable":
        # RS-06: this heading is the one the SYSTEM stands behind — the build
        # refuses to write it unless its own configuration probe agreed that no
        # research capability was configured here. The machine's account of what
        # it looked at is stamped into manifest.json -> research_env.
        agent_pieces.append(
            "<h4>Prior art — no research tooling available (verified at build time)</h4><p>"
            + (
                esc(item["research_reason"])
                if str(item.get("research_reason") or "").strip()
                else "The build-time probe found no research MCP server and no permitted "
                "built-in web-research tool in this environment. See "
                "<code>manifest.json</code> &rarr; <code>research_env</code> for what was "
                "checked."
            )
            + "</p>"
        )
    instr = item.get("agent_instructions")
    if instr:
        agent_pieces.append("<h4>Agent instructions</h4>" + gen_agent_instructions(instr))
    sch = gen_code_block(item.get("schema"), "SCHEMA")
    if sch:
        agent_pieces.append("<h4>Schema</h4>" + sch)
    mk = gen_mockup_block(item.get("mockup"))
    if mk:
        agent_pieces.append("<h4>Mockup</h4>" + mk)
    code = gen_code_block(item.get("code"), "CODE")
    if code:
        agent_pieces.append("<h4>Code excerpt</h4>" + code)
    if touches:
        agent_pieces.append(f"<h4>Files / areas touched</h4><p><code>{esc(touches)}</code></p>")
    agent_spec = ""
    if agent_pieces:
        agent_spec = f"""
    <details class="agent-spec" data-role="agent-spec">
      <summary><span class="notes-label">Agent spec</span> <span class="agent-tag">FOR CLAUDE</span></summary>
      <div class="agent-spec-body">
      {"".join(agent_pieces)}
      </div>
    </details>"""

    # HUMAN layer: prefer human_summary, fallback to description.
    summary_block = ""
    if human_summary:
        summary_block = f'<p class="human-summary">{esc(human_summary)}</p>'
    elif desc:
        summary_block = f'<p class="human-summary">{esc(desc)}</p>'

    deliverable_block = ""
    if deliverable:
        deliverable_block = (
            f'<div class="deliverable"><strong>When done</strong>{esc(deliverable)}</div>'
        )

    why_block = f'<p class="why"><strong>Why:</strong> {esc(why)}</p>' if why else ""

    meta_parts = [session_link]
    if owner:
        meta_parts.append(f"<span><strong>Owner:</strong> {esc(owner)}</span>")
    if target:
        meta_parts.append(f"<span><strong>Target:</strong> {esc(target)}</span>")
    # Touches is moved into agent-spec when there's an agent_spec; if not, leave it visible.
    if touches and not agent_pieces:
        meta_parts.append(f"<span><strong>Touches:</strong> <code>{esc(touches)}</code></span>")

    return f'''<!-- ARTICLE:{iid}:BEGIN -->
  <article class="item" id="{attr(iid)}" data-item-id="{attr(iid)}" data-status="TODO" data-priority="{attr(pri)}" data-cat="{attr(item["category"])}" data-updated="{attr(updated)}">
    <header class="item-head">
      <span class="id-tag">{esc(iid.upper())}</span>
      <h3 class="title">{esc(title)}</h3>
      <span class="pill status-TODO">TODO</span>
      <span class="chip priority-{attr(pri)}">{esc(pri)}</span>
      <span class="chip">Effort: {esc(eff)}</span>
    </header>
    {summary_block}
    {deliverable_block}
    {why_block}
    <div class="meta-row">
      {"".join(meta_parts)}
    </div>{agent_spec}
    <details class="notes">
      <summary><span class="notes-label">Notes (0)</span></summary>
      <div class="notes-content"><span class="empty">No notes yet.</span></div>
    </details>
  </article>
<!-- ARTICLE:{iid}:END -->'''


def gen_category_section(category, items, item_to_session):
    cat_items = [it for it in items if it["category"] == category["key"]]
    if not cat_items:
        return ""
    blurb = category.get("description", "")
    item_cards = "\n".join(gen_item_article(it, item_to_session) for it in cat_items)
    return f'''<section class="category" data-cat="{attr(category["key"])}">
  <h2 class="section">{esc(category["label"])}
    {f'<span class="section-blurb">{esc(blurb)}</span>' if blurb else ""}
  </h2>
{item_cards}
</section>
'''


_HOTSPOT_ORDER = {"high": 0, "medium": 1, "low": 2}


def gen_decision_hotspots_section(items):
    """Static (build-time) 'Decision hotspots' section — the short list of
    judgment calls (schema choices, tradeoffs) most likely to be revisited,
    surfaced above the Session Plan instead of buried in execution order.

    Additive-only: renders from `tweak_likelihood`/`alternatives`, both
    optional item fields. Returns "" (section omitted) when no item uses
    tweak_likelihood, so specs that predate this feature build identically.

    Also pulls in "build despite alternatives" items (RS-01, 2026-08-12): a
    `prior_art.decision == "build"` item that ALSO carries a `note` is, by
    definition, a build call made despite a credible alternative surfacing --
    exactly the kind of judgment call this section exists to surface, whether
    or not the author separately flagged `tweak_likelihood`. Such an item
    without an explicit `tweak_likelihood` sorts as `high` (untested build
    calls are inherently the riskiest hotspot) and renders an extra line
    naming the alternative and the stated reason for building anyway.
    """
    tweak_hot = [it for it in items if it.get("tweak_likelihood") in _HOTSPOT_ORDER]
    tweak_ids = {it["id"] for it in tweak_hot}
    prior_art_hot = [
        it for it in items
        if it["id"] not in tweak_ids
        and (it.get("prior_art") or {}).get("decision") == "build"
        and (it.get("prior_art") or {}).get("note")
    ]
    hot_items = tweak_hot + prior_art_hot
    if not hot_items:
        return ""
    hot_items.sort(key=lambda it: _HOTSPOT_ORDER.get(it.get("tweak_likelihood"), 0))

    rows = []
    for it in hot_items:
        level = it.get("tweak_likelihood") or "high"
        why = it.get("why") or it.get("human_summary") or it.get("description") or ""
        pa = it.get("prior_art") or {}
        prior_art_html = ""
        if pa.get("decision") == "build" and pa.get("note"):
            prior_art_html = (
                '\n    <p class="hotspot-why"><strong>Prior art:</strong> built despite '
                f'{esc(pa.get("source", ""))} — {esc(pa["note"])}</p>'
            )
        alts = it.get("alternatives") or []
        if isinstance(alts, str):
            alts = [alts]
        alts_html = ""
        if alts:
            alt_lines = "\n".join(f"      <li>{esc(a)}</li>" for a in alts)
            alts_html = f'\n    <ul class="hotspot-alts">\n{alt_lines}\n    </ul>'
        rows.append(f'''  <li class="hotspot-item">
    <span class="hotspot-title">{esc(it.get("title", it["id"]))}</span>
    <span class="hotspot-pill {level}">{esc(level)}</span>
    {f'<p class="hotspot-why">{esc(why)}</p>' if why else ""}{prior_art_html}{alts_html}
  </li>''')

    return f'''<section class="decision-hotspots" data-cat="decision-hotspots">
  <h2 class="section">Decision hotspots
    <span class="section-blurb">{len(hot_items)} judgment call{"s" if len(hot_items) != 1 else ""} likely to be revisited</span>
  </h2>
  <ul class="hotspot-list">
{chr(10).join(rows)}
  </ul>
</section>
'''


def gen_scope_section(spec):
    """Static 'Scope boundary' section — out_of_scope (work consciously ruled
    beyond this plan; never graduates into sessions) and open_questions (the
    fog register: decisions the interview could not yet state sharply enough
    to assign to a session). Additive-only: returns "" when neither field is
    used, so specs that predate this feature build identically. Reuses the
    decision-hotspots CSS — same visual shape, no template change.
    """
    oos = spec.get("out_of_scope") or []
    oq = spec.get("open_questions") or []
    if not oos and not oq:
        return ""

    def block(title, blurb, entries, pill):
        if not entries:
            return ""
        rows = "\n".join(
            f'''  <li class="hotspot-item">
    <span class="hotspot-title">{esc(e)}</span>
    <span class="hotspot-pill {pill}">{"out of scope" if pill == "low" else "open"}</span>
  </li>'''
            for e in entries
        )
        return f'''<section class="decision-hotspots" data-cat="scope-{pill}">
  <h2 class="section">{title}
    <span class="section-blurb">{blurb}</span>
  </h2>
  <ul class="hotspot-list">
{rows}
  </ul>
</section>
'''

    return block(
        "Open decisions",
        "not yet sharp enough to assign — resolve before the sessions that consume them",
        oq,
        "medium",
    ) + block(
        "Out of scope",
        "consciously ruled beyond this plan — returns only as a fresh plan, never graduates",
        oos,
        "low",
    )


# The rendered plan change log (RP-06). Built EMPTY: `_changelog.ndjson` is the
# record, and `/plan-execute`'s writers (`article_block.set_change_log`) fill this
# list in as the plan is amended mid-run. Rendering it here rather than in the
# executor keeps the one-renderer rule — the executor only ever rewrites the rows
# between the anchors, never assembles the section.
PLAN_CHANGES_SECTION = '''<!-- PLAN-CHANGES:BEGIN -->
<section class="decision-hotspots plan-changes" data-cat="plan-changes">
  <h2 class="section">Plan changes
    <span class="section-blurb">amendments made while this plan was running — oldest first</span>
  </h2>
  <ul class="hotspot-list" data-role="plan-changes"><li class="hotspot-item"><span class="hotspot-title">No plan changes recorded — this plan has run as it was built.</span></li></ul>
</section>
<!-- PLAN-CHANGES:END -->'''


# --------------------------------------------------------------------------
# Per-session execution-bundle emitters
# --------------------------------------------------------------------------
def gen_post_session_section(post_session):
    """Render a 'Post-session actions' section so the subagent KNOWS what will
    fire after it returns, and leaves the working tree appropriately staged.
    The subagent does NOT run these itself — /plan-execute does, after the
    closeout is applied."""
    if not post_session:
        return ""
    lines = ["", "## Post-session actions (run by /plan-execute, NOT by you)", ""]
    git = post_session.get("git", "none")
    if git != "none":
        lines.append(f"- **git:** `{git}` — when you finish, leave the working tree in a "
                     "coherent, committable state (no half-edits, no debug scratch).")
    gates = post_session.get("pre_deploy_gates") or []
    if gates:
        lines.append(f"- **pre-deploy gates:** {', '.join(gates)} (must pass before any deploy).")
    if post_session.get("deploy_argv"):
        lines.append("- **deploy:** a project-local command vector will run after gates pass.")
    elif post_session.get("deploy", "none") not in (None, "none"):
        lines.append(f"- **deploy:** target `{post_session['deploy']}` will run after gates pass.")
    if post_session.get("rollback_hint"):
        lines.append(f"- **rollback hint (if this deploy is later invalidated):** "
                     f"{post_session['rollback_hint']}")
    if post_session.get("skip_if_partial"):
        lines.append("- These actions are SKIPPED unless your result is `DONE`.")
    lines.append("")
    lines.append("Because these are plan-declared, they are pre-authorized and fire without "
                 "re-prompting. Make sure your final state is shippable.")
    return "\n".join(lines)


def gen_verify_section(verify):
    """Render a 'Verification gates' section so the subagent knows its claimed-
    DONE work will be checked BEFORE it counts as done — and that a gate failure
    re-dispatches it with the failure attached. The subagent does NOT run these;
    /plan-execute does, at the apply boundary."""
    has_gates = bool(verify and verify.get("gates"))
    require_evidence = bool(verify and verify.get("require_evidence"))
    checks = (verify or {}).get("checks") or []
    if not has_gates and not require_evidence and not checks:
        return ""
    on_fail = verify.get("on_fail", "rework")
    mr = verify.get("max_rework", 1)
    lines = ["", "## Verification gates (run by /plan-execute, NOT by you)", ""]
    if has_gates:
        gates = ", ".join(f"`{g}`" for g in verify["gates"])
        lines.append(f"After you return `DONE`, these gates MUST pass before the session counts as "
                     f"done: {gates}.")
    if require_evidence:
        lines.append("This session **requires evidence**: your closeout MUST include an `evidence` "
                     "array of paths to artifacts that PROVE the work engaged — e.g. an eval/metrics "
                     "JSON, the saved output of a `grep -c <log-event>` (count > 0), a screenshot, or "
                     "a command transcript. Write those files to disk (commit-safe locations), then "
                     "list their paths. `DONE` is REFUSED until every listed path exists and is "
                     "non-empty — a metric swing without log/grep proof of engagement does not count.")
    if checks:
        lines.append("**Evidence contracts** — produce EVERY one of these named artifacts; the runner "
                     "asserts each path exists and is non-empty before `DONE` is granted (list them in "
                     "your closeout `evidence` array):")
        for c in checks:
            a = f" — {c['assert']}" if c.get("assert") else ""
            lines.append(f"  - `{c['evidence_path']}` ({c['name']}){a}")
    if on_fail == "rework":
        lines.append(f"- If a gate (or the evidence check) fails, you are re-dispatched with the "
                     f"failure attached (up to {mr} rework attempt{'s' if mr != 1 else ''}), then the "
                     f"session halts.")
    else:
        lines.append("- If a gate (or the evidence check) fails, the session halts immediately for "
                     "human investigation.")
    lines.append("- Do NOT claim `DONE` unless you believe these checks will pass — a self-reported "
                 "`DONE` that fails is the exact failure this catches. If you cannot make them pass, "
                 "return `PARTIAL` or `BLOCKED` with the reason in `notes`.")
    lines.append("")
    return "\n".join(lines)


def gen_session_prompt_md(session, plan_dir_name, items_by_id, post_session=None, verify=None):
    """Render the invocation prompt the orchestrator hands to the subagent.

    The prompt body comes from session['prompt']. We wrap it with metadata
    (which items are in scope, where the per-session context bundle lives), a
    Post-session actions notice (so the subagent leaves the tree staged), and a
    strict closeout contract. The subagent never edits PLAN.html — it returns a
    structured closeout block.
    """
    sid = session["id"]
    title = session.get("title", sid)
    items = session.get("items", [])
    body = session.get("prompt", "").strip()
    if not body:
        body = (
            f"Execute the work for session {sid.upper()} — {title}. "
            "Use the agent-spec content in the context bundle for details."
        )
    item_lines = []
    for iid in items:
        it = items_by_id.get(iid, {})
        t = it.get("title", iid)
        s = it.get("human_summary") or it.get("description") or ""
        item_lines.append(f"- **{iid.upper()}** — {t}" + (f": {s}" if s else ""))
    items_block = "\n".join(item_lines) if item_lines else "_(no items linked)_"

    closeout_example = {
        "session": sid,
        "result": "DONE",
        "items_completed": items,
        "items_blocked": [],
        "notes": {iid: "one-line outcome" for iid in items} | {sid: "one-line session outcome"},
        "dispatch_next": True,
        "human_checkpoint_reason": None,
    }
    check_paths = [c["evidence_path"] for c in (verify or {}).get("checks", []) if c.get("evidence_path")]
    if verify and (verify.get("require_evidence") or check_paths):
        closeout_example["evidence"] = check_paths or [
            "docs/operations/<this-session>-evidence.md",
            "_evidence/<this-session>/grep-count.txt",
        ]
    closeout_json = json.dumps(closeout_example, indent=2)
    verify_block = gen_verify_section(verify)
    post_session_block = gen_post_session_section(post_session)

    return f"""# Session {sid.upper()} — {title}

You are executing **SESSION {sid.upper()}** as a subagent dispatched by `/plan-execute`.

## Plan location
The plan dashboard is at the sibling `PLAN.html` in this directory: `{plan_dir_name}/`.
You do **NOT** edit PLAN.html — the orchestrator handles all state mutation.

## Items in scope
{items_block}

## Context bundle
For schemas, mockups, code excerpts, and agent instructions per item, read:
`sessions/{sid}.context.md` (sibling of this file).

## Work
{body}
{verify_block}
{post_session_block}

## Implementation notes

Maintain a running implementation-notes log as you work. As much as a spec covers, there are always ambiguities and unknown unknowns — this log is your out to make a reasonable call and keep the human in the loop rather than stall or guess silently. Capture:
- Design decisions where the spec was ambiguous
- Intentional deviations from the item/spec, and why
- Tradeoffs considered and the reasoning for the choice made
- Open questions to confirm with the human later

Fold anything material from this log into the closeout's `human_summary`/`notes` fields below — the closeout is what the plan's history actually retains.

## If you hit an edge case not covered by the plan

Pick the conservative option, keep going, and record it — don't stall waiting for a human unless you're genuinely blocked (see abort conditions above, if this session has them). Record each deviation as one short line under an OPTIONAL `deviations` array in your closeout JSON (alongside `notes`) — this is how plan-vs-reality drift reaches the next session instead of evaporating. Omit the field entirely if you didn't deviate.

## If you fail twice at the SAME root cause — the stuck protocol

**Trigger:** two consecutive failed attempts whose error has the SAME root-cause signature (same error class and same message locus, ignoring paths, line numbers and whitespace). A *different* error on the second attempt is progress — keep going, the protocol does not fire. Repeating the same one means your model of the problem is wrong, and a third blind retry will not fix that.

This is not advisory. The orchestrator records a normalised failure signature and a consecutive counter for this session in `run_state.json` on every rework cycle; when the same signature repeats, the research pass below is injected into the feedback file your re-dispatch reads (`_verify_state/<session>.feedback.md`) and a `stuck_protocol_armed` event is written to `run.ndjson`.

**Action, in order:**
1. **One time-boxed research pass — 10 minutes, hard stop.** Look OUTSIDE this repository for this exact error class + locus. Tiered, first available wins, degrade gracefully: **Perplexity** (`mcp__perplexity-ask__perplexity_ask`) → **Exa** (`mcp__exa__web_search_exa`) → **Ref** (`mcp__ref__ref_search_documentation`). If a tier's MCP is not configured, drop to the next and say so; if none is available, the built-in web search counts. A pass that finds nothing is reported as "nothing found" — never silently skipped.
2. **Then exactly one of two things:** apply the fix, citing what the research changed about your diagnosis — or close `BLOCKED` carrying a `decision_brief` (below). Never silence the check, weaken the assertion, or retry blind.

This research pass is the rung between "retry" and "raise the model" on the standing escalation ladder. Do not jump past it to a model escalation.

## Closeout — return EXACTLY this block at the END of your final message

<plan-execute-closeout>
{closeout_json}
</plan-execute-closeout>

Closeout contract:
- `result`: one of `DONE` (all items in scope completed), `PARTIAL` (some items completed, session needs continuation), or `BLOCKED` (cannot proceed; explain in notes)
- `items_completed`: list of item IDs in scope that are now finished
- `items_blocked`: list of item IDs in scope that cannot be completed; explain each in `notes`
- `notes`: object mapping each item ID (and optionally the session ID) to a one-line outcome
- `dispatch_next`: hint for orchestrator; true normally, false if you set a human checkpoint
- `human_checkpoint_reason`: null normally. Set it ONLY when a human must decide something specific — and then state the decision itself in plain language (what happened, what the options are, what you recommend), never just "please review". If you cannot name a decision, leave it null.
- `evidence` (only if this session requires evidence — see "Verification gates" above): a list of paths to artifacts that prove the work engaged. Each path must exist and be non-empty or `DONE` is refused.
- `deviations` (optional): array of strings, one per edge case where you deviated from the plan (conservative option chosen + why, one line each). Omit entirely if you didn't deviate — routine sessions should not generate noise.
- `decision_brief`: **REQUIRED when `result` is `BLOCKED`** (recommended whenever you set `human_checkpoint_reason`). "Stuck, please advise" is not a report — a blocked closeout without this is REFUSED and reworks like any malformed closeout. Shape: `{{"attempts": ["what you tried", …], "findings": [{{"source": "…", "takeaway": "…"}}, …], "options": ["…"] (1-3), "recommendation": "…"}}`. `findings` carries what the stuck-protocol research pass turned up, with its sources — if it genuinely found nothing, say so as a finding (`{{"source": "none", "takeaway": "searched X and Y, nothing found"}}`). At most three options, and name the one you'd pick. The brief is rendered into `HALT_NOTICE.txt`, so the operator sees the options and your recommendation without opening any file.

The closeout block must be the LAST non-whitespace content of your message. Do not embed it inside markdown code fences (no triple-backtick wrap). HTML-escaping of note text is handled by the orchestrator.
"""


def gen_session_context_md(session, items_by_id):
    """Render the per-session technical-spec bundle (schemas, mockups, code,
    instructions) for items in scope. Keeps the subagent's reading load to
    ~5-15KB instead of the full PLAN.html (~250KB)."""
    sid = session["id"]
    sections = [f"# {sid.upper()} — context bundle\n"]
    for iid in session.get("items", []):
        it = items_by_id.get(iid, {})
        sections.append(f"## {iid.upper()} — {it.get('title', iid)}\n")
        if it.get("human_summary"):
            sections.append(f"**Summary:** {it['human_summary']}\n")
        if it.get("deliverable"):
            sections.append(f"**Deliverable:** {it['deliverable']}\n")
        if it.get("description"):
            sections.append(f"**Detail:** {it['description']}\n")
        instr = it.get("agent_instructions")
        if instr:
            sections.append("### Agent instructions")
            if isinstance(instr, list):
                for line in instr:
                    sections.append(f"- {line}")
            else:
                sections.append(str(instr))
            sections.append("")
        sch = it.get("schema")
        if sch:
            sections.append("### Schema")
            if isinstance(sch, dict):
                lang = sch.get("lang", "")
                code = sch.get("code", "")
            else:
                lang, code = "", str(sch)
            sections.append(f"```{lang}\n{code}\n```\n")
        mk = it.get("mockup")
        if mk:
            sections.append("### Mockup")
            if isinstance(mk, dict):
                if mk.get("svg"):
                    sections.append(mk["svg"])
                elif mk.get("ascii"):
                    sections.append(f"```\n{mk['ascii']}\n```")
                elif mk.get("img"):
                    sections.append(f"![mockup]({mk['img']})")
            else:
                sections.append(f"```\n{mk}\n```")
            sections.append("")
        code = it.get("code")
        if code:
            sections.append("### Code excerpt")
            if isinstance(code, dict):
                lang, body = code.get("lang", ""), code.get("code", "")
            else:
                lang, body = "", str(code)
            sections.append(f"```{lang}\n{body}\n```\n")
        if it.get("touches"):
            sections.append(f"**Touches:** `{it['touches']}`\n")
    return "\n".join(sections)


PLAN_SCHEMA_VERSION = 6
"""The stamp a freshly built plan carries, and the ONE constant every
version-gated executor feature keys off (`closeout_pipeline.plan_impact`,
`closeout_pipeline.decision_brief`). `/plan-execute` accepts >= 2; the extra
features switch on at 3/4/5, so plans built before each bump keep their exact
old behaviour instead of gaining new refusals retroactively.

History: 2 — the executable-plan schema (2026-07). 3 — `plan_impact`
closeouts + the REPLAN checkpoint + the rendered plan change log (2026-08-12).
4 — the mandatory per-item prior-art decision (`prior_art` or
`research_status: "skipped"`, RS-01/RS-02, 2026-08-12): validate_spec() refuses
a build where an item carries neither, but ONLY on a spec that opts in — see
`PRIOR_ART_MIN_SCHEMA` immediately below. (First shipped unconditional; broke
11/15 plan-execute test shards plus the live mutation engine within the same
session, because validate_spec() is shared plumbing dozens of unrelated
callers reuse to materialize a throwaway spec — reworked same-day to gate on
an explicit opt-in instead, exactly like every other v3+/v4+ feature here.)
5 — the BLOCKED `decision_brief` (RS-04, 2026-08-12): a closeout with
`result: "BLOCKED"` must carry attempts / sourced findings / at most three
options / a recommendation, or the closeout is refused. Gated by
`closeout_pipeline.DECISION_BRIEF_MIN_SCHEMA` on the MANIFEST's stamp, so every
plan already on disk (all at 3 or 4) keeps the old contract — measured against
all ten live plans in `_plans/` before the bump landed, none newly refused.
6 — the UPWARD ESCALATION climb (ESC-02, 2026-08-13): a session whose gate keeps
failing at the same root cause is re-dispatched one rung UP the SSOT's ladder
instead of on the same rung forever. Gated by
`plan-execute/scripts/escalation.ESCALATION_MIN_SCHEMA` on the MANIFEST's stamp,
so every plan already on disk dispatches byte-identically — proved by a FROZEN v5
fixture manifest (`plan-execute/fixtures/v5-claude-lane/`) whose `begin` output is
asserted unchanged, not by regenerating historical plans (a read-only probe found
only 1 of 26 plans on disk regenerates identically TODAY, before any change, so a
regenerate-and-compare gate would have been false on arrival). A session opts out
with `escalation: false`. The same version carries the optional
`routing_experiment: {kind, proposal_id}` session tag, validated here and
propagated into the manifest for the outcome ledger to read back.
"""

INFOGRAPHIC_COVERAGE_MIN_SCHEMA = 5
"""The spec's OWN `plan_schema_version` at which validate_spec() refuses a plan
whose Plan-Achievement infographic binds items to groups but leaves some item
out of every group. Same opt-in shape as `PRIOR_ART_MIN_SCHEMA` below, for the
same reason.

WHY THE CHECK EXISTS: the progress bar counts through the group list, so an
unplaced item is not merely undrawn — it is absent from the DENOMINATOR. The
bar then reports progress over a subset while looking like it reports the whole
plan. Measured on the 2026-08-12 framework-upgrade plan: five items sat in two
categories that had no group, and the bar counted 18 of 25 items. It read 100%
complete while five items were still unfinished, and `plan-execute`'s
containment gate could only WARN, because the plan already had the defect when
each mutation ran.

WHY IT IS GATED, not unconditional: measured 2026-08-13 across all 25 `spec.json`
files under `_plans/` — 4 of them (gearbox-dyno-2026-07-26,
kun-chen-harness-adaptations-2026-07-05, dual-harness-plan-skills-2026-07-28 and
the framework-upgrade plan itself) leave at least one item unplaced and would be
refused outright. None carries a `plan_schema_version` key, so all four keep
building exactly as before. This is the s09 lesson applied at authoring time
rather than after the rework.
"""

PRIOR_ART_MIN_SCHEMA = 4
"""The spec's OWN top-level `plan_schema_version`, if present, at which
validate_spec() enforces the mandatory per-item prior-art decision (see the
item loop in `validate_spec`). Below it — including the overwhelmingly common
case of no `plan_schema_version` key at all — the requirement does not fire;
that spec validates exactly as it did before this feature existed.

This is deliberately NOT the same knob as `PLAN_SCHEMA_VERSION` above (which is
an OUTPUT stamp `gen_manifest` assigns to a built plan's manifest.json, read
back later by /plan-execute) — there has never been an input-side notion of
"this spec's version" until this constant. `validate_spec(spec)` takes no
version argument; it reads `spec.get("plan_schema_version")` directly, so
every existing call site (build(), the --dry-run-shipping preview, the
mutation engine's re-validation of a live plan's spec.json, and every
unrelated test suite across the repo that calls validate_spec()/build() to
materialize a fixture) is unaffected with ZERO code changes on their end.
plan-builder's own interview flow (SKILL.md) is expected to stamp
`"plan_schema_version": 4` on every NEW spec it compiles — that is the one
deliberate way a spec opts in. Mirrors `closeout_pipeline.PLAN_IMPACT_MIN_SCHEMA`
one layer earlier (input-side instead of runtime-manifest-side): same
"unset/unknown = pre-bump = unenforced" conservative default.
"""


def gen_manifest(spec, plan_schema_version=PLAN_SCHEMA_VERSION):
    """Build the IMMUTABLE dispatch graph. Mutable runtime state lives in
    run_state.json — never write halt/lock/etc. here."""
    phases_by_id = {p["id"]: p for p in spec.get("phases", []) if "id" in p}
    sessions_by_id = {s["id"]: s for s in spec["sessions"]}
    sessions = []
    for s in spec["sessions"]:
        d = s.get("dispatch") or {}
        sess = {
            "id": s["id"],
            "title": s.get("title", s["id"]),
            "items": s.get("items", []),
            "model": s.get("model", "Sonnet"),
            "effort": s.get("effort", ""),
            "reasoning": s.get("reasoning", ""),
            # Structured second-model-review gate (Layer 1, 2026-07-10). Declared
            # codex_peer triggers for this session; the §4.0 model-lint turns a
            # non-empty list without an adversarial-review gate into a 🔴. Validated
            # in validate_peer_triggers() below (called before manifest write).
            "peer_triggers": s.get("peer_triggers", []) or [],
            # Routing task_class (s06, EXE-01 — optional, additive). Consumed by
            # /plan-execute's executor_policy enforcement: under a codex-focused
            # dial (active_provider: openai) only a session whose task_class is
            # opted into the SSOT's executor_policy.executor_for may auto-dispatch
            # to Codex; unset/unknown FAILS CLOSED to Claude execution. `linchpin`
            # is permanently barred from unsupervised Codex execution.
            "task_class": (s.get("task_class") or "").strip().lower(),
            # Marks THE closing plan-level acceptance review (2026-07-26). Read by
            # /plan-execute (surface its verdict at `complete` instead of a bare
            # "success") and by plan-harden's `acceptance-review-missing` lint. At
            # most one session per plan — validated in validate_spec().
            "acceptance_review": bool(s.get("acceptance_review", False)),
            # ESC-02 opt-OUT + the routing-canary tag. BOTH are emitted ONLY when
            # the spec declares them, so a manifest for a spec that carries
            # neither is byte-identical to the one the previous builder wrote.
            **({"escalation": s["escalation"]} if s.get("escalation") is False else {}),
            **({"routing_experiment": dict(s["routing_experiment"])}
               if isinstance(s.get("routing_experiment"), dict) else {}),
            "prompt_file": f"sessions/{s['id']}.prompt.md",
            "context_file": f"sessions/{s['id']}.context.md",
            "dispatch": {
                "subagent_type": d.get("subagent_type"),
                "parallel_group": d.get("parallel_group"),
                # Parallel-group contract fields (S06 / PL-02). Emitted ONLY when
                # declared, so an existing plan's manifest is byte-identical:
                # `isolation` = the group's isolation mechanism ("worktree", i.e.
                # orchestrator-managed `git worktree add` — the only legal value,
                # M3); `integrates_group` = this session is that group's declared
                # integration session (§3).
                **({"isolation": d["isolation"]} if d.get("isolation") else {}),
                **({"integrates_group": d["integrates_group"]}
                   if d.get("integrates_group") else {}),
                "depends_on": d.get("depends_on", []) or [],
                "depends_on_policy": d.get("depends_on_policy", "all"),
                "requires_human_checkpoint": bool(d.get("requires_human_checkpoint", False)),
                # The decision brief a human sees when this gate parks the plan
                # (reason / decision / options). Required whenever
                # requires_human_checkpoint is true — validated above.
                "checkpoint": d.get("checkpoint"),
                # OR-03 per-gate autonomy policy for the AWAITS_REVIEW-ack gate:
                # "block" (default) | "notify-and-continue". Only honored by
                # /plan-execute's gate_policy for the allowlisted rubber-stamp gate
                # TYPE, and NEVER when requires_human_checkpoint / *irreversible.
                "checkpoint_policy": d.get("checkpoint_policy"),
                "guards_irreversible": bool(d.get("guards_irreversible", False)),
                # Declared shell capabilities for a `codex exec` dispatch. Omitted
                # from the manifest entirely when unset, so an existing plan's
                # manifest — and the command run.py builds from it — is unchanged.
                **({"codex_shell": d["codex_shell"]} if d.get("codex_shell") else {}),
                "max_retries": int(d.get("max_retries", 0) or 0),
                "model_fallbacks": d.get("model_fallbacks", []) or [],
                "reasoning_fallbacks": d.get("reasoning_fallbacks", []) or [],
            },
        }
        # Resolve the phase_closer defaults + per-session override at BUILD time
        # so /plan-execute never re-merges; bind deploy authorization to inputs.
        ps = resolve_post_session(s, phases_by_id)
        if ps is not None:
            if _deploy_present(ps):
                ps = dict(ps)
                ps["deploy_auth_digest"] = deploy_auth_digest(s["id"], sessions_by_id)
            sess["post_session"] = ps
        # Resolve verify gates (phase default + per-session override) at build
        # time so /plan-execute reads the merged block and never re-merges.
        vb = resolve_verify(s, phases_by_id)
        if vb is not None:
            sess["verify"] = vb
        sessions.append(sess)
    manifest = {
        "plan_schema_version": plan_schema_version,
        "title": spec["title"],
        "created": today_iso(),
        "items": [
            {
                "id": it["id"],
                "title": it.get("title", it["id"]),
                "category": it["category"],
                # `touches` — what this item WRITES. Carried into the manifest
                # because the parallel-group contract's M2 (dependency/lockfile
                # ban) and M5 (overlapping writes) are BOTH computed from it at
                # dispatch time, and M2a fails closed without it. Measured at
                # contract-freeze time: 307 manifest items in this repo, 0 with
                # `touches`, against 177 spec items that declared one — i.e. both
                # tree-protecting rules were keyed on a field the builder never
                # wrote. Emitted only when declared, so a manifest for a spec
                # without it is unchanged. See parallel-group-contract.md §2 M2a.
                **({"touches": it["touches"]} if str(it.get("touches") or "").strip() else {}),
            }
            for it in spec["items"]
        ],
        "sessions": sessions,
    }
    # Scope boundary + fog register (2026-08-01). Structured so plan-harden's
    # decision-debt lint and the closing acceptance review read them from the
    # manifest instead of parsing PLAN.html. Omitted when unused.
    for f in ("out_of_scope", "open_questions"):
        v = spec.get(f)
        if isinstance(v, list) and v:
            manifest[f] = list(v)
    # Why this plan is a chain (S07 / PL-03). Carried so plan-harden's
    # parallelization lint reads the author's stated reason from the manifest
    # instead of re-flagging a chain the author already justified.
    if str(spec.get("serial_reason") or "").strip():
        manifest["serial_reason"] = spec["serial_reason"]
    # The build-time research-environment record (RS-06). READ BACK OFF THE SPEC,
    # never re-probed here — gen_manifest must stay a pure function of spec.json,
    # or `plan_mutate`'s "manifest.json == gen_manifest(spec.json)" consistency
    # check would fail the moment a plan is regenerated on a different machine.
    # Absent from a spec that makes no research-skip claim, so every manifest
    # already on disk regenerates byte-identically.
    if isinstance(spec.get("research_env"), dict) and spec["research_env"]:
        manifest["research_env"] = spec["research_env"]
    # Carry the opt-in halt/complete-notify hooks as IMMUTABLE dispatch config
    # (not runtime state).
    for hook in ("notify_on_halt", "notify_on_complete"):
        notify = spec.get(hook)
        if (
            isinstance(notify, dict)
            and isinstance(notify.get("command"), str)
            and notify["command"].strip()
        ):
            manifest[hook] = {"command": notify["command"]}
    return manifest


def init_run_state():
    """Initial run_state.json contents. Mutated by /plan-execute."""
    return {
        "schema_version": 2,
        "halt": {"set": False, "reason": None, "by_session": None, "at": None},
        "last_batch": None,
        "lock_token": None,
    }


GITIGNORE_LINES = [
    "_plans/*/.lock",
    "_plans/*/_closeouts/",
    "_plans/*/run.ndjson",
    "_plans/*/run_state.json",
    "_plans/*/HALT_NOTICE.txt",
]


def amend_gitignore(project_root):
    """Add plan-execute runtime-state lines to project's .gitignore (idempotent)."""
    gi = Path(project_root) / ".gitignore"
    existing = gi.read_text() if gi.exists() else ""
    needed = [ln for ln in GITIGNORE_LINES if ln not in existing]
    if not needed:
        return None
    new_body = (
        existing.rstrip()
        + (
            "\n\n# plan-execute runtime state (auto-added by plan-builder)\n"
            + "\n".join(needed)
            + "\n"
        )
        if existing
        else "# plan-execute runtime state (auto-added by plan-builder)\n"
        + "\n".join(needed)
        + "\n"
    )
    gi.write_text(new_body)
    return gi


# --------------------------------------------------------------------------
# Session card — dual layer with prominent action bar
# --------------------------------------------------------------------------
def session_purpose_fallback(session_title, item_ids, items_by_id):
    """Build a substantive plain-English purpose paragraph when no human_summary
    is provided on the session.

    Strategy: pull each item's `human_summary` (preferred) or `description`
    (fallback). Concatenate into a paragraph that actually explains WHAT the
    session does, not just lists titles. If items carry no narrative content,
    fall back to a title-list — better than nothing.
    """
    items = [items_by_id[iid] for iid in item_ids if iid in items_by_id]
    if not items:
        return f"Pick up the work in {session_title}."

    # Per-item rich text: prefer human_summary, then description, last resort title.
    rich = []
    has_narrative = False
    for it in items:
        if it.get("human_summary"):
            rich.append(it["human_summary"].strip())
            has_narrative = True
        elif it.get("description"):
            # Take first sentence to keep length sane.
            d = it["description"].strip()
            first = re.split(r"(?<=[.!?])\s+", d, maxsplit=1)[0]
            rich.append(first if first.endswith((".", "!", "?")) else first + ".")
            has_narrative = True
        else:
            rich.append(f'"{it["title"]}".')

    if has_narrative:
        # Join the per-item narrative into one paragraph. The serif rendering at
        # 26px will give it the right editorial weight.
        return " ".join(rich)

    # Pure title-list fallback when no item carries narrative content.
    titles = [it["title"] for it in items]
    if len(titles) == 1:
        return f'Bring "{titles[0]}" across the finish line.'
    if len(titles) == 2:
        return f'Cover "{titles[0]}" and "{titles[1]}".'
    head = ", ".join(f'"{t}"' for t in titles[:2])
    return f"Cover {len(titles)} items: {head}, and {len(titles) - 2} more."


def gen_session_article(session, sessions_list, idx, items_by_id=None):
    sid = session["id"]
    total = len(sessions_list)
    model = session.get("model", "Sonnet")
    model_lower = model.lower()
    effort = session.get("effort", "")
    reasoning = session.get("reasoning", "")
    reasoning_lower = reasoning.lower()
    why_model = session.get("why_model", "")
    items = session.get("items", [])
    human_summary = session.get("human_summary", "")
    deliverable = session.get("deliverable", "")
    agent_instr = session.get("agent_instructions")
    items_by_id = items_by_id or {}

    next_session = None
    if idx + 1 < total:
        next_session = sessions_list[idx + 1]["id"]

    item_links = " · ".join(f'<a href="#{i}">{i.upper()}</a>' for i in items)
    updated = session.get("updated", today_iso())

    dispatch = session.get("dispatch") or {}
    dispatch_chips = []
    sa = dispatch.get("subagent_type")
    pg = dispatch.get("parallel_group")
    deps = dispatch.get("depends_on") or []
    if sa:
        dispatch_chips.append(f'<span class="chip">agent: <code>{esc(sa)}</code></span>')
    if pg:
        iso = dispatch.get("isolation")
        dispatch_chips.append(
            f'<span class="chip">parallel: <code>{esc(pg)}</code>'
            + (f' <em>({esc(iso)}-isolated)</em>' if iso else "")
            + "</span>"
        )
    if dispatch.get("integrates_group"):
        dispatch_chips.append(
            '<span class="chip">integrates: '
            f'<code>{esc(dispatch["integrates_group"])}</code></span>'
        )
    if deps:
        dispatch_chips.append(
            '<span class="chip">depends on '
            + " · ".join(f'<a href="#{d}">{d.upper()}</a>' for d in deps)
            + "</span>"
        )
    if dispatch.get("requires_human_checkpoint"):
        dispatch_chips.append('<span class="chip">⏸ human checkpoint</span>')
    dispatch_row = (
        f'<p class="session-dispatch"><strong>Dispatch:</strong> {" ".join(dispatch_chips)}</p>'
        if dispatch_chips
        else ""
    )

    # ALWAYS render the human-purpose block. Use human_summary if provided;
    # otherwise auto-build a plain-English sentence from the items in scope.
    purpose_text = human_summary or session_purpose_fallback(
        session.get("title", sid), items, items_by_id
    )
    eyebrow_label = "What we’re doing" if human_summary else "What this session covers"
    summary_block = (
        f'<div class="purpose-eyebrow">{eyebrow_label}</div>'
        f'<p class="session-summary">{esc(purpose_text)}</p>'
    )

    deliverable_block = (
        f'<div class="deliverable"><strong>By end of session you’ll have</strong>{esc(deliverable)}</div>'
        if deliverable
        else ""
    )
    why_model_block = (
        f'<p class="model-rec"><strong>Why {esc(model)}:</strong> {esc(why_model)}</p>'
        if why_model
        else ""
    )

    step = f"Session {idx + 1} of {total}"

    next_btn = ""
    if next_session:
        next_btn = f'<a href="#{next_session}" class="btn-secondary" title="Jump to {next_session.upper()}">Open next {esc(next_session.upper())} →</a>'

    actions_html = f"""    <div class="session-actions">
      <span class="actions-label">Run this session</span>
      <code class="invoke-cmd">/plan-execute &lt;plan-dir&gt; --session {esc(sid)}</code>
      <a href="#{sid}" class="btn-secondary">Jump to card</a>
      {next_btn}
      <span style="color:var(--text-subtle);font-size:14.5px;margin-left:auto;">orchestrator dispatches subagent →</span>
    </div>"""

    agent_pieces = []
    if agent_instr:
        agent_pieces.append("<h4>Agent instructions</h4>" + gen_agent_instructions(agent_instr))
    agent_pieces.append(
        f"""<h4>Per-session execution bundle</h4>
        <p>The orchestrator reads these files when dispatching {esc(sid.upper())}:</p>
        <ul>
          <li><code>sessions/{esc(sid)}.prompt.md</code> — invocation prompt</li>
          <li><code>sessions/{esc(sid)}.context.md</code> — per-item agent-spec bundle (schemas, mockups, code excerpts)</li>
        </ul>
        <p>The subagent returns a <code>&lt;plan-execute-closeout&gt;</code> JSON block — see operating manual at top of file.</p>"""
    )

    agent_spec = f"""
    <details class="agent-spec" data-role="agent-spec">
      <summary><span class="notes-label">Agent spec · execution bundle</span> <span class="agent-tag">FOR CLAUDE</span></summary>
      <div class="agent-spec-body">
      {"".join(agent_pieces)}
      </div>
    </details>"""

    return f'''<!-- ARTICLE:{sid}:BEGIN -->
  <article class="session" id="{attr(sid)}" data-session-id="{attr(sid)}" data-status="TODO" data-updated="{attr(updated)}" data-shipping="none">
    <div class="session-step">{esc(step)}</div>
    <header class="item-head">
      <span class="id-tag id-session">{esc(sid.upper())}</span>
      <h3 class="title">{esc(session["title"])}</h3>
      <span class="pill status-TODO">TODO</span>
      <span class="pill ship-badge status-TODO" data-role="ship-badge" title="Post-session shipping status (committed / pushed / PR-open / deployed / SHIP-FAILED)">—</span>
      <span class="chip model-{attr(_model_css_class(model_lower))}">{esc(model)}</span>
      {f'<span class="chip chip-effort">{esc(effort)}</span>' if effort else ""}
      {f'<span class="chip chip-reasoning reasoning-{attr(reasoning_lower)}" title="Reasoning effort">◐ {esc(reasoning)}</span>' if reasoning else ""}
    </header>
    {summary_block}
    {deliverable_block}
    <p class="session-items"><strong>Items in scope:</strong> {item_links}</p>
    {dispatch_row}
    {why_model_block}
{actions_html}
{agent_spec}
    <details class="notes"><summary><span class="notes-label">Notes (0)</span></summary><div class="notes-content"><span class="empty">No notes yet.</span></div></details>
  </article>
<!-- ARTICLE:{sid}:END -->
'''


def gen_sessions_block(sessions, items_by_id=None):
    return "\n".join(
        gen_session_article(s, sessions, i, items_by_id) for i, s in enumerate(sessions)
    )


# --------------------------------------------------------------------------
# Plan Achievement infographics (5 templates) — unchanged from previous version
# --------------------------------------------------------------------------
def infographic_phase_journey_data_js(info):
    phases = info.get("phases", [])
    anchor_now = info.get("anchor_now", {"name": "Now", "tagline": ""})
    anchor_goal = info.get("anchor_goal", {"name": "Goal", "tagline": ""})
    phases_js = json.dumps(phases, indent=6).replace("\n", "\n    ")
    return f"""    const INFOGRAPHIC_TYPE = 'phase-journey';
    const PHASES = {phases_js};
    const ANCHOR_NOW  = {json.dumps(anchor_now)};
    const ANCHOR_GOAL = {json.dumps(anchor_goal)};
"""


def infographic_maturity_ladder_data_js(info):
    levels = info.get("levels", [])
    anchor_bottom = info.get("anchor_bottom", {"name": "Now", "tagline": ""})
    anchor_top = info.get("anchor_top", {"name": "Goal", "tagline": ""})
    return f"""    const INFOGRAPHIC_TYPE = 'maturity-ladder';
    const LEVELS = {json.dumps(levels, indent=6).replace(chr(10), chr(10) + "    ")};
    const ANCHOR_BOTTOM = {json.dumps(anchor_bottom)};
    const ANCHOR_TOP    = {json.dumps(anchor_top)};
"""


def infographic_hub_spoke_data_js(info):
    hub = info.get("hub", {"name": "Goal", "tagline": ""})
    spokes = info.get("spokes", [])
    return f"""    const INFOGRAPHIC_TYPE = 'hub-spoke';
    const HUB = {json.dumps(hub)};
    const SPOKES = {json.dumps(spokes, indent=6).replace(chr(10), chr(10) + "    ")};
"""


def infographic_before_after_data_js(info):
    before = info.get("before", {"name": "Before", "bullets": []})
    after = info.get("after", {"name": "After", "bullets": []})
    workstreams = info.get("workstreams", [])
    return f"""    const INFOGRAPHIC_TYPE = 'before-after';
    const BEFORE = {json.dumps(before)};
    const AFTER = {json.dumps(after)};
    const WORKSTREAMS = {json.dumps(workstreams, indent=6).replace(chr(10), chr(10) + "    ")};
"""


def infographic_pillars_data_js(info):
    roof = info.get("roof", {"name": "Goal"})
    pillars = info.get("pillars", [])
    foundation = info.get("foundation", {"name": "Current capability"})
    return f"""    const INFOGRAPHIC_TYPE = 'pillars';
    const ROOF = {json.dumps(roof)};
    const PILLARS = {json.dumps(pillars, indent=6).replace(chr(10), chr(10) + "    ")};
    const FOUNDATION = {json.dumps(foundation)};
"""


def infographic_custom_data_js(info):
    groups = info.get("groups", [])
    renderer_js = info.get("renderer_js", "")
    return f"""    const INFOGRAPHIC_TYPE = 'custom';
    const GROUPS = {json.dumps(groups, indent=6).replace(chr(10), chr(10) + "    ")};
    const CUSTOM_RENDERER_JS = {json.dumps(renderer_js)};
"""


INFOGRAPHIC_DATA_JS = {
    "phase-journey": infographic_phase_journey_data_js,
    "maturity-ladder": infographic_maturity_ladder_data_js,
    "hub-spoke": infographic_hub_spoke_data_js,
    "before-after": infographic_before_after_data_js,
    "pillars": infographic_pillars_data_js,
    "custom": infographic_custom_data_js,
}


def gen_plan_achievement_section(spec):
    info = spec["infographic"]
    title_html = info.get("title", "Plan Achievement")
    eyebrow = info.get("eyebrow", "Plan Achievement · Visual Story")
    narrative = info.get("narrative", "")

    viewbox = info.get("viewBox") or {
        "phase-journey": "0 0 1200 240",
        "maturity-ladder": "0 0 800 460",
        "hub-spoke": "0 0 1000 480",
        "before-after": "0 0 1100 320",
        "pillars": "0 0 1100 380",
        "custom": "0 0 1200 320",
    }.get(info["type"], "0 0 1200 240")

    inner_svg = ""
    if info["type"] == "custom":
        if info.get("svg_inline_file"):
            svg_path = Path(info["svg_inline_file"])
            if not svg_path.is_absolute():
                svg_path = Path.cwd() / svg_path
            if svg_path.exists():
                inner_svg = svg_path.read_text()
                inner_svg = re.sub(r"^<!--.*?-->", "", inner_svg, flags=re.DOTALL).strip()
            else:
                inner_svg = f'<text x="20" y="40" fill="red">SVG file not found: {svg_path}</text>'
        else:
            inner_svg = info.get("svg_inline", "")

    return f'''<!-- PLAN ACHIEVEMENT INFOGRAPHIC — human-only visual story -->
<section class="plan-achievement" id="plan-achievement" data-role="human-display">
  <div class="achievement-header">
    <div>
      <div class="achievement-eyebrow">{esc(eyebrow)}</div>
      <div class="achievement-title">{title_html}</div>
    </div>
    <div class="achievement-meta">
      <div class="achievement-counter">
        <div class="achievement-num" id="ach-overall-pct">0%</div>
        <div class="achievement-num-label">overall</div>
      </div>
    </div>
  </div>

  <svg class="phase-journey" id="phase-journey" viewBox="{viewbox}" preserveAspectRatio="xMidYMid meet"
       aria-label="Plan achievement infographic">{inner_svg}</svg>

  {f'<div class="achievement-narrative"><strong>The story</strong>{esc(narrative)}</div>' if narrative else ""}
</section>'''


def load_infographic_renderers_js():
    out = []
    info_dir = SKILL_DIR / "assets" / "infographics"
    for name in [
        "phase-journey",
        "maturity-ladder",
        "hub-spoke",
        "before-after",
        "pillars",
        "custom",
    ]:
        p = info_dir / f"{name}.js"
        if p.exists():
            out.append(f"// === {name} renderer ===\n" + p.read_text())
        else:
            out.append(
                f"// === {name} renderer (stub — file not found at {p}) ===\n"
                f"function render_{name.replace('-', '_')}(svg, _) {{ "
                f'svg.innerHTML = \'<text x="50" y="50" fill="red">{name} renderer missing</text>\'; }}'
            )
    return "\n\n".join(out)


RENDER_DISPATCH_JS = """
    function renderPhaseJourney(itemsArr) {
      const svg = document.getElementById('phase-journey');
      if (!svg) return;
      const type = (typeof INFOGRAPHIC_TYPE !== 'undefined') ? INFOGRAPHIC_TYPE : 'phase-journey';
      const renderers = {
        'phase-journey':   typeof render_phase_journey   !== 'undefined' ? render_phase_journey   : null,
        'maturity-ladder': typeof render_maturity_ladder !== 'undefined' ? render_maturity_ladder : null,
        'hub-spoke':       typeof render_hub_spoke       !== 'undefined' ? render_hub_spoke       : null,
        'before-after':    typeof render_before_after    !== 'undefined' ? render_before_after    : null,
        'pillars':         typeof render_pillars         !== 'undefined' ? render_pillars         : null,
        'custom':          typeof render_custom          !== 'undefined' ? render_custom          : null,
      };
      const fn = renderers[type];
      if (typeof fn === 'function') {
        fn(svg, itemsArr);
      } else {
        svg.innerHTML = '<text x="20" y="40" fill="currentColor">Unknown infographic type: ' + type + '</text>';
      }

      // Overall % big number
      const pctEl = document.getElementById('ach-overall-pct');
      if (pctEl) {
        const total = itemsArr.length;
        let done = 0;
        itemsArr.forEach(it => { if ((it.dataset.status || 'TODO') === 'DONE') done++; });
        const pct = total > 0 ? Math.round((done/total)*100) : 0;
        pctEl.textContent = pct + '%';
      }
    }
"""


# --------------------------------------------------------------------------
# Main build
# --------------------------------------------------------------------------
def slugify(title):
    """Lowercase, replace non-alphanumerics with hyphens, trim hyphens, cap 60 chars."""
    s = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return (s[:60].rstrip("-")) or "plan"


def render_html(spec, plan_dir):
    """Pure HTML render — separated so it can be re-invoked on --rebuild."""
    template = TEMPLATE_PATH.read_text()

    abs_path = str((plan_dir / "PLAN.html").resolve())
    location_comment = (
        f"\n<!-- ====================================================================\n"
        f"     PLAN_LOCATION: {abs_path}\n"
        f"     LAYOUT: sibling files in this directory — manifest.json (immutable\n"
        f"     dispatch graph), run_state.json (mutable runtime), _closeouts/ (write-\n"
        f"     ahead log), sessions/sNN.prompt.md + sessions/sNN.context.md.\n"
        f"     Run via /plan-execute &lt;this-directory&gt;.\n"
        f"     ==================================================================== -->\n"
    )
    template = template.replace("<main>\n", f"<main>{location_comment}\n", 1)

    item_to_session = {}
    for s in spec["sessions"]:
        for iid in s["items"]:
            item_to_session[iid] = s["id"]

    template = template.replace("{{PLAN_TITLE}}", esc(spec["title"]))
    meta_line = spec.get("subtitle", "")
    if "meta" in spec:
        meta_line = spec["meta"]
    if not meta_line:
        meta_line = f"{len(spec['sessions'])} sessions · {len(spec['items'])} items"
    template = template.replace("{{PLAN_META_LINE}}", meta_line)

    first_sess = (
        spec["sessions"][0]
        if spec["sessions"]
        else {"id": "s01", "title": "First session", "model": "Sonnet", "effort": ""}
    )
    template = template.replace(
        "{{FIRST_SESSION_TITLE}}", f"{first_sess['id'].upper()} — {esc(first_sess['title'])}"
    )
    template = template.replace("{{FIRST_SESSION_MODEL}}", esc(first_sess.get("model", "Sonnet")))
    template = template.replace("{{FIRST_SESSION_EFFORT}}", esc(first_sess.get("effort", "")))
    template = template.replace("{{FIRST_SESSION_ID}}", attr(first_sess["id"]))

    session_plan_blurb = f"{len(spec['sessions'])} sessions · {len(spec['items'])} items"
    template = template.replace("{{SESSION_PLAN_BLURB}}", session_plan_blurb)
    template = template.replace("{{SESSION_TOTAL_COUNT}}", str(len(spec["sessions"])))

    session_strip = gen_session_strip(spec["sessions"])
    cat_chips = gen_category_filter_chips(spec["categories"])
    cats_array = gen_cats_array_js(spec["categories"])
    cat_colors = gen_category_colors_css(spec["categories"])
    plan_achievement = gen_plan_achievement_section(spec)
    items_by_id = {it["id"]: it for it in spec["items"]}
    sessions_html = gen_sessions_block(spec["sessions"], items_by_id)
    cat_sections = "\n".join(
        gen_category_section(c, spec["items"], item_to_session) for c in spec["categories"]
    )

    info_type = spec["infographic"]["type"]
    info_data_js = INFOGRAPHIC_DATA_JS[info_type](spec["infographic"])
    renderers_raw = load_infographic_renderers_js()
    renderers_local = re.sub(
        r"window\.render_(\w+)\s*=\s*function", r"function render_\1", renderers_raw
    )
    infographic_full_js = info_data_js + "\n" + renderers_local + "\n" + RENDER_DISPATCH_JS

    decision_hotspots = gen_decision_hotspots_section(spec["items"])
    scope_section = gen_scope_section(spec)

    template = template.replace("<!-- INSERT_SESSION_STRIP -->", session_strip)
    template = template.replace("<!-- INSERT_PLAN_ACHIEVEMENT -->", plan_achievement)
    template = template.replace(
        "<!-- INSERT_DECISION_HOTSPOTS -->", decision_hotspots + scope_section
    )
    template = template.replace("<!-- INSERT_CATEGORY_FILTER_CHIPS -->", cat_chips)
    template = template.replace("<!-- INSERT_SESSIONS -->", sessions_html)
    template = template.replace("<!-- INSERT_CATEGORY_SECTIONS -->", cat_sections)
    template = template.replace("<!-- INSERT_PLAN_CHANGES -->", PLAN_CHANGES_SECTION)
    template = template.replace(
        "/* INSERT_INFOGRAPHIC_DATA */",
        "/* (infographic data injected at INSERT_INFOGRAPHIC_RENDERERS marker) */",
    )
    template = re.sub(
        r"/\* INSERT_INFOGRAPHIC_RENDERERS[^*]*\*/", lambda m: infographic_full_js, template
    )
    template = template.replace("/* INSERT_CATS_ARRAY */ []", cats_array)
    template = template.replace("/* INSERT_CATEGORY_COLORS */", cat_colors)
    return template


def _extract_inline_script(html_text):
    """Return the largest inline ``<script>…</script>`` body — the dashboard JS."""
    blocks = re.findall(r"<script>(.*?)</script>", html_text, re.DOTALL)
    return max(blocks, key=len) if blocks else None


def validate_dashboard_js(html_text):
    """Static-check the dashboard's inline JS for undefined identifiers.

    Guards the ``isOpus is not defined`` class (2026-06-06): a runtime
    ``ReferenceError`` inside a render function aborts ``recomputeCounts()``
    mid-run and silently freezes the session-strip / nav / donuts at their
    authored fallback statuses — a broken dashboard that still *looks* valid.
    ``node --check`` cannot catch this (it is a runtime error, not syntax), so
    we run ESLint ``no-undef`` over the assembled inline script.

    Raises ``ValueError`` on a real undefined-identifier error. Degrades
    gracefully (warn + skip) when node/eslint are unavailable, so plan
    generation never hard-depends on a JS toolchain being installed.
    """
    script = _extract_inline_script(html_text)
    if not script:
        print(
            "WARNING: dashboard JS validation skipped — no inline <script> found.",
            file=sys.stderr,
        )
        return

    # Unit-suite opt-out, same switch as render_verify / structural_gate.
    # MEASURED 2026-08-14: the two `npx eslint` spawns below cost ~2.5 s EVERY
    # time a test builds a plan, which the plan-execute suite does per test.
    # DEFAULT IS ON — only conftest.py sets this, so a real plan build still
    # runs the no-undef guard. The warn-and-skip contract below already treats
    # unavailable tooling as skipped-not-passed, so this adds no new silent
    # pass. test_render_verify_optout.py holds the known-positive control.
    if os.environ.get("PLAN_EXECUTE_SKIP_BROWSER_CHECKS") == "1":
        return

    if shutil.which("node") is None or shutil.which("npx") is None:
        print(
            "WARNING: dashboard JS validation skipped — node/npx not on PATH. "
            "Install Node.js to enable the no-undef guard against broken dashboards.",
            file=sys.stderr,
        )
        return

    probe = subprocess.run(
        ["npx", "--no-install", "eslint", "--version"],
        capture_output=True,
        text=True,
    )
    if probe.returncode != 0:
        print(
            "WARNING: dashboard JS validation skipped — eslint not available via "
            "`npx --no-install eslint` (run `npm i -g eslint` to enable the guard).",
            file=sys.stderr,
        )
        return

    proc = subprocess.run(
        [
            "npx", "--no-install", "eslint",
            "--no-config-lookup",
            "--config", str(DASHBOARD_ESLINT_CONFIG),
            "--stdin", "--stdin-filename", "dashboard.js",
            "-f", "json",
        ],
        input=script,
        capture_output=True,
        text=True,
    )
    try:
        results = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        print(
            "WARNING: dashboard JS validation skipped — could not parse eslint output "
            f"(exit {proc.returncode}): {(proc.stderr or '').strip()[:300]}",
            file=sys.stderr,
        )
        return

    errors = [
        m
        for r in results
        for m in r.get("messages", [])
        if m.get("severity") == 2
    ]
    if errors:
        detail = "\n".join(
            f"  line {m.get('line')}:{m.get('column')}  "
            f"{m.get('ruleId') or 'fatal'}  {m.get('message')}"
            for m in errors
        )
        raise ValueError(
            "Dashboard JS validation FAILED — the generated dashboard contains "
            "JavaScript that throws at runtime and silently breaks the nav / donuts "
            "/ session strip:\n"
            f"{detail}\n"
            "If an identifier is a legitimate new browser or injected-data global, "
            "add it to scripts/dashboard-eslint.config.mjs. Otherwise it is a real "
            "bug (typo / undeclared variable) in assets/base-template.html or "
            "assets/infographics/*.js — fix it there."
        )


# Any article whose data-status is not TODO means the plan has been executed.
# Anchored to the <article> tag on purpose: the template's CSS carries
# `[data-status="DONE"]` selectors for every status, so a bare attribute scan
# fires on a freshly built plan. Dependency-free on purpose too — this guard
# must still refuse when the co-located plan-execute skill is absent.
_EXECUTED_STATUS_RE = re.compile(r'<article [^>]*data-status="(?!TODO")')


def carry_over_recorded_state(prior_html, html_text, plan_html, preserve_state):
    """Carry an executed plan's recorded state onto the freshly rendered HTML.

    Per-session/item status lives ONLY in PLAN.html's `data-status` attributes —
    not in run_state.json, and not fully in _closeouts/ (a checkpoint acked by
    the orchestrator leaves no closeout). /plan-execute's dispatcher and its
    structural DONE-gate both read it from here, so a rebuild that re-renders
    every article at TODO destroys dispatch state, not just badges.

    Returns the HTML to write. Refuses rather than wiping.
    """
    executed = bool(_EXECUTED_STATUS_RE.search(prior_html))
    if executed and not preserve_state:
        raise ValueError(
            f"{plan_html} records executed sessions/items (non-TODO data-status), and "
            "rebuilding without --preserve-state would reset every one of them to TODO. "
            "That status is the ONLY record of what has run — /plan-execute dispatches "
            "from it — so this would destroy dispatch state, not just the dashboard. "
            "Re-run with --preserve-state to carry it forward, or delete PLAN.html "
            "first to deliberately restart the plan from scratch."
        )
    if not preserve_state:
        return html_text
    if _article_block is None:
        if not executed:
            return html_text
        raise ValueError(
            f"{plan_html} records executed sessions/items, but the co-located "
            "plan-execute skill (scripts/article_block.py) is missing, so their status "
            "cannot be carried forward. Restore plan-execute and re-run."
        )
    return _article_block.carry_over_state(prior_html, html_text)


def build(spec, out_path, project_root=None, preserve_state=False):
    """Build the multi-file plan layout under `out_path` (a DIRECTORY).

    Emits:
      out_path/PLAN.html
      out_path/spec.json
      out_path/manifest.json              (immutable dispatch graph)
      out_path/run_state.json             (mutable runtime state)
      out_path/sessions/<id>.prompt.md
      out_path/sessions/<id>.context.md
      out_path/_closeouts/                (empty dir for write-ahead log)

    For backward compat: if `out_path` ends with `.html`, treat its PARENT as
    the plan directory and place PLAN.html alongside the other files.
    """
    # Emit the integration session for every worktree-isolated parallel group
    # BEFORE anything reads `spec["sessions"]` — so the dashboard, the manifest,
    # the prompt files and the spec.json written below all show the same plan.
    # Idempotent, so a --rebuild does not stack duplicates.
    spec = with_integration_sessions(spec)
    validate_spec(spec)
    # RS-06 (2026-08-13): stamp the build-time research-environment record onto
    # the spec BEFORE anything renders or serializes it, so PLAN.html, spec.json
    # and manifest.json all describe the same observation. Returns None — and so
    # changes nothing at all — for a spec that makes no research-skip claim,
    # which is every plan built before today.
    _env = research_env_record(spec)
    if _env is not None:
        spec = dict(spec)
        spec["research_env"] = _env
    # Build-time shipping guard: deploy targets / gates must resolve and git
    # adapter flags must exist BEFORE manifest.json is written.
    validate_shipping_resolves(spec, project_root)
    # Build-time verify guard: every verify gate id must resolve in the registry.
    validate_verify_resolves(spec, project_root)
    # Build-time peer-review guard: peer_triggers values are valid AND any
    # declared trigger carries an adversarial-review gate (Layer 1, 2026-07-10).
    validate_peer_triggers(spec)

    # Resolve plan_dir: accept either a directory or a legacy `*.html` path.
    if str(out_path).endswith(".html"):
        plan_dir = out_path.parent
        plan_dir.mkdir(parents=True, exist_ok=True)
    else:
        plan_dir = out_path
        plan_dir.mkdir(parents=True, exist_ok=True)

    items_by_id = {it["id"]: it for it in spec["items"]}
    phases_by_id = {p["id"]: p for p in spec.get("phases", []) if "id" in p}

    # 1. HTML
    html_text = render_html(spec, plan_dir)
    plan_html = plan_dir / "PLAN.html"
    # Rebuild over a plan that has already run: every article renders at TODO,
    # so writing straight out would reset the dashboard AND the dispatch state
    # /plan-execute reads from it. Carry the recorded state across first.
    prior_html = plan_html.read_bytes().decode("utf-8") if plan_html.exists() else None
    if prior_html is not None:
        html_text = carry_over_recorded_state(prior_html, html_text, plan_html, preserve_state)
    # Post-render guard: the dashboard's inline JS must not contain a runtime
    # undefined-identifier error (would silently freeze the nav/donuts/strip).
    # Validate BEFORE writing so a broken dashboard never lands on disk and a
    # --rebuild keeps the prior working PLAN.html. See dashboard-eslint.config.mjs.
    validate_dashboard_js(html_text)
    plan_html.write_text(html_text)

    # 2. spec.json (authoring input, kept for re-builds)
    (plan_dir / "spec.json").write_text(json.dumps(spec, indent=2, ensure_ascii=False))

    # 3. manifest.json — IMMUTABLE dispatch graph (regenerate each build)
    manifest = gen_manifest(spec)
    (plan_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False))

    # 4. run_state.json — mutable. Only seed if missing (preserve runtime).
    rs_path = plan_dir / "run_state.json"
    if not rs_path.exists() or not preserve_state:
        rs_path.write_text(json.dumps(init_run_state(), indent=2))

    # 5. sessions/*.prompt.md + *.context.md
    sessions_dir = plan_dir / "sessions"
    sessions_dir.mkdir(exist_ok=True)
    for s in spec["sessions"]:
        sid = s["id"]
        resolved_ps = resolve_post_session(s, phases_by_id)
        resolved_vb = resolve_verify(s, phases_by_id)
        (sessions_dir / f"{sid}.prompt.md").write_text(
            gen_session_prompt_md(s, plan_dir.name, items_by_id,
                                  post_session=resolved_ps, verify=resolved_vb)
        )
        (sessions_dir / f"{sid}.context.md").write_text(gen_session_context_md(s, items_by_id))

    # 6. _closeouts/ directory placeholder
    (plan_dir / "_closeouts").mkdir(exist_ok=True)

    # 7. .gitignore amendment (P11) when registered in a project
    if project_root:
        amend_gitignore(project_root)

    return plan_html


def register_plan_in_project(plan_html_path, spec, project_root):
    project_root = Path(project_root).resolve()
    index_path = project_root / "_plans_index.md"
    plan_dir = plan_html_path.parent.resolve()
    try:
        rel_dir = plan_dir.relative_to(project_root)
    except ValueError:
        rel_dir = plan_dir
    rel_html = f"{rel_dir}/PLAN.html"

    title = spec["title"]
    today = today_iso()
    n_sessions = len(spec["sessions"])
    n_items = len(spec["items"])

    if index_path.exists():
        body = index_path.read_text()
    else:
        body = (
            "# Active Plans\n\n"
            "This file lists all plan dashboards in this project. Each plan is a directory\n"
            "built by the `plan-builder` skill — PLAN.html for viewing, manifest.json + sessions/\n"
            "for execution. To run a session: `/plan-execute <plan-dir>` (or `--session sNN`).\n\n"
            "## Plans\n\n"
        )

    entry = (
        f"- **[{title}]({rel_html})** · {n_sessions} sessions · {n_items} items · created {today}"
        f" · run: `/plan-execute {rel_dir}`\n"
    )
    body = re.sub(
        rf"^- \*\*\[[^\]]+\]\({re.escape(str(rel_html))}\).*\n", "", body, flags=re.MULTILINE
    )
    body = body.rstrip() + "\n" + entry
    index_path.write_text(body)

    snippet = (
        f"**Active plan:** [{title}]({rel_html}) · "
        f"{n_sessions} sessions · run via `/plan-execute {rel_dir}` · "
        f"see also `_plans_index.md`"
    )
    return index_path, snippet


def _print_shipping_dry_run(spec, project_root):
    """--dry-run-shipping: enumerate the destructive surface, write nothing.

    Surfaces the pre-authorized commit/push/PR/deploy/gate actions at authoring
    time so the operator sees them before execution (Q6 visibility lever)."""
    # Same synthesis `build()` does — an auto-emitted integration session OWNS a
    # commit, so it belongs in the destructive-surface preview.
    spec = with_integration_sessions(spec)
    validate_spec(spec)
    validate_shipping_resolves(spec, project_root)
    validate_peer_triggers(spec)
    phases_by_id = {p["id"]: p for p in spec.get("phases", []) if "id" in p}
    sessions_by_id = {s["id"]: s for s in spec["sessions"]}
    print("=" * 72)
    print(f"SHIPPING DRY-RUN — {spec.get('title', '(untitled)')}  (no files written)")
    print("=" * 72)
    any_decl = False
    for s in spec["sessions"]:
        ps = resolve_post_session(s, phases_by_id)
        if not ps:
            continue
        any_decl = True
        parts = []
        if ps.get("git", "none") != "none":
            parts.append(f"git:{ps['git']}")
        for g in ps.get("pre_deploy_gates", []) or []:
            parts.append(f"gate:{g}")
        if ps.get("deploy_argv"):
            parts.append(f"deploy:argv({' '.join(ps['deploy_argv'])})")
        elif ps.get("deploy", "none") not in (None, "none"):
            parts.append(f"deploy:{ps['deploy']}")
        line = f"  {s['id']}: " + ", ".join(parts)
        if _deploy_present(ps):
            line += f"  [auth-digest {deploy_auth_digest(s['id'], sessions_by_id)[:12]}…]"
            line += f"  rollback_hint={ps.get('rollback_hint')!r}"
        print(line)
    if not any_decl:
        print("  (no sessions declare shipping actions — default behaviour, nothing fires)")
    print("=" * 72)


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Build a plan dashboard + execution bundle.")
    parser.add_argument("spec", help="Path to plan spec JSON file")
    parser.add_argument(
        "output",
        nargs="?",
        help="Plan directory (defaults to ./_plans/<slug>-<today>). "
        "Legacy: a path ending in .html places PLAN.html beside other files in the parent dir.",
    )
    parser.add_argument(
        "--register-in",
        metavar="PROJECT_ROOT",
        help="Register the plan in <PROJECT_ROOT>/_plans_index.md and amend .gitignore.",
    )
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="Rebuild over an existing plan directory (regenerates PLAN.html + manifest.json + sessions/).",
    )
    parser.add_argument(
        "--preserve-state",
        action="store_true",
        help="With --rebuild: carry the executed plan's recorded state forward — every "
        "article's status/notes/shipping badge in PLAN.html, plus run_state.json. "
        "Sessions or items ADDED by the rebuild start at TODO. Without it, a rebuild "
        "over a plan that has already run is REFUSED rather than silently reset.",
    )
    parser.add_argument(
        "--dry-run-shipping",
        action="store_true",
        help="Print every shipping action declared across all sessions/phases "
        "(the destructive surface) WITHOUT writing any files.",
    )
    args = parser.parse_args()

    spec_path = Path(args.spec)
    spec = json.loads(spec_path.read_text())
    # Apply the integration-session synthesis once, here, so the index entry and
    # the printed counts below describe the plan that was actually built (build()
    # applies it again — it is idempotent).
    spec = with_integration_sessions(spec)

    if args.dry_run_shipping:
        _print_shipping_dry_run(spec, args.register_in)
        return

    if args.output:
        out_path = Path(args.output)
    else:
        slug = slugify(spec["title"])
        anchor = Path(args.register_in) if args.register_in else Path.cwd()
        out_path = anchor / "_plans" / f"{slug}-{today_iso()}"

    if out_path.exists() and out_path.is_dir() and not args.rebuild:
        existing = list(out_path.iterdir())
        if existing:
            print(
                f"Plan directory {out_path} is not empty. Use --rebuild to overwrite "
                "(add --preserve-state to carry an executed plan's status forward).",
                flush=True,
            )

    plan_html = build(
        spec, out_path, project_root=args.register_in, preserve_state=args.preserve_state
    )
    print(f"Built {plan_html} ({plan_html.stat().st_size:,} bytes)")
    print(f"Plan directory: {plan_html.parent}")
    print(f"  manifest.json  ({(plan_html.parent / 'manifest.json').stat().st_size:,} bytes)")
    print(f"  sessions/      ({len(list((plan_html.parent / 'sessions').iterdir()))} files)")

    if args.register_in:
        index_path, snippet = register_plan_in_project(plan_html, spec, args.register_in)
        print(f"Registered in {index_path}")
        print()
        print("=" * 72)
        print("Suggest pasting this into the project's CLAUDE.md or session-handoff file:")
        print("=" * 72)
        print(snippet)
        print("=" * 72)


if __name__ == "__main__":
    main()
