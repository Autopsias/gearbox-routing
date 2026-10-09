#!/usr/bin/env python3
"""Fix routing: every route is dispatchable, its FLAGS are real, and the docs agree.

Split out of test_health_shape.py, which is about shape detection and check
visibility. These tests guard a quieter failure: a fix route that names a slash
command no agent can fire, one that passes a flag its target does not define
(so the argument lands on a positional and the dispatch changes nothing), or a
probe the collector leaves pending with no instruction anywhere. All three read
as work-in-progress on the dashboard and are in fact work nobody will ever do.

Helpers and the frozen check-id set are IMPORTED from test_health_shape rather
than retyped — a second copy of ALL_CHECK_IDS is a second thing to drift.
"""
import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import health  # noqa: E402
from test_health_shape import ALL_CHECK_IDS, card, git, make_repo  # noqa: E402


# ---------- routes are dispatchable, docs match the code ----------

SKILL_MD = Path(health.__file__).resolve().parent.parent / "SKILL.md"
CHECK_ID = re.compile(r"`([a-z]+\.[a-z0-9-]+)`")


def table_ids(header_cell):
    """Check ids in column 1 of the SKILL.md table whose first header cell matches.

    Scoped to one table on purpose — SKILL.md holds three, and a document-wide
    grep for backticked ids would let the routing table satisfy an assertion
    about the probe table. Ids must be in backticks; that is why the tables were
    restructured rather than the parser loosened.
    """
    ids, inside = set(), False
    for line in SKILL_MD.read_text().splitlines():
        if not line.strip().startswith("|"):
            inside = False
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if cells[0] == header_cell:
            inside = True
        elif inside and set(cells[0]) - set("-: "):
            ids |= set(CHECK_ID.findall(cells[0]))
    return ids


def probe_ids(tmp_path):
    """Every id the collector can leave `pending` — read from the code, not a list."""
    repo = make_repo(tmp_path, "probeids", {"a.py": "x = 1\n"})
    shape = health.detect_shape(repo, health.tracked_files(repo))
    return {c["id"] for c in health.pending_checks(repo, shape)}


def test_skill_md_documents_every_probe(tmp_path):
    documented = table_ids("Probe")
    assert documented, "the probe table did not parse — check the backticked ids"
    assert documented == probe_ids(tmp_path)


def test_skill_md_routing_table_matches_the_code(tmp_path):
    import routes
    documented = table_ids("Failing check")
    assert documented, "the routing table did not parse"
    assert documented == set(routes.ROUTES)


def test_every_route_is_keyed_on_a_real_check_id():
    import routes
    assert set(routes.ROUTES) <= ALL_CHECK_IDS


def test_every_check_the_collector_emits_has_a_decided_route():
    """16 advisory ids once had no entry at all and fell through to UNKNOWN.

    So `--advisory` — the go-ahead path that promises a pre-built dispatch —
    answered "no fix route is defined, go edit routes.py" for two thirds of the
    advisory tier. `hyg.large-files` reproduced it on a live run.

    Stated as an equality against the frozen id set, not as a count: a check
    added later arrives here as a failing test, which is the moment to decide
    who fixes it. Deciding "no automation fits" is a legitimate answer — it is
    an `operator()` row, pinned by name in OPERATOR_ONLY below.
    """
    import routes
    assert set(routes.ROUTES) == ALL_CHECK_IDS, \
        "no route decided for: " + ", ".join(sorted(ALL_CHECK_IDS - set(routes.ROUTES)))


def test_function_length_routes_to_the_focus_that_fixes_function_length():
    """The new check's route, pinned by its ARGUMENT and not just its existence.

    An unfocused `/code-quality --fix` would send safe-refactor at whichever
    violation it met first, which is how a check arrives with a route that looks
    dispatchable and fixes something else. `--focus` is one of the command's own
    declared values (file-size|function-length|complexity).
    """
    import routes
    r = routes.ROUTES["cq.function-length"]
    assert r["dispatch"] == "command-file"
    assert "--fix --focus=function-length" in r["prompt"]
    # The flags are verified against THIS repo's command file, not `r["flag_check"]`.
    # That field reads ~/.claude/commands — machine state a clean CI checkout does
    # not have, so it said "not on this machine" and failed every CI run. The
    # deployed copy keeps its own test, which skips out loud.
    assert routes.flag_check(COMMAND_SRC / "code-quality.md",
                             "--fix --focus=function-length") == ""


