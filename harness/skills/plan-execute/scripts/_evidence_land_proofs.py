"""LND-01 — the land stage's PROOF RUN.

Writes ``_evidence/s08/land-proofs.json``: one machine-readable record per proof,
each carrying the command, its exit code, the shas BEFORE and AFTER, and its
PAIRED NEGATIVE CONTROL. A record with only a positive result is not evidence —
a check that returns "clean" because its input was empty is worse than no check,
so every claim here is paired with the planted condition that makes it fail.

Run it:  python3 _evidence_land_proofs.py <out.json>

The fixture lives in ``_land_fixture.py`` and is imported by ``test_land.py``
too, so the tests and the evidence exercise ONE fixture and cannot drift apart.
"""

import json
import subprocess
import sys
import time
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import land_state as lst  # noqa: E402
from _land_fixture import (_arm_prepush, _arm_slow_commit,  # noqa: E402
                           _arm_slow_gate, _kill_when, build_repo,
                           checked_out_branch, isolate, local_main, main_side,
                           origin_main, release_lock, run_cli, sl_lock_path, work)
from worktree import git  # noqa: E402


# --------------------------------------------------------------------------
# Proofs
# --------------------------------------------------------------------------
def _rec(name, claim, positive, negative):
    return {"proof": name, "claim": claim, "positive": positive, "negative_control": negative,
            "holds": bool(positive.get("ok")) and bool(negative.get("ok"))}


def proof_checkpoint(tmp):
    """The land checkpoint is un-skippable: no CLI route pushes without an ack."""
    fx = build_repo(tmp / "cp")
    iso = isolate(fx, "plan-a")
    work(iso["tree"], "src/feature.py", "FEATURE = 1\n")
    before = origin_main(fx)
    # The premature ack runs FIRST, before any land — only then is "no candidate
    # yet" true. (It once ran AFTER two parked lands, so a candidate existed, the
    # ack SUCCEEDED, and the record misdescribed its own probe.) `ok` asserts it.
    rc_ack, ack_out = run_cli("land-ack", iso["plan_dir"], "--note", "forged")
    attempts = []
    for cmd in ("land", "land-resume"):
        rc, out = run_cli(cmd, iso["plan_dir"])
        attempts.append({"command": f"run.py {cmd} <plan-dir>", "exit_code": rc,
                         "action": out.get("action"),
                         "origin_main_after": origin_main(fx)})
    positive = {
        "premature_land_ack": {"command": "run.py land-ack <plan-dir> BEFORE any land "
                                          "(no candidate exists yet) — must refuse",
                               "exit_code": rc_ack, "action": ack_out.get("action")},
        "attempts": attempts,
        "origin_main_before": before, "origin_main_after": origin_main(fx),
        "ok": (rc_ack == 1 and ack_out.get("action") == "error"
               and all(a["action"] == "land-awaits-review" and a["exit_code"] == 1
                       for a in attempts) and origin_main(fx) == before),
    }
    # NEGATIVE CONTROL — the identical command WITH the ack must land, or the
    # park above proves only that the fixture cannot land at all. This is the
    # FIRST ack of a real candidate, and `ok` requires it to be accepted.
    rc_a, ack2 = run_cli("land-ack", iso["plan_dir"], "--note", "approved")
    rc_l, landed = run_cli("land", iso["plan_dir"])
    negative = {"command": "run.py land-ack <plan-dir> && run.py land <plan-dir>",
                "ack_exit_code": rc_a, "ack_action": ack2.get("action"),
                "land_exit_code": rc_l,
                "action": landed.get("action"), "merge_sha": landed.get("merge_sha"),
                "origin_main_before": before, "origin_main_after": origin_main(fx),
                "ok": (rc_a == 0 and ack2.get("action") == "land-acked"
                       and landed.get("action") == "landed" and origin_main(fx) != before)}
    return _rec("by-name-dispatch-park",
                "No entry point advances the default branch without a recorded human ack; "
                "with the ack the same command lands.", positive, negative)


