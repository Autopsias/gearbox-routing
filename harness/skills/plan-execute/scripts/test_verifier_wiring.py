"""WIRE-03: the cross-family gate is WIRED — proved through verify.py, both ways.

WHAT MAKES THIS DIFFERENT from `test_codex_review_backend.py`, which already
stands a recorded `codex` on PATH: that suite calls `llm_review_gate.main` with
a hand-written argv. This one resolves `cross-family-review-medium` THROUGH THE
REGISTRY the way `verify.py` does (`shipping.resolve_gate` → `run_deploy_argv`)
and never touches the backend function, so what it proves is the argv the
registry actually ships — the `-c sandbox_mode="read-only"` and the
`--output-schema` included. A registry entry that stopped passing `--reviewer
codex` would leave every backend unit test green and this one red.

THE TWO DIRECTIONS ARE ONE FILE ON PURPOSE. Case 2's whole claim is a NEGATIVE
— "the codex shim was never invoked" — and an absent record file is equally
well explained by a shim that cannot record at all. Case 1 stands the SAME shim,
in the same shape, and proves it DOES record when a review runs. Neither
assertion is worth much without the other, so they are asserted side by side.

HERMETIC BY SHADOWING, NOT BY STRIPPING. The shim's bin dir goes FIRST on the
ambient `PATH` and nothing is removed from it. `codex_review_backend` resolves
the binary with `shutil.which`, which honours PATH order, so the shim wins over
whatever real `codex` this box has installed — while `git`, `python3` and
`gitleaks` stay exactly where the box keeps them. An earlier version stripped
every PATH DIRECTORY that held a `codex`; on a box that installs codex ALONGSIDE
the toolchain (Homebrew node drops it in Homebrew's `bin` directory, which is also
where `gitleaks` lives) that stripped `gitleaks` too, the egress guard failed
closed, and BOTH cases failed. It is the NEUTER PROBE that needs a box with no
codex at all — so the probe does the stripping, in its own one-off edit, and the
shipped fixture stays machine-independent (`_evidence/s04/neuter-probe.txt`).

Run: pytest skills/plan-execute/scripts/test_verifier_wiring.py -q
"""
import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
#: skills/plan-execute/scripts → parents[2] is the repo root this checkout ships.
REPO = SCRIPTS.parents[2]

import pytest  # noqa: E402

import llm_review_gate as g  # noqa: E402
import llm_review_ledger as ledger  # noqa: E402
import outcomes as outc  # noqa: E402
import run as run_cli  # noqa: E402
import run_state_io as rsi  # noqa: E402
import shipping as shp  # noqa: E402
import verifier_park as vpk  # noqa: E402
import verify as vfy  # noqa: E402
from rework import _feedback_path  # noqa: E402
from test_verify import _begin_doing, _status, _verify_sess, make_plan, write_closeout  # noqa: E402

GATE_ID = "cross-family-review-medium"
GATE = f"gate:{GATE_ID}"
SID = "s01"
#: `prepare_surface` keys the ledger on (session, level, reviewer) — the level and
#: the reviewer come from the registry argv, so this name is itself an assertion
#: that the entry still says `--level medium --reviewer codex`.
LEDGER_SESSION = f"{SID}.medium.codex"

#: The recorded `codex`, with its record path and its answer BAKED IN. The shim
#: next door in `test_codex_review_backend.py` is configured through env vars,
#: which cannot work here: `run_deploy_argv` builds the child environment from an
#: allowlist (PATH/HOME/LANG/TMPDIR/USER/SHELL plus the gate's PLAN_EXECUTE_*),
#: so an env-configured recorder would arrive empty and record nothing — and an
#: empty record file is exactly what case 2 reads as proof.
_SHIM = '''#!/usr/bin/env python3
import json, pathlib, sys
ARGV_FILE = {argv_file!r}
ANSWER = {answer!r}
argv = sys.argv[1:]
with open(ARGV_FILE, "a") as fh:
    fh.write(json.dumps(argv) + "\\n")
if argv[:1] == ["--version"]:
    print("codex-cli 0.147.0-shim"); raise SystemExit(0)
if argv[:2] == ["login", "status"]:
    print("Logged in using ChatGPT"); raise SystemExit(0)
sys.stdin.read()                      # the prompt; a reader that never reads deadlocks
print(json.dumps({{"type": "thread.started", "thread_id": "th_wiring_1"}}))
print(json.dumps({{"type": "item.completed"}}))
if "-o" in argv:
    pathlib.Path(argv[argv.index("-o") + 1]).write_text(ANSWER)
raise SystemExit(0)
'''

#: The reviewed surface is exactly `a.py` (see `_fixture_tree`), so the reviewer's
#: attestation can be a literal. A shortfall here would fail the run whatever the
#: findings said — which is the point of `codex_review_backend.attest`.
_ANSWER = json.dumps({"verdict": "PASS", "reviewed": ["a.py"], "findings": []})


def _git(root, *args):
    subprocess.run(["git", *args], cwd=str(root), check=True, capture_output=True)


