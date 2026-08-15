"""ESC-02 — the UPWARD escalation engine: climb a rung when a session keeps
failing the same gate.

The rule the SSOT states (``model-routing.yaml`` → the neutral ``escalation:``
block and, machine-readably, ``providers.<name>.escalation``): *two failures at
the same root cause stop the retry loop; raise reasoning within the model's live
range, then raise the model.* Until this module existed /plan-execute's rework
loop re-dispatched a failing session on the SAME rung forever — the policy had no
consumer.

WHAT THIS MODULE IS NOT
-----------------------
It is NOT a second ladder. Every rung comes from
``scripts/resolve_route.escalate()`` — the one funnel that ``resolve``/``escalate``
/``degrade`` already share, so the climb, the baseline and the downward fallback
cannot drift apart. This module decides only *how many* rungs to take and *which
ones are unavailable*; ``resolve_route`` decides what a rung IS.

THE LADDER POSITION (SSOT order, budget-aware — grill 2026-08-13)
-----------------------------------------------------------------
    attempt 1 fails                  -> rework at the SAME rung, with feedback
    attempt 2 fails, SAME signature  -> stuck protocol arms (research pass)
    attempt 3 = FIRST ESCALATED      -> carries the research output AND climbs 1
    each further same-signature fail -> one more rung

The research pass is NOT a separately spent attempt: it rides along with the
first escalated attempt, exactly as the SSOT says ("THE RESEARCH PASS IS NOT AN
EXTRA ROUND").

THE CLIMB ACCUMULATES — one rule, in ``climb_steps``: *a failure that leaves the
session stuck at the same root cause buys exactly ONE rung, measured from the
ladder position it has already reached.*

    climb = min(reached + (1 if a NEW failure left it stuck), rework_count)

``reached`` is the persisted ladder position; "stuck" is ``stuck_protocol``'s
consecutive same-signature counter hitting ``TRIGGER_AT``. A DIFFERENT error is
progress: it buys no rung — and costs none, the session holds what it reached.
A SECOND root cause therefore climbs from the current rung rather than
restarting at the authored cell, which is what keeps the apex reachable at all.
``rework_count`` (from ``_verify_state/<sid>.json``) clamps the total, so no
accounting bug can climb faster than the budget spent.

COMPUTED, NEVER STORED-AND-FIRED
--------------------------------
``compute()`` writes NOTHING, and the climb advances on a NEW FAILURE, never on a
new dispatch — so a crash between ``begin`` and ``apply`` re-derives the SAME
rung on resume instead of climbing twice. The persisted escalation state is only
observations and operator actions: the refused-rungs set and the generation
counter (written by ``record-refusal`` / ``redispatch`` / ``amend-session``), and
the ladder position + rung + failure count each dispatch actually settled on
(``record_dispatch``, an observation of what happened, never a pre-decision).

REFUSAL RULE (scope stated honestly)
------------------------------------
A refusal enters the refused set ONLY through :func:`record_refusal`, reached
from the ``record-refusal`` subcommand the orchestrator calls when it OBSERVES a
real dispatch error or a ``CODEX-DISPATCH-FAILED`` wrapper signal. Nothing infers
a refusal. A refused rung is skipped DOWNWARD (the session re-dispatches at the
previous rung, floored at the authored cell — never onto the downward fallback
ladder), the refusal is not charged against ``max_rework``, and the rung is never
re-proposed for the rest of the session.

OUT OF SCOPE, and why: a blocked Claude model override that SILENTLY inherits
another model raises no error at all (plan-execute SKILL.md § "model_arg"), so no
refused-rungs set can catch it. That case is addressed by the served-model
ATTESTATION handoff (s05), not here — which is also why every rung this module
reports is stamped ``model_ran_source: "requested"``, never "attested".

BOTH LANES, ONE LADDER (ESC-03, s04 — the boundary s03 left is now closed)
--------------------------------------------------------------------------
The climb is wired into the CLAUDE-backend dispatch path AND the codex-backed one
(an explicit Codex pin, `active_provider: openai`, or `--harness codex`). s03
predicted this would be a wiring job rather than a second ladder, and it was:
nothing in this module changed for it. The caller passes `provider` — "anthropic"
for a Claude-lane member, "openai" for a codex-backed one — and every rung still
comes from `resolve_route.escalate()`.

The one thing the codex lane does differently lives entirely in `run.py`: the cell
the climb starts FROM is the RESOLVED `(codex_model, codex_effort)` pair, not the
manifest's `model`/`reasoning` (which may name a Claude token that translation
turned into a gpt-5.6 model). Feeding this module a `sonnet` cell against the
openai profile raises in the resolver, which is exactly what it should do.

VERSION GATE
------------
Escalation fires only on manifests stamped ``plan_schema_version >=
ESCALATION_MIN_SCHEMA``. Every plan already on disk is at 5 or below and keeps
its exact pre-existing dispatch, byte for byte. A session opts out with
``escalation: false``.
"""

