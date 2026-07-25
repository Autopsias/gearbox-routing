"""Tests for gate_policy — the per-gate block vs notify-and-continue resolver (OR-03).

The whole safety story is fail-closed: the DEFAULT is block, and
notify-and-continue is granted ONLY for an evidence-derived allowlist of gate
TYPES that also satisfy every guard. These tests lock that down so a future edit
can't silently widen auto-continue.
"""

import gate_policy as gp

ELIGIBLE = "session_review_ack"


# ---- the happy path: an eligible, opted-in, non-checkpoint gate --------------


def test_eligible_type_default_blocks_without_optin():
    # Conservative default: an eligible type still BLOCKS until an author
    # explicitly opts in. Being on the allowlist only makes it eligible.
    assert gp.resolve_gate_policy(ELIGIBLE, {}) == "block"
    assert gp.is_notify_and_continue(ELIGIBLE, {}) is False


def test_eligible_type_explicit_opt_in():
    d = {"checkpoint_policy": "notify-and-continue"}
    assert gp.resolve_gate_policy(ELIGIBLE, d) == "notify-and-continue"


def test_bmad_elicitation_menu_is_eligible():
    d = {"checkpoint_policy": "notify-and-continue"}
    assert gp.resolve_gate_policy("bmad_elicitation_menu", d) == "notify-and-continue"
    # ...but still blocks without the opt-in.
    assert gp.resolve_gate_policy("bmad_elicitation_menu", {}) == "block"


# ---- fail-closed default: unknown / missing types block ----------------------


def test_unknown_type_blocks():
    assert gp.resolve_gate_policy("some_new_gate", {}) == "block"
    assert gp.is_notify_and_continue("some_new_gate", {}) is False


def test_unknown_type_cannot_opt_in_via_content():
    # A non-allowlisted type CANNOT escape block by setting the policy field —
    # this is a per-TYPE allowlist, never a content heuristic.
    d = {"checkpoint_policy": "notify-and-continue"}
    assert gp.resolve_gate_policy("some_new_gate", d) == "block"


def test_none_and_empty_dispatch_default_block_for_unknown():
    assert gp.resolve_gate_policy("some_new_gate", None) == "block"
    assert gp.resolve_gate_policy("some_new_gate") == "block"


# ---- absolute guards: these ALWAYS block, even on an eligible type ------------


def test_requires_human_checkpoint_always_blocks():
    d = {"requires_human_checkpoint": True, "checkpoint_policy": "notify-and-continue"}
    assert gp.resolve_gate_policy(ELIGIBLE, d) == "block"


def test_guards_irreversible_always_blocks():
    d = {"guards_irreversible": True, "checkpoint_policy": "notify-and-continue"}
    assert gp.resolve_gate_policy(ELIGIBLE, d) == "block"


def test_irreversible_flag_always_blocks():
    d = {"irreversible": True, "checkpoint_policy": "notify-and-continue"}
    assert gp.resolve_gate_policy(ELIGIBLE, d) == "block"


def test_requires_human_checkpoint_dominates_string_truthy():
    d = {"requires_human_checkpoint": "yes"}
    assert gp.resolve_gate_policy(ELIGIBLE, d) == "block"


# ---- explicit block veto on an eligible type ---------------------------------


def test_eligible_type_explicit_block_veto():
    d = {"checkpoint_policy": "block"}
    assert gp.resolve_gate_policy(ELIGIBLE, d) == "block"


def test_unrecognized_policy_token_fails_closed():
    d = {"checkpoint_policy": "maybe-continue"}
    assert gp.resolve_gate_policy(ELIGIBLE, d) == "block"


# ---- truthiness edge cases on the guard flags --------------------------------


def test_falsey_guard_values_do_not_block():
    # With an explicit opt-in present, falsey guard flags must not force a block.
    for v in (False, 0, None, "", "false", "no"):
        d = {"requires_human_checkpoint": v, "irreversible": v,
             "checkpoint_policy": "notify-and-continue"}
        assert gp.resolve_gate_policy(ELIGIBLE, d) == "notify-and-continue", v