@pytest.fixture
def shim(tmp_path, monkeypatch):
    """A recorded `codex` FIRST on the ambient PATH. -> the argv-record path.

    PREPEND ONLY — the ambient PATH is passed through untouched. `shutil.which`
    (how `codex_review_backend` finds the binary) returns the first executable
    match, so this shim shadows a real `codex` without hiding the `git` /
    `python3` / `gitleaks` the gate subprocess needs from the same directories.
    """
    bindir = tmp_path / "bin"
    bindir.mkdir()
    argv_file = tmp_path / "codex-argv.ndjson"
    exe = bindir / "codex"
    exe.write_text(_SHIM.format(argv_file=str(argv_file), answer=_ANSWER))
    exe.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}")
    return argv_file


@pytest.fixture
def deployed_home(tmp_path, monkeypatch):
    """`$HOME/.claude` pointed at THIS checkout.

    The registry argv prefers `$HOME/.claude/skills/plan-execute/scripts/
    llm_review_gate.py` and only falls back to a repo-relative path. Without this
    the fixture would exercise whatever is deployed on the developer's box
    instead of the code under test — or, on a box with nothing deployed, fall
    back to a relative path that does not exist inside the fixture tree and
    report an exec error as a gate result."""
    home = tmp_path / "home"
    home.mkdir()
    (home / ".claude").symlink_to(REPO, target_is_directory=True)
    monkeypatch.setenv("HOME", str(home))
    return home


def _fixture_tree(tmp_path, *, restricted, gate=GATE_ID, gate2=None, **sess_extra):
    """A built plan whose PROJECT ROOT is a git tree with a one-file diff.

    `.gitignore` keeps `_plans/` and `.claude/` out of the reviewed surface so
    the surface is exactly `a.py` — the dashboard and the closeout this very test
    writes would otherwise show up as untracked files the reviewer is held to
    attest, and the expected count would drift with the harness rather than with
    the code under review.

    `restricted=True` plants a git-ignored `.env`: rule 1 of the egress guard is
    a FULL-TREE filename walk precisely because `.env` is normally git-ignored,
    so this is the real restricted disposition and not a stand-in for it."""
    gates = [gate] + ([gate2] if gate2 else [])
    plan_dir = make_plan(tmp_path, [_verify_sess(gates, **sess_extra)],
                         gates={gate2: {"kind": "argv", "argv": ["true"]}} if gate2 else None)
    root = plan_dir.parent.parent
    (root / ".gitignore").write_text("_plans/\n.claude/\n.env\n")
    (root / "a.py").write_text("def bump(n):\n    return n + 1\n")
    (root / "b.py").write_text("VERSION = 1\n")
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "base")
    (root / "a.py").write_text("def bump(n):\n    return n + 2\n")
    if restricted:
        (root / ".env").write_text("KEY=1\n")
    _begin_doing(plan_dir, SID)
    write_closeout(plan_dir, SID)
    return plan_dir


def _run_the_registry_gate(plan_dir, gate=GATE):
    """Drive the gate the way `verify.py` does — id in, action out.

    Deliberately NOT `codex_review_backend.backend(...)`: the claim under test is
    that the REGISTRY entry ships a working argv, and a direct call would prove
    only that the function it eventually reaches still works."""
    vfy.verify_begin(plan_dir, SID)
    return vfy.verify_run_argv(plan_dir, SID, gate)


def _reviews(argv_file):
    """Only the REVIEW invocations. `--version` and `login status` are recorded
    too, and counting a probe as a review would hide a second spawn."""
    if not Path(argv_file).exists():
        return []
    return [a for a in (json.loads(ln) for ln in
                        Path(argv_file).read_text().splitlines())
            if "--output-schema" in a]


def _ledger(plan_dir, sess=LEDGER_SESSION):
    p = ledger.ledger_path(plan_dir, sess)
    if not p.exists():
        return []
    return [json.loads(ln) for ln in p.read_text().splitlines() if ln.strip()]


def _verdict_records(recs):
    """Records that mean A REVIEW HAPPENED, as opposed to a degrade stamp.

    `ledger.record` appends a `kind: surface` row for every judged round and the
    backend stamps `verifier: cross_family` only once codex has answered; the
    degrade path writes neither (and no `attempt` key, so it cannot advance the
    rework delta either)."""
    return [r for r in recs
            if r.get("kind") == "surface" or r.get("verifier") == "cross_family"]


# --------------------------------------------------------------------------
# Case 1 — a Claude-built session, a reviewable tree: Codex is what reviews it.
# --------------------------------------------------------------------------
def test_a_claude_built_session_is_reviewed_by_codex_through_the_registry(
        tmp_path, shim, deployed_home):
    plan_dir = _fixture_tree(tmp_path, restricted=False)
    out = _run_the_registry_gate(plan_dir)

    runs = _reviews(shim)
    assert len(runs) == 1, f"exactly one review spawn expected, got {runs}"
    argv = runs[0]
    # The two facts the REGISTRY is responsible for, read off the argv the shim
    # was actually handed. Read-only is the security claim; --output-schema is
    # what makes the answer parseable rather than prose.
    assert 'sandbox_mode="read-only"' in argv, argv
    assert "--output-schema" in argv, argv
    # The shim records `sys.argv[1:]`, so `codex` itself is the process name
    # and `exec` is the first argument — a `codex exec resume` retry would
    # show up here, and there must not be one.
    assert argv[0] == "exec", argv

    # "passed" and not a looser set: this session declares exactly one gate, so
    # the pass advances straight to the end of the block. A "run-argv" here would
    # mean the fixture grew a second gate and this assertion stopped meaning
    # "the cross-family gate returned 0".
    assert out["action"] == "passed", out
    verdicts = _verdict_records(_ledger(plan_dir))
    assert verdicts, "a completed cross-family review must leave a verdict in the ledger"
    # The transcript IS the evidence artifact -- `pytest -rP` prints it.
    print(f"\nCASE 1 codex-shim review invocations: {len(runs)}")
    print(f"CASE 1 argv: {argv}")
    print(f"CASE 1 verify action: {out['action']}")
    print(f"CASE 1 ledger verdict records: {json.dumps(verdicts)}")


