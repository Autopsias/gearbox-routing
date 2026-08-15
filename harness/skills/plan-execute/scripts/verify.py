"""Session verification gates — run automated checks before a DONE closeout is
*really* DONE (the "don't ship trash" boundary).

A plan built by ``/plan-builder`` may declare, per session (or phase), a
``verify`` block: an ordered list of gate ids (tests / ``/eval --smoke`` /
type-check / a reviewer) that MUST pass before the orchestrator finalizes the
session as DONE. This runs at the ``apply`` boundary, AFTER the closeout is
validated and item statuses are applied, but BEFORE shipping — so a self-reported
DONE that fails its tests never advances or ships.

State machine (mirrors ``shipping.py``, deliberately leaner):

  * Gates are resolved through the SAME ``eval-gates`` registry as shipping's
    ``pre_deploy_gates`` (``shipping.resolve_gate``) — skill-kind (orchestrator
    invokes) or argv-kind (this helper runs, ``shell=False``, allow-listed env).
  * Gates run sequentially within one session. No cross-session resource locks:
    verify gates are read-only checks. A gate that is NOT safe to run
    concurrently must not be placed on a SHARED-TREE ``parallel_group`` member —
    under ``dispatch.isolation: "worktree"`` a member's gates run inside that
    member's own worktree (``gate_cwd``), which resolves the common case. That
    is contract M4, and M4 is NOT machine-enforced: nothing here can read a gate
    script's intent.
  * On all-pass → ``verify-finalize`` flips the session DOING→DONE (or
    →AWAITS_REVIEW when the closeout also asked for a human checkpoint — verify
    runs FIRST, so we never spend human attention on work that fails its gates).
  * On a gate FAIL:
      - ``on_fail: rework`` (default) and rework budget remains → session →
        PARTIAL, a redacted feedback file is written, and the loop re-dispatches
        the session with that feedback appended. Bounded by ``max_rework``.
      - ``on_fail: halt`` OR rework budget exhausted → session → BLOCKED + halt.

Durability + idempotency reuse ``ship_state_io`` (durable JSON writer with
``.bak`` + fsync, validate-on-read) and bind to the manifest digest, so a rebuilt
plan refuses stale verify state (``state-drift``). The ``rework_count`` survives
across re-dispatches (each rework produces a NEW closeout, hence a new
``closeout_digest``) so ``max_rework`` is enforced across attempts, not reset.

The session stays in ``DOING`` for the whole dispatch→verify cycle — no new
dashboard status, so the load-bearing PLAN.html nav JS is untouched. A session
left ``DOING`` with a persisted ``_closeouts/<sid>.json`` and a pending
``_verify_state/<sid>.json`` is the crash-recovery signal: re-run with
``verify-begin --resume``.
"""

import sys as _sys
from datetime import UTC, datetime
from pathlib import Path

import article_block as ab
import closeout_pipeline as cp
import escalation as esca
import gate_policy as gp
import manifest_io as mio
import outcomes as outc
import render_verify as rv
import replan as rp
import run_state_io as rsi
import ship_state_io as ssio
import shipping as shp
import shipping_adapter as adapter
import structural_gate as sg
import stuck_protocol as sp
import worktree as wtree

GATE_PENDING = "pending"
GATE_PASSED = "passed"
GATE_FAILED = "failed"


def _now():
    return datetime.now(UTC).isoformat()


def _html(plan_dir):
    return Path(plan_dir) / "PLAN.html"


# --------------------------------------------------------------------------
# Resolution
# --------------------------------------------------------------------------
def _resolved_verify(manifest, session_id):
    s = mio.session_by_id(manifest).get(session_id)
    return (s.get("verify") if s else None) or None


def compute_gates(plan_dir, manifest, session_id):
    """Ordered gate step descriptors for a session's resolved ``verify`` block.

    Reuses ``shipping.resolve_gate`` so a verify gate and a ``pre_deploy_gate``
    with the same id resolve identically. Raises ``shipping.StepResolveError`` on
    an unknown gate id so the caller halts with a precise reason."""
    vb = _resolved_verify(manifest, session_id)
    if not vb:
        return []
    return [shp.resolve_gate(plan_dir, gid) for gid in (vb.get("gates") or [])]


# --------------------------------------------------------------------------
# State
# --------------------------------------------------------------------------
def verify_state_path(plan_dir, session_id):
    return Path(plan_dir) / "_verify_state" / f"{session_id}.json"


def _load_state(plan_dir, session_id):
    return ssio.read_json_with_bak(verify_state_path(plan_dir, session_id))


def _save_state(plan_dir, session_id, state):
    state["updated_at"] = _now()
    ssio.durable_write_json(verify_state_path(plan_dir, session_id), state)


def _feedback_path(plan_dir, session_id):
    return Path(plan_dir) / "_verify_state" / f"{session_id}.feedback.md"