# ---------- a diagnosis-pending check never gets told to run a probe ----------

def test_a_diagnosis_pending_check_never_gets_told_to_run_a_probe(tmp_path):
    """fix_rows used to route EVERY `pending` check to PROBE. Now
    a DETERMINISTIC check (workflow_checks.py, via common.unmeasured) can go
    `pending` with a "could not read X" DIAGNOSIS instead of an unrun probe
    command — and PROBE told the agent to run a probe that does not exist for
    it, dropping the check's real fix route entirely.

    Reproduced exactly as found live (review): an unlinked tracked
    workflow leaves three BLOCKING security checks `pending` this way.
    """
    import routes
    wf = ("name: ci\non: [push]\npermissions:\n  contents: read\njobs:\n  t:\n"
          "    runs-on: ubuntu-latest\n    timeout-minutes: 5\n    steps:\n"
          "      - run: echo hi\n")
    repo = make_repo(tmp_path, "unlinkwf", {"README.md": "# demo\n",
                                            ".github/workflows/ci.yml": wf})
    (repo / ".github" / "workflows" / "ci.yml").unlink()
    health.collect(repo, fresh=True)
    by = {c["id"]: c for c in card(repo)["checks"]}
    checks = ("sec.workflow-permissions", "sec.action-pinning", "sec.dangerous-workflow")
    for cid in checks:
        assert by[cid]["status"] == "pending", f"{cid}: {by[cid]}"
        assert by[cid]["unread"] == [".github/workflows/ci.yml"], by[cid]
    rows = {r["id"]: r for r in routes.fix_rows(card(repo))}
    for cid in checks:
        r = rows[cid]
        assert r["dispatch"] != "probe", \
            f"{cid} was told to run a probe command that does not exist for it"
        assert "ci.yml" in r["prompt"], r["prompt"]
        assert "health.py collect" in r["prompt"], \
            "the row must say how to get a real measurement, not guess one"


def test_an_unrun_probe_still_gets_PROBE_not_the_new_route(tmp_path):
    """The known negative: a genuinely never-run probe (no `unread` stamp) must
    keep going to PROBE — the new branch is scoped to the diagnosis case only."""
    import routes
    repo = make_repo(tmp_path, "probeonly", {"a.py": "x = 1\n"})
    health.collect(repo, fresh=True)
    rows = {r["id"]: r for r in routes.fix_rows(card(repo), advisory=True)}
    assert rows["sec.secrets-history"]["dispatch"] == "probe"


# ---------- a decided operator row vs. one nobody has decided ----------

def test_a_deliberate_operator_row_is_not_shaped_like_an_undecided_one():
    """`"dispatch": "operator"` with an empty `agent` used to mean BOTH answers.

    `hyg.todo-density` (nothing automates it — decided, and the operator
    sentence IS the fix) and a check nobody ever wrote a route for emitted
    identically shaped rows, so SKILL.md step 6 told the agent to go add a
    route for a row that already carries the only answer it will ever have.
    `routed` separates them, and it is True on every ROUTES entry — including
    the operator ones, because routing something to the human is a decision.
    """
    import routes
    for cid, r in routes.ROUTES.items():
        assert r["routed"] is True, cid
    assert routes.UNKNOWN["routed"] is False
    # "never measured" is not "unrouted": the probe row carries an instruction.
    assert routes.PROBE["routed"] is True


def test_skill_md_reads_routed_rather_than_the_row_shape():
    """The doc half of the same defect: step 6 keyed "no route" on the row SHAPE, which
    the two deliberate operator rows also have."""
    section = skill_md_section("## `--fix-blocking`")
    assert section, "the --fix-blocking section did not parse"
    assert '"routed": false' in section, section
    assert '"routed": true' in section, section


def test_the_speed_routes_point_at_a_section_that_exists():
    """Three routes send the agent to SKILL.md's speed ladder rather than
    restating it. A renamed heading would make them routes in name only."""
    import routes
    assert skill_md_section("## Test & CI speed — the ladder"), \
        "the speed ladder section did not parse"
    for cid in ("test.runtime", "test.collection-cost", "test.parallel-safety"):
        assert "Test & CI speed" in routes.ROUTES[cid]["prompt"], cid


