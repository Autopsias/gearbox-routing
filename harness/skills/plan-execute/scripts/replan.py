"""RP-05 — `plan_impact` closeouts and the REPLAN checkpoint.

The gap this closes
-------------------
A session that learns something invalidating FUTURE work had exactly one place
to put it: its `notes` field. The plan carried on regardless, and the discovery
was found weeks later inside a session card nobody re-reads. `plan_impact`
(validated in `closeout_pipeline`) turns that discovery into a PARK: the plan
stops and the operator gets a decision brief naming the invalidated sessions,
the reason, and at most three options.

Why the halt flag, not an AWAITS_REVIEW article
-----------------------------------------------
A human checkpoint parks ONE session; every other ready session still
dispatches. A REPLAN says the PLAN is wrong, so nothing may advance — and that
is exactly what the halt flag means. S03 already closed the hole where `begin`
walked past a halt, so the stop is real on every door.

The halt carries ``kind: "replan"`` so the commands that RESOLVE it
(`add/amend/retire-session`, `redispatch`, `resolve-replan`) are let through
while `plan` / `begin` stay refused. Without that flavor the operator would be
deadlocked against the very gate they are answering.

Precedence
----------
A closeout may carry BOTH `human_checkpoint_reason` and `plan_impact`. The human
checkpoint parks FIRST — the operator is already being asked to look at this
session's result, and the replan decision may depend on what they decide. The
REPLAN park follows immediately after that checkpoint is acked. Precedence is an
ORDER, never a discard.

RP-08 — the brief owes a recommendation
---------------------------------------
The brief names three options; for a while the `recommendation` slot next to
them was always null, "to be filled in by the orchestrator when it presents
this". The module still refuses to AUTHOR one (a static function cannot judge
which sessions a specific discovery invalidated), but the slot is no longer
silently optional: `recommend-replan` is the explicit, recorded input that
supplies the judgement, the brief renders it into `HALT_NOTICE.txt` beside the
options, and `resolve` REFUSES while it is empty. Gated on
`RECOMMENDATION_MIN_SCHEMA` plus a per-record flag, so no plan already on disk
and no park already raised gains the requirement retroactively.

This module lives apart from `run.py` because `verify.py` needs it too (a
verify-gated session's park is deferred to `verify-finalize`, for the same
reason the human checkpoint is: never spend human attention on work that has
not passed its gates), and `run.py` already imports `verify`.
"""

import json
import os
import tempfile
from pathlib import Path

import article_block as ab
import closeout_pipeline as cp
import manifest_io as mio
import plan_mutate as pm
import run_state_io as rsi

REPLAN_DIR = "_replan"
REPLAN_DECISIONS = ("amend", "retire", "proceed")

# The manifest `plan_schema_version` at which the brief's `recommendation` stops
# being an optional slot and becomes a REQUIREMENT the resolve path enforces
# (RP-08). Same gating discipline as `closeout_pipeline.DECISION_BRIEF_MIN_SCHEMA`
# and `build_plan.PRIOR_ART_MIN_SCHEMA`, for the same reason: a plan already on
# disk keeps the contract it was built under. Belt and braces on top of the
# version stamp — the PARK records `recommendation_required` on the record
# itself, so a park raised BEFORE this code existed (no flag) can still be
# resolved, even on a plan whose stamp is new enough.
RECOMMENDATION_MIN_SCHEMA = 5


def _html(plan_dir):
    return Path(plan_dir) / "PLAN.html"


def _statuses(plan_dir):
    return ab.read_all_statuses(_html(plan_dir).read_bytes().decode("utf-8"))


def record_path(plan_dir, session_id):
    return Path(plan_dir) / REPLAN_DIR / f"{session_id}.json"


def load(plan_dir, session_id):
    p = record_path(plan_dir, session_id)
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return None


def pending(plan_dir):
    """Every recorded-but-unresolved REPLAN park, oldest first."""
    d = Path(plan_dir) / REPLAN_DIR
    if not d.is_dir():
        return []
    out = [
        rec
        for p in sorted(d.glob("*.json"))
        if (rec := load(plan_dir, p.stem)) and not rec.get("resolved")
    ]
    return sorted(out, key=lambda r: r.get("at") or "")