# --------------------------------------------------------------------------
# Case 2 — a restricted tree: no codex process is ever started.
# --------------------------------------------------------------------------
def test_a_restricted_tree_never_starts_codex_and_records_no_verdict(
        tmp_path, shim, deployed_home):
    plan_dir = _fixture_tree(tmp_path, restricted=True)
    out = _run_the_registry_gate(plan_dir)

    # The shim is standing on PATH, able to record — case 1 proves that with the
    # same script. So an empty record here is a measurement, not an absence.
    assert _reviews(shim) == [], (
        "a restricted tree must not start a codex process at all")

    # exit 2 + the `on_box_human` marker = NOBODY MAY REVIEW THIS (s05). It used
    # to fall through verify.py's bare `indeterminate` path and sit PENDING
    # forever; it now parks for a human, which is the only thing that resolves.
    assert out["action"] == "awaits-review", out
    assert out["cause"] == "cross_family", out
    feedback = _feedback_path(plan_dir, SID).read_text()
    assert "VERIFIER: on_box_human" in feedback, feedback
    assert "DEGRADED_FROM: cross_family" in feedback, feedback

    assert _verdict_records(_ledger(plan_dir)) == [], (
        "a degrade must leave the ledger with no verdict of any kind")
    # The gate is parked, not failed, and the rework budget is untouched — a tree
    # that cannot be reviewed is not the agent's failure.
    st = json.loads((plan_dir / "_verify_state" / f"{SID}.json").read_text())
    assert st["gate_status"][GATE] == vpk.AWAITS and st["rework_count"] == 0, st
    assert _status(plan_dir, SID) == "AWAITS_REVIEW", _status(plan_dir, SID)
    print(f"\nCASE 2 codex-shim review invocations: {len(_reviews(shim))}")
    print(f"CASE 2 shim record file exists: {Path(shim).exists()} "
          f"(probe invocations only: {Path(shim).read_text().count(chr(10)) if Path(shim).exists() else 0})")
    print(f"CASE 2 verify action: {out['action']} (registry indeterminate_exit = 2)")
    print(f"CASE 2 dashboard status: {_status(plan_dir, SID)}")
    print("CASE 2 markers: " + " | ".join(
        ln for ln in feedback.splitlines()
        if ln.startswith(("VERIFIER:", "DEGRADED_FROM:"))))
    print(f"CASE 2 ledger verdict records: {_verdict_records(_ledger(plan_dir))}")
    print(f"CASE 2 gate_status={st['gate_status'][GATE]} rework_count={st['rework_count']}")



# ==========================================================================
# WIRE-04 (s05) — harness-aware gates, the on-box park, and its resolution.
#
# The new fact every case below turns on: WHICH FAMILY BUILT THIS CODE. It is
# read from the session's own `dispatch_started` / `dispatch_families` record in
# run.ndjson — the durable record the SSOT names as the derivation source — and
# never from a runner argument, because `verify.declared_env` calls `gate_env`
# without a harness and no such parameter exists to read.
# ==========================================================================
LLM_GATE_ID = "llm-review-medium"
LLM_GATE = f"gate:{LLM_GATE_ID}"
CLAUDE_LEDGER = f"{SID}.medium.claude"

#: A recorded `claude`. `llm_review_gate.run_once` invokes
#: `claude -p <prompt> --output-format json` and parses stdout as the CLI's JSON
#: envelope, so the shim answers in exactly that shape. MODE picks which of the
#: three outcomes the gate is being shown.
_CLAUDE_SHIM = '''#!/usr/bin/env python3
import json, sys
ARGV_FILE = {argv_file!r}
MODE = {mode!r}
argv = sys.argv[1:]
with open(ARGV_FILE, "a") as fh:
    fh.write(json.dumps(argv[:1]) + "\\n")
if MODE == "transport":
    sys.stderr.write("shim: reviewer could not start (transport)\\n")
    raise SystemExit(3)
if MODE == "findings":
    result = ("REVIEWED_FILES: 1\\n```json\\n" + json.dumps(
        [{{"file": "a.py", "line": 2, "severity": "high", "summary": "bad"}}]) + "\\n```")
else:
    result = "REVIEWED_FILES: 1\\n```json\\n[]\\n```"
print(json.dumps({{"result": result, "is_error": False}}))
'''


@pytest.fixture
def claude_shim(tmp_path, monkeypatch):
    """-> `install(mode)`; returns the argv-record path. Same PREPEND-ONLY rule
    as the codex `shim` fixture, for the same reason."""
    bindir = tmp_path / "cbin"
    bindir.mkdir()
    argv_file = tmp_path / "claude-argv.ndjson"

    def install(mode="clean"):
        exe = bindir / "claude"
        exe.write_text(_CLAUDE_SHIM.format(argv_file=str(argv_file), mode=mode))
        exe.chmod(0o755)
        monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}")
        return argv_file

    return install


