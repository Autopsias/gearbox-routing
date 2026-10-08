"""LND-01 — the LAND RE-GATE (contract §4.4, §5.1b, §10.1).

Its own module for two reasons. The first is this repo's 500-LOC ratchet, which
``land_steps.py`` was one line under. The second is that the re-gate is the one
step with a CACHE, and a cache is exactly where a gate quietly stops gating:

**ONLY A PASS IS EVER CACHED.** Two measured defects, one root cause — a
verdict written to the cache before anyone asked what the verdict WAS:

* a whole-run cache keyed on ``(expected, plan_head, merge_sha)`` returned
  "continue" whenever a digest existed, so a land parked on a RED gate sailed
  through its own cache on the next invocation and reached the human-approval
  step;
* the same cache stored an INDETERMINATE result, so a gate that could not decide
  never ran again. Three ``run.py land`` invocations, one gate execution — and
  once the undecided condition cleared the land STILL parked, because nothing an
  operator controls invalidates that key. The park's own brief says "re-run
  ``run.py land``", and re-running could not clear it.

So the cache is PER GATE, it holds ARGV gates only, and it admits
``outcome == "pass"`` and nothing else. A failed or undecided argv gate re-runs
on every entry, which is the only behaviour under which the brief's own advice
("re-run `run.py land`") is true. A SKILL gate is not in it at all: its outcome
is recorded by ``land_record``, not run here, so replaying a stamped fail is
reporting the orchestrator's answer rather than replaying a stale run — and
another ``land-record`` overwrites it.

The pass cache is also what makes the skill-gate round trip affordable: an argv
gate resolved before the directive is not re-run after ``land-record``
(measured: one gate, two executions, one candidate — with the shipped 900 s
review gates that is a whole extra review of a tree that did not change).

§4.4 is why there is no skip at all: a clean textual merge does not imply a
working tree. One session renames a symbol, another adds a caller of the old
name, and git merges both without complaint.
"""

import json
import os
import time
from pathlib import Path

import gate_expect as gx
import gate_files
import gate_timing as gt
import land_at_land as lal
import land_flaky as lfk
import land_repair as lrp
import land_start as lsg
import land_state as lst
import manifest_io as mio
import review_context as rvs
import run_state_io as rsi
import ship_state_io as ssio
import shipping as shp
import verifier_park as vpk
from land_state import park, save

#: A gate that REFUSED on policy: under the Codex harness with no Claude-verifier
#: opt-in, no model may read this tree at all (`verifier_park.refusal`). It is an
#: indeterminate exit like any other, but it is not a TIMEOUT: it is deterministic
#: and identical on every retry, so none of `gate-indeterminate`'s documented
#: remedies (raise the timeout, narrow the scope, run it by hand) can move it.
#: Told apart here so it gets a park with a resolution path instead of one whose
#: own brief ("re-run `run.py land`") reproduces it.
ON_BOX = "on-box"
#: The two dispositions a human may record, taken from the SESSION-scope park
#: rather than re-spelled: `ack-checkpoint --session sNN --decision <x>` and
#: `land-verify --decision <x>` are the same two words at two scopes, and a second
#: copy of that vocabulary is a contract that drifts.
BLOCKED, VERIFIED_ON_BOX = sorted(vpk.DECISIONS)
DECISIONS = (BLOCKED, VERIFIED_ON_BOX)
#: Green-or-green-enough: what `_verdict` lets through. `verified-on-box` is NOT
#: `pass` anywhere else — it is its own outcome in §5.1c's digest, so the ack a
#: human later gives is bound to a digest that says a HUMAN cleared that gate.
_SETTLED = ("pass", VERIFIED_ON_BOX)
ON_BOX_PARK = "gate-on-box-verification"
BLOCKED_PARK = "gate-on-box-blocked"


