#!/usr/bin/env python3
"""resolve_route.py — the vendored, provider-neutral route + fallback-ladder resolver.

Turns `(task_class, active_provider)` into a concrete `{model_id, native_effort}`,
and — on a CALLER-SUPPLIED signal (this module detects nothing itself; see
ARCHITECTURE.md §3 "Resolver contract") — walks that provider's escalation ladder
on `failure` or degrade ladder on `entitlement`/`unavailable`/an opted-in `refusal`.

This is the REFERENCE implementation the schema in ARCHITECTURE.md §3 is tested
against. It reads ONLY `claude/model-routing.yaml` (`task_classes:` +
`providers.<active_provider>`) — there are NO Anthropic-specific (or any other
provider's) constants baked in here. A provider profile that declines to declare
an `escalation:`/`degrade:` block (or spells it the literal string `"none"`) makes
this resolver return an explicit `exhausted` result for that ladder — never a
silent fallback borrowed from another provider's shape.

stdlib-only (no PyYAML) — matches the house rule already used by
verify-routing.sh / render-routing-digest.py / routing-retro's retro_scan.py: this
repo's guard/renderer/scanner all regex-parse the SSOT rather than requiring a YAML
dependency, so a consumer with no venv can still import this module. See each of
those files' own header comment for the same rationale.

Public API (stateless — one call, one decision; the caller owns retry state):

  resolve(task_class, active_provider, current=None, signal="none") -> dict
      {"model_id": str|None, "native_effort": str|None} on a normal decision, or
      the string "exhausted" when no rung remains (ladder opted out, or already
      at the terminal rung). This is the ONE normative entry point — see
      ARCHITECTURE.md §3 for the full contract (baseline / escalation-walk /
      degrade-walk semantics) and docs/INTEGRATION.md §1 for the exact call shape
      other consumers (e.g. render-routing-digest.py) already depend on.

  escalate(task_class, active_provider, current, reason=None) -> dict|"exhausted"
      Convenience wrapper: resolve(..., current=current, signal="failure").
      `reason` is accepted for caller-side logging/audit only (e.g. "2 failures at
      the same root cause") — never evaluated here; the resolver does not decide
      WHETHER to escalate, only WHAT the next rung is once the caller has decided.

  degrade(task_class, active_provider, current, signal="unavailable") -> dict|"exhausted"
      Convenience wrapper: resolve(..., current=current, signal=signal). `signal`
      must be one of the provider's declared `degrade.signals` signals (typically
      "entitlement" or "unavailable"; "refusal" only if that profile opts in) — an
      unrecognized-but-well-formed signal (e.g. "refusal" on a provider that didn't
      opt in) raises RouteResolverError; a signal outside the whole taxonomy (see
      _SIGNALS) raises ValueError. Never silently coerced to a default.

  NAMING NOTE — `escalate`'s `reason` vs `degrade`'s `signal` are DELIBERATELY
  named differently, not a typo: `escalate`'s extra param is inert audit prose,
  `degrade`'s is a real dispatch value forwarded straight into `signal=`. See
  `degrade()`'s own docstring for the full rationale (Claude adversarial review
  finding #3, S09 closeout).

Both `escalate`/`degrade` exist because the session brief names them explicitly;
they carry NO ladder-walking logic of their own — `resolve()` is the single
source of that behavior, so the three functions can never drift apart from
each other.
"""
from __future__ import annotations

import re
from typing import Optional

_SIGNALS = ("none", "failure", "refusal", "entitlement", "unavailable")
# Gearbox's SHIPPED 3-tier vocabulary (ARCHITECTURE.md §1) — used ONLY as a
# fallback ordering hint for _Profile.tier_rank()'s floor/ceiling comparisons
# when a provider's own `models:` keys happen to match these exact names (true
# for every shipped example profile). NOT an enforced constraint: a provider
# profile using a different tier vocabulary still resolves correctly (baseline/
# escalation logic never consults this constant at all — only tier_rank()'s
# floor comparison does, and it raises loudly on an unrecognized tier rather
# than silently defaulting, see tier_rank()'s docstring).
_TIERS = ("cheap_fast", "workhorse", "frontier_reasoner")