def proof_stale_ack(tmp):
    """§5.2 — the ack is bound to {plan_head, main_head, gate_digest}."""
    fx = build_repo(tmp / "stale")
    iso = isolate(fx, "plan-a")
    work(iso["tree"], "src/feature.py", "FEATURE = 1\n")
    run_cli("land", iso["plan_dir"])
    run_cli("land-ack", iso["plan_dir"], "--note", "approved this candidate")
    approved = lst.load(iso["plan_dir"])["ack"]
    before = origin_main(fx)
    moved = main_side(fx, "src/elsewhere.py", "ELSEWHERE = 1\n")
    rc, out = run_cli("land", iso["plan_dir"])
    st = lst.load(iso["plan_dir"])
    positive = {"command": "main moves under a recorded ack, then run.py land <plan-dir>",
                "exit_code": rc, "action": out.get("action"),
                "approved_main_head": approved["main_head"], "main_head_now": moved,
                "origin_main_before": before, "origin_main_after": origin_main(fx),
                "ack_after": st.get("ack"), "state_after": st.get("state"),
                "ok": (out.get("action") == "land-awaits-review" and rc == 1
                       and st.get("ack") is None and origin_main(fx) == moved)}
    rc2, out2 = run_cli("land-ack", iso["plan_dir"], "--note", "re-approved")
    rc3, out3 = run_cli("land", iso["plan_dir"])
    negative = {"command": "re-ack the NEW candidate, then run.py land <plan-dir>",
                "ack_exit_code": rc2, "land_exit_code": rc3, "action": out3.get("action"),
                "new_main_head": out2.get("ack", {}).get("main_head"),
                "origin_main_after": origin_main(fx),
                "ok": out3.get("action") == "landed" and origin_main(fx) != moved}
    return _rec("stale-ack-invalidation",
                "An ack whose main_head no longer matches re-parks AWAITS_REVIEW and is "
                "cleared; a fresh ack on the new candidate lands.", positive, negative)


def proof_forged_ack(tmp):
    """A control that ATTEMPTS THE FORBIDDEN OPERATION rather than re-walking a
    success path: write an ack directly into the runtime state — the shape a
    corrupted, replayed or hand-edited approval record has — and require the
    push to refuse it."""
    fx = build_repo(tmp / "forged")
    iso = isolate(fx, "plan-a")
    work(iso["tree"], "src/feature.py", "FEATURE = 1\n")
    run_cli("land", iso["plan_dir"])
    st = lst.load(iso["plan_dir"])
    before = origin_main(fx)
    forged = {"plan_head": st["plan_head"], "main_head": "0" * 40,
              "gate_digest": st["gate_digest"], "at": "forged", "note": "never approved"}
    st["ack"], st["state"] = forged, "acked"
    lst.save(iso["plan_dir"], lst.context(iso["plan_dir"]), st)
    rc, out = run_cli("land", iso["plan_dir"])
    after = lst.load(iso["plan_dir"])
    positive = {"command": "write an ack whose main_head names no real commit, then land",
                "forged_ack": forged, "exit_code": rc, "action": out.get("action"),
                "origin_main_before": before, "origin_main_after": origin_main(fx),
                "ack_after": after.get("ack"),
                "ok": (out.get("action") == "land-awaits-review" and rc == 1
                       and origin_main(fx) == before and after.get("ack") is None)}
    # PAIRED CONTROL — the genuine ack, on the same fixture, DOES land. Without
    # it the refusal above could be a fixture that cannot land at all.
    rc2, out2 = _land_with_ack(fx, iso)
    negative = {"command": "the real `run.py land-ack`, same fixture", "exit_code": rc2,
                "action": out2.get("action"), "origin_main_after": origin_main(fx),
                "ok": out2.get("action") == "landed" and origin_main(fx) != before}
    return _rec("forged-ack-refused",
                "An approval record that does not match the live candidate is refused and "
                "cleared, and the default branch does not move; the genuine ack lands.",
                positive, negative)


