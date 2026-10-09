"""The `fix-routes` CLI: what it emits, and the two ways it may stay silent.

Split out of test_routes.py, which sat at exactly its 800-line test limit — the
same trap health.py was in, where any addition trips the ratchet. The seam is
real: everything here drives `health.py fix-routes` as a SUBPROCESS and asserts
on the rows it prints, while test_routes.py asserts on the ROUTES table and the
documents that describe it. Nothing here reads ROUTES directly.

Silence is the thing under test. `fix-routes` prints one line per finding, and
SKILL.md reads no output as "nothing blocking is failing" — so a never-collected
repo, an unmeasured check, an unrouted one and a check whose tier is neither
blocking nor advisory each have to produce a ROW rather than nothing.
"""
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import health  # noqa: E402
from test_health_shape import make_repo  # noqa: E402


def test_fix_routes_before_collect_says_so_instead_of_raising(tmp_path):
    """"No scorecard" is not the same answer as "no failing blocking check".

    stdout stays empty — every `fix-routes` line is a finding — but the exit
    code is non-zero and stderr names the command to run. Silently exiting 0
    would turn a never-collected repo into a false all-clear under SKILL.md's
    "no output means nothing blocking is failing".
    """
    repo = make_repo(tmp_path, "uncollected", {"a.py": "x = 1\n"})
    out = subprocess.run([sys.executable, str(Path(health.__file__)), "fix-routes",
                          "--repo", str(repo)], capture_output=True, text=True,
                         cwd=str(Path(health.__file__).parent))
    assert out.returncode != 0
    assert out.stdout == "", out.stdout
    assert "Traceback" not in out.stderr, out.stderr
    assert "collect" in out.stderr, out.stderr
    # ...and it must not have WRITTEN anything to say so. The guard reached the
    # scorecard through health_dir(), which mkdirs — so a read-only query left
    # `.claude/health/` behind in a repo it was only asked a question about.
    assert not (repo / ".claude" / "health").exists(), \
        "a read-only query created .claude/health/"


def blocking_repo(tmp_path, name):
    repo = make_repo(tmp_path, name, {"a.py": "x = 1\n"})
    health.collect(repo)
    return repo


def fix_routes(repo, *extra):
    out = subprocess.run([sys.executable, str(Path(health.__file__)), "fix-routes",
                          "--repo", str(repo), *extra], capture_output=True, text=True,
                         cwd=str(Path(health.__file__).parent))
    assert out.returncode == 0, out.stderr
    return [json.loads(x) for x in out.stdout.splitlines() if x.strip()]


def blocking_by_status(repo, status):
    return {c["id"] for c in health.load_card(repo)["checks"]
            if c["tier"] == "blocking" and c["status"] == status}


def test_an_unmeasured_blocking_check_is_a_row_not_silence(tmp_path):
    """The undecided-route defect, one layer in: `pending` is not `pass`.

    A freshly collected repo has `sec.secrets-history` blocking and PENDING —
    nobody has run gitleaks yet. Filtering on `status == "fail"` printed nothing,
    and SKILL.md turns no output into "nothing blocking is failing", so an
    unmeasured repo reported clean. Every unmeasured blocking check now prints
    its own row, and the probe to run is on it.
    """
    repo = blocking_repo(tmp_path, "unmeasured")
    unmeasured = blocking_by_status(repo, "pending")
    assert "sec.secrets-history" in unmeasured, "the fixture stopped reproducing it"

    rows = fix_routes(repo)
    assert {r["id"] for r in rows} == unmeasured
    row = next(r for r in rows if r["id"] == "sec.secrets-history")
    assert row["status"] == "pending"
    assert row["dispatch"] == "probe"
    assert row["operator"] == "", "running gitleaks is not operator-only work"
    assert "gitleaks" in row["detail"], row["detail"]
    assert "health.py set" in row["prompt"], row["prompt"]


def test_fix_routes_is_silent_only_once_every_blocking_check_was_measured(tmp_path):
    """Silence has exactly one meaning: measured, and not failing.

    This test used to assert silence on a freshly collected repo — which pinned
    the false all-clear in. It now has to RECORD the pending blocking probes
    before it may expect silence.
    """
    repo = blocking_repo(tmp_path, "quiet")
    assert fix_routes(repo), "a freshly collected repo has unmeasured blocking checks"
    for cid in blocking_by_status(repo, "pending"):
        health.set_result(repo, cid, "pass", "probed, clean")
    assert fix_routes(repo) == []