from pathlib import Path

import run_state_io as rsi
import ship_state_io as ssio
import stuck_protocol as sp

ESCALATION_MIN_SCHEMA = 6
"""The MANIFEST stamp at which the upward climb switches on.

Same opt-in shape (and the same reason) as every other version-gated executor
feature here: a plan built before this feature existed must never gain a new
dispatch behaviour retroactively. `build_plan.PLAN_SCHEMA_VERSION` is pinned
>= this constant by a test, so the stamp and the gate cannot drift apart
silently — see `version-gate-new-plan-requirements` in project memory for the
failure this convention exists to prevent."""

STATE_KEY = "escalation"

# The one legitimate source value while no attestation channel exists (s05 lands
# it). "requested" means: this is what we ASKED Task for. It is NOT a claim about
# what actually served the request.
SOURCE_REQUESTED = "requested"
SOURCE_UNKNOWN = "unknown"


class EscalationError(Exception):
    """A refusal recorded against a session that cannot be escalated at all."""


# --------------------------------------------------------------------------
# Persisted state — run_state.json only (mutable runtime state, NEVER the
# manifest, never a digest input, so it can never trip `state-drift`).
# --------------------------------------------------------------------------
def rung_key(model, reasoning):
    """The stable identity of one ladder cell, used as the refused-set member.

    `inherit`/`unset` rather than the empty string so a hand-read run_state is
    legible and two different absences never collide."""
    return f"{model or 'inherit'}@{reasoning or 'unset'}"


def session_state(plan_dir, session_id):
    """This session's escalation state, defaulted. Read-only — never writes."""
    blob = (rsi.load_state(plan_dir).get(STATE_KEY) or {}).get(session_id) or {}
    return {
        "refused": list(blob.get("refused") or []),
        "generation": int(blob.get("generation") or 0),
        # TWO POSITIONS, deliberately, because a refusal makes them differ:
        #   `climb`     — how far UP THE LADDER the climb has got to. Only ever
        #                 rises. This is what the next climb accumulates from.
        #   `last_rung` — the rung the last dispatch actually BOUND to. A refused
        #                 rung steps this DOWN to the previous one.
        # Collapsing them capped the ladder permanently: the step-down wrote the
        # lower rung back as the accumulator, so `climb_steps` could only ever
        # re-propose the refused rung, and every rung ABOVE a refused one became
        # unreachable while `remaining` still listed them as untried.
        "climb": int(blob.get("climb") or 0),
        "last_rung": int(blob.get("last_rung") or 0),
        # The stuck-protocol `attempts` counter as of that dispatch. Its ONLY job
        # is to answer "has a new failure been recorded since we last dispatched?",
        # which is what keeps the accumulating climb idempotent on resume.
        "last_attempts": int(blob.get("last_attempts") or 0),
    }


def _save_session_state(plan_dir, session_id, rec):
    state = rsi.load_state(plan_dir)
    esc = state.setdefault(STATE_KEY, {})
    esc[session_id] = rec
    rsi.save_state(plan_dir, state)
    return rec


