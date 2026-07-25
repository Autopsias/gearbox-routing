#!/usr/bin/env python3
"""test_resolve_route.py — unit tests for claude/scripts/resolve_route.py.

stdlib unittest only (matches the house no-extra-dependency rule this repo's
guard/renderer/scanner all follow — see resolve_route.py's own header). Run:

  python3 claude/fixtures/route-resolver/test_resolve_route.py

Covers (per the S09 session brief):
  1. resolve() baseline for each real example provider (anthropic/openai/gemini)
     against the REAL repo SSOT.
  2. An escalation rung sequence (effort ladder exhaustion -> tier advance).
  3. A degrade step (frontier_reasoner -> workhorse, floor-respecting).
  4. The "provider declares no ladder" path (escalation: none / degrade: none)
     against the fixture SSOT, plus the historical `on:` YAML-1.1 bare-key gotcha
     (the SSOT's degrade-signal key is named `signals:` to avoid it outright).
"""
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.join(_HERE, "..", "..", "scripts")
sys.path.insert(0, _SCRIPTS_DIR)

import resolve_route as rr  # noqa: E402

REAL_SSOT = os.path.normpath(os.path.join(_HERE, "..", "..", "model-routing.yaml"))
FIXTURE_SSOT = os.path.join(_HERE, "no-ladder-provider.yaml")
MALFORMED_SSOT = os.path.join(_HERE, "malformed-provider.yaml")


class TestBaselineResolveRealProviders(unittest.TestCase):
    """resolve() for each example provider against the REAL repo SSOT."""

    def test_anthropic_agentic_build(self):
        result = rr.resolve("agentic_build", "anthropic", ssot_path=REAL_SSOT)
        self.assertEqual(result, {"model_id": "claude-sonnet-5", "native_effort": "high"})

    def test_anthropic_mechanical_no_effort_dial(self):
        # cheap_fast tier is all-null for anthropic -> native_effort is None
        # (omit the dial), never a substituted value.
        result = rr.resolve("mechanical", "anthropic", ssot_path=REAL_SSOT)
        self.assertEqual(result["model_id"], "claude-haiku-4-5")
        self.assertIsNone(result["native_effort"])

    def test_openai_deep_reasoning(self):
        result = rr.resolve("deep_reasoning", "openai", ssot_path=REAL_SSOT)
        self.assertEqual(result, {"model_id": "gpt-5.5", "native_effort": "medium"})

    def test_gemini_linchpin(self):
        result = rr.resolve("linchpin", "gemini", ssot_path=REAL_SSOT)
        self.assertEqual(result, {"model_id": "gemini-3.1-pro-preview", "native_effort": "high"})

    def test_unknown_task_class_raises(self):
        with self.assertRaises(rr.RouteResolverError):
            rr.resolve("not_a_real_class", "anthropic", ssot_path=REAL_SSOT)

    def test_unknown_provider_raises(self):
        with self.assertRaises(rr.RouteResolverError):
            rr.resolve("agentic_build", "not_a_real_provider", ssot_path=REAL_SSOT)


class TestEscalationLadder(unittest.TestCase):
    """An escalation rung sequence: effort ladder exhaustion -> tier advance,
    entering the new tier's effort_ladder at ITS FIRST rung."""

    def test_anthropic_agentic_build_escalation_sequence(self):
        base = rr.resolve("agentic_build", "anthropic", ssot_path=REAL_SSOT)
        self.assertEqual(base, {"model_id": "claude-sonnet-5", "native_effort": "high"})

        rung1 = rr.escalate("agentic_build", "anthropic", current=base, ssot_path=REAL_SSOT)
        self.assertEqual(rung1, {"model_id": "claude-sonnet-5", "native_effort": "xhigh"})

        # workhorse effort_ladder is [medium, high, xhigh] -> spent. Advance to
        # frontier_reasoner at ITS FIRST rung (low), not its intent-map level.
        rung2 = rr.escalate("agentic_build", "anthropic", current=rung1, ssot_path=REAL_SSOT)
        self.assertEqual(rung2, {"model_id": "claude-opus-4-8", "native_effort": "low"})

        rung3 = rr.escalate("agentic_build", "anthropic", current=rung2, ssot_path=REAL_SSOT)
        self.assertEqual(rung3, {"model_id": "claude-opus-4-8", "native_effort": "medium"})

    def test_escalation_exhausts_at_top_rung(self):
        top = {"model_id": "claude-opus-4-8", "native_effort": "max"}
        result = rr.escalate("linchpin", "anthropic", current=top, ssot_path=REAL_SSOT)
        self.assertEqual(result, "exhausted")

    def test_escalation_first_entry_with_no_prior_effort(self):
        # current has a model but no native_effort recorded yet (e.g. a
        # no-effort-dial tier) -> escalation enters at the ladder's first rung.
        current = {"model_id": "claude-sonnet-5", "native_effort": None}
        result = rr.escalate("standard_build", "anthropic", current=current, ssot_path=REAL_SSOT)
        self.assertEqual(result, {"model_id": "claude-sonnet-5", "native_effort": "medium"})


