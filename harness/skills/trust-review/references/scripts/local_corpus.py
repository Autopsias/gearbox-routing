"""local_corpus.py - a plain folder of Markdown notes as the default internal corpus.

TRUST_CORPUS_DIR names the folder. Search uses ripgrep when on PATH, else a stdlib walk over
*.md; both return the same rows ({"results":[{path,title,snippet,score}]}), which
internal_corpus_adapter.parse_internal_search reads. Write-back lands only in the quarantine
subfolder. The quarantine subfolder is never searched or read: a matching line returned without
its frontmatter would lose the derived-provenance marker and could pass as independent evidence.
Stdlib only.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional

ENV_VAR = "TRUST_CORPUS_DIR"
QUARANTINE_SUBDIR = os.path.join("Trust Layer", "Quarantine")   # same zone vault_writeback.py uses


def corpus_dir() -> Optional[Path]:
    """The configured corpus folder, or None when unset / not a readable directory."""
    raw = os.environ.get(ENV_VAR, "").strip()
    if not raw:
        return None
    try:
        p = Path(raw).expanduser()
        if not (p.is_dir() and os.access(p, os.R_OK | os.X_OK)):
            return None
        p = p.resolve()
    except (OSError, RuntimeError, ValueError):   # unknown ~user, symlink loop, NUL byte
        return None
    # A root that sits inside a quarantine folder would make every note look unquarantined.
    return None if any(part.lower() == "quarantine" for part in p.parts) else p


def configured_error() -> Optional[str]:
    """Why a CONFIGURED corpus is unusable, or None (unset, or usable). Unset is not an error."""
    if not os.environ.get(ENV_VAR, "").strip():
        return None
    return None if corpus_dir() else f"{ENV_VAR} is set but is not a readable corpus directory"


def can_write(root: Optional[Path] = None) -> bool:
    """True only when write_quarantined could land: the quarantine folder (or, if absent, its nearest
    existing parent inside the corpus) is a real directory we may write to. Read-only => False."""
    root = root or corpus_dir()
    if root is None:
        return False
    walk = root / QUARANTINE_SUBDIR
    while not walk.exists() and walk != root:
        walk = walk.parent
    # No component between the root and walk may be a symlink (an ancestor link redirects the write).
    return (walk.is_dir() and os.path.realpath(walk) == os.path.join(os.path.realpath(root), os.path.relpath(walk, root))
            and os.access(walk, os.W_OK | os.X_OK))


def _quarantined(path: Path, root: Path) -> bool:
    """True for a quarantined note, or any path that resolves (symlinks included) outside the corpus."""
    try:
        real = path.resolve()
        rel = real.relative_to(root)
    except (OSError, ValueError):
        return True
    return any(part.lower() == "quarantine" for part in rel.parts[:-1])


def _tokens(query: str) -> List[str]:
    return sorted({t for t in re.findall(r"\w+", query.lower()) if len(t) >= 3})


class CorpusSearchError(RuntimeError):
    """The corpus search failed; an empty result must not be read as 'nothing found'."""


def _candidate_lines(root: Path, toks: List[str], errors: List[str]):
    """Yield (file, line) for lines containing any token. rg when present, else a walk."""
    yield from (_rg_lines if shutil.which("rg") else _walk_lines)(root, toks, errors)


def _rg_lines(root: Path, toks: List[str], errors: List[str]):
    args = ["rg", "--no-heading", "--with-filename", "--null", "-i", "-F", "--no-ignore", "--glob", "*.md"]
    for t in toks:
        args += ["-e", t]
    res = subprocess.run(args + [str(root)], capture_output=True, text=True, errors="replace")
    if res.returncode == 2 and res.stdout:   # rg: some files unreadable, matches still printed => partial
        errors.append("ripgrep: " + res.stderr.strip()[:300])
    elif res.returncode not in (0, 1):   # 0 = matches, 1 = none; anything else is a failed search
        raise CorpusSearchError(f"ripgrep exited {res.returncode}: {res.stderr.strip()[:300]}")
    for out in res.stdout.splitlines():
        f, _, line = out.partition("\0")
        yield Path(f), line


def _walk_lines(root: Path, toks: List[str], errors: List[str]):
    for dirpath, dirs, files in os.walk(root, onerror=lambda e: errors.append(f"{e.filename}: {e.__class__.__name__}")):
        dirs[:] = [d for d in dirs if d.lower() != "quarantine" and not d.startswith(".")]
        for fn in files:
            if fn.lower().endswith(".md"):
                fp = Path(dirpath) / fn
                if _quarantined(fp, root):   # resolve before opening: never read through an escaping symlink
                    continue
                try:
                    for line in fp.read_text(encoding="utf-8", errors="replace").splitlines():
                        if any(t in line.lower() for t in toks):
                            yield fp, line
                except OSError as exc:
                    errors.append(f"{fp}: {exc.__class__.__name__}")


def search(query: str, k: int = 5, root: Optional[Path] = None) -> dict:
    """Best line per note, scored by the share of query tokens that line holds."""
    root = root or corpus_dir()
    toks = _tokens(query)
    if root is None:   # configured-but-unusable must read as a failed lane, not a clean empty search
        err = configured_error()
        return {"query": query, "results": [], "error": err} if err else {"query": query, "results": []}
    if not toks:
        return {"query": query, "results": []}
    best: dict = {}
    errors: List[str] = []
    for fp, line in _candidate_lines(root, toks, errors):   # CorpusSearchError propagates: the adapter types a raising lane as failed
        if _quarantined(fp, root):
            continue
        low = line.lower()
        score = sum(t in low for t in toks) / len(toks)
        if score > best.get(fp, (0, ""))[0]:
            best[fp] = (score, line.strip())
    rows = sorted(best.items(), key=lambda kv: (-kv[1][0], str(kv[0])))[:k]
    out = {"query": query, "results": [
        {"path": str(fp), "title": fp.stem, "snippet": line, "score": score} for fp, (score, line) in rows]}
    if errors:   # unreadable files: the search was incomplete, say so
        out["incomplete"] = True
        out["errors"] = errors
    return out


def read(path: str, root: Optional[Path] = None) -> str:
    """Read one note inside the corpus. Refuses paths outside it and anything in quarantine."""
    root = root or corpus_dir()
    if root is None:
        raise FileNotFoundError("no corpus configured")
    p = (root / path).resolve() if not os.path.isabs(path) else Path(path).resolve()
    if _quarantined(p, root):
        raise PermissionError(f"{path!r} is outside the corpus or quarantined")
    return p.read_text(encoding="utf-8")


def write_quarantined(note_id: str, text: str, root: Optional[Path] = None) -> Path:
    """Write a derived note into the quarantine subfolder only - never into the notes."""
    root = root or corpus_dir()
    if root is None:
        raise FileNotFoundError("no corpus configured")
    qdir = root / QUARANTINE_SUBDIR
    dest = qdir / (re.sub(r"[^\w.-]", "_", os.path.basename(note_id)) + ".md")
    # Refuse symlinks and anything resolving outside the corpus quarantine, BEFORE mkdir/write.
    walk = qdir
    while walk != root:
        if walk.is_symlink():
            raise PermissionError(f"{walk} is a symlink; refusing quarantine write")
        walk = walk.parent
    if dest.is_symlink():
        raise PermissionError(f"{dest} is a symlink; refusing quarantine write")
    qroot = (root.resolve() / QUARANTINE_SUBDIR)
    if not (qdir.resolve() == qroot and dest.resolve().parent == qroot):
        raise PermissionError("quarantine target resolves outside the corpus quarantine")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8")
    return dest