def _claude_runs(argv_file):
    if not Path(argv_file).exists():
        return []
    return [json.loads(ln) for ln in Path(argv_file).read_text().splitlines()]


def _codex_harness(plan_dir):
    """Stamp the session's dispatch record the way `begin --harness codex` does.

    BOTH events, because `review_context.harness` reads both and `begin` writes
    both: the run-level `harness` stamp and the per-session executor family."""
    rsi.log_event(plan_dir, "dispatch_started", session_ids=[SID], harness="codex")
    rsi.log_event(plan_dir, "dispatch_families", session_ids=[SID],
                  families={SID: "openai"})


def _opt_in(monkeypatch, tmp_path, value):
    """Point the gate at an SSOT that answers `value` for the new key."""
    ssot = tmp_path / "ssot.yaml"
    ssot.write_text("executor_policy:\n  verification:\n"
                    f"    claude_verifier_under_codex_harness: {value}\n")
    monkeypatch.setenv("PLAN_EXECUTE_ROUTING_SSOT", str(ssot))
    return ssot


def _excerpt_lines(plan_dir):
    """The gate's own stdout, as the feedback file recorded it."""
    text = _feedback_path(plan_dir, SID).read_text()
    body = text.split("```", 2)[1] if "```" in text else text
    return [ln for ln in body.splitlines() if ln.strip()]


def _state(plan_dir):
    return json.loads((plan_dir / "_verify_state" / f"{SID}.json").read_text())


# --------------------------------------------------------------------------
# (a) Codex harness, no opt-in → the marker, the park, and NO rework charge.
# --------------------------------------------------------------------------
def test_codex_harness_without_the_opt_in_parks_instead_of_reviewing(
        tmp_path, claude_shim, deployed_home, monkeypatch):
    argv_file = claude_shim("clean")          # standing, able to record
    plan_dir = _fixture_tree(tmp_path, restricted=False, gate=LLM_GATE_ID)
    _codex_harness(plan_dir)
    out = _run_the_registry_gate(plan_dir, LLM_GATE)

    # The reviewer was never invoked: under the Codex harness the cross-family
    # reviewer IS `claude -p`, and it is opt-in.
    assert _claude_runs(argv_file) == [], "no Claude reviewer may run without the opt-in"
    assert out["action"] == "awaits-review", out
    assert out["cause"] == vpk.CAUSE_NO_OPT_IN, out

    lines = _excerpt_lines(plan_dir)
    # LINE 1 IS THE REVIEWER IDENTITY; the marker is a LATER line found by
    # prefix. An exact-first-line match would miss it, which is the whole point
    # of the prefix scan. NO WINDOW bounds it -- see (a2).
    assert lines[0].startswith("[llm-review-gate] reviewer:"), lines[:3]
    hit = [i for i, ln in enumerate(lines) if ln.startswith(vpk.MARKER)]
    assert hit and hit[0] > 0, lines[:12]

    st = _state(plan_dir)
    assert st["gate_status"][LLM_GATE] == vpk.AWAITS, st
    assert st["rework_count"] == 0, "a park is never a rework charge"
    assert _status(plan_dir, SID) == "AWAITS_REVIEW"
    assert _verdict_records(_ledger(plan_dir, CLAUDE_LEDGER)) == []
    print(f"\n(a) claude reviewer invocations: {_claude_runs(argv_file)}")
    print(f"(a) stdout line 1: {lines[0]}")
    print(f"(a) marker matched at line index {hit[0]} (prefix scan, not first-line)")
    print(f"(a) verify action={out['action']} cause={out['cause']}")
    print(f"(a) gate_status={st['gate_status'][LLM_GATE]} rework_count={st['rework_count']} "
          f"dashboard={_status(plan_dir, SID)}")


def test_the_marker_is_found_past_the_preamble_however_long_it_gets(
        tmp_path, shim, deployed_home, monkeypatch):
    """THE MARKER IS NOT IN THE FIRST TEN LINES. The preamble prints ONE LINE PER
    UNTRACKED FILE, so any window is outrun by one more file -- see `vpk.VERIFIER`
    for what that cost before the fix."""
    plan_dir = _fixture_tree(tmp_path, restricted=True)   # cross-family degrade
    for i in range(8):
        (plan_dir.parent.parent / f"u{i}.py").write_text(f"U = {i}\n")

    seen = {}                       # OBSERVE the real stdout, never stand in for it

    def spy(pd, sid, state, gate, excerpt, res, _router=vpk.indeterminate):
        seen["stdout"] = (res or {}).get("stdout") or ""
        return _router(pd, sid, state, gate, excerpt, res)

    monkeypatch.setattr(vfy.vpk, "indeterminate", spy)
    out = _run_the_registry_gate(plan_dir)

    lines = seen["stdout"].splitlines()
    at = [i for i, ln in enumerate(lines) if ln.startswith(vpk.MARKER)]
    # The fixture must still REPRODUCE the defect or this test proves nothing.
    assert at and at[0] >= 10, f"marker at {at} -- inside the old 10-line window"
    assert vpk.marker_in(seen["stdout"]) and vpk.cause_in(seen["stdout"]) == "cross_family"
    assert out["action"] == "awaits-review" and "transport_attempt" not in out, out
    st = _state(plan_dir)
    assert st["gate_status"][GATE] == vpk.AWAITS and st["rework_count"] == 0, st
    assert _status(plan_dir, SID) == "AWAITS_REVIEW"
    print(f"\n(a2) 8 untracked files -> marker at stdout line index {at[0]} "
          f"(removed window was 10); line before it: {lines[at[0] - 1][:66]}\n"
          f"(a2) cause_in={vpk.cause_in(seen['stdout'])} action={out['action']} "
          f"transport_attempt absent={'transport_attempt' not in out} gate="
          f"{st['gate_status'][GATE]} dashboard={_status(plan_dir, SID)}")


