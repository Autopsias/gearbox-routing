#!/usr/bin/env python3
"""Statistical policy constants and path anchors for the outcome aggregator.

A leaf module: aggregate_outcomes.py and both of its halves read these, and
routing them through any one of the three would make the import graph a cycle.
Nothing here imports a sibling except retro_scan, which is itself a leaf.

These constants are the aggregator's OWN policy. Class defaults and prices are
read live from model-routing.yaml and are never hardcoded — see
class_defaults_from_ssot in aggregate_outcomes.py.
"""
import sys
from pathlib import Path

# --------------------------------------------------------------------------
# Constants — the aggregator's own statistical policy (NOT SSOT-sourced; class
# defaults and prices ARE read live from model-routing.yaml, never hardcoded —
# see class_defaults_from_ssot / _prices below).
# --------------------------------------------------------------------------
RESULTS = {"passed", "rework", "exhausted", "blocked", "done_unverified", "wontfix"}
PROPOSAL_ELIGIBLE_RESULTS = {"passed", "exhausted"}       # gate-checked outcomes
EXCLUDED_TERMINAL_RESULTS = {"blocked", "done_unverified", "wontfix"}

MIN_N_UPGRADE = 6
UPGRADE_PASS_MAX = 0.60
UPGRADE_ESCALATION_MIN = 0.50

MIN_N_DOWNGRADE_OPEN = 6            # deliberate call — see module docstring
DOWNGRADE_OPEN_PASS_MIN = 0.90
DOWNGRADE_OPEN_ESCALATION_MAX = 0.0

SMOKE_N = 3
ADOPTION_MIN_N = 10
ADOPTION_PASS_MIN = 0.90
ADOPTION_ATTEMPTS_PER_SUCCESS_MAX = 1.1

# OPERATOR DECISION, 2026-08-15. A record whose `model_ran_source` is "requested"
# counts as evidence: we accept that the model asked for is the model that ran.
#
# The reasoning, recorded because a future reader will otherwise re-litigate it:
# a downgrade that ACTUALLY happens is already recorded — the orchestrator writes
# `degraded_from` whenever it observes a refusal and re-dispatches lower, and the
# cohort is attributed to what RAN. The only unrecorded case is SILENT inheritance,
# where Claude Code honours a blocked model override by quietly falling back and
# reporting no error. That is rare, and in this lineup it is nearly always
# fable -> opus, one rung. The operator judged that acceptable rather than leave
# the whole learning half inert waiting on an attestation source that does not
# exist for in-session dispatch (and cannot exist today for the Codex lane).
#
# What is still REFUSED: `unknown`. A record that cannot even name what was asked
# for is not evidence of anything, and counting it would be the "check that cannot
# fail" defect this plan spent itself finding.
#
# `attested_share` is still computed and reported per cell, so the day a real
# attestation source lands you can see the mix improve instead of guessing.
ADMISSIBLE_SOURCES = ("attested", "requested")

MALFORMED_MAX_LINES = 3             # >= this many -> abort
MALFORMED_MAX_RATIO = 0.02          # > this ratio -> abort

APEX_REVISIT_THRESHOLD = 5

# [HARDENED finding 6] cost_per_success reads each attempt's `usage.cost_usd`
# (route-at-dispatch contract rule 6, written by plan-execute's outcomes.py
# since s07). It reads "n/a" unless EVERY attempt in the pool carries a cost —
# and every Agent-tool transcript measured in s07 had cache-creation tokens,
# which model_prices cannot price, so most cells read "n/a". Said plainly in the
# `cost_per_success_caveat` output field rather than left to look like no data.
COST_PER_SUCCESS_CAVEAT = (
    "cost_per_success reads usage.cost_usd from ledger records and is n/a unless "
    "every attempt in the cell carries a cost: cache-creation tokens (on every "
    "Agent-tool transcript measured) have no model_prices rate, so those attempts stay unpriced."
)


# --------------------------------------------------------------------------
# Paths (same anchor pattern as skills/plan-execute/scripts/outcomes.py and
# scripts/resolve_route.py — this file lives at <root>/skills/routing-retro/
# scripts/, same depth as skills/plan-execute/scripts/, so parents[3] == root).
# --------------------------------------------------------------------------
def _root():
    return Path(__file__).resolve().parents[3]


def default_ledger_path():
    return _root() / "evals" / "routing" / "outcomes.ndjson"


def default_ssot_path():
    return _root() / "model-routing.yaml"


def default_adoptions_path():
    return default_ledger_path().parent / "adoptions.ndjson"


def stamp_path(ledger_path):
    return Path(ledger_path).parent / ".last-aggregated"


def _import_resolver():
    """Import scripts/resolve_route.py — same lazy-sys.path trick outcomes.py's
    ``_run._import_resolver`` uses."""
    scripts_dir = str(_root() / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    import resolve_route  # noqa: PLC0415

    return resolve_route



def _norm_reasoning(v):
    """None and "" both mean "no effort dial" (mechanical/haiku classes have no
    reasoning rung) — normalize to "" on BOTH sides that build a cell key.
    resolve_route's native_effort reports None for a dial-less class; the
    ledger writer's run.py::_reasoning_tier normalizes an unset dial to "" —
    without this, ("mechanical", "haiku", None, ...) and
    ("mechanical", "haiku", "", ...) never match and the class reports
    current_n: 0 / no_data forever, even with real cohorts. [HARDENED finding 2]"""
    return v if v else ""