def _new_state(plan_dir, manifest, session_id, gates, vb):
    co = cp.load_closeout(plan_dir, session_id)
    return {
        "session_id": session_id,
        "manifest_digest": mio.manifest_digest(plan_dir),
        "closeout_digest": co.get("_closeout_digest") if co else None,
        "gates": [g["name"] for g in gates],
        "on_fail": vb.get("on_fail", "rework"),
        "max_rework": int(vb.get("max_rework", 1)),
        "rework_count": 0,
        "human_checkpoint_reason": co.get("human_checkpoint_reason") if co else None,
        "require_evidence": bool(vb.get("require_evidence")) if vb else False,
        "gate_status": {g["name"]: GATE_PENDING for g in gates},
        "outcome": None,
        "started_at": _now(),
        "updated_at": _now(),
        "failures": {},
    }


def gate_cwd(plan_dir, session_id, gate):
    """Where an argv gate runs. For an ISOLATED PARALLEL MEMBER: inside its own
    worktree; for everything else (including the integration session, whose
    gates must test the MERGED tree): the resolved project cwd, unchanged.

    Contract M4: "Under isolation a per-member gate runs INSIDE that member's
    worktree, which resolves the common case" — a gate deriving its file set from
    the working tree would otherwise test its peers' half-finished edits. The
    gate's own relative position under the project root is preserved, so a gate
    declared with ``cwd: "backend"`` lands in ``<worktree>/backend``.
    """
    declared = gate.get("cwd")
    path = wtree.member_path(plan_dir, session_id)
    if not path:
        return declared
    if not declared:
        # A skill-kind gate carries no `cwd` at all (``shipping._resolve_gate``),
        # so its implicit location is the project root — whose counterpart under
        # isolation is the worktree root. Returning None here would silently send
        # every skill gate at the shared tree, which is the one thing M4 forbids.
        return path
    root = wtree.repo_root(declared) or wtree.repo_root(plan_dir)
    if root is None:
        return declared
    try:
        rel = Path(declared).resolve().relative_to(Path(root).resolve())
    except ValueError:
        # Gate cwd lives outside the repository — a worktree has no counterpart
        # for it, so leave it alone rather than invent one.
        return declared
    return str(Path(path) / rel)


def _gate_by_name(gates, name):
    for g in gates:
        if g["name"] == name:
            return g
    return None


def _first_pending(state):
    for name in state["gates"]:
        if state["gate_status"].get(name) != GATE_PASSED:
            return name
    return None


# --------------------------------------------------------------------------
# verify-begin
# --------------------------------------------------------------------------
def verify_begin(plan_dir, session_id, *, dry_run=False, resume=False):
    manifest = mio.load_manifest(plan_dir)
    gates = compute_gates(plan_dir, manifest, session_id)
    vb0 = _resolved_verify(manifest, session_id)
    require_ev = bool(vb0 and vb0.get("require_evidence"))
    # A verify block with no gates but require_evidence STILL runs — the evidence
    # assertion fires at verify-finalize. Only a truly empty block is a noop.
    if not gates and not require_ev:
        return {"action": "noop", "session": session_id}

    co = cp.load_closeout(plan_dir, session_id)
    result = co.get("result") if co else None
    # Only a claimed-complete session is verified. PARTIAL/BLOCKED never reach
    # here from a healthy apply, but guard anyway.
    if result != "DONE":
        return {"action": "noop", "session": session_id, "reason": f"result={result}"}

    state = _load_state(plan_dir, session_id)
    if state is not None:
        # State-drift: a rebuilt manifest invalidates prior verify state.
        if state.get("manifest_digest") != mio.manifest_digest(plan_dir):
            return _fail(plan_dir, session_id, "state-drift",
                         "manifest changed since verify state was written; "
                         f"delete {verify_state_path(plan_dir, session_id)} to re-verify",
                         dry_run)
        if state.get("outcome") == GATE_PASSED:
            return {"action": "already-verified", "session": session_id}
        if state.get("outcome") == "rework":
            # A prior attempt asked for rework; this is the fresh closeout's pass.
            new_digest = co.get("_closeout_digest") if co else None
            state["closeout_digest"] = new_digest
            state["human_checkpoint_reason"] = co.get("human_checkpoint_reason") if co else None
            state["gate_status"] = {g["name"]: GATE_PENDING for g in gates}
            state["gates"] = [g["name"] for g in gates]
            state["outcome"] = None
            _save_state(plan_dir, session_id, state)
    else:
        vb = _resolved_verify(manifest, session_id)
        state = _new_state(plan_dir, manifest, session_id, gates, vb)
        _save_state(plan_dir, session_id, state)
        rsi.log_event(plan_dir, "verify_started", session_ids=[session_id],
                      gates=state["gates"], attempt=state["rework_count"] + 1)
    return _advance(plan_dir, session_id, gates, state, dry_run)


