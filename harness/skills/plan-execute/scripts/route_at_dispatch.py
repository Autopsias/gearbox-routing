"""Route at dispatch — the executor half of plan schema v8.

Authority: ``../references/route-at-dispatch-contract.md`` (v1), sections 3 and 4.
From v8 a session may name only its ``task_class``. The first ``begin`` of an
escalation generation resolves the class default for the provider of the lane
the session runs on, and freezes ``{model, reasoning, routing_version}`` in
run_state.json under ``resolved_cell``. Every reader of the session's cell reads
it back through ``effective_cell``; nothing calls ``resolve()`` a second time, so
a routing-file change mid-plan cannot move the base of the escalation ladder.

Kept out of run.py, which is pinned by the file-size ratchet. A manifest below v8
never reaches any of this: ``effective_session`` hands back the SAME session
object, so a v7 dispatch payload stays byte-identical.
"""
import copy
import os
import re
import sys
from pathlib import Path

import escalation_state as est
import manifest_io as mio
import plan_version_gate as pvg
import provider_lane as pl
import run_state_io as rsi

FREEZE_KEY = "resolved_cell"
SWITCH_KEY = "route_at_dispatch"
ENV = "PLAN_EXECUTE_ROUTE_AT_DISPATCH"
_ENV_OFF = ("0", "false", "off", "no")
_PAIR = ("model", "reasoning")
_BUILDER = Path(__file__).resolve().parents[2] / "plan-builder" / "scripts"


def is_v8(manifest):
    v = (manifest or {}).get("plan_schema_version")
    return isinstance(v, int) and not isinstance(v, bool) and v >= pvg.ROUTE_AT_DISPATCH_MIN_SCHEMA


def _set(session, key):
    return bool(str(session.get(key) or "").strip())


def pinned(session):
    """Did the author write an override? Either half counts: begin refuses a lone
    half at v8, and a v8 manifest carries ``model: ""`` when none was written."""
    return any(_set(session, k) for k in _PAIR)


# What `prepare` decided, held in memory until `commit` runs under the plan lock:
# begin's own pre-lock readers see it, run_state.json does not. The cells carry NO
# generation: a redispatch or amend can land before the lock, so `commit` reads the
# generation under it. The cells were resolved from the manifest and routing text
# `begin` loaded BEFORE the lock, so `prepare` keeps a copy of each — taken before
# it resolves anything — and `commit` compares (never re-resolves) under it. A begin
# that stops before the lock leaves this to die with the process; the next
# `prepare` replaces it.
_pending = {}


def _read_text(path):
    """The routing file read exactly as `provider_lane.load_routing` reads it, or
    None when it cannot be — the shape begin's own ``ssot_text`` has."""
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError:
        return None


def _manifest_now(plan_dir):
    try:
        return mio.load_manifest(plan_dir)
    except (mio.ManifestError, OSError):
        return None


def switch_on(plan_dir):
    """The switch as `begin` last recorded it; on when never recorded. While it is
    off it wins over every frozen cell (contract §3.6)."""
    p = _pending.get(str(plan_dir))
    sw = p["switch"] if p else rsi.load_state(plan_dir).get(SWITCH_KEY)
    return (sw or {}).get("enabled") is not False


def frozen_cell(plan_dir, session_id):
    """The cell frozen for this session's CURRENT escalation generation, or None.
    A redispatch or amend bumps the generation, which is what makes it resolve again.
    Between `prepare` and `commit`, this begin's own decision is the answer."""
    p = _pending.get(str(plan_dir))
    if p and session_id in p["cells"]:
        return p["cells"][session_id]
    rec = (rsi.load_state(plan_dir).get(FREEZE_KEY) or {}).get(session_id)
    gen = est.session_state(plan_dir, session_id)["generation"]
    return rec if isinstance(rec, dict) and rec.get("generation") == gen else None


def effective_cell(plan_dir, manifest, session):
    """``(model, reasoning)`` for every reader of a session's cell: the frozen cell
    for an unpinned v8 session while route-at-dispatch is on, else the authored
    pair. A missing freeze is said out loud — downstream it reads as "no model",
    which silently turns the escalation ladder off."""
    if is_v8(manifest) and not pinned(session) and switch_on(plan_dir):
        rec = frozen_cell(plan_dir, session["id"])
        if rec:
            return rec["model"], rec["reasoning"]
        print(f"WARNING route-at-dispatch: session {session['id']} names no model and has no "
              "frozen cell for its current generation — `begin` has not resolved it, so this "
              "reader sees no model.", file=sys.stderr)
    return session.get("model"), session.get("reasoning")


