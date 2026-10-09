"""Unit checks for the llm-review-* gate's findings-block parser.

The fixture harness (`fixtures/llm-review-gate/rerun.sh`) proves the gate
end-to-end against the real reviewer; these are the fast checks that the
classifier itself never turns an unrecognised answer into a pass.
"""
import subprocess

import llm_review_gate as g


def test_low_clean_marker_is_empty_block():
    assert g.classify("(none)")[:2] == ("empty", 0)
    assert g.classify("  none  ")[:2] == ("empty", 0)


def test_low_finding_lines_count():
    out = "sumrange.py:9 — off-by-one\nother.py:12 — leaks a handle"
    assert g.classify(out)[:2] == ("findings", 2)
    # the reviewer sometimes wraps the line in backticks
    assert g.classify("`sumrange.py:9 — off-by-one`")[:2] == ("findings", 1)


def test_json_array_shapes():
    assert g.classify('prose\n\n```json\n[]\n```')[:2] == ("empty", 0)
    assert g.classify('prose\n\n```json\n[{"file": "a.py"}]\n```')[:2] == ("findings", 1)
    assert g.classify('[{"file": "a.py"}, {"file": "b.py"}]')[:2] == ("findings", 2)


def test_unparseable_shapes_are_never_a_pass():
    # These are the INDETERMINATE captures in fixtures/llm-review-gate/.
    for bad in ("", "   ", "The review is complete. Both issues were reported above."):
        verdict, count, _ = g.classify(bad)
        assert verdict == "unparseable", bad
        assert count is None
    assert g.classify(None)[0] == "unparseable"


def test_intent_is_wrapped_in_untrusted_markers():
    # The prompt is PROSE, not a bare slash command. Measured on CLI
    # 2.1.232: `claude -p "/code-review high"` QUEUES the command and never runs
    # it (stalled transcripts held one `queue-operation` line and nothing else;
    # a level-low probe returned num_turns=0 with empty stdout). The old
    # assertions here pinned that dead shape, so they pinned a gate that could
    # only ever time out. fixtures/llm-review-gate/rerun.sh is the end-to-end
    # proof of the replacement: 11/11, planted bug caught at every level.
    p = g.build_prompt("low", "widen the window on purpose")
    # And it must NOT hand the review to the code-review SKILL either. That
    # skill fans finders over the repo root, so the scoped diff is never opened
    # and --scope stops meaning anything (2026-08-23: 11 agents, 426 tool calls,
    # 110M cached tokens, top-read file outside the scope, no verdict at 900s).
    assert "Do NOT invoke the code-review skill" in p
    # The level used to reach the reviewer as that skill's argument. It must
    # still reach it, now as the depth instruction.
    assert g.srf.depth_line("low") in p
    assert "git diff HEAD" in p
    assert "```json" in p, "the parseable output shape must be pinned in the prompt"
    assert g.INTENT_HEADER in p and g.INTENT_FOOTER in p
    assert "widen the window on purpose" in p
    # Intent may DOWNGRADE a finding, never remove it: the verdict is the
    # array's length, so an instruction to drop entries would let a claim of
    # deliberateness clear a real defect.
    assert "ask-user" in p and "NEVER drop a finding" in p
    # No intent -> no markers, and nothing that smuggles an empty intent block in.
    bare = g.build_prompt("medium", "")
    assert g.INTENT_HEADER not in bare and g.INTENT_FOOTER not in bare
    assert "ask-user" not in bare
    assert g.srf.depth_line("medium") in bare and "```json" in bare


def test_exit_codes_are_three_valued():
    assert (g.PASS, g.FINDINGS, g.INDETERMINATE) == (0, 1, 2)


# --------------------------------------------------------------------------
# D1 — a NEW file is part of the change, and `git diff HEAD` does not contain it
# --------------------------------------------------------------------------
# Measured on S07: `render_report.py`, 173 new lines, was untracked and
# therefore absent from the reviewed diff across three rounds that each reported a
# clean PASS. Staging it and reading it found a real bug immediately.
def _repo(tmp_path):
    import subprocess
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / ".gitignore").write_text("build/\n")
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "artifact.o").write_text("x")
    (tmp_path / "brand_new.py").write_text("def f():\n    pass\n")
    return tmp_path


