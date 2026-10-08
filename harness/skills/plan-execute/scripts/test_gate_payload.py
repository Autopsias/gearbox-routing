"""Change D (payload discipline) and Change A (gate durations).

Every test here has been neuter-probed by hand -- the code was broken one
way per test and the matching test was confirmed to fail on a real assertion.
There is NO neuters module; do not cite one (this line used to name a file
that was never in the tree -- caught by review, 2026-08-23).
"""

import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import gate_timing as gt          # noqa: E402
import llm_review_gate as g       # noqa: E402
import llm_review_surface as srf  # noqa: E402

EV = "_plans/p/_evidence/s09b"
LOG = f"{EV}/neuter-probes.txt"
SH = f"{EV}/run_probes.sh"
PY = f"{EV}/show_refusals.py"
MOD = "evidence_proof.py"


def _git(tmp_path):
    e = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
         "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "base"],
                   cwd=tmp_path, check=True, env=e)


def _tree(tmp_path):
    """The s09b shape: two logs, one executable probe, two real modules."""
    (tmp_path / EV).mkdir(parents=True)
    for rel, body in ((LOG, "log line\n" * 50), (f"{EV}/real-plan-probe.txt", "probe\n" * 20),
                      (SH, "#!/bin/sh\necho hi\n"), (PY, "x = 1\n"), (MOD, "y = 2\n")):
        (tmp_path / rel).write_text(body)
    os.chmod(tmp_path / SH, 0o755)
    return [LOG, f"{EV}/real-plan-probe.txt", SH, PY, MOD]


# --------------------------------------------------------------- change D
def test_captured_logs_leave_the_deep_read_list(tmp_path):
    files = _tree(tmp_path)
    source, artifacts = srf.split_untracked(str(tmp_path), files)
    assert artifacts == [LOG, f"{EV}/real-plan-probe.txt"]
    assert source == [SH, PY, MOD]


def test_an_executable_artifact_is_still_source(tmp_path):
    """The s09b review found a real data-loss bug in a run_probes.sh. A rule
    that excluded the evidence DIRECTORY would have lost that finding."""
    _tree(tmp_path)
    assert not srf.is_captured_output(str(tmp_path), SH)
    assert not srf.is_captured_output(str(tmp_path), PY)


def test_an_executable_file_is_source_even_with_a_log_extension(tmp_path):
    """The extension rule alone would swallow a probe script someone named
    `.txt`. The executable bit is the backstop, and this is the ONLY case that
    reaches it -- `.sh`/`.py` are already kept by extension."""
    (tmp_path / EV).mkdir(parents=True)
    rel = f"{EV}/probe.txt"
    p = tmp_path / rel
    p.write_text("#!/bin/sh\necho hi\n")
    assert srf.is_captured_output(str(tmp_path), rel) is True
    os.chmod(p, 0o755)
    assert srf.is_captured_output(str(tmp_path), rel) is False


def test_the_prompt_reads_source_in_full_and_only_lists_the_logs(tmp_path):
    files = _tree(tmp_path)
    block = srf.untracked_block(str(tmp_path), files)
    deep, listed = block.split("CAPTURED RUN OUTPUT", 1)
    # the deep-read instruction covers the source files and none of the logs
    assert "Read each one in full" in deep
    for p in (SH, PY, MOD):
        assert p in deep
    assert LOG not in deep
    # the logs are still VISIBLE, with their size, and explicitly not deep-read
    assert "DO NOT read them in full" in listed
    assert LOG in listed and "bytes)" in listed


def test_nothing_is_hidden_every_untracked_path_appears(tmp_path):
    files = _tree(tmp_path)
    block = srf.untracked_block(str(tmp_path), files)
    for p in files:
        assert p in block, f"{p} vanished from the surface silently"


def test_the_banner_resolve_surface_prints_excludes_captured_output(capsys, tmp_path):
    """Through resolve_surface, not surface_size -- the call site is the change.
    Source here is 4 lines; the two logs add 70 more that nobody is asked to read."""
    _tree(tmp_path)
    _git(tmp_path)
    out = srf.resolve_surface("low", str(tmp_path), None)
    assert out[0] is not None, out
    printed = capsys.readouterr().out
    assert "surface size = 3 file(s), +4/-0" in printed, printed
    assert "CAPTURED RUN OUTPUT" in printed
    assert "not counted toward the surface size" in printed


def test_captured_output_does_not_count_toward_the_detection_band(tmp_path):
    """The band models READ effort. Counting machine output pushed s09b a whole
    band down (1,116 lines ~28% vs 823 lines of source ~42%)."""
    files = _tree(tmp_path)
    _git(tmp_path)
    source, _ = srf.split_untracked(str(tmp_path), files)
    with_logs = srf.surface_size(str(tmp_path), None, (), files)
    src_only = srf.surface_size(str(tmp_path), None, (), source)
    assert src_only is not None and with_logs is not None
    assert src_only[0] < with_logs[0]      # fewer files
    assert src_only[1] < with_logs[1]      # fewer added lines


def test_build_prompt_still_has_no_new_files_section_when_there_are_none():
    assert "UNTRACKEDNESS" not in g.build_prompt("low", "")