# ---------- the emitted PROSE, not the route's structured fields ----------
#
# `sec.dangerous-workflow` dispatched with "Read <skill>/vendor/gha-security-
# review/SKILL.md first and trace the attack path before editing". Nothing ever
# substituted `<skill>`. A subagent is spawned with the prompt STRING and
# nothing else — it has not read SKILL.md, which is the only place `<skill>` is
# defined — so the very first instruction it was handed named a file that is not
# there, and it skips or stalls on it.
#
# Every test above reads a route's structured fields (agent, command, flags).
# None read the prose, which is the half that actually reaches the subagent, so
# the leak was invisible to this whole file. This one reads the prose from BOTH
# places a `fix-routes` row gets it — routes.py's prompts and operator
# sentences, and probes.py's `detail`, because PROBE's own prompt says "run the
# probe named in this row's `detail`" and an unresolvable path there is the same
# defect one file over. It asserts the CLASS rather than the two lines:
#
#   * a `<...>` that touches a `/` is part of a PATH, and nothing downstream can
#     resolve it, so it has to be resolved before it is emitted;
#   * any other `<...>` is an argument the READER supplies — those are pinned by
#     name below, so a new one is a decision instead of a silent addition;
#   * every path inside this skill that a row names must be openable.
#
# The path half is scoped to this skill on purpose: ~/.claude/commands is machine
# state a clean CI checkout does not have, and the two tests below already cover
# that directory in both its portable and its runtime form.

SKILL_DIR = SKILL_MD.parent
PLACEHOLDER = re.compile(r"<[^<>\s][^<>]*>")
ABS_PATH = re.compile(r"/\S+")

# The `<...>` tokens a row may still carry: each is an argument the reader types,
# not a path anybody can resolve on their behalf. Pinned by name — adding one is
# a claim that the reader, and not the emitter, is the one who knows the value.
ARGUMENT_METAVARS = {
    "<name>",                 # which vendored skill, in `vendor.py diff <name>`
    "<branch>",               # which branch, in `git log <branch>`
    "<src>",                  # which source tree, in `bandit -r <src> -ll`
    # `health.py set`, on the PROBE row — the answer is what the agent measured.
    "<id>", "<pass|warn|fail|na>", "<one-line detail>", "<repo>",
}


# The rows that name a path inside this skill, pinned BY NAME rather than left
# to a count. A probe's `detail` is its command only while the probe APPLIES —
# `probe()` emits the reason instead once the repo shape rules it out — so a
# thinner fixture silently drops rows out of the scan, and a count still passes.
# `sec.supply-chain` did exactly that: with no manifest in the fixture it read
# "na: no dependency manifest tracked" and its commands were never checked.
# That is why the fixture below tracks a manifest.
PATH_BEARING = {"sec.dangerous-workflow", "sec.vendor-pins",
                "test.runtime", "test.collection-cost", "test.parallel-safety"}


def emitted_text(tmp_path):
    """Every string a `fix-routes` row can carry, as {(row id, field): text}.

    Both sources, because a row is assembled from both: the route (routes.py)
    and, for an unmeasured check, the `detail` the collector wrote (probes.py).

    The fixture tracks a manifest and a test suite so the probes stay PENDING
    and carry their commands — see PATH_BEARING above.
    """
    import routes
    rows = dict(routes.ROUTES, UNKNOWN=routes.UNKNOWN, PROBE=routes.PROBE)
    out = {(cid, field): v for cid, r in rows.items() for field, v in r.items()
           if isinstance(v, str) and v}
    repo = make_repo(tmp_path, "emitted", {"a.py": "x = 1\n",
                                           "requirements.txt": "requests==2.32.3\n",
                                           "tests/test_a.py": "def test_a():\n    pass\n"})
    shape = health.detect_shape(repo, health.tracked_files(repo))
    for c in health.pending_checks(repo, shape):
        out[(c["id"], "detail")] = c["detail"]
    return out


