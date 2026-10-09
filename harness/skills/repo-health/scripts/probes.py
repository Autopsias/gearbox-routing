#!/usr/bin/env python3
"""The expensive probes — the ones a model runs and records with `health.py set`.

Every id here is emitted on EVERY run. A probe that does not apply says "na"
and names what was measured; it never disappears, because a scorecard that
silently shrinks reads healthier than the repo is.

Imports common + shape: health.py imports this, never the other way round.
"""
import re
import tempfile
import uuid
from pathlib import Path

from common import check, run, pending_if_unread, read_or_unread
from shape import MANIFEST_LOOKED_FOR
from vendor import vendored_dirs

# Probes whose answer depends on the WORLD and not on this tree: a CVE feed, the
# latest versions on PyPI, an upstream repo that moved. health.py preserves a
# recorded probe result while the tree hash is unchanged, which is exactly right
# for the probes that READ the tree — identical code, still-true answer. It
# cannot bound these, and sec.vendor-pins is the sharpest case: it exists to
# notice that upstream moved, and upstream moving never changes a byte here, so
# keyed on the tree alone its pass would stand forever. So they expire instead.
#
# Declared HERE, beside the probes themselves, and not next to the preservation
# logic that consumes it: a set of ids maintained in the other file is exactly
# the kind of second list that drifts out of step with the first (which is the
# defect this whole module was split out to fix). test_health_shape.py asserts
# every id in here is really emitted, so a rename cannot silently unbind one.
WORLD_PROBES = {"sec.dep-vulns", "hyg.dep-freshness", "sec.vendor-pins",
                "sec.supply-chain"}
WORLD_PROBE_DAYS = 7

# A probe's `detail` is the command the agent is told to RUN — `fix-routes` puts
# it on the row and the PROBE prompt points straight at it. So the paths in it
# are resolved here, against this file, exactly as routes.py resolves the ones
# in its prompts: `<skill>` is a SKILL.md convention, and nothing downstream of
# SKILL.md can expand it. Same reason the deploy target is not used — a source
# checkout must probe the copy it is actually running.
SKILL_DIR = Path(__file__).resolve().parent.parent


def findings_path():
    """A scratch file for the supply-chain auditor's JSON — fresh on every run.

    `collect.py` writes it and `health_render.py` reads it back, so the two commands
    must name ONE path — and it must be a path nothing else can be holding.
    A single shared `/tmp/supply-chain.json` breaks both ways round: two health
    runs on different repos interleave and the second renders the FIRST repo's
    findings, and a file left behind by a collect that died renders as this
    run's result. Either way the verdict is wrong and nothing says so.

    Unique per CALL, not per import: one collect is one run. Deliberately not
    created here — `collect.py` creates it, so a render after a failed collect
    gets "no such file" instead of stale findings. ponytail: not cleaned up
    either; it is a scratch file in the OS temp dir, which is what that is for.
    """
    return Path(tempfile.gettempdir()) / f"repo-health-supply-chain-{uuid.uuid4().hex[:12]}.json"


def suite_command(repo, shape, unread=None, tracked=None):
    """The command that runs the suite, or "". A present-but-unopenable
    Makefile/package.json is appended to `unread` (when given) instead of
    reading as "no suite" -- see common.read_or_unread; `tracked` is the
    repo's tracked-file list, which is half of what "present" means."""
    unread = [] if unread is None else unread
    if re.search(r"^test:", read_or_unread(repo, "Makefile", unread, tracked), re.M):
        return "make test"
    if '"test"' in read_or_unread(repo, "package.json", unread, tracked):
        return "npm test"
    py_names = "\n".join(shape["py"])
    if shape["py"] and (re.search(r"(^|/)test_[^/]+\.py$|_test\.py$", py_names, re.M)
                       or "tests/" in py_names):
        return "python3 -m pytest -q"
    return ""


def probe(cid, layer, tier, title, why_not, cmd):
    """One expensive probe, ALWAYS emitted.

    A check that disappears when the repo shape says it does not apply is
    indistinguishable from a check that passed — and when the shape reading is
    wrong (requirements-ci.txt) the scorecard silently shrinks and
    reads healthier. So `why_not` is either empty (the probe applies: pending,
    with the command to run) or the measured reason it does not, in words that
    name what was looked for.
    """
    return check(cid, layer, tier, title, "na" if why_not else "pending",
                 why_not or cmd)


