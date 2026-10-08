"""LND-01 rework — the NEUTER PROOFS for the four resume-path fixes.

Its own module because ``_evidence_land_proofs.py`` is 479 LOC against this
repo's 500-LOC bound, and because these proofs are a different KIND: the others
plant a condition and watch the protocol refuse; these revert a fix and watch the
test that guards it FAIL.

    positive        the regression test, against the fixed tree  -> must PASS
    negative control the SAME test, against a tree with the fix
                     reverted by the patch below                 -> must FAIL

A test that passes when the fix is removed is not a test, it is a comment. This
repo has recorded that as "a passing neuter is a broken probe" — and its
corollary: a FAILING neuter is only a receipt if it fails for the RIGHT reason,
so each record carries the neutered run's own assertion text.

The neuter is applied to a COPY of the scripts tree in a temp directory, never
to the checkout. A planted failure in a shared tree becomes a finding against
whichever peer session commits next; that too is measured here.

Run it:  python3 _evidence_land_rework.py <out.json>
"""

import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
REPO = SCRIPTS.parents[2]
TEST = "test_land_resume.py"
SURFACE_TEST = "test_llm_review_surface.py"
ROUND3_TEST = "test_land_round3.py"

# Each entry reverts ONE fix with the smallest edit that restores the measured
# defect — nothing else changes, so a failure can only be that defect.
NEUTERS = (
    {"finding": "1 — an indeterminate re-gate parks the land forever",
     "test": "test_an_indeterminate_regate_re_runs_until_it_can_decide",
     "file": "land_gate.py",
     "reverts": "the pass-only cache — restoring the whole-run cache that stored and "
                "replayed ANY outcome, indeterminate included",
     "patches": [
         ('            if rec["outcome"] == "pass":',
          '            if True:  # NEUTERED: cache every outcome, pass or not'),
         ('    if rec and rec.get("gate_key") == key and rec.get("outcome") == "pass":',
          '    if rec and rec.get("gate_key") == key:  # NEUTERED: replay any outcome')]},
    {"finding": "2 — the §5.1a stray-pathspec refusal fires once",
     "test": "test_the_stray_pathspec_refusal_fires_on_every_run_not_just_the_first",
     "file": "land_steps.py",
     "reverts": "the ordering — record_sha back ahead of the stray check AND the "
                "short-circuit park() then makes reachable, exactly as it stood",
     "patches": [
         ('    if not (st.get("record_sha") and rev(path, "HEAD") == st["record_sha"]):',
          '    if st.get("record_sha") and rev(path, "HEAD") == st["record_sha"]:\n'
          '        return None  # NEUTERED: the one-shot short-circuit\n'
          '    if True:'),
         ('    recorded = lst.range_files(path, st["merge_sha"], "HEAD")',
          '    st["record_sha"] = rev(path, "HEAD")  # NEUTERED: written before the check\n'
          '    recorded = lst.range_files(path, st["merge_sha"], "HEAD")')]},
    {"finding": "3 — a stale land worktree is force-removed with content in it",
     "test": "test_a_stale_land_worktree_holding_content_is_never_force_removed",
     "file": "land_steps.py",
     "reverts": "force=False on the stale-worktree teardown",
     "patches": [
         ('        status, extra = wt.teardown_worktree(ctx["root"], Path(path), False)',
          '        status, extra = wt.teardown_worktree(ctx["root"], Path(path), True)'
          '  # NEUTERED')]},
    {"finding": "4 — every skill-gate round trip re-runs all argv gates",
     "test": "test_a_passing_argv_gate_is_not_re_run_by_the_skill_gate_round_trip",
     "file": "land_gate.py",
     "reverts": "the per-gate pass cache lookup, so the accumulated argv results are "
                "discarded across the invoke-skill round trip",
     "patches": [
         ("        rec = _cached_pass(st, gid, key)",
          "        rec = None  # NEUTERED: discard the accumulated argv results")]},
    {"finding": "1b — the stray-pathspec park never clears (regression of fix 2)",
     "test": "test_the_stray_pathspec_refusal_fires_on_every_run_not_just_the_first",
     "file": "land_steps.py",
     "reverts": "the reset of the land worktree back to the approved merge, so a "
                "refused record commit stays inside merge_sha..HEAD forever",
     "patches": [
         ('        if rev(path, "HEAD") != merge_sha:',
          '        if False:  # NEUTERED: keep the refused record commit')]},
    {"finding": "2b — the dirty-worktree brief names a blind command (regression of fix 3)",
     "test": "test_a_git_ignored_gate_artefact_parks_with_a_command_that_can_see_it",
     "file": "land_brief.py",
     "reverts": "--ignored=matching in the preserved-worktree recovery block",
     "patches": [
         ('    lines += ["", f"  cd {path}", "  git status --porcelain --ignored=matching",',
          '    lines += ["", f"  cd {path}", "  git status --porcelain",  # NEUTERED')]},
    {"finding": "5 — a dropped path is named with the wrong reason",
     "test": "test_a_dropped_path_is_named_with_the_reason_that_actually_dropped_it",
     "test_file": SURFACE_TEST,
     "file": "llm_review_surface.py",
     "reverts": "the scope-only second diff that tells the two drop reasons apart",
     "patches": [
         ("    scoped = set(diff_stat(cwd, base, scope, ()) or []) "
          "if scope and exclude else None",
          "    scoped = None  # NEUTERED: one reason for every dropped path")]},
    {"finding": "r3f1 — a resume after the land worktree is removed re-walks a landed plan",
     "test": "test_a_landed_plan_resumes_to_landed_after_its_worktree_is_removed_by_hand",
     "test_file": ROUND3_TEST,
     "file": "land_push.py",
     "reverts": "the worktree-independent ancestry check — restoring the is_dir() guard "
                "under which already_landed answers False for a plan already on the target",
     "patches": [
         ('    return git(["merge-base", "--is-ancestor", st["landed_sha"], _land_target(ctx)],\n'
          '               ctx["root"])[0] == 0',
          '    if not (st.get("land_path") and Path(st["land_path"]).is_dir()):\n'
          '        return False  # NEUTERED: require the worktree directory to exist\n'
          '    return git(["merge-base", "--is-ancestor", st["landed_sha"], _land_target(ctx)],\n'
          '               ctx["root"])[0] == 0')]},
    {"finding": "r3f2 — a timed-out gate scores fail instead of indeterminate",
     "test": "test_a_timed_out_gate_parks_indeterminate_not_failed",
     "test_file": ROUND3_TEST,
     "file": "land_gate.py",
     "reverts": "the shared argv_outcome classification — restoring the rc-only branch "
                "under which returncode None (timeout, exec error) collapses to fail",
     "patches": [
         ('    verdict = ssio.argv_outcome(res, g.get("indeterminate_exit"))\n'
          '    outcome = "skipped" if verdict == "indeterminate" else verdict',
          '    if g.get("indeterminate_exit") is not None and rc == g["indeterminate_exit"]:\n'
          '        outcome = "skipped"  # NEUTERED: timeout/error collapse to fail\n'
          '    else:\n'
          '        outcome = "pass" if rc == 0 else "fail"')]},
    {"finding": "r3f3 — the decision brief offers a subcommand that does not exist",
     "test": "test_the_abandon_option_in_the_review_brief_actually_runs",
     "test_file": ROUND3_TEST,
     "file": "land_brief.py",
     "reverts": "the executable abandon command — restoring `run.py retire-plan`, which "
                "no parser registers (argparse exit 2)",
     "patches": [
         ('        f"  abandon    ->  git -C {root} worktree remove {land_path}",',
          '        f"  abandon    ->  run.py retire-plan {plan_dir}",  # NEUTERED')]},
    {"finding": "r3f4 — the evidence record misdescribes its own probe",
     "test": "test_proof_checkpoint_runs_the_premature_ack_before_any_land_and_checks_it",
     "test_file": ROUND3_TEST,
     "file": "_evidence_land_proofs.py",
     "reverts": "the ack ordering — running the 'premature' ack AFTER the parked lands "
                "again, when a candidate exists and the ack is ACCEPTED",
     "patches": [
         ('    rc_ack, ack_out = run_cli("land-ack", iso["plan_dir"], "--note", "forged")\n'
          '    attempts = []',
          '    attempts = []  # NEUTERED: the ack moves below, after the lands'),
         ('    positive = {\n        "premature_land_ack"',
          '    rc_ack, ack_out = run_cli("land-ack", iso["plan_dir"], "--note", "forged")\n'
          '    positive = {\n        "premature_land_ack"')]},
    {"finding": "r3f5 — an upstream-only repo is treated as having no remote",
     "test": "test_an_upstream_only_repo_still_pushes_the_land",
     "test_file": ROUND3_TEST,
     "file": "land_state.py",
     "reverts": "the sole-remote fallback — restoring `origin or nothing`, under which "
                "the land fetches nothing, pushes nothing and reports a shipped land",
     "patches": [
         ('    remote = "origin" if "origin" in remotes '
          'else (remotes[0] if len(remotes) == 1 else None)',
          '    remote = "origin" if "origin" in remotes else None  # NEUTERED')]},
    {"finding": "r3f6 — the stray-pathspec refusal fails open on a git error",
     "test": "test_range_files_fails_closed_and_the_pathspec_check_parks_on_it",
     "test_file": ROUND3_TEST,
     "file": "land_state.py",
     "reverts": "range_files failing closed — restoring `[]` on git failure, so \"git "
                "errored\" and \"no stray paths\" are the same answer",
     "patches": [
         ('    return None if rc != 0 else [p for p in out.split("\\0") if p]',
          '    return [] if rc != 0 else [p for p in out.split("\\0") if p]  # NEUTERED')]},
    {"finding": "r3f7 — the review brief declares an argument nothing reads",
     "test": "test_the_review_brief_takes_no_dead_catch_up_argument",
     "test_file": ROUND3_TEST,
     "file": "land_brief.py",
     "reverts": "the dead-parameter removal — restoring `catch_up_info` to review()'s "
                "signature",
     "patches": [
         ('def review(candidate, gates, plan_dir, land_path, root, prior=None):',
          'def review(candidate, gates, catch_up_info, plan_dir, land_path, root, '
          'prior=None):  # NEUTERED')]},
)


