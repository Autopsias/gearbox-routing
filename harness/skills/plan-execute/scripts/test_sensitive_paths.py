"""sensitive_paths: a change touching a sensitive path raises the review level."""
import json
import subprocess

import sensitive_paths as sp


def test_hooks_hit_swaps_low_for_medium_and_adds_adversarial():
    out = sp.raise_gates(["hooks/dispatch-audit.py"], ["code-review-gate", "llm-review-low"])
    assert out == ["code-review-gate", "llm-review-medium", "adversarial-review"]


def test_cross_family_low_hit_swaps_to_medium():
    out = sp.raise_gates([".github/workflows/verify.yml"], ["cross-family-review-low"])
    assert out == ["cross-family-review-medium", "adversarial-review"]


def test_canary_ids_swap_to_their_medium_canary():
    out = sp.raise_gates(["settings.json"], ["llm-review-low-canary"])
    assert out == ["llm-review-medium-canary", "adversarial-review"]


def test_every_listed_path_is_a_hit():
    for p in ("hooks/a/b.py", "settings.json", "githooks/pre-commit",
              "scripts/gearbox", "scripts/gearbox_deploy.py", "model-routing.yaml",
              ".github/workflows/ci.yml"):
        assert sp.hits([p]) == [p], p


def test_path_outside_the_list_leaves_ids_unchanged():
    ids = ["code-review-gate", "llm-review-low"]
    assert sp.raise_gates(["skills/foo/hooks/x.py", "skills/foo/settings.json"], ids) == ids


def test_empty_path_list_leaves_ids_unchanged():
    ids = ["llm-review-low"]
    assert sp.raise_gates([], ids) == ids
    assert sp.raise_gates(["hooks/x.py"], ids, patterns=()) == ids


def test_unreadable_paths_fail_closed():
    """None = the file set could not be read: raised, never left at low."""
    assert sp.raise_gates(None, ["llm-review-low"]) == ["llm-review-medium", "adversarial-review"]


def test_effective_verify_unreadable_paths_synthesize_the_block(monkeypatch):
    monkeypatch.setattr(sp, "session_paths", lambda *_: None)
    assert sp.effective_verify("p", "s01", None) == {"gates": ["adversarial-review"],
                                                     "max_rework": 1}


def test_effective_verify_empty_paths_leave_vb_alone(monkeypatch):
    monkeypatch.setattr(sp, "session_paths", lambda *_: [])
    assert sp.effective_verify("p", "s01", None) is None


def test_no_duplicate_ids():
    out = sp.raise_gates(["hooks/x.py"],
                         ["llm-review-low", "llm-review-medium", "adversarial-review"])
    assert out == ["llm-review-medium", "adversarial-review"]


def test_session_already_at_medium_only_gains_adversarial():
    ids = ["code-review-gate", "llm-review-medium", "cross-family-review-medium"]
    assert sp.raise_gates(["hooks/x.py"], ids) == ids + ["adversarial-review"]


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def test_compute_gates_raises_a_session_whose_change_hits_hooks(tmp_path, monkeypatch):
    """End to end through verify.compute_gates: the real file set of a real tree."""
    import verify
    root = tmp_path / "repo"
    (root / "hooks").mkdir(parents=True)
    _git(root, "init", "-q")
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q",
         "--allow-empty", "-m", "base")
    # The bundled default registry has no adversarial-review; a project declares it.
    (root / ".claude").mkdir()
    (root / ".claude" / "eval-gates.json").write_text(json.dumps(
        {"adversarial-review": {"kind": "skill", "skill": "adversarial-review"}}))
    plan = root / "_plans" / "p"
    plan.mkdir(parents=True)
    manifest = {"sessions": [{"id": "s01", "verify": {"gates": ["llm-review-low"]}}]}
    (plan / "manifest.json").write_text(json.dumps(manifest))
    monkeypatch.chdir(root)

    def names():
        return [g["name"] for g in verify.compute_gates(str(plan), manifest, "s01")]

    assert names() == ["gate:llm-review-low"]
    (root / "hooks" / "new.py").write_text("x = 1\n")
    assert names() == ["gate:llm-review-medium", "gate:adversarial-review"]