def test_fix_routes_dispatches_a_planted_blocking_failure(tmp_path):
    repo = blocking_repo(tmp_path, "planted")
    health.set_result(repo, "test.suite", "fail", "3 tests failed")
    rows = [r for r in fix_routes(repo) if r["status"] == "fail"]
    assert [r["id"] for r in rows] == ["test.suite"]
    row = rows[0]
    assert row["dispatch"] == "command-file"
    assert row["agent"] == "general-purpose"
    assert row["detail"] == "3 tests failed"
    assert row["command_file"].endswith("commands/test-orchestrate.md")
    # --run-first, not --fix: test-orchestrate declares no --fix, and the old
    # assertion froze that mistake in by pinning the wrong string.
    assert "$ARGUMENTS = `--run-first`" in row["prompt"]
    assert row["operator"] == ""


def test_fix_routes_ignores_a_failing_advisory_check(tmp_path):
    repo = blocking_repo(tmp_path, "advisory")
    health.set_result(repo, "cq.lint", "fail", "12 findings")
    assert "cq.lint" not in {r["id"] for r in fix_routes(repo)}


def test_every_advisory_route_is_reachable_from_the_cli(tmp_path):
    """ROUTES carried 12 advisory rows and NO CLI path emitted any of them.

    `fix_rows` filtered to blocking, so after the SKILL.md step-5 go-ahead to
    fix advisory findings there was nowhere to get the pre-built dispatch
    prompt that SKILL.md forbids hand-writing — the advisory half of the one
    source of truth was decoration. `--advisory` is that go-ahead; the default
    stays blocking-only, which is what `--fix-blocking` may touch unasked.

    `warn` counts, not just `fail`: most advisory checks top out at `warn`
    (cq.file-size, hyg.todo-density, ci.caching all do), so a fail-only filter
    would have left those routes exactly as unreachable as before.
    """
    import routes
    repo = blocking_repo(tmp_path, "advreach")
    card = health.load_card(repo)
    routed = {c["id"] for c in card["checks"] if c["tier"] == "advisory"} & set(routes.ROUTES)
    # Every advisory check now has a decided route, so the bound is the
    # whole advisory tier rather than the 12 that once existed — a route that
    # stops being reachable from the CLI has to fail here, not shrink the count.
    assert len(routed) >= 32, f"the fixture stopped covering the advisory routes: {routed}"
    for c in card["checks"]:
        if c["id"] in routed:
            c["status"], c["detail"] = "warn", "planted"
    health.save_card(repo, card)

    default = {r["id"] for r in fix_routes(repo)}
    assert not (routed & default), "the default output must stay blocking-only"

    rows = {r["id"]: r for r in fix_routes(repo, "--advisory")}
    assert routed <= set(rows), sorted(routed - set(rows))
    assert default <= set(rows), "--advisory must ADD to the blocking rows, not replace them"
    for cid in routed:
        r = rows[cid]
        assert r["tier"] == "advisory", cid
        assert r["dispatch"] != "probe", cid
        assert r["prompt"] or r["operator"], cid
    assert "$ARGUMENTS = `--fix`" in rows["cq.file-size"]["prompt"]


def test_a_blocking_check_at_warn_is_routed_in_both_modes(tmp_path):
    """A blocking `warn` is a finding. It used to produce nothing at all.

    `sec.action-pinning` warns when only first-party `actions/*` are unpinned,
    and `sec.dangerous-workflow` warns when event data is interpolated near
    `run:`. Both are blocking, neither ever reaches `fail` in those states, and
    the filter routed blocking checks on `fail` alone — so the dashboard said
    NEEDS ATTENTION while `fix-routes` printed an empty list, which SKILL.md
    step 1 reads as "nothing blocking is failing".

    Third instance of one class (`pending`, then advisory `warn`, then this),
    so the assertion is the class: only `pass` and `na` may be silent.
    """
    repo = blocking_repo(tmp_path, "blockwarn")
    planted = ("sec.action-pinning", "sec.dangerous-workflow")
    for cid in blocking_by_status(repo, "pending"):
        health.set_result(repo, cid, "pass", "probed, clean")
    for cid in planted:
        health.set_result(repo, cid, "warn", "planted")

    for extra in ((), ("--advisory",)):
        rows = {r["id"]: r for r in fix_routes(repo, *extra)}
        for cid in planted:
            assert cid in rows, f"{cid} at warn produced no row (extra={extra})"
            r = rows[cid]
            assert r["status"] == "warn"
            assert r["dispatch"] == "agent" and r["agent"] == "ci-infrastructure-builder"
            assert r["prompt"], cid