class RouteResolverError(Exception):
    """Malformed SSOT or an unresolvable provider/task_class — always fails
    loudly (never silently substitutes a guess)."""


# ---------------------------------------------------------------------------
# SSOT parsing (regex slicing — same technique as verify-routing.sh /
# render-routing-digest.py / retro_scan.py; see module docstring).
# ---------------------------------------------------------------------------
def _sub_block(text: str, key: str, indent: int) -> Optional[str]:
    """Body of a `<indent spaces><key>:` block: everything after that header line
    up to (not including) the next line at indent <= `indent`. Tolerates a
    trailing inline comment on the header line. None if `key:` isn't found.

    NORMALIZATION NOTE (the YAML 1.1 bare-key gotcha the schema renamed around):
    this is a plain substring/regex match on literal key text — it never
    round-trips through a real YAML loader, so there is no risk of a bare `on`
    key parsing to the boolean `True` the way a YAML-1.1 loader (e.g. PyYAML's
    default resolver) would coerce it. The SSOT's degrade-signal key is named
    `signals:` (not `on:`) precisely to avoid this trap outright — see
    `_find_list(block, "signals")` below. If this module is ever rewritten on
    top of a real YAML library, any future bare `on`-shaped key MUST be read
    with a loader whose implicit resolver is disabled for bools (or the key
    quoted in the SSOT) — regex parsing sidesteps the trap entirely, which is
    why the house rule (no PyYAML) is kept here too.
    """
    header_rx = re.compile(rf"^{' ' * indent}{re.escape(key)}:[^\n]*\n", re.M)
    hm = header_rx.search(text)
    if hm is None:
        return None
    body_start = hm.end()
    sib_rx = re.compile(rf"^ {{0,{indent}}}\S", re.M)
    sib = sib_rx.search(text, body_start)
    end = sib.start() if sib else len(text)
    return text[body_start:end]


def _find_scalar(block: str, key: str) -> Optional[str]:
    m = re.search(rf"^\s*{re.escape(key)}:\s*(\S+)", block, re.M)
    return m.group(1).rstrip(",") if m else None


def _find_list(block: str, key: str) -> Optional[list]:
    """`key: [a, b]` on one line, or a `- item` block below `key:`."""
    m = re.search(rf"^\s*{re.escape(key)}:\s*\[([^\]]*)\]", block, re.M)
    if m:
        return [x.strip() for x in m.group(1).split(",") if x.strip()]
    sub = _sub_block(block, key, _line_indent(block, key))
    if sub is None:
        return None
    items = re.findall(r"^\s*-\s*(\S+)", sub, re.M)
    return items or None


def _line_indent(text: str, key: str) -> int:
    m = re.search(rf"^(\s*){re.escape(key)}:", text, re.M)
    return len(m.group(1)) if m else 0


def _find_flow_map(block: str, key: str) -> Optional[dict]:
    """`key: { a: b, c: d }` — a single-line YAML flow mapping (the shape
    `degrade.ladder` / `degrade.effort_on_degrade` actually use in the SSOT: a
    short tier->tier or tier->level map that never spans multiple lines). Not
    the same shape as `_sub_block`'s multi-line nested block — this is scoped
    to ONE line, so it can't accidentally swallow a sibling key below it."""
    m = re.search(rf"^\s*{re.escape(key)}:\s*\{{([^}}]*)\}}", block, re.M)
    if not m:
        return None
    pairs = dict(re.findall(r"([\w-]+):\s*([\w-]+)", m.group(1)))
    return pairs or None


def _is_none_literal(block_or_scalar: Optional[str]) -> bool:
    """A profile block that is literally the string 'none' (opt-out), e.g.
    `escalation: none` / `degrade: none`."""
    return isinstance(block_or_scalar, str) and block_or_scalar.strip() == "none"


