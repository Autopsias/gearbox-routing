#!/usr/bin/env python3
"""doc_check.py — rule-based check that the operating documents name only things
that exist.

Reads README.md, UPDATE-PLAYBOOK.md, CLAUDE.md, AGENTS.md, rules/*.md and
docs/reference_*.md, pulls every backticked "claim" out of them, and checks
each one against the real repository and `~/.claude/` — no model, no network.

A claim is a backticked token that is either:
  (a) a repository PATH — it contains a slash, or its filename ends in a known
      source extension — and it must resolve from the repository root or from
      `~/.claude/`; or
  (b) a `--flag` that shares a backtick span or a text line with a script path
      that exists.

Two design rules, fixed by the plan:
  - Documents never count as evidence for a claim. Only `git ls-files`, the
    filesystem, and a script's own source text can confirm one.
  - An ambiguous token is SKIPPED and counted as skipped, never guessed.

Skipped, not failed: a placeholder (`<plan-dir>`, `sNN`, a bare `*`), a path
under `~/.gearbox-state`, a token inside a fenced code block whose info string marks it
as an example (```example, ```sh example, ...), and any token this script
cannot place under the repo root or `~/.claude/`.

Usage:
    python3 scripts/doc_check.py            # text report, exit 0
    python3 scripts/doc_check.py --json      # machine-readable report
    python3 scripts/doc_check.py --strict    # exit 1 if any claim failed
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CLAUDE_DIR = Path.home() / ".claude"

# The operating documents this check reads. Globs are expanded against the
# repo root; a spec that matches nothing is reported as a missing document,
# never as an error — a checkout (e.g. a plan worktree) can legitimately lack
# an untracked file like AGENTS.md.
DOCUMENT_GLOBS = [
    "README.md",
    "UPDATE-PLAYBOOK.md",
    "CLAUDE.md",
    "AGENTS.md",
    "rules/*.md",
    "docs/reference_*.md",
]

# Extensions that make a bare (no-slash) backticked token a path claim.
PATH_EXTENSIONS = {
    ".py", ".sh", ".md", ".json", ".yaml", ".yml", ".txt", ".html",
    ".js", ".ts", ".toml", ".cfg", ".ini", ".plist", ".ndjson", ".jsonl",
    ".log", ".pathspec",
}
# Extensions treated as script source when a `--flag` needs a script to check
# it against. Deliberately narrower than PATH_EXTENSIONS: a document (.md,
# .json, ...) is never evidence, so it can never anchor a flag claim.
SCRIPT_EXTENSIONS = {".py", ".sh", ".js", ".ts"}

# OTHER-CHECKOUT waivers: a (document, token) pair the document itself places
# in the Gearbox public-export checkout (`~/Gearbox`), never in this repo — so
# `git ls-files` here can never confirm it. Verified that all three
# exist there. Keyed by (document, token) so a document that claims the SAME
# token WITHOUT placing it in that checkout still fails, per the checker's own
# rule that a document is never evidence for its own claim.
OTHER_CHECKOUT_WAIVERS = {
    ("README.md", "scripts/sync-from-claude.py"),
    ("README.md", "docs/HARNESS.md"),
    ("UPDATE-PLAYBOOK.md", "scripts/sync-from-claude.py"),
    ("UPDATE-PLAYBOOK.md", "harness/SYNCED-FROM"),
}

# RUNTIME-FILE waivers: a (document, token) pair naming a bare filename the
# document places in a RUNTIME location — never in this repository — so it
# can never appear in `git ls-files` or on disk here. Verified:
# each one below is a bare basename inside a section of its document that
# names the runtime root explicitly. Keyed by (document, token), same rule as
# OTHER_CHECKOUT_WAIVERS — the SAME token claimed without that runtime
# context still fails. Deliberately NOT keyed on `git check-ignore`: this
# repo's .gitignore:6 is `/*`, so every untracked path here reads as ignored
# and a real stale mention would be silently skipped by that rule.
RUNTIME_FILE_WAIVERS = {
    # README.md's "~/.codex can never be a clone" paragraph — auth.json lives
    # in the live ~/.codex, not this repo.
    ("README.md", "auth.json"): "~/.codex/",
    # README.md's compaction-state section — all under ~/.gearbox-state/compaction/.
    ("README.md", "decisions.ndjson"): "~/.gearbox-state/compaction/",
    ("README.md", "VERSION.json"): "~/.gearbox-state/compaction/policy/",
    ("README.md", "model-windows.json"): "~/.gearbox-state/compaction/",
    ("README.md", "policy/VERSION.json"): "~/.gearbox-state/compaction/",
    ("README.md", "sessions.ndjson"): "~/.gearbox-state/compaction/",
    ("README.md", "instructions.ndjson"): "~/.gearbox-state/compaction/",
    # CLAUDE.md's "never edit your own settings files" rule — settings.local.json
    # lives in the live ~/.claude, not this repo.
    ("CLAUDE.md", "settings.local.json"): "the live ~/.claude",
    # AGENTS.md (the Codex twin of CLAUDE.md) carries the same rule.
    ("AGENTS.md", "settings.local.json"): "the live ~/.claude",
}

FLAG_RE = re.compile(r"^--[A-Za-z][\w-]*$")
# Whole-flag match: a flag name must not be found as a substring of a longer
# flag (`--force` must not "pass" against a script that only has
# `--force-with-lease`). `-` is a legal flag-name character, so the boundary
# cannot be `\b`; require a non-word, non-hyphen character (or start/end of
# string) on each side.
FLAG_BOUNDARY_LEFT = r"(?<![\w-])"
FLAG_BOUNDARY_RIGHT = r"(?![\w-])"
BACKTICK_RE = re.compile(r"`([^`\n]+)`")
PLACEHOLDER_CHARS_RE = re.compile(r"[<>*]")
# The session-id PLACEHOLDER, which is the literal `sNN` — never a concrete id.
# `s\d{2}` was exactly inverted: it skipped real paths like `_evidence/s01/` (so
# the check passed on nothing) and never matched the `sNN` it was written for,
# because `\d{2}` cannot match the letters "NN".
SNN_RE = re.compile(r"(?<![\w])s(?:NN|nn)(?![\w])")
# Wrapping/trailing punctuation to strip off a backtick word before classifying it.
# Leading "." is deliberately NOT stripped — real paths start with one (.github/,
# .gitignore, .githooks) and a strip() call here once ate that dot, turning a
# correct `.github/workflows/verify.yml` claim into a false "not found" failure.
LEADING_STRIP = "\"'()[]{}"
TRAILING_STRIP = ",.;:()[]{}\"'"


@dataclass
class Claim:
    kind: str            # "path" or "flag"
    token: str
    document: str         # path relative to repo root, for reporting
    line_no: int
    status: str = ""       # "pass" | "fail" | "skip"
    reason: str = ""
    resolved: str = field(default="")  # resolved path, for a passing path claim


def iter_documents(repo_root: Path) -> tuple[list[Path], list[str]]:
    """Expand DOCUMENT_GLOBS against repo_root. Returns (found, missing specs)."""
    found: list[Path] = []
    missing: list[str] = []
    for spec in DOCUMENT_GLOBS:
        if any(ch in spec for ch in "*?["):
            matches = sorted(repo_root.glob(spec))
            if not matches:
                missing.append(spec)
            found.extend(matches)
        else:
            p = repo_root / spec
            if p.exists():
                found.append(p)
            else:
                missing.append(spec)
    return found, missing


def _git_ls_files(repo_root: Path) -> set[str]:
    try:
        out = subprocess.run(
            ["git", "ls-files"], cwd=repo_root,
            capture_output=True, text=True, check=True,
        )
        return set(out.stdout.splitlines())
    except Exception:
        return set()


def _strip_word(word: str) -> str:
    return word.lstrip(LEADING_STRIP).rstrip(TRAILING_STRIP)


def _tracked_dirs(tracked: set[str]) -> set[str]:
    """Every directory prefix that appears in `git ls-files`, at every depth.

    `"scripts/doc_check.py"` contributes `{"scripts"}`;
    `"skills/plan-execute/scripts/run.py"` contributes `{"skills",
    "skills/plan-execute", "skills/plan-execute/scripts"}`.
    """
    dirs: set[str] = set()
    for p in tracked:
        parts = p.split("/")
        for i in range(1, len(parts)):
            dirs.add("/".join(parts[:i]))
    return dirs


def _tracked_basenames(tracked: set[str]) -> set[str]:
    return {p.rsplit("/", 1)[-1] for p in tracked}


def _claim_kind(word: str) -> str | None:
    """Classify a whitespace-split backtick word, or None if it names no claim."""
    w = word
    if not w:
        return None
    if FLAG_RE.match(w):
        return "flag"
    if "://" in w:
        return None  # a URL names an internet resource, never a repository path
    if "/" in w or Path(w).suffix.lower() in PATH_EXTENSIONS:
        return "path"
    return None


def extract_claims(text: str, doc_name: str) -> list[Claim]:
    """Pull every path/flag claim out of one document's raw text.

    Tracks fenced code blocks (```) and skips any block whose info string
    contains the word "example" — the documented convention for a fence that
    shows a hypothetical value rather than a real one.
    """
    claims: list[Claim] = []
    in_fence = False
    fence_is_example = False
    for line_no, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("```"):
            if not in_fence:
                in_fence = True
                fence_is_example = "example" in stripped[3:].strip().lower()
            else:
                in_fence = False
                fence_is_example = False
            continue
        if in_fence and fence_is_example:
            continue
        for span in BACKTICK_RE.findall(line):
            for word in span.split():
                w = _strip_word(word)
                kind = _claim_kind(w)
                if kind is not None:
                    claims.append(Claim(kind=kind, token=w, document=doc_name, line_no=line_no))
    return claims


def _is_placeholder(token: str) -> bool:
    return bool(PLACEHOLDER_CHARS_RE.search(token)) or bool(SNN_RE.search(token))


def _is_under_dyno(token: str) -> bool:
    return token.startswith("~/.gearbox-state") or "/.gearbox-state/" in token


def _strip_doc_anchor(token: str) -> str:
    """Drop a trailing markdown '#anchor' from a *.md path claim before resolving it."""
    if ".md#" in token:
        return token.split("#", 1)[0]
    return token


def _resolve_path(token: str, repo_root: Path, claude_dir: Path, tracked: set[str]) -> Path | None:
    """Resolve token from the repo root or from ~/.claude/. None if it can't be placed there."""
    token = _strip_doc_anchor(token)
    if token.startswith("~/.claude"):
        rel = token[len("~/.claude"):].lstrip("/")
        candidate = claude_dir if not rel else claude_dir / rel
        return candidate if candidate.exists() else None
    if token.startswith("~") or token.startswith("/"):
        return None  # outside the two allowed roots — ambiguous, not failed
    if token in tracked:
        return repo_root / token
    candidate = repo_root / token
    return candidate if candidate.exists() else None


def _check_path_claim(
    claim: Claim,
    repo_root: Path,
    claude_dir: Path,
    tracked: set[str],
    tracked_dirs: set[str],
    tracked_basenames: set[str],
) -> None:
    token = claim.token
    if (claim.document, token) in OTHER_CHECKOUT_WAIVERS:
        claim.status, claim.reason = "skip", "waived: this document places it in the Gearbox checkout"
        return
    if (claim.document, token) in RUNTIME_FILE_WAIVERS:
        where = RUNTIME_FILE_WAIVERS[(claim.document, token)]
        claim.status, claim.reason = "skip", f"waived: this document places it in a runtime location ({where})"
        return
    if _is_placeholder(token):
        claim.status, claim.reason = "skip", "placeholder"
        return
    if _is_under_dyno(token):
        claim.status, claim.reason = "skip", "path under ~/.gearbox-state"
        return

    stripped = _strip_doc_anchor(token)
    segments = stripped.split("/")
    # Precedence: once the first segment names a directory `git ls-files`
    # actually tracks, this IS a repository path claim (like `scripts/`) and
    # none of the ambiguity rules below apply to it — it must resolve or fail,
    # exactly like a real but deleted file (`scripts/deleted-command`) does.
    # A `~`- or `/`-rooted token (`~/.claude`, `~/.claude/scripts/gearbox`) is
    # resolved against a different root entirely (see `_resolve_path`) and is
    # never ambiguous the way a bare repo-relative word is — it must bypass
    # these rules too, or a real `~/.claude` claim with no file extension on
    # any segment gets skipped instead of resolved.
    first_seg_is_tracked_dir = "/" in stripped and segments[0] in tracked_dirs
    is_rooted_elsewhere = stripped.startswith("~") or stripped.startswith("/")

    if not first_seg_is_tracked_dir and not is_rooted_elsewhere:
        if "$" in token or '"' in token:
            claim.status, claim.reason = "skip", "shell fragment"
            return
        has_real_extension = any(Path(seg).suffix.lower() in PATH_EXTENSIONS for seg in segments)
        if not has_real_extension:
            claim.status, claim.reason = (
                "skip",
                "not a path — no segment has a file extension or is a tracked directory",
            )
            return

    resolved = _resolve_path(token, repo_root, claude_dir, tracked)
    if resolved is not None:
        claim.status, claim.reason, claim.resolved = "pass", "resolved", str(resolved)
        return
    if token.startswith("~") or token.startswith("/"):
        claim.status, claim.reason = "skip", "outside repo root and ~/.claude — ambiguous"
        return
    if not first_seg_is_tracked_dir and "/" not in stripped and stripped in tracked_basenames:
        claim.status, claim.reason = (
            "skip",
            "resolves by basename elsewhere in git ls-files, not at this literal path",
        )
        return
    claim.status, claim.reason = "fail", "not in git ls-files and not on disk"


def _check_flag_claims(line_claims: list[Claim], repo_root: Path, claude_dir: Path) -> None:
    path_claims = [c for c in line_claims if c.kind == "path"]
    flag_claims = [c for c in line_claims if c.kind == "flag"]
    if not flag_claims:
        return
    script_hits = [
        c for c in path_claims
        if c.status == "pass" and Path(c.token).suffix.lower() in SCRIPT_EXTENSIONS
    ]
    for c in flag_claims:
        if not script_hits:
            c.status, c.reason = "skip", "no resolvable script path on this line"
        elif len(script_hits) > 1:
            c.status, c.reason = "skip", "multiple script paths on this line — ambiguous owner"
        else:
            script_path = Path(script_hits[0].resolved)
            try:
                source = script_path.read_text(errors="ignore")
            except OSError as exc:
                c.status, c.reason = "skip", f"could not read {script_hits[0].token}: {exc}"
                continue
            pattern = re.compile(FLAG_BOUNDARY_LEFT + re.escape(c.token) + FLAG_BOUNDARY_RIGHT)
            if pattern.search(source):
                c.status, c.reason = "pass", f"found in {script_hits[0].token}"
            else:
                c.status, c.reason = "fail", f"not found in {script_hits[0].token}"


def resolve_claims(claims: list[Claim], repo_root: Path, claude_dir: Path, tracked: set[str]) -> None:
    """Resolve every claim's status in place, one document-line group at a time."""
    tracked_dirs = _tracked_dirs(tracked)
    tracked_basenames = _tracked_basenames(tracked)
    by_line: dict[tuple[str, int], list[Claim]] = {}
    for c in claims:
        by_line.setdefault((c.document, c.line_no), []).append(c)
    for line_claims in by_line.values():
        for c in line_claims:
            if c.kind == "path":
                _check_path_claim(c, repo_root, claude_dir, tracked, tracked_dirs, tracked_basenames)
        _check_flag_claims(line_claims, repo_root, claude_dir)


def run_check(repo_root: Path, claude_dir: Path) -> dict:
    docs, missing = iter_documents(repo_root)
    tracked = _git_ls_files(repo_root)
    claims: list[Claim] = []
    for doc in docs:
        rel = str(doc.relative_to(repo_root))
        text = doc.read_text(encoding="utf-8", errors="ignore")
        claims.extend(extract_claims(text, rel))
    resolve_claims(claims, repo_root, claude_dir, tracked)

    passed = [c for c in claims if c.status == "pass"]
    failed = [c for c in claims if c.status == "fail"]
    skipped = [c for c in claims if c.status == "skip"]
    return {
        "checked": len(passed),
        "failed": len(failed),
        "skipped": len(skipped),
        "documents_checked": [str(d.relative_to(repo_root)) for d in docs],
        "documents_missing": missing,
        "failures": [
            {"document": c.document, "line": c.line_no, "kind": c.kind, "token": c.token, "reason": c.reason}
            for c in failed
        ],
        "skips": [
            {"document": c.document, "line": c.line_no, "kind": c.kind, "token": c.token, "reason": c.reason}
            for c in skipped
        ],
    }


def format_report(result: dict) -> str:
    lines = [
        f"doc_check: checked={result['checked']} failed={result['failed']} skipped={result['skipped']}",
        f"documents checked: {', '.join(result['documents_checked']) or '(none)'}",
    ]
    if result["documents_missing"]:
        lines.append(f"documents missing (skipped entirely): {', '.join(result['documents_missing'])}")
    if result["failures"]:
        lines.append("")
        lines.append("FAILURES:")
        for f in result["failures"]:
            lines.append(f"  {f['document']}:{f['line']}  {f['kind']} `{f['token']}`  — {f['reason']}")
    if result["skips"]:
        lines.append("")
        lines.append("SKIPPED:")
        for s in result["skips"]:
            lines.append(f"  {s['document']}:{s['line']}  {s['kind']} `{s['token']}`  — {s['reason']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit a machine-readable report")
    parser.add_argument("--strict", action="store_true", help="exit 1 if any claim failed")
    parser.add_argument("--repo-root", default=str(DEFAULT_REPO_ROOT))
    parser.add_argument("--claude-dir", default=str(DEFAULT_CLAUDE_DIR))
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root)
    if not repo_root.is_dir():
        print("doc_check: --repo-root %s is not a directory" % repo_root, file=sys.stderr)
        return 2

    result = run_check(repo_root, Path(args.claude_dir))
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(format_report(result))

    # `checked == 0` is not a clean run, it is a run that measured nothing, and
    # under --strict it used to exit 0. Measured on the land gate:
    # `--strict --repo-root /nonexistent/...` printed `checked=0 failed=0
    # skipped=0` and exited 0, so a mistyped root or a moved document set passes
    # the gate while validating nothing.
    if args.strict and result["checked"] == 0:
        print("doc_check: --strict found no checkable claims at all", file=sys.stderr)
        return 1
    if args.strict and result["failed"] > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