def _run_test(scripts_dir, spec, timeout=900):
    """One regression test, as a REAL pytest subprocess in ``scripts_dir``."""
    argv = [sys.executable, "-m", "pytest", spec.get("test_file", TEST), "-q",
            "-p", "no:randomly", "-k", spec["test"], "--no-header", "-x"]
    p = subprocess.run(argv, cwd=str(scripts_dir), capture_output=True, text=True,
                       timeout=timeout)
    return {"command": " ".join(argv[1:]), "cwd": str(scripts_dir),
            "exit_code": p.returncode, "tail": p.stdout[-1500:]}


def _neutered_tree(tmp, spec, i):
    """A COPY of the two scripts trees the entry points import from, with one fix
    reverted. Both are copied because `run.py` reaches into plan-builder, and a
    check pointed at an incomplete tree reports a defect that is not there."""
    dest = Path(tmp) / f"neuter-{i}"
    for skill in ("plan-execute", "plan-builder"):
        shutil.copytree(REPO / "skills" / skill / "scripts",
                        dest / "skills" / skill / "scripts",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    target = dest / "skills" / "plan-execute" / "scripts" / spec["file"]
    src = target.read_text()
    for old, sub in spec["patches"]:
        # An anchor that matched zero times would neuter NOTHING and the proof
        # would read as "the test passes without the fix" — the exact wrong
        # conclusion. Two matches would neuter more than the one fix. Both refuse.
        if src.count(old) != 1:
            raise AssertionError(f"neuter anchor in {spec['file']} matched "
                                 f"{src.count(old)} times, not once: {old!r}")
        src = src.replace(old, sub)
    target.write_text(src)
    return dest / "skills" / "plan-execute" / "scripts"


def proof_neuter(tmp, spec, i):
    positive = {**_run_test(SCRIPTS, spec), "expected": "pass"}
    positive["ok"] = positive["exit_code"] == 0
    scripts = _neutered_tree(tmp, spec, i)
    negative = {**_run_test(scripts, spec), "expected": "fail",
                "reverted": spec["reverts"],
                "patch": {"file": spec["file"],
                          "edits": [{"from": o, "to": n} for o, n in spec["patches"]]}}
    # A NON-ZERO EXIT IS NOT THE RECEIPT. A collection error, an ImportError, a
    # syntax error the patch itself introduced or a timeout all exit non-zero and
    # would read as "the neuter worked". The receipt is pytest reporting a FAILED
    # assertion — the test's own check disagreeing with the neutered behaviour.
    tail = negative["tail"]
    negative["failed_on_an_assertion"] = "AssertionError" in tail and " failed" in tail
    negative["ok"] = negative["exit_code"] != 0 and negative["failed_on_an_assertion"]
    # The finding tag is IN the name: two findings can guard the same test (2 and
    # 1b both ride `test_the_stray_pathspec_refusal_...`), and two records with
    # one name collapse to one for any reader that keys on it.
    tag = spec["finding"].split(" ", 1)[0]
    return {"proof": f"neuter::{tag}::{spec['test']}", "finding": spec["finding"],
            "claim": "The regression test PASSES against the fix and FAILS against a tree "
                     f"with {spec['reverts']} reverted — so it can tell the two apart.",
            "positive": positive, "negative_control": negative,
            "holds": positive["ok"] and negative["ok"]}


def main(argv=None):
    argv = argv or sys.argv[1:]
    out_path = Path(argv[0]) if argv else Path("land-rework-proofs.json")
    tmp = Path(tempfile.mkdtemp(prefix="land-rework-"))
    records = []
    for i, spec in enumerate(NEUTERS):
        try:
            records.append(proof_neuter(tmp, spec, i))
        except Exception as e:                            # noqa: BLE001 — a proof that
            records.append({"proof": f"neuter::{spec['test']}",   # CRASHED is a FAIL,
                            "finding": spec["finding"],           # never an omission
                            "holds": False, "error": f"{type(e).__name__}: {e}"})
    doc = {"artifact": "land-rework-neuter-proofs", "session": "s08", "item": "LND-01",
           "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "generator": "skills/plan-execute/scripts/_evidence_land_rework.py",
           "regression_tests": [f"skills/plan-execute/scripts/{TEST}",
                                f"skills/plan-execute/scripts/{SURFACE_TEST}"],
           "method": "each fix is reverted by a one-line patch applied to a COPY of the "
                     "plan-execute + plan-builder scripts trees; the same test is then "
                     "re-run there and MUST fail",
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