def effective_session(plan_dir, manifest, session):
    """The session with its effective cell in place — the SAME object when nothing
    was resolved, so every pre-v8 path is untouched."""
    cell = effective_cell(plan_dir, manifest, session)
    if cell == (session.get("model"), session.get("reasoning")):
        return session
    return {**session, "model": cell[0], "reasoning": cell[1]}


def escalation_provider(plan_dir, manifest, session_id, lane):
    """The ladder a climb walks. An unpinned v8 session climbs the ladder of the
    provider its cell was FROZEN for — a reader in another process (verify's rework
    announcement, off the GLM tree) must not walk a zai cell up the anthropic ladder.
    Everything else, every pre-v8 session included, is unchanged."""
    session = next((s for s in (manifest or {}).get("sessions", []) if s.get("id") == session_id),
                   None)
    if session and is_v8(manifest) and not pinned(session) and switch_on(plan_dir):
        rec = frozen_cell(plan_dir, session_id)
        if rec and rec.get("provider") and (rec["provider"] == "openai") == (lane == "codex"):
            return rec["provider"]
    return pl.escalation_provider(lane)


def provenance(manifest, session):
    """The ledger's ``routing_provenance`` at v8, or None below it. Decided by what
    the author WROTE, never by comparing cells: an override equal to the class
    default is still ``pinned_override`` (contract §4)."""
    if not is_v8(manifest):
        return None
    return "pinned_override" if pinned(session) else "default_resolved"


def routing_version(ssot_text):
    m = re.search(r"^version:\s*(\d+)", ssot_text or "", re.M)
    return int(m.group(1)) if m else None


class _Refused(Exception):
    pass


def _resolve(plan_dir, session, provider, resolver, ssot_path, version):
    """The cell this session dispatches at — its standing freeze, or a fresh
    resolve — WITHOUT a generation: `commit` stamps that under the plan lock."""
    sid = session["id"]
    tc = str(session.get("task_class") or "").strip().lower()
    cur = frozen_cell(plan_dir, sid)
    if cur and cur.get("provider") == provider:
        if cur.get("routing_version") != version:
            print(f"route-at-dispatch: {sid} keeps its frozen cell {cur['model']}@"
                  f"{cur['reasoning'] or 'unset'} from routing v{cur.get('routing_version')}; "
                  f"the routing file is now v{version}.", file=sys.stderr)
        return {k: v for k, v in cur.items() if k != "generation"}
    if cur:
        print(f"route-at-dispatch: {sid} was frozen for provider {cur.get('provider')!r} but now "
              f"runs on {provider!r} — resolving its class default there.", file=sys.stderr)
    try:
        got = resolver().resolve(tc, provider, ssot_path=ssot_path)
    except Exception as e:  # noqa: BLE001 — any gap is a refusal, never an inherit
        raise _Refused(f"{sid}: task_class {tc!r} does not resolve on provider {provider!r} "
                       f"({e})") from e
    if not isinstance(got, dict) or not got.get("model_id"):
        raise _Refused(f"{sid}: task_class {tc!r} resolves to no cell on provider {provider!r} "
                       f"(got {got!r})")
    return {"model": got["model_id"], "reasoning": got.get("native_effort") or "",
            "routing_version": version, "provider": provider, "task_class": tc}