def test_untracked_files_are_enumerated_and_ignored_ones_are_not(tmp_path):
    found, dropped = g.untracked_files(str(_repo(tmp_path)))
    assert "brand_new.py" in found
    assert dropped == []
    assert not any(p.startswith("build/") for p in found), found


def test_a_non_git_tree_is_INDETERMINATE_never_a_pass(tmp_path):
    """The gate cannot name its reviewed surface here, so it refuses. It must not
    even reach the reviewer — a pass computed over an unknown surface is the
    silent-green this whole file exists to prevent."""
    assert g.untracked_files(str(tmp_path)) is None
    assert g.main(["--level", "low", "--cwd", str(tmp_path)]) == g.INDETERMINATE


def test_too_many_untracked_files_is_INDETERMINATE_never_a_pass(tmp_path):
    repo = _repo(tmp_path)
    for i in range(g.UNTRACKED_MAX + 1):
        (repo / f"junk{i}.txt").write_text("x")
    assert g.main(["--level", "low", "--cwd", str(repo)]) == g.INDETERMINATE


def test_main_actually_hands_the_new_files_to_the_reviewer(tmp_path, monkeypatch):
    """The wiring, not just the builder: whatever `untracked_files` finds has to
    reach the prompt the reviewer is actually run with."""
    repo = _repo(tmp_path)
    seen = {}

    def fake_run_once(prompt, cwd, timeout):
        seen["prompt"] = prompt
        return {"result": "```json\n[]\n```"}, ""

    monkeypatch.setattr(g, "run_once", fake_run_once)
    assert g.main(["--level", "low", "--cwd", str(repo)]) == g.PASS
    assert "brand_new.py" in seen["prompt"]
    assert "build/artifact.o" not in seen["prompt"], "an ignored file is not the change"


def test_the_prompt_puts_new_files_in_the_reviewed_surface():
    p = g.build_prompt("low", "", ["skills/routing-retro/scripts/render_report.py"])
    assert "render_report.py" in p
    assert "Review only that diff." not in p, "the diff is no longer the whole surface"
    for needle in ("UNTRACKED", "Read each one in full", "ADDED"):
        assert needle in p, needle
    # The CONTROL: with no new files the prompt is the plain diff instruction, so
    # the assertions above cannot pass on a prompt that always says "UNTRACKED".
    bare = g.build_prompt("low", "")
    assert "Review only that diff." in bare
    assert "UNTRACKED" not in bare


# --------------------------------------------------------------------------
# The committed-work surface. `git diff HEAD` is EMPTY once a
# session commits, so the reviewer was handed nothing and its empty findings
# array read as a PASS. Measured across four sessions of one plan.
# --------------------------------------------------------------------------

def _committed_repo(tmp_path):
    """A repo whose work is COMMITTED: clean working tree, one commit of real
    change on top of a base."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    for cfg in (["user.email", "t@t"], ["user.name", "t"]):
        subprocess.run(["git", "config", *cfg], cwd=tmp_path, check=True)
    (tmp_path / "app.py").write_text("def f():\n    return 1\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=tmp_path, check=True)
    base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp_path,
                          capture_output=True, text=True, check=True).stdout.strip()
    (tmp_path / "app.py").write_text("def f():\n    return 2\n")
    subprocess.run(["git", "commit", "-qam", "the session's work"], cwd=tmp_path, check=True)
    return tmp_path, base


def test_an_empty_surface_is_INDETERMINATE_never_a_pass(tmp_path, monkeypatch):
    """The bug: committed work + no base = nothing reviewed, reported green."""
    repo, _ = _committed_repo(tmp_path)

    def fake_run_once(prompt, cwd, timeout):  # would return a clean verdict
        return {"result": "```json\n[]\n```"}, ""

    monkeypatch.setattr(g, "run_once", fake_run_once)
    assert g.main(["--level", "low", "--cwd", str(repo)]) == g.INDETERMINATE


def test_a_base_ref_puts_the_committed_work_back_in_the_surface(tmp_path, monkeypatch):
    """The fix, and its CONTROL: the SAME repo that returns INDETERMINATE above
    reviews real content once --base names where the session started."""
    repo, base = _committed_repo(tmp_path)
    seen = {}

    def fake_run_once(prompt, cwd, timeout):
        seen["prompt"] = prompt
        # An honest reviewer attests what it read; one file is in this surface.
        return {"result": "REVIEWED_FILES: 1\n```json\n[]\n```"}, ""

    monkeypatch.setattr(g, "run_once", fake_run_once)
    assert g.main(["--level", "low", "--cwd", str(repo), "--base", base]) == g.PASS
    assert ".diff" in seen["prompt"], "the reviewer must be handed the diff FILE"
    assert g.diff_stat(str(repo), base) == ["app.py"]
    # NEGATIVE control: the same call without the base sees nothing at all.
    assert g.diff_stat(str(repo)) == []


def test_an_unresolvable_base_is_INDETERMINATE_never_a_pass(tmp_path, monkeypatch):
    """A typo'd or unfetched ref diffs nothing, which would report green."""
    repo, _ = _committed_repo(tmp_path)
    monkeypatch.setattr(g, "run_once",
                        lambda p, c, t: ({"result": "```json\n[]\n```"}, ""))
    assert g.main(["--level", "low", "--cwd", str(repo),
                   "--base", "deadbeefdeadbeef"]) == g.INDETERMINATE