def gate_cwd(declared, plan_tree, land_path):
    """Re-root a resolved gate cwd from the PLAN worktree into the LAND worktree,
    preserving its relative position (``<tree>/backend`` -> ``<land>/backend``).

    Returns None when it CANNOT — and the caller parks. Returning the declared
    path unmapped (the shape `plan_scope.gate_cwd` uses, where degrading is
    right) would run an arbitrary gate argv **in the operator's checkout**: the
    exact collision this isolation exists to prevent, and at the one moment the
    tree under test is supposed to be the merged candidate. Fail closed.
    """
    if not declared:
        return land_path
    # LEXICAL on the plan side: resolving `declared` would follow a plan-side
    # symlink and check a different land dir than the one the gate runs in.
    p = os.path.normpath(os.path.join(plan_tree, declared))
    roots = (os.path.normpath(plan_tree), os.path.realpath(plan_tree))
    rel = next((os.path.relpath(p, r) for r in roots if p == r or p.startswith(r + os.sep)), None)
    if rel is None:
        return None
    try:
        # Resolve the MAPPED dir too: a candidate `sub` that is a symlink out of
        # the land worktree passes the plan-side check and would run the gate there.
        mapped = (Path(land_path) / rel).resolve(strict=True)
        mapped.relative_to(Path(land_path).resolve(strict=True))
    except (ValueError, OSError):
        return None
    return str(mapped)


def _run_argv_gate(plan_dir, ctx, st, gid, g, cwd, log=""):
    env = rvs.declared_env(g, plan_dir, "land", cwd)
    if rvs.BASE_ENV in (g.get("env_allowlist") or []):
        # §10.1's POST-COMMIT input. The land's surface is the RANGE
        # `<expected>..HEAD` — everything this land would add to the default
        # branch. `git diff HEAD` is forbidden in both phases: it excludes
        # untracked files entirely, so an all-new-files session produces an EMPTY
        # diff and passes every gate that reads it, exit 0, zero findings.
        env[rvs.BASE_ENV] = st["expected"]
    if rvs.EXCLUDE_ENV in (g.get("env_allowlist") or []):
        # §5.1b — "with `_plans/<plan-slug>/` excluded from the gate's file set".
        # The range above necessarily contains the record commit `step_record`
        # made one step earlier; on this repo's own plan that is PLAN.html
        # (222 KB), spec.json (107 KB), run.ndjson and every closeout —
        # machine-generated content nobody wrote and nobody can act on, and the
        # single likeliest way to push a reviewer into its indeterminate exit.
        # Keyed on the gate DECLARING the EXCLUDE var itself, so it reaches the
        # review gates and nothing else: an env var handed to every argv gate is
        # how PLAN_EXECUTE_REVIEW_BASE once leaked into code-review-gate's pytest
        # children (2026-08-20). Keyed on SCOPE it was a silent no-op waiting to
        # happen — `review_context.declared_env` derives a scope from the SESSION
        # spec and there is no session called "land", so the land never sets
        # SCOPE at all. A gate that declared EXCLUDE but dropped the unused SCOPE
        # would have lost §5.1b entirely and handed the reviewer the whole record.
        env[rvs.EXCLUDE_ENV] = f"{lst.ps.PLANS_DIR}/{ctx['slug']}"
    t0 = time.monotonic()
    res = ssio.run_deploy_argv(g["argv"], cwd=cwd, timeout=g.get("timeout", 1200),
                               env_allowlist=g.get("env_allowlist", []), env_extra=env)
    wall_s = round(time.monotonic() - t0, 1)     # the brief's per-gate cost (Long gates)
    rc = res.get("returncode")
    # `indeterminate_exit` is DECLARED by all three shipped llm-review gates and
    # means "I could not decide", not "I found something". Collapsing it into
    # `fail` was measured in this repo as charging a TIMED-OUT review as a real
    # failure — and here it would also bake a false verdict into §5.1c's
    # gate_digest, so re-running to a genuine answer would change the digest and
    # invalidate the human's ack (§5.2). §5.1c's vocabulary has a third value for
    # exactly this, and `_verdict` treats it as NOT-green. The classification
    # itself is `ssio.argv_outcome`, shared with `verify`, because a TIMEOUT or
    # exec error (`returncode` None) is the MOTIVATING indeterminate case — and
    # scoring it `fail` here meant no timeout could ever reach the
    # `gate-indeterminate` park its own brief describes.
    verdict = ssio.argv_outcome(res, g.get("indeterminate_exit"))
    miss = None if verdict == "indeterminate" else gx.gate_miss(g, res)  # EXPECT, as verify
    outcome = "skipped" if verdict == "indeterminate" else "fail" if miss else verdict
    # A POLICY REFUSAL IS NOT A TIMEOUT. Both leave by `indeterminate_exit`, and
    # collapsing them sent a Codex-built plan into `gate-indeterminate`, whose
    # brief says "re-run `run.py land`" — which reproduces the identical exit 2,
    # because `verifier_park.refusal` is a decision about the SSOT and the harness,
    # not about how long anything took. `cause_in` reads the gate's OWN first
    # `VERIFIER:` line (None when there is no marker), so this cannot be faked by
    # a reviewer echoing the marker back in its findings.
    cause = vpk.cause_in(res.get("stdout") or "") if outcome == "skipped" else None
    rec = {"gate_id": gid, "outcome": ON_BOX if cause else outcome, "cwd": cwd,
            "returncode": rc, "cause": cause, "wall_s": wall_s,
            "log": lrp.write_log(ctx, gid + log, res),       # LND-03: the full output
            "findings_count": 1 if outcome == "fail" else 0,
            "excerpt": gx.expect_excerpt(
                gid, miss, ((res.get("stderr") or "") + (res.get("stdout") or ""))[-4000:])}
    gt.log_run(plan_dir, "land" + log.replace(".", "-"), None, gid, wall_s, rec["outcome"])
    if log != ".base":        # LND-12: every land-tree failure is recorded, and saved, now
        lfk.record(plan_dir, ctx, st, rec)
    return rec