def _override_refusal(session, provider, normalize, codex_route, reasoning_tier):
    """Contract §4 at begin, as defence in depth for a hand-edited manifest: the
    pair rule, an effort level and a model the lane can dispatch, and the floor for
    a risky session (the builder's own walk)."""
    sid = session["id"]
    have = [k for k in _PAIR if _set(session, k)]
    if len(have) == 1:
        missing = next(k for k in _PAIR if k not in have)
        return (f"{sid}: sets {have[0]} without {missing} — at plan_schema_version 8 model and "
                "reasoning are an override pair: set both (with why_model) or neither")
    model = str(session.get("model") or "").strip()
    # Every lane reads a pinned effort through run.py's `_reasoning_tier`, and an
    # unknown value normalises to '': no tier agent, no directive, no Codex effort
    # of its own — the session silently runs at the orchestrator's (or `standard`).
    # A known level with no tier agent (sonnet xhigh, every max) is NOT refused:
    # it dispatches through session-effort-worker and begin warns about it.
    if not reasoning_tier(session.get("reasoning")):
        return (f"{sid}: pinned reasoning {session.get('reasoning')!r} is not an effort level "
                f"plan-execute knows for model {model!r} (run.py _REASONING_DIRECTIVE). It "
                "would normalise to none, so no tier agent, directive or Codex effort binds "
                "it and the session silently runs at a default effort. Pin a known level")
    if provider == "openai":
        try:
            codex_route(session)
        except Exception as e:  # noqa: BLE001 — the Codex route's own refusal, verbatim
            return f"{sid}: pinned model {model!r} does not route on the codex lane ({e})"
    elif normalize(model) is None:
        return (f"{sid}: pinned model {model!r} normalises to no Claude model token, so it would "
                f"run on the orchestrator's model. Pin a model the {provider} lane dispatches")
    tc = str(session.get("task_class") or "").strip().lower()
    if not (session.get("peer_triggers") or tc == "linchpin"):
        return None
    try:
        if str(_BUILDER) not in sys.path:
            sys.path.insert(0, str(_BUILDER))
        import route_at_dispatch_build as radb  # noqa: PLC0415 — only a risky override needs it

        radb._refuse_below_floor(session, sid, tc)
    except Exception as e:  # noqa: BLE001 — below the floor, or a floor that cannot be checked
        return f"{sid}: {e}"
    return None


def record_gate(plan_dir, enabled, reason, version):
    """Log the switch AND persist it, like `record_isolation_gate`: readers after
    `begin` (verify, apply, the ledger) never see the flag itself."""
    rsi.log_event(plan_dir, "route_at_dispatch_gate", session_ids=[], enabled=enabled,
                  reason=reason, plan_schema_version=version)
    state = rsi.load_state(plan_dir)
    state[SWITCH_KEY] = {"enabled": bool(enabled), "reason": reason}
    rsi.save_state(plan_dir, state)


def prepare(plan_dir, manifest, sessions, no_route, resolver, ssot_path, ssot_text,
            lane_provider, normalize, codex_route, reasoning_tier):
    """`begin`'s route-at-dispatch step, BEFORE the plan lock: it writes nothing.
    Every refusal for the batch is raised at once; what it decides is held for
    ``commit``, which `begin` calls once the lock is held.

    ``lane_provider(session)`` names the provider of the lane the session will run
    on (``--harness codex`` → openai, else provider_lane's order, with the lane's
    own profile when the openai dial fails a session closed to Claude).
    ``normalize``, ``codex_route`` and ``reasoning_tier`` are run.py's own Claude
    normaliser, Codex route and effort normaliser, so a pinned cell is checked by
    the code that will dispatch it."""
    _pending.pop(str(plan_dir), None)
    # The baseline is what the cells are resolved FROM, captured before the loop: a
    # baseline read after it would accept a change that landed mid-resolution.
    baseline = copy.deepcopy(manifest)
    version = (manifest or {}).get("plan_schema_version")
    if isinstance(version, int) and version > pvg.SUPPORTED_MAX_SCHEMA:
        raise SystemExit(
            f"refusing to begin: manifest plan_schema_version {version} is above the highest "
            f"this plan-execute supports ({pvg.SUPPORTED_MAX_SCHEMA}). Update plan-execute; "
            "never run a newer plan on an older executor.")
    if not is_v8(manifest):
        return
    off = no_route or os.environ.get(ENV, "").strip().lower() in _ENV_OFF
    enabled, reason = pvg.route_at_dispatch_enabled(manifest, False if off else None)
    rv = routing_version(ssot_text)
    refusals, cells = [], {}
    for s in sessions:
        if pinned(s):
            why = _override_refusal(s, lane_provider(s), normalize, codex_route, reasoning_tier)
            if why:
                refusals.append(why)
        elif not enabled:
            refusals.append(
                f"{s['id']}: names no model and route-at-dispatch is off ({reason}). Under the "
                "switch only authored cells dispatch; an unpinned session never inherits the "
                "orchestrator's model. Pin it (amend-session --model/--reasoning) or drop the switch")
        else:
            try:
                cells[s["id"]] = _resolve(plan_dir, s, lane_provider(s), resolver, ssot_path, rv)
            except _Refused as e:
                refusals.append(f"{e} — refusing rather than inheriting the orchestrator's model")
    if refusals:
        raise SystemExit("refusing to begin (route-at-dispatch):\n  " + "\n  ".join(refusals))
    _pending[str(plan_dir)] = {"switch": {"enabled": bool(enabled), "reason": reason},
                               "cells": cells, "version": version, "ssot_path": ssot_path,
                               "manifest": baseline, "ssot_text": ssot_text}


