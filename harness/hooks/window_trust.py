"""When may the compaction veto ACT on the context window it was handed?

Split out of compact-policy.py: the rule and the measurements that
justify it belong in one place, and the policy file was at its size bound.
"""

# THE LIVE SAFETY MARGIN. With less room than this between the context RIGHT
# NOW and the model's own limit, there is nowhere left to defer TO: the veto
# disarms itself and the compaction proceeds.
#
# Measured against `model_window - ctx`, NEVER against the headroom frozen at
# the first PreCompact call — that distinction is the whole self-disarm.
# Replaying the recorded run through the frozen form (first trigger 75,035, model
# window 200,000, so a frozen headroom of 124,965 that never shrinks) leaves
# the veto blocking on all seven triggers, the last at ctx 167,281, immediately
# after which the run died with "Prompt is too long". There is NO harness
# safety net past the model limit: the recorded run measured zero error-triggered
# compactions on the way down. 100k is ~6 turns of the 15.5k-per-turn growth it
# measured, plus the output reserve; erring long costs one early compaction,
# erring short costs the session.
MIN_HEADROOM = 100_000


def _int_or_none(value):
    """An honest int, or None. Booleans are not ints for our purposes."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def live_headroom(ctx, model_window):
    """What is ACTUALLY left, right now: model_window - ctx, or None.

    Not `model_window - effective_window`. The headroom frozen at the first
    PreCompact call does not shrink as the conversation grows, so a self-disarm
    bound on it never fires — which is exactly what the recorded run replays to.
    """
    ctx, model_window = _int_or_none(ctx), _int_or_none(model_window)
    return None if ctx is None or model_window is None else model_window - ctx

# A WINDOW WE DID NOT READ FROM THE TRANSCRIPT MUST PROVE ITSELF BEFORE WE ACT
# ON IT.  (Added after a retro found the veto had never once fired.)
#
# The window almost never comes from the transcript — MEASURED on real
# transcripts: none carries a ``contextWindow`` field, and every
# one writes the BARE model id, so the ``[1m]`` marker is gone.  In practice the
# window therefore comes from the operator's override file, which is an
# ASSERTION, not an observation.
#
# Understating an asserted window is already caught: ``_resolve`` returns
# ``read:model_window_contradicted`` the moment ctx exceeds it.  OVERSTATING is
# the one that kills a session, and nothing caught it — assert 1M on a session
# whose real window is 200k and the veto happily blocks at 190k, deferring past
# a limit that is already there.  That is the recorded run's death.
#
# The fix is to let the SESSION corroborate the claim.  A session that is alive
# at ctx tokens has a window of at least ctx.  So once ctx passes this bound the
# asserted window is no longer bigger than anything we have evidence for, and
# blocking is safe; below it we have no such evidence and fall back to ALLOW,
# which is exactly the behaviour that shipped before this guard.  The veto's
# useful band (a ceiling of 250k-300k) sits entirely above the bound, so this
# costs the intervention nothing — and in a genuinely 200k session ctx can never
# reach it, so the veto can never fire there at all.
WINDOW_TRUST_MIN_FRACTION = 0.20


def corroborated(ctx, model_window):
    """True when this session has itself demonstrated a window worth trusting.

    A session alive at ``ctx`` tokens has a window of at least ``ctx``. So the
    evidence required SCALES WITH THE CLAIM: a bigger asserted window needs a
    bigger demonstration before the veto acts on it. At 20%, a 1M claim must be
    corroborated past 200k — which a genuinely 200k session can never reach, so
    the veto can never fire there — while an honest 200k window needs only 40k
    and behaves exactly as it always did.

    An ABSOLUTE bound got this wrong: it demanded 200k of every session
    regardless of the claim, which disarmed the veto across every fixture in the
    suite and would have disarmed it for every small-window model too. The
    danger was never a small ctx; it was a large claim with nothing behind it.
    """
    if not isinstance(ctx, int) or isinstance(ctx, bool):
        return False
    if not isinstance(model_window, int) or isinstance(model_window, bool):
        return False
    return ctx >= WINDOW_TRUST_MIN_FRACTION * model_window


# NEUTERING THESE RULES — read before editing compact_policy_neuters.py.
#
# Rule (f2) is the SECOND self-disarm; MIN_HEADROOM is the first. Two probes
# (c_recorded_replay, c_envelope) assert only that the veto releases SOMEWHERE, so
# either rule alone satisfies them. Zeroing just MIN_HEADROOM left this guard
# releasing on the recorded-run fixture — a 200k window, ctx 75k-167k, entirely below the
# bound — and both probes passed while neutered. A probe that removes one of two
# redundant protections proves nothing about either, so n_headroom removes both.
#
# And patch `corroborated`, never WINDOW_TRUST_MIN_CTX. Once the rule moved into
# this module, setting the constant on the policy module changed a name nothing
# read, and all three probes passed neutered a second time — the very defect
# they exist to catch, reintroduced by the extraction that fixed it.