def test_an_unrouted_blocking_failure_becomes_an_explicit_operator_row(tmp_path):
    """A blocking check nobody wrote a route for must be LOUD, not absent."""
    repo = blocking_repo(tmp_path, "unrouted")
    card = health.load_card(repo)
    card["checks"].append({"id": "sec.invented", "layer": "security",
                           "tier": "blocking", "title": "Invented", "status": "fail",
                           "detail": "planted", "fix": ""})
    health.save_card(repo, card)
    row = next(r for r in fix_routes(repo) if r["id"] == "sec.invented")
    assert row["dispatch"] == "operator"
    assert row["agent"] == ""
    assert "no fix route" in row["operator"]


def test_a_failing_check_with_an_unrecognised_tier_is_a_row_not_silence(tmp_path):
    """The silent fallthrough the STATUS rule one line up already refuses.

    `fix_rows` filtered on `tier in ("blocking",)`, so a failing check whose tier is
    `None` — or anything a later version adds — matched no tier and produced NO ROW in
    either mode; SKILL.md step 1 reads that silence as "nothing blocking is failing".
    """
    repo = blocking_repo(tmp_path, "badtier")
    card = health.load_card(repo)
    card["checks"].append({"id": "sec.invented-tier", "layer": "security", "tier": None,
                           "title": "Untiered", "status": "fail", "detail": "planted"})
    health.save_card(repo, card)
    for extra in ((), ("--advisory",)):
        rows = {r["id"]: r for r in fix_routes(repo, *extra)}
        assert "sec.invented-tier" in rows, \
            f"a failing check with no tier produced no row at all (extra={extra})"
        row = rows["sec.invented-tier"]
        assert (row["status"], row["dispatch"], row["agent"]) == ("fail", "operator", ""), row
        assert "tier" in row["operator"], row["operator"]


def test_fix_routes_marks_a_decided_operator_row_routed_and_an_unknown_one_not(tmp_path):
    """The same distinction at RUNTIME, over a live card, since that is where a
    reader meets it: two operator rows in one `--advisory` run, one deliberate
    and one nobody decided, and the reader must be able to tell them apart."""
    repo = blocking_repo(tmp_path, "routedflag")
    card = health.load_card(repo)
    card["checks"].append({"id": "hyg.invented", "layer": "hygiene", "tier": "advisory",
                           "title": "Invented", "status": "warn", "detail": "planted",
                           "fix": ""})
    for c in card["checks"]:
        if c["id"] == "hyg.todo-density":
            c["status"], c["detail"] = "warn", "planted"
    health.save_card(repo, card)

    rows = {r["id"]: r for r in fix_routes(repo, "--advisory")}
    decided, undecided = rows["hyg.todo-density"], rows["hyg.invented"]
    for r in (decided, undecided):
        assert r["dispatch"] == "operator" and r["agent"] == "", r
    assert decided["routed"] is True, "a deliberate operator row read as unrouted"
    assert undecided["routed"] is False
    assert "add it to" in undecided["operator"]
    assert "add it to" not in decided["operator"]



def test_fix_route_rows_say_when_the_card_is_stale(tmp_path):
    """A dispatch computed from a scorecard collected against a tree nobody has
    can send an agent to fix what is already fixed, or miss what is broken now.
    The dashboard says so on its masthead; these rows said nothing at all
    (review)."""
    repo = blocking_repo(tmp_path, "stalecard")
    health.set_result(repo, "test.suite", "fail", "3 tests failed")
    card = health.load_card(repo)
    card["tree"] = "0" * 40          # a tree this repo has never had
    health.save_card(repo, card)
    rows = fix_routes(repo)
    assert rows, "fixture produced no rows"
    assert all("stale" in r for r in rows), "a dispatch row hid a stale card"
    assert "collect" in rows[0]["stale"]


def test_fix_route_rows_are_silent_when_the_card_matches_the_tree(tmp_path):
    """The known negative — a caveat printed on every row marks nothing."""
    repo = blocking_repo(tmp_path, "freshcard")
    health.set_result(repo, "test.suite", "fail", "3 tests failed")
    rows = fix_routes(repo)
    assert rows, "fixture produced no rows"
    assert not any("stale" in r for r in rows), f"false stale alarm: {rows[0]}"