def record_refusal(plan_dir, session_id, model, reasoning, reason, source):
    """THE explicit refusal interface (nothing infers a refusal — see the module
    docstring). Marks one ladder cell unavailable for the rest of this session.

    Idempotent: recording the same cell twice is a no-op, so an orchestrator that
    retries the report cannot corrupt the set."""
    rec = session_state(plan_dir, session_id)
    key = rung_key(model, reasoning)
    if key not in rec["refused"]:
        rec["refused"].append(key)
        _save_session_state(plan_dir, session_id, rec)
    rsi.log_event(plan_dir, "escalation_rung_refused", session_ids=[session_id],
                  rung=key, model=model, reasoning=reasoning, reason=reason,
                  source=source, refused=rec["refused"])
    return rec


def record_dispatch(plan_dir, session_id, rung, climb=None):
    """Record where a dispatch got to. Called by `cmd_begin` once the cell is
    settled (after any decline or refusal step-down), never before.

    `rung` is what BOUND; `climb` is the ladder position that was ASKED FOR
    (they differ exactly when a refused rung forced a step-down). See
    `session_state` for why conflating them capped the ladder permanently.

    This is an OBSERVATION, not a stored-and-fired decision — the distinction the
    module docstring turns on. `compute()` still re-derives the rung from scratch
    on every call; what this adds is the one fact no derivation can recover:

      * WHICH RUNG THE ATTEMPT THAT JUST FAILED RAN ON. The failure counters move
        on after that attempt, so re-deriving the rung at halt time answers a
        different question. When the FINAL failure carries a different signature
        from the streak that drove the climb, `consecutive` has already reset and
        the derived rung is lower than the one that really ran — which named the
        wrong rung in the BLOCKED brief and listed rungs that WERE tried as
        untried.
      * THE BASE OF THE NEXT CLIMB. The climb ACCUMULATES across root causes: each
        streak is measured from the rung the session is standing on, not from the
        authored cell. Deriving the rung from the current streak alone stalled the
        ladder — a session that climbed to rung 2 on cause A, then failed twice
        consecutively on cause B, sat at rung 2 while demonstrably stuck again
        (measured 2026-08-14). And a different error must not hand the model BACK
        either, so this value only ever rises within a generation.

    Re-running `begin` on resume re-records the same values and derives the same
    rung, because the climb advances on a NEW FAILURE (`attempts` moving), never on
    a new dispatch."""
    rec = session_state(plan_dir, session_id)
    rung = max(0, int(rung or 0))
    rec["last_rung"] = rung
    # Only ever rises — a refusal step-down must not un-climb the ladder.
    rec["climb"] = max(rec["climb"], rung if climb is None else max(0, int(climb)))
    rec["last_attempts"] = _attempts(plan_dir, session_id)
    return _save_session_state(plan_dir, session_id, rec)


def last_rung(plan_dir, session_id):
    """The rung the most recent dispatch bound to (0 = as authored). Read-only."""
    return session_state(plan_dir, session_id)["last_rung"]


def last_climb(plan_dir, session_id):
    """The ladder position the climb has REACHED, refusals included. Read-only.

    Differs from `last_rung` only when a refused rung forced a step-down, which is
    exactly the case the BLOCKED brief has to be able to describe."""
    return session_state(plan_dir, session_id)["climb"]


def reset(plan_dir, session_id, why):
    """Fresh session, fresh ladder. Clears the refused set AND bumps the
    generation counter.

    The generation bump is what keeps the OUTCOME LEDGER honest: an amendment
    that changes the authored cell (or a deliberate redispatch) starts a new
    cohort, and records from before it must not be averaged together with records
    from after it as though they described the same experiment."""
    rec = session_state(plan_dir, session_id)
    new = {"refused": [], "generation": rec["generation"] + 1,
           "climb": 0, "last_rung": 0, "last_attempts": 0}
    _save_session_state(plan_dir, session_id, new)
    rsi.log_event(plan_dir, "escalation_reset", session_ids=[session_id],
                  reason=why, generation=new["generation"],
                  cleared_refused=rec["refused"])
    return new


