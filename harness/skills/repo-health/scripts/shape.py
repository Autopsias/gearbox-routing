#!/usr/bin/env python3
"""What shape is this repo? — the one manifest/dependency predicate.

Split out of health.py because every "does this repo have dependencies"
question has to have exactly ONE answer. It used to have two: is_manifest()
matched three literal filenames while ai.lockfiles ran its own
startswith("requirements") test, and the two disagreed about this
repo — requirements-ci.txt matched neither, and five dependency checks vanished
from the scorecard as a result.

Imports common only: health.py imports this, never the other way round.
"""
import re
from pathlib import Path

from common import excluded, quality_excludes, read_or_none, read_or_unread, run


def tracked_files(repo):
    # -z: every path comes back verbatim. Without it git C-quotes any name with a
    # non-ASCII byte (`"docs/\342\200\234report\342\200\235.pdf"`), and
    # that quoted string is not a path: hyg.large-files could not stat it and
    # reported the file as "could not be measured".
    # Every check that opens a tracked file reads this list.
    rc, out = run(["git", "ls-files", "-z"], repo)
    return [f for f in out.split("\0") if f] if rc == 0 else []


# Renovate's pip_requirements manager matches requirements files by BASENAME, not
# by the literal string "requirements.txt" — which is why this repo's
# requirements-ci.txt used to read as "no dependencies at all" and took five
# dependency checks off the scorecard with it (observed).
# Source: docs.renovatebot.com/modules/manager/pip_requirements
REQ_NAME = re.compile(r"^[\w-]*requirements([-._]\w+)?\.(txt|pip)$")
# Dependabot's companion filenames — the ones a basename regex cannot express.
PLAIN_MANIFESTS = ("package.json", "setup.py", "Pipfile")
LOCK_NAMES = ("uv.lock", "poetry.lock", "package-lock.json", "pnpm-lock.yaml",
              "yarn.lock")
# Structural, and NOT negotiable by config: quality_excludes() reads
# [tool.claude-quality] from pyproject.toml and returns () for every repo that has
# no such section — i.e. every OTHER repo this vendored script runs in. Dependency
# shape must never depend on that section existing.
VENDOR_PARTS = {"vendor", "third_party", "third-party", "node_modules"}
_OPS = r"===|==|>=|<=|~=|!=|<|>"
_REQ_SPEC = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._-]*"                    # distribution name
    r"(\[[A-Za-z0-9._,-]+\])?"                       # extras
    rf"(({_OPS})[^,]+(,({_OPS})[^,]+)*)?"            # optional version spec
    r"(@\S+)?")                                      # direct URL reference
# pip's OWN syntax: the five short flags it defines, glued to their dash, and any
# long flag. Deliberately NOT a bare leading "-": accepting that made a markdown
# bullet ("- Access is restricted to …") read as a requirement line, which is the
# cdxgen #666 false positive this module claims to block, and made every
# `--hash=sha256:…` continuation count as a dependency (both observed).
_PIP_OPTION = re.compile(r"(-[cefir]|--[a-z][a-z-]+)([=\s]|$)")
_EDITABLE = re.compile(r"^(-e|--editable)[=\s]+")
# A VCS reference — bare, or as the right-hand side of a PEP 508 `name @ …`.
# _REQ_SPEC cannot match one (the "+" is not in a distribution name), so without
# this a file of nothing but VCS references was not a manifest at all, and the
# least-pinned dependency there is never appeared as unpinned.
_VCS = re.compile(r"(^|@)(git|hg|bzr|svn)\+[a-z+]*://")
_VCS_COMMIT = re.compile(r"@[0-9a-f]{40}(?![0-9a-f])")
MANIFEST_LOOKED_FOR = (
    "looked for requirements*.txt/.pip whose every non-comment line is pip "
    "syntax, package.json, setup.py, Pipfile, setup.cfg with install_requires, "
    "pyproject.toml with [project]/[tool.poetry] — outside vendor/, "
    "third_party/ and node_modules/")


def vendored(f):
    """Third-party code living in this tree. Never this repo's own dependencies."""
    return any(p in VENDOR_PARTS for p in Path(f).parts[:-1])