def _land_with_ack(fx, iso):
    run_cli("land", iso["plan_dir"])
    run_cli("land-ack", iso["plan_dir"], "--note", "approved")
    return run_cli("land", iso["plan_dir"])


def proof_pair_two_v7(tmp):
    """Two v7 plans land back to back; the second re-syncs onto the first."""
    fx = build_repo(tmp / "pair", slugs=("plan-a", "plan-b"))
    a, b = isolate(fx, "plan-a"), isolate(fx, "plan-b")
    work(a["tree"], "src/a_feature.py", "A_FEATURE = 1\n")
    work(b["tree"], "src/b_feature.py", "B_FEATURE = 1\n")
    m0 = origin_main(fx)
    rc_a, out_a = _land_with_ack(fx, a)
    m1 = origin_main(fx)
    # B captured nothing yet; make the contention REAL by moving main during B's
    # push window with a pre-push hook that fires exactly once.
    _arm_prepush(fx)
    rc_b, out_b = _land_with_ack(fx, b)
    st_b = lst.load(b["plan_dir"])
    m2 = origin_main(fx)
    both = git(["ls-tree", "-r", "--name-only", m2], fx["origin"], check=True)[1]
    positive = {"command": "land plan-a, then land plan-b with main moving mid-push",
                "plan_a_exit": rc_a, "plan_a_action": out_a.get("action"),
                "plan_b_exit": rc_b, "plan_b_action": out_b.get("action"),
                "origin_main_before": m0, "after_plan_a": m1, "after_plan_b": m2,
                "plan_b_push_attempts": st_b.get("push_attempts"),
                "plan_b_resyncs": (st_b.get("ack") or {}).get("resyncs"),
                "ok": (out_a.get("action") == "landed" and out_b.get("action") == "landed"
                       and m0 != m1 != m2 and len(st_b.get("push_attempts") or []) >= 1
                       and "a_feature.py" in both and "b_feature.py" in both)}
    negative = _resync_exhausted(tmp / "pair-neg")
    return _rec("land-pair-two-v7-plans",
                "Two v7 plans land serially; the second absorbs a main that moved under it "
                "via §4.5's bounded re-sync, and BOTH features are on the default branch.",
                positive, negative)


def _resync_exhausted(tmp):
    """NEGATIVE CONTROL for the re-sync: a main that moves on EVERY attempt must
    exhaust the bound of 3 and PARK, not spin. A loop that always succeeds has
    not been shown able to refuse."""
    fx = build_repo(tmp)
    iso = isolate(fx, "plan-a")
    work(iso["tree"], "src/feature.py", "FEATURE = 1\n")
    _arm_prepush(fx, times=99)
    rc, out = _land_with_ack(fx, iso)
    st = lst.load(iso["plan_dir"])
    return {"command": "main moves on EVERY push attempt", "exit_code": rc,
            "action": out.get("action"), "kind": out.get("kind"),
            "attempts": len(st.get("push_attempts") or []),
            "branch_kept": git(["rev-parse", "--verify", "--quiet",
                                iso["branch"]], fx["root"])[1],
            "ok": (out.get("action") == "land-parked"
                   and out.get("kind") in ("push-rejected", "resync-exhausted")
                   and len(st.get("push_attempts") or []) == lst.MAX_RESYNC)}