def _advance(plan_dir, session_id, gates, state, dry_run):
    name = _first_pending(state)
    if name is None:
        state["outcome"] = GATE_PASSED
        _save_state(plan_dir, session_id, state)
        return {"action": "passed", "session": session_id}
    g = _gate_by_name(gates, name)
    if g["kind"] == "skill":
        out = {"action": "invoke-skill", "session": session_id, "gate": name,
               "skill": g["skill"], "args": g.get("args", ""), "step": name,
               "success_criteria": g.get("success_criteria")}
        # An isolated member's gate must look at THAT MEMBER's worktree, not the
        # shared tree its peers are still editing (contract M4). The orchestrator
        # invokes a skill-kind gate itself, so the location has to travel to it.
        wt_cwd = gate_cwd(plan_dir, session_id, g)
        if wt_cwd and wt_cwd != g.get("cwd"):
            out["cwd"] = wt_cwd
        return out
    # argv-kind gate: this helper runs it.
    if dry_run:
        return _run_gate(plan_dir, session_id, name, dry_run=True)
    return {"action": "run-argv", "session": session_id, "gate": name, "step": name}


# --------------------------------------------------------------------------
# verify-record (skill-kind outcome) / verify-run-argv (argv-kind)
# --------------------------------------------------------------------------
# A gate's failure output is not stderr — it IS the finding list, and the whole
# list is what the rework attempt has to act on. `adapter.redact`'s default 800
# chars is sized for a shipping stderr tail; applied here it kept only the LAST
# findings and silently dropped the first ones. That truncation destroyed real
# review findings twice (s01 2026-08-14, s03 2026-08-14) — in both cases the lost
# text was unrecoverable, because the raw gate output is not persisted anywhere
# else. Still bounded (redaction and a cap both still apply), just wide enough to
# hold a full findings array.
GATE_EXCERPT_MAX = 8000


def verify_record(plan_dir, session_id, gate, status, result_file=None):
    state = _load_state(plan_dir, session_id)
    if state is None:
        return {"action": "error", "message": f"no verify state for {session_id}"}
    gates = compute_gates(plan_dir, mio.load_manifest(plan_dir), session_id)
    excerpt = ""
    if result_file:
        try:
            excerpt = adapter.redact(Path(result_file).read_text(),
                                     max_len=GATE_EXCERPT_MAX)
        except OSError:
            excerpt = ""
    if status == "done":
        state["gate_status"][gate] = GATE_PASSED
        _save_state(plan_dir, session_id, state)
        return _advance(plan_dir, session_id, gates, state, dry_run=False)
    return _gate_failed(plan_dir, session_id, state, gate, excerpt)


def _run_gate(plan_dir, session_id, gate, *, dry_run=False):
    state = _load_state(plan_dir, session_id)
    gates = compute_gates(plan_dir, mio.load_manifest(plan_dir), session_id)
    g = _gate_by_name(gates, gate)
    if g is None or g["kind"] != "argv":
        return {"action": "error", "message": f"{gate} is not an argv-kind gate"}
    fake = g.get("fixture_fake")
    if dry_run and fake:
        res = {"returncode": fake.get("returncode", 0), "stdout": fake.get("stdout", ""),
               "stderr": fake.get("stderr", "")}
    elif dry_run:
        res = {"returncode": 0, "stdout": "(dry-run pass)", "stderr": ""}
    else:
        res = ssio.run_deploy_argv(g["argv"], cwd=gate_cwd(plan_dir, session_id, g),
                                   env_allowlist=g.get("env_allowlist", []),
                                   timeout=g.get("timeout", 1200))
    if res.get("returncode") == 0:
        state["gate_status"][gate] = GATE_PASSED
        _save_state(plan_dir, session_id, state)
        return _advance(plan_dir, session_id, gates, state, dry_run)
    excerpt = adapter.redact((res.get("stderr") or "") + (res.get("stdout") or ""),
                             max_len=GATE_EXCERPT_MAX)
    return _gate_failed(plan_dir, session_id, state, gate, excerpt)


def verify_run_argv(plan_dir, session_id, gate):
    return _run_gate(plan_dir, session_id, gate, dry_run=False)


