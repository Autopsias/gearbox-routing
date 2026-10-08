"""The review gate's surface is BOUNDED — by the session's base and its scope.

Why these exist, measured 2026-08-20 over 21h and two plans in one checkout:
16 of 17 verify failures were `llm-review-medium`, and the rework loop never
converged because the surface was the whole working tree and verify runs BEFORE
a session commits. Every round re-reviewed the session's entire accumulated
output with a fresh model sample, and a concurrent session's files came with it.
"""
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import review_context as rvs  # noqa: E402


def _plan(tmp_path, events, sessions=()):
    d = tmp_path / "plan"
    (d / "sessions").mkdir(parents=True)
    (d / "run.ndjson").write_text("".join(json.dumps(e) + "\n" for e in events))
    (d / "run_state.json").write_text(json.dumps({"schema_version": 1}))
    if sessions:                           # only the land roll-up needs a manifest
        (d / "manifest.json").write_text(json.dumps(
            {"plan_schema_version": 2, "sessions": [{"id": s} for s in sessions]}))
    return d


def _repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    def run(*a):
        return subprocess.run(list(a), cwd=r, check=True, capture_output=True)
    run("git", "init", "-q")
    run("git", "config", "user.email", "t@t")
    run("git", "config", "user.name", "t")
    return r, run


def _commit(run, r, name, when):
    (r / name).write_text(name)
    run("git", "add", "-A")
    env = {"GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when}
    subprocess.run(["git", "commit", "-qm", name], cwd=r, check=True,
                   capture_output=True, env={**__import__("os").environ, **env})
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=r, check=True,
                          capture_output=True, text=True).stdout.strip()


# ---------- the base is the commit that was HEAD at the FIRST dispatch ----------

def test_base_is_the_commit_that_was_head_when_the_session_was_dispatched(tmp_path):
    r, run = _repo(tmp_path)
    early = _commit(run, r, "a.txt", "2026-01-01T10:00:00+00:00")
    _commit(run, r, "b.txt", "2026-01-01T12:00:00+00:00")
    d = _plan(tmp_path, [{"event": "dispatch_started", "session_ids": ["s01"],
                          "ts": "2026-01-01T11:00:00+00:00"}])
    assert rvs.derive_base(str(d), "s01", str(r)) == early


def test_a_redispatch_keeps_the_FIRST_base(tmp_path):
    """The tree accumulates across a redispatch, so only the first base covers it."""
    r, run = _repo(tmp_path)
    early = _commit(run, r, "a.txt", "2026-01-01T10:00:00+00:00")
    _commit(run, r, "b.txt", "2026-01-01T12:00:00+00:00")
    d = _plan(tmp_path, [
        {"event": "dispatch_started", "session_ids": ["s01"], "ts": "2026-01-01T11:00:00+00:00"},
        {"event": "dispatch_started", "session_ids": ["s01"], "ts": "2026-01-01T13:00:00+00:00"},
    ])
    assert rvs.derive_base(str(d), "s01", str(r)) == early


def test_a_session_never_dispatched_has_no_base(tmp_path):
    r, _ = _repo(tmp_path)
    d = _plan(tmp_path, [{"event": "dispatch_started", "session_ids": ["s02"],
                          "ts": "2026-01-01T11:00:00+00:00"}])
    assert rvs.derive_base(str(d), "s01", str(r)) is None


def test_no_base_is_not_an_error_it_is_the_old_behaviour(tmp_path):
    """Every plan built before this returns None here and keeps `git diff HEAD`."""
    r, _ = _repo(tmp_path)
    d = _plan(tmp_path, [])
    assert rvs.get_base(str(d), "s01", str(r)) is None
    env = rvs.gate_env(str(d), "s01", str(r))
    assert rvs.BASE_ENV not in env and rvs.SCOPE_ENV not in env


def test_the_derived_base_is_cached_so_it_cannot_drift(tmp_path):
    r, run = _repo(tmp_path)
    early = _commit(run, r, "a.txt", "2026-01-01T10:00:00+00:00")
    d = _plan(tmp_path, [{"event": "dispatch_started", "session_ids": ["s01"],
                          "ts": "2026-01-01T11:00:00+00:00"}])
    assert rvs.get_base(str(d), "s01", str(r)) == early
    stored = json.loads((d / "run_state.json").read_text())
    assert stored["session_base"]["s01"]["base_ref"] == early
    # a later commit must NOT move a base already recorded
    _commit(run, r, "b.txt", "2026-01-01T12:00:00+00:00")
    assert rvs.get_base(str(d), "s01", str(r)) == early


