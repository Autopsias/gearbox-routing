"""Give an approved Claude reviewer a fixed source bundle without local tools."""

import re
import subprocess
import tempfile
from pathlib import Path

MAX_BYTES = 250_000


def approved(ssot, cwd):
    if not cwd:
        return False
    try:
        text = ssot.read_text(encoding="utf-8")
        roots = re.findall(r'^\s*claude_verifier_repo_git_dir:\s*"([^"\n]+)"[ \t]*$',
                           text, re.MULTILINE)
        if len(roots) != 1:
            return False
        actual = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--path-format=absolute", "--git-common-dir"],
            capture_output=True, text=True, check=True, timeout=5,
        ).stdout.strip()
        return Path(actual).resolve(strict=True) == Path(roots[0]).expanduser().resolve(strict=True)
    except (OSError, ValueError, subprocess.SubprocessError):
        return False


def _source_path(root, name):
    path = root / name
    if (path.resolve() != path or not path.is_relative_to(root)
            or any(part == ".git" or part.startswith((".env", ".gitleaks"))
                   for part in Path(name).parts)):
        raise ValueError("review path is outside the approved source surface")
    return path


def bundle(cwd, diff_file, changed, new_files):
    root = Path(cwd).resolve(strict=True)
    patch = Path(diff_file) if diff_file else None
    if changed and (not patch or not patch.is_file() or patch.stat().st_size > MAX_BYTES):
        raise ValueError("tracked changes require a bounded patch")
    parts = ["## Patch\n" + patch.read_text(encoding="utf-8")] if patch else []
    size = sum(len(part.encode()) for part in parts)
    for name in changed:
        _source_path(root, name)
    for name in new_files:
        path = _source_path(root, name)
        if not path.is_file() or path.stat().st_size > MAX_BYTES:
            raise ValueError("review source is not a bounded regular file")
        part = f"## Source file {name}\n{path.read_text(encoding='utf-8')}"
        size += len(part.encode())
        if size > MAX_BYTES:
            raise ValueError("review bundle exceeds its size limit")
        parts.append(part)
    return "\n\n".join(parts)


def review(run, prompt, timeout, model):
    from egress import _text_hit

    if len(prompt.encode()) > MAX_BYTES or _text_hit(prompt, "claude-review.txt"):
        return None, "scoped Claude review input did not pass its outbound check"
    with tempfile.TemporaryDirectory(prefix="scoped-claude-review-") as cwd:
        return run(prompt, cwd, timeout, model=model, scoped=True)