# --------------------------------------------------------------- change A
def test_gate_completed_carries_a_duration_and_the_per_attempt_split(tmp_path):
    t0 = gt.now()
    assert gt.log_gate(str(tmp_path), "s09b", "llm-review-medium", t0,
                       "indeterminate", [1800000, 331000]) is True
    recs = [json.loads(ln) for ln in (tmp_path / "run.ndjson").read_text().splitlines()]
    ev = [r for r in recs if r["event"] == "gate_completed"]
    assert len(ev) == 1
    assert ev[0]["duration_ms"] >= 0
    assert ev[0]["gate"] == "llm-review-medium"
    assert ev[0]["outcome"] == "indeterminate"
    assert ev[0]["session_ids"] == ["s09b"]
    # the silent-retry signature: attempt 1 at the wall limit, attempt 2 fresh
    assert ev[0]["attempt_ms"] == [1800000, 331000]


def test_gate_completed_carries_the_reviewer_cost_per_attempt(tmp_path):
    ms, usd = [], []
    gt.record_attempt(gt.now(), None, ms, usd)                       # attempt died
    gt.record_attempt(gt.now(), {"total_cost_usd": 0.1834}, ms, usd)  # claude -p
    gt.record_attempt(gt.now(), {"result": "[]"}, ms, usd)           # codex: no cost
    gt.record_attempt(gt.now(), {"total_cost_usd": "0.2"}, ms, usd)  # wrong type
    assert len(ms) == 4 and usd == [None, 0.1834, None, None]
    assert gt.log_gate(str(tmp_path), "s01", "llm-review-low", gt.now(), "passed", ms, usd)
    ev = json.loads((tmp_path / "run.ndjson").read_text().splitlines()[-1])
    assert ev["attempt_usd"] == [None, 0.1834, None, None]


def test_the_claude_reviewer_is_pinned_and_never_inherits_the_session_model(monkeypatch):
    seen = {}

    def fake_run(argv, cwd, timeout):
        seen["argv"] = argv
        raise OSError("not launched")

    monkeypatch.setattr(g.pg, "run", fake_run)
    g.run_once("p", ".", 1)
    i = seen["argv"].index("--model")
    assert seen["argv"][i + 1] == "opus"


def test_no_plan_dir_logs_nothing_rather_than_raising():
    assert gt.log_gate("", "s01", "llm-review-low", gt.now(), "passed", []) is False


def test_instrumentation_never_decides_a_gate(tmp_path):
    """An unwritable journal must not turn a real verdict into a crash."""
    bad = tmp_path / "nope"
    bad.write_text("i am a file, not a directory")
    assert gt.log_gate(str(bad), "s01", "llm-review-low", gt.now(), "passed", []) is False


# --- the review must happen in ONE session, at the level it was asked for ----
# Measured 2026-08-23. The prompt used to say `Invoke the code-review skill
# (Skill tool, skill="code-review", args="<level>")`. That skill dispatches
# finders against the REPO ROOT, so on a 5-file scoped review it opened the
# diff file zero times, read `run.py` 47 times (outside the scope), spent 426
# tool calls and 110M cached tokens across 11 agents, and returned no verdict
# at the 900s timeout. Both attempts. The level was that call's only argument,
# so removing the call would have made every level review identically.

def test_the_prompt_never_hands_the_review_to_the_fan_out_skill():
    for level in ("low", "medium", "high"):
        p = g.build_prompt(level, "")
        assert "Do NOT invoke the code-review skill" in p, level
        assert "do NOT dispatch subagents" in p, level


def test_each_level_still_buys_a_different_review():
    seen = {srf.depth_line(lv) for lv in ("low", "medium", "high")}
    assert len(seen) == 3, "the level must change the prompt, or it buys nothing"
    for level in ("low", "medium", "high"):
        assert srf.depth_line(level) in g.build_prompt(level, "")


def test_an_unknown_level_reviews_at_the_strictest_depth():
    # Never an empty string and never a silent shallow default: an unrecognised
    # level must not quietly buy a cheaper review than the caller asked for.
    assert srf.depth_line("bogus") == srf.depth_line("high")
    assert srf.depth_line("bogus").strip()


# --- the artifact split must never hide source -------------------------------

def test_a_build_file_is_source_even_though_txt_is_a_receipt_extension(tmp_path):
    # Found by this gate's own review of this change, 2026-08-23, and
    # reproduced: `.txt` is a receipt extension AND a source extension, so the
    # extension test alone listed a new dependency pin instead of reviewing it.
    for name in ("requirements.txt", "constraints.txt", "CMakeLists.txt"):
        (tmp_path / name).write_text("pinned==1.0\n")
        assert not srf.is_captured_output(str(tmp_path), name), name


def test_a_receipt_under_evidence_is_still_listed_not_read(tmp_path):
    ev = tmp_path / "_plans" / "p" / "_evidence" / "s01"
    ev.mkdir(parents=True)
    (ev / "probe.txt").write_text("captured\n")
    rel = "_plans/p/_evidence/s01/probe.txt"
    assert srf.is_captured_output(str(tmp_path), rel)


def test_an_executable_under_evidence_is_source(tmp_path):
    # The s09b data-loss bug lived in a run_probes.sh inside _evidence/.
    ev = tmp_path / "_plans" / "p" / "_evidence" / "s01"
    ev.mkdir(parents=True)
    f = ev / "run_probes.txt"
    f.write_text("#!/bin/sh\n")
    f.chmod(0o755)
    assert not srf.is_captured_output(str(tmp_path), "_plans/p/_evidence/s01/run_probes.txt")