def test_no_row_emits_an_unresolved_placeholder_or_a_path_that_is_not_there(tmp_path):
    texts = emitted_text(tmp_path)
    # An all-clear because nothing was extracted is the same exit code as an
    # all-clear because everything resolved, so the extractor states its yield.
    assert any(f == "prompt" for _, f in texts), "no route prompts extracted"
    assert any(f == "detail" for _, f in texts), "no probe details extracted"

    reached = set()
    for (cid, field), text in texts.items():
        for m in PLACEHOLDER.finditer(text):
            ph = m.group()
            edges = text[max(0, m.start() - 1):m.start()] + text[m.end():m.end() + 1]
            assert "/" not in edges, (
                f"{cid}.{field} emits {ph} as part of a PATH and nothing "
                f"downstream resolves it — the reader is sent to a file that is "
                f"not there: {text}")
            assert ph in ARGUMENT_METAVARS, (
                f"{cid}.{field} emits the unresolved placeholder {ph}: {text}")
        for path in ABS_PATH.findall(text):
            path = path.rstrip("`.,;:\"')")
            if not path.startswith(f"{SKILL_DIR}/"):
                continue
            assert Path(path).is_file(), \
                f"{cid}.{field} tells the reader to open {path}, which is not there"
            reached.add(cid)
    assert PATH_BEARING <= reached, \
        "these rows name an in-skill path and the scan never saw one: " \
        + ", ".join(sorted(PATH_BEARING - reached))


# The complete set of checks whose fix is genuinely a human's. Every addition
# here is a claim that NO agent may do the work, so the set is pinned by name
# rather than by a predicate a new row could satisfy by accident. These rows are
# `routed` all the same — "we decided nothing automates this" is a decision, and
# telling it apart from "nobody decided" is what `routed` exists for:
#   ai.agents-md     — /init is a Claude Code built-in, so there is no command
#                      file for a subagent to read and follow.
#   hyg.todo-density — no command fixes it. The nearest one, /declutter, is
#                      read-only by invariant and defines no --fix; each marker
#                      is either work to schedule or a stale line to delete, and
#                      only the owner knows which.
#   hyg.large-files  — a >5 MB tracked file is as likely to be a needed fixture
#                      as a mistake, and LFS / release asset / delete each change
#                      how the repo is cloned. History still holds the blob.
#   hyg.activity     — the date of the last commit is a fact about the project.
#                      No edit to the tree moves it.
#   hyg.unmerged-work — merging or deleting a branch publishes or destroys work
#                      only its author can value, and a dropped stash is gone.
#   sec.supply-chain — every remedy is drop / replace / vendor / accept, and
#                      choosing a replacement library is the call CLAUDE.md
#                      reserves for a human.
#   sec.vendor-pins  — applying a vendored update is a human decision by the
#                      vendoring skill's own invariant; vendor.py has verify,
#                      check and diff, and deliberately no `update`.
OPERATOR_ONLY = {"ai.agents-md", "hyg.todo-density", "hyg.large-files",
                 "hyg.activity", "hyg.unmerged-work", "sec.supply-chain",
                 "sec.vendor-pins"}


def test_no_route_is_a_bare_slash_command():
    """The defect itself: a route an agent cannot fire.

    Probed — subagents declaring `SlashCommand` in their frontmatter
    are not given it. So every route must name either a subagent or a prompt
    FILE an agent can read. The operator rows are the named exceptions above.
    """
    import routes
    for cid, r in routes.ROUTES.items():
        if r["dispatch"] == "operator":
            assert cid in OPERATOR_ONLY, f"{cid} is operator homework"
            assert r["operator"], f"{cid} is operator-only with no reason given"
            continue
        assert r["agent"], cid
        assert r["prompt"], cid
        if r["dispatch"] == "command-file":
            # The command files were written for the main session and name
            # tools a spawned agent does not have; the live probe stalled on
            # exactly that. The prompt must say so, or the route is a route in
            # name only.
            assert "NO SlashCommand" in r["prompt"], cid
            assert "use the Agent tool" in r["prompt"], cid