def closeout_impact(closeout, manifest=None):
    """The ACTIVE `plan_impact` block of a closeout, or None.

    Two ways to get None, and both matter:

      * the closeout has no key, a null, or a malformed block. Tolerant by
        design — the LOUD refusal for a malformed block is
        `closeout_pipeline.plan_impact_violations`, which runs before anything
        is persisted, so a record on disk has already been validated.
      * `manifest` is given and its `plan_schema_version` predates the field.
        The version gate has to cover the PARK, not merely the validation:
        otherwise a plan built before this feature keeps its old validation and
        still gains a brand-new halt, which is the retroactive breakage the
        stamp exists to prevent. Pass the manifest on every executor path;
        omit it only where the version has already been checked.
    """
    if not isinstance(closeout, dict):
        return None
    if manifest is not None:
        version = manifest.get("plan_schema_version") or 0
        if version < cp.PLAN_IMPACT_MIN_SCHEMA:
            return None
    pi = closeout.get("plan_impact")
    if isinstance(pi, dict) and pi.get("invalidates"):
        return pi
    return None


def brief(plan_dir, session_id, plan_impact, manifest, statuses, recommendation=None):
    """The decision brief the orchestrator presents verbatim.

    Same shape as the author's pre-dispatch `dispatch.checkpoint` brief
    (reason / decision / options), so the orchestrator has one presenter and not
    two. Exactly three options — amend, retire, proceed — because a decision
    card with a fourth option is a decision the operator has to make twice.

    `recommendation` is still not authored here — a recommendation is a
    judgement about THIS plan, and a canned string would read as authoritative
    while being uninformed. What changed in RP-08 is that the empty slot is no
    longer SILENTLY optional: `recommendation_required` marks the brief as owing
    one, `record_recommendation` is how the holder of that judgement supplies it,
    and `resolve` refuses until it has been supplied.
    """
    by_id = mio.session_by_id(manifest)
    invalidates = list(plan_impact.get("invalidates") or [])
    reason = str(plan_impact.get("reason") or "").strip()
    named = ", ".join(invalidates)
    return {
        "kind": "replan",
        "by_session": session_id,
        "reason": reason,
        "decision": (
            f"{session_id} learned something that invalidates {named}. Amend those "
            "sessions, retire them, or proceed unchanged?"
        ),
        "invalidates": [
            {
                "id": sid,
                "title": (by_id.get(sid) or {}).get("title", sid),
                "status": statuses.get(sid, "TODO"),
            }
            for sid in invalidates
        ],
        "options": [
            {
                "key": "amend",
                "label": f"Amend {named} to account for it",
                "how": (
                    "run.py amend-session <plan> --session <sid> --prompt/--depends-on … "
                    "(or `redispatch` a finished one), then run.py resolve-replan "
                    f"--session {session_id} --decision amend --reason '<what you changed>'"
                ),
            },
            {
                "key": "retire",
                "label": f"Retire {named} — that work no longer makes sense",
                "how": (
                    "run.py retire-session <plan> --session <sid> --reason '…', then "
                    f"run.py resolve-replan --session {session_id} --decision retire "
                    "--reason '<why>'"
                ),
            },
            {
                "key": "proceed",
                "label": "Proceed unchanged — the discovery does not actually invalidate them",
                "how": (
                    f"run.py resolve-replan --session {session_id} --decision proceed "
                    "--reason '<why the named sessions still hold>'"
                ),
            },
        ],
        # Supplied by the orchestrator through `recommend-replan` before it
        # presents this to a human. The MODULE never authors one: a
        # recommendation is a judgement about THIS plan, not a constant. It is
        # recorded, rendered, and — on a plan stamped new enough — required.
        "recommendation": (str(recommendation).strip() or None) if recommendation else None,
        "recommendation_required": (manifest.get("plan_schema_version") or 0)
        >= RECOMMENDATION_MIN_SCHEMA,
        "how_to_recommend": (
            f"run.py recommend-replan <plan-dir> --session {session_id} "
            "--recommendation '<which option you would pick, and why>'"
        ),
    }