# --------------------------------------------------------------------------
# Derivation — pure. Reads state, writes none.
# --------------------------------------------------------------------------
def enabled(manifest, session):
    """Version gate + per-session opt-out. Both fail CLOSED (no escalation)."""
    if int(manifest.get("plan_schema_version") or 0) < ESCALATION_MIN_SCHEMA:
        return False
    return session.get("escalation") is not False


def _rework_count(plan_dir, session_id):
    st = ssio.read_json_with_bak(
        Path(plan_dir) / "_verify_state" / f"{session_id}.json"
    )
    return int((st or {}).get("rework_count") or 0)


def _stuck(plan_dir, session_id):
    return (rsi.load_state(plan_dir).get("stuck") or {}).get(session_id) or {}


def _consecutive(plan_dir, session_id):
    return int(_stuck(plan_dir, session_id).get("consecutive") or 0)


def _attempts(plan_dir, session_id):
    """Total failures recorded for this session — the counter that moves exactly
    once per failed attempt, whatever the signature."""
    return int(_stuck(plan_dir, session_id).get("attempts") or 0)


def dispatchable_cell(model, reasoning):
    """True when (model, reasoning) names a cell the ladder can actually climb FROM.

    BOTH halves are required, and the effort half is the non-obvious one. A
    session may legally declare `model` with no `reasoning` — but an unset effort
    is not a rung, and `escalate()` resolves a non-rung `native_effort` to the
    tier's FIRST rung. So `sonnet` with no `reasoning` would "climb" to
    sonnet@low and then sonnet@medium: two rungs spent walking DOWN and back to
    where it started, while the failure that triggered them goes unaddressed.
    Measured against the real SSOT on 2026-08-14.

    Fail closed rather than guess: nothing here knows what effort an unpinned
    session actually runs at, so no rung above it can be named honestly."""
    return bool(model) and bool(reasoning)


def climb_steps(plan_dir, session_id):
    """How many rungs up this session's NEXT dispatch should run.

    ONE RULE: *a failure that leaves the session stuck at the same root cause buys
    exactly one rung, measured from the rung it is standing on.* Everything else
    follows from it —

      * 0 for a first dispatch and for the first (same-tier, feedback-carrying)
        rework: the stuck protocol has not armed, so nothing is bought.
      * a DIFFERENT error buys nothing (that is progress) but costs nothing
        either — the session HOLDS the rung it reached. Handing the model back on
        a signature change would punish progress.
      * a NEW streak climbs from the CURRENT rung, not from the authored cell.
        Deriving the rung from the live streak alone stalled the ladder: a session
        at rung 2 that then failed twice consecutively on a second root cause was
        armed-and-stuck again and still got no more capability (measured
        2026-08-14).

    Idempotent by construction: the climb advances on a NEW FAILURE (`attempts`
    moving past the value recorded at the last dispatch), never on a new dispatch,
    so re-running `begin` after a crash re-derives the same rung. `rework_count`
    clamps the total — no accounting bug can climb faster than the budget spent."""
    st = session_state(plan_dir, session_id)
    # From the LADDER POSITION reached, not from the rung that last bound: a
    # refusal steps the dispatch down but must not un-climb the ladder, or every
    # rung above a refused one is unreachable for the rest of the session.
    rung = st["climb"]
    if (_attempts(plan_dir, session_id) > st["last_attempts"]
            and _consecutive(plan_dir, session_id) >= sp.TRIGGER_AT):
        rung += 1
    return max(0, min(rung, _rework_count(plan_dir, session_id)))


# A ladder longer than this is a cycle in the SSOT, not a ladder. The real
# anthropic climb from the sonnet floor is 5 cells; openai's is 4.
_LADDER_CAP = 12