# --------------------------------------------------------------------------
# Fail → rework or halt
# --------------------------------------------------------------------------
def _escalation_descriptor(plan_dir, session_id, steps=None):
    """The rung `begin` would compute for this session RIGHT NOW, or None.

    `steps` overrides the derived climb — used by the halt path, which must name
    the rung the attempt that JUST FAILED ran on, not the one a further attempt
    would have used."""
    import run as _run  # noqa: PLC0415 — lazy: run.py imports verify.py

    manifest = mio.load_manifest(plan_dir)
    session = mio.session_by_id(manifest).get(session_id)
    if not session:
        return None
    # The LANE, not the run-level dial: verify has no `--harness` flag, so it
    # recovers the lane from the session's own dispatch history plus the same
    # pre-pass rules `begin` applies. The event read is the LAST
    # `dispatch_started` (see `_dispatch_lane`, run.py) — deliberately NOT
    # `codex_dispatch`: run.py:1017 records that keying off the mere PRESENCE of
    # a `codex_dispatch` event is what pinned the lane to codex forever, so a
    # later edit "aligning" this code to the old wording would reintroduce it.
    provider, ssot_text = _run._load_routing()
    lane = _run._dispatch_lane(session, provider or "anthropic", ssot_text,
                               plan_dir=plan_dir)
    # THE CELL THE CLIMB STARTS FROM is lane-specific (ESC-03): on the codex lane
    # it is the RESOLVED (codex_model, codex_effort) pair, because the manifest's
    # own `model` may be a Claude token translation turned into a gpt-5.6 model —
    # and the openai ladder cannot be walked from `sonnet`. Resolved through the
    # SAME single translation surface `begin` uses, so the rung announced here and
    # the rung dispatched there are the same rung. A session that no longer
    # resolves simply gets no announcement (the dispatcher will block it loudly,
    # which is the right place for that error to appear).
    if lane == "codex":
        try:
            model, reasoning = _run._resolve_codex_dispatch(
                session.get("model"), session.get("reasoning"), ssot_text,
                task_class=(session.get("task_class") or "").strip().lower() or None,
            )
        except _run.UnroutableCodexSession:
            return None
    else:
        model = _run._normalize_model(session.get("model"))
        reasoning = _run._reasoning_tier(session.get("reasoning"))
    return _run._escalation_descriptor(
        plan_dir, manifest, session, model, reasoning, steps=steps, lane=lane,
    )


def _escalation_next(plan_dir, session_id):
    """`{authored, ran, rung}` for the next dispatch when it will run ABOVE the
    authored cell, else None.

    Whether the climb will actually BIND is decided by the same
    `run._escalation_descriptor` the dispatcher calls, which flattens a
    non-binding climb to rung 0 — a session pinned to a functional Claude agent
    therefore yields None here and is never told it escalated when it did not. A
    codex-lane session DOES climb since ESC-03, and is announced with the rung it
    will actually run (its gpt-5.6 cell, not its manifest token)."""
    desc = _escalation_descriptor(plan_dir, session_id)
    if not desc or desc["rung"] == 0:
        return None
    # `authored` is the ladder's BASE RUNG — the cell this session resolves to on
    # its own lane. On the Claude lane that equals the manifest token, so the two
    # were used interchangeably. On the codex lane they are NOT the same: a
    # session the manifest authors as `Opus`@`high` resolves to a gpt-5.6 cell,
    # and calling that "what the plan authored" tells the subagent it was
    # authored as a model no human ever wrote down. Carry the manifest's own
    # declaration alongside, so the note can say both without inventing either.
    session = mio.session_by_id(mio.load_manifest(plan_dir)).get(session_id) or {}
    return {"authored": desc["authored"], "ran": desc["ran"], "rung": desc["rung"],
            "model_ran_source": desc["model_ran_source"],
            "declared": {"model": session.get("model"),
                         "reasoning": session.get("reasoning")}}


def _authored_sentence(nxt):
    """The opening sentence of the escalation note, true on BOTH lanes.

    The base rung and the manifest's own token coincide on the Claude lane and
    diverge on the codex lane (see `_escalation_next`). When they diverge, say
    both rather than picking one — claiming the plan authored a gpt-5.6 cell is
    false, and hiding the base rung leaves the raise unexplained.
    """
    base = f"`{nxt['authored']['model']}@{nxt['authored']['reasoning'] or 'unset'}`"
    dm, dr = nxt.get("declared", {}).get("model"), nxt.get("declared", {}).get("reasoning")
    declared = f"`{dm}@{dr or 'unset'}`" if dm else None
    if not declared or declared.lower() == base.lower():
        return f"The plan authored this session as {base}."
    return (f"The plan authored this session as {declared}, which resolves on this "
            f"session's lane to {base} — the base rung of the ladder below.")


def _escalation_status(plan_dir, session_id):
    """One line naming the rung this session ACTUALLY REACHED and the rungs above
    it that the exhausted rework budget never bought. "" when the session never
    escalated at all (pre-v6 manifest, opt-out, unpinned model, or a budget that
    ran out before the climb ever armed).

    The position comes from `escalation.last_climb` — what `begin` RECORDED at
    dispatch, not a re-derivation. Re-deriving it here answered a different
    question: by halt time the failure counters have moved past the attempt that
    just failed, and when that final failure carried a DIFFERENT signature from
    the streak that drove the climb, the streak has already reset — so the brief
    named a lower rung than the one that ran and listed rungs that WERE tried as
    untried.

    `last_climb`, not `last_rung`, so a REFUSED rung is still described. Feeding
    the climb back through `escalation.compute` re-applies the refusal step-down,
    which lands the reported rung back on what actually ran AND populates
    `refused_skipped` — so the brief can say "every escalated rung was refused"
    where that is the truth, instead of falling silent because the refusal had
    pushed the recorded rung back to 0."""
    return esca.brief_line(
        _escalation_descriptor(plan_dir, session_id,
                               steps=esca.last_climb(plan_dir, session_id))
    )