def needs_recommendation(record):
    """True when this park still owes the operator the orchestrator's judgement.

    Reads the RECORD, not the manifest: a park raised before RP-08 carries no
    `recommendation_required` flag and is therefore never retroactively blocked,
    whatever the plan's stamp says today.
    """
    b = (record or {}).get("brief") or {}
    return bool(b.get("recommendation_required")) and not str(
        b.get("recommendation") or ""
    ).strip()


def format_brief(b):
    """Render a brief as plain text for HALT_NOTICE.txt / any text surface.

    The operator reads the reason, the invalidated sessions, the three options
    AND the recommendation without opening `_replan/<sid>.json` — the same rule
    `closeout_pipeline.format_decision_brief` follows for a BLOCKED closeout.
    A missing-but-required recommendation renders LOUDLY, with the command that
    supplies it, because that is the state the operator must not silently act in.
    """
    if not isinstance(b, dict):
        return ""
    sid = b.get("by_session") or "(unknown)"
    lines = [f"REPLAN DECISION — raised by {sid}"]
    if b.get("reason"):
        lines.append(f"  Why: {b['reason']}")
    inv = [i for i in (b.get("invalidates") or []) if isinstance(i, dict)]
    if inv:
        lines.append("  Invalidates:")
        lines += [
            f"    - {i.get('id')} [{i.get('status', '?')}] {i.get('title', '')}".rstrip()
            for i in inv
        ]
    options = [o for o in (b.get("options") or []) if isinstance(o, dict)]
    if options:
        lines.append("  Options:")
        for n, o in enumerate(options, 1):
            lines.append(f"    {n}. {o.get('key')} — {o.get('label')}")
            if o.get("how"):
                lines.append(f"       how: {o['how']}")
    rec = str(b.get("recommendation") or "").strip()
    if rec:
        lines.append(f"  RECOMMENDATION: {rec}")
    elif b.get("recommendation_required"):
        lines.append(
            "  RECOMMENDATION: NOT RECORDED — this brief is incomplete and "
            "`resolve-replan` will refuse until it is supplied:"
        )
        lines.append(f"       {b.get('how_to_recommend')}")
    else:
        lines.append("  RECOMMENDATION: (none recorded)")
    return "\n".join(lines)


def _notice_detail(plan_dir):
    """Every unresolved park, rendered — a re-render must not drop the second
    park just because the first one was the trigger."""
    return "\n\n".join(
        format_brief(r.get("brief")) for r in pending(plan_dir) if r.get("brief")
    )


def _write_record(plan_dir, session_id, record):
    d = Path(plan_dir) / REPLAN_DIR
    d.mkdir(exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(d), prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(record, indent=2, ensure_ascii=False))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, record_path(plan_dir, session_id))
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def park(plan_dir, session_id, plan_impact, manifest):
    """Record the REPLAN park and halt the plan. Idempotent.

    Idempotent in the way that matters for crash replay: re-running `apply`
    after a crash between the closeout persist and this call re-parks with the
    same record and the same halt. A park that was already RESOLVED is never
    re-raised — replaying an old closeout must not re-halt a decision the
    operator already made.
    """
    prior = load(plan_dir, session_id)
    if prior and prior.get("resolved"):
        return {"parked": False, "already_resolved": True,
                "resolution": prior.get("resolution")}

    statuses = _statuses(plan_dir)
    # A replay must not discard a recommendation already recorded against this
    # park — the judgement was made once and stays made.
    the_brief = brief(
        plan_dir, session_id, plan_impact, manifest, statuses,
        recommendation=((prior or {}).get("brief") or {}).get("recommendation"),
    )
    record = {
        "session": session_id,
        "invalidates": list(plan_impact.get("invalidates") or []),
        "reason": str(plan_impact.get("reason") or "").strip(),
        "at": (prior or {}).get("at") or rsi._now(),
        "brief": the_brief,
        "resolved": False,
        "resolution": None,
    }
    _write_record(plan_dir, session_id, record)

    # Visible where the operator actually looks: a note on the reporting
    # session's card AND on every session it invalidated, each keeping its own
    # status — a REPLAN reports a fact, it does not decide anyone's fate.
    if not prior:
        ab.apply_mutation(
            _html(plan_dir), session_id, status=statuses.get(session_id, "DONE"),
            note=f"REPLAN: invalidates {', '.join(record['invalidates'])} — {record['reason']}",
        )
        for sid in record["invalidates"]:
            try:
                ab.apply_mutation(
                    _html(plan_dir), sid, status=statuses.get(sid, "TODO"),
                    note=f"flagged by {session_id}'s plan_impact — {record['reason']}",
                )
            except ab.AnchorError:
                continue

    owed = needs_recommendation(record)
    rsi.set_halt(
        plan_dir,
        f"REPLAN — {session_id} invalidated {', '.join(record['invalidates'])}: "
        f"{record['reason']}. "
        + (
            f"Record your recommendation first (`run.py recommend-replan <plan-dir> "
            f"--session {session_id} --recommendation '…'`), then decide with "
            if owed
            else "Decide with "
        )
        + f"`run.py resolve-replan <plan-dir> --session {session_id} "
        "--decision amend|retire|proceed --reason '…'`.",
        session_id,
        kind="replan",
        # RP-08 — the options AND the recommendation slot land in HALT_NOTICE.txt,
        # so the operator sees the whole decision without opening a JSON file.
        detail=_notice_detail(plan_dir),
    )
    rsi.log_event(
        plan_dir, "replan_checkpoint", session_ids=[session_id],
        invalidates=record["invalidates"], reason=record["reason"],
    )
    return {"parked": True, "brief": the_brief, "invalidates": record["invalidates"],
            "needs_recommendation": owed}