def test_the_prompt_overrides_a_trailing_sign_off_instruction():
    """Measured: two full reviews of a real 25-file diff found genuine
    defects, then closed with a `Next:`/`Needs you:` sign-off instead of the
    fenced array, and both scored INDETERMINATE. The prompt must tell the
    reviewer that the array outranks any output-style closing line."""
    p = g.build_prompt("medium", "")
    for needle in ("LAST thing in your message".lower(), "overrides", "next:"):
        assert needle in p.lower(), needle


def test_a_base_prompt_still_carries_the_whole_findings_instruction():
    """The bypass the test above could not see. `--base` used to select its
    branch of an `A if base else B` that had the ENTIRE findings-block
    instruction concatenated onto the else-side, so a based run asked for a
    review and never asked for an array. Measured: two 660s reviews
    of the s03 diff answered in prose and were scored INDETERMINATE.

    The no-base call is the control -- it passed throughout the outage."""
    based = g.build_prompt("medium", "", base="791519cc5")
    unbased = g.build_prompt("medium", "")
    for needle in ("fenced array", "empty array", "last thing in your message",
                   "indeterminate", "overrides"):
        assert needle in based.lower(), f"missing from --base prompt: {needle}"
        assert needle in unbased.lower(), f"missing from control prompt: {needle}"
    assert "git diff 791519cc5" in based
    assert "git diff HEAD" in unbased


def test_another_plans_untracked_files_are_not_this_sessions_surface():
    """Measured on s03: 5 of 6 blocking findings were against
    already-EXECUTED evidence scripts of a CLOSED sibling plan and of a finished
    session of this one. The session could not fix them -- editing a script after
    it produced a recorded result destroys the thing that makes it evidence.

    Every keep below is the control: authored code, and the session's OWN
    evidence, must survive the rule that drops the rest."""
    mine, sess = "_plans/example-portability-plan-2026-08-19", "s03"

    drop = {
        "_plans/example-fix-plan-2026-08-15/_evidence/s04/s04_repair.py": "another plan",
        f"{mine}/_evidence/s02i/verify_merge.py": "another session",
        f"{mine}/_verify_state/s03.json": "bookkeeping",
        f"{mine}/_worktrees/g1.json": "bookkeeping",
    }
    for path, why in drop.items():
        assert g._out_of_surface(path, mine, sess), f"should drop ({why}): {path}"

    keep = [
        f"{mine}/_evidence/{sess}/live-proof.md",   # the session's OWN evidence
        "apps/api/app/core/llm_orca.py",            # authored production code
        "docs/operations/note.md",                  # authored prose
        "scripts/_plans_helper.py",                 # not under _plans/ at all
    ]
    for path in keep:
        assert g._out_of_surface(path, mine, sess) is None, f"should keep: {path}"