# ---------- the flags a route passes must exist in the file it dispatches ----------
#
# The gap the first attempt left open: the tests above prove a route names a
# real prompt file, and NOTHING proved the arguments it passes mean anything to
# that file. Four routes were wrong at once — `--fix` to test-orchestrate (which
# has no such flag, so it lands on the `test_scope` positional), `--fix` to
# ci-orchestrate four times (its flag is `--fix-all`; the unmatched text becomes
# the issue description and the run stays in non-fix mode), and `--fix` to the
# `declutter` skill, which is read-only by invariant and declares no flags at all.
#
# Flags are read from the target's `argument-hint:` frontmatter — the declared
# contract, and the one robustly parseable list in these files. A whole-file
# grep is NOT usable: commands/test-orchestrate.md carries the prose line
# "pytest --fix or similar" in its guard rails, so a document-wide scan would
# have accepted the exact bug this test exists to catch.
#
# ~/.claude/commands/*.md is machine state a clean Linux CI checkout does not
# have, so THIS test checks the flags against this repo's commands/<name>.md —
# the source that gets deployed there, and the only copy a clean checkout has.
# It is the CI-portable half and it stays. The other two halves are below:
# test_the_deployed_command_files_declare_every_flag_the_routes_pass checks the
# copy runtime actually dispatches (skipping OUT LOUD when it is absent), and
# routes.cmd() carries the same answer per row as `flag_check` so a stale
# deployed file is reported at run time rather than dispatched into.
COMMAND_SRC = Path(health.__file__).resolve().parents[3] / "commands"
DEPLOYED = Path.home() / ".claude" / "commands"
ARGS_IN_PROMPT = re.compile(r"\$ARGUMENTS = `([^`]*)`")


def declared_flags(md):
    """routes.declared_flags, but LOUD when it cannot parse.

    The parser itself lives in routes.py because `fix-routes` needs the same
    answer at run time; a second copy of the regexes here is a second thing to
    drift. What differs is the verdict on "no flags found": at run time that is
    a `flag_check` string on the row, and here it must fail the suite — a route
    whose target has no `argument-hint:` must have its flags pinned explicitly,
    never be waved through by an extractor that quietly returned nothing.
    """
    import routes
    flags = routes.declared_flags(md)
    assert flags, (f"{md.name}: no flags parsed from an `argument-hint:` line — "
                   "pin this route's flags explicitly instead of passing on empty")
    return flags


def test_every_route_passes_flags_the_target_file_defines():
    import routes
    checked = 0
    for cid, r in routes.ROUTES.items():
        if r["dispatch"] != "command-file":
            continue
        src = COMMAND_SRC / (r["command"].lstrip("/") + ".md")
        assert src.is_file(), f"{cid} dispatches {src}, which is not in this repo"
        passed = ARGS_IN_PROMPT.search(r["prompt"])
        assert passed, f"{cid}: the dispatch prompt carries no $ARGUMENTS block"
        declared = declared_flags(src)
        for flag in routes.FLAG.findall(passed.group(1)):
            assert flag in declared, (
                f"{cid} passes {flag} to {src.name}, which declares only "
                + ", ".join(sorted(declared)))
            checked += 1
    # A pass because nothing was extracted is the same exit code as a pass
    # because everything matched, so the extractor states its own yield.
    assert checked >= 10, f"only {checked} flags checked — the extractor is broken"


def test_the_deployed_command_files_declare_every_flag_the_routes_pass():
    """The test above is CI-portable; THIS one matches runtime.

    Routes resolve against `~/.claude/commands` — the deploy target — so the
    in-repo copy passing is no evidence the file a dispatch will actually reach
    still declares the flag. A stale deployed file turns `--fix-all` into a
    positional argument and the run changes nothing.

    That directory is machine state, so this skips on a clean checkout — but it
    SKIPS OUT LOUD with the reason, never silently passing, because a check that
    reports all-clear without looking is the defect this whole file exists to
    remove.
    """
    import routes
    if not DEPLOYED.is_dir():
        pytest.skip(f"no deployed commands at {DEPLOYED} (expected on a clean CI "
                    "checkout) — the in-repo copy is covered by "
                    "test_every_route_passes_flags_the_target_file_defines")
    bad = {cid: r["flag_check"] for cid, r in routes.ROUTES.items()
           if r["dispatch"] == "command-file" and r["flag_check"]}
    assert not bad, ("the deployed command files disagree with the routes — "
                     "run `gearbox deploy`, or fix the route: " + json.dumps(bad, indent=2))