# --------------------------------------------------------------------------
# (b) Codex harness WITH the opt-in → today's `claude -p` path, once.
# --------------------------------------------------------------------------
def test_the_opt_in_lets_the_claude_reviewer_run_under_the_codex_harness(
        tmp_path, claude_shim, deployed_home, monkeypatch):
    argv_file = claude_shim("clean")
    _opt_in(monkeypatch, tmp_path, "true")
    plan_dir = _fixture_tree(tmp_path, restricted=False, gate=LLM_GATE_ID)
    _codex_harness(plan_dir)
    out = _run_the_registry_gate(plan_dir, LLM_GATE)

    runs = _claude_runs(argv_file)
    assert len(runs) == 1, f"exactly one reviewer spawn expected, got {runs}"
    assert runs[0] == ["-p"], runs
    assert out["action"] == "passed", out
    print(f"\n(b) opt-in true → claude reviewer invocations: {len(runs)} {runs}")
    print(f"(b) verify action: {out['action']}")


# --------------------------------------------------------------------------
# (c) Claude harness → completely unchanged. No marker anywhere.
# --------------------------------------------------------------------------
def test_the_claude_harness_is_untouched(tmp_path, claude_shim, deployed_home):
    argv_file = claude_shim("clean")
    plan_dir = _fixture_tree(tmp_path, restricted=False, gate=LLM_GATE_ID)
    # No dispatch record at all → `harness` reads "claude", the pre-s05 default.
    # KNOWN POSITIVE FIRST: a reader that answers False because it could not
    # read the file at all is indistinguishable from one that read `false`.
    yes = tmp_path / "yes.yaml"
    yes.write_text("    claude_verifier_under_codex_harness: true\n")
    assert vpk.claude_verifier_opted_in(yes) is True, "the opt-in reader can say YES"
    assert vpk.claude_verifier_opted_in() is False, (
        "and against the REPO's own SSOT it says NO: the opt-in ships OFF")
    out = _run_the_registry_gate(plan_dir, LLM_GATE)

    assert len(_claude_runs(argv_file)) == 1, _claude_runs(argv_file)
    assert out["action"] == "passed", out
    assert not _feedback_path(plan_dir, SID).exists() or (
        vpk.MARKER not in _feedback_path(plan_dir, SID).read_text())
    print(f"\n(c) claude harness, opt-in off → reviewer ran: "
          f"{len(_claude_runs(argv_file))}, action={out['action']}, no marker")


# --------------------------------------------------------------------------
# (d) exit 2 with NO marker is a TRANSPORT failure: no charge, bounded retry.
#     exit 1 is a blocking finding: charged, exactly as before.
# --------------------------------------------------------------------------
def test_a_transport_indeterminate_is_never_charged_and_is_bounded(
        tmp_path, claude_shim, deployed_home):
    claude_shim("transport")
    plan_dir = _fixture_tree(tmp_path, restricted=False, gate=LLM_GATE_ID)
    seen = []
    for _ in range(vpk.MAX_TRANSPORT + 1):
        seen.append(_run_the_registry_gate(plan_dir, LLM_GATE))

    fb = _feedback_path(plan_dir, SID).read_text()
    assert vpk.MARKER not in fb, "a transport failure carries NO on-box marker"
    # Every attempt inside the budget: not a finding, not a charge, retried.
    for i, out in enumerate(seen[:vpk.MAX_TRANSPORT]):
        assert out["action"] == "indeterminate", (i, out)
        assert out["rework_count"] == 0, (i, out)
        assert out["transport_attempt"] == i + 1, (i, out)
    # And the budget is a BOUND, not a suggestion: the next one blocks, naming
    # the transport rather than the agent.
    assert seen[-1]["action"] == "halted", seen[-1]
    assert seen[-1]["transport"] is True, seen[-1]
    assert seen[-1]["rework_count"] == 0, seen[-1]
    assert "transport failure" in seen[-1]["reason"]
    print(f"\n(d) transport attempts: "
          f"{[(o['action'], o['rework_count']) for o in seen]}")
    print(f"(d) blocker reason: {seen[-1]['reason'][:160]}")


def test_a_blocking_finding_still_charges_rework(tmp_path, claude_shim, deployed_home):
    claude_shim("findings")
    plan_dir = _fixture_tree(tmp_path, restricted=False, gate=LLM_GATE_ID)
    out = _run_the_registry_gate(plan_dir, LLM_GATE)
    assert out["action"] == "rework", out
    assert out["attempt"] == 1, out
    assert _state(plan_dir)["rework_count"] == 1, _state(plan_dir)
    print(f"\n(d) exit 1 (blocking finding) → action={out['action']} "
          f"rework_count={_state(plan_dir)['rework_count']}")


