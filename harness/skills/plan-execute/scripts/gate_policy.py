"""Per-gate autonomy policy — block vs notify-and-continue.

OR-03 (push-style checkpoints). The assessment evidence for April–May showed a
quarter of everything the operator typed was a rubber-stamp: 'proceed', 'yes',
'c', 'y', or a menu letter answered to a gate that, historically, ALWAYS
received the same "keep going" answer. Two gate TYPES accounted for almost all
of it: plan-execute post-session AWAITS_REVIEW acknowledgements, and BMAD
numbered elicitation menus in the epic-dev conductor.

This module encodes a **fail-closed, per-gate-TYPE allowlist**. The default is
``block`` (today's behaviour — surface an AWAITS_REVIEW checkpoint and wait). A
gate is reclassified to ``notify-and-continue`` (proceed after emitting a
notification, recording the auto-continue in run.ndjson) ONLY when EVERY
condition below holds — the moment any is unmet or ambiguous, it blocks:

  (a) the gate's TYPE is on ``NOTIFY_ELIGIBLE_GATE_TYPES`` (the explicit,
      evidence-derived allowlist); an unknown/new type is never eligible;
  (b) ``dispatch.requires_human_checkpoint`` is falsey — an absolute human
      checkpoint NEVER auto-continues, regardless of any other field;
  (c) the gate guards no irreversible / destructive action
      (``dispatch.guards_irreversible`` / ``dispatch.irreversible`` falsey);
  (d) the per-gate ``dispatch.checkpoint_policy`` field EXPLICITLY opts in with
      ``"notify-and-continue"``. This is deliberately opt-IN, not opt-out: an
      unset / null / ``"block"`` field means block, so EVERY existing plan keeps
      today's block-and-wait behaviour untouched until an author consciously
      flips a specific gate. Being on the allowlist only makes a gate *eligible*
      to opt in; it never auto-continues on its own.

Crucially this is a TYPE allowlist, never a content heuristic: a plan cannot
set ``checkpoint_policy: notify-and-continue`` on a non-allowlisted gate type
to escape a block — that request is refused (returns ``block``). New gate types
default to block until a human adds them here.
"""

# The ONLY gate types eligible for notify-and-continue. Evidence-derived; extend
# this set only with human review + fresh evidence that the type is rubber-stamp.
NOTIFY_ELIGIBLE_GATE_TYPES = frozenset(
    {
        # plan-execute: a subagent's post-session `human_checkpoint_reason` that
        # asks only for a review acknowledgement (the "AWAITS_REVIEW ack").
        "session_review_ack",
        # epic-dev-conductor: a BMAD numbered elicitation menu ("1-9 select an
        # option") whose default answer is always "proceed"/"c"/"y".
        "bmad_elicitation_menu",
    }
)

VALID_POLICIES = ("block", "notify-and-continue")

BLOCK = "block"
NOTIFY_AND_CONTINUE = "notify-and-continue"


def _truthy(v):
    """Conservative truthiness: only real booleans / obvious yes-values count.

    A missing / null / unparsable field is treated as False for the *guard*
    flags (requires_human_checkpoint, irreversible) — but note those flags
    force a BLOCK when True, so treating an ambiguous value as False here is
    balanced by the fact that we only ever GRANT notify-and-continue when the
    positive allowlist + explicit conditions all hold.
    """
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    if isinstance(v, str):
        return v.strip().lower() in {"true", "yes", "1", "on"}
    return False


def resolve_gate_policy(gate_type, dispatch=None):
    """Return the effective policy for a gate: 'block' | 'notify-and-continue'.

    Fail-closed. See module docstring for the four conjoined conditions.

    Args:
        gate_type: the gate's TYPE string (e.g. 'session_review_ack').
        dispatch:  the manifest session's ``dispatch`` block (a dict) or None.
    """
    dispatch = dispatch if isinstance(dispatch, dict) else {}

    # (b) Absolute human checkpoint — never auto-continue. Checked FIRST so it
    # dominates every other signal.
    if _truthy(dispatch.get("requires_human_checkpoint")):
        return BLOCK

    # (c) Irreversible / destructive guard — never auto-continue.
    if _truthy(dispatch.get("guards_irreversible")) or _truthy(dispatch.get("irreversible")):
        return BLOCK

    # (a) TYPE allowlist. Unknown/new types are never eligible.
    if gate_type not in NOTIFY_ELIGIBLE_GATE_TYPES:
        return BLOCK

    # (d) Explicit per-gate opt-IN required. An eligible type blocks by default
    # (conservative: existing plans are unchanged) and auto-continues ONLY when
    # the author pinned checkpoint_policy: notify-and-continue. Unset / null /
    # "block" / any unrecognized token → block (fail-closed).
    policy = dispatch.get("checkpoint_policy")
    if policy is None:
        return BLOCK
    if str(policy).strip().lower() == NOTIFY_AND_CONTINUE:
        return NOTIFY_AND_CONTINUE
    return BLOCK


def is_notify_and_continue(gate_type, dispatch=None):
    """Convenience boolean wrapper around :func:`resolve_gate_policy`."""
    return resolve_gate_policy(gate_type, dispatch) == NOTIFY_AND_CONTINUE