def test_without_plan_context_the_scoping_rule_does_not_fire():
    """Fail-open on scoping: a gate run with no plan context cannot evaluate
    whose file this is, so it reviews it rather than guessing it away. Only the
    bookkeeping rule -- which needs no context -- still applies."""
    other = "_plans/example-fix-plan-2026-08-15/_evidence/s04/s04_repair.py"
    assert g._out_of_surface(other) is None
    assert g._out_of_surface("_plans/x/_verify_state/s03.json") == "harness bookkeeping"


def test_a_reviewer_that_read_a_different_surface_is_caught():
    """THE DETECTOR for the class that cost model-portability s03 a full cycle.
    The gate named a 33-file range containing six production modules; the
    reviewer answered "the committed range is two documentation files" -- exactly
    `git diff origin/main` -- and its findings looked entirely normal. The only
    thing that gave it away was the count.

    Controls in both directions: an honest full read passes, and a small honest
    drift (generated output folded out of scope) must NOT be called a failure."""
    assert g.surface_attested(33, 33), "an exact read must pass"
    assert g.surface_attested(30, 33), "honest drift must not fail"
    assert not g.surface_attested(2, 33), "reviewing 2 of 33 must be caught"
    assert not g.surface_attested(None, 33), "no attestation is not a pass"
    assert g.surface_attested(None, 0) is True or g.surface_attested(0, 0)


def test_the_attested_count_is_parsed_from_the_reviewer_answer():
    assert g.reviewed_count("prose\nREVIEWED_FILES: 12\n```json\n[]\n```") == 12
    assert g.reviewed_count("REVIEWED_FILES:7") == 7
    # last one wins: the reviewer may quote the instruction before answering it
    assert g.reviewed_count("REVIEWED_FILES: <n>\nREVIEWED_FILES: 4") == 4
    assert g.reviewed_count("no attestation here") is None
    assert g.reviewed_count(None) is None


def test_the_prompt_hands_over_a_file_not_a_git_command():
    """A git command is a range the reviewer can re-derive; a path is not."""
    p = g.build_prompt("medium", "", base="abc123", diff_file="/tmp/x.diff",
                       diff_files=33)
    assert "/tmp/x.diff" in p
    assert "REVIEWED_FILES" in p
    for forbidden in ("do not run `git diff`", "origin/main"):
        assert forbidden in p.lower(), forbidden
    # control: without a diff file the old ref-naming behaviour is unchanged
    assert "git diff abc123" in g.build_prompt("medium", "", base="abc123")


def test_a_new_file_being_untracked_is_never_offered_as_a_defect():
    """A session is reviewed BEFORE the orchestrator commits it, so every file it
    ADDS is untracked at review time. A reviewer that reads that as "not
    committed — a fresh checkout would ModuleNotFoundError" spends a rework
    attempt on the workflow instead of the code; it cost s14 of
    repo-health-that-cannot-lie its last attempt while the file was
    provably not ignored and staged fine."""
    p = g.build_prompt("low", "", ["skills/repo-health/scripts/route_table.py"])
    assert "UNTRACKEDNESS is never itself a finding" in p
    assert "git add" in p and "not a defect" in p
    # the carve-out survives: a file a .gitignore rule really would drop is real
    assert ".gitignore" in p
    # control: no new files, no rule to state
    assert "UNTRACKEDNESS" not in g.build_prompt("low", "")


def test_an_EMPTY_answer_cannot_clear_an_open_prior_through_main(tmp_path, monkeypatch):
    """THE WIRING, not judge(). judge() has always returned fail=True for an
    unanswered prior, but main() short-circuited on `verdict == "empty"` and
    returned PASS before the verdict was used. `[]` is the LIKELY answer, not an
    exotic one: the base prompt asks for an empty array when nothing blocks while
    the prior section asks for one object per prior, and `[]` satisfies the first.
    So a session could clear a HIGH prior by saying nothing (review)."""
    repo = _repo(tmp_path)
    plan = tmp_path / "_plans" / "p"
    (plan / "_verify_state").mkdir(parents=True)
    (plan / "run.ndjson").write_text(
        '{"event": "dispatch_started", "session_ids": ["s01"], "ts": "2026-08-20T00:00:00+00:00"}\n')
    # the prior's file must be IN this attempt's surface, or the (correct)
    # unverifiable rule carries it instead of blocking on it
    prior = {"kind": "finding", "fid": "abc", "file": "brand_new.py", "severity": "high",
             "summary": "unguarded read", "status": "open", "attempt": 1}
    import json as _json
    (plan / "_verify_state" / "s01.low.claude.findings.ndjson").write_text(_json.dumps(prior) + "\n")

    monkeypatch.setattr(g, "run_once", lambda prompt, cwd, timeout: ({"result": "```json\n[]\n```"}, ""))
    rc = g.main(["--level", "low", "--cwd", str(repo),
                 "--plan-dir", str(plan), "--session", "s01"])
    assert rc == g.FINDINGS, "an unanswered open prior must not pass on an empty array"


