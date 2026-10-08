#!/usr/bin/env python3
"""ISO-02 evidence — one END-TO-END isolated ship, measured, not asserted.

Builds a throwaway repo (with a real `origin`), builds a real plan with
`build_plan`, runs the REAL `run.py begin --isolate`, writes a session's work
into the plan worktree and its record into the OUTER plan directory, then drives
the REAL shipping state machine (`ship-begin` -> `ship-run commit` -> `ship-run
push` -> `ship-finalize`) and reports what actually moved.

Every number in the output is read back from git AFTER the operation. The script
FAILS (exit 1) rather than emitting a green artifact if the ship did nothing, if
any `_plans/` path rode the commit, or if the primary checkout's tree moved.

    python3 _evidence_ship_scoping.py <output.json>
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS.parent.parent / "plan-builder" / "scripts"))

import build_plan  # noqa: E402
import closeout_pipeline as cp  # noqa: E402
import manifest_io as mio  # noqa: E402
import plan_scope as ps  # noqa: E402
import plan_ship as pship  # noqa: E402
import plan_teardown as pt  # noqa: E402
import plan_worktree as pwt  # noqa: E402
import review_context as rvs  # noqa: E402
import ship_state_io as ssio  # noqa: E402
import shipping as shp  # noqa: E402
import verify as vfy  # noqa: E402
from worktree import git  # noqa: E402

SLUG = "iso-ship-fixture"
SESSION = "s01"


def _spec():
    return {
        "title": "ISO-02 ship fixture",
        "categories": [{"key": "work", "label": "Work"}],
        "items": [{"id": "i1", "title": "I1", "category": "work"}],
        "phases": [],
        "sessions": [{"id": SESSION, "title": "Fixture session", "model": "Sonnet",
                      "items": ["i1"], "prompt": "do the fixture work",
                      "post_session": {"git": "commit-push"}}],
        "infographic": {
            "type": "phase-journey", "title": "t",
            "phases": [{"num": 1, "name": "P1", "items": ["i1"]}],
            "anchor_now": {"name": "a", "tagline": "b"},
            "anchor_goal": {"name": "c", "tagline": "d"},
        },
    }


def _build_repo(tmp):
    origin = tmp / "origin.git"
    origin.mkdir()
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    root = tmp / "proj"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("A = 1\n")
    (root / ".gitignore").write_text("__pycache__/\n")
    (root / ".claude").mkdir()
    (root / ".claude" / "deploy-targets.json").write_text("{}")
    (root / ".claude" / "eval-gates.json").write_text("{}")
    plan_dir = root / "_plans" / SLUG
    build_plan.build(_spec(), plan_dir, project_root=str(root))
    git(["init", "-q", "-b", "main", "."], root, check=True)
    git(["config", "user.email", "t@example.com"], root, check=True)
    git(["config", "user.name", "T"], root, check=True)
    git(["add", "-A"], root, check=True)
    git(["commit", "-q", "-m", "base"], root, check=True)
    git(["remote", "add", "origin", str(origin)], root, check=True)
    git(["push", "-q", "-u", "origin", "main"], root, check=True)
    hooks = root / ".git" / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    (hooks / "post-commit").write_text("#!/bin/sh\nexit 0\n")
    (hooks / "post-commit").chmod(0o755)
    return root, origin, plan_dir


def _begin(plan_dir):
    """The REAL dispatch path — run.py in a subprocess, exactly as SKILL.md drives it."""
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / "run.py"), "begin", str(plan_dir),
         "--sessions", SESSION, "--isolate"],
        capture_output=True, text=True, check=False,
        env={**os.environ, "PYTHONPATH": str(SCRIPTS)},
    )
    if proc.returncode != 0:
        raise SystemExit(f"run.py begin failed ({proc.returncode}):\n{proc.stderr}")
    return json.loads(proc.stdout)


def _ship(plan_dir):
    """Drive the shipping state machine to completion, recording each directive."""
    trail = []
    action = shp.ship_begin(plan_dir, SESSION)
    while action.get("action") in ("invoke-skill", "run-argv"):
        trail.append({"action": action["action"], "step": action.get("step"),
                      "skill": action.get("skill")})
        if action["action"] != "run-argv":
            raise SystemExit(
                f"step {action.get('step')!r} came back as a SKILL directive under "
                "isolation — /commit-orchestrate would run in the orchestrating "
                "conversation's cwd, not the plan worktree (ISO-02).")
        action = shp.ship_run_argv(plan_dir, SESSION, action["step"])
    trail.append({"action": action.get("action"), "reason": action.get("reason"),
                  "message": action.get("message")})
    return action, trail


def _plant_decoy(tree):
    """Planted AFTER the ship so it cannot ride the commit. It is the reason the
    evidence split is observable at all: an agent told to `cd` into the worktree
    writes `_evidence/s01/proof.txt` RELATIVE TO IT, and unless `_evidence/` is
    pinned to the outer plan directory by RULE a "first existing root wins" search
    finds this one — the gate then passes on a file the plan record will never
    contain. (Measured: the first neuter probe of the split PASSED while neutered,
    because without the decoy both orderings returned the same path.)"""
    for rel in (Path("_evidence") / SESSION,
                Path("_plans") / SLUG / "_evidence" / SESSION):
        # BOTH spellings, because a manifest declares the repo-relative one: this
        # plan's own says `_plans/<slug>/_evidence/s02`. Matching only the literal
        # `_evidence/` prefix sends that down the repo-relative branch, which
        # searches the worktree FIRST -- and the worktree's `_plans/` copy is the
        # FROZEN one from the pinned base.
        (Path(tree) / rel).mkdir(parents=True, exist_ok=True)
        (Path(tree) / rel / "proof.txt").write_text("DECOY\n")


def _vanished_worktree_halt(plan_dir, tree):
    """A plan that CLAIMS a worktree which is gone must HALT, not traceback.

    The checkout is MOVED ASIDE rather than deleted, so every other reading in
    this artifact still describes a live plan. `compute_steps` translates the
    `wt.WorktreeError` refusal; every entry point that resolves steps —
    `ship_begin`, `ship_status`, and `ship_record`/`ship_run_argv` through
    `_reload` — must halt, not traceback. The mid-ship path is EXERCISED below,
    not stated (attempt 2's reviewer probed exactly that gap: a traceback after
    `ship-begin` already held the lease, and the lease stayed held)."""
    gone = Path(str(tree) + ".moved-aside")
    Path(tree).rename(gone)
    try:
        raised = status = reason = None
        try:
            status = shp.ship_status(plan_dir, SESSION)
        except BaseException as e:            # noqa: BLE001 — the failure measured
            raised = f"{type(e).__name__}: {e}"
        try:
            shp.compute_steps(plan_dir, mio.load_manifest(plan_dir), SESSION)
            reason = "NO REFUSAL AT ALL"
        except shp.StepResolveError as e:
            reason = e.reason
        except BaseException as e:            # noqa: BLE001
            reason = f"UNTRANSLATED {type(e).__name__}"
        try:
            rec = shp.ship_record(plan_dir, SESSION, "commit", "done")
            record = {"action": rec.get("action"), "reason": rec.get("reason")}
        except BaseException as e:            # noqa: BLE001
            record = {"raised": f"{type(e).__name__}: {e}"}
        held = [str(r) for r in ssio.held_ship_locks(plan_dir)]
    finally:
        gone.rename(tree)
    return {"ship_status_raised": raised,
            "ship_status_steps": (status or {}).get("steps"),
            "ship_status_error": (status or {}).get("error"),
            "compute_steps_reason": reason, "ship_record_mid_ship": record,
            "leases_held_after_mid_ship_halt": held}


def _post_teardown_degrade(plan_dir):
    """After a LEGITIMATE teardown the plan must degrade to the pre-isolation
    path, not refuse forever (the recurring defect class: a refusal that cannot
    be cleared). The decoys left in the tree make the FIRST teardown refuse
    ("preserved") — claim stays LIVE; the forced second removes. Runs LAST."""
    first = pt.remove_plan_worktree(plan_dir)
    claim_while_preserved = bool(ps.claim(plan_dir).get("path"))
    second = pt.remove_plan_worktree(plan_dir, force=True)
    try:
        ps.require_live(plan_dir)
        refusal = None
        preamble = ps.dispatch_preamble(plan_dir, {"id": SESSION}, SESSION, None)
    except BaseException as e:            # noqa: BLE001 — the failure measured
        refusal = f"{type(e).__name__}: {e}"
        preamble = f"UNREADABLE — refused: {refusal}"
    return {"refused_teardown_status": first["status"],
            "claim_live_while_preserved": claim_while_preserved,
            "forced_teardown_status": second["status"],
            "claim_after": ps.claim(plan_dir),
            "plan_worktree_after": ps.plan_worktree(plan_dir),
            "require_live_refusal_after": refusal, "preamble_after": preamble}


def _rev(repo, ref):
    """A ref's sha, or None. `None` is a REAL answer here — a ship that failed
    never publishes its branch, and the report must be able to say so instead of
    crashing before it is written."""
    rc, out, _ = git(["rev-parse", ref], repo)
    return out if rc == 0 else None


def _v6_neighbour_sweep(root, plan_dir):
    """§8.d with a REAL schema-6 neighbour: today's repo-wide `git add -A`, run in
    the shared checkout while the v7 plan is live."""
    nb = root / "_plans" / "legacy-neighbour"
    nb.mkdir(parents=True, exist_ok=True)
    (nb / "manifest.json").write_text(json.dumps({"plan_schema_version": 6}))
    (nb / "PLAN.html").write_text("<html>neighbour</html>\n")
    enabled, reason = pwt.isolation_enabled(json.loads((nb / "manifest.json").read_text()))
    git(["add", "-A"], root, check=True)
    staged = git(["diff", "--cached", "--name-only"], root, check=True)[1].splitlines()
    git(["commit", "-q", "-m", "chore(legacy-neighbour): sweep"], root, check=True)
    sha = git(["rev-parse", "HEAD"], root, check=True)[1]
    tracked = git(["ls-tree", "-r", "--name-only", sha], root, check=True)[1].splitlines()
    claim = pwt.plan_state_dir(root, SLUG) / "worktree.json"
    return {
        "neighbour_plan_schema_version": 6,
        "neighbour_isolation_enabled": enabled,
        "neighbour_isolation_reason": reason,
        "commit": sha,
        "swept_v7_record_paths": [p for p in staged if p.startswith(f"_plans/{SLUG}/")],
        "swept_runtime_paths": [p for p in tracked if "plan-state" in p],
        "runtime_claim_path": str(claim),
        "runtime_claim_inside_git_dir": ".git" in claim.parts,
        "runtime_claim_survived": claim.is_file(),
        "decision_inputs_after_sweep": {
            "worktree": ps.plan_worktree(plan_dir),
            "branch": ps.plan_branch(plan_dir),
            "base_ref": ps.pinned_base(plan_dir),
        },
    }


def _write_session_work(tree, plan_dir):
    """What the dispatched agent would produce: code in the WORKTREE, record in
    the OUTER plan directory, plus one deliberate mistake — a write into the
    worktree's FROZEN `_plans/` copy, which must never be staged and must never
    satisfy the evidence gate."""
    (tree / "src" / "feature.py").write_text("FEATURE = True\n")
    (tree / "docs").mkdir(exist_ok=True)
    (tree / "docs" / "note.md").write_text("# note\n")
    (plan_dir / "_evidence" / SESSION).mkdir(parents=True, exist_ok=True)
    (plan_dir / "_evidence" / SESSION / "proof.txt").write_text("engaged\n")
    (tree / "_plans" / SLUG / "PLAN.html").write_text(
        "<html>STALE — written inside the worktree</html>\n")


def _problems(report, before_main):
    """Everything that must be TRUE for this artifact to mean anything. A ship
    that did nothing, or one that moved the primary checkout, fails here rather
    than being written out green.

    A table rather than a chain of ``if``s: each row is one claim the artifact
    makes, so adding a claim cannot quietly skip the ones around it.
    """
    files = report["plan_branch_commit_files"]
    main, split = report["outer_main"], report["evidence_path_split"]
    sweep = report["v6_neighbour_sweep"]
    nxt, van = report["review_base"]["next_session"], report["vanished_worktree"]
    td = report["post_teardown"]
    checks = [
        (report["ship_final_action"] == "done",
         f"shipping did not finish: {report['ship_trail'][-1]!r}"),
        (bool(files), "the plan branch commit changed no files"),
        ("src/feature.py" in files, "declared file src/feature.py is not in the commit"),
        ("docs/note.md" in files, "declared file docs/note.md is not in the commit"),
        (not report["plans_paths_since_base"],
         f"_plans/ paths rode the commit: {report['plans_paths_since_base']}"),
        (main["tree_hash_equal"],
         "the primary checkout's tree MOVED during an isolated ship"),
        (before_main == main["commit_after"],
         "the primary checkout's `main` MOVED during an isolated ship"),
        (report["origin"]["plan_branch_matches_local"],
         "the plan branch was not published to origin"),
        (report["dispatch"]["prompt_names_worktree"],
         "the dispatched prompt does not name the plan worktree"),
        (split["_evidence/s01/proof.txt"].startswith(report["plan_dir"]),
         "an `_evidence/` path did not resolve in the OUTER plan directory"),
        (split["docs/note.md"].startswith(report["worktree"]),
         "a repo-relative path did not resolve in the plan worktree"),
        (split["_evidence/s01/proof.txt"] != split["decoy_in_worktree"],
         "the worktree-relative DECOY was accepted as the plan's evidence"),
        (split[f"_plans/{SLUG}/_evidence/{SESSION}/proof.txt"].startswith(
            report["plan_dir"]),
         "the repo-relative spelling of the RECORD did not resolve in the OUTER "
         "plan directory"),
        (split[f"_plans/{SLUG}/_evidence/{SESSION}/proof.txt"]
         != split["decoy_in_frozen_plans_copy"],
         "the FROZEN `_plans/` copy inside the worktree was accepted as the record"),
        (nxt["base"] == report["plan_branch_commit"],
         f"the next session's review base is {nxt['base']}, not this ship's commit "
         f"{report['plan_branch_commit']} — its surface would re-review this one"),
        (nxt["base"] != nxt["pinned"],
         "the next session's review base is the branch CUT POINT: the review "
         "surface grows with every session"),
        (not ({"src/feature.py", "docs/note.md"} & set(nxt["surface"] or [])),
         f"the next session's surface re-reviews THIS session's work: {nxt['surface']}"),
        (van["compute_steps_reason"] == "plan-worktree-missing",
         f"a vanished plan worktree did not halt shipping: {van['compute_steps_reason']}"),
        (not van["ship_status_raised"],
         f"ship_status raised on a vanished worktree: {van['ship_status_raised']}"),
        (van["ship_record_mid_ship"] == {"action": "failed",
                                         "reason": "plan-worktree-missing"},
         f"mid-ship ship_record on a vanished worktree did not halt cleanly: "
         f"{van['ship_record_mid_ship']}"),
        (not van["leases_held_after_mid_ship_halt"],
         f"the mid-ship halt left leases held: {van['leases_held_after_mid_ship_halt']}"),
        (td["refused_teardown_status"] == "preserved" and td["claim_live_while_preserved"],
         "a REFUSED teardown did not keep the claim live — the degrade would "
         "key on the stamp merely existing"),
        (td["forced_teardown_status"] == "removed" and td["claim_after"] == {}
         and td["plan_worktree_after"] is None
         and not td["require_live_refusal_after"] and td["preamble_after"] == "",
         f"a SUCCESSFUL teardown did not degrade to the pre-isolation path: {td}"),
        (not sweep["swept_runtime_paths"],
         "a v6 neighbour swept RUNTIME state into a commit"),
        (bool(sweep["swept_v7_record_paths"]),
         "the v6 neighbour sweep reached nothing — it proves nothing"),
    ]
    return [msg for ok, msg in checks if not ok]


RESIDUAL_GAPS = [
    "`_plans/<slug>/_shipping_state/` is a DECISION INPUT (ship-begin reads it for "
    "idempotency) that still lives inside the working tree, so a v6 neighbour's "
    "`git add -A` commits it — visible in swept_v7_record_paths. It is copied INTO a "
    "commit, never rewritten, so the plan's own reads are unaffected; §8.e would still "
    "place it under $GIT_COMMON_DIR/plan-state/. NAMED, not covered: relocating it is a "
    "durable-state migration this session did not take.",
]


def _measure(tmp):
    """Run the whole thing once and hand back every raw reading it produced.

    Kept separate from ``_report`` so that WHAT WAS MEASURED and HOW IT IS
    PRESENTED cannot drift into one another — and so neither function is a
    hundred lines the reviewer has to hold in their head at once.
    """
    root, origin, plan_dir = _build_repo(tmp)
    member = _begin(plan_dir)["batch"][0]
    state = pwt.load_state(plan_dir)
    tree, branch = Path(state["path"]), state["branch"]
    _write_session_work(tree, plan_dir)

    m = {"root": root, "origin": origin, "plan_dir": plan_dir, "member": member,
         "state": state, "tree": tree, "branch": branch,
         "before_main": git(["rev-parse", "main"], root, check=True)[1],
         "before_tree": git(["rev-parse", "HEAD^{tree}"], root, check=True)[1],
         "dirty": git(["status", "--porcelain"], tree, check=True)[1].splitlines()}

    cp.persist(plan_dir, SESSION, {"session": SESSION, "result": "DONE",
                                   "items_completed": ["i1"], "items_blocked": [],
                                   "notes": {}})
    m["final"], m["trail"] = _ship(plan_dir)
    _plant_decoy(tree)

    head = _rev(root, branch)
    m["head"] = head
    m["changed"] = git(["show", "--name-only", "--format=", head],
                       root, check=True)[1].split() if head else []
    m["after_main"] = git(["rev-parse", "main"], root, check=True)[1]
    m["after_tree"] = git(["rev-parse", "HEAD^{tree}"], root, check=True)[1]
    m["tracked_dirt_after"] = git(["status", "--porcelain", "-uno"],
                                  root, check=True)[1].splitlines()
    m["remote"] = _rev(origin, f"refs/heads/{branch}")
    m["remote_main"] = _rev(origin, "refs/heads/main")
    m["plans_paths"] = pship.plans_paths(tree, state["base_ref"])
    m["failures"] = (ssio.load_ship_state(plan_dir, SESSION) or {}).get("failures")
    m["sweep"] = _v6_neighbour_sweep(root, plan_dir)
    m["next_base"] = _next_session_base(plan_dir, tree)
    m["vanished"] = _vanished_worktree_halt(plan_dir, tree)   # moves the tree aside
    return m


def _next_session_base(plan_dir, tree):
    """The bound the NEXT session's review gate would get, measured against the
    commit this ship actually made.

    Under isolation every session commits to the same plan branch, so a base that
    is the branch's cut point makes session sNN's gate re-review every earlier
    session's commits and the surface grows with each session — the shape that
    cost this repo 16 failed gate rounds in 21 hours (2026-08-20). A
    `dispatch_started` for a second session is appended (which is exactly what
    `begin` writes) and the base is read back through the REAL resolver at the
    REAL gate cwd.
    """
    nxt = "s02"
    with (Path(plan_dir) / "run.ndjson").open("a") as fh:
        fh.write(json.dumps({"ts": subprocess.run(
            ["date", "-u", "+%Y-%m-%dT%H:%M:%SZ"], capture_output=True, text=True,
            check=True).stdout.strip(), "event": "dispatch_started",
            "session_ids": [nxt]}) + "\n")
    gate_cwd = vfy.gate_cwd(plan_dir, nxt, {"kind": "skill", "skill": "x"})
    base = rvs.get_base(plan_dir, nxt, gate_cwd)
    return {"session": nxt, "gate_cwd": gate_cwd, "base": base,
            "pinned": ps.pinned_base(plan_dir),
            "surface": git(["diff", "--name-only", base], tree, check=True)[1].split()
            if base else None}


def _resolved(plan_dir, raw):
    return str(ps.resolve_evidence_path(plan_dir, SESSION, raw).resolve())


def _report(m):
    """The artifact, assembled from `_measure`'s readings. No git calls here."""
    plan_dir, tree, branch = m["plan_dir"], m["tree"], m["branch"]
    prompt = m["member"]["prompt_text"]
    return {
        "measured_at": subprocess.run(["date", "-u", "+%Y-%m-%dT%H:%M:%SZ"],
                                      capture_output=True, text=True,
                                      check=True).stdout.strip(),
        "fixture_root": str(m["root"]),
        "plan_dir": str(Path(plan_dir).resolve()),
        "worktree": str(Path(tree).resolve()),
        "dispatch": {
            "session": m["member"]["id"],
            "worktree": m["member"].get("worktree"),
            "worktree_branch": m["member"].get("worktree_branch"),
            "isolation": m["member"].get("isolation"),
            "prompt_names_worktree": str(tree) in prompt,
            "prompt_names_branch": branch in prompt,
            "prompt_names_outer_plan_dir": str(Path(plan_dir).resolve()) in prompt,
        },
        "worktree_dirty_before_ship": m["dirty"],
        "ship_trail": m["trail"],
        "ship_final_action": m["final"].get("action"),
        "ship_failures": m["failures"],
        "plan_branch": branch,
        "plan_branch_commit": m["head"],
        "plan_branch_commit_files": m["changed"],
        "plans_paths_since_base": m["plans_paths"],
        "outer_main": {
            "commit_before": m["before_main"], "commit_after": m["after_main"],
            "tree_hash_before": m["before_tree"], "tree_hash_after": m["after_tree"],
            "tree_hash_equal": m["before_tree"] == m["after_tree"],
            "tracked_dirt_after": m["tracked_dirt_after"],
        },
        "origin": {"plan_branch": m["remote"],
                   "plan_branch_matches_local": m["remote"] == m["head"],
                   "main": m["remote_main"], "main_unmoved": m["remote_main"] == m["after_main"]},
        "evidence_path_split": {
            "_evidence/s01/proof.txt": _resolved(plan_dir, "_evidence/s01/proof.txt"),
            "docs/note.md": _resolved(plan_dir, "docs/note.md"),
            f"_plans/{SLUG}/_evidence/{SESSION}/proof.txt": _resolved(
                plan_dir, f"_plans/{SLUG}/_evidence/{SESSION}/proof.txt"),
            "decoy_in_worktree": str((Path(tree) / "_evidence" / SESSION
                                      / "proof.txt").resolve()),
            "decoy_in_frozen_plans_copy": str((Path(tree) / "_plans" / SLUG
                                               / "_evidence" / SESSION
                                               / "proof.txt").resolve()),
            "frozen_copy_rejected": _resolved(plan_dir, "_evidence/s01/proof.txt") != str(
                (tree / "_plans" / SLUG / "_evidence" / SESSION / "proof.txt").resolve()),
        },
        "review_base": {
            "pinned": ps.pinned_base(plan_dir),
            "gate_cwd": vfy.gate_cwd(plan_dir, SESSION, {"kind": "skill", "skill": "x"}),
            "get_base": rvs.get_base(plan_dir, SESSION, str(tree)),
            "derived_from_run_ndjson": rvs.derive_base(plan_dir, SESSION, str(tree)),
            "next_session": m["next_base"],
        },
        "vanished_worktree": m["vanished"],
        "v6_neighbour_sweep": m["sweep"],
        "residual_gaps": RESIDUAL_GAPS,
    }


def run(out_path):
    tmp = Path(tempfile.mkdtemp(prefix="iso02-evidence-"))
    try:
        m = _measure(tmp)
        report = _report(m)
        # LAST — removes the worktree for real; every reading above is taken.
        report["post_teardown"] = _post_teardown_degrade(m["plan_dir"])
        problems = _problems(report, m["before_main"])
        report["problems"] = problems
        report["verdict"] = "PASS" if not problems else "FAIL"
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps({"verdict": report["verdict"], "out": str(out_path),
                          "problems": problems}, indent=2))
        return 0 if not problems else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(f"usage: {Path(__file__).name} <output.json>")
    sys.exit(run(sys.argv[1]))