def proof_pair_v6_during_v7(tmp):
    """TWO SEPARATE PROCESSES. An in-process fixture proves nothing about exclusion."""
    fx = build_repo(tmp / "v6", slugs=("plan-a", "legacy-v6"))
    iso = isolate(fx, "plan-a")
    work(iso["tree"], "src/feature.py", "FEATURE = 1\n")
    v6_dir = fx["plans"]["legacy-v6"]
    holder = subprocess.Popen(
        [sys.executable, str(SCRIPTS / "_land_lease_holder.py"), str(v6_dir), "25"],
        stdout=subprocess.PIPE, text=True)
    ready = holder.stdout.readline().strip()          # the child says it HAS the lease
    _, holder_resource, holder_lock = ready.split(" ", 2)
    before = origin_main(fx)
    rc, out = run_cli("land", iso["plan_dir"])        # a second, separate process
    land_resource = lst.context(iso["plan_dir"])["resource"]
    land_lock = str(sl_lock_path(iso["plan_dir"], land_resource))
    # DERIVED INDEPENDENTLY of `_ship_lock_path`, from git itself: asking the
    # implementation where its own lock lives cannot catch the implementation
    # putting it in the wrong place — both sides would agree on a wrong answer
    # and the proof would still read green.
    common = git(["rev-parse", "--path-format=absolute", "--git-common-dir"],
                 fx["root"], check=True)[1]
    independent = sorted(str(x) for x in (Path(common) / "plan-locks").glob("*.lock"))
    positive = {"command": "process A: a v6 ship step holds git:<root>; process B: run.py land",
                "holder_pid": holder.pid, "holder_resource": holder_resource,
                "holder_lock_file": holder_lock, "land_resource": land_resource,
                "land_lock_file": land_lock,
                "same_lock_file": holder_lock == land_lock,
                "git_common_dir": common,
                "lock_files_git_itself_reports": independent,
                "lock_file_is_where_git_says": land_lock in independent,
                "exit_code": rc, "action": out.get("action"),
                "resource": out.get("resource"),
                "origin_main_before": before, "origin_main_after": origin_main(fx),
                # `same_lock_file` is asserted, not assumed: two spellings of one
                # repo slug to two files and BOTH sides "acquire" — s03b's
                # `same file? False`, reproduced in this fixture.
                "ok": (out.get("action") == "land-locked" and holder_lock == land_lock
                       and land_lock in independent and origin_main(fx) == before)}
    holder.terminate()
    holder.wait(timeout=30)
    release_lock(v6_dir, holder_resource)
    rc2, out2 = _land_with_ack(fx, iso)
    negative = {"command": "the same command after the v6 holder releases",
                "exit_code": rc2, "action": out2.get("action"),
                "ok": out2.get("action") == "landed"}
    return _rec("land-pair-v6-shipping-during-v7-land",
                "The repo lease EXCLUDES across process boundaries: a v6 plan holding "
                "git:<root> refuses a v7 land, and the same land succeeds once released.",
                positive, negative)