def commit(plan_dir):
    """Persist what ``prepare`` decided. `begin` calls it right after
    ``rsi.acquire_lock``, so two concurrent begins never race on run_state.json.
    Each cell is stamped with the generation read HERE, under the lock, not the one
    `prepare` saw: a redispatch or amend that landed in between would otherwise
    leave a freeze no reader matches, and the ladder would see no model."""
    p = _pending.pop(str(plan_dir), None)
    if not p:
        return
    # Compare-and-set: the cells were resolved from the manifest and routing text
    # `begin` loaded before the lock. If either moved since, an amend or a
    # routing bump landed mid-begin and the resolved cells describe a plan that no
    # longer exists. Refuse and write nothing — never re-resolve silently here.
    # `begin` calls this first under the lock, so a refusal leaves every session TODO.
    stale = [name for name, now, then in (
        ("manifest.json", _manifest_now(plan_dir), p["manifest"]),
        (str(p["ssot_path"]), _read_text(p["ssot_path"]), p["ssot_text"]),
    ) if now != then]
    if stale:
        raise SystemExit(
            "refusing to begin (route-at-dispatch): the plan or routing file changed during "
            f"begin ({', '.join(stale)}) after the cells were resolved and before the plan lock "
            "was taken. Nothing was written; re-run begin.")
    record_gate(plan_dir, p["switch"]["enabled"], p["switch"]["reason"], p["version"])
    state = rsi.load_state(plan_dir)
    fresh, kept = {}, {}
    for sid, cell in p["cells"].items():
        rec = {**cell, "generation": est.session_state(plan_dir, sid)["generation"]}
        old = (state.get(FREEZE_KEY) or {}).get(sid)
        if old == rec:
            continue
        # Another begin froze this generation on this lane after our pre-lock read:
        # the first freeze stands. A new generation, or a lane change `_resolve`
        # re-resolved on purpose, still replaces it.
        if isinstance(old, dict) and old.get("generation") == rec["generation"] \
                and old.get("provider") == rec.get("provider"):
            kept[sid] = old
            continue
        fresh[sid] = rec
    if kept:
        rsi.log_event(plan_dir, "route_freeze_kept", session_ids=sorted(kept), cells=kept)
        print(f"route-at-dispatch: kept the cell another begin froze first for "
              f"{', '.join(sorted(kept))}.", file=sys.stderr)
    if fresh:
        state.setdefault(FREEZE_KEY, {}).update(fresh)
        rsi.save_state(plan_dir, state)
        rsi.log_event(plan_dir, "route_resolved", session_ids=sorted(fresh), cells=fresh)


def receipt(plan_dir, manifest, by_id, batch):
    """One stderr line per v8 session: the cell it runs, how its effort binds, and
    why it has that cell. Silent below v8."""
    if not is_v8(manifest):
        return
    for m in batch:
        s = by_id[m["id"]]
        if m.get("backend") == "codex":
            cell, mech = f"{m.get('codex_model')}@{m.get('codex_effort')}", "codex_cli"
        else:
            cell = f"{m.get('model_arg') or 'inherit'}@{m.get('reasoning') or 'unset'}"
            mech = m.get("effort_mechanism")
        if pinned(s):
            why = f"override: {s.get('why_model') or '(no why_model)'}"
        else:
            why = f"class default, routing v{(frozen_cell(plan_dir, m['id']) or {}).get('routing_version')}"
        esc = m.get("escalated_from")
        if esc:
            base = esc["authored"]
            why += f"; escalated rung {esc['rung']} from {base['model']}@{base['reasoning'] or 'unset'}"
        print(f"route: {m['id']} {s.get('task_class')} -> {cell} [{mech}] ({why})", file=sys.stderr)
