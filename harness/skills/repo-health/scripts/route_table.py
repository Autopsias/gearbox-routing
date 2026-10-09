#!/usr/bin/env python3
"""The ROUTES data table — split out of routes.py to stay under the house
500-line-per-file limit.

`build_routes` takes the three route-builders (`cmd`/`agent`/`operator`) and the
two values the table quotes directly (`SKILL_DIR`, `LADDER`) as ARGUMENTS rather
than importing them from routes.py: a naive split — this file importing routes.py
for the builders, routes.py importing this file for ROUTES — is a cycle, and
the builders cannot simply move here instead, because
test_a_route_reports_the_flags_its_deployed_target_does_not_declare
monkeypatches `routes.COMMANDS` and expects `routes.cmd` to observe it; a copy
of `cmd()` living in a different module would close over that module's own
`COMMANDS` and never see the patch. So the builders stay in routes.py, and this
file stays a pure function of them — a one-way edge, routes.py -> route_table.py,
never the other way round.

`build_routes` itself is split one builder per table section (security/CI/test
speed/hygiene/AI-readiness) so no single function crosses the house 100-line
function-length ratchet; each section builder is a pure function of the same
five arguments and returns its slice of the dict, and `build_routes` just
merges the slices in the original check-id order.
"""


def _blocking_routes(cmd, agent, operator, SKILL_DIR):
    """The checks that block: secrets, dep vulns, workflow hardening, suite health."""
    return {
        "sec.secrets-history": agent(
            "general-purpose",
            "gitleaks found secrets in git history. For each finding: identify the "
            "file and the credential kind, `git rm --cached` the file if it is still "
            "tracked, add it to .gitignore, and report the exact secrets found (kind "
            "and location only, NEVER the value). Do not rewrite history.",
            operator="rotate every leaked credential; history rewrite is a human call"),
        "sec.tracked-sensitive": agent(
            "general-purpose",
            "A secret-bearing file is tracked (.env / *.pem / id_rsa* / *.p12 / "
            "*.pfx). `git rm --cached` it, add a matching .gitignore rule, and "
            "report the paths. Never print the file contents.",
            operator="rotate the credential before anything else"),
        "sec.dep-vulns": agent(
            "security-scanner",
            "--mode=fix. A dependency audit (pip-audit / npm audit) reported "
            "high/critical advisories WITH a fix available. Upgrade to the fixed "
            "versions, re-run the audit, and report the before/after counts."),
        "sec.workflow-permissions": agent(
            "ci-infrastructure-builder",
            "Add a least-privilege top-level `permissions:` block to every GitHub "
            "Actions workflow that lacks one (`contents: read` for a test job). "
            "Change nothing else."),
        "sec.action-pinning": agent(
            "ci-infrastructure-builder",
            "Pin every `uses:` action reference to a full 40-hex commit SHA with the "
            "version in a trailing comment. Resolve each SHA from the upstream repo — "
            "never invent one. Third-party actions first, first-party after."),
        "sec.dangerous-workflow": agent(
            "ci-infrastructure-builder",
            "Fix the expression-injection / pull_request_target class in the "
            f"workflows. Read {SKILL_DIR}/vendor/gha-security-review/SKILL.md first "
            "and trace the attack path before editing; pass ${{ github.event.* }} "
            "through `env:` instead of interpolating it into `run:`."),
        # --run-first, not --fix: test-orchestrate has no --fix (the argument would
        # be read as its `test_scope` positional). Fixing is what the command does
        # by default; --run-first is what stops it answering from a stale cache,
        # which is the whole point when the collector just watched the suite go red.
        "test.suite": cmd("test-orchestrate", "--run-first"),
    }