def _parse_task_classes(ssot_text: str) -> dict:
    tc_start = ssot_text.find("\ntask_classes:\n")
    if tc_start == -1:
        raise RouteResolverError("SSOT has no `task_classes:` block")
    block = _sub_block(ssot_text, "task_classes", 0) or ""
    rx = re.compile(r"^\s*([\w-]+):\s*\{\s*tier:\s*([\w-]+),\s*effort:\s*(\w+)\s*\}", re.M)
    parsed = {mo.group(1): {"tier": mo.group(2), "intent": mo.group(3)} for mo in rx.finditer(block)}
    if not parsed:
        raise RouteResolverError("parsed zero rows from SSOT `task_classes:` — parser or SSOT broken")
    return parsed


def _parse_provider_block(ssot_text: str, provider: str) -> str:
    providers_block = _sub_block(ssot_text, "providers", 0)
    if providers_block is None:
        raise RouteResolverError("SSOT has no `providers:` block")
    block = _sub_block(providers_block, provider, 2)
    if block is None:
        raise RouteResolverError(f"no `providers.{provider}:` block in SSOT")
    return block


def _parse_models(provider_block: str) -> dict:
    models_slice = _sub_block(provider_block, "models", 4)
    if models_slice is None:
        raise RouteResolverError("provider block has no `models:` key")
    return dict(re.findall(r"^\s+([\w-]+):\s*(\S+)", models_slice, re.M))


def _parse_effort_map(provider_block: str, tiers) -> dict:
    effort_slice = _sub_block(provider_block, "effort", 4) or ""
    map_slice = _sub_block(effort_slice, "map", 6) or ""
    effort_map = {}
    for tier in tiers:
        row = re.search(rf"^\s*{re.escape(tier)}:\s*\{{([^}}]*)\}}", map_slice, re.M)
        if row:
            pairs = dict(re.findall(r"(\w+):\s*(null|\w+)", row.group(1)))
            effort_map[tier] = {k: (None if v == "null" else v) for k, v in pairs.items()}
    return effort_map


def _parse_escalation(provider_block: str) -> Optional[dict]:
    """None => opted out via the EXPLICIT literal `escalation: none`. Otherwise
    {"effort_ladder": {tier: [lvl,...]}, "model_ladder": [tier,...]}.

    A GENUINELY MISSING `escalation:` key (no `escalation:` line at all — e.g. a
    typo'd `esclation:`, or a profile authored before this key was required)
    raises RouteResolverError instead of silently degrading to the opt-out
    result. ARCHITECTURE.md §3 Schema rules is explicit: "a missing block is a
    validation error, not a default" — treating a missing key the same as an
    explicit `none` would let a typo silently and permanently disable
    escalation for a provider without ever surfacing as an error (Claude
    adversarial review finding #2, S09 closeout)."""
    scalar = _find_scalar(provider_block, "escalation")
    if _is_none_literal(scalar):
        return None
    if scalar is None and _sub_block(provider_block, "escalation", 4) is None:
        raise RouteResolverError(
            "provider block has no `escalation:` key at all — ARCHITECTURE.md §3 requires "
            "either a real escalation block or the explicit literal `escalation: none` "
            "(opt-out); a missing key is a validation error, not a default"
        )
    block = _sub_block(provider_block, "escalation", 4)
    if block is None:
        return None
    effort_ladder_slice = _sub_block(block, "effort_ladder", 6) or ""
    effort_ladder = {}
    for mo in re.finditer(r"^\s*([\w-]+):\s*\[([^\]]*)\]", effort_ladder_slice, re.M):
        tier, items = mo.group(1), mo.group(2)
        effort_ladder[tier] = [x.strip() for x in items.split(",") if x.strip()]
    model_ladder = _find_list(block, "model_ladder") or []
    if not effort_ladder and not model_ladder:
        return None
    return {"effort_ladder": effort_ladder, "model_ladder": model_ladder}