def _informative(ln):
    """One raw line reduced to what it actually says, or "" if it says nothing.

    Comments go (pip's rule: `#` at the start of the line or after whitespace, so
    the `#egg=` fragment of a VCS URL survives), and so does a trailing
    line-continuation backslash — pip-compile writes every hash-pinned
    requirement across several lines and the first one carries the version.
    """
    ln = re.sub(r"(^|\s)#.*$", "", ln).strip()
    return ln[:-1].strip() if ln.endswith("\\") else ln


def _pip_option(ln):
    """Is this line one of pip's own directives — `-r`, `-e`, `--hash=…`?

    Evidence that a file is a requirements file, and NEVER a dependency: see
    _requirement_spec for why those have to be two different questions.
    """
    return bool(_PIP_OPTION.match(ln))


def _requirement_spec(ln):
    """The dependency this line declares, normalised — or "" if it declares none.

    This is the SECOND of the two questions a requirements line can be asked, and
    it is not the same question as _pip_option's. `-r base.txt` and
    `--hash=sha256:…` are pip syntax but they are not dependencies; one predicate
    answering both made a fully hash-pinned file — the safest kind there is —
    report as unpinned (observed).

    `-e <thing>` is unwrapped first, because the thing after it IS a dependency
    when it is a VCS URL (`-e git+https://…@main`) and is the repo itself (`.`)
    otherwise, which drops out on its own for failing to parse.
    """
    ln = _EDITABLE.sub("", _informative(ln))
    if not ln or _pip_option(ln):
        return ""
    ln = ln.split(";", 1)[0].strip()                 # environment marker
    ln = re.sub(rf"\s*({_OPS})\s*", r"\1", ln)       # "pytest == 9" -> "pytest==9"
    ln = re.sub(r"\s*\[", "[", ln)                   # "black [jupyter]" -> extras
    ln = re.sub(r"\s*@\s*", "@", ln, count=1)        # PEP 508 direct reference
    if re.search(r"\s", ln):                         # prose: leftover whitespace
        return ""
    return ln if _VCS.search(ln) or _REQ_SPEC.fullmatch(ln) else ""


def _pinned(spec):
    """Does this dependency name exactly one thing?

    A VCS reference is pinned only by a full commit sha — a branch or tag ref
    resolves to different code tomorrow, and `@main` is the least pinned thing a
    requirements file can hold. Anything else needs `==`/`===` or a direct URL,
    which names one artifact.
    """
    if _VCS.search(spec):
        return bool(_VCS_COMMIT.search(spec))
    return "==" in spec or "@" in spec


def is_requirements(repo, f):
    """A pip requirements file: Renovate's basename pattern + a content sniff.

    The basename regex alone also matches prose — `functional_requirements.txt`
    is the canonical false positive (cdxgen #666). The sniff is that EVERY
    informative line is pip syntax, not merely one of them: a single sentence,
    markdown bullet, or heading disqualifies the file. One-line-is-enough was
    what let a bullet list read as a manifest.

    ponytail: a file of nothing but bare distribution names (`requests\\nflask`)
    is indistinguishable from a list of one-word headings and is taken as a
    manifest. That is deliberate — bare-name requirements files are common and
    real, and the failure direction is extra checks rather than the five that
    vanished. Tighten only if a real repo trips it.
    """
    if vendored(f) or not REQ_NAME.match(Path(f).name):
        return False
    body = read_or_none(repo, f)
    if body is None:
        # Tracked, named like a requirements file, and it would not OPEN, so the
        # content sniff never ran. It counts as a manifest on purpose: `read()`
        # returned "" for an unreadable file, no line was pip syntax, and five
        # dependency checks then left the board saying "no dependency manifest
        # tracked" about a file that is tracked (found by review).
        # Same failure direction the ponytail note above already chose — extra
        # checks, never the five that vanished.
        return True
    pip_syntax = False
    for raw in body.splitlines():
        ln = _informative(raw)
        if not ln:
            continue
        if not (_pip_option(ln) or _requirement_spec(ln)):
            return False
        pip_syntax = True
    return pip_syntax