def proof_pair_operator_dirty_main(tmp):
    """The operator is ON main with uncommitted edits while the plan lands."""
    fx = build_repo(tmp / "dirty")
    iso = isolate(fx, "plan-a")
    work(iso["tree"], "src/feature.py", "FEATURE = 1\n")
    (Path(fx["root"]) / "shared.txt").write_text("OPERATOR EDIT, uncommitted\n")
    before_branch, before_main = checked_out_branch(fx), local_main(fx)
    before_dirty = git(["status", "--porcelain"], fx["root"], strip=False)[1]
    rc, out = _land_with_ack(fx, iso)
    after_dirty = git(["status", "--porcelain"], fx["root"], strip=False)[1]
    positive = {"command": "run.py land <plan-dir> with the primary checkout ON main, DIRTY",
                "exit_code": rc, "action": out.get("action"),
                "checked_out_branch_before": before_branch,
                "checked_out_branch_after": checked_out_branch(fx),
                "local_main_before": before_main, "local_main_after": local_main(fx),
                "origin_main_after": origin_main(fx),
                "operator_dirty_before": before_dirty.strip().splitlines(),
                "operator_dirty_after": after_dirty.strip().splitlines(),
                # The claim is not "nothing appeared" — §8.a has the orchestrator
                # writing `_plans/<slug>/` in the primary checkout for the whole
                # run, and the land's own brief (LAND_NOTICE.txt) joins it there.
                # The claim is that NOTHING OUTSIDE THE PLAN'S RECORD moved, and
                # the operator's own edit is byte-identical.
                "appeared_outside_the_plan_record": [
                    d for d in after_dirty.strip().splitlines()
                    if d not in before_dirty.strip().splitlines()
                    and not d[3:].startswith("_plans/plan-a/")],
                "operator_file_content": (Path(fx["root"]) / "shared.txt").read_text(),
                "operator_edit_still_uncommitted": "M shared.txt" in after_dirty,
                "catch_up": out.get("catch_up"),
                "ok": (out.get("action") == "landed"
                       and checked_out_branch(fx) == before_branch == "main"
                       and local_main(fx) == before_main
                       and (Path(fx["root"]) / "shared.txt").read_text()
                           == "OPERATOR EDIT, uncommitted\n"
                       and not [d for d in after_dirty.strip().splitlines()
                                if d not in before_dirty.strip().splitlines()
                                and not d[3:].startswith("_plans/plan-a/")]
                       and out.get("catch_up", {}).get("state") == "dirty"
                       and "shared.txt" in (out.get("catch_up", {}).get("dirty_paths") or [])
                       and not out.get("catch_up", {}).get("commands")
                       and out.get("local_default_unchanged") is True)}
    # NEGATIVE CONTROL — a primary checkout with NO operator edit gets a
    # fast-forward path instead of the refusal, so the brief is shown to be
    # READING the tree rather than printing one fixed answer. (It is never
    # literally "clean": the orchestrator writes `_plans/<slug>/` in the primary
    # checkout for the whole run by construction — §8.a — which is exactly the
    # case the classifier tells apart.)
    fx2 = build_repo(tmp / "clean")
    iso2 = isolate(fx2, "plan-a")
    work(iso2["tree"], "src/feature.py", "FEATURE = 1\n")
    _, out2 = _land_with_ack(fx2, iso2)
    cu2 = out2.get("catch_up", {})
    negative = {"command": "the same land with no operator edit in the primary checkout",
                "catch_up": cu2,
                "ok": (cu2.get("state") in ("clean", "dirty-plan-record-only")
                       and bool(cu2.get("commands"))
                       and all(d.startswith("_plans/") for d in cu2.get("dirty_paths") or []))}
    return _rec("land-pair-operator-on-dirty-main",
                "A land never touches the operator's checkout: same branch, same local "
                "main sha, same uncommitted edits — and the brief reads the tree.",
                positive, negative)