def _verdict(plan_dir, ctx, st, results):
    """None when every gate SETTLED green; a park otherwise. Called on EVERY entry
    — including the cached one — so a red result can never age into a green.

    FOUR park kinds, because four different things need four different next
    actions from the operator, and one of them is not a defect at all:

      fail          -> `gate-failed`.
      blocked       -> `gate-on-box-blocked`: a human read the merged tree and
                       said no. Terminal until the candidate changes.
      on-box        -> `gate-on-box-verification`: no MODEL may read this tree,
                       so a human must. Has a recorded resolution (`land-verify`)
                       precisely because re-running cannot clear it.
      skipped       -> `gate-indeterminate`: the gate could not DECIDE (a timeout,
                       a reviewer that never answered). Re-running can clear it,
                       which is why its brief says so.

    Ordered by severity of the answer, not by how it left: a real finding outranks
    a refusal, and a human's BLOCKED outranks a park still waiting on one.
    """
    by = {k: [r["gate_id"] for r in results if r["outcome"] == k]
          for k in ("fail", BLOCKED, ON_BOX, "skipped")}
    if not any(by.values()):
        return None
    excerpt = "\n\n".join(f"--- {r['gate_id']} ({r['outcome']}, rc={r.get('returncode')}) ---\n"
                           f"{r.get('excerpt', '')}" for r in results
                           if r["outcome"] not in _SETTLED)
    if by["fail"]:
        return park(plan_dir, ctx, st, "gate-failed",
                    f"LAND PARKED — the re-gate FAILED on the merged tree: {by['fail']}. Nothing "
                    "was pushed.\n\nA clean textual merge does not imply a working tree — one "
                    "session renames a symbol, another adds a caller of the old name, and the "
                    f"merge is silent. That is what this catches (§4.4).\n\n{excerpt}",
                    failed=by["fail"], undecided=by["skipped"])
    if by[BLOCKED]:
        return park(plan_dir, ctx, st, BLOCKED_PARK,
                    f"LAND PARKED — you recorded BLOCKED for {by[BLOCKED]} after reviewing the "
                    "merged tree on this box. Nothing was pushed and nothing is claimed about "
                    "the tree.\n\nThe plan branch, its worktree and this land worktree are all "
                    "untouched — no work is ever deleted by a park. Fix what you found on the "
                    f"plan branch and run `run.py land {plan_dir}` again: a new plan head is a "
                    "new candidate, and a disposition is bound to the candidate it judged, so "
                    "you will be asked again rather than held to this answer.",
                    blocked=by[BLOCKED])
    if by[ON_BOX]:
        return _park_on_box(plan_dir, ctx, st, by[ON_BOX], results, excerpt)
    return park(plan_dir, ctx, st, "gate-indeterminate",
                f"LAND PARKED — the re-gate COULD NOT DECIDE: {by['skipped']} exited with its "
                "declared indeterminate code (a timeout, or a reviewer that never "
                "answered). Nothing was pushed and nothing is claimed about the merged "
                f"tree.\n\nRe-run `run.py land {plan_dir}` — an indeterminate gate is not a "
                "finding, and it is never counted as green.\n\n" + excerpt,
                undecided=by["skipped"])