def is_manifest(repo, f):
    """THE answer to "is this a dependency manifest of this repo".

    Every manifest question in the collector routes here. Two predicates that
    disagree is how a repo ends up with dependency checks that contradict each
    other — ai.lockfiles used to run its own `startswith("requirements")` test.
    """
    if vendored(f):
        return False
    name = Path(f).name
    if name in PLAIN_MANIFESTS:
        return True
    # `read_or_none`, not `read`: a tracked manifest that will not open is
    # unmeasured, not empty, and it counts as a manifest for the reason
    # is_requirements gives above.
    if name == "pyproject.toml":
        body = read_or_none(repo, f)
        return body is None or "[project]" in body or "[tool.poetry]" in body
    if name == "setup.cfg":
        body = read_or_none(repo, f)
        return body is None or "install_requires" in body
    return is_requirements(repo, f)


def is_lockfile(f):
    return not vendored(f) and Path(f).name in LOCK_NAMES


def detect_shape(repo, files):
    # "Is this OUR source?" is the repo's own question, and the quality ratchet
    # already answers it — so shape detection reads the same canonical exclude
    # list the ratchet and ruff use. Without this, a vendored third-party skill
    # that ships its own pyproject.toml makes the whole repo look like a Python
    # package with dependencies, and repo-health audits somebody else's
    # dependency list as if it were ours (a vendored skill's
    # scripts/pyproject.toml + uv.lock).
    excl = quality_excludes(repo)
    ours = [f for f in files if not excluded(f, excl)]

    py = [f for f in ours if f.endswith(".py")]
    js = [f for f in ours if Path(f).suffix in {".ts", ".tsx", ".js", ".jsx"}]
    # Workflows are the one exemption: they live under `.github/` by definition,
    # and excluded() drops any path with a dot-directory component. Filtering
    # them the same way would make every repo look like it has no CI.
    wfs = [f for f in files if re.match(r"\.github/workflows/.*\.ya?ml$", f)]
    manifests = [f for f in ours if is_manifest(repo, f)]
    locks = [f for f in ours if is_lockfile(f)]
    hooks = any(f.startswith((".pre-commit-config", "githooks/")) for f in files)
    return {"py": py, "js": js, "workflows": wfs, "manifests": manifests,
            "locks": locks, "hooks": hooks}


def unpinned_requirements(repo, files, tracked=None):
    """(files with an unpinned dependency, files that could not be READ).

    EVERY file `is_manifest` claims is opened here, not just requirements-style
    ones: this used to filter on `is_requirements(repo, f)` FIRST, and that
    predicate is a BASENAME test — "pyproject.toml" and "setup.cfg" never match
    it, so an unopenable one of those was never even attempted and never reached
    `unread`. `is_manifest` already counts an unreadable tracked pyproject.toml
    or setup.cfg AS a manifest (its own "unreadable still counts" rule), so
    `ai.lockfiles` saw it in `shape["manifests"]` and, finding it neither read
    nor named, called the sibling lockfile sufficient: an unlinked tracked
    pyproject.toml with `requests>=2.0` read "lockfile: uv.lock — pass" (found by
    review). `read_or_unread`, not `read_or_none`: it is the one that
    tells present-but-unopenable apart from absent (dangling symlink, unlinked
    from a sparse checkout), and `tracked` — the caller's tracked-file list — is
    the other half of what "present" means.

    Asks _requirement_spec, NOT the file-detection predicate, for WHICH deps are
    unpinned: comments, `-r` includes and `--hash=` continuations are pip syntax
    that makes a file count as a manifest, and none of them is a dependency that
    can be unpinned. Only a requirements-style file is asked this question at
    all — pyproject.toml/setup.cfg declare dependencies in a grammar this
    predicate does not parse, so an OPENED one of those is simply never asked,
    same as before this fix; it is `unread` only when it would not open.
    """
    out, unread = [], []
    for f in files:
        body = read_or_unread(repo, f, unread, tracked)
        if f in unread or not is_requirements(repo, f):
            continue
        specs = [s for s in (_requirement_spec(ln) for ln in body.splitlines()) if s]
        if any(not _pinned(s) for s in specs):
            out.append(f)
    return out, unread