# --------------------------------------------------------------------------
# (e) A CROSS-FAMILY gate under the Codex harness parks the same way.
#     Codex reviewing Codex-built code is not cross-family, and the family that
#     did not write it — Claude — needs the opt-in this session has not given.
# --------------------------------------------------------------------------
def test_a_cross_family_gate_under_the_codex_harness_parks(
        tmp_path, shim, claude_shim, deployed_home):
    claude_argv = claude_shim("clean")
    plan_dir = _fixture_tree(tmp_path, restricted=False)      # cross-family gate
    _codex_harness(plan_dir)
    out = _run_the_registry_gate(plan_dir)

    assert _reviews(shim) == [], "no codex review may run: Codex built this code"
    assert _claude_runs(claude_argv) == [], "and Claude may not read it without the opt-in"
    assert out["action"] == "awaits-review", out
    assert out["cause"] == vpk.CAUSE_NO_OPT_IN, out
    lines = _excerpt_lines(plan_dir)
    assert lines[0].startswith("[llm-review-gate] reviewer: family=codex"), lines[:3]
    hit = [i for i, ln in enumerate(lines) if ln.startswith(vpk.MARKER)]
    assert hit and hit[0] > 0, lines[:12]
    assert _status(plan_dir, SID) == "AWAITS_REVIEW"
    assert _state(plan_dir)["rework_count"] == 0
    print(f"\n(e) cross-family gate under codex harness: codex spawns="
          f"{len(_reviews(shim))} claude spawns={len(_claude_runs(claude_argv))}")
    print(f"(e) identity line 1: {lines[0]}")
    print(f"(e) marker at line index {hit[0]}; action={out['action']}; "
          f"dashboard={_status(plan_dir, SID)}")


# --------------------------------------------------------------------------
# (e2) ...and WITH the opt-in it is CLAUDE that reviews, not Codex.
#     The mirror of (e), and the case that measured 1 codex spawn / 0 claude and
#     a cross-family PASS before `reviewer_for` existed: the opt-in exists to let
#     the family that did NOT write the code read it, so turning it on must not
#     leave the registry's `--reviewer codex` pointed at Codex-built code.
# --------------------------------------------------------------------------
def test_the_opt_in_makes_a_cross_family_gate_reviewed_by_claude_not_codex(
        tmp_path, shim, claude_shim, deployed_home, monkeypatch, capsys):
    claude_argv = claude_shim("clean")
    _opt_in(monkeypatch, tmp_path, "true")
    plan_dir = _fixture_tree(tmp_path, restricted=False)      # cross-family gate
    _codex_harness(plan_dir)
    out = _run_the_registry_gate(plan_dir)

    codex_runs, claude_runs = _reviews(shim), _claude_runs(claude_argv)
    assert len(claude_runs) == 1 and claude_runs[0] == ["-p"], claude_runs
    assert codex_runs == [], (
        "Codex built this code: reviewing it with Codex is not a cross-family review")
    assert out["action"] == "passed", out
    # THE LEDGER KEY IS THE GATE'S, NOT THE RUNNING FAMILY'S (WIRE-07 finding 2):
    # `reviewer_for` is many-to-one, so keying on who RAN put this gate and
    # `llm-review-medium` on ONE ledger and the second to run skipped its review.
    # The DECLARED `--reviewer` is the only axis that tells the two entries apart.
    assert _ledger(plan_dir, LEDGER_SESSION), "the gate's OWN ledger, keyed on its declared ask"
    assert _ledger(plan_dir, CLAUDE_LEDGER) == [], "llm-review-medium's ledger is not touched"

    # LINE 1 MUST TELL THE TRUTH. The flip sits before the identity print; doing it
    # after would leave line 1 saying family=codex while Claude ran, which is the
    # ordering trap this case exists to pin. In-process for stdout the registry
    # subprocess does not hand back.
    rc = g.main(["--level", "medium", "--reviewer", "codex", "--harness", "codex",
                 "--cwd", str(plan_dir.parent.parent)])
    line1 = capsys.readouterr().out.splitlines()[0]
    assert line1.startswith("[llm-review-gate] reviewer: family=claude"), line1
    print(f"\n(e2) opt-in true, cross-family gate, codex harness -> "
          f"codex spawns={len(codex_runs)} claude spawns={len(claude_runs)}")
    print(f"(e2) verify action={out['action']}; ledger written: {LEDGER_SESSION} "
          f"(llm-review ledger rows: {len(_ledger(plan_dir, CLAUDE_LEDGER))})")
    print(f"(e2) stdout line 1: {line1}  (gate rc={rc})")


# --------------------------------------------------------------------------
# (f) THE PARK MUST RESOLVE. Both decisions, end to end.
# --------------------------------------------------------------------------
def _ledger_records():
    """The outcomes ledger, which conftest points at tmp_path. `resolution` is
    NOT a field on the record — it only feeds `record_id` — so callers compare
    the tail rather than filtering on it."""
    path = Path(os.environ[outc.LEDGER_PATH_ENV])
    if not path.exists():
        return []
    return [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()]


def _parked_plan(tmp_path, checkpoint=None):
    plan_dir = _fixture_tree(tmp_path, restricted=False, gate=LLM_GATE_ID,
                             post_session={"git": "commit"})
    if checkpoint:            # a checkpoint the SUBAGENT asked for, before any park
        write_closeout(plan_dir, SID, checkpoint=checkpoint)
    _codex_harness(plan_dir)
    out = _run_the_registry_gate(plan_dir, LLM_GATE)
    assert out["action"] == "awaits-review", out
    # While parked, shipping DEFERS — the park writes the checkpoint into the
    # closeout, which is what `shipping.checkpoint_pending` reads.
    assert shp.ship_begin(plan_dir, SID, dry_run=True)["action"] == "deferred"
    return plan_dir