def _park_on_box(plan_dir, ctx, st, gates, results, excerpt):
    """§5.1b at LAND scope — no model may review this merged tree, so a human must.

    The land-scope counterpart of `verifier_park.park`, and it exists for the same
    reason that one does: a policy refusal is neither a pass nor a failure, and
    the only honest way past it is a disposition a HUMAN records.
    """
    # PER GATE as well as deduped: two gates can refuse for DIFFERENT reasons (one
    # for the missing opt-in, one because the tree is restricted), and stamping
    # both dispositions with whichever cause sorted first would misreport what the
    # human was actually shown.
    per_gate = {r["gate_id"]: r.get("cause") for r in results if r["outcome"] == ON_BOX}
    causes = sorted({c for c in per_gate.values() if c})
    return park(
        plan_dir, ctx, st, ON_BOX_PARK,
        f"LAND PARKED — NO MODEL MAY REVIEW THIS MERGED TREE: {gates}.\n\n"
        + "\n".join(f"  {c}: {vpk.reason_for(c)}" for c in causes)
        + "\n\nThat is a POLICY answer, not a timeout: it is decided before the tree is "
        "read and it is identical on every retry, so re-running `land` cannot clear it "
        "and this is deliberately NOT the `gate-indeterminate` park. Nothing was pushed "
        "and nothing is claimed about the tree.\n\nReview it yourself — the merged "
        "candidate is already checked out:\n\n"
        f"  cd {st.get('land_path')}\n"
        f"  git diff {st.get('expected')}..HEAD\n\n"
        "Then record what you found — one of exactly two:\n\n"
        f"  run.py land-verify {plan_dir} --decision {VERIFIED_ON_BOX} --note '<what you checked>'\n"
        f"  run.py land {plan_dir}\n"
        "    You reviewed the merged tree and it is sound. The gate is recorded "
        f"`{VERIFIED_ON_BOX}` — its OWN outcome, never `pass` — and the land goes on to "
        "ask for your ack.\n\n"
        f"  run.py land-verify {plan_dir} --decision {BLOCKED} --note '<why>'\n"
        "    It is not sound, or you will not review it. The land parks and nothing "
        "ships.\n\nEither answer is bound to THIS candidate (plan head, merge, gate set). "
        "Move any of them and you are asked again — you will never land a tree you did "
        f"not see.\n\n{excerpt}",
        on_box=gates, causes=causes, on_box_causes=per_gate)