class ResolveError(Exception):
    """Refusal: this REPLAN cannot be resolved (or briefed) as asked. Nothing
    was written."""


def record_recommendation(plan_dir, session_id, recommendation):
    """Record the orchestrator's judgement onto a parked REPLAN brief (RP-08).

    This is the explicit, recorded input the null slot used to depend on
    convention for. It stores, re-renders HALT_NOTICE.txt, and logs — it never
    decides anything, and it is deliberately a SEPARATE step from `resolve`: a
    recommendation supplied in the same breath as the operator's decision is
    theatre, not advice.
    """
    text = str(recommendation or "").strip()
    if not text:
        raise ResolveError(
            "refusing to record an empty recommendation: name the option you would "
            "pick (amend / retire / proceed) and why, in one line."
        )
    rec = load(plan_dir, session_id)
    if rec is None:
        raise ResolveError(
            f"no REPLAN park recorded for {session_id}. `recommend-replan` fills in the "
            "brief of a park raised by a closeout's `plan_impact`; there is none here."
        )
    if rec.get("resolved"):
        raise ResolveError(
            f"{session_id}'s REPLAN was already resolved as "
            f"{(rec.get('resolution') or {}).get('decision')!r}. A recommendation "
            "recorded after the decision advises nobody — nothing was written."
        )
    b = rec.setdefault("brief", {})
    replaced = str(b.get("recommendation") or "").strip() or None
    b["recommendation"] = text
    _write_record(plan_dir, session_id, rec)

    rsi.log_event(
        plan_dir, "replan_recommendation", session_ids=[session_id],
        recommendation=text, replaced=replaced,
    )
    # Re-render the notice in place: the halt itself has not changed, so this
    # must not re-fire the halt event or the notify hook.
    halt = rsi.load_state(plan_dir).get("halt", {})
    rendered = False
    if halt.get("set") and halt.get("kind") == "replan":
        rendered = rsi.rewrite_halt_notice(plan_dir, _notice_detail(plan_dir))
    return {"recorded": True, "session": session_id, "recommendation": text,
            "replaced": replaced, "halt_notice_updated": rendered,
            "brief": b,
            "next": f"present the brief, then `run.py resolve-replan <plan-dir> --session "
                    f"{session_id} --decision amend|retire|proceed --reason '…'`"}