def _ladder(resolver, task_class, provider, authored, ssot_path=None):
    """The WHOLE ladder from `authored` to the apex, walked ONCE.

    Returns (rungs, hit_top). `rungs[0]` is always the authored cell, so the index
    into this list IS the rung number and index 0 is the floor; `hit_top` says the
    walk ended at the resolver's EXHAUSTED fixpoint rather than at the cap.

    ONE walk, then index into it. Walking to N and then walking AGAIN from the
    chosen rung to collect what is left above it re-climbed cells the first walk
    had already resolved — and every `escalate()` call re-reads and re-parses the
    ~1400-line SSOT, so a duplicated hop is a duplicated parse.

    NOT CACHED, deliberately, and the numbers are why (measured 2026-08-14 on the
    real SSOT): one `escalate()` is 1.3 ms, a whole `compute()` is 6.6 ms, and
    `compute()` runs at most twice per gate failure — against a subagent dispatch
    that costs minutes. Memoising `resolve_route._load` would buy ~5 ms on a
    human-paced path and would put a staleness failure mode on the one file every
    routing decision in the tree reads. Wrong trade; the number is recorded here
    so the next reader does not have to re-derive it."""
    rungs = [dict(authored)]
    seen = {rung_key(authored.get("model_id"), authored.get("native_effort"))}
    hit_top = False
    while len(rungs) < _LADDER_CAP:
        nxt = resolver.escalate(task_class, provider, current=rungs[-1], ssot_path=ssot_path)
        if nxt == resolver.EXHAUSTED:
            hit_top = True
            break
        # NO TWO RUNGS OF ONE LADDER MAY SHARE A KEY (s04 hardening). The refused
        # set, `remaining`, and the CLI's `--model/--reasoning` all identify a rung
        # by `model@effort`, so two rungs resolving to the same pair would CONFLATE:
        # refusing one would silently refuse the other, and the ladder would report
        # a rung as untried that a refusal had already removed. Today the SSOT makes
        # that unreachable (verify-routing.sh fails any provider whose two tiers
        # resolve to one model), which is precisely why this is cheap insurance
        # against a future re-pin rather than a live code path — and why it FAILS
        # LOUDLY instead of quietly de-duplicating: a ladder that walks the same
        # cell twice is a broken SSOT, not a dispatch to paper over.
        key = rung_key(nxt.get("model_id"), nxt.get("native_effort"))
        if key in seen:
            raise EscalationError(
                f"provider '{provider}' escalation ladder revisits rung {key!r} "
                f"(walk so far: {[rung_key(r['model_id'], r['native_effort']) for r in rungs]}) — "
                "two rungs sharing one (model, effort) identity cannot be told apart by the "
                "refused set or by `record-refusal`. Fix the provider profile: no two tiers may "
                "resolve to the same model, and no effort_ladder may repeat a level."
            )
        seen.add(key)
        rungs.append(nxt)
    return rungs, hit_top


def ladder_keys(resolver, task_class, provider, authored_model, authored_reasoning,
                ssot_path=None):
    """Every rung key ABOVE the authored cell, in climb order.

    The refusal interface validates against this. Without the check,
    `record-refusal --model fable` with no `--reasoning` recorded `fable@unset`,
    reported success, and the very next dispatch sent `fable@medium` again — a
    refusal that reads as accepted and changes nothing is worse than one that is
    rejected (measured 2026-08-14)."""
    rungs, _ = _ladder(resolver, task_class, provider,
                       {"model_id": authored_model,
                        "native_effort": authored_reasoning or None},
                       ssot_path=ssot_path)
    return [rung_key(r["model_id"], r["native_effort"]) for r in rungs[1:]]