def _parse_degrade(provider_block: str) -> Optional[dict]:
    """None => opted out via the EXPLICIT literal `degrade: none`. Otherwise
    {"signals": [...], "ladder": {tier: tier}, "floor": tier, "effort_on_degrade": {tier: lvl}}.

    A GENUINELY MISSING `degrade:` key raises RouteResolverError — see
    `_parse_escalation`'s docstring for the identical rationale (ARCHITECTURE.md
    §3: missing block is a validation error, not a default)."""
    scalar = _find_scalar(provider_block, "degrade")
    if _is_none_literal(scalar):
        return None
    if scalar is None and _sub_block(provider_block, "degrade", 4) is None:
        raise RouteResolverError(
            "provider block has no `degrade:` key at all — ARCHITECTURE.md §3 requires "
            "either a real degrade block or the explicit literal `degrade: none` (opt-out); "
            "a missing key is a validation error, not a default"
        )
    block = _sub_block(provider_block, "degrade", 4)
    if block is None:
        return None
    on_signals = _find_list(block, "signals") or []
    # `ladder:` / `effort_on_degrade:` are single-line flow mappings in the SSOT
    # (e.g. `ladder: { frontier_reasoner: workhorse }`) — try the flow-map shape
    # first, falling back to a multi-line nested block for a profile authored
    # with the other (also-legal YAML) shape.
    ladder = _find_flow_map(block, "ladder") or {}
    if not ladder:
        ladder_slice = _sub_block(block, "ladder", 6) or ""
        ladder = dict(re.findall(r"^\s*([\w-]+):\s*([\w-]+)", ladder_slice, re.M))
    floor = _find_scalar(block, "floor")
    effort_on_degrade = _find_flow_map(block, "effort_on_degrade") or {}
    if not effort_on_degrade:
        eod_slice = _sub_block(block, "effort_on_degrade", 6) or ""
        effort_on_degrade = dict(re.findall(r"^\s*([\w-]+):\s*([\w-]+)", eod_slice, re.M))
    if not ladder and not floor:
        return None
    return {"signals": on_signals, "ladder": ladder, "floor": floor, "effort_on_degrade": effort_on_degrade}