def _quality_routes(cmd, agent):
    """Advisory code-quality rows — same table so the dashboard and docs share a source."""
    return {
        "cq.file-size": cmd("code-quality", "--fix"),
        # --focus (one of the command's own file-size|function-length|complexity),
        # not a bare --fix: unfocused, safe-refactor takes whichever it meets first.
        "cq.function-length": cmd("code-quality", "--fix --focus=function-length"),
        "cq.complexity": cmd("code-quality", "--fix --focus=complexity"),
        "cq.ratchet": cmd("code-quality", "--adopt"),
        "cq.lint": agent("linting-fixer",
                         "Run the repo's linter (ruff / eslint) and fix every "
                         "finding. Apply safe autofixes first, then the rest by hand."),
        "cq.slop": agent("linting-fixer",
                         "Clear the agent-slop rule set: ruff F401, F841, E722, "
                         "ERA001, BLE001. Delete dead code rather than annotating it."),
    }


def _security_advisory_routes(agent, operator, SKILL_DIR):
    """Advisory security rows: SAST, diff review, supply chain, vendoring, dependabot."""
    return {
        "sec.sast": agent("security-scanner",
                          "--mode=fix over the semgrep/bandit findings. Triage in "
                          "context first: fix confirmed issues, annotate the rest."),
        # Only CONFIRMED findings are ever recorded (SKILL.md's false-positive bar
        # runs before `health.py set`), so this route fixes; it does not re-triage.
        "sec.diff-review": agent(
            "security-scanner",
            "--mode=fix. The scoped reviewers confirmed an exploitable finding in "
            "the recent-changes window and the check detail names it. Each "
            "confirmed finding carries a file, a line, the path attacker-controlled "
            "input takes to reach it, and the impact — close that path and say how. "
            "Everything that did not clear that bar was already dropped, so do not "
            "re-triage and do not widen the scope beyond the window."),
        # No agent may swap a library for the repo: the remedies here are all
        # dependency decisions, and picking the replacement is exactly the call
        # CLAUDE.md reserves for a human (maintained + widely used, vetted).
        "sec.supply-chain": operator(
            "every remedy is a dependency decision rather than an edit — drop the "
            "package, replace it with a maintained equivalent, vendor it, or accept "
            "the risk. An agent choosing the replacement is how an unvetted "
            "dependency gets in"),
        # vendor.py has verify/check/diff and deliberately no `update`: applying a
        # vendored update is a human decision by the vendoring skill's own invariant.
        "sec.vendor-pins": operator(
            "the vendored copy was edited locally, or upstream moved. Read what "
            f"changed with `python3 {SKILL_DIR}/scripts/vendor.py diff <name>` "
            "(<name> is the vendored skill the check detail names), then re-vendor "
            "and move the pin yourself — applying a vendored update is always a "
            "human decision, which is why vendor.py has no `update`"),
        "sec.dep-update-config": agent(
            "ci-infrastructure-builder",
            "No automated dependency updates. Add .github/dependabot.yml covering "
            "every ecosystem this repo tracks a manifest for, plus github-actions, "
            "on a weekly schedule. Read the manifests to get the directories and "
            "package-ecosystem names right. Add nothing else."),
    }


def _ci_routes(cmd, agent):
    """CI workflow hardening rows: timeouts, concurrency, caching, gating."""
    return {
        # ci-orchestrate's fix switch is --fix-all, and --check-actions is what
        # scopes it to GitHub Actions WORKFLOW fixes rather than to failing test
        # gates — which is what these four checks are about. The leading text is the
        # command's `issue` positional, so each row says which workflow gap it means
        # instead of four rows arriving with identical arguments.
        "ci.timeouts": cmd("ci-orchestrate",
                           "jobs without timeout-minutes --check-actions --fix-all"),
        "ci.concurrency": cmd("ci-orchestrate",
                              "no workflow sets a concurrency group --check-actions --fix-all"),
        "ci.caching": cmd("ci-orchestrate",
                          "lockfile tracked but no dependency caching --check-actions --fix-all"),
        "ci.retention": cmd("ci-orchestrate",
                            "upload-artifact steps without retention-days --check-actions --fix-all"),
        "ci.wall-clock": cmd("ci-orchestrate", "CI wall-clock too slow --strategic"),
        "ci.server-side-gate": agent(
            "ci-infrastructure-builder",
            "Add ONE workflow that calls the same contract the local gate runs "
            "(`make check` / `make test`) — never a second copy of the commands. "
            "Obey SKILL.md's CI rules: top-level least-privilege permissions, "
            "SHA-pinned actions, timeout-minutes, concurrency, measured path filters."),
        "ci.pre-commit": agent(
            "ci-infrastructure-builder",
            "Adopt pre-commit (or a githooks dir wired via core.hooksPath) running "
            "the repo's existing lint/test contract. Do not invent new checks."),
    }