def compute(plan_dir, manifest, session, provider, resolver, authored_model,
            authored_reasoning, task_class=None, ssot_path=None, steps=None):
    """The dispatch-time escalation decision for one session. WRITES NOTHING.

    Returns ``None`` when this session does not escalate at all (version gate,
    opt-out, or no dispatchable authored model). Otherwise a descriptor:

        {"authored": {"model","reasoning"}, "ran": {"model","reasoning"},
         "rung": int,              # 0 = the authored cell
         "attempt": int,           # the DISPATCH attempt this is (1 = first)
         "climb_requested": int,   # rungs the failure history asked for
         "refused_skipped": [key], # rungs stepped DOWN past
         "ladder_exhausted": bool, # the ladder ended before `climb_requested`
         "remaining": [{"model","reasoning"}],  # rungs still above `ran`
         "generation": int, "model_ran_source": "requested"|"unknown"}

    `rung == 0` means "dispatch exactly as authored" — the caller checks that
    rather than a separate flag, so there is one truth about the cell.

    `attempt` and `rung` are DIFFERENT numbers and both are needed: the first
    attempt is 1 at rung 0, the climb only arms on the third, and a refusal
    step-down or a held rung breaks the correspondence entirely. Reporting the
    rung index in the `attempt` slot would make every escalated ledger record
    misattributed by one.
    """
    if not enabled(manifest, session):
        return None
    if not dispatchable_cell(authored_model, authored_reasoning):
        # Nothing to climb FROM — see `dispatchable_cell`. Fail closed here (the
        # invariant lives with the arithmetic); the caller says it out loud.
        return None
    authored_reasoning = authored_reasoning or None

    authored = {"model_id": authored_model, "native_effort": authored_reasoning}
    want = climb_steps(plan_dir, session["id"]) if steps is None else steps
    st = session_state(plan_dir, session["id"])
    refused = set(st["refused"])

    rungs, hit_top = _ladder(resolver, task_class or "agentic_build", provider,
                             authored, ssot_path=ssot_path)
    # The ladder ran out before it could take every rung the failure history asked
    # for. (`hit_top` alone is not that: a walk that reaches the apex in FEWER
    # steps than requested is exhausted; one that reaches it in more is not.)
    exhausted = hit_top and want > len(rungs) - 1
    # Step DOWN past any refused rung, floored at index 0 — the AUTHORED cell.
    # `idx > 0` is that floor, and it deliberately outranks the refused set: if the
    # authored cell itself were reported refused we still dispatch it rather than
    # walk the DOWNWARD fallback ladder, because dropping a session below the tier
    # its author chose is a different decision from declining to climb, and only
    # `degrade()` is allowed to make it.
    idx = min(want, len(rungs) - 1)
    skipped = []
    while idx > 0 and rung_key(rungs[idx]["model_id"], rungs[idx]["native_effort"]) in refused:
        skipped.append(rung_key(rungs[idx]["model_id"], rungs[idx]["native_effort"]))
        idx -= 1
    chosen = rungs[idx]

    # What is still ABOVE the chosen rung — the operator needs it in the BLOCKED
    # brief when the budget runs out mid-ladder ("you stopped at fable@medium;
    # fable@high and fable@xhigh were never tried"). Sliced out of the SAME walk;
    # a refused rung is not "untried", it is unavailable, so it is filtered out.
    remaining = [
        {"model": r["model_id"], "reasoning": r["native_effort"]}
        for r in rungs[idx + 1:]
        if rung_key(r["model_id"], r["native_effort"]) not in refused
    ]

    return {
        "authored": {"model": authored_model, "reasoning": authored_reasoning or ""},
        "ran": {"model": chosen["model_id"], "reasoning": chosen["native_effort"] or ""},
        "rung": idx,
        # The DISPATCH attempt, not the rung: attempt 1 is the first dispatch, and
        # the climb does not arm until attempt 3. Derived from the rework budget
        # actually spent, which is the only counter that tracks attempts.
        "attempt": _rework_count(plan_dir, session["id"]) + 1,
        "climb_requested": want,
        "refused_skipped": skipped,
        "refused": sorted(refused),
        "ladder_exhausted": exhausted,
        "remaining": remaining,
        "generation": st["generation"],
        "model_ran_source": SOURCE_REQUESTED if chosen["model_id"] else SOURCE_UNKNOWN,
    }


