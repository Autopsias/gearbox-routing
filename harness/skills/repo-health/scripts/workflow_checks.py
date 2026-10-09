#!/usr/bin/env python3
"""GitHub Actions workflow checks for repo-health.

One function per check, each returning a check dict. Split out of health.py so
that file stays under the house 500-LOC limit; the grouping is real, not
arbitrary — every function here reads workflow YAML and nothing else does.
"""
import re

from common import FIRST_PARTY, check, read_files, unmeasured



def na_workflow_checks(shape):
    note = "no GitHub Actions workflows" + ("; gates run via git hooks" if shape["hooks"] else "")
    for cid, layer, tier, title in (
            ("sec.workflow-permissions", "security", "blocking", "Workflow token permissions"),
            ("sec.action-pinning", "security", "blocking", "Third-party actions SHA-pinned"),
            ("sec.dangerous-workflow", "security", "blocking", "Dangerous workflow patterns"),
            ("ci.timeouts", "ci", "advisory", "Jobs set timeout-minutes"),
            ("ci.concurrency", "ci", "advisory", "Concurrency cancellation"),
            ("ci.caching", "ci", "advisory", "Dependency caching"),
            ("ci.retention", "ci", "advisory", "Artifact retention set")):
        yield check(cid, layer, tier, title, "na", note)


def wf_permissions(texts):
    bad = [w for w, t in texts.items() if not re.search(r"^permissions:", t, re.M)]
    return check("sec.workflow-permissions", "security", "blocking",
                 "Workflow token permissions",
                 "fail" if bad else "pass",
                 ("missing top-level permissions block: " + ", ".join(bad)) if bad
                 else f"all {len(texts)} workflows declare a permissions block",
                 "add `permissions: contents: read` at the top of each workflow" if bad else "")


def wf_pinning(texts):
    third, first = [], []
    for w, t in texts.items():
        for m in re.finditer(r"^\s*(?:-\s+)?uses:\s*([^\s#]+)", t, re.M):
            ref = m.group(1).strip("'\"")
            if ref.startswith(("./", "docker://")) or re.search(r"@[0-9a-f]{40}$", ref):
                continue
            (first if ref.startswith(FIRST_PARTY) else third).append(f"{w}: {ref}")
    status = "fail" if third else ("warn" if first else "pass")
    return check("sec.action-pinning", "security", "blocking",
                 "Third-party actions SHA-pinned", status,
                 "; ".join(third + first) or "every action pinned to a commit SHA",
                 "pin each action to a full commit SHA (keep the tag in a comment)"
                 if status != "pass" else "")


EVENT = "${{ github.event."
SCRIPT_KEY = re.compile(r"^(\s*(?:-\s+)?)(?:run|script):\s*(.*)$")


def event_in_script(text):
    """Is event data interpolated INSIDE a `run:` or `script:` body?

    That is where `${{ }}` is dangerous: the runner pastes the value into the
    script text before the shell (or github-script) ever parses it. In `env:`,
    `if:` and `with:` the Actions runtime evaluates it as data — and `env:` is the
    very fix this check prescribes (vendor/gha-security-review, "Key distinction").
    This used to ask only whether the FILE held both `run:` and the expression, so
    a workflow that had already moved the value into `env:` kept its warning
    forever.

    No YAML parser on purpose — the collector is stdlib-only. A body is the rest
    of the key's own line, plus, for a `|` or `>` block scalar, every following
    line indented deeper than the key.
    """
    key_col = None                      # column of the run:/script: key we are inside
    for line in text.splitlines():
        if key_col is not None:
            if not line.strip() or len(line) - len(line.lstrip()) > key_col:
                if EVENT in line:
                    return True
                continue
            key_col = None
        m = SCRIPT_KEY.match(line)
        if m:
            if EVENT in m.group(2):
                return True
            if m.group(2)[:1] in ("|", ">"):
                key_col = len(m.group(1))
    return False


def wf_danger(texts):
    danger = [w for w, t in texts.items() if "pull_request_target" in t
              and re.search(r"github\.event\.pull_request\.head|ref:\s*\$\{\{", t)]
    inject = [w for w, t in texts.items() if w not in danger and event_in_script(t)]
    status = "fail" if danger else ("warn" if inject else "pass")
    detail = (("pull_request_target + PR-head checkout: " + ", ".join(danger)) if danger
              else (("event data interpolated inside run:/script:, verify: "
                     + ", ".join(inject)) if inject
                    else "no pull_request_target/injection patterns found"))
    return check("sec.dangerous-workflow", "security", "blocking",
                 "Dangerous workflow patterns", status, detail,
                 "never check out the PR head under pull_request_target; pass event data via env"
                 if status != "pass" else "")


def wf_timeouts(texts):
    bad = [w for w, t in texts.items() if t.count("runs-on:") > t.count("timeout-minutes:")]
    return check("ci.timeouts", "ci", "advisory", "Jobs set timeout-minutes",
                 "warn" if bad else "pass",
                 ("jobs without timeout-minutes in: " + ", ".join(bad)) if bad
                 else "every job sets timeout-minutes",
                 "add timeout-minutes (~30) to each job; the default is 360" if bad else "")