def proof_pair_kill_mid_merge(tmp):
    """A REAL SIGKILL inside the land-worktree merge."""
    fx = build_repo(tmp / "kill")
    iso = isolate(fx, "plan-a")
    work(iso["tree"], "src/feature.py", "FEATURE = 1\n")
    _arm_slow_commit(fx)
    before_main, before_origin = local_main(fx), origin_main(fx)
    proc, land_path = _kill_when(fx, iso, lambda st: st.get("land_path")
                                 and lst.merge_in_progress(st["land_path"]),
                                 lambda st: st.get("land_path"))
    outer_dirty = git(["status", "--porcelain"], fx["root"], strip=False)[1].strip()
    rc_p, parked = run_cli("land", iso["plan_dir"])
    git(["merge", "--abort"], land_path)                    # the brief's own command
    rc_r, resumed = _land_with_ack(fx, iso)
    positive = {"command": "SIGKILL run.py land inside the merge, then resume",
                "killed_pid": proc.pid, "land_worktree": land_path,
                "merge_head_in_land_worktree_at_kill": True,
                "merge_head_in_primary_checkout": lst.merge_in_progress(fx["root"]),
                "merge_head_in_plan_worktree": lst.merge_in_progress(iso["tree"]),
                "local_main_before": before_main, "local_main_after_kill": local_main(fx),
                "origin_main_before": before_origin,
                "origin_main_after_kill": origin_main(fx),
                "primary_checkout_dirty_after_kill": outer_dirty.splitlines(),
                "resume_exit_code": rc_p, "resume_action": parked.get("action"),
                "resume_kind": parked.get("kind"),
                "after_abort_exit": rc_r, "after_abort_action": resumed.get("action"),
                "origin_main_after_resume": origin_main(fx),
                "ok": (local_main(fx) == before_main
                       and not lst.merge_in_progress(fx["root"])
                       and not lst.merge_in_progress(iso["tree"])
                       and parked.get("kind") == "merge-in-progress"
                       and resumed.get("action") == "landed"
                       and origin_main(fx) != before_origin)}
    # NEGATIVE CONTROL — the confinement claim rests on `merge_in_progress` being
    # FALSE for the operator's checkout. A predicate that only ever answers False
    # has not been shown able to answer anything: plant the identical state THERE
    # and require it to be seen. (Planted, read, and immediately aborted.)
    fx2 = build_repo(tmp / "kill-neg")
    main_side(fx2, "shared.txt", "main's line\n")
    git(["fetch", "origin", "main"], fx2["root"], check=True)
    (Path(fx2["root"]) / "shared.txt").write_text("the operator's line\n")
    git(["commit", "-q", "-am", "operator commit"], fx2["root"], check=True)
    git(["merge", "origin/main"], fx2["root"])              # conflicts on purpose
    detected = lst.merge_in_progress(fx2["root"])
    git(["merge", "--abort"], fx2["root"])
    negative = {"command": "plant a half-finished merge in the PRIMARY checkout and ask "
                           "the same predicate about it",
                "merge_in_progress_when_planted": detected,
                "merge_in_progress_after_abort": lst.merge_in_progress(fx2["root"]),
                "ok": detected is True and lst.merge_in_progress(fx2["root"]) is False}
    return _rec("land-pair-kill-mid-merge",
                "A kill mid-merge confines the mess to the land worktree (MERGE_HEAD there, "
                "local main and origin/main unmoved, primary checkout and plan worktree "
                "clean), the next land NAMES it rather than auto-concluding it, and cleanup "
                "owns it. The confinement predicate is shown able to report the opposite.",
                positive, negative)


def _killed_between_steps(tmp):
    """Kill BETWEEN step 3 (merge) and step 5 (gates); return the fixture and the
    merge sha that existed at the moment of the kill."""
    fx = build_repo(tmp)
    iso = isolate(fx, "plan-a")
    work(iso["tree"], "src/feature.py", "FEATURE = 1\n")
    _arm_slow_gate(fx)
    before = origin_main(fx)
    proc, merged = _kill_when(
        fx, iso, lambda st: st.get("merge_sha") and st.get("record_sha"),
        lambda st: st["merge_sha"])
    return fx, iso, merged, before, proc


def proof_resume_after_kill(tmp):
    """§ durable state — a crash mid-land RESUMES, it does not restart."""
    fx, iso, merged, before, proc = _killed_between_steps(tmp / "resume")
    rc, out = _land_with_ack(fx, iso)
    st = lst.load(iso["plan_dir"])
    positive = {"command": "SIGKILL run.py land between the merge (step 3) and the gates "
                           "(step 5), then re-run with NO operator action",
                "killed_pid": proc.pid, "merge_sha_at_kill": merged,
                "origin_main_before": before, "origin_main_after_kill": before,
                "exit_code": rc, "action": out.get("action"),
                "merge_sha_after_resume": st.get("merge_sha"),
                "resumed_not_redone": merged == st.get("merge_sha"),
                "origin_main_after": origin_main(fx),
                "ok": (out.get("action") == "landed" and merged is not None
                       and merged == st.get("merge_sha") and origin_main(fx) != before)}
    # NEGATIVE CONTROL — "it resumed" is the equality `merge_sha_at_kill ==
    # merge_sha_after`. An equality that can never break proves nothing, so:
    # move the default branch under the same killed land and require the merge to
    # be REBUILT (§4.1's captured sha changed), i.e. the very same equality FAILS.
    fx2, iso2, merged2, before2, _ = _killed_between_steps(tmp / "resume-neg")
    main_side(fx2, "src/elsewhere.py", "E = 1\n")
    rc2, out2 = _land_with_ack(fx2, iso2)
    st2 = lst.load(iso2["plan_dir"])
    negative = {"command": "the same kill, but the default branch moves before the resume",
                "merge_sha_at_kill": merged2, "exit_code": rc2, "action": out2.get("action"),
                "merge_sha_after_resume": st2.get("merge_sha"),
                "resumed_not_redone": merged2 == st2.get("merge_sha"),
                "ok": (merged2 is not None and st2.get("merge_sha") is not None
                       and merged2 != st2.get("merge_sha"))}
    return _rec("resume-after-kill-between-steps",
                "A land killed between the merge and the gates resumes on the SAME merge "
                "commit with no operator action; and when the default branch moved under "
                "it, the same equality correctly reports a REBUILT candidate instead.",
                positive, negative)