# ---------- the scope comes from the session spec, and is optional ----------

def test_scope_is_read_from_the_session_spec():
    assert rvs.get_scope({"review_scope": ["skills/a", "./skills/b"]}) == ["skills/a", "skills/b"]
    assert rvs.get_scope({"review_scope": "skills/a"}) == ["skills/a"]


def test_a_session_with_no_scope_reviews_the_whole_tree():
    assert rvs.get_scope({}) == []
    assert rvs.get_scope({"review_scope": []}) == []
    assert rvs.get_scope(None) == []


def test_gate_env_omits_keys_it_has_no_value_for(tmp_path):
    """An unset var must be ABSENT, never an empty string the gate must decode."""
    r, run = _repo(tmp_path)
    _commit(run, r, "a.txt", "2026-01-01T10:00:00+00:00")
    d = _plan(tmp_path, [{"event": "dispatch_started", "session_ids": ["s01"],
                          "ts": "2026-01-01T11:00:00+00:00"}])
    env = rvs.gate_env(str(d), "s01", str(r))
    # plan dir + session + harness always; base when derivable; scope only with a
    # manifest. HARNESS is in the always group because it ALWAYS has a value: a
    # session with no dispatch record reads "claude", the pre-WIRE-04 default, and
    # that is an answer rather than a missing one.
    assert set(env) == {rvs.BASE_ENV, rvs.PLAN_DIR_ENV, rvs.SESSION_ENV,
                        rvs.HARNESS_ENV}, env
    assert env[rvs.BASE_ENV] and env[rvs.SESSION_ENV] == "s01"
    assert env[rvs.HARNESS_ENV] == "claude"


def test_gate_env_hands_the_gate_an_ABSOLUTE_plan_dir(tmp_path, monkeypatch):
    """The gate runs with cwd = a member worktree. A relative plan dir there names
    the worktree's FROZEN `_plans/` copy, so the findings ledger was written (and
    then committed) inside the worktree instead of the orchestrator's plan dir."""
    r, _ = _repo(tmp_path)
    d = _plan(tmp_path, [])
    monkeypatch.chdir(d.parent)
    env = rvs.gate_env(d.name, "s01", str(r))
    assert env[rvs.PLAN_DIR_ENV] == str(d.resolve())


def test_gate_env_reads_the_harness_off_the_session_dispatch_record(tmp_path):
    """WIRE-04 — the source is the session's OWN durable dispatch record.

    Not a runner argument: `verify.declared_env` calls `gate_env` without a
    harness and no such parameter exists. THE LAST pair wins, so a session
    re-dispatched on the other harness (a rework, or a harness change mid-plan)
    reads the harness it MOST RECENTLY ran under — keying off the mere presence
    of a codex record pinned the lane to codex forever."""
    r, run = _repo(tmp_path)
    _commit(run, r, "a.txt", "2026-01-01T10:00:00+00:00")
    events = [
        {"event": "dispatch_started", "session_ids": ["s01"],
         "ts": "2026-01-01T11:00:00+00:00", "harness": "codex"},
        {"event": "dispatch_families", "session_ids": ["s01"],
         "ts": "2026-01-01T11:00:01+00:00", "families": {"s01": "openai"}},
    ]
    d = _plan(tmp_path, events)
    assert rvs.harness(str(d), "s01") == "codex"
    assert rvs.gate_env(str(d), "s01", str(r))[rvs.HARNESS_ENV] == "codex"
    # A LATER Claude dispatch moves it back.
    d2 = _plan(tmp_path / "again", events + [
        {"event": "dispatch_started", "session_ids": ["s01"],
         "ts": "2026-01-01T12:00:00+00:00"},
        {"event": "dispatch_families", "session_ids": ["s01"],
         "ts": "2026-01-01T12:00:01+00:00", "families": {"s01": "anthropic"}},
    ])
    assert rvs.harness(str(d2), "s01") == "claude"
    # The dial can send ONE session to Codex under the Claude harness; that
    # session's code is still Codex-built.
    d3 = _plan(tmp_path / "dial", [
        {"event": "dispatch_started", "session_ids": ["s01", "s02"],
         "ts": "2026-01-01T11:00:00+00:00"},
        {"event": "dispatch_families", "session_ids": ["s01", "s02"],
         "ts": "2026-01-01T11:00:01+00:00",
         "families": {"s01": "openai", "s02": "anthropic"}},
    ])
    assert rvs.harness(str(d3), "s01") == "codex"
    assert rvs.harness(str(d3), "s02") == "claude"
    # No dispatch record at all: the pre-WIRE-04 default, never a crash.
    assert rvs.harness(str(tmp_path / "nope"), "s01") == "claude"