class _Profile:
    """Everything resolved from one provider's SSOT block. `models`/`effort_map`
    are parsed eagerly (every call needs them, even a bare baseline resolve).
    `escalation`/`degrade` are parsed LAZILY, on first access via the
    `escalation`/`degrade` properties below — not in `__init__` — so that a
    provider block missing one of those keys entirely (a validation error per
    ARCHITECTURE.md §3, see `_parse_escalation`/`_parse_degrade`) only raises
    when a caller actually asks to escalate/degrade, never when they just want
    a plain baseline `{model_id, native_effort}` for a provider that hasn't
    filled in ladders yet. Eagerly parsing both in `__init__` was tried first
    and rejected: it broke baseline resolution for ANY profile missing either
    key, which is a much larger blast radius than the bug it was fixing."""

    __slots__ = ("name", "models", "effort_map", "_block", "_escalation", "_degrade")

    _UNSET = object()

    def __init__(self, ssot_text: str, provider: str):
        block = _parse_provider_block(ssot_text, provider)
        self.name = provider
        self.models = _parse_models(block)
        self.effort_map = _parse_effort_map(block, self.models.keys())
        self._block = block
        self._escalation = self._UNSET
        self._degrade = self._UNSET

    @property
    def escalation(self) -> Optional[dict]:
        if self._escalation is self._UNSET:
            self._escalation = _parse_escalation(self._block)
        return self._escalation

    @property
    def degrade(self) -> Optional[dict]:
        if self._degrade is self._UNSET:
            self._degrade = _parse_degrade(self._block)
        return self._degrade

    def tier_of(self, model_id: str) -> Optional[str]:
        for tier, mid in self.models.items():
            if mid == model_id:
                return tier
        return None

    def native_effort(self, tier: str, intent: str) -> Optional[str]:
        """`None` is a LEGITIMATE result only for a tier whose effort map is
        present and entirely null (rejects the dial — "omit the dial" per
        ARCHITECTURE.md §3). Anything else missing is a malformed SSOT and
        must raise, never silently return `None` (which would be
        indistinguishable from the legitimate no-dial case). Two distinct
        malformed shapes, both closed here (Codex adversarial review finding
        #1, S09 closeout — reproduced: a `workhorse` map missing `standard`
        silently resolved to `native_effort: None` for a `standard` intent
        instead of raising):
          1. the tier has NO row at all in `effort.map` (parser found zero
             keys for it) — `self.effort_map.get(tier)` is `None`;
          2. the tier's row exists, is NOT all-null, but omits the mandatory
             `standard` fallback key ARCHITECTURE.md §3 requires every
             non-null map to carry."""
        tier_map = self.effort_map.get(tier)
        if tier_map is None:
            raise RouteResolverError(
                f"provider '{self.name}' effort.map has no row at all for tier '{tier}' — "
                "every tier in `models:` must have a corresponding `effort.map` row (an "
                "all-null row is how a tier legitimately rejects the dial; a MISSING row "
                "is a malformed SSOT, not the same thing)"
            )
        if all(v is None for v in tier_map.values()):
            return None  # tier rejects the dial entirely — the one legitimate None
        if "standard" not in tier_map:
            raise RouteResolverError(
                f"provider '{self.name}' effort.map.{tier} is non-null but omits the "
                "mandatory `standard` key — ARCHITECTURE.md §3: \"`standard` is the "
                "mandatory intent in every non-null effort map\""
            )
        # Mandatory `standard` fallback per ARCHITECTURE.md §3.
        return tier_map.get(intent, tier_map["standard"])

    def tier_rank(self, tier: str) -> int:
        """Ordinal rank of `tier` for floor/ceiling comparisons, derived from
        THIS PROFILE's own `models:` key order — never the hardcoded `_TIERS`
        module constant. `_TIERS` is Gearbox's shipped 3-tier vocabulary
        (cheap_fast < workhorse < frontier_reasoner) used only as the FALLBACK
        rank source for a profile that happens to use those exact names; a
        profile is not required to. Raises RouteResolverError on a tier name
        this profile doesn't recognize at all, rather than silently defaulting
        it to an edge rank (-1/least, or a large number/greatest) the way a
        `.get(tier, sentinel)` lookup would — that silent default is exactly
        the kind of Anthropic-shape-borrowing this module otherwise refuses to
        do (Claude adversarial review finding #4, S09 closeout)."""
        if tier in self.models:
            # Prefer the profile's own declared tier order when it matches the
            # known vocabulary; fall back to the shipped _TIERS order otherwise
            # (both orderings agree for every shipped example profile today).
            ordered = [t for t in _TIERS if t in self.models] or list(self.models)
            if tier in ordered:
                return ordered.index(tier)
        raise RouteResolverError(
            f"tier '{tier}' is not a recognized tier for provider '{self.name}' "
            f"(known tiers: {sorted(self.models)}) — cannot compare it against a floor/ceiling"
        )


def _load(ssot_path: str, active_provider: str):
    with open(ssot_path, encoding="utf-8") as f:
        text = f.read()
    task_classes = _parse_task_classes(text)
    profile = _Profile(text, active_provider)
    return task_classes, profile


def _default_ssot_path() -> str:
    import os

    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(here, "..", "model-routing.yaml"))


def _baseline(task_classes: dict, profile: _Profile, task_class: str) -> dict:
    if task_class not in task_classes:
        raise RouteResolverError(f"unknown task_class '{task_class}'")
    row = task_classes[task_class]
    tier, intent = row["tier"], row["intent"]
    model_id = profile.models.get(tier)
    if model_id is None:
        raise RouteResolverError(
            f"provider '{profile.name}' has no model for tier '{tier}' "
            f"(task_class '{task_class}')"
        )
    return {"model_id": model_id, "native_effort": profile.native_effort(tier, intent)}