PROOFS = (proof_checkpoint, proof_stale_ack, proof_forged_ack, proof_pair_two_v7,
          proof_pair_v6_during_v7, proof_pair_operator_dirty_main,
          proof_pair_kill_mid_merge, proof_resume_after_kill)


def _jobs():
    """Every proof as ``(name, callable(tmp))``. The NEUTER proofs — each fix
    reverted on a COPY of the tree, its guarding test re-run there and required
    to FAIL — live in ``_evidence_land_rework`` (its own module for the 500-LOC
    bound) and are appended here so ONE artifact carries both kinds: a fix whose
    test cannot fail is not covered, and that belongs beside the protocol proofs
    rather than in a second file nobody opens."""
    import _evidence_land_rework as rw
    return ([(fn.__name__, fn) for fn in PROOFS] +
            [(f"neuter-{i}", lambda tmp, s=spec, i=i: rw.proof_neuter(tmp, s, i))
             for i, spec in enumerate(rw.NEUTERS)])


def main(argv=None):
    argv = argv or sys.argv[1:]
    out_path = Path(argv[0]) if argv else Path("land-proofs.json")
    import tempfile
    records, tmp = [], Path(tempfile.mkdtemp(prefix="land-proofs-"))
    for name, fn in _jobs():
        try:
            records.append(fn(tmp))
        except Exception as e:                          # noqa: BLE001 — a proof that
            records.append({"proof": name, "holds": False,        # CRASHED is a FAIL,
                            "error": f"{type(e).__name__}: {e}"})  # never an omission
    doc = {"artifact": "land-proofs", "session": "s08", "item": "LND-01",
           "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "generator": "skills/plan-execute/scripts/_evidence_land_proofs.py",
           "git_version": subprocess.run(["git", "--version"], capture_output=True,
                                         text=True).stdout.strip(),
           "contract": ["plan-isolation-contract.md §4 (land), §5 (approval binding), "
                        "§8.c (record), §12.3 (teardown order)"],
           "neuter_proofs": "skills/plan-execute/scripts/_evidence_land_rework.py — each "
                            "rework fix reverted on a COPY of the tree, its regression "
                            "test re-run there and required to FAIL",
           "production_path": ["run.py land / land-ack / land-record / land-status",
                               "land.land -> land_steps.STEPS -> land_steps.step_finish",
                               "ship_locks.acquire_ship_lock (repo-scoped git: lease)",
                               "plan_ship.record_plan (§8.c explicit pathspec)",
                               "shipping.resolve_gate -> ship_state_io.run_deploy_argv"],
           "fixture": {"tmp_root": str(tmp), "shape": "bare origin + primary checkout on "
                       "main + locked plan worktree(s) + third clones for main-side moves"},
           "records": records,
           "all_proofs_hold": all(r.get("holds") for r in records)}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n")
    print(json.dumps({"written": str(out_path), "proofs": len(records),
                      "all_proofs_hold": doc["all_proofs_hold"],
                      "failed": [r["proof"] for r in records if not r.get("holds")]},
                     indent=2))
    return 0 if doc["all_proofs_hold"] else 1


if __name__ == "__main__":
    sys.exit(main())
