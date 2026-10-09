#!/usr/bin/env python3
"""trust_ladder.py — the countable half of the trust-ladder review.

A repo records what it has learned not to do in two kinds of place: prose (a
rule, a memory note) that a model may or may not read, and checks (a lint, a
test, a hook) that refuse the mistake whatever anyone reads. This script counts
both, so the review states numbers instead of guessing them:

  - prose layer: CLAUDE.md, AGENTS.md, .claude/rules/*.md (plus rules/*.md
    when the repo is itself a Claude Code config tree) and the project's
    Claude Code memory notes (files and bytes);
  - mechanical layer: lint, type, pre-commit, CI, hook and custom-check files,
    and the test file count;
  - graduation candidates: prose lines that say NEVER or ALWAYS and name a
    grep-able token (a backticked command, flag, path or pattern) — the rules a
    check could enforce instead;
  - fix and revert history: commits in the last 90 days whose subject says fix,
    revert or hotfix;
  - docs drift: backticked paths in CLAUDE.md, AGENTS.md and README.md that do
    not resolve (the same claim rule a doc-path checker uses, without waivers);
  - verification base: .claude/launch.json, a Makefile check target, an e2e or
    integration test directory.

TODO density and the one-command verification loop are health checks already
(`hyg.todo-density`, `ai.verify-command`); this script names them, never
recounts them.

The memory folder follows the Claude Code convention: every character of the
resolved repo path that is not a letter or digit becomes "-", under
~/.claude/projects/<slug>/memory. A folder that does not exist reports null
counts, never 0 — "not found" is not "no notes".

Usage:
    python3 trust_ladder.py <repo>                  # text report
    python3 trust_ladder.py <repo> --json           # machine-readable report
    python3 trust_ladder.py <repo> --memory-dir DIR # memory notes from DIR
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import excluded, quality_excludes, run  # noqa: E402
from shape import tracked_files, vendored  # noqa: E402

WINDOW_DAYS = 90
RULE_WORD = re.compile(r"\b(never|always)\b", re.I)
BACKTICK = re.compile(r"`([^`\n]+)`")
LINE_SUFFIX = re.compile(r":\d+(?:[-:]\d+)*$")
PATH_EXTENSIONS = {".py", ".sh", ".md", ".json", ".yaml", ".yml", ".txt",
                   ".html", ".js", ".ts", ".tsx", ".jsx", ".toml", ".cfg",
                   ".ini", ".ndjson", ".jsonl"}
TEST_FILE = re.compile(r"(^test_.*\.py$|_test\.py$|\.(test|spec)\.[jt]sx?$)")
CUSTOM_CHECK = re.compile(r"^(check|lint|verify)[_-].+\.(py|sh|js|ts)$"
                          r"|[_-](check|lint|gate)\.(py|sh|js|ts)$")
E2E_PARTS = {"e2e", "integration", "integration_tests", "integration-tests"}
SEE_ALSO = {
    "hyg.todo-density": "TODO/FIXME markers in source — read that health row",
    "ai.verify-command": "one command that runs lint + tests — read that health row",
}


def memory_slug(repo):
    """Claude Code's project folder name: every non-alphanumeric char -> '-'."""
    return re.sub(r"[^A-Za-z0-9]", "-", str(Path(repo).resolve()))


def default_memory_dir(repo):
    return Path.home() / ".claude" / "projects" / memory_slug(repo) / "memory"


def _files_bytes(paths):
    paths = [p for p in paths if p.is_file()]
    return {"files": len(paths), "bytes": sum(p.stat().st_size for p in paths)}


def claude_roots(repo):
    """Where the repo keeps its Claude Code rules and hooks.

    `.claude/` always; the repo root too when the repo IS a Claude Code config
    tree (a source clone of ~/.claude: settings.json beside agents/, commands/
    or skills/), whose rules/ and hooks/ sit at the top level.
    """
    roots = [repo / ".claude"]
    if (repo / "settings.json").is_file() and any(
            (repo / d).is_dir() for d in ("agents", "commands", "skills")):
        roots.append(repo)
    return roots