def _escalation_walk(profile: _Profile, current: dict) -> "dict | str":
    if profile.escalation is None:
        return "exhausted"  # explicit opt-out, never an Anthropic-shaped default
    cur_model = current.get("model_id") if current else None
    cur_effort = current.get("native_effort") if current else None
    if cur_model is None:
        return "exhausted"
    cur_tier = profile.tier_of(cur_model)
    if cur_tier is None:
        raise RouteResolverError(f"current model_id '{cur_model}' is not in provider '{profile.name}' models")

    # 1) walk the CURRENT tier's effort_ladder past the current rung.
    rungs = profile.escalation["effort_ladder"].get(cur_tier, [])
    if cur_effort in rungs:
        idx = rungs.index(cur_effort)
        if idx + 1 < len(rungs):
            return {"model_id": cur_model, "native_effort": rungs[idx + 1]}
    elif rungs and cur_effort is None:
        # No effort was set yet (tier rejects the dial or baseline omitted it) —
        # entering the ladder for the first time lands on its first rung.
        return {"model_id": cur_model, "native_effort": rungs[0]}

    # 2) current tier's effort_ladder is spent (or empty) — advance model_ladder
    # one rung, entering the NEW tier's effort_ladder at ITS FIRST rung (not its
    # intent-map level) per ARCHITECTURE.md §3. Skip a rung whose resolved
    # model_id equals the current one (tier-aliasing no-op), and skip any rung
    # BELOW the provider's absolute floor (`degrade.floor` — floor is a single
    # provider-wide concept ARCHITECTURE.md §3 says overrides "any ladder step,
    # escalation OR degrade", so an escalation model_ladder that happens to be
    # authored out of ascending order, e.g. [workhorse, cheap_fast,
    # frontier_reasoner], must not be able to escalate a task DOWN below the
    # floor. Codex adversarial review finding #2, S09 closeout — reproduced: a
    # misordered ladder let escalation land on cheap_fast with floor=workhorse).
    # A profile with no `degrade:` block (or `degrade: none`) has no floor to
    # enforce here — nothing to skip against.
    floor = None
    try:
        floor = profile.degrade.get("floor") if profile.degrade else None
    except RouteResolverError:
        floor = None  # a missing degrade: key means "no floor known" for THIS check
    model_ladder = profile.escalation["model_ladder"]
    if cur_tier not in model_ladder:
        return "exhausted"
    for nxt_tier in model_ladder[model_ladder.index(cur_tier) + 1:]:
        nxt_model = profile.models.get(nxt_tier)
        if nxt_model is None or nxt_model == cur_model:
            continue  # dead/aliased rung — keep walking
        if floor and profile.tier_rank(nxt_tier) < profile.tier_rank(floor):
            continue  # below the absolute floor — never escalate a task down to it
        nxt_rungs = profile.escalation["effort_ladder"].get(nxt_tier, [])
        return {"model_id": nxt_model, "native_effort": nxt_rungs[0] if nxt_rungs else None}
    return "exhausted"


def _degrade_walk(profile: _Profile, current: dict, signal: str) -> "dict | str":
    if profile.degrade is None:
        return "exhausted"  # explicit opt-out
    if signal not in profile.degrade["signals"]:
        raise RouteResolverError(
            f"signal '{signal}' is not in provider '{profile.name}' degrade.signals {profile.degrade['signals']!r} "
            "— that signal is not auto-rerouted for this provider (e.g. an unopted-in 'refusal' "
            "surfaces to the operator instead; see ARCHITECTURE.md §3 degrade.signals note)"
        )
    cur_model = current.get("model_id") if current else None
    if cur_model is None:
        return "exhausted"
    cur_tier = profile.tier_of(cur_model)
    if cur_tier is None:
        raise RouteResolverError(f"current model_id '{cur_model}' is not in provider '{profile.name}' models")

    floor = profile.degrade["floor"]
    if floor and profile.tier_rank(cur_tier) <= profile.tier_rank(floor):
        return "exhausted"  # already at/below the absolute floor

    target_tier = profile.degrade["ladder"].get(cur_tier)
    if target_tier is None:
        return "exhausted"
    if floor and profile.tier_rank(target_tier) < profile.tier_rank(floor):
        target_tier = floor  # floor overrides any ladder step (ARCHITECTURE.md §3)

    target_model = profile.models.get(target_tier)
    if target_model is None:
        raise RouteResolverError(f"provider '{profile.name}' has no model for degrade target tier '{target_tier}'")
    native_effort = profile.degrade["effort_on_degrade"].get(target_tier)
    return {"model_id": target_model, "native_effort": native_effort}