def test_an_EMPTY_answer_with_NO_priors_still_passes(tmp_path, monkeypatch):
    """The control: dropping the short-circuit must not turn a genuinely clean
    round into a failure. judge() returns fail=False when there is nothing open."""
    repo = _repo(tmp_path)
    plan = tmp_path / "_plans" / "p"
    (plan / "_verify_state").mkdir(parents=True)
    (plan / "run.ndjson").write_text(
        '{"event": "dispatch_started", "session_ids": ["s01"], "ts": "2026-08-20T00:00:00+00:00"}\n')
    monkeypatch.setattr(g, "run_once", lambda prompt, cwd, timeout: ({"result": "```json\n[]\n```"}, ""))
    assert g.main(["--level", "low", "--cwd", str(repo),
                   "--plan-dir", str(plan), "--session", "s01"]) == g.PASS


def test_the_attested_count_asks_for_the_SAME_number_the_gate_measures():
    """`expected = len(changed) + len(new_files)`, but the prompt announced only
    the diff count and asked for "how many CHANGED files you read". A reviewer
    that read everything and reported the diff's number failed the tolerance once
    untracked files exceeded about a third of the surface — UNTRACKED_MAX is 60,
    so it is reachable. That fails an HONEST review, costing an attempt while
    never passing bad code (review)."""
    p = g.build_prompt("low", "", ["a/new.py", "b/new.py"], base="HEAD",
                       diff_file="/tmp/x.diff", diff_files=7)
    assert "= 9 in total" in p                 # 7 changed + 2 new, the measured number
    assert "IN TOTAL" in p
    assert "how many changed files you read" not in p
    # control: with no new files the announcement stays the plain diff count
    bare = g.build_prompt("low", "", [], base="HEAD", diff_file="/tmp/x.diff", diff_files=7)
    assert "in total" not in bare and "7 changed file(s)" in bare


def test_two_review_LEVELS_in_one_session_do_not_share_a_ledger(tmp_path, monkeypatch):
    """`land` re-runs the UNION of every session's gates, so one session id can
    carry both llm-review-low and llm-review-medium. On a shared ledger the second
    gate read the first one's surface record, found no file changed since it, and
    returned INDETERMINATE in under 1.5s without reviewing anything -- measured on
    four consecutive land runs, 2026-08-23. The ledger is keyed per level."""
    repo = _repo(tmp_path)
    plan = tmp_path / "_plans" / "p"
    (plan / "_verify_state").mkdir(parents=True)
    (plan / "run.ndjson").write_text(
        '{"event": "dispatch_started", "session_ids": ["land"], "ts": "2026-08-20T00:00:00+00:00"}\n')
    monkeypatch.setattr(g, "run_once", lambda prompt, cwd, timeout: ({"result": "```json\n[]\n```"}, ""))

    argv = ["--cwd", str(repo), "--plan-dir", str(plan), "--session", "land"]
    assert g.main(["--level", "low"] + argv) == g.PASS
    assert g.main(["--level", "medium"] + argv) == g.PASS, (
        "the second LEVEL must review, not inherit the first level's surface")

    led = plan / "_verify_state"
    assert (led / "land.low.claude.findings.ndjson").exists()
    assert (led / "land.medium.claude.findings.ndjson").exists()
    # Known negative for the same guard: one level re-reading ITS OWN unchanged
    # surface must still refuse, or this test would pass on a gate that simply
    # never converges.
    import llm_review_ledger as ledger
    ctx = ledger.context(str(plan), "land.low.claude", str(repo), ["brand_new.py"])
    assert ctx.attempt == 2 and ledger.narrow_to_delta(ctx, [], [])[0] == set()