def _gate_failed(plan_dir, session_id, state, gate, excerpt):
    state["gate_status"][gate] = GATE_FAILED
    state["failures"][gate] = excerpt
    fb = _feedback_path(plan_dir, session_id)
    fb.parent.mkdir(parents=True, exist_ok=True)
    attempt = state["rework_count"] + 1

    # RS-03 — the stuck protocol's TRIGGER, recorded rather than asserted. Every
    # failure here reduces to a normalised root-cause signature kept in run_state;
    # the SECOND consecutive failure with the same signature arms the protocol and
    # the research pass goes into the feedback the re-dispatch actually reads. A
    # different error next time is progress and resets the counter — which is why
    # this counts signatures, not attempts.
    stuck = sp.record_failure(plan_dir, session_id, excerpt)
    # CLEAN-BRIEF CONTRACT (ESC-02): what an escalated re-dispatch reads is exactly
    # this — the ORIGINAL session prompt plus this redacted, bounded file. Never a
    # prior attempt's transcript. Keeping the rung note in THIS file (rather than
    # anywhere upstream) makes that guarantee mechanical: there is one file, and it
    # is the only thing the re-dispatch gains.
    fb.write_text(
        f"# Verification feedback — {session_id} (attempt {attempt})\n\n"
        f"Gate `{gate}` FAILED. Fix the cause, then this session re-runs and "
        f"re-verifies.\n\n## Gate output (redacted, tail)\n\n```\n{excerpt}\n```\n"
        + (sp.research_prompt(stuck) if stuck["triggered"] else "")
    )
    if stuck["triggered"]:
        rsi.log_event(plan_dir, "stuck_protocol_armed", session_ids=[session_id],
                      gate=gate, signature=stuck["sig"], error_class=stuck["class"],
                      consecutive=stuck["consecutive"])

    rework_ok = state.get("on_fail", "rework") == "rework" and state["rework_count"] < state["max_rework"]
    if rework_ok:
        state["rework_count"] += 1
        state["outcome"] = "rework"
        _save_state(plan_dir, session_id, state)
        ab.apply_mutation(_html(plan_dir), session_id, status="PARTIAL",
                          note=f"verify rework {state['rework_count']}/{state['max_rework']}: "
                               f"gate {gate} failed")
        rsi.log_event(plan_dir, "verify_rework", session_ids=[session_id], gate=gate,
                      attempt=state["rework_count"], max_rework=state["max_rework"])
        # TEL-01 — one record per resolution moment: a rework is recorded here,
        # not just at the eventual pass/halt, so attempts-per-success is
        # computable from the ledger alone.
        outc.write(plan_dir, session_id, resolution=f"verify_rework:{gate}", result="rework",
                  verified=False, attempt=state["rework_count"], rework_count=state["rework_count"],
                  gates_failed=sorted(state.get("failures") or {})[:3],
                  stuck_armed=stuck["triggered"])
        # ESC-02 — the rung the NEXT dispatch will compute, surfaced here so the
        # orchestrator can announce it. Advisory only: `begin` re-derives it from
        # the same inputs and is the single authority, so a crash between these
        # two points cannot double-climb.
        try:
            nxt = _escalation_next(plan_dir, session_id)
        except Exception as e:  # noqa: BLE001 — never let diagnostics break the rework
            # ...but never SILENTLY either: a swallowed TypeError here would make
            # the climb look declined rather than broken, and the tests would still
            # be green. Say it on stderr and carry on with the rework.
            print(f"escalation: could not derive the next rung for {session_id} ({e})",
                  file=_sys.stderr)
            nxt = None
        if nxt:
            # Wording states the RUNG, never a delta. Once a session has climbed it
            # never hands the model back (a different error is progress, and progress
            # does not cost you the bigger model), so a later attempt can HOLD this
            # rung rather than rise to it — and "runs one rung UP" would then be
            # prose that is also false.
            fb.write_text(fb.read_text() + (
                f"\n## Escalation — this attempt runs ABOVE the authored tier\n\n"
                f"{_authored_sentence(nxt)} Because the same root cause "
                f"has now failed repeatedly, the standing ladder raises it to "
                f"`{nxt['ran']['model']}@{nxt['ran']['reasoning'] or 'unset'}` "
                f"(rung {nxt['rung']}) for this attempt.\n\n"
                "A stronger model is not a licence to retry the same approach harder. The "
                "research pass above rides along with this rung precisely so the diagnosis "
                "changes before the model does.\n"
            ))
        return {"action": "rework", "session": session_id, "gate": gate,
                "attempt": state["rework_count"], "max_rework": state["max_rework"],
                "feedback_file": str(fb),
                **({"escalation_next": nxt} if nxt else {}),
                **({"stuck_protocol": stuck} if stuck["triggered"] else {})}

    state["outcome"] = "halted"
    _save_state(plan_dir, session_id, state)
    reason = (f"verify gate {gate} failed; on_fail={state.get('on_fail')}, "
              f"rework {state['rework_count']}/{state['max_rework']} exhausted")
    # ESC-02 — when the budget runs out MID-LADDER, say where the climb stopped
    # and which rungs were never tried. Without this the BLOCKED brief reads
    # "we tried and failed" when the truth is "we stopped one rung short", and
    # the operator cannot tell those apart. Silent when no climb ever armed.
    # Best-effort: a missing manifest or resolver must not swallow the halt reason.
    try:
        ladder = _escalation_status(plan_dir, session_id)
    except Exception as e:  # noqa: BLE001 — diagnostics must never mask the halt
        print(f"escalation: could not describe the ladder for {session_id} ({e})",
              file=_sys.stderr)
        ladder = ""
    if ladder:
        reason += f". {ladder}"
    ab.apply_mutation(_html(plan_dir), session_id, status="BLOCKED", note=reason)
    rsi.set_halt(plan_dir, f"{session_id}: {reason}", session_id)
    rsi.log_event(plan_dir, "verify_failed", session_ids=[session_id], gate=gate,
                  excerpt=excerpt[:200])
    # TEL-01 — rework exhausted (or on_fail: halt fired on the first failure);
    # either way there is no result enum value beyond "exhausted" for a verify
    # halt, so both causes record the same result — the halt reason string
    # (not a ledger field) is where the distinction actually lives.
    outc.write(plan_dir, session_id, resolution=f"verify_halt:{gate}", result="exhausted",
              verified=False, attempt=state["rework_count"] + 1, rework_count=state["rework_count"],
              gates_failed=sorted(state.get("failures") or {})[:3],
              stuck_armed=stuck["triggered"])
    return {"action": "halted", "session": session_id, "gate": gate, "reason": reason,
            "feedback_file": str(fb),
            **({"stuck_protocol": stuck} if stuck["triggered"] else {})}