def test_a_route_reports_the_flags_its_deployed_target_does_not_declare(tmp_path,
                                                                       monkeypatch):
    """The same rule at RUNTIME: `flag_check` is empty ONLY when the flags were verified.

    Three ways a route can be undispatchable, and none of them may read as clean:
    the deployed file does not declare the flag, the deployed file declares no
    parseable `argument-hint:` at all, and the deployed file is not there.
    """
    import routes
    monkeypatch.setattr(routes, "COMMANDS", tmp_path)
    routes.declared_flags.cache_clear()
    (tmp_path / "thing.md").write_text(
        "---\nargument-hint: \"[issue] [--check] [--fix-all]\"\n---\nbody\n")

    ok = routes.cmd("thing", "gap description --check --fix-all")
    assert ok["flag_check"] == "", ok["flag_check"]
    assert ok["command_file_found"] is True

    stale = routes.cmd("thing", "--fix")["flag_check"]
    assert "--fix" in stale and "--check" in stale, stale

    (tmp_path / "bare.md").write_text("---\ndescription: no argument-hint\n---\nbody\n")
    assert "cannot verify" in routes.cmd("bare", "--fix")["flag_check"]

    absent = routes.cmd("gone", "--fix")
    assert absent["command_file_found"] is False
    assert "cannot verify" in absent["flag_check"], absent["flag_check"]

    # No flags passed => nothing to verify, and that IS a clean answer —
    # but only once the file was found. See the test below.
    assert routes.cmd("thing", "some positional only")["flag_check"] == ""


def test_a_flagless_route_to_a_missing_file_is_not_verified_clean(tmp_path,
                                                                 monkeypatch):
    """Empty `flag_check` means VERIFIED, so it may not precede the file check.

    The ordering bug: "no flags passed, nothing to verify" returned "" BEFORE
    asking whether the command file exists at all. A flagless `cmd()` route
    therefore reported itself verified clean while dispatching a file that is
    not on the machine — and `test_the_deployed_command_files_declare_every_
    flag_the_routes_pass`, which reads exactly this field, stayed green over
    it. Same defect class as the rest of this file: a check that answers
    "clean" because it never looked.
    """
    import routes
    monkeypatch.setattr(routes, "COMMANDS", tmp_path)
    routes.declared_flags.cache_clear()
    r = routes.cmd("gone", "just a positional, no flags")
    assert r["command_file_found"] is False
    assert r["flag_check"], "an absent command file reported itself verified clean"


def test_skill_md_never_hardcodes_the_deploy_path():
    """An invocation through the deploy path runs the STALE deployed copy when
    the skill is exercised from a source checkout — so an acceptance test that
    follows SKILL.md literally would validate code the change never touched.

    Keyed on the path plus a trailing `/`, which is what makes it a path being
    USED. SKILL.md names the bare directory once, to say never to write it.
    """
    used = [ln for ln in SKILL_MD.read_text().splitlines()
            if ".claude/skills/repo-health/" in ln]
    assert not used, used


def skill_md_section(title):
    """The body of one `## ` section of SKILL.md, so an assertion about one
    procedure cannot be satisfied by prose somewhere else in the file."""
    out, inside = [], False
    for line in SKILL_MD.read_text().splitlines():
        if line.startswith("## "):
            inside = line.strip() == title
        elif inside:
            out.append(line)
    return "\n".join(out)


def history_lines(repo):
    f = repo / ".claude" / "health" / "history.jsonl"
    return [json.loads(ln) for ln in f.read_text().splitlines() if ln.strip()]