def test_the_land_re_gate_reads_codex_when_ANY_session_was_codex_built(tmp_path):
    """WIRE-04 — the LAND scope is the plan's MERGED tree, not a session.

    `land_gate._run_argv_gate` calls `declared_env(g, plan_dir, "land", cwd)`, and
    no session is ever CALLED "land": the per-session lookup found no record and
    fell through to "claude", so the re-gate ran `--harness claude`, `refusal()`
    returned None, and the on-box `claude -p` reviewer read a Codex-built plan's
    whole tree — at the one moment that tree is everything the plan will push."""
    r, run = _repo(tmp_path)
    _commit(run, r, "a.txt", "2026-01-01T10:00:00+00:00")
    gate = {"env_allowlist": [rvs.HARNESS_ENV]}          # as the six review gates declare it
    mixed = [
        {"event": "dispatch_started", "session_ids": ["s01", "s02"],
         "ts": "2026-01-01T11:00:00+00:00"},
        {"event": "dispatch_families", "session_ids": ["s01", "s02"],
         "ts": "2026-01-01T11:00:01+00:00",
         "families": {"s01": "openai", "s02": "anthropic"}},
    ]
    d = _plan(tmp_path / "mixed", mixed, sessions=["s01", "s02"])
    assert rvs.declared_env(gate, str(d), "land", str(r))[rvs.HARNESS_ENV] == "codex"
    assert rvs.harness(str(d), "s01") == "codex"         # per session, unchanged
    assert rvs.harness(str(d), "s02") == "claude"
    # A plan with no Codex-built session anywhere still reads claude.
    d2 = _plan(tmp_path / "pure", mixed[:1], sessions=["s01", "s02"])
    assert rvs.declared_env(gate, str(d2), "land", str(r))[rvs.HARNESS_ENV] == "claude"
    # ANY-of-LAST, never any-record-anywhere: s01 re-dispatched onto the Claude
    # harness stops counting, and nothing else in the plan is Codex-built.
    d3 = _plan(tmp_path / "moved", mixed + [
        {"event": "dispatch_started", "session_ids": ["s01"],
         "ts": "2026-01-01T12:00:00+00:00"},
        {"event": "dispatch_families", "session_ids": ["s01"],
         "ts": "2026-01-01T12:00:01+00:00", "families": {"s01": "anthropic"}},
    ], sessions=["s01", "s02"])
    assert rvs.declared_env(gate, str(d3), "land", str(r))[rvs.HARNESS_ENV] == "claude"


# ---------- and it must actually REACH the gate process ----------

def test_env_extra_reaches_the_gate_subprocess():
    """The whole chain is worthless if the var never crosses the process line.

    `build_allowlisted_env` deliberately does NOT inherit the ambient
    environment, so a value the orchestrator derived cannot arrive by being
    exported — it has to be handed over explicitly. This asserts the handover,
    not the function that computes it.
    """
    import ship_state_io as ssio
    res = ssio.run_deploy_argv(
        ["/bin/sh", "-c", 'printf "%s|%s" "$PLAN_EXECUTE_REVIEW_BASE" "$PLAN_EXECUTE_REVIEW_SCOPE"'],
        cwd=".", env_allowlist=[], timeout=30,
        env_extra={"PLAN_EXECUTE_REVIEW_BASE": "abc123",
                   "PLAN_EXECUTE_REVIEW_SCOPE": "skills/x,skills/y"})
    assert res["returncode"] == 0, res
    assert res["stdout"] == "abc123|skills/x,skills/y", res["stdout"]


def test_env_extra_absent_leaves_the_gate_with_nothing_to_decode():
    import ship_state_io as ssio
    res = ssio.run_deploy_argv(
        ["/bin/sh", "-c", 'printf "[%s]" "${PLAN_EXECUTE_REVIEW_BASE-UNSET}"'],
        cwd=".", env_allowlist=[], timeout=30)
    assert res["stdout"] == "[UNSET]", res["stdout"]


def test_scope_keeps_a_root_dotfile():
    assert rvs.get_scope({"review_scope": [".file-size-exceptions", "./skills/a"]}) == [".file-size-exceptions", "skills/a"]


# ---------- verify hands a gate ONLY the env keys that gate declares ----------