# --------------------------------------------------------------------------
# Evidence gate (Vista ③) — structural "mechanism engagement" assertion
# --------------------------------------------------------------------------
def _check_evidence(plan_dir, session_id):
    """A ``require_evidence`` session must carry an ``evidence`` list in its
    closeout whose every path exists and is non-empty. This converts the
    "verify the fix actually engaged" discipline (grep the log; if count==0 it's
    a no-op) from remembered vigilance into a refusal-to-finalize-without-proof.

    Relative paths resolve against the PLAN DIRECTORY first (the convention the
    session prompts declare — ``_evidence/<sid>/…``), falling back to ``cwd``
    (project-root-relative paths like ``docs/operations/…``). Returns
    ``(ok, excerpt)``."""
    co = cp.load_closeout(plan_dir, session_id)
    ev = (co or {}).get("evidence")
    if not isinstance(ev, list) or not ev:
        return False, (
            "require_evidence is set for this session but its closeout carried no "
            "`evidence` array. Add an `evidence` list of paths (eval JSON, log-grep "
            "output, a screenshot, a command transcript) that PROVE the work engaged "
            "— a count==0 grep is a no-op no matter what the metrics say — then "
            "return DONE again."
        )
    problems = []
    for p in ev:
        if not isinstance(p, str) or not p.strip():
            problems.append(f"{p!r} (not a non-empty path string)")
            continue
        path = Path(p)
        if not path.is_absolute():
            in_plan = Path(plan_dir) / path
            path = in_plan if in_plan.exists() else Path.cwd() / path
        if not path.exists():
            problems.append(f"{p} (does not exist)")
        elif path.is_file() and path.stat().st_size == 0:
            problems.append(f"{p} (empty file — no proof inside)")
        elif path.is_dir() and not any(path.iterdir()):
            problems.append(f"{p} (empty directory)")
    if problems:
        return False, ("Declared evidence failed the presence / non-empty check:\n  - "
                       + "\n  - ".join(problems))
    return True, ""


