#!/usr/bin/env python3
"""resolve_route.py — the vendored, provider-neutral route + fallback-ladder resolver.

The harness's own resolver: a superset of claude/scripts/resolve_route.py, with
four deliberate deviations from it:
  1. task-class block name: reads `task_classes_v2:` (the {tier, effort-intent}
     shape) FIRST, falling back to `task_classes:`, so renaming the block to
     `task_classes:` once the legacy flat {model, effort} rows retire needs no
     resolver edit.
  2. tier ordering is PROVIDER-DECLARED: `tier_rank()` derives ordering from the
     profile's own `models:` key declaration order (ascending), never from a
     hard-coded tier list. A hard-coded three-tier list raised
     `RouteResolverError: tier '<4th tier>' is not a recognized tier` on a
     four-tier profile even with the tier present in profile.models.
  3. PER-PROVIDER TASK-CLASS MAP: a provider profile MAY declare its own
     `task_classes:` block (indent 4, same {tier, effort-intent} row shape as the
     neutral top-level block). Its rows OVERRIDE the neutral ones for that
     provider only; a profile that declares none resolves EXACTLY as before.
     Needed because the two line-ups are not parallel — OpenAI's top tier is its
     everyday judgement default while Anthropic's (fable) is escalation-only. See
     `_parse_provider_task_classes` and `_load`.
  4. `escalation.model_ladder_entry` (the ONE authorized semantic change to this
     shared funnel): an OPTIONAL per-tier flow map naming the effort rung at which
     the model ladder ENTERS a tier. Without it the walk always entered a new tier
     at its FIRST effort rung, which made the mandated rework climb
     inexpressible: a stuck opus@high session escalated to fable@LOW — WEAKER than
     the rung just left. With the key: sonnet@high -> opus@high -> opus@xhigh ->
     fable@medium -> fable@high -> fable@xhigh. A profile declaring no
     `model_ladder_entry` resolves BYTE-IDENTICALLY to the old walk
     (regression-tested on both providers). See `_escalation_walk` step 2 for the
     normative wording and `_Profile.escalation` for the validation, which mirrors
     verify-routing.sh check (0b).
Acceptance test: scripts/test_resolve_route.py (baseline pin byte-identity + the
four-tier fable→opus→sonnet degrade walk + escalation/floor behavior + the
per-provider map, including that an unknown class name raises).

Turns `(task_class, active_provider)` into a concrete `{model_id, native_effort}`,
and — on a CALLER-SUPPLIED signal (this module detects nothing itself; see
ARCHITECTURE.md §3 "Resolver contract") — walks that provider's escalation ladder
on `failure` or degrade ladder on `entitlement`/`unavailable`/an opted-in `refusal`.

This is the REFERENCE implementation the schema in ARCHITECTURE.md §3 is tested
against. It reads ONLY `~/.claude/model-routing.yaml` (`task_classes_v2:` +
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
      the `EXHAUSTED` sentinel (the string "exhausted") when no rung remains
      (ladder opted out, or already at the terminal rung). This is the ONE
      normative entry point — see ARCHITECTURE.md §3 for the full contract
      (baseline / escalation-walk / degrade-walk semantics) and
      docs/INTEGRATION.md §1 for the exact call shape other consumers (e.g.
      render-routing-digest.py) already depend on.

      `current` ACCEPTS ANYTHING THIS RETURNS, including `EXHAUSTED` — the retry
      shape is a loop that feeds the last answer back in, so the terminal state
      is absorbing rather than a crash (see the `EXHAUSTED` docstring).

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

EXHAUSTED = "exhausted"
"""The terminal-rung sentinel every ladder walk returns, and a FIXPOINT of
`resolve()`: feeding it straight back in returns it again, unchanged.

Why that matters: the documented retry shape is a loop —
`cur = escalate(..., current=cur)` until the ladder ends — so the caller WILL feed
the previous return value back in, and before this contract existed the sentinel
(a bare `str`) hit `current.get(...)` and raised `AttributeError`. That was a
latent crash for ANY caller sitting at a ceiling under ANY provider, not an
openai quirk: anthropic reaches it too the moment `fable@xhigh` escalates. Making
the terminal state absorbing — rather than special-casing whichever profile
happened to expose it — is the contract fix; see `resolve()`'s guard.