def test_verify_passes_computed_env_only_to_gates_that_declare_it(tmp_path, monkeypatch):
    computed = {"PLAN_EXECUTE_REVIEW_BASE": "abc", "PLAN_EXECUTE_REVIEW_SCOPE": "x",
                "PLAN_EXECUTE_PLAN_DIR": "p", "PLAN_EXECUTE_SESSION": "s01"}
    monkeypatch.setattr(rvs, "gate_env", lambda *a, **k: dict(computed))
    deterministic = {"argv": ["bash", "gate.sh"]}                       # no allowlist
    assert rvs.declared_env(deterministic, "p", "s01", ".") == {}
    review = {"argv": ["python3", "llm_review_gate.py"],
              "env_allowlist": ["PLAN_EXECUTE_INTENT_FILE", "PLAN_EXECUTE_REVIEW_BASE",
                                "PLAN_EXECUTE_PLAN_DIR"]}
    assert rvs.declared_env(review, "p", "s01", ".") == {
        "PLAN_EXECUTE_REVIEW_BASE": "abc", "PLAN_EXECUTE_PLAN_DIR": "p"}


def test_tests_never_see_an_ambient_plan_execute_var():
    """The conftest scrub: a gate's pytest children must see only what they set."""
    import os
    leaked = [k for k in ("PLAN_EXECUTE_REVIEW_BASE", "PLAN_EXECUTE_REVIEW_SCOPE",
                          "PLAN_EXECUTE_PLAN_DIR", "PLAN_EXECUTE_SESSION",
                          "PLAN_EXECUTE_INTENT_FILE") if k in os.environ]
    assert not leaked, leaked


# ---------- main merged in after dispatch is folded into the base ----------

def _fork(tmp_path):
    """plan branch at `base`, `main` one commit ahead, published as origin/main."""
    r, run = _repo(tmp_path)
    _commit(run, r, "root.txt", "2026-01-01T00:00:00")
    run("git", "branch", "-M", "plan")
    base = _commit(run, r, "plan_before.txt", "2026-01-01T00:01:00")
    run("git", "checkout", "-q", "-b", "main", "HEAD~1")
    _commit(run, r, "main_only.txt", "2026-01-01T00:02:00")
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True, capture_output=True)
    run("git", "remote", "add", "origin", str(remote))
    run("git", "push", "-q", "origin", "main")
    run("git", "fetch", "-q", "origin")
    run("git", "checkout", "-q", "plan")
    return r, run, base


def _surface(r, base):
    return set(subprocess.run(["git", "diff", "--name-only", base], cwd=r, check=True,
                              capture_output=True, text=True).stdout.split())


def test_a_main_merge_after_dispatch_drops_out_of_the_surface(tmp_path):
    r, run, base = _fork(tmp_path)
    run("git", "merge", "-q", "--no-edit", "origin/main")
    _commit(run, r, "session_work.txt", "2026-01-01T00:03:00")
    assert _surface(r, base) == {"main_only.txt", "session_work.txt"}  # the old, wide surface
    assert _surface(r, rvs.net_of_default_merges(base, r, "main")) == {"session_work.txt"}


def test_no_main_merge_leaves_the_base_alone(tmp_path):
    r, run, base = _fork(tmp_path)
    _commit(run, r, "session_work.txt", "2026-01-01T00:03:00")
    assert rvs.net_of_default_merges(base, r, "main") == base
    assert rvs.net_of_default_merges(None, r, "main") is None
    assert rvs.net_of_default_merges(base, r, "no-such-branch") == base


def test_a_conflicting_merge_keeps_the_wide_surface(tmp_path):
    r, run, base = _fork(tmp_path)
    (r / "root.txt").write_text("plan side")
    run("git", "commit", "-qam", "plan edit")
    plan_tip = subprocess.run(["git", "rev-parse", "HEAD"], cwd=r, check=True,
                              capture_output=True, text=True).stdout.strip()
    run("git", "checkout", "-q", "main")
    (r / "root.txt").write_text("main side")
    run("git", "commit", "-qam", "main edit")
    run("git", "push", "-q", "origin", "main")
    run("git", "fetch", "-q", "origin")
    run("git", "checkout", "-q", "plan")
    run("git", "merge", "-q", "-s", "ours", "--no-edit", "origin/main")  # resolved by hand
    assert rvs.net_of_default_merges(plan_tip, r, "main") == plan_tip


def test_a_local_origin_ref_moved_by_the_session_folds_nothing(tmp_path):
    r, run, base = _fork(tmp_path)
    _commit(run, r, "session_work.txt", "2026-01-01T00:03:00")
    run("git", "update-ref", "refs/remotes/origin/main", "HEAD")  # the session hides its own work
    assert rvs.net_of_default_merges(base, r, "main") == base
    assert "session_work.txt" in _surface(r, rvs.net_of_default_merges(base, r, "main"))