def _rel(repo, p):
    """Repo-relative when p is inside the repo; absolute otherwise.

    core.hooksPath may point outside the checkout (a worktree sharing the main
    clone's hooks), so a path beyond the repo is reported as it is, not refused.
    """
    try:
        return str(p.relative_to(repo))
    except ValueError:
        return str(p)


def prose_sources(repo, memory_dir):
    """{group: [Path]} for the prose layer; the memory group is None if absent."""
    return {
        "CLAUDE.md": [repo / "CLAUDE.md"],
        "AGENTS.md": [repo / "AGENTS.md"],
        "rules": sorted(p for root in claude_roots(repo) for p in (root / "rules").glob("*.md")),
        "memory": sorted(memory_dir.glob("*.md")) if memory_dir.is_dir() else None,
    }


def prose_inventory(sources):
    out = {}
    for group, paths in sources.items():
        out[group] = ({"files": None, "bytes": None} if paths is None
                      else _files_bytes(paths))
    return out


def _has_toml_section(path, section):
    try:
        # Line-anchored: a comment that mentions "[tool.mypy]" is not the section.
        return re.search(rf"^\s*\[{re.escape(section)}[\].]", path.read_text(errors="ignore"), re.M) is not None
    except OSError:
        return False


def _git_hooks(repo):
    rc, out = run(["git", "config", "core.hooksPath"], repo)
    d = (repo / out.strip()) if rc == 0 and out.strip() else None
    return sorted(_rel(repo, p) for p in d.glob("*") if p.is_file()) if d and d.is_dir() else []


def _claude_hooks(repo):
    return sorted(_rel(repo, p) for root in claude_roots(repo) for p in (root / "hooks").glob("*")
                  if p.is_file() and p.suffix != ".md" and not p.name.startswith("test_"))


def _named(files, *names):
    """Tracked files with one of these basenames, at any depth (a monorepo keeps
    its lint config beside the app it lints, e.g. apps/api/ruff.toml)."""
    return sorted(f for f in files if Path(f).name in names)


def _sections(repo, files, name, section):
    return [f"{f} [{section}]" for f in _named(files, name) if _has_toml_section(repo / f, section)]


def _plan_record(f):
    """A plan's own record (_plans/, _evidence/): one-off probes, not standing checks."""
    return any(part in ("_plans", "_evidence") for part in Path(f).parts)


def mechanical_inventory(repo, files):
    """{kind: [repo-relative path]} for every check-shaped config in the repo."""
    excl = quality_excludes(repo)
    files = [f for f in files if not vendored(f) and not excluded(f, excl)]
    found = {
        "ruff": _named(files, "ruff.toml", ".ruff.toml")
        + _sections(repo, files, "pyproject.toml", "tool.ruff"),
        "eslint": sorted(f for f in files if Path(f).name.startswith((".eslintrc", "eslint.config."))),
        "mypy": _named(files, "mypy.ini", ".mypy.ini")
        + _sections(repo, files, "pyproject.toml", "tool.mypy")
        + _sections(repo, files, "setup.cfg", "mypy"),
        "tsc": _named(files, "tsconfig.json"),
        "pre-commit": [n for n in (".pre-commit-config.yaml",) if (repo / n).is_file()]
        + [n + "/" for n in (".githooks", ".husky") if (repo / n).is_dir()] + _git_hooks(repo),
        "ci": sorted(f".github/workflows/{p.name}" for p in (repo / ".github" / "workflows").glob("*.y*ml"))
        + ([".gitlab-ci.yml"] if (repo / ".gitlab-ci.yml").is_file() else []),
        "claude-hooks": _claude_hooks(repo),
        "custom-checks": sorted(f for f in files if not Path(f).name.startswith("test_")
                                and not _plan_record(f)
                                and (f.startswith("scripts/quality/")
                                     or CUSTOM_CHECK.search(Path(f).name))),
    }
    tests = sum(1 for f in files if TEST_FILE.search(Path(f).name))
    return {"configs": found, "test_files": tests}


def _grep_token(span):
    """The kind of grep-able thing a backticked span names, or None."""
    s = span.strip()
    if len(s) < 2:
        return None
    if s.startswith("-"):
        return "flag"
    if "/" in s or s.startswith((".", "~")) or Path(s).suffix.lower() in PATH_EXTENSIONS:
        return "path"
    if re.search(r"[*?\[\]|\\^$]", s):
        return "pattern"
    if " " in s:
        return "command"
    return None