def _check_named_checks(plan_dir, session_id):
    """Assert the manifest's ``verify.checks`` artifact contracts.

    Each ``checks[]`` entry names an ``evidence_path`` the session MUST have
    produced. plan-builder's docs promise "the runner asserts each path exists
    and is non-empty" — before 2026-08-07 nothing did (the checks were rendered
    into prompts but never executed; found by an adversarial review of a live
    plan). The ``assert`` field stays a human-readable semantic contract; this
    function enforces the structural half: existence + non-emptiness, with the
    same plan-dir-then-cwd resolution ``_check_evidence`` uses. Runs whenever
    ``checks`` is non-empty — independent of ``require_evidence`` (the schema
    allows checks to stand alone). Returns ``(ok, excerpt)``."""
    try:
        vb = _resolved_verify(mio.load_manifest(plan_dir), session_id)
    except Exception:  # noqa: BLE001 — no manifest → nothing to assert
        return True, ""
    checks = (vb or {}).get("checks") or []
    problems = []
    for c in checks:
        if not isinstance(c, dict):
            continue
        p = c.get("evidence_path")
        name = c.get("name") or "<unnamed>"
        if not isinstance(p, str) or not p.strip():
            problems.append(f"check '{name}': evidence_path missing/empty in manifest")
            continue
        path = Path(p)
        if not path.is_absolute():
            in_plan = Path(plan_dir) / path
            path = in_plan if in_plan.exists() else Path.cwd() / path
        if not path.exists():
            problems.append(f"check '{name}': {p} (does not exist)"
                            + (f" — must show: {c.get('assert')}" if c.get("assert") else ""))
        elif path.is_file() and path.stat().st_size == 0:
            problems.append(f"check '{name}': {p} (empty file — no proof inside)")
    if problems:
        return False, ("Named evidence-artifact contracts (verify.checks) failed:\n  - "
                       + "\n  - ".join(problems))
    return True, ""


# --------------------------------------------------------------------------
# verify-finalize
# --------------------------------------------------------------------------
def verify_finalize(plan_dir, session_id, _state=None, *, skip_evidence=False):
    state = _state or _load_state(plan_dir, session_id)
    if state is None:
        return {"action": "error", "message": f"no verify state for {session_id}"}
    if _first_pending(state) is not None:
        return {"action": "error", "message": "verify not complete; gates still pending"}

    # Evidence assertion runs LAST — after every gate has passed — so a session is
    # finalized only when its proof artifacts are actually on disk. A miss reworks
    # or halts exactly like a failed gate (synthetic gate name ``evidence``).
    if state.get("require_evidence") and not skip_evidence:
        ok, excerpt = _check_evidence(plan_dir, session_id)
        if not ok:
            return _gate_failed(plan_dir, session_id, state, "evidence", excerpt)

    # Named artifact contracts (verify.checks) are asserted independently of
    # require_evidence — a standalone checks block is valid per the schema and
    # was silently inert before 2026-08-07.
    if not skip_evidence:
        ok, excerpt = _check_named_checks(plan_dir, session_id)
        if not ok:
            return _gate_failed(plan_dir, session_id, state, "evidence", excerpt)

    state["outcome"] = GATE_PASSED
    _save_state(plan_dir, session_id, state)

    hc = state.get("human_checkpoint_reason")
    # OR-03: apply the same per-gate notify-and-continue policy the run.cmd_apply
    # (non-verify) path applies to the AWAITS_REVIEW-ack gate. A rubber-stamp gate
    # on an opted-in session auto-continues to DONE with a notification + a
    # gate_auto_continue event instead of parking in AWAITS_REVIEW. Fail-closed:
    # requires_human_checkpoint / irreversible / non-allowlisted → still blocks.
    hc_dispatch = None
    try:
        s = mio.session_by_id(mio.load_manifest(plan_dir)).get(session_id)
        hc_dispatch = s.get("dispatch") if s else None
    except Exception:  # noqa: BLE001 — resolve conservatively (→ block) on any error
        hc_dispatch = None
    hc_auto_continue = bool(hc) and gp.is_notify_and_continue("session_review_ack", hc_dispatch)
    if hc and hc_auto_continue:
        final_status = "DONE"
        note = "verified; session complete (gate auto-continued — notify-and-continue)"
    elif hc:
        final_status = "AWAITS_REVIEW"
        note = f"verified; checkpoint: {hc}"
    else:
        final_status = "DONE"
        note = "verified; session complete"
    ab.apply_mutation(_html(plan_dir), session_id, status=final_status, note=note)

    # PS-01 structural DONE-gate (PRIMARY, browser-free) — same discipline as
    # run.cmd_apply's item-level check, applied here to the session-level
    # write this function just made (the write cmd_apply deferred while a
    # verify block was pending). Re-read PLAN.html from disk; a mismatch means
    # the dashboard did NOT actually flip even though apply_mutation ran, and
    # that is treated exactly like a failed gate — rework or halt, never a
    # silent DONE.
    gate = sg.run_gate(plan_dir, {session_id: final_status})
    if gate["status"] == "failed":
        return _gate_failed(plan_dir, session_id, state, "structural", "; ".join(gate["reasons"]))

    # SECONDARY, best-effort visual confirmation. Never blocking on its own —
    # an environment with no headless Chrome must still be able to finish —
    # but a CONFIRMED real layout finding (not merely "unavailable") is treated
    # the same as a failed gate: the dashboard is genuinely broken, not just
    # unconfirmed.
    try:
        render = rv.check(plan_dir)
    except Exception as e:  # pragma: no cover - defensive
        render = {"status": "unavailable", "reason": f"render-verify errored: {e}"}
    if render.get("status") == "failed":
        return _gate_failed(
            plan_dir, session_id, state, "render",
            f"headless-Chrome layout-audit banner found a real finding: {render.get('reason')}",
        )

    # RP-05 — a verify-gated session's `plan_impact` parks HERE, not at `apply`:
    # a discovery that invalidates future work is only worth an operator's
    # attention once the session that made it has passed its own gates. When the
    # session ALSO parks on a human checkpoint, that gate goes first and
    # `ack-checkpoint` raises the REPLAN afterwards (same precedence as apply).
    replan = None
    if final_status != "AWAITS_REVIEW":
        manifest = mio.load_manifest(plan_dir)
        try:
            impact = rp.closeout_impact(cp.load_closeout(plan_dir, session_id), manifest)
        except (OSError, ValueError):
            impact = None
        if impact:
            replan = rp.park(plan_dir, session_id, impact, manifest)

    # TEL-01 — verify-finalize(passed): every gate (structural + render + the
    # declared verify block) has now actually passed. One record here, before
    # the AWAITS_REVIEW/DONE branching below, since "passed" is the same fact
    # on all three paths — only the human-checkpoint routing differs.
    last_fail = sp.last_failure(plan_dir, session_id)
    outc.write(plan_dir, session_id, resolution="verify_finalize", result="passed",
              verified=True, attempt=state["rework_count"] + 1, rework_count=state["rework_count"],
              stuck_armed=bool(last_fail and last_fail.get("consecutive", 0) >= sp.TRIGGER_AT))

    if hc and hc_auto_continue:
        rsi.log_event(plan_dir, "verify_passed", session_ids=[session_id], then="DONE")
        rsi.log_event(
            plan_dir, "gate_auto_continue", session_ids=[session_id],
            gate_type="session_review_ack", reason=hc, policy="notify-and-continue",
        )
        notify = rsi.notify_gate_continue(plan_dir, session_id, "session_review_ack", hc)
        rsi.log_event(
            plan_dir, "gate_notify", session_ids=[session_id],
            gate_type="session_review_ack", notify=notify.get("action"),
        )
        return {"action": "done", "session": session_id, "final_status": "DONE",
                "gate_auto_continue": True, "structural_gate": gate, "render_verify": render,
                **({"replan": replan} if replan else {})}
    if hc:
        rsi.log_event(plan_dir, "verify_passed", session_ids=[session_id], then="AWAITS_REVIEW")
        rsi.log_event(plan_dir, "checkpoint_reached", session_ids=[session_id], reason=hc)
        return {"action": "done", "session": session_id, "final_status": "AWAITS_REVIEW",
                "human_checkpoint_reason": hc, "structural_gate": gate, "render_verify": render}
    rsi.log_event(plan_dir, "verify_passed", session_ids=[session_id], then="DONE")
    return {"action": "done", "session": session_id, "final_status": "DONE",
            "structural_gate": gate, "render_verify": render,
            **({"replan": replan} if replan else {})}