def diff_window(repo):
    """Why sec.diff-review does not apply to this repo, or "" when it does.

    `rc != 0` is git FAILING, not an empty window, and folding the two together
    made the check say "no commits in the last 30 days" about a repo it never
    read — a measured-sounding `na` over a question nobody answered.
    """
    rc, out = run(["git", "log", "--oneline", "-1", "--since=30.days"], repo)
    if rc != 0:
        # "" means the probe APPLIES, so the check stays `pending` with the
        # command on it. That is the honest answer to a git call that failed: we
        # do not know whether there is a diff, so nobody has looked yet. `na`
        # would have claimed "no commits in the last 30 days" — a measured
        # sentence about a repo git never answered for — and `na` is CLEAR, so
        # the check would also have left fix-routes entirely.
        return ""
    return "" if out else "no commits in the last 30 days — nothing to review"


def house_ratchet(repo):
    """(directory holding the house ratchet, why there is none — "" when there is).

    scripts/quality/ is where the ratchet lives in its source repo; tools/ is
    where vendor_quality.py puts the byte-identical copies in every ADOPTING
    repo. Looking in the first place only made every adopter read "no house
    ratchet".
    """
    for d in ("scripts/quality", "tools"):
        if (repo / d / "check_file_sizes.py").exists():
            return d, ""
    return "scripts/quality", ("no scripts/quality/check_file_sizes.py and no vendored "
                               "tools/check_file_sizes.py — this repo has no house ratchet")


