"""EXE-04 — route-at-dispatch end to end, on a v8 fixture plan with no models.

Authority: ../references/route-at-dispatch-contract.md. The unit suites prove each
part alone (test_route_at_dispatch.py §3-4, test_locked_checks.py §5,
test_usage_ledger.py §6). This file proves the parts MEET: one plan, built by this
tree's build_plan.py, driven by this tree's run.py as a separate process with the
same switches the real harnesses use —

  * anthropic: plain `begin` (the Claude tree);
  * zai:       CLAUDE_CONFIG_DIR ending in `.claude-glm` (the GLM tree);
  * openai:    `begin --harness codex` (the Codex orchestrator).

Everything happens in a throwaway git repository under tmp_path: plan isolation
cuts its branch and worktree THERE. Every command calls this worktree's scripts by
absolute path, with PLAN_EXECUTE_ROUTING_SSOT on a copy of this worktree's
model-routing.yaml, so a test that edits the routing file edits only the copy.
Expected cells are read from that copy through resolve_route.resolve — never
typed in. Nothing here calls a model or the network.

Run: pytest skills/plan-execute/scripts/test_route_at_dispatch_e2e.py -q
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent
TREE = SCRIPTS.parents[2]
RUN_PY = SCRIPTS / "run.py"
BUILD_PY = TREE / "skills" / "plan-builder" / "scripts" / "build_plan.py"
sys.path.insert(0, str(TREE / "scripts"))

import resolve_route as rr  # noqa: E402
import route_at_dispatch as rad  # noqa: E402
from _land_fixture import json_tail  # noqa: E402
from test_route_at_dispatch import _move_standard_build  # noqa: E402

LOCKED = "tests/test_locked.py"
SLUG = "e2e"
# A real `claude -p --output-format json` result, trimmed (the same shape
# test_usage_ledger.py cuts from evals/routing/s09-fable-effort-canary).
CLAUDE_JSON = {
    "type": "result", "subtype": "success", "is_error": False, "num_turns": 1,
    "total_cost_usd": 0.0123, "result": "done",
    "usage": {"input_tokens": 7, "cache_creation_input_tokens": 0,
              "cache_read_input_tokens": 1200, "output_tokens": 42},
    "modelUsage": {"claude-sonnet-5-5": {
        "inputTokens": 7, "outputTokens": 42, "cacheReadInputTokens": 1200,
        "cacheCreationInputTokens": 0, "costUSD": 0.0123}},
}


def _spec():
    items = [{"id": f"it-{n}", "title": f"IT-{n}", "category": "c", "touches": "src/a.py",
              "prior_art": {"decision": "build", "source": "fixture"}} for n in (1, 2, 3)]
    sess = [
        {"id": "s01", "title": "S1", "task_class": "standard_build", "items": ["it-1"]},
        {"id": "s02", "title": "S2", "task_class": "agentic_build", "items": ["it-2"],
         "peer_triggers": ["architecture_decision"],
         "prompt": "work, then run /adversarial-review on the design"},
        {"id": "s03", "title": "S3", "task_class": "standard_build", "items": ["it-3"],
         "verify": {"gates": ["mark"], "locked": [LOCKED]}},
    ]
    for s in sess:
        s.setdefault("prompt", "work")
        s.update(dispatch={"subagent_type": None, "depends_on": []})
    return {"title": "Route at dispatch e2e", "plan_schema_version": 8,
            "created": "2026-09-29", "categories": [{"key": "c", "label": "C"}],
            "items": items, "sessions": sess,
            "infographic": {"type": "phase-journey",
                            "phases": [{"id": "p1", "label": "P1",
                                        "items": ["it-1", "it-2", "it-3"]}]}}


def _git(root, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
                          cwd=root, check=True, capture_output=True, text=True).stdout.strip()


class Fixture:
    """A throwaway repository holding one built v8 plan, and the env to drive it."""

    def __init__(self, tmp_path):
        self.tmp = tmp_path
        self.root = tmp_path / "proj"
        self.plan_dir = self.root / "_plans" / SLUG
        self.routing = tmp_path / "model-routing.yaml"
        self.ledger = tmp_path / "ledger" / "outcomes.ndjson"
        self.marker = tmp_path / "gate-ran"
        shutil.copy(TREE / "model-routing.yaml", self.routing)
        egress = tmp_path / "egress"
        egress.mkdir()
        keep = ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "USER", "SHELL")
        self.env = {k: os.environ[k] for k in keep if k in os.environ}
        self.env.update(PLAN_EXECUTE_ROUTING_SSOT=str(self.routing),
                        PLAN_EXECUTE_OUTCOME_LEDGER=str(self.ledger),
                        PLAN_EXECUTE_EGRESS_ROOT=str(egress),
                        PLAN_EXECUTE_SKIP_BROWSER_CHECKS="1")
        self._build()

    def _build(self):
        root = self.root
        (root / "tests").mkdir(parents=True)
        (root / "src").mkdir()
        (root / "src" / "a.py").write_text("A = 1\n")
        (root / LOCKED).write_text("def test_it():\n    assert 1 + 1 == 2\n")
        (root / ".gitignore").write_text("__pycache__/\n")
        cl = root / ".claude"
        cl.mkdir()
        (cl / "deploy-targets.json").write_text("{}")
        (cl / "eval-gates.json").write_text(json.dumps(
            {"mark": {"kind": "argv", "argv": ["sh", "-c", f"touch '{self.marker}'"]}}))
        _git(root, "init", "-q", "-b", "main")
        _git(root, "add", "-A")
        _git(root, "commit", "-qm", "fixture: the locked test")   # the EARLIER commit
        spec = self.tmp / "spec.json"
        spec.write_text(json.dumps(_spec()))
        self.cli(BUILD_PY, str(spec), str(self.plan_dir), "--register-in", str(root))
        _git(root, "add", "-A")
        _git(root, "commit", "-qm", "fixture: the plan")

    def cli(self, script, *args, env=None, check=True):
        p = subprocess.run([sys.executable, str(script), *args], cwd=self.root,
                           env={**self.env, **(env or {})}, capture_output=True, text=True,
                           timeout=120)
        if check and p.returncode:
            raise AssertionError(f"{Path(script).name} {args} exited {p.returncode}:\n"
                                 f"{p.stdout}\n{p.stderr}")
        return p

    def run(self, *args, **kw):
        return self.cli(RUN_PY, args[0], str(self.plan_dir), *args[1:], **kw)

    def begin(self, *sessions, harness_args=(), env=None):
        p = self.run("begin", "--sessions", *sessions, *harness_args, env=env)
        return json_tail(p.stdout), p.stderr

    def json(self, *args, **kw):
        return json_tail(self.run(*args, **kw).stdout)

    def state(self):
        return json.loads((self.plan_dir / "run_state.json").read_text())


HARNESS = {  # provider -> (begin args, env): the switches each real harness uses
    "anthropic": ((), {}),
    "zai": ((), {"CLAUDE_CONFIG_DIR": "<tmp>/.claude-glm"}),
    "openai": (("--harness", "codex"), {}),
}


def _harness(fx, provider):
    args, env = HARNESS[provider]
    return args, {k: v.replace("<tmp>", str(fx.tmp)) for k, v in env.items()}


def _cell(fx, task_class, provider):
    got = rr.resolve(task_class, provider, ssot_path=str(fx.routing))
    return got["model_id"], got["native_effort"] or ""


def _ran(member):
    if member["backend"] == "codex":
        return member["codex_model"], member["codex_effort"]
    return member["model_arg"], member["reasoning"]


@pytest.fixture
def fx(tmp_path):
    return Fixture(tmp_path)



MECHANISMS = {"agent_definition", "tier_agent", "prompt_directive_advisory"}  # contract §3.5


def _receipt_line(fx, sid, task_class, member, why="class default"):
    model, effort = _ran(member)
    mech = "codex_cli" if member["backend"] == "codex" else member["effort_mechanism"]
    version = rad.routing_version(fx.routing.read_text())
    return f"route: {sid} {task_class} -> {model}@{effort} [{mech}] ({why}, routing v{version}"


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# --------------------------------------------------------------------------
# 1-2. Built with no models; each harness resolves every session to its SSOT
#      cell, prints the cell and its effort_mechanism, and isolates in tmp_path
# --------------------------------------------------------------------------
@pytest.mark.parametrize("provider", ["anthropic", "zai", "openai"])
def test_each_harness_resolves_every_session_to_its_ssot_cell(fx, provider):
    man = json.loads((fx.plan_dir / "manifest.json").read_text())
    assert man["plan_schema_version"] == 8
    assert [(s["model"], s.get("reasoning", "")) for s in man["sessions"]] == [("", "")] * 3
    args, env = _harness(fx, provider)
    out, err = fx.begin("s01", "s02", "s03", harness_args=args, env=env)
    assert out["active_provider"] == ("anthropic" if provider == "openai" else provider)
    by_id = {m["id"]: m for m in out["batch"]}
    frozen = fx.state()["resolved_cell"]
    for sid, tc in (("s01", "standard_build"), ("s02", "agentic_build"),
                    ("s03", "standard_build")):
        want = _cell(fx, tc, provider)
        m = by_id[sid]
        assert _ran(m) == want, (sid, provider)
        f = frozen[sid]
        assert (f["model"], f["reasoning"], f["provider"], f["task_class"]) == \
            (*want, provider, tc)
        # The begin receipt: the resolved cell and how its effort binds.
        assert _receipt_line(fx, sid, tc, m) in err, err
        if provider == "openai":
            assert m["backend"] == "codex"
        else:
            assert m["backend"] == "claude" and m["effort_mechanism"] in MECHANISMS
            if m["effort_mechanism"] == "tier_agent":
                assert m["subagent_type"] == f"tier-{want[0]}-{want[1]}"
    # Plan isolation cut its branch and worktree in the THROWAWAY repository.
    tree = Path(by_id["s01"]["worktree"])
    assert tree == fx.root / ".plan-worktrees" / SLUG and tree.is_dir()
    assert _git(fx.root, "branch", "--list", f"plan/{SLUG}") != ""
    assert _git(TREE, "branch", "--list", f"plan/{SLUG}") == ""


# --------------------------------------------------------------------------
# 3-4. A one-byte edit to the locked check fails verify-begin before any gate;
#      the rework's ledger record carries the receipt's usage
# --------------------------------------------------------------------------
def _closeout(fx, sid, item):
    path = fx.tmp / f"{sid}.closeout.md"
    path.write_text("<plan-execute-closeout>\n" + json.dumps(
        {"session": sid, "result": "DONE", "items_completed": [item], "items_blocked": [],
         "notes": {item: "done"}, "dispatch_next": True, "human_checkpoint_reason": None})
        + "\n</plan-execute-closeout>\n")
    return path


def _attempt(fx, n, *, edit):
    """One real attempt at s03: begin, the worker's (optional) one-byte edit to the
    locked check in the plan worktree, record-receipt, apply, verify-begin."""
    out, err = fx.begin("s03")
    member = out["batch"][0]
    locked = Path(member["worktree"]) / LOCKED
    if edit and n == 1:
        locked.write_text(locked.read_text().replace("2\n", "3\n"))   # one byte
    transcript = fx.tmp / "result.json"
    transcript.write_text(json.dumps(CLAUDE_JSON))
    fx.run("record-receipt", "--session", "s03", "--agent-id", f"agent-{n}",
           "--transcript", str(transcript))
    fx.run("apply", "--session", "s03", "--output-file", str(_closeout(fx, "s03", "it-3")))
    return member, err, fx.json("verify-begin", "--session", "s03")


def _ledger(fx):
    return [json.loads(ln) for ln in fx.ledger.read_text().splitlines() if ln.strip()]


def test_a_one_byte_edit_to_a_locked_check_is_refused_and_the_ledger_carries_usage(fx):
    member, _, out = _attempt(fx, 1, edit=True)
    snap = json.loads((fx.plan_dir / "_verify_state" / "s03.locked.json").read_text())
    assert snap["repo_root"] == member["worktree"]        # hashed where the worker writes
    assert out["action"] == "rework" and out["refusal"] == "LOCKED_CHECK_EDITED"
    assert out["edited"] == [[LOCKED, "changed", "locked check"]]
    gates = json.loads((fx.plan_dir / "_verify_state" / "s03.json").read_text())["gate_status"]
    assert gates["locked-checks"] == "LOCKED_CHECK_EDITED"
    assert gates["gate:mark"] == "pending"
    assert not fx.marker.exists()                         # no gate ran
    (rec,) = _ledger(fx)
    usage = rec["usage"]
    assert usage["source"] == "claude-json" and usage["scope"] == "worker"
    assert all(usage[k] is not None for k in ("input_tokens", "output_tokens",
                                              "cache_read_tokens", "cache_creation_tokens"))
    assert (usage["input_tokens"], usage["output_tokens"], usage["cost_usd"]) == (7, 42, 0.0123)
    assert (rec["result"], rec["gates_failed"], rec["routing_provenance"]) == \
        ("rework", ["locked-checks"], "default_resolved")
    assert (rec["model_authored"], rec["reasoning_authored"]) == \
        _cell(fx, "standard_build", "anthropic")


def test_an_untouched_locked_check_lets_the_same_gate_run(fx):
    """The control: without the edit the same verify-begin runs the gate, so "the
    marker is absent" above is not true of a gate that never could run."""
    _, _, out = _attempt(fx, 1, edit=False)
    assert out["action"] == "run-argv"
    assert fx.json("verify-run", "--session", "s03", "--gate", "gate:mark")["action"] == "passed"
    assert fx.marker.exists()