# --------------------------------------------------------------------------
# helpers / status / simulate
# --------------------------------------------------------------------------
def _fail(plan_dir, session_id, reason, message, dry_run):
    if not dry_run:
        ab.apply_mutation(_html(plan_dir), session_id, status="BLOCKED", note=f"verify {reason}")
        rsi.set_halt(plan_dir, f"{session_id}: verify {reason} — {message}", session_id)
        rsi.log_event(plan_dir, "verify_failed", session_ids=[session_id], reason=reason)
    return {"action": "failed", "session": session_id, "reason": reason, "message": message}


def verify_status(plan_dir, session_id):
    manifest = mio.load_manifest(plan_dir)
    gates = [g["name"] for g in compute_gates(plan_dir, manifest, session_id)]
    state = _load_state(plan_dir, session_id)
    return {"session": session_id, "gates": gates,
            "state": state and {k: state[k] for k in
                                ("gate_status", "outcome", "rework_count", "max_rework",
                                 "require_evidence")
                                if k in state}}


def verify_simulate(plan_dir, session_id):
    """CI / smoke: run the full verify pipeline, auto-passing every gate (no real
    skill / real command). Produces real state + events."""
    out = verify_begin(plan_dir, session_id, dry_run=True)
    guard = 0
    while out.get("action") in ("invoke-skill", "run-argv") and guard < 50:
        guard += 1
        if out["action"] == "invoke-skill":
            state = _load_state(plan_dir, session_id)
            state["gate_status"][out["gate"]] = GATE_PASSED
            _save_state(plan_dir, session_id, state)
            gates = compute_gates(plan_dir, mio.load_manifest(plan_dir), session_id)
            out = _advance(plan_dir, session_id, gates, state, dry_run=True)
        else:
            out = _run_gate(plan_dir, session_id, out["gate"], dry_run=True)
    if out.get("action") == "passed":
        out = verify_finalize(plan_dir, session_id, skip_evidence=True)
    return out