Public because callers should compare against this name, not re-type the literal
(`if r == resolve_route.EXHAUSTED`). The VALUE is frozen: pre-existing consumers
compare to the string "exhausted" (scripts/test_resolve_route.py, run.py's
NO-CODEX note), so this constant renames nothing on the wire."""
# PORT NOTE (four-tier fix): claude/scripts/resolve_route.py defines a hard-coded
# 3-tier `_TIERS = ("cheap_fast", "workhorse", "frontier_reasoner")` and prefers
# it as tier_rank()'s ordering source, which raises on any profile carrying a
# tier outside that set (e.g. a fourth, top tier). The constant is
# REMOVED: tier ordering is provider-declared — a profile's `models:` keys MUST
# be listed in ascending capability order, and tier_rank() reads that order
# (see the `providers:` block comment in model-routing.yaml).


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


def _unquote(v: str) -> str:
    """Strip one layer of YAML scalar quoting. The regex grammar here accepts
    BOTH bare and quoted scalars so a harmless formatting edit (`high` ->
    `"high"`) cannot silently drop a key and fall back to a different effort
    (found in an adversarial review — reproduced fail-open)."""
    return v.strip().strip("\"'")


def _find_scalar(block: str, key: str) -> Optional[str]:
    m = re.search(rf"^\s*{re.escape(key)}:\s*(\S+)", block, re.M)
    return _unquote(m.group(1).rstrip(",")) if m else None


def _find_list(block: str, key: str) -> Optional[list]:
    """`key: [a, b]` on one line, or a `- item` block below `key:`."""
    m = re.search(rf"^\s*{re.escape(key)}:\s*\[([^\]]*)\]", block, re.M)
    if m:
        return [_unquote(x) for x in m.group(1).split(",") if x.strip()]
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
    pairs = dict(re.findall(r"[\"']?([\w.-]+)[\"']?:\s*[\"']?([\w.-]+)[\"']?", m.group(1)))
    return pairs or None


def _is_none_literal(block_or_scalar: Optional[str]) -> bool:
    """A profile block that is literally the string 'none' (opt-out), e.g.
    `escalation: none` / `degrade: none`."""
    return isinstance(block_or_scalar, str) and block_or_scalar.strip() == "none"


_TASK_CLASS_ROW_RX = re.compile(
    r"^\s*([\w-]+):\s*\{\s*tier:\s*[\"']?([\w-]+)[\"']?\s*,\s*effort:\s*[\"']?(\w+)[\"']?\s*\}", re.M
)


def _parse_task_classes(ssot_text: str) -> dict:
    """The NEUTRAL, top-level task-class map — the DEFAULT for every provider.

    PORT NOTE: prefers `task_classes_v2:` (the {tier, effort-intent} shape, added
    while legacy flat rows still hold the name `task_classes:`), falling back to
    `task_classes:` so that the later rename (v2 → task_classes, legacy retired)
    needs no resolver edit."""
    for block_name in ("task_classes_v2", "task_classes"):
        if ssot_text.find(f"\n{block_name}:\n") == -1:
            continue
        block = _sub_block(ssot_text, block_name, 0) or ""
        parsed = {mo.group(1): {"tier": mo.group(2), "intent": mo.group(3)}
                  for mo in _TASK_CLASS_ROW_RX.finditer(block)}
        if parsed:
            return parsed
    raise RouteResolverError(
        "parsed zero {tier, effort} rows from SSOT `task_classes_v2:`/`task_classes:` — parser or SSOT broken"
    )


def _parse_provider_task_classes(provider_block: str) -> dict:
    """OPTIONAL per-provider task-class map — same {tier, effort-INTENT}
    row shape as the neutral block, declared at indent 4 inside a provider profile.

    ABSENT  -> that provider uses the neutral rows verbatim (the original behaviour,
               byte-for-byte; providers.anthropic declares none, so no Claude
               route moved when this landed).
    PRESENT -> its rows OVERRIDE the neutral ones for THAT PROVIDER only, class by
               class (`{**neutral, **provider}` — see `_load`).

    WHY THE AXIS SPLITS: the two line-ups are not parallel. OpenAI's TOP tier
    is its EVERYDAY judgement default, while Anthropic's TOP tier (fable) is
    escalation-apex-only — one
    shared tier per task class cannot be right for both. Splitting the axis also
    lets two classes that share a neutral cell (agentic_build / linchpin) take
    different intents under one provider.

    A DECLARED-BUT-UNPARSEABLE block RAISES rather than returning {}: silently
    falling back to the neutral rows is exactly the fail-open this schema keeps
    closing — a typo'd row would route judgement work somewhere nobody chose."""
    block = _sub_block(provider_block, "task_classes", 4)
    if block is None:
        return {}
    parsed = {mo.group(1): {"tier": mo.group(2), "intent": mo.group(3)}
              for mo in _TASK_CLASS_ROW_RX.finditer(block)}
    if not parsed:
        raise RouteResolverError(
            "provider declares a `task_classes:` block but ZERO {tier, effort} rows parsed from it — "
            "an unparseable override would silently fall back to the neutral rows"
        )
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
    return {k: _unquote(v) for k, v in re.findall(r"^\s+([\w-]+):\s*(\S+)", models_slice, re.M)}