def _test_speed_routes(agent, LADDER):
    """The three test/CI speed rows — share one prompt prefix (LADDER)."""
    return {
        "test.runtime": agent(
            "ci-infrastructure-builder",
            LADDER + "The suite is slow. Rung 1 first: time the run and the "
            "collection separately, because which of the two is slow decides "
            "everything after it. Take the rung-2 static wins before you propose "
            "parallelism, and report the before/after seconds for both numbers."),
        "test.collection-cost": agent(
            "ci-infrastructure-builder",
            LADDER + "Collection is expensive. Every xdist worker re-imports every "
            "test module, so this cost is MULTIPLIED by parallelism rather than "
            "divided by it — which is why it is rung 2 and parallelising first "
            "makes the suite slower. Move module-level work in test files into "
            "fixtures, set `testpaths`, disable plugin autoload in the fast "
            "profile, drop heavy unused imports. Re-time collection and report it."),
        "test.parallel-safety": agent(
            "ci-infrastructure-builder",
            LADDER + "The suite is not parallel-safe. That is rung 3, and rung 4 "
            "stays closed until it passes: find the shared state — a fixed tmp "
            "path, a hardcoded port, one shared database, module-level globals "
            "mutated by tests — and give each worker its own (tmp_path, port 0, a "
            "per-worker DB name). Prove it with random order and `-n 2`, twice "
            "each; both green twice or it is not fixed. Do not add `-n auto`."),
    }


def _hygiene_routes(agent, operator):
    """Repo-hygiene rows: TODOs, tracked junk, large files, README, staleness, deps."""
    return {
        # No command fixes TODO density, so this row says so rather than spending a
        # budget to change nothing. `/declutter` was the old route: it is read-only
        # by invariant ("never edits, never deletes"), declares no --fix, and its
        # fan-out is expensive on a mid-size repo.
        "hyg.todo-density": operator(
            "triage the markers yourself: each TODO/FIXME is either work to schedule "
            "or a stale line to delete, and only the owner knows which. No command "
            "decides that — /declutter is read-only and has no --fix"),
        "hyg.tracked-junk": agent(
            "general-purpose",
            "`git rm --cached` the tracked build junk named in the check detail and "
            "extend .gitignore so it cannot come back. Never delete the working copy."),
        # Not hyg.tracked-junk's route: junk is junk at any size, but a >5 MB file
        # is as likely to be a needed fixture or model as a mistake, and all three
        # remedies change how the repo is cloned.
        "hyg.large-files": operator(
            "decide what the tracked file over 5 MB actually IS before anything "
            "moves it: a needed asset belongs in Git LFS or a release attachment, a "
            "mistake gets untracked, and either way the blob stays in history until "
            "somebody rewrites it. Only the owner can tell an asset from an "
            "accident, and all three answers change how the repo is cloned"),
        "hyg.readme": agent(
            "general-purpose",
            "The README has no runnable quickstart. Read the repo's Makefile, "
            "package.json, pyproject.toml and CI workflows for the commands that "
            "actually work, then add a quickstart with the exact setup, run and "
            "test commands — copied from those files, never invented. RUN each "
            "command you write down; drop any that does not work rather than "
            "documenting it."),
        # A dormant repo is a fact about the project, not a defect in the tree.
        "hyg.activity": operator(
            "nothing in the tree fixes this — the date of the last commit is a fact "
            "about the project, not a defect in the code. Archive the repo, hand it "
            "to someone, or read the row as the reminder it is"),
        "hyg.unmerged-work": operator(
            "merging or deleting a branch publishes or destroys work whose value "
            "only its author knows, and a dropped stash is not recoverable. Triage "
            "the list yourself: `git log <branch>` and `git stash show -p` say what "
            "is in each one"),
        "hyg.notebook-outputs": agent(
            "general-purpose",
            "Notebook outputs are committed, and secrets hide in output JSON. Strip "
            "them with nbstripout on the notebooks named in the check detail, then "
            "wire nbstripout into the repo's existing hook config so they cannot "
            "come back. Do not touch a single source cell. If an output was a "
            "credential, say so — rotating it is the owner's job."),
        "hyg.dep-unused": agent(
            "general-purpose",
            "deptry (Python) or knip (TS) reported unused and/or undeclared "
            "dependencies. Remove each unused one from the manifest, and DECLARE "
            "each undeclared one the code really imports — an import that works "
            "only because something else pulls it in transitively is a break "
            "waiting for that something else to drop it. Re-run the tool and the "
            "test suite, and report the before/after counts."),
        "hyg.dep-freshness": agent(
            "general-purpose",
            "Dependencies have gone stale — rot before it is a CVE. Upgrade every "
            "one whose newer release stays inside the manifest's declared "
            "constraint, refresh the lockfile with the repo's own tool (never by "
            "hand), run the test suite, and report before/after. List the "
            "major-version bumps you did not take; do not take them.",
            operator="major-version bumps break APIs — decide which are worth it"),
    }