def test_diff_review_window_base_is_the_last_history_line(tmp_path):
    """health_render.py appends THIS run's history entry, and render runs AFTER the probes.

    So at probe time the newest line is still the PREVIOUS run — which is the
    usable base. SKILL.md said *second-to-last*, which skips a whole run and
    re-reviews its commits. Both halves are asserted here: the ordering fact,
    by running collect and render rather than by reading the doc, and the doc
    agreeing with it.
    """
    import health_render as render
    repo = make_repo(tmp_path, "window", {"a.py": "x = 1\n"})
    health.collect(repo)
    render.render(repo)
    first = history_lines(repo)[-1]

    (repo / "b.py").write_text("y = 2\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "second")
    health.collect(repo)          # probe time for run 2 — render has not run yet
    assert len(history_lines(repo)) == 1, "render appended before the probes ran"
    assert history_lines(repo)[-1]["commit"] == first["commit"]
    # and it is a real base: an earlier commit, not this run's HEAD
    assert first["commit"] != card(repo)["commit"]

    section = skill_md_section("## Reviewing the diff without the operator")
    assert section, "the diff-review section did not parse"
    assert "second-to-last" not in section, section
    assert "last line" in section, section



# ---------- every first-party doc describes the SAME diff-review route ----------
#
# SKILL.md, checks.md and probes.py were corrected to the dispatched-review method;
# vendor/README.md still said "Use `/security-review`; record it as `sec.diff-review`",
# and the diff missed it. Three documents agreeing and a fourth contradicting them
# sends whoever follows the fourth back to a probe that sits pending forever. So the
# rule is the CLASS: a first-party doc may name the built-in (a human should still run
# it), never without saying in the SAME block that no agent can fire it.
BUILTIN = re.compile(r"(?<![\w/-])/security-review\b")
NOT_DISPATCHABLE = re.compile(r"operator[- ]only|only the operator|no agent can "
                              r"fire|an agent cannot fire|cannot be fired", re.I)


def doc_blocks(md):
    """Blank-line blocks, but one block per table ROW: a whole table is a single
    paragraph, and lumped together a right row would vouch for a wrong one."""
    for chunk in md.read_text().split("\n\n"):
        lines = chunk.splitlines()
        yield from (lines if any(x.lstrip().startswith("|") for x in lines) else [chunk])


def test_no_first_party_doc_offers_the_built_in_as_the_diff_review_route():
    seen = 0
    for md in SKILL_DIR.rglob("*.md"):
        parts = md.relative_to(SKILL_DIR).parts
        if parts[0] == "vendor" and len(parts) > 2:
            continue                  # vendor/<skill>/**: upstream's own words
        for block in doc_blocks(md):
            if not BUILTIN.search(block):
                continue
            seen += 1
            assert NOT_DISPATCHABLE.search(block), (
                f"{md.relative_to(SKILL_DIR)} names the built-in /security-review "
                f"without saying an agent cannot fire it, so a reader puts "
                f"sec.diff-review back into pending forever: {block}")
    # An all-clear because nothing matched is the same exit code as a real one.
    assert seen >= 3, f"only {seen} mentions scanned — the scan stopped looking"


# ---------- the supply-chain findings file is per-run, never shared ----------
FINDINGS_PATH = re.compile(r"(?<![\w/-])/\S+\.json")


def supply_chain_findings(repo):
    """The path(s) the `sec.supply-chain` row says to write, then read back."""
    shape = health.detect_shape(repo, health.tracked_files(repo))
    detail = next(c["detail"] for c in health.pending_checks(repo, shape)
                  if c["id"] == "sec.supply-chain")
    assert detail.startswith("run: "), f"the probe did not apply: {detail}"
    return FINDINGS_PATH.findall(detail)


def test_the_supply_chain_findings_file_is_never_shared_across_runs_or_repos(tmp_path):
    """Resolving the old `<out>` placeholder produced ONE global /tmp file.

    `health_render.py` is told to read the JSON `collect.py` just wrote. Sharing one path,
    two runs on different repos interleave and the second renders the FIRST repo's
    findings — a wrong verdict, delivered silently — and a stale file from a collect
    that died renders as this run's result. Nor may the placeholder return:
    test_no_row_emits_an_unresolved_placeholder... forbids one in an emitted path.
    """
    files = {"a.py": "x = 1\n", "requirements.txt": "requests==2.32.3\n"}
    a1 = supply_chain_findings(make_repo(tmp_path, "scA", files))
    a2 = supply_chain_findings(tmp_path / "scA")          # same repo, second run
    b1 = supply_chain_findings(make_repo(tmp_path, "scB", files))

    for paths in (a1, a2, b1):
        assert len(paths) == 2, f"collect writes it and render reads it: {paths}"
        assert paths[0] == paths[1], f"render must read what collect wrote: {paths}"
        assert not Path(paths[0]).exists(), \
            f"{paths[0]} exists already — a previous run's file reads as this run's"
    assert a1[0] != b1[0], f"two repos share one findings file: {a1[0]}"
    assert a1[0] != a2[0], f"two runs share one findings file: {a1[0]}"