class TestDegradeLadder(unittest.TestCase):
    """A degrade step: frontier_reasoner -> workhorse, floor-respecting,
    per-target compensation effort."""

    def test_anthropic_linchpin_degrades_to_workhorse(self):
        base = rr.resolve("linchpin", "anthropic", ssot_path=REAL_SSOT)
        self.assertEqual(base["model_id"], "claude-opus-4-8")

        degraded = rr.degrade("linchpin", "anthropic", current=base, signal="unavailable", ssot_path=REAL_SSOT)
        self.assertEqual(degraded, {"model_id": "claude-sonnet-5", "native_effort": "high"})

    def test_floor_blocks_further_degrade(self):
        # Already at the floor tier (workhorse) -> exhausted, never drops to cheap_fast.
        at_floor = {"model_id": "claude-sonnet-5", "native_effort": "high"}
        result = rr.degrade("linchpin", "anthropic", current=at_floor, signal="unavailable", ssot_path=REAL_SSOT)
        self.assertEqual(result, "exhausted")

    def test_degrade_signal_not_in_providers_on_raises(self):
        # 'refusal' is not in anthropic's degrade.signals by default (ARCHITECTURE.md
        # §3: a refused task surfaces to the operator, never auto-rerouted).
        base = rr.resolve("linchpin", "anthropic", ssot_path=REAL_SSOT)
        with self.assertRaises(rr.RouteResolverError):
            rr.degrade("linchpin", "anthropic", current=base, signal="refusal", ssot_path=REAL_SSOT)


class TestNoLadderProviderPath(unittest.TestCase):
    """The 'provider declares no ladder' path: escalation: none / degrade: none
    yields an explicit 'exhausted', never a silent Anthropic-shaped default."""

    def test_baseline_still_resolves(self):
        result = rr.resolve("agentic_build", "no_ladder_provider", ssot_path=FIXTURE_SSOT)
        self.assertEqual(result, {"model_id": "fixture-mid-1", "native_effort": "high"})

    def test_escalation_none_yields_exhausted(self):
        base = rr.resolve("agentic_build", "no_ladder_provider", ssot_path=FIXTURE_SSOT)
        result = rr.escalate("agentic_build", "no_ladder_provider", current=base, ssot_path=FIXTURE_SSOT)
        self.assertEqual(result, "exhausted")

    def test_degrade_none_yields_exhausted(self):
        base = rr.resolve("linchpin", "no_ladder_provider", ssot_path=FIXTURE_SSOT)
        result = rr.degrade("linchpin", "no_ladder_provider", current=base, signal="unavailable", ssot_path=FIXTURE_SSOT)
        self.assertEqual(result, "exhausted")

    def test_bare_on_key_read_as_string_not_boolean(self):
        """The YAML-1.1 bare-key gotcha the session brief flagged: a real YAML
        loader (e.g. PyYAML's default resolver) parses a bare `on:` key/value
        to the boolean True — the reason the SSOT's degrade-signal key is
        named `signals:` rather than `on:`. This resolver never uses a YAML
        loader either way — it regex-matches the literal text — so
        `bare_signal_provider`'s `degrade.signals: [entitlement, unavailable]`
        must be read as those two neutral strings, proven here by successfully
        walking the ladder for the 'unavailable' signal (would KeyError/
        silently no-op if the key had been coerced to a boolean elsewhere in
        parsing)."""
        base = rr.resolve("linchpin", "bare_signal_provider", ssot_path=FIXTURE_SSOT)
        result = rr.degrade("linchpin", "bare_signal_provider", current=base, signal="unavailable", ssot_path=FIXTURE_SSOT)
        self.assertEqual(result, {"model_id": "fixture-mid-2", "native_effort": "high"})

        # A signal NOT in this provider's degrade.signals must still raise, proving
        # the `signals` list is being checked as real content, not silently empty
        # (which would make every signal reachable).
        with self.assertRaises(rr.RouteResolverError):
            rr.degrade("linchpin", "bare_signal_provider", current=base, signal="refusal", ssot_path=FIXTURE_SSOT)