def wf_concurrency(texts):
    ok = any("concurrency:" in t for t in texts.values())
    return check("ci.concurrency", "ci", "advisory", "Concurrency cancellation",
                 "pass" if ok else "warn",
                 "concurrency group present" if ok else "no workflow sets a concurrency group",
                 "" if ok else "add concurrency + cancel-in-progress to PR/push workflows")


RUNS_ON = re.compile(r"^\s*runs-on:(.*(?:\n\s*-\s.*)*)", re.M)


def runners(texts):
    """(self-hosted, unreadable, hosted) job counts across every workflow.

    Inline (`runs-on: [self-hosted, macOS]`) and list form both. "Unreadable" is
    a runner taken from a repository variable or an input — `${{ vars.X }}` — whose
    value is not in the YAML at all; the vendored quality-ratchet workflow is
    written that way, so every adopting repo has one (a repo full of
    self-hosted jobs plus this one, whose variable names the same runner). A
    `matrix.` expression is NOT unreadable in that sense: its values sit in the
    file and are nearly always hosted labels, so it counts as hosted.
    """
    self_hosted = unreadable = hosted = 0
    for t in texts.values():
        for m in RUNS_ON.finditer(t):
            r = m.group(1)
            if "self-hosted" in r:
                self_hosted += 1
            elif "${{" in r and "matrix." not in r:
                unreadable += 1
            else:
                hosted += 1
    return self_hosted, unreadable, hosted


def wf_caching(texts, shape):
    ok = any("actions/cache" in t or re.search(r"^\s*cache:", t, re.M) for t in texts.values())
    # A repo with no lockfile has nothing to cache, so it passes — but it used to
    # pass with the detail "no dependency caching found" and a fix line telling
    # the reader to add caching. A pass whose own detail reads like a failure is
    # the report lying in miniature; say which of the two passes this is.
    if ok:
        return check("ci.caching", "ci", "advisory", "Dependency caching", "pass",
                     "cache in use")
    if not shape["locks"]:
        return check("ci.caching", "ci", "advisory", "Dependency caching", "pass",
                     "no lockfile tracked — nothing to cache")
    # actions/cache carries dependencies between THROWAWAY hosted VMs. A
    # self-hosted runner keeps its own disk, so whether dependencies are cached
    # there is a fact about the machine, not the YAML — `na`, not a warning the
    # repo can only clear by adding a cache step it does not need.
    # One job that NAMES a hosted runner keeps the warning.
    self_hosted, unreadable, hosted = runners(texts)
    if self_hosted and not hosted:
        return check("ci.caching", "ci", "advisory", "Dependency caching", "na",
                     f"{self_hosted} job(s) name a self-hosted runner"
                     + (f", {unreadable} take the runner from a variable this check "
                        "cannot read" if unreadable else "")
                     + " and none names a GitHub-hosted one — a self-hosted runner's "
                     "dependency cache lives on its own disk, which workflow YAML "
                     "cannot show")
    return check("ci.caching", "ci", "advisory", "Dependency caching", "warn",
                 "lockfile tracked but no dependency caching in any workflow",
                 "enable cache: on setup-python/setup-node")


def wf_retention(texts):
    bad = [w for w, t in texts.items() if "upload-artifact" in t and "retention-days" not in t]
    return check("ci.retention", "ci", "advisory", "Artifact retention set",
                 "warn" if bad else "pass",
                 ("upload-artifact without retention-days: " + ", ".join(bad)) if bad
                 else "no unbounded artifact uploads",
                 "set retention-days on upload-artifact steps" if bad else "")


def over_what_was_read(c, texts, unread):
    """Every check above, told which of the tracked workflows it actually READ.

    `read()` returned "" for a workflow it could not open — deleted from the
    worktree mid-rebase, a sparse checkout, a dangling symlink — and the seven
    checks then measured that empty string as content. An empty file has no
    `pull_request_target`, no unpinned `uses:`, more `timeout-minutes:` than it
    needs and no `upload-artifact`, so TWO BLOCKING SECURITY CHECKS reported pass
    over bytes nothing had read, while `sec.workflow-permissions` failed on the
    same file — one scorecard contradicting itself (found by review).

    Unreadable files never reach the checks now; this folds them back in through
    the one shared rule every check that reads a set of inputs uses. Note which
    way `unmeasured` resolves "none of them opened": `pending`, never `na` — `na`
    is CLEAR to fix-routes, and routing this to it made these three blocking
    checks disappear from the fix list entirely.
    """
    named = ", ".join(sorted(unread)[:3]) + ("…" if len(unread) > 3 else "")
    return unmeasured(c, texts, unread,
                      f" ({len(unread)} tracked workflow(s) could not be read and "
                      f"were NOT measured: {named})")


def workflow_checks(repo, shape):
    if not shape["workflows"]:
        yield from na_workflow_checks(shape)
        return
    texts, unread = read_files(repo, shape["workflows"])
    for c in (wf_permissions(texts), wf_pinning(texts), wf_danger(texts),
              wf_timeouts(texts), wf_concurrency(texts), wf_caching(texts, shape),
              wf_retention(texts)):
        yield over_what_was_read(c, texts, unread)