def _parse_effort_map(provider_block: str, tiers) -> dict:
    effort_slice = _sub_block(provider_block, "effort", 4) or ""
    map_slice = _sub_block(effort_slice, "map", 6) or ""
    effort_map = {}
    for tier in tiers:
        row = re.search(rf"^\s*{re.escape(tier)}:\s*\{{([^}}]*)\}}", map_slice, re.M)
        if row:
            pairs = dict(re.findall(r"(\w+):\s*[\"']?(null|\w+)[\"']?", row.group(1)))
            effort_map[tier] = {k: (None if v == "null" else v) for k, v in pairs.items()}
    return effort_map


def _parse_escalation(provider_block: str) -> Optional[dict]:
    """None => opted out via the EXPLICIT literal `escalation: none`. Otherwise
    {"effort_ladder": {tier: [lvl,...]}, "model_ladder": [tier,...],
     "model_ladder_entry": {tier: lvl}}.

    `model_ladder_entry` (the ONE authorized semantic
    extension to this funnel) is OPTIONAL and defaults to `{}`: the effort rung
    at which the model ladder ENTERS a tier. Absent for a tier => the historical
    enter-at-the-first-effort-rung behaviour, byte-identical. See
    `_escalation_walk` step 2 for the contract and why the key exists.

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
        effort_ladder[tier] = [_unquote(x) for x in items.split(",") if x.strip()]
    model_ladder = _find_list(block, "model_ladder") or []
    # Single-line flow map, same shape (and same one-line scoping guarantee) as
    # `degrade.ladder`. A profile that declares none gets `{}`.
    entry = _find_flow_map(block, "model_ladder_entry") or {}
    if not effort_ladder and not model_ladder:
        return None
    return {
        "effort_ladder": effort_ladder,
        "model_ladder": model_ladder,
        "model_ladder_entry": entry,
    }


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
    """Everything resolved from one provider's SSOT block.

    EXTERNAL CONSUMER PIN: ~/.claude/skills/plan-execute/scripts/run.py
    imports `_Profile` (plus `_parse_provider_block`/`_sub_block`/`_find_scalar`)
    for its codex-wrapper dispatch resolution — executable pin in its
    test_codex_dispatch.py. Renaming/restructuring these requires updating that
    consumer in the same commit.

    `models`/`effort_map`
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

    __slots__ = ("name", "models", "effort_map", "task_classes", "_block", "_escalation", "_degrade")

    _UNSET = object()

    def __init__(self, ssot_text: str, provider: str):
        block = _parse_provider_block(ssot_text, provider)
        self.name = provider
        self.models = _parse_models(block)
        self.effort_map = _parse_effort_map(block, self.models.keys())
        # `{}` when the profile declares no override — the neutral rows then
        # apply unchanged. Eager (like models/effort_map) because every baseline
        # resolve consults it.
        self.task_classes = _parse_provider_task_classes(block)
        self._block = block
        self._escalation = self._UNSET
        self._degrade = self._UNSET

    @property
    def escalation(self) -> Optional[dict]:
        if self._escalation is self._UNSET:
            esc = _parse_escalation(self._block)
            # Ladder-vs-declaration consistency (found in an adversarial
            # review — reproduced: swapping two `models:` declarations
            # silently changed fallback routes). The SSOT contracts `models:`
            # key order as ascending capability; a model_ladder that walks that
            # order out of sequence means one of the two is wrong — fail loudly.
            if esc:
                unknown = [t for t in esc["model_ladder"] if t not in self.models]
                if unknown:
                    raise RouteResolverError(
                        f"provider '{self.name}' escalation.model_ladder names tier(s) {unknown} "
                        f"not declared under models: {list(self.models)}"
                    )
                idxs = [list(self.models).index(t) for t in esc["model_ladder"]]
                if idxs != sorted(idxs):
                    raise RouteResolverError(
                        f"provider '{self.name}' escalation.model_ladder {esc['model_ladder']} is not in "
                        f"the models: declaration order {list(self.models)} — models: keys MUST be "
                        "declared ascending and the ladder must walk that order (tier ordering is "
                        "provider-declared; a mismatch silently rewires fallback routes)"
                    )
                # Same consistency contract for `model_ladder_entry`: every
                # key must be a declared tier, and every VALUE must be a rung of
                # THAT tier's own effort_ladder. An entry rung outside the ladder
                # would leave the climb standing on a level the ladder cannot walk
                # off (step 1 below finds no index for it and falls straight through
                # to the model ladder), silently skipping the rest of the tier. This
                # mirrors verify-routing.sh check (0b) — the guard and the resolver
                # enforce the same rule, deliberately, because either one alone can
                # be bypassed (the guard by an unguarded caller, the resolver by a
                # profile nobody resolves through in CI).
                for _tier, _rung in (esc.get("model_ladder_entry") or {}).items():
                    if _tier not in self.models:
                        raise RouteResolverError(
                            f"provider '{self.name}' escalation.model_ladder_entry names tier "
                            f"'{_tier}' not declared under models: {list(self.models)}"
                        )
                    _rungs = esc["effort_ladder"].get(_tier, [])
                    if _rung not in _rungs:
                        raise RouteResolverError(
                            f"provider '{self.name}' escalation.model_ladder_entry.{_tier} = "
                            f"'{_rung}' is not a rung of that tier's effort_ladder {_rungs!r} — "
                            "the climb would enter on a level the ladder cannot walk off"
                        )
            self._escalation = esc
        return self._escalation

    @property
    def degrade(self) -> Optional[dict]:
        if self._degrade is self._UNSET:
            deg = _parse_degrade(self._block)
            # Same consistency contract for the degrade ladder: every source and
            # target tier (and the floor) must be a declared tier, and every step
            # must go DOWNWARD in the declared order.
            if deg:
                names = set(deg["ladder"]) | set(deg["ladder"].values()) | ({deg["floor"]} if deg["floor"] else set())
                unknown = sorted(n for n in names if n not in self.models)
                if unknown:
                    raise RouteResolverError(
                        f"provider '{self.name}' degrade block names tier(s) {unknown} "
                        f"not declared under models: {list(self.models)}"
                    )
                order = list(self.models)
                # RESCUE EXCEPTION: the step out of the BOTTOM tier
                # (declaration index 0) may point upward. That is not a degrade —
                # there is by definition nothing below it to drop to — it is a
                # rescue off a bottom tier that refused. `_degrade_walk` already
                # returns "exhausted" for any current tier at/below `floor`, so
                # allowing it here changes ZERO resolutions; it only stops a
                # legitimate SSOT from failing to PARSE. Without it,
                # providers.openai's `cheap_fast: workhorse` rescue edge (added for
                # the bottom tier, which otherwise had a NULL fallback on
                # refusal) made EVERY openai degrade() call raise — the profile was
                # unresolvable, not merely un-walked.
                # DELIBERATELY keyed on index 0, NOT on `floor`: a floor-relative
                # exemption would also swallow a genuinely MISORDERED models:
                # declaration (the failure mode the acceptance test's swapped-tier
                # fixture exists to catch), because a misorder moves the floor too.
                bad = {
                    s: t for s, t in deg["ladder"].items()
                    if order.index(t) >= order.index(s) and order.index(s) != 0
                }
                if bad:
                    raise RouteResolverError(
                        f"provider '{self.name}' degrade.ladder step(s) {bad} do not go DOWNWARD in the "
                        f"models: declaration order {order} — either the ladder or the declaration order is wrong"
                    )
            self._degrade = deg
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
        THIS PROFILE's own `models:` key declaration order — the SSOT contracts
        that order to be ascending capability (see the `providers:` block
        comment in model-routing.yaml). PORT NOTE (four-tier fix): the Gearbox
        original preferred a hard-coded 3-tier `_TIERS` ordering here, which
        raised on a four-tier profile's extra tier even when that tier was
        present in profile.models — the ordering source is now purely
        provider-declared. Raises RouteResolverError on a tier name this
        profile doesn't recognize at all, rather than silently defaulting it
        to an edge rank the way a `.get(tier, sentinel)` lookup would (Claude
        adversarial review finding #4, S09 closeout)."""
        if tier in self.models:
            return list(self.models).index(tier)
        raise RouteResolverError(
            f"tier '{tier}' is not a recognized tier for provider '{self.name}' "
            f"(known tiers: {sorted(self.models)}) — cannot compare it against a floor/ceiling"
        )


def _load(ssot_path: str, active_provider: str):
    """Returns (EFFECTIVE task-class map, profile).

    Effective = the neutral rows with this provider's own `task_classes:`
    rows layered on top, class by class. A provider that declares none gets the
    neutral map object's contents unchanged.

    The class VOCABULARY stays neutral-owned: a per-provider row may only re-point
    a class the neutral block already declares. A name that isn't there is a hard
    error, never a silent no-op — otherwise `standard_buld:` would look encoded and
    resolve through the neutral row forever."""
    with open(ssot_path, encoding="utf-8") as f:
        text = f.read()
    neutral = _parse_task_classes(text)
    profile = _Profile(text, active_provider)
    unknown = sorted(c for c in profile.task_classes if c not in neutral)
    if unknown:
        raise RouteResolverError(
            f"providers.{active_provider}.task_classes names class(es) {unknown} absent from the neutral "
            f"`task_classes:` block {sorted(neutral)} — the class vocabulary is provider-NEUTRAL; an "
            "unknown name here would silently resolve through the neutral row instead of overriding it"
        )
    return {**neutral, **profile.task_classes}, profile


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
        return EXHAUSTED  # explicit opt-out, never an Anthropic-shaped default
    cur_model = current.get("model_id") if current else None
    cur_effort = current.get("native_effort") if current else None
    if cur_model is None:
        return EXHAUSTED
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
    # one rung, entering the NEW tier's effort_ladder at the rung
    # `escalation.model_ladder_entry` names for it, falling back to ITS FIRST rung
    # (never its intent-map level) when the profile declares no entry for that
    # tier. ARCHITECTURE.md §3 "Escalation walk" — AMENDED by the one authorized semantic
    # extension to this funnel; the original contract was enter-at-first-rung
    # unconditionally,
    # and a profile declaring no `model_ladder_entry` still resolves exactly that
    # way, byte for byte.
    #
    # WHY THE KEY EXISTS (it is not cosmetic): enter-at-first-rung sends a stuck
    # opus@high session to fable@LOW — a rung nobody would elect on purpose, and
    # WEAKER than the one the session just left, i.e. an "escalation" that
    # descends. The OpenAI lane has the same defect one tier lower.
    # The entry key makes the mandated Anthropic climb
    # expressible end to end: sonnet@high -> opus@HIGH -> fable@MEDIUM ->
    # fable@high -> fable@xhigh -> exhausted.
    #
    # Validated in `_Profile.escalation` (declared tier + a real rung of that
    # tier's ladder), so the lookup here can be a plain `.get` without a second
    # correctness story. Skip a rung whose resolved
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
        return EXHAUSTED
    for nxt_tier in model_ladder[model_ladder.index(cur_tier) + 1:]:
        nxt_model = profile.models.get(nxt_tier)
        if nxt_model is None or nxt_model == cur_model:
            continue  # dead/aliased rung — keep walking
        if floor and profile.tier_rank(nxt_tier) < profile.tier_rank(floor):
            continue  # below the absolute floor — never escalate a task down to it
        nxt_rungs = profile.escalation["effort_ladder"].get(nxt_tier, [])
        entry = (profile.escalation.get("model_ladder_entry") or {}).get(nxt_tier)
        return {
            "model_id": nxt_model,
            "native_effort": entry or (nxt_rungs[0] if nxt_rungs else None),
        }
    return EXHAUSTED


def _degrade_walk(profile: _Profile, current: dict, signal: str) -> "dict | str":
    if profile.degrade is None:
        return EXHAUSTED  # explicit opt-out
    if signal not in profile.degrade["signals"]:
        raise RouteResolverError(
            f"signal '{signal}' is not in provider '{profile.name}' degrade.signals {profile.degrade['signals']!r} "
            "— that signal is not auto-rerouted for this provider (e.g. an unopted-in 'refusal' "
            "surfaces to the operator instead; see ARCHITECTURE.md §3 degrade.signals note)"
        )
    cur_model = current.get("model_id") if current else None
    if cur_model is None:
        return EXHAUSTED
    cur_tier = profile.tier_of(cur_model)
    if cur_tier is None:
        raise RouteResolverError(f"current model_id '{cur_model}' is not in provider '{profile.name}' models")

    floor = profile.degrade["floor"]
    if floor and profile.tier_rank(cur_tier) <= profile.tier_rank(floor):
        return EXHAUSTED  # already at/below the absolute floor

    target_tier = profile.degrade["ladder"].get(cur_tier)
    if target_tier is None:
        return EXHAUSTED
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
    current: "Optional[dict | str]" = None,
    signal: str = "none",
    ssot_path: Optional[str] = None,
) -> "dict | str":
    """The ONE normative entry point (ARCHITECTURE.md §3). See module docstring.

    - signal="none", current=None  -> baseline resolve of task_class -> {tier,
      intent} -> {model_id, native_effort} for active_provider.
    - signal="failure"             -> escalation ladder walk from `current`.
    - signal in ("refusal", "entitlement", "unavailable") -> degrade ladder walk
      from `current` (raises if that signal isn't in this provider's `degrade.signals`).

    Returns {"model_id": ..., "native_effort": ...}, or `EXHAUSTED` when the
    relevant ladder has no further rung (opted-out profile, or already at the
    terminal/floor rung) — never a silently-borrowed default from another
    provider's shape.

    `current` accepts what this function RETURNS — a rung dict, `None`, or
    `EXHAUSTED` itself, which is absorbing (see the EXHAUSTED docstring). Any
    other type is a caller bug and raises rather than being coerced.
    """
    if signal not in _SIGNALS:
        raise ValueError(f"signal must be one of {_SIGNALS}, got {signal!r}")

    # CLOSED-LOOP CONTRACT — guard here, in the shared funnel, NOT in
    # each walk: `escalate()`/`degrade()` are thin wrappers over this call, so one
    # check covers all three public entry points and they cannot drift apart. A
    # ladder that has ended stays ended; asking it to move again is not an error,
    # it is the same answer. (Anything OTHER than a rung dict or the sentinel is a
    # different thing entirely — a caller passing a bare model_id string, say —
    # and must fail loudly, which is why this is not a blanket `isinstance` coerce.)
    if current == EXHAUSTED:
        return EXHAUSTED
    if current is not None and not isinstance(current, dict):
        raise RouteResolverError(
            f"`current` must be a rung dict, None, or the {EXHAUSTED!r} sentinel — got "
            f"{type(current).__name__} {current!r}"
        )

    task_classes, profile = _load(ssot_path or _default_ssot_path(), active_provider)

    if signal == "none":
        return _baseline(task_classes, profile, task_class)
    if signal == "failure":
        return _escalation_walk(profile, current or {})
    return _degrade_walk(profile, current or {}, signal)