def graduation_candidates(repo, sources):
    """Prose lines with NEVER/ALWAYS that name at least one grep-able token."""
    out = []
    for group, paths in sources.items():
        for path in paths or []:
            if not path.is_file():
                continue
            label = f"memory/{path.name}" if group == "memory" else str(path.relative_to(repo))
            for n, line in enumerate(path.read_text(errors="ignore").splitlines(), 1):
                if not RULE_WORD.search(line):
                    continue
                tokens = [t for t in BACKTICK.findall(line) if _grep_token(t)]
                if tokens:
                    out.append({"file": label, "line": n, "tokens": tokens,
                                "text": line.strip()[:200]})
    return out


def _classify(subject):
    s = subject.lower()
    if re.search(r"\brevert", s):
        return "revert"
    if "hotfix" in s:
        return "hotfix"
    if re.search(r"\bfix(e[sd])?\b", s):
        return "fix"
    return None


def fix_history(repo):
    """Counts over the last WINDOW_DAYS; None when the repo is not a git work tree."""
    rc, _ = run(["git", "rev-parse", "--is-inside-work-tree"], repo)
    if rc != 0:
        return None
    counts = {"window_days": WINDOW_DAYS, "commits": 0, "fix": 0, "revert": 0, "hotfix": 0}
    rc, _ = run(["git", "rev-parse", "--verify", "-q", "HEAD"], repo)
    if rc != 0:
        return counts  # no commits yet: a real zero
    rc, out = run(["git", "log", f"--since={WINDOW_DAYS}.days", "--format=%s"], repo)
    if rc != 0:
        return None
    for subject in out.splitlines():
        counts["commits"] += 1
        kind = _classify(subject)
        if kind:
            counts[kind] += 1
    return counts


def _path_claims(line):
    """Backticked words on one line that claim a repo-relative path."""
    for span in BACKTICK.findall(line):
        for word in span.split():
            w = word.lstrip("\"'([{").rstrip(",.;:)]}\"'").split("#", 1)[0]
            w = LINE_SUFFIX.sub("", w)  # `src/app.py:12` claims src/app.py
            if not w or "://" in w or re.search(r"[<>*$]", w) or w.startswith(("~", "/")):
                continue
            if "/" in w or Path(w).suffix.lower() in PATH_EXTENSIONS:
                yield w


def docs_drift(repo, files, memory_dir=None):
    """Backticked paths in the three front docs that resolve nowhere.

    A bare file name that is one of the project's memory notes resolves: CLAUDE.md
    points at its notes by name (`feedback_x.md`), and they live outside the repo.
    """
    notes = {p.name for p in memory_dir.glob("*.md")} if memory_dir and memory_dir.is_dir() else set()
    dirs = {str(Path(f).parent) for f in files} | {f.split("/", 1)[0] for f in files}
    names = {Path(f).name for f in files}
    checked, broken = 0, []
    for doc in ("CLAUDE.md", "AGENTS.md", "README.md"):
        path = repo / doc
        if not path.is_file():
            continue
        for n, line in enumerate(path.read_text(errors="ignore").splitlines(), 1):
            for w in _path_claims(line):
                first = w.split("/", 1)[0]
                has_ext = any(Path(seg).suffix.lower() in PATH_EXTENSIONS for seg in w.split("/"))
                if not (has_ext or ("/" in w and first in dirs)):
                    continue  # not a path claim: no extension, no known directory
                if "/" not in w and w in names and not (repo / w).exists():
                    continue  # a bare name that lives elsewhere in the tree
                checked += 1
                if not ((repo / w).exists() or w in notes):
                    broken.append({"file": doc, "line": n, "path": w})
    return {"checked": checked, "unresolved": len(broken), "paths": broken}


