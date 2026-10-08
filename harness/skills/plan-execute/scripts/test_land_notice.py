"""LND-01 (finish-every-plan) — land stops leaving its own files modified.

Authority: ``../references/finish-contract.md`` "Decisions taken in s01 (a)" and
revision R4 of ``plan-isolation-contract.md``. ``LAND_NOTICE.txt`` and
``_plans/<slug>/_worktrees/`` are LOCAL RUNTIME: ``record_plan`` never adds,
changes or deletes them, by name, whatever the target's ``.gitignore`` says.
``land.json`` also carries ``landed_base``, the origin tip the pushed merge was
built on, which finish reads instead of the later-overwritten ``expected``.

    pytest skills/plan-execute/scripts/test_land_notice.py -q
"""

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import land_state as lst  # noqa: E402
import plan_record as precord  # noqa: E402
from _land_fixture import (_arm_prepush, build_repo, isolate,  # noqa: E402
                           run_cli, work)
from worktree import git  # noqa: E402

SLUG = "plan-a"
NOTICE = f"_plans/{SLUG}/LAND_NOTICE.txt"
G1 = f"_plans/{SLUG}/_worktrees/g1.json"
G2 = f"_plans/{SLUG}/_worktrees/g2.json"
# plan-builder's ignore lines as they stood BEFORE R4 — the shape origin's tree
# has in a repo whose new lines were never committed. The runtime files must stay
# out of the record by NAME, not because an ignore line happens to cover them.
OLD_IGNORE = "".join(f"_plans/*/{p}\n" for p in
                     (".lock", "_closeouts/", "run.ndjson", "run_state.json",
                      "HALT_NOTICE.txt"))


def _blob(cwd, rev, path):
    rc, out, _ = git(["rev-parse", f"{rev}:{path}"], cwd)
    return out if rc == 0 else None


# ------------------------------------------- (a) the contract's tracked-blob test
@pytest.mark.parametrize("modified,deleted", [(NOTICE, G1), (G1, NOTICE)])
def test_record_never_adds_changes_or_deletes_a_runtime_local_path(tmp_path, modified,
                                                                   deleted):
    root = tmp_path / "proj"
    plan_dir = root / "_plans" / SLUG
    (plan_dir / "_worktrees").mkdir(parents=True)
    (plan_dir / "PLAN.html").write_text("<html>base</html>\n")
    (root / NOTICE).write_text("base notice\n")
    (root / G1).write_text('{"group": "g1", "v": "base"}\n')
    git(["init", "-q", "-b", "main", "."], root, check=True)
    git(["config", "user.email", "t@example.com"], root, check=True)
    git(["config", "user.name", "T"], root, check=True)
    git(["add", "-A"], root, check=True)
    git(["commit", "-q", "-m", "base"], root, check=True)
    base = git(["rev-parse", "HEAD"], root, check=True)[1]
    target = tmp_path / "land"
    git(["worktree", "add", "-q", "--detach", str(target), base], root, check=True)

    # Live copies: one runtime file modified, one deleted, one untracked added,
    # and a real record change so the call commits instead of no-op'ing.
    (root / modified).write_text("LIVE bytes the record must not carry\n")
    (root / deleted).unlink()
    (root / G2).write_text('{"group": "g2"}\n')
    (plan_dir / "PLAN.html").write_text("<html>live</html>\n")

    res = precord.record_plan(target, plan_dir)
    assert res["status"] == "recorded", res
    record = res["sha"]
    changed = git(["diff", "--name-status", base, record], target, check=True)[1]
    assert changed.split() == ["M", f"_plans/{SLUG}/PLAN.html"], changed
    for path in (NOTICE, G1):
        assert _blob(target, record, path) == _blob(root, base, path) is not None
    assert _blob(target, record, G2) is None
    # The restore left the land worktree clean — §12.4's teardown refuses a dirty one.
    assert git(["status", "--porcelain", "-uall"], target, check=True)[1] == ""
    # The live copies in the owner are untouched: runtime is never deleted.
    assert (root / modified).read_text().startswith("LIVE")
    assert (root / G2).is_file() and not (root / deleted).exists()