def _gate_set_digest(gates):
    """A digest over the RESOLVED gate set — ids, kinds, argv, cwd, timeouts.

    In the cache key because keying only on the candidate meant a changed
    manifest gate list, or a changed registry entry, silently reused the previous
    verdict: the gates that produced the green were not the gates now declared.
    """
    import hashlib
    # `env_allowlist` is in here because it is not decoration: `_run_argv_gate`
    # injects the review BASE and EXCLUDE only when the allowlist declares them,
    # so editing it changes WHAT THE GATE REVIEWS. Left out, a gate whose scoping
    # was just removed replayed its previous pass — the cache key was identical.
    # `timeout` is in for the same reason a step lower: it decides whether the
    # gate can reach a verdict at all.
    blob = json.dumps([[gid, g.get("kind"), g.get("argv"), g.get("cwd"),
                        g.get("skill"), g.get("indeterminate_exit"),
                        g.get("timeout"), sorted(g.get("env_allowlist") or []),
                        g.get("expect")]
                       for gid, g in gates], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _resolve_all(plan_dir, ctx, st):
    """[(gate_id, descriptor)] for the plan's union PLUS every gate the candidate
    tree flags ``at_land`` (R2), sorted by id. A plan-declared gate keeps its own
    resolution UNLESS the candidate flags it ``at_land``: then the candidate's
    entry wins, because that is the definition CI will run on this tree, and the
    plan's copy may be stale. Raises StepResolveError or ``lal.Unreadable``."""
    flagged = dict(lal.at_land_gates(st["land_path"], ctx["plan_tree"]))
    gates = {gid: shp.resolve_gate(plan_dir, gid, None)      # only when no candidate entry
             for gid in lst.union_gates(mio.load_manifest(plan_dir)) if gid not in flagged}
    st["at_land"] = sorted(flagged)
    st["at_land_warning"] = lal.brief_note(st["land_path"], st["expected"], st["at_land"])
    gates.update(flagged)
    # A plan gate whose registry entry says `land_covered_by: <id>` does not run at
    # land when <id> is an at_land gate of this same land: that gate (the full
    # suite) already runs the same tests on the same merged tree. Only an at_land
    # gate can cover, and an at_land gate is never dropped, so a cover cannot
    # remove the check that does the covering. The candidate tree is the plan's own
    # code, so the cover's registry entry must be IDENTICAL in the trusted outer
    # registry: a plan that changes any field of `ci-check` (argv, cwd, expect,
    # indeterminate_exit, ...) loses the cover and the covered gate runs.
    # Operator decision 2026-10-04.
    outer, cand = shp.eval_gates(plan_dir), lal.registry(st["land_path"])
    st["covered"] = {gid: c for gid, g in gates.items()
                     if gid not in flagged and (c := g.get("land_covered_by")) in flagged
                     and outer.get(c) == cand.get(c)}
    return sorted((gid, g) for gid, g in gates.items() if gid not in st["covered"])


def _surface(st, ctx):
    """§10.1/§10.3 — what the re-gate is actually looking at, recorded.

    ``None`` means the file set could not be derived at all, which fails CLOSED:
    a gate that cannot read its own inputs and reports green is the silent pass
    `gate_files` exists to refuse. An honestly EMPTY set is not a pass either —
    it means this land adds nothing outside the plan's own record.
    """
    files = gate_files.changed_files(st["land_path"], st["expected"])
    if files is None:
        return None
    pathspec = f"{lst.ps.PLANS_DIR}/{ctx['slug']}"
    return [f for f in files
            if f != pathspec and not f.startswith(pathspec + "/")]


def _bare(rec):
    """A result WITHOUT its cache stamp. `gate_key` is bookkeeping, not a verdict,
    and leaving it on half of `st["gates"]` makes the record read as two shapes."""
    return {k: v for k, v in rec.items() if k != "gate_key"}


def _cached_pass(st, gid, key):
    """An argv gate PASS this candidate already earned, or None.

    ``key`` is compared, never assumed: a record stamped with another candidate's
    key is a verdict about a different tree. And only ``pass`` is ever WRITTEN,
    so "cached" and "green" are the same word here by construction — which is the
    whole of findings 1 and 4.
    """
    rec = (st.get("argv_gates") or {}).get(gid)
    if rec and rec.get("gate_key") == key and rec.get("outcome") == "pass":
        return rec
    return None


def disposition(st, gid, key):
    """A HUMAN's recorded on-box disposition for this gate on THIS candidate, or None.

    ``key`` is compared exactly as ``_cached_pass`` compares it: a disposition is a
    statement about ONE merged tree, never a standing permission, so a record
    stamped with another candidate's key is not an answer about this one.

    Returned INSTEAD OF RUNNING THE GATE, and that is the point — the gate refused
    on policy, so running it again would only reproduce the refusal the human just
    resolved. It is also why this is checked before the pass cache: the two are
    mutually exclusive (only a `pass` is ever cached) and the refusal is the case
    that must not re-execute.
    """
    rec = (st.get("on_box_gates") or {}).get(gid)
    return rec if rec and rec.get("gate_key") == key else None


def step_gate(plan_dir, ctx, st):
    """Run the plan's UNION of verify gates in the land worktree, on the exact
    tree that will be pushed (§5.1b: AFTER ``record-plan``, never before).

    Every gate's PASS is cached under ``(expected, plan_head, merge_sha,
    gate-set-digest)`` — the four inputs that decide WHAT is being gated. Nothing
    else is cached: a fail and an indeterminate both re-run, every entry, so the
    only way out of a red land is a green gate.
    """
    try:
        gates = _resolve_all(plan_dir, ctx, st)
    except lal.Unreadable as e:
        return park(plan_dir, ctx, st, "at-land-registry-unreadable",
                    f"LAND PARKED — a gate registry could not be read: {e}. Nothing was "
                    "pushed. An unreadable registry could hide an `at_land` gate, so land "
                    f"refuses rather than drop it. Fix the file, then run `run.py land {plan_dir}`.",
                    registry_error=str(e))
    except shp.StepResolveError as e:
        return park(plan_dir, ctx, st, "gate-unresolvable",
                    f"LAND PARKED — a verify gate does not resolve ({e.reason}): {e}. A gate "
                    "that cannot run is never counted green — an unlaunchable gate and a "
                    "passing one are the same exit code.")
    key = (f"{st['expected']}:{st.get('plan_head')}:{st.get('merge_sha')}"
           f":{_gate_set_digest(gates)}")
    if st.get("gate_key") != key:
        # The key is written WITH the reset, not only at the bottom. Left stale,
        # a park between here and there (an unreadable surface, an empty one, a
        # gate cwd outside the land) leaves `land_record` accepting a verdict,
        # stamping it with the OLD key and returning success — and `step_gate`
        # then discards it. A receipt for work that was thrown away.
        # `on_box_gates` resets WITH the rest: a human's disposition judged the old
        # candidate, and a new plan head, merge or gate set is a different tree.
        st.update({"gate_key": key, "skill_gates": {}, "argv_gates": {},
                   "on_box_gates": {}})
    lsg.settle(plan_dir, st)        # LND-13: start_pending ends with its session only
    surface = _surface(st, ctx)
    st["files_examined"] = None if surface is None else len(surface)
    if surface is None:
        return park(plan_dir, ctx, st, "gate-surface-unreadable",
                    f"LAND PARKED — the re-gate's file set could not be derived in "
                    f"{st['land_path']} (§10.1). A check that cannot read its inputs and "
                    "reports green is the silent pass this refuses.")
    if not surface:
        return park(plan_dir, ctx, st, "gate-empty-surface",
                    f"LAND PARKED — files_examined: 0. Outside the plan's own record, this "
                    f"land adds NOTHING to {ctx['default']}. §10.3: an unexamined zero is a "
                    "check pointed at nothing, and a land that ships no work is a decision "
                    "for you, not for the state machine.")
    results, ran = [], 0
    for gid, g in gates:
        if g["kind"] == "skill":
            # A skill verdict is RECORDED by `land_record`, never run here — so
            # a stamped FAIL is not a cached RUN, it is the orchestrator's answer,
            # and it counts as one. It still converges: another `land-record`
            # overwrites it. (The argv branch below is the opposite case, where
            # the outcome IS a run and caching a not-pass would replay it forever.)
            rec = (st.get("skill_gates") or {}).get(gid)
            if not rec or rec.get("gate_key") != key:
                st["gate_key"] = key
                save(plan_dir, ctx, st)
                return {"action": "invoke-skill", "skill": g["skill"], "args": g.get("args", ""),
                        "gate": gid, "cwd": st["land_path"], "stage": "land-regate",
                        "gate_key": key, "files_examined": len(surface),
                        "record_with": f"run.py land-record {plan_dir} --gate {gid} "
                                       "--status passed|failed"}
            results.append({"gate_id": gid, "wall_s": None, **_bare(rec)})
            continue
        rec = disposition(st, gid, key) or _cached_pass(st, gid, key)
        if rec is None:
            cwd = gate_cwd(g.get("cwd"), ctx["plan_tree"], st["land_path"])
            if cwd is None:
                return park(plan_dir, ctx, st, "gate-cwd-outside-land",
                            f"LAND PARKED — gate {gid!r} declares cwd {g.get('cwd')!r}, which "
                            f"is missing from, or resolves outside, the land worktree {st['land_path']}. Running "
                            "it unmapped would execute the gate in the OPERATOR'S CHECKOUT — "
                            "the collision this isolation exists to prevent — and would gate "
                            "a tree that is not the candidate.")
            rec = _run_argv_gate(plan_dir, ctx, st, gid, g, cwd)
            ran += 1
            if rec["outcome"] == "pass":
                # Written only AFTER the verdict is known to be green, and only
                # then. Persisted immediately so the skill-gate round trip below
                # (and any crash) resumes without paying for this gate twice.
                st.setdefault("argv_gates", {})[gid] = {**rec, "gate_key": key}
                st["gate_key"] = key
                save(plan_dir, ctx, st)
        results.append({"wall_s": None, **_bare(rec)})
    st.update({"gate_key": key, "gates": results, "gate_digest": lst.gate_digest(results)})
    save(plan_dir, ctx, st)
    rsi.log_event(plan_dir, "plan_land_regate", session_ids=[], slug=ctx["slug"],
                  gates=len(results), executed=ran, gate_digest=st["gate_digest"],
                  files_examined=st.get("files_examined"),
                  failed=[r["gate_id"] for r in results if r["outcome"] != "pass"])
    # LND-03: a red re-gate the plan can repair gets a repair session, not a park.
    # LND-12: a green one still parks while a check that failed on it is held.
    out = (lrp.repair(plan_dir, ctx, st, results, gates) or _verdict(plan_dir, ctx, st, results)
           or lfk.flake_hold(plan_dir, ctx, st))
    # LND-13: green, so no directive follows.
    return out or lsg.green(plan_dir, ctx, st, lfk.unrepaired(ctx, st))