def escalate(
    task_class: str,
    active_provider: str,
    current: "dict | str",
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


EFFORTS = ("low", "medium", "high", "xhigh", "max")  # run.py's --reasoning order, weakest first


def rank(profile: _Profile, cell: dict) -> Optional[tuple]:
    """(tier rank, effort rank) of `cell` on `profile`'s ladder: tiers by `models:` order
    (the model_ladder is validated to walk it), efforts by EFFORTS, no dial lowest; so
    every opus cell outranks every sonnet cell. None when its model is not on this
    provider's ladder or its effort is not a known level."""
    tier = profile.tier_of(str(cell.get("model_id") or "").strip().lower())
    effort = cell.get("native_effort")
    effort = effort.strip().lower() if isinstance(effort, str) else effort
    if tier is None or (effort is not None and effort not in EFFORTS):
        return None
    return (profile.tier_rank(tier), EFFORTS.index(effort) if effort else -1)


def below_floor(task_class: str, provider: str, cell: dict, ssot_path: Optional[str] = None) -> bool:
    """Route-at-dispatch contract section 4: True when `cell`
    ranks strictly LOWER than the class default on `provider`'s ladder. Not "escalate()
    reaches the default": the walk from sonnet@high skips deep_reasoning's opus@medium.
    Unrankable cells and ladderless providers are not below; the cross-provider skip is
    the caller's (only it knows the tree's provider)."""
    task_classes, profile = _load(ssot_path or _default_ssot_path(), provider)
    if profile.escalation is None:
        return False
    mine = rank(profile, cell)
    floor = rank(profile, _baseline(task_classes, profile, task_class))
    return mine is not None and floor is not None and mine < floor


def degrade(
    task_class: str,
    active_provider: str,
    current: "dict | str",
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


if __name__ == "__main__":
    from resolve_route_demo import _demo  # the self-check lives beside this file

    _demo()