def resolve(
    task_class: str,
    active_provider: str,
    current: Optional[dict] = None,
    signal: str = "none",
    ssot_path: Optional[str] = None,
) -> "dict | str":
    """The ONE normative entry point (ARCHITECTURE.md §3). See module docstring.

    - signal="none", current=None  -> baseline resolve of task_class -> {tier,
      intent} -> {model_id, native_effort} for active_provider.
    - signal="failure"             -> escalation ladder walk from `current`.
    - signal in ("refusal", "entitlement", "unavailable") -> degrade ladder walk
      from `current` (raises if that signal isn't in this provider's `degrade.signals`).

    Returns {"model_id": ..., "native_effort": ...}, or the string "exhausted"
    when the relevant ladder has no further rung (opted-out profile, or already
    at the terminal/floor rung) — never a silently-borrowed default from another
    provider's shape.
    """
    if signal not in _SIGNALS:
        raise ValueError(f"signal must be one of {_SIGNALS}, got {signal!r}")
    task_classes, profile = _load(ssot_path or _default_ssot_path(), active_provider)

    if signal == "none":
        return _baseline(task_classes, profile, task_class)
    if signal == "failure":
        return _escalation_walk(profile, current or {})
    return _degrade_walk(profile, current or {}, signal)


def escalate(
    task_class: str,
    active_provider: str,
    current: dict,
    reason: Optional[str] = None,
    ssot_path: Optional[str] = None,
) -> "dict | str":
    """Convenience wrapper over resolve(..., signal='failure'). `reason` (e.g.
    "2 failures at the same root cause") is caller-side audit text only — this
    function does not evaluate whether escalation is warranted, only what the
    next rung is once the caller has already decided it is. `task_class` is
    accepted for symmetry/logging with `resolve()`'s signature but is not
    re-consulted mid-ladder — the escalation walk is driven entirely by `current`."""
    _ = reason  # audit-only, intentionally unused in logic
    return resolve(task_class, active_provider, current=current, signal="failure", ssot_path=ssot_path)


def degrade(
    task_class: str,
    active_provider: str,
    current: dict,
    signal: str = "unavailable",
    ssot_path: Optional[str] = None,
) -> "dict | str":
    """Convenience wrapper over resolve(..., signal=signal).

    NAMING NOTE (deliberately asymmetric with escalate()'s `reason` param — do
    not assume the two mean the same thing): `escalate()`'s `reason` is
    caller-side AUDIT TEXT ONLY, never consulted in logic. `degrade()`'s
    `signal` here IS the dispatch value passed straight to resolve() — it must
    be one of the active provider's declared `degrade.signals` signals (typically
    "entitlement" or "unavailable"; "refusal" only if that profile opts in, see
    ARCHITECTURE.md §3 degrade.signals note). An unrecognized signal raises
    RouteResolverError via resolve(), never silently falls through to a
    default. (Claude adversarial review finding #3, S09 closeout: the two
    wrappers' extra parameter serves categorically different jobs — control
    flow here, inert prose in escalate() — hence the different name.)"""
    return resolve(task_class, active_provider, current=current, signal=signal, ssot_path=ssot_path)


# ---------------------------------------------------------------------------
def _demo():
    """ponytail: smallest runnable self-check — not a test framework. Resolves,
    escalates, and degrades for TWO providers using the real repo SSOT, and
    proves the `degrade: none` / no-ladder path returns 'exhausted' explicitly
    rather than borrowing anthropic's shape. Run: python3 resolve_route.py"""
    ssot = _default_ssot_path()
    print(f"SSOT: {ssot}\n")
    for provider in ("anthropic", "openai"):
        print(f"=== {provider} ===")
        base = resolve("agentic_build", provider, ssot_path=ssot)
        print("  baseline agentic_build ->", base)
        esc1 = escalate("agentic_build", provider, current=base, ssot_path=ssot)
        print("  escalate x1            ->", esc1)
        esc2 = escalate("agentic_build", provider, current=esc1, ssot_path=ssot)
        print("  escalate x2            ->", esc2)
        deg = degrade("agentic_build", provider, current=base, signal="unavailable", ssot_path=ssot)
        print("  degrade (unavailable)  ->", deg)
        print()


if __name__ == "__main__":
    _demo()
