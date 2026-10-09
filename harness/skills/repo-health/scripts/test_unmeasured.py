"""One defect family: SOMETHING EMPTY OR NULL TREATED AS SOMETHING MEASURED.

Ten instances were found across this collector by three independent
reviews — an empty vendor manifest, a null status, a tier outside the
vocabulary, zero files left after a filter, an unvalidated status the operator
typed, a probe that could not reach its feed, a tracked workflow that would not
open, a tracked manifest that would not open, an unreachable `gh`, and three
git calls that failed. Every one made a check report a result it had not taken.
That is the defect this whole skill exists to eliminate, and it got in while
closing a coverage gap.

Fixing them one at a time is what ships the next one, so the family has THREE
shared guards and every site routes through them:

* `common.certified` — the NULL half. A field outside its vocabulary is never
  repaired into a verdict; the check is marked NOT MEASURED and says which value
  could not be read. Every check passes through it at birth (common.check) and
  again when read back (card.load_card).
* `common.check(..., measured=, nothing=)` — the EMPTY half. A conclusive status
  over a population of zero is refused and becomes `na`. Any check whose verdict
  comes from a list passes its population.
* `common.read_or_none` + `common.unmeasured` — the UNREAD half. "Could not open"
  stops being spelled the same as "empty", and a check that read only part of its
  input names what it missed and never stands as `pass`.

Their siblings in other vocabularies keep the same rule: `vendor.load` measures
the manifest against the trees actually on DISK, `vendor.check` exits 2 when
upstream could not be reached, and `probe_checks.could_not_look` records a probe
that never reached its feed as GAP ("we did not look") rather than ERROR ("the
gate is broken"). Those live with their own tests, in test_type_guards.py and
test_probe_checks.py; the end-to-end proof for the unread-input half is in
probe_cases.py, where the plants are a tracked file removed from the worktree.

Every test here was confirmed to fail against the pre-fix code, and each guard is
fed a known positive PER SHAPE — explicit null, misspelt value, missing key,
non-dict, empty population, unreadable member — plus a known NEGATIVE, so a guard
that simply swallowed everything would be caught too.
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import card as cardmod  # noqa: E402
import common  # noqa: E402
import health  # noqa: E402
import quality_checks  # noqa: E402
import health_render as render  # noqa: E402
import routes  # noqa: E402
from test_health_shape import card, make_repo  # noqa: E402


# ---------- the null half: certified() ----------

def test_an_explicit_null_status_is_NOT_a_measured_result():
    """The exact hole: `{**CHECK_FIELDS, **c}` fills a MISSING key, not a null one.

    A null status left the pending list, skipped the score cap, read green on the
    dashboard, then killed render with `KeyError: None`.
    """
    assert {**common.CHECK_FIELDS, **{"status": None}}["status"] is None, \
        "the merge this replaced does not override an explicit null"
    c = common.certified({"id": "x", "status": None, "tier": "blocking",
                          "layer": "security"})
    assert c["status"] == "pending"
    assert "NOT MEASURED" in c["detail"]


@pytest.mark.parametrize("status", ["passs", "", "PASS", 0, [], {"a": 1}])
def test_a_status_outside_the_vocabulary_is_NOT_a_measured_result(status):
    c = common.certified({"id": "x", "status": status, "tier": "advisory",
                          "layer": "security", "detail": "planted"})
    assert c["status"] == "pending", f"{status!r} was accepted as a verdict"
    assert "UNREADABLE" in c["detail"] and "planted" in c["detail"]


def test_certified_leaves_a_REAL_status_alone():
    """The known negative: a guard that rewrote everything would pass the tests
    above and destroy every real verdict on the card."""
    for status in common.STATUSES:
        c = common.certified({"id": "x", "status": status, "tier": "blocking",
                              "layer": "security", "detail": "kept"})
        assert (c["status"], c["detail"]) == (status, "kept")


@pytest.mark.parametrize("raw,want", [
    ({}, common.CHECK_FIELDS),
    ("not a dict", common.CHECK_FIELDS),
    (None, common.CHECK_FIELDS),
])
def test_a_check_that_is_not_a_dict_still_produces_a_TOTAL_record(raw, want):
    assert common.certified(raw) == want


def test_a_non_string_text_field_is_made_readable_not_left_to_crash():
    """`": " in c["detail"]` and esc() both assume a str; render died on a list."""
    c = common.certified({"id": 7, "status": "pass", "tier": "advisory",
                          "layer": "security", "detail": ["a", "b"], "fix": None})
    assert isinstance(c["detail"], str) and isinstance(c["id"], str)
    assert c["fix"] == ""


# ---------- the null half, end to end ----------

def collected(tmp_path, name="nullstatus"):
    repo = make_repo(tmp_path, name, {"README.md": "# demo\n",
                                      "src/app.py": "def f():\n    return 1\n"})
    health.collect(repo, fresh=True)
    return repo


def test_a_null_status_on_a_stored_card_never_reads_as_a_recorded_probe(tmp_path):
    """recorded_probes() dropped its null guard when it moved into card.py, so a
    null-status check counted as measured and its probe was never re-run."""
    repo = collected(tmp_path)
    raw = card(repo)
    for c in raw["checks"]:
        if c["id"] == "sec.secrets-history":
            c["status"] = None
    cardmod.save_card(repo, raw)
    kept = cardmod.recorded_probes(repo, raw["tree"])
    assert "sec.secrets-history" not in kept, "a null status counted as a result"


def test_a_null_status_renders_and_caps_the_score_instead_of_crashing(tmp_path):
    """It used to be `KeyError: None` out of board_html — and before that, a
    green dashboard, because a null status is in neither the pending list nor
    the fail list."""
    repo = collected(tmp_path, "nullrender")
    raw = card(repo)
    raw["checks"].append({"id": "sec.invented", "layer": "security",
                          "tier": "blocking", "title": "Invented", "status": None,
                          "detail": "planted"})
    cardmod.save_card(repo, raw)
    render.render(repo)                       # used to raise KeyError: None
    checks = health.load_card(repo)["checks"]
    score, note = render.overall_score(checks)
    assert note, "an unreadable blocking check left the score uncapped"
    assert (health.health_dir(repo) / "HEALTH.html").exists()


def test_an_unrecognised_tier_weighs_as_blocking_and_does_not_crash_render(tmp_path):
    """`WEIGHT['']` was a straight KeyError; `tier == "blocking"` was worse —
    silent, and it let an untiered failure skip the score cap."""
    assert "" not in render.WEIGHT, "the KeyError this replaced"
    bad = common.certified({"id": "x", "layer": "security", "tier": "",
                            "status": "fail", "title": "t"})
    assert render.blocking(bad), "an unreadable tier bought the lighter weight"
    assert render.overall_score([bad])[0] <= 59
    repo = collected(tmp_path, "notier")
    raw = card(repo)
    raw["checks"].append({"id": "sec.invented", "layer": "security", "tier": "",
                          "title": "Untiered", "status": "pass", "detail": "planted"})
    cardmod.save_card(repo, raw)
    render.render(repo)                       # used to raise KeyError: ''


def test_the_operator_cannot_record_a_status_that_does_not_exist(tmp_path):
    """`health.py set cq.lint passs` wrote the typo straight into the card, where
    it scored as neither pass nor fail and took the next render down."""
    repo = collected(tmp_path, "badset")
    with pytest.raises(SystemExit) as e:
        health.set_result(repo, "cq.lint", "passs", "typo")
    assert "unknown status" in str(e.value)
    assert {c["id"]: c["status"] for c in card(repo)["checks"]}["cq.lint"] == "pending"
    health.set_result(repo, "cq.lint", "pass", "really ran")   # the known negative
    assert {c["id"]: c["status"] for c in card(repo)["checks"]}["cq.lint"] == "pass"


# ---------- the empty half: check(measured=) ----------

@pytest.mark.parametrize("population", [0, [], ()])
def test_a_verdict_over_an_empty_population_is_refused(population):
    c = common.check("x.y", "security", "advisory", "Title", "pass",
                     "nothing found", measured=population, nothing="looked at zero")
    assert (c["status"], c["detail"], c["fix"]) == ("na", "looked at zero", "")


@pytest.mark.parametrize("status", ["pass", "warn", "fail"])
def test_every_conclusive_status_is_refused_over_nothing(status):
    assert common.check("x.y", "security", "advisory", "T", status,
                        "d", measured=[])["status"] == "na"


def test_a_verdict_over_a_REAL_population_is_left_alone():
    """The known negative. `measured` omitted must also change nothing, or every
    check that does not measure a list would silently turn into `na`."""
    for measured in (1, ["a"], None):
        c = common.check("x.y", "security", "advisory", "T", "pass", "d", "fx",
                         measured=measured, nothing="unused")
        assert (c["status"], c["detail"], c["fix"]) == ("pass", "d", "fx"), measured


def test_na_and_pending_are_not_touched_by_the_population_gate():
    """Neither claims a measurement, so neither needs one."""
    for status in ("na", "pending"):
        assert common.check("x.y", "security", "advisory", "T", status, "d",
                            measured=0)["detail"] == "d"


# ---------- the empty half, in the two checks it was found in ----------

JUNK_ONLY = {"README.md": "# demo\n", "build/app.py": "x = 1\n" * 501,
             ".venv/lib/site.py": "def f():\n" + "    y = 1\n" * 200}


def test_the_size_checks_refuse_to_speak_for_a_repo_they_never_read(tmp_path):
    """Both gated on a list they then filtered down to nothing inside the loop.

    A repo whose only tracked source is build output read "no oversized source
    files" and "no function over 100 lines" — over ZERO files opened, and with a
    501-line file and a 201-line function sitting right there.
    """
    repo = make_repo(tmp_path, "junkonly", JUNK_ONLY)
    health.collect(repo, fresh=True)
    got = {c["id"]: c for c in card(repo)["checks"]}
    for cid in ("cq.file-size", "cq.function-length"):
        assert got[cid]["status"] == "na", f"{cid}: {got[cid]['detail']}"
        assert "scope" in got[cid]["detail"] or "tracked" in got[cid]["detail"]


def test_a_file_the_size_check_cannot_read_is_named_not_skipped(tmp_path):
    """A file in scope that will not open was `continue`d past in silence, and
    the check still reported over the ones that did open."""
    repo = make_repo(tmp_path, "unreadable", {"README.md": "# demo\n",
                                              "ok.py": "x = 1\n"})
    (repo / "gone.py").symlink_to("no-such-target")
    got = quality_checks.cq_file_size(repo, ["ok.py", "gone.py"])
    assert got["status"] == "warn"
    assert "could not be read" in got["detail"] and "gone.py" in got["detail"]
    # and with NOTHING readable in scope it is `pending`, not a measured `na`
    none = quality_checks.cq_file_size(repo, ["gone.py"])
    assert none["status"] == "pending", none


# ---------- the unread half: read_or_none / unmeasured ----------

def test_could_not_open_stops_being_spelled_the_same_as_empty(tmp_path):
    (tmp_path / "there").write_text("")
    assert common.read_or_none(tmp_path, "there") == ""
    assert common.read_or_none(tmp_path, "gone") is None
    assert common.read(tmp_path, "gone") == "", "read() keeps its old contract"
    texts, unread = common.read_files(tmp_path, ["there", "gone"])
    assert texts == {"there": ""} and unread == ["gone"]


def test_a_clean_reading_over_a_PARTIAL_input_never_stands_as_pass():
    clean = common.check("x.y", "security", "blocking", "T", "pass", "nothing found")
    got = common.unmeasured(clean, ["read.yml"], ["a.yml"], " (1 not read)")
    assert (got["status"], got["detail"]) == ("warn", "nothing found (1 not read)")
    assert common.unmeasured(clean, ["read.yml"], [], " (never)") == clean, \
        "the known negative"


def test_NOTHING_read_is_pending_and_never_na(tmp_path):
    """`na` is CLEAR to routes.fix_rows, so routing "we could not read any of it"
    there made three BLOCKING security checks vanish from the fix list — the
    silent all-clear this skill exists to prevent, reintroduced by the first fix
    for this very family (found by the second review).

    Not `dispatch: probe` (round 3): `unmeasured()` stamps `unread`
    on this exact branch, and `fix_rows` reads it to route a "could not read X"
    DIAGNOSIS to `unreadable()` rather than to a probe command that does not
    exist for a deterministic check — see test_routes.py for the reproduction.
    """
    clean = common.check("x.y", "security", "blocking", "T", "pass", "nothing found")
    got = common.unmeasured(clean, [], ["a.yml", "b.yml"], " (none opened)")
    assert got["status"] == "pending", "an unread check read as a measured `na`"
    assert got["status"] not in routes.CLEAR
    rows = list(routes.fix_rows({"checks": [got]}))
    assert len(rows) == 1, "a check that measured nothing produced no row at all"
    assert rows[0]["dispatch"] != "probe", \
        "a deterministic check with no probe was told to run one"
    assert "a.yml" in rows[0]["prompt"] and "b.yml" in rows[0]["prompt"]


def test_a_finding_keeps_its_own_status_when_part_of_the_input_was_unread():
    """warn must not be promoted, and fail must not be demoted — the rule only
    refuses an unearned `pass`. A measured finding is never traded away to
    report an unmeasured input: hyg.unmerged-work hid a real stash that way."""
    for status in ("warn", "fail", "na", "pending"):
        c = common.check("x.y", "security", "blocking", "T", status, "d")
        assert common.unmeasured(c, ["read"], ["a"], " (+)")["status"] == status


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads a chmod 000 file anyway")
def test_a_recorded_PROBE_result_never_stands_in_for_a_deterministic_check(tmp_path):
    """collect() carried any `pending` check's recorded result across an unchanged
    tree. Deterministic checks can now come out `pending` too (they read nothing
    they needed), so that rule would have let the PREVIOUS run's `pass` — taken
    when the file could still be opened — stand in for a measurement this run
    could not take. The stale-artifact shape, one door further in.
    """
    wf = ("name: ci\non: [push]\npermissions:\n  contents: read\njobs:\n  t:\n"
          "    runs-on: ubuntu-latest\n    timeout-minutes: 5\n    steps:\n"
          "      - run: echo hi\n")
    repo = make_repo(tmp_path, "carry", {"README.md": "# demo\n",
                                         ".github/workflows/ci.yml": wf})
    health.collect(repo, fresh=True)
    assert {c["id"]: c["status"] for c in card(repo)["checks"]}["sec.action-pinning"] \
        == "pass"
    # chmod, not delete: the tree hash must stay EQUAL or preservation never fires
    (repo / ".github" / "workflows" / "ci.yml").chmod(0)
    try:
        health.collect(repo)                       # not --fresh: carry-over is live
        got = {c["id"]: c for c in card(repo)["checks"]}["sec.action-pinning"]
    finally:
        (repo / ".github" / "workflows" / "ci.yml").chmod(0o644)
    assert got["status"] == "pending", "last run's pass stood in for this one"
    assert "could not be read" in got["detail"]


# ---------- the empty half, in the checks that read git ----------

def test_a_git_call_that_FAILS_is_never_reported_as_a_clean_reading(monkeypatch):
    """Three hygiene checks defaulted a failed `run()` to an empty result and
    printed it as a measurement: "0 TODO/FIXME markers", "last commit -1 days
    ago", "no unmerged local branches, no stashes" — each beside a green pass.
    """
    monkeypatch.setattr(health, "run", lambda cmd, cwd: (128, "fatal: not a repo"))
    monkeypatch.setattr(health, "tracked_files", lambda repo: [])
    got = {c["id"]: c for c in health.hyg_docs(Path("/nonexistent"))}
    for cid in ("hyg.todo-density", "hyg.activity", "hyg.unmerged-work"):
        # `pending`, not `na`: nobody measured these, and `na` is CLEAR.
        assert got[cid]["status"] == "pending", f"{cid}: {got[cid]['detail']}"
        assert "never" in got[cid]["detail"] or "could not" in got[cid]["detail"]


@pytest.mark.parametrize("rc,want", [(128, "pending"), (0, "na")])
def test_a_failed_git_remote_is_not_the_same_answer_as_no_remote(monkeypatch, rc, want):
    """`rc != 0 or not out.strip()` printed the same confident "no git remote —
    nothing is published from this repo" for a git call that never answered.
    Measured "does not apply" is `na`; unanswered is `pending`."""
    monkeypatch.setattr(health, "run", lambda cmd, cwd: (rc, ""))
    c = health.ci_server_gate(Path("/nonexistent"), {"workflows": [], "hooks": False})
    assert c["status"] == want
    assert ("never established" in c["detail"]) == (rc != 0), c["detail"]


def test_a_failed_git_log_does_not_claim_there_is_nothing_to_review(monkeypatch):
    """sec.diff-review's `na` said "no commits in the last 30 days" whenever git
    failed — a measured-sounding sentence over an unanswered question, and `na`
    also dropped the check out of fix-routes. It stays `pending`: nobody looked,
    and the row carries the command that would settle it."""
    import probes
    monkeypatch.setattr(probes, "run", lambda cmd, cwd: (128, "fatal: not a repo"))
    shape = {"py": [], "js": [], "workflows": [], "manifests": [], "locks": [],
             "hooks": False}
    got = {c["id"]: c for c in probes.pending_checks(Path("/nonexistent"), shape)}
    assert got["sec.diff-review"]["status"] == "pending"
    assert "no commits" not in got["sec.diff-review"]["detail"]


def test_git_grep_finding_NOTHING_is_still_a_real_zero(monkeypatch):
    """The known negative: `git grep -c` exits 1 when there are no matches, and
    turning that into `na` would lose a check that legitimately passes."""
    monkeypatch.setattr(health, "run", lambda cmd, cwd: (1, ""))
    monkeypatch.setattr(health, "tracked_files", lambda repo: [])
    got = {c["id"]: c for c in health.hyg_docs(Path("/nonexistent"))}
    assert got["hyg.todo-density"]["status"] == "pass"
    assert "0 TODO/FIXME markers" in got["hyg.todo-density"]["detail"]


# ---------- the two siblings review found in the fix itself ----------

def test_a_null_or_list_layer_does_not_kill_the_whole_render():
    """certified() guarded null status and bad tier and called a bad layer 'a
    display gap'. It was `AttributeError: 'NoneType'.lower()` out of
    render.finding_html -- exit 1, no dashboard. Same shape, same guard."""
    for bad in (None, ["a", "b"], {"x": 1}):
        c = common.certified({"id": "x", "title": "t", "status": "fail", "tier": "blocking",
                              "layer": bad, "detail": "d"})
        assert isinstance(c["layer"], str)
        html = render.finding_html(c)          # must not raise
        assert "x" in html


def _unopenable(path):
    """chmod 0 a file for the duration of a with-block (restored after)."""
    import contextlib
    @contextlib.contextmanager
    def _cm():
        path.chmod(0)
        try:
            yield
        finally:
            path.chmod(0o644)
    return _cm()


def test_an_unopenable_Makefile_leaves_test_suite_PENDING_not_na(tmp_path):
    """`read()` read a chmod-0 Makefile as "" -> "no test suite detected" -> `na`,
    and `na` is in routes.CLEAR, so a BLOCKING check left the fix-routes board on
    a sentence that was false. Present-and-unopenable is "could not measure"."""
    repo = make_repo(tmp_path, "mk", {"Makefile": "test:\n\tpytest -q\n", "a.py": "x = 1\n"})
    with _unopenable(repo / "Makefile"):
        health.collect(repo, fresh=True)
        by = {c["id"]: c for c in card(repo)["checks"]}
    assert by["test.suite"]["status"] == "pending", by["test.suite"]
    assert "Makefile" in by["test.suite"]["detail"]
    assert by["ai.verify-command"]["status"] == "pending", by["ai.verify-command"]
    # and it stays on the fix-routes board (na would have dropped it)
    ids = {r["id"] for r in routes.fix_rows(card(repo))}
    assert "test.suite" in ids


def test_an_unopenable_agents_doc_and_readme_are_unmeasured_not_thin(tmp_path):
    repo = make_repo(tmp_path, "docs", {"CLAUDE.md": "run `make test`\n" * 3,
                                        "README.md": ("x\n" * 30) + "```sh\nmake\n```\n",
                                        "a.py": "x = 1\n"})
    with _unopenable(repo / "CLAUDE.md"), _unopenable(repo / "README.md"):
        health.collect(repo, fresh=True)
        by = {c["id"]: c for c in card(repo)["checks"]}
    assert by["ai.agents-md"]["status"] == "pending", by["ai.agents-md"]
    assert "CLAUDE.md" in by["ai.agents-md"]["detail"]
    assert by["hyg.readme"]["status"] == "pending", by["hyg.readme"]
    assert "README.md" in by["hyg.readme"]["detail"]


def test_an_ABSENT_Makefile_is_still_na_not_pending(tmp_path):
    """The control: absent is a measured answer. Only present-and-unopenable is not."""
    repo = make_repo(tmp_path, "nomk", {"a.py": "x = 1\n"})
    health.collect(repo, fresh=True)
    by = {c["id"]: c for c in card(repo)["checks"]}
    assert by["test.suite"]["status"] == "na", by["test.suite"]


# ---------- the two the reviewer found IN the fix (round 3) ----------

def _git(repo, *a):
    import subprocess
    subprocess.run(["git", *a], cwd=repo, check=True, capture_output=True)


def test_a_dangling_symlink_Makefile_is_unmeasured_not_absent(tmp_path):
    """`Path.exists()` follows symlinks: a tracked Makefile that is a dangling
    link took the ABSENT path and read as "no test suite". Present means on
    disk as anything (lexists) or tracked."""
    repo = make_repo(tmp_path, "dl", {"a.py": "x = 1\n"})
    os.symlink("no-such-target", repo / "Makefile")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "link")
    health.collect(repo, fresh=True)
    by = {c["id"]: c for c in card(repo)["checks"]}
    assert by["test.suite"]["status"] == "pending", by["test.suite"]
    assert "Makefile" in by["test.suite"]["detail"]


def test_a_tracked_Makefile_unlinked_from_the_worktree_is_unmeasured_not_absent(tmp_path):
    """probe_setup.hide_from_worktree's shape: git ls-files still lists it, the
    disk does not have it. Not on disk at all, so lexists cannot see it --
    trackedness is the other half of 'present'."""
    repo = make_repo(tmp_path, "hid", {"Makefile": "test:\n\tpytest -q\n", "a.py": "x = 1\n"})
    (repo / "Makefile").unlink()
    health.collect(repo, fresh=True)
    by = {c["id"]: c for c in card(repo)["checks"]}
    assert by["test.suite"]["status"] == "pending", by["test.suite"]
    assert "Makefile" in by["test.suite"]["detail"]


def test_a_detected_suite_command_survives_an_unopenable_sibling_file(tmp_path):
    """package.json readable with a "test" script + Makefile chmod 0: the suite
    WAS detected. pending_if_unread used to replace the detail and the operator
    lost 'run: npm test' with no change of status. The conclusion stands; the
    note is appended."""
    repo = make_repo(tmp_path, "both", {"package.json": '{"scripts": {"test": "jest"}}\n',
                                        "Makefile": "build:\n\ttrue\n", "a.py": "x = 1\n"})
    with _unopenable(repo / "Makefile"):
        health.collect(repo, fresh=True)
        by = {c["id"]: c for c in card(repo)["checks"]}
    assert by["test.suite"]["status"] == "pending"
    assert "npm test" in by["test.suite"]["detail"], by["test.suite"]["detail"]
    assert "Makefile" in by["test.suite"]["detail"]


def test_a_REAL_finding_survives_an_unopenable_sibling_manifest(tmp_path):
    """requirements.txt readable with an unpinned spec + pyproject.toml chmod 0.
    pending_if_unread used to replace BOTH status and detail, so the concrete
    "unpinned requirements" finding and its fix route vanished and the row read
    only "could not read pyproject.toml". Worse than cosmetic: `pending` is
    excluded from render.VALUE, so a repo with a KNOWN unpinned dependency
    scored HIGHER for also owning an unreadable file. An unread sibling can add
    to a finding, never retract it (review)."""
    repo = make_repo(tmp_path, "pins", {"requirements.txt": "requests>=2.0\n",
                                        "pyproject.toml": '[project]\nname = "x"\n',
                                        "uv.lock": "{}\n", "a.py": "x = 1\n"})
    with _unopenable(repo / "pyproject.toml"):
        health.collect(repo, fresh=True)
        by = {c["id"]: c for c in card(repo)["checks"]}
    lock = by["ai.lockfiles"]
    assert lock["status"] == "warn", lock            # not demoted out of the score
    assert lock["status"] in render.VALUE, lock      # and therefore still weighed
    assert "requirements.txt" in lock["detail"], lock["detail"]   # the finding
    assert "pyproject.toml" in lock["detail"], lock["detail"]     # and the gap
    row = next(r for r in routes.fix_rows({"checks": [lock]}, advisory=True)
               if r["id"] == "ai.lockfiles")
    assert row.get("routed") is True, row            # the real route, not UNREADABLE


def test_a_MISSING_LOCKFILE_still_counts_when_a_manifest_will_not_open(tmp_path):
    """The sibling of the test above, and the case the first guard missed. Every
    manifest is PINNED, so `unpinned` is empty -- but there is no lockfile at all,
    and "no lockfile committed" is read off the tracked-file list, not off the
    manifest that would not open. `finding_stands=bool(unpinned)` demoted it to
    `pending` anyway, so a repo with NO lockfile dropped out of render.VALUE and
    scored higher for owning one unreadable file (review)."""
    repo = make_repo(tmp_path, "nolock", {"requirements.txt": "requests==2.31.0\n",
                                          "pyproject.toml": '[project]\nname = "x"\n',
                                          "a.py": "x = 1\n"})
    with _unopenable(repo / "pyproject.toml"):
        health.collect(repo, fresh=True)
        by = {c["id"]: c for c in card(repo)["checks"]}
    lock = by["ai.lockfiles"]
    assert lock["status"] == "warn", lock
    assert lock["status"] in render.VALUE, lock
    assert "no lockfile" in lock["detail"], lock["detail"]
    assert "pyproject.toml" in lock["detail"], lock["detail"]


def test_an_unrun_probe_keeps_its_PROBE_route_when_a_sibling_is_unreadable():
    """The other half of the fix, found by review.

    `unread` is the marker that says "this pending is a could-not-read
    DIAGNOSIS", and fix_rows sends anything carrying it to unreadable() —
    "restore the file". Stamped on a probe that simply never ran, it replaced
    "run: npm test" with "restore the Makefile", so the probe never ran and the
    check stayed unmeasured — a fix route that routes away from the fix.
    """
    import routes
    c = common.check("test.suite", "tests", "blocking", "Suite green",
                     "pending", "not run yet — run: npm test")
    out = common.pending_if_unread(c, ["Makefile"], "the suite")
    assert out["status"] == "pending"
    assert "npm test" in out["detail"], "the operator lost the command to run"
    assert "Makefile" in out["detail"], "the unreadable sibling went unmentioned"
    assert "unread" not in out, "an unrun probe was marked as a could-not-read diagnosis"
    row = next(r for r in routes.fix_rows({"checks": [out]}, advisory=True)
               if r["id"] == "test.suite")
    assert row["dispatch"] == "probe", f"routed away from the probe: {row}"


def test_a_conclusion_the_unread_file_invalidates_IS_marked_unread():
    """The known negative: a `pass` that rests on the unopenable file still
    becomes a could-not-read diagnosis, routed to the real fix."""
    import routes
    c = common.check("test.suite", "tests", "blocking", "Suite green",
                     "pass", "no test suite detected")
    out = common.pending_if_unread(c, ["Makefile"], "the suite")
    assert out["status"] == "pending"
    assert out.get("unread") == ["Makefile"]
    row = next(r for r in routes.fix_rows({"checks": [out]}, advisory=True)
               if r["id"] == "test.suite")
    assert row["dispatch"] != "probe", f"an unreadable file offered as a probe: {row}"


def test_a_missing_binary_is_a_result_not_a_crash(tmp_path):
    """Every caller of run() handles rc != 0 — that IS the contract. An absent
    executable raised FileNotFoundError straight past them instead, which took
    the renderer down on a machine with no git: no dashboard, a traceback where
    a page should be (review)."""
    rc, out = common.run(["definitely-not-a-real-binary-xyz", "--version"], tmp_path)
    assert rc != 0
    assert "could not run on this machine" in out
    assert "definitely-not-a-real-binary-xyz" in out


def test_a_binary_that_exists_still_reports_its_own_code(tmp_path):
    """The known negative: the guard must not swallow real exit codes."""
    rc, out = common.run(["python3", "-c", "import sys; print('hi'); sys.exit(3)"], tmp_path)
    assert rc == 3 and "hi" in out
    # The literal run() actually emits. The first version of this line asserted
    # "not available", a string no source file produces, so the known-negative
    # could never fail — a check pointed at nothing (review).
    assert "could not run on this machine" not in out


def test_a_binary_present_but_not_executable_is_also_a_result(tmp_path):
    """The errno the first guard missed. A hand-listed pair of exception types
    left PermissionError raising a traceback out of render and collect — the
    same crash, one errno over (review)."""
    fake = tmp_path / "git"
    fake.write_text("#!/bin/sh\necho hi\n")
    fake.chmod(0o644)                     # on PATH, not executable
    rc, out = common.run([str(fake), "--version"], tmp_path)
    assert rc == 127, f"expected a result, got rc={rc}: {out}"
    assert "could not run on this machine" in out