class TestMissingLadderKeyRaises(unittest.TestCase):
    """A GENUINELY MISSING `escalation:`/`degrade:` key (no key at all — as
    opposed to the explicit opt-out literal `none`) must raise
    RouteResolverError, per ARCHITECTURE.md §3: "a missing block is a
    validation error, not a default." Added post-review (Claude adversarial
    review finding #2, S09 closeout) after the initial cut of this module
    treated a missing key identically to an explicit `none` opt-out."""

    def test_baseline_resolve_unaffected_by_missing_ladder_keys(self):
        # A missing escalation:/degrade: key must NOT break plain baseline
        # resolution — only escalate()/degrade() should ever raise for it
        # (lazy parsing; see _Profile's escalation/degrade properties).
        result = rr.resolve("agentic_build", "missing_ladder_key_provider", ssot_path=FIXTURE_SSOT)
        self.assertEqual(result, {"model_id": "fixture-mid-3", "native_effort": "high"})

    def test_escalate_raises_on_missing_escalation_key(self):
        base = rr.resolve("agentic_build", "missing_ladder_key_provider", ssot_path=FIXTURE_SSOT)
        with self.assertRaises(rr.RouteResolverError):
            rr.escalate("agentic_build", "missing_ladder_key_provider", current=base, ssot_path=FIXTURE_SSOT)

    def test_degrade_raises_on_missing_degrade_key(self):
        base = rr.resolve("agentic_build", "missing_ladder_key_provider", ssot_path=FIXTURE_SSOT)
        with self.assertRaises(rr.RouteResolverError):
            rr.degrade("agentic_build", "missing_ladder_key_provider", current=base,
                       signal="unavailable", ssot_path=FIXTURE_SSOT)


class TestTierRankUnrecognizedTierRaises(unittest.TestCase):
    """_Profile.tier_rank() must raise on a tier name it doesn't recognize
    rather than silently defaulting it to an edge rank (Claude adversarial
    review finding #4, S09 closeout: a `.get(tier, sentinel)` lookup would
    have silently mis-ranked an unrecognized tier instead of failing loudly)."""

    def test_unrecognized_tier_in_floor_comparison_raises(self):
        import os as _os
        here = _os.path.dirname(_os.path.abspath(__file__))
        ssot_path = FIXTURE_SSOT
        with open(ssot_path, encoding="utf-8") as f:
            text = f.read()
        profile = rr._Profile(text, "no_ladder_provider")
        with self.assertRaises(rr.RouteResolverError):
            profile.tier_rank("not_a_real_tier")

    def test_recognized_tier_ranks_in_declared_order(self):
        with open(FIXTURE_SSOT, encoding="utf-8") as f:
            text = f.read()
        profile = rr._Profile(text, "no_ladder_provider")
        self.assertLess(profile.tier_rank("cheap_fast"), profile.tier_rank("workhorse"))
        self.assertLess(profile.tier_rank("workhorse"), profile.tier_rank("frontier_reasoner"))


class TestCodexReviewRegressions(unittest.TestCase):
    """Regression tests for the two HIGH findings Codex's adversarial-review
    pass caught in S09's closeout review (2026-07-05) — see
    malformed-provider.yaml's header comment for full detail on each."""

    def test_effort_map_missing_standard_raises_not_silent_none(self):
        # Codex finding #1: a non-null workhorse map missing `standard` used to
        # silently resolve to native_effort=None for a `standard` intent task
        # — indistinguishable from a legitimate no-effort-dial tier. Must raise.
        with self.assertRaises(rr.RouteResolverError):
            rr.resolve("standard_build", "missing_standard_provider", ssot_path=MALFORMED_SSOT)

    def test_escalation_never_lands_below_absolute_floor(self):
        # Codex finding #2: a misordered model_ladder ([workhorse, cheap_fast,
        # frontier_reasoner]) used to let escalation step DOWN to cheap_fast
        # once workhorse's effort_ladder was spent, violating floor=workhorse
        # (ARCHITECTURE.md §3: floor overrides ANY ladder step, escalation or
        # degrade). Must skip the below-floor rung and land on frontier_reasoner.
        base = rr.resolve("standard_build", "misordered_ladder_provider", ssot_path=MALFORMED_SSOT)
        self.assertEqual(base, {"model_id": "m-mid-2", "native_effort": "medium"})
        escalated = rr.escalate("standard_build", "misordered_ladder_provider", current=base, ssot_path=MALFORMED_SSOT)
        # frontier_reasoner has no effort_ladder row in this fixture, so the
        # ladder-entry rung is None (empty ladder) — the load-bearing assertion
        # is the MODEL: it must be frontier_reasoner, never the below-floor
        # cheap_fast the pre-fix code would have returned here.
        self.assertEqual(escalated["model_id"], "m-frontier-2")
        self.assertNotEqual(escalated["model_id"], "m-cheap-2", "escalation must never land below the absolute floor")


if __name__ == "__main__":
    unittest.main(verbosity=2)