def test_a_plain_ack_cannot_rubber_stamp_a_verifier_park(
        tmp_path, claude_shim, deployed_home):
    claude_shim("clean")
    plan_dir = _parked_plan(tmp_path)
    with pytest.raises(SystemExit) as e:
        run_cli.cmd_ack_checkpoint(plan_dir, SID)
    assert "verified-on-box" in str(e.value) and "blocked" in str(e.value), e.value
    assert _status(plan_dir, SID) == "AWAITS_REVIEW"
    print(f"\n(f) plain ack REFUSED: {str(e.value)[:200]}")


def test_verified_on_box_clears_the_park_and_lets_shipping_proceed(
        tmp_path, claude_shim, deployed_home):
    claude_shim("clean")
    plan_dir = _parked_plan(tmp_path)
    before = len(_ledger_records())
    out = vpk.resolve(plan_dir, SID, "verified-on-box", note="read the diff by hand")

    assert out["action"] == "done", out
    assert out["final_status"] == "DONE", out
    assert out["verifier_disposition"] == vpk.VERIFIED_ON_BOX, out
    st = _state(plan_dir)
    assert "on_box_review" not in st, st
    assert st["gate_status"][LLM_GATE] == "passed", st
    assert st["outcome"] == "passed", st
    assert _status(plan_dir, SID) == "DONE"
    # The disposition is RECORDED, not merely acted on.
    co = json.loads((plan_dir / "_closeouts" / f"{SID}.json").read_text())
    assert co["human_checkpoint_reason"] is None, co
    assert co["verifier_dispositions"][-1]["disposition"] == vpk.VERIFIED_ON_BOX, co
    # ...and it reaches the OUTCOMES LEDGER too. `before` makes this a DELTA:
    # verify_finalize writes its own record afterwards, so "the tail says passed"
    # alone would be satisfied by that one.
    led_recs = _ledger_records()
    assert len(led_recs) > before, led_recs
    disp = led_recs[before]
    assert disp["result"] == "passed" and disp["verified"] is True, disp
    assert disp["session"] == SID and disp["rework_count"] == 0, disp
    # ...and shipping is no longer deferred.
    ship = shp.ship_begin(plan_dir, SID, dry_run=True)
    assert ship["action"] == "dry-run", ship
    assert ship["steps"], ship
    print(f"\n(f) VERIFIED-ON-BOX → verify {out['action']}/{out['final_status']}, "
          f"gate={st['gate_status'][LLM_GATE]}, dashboard={_status(plan_dir, SID)}")
    print(f"(f) recorded disposition: {json.dumps(co['verifier_dispositions'][-1])}")
    print(f"(f) shipping: action={ship['action']} steps="
          f"{[s.get('name') for s in ship['steps']]}")
    print(f"(f) outcomes ledger record (+{len(led_recs) - before} rows): "
          f"result={disp['result']} verified={disp['verified']} session={disp['session']}")


def test_a_park_neither_destroys_nor_answers_the_sessions_own_checkpoint(
        tmp_path, claude_shim, deployed_home):
    """THE CONTROL IS THE TEST ABOVE: a park over a session with NO checkpoint of
    its own ends DONE, closeout field None, shipping dry-run. WITH one, two
    authors share one field -- neither may destroy the other, and resolving the
    park must not answer a question it did not ask."""
    claude_shim("clean")
    own = "I could not tell whether the rollback path is safe -- please decide"
    plan_dir = _parked_plan(tmp_path, checkpoint=own)

    # While parked, BOTH are visible: on the closeout and in the human brief.
    parked = json.loads((plan_dir / "_closeouts" / f"{SID}.json").read_text())
    both = parked["human_checkpoint_reason"]
    assert own in both and vpk.reason_for(vpk.CAUSE_NO_OPT_IN) in both, parked
    assert own in _feedback_path(plan_dir, SID).read_text()

    out = vpk.resolve(plan_dir, SID, "verified-on-box", note="read the diff by hand")

    # The gate is satisfied; the SESSION's question is not.
    assert out["verifier_disposition"] == vpk.VERIFIED_ON_BOX, out
    assert out["final_status"] == "AWAITS_REVIEW", out
    assert out["human_checkpoint_reason"] == own, out
    co = json.loads((plan_dir / "_closeouts" / f"{SID}.json").read_text())
    assert co["human_checkpoint_reason"] == own, co
    assert _status(plan_dir, SID) == "AWAITS_REVIEW"
    ship = shp.ship_begin(plan_dir, SID, dry_run=True)
    assert ship["action"] == "deferred", ship
    print(f"\n(g) while parked the closeout carried BOTH -> {both!r}\n"
          f"(g) after VERIFIED-ON-BOX: final_status={out['final_status']} checkpoint"
          f"={co['human_checkpoint_reason']!r} dashboard={_status(plan_dir, SID)}"
          f" shipping={ship['action']}")


#: A second, trivially-passing verify gate. The park in this suite is always the
#: FIRST of the two, which is the shape the bypass needed: resolving it removes the
#: park record while the gate after it has still never run.
SECOND_GATE_ID = "smoke2"
SECOND_GATE = f"gate:{SECOND_GATE_ID}"