# --------------------------------------------------------------------------
# Hardened cases
# --------------------------------------------------------------------------
def test_a_routing_file_edit_between_two_begins_leaves_the_frozen_cell_unchanged(fx):
    worktree_file = _sha(TREE / "model-routing.yaml")
    first, _ = fx.begin("s01")
    fx.run("release")
    before = _ran(first["batch"][0])
    old, new = _move_standard_build(fx.routing)           # edits the tmp copy only
    moved = _cell(fx, "standard_build", "anthropic")
    assert moved != before, "known positive: the edit really moves the class default"
    second, err = fx.begin("s01")
    assert _ran(second["batch"][0]) == before
    assert fx.state()["resolved_cell"]["s01"]["routing_version"] == old
    assert f"from routing v{old}; the routing file is now v{new}" in err
    assert _sha(TREE / "model-routing.yaml") == worktree_file


def test_an_unpinned_session_climbs_from_its_resolved_cell_after_two_same_signature_failures(fx):
    """Two real LOCKED_CHECK_EDITED refusals (the edit stays in the worktree), then
    the third begin climbs one rung from the cell begin resolved — not from any
    authored model, which this session never had."""
    base = _cell(fx, "standard_build", "anthropic")
    for n in (1, 2):
        member, _, out = _attempt(fx, n, edit=True)
        assert _ran(member) == base and not member.get("escalated_from")   # same rung first
        assert out["refusal"] == "LOCKED_CHECK_EDITED"
    assert out["stuck_protocol"]["consecutive"] == 2
    nxt = rr.escalate("standard_build", "anthropic",
                      current={"model_id": base[0], "native_effort": base[1] or None},
                      ssot_path=str(fx.routing))
    out, err = fx.begin("s03")
    m = out["batch"][0]
    assert m["escalated_from"]["authored"] == {"model": base[0], "reasoning": base[1]}
    assert m["escalated_from"]["rung"] == 1
    assert _ran(m) == (nxt["model_id"], nxt["native_effort"]) != base
    assert f"escalated rung 1 from {base[0]}@{base[1]}" in err


def test_zai_provider_env_under_the_codex_harness_resolves_for_openai(fx):
    out, _ = fx.begin("s02", harness_args=("--harness", "codex"),
                      env={"PLAN_EXECUTE_ROUTING_PROVIDER": "zai"})
    want = _cell(fx, "agentic_build", "openai")
    assert want != _cell(fx, "agentic_build", "zai")      # the cases really differ
    assert _ran(out["batch"][0]) == want
    assert fx.state()["resolved_cell"]["s02"]["provider"] == "openai"