def _ai_readiness_routes(agent, operator):
    """AI-readiness rows: AGENTS.md, a single verify command, reproducible installs."""
    return {
        # /init is a BUILT-IN slash command. Built-ins are not files under
        # ~/.claude/commands, so there is nothing for a subagent to read and follow —
        # this is the one route the probe could not make self-dispatchable.
        "ai.agents-md": operator(
            "run /init yourself, then trim the result to commands + conventions"),
        "ai.verify-command": agent(
            "ci-infrastructure-builder",
            "There is no single command an agent can run to verify a change. Add "
            "ONE `make check` target that calls the repo's EXISTING lint, type and "
            "test commands — never a new check, and never a second copy of the "
            "commands: call the same entry points CI and the hooks already call. "
            "Then name it in AGENTS.md/CLAUDE.md so an agent finds it."),
        "ai.lockfiles": agent(
            "general-purpose",
            "Dependency resolution is not reproducible. If the lockfile is missing, "
            "generate it with the tool the repo already uses (uv lock, npm install "
            "--package-lock-only, poetry lock) and commit it — never hand-write a "
            "lockfile. If the check detail names unpinned requirements, pin each to "
            "the version currently resolved in the environment the suite passes in. "
            "Run the suite afterwards; a pin that breaks it is the wrong pin."),
    }


def build_routes(cmd, agent, operator, SKILL_DIR, LADDER):
    """Keyed on the exact check id health.py emits (test_health_shape.py holds the
    frozen id set, so a rename breaks there first). An id with no entry falls to
    UNKNOWN — which is the right answer for any check somebody added without
    deciding who fixes it. Advisory ids reach it too, under `--advisory`.

    One builder per table section (blocking / quality / security-advisory / CI /
    test-speed / hygiene / AI-readiness), merged here in the original check-id
    order — see this module's docstring for why the split is by section rather
    than by anything else.
    """
    return {
        **_blocking_routes(cmd, agent, operator, SKILL_DIR),
        **_quality_routes(cmd, agent),
        **_security_advisory_routes(agent, operator, SKILL_DIR),
        **_ci_routes(cmd, agent),
        **_test_speed_routes(agent, LADDER),
        **_hygiene_routes(agent, operator),
        **_ai_readiness_routes(agent, operator),
    }