def _no_verify_plan(tmp_path, monkeypatch):
    """A real git tree whose plan has one session with NO verify block."""
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q",
         "--allow-empty", "-m", "base")
    (root / ".claude").mkdir()
    (root / ".claude" / "eval-gates.json").write_text(json.dumps(
        {"adversarial-review": {"kind": "skill", "skill": "adversarial-review"}}))
    plan = root / "_plans" / "p"
    plan.mkdir(parents=True)
    manifest = {"sessions": [{"id": "s01"}]}
    (plan / "manifest.json").write_text(json.dumps(manifest))
    monkeypatch.chdir(root)
    return root, plan, manifest


def test_no_verify_session_hitting_hooks_gets_adversarial_review(tmp_path, monkeypatch):
    import verify
    root, plan, manifest = _no_verify_plan(tmp_path, monkeypatch)
    (root / "hooks").mkdir()
    (root / "hooks" / "new.py").write_text("x = 1\n")
    gates = verify.compute_gates(str(plan), manifest, "s01")
    assert [g["name"] for g in gates] == ["gate:adversarial-review"]


def test_no_verify_session_outside_the_list_gets_no_gates(tmp_path, monkeypatch):
    import verify
    root, plan, manifest = _no_verify_plan(tmp_path, monkeypatch)
    (root / "skills" / "foo").mkdir(parents=True)
    (root / "skills" / "foo" / "x.py").write_text("x = 1\n")
    assert verify.compute_gates(str(plan), manifest, "s01") == []


def test_apply_marks_a_no_verify_sensitive_session_verify_pending(tmp_path, capsys, monkeypatch):
    """Through run.cmd_apply: without the synthesized block the session would go
    straight to DONE and never enter the verify sub-loop."""
    import run
    from test_verify import SESS, _begin_doing, _status, make_plan
    plan_dir = make_plan(tmp_path, [SESS])
    _begin_doing(plan_dir, "s01")
    out_file = tmp_path / "co.txt"
    out_file.write_text(
        '<plan-execute-closeout>\n'
        '{"session":"s01","result":"DONE","items_completed":["it-1"],'
        '"items_blocked":[],"notes":{},"dispatch_next":true,'
        '"human_checkpoint_reason":null}\n'
        '</plan-execute-closeout>')
    monkeypatch.setattr(sp, "session_paths", lambda *_: ["hooks/x.py"])
    run.cmd_apply(str(plan_dir), "s01", str(out_file))
    printed = json.loads(capsys.readouterr().out)
    assert printed["verify_pending"] is True
    assert printed["verify"] == {"gates": ["adversarial-review"], "max_rework": 1}
    assert _status(plan_dir, "s01") == "DOING"


def test_git_tree_whose_file_set_fails_raises_through_compute_gates(tmp_path, monkeypatch):
    """Codex rework-2 finding: a valid worktree where changed_files returns None
    must fail closed, not fall through at the low level."""
    import gate_files
    import verify
    root, plan, manifest = _no_verify_plan(tmp_path, monkeypatch)
    manifest["sessions"][0]["verify"] = {"gates": ["llm-review-low"]}
    monkeypatch.setattr(gate_files, "changed_files", lambda *_a, **_k: None)
    assert sp.session_paths(str(plan), "s01") is None
    names = [g["name"] for g in verify.compute_gates(str(plan), manifest, "s01")]
    assert names == ["gate:llm-review-medium", "gate:adversarial-review"]


def test_tree_with_no_git_reads_as_empty(tmp_path, monkeypatch):
    import verify
    plan = tmp_path / "_plans" / "p"
    plan.mkdir(parents=True)
    manifest = {"sessions": [{"id": "s01"}]}
    (plan / "manifest.json").write_text(json.dumps(manifest))
    monkeypatch.chdir(tmp_path)
    assert sp.session_paths(str(plan), "s01") == []
    assert verify.compute_gates(str(plan), manifest, "s01") == []