def resolve(plan_dir, session_id, decision, reason):
    """Record the operator's answer and release the plan.

    For `amend` / `retire` the answer is only accepted once the plan ACTUALLY
    changed: the change log is the record of every mutation, so it is also the
    proof. Without that check, `--decision amend` is a rubber stamp that clears
    the halt and leaves the invalidated sessions exactly as they were.
    """
    if decision not in REPLAN_DECISIONS:
        raise ResolveError(f"--decision must be one of {', '.join(REPLAN_DECISIONS)}")
    if not (reason or "").strip():
        raise ResolveError(
            "refusing to resolve a REPLAN without --reason: the plan's change log is the "
            "only place the WHY of a mid-run amendment survives. State it in one line."
        )
    rec = load(plan_dir, session_id)
    if rec is None:
        raise ResolveError(
            f"no REPLAN park recorded for {session_id}. `resolve-replan` answers a park "
            "raised by a closeout's `plan_impact`; there is nothing to answer here."
        )
    if rec.get("resolved"):
        # Idempotent, like ack-checkpoint: a retried command is a no-op success,
        # never an error that halts a run.
        return {"resolved": False, "already": True, "session": session_id,
                "resolution": rec.get("resolution")}

    # RP-08 — an incomplete brief cannot be resolved. Checked BEFORE the
    # change-log gate on purpose: a recommendation is advice the operator needs
    # to make the decision, so its absence is a problem from before they chose,
    # not a paperwork failure at the end.
    if needs_recommendation(rec):
        raise ResolveError(
            f"refusing to resolve {session_id}: its REPLAN brief carries no "
            "recommendation. The brief offers three options; whoever presents it "
            "owes the operator a judgement about which one to take and why — an "
            "empty slot pushes that judgement back onto the person the brief is "
            "supposed to help. Record it FIRST, then re-run this command:\n"
            f"  {(rec.get('brief') or {}).get('how_to_recommend')}\n"
            "It is stored on the park record and rendered into HALT_NOTICE.txt "
            "alongside the options."
        )

    invalidated = set(rec.get("invalidates") or [])
    if decision in ("amend", "retire") and not changes_since(plan_dir, rec, invalidated):
        raise ResolveError(
            f"refusing to resolve {session_id} as {decision!r}: nothing in this plan's "
            f"change log has touched {sorted(invalidated)} since the REPLAN was raised. "
            "Apply the amendment FIRST (`amend-session` / `retire-session` / `redispatch` "
            "— never by hand), then re-run this command. If the named sessions in fact "
            "still hold, the honest answer is --decision proceed."
        )

    rec["resolved"] = True
    recommended = str((rec.get("brief") or {}).get("recommendation") or "").strip() or None
    rec["resolution"] = {"decision": decision, "reason": reason.strip(), "at": rsi._now(),
                         "recommendation": recommended}
    _write_record(plan_dir, session_id, rec)

    pm.record_change(
        plan_dir, op="resolve-replan", session=session_id,
        summary=f"REPLAN raised by {session_id} resolved as {decision}: {reason.strip()} "
                f"(invalidated: {', '.join(sorted(invalidated)) or 'none'})",
        sessions_touched=sorted(invalidated),
    )
    ab.apply_mutation(
        _html(plan_dir), session_id,
        status=_statuses(plan_dir).get(session_id, "DONE"),
        note=f"REPLAN resolved ({decision}): {reason.strip()}",
    )
    rsi.log_event(
        plan_dir, "replan_resolved", session_ids=[session_id],
        decision=decision, reason=reason.strip(), invalidates=sorted(invalidated),
        recommendation=recommended,
    )
    # Release the plan only if THIS park is what halted it, and only once every
    # park is answered — two replans must not be cleared by one decision.
    halt = rsi.load_state(plan_dir).get("halt", {})
    cleared = False
    if halt.get("set") and halt.get("kind") == "replan" and not pending(plan_dir):
        rsi.clear_halt(plan_dir)
        cleared = True
    return {"resolved": True, "session": session_id, "decision": decision,
            "reason": reason.strip(), "invalidates": sorted(invalidated),
            "recommendation": recommended, "halt_cleared": cleared,
            "next": "run `plan` to dispatch from the amended graph"}


def changes_since(plan_dir, rec, invalidated):
    """Change-log entries touching `invalidated` after this park was raised."""
    at = rec.get("at") or ""
    return [
        e
        for e in pm.read_changelog(plan_dir)
        if (e.get("at") or "") > at
        and ({e.get("session")} | set(e.get("sessions_touched") or [])) & set(invalidated)
    ]