def pending_checks(repo, shape, files=None):
    no_manifest = ("" if shape["manifests"]
                   else "no dependency manifest tracked — " + MANIFEST_LOOKED_FOR)
    no_py = "" if shape["py"] else "no first-party .py files tracked (outside the repo's own excludes)"
    # cq.lint and sec.sast are the two checks the catalog promises to a JS/TS repo
    # as well — eslint for the linter, and semgrep's p/default rules cover TS. Gated
    # on .py alone they read "na: no first-party .py files tracked" on a pure-TS
    # repo, which is the shrinking scorecard one layer down: a whole lint layer went
    # quiet on a repo with plenty to lint (observed). The ruff-only rule
    # sets below (C901, F401…) stay on no_py, because those really are Python-only.
    no_code = ("" if (shape["py"] or shape["js"]) else
               "no first-party .py/.ts/.js files tracked (outside the repo's own excludes)")
    lint_cmd = "run: " + " ; ".join((["ruff check ."] if shape["py"] else [])
                                    + (["npx eslint ."] if shape["js"] else [])
                                    or ["ruff check . / npx eslint . for this ecosystem"])
    sast_cmd = ("run: semgrep scan --config p/default"
                + (" --config p/python" if shape["py"] else "") + " --error"
                + (" (fallback: bandit -r <src> -ll)" if shape["py"] else "")
                + " — deep pass: security-scanner agent")
    no_window = diff_window(repo)
    # The DISK decides whether this install vendors anything, not the manifest.
    # Gating on vendor.json alone meant an install carrying third-party skill
    # trees with no manifest read `na — it vendors no skills`: the same empty
    # manifest that made vendor.py's own `verify` exit 0 (see vendor.load), one
    # layer up, where the operator actually reads it.
    no_vendor_json = ("" if (SKILL_DIR / "vendor.json").exists() or vendored_dirs()
                      else "this repo-health install has no vendor.json and no "
                           "vendor/ trees — it vendors no skills")
    ratchet_dir, no_ratchet = house_ratchet(repo)
    suite_unread = []
    suite = suite_command(repo, shape, suite_unread, files)
    no_suite = ("" if suite else "no test suite detected — looked for a `test:` target in "
                                 "Makefile, a \"test\" script in package.json, and "
                                 "test_*.py/*_test.py/tests/ among the tracked .py files")
    no_workflows = ("" if shape["workflows"]
                    else "no .github/workflows/*.yml — this repo runs no GitHub Actions")
    audit = (["pip-audit  (or: uvx pip-audit)"] if shape["py"] else []) \
        + (["npm audit --audit-level=high"] if "package.json" in " ".join(shape["manifests"]) else [])

    yield check("sec.secrets-history", "security", "blocking",
                "No secrets in git history", "pending",
                "run: gitleaks git . --no-banner --redact  (older CLI: gitleaks detect --source .)")
    yield probe("sec.dep-vulns", "security", "blocking",
                "No known-vulnerable dependencies", no_manifest,
                "run: " + ("; ".join(audit) or "pip-audit / npm audit for this ecosystem"))
    yield probe("hyg.dep-unused", "hygiene", "advisory",
                "No unused/undeclared dependencies", no_manifest,
                "run: deptry .  (or: uvx deptry .)  — Python; knip for TS repos")
    yield probe("hyg.dep-freshness", "hygiene", "advisory",
                "Dependencies reasonably fresh", no_manifest,
                "run: pip list --outdated (count) / npm outdated — rot before it is a CVE")
    findings = findings_path()
    yield probe("sec.supply-chain", "security", "advisory",
                "Dependency tree free of supply-chain risk", no_manifest,
                f"run: Trail of Bits' supply-chain-risk-auditor (trailofbits/skills, not "
                f"vendored here; install it yourself): collect.py . --json {findings}  then "
                f"render.py {findings} — covers the whole "
                "lockfile tree, abandoned upstreams, publisher concentration, "
                "install scripts")
    yield probe("sec.diff-review", "security", "advisory",
                "Recent changes security-reviewed", no_window,
                "dispatch scoped reviewer agents over the review window — SKILL.md "
                "'Reviewing the diff without the operator'. The built-in "
                "/security-review is operator-only and cannot be fired by an "
                "agent, which is why this probe used to sit pending forever. "
                "Record confirmed findings only, never raw agent output")
    yield probe("sec.sast", "security", "advisory", "Static analysis (SAST) clean",
                no_code, sast_cmd)
    yield probe("cq.lint", "code_quality", "advisory", "Linter clean", no_code, lint_cmd)
    yield probe("cq.complexity", "code_quality", "advisory", "Complexity in bounds", no_py,
                "run: ruff check --select C901 .")
    yield probe("cq.slop", "code_quality", "advisory", "No agent-slop patterns", no_py,
                "run: ruff check --select F401,F841,E722,ERA001,BLE001 . (count)")
    yield probe("sec.vendor-pins", "security", "advisory",
                "Vendored skills at reviewed pins", no_vendor_json,
                f"run: python3 {SKILL_DIR}/scripts/vendor.py verify && python3 "
                f"{SKILL_DIR}/scripts/vendor.py check  "
                "(verify = local copy unedited; check = upstream moved. "
                "Applying an update is always a human decision)")
    yield probe("cq.ratchet", "code_quality", "advisory",
                "House quality ratchet green", no_ratchet,
                f"run: python3 {ratchet_dir}/check_file_sizes.py && "
                f"python3 {ratchet_dir}/check_function_lengths.py")
    yield pending_if_unread(probe("test.suite", "tests", "blocking", "Test suite green", no_suite,
                f"run: {suite}"), suite_unread, "test-suite presence")
    yield pending_if_unread(probe("test.runtime", "tests", "advisory", "Suite runtime reasonable", no_suite,
                "record wall-clock seconds of the suite run "
                "(add --durations=15 to name the slow tests)"), suite_unread, "test-suite presence")
    yield pending_if_unread(probe("test.collection-cost", "tests", "advisory",
                "Collection is cheap", no_suite,
                "run: time pytest --collect-only -q  — collection is paid by EVERY "
                "invocation and by every parallel worker; warn over ~10 s or 15% of the run"), suite_unread, "test-suite presence")
    yield pending_if_unread(probe("test.parallel-safety", "tests", "advisory",
                "Suite is parallel-safe", no_suite,
                "only if xdist is installed: pytest -p randomly (order) and pytest -n 2, "
                "twice each — both green twice => safe; else record na with the reason"), suite_unread, "test-suite presence")
    yield probe("ci.wall-clock", "ci", "advisory", "CI wall-clock reasonable", no_workflows,
                "run: gh run list --limit 20 --json durationMs,conclusion,workflowName")