def verification_base(repo, files):
    makefile = repo / "Makefile"
    text = makefile.read_text(errors="ignore") if makefile.is_file() else ""
    excl = quality_excludes(repo)
    e2e = sorted({"/".join(Path(f).parts[:i + 1]) for f in files
                  if not vendored(f) and not excluded(f, excl)
                  for i, part in enumerate(Path(f).parts[:-1]) if part in E2E_PARTS})
    return {
        "launch_json": (repo / ".claude" / "launch.json").is_file(),
        "makefile_check_target": bool(re.search(r"^(check|verify)\s*:", text, re.M)),
        "e2e_or_integration_dirs": e2e,
    }


def build_report(repo, memory_dir):
    repo = Path(repo).resolve()
    files = tracked_files(repo)
    sources = prose_sources(repo, memory_dir)
    return {
        "repo": str(repo),
        "memory_dir": str(memory_dir),
        "memory_dir_found": sources["memory"] is not None,
        "prose": prose_inventory(sources),
        "mechanical": mechanical_inventory(repo, files),
        "candidates": graduation_candidates(repo, sources),
        "history": fix_history(repo),
        "docs_drift": docs_drift(repo, files, memory_dir),
        "verification": verification_base(repo, files),
        "see_also": SEE_ALSO,
    }


def _prose_lines(r):
    lines = ["## Prose layer (rules a reader must follow)"]
    for group, c in r["prose"].items():
        lines.append(f"  {group}: {c['files']} files, {c['bytes']} bytes")
    if not r["memory_dir_found"]:
        lines.append(f"  memory dir not found: {r['memory_dir']}")
    return lines


def _mechanical_lines(r):
    m = r["mechanical"]
    lines = ["## Mechanical layer (checks that refuse the mistake)"]
    for kind, paths in m["configs"].items():
        shown = ", ".join(paths[:8]) + (f", +{len(paths) - 8} more" if len(paths) > 8 else "")
        lines.append(f"  {kind}: {len(paths)}" + (f" ({shown})" if paths else ""))
    lines.append(f"  test files: {m['test_files']}")
    return lines


def _history_lines(r):
    h = r["history"]
    if h is None:
        return ["## Fix and revert history", "  not measured: not a git work tree"]
    return ["## Fix and revert history",
            f"  last {h['window_days']} days: {h['commits']} commits; fix {h['fix']}, "
            f"revert {h['revert']}, hotfix {h['hotfix']}"]


def format_report(r):
    lines = [f"trust_ladder: {r['repo']}", ""] + _prose_lines(r) + [""] + _mechanical_lines(r)
    lines += ["", f"## Graduation candidates: {len(r['candidates'])} NEVER/ALWAYS lines "
              "that name a grep-able token"]
    lines += [f"  {c['file']}:{c['line']}  {c['text']}" for c in r["candidates"]]
    lines += [""] + _history_lines(r)
    d = r["docs_drift"]
    lines += ["", f"## Docs drift: {d['unresolved']} of {d['checked']} backticked paths "
              "in CLAUDE.md, AGENTS.md, README.md do not resolve"]
    lines += [f"  {p['file']}:{p['line']}  {p['path']}" for p in d["paths"]]
    v = r["verification"]
    lines += ["", "## Verification base",
              f"  .claude/launch.json: {'yes' if v['launch_json'] else 'no'}",
              f"  Makefile check target: {'yes' if v['makefile_check_target'] else 'no'}",
              f"  e2e/integration dirs: {', '.join(v['e2e_or_integration_dirs']) or 'none'}",
              "", "## Measured elsewhere (not recounted)"]
    lines += [f"  {k}: {v}" for k, v in r["see_also"].items()]
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("repo")
    ap.add_argument("--json", action="store_true", help="emit the report as JSON")
    ap.add_argument("--memory-dir", help="the project's memory notes folder "
                    "(default: ~/.claude/projects/<slug>/memory)")
    args = ap.parse_args(argv)
    repo = Path(args.repo)
    if not repo.is_dir():
        print(f"trust_ladder: {repo} is not a directory", file=sys.stderr)
        return 2
    memory_dir = Path(args.memory_dir) if args.memory_dir else default_memory_dir(repo)
    report = build_report(repo, memory_dir)
    if args.json:
        if not report["memory_dir_found"]:
            print(f"memory dir not found: {memory_dir}", file=sys.stderr)
        print(json.dumps(report, indent=2))
    else:
        print(format_report(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