def brief_line(desc):
    """One plain line naming where the climb stopped and what it never tried —
    rendered into the BLOCKED halt reason so an operator sees it without opening
    a file. Returns "" when there is nothing worth saying.

    SILENT WHEN NO CLIMB EVER ARMED. A session that halts on its first attempt (or
    whose budget ran out before two consecutive same-cause failures) never entered
    the ladder, and framing that halt as "escalation stopped at rung 0, here are
    the untried rungs above it" invents a climb that did not happen — the operator
    reads it as "we escalated and still failed". `climb_requested == 0` is exactly
    "no rung was ever asked for", so it is the gate."""
    if not desc or (desc["rung"] == 0 and not desc.get("climb_requested")):
        return ""
    ran = f"{desc['ran']['model']}@{desc['ran']['reasoning'] or 'unset'}"
    if desc["rung"] == 0:
        # Asked for a climb, got none. THREE different causes, and naming the wrong
        # one is worse than naming none: the climb was DECLINED (it could never
        # bind for this session), the rungs were REFUSED at dispatch, or the
        # authored cell is already the apex and there was nowhere to go.
        nxt = ", ".join(f"{r['model']}@{r['reasoning'] or 'unset'}" for r in desc["remaining"])
        if desc.get("declined"):
            # `remaining` here is measured from the rung the climb WOULD have
            # reached, so it silently omits every rung below it — and no amount of
            # extra rework budget can buy any of them. Say what actually happened
            # and what would actually change it.
            would = desc.get("would_have") or desc["ran"]
            return (f"escalation never armed for this session — the climb is DECLINED "
                    f"({desc['declined']}), so it ran as authored ({ran}) however often it "
                    f"failed. It would otherwise have reached "
                    f"{would['model']}@{would['reasoning'] or 'unset'}. Raising max_rework "
                    "cannot help; remove what blocks the climb (a non-tier "
                    "dispatch.subagent_type) or accept the authored tier.")
        if desc.get("refused_skipped"):
            return (f"escalation never left the authored tier ({ran}) — every escalated rung "
                    f"was refused at dispatch ({', '.join(desc['refused_skipped'])})"
                    + (f"; untried rungs: {nxt}." if nxt else "."))
        if not nxt:
            return (f"escalation had nowhere to go: the authored cell ({ran}) is already the "
                    "top of this provider's ladder.")
        return (f"escalation never left the authored tier ({ran}); untried rungs above it: "
                f"{nxt}. Raise the session's max_rework to let the climb continue.")
    if desc["remaining"]:
        nxt = ", ".join(f"{r['model']}@{r['reasoning'] or 'unset'}" for r in desc["remaining"])
        return (f"escalation stopped at rung {desc['rung']} ({ran}); untried rungs above it: "
                f"{nxt}. Raise the session's max_rework to let the climb continue.")
    return f"escalation reached the top of the ladder at rung {desc['rung']} ({ran}) — no rung remains."


# --------------------------------------------------------------------------
def _demo():  # pragma: no cover - ponytail: smallest runnable self-check
    """Walk the real SSOT's anthropic ladder from sonnet@medium with no plan on
    disk, proving the climb is the SSOT's and stops where the SSOT stops."""
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
    import resolve_route as rr

    cur = {"model_id": "sonnet", "native_effort": "medium"}
    seen = [cur]
    while cur != rr.EXHAUSTED and len(seen) < 12:
        cur = rr.escalate("agentic_build", "anthropic", current=cur)
        if cur != rr.EXHAUSTED:
            seen.append(cur)
    print(" -> ".join(f"{r['model_id']}@{r['native_effort']}" for r in seen))
    assert seen[-1] == {"model_id": "fable", "native_effort": "xhigh"}, seen[-1]
    print("OK: the climb ends at fable@xhigh — never opus@max, never fable@max.")


if __name__ == "__main__":
    _demo()