# ---------------------------- (b) after a fixture land the primary stays clean
def test_a_land_leaves_no_runtime_local_file_modified_in_the_primary(tmp_path):
    fx = build_repo(tmp_path)
    root = fx["root"]
    with (root / ".gitignore").open("a") as fh:
        fh.write(OLD_IGNORE)
    git(["commit", "-q", "-am", "plan-builder ignore lines, pre-R4"], root, check=True)
    git(["push", "-q", "origin", "main"], root, check=True)
    iso = isolate(fx, SLUG)
    plan_dir = iso["plan_dir"]
    (root / G1).parent.mkdir()
    (root / G1).write_text('{"note": "no group key, so cleanup skips it"}\n')
    work(iso["tree"], "src/f.py", "F = 1\n")
    assert run_cli("land", plan_dir)[1]["action"] == "land-awaits-review"
    assert run_cli("land-ack", plan_dir, "--note", "ok")[1]["action"] == "land-acked"
    rc, out = run_cli("land", plan_dir)
    assert (rc, out["action"]) == (0, "landed"), out

    git(["fetch", "-q", "origin"], root, check=True)
    tree = git(["ls-tree", "-r", "--name-only", "origin/main", "--", f"_plans/{SLUG}"],
               root, check=True)[1].split()
    assert f"_plans/{SLUG}/PLAN.html" in tree                    # the record did land
    assert NOTICE not in tree and not [p for p in tree if "/_worktrees/" in p], tree

    # Catch the primary up the way finish does: an untracked record file that is
    # byte-identical to origin's copy is restored by the fast-forward itself.
    # Before R4 LAND_NOTICE.txt was in the record with OLDER bytes than the live
    # one, so it blocked this fast-forward or, once tracked, showed as modified.
    for entry in git(["status", "--porcelain", "-uall", "-z", "--", f"_plans/{SLUG}"],
                     root, strip=False)[1].split("\0"):
        path = entry[3:]
        if entry.startswith("?? ") and path in tree:
            shown = git(["show", f"origin/main:{path}"], root, strip=False)[1]
            if shown == (root / path).read_text():
                (root / path).unlink()
    rc, _, err = git(["merge", "-q", "--ff-only", "origin/main"], root)
    assert rc == 0, err
    status = git(["status", "--porcelain", "-uall", "--", f"_plans/{SLUG}"], root,
                 strip=False)[1].splitlines()
    assert not [s for s in status if not s.startswith("?? ")], status
    assert f"?? {NOTICE}" in status and f"?? {G1}" in status      # kept, never lost


# ------------------------- (c) landed_base = the origin tip the merge was built on
def test_landed_base_is_the_origin_tip_the_pushed_merge_was_built_on(tmp_path):
    fx = build_repo(tmp_path)
    iso = isolate(fx, SLUG)
    work(iso["tree"], "src/f.py", "F = 1\n")
    _arm_prepush(fx, times=1)                 # one competing lander forces a re-sync
    assert run_cli("land", iso["plan_dir"])[1]["action"] == "land-awaits-review"
    assert run_cli("land-ack", iso["plan_dir"], "--note", "ok")[1]["action"] == "land-acked"
    rc, out = run_cli("land", iso["plan_dir"])
    assert (rc, out["action"]) == (0, "landed"), out
    st = lst.load(iso["plan_dir"])
    raced = git(["log", "--format=%H", "--grep=^race 0$", "main"], fx["origin"],
                check=True)[1]
    assert raced and st["landed_base"] == raced
    assert st["ack"]["resyncs"][-1]["main_head"] == raced
    # KNOWN POSITIVES — the pre-race tip and the later-overwritten `expected` both
    # differ, so a field copied from either would fail here.
    assert st["push_attempts"][0]["expected"] != raced
    assert st["expected"] != raced
    assert git(["merge-base", "--is-ancestor", raced, st["landed_sha"]],
               fx["root"])[0] == 0