def _two_gate_parked_plan(tmp_path):
    plan_dir = _fixture_tree(tmp_path, restricted=False, gate=LLM_GATE_ID,
                             gate2=SECOND_GATE_ID, post_session={"git": "commit"})
    _codex_harness(plan_dir)
    out = _run_the_registry_gate(plan_dir, LLM_GATE)
    assert out["action"] == "awaits-review", out
    return plan_dir


def test_resolving_a_park_leaves_a_later_gate_required_and_refuses_a_plain_ack(
        tmp_path, claude_shim, deployed_home):
    """RESOLVING THE PARK IS NOT FINISHING THE SESSION. Measured before
    the fix: resolve returned run-argv for gate:smoke2, the dashboard stayed at
    AWAITS_REVIEW, and a plain `ack-checkpoint` then marked the session DONE with
    smoke2 never run -- the very bypass the park exists to prevent, one door over."""
    claude_shim("clean")
    plan_dir = _two_gate_parked_plan(tmp_path)
    out = vpk.resolve(plan_dir, SID, "verified-on-box", note="read the diff by hand")

    # The pass CONTINUES: the human satisfied one gate, not the session.
    assert out["action"] == "run-argv" and out["gate"] == SECOND_GATE, out
    st = _state(plan_dir)
    assert st["gate_status"][LLM_GATE] == "passed", st
    assert st["gate_status"][SECOND_GATE] != "passed", st
    assert st.get("outcome") is None, st
    assert _status(plan_dir, SID) != "DONE", _status(plan_dir, SID)
    # Shipping stays DEFERRED -- clearing the closeout checkpoint is what un-defers
    # it, and verification is not complete.
    assert shp.ship_begin(plan_dir, SID, dry_run=True)["action"] == "deferred"
    # ...and the plain ack that used to walk through here is refused BY NAME.
    with pytest.raises(SystemExit) as e:
        run_cli.cmd_ack_checkpoint(plan_dir, SID)
    assert SECOND_GATE in str(e.value) and "INCOMPLETE" in str(e.value), e.value
    assert _status(plan_dir, SID) != "DONE", _status(plan_dir, SID)
    print(f"\n(f2) after resolve: action={out['action']} gate={out['gate']}")
    print(f"(f2) gate_status={st['gate_status']} outcome={st.get('outcome')} "
          f"dashboard={_status(plan_dir, SID)}")
    print(f"(f2) plain ack REFUSED: {str(e.value)[:180]}")

    # And the pass still FINISHES: the remaining gate runs, finalize clears the
    # checkpoint, and shipping proceeds. Deferring it must not strand it.
    ran = vfy.verify_run_argv(plan_dir, SID, SECOND_GATE)
    assert ran["action"] == "passed", ran
    fin = vfy.verify_finalize(plan_dir, SID)
    assert fin["action"] == "done" and fin["final_status"] == "DONE", fin
    co = json.loads((plan_dir / "_closeouts" / f"{SID}.json").read_text())
    assert co["human_checkpoint_reason"] is None, co
    ship = shp.ship_begin(plan_dir, SID, dry_run=True)
    assert ship["action"] == "dry-run" and ship["steps"], ship
    print(f"(f2) remaining gate ran: {ran['action']}; finalize={fin['final_status']}; "
          f"closeout checkpoint={co['human_checkpoint_reason']}; shipping={ship['action']}")


def test_blocked_records_the_disposition_and_ships_nothing(
        tmp_path, claude_shim, deployed_home):
    claude_shim("clean")
    plan_dir = _parked_plan(tmp_path)
    before = len(_ledger_records())
    out = vpk.resolve(plan_dir, SID, "blocked", note="cannot verify this by hand")

    assert out["action"] == "blocked", out
    assert out["verifier_disposition"] == vpk.BLOCKED, out
    assert _status(plan_dir, SID) == "BLOCKED"
    st = _state(plan_dir)
    assert "on_box_review" not in st and st["outcome"] == "halted", st
    assert st["rework_count"] == 0, "a BLOCKED disposition is not a rework charge"
    co = json.loads((plan_dir / "_closeouts" / f"{SID}.json").read_text())
    assert co["verifier_dispositions"][-1]["disposition"] == vpk.BLOCKED, co
    led_recs = _ledger_records()
    assert len(led_recs) == before + 1, led_recs
    disp = led_recs[-1]
    assert disp["result"] == "blocked" and disp["verified"] is False, disp
    # Nothing ships: the checkpoint stands and the plan is halted.
    ship = shp.ship_begin(plan_dir, SID, dry_run=True)
    assert ship["action"] == "deferred", ship
    print(f"\n(f) BLOCKED → verify {out['action']}, dashboard={_status(plan_dir, SID)}, "
          f"rework_count={st['rework_count']}")
    print(f"(f) recorded disposition: {json.dumps(co['verifier_dispositions'][-1])}")
    print(f"(f) outcomes ledger record (+{len(led_recs) - before} rows): "
          f"result={disp['result']} verified={disp['verified']} session={disp['session']}")
    print(f"(f) shipping: action={ship['action']} reason={ship.get('reason')}")


if __name__ == "__main__":       # pragma: no cover
    sys.exit(pytest.main([__file__, "-q", "--no-header"]))
