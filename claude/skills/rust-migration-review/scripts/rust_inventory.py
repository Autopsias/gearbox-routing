#!/usr/bin/env python3
"""rust_inventory.py - read-only Rust-migration inventory for /rust-migration-review.

Stdlib Python 3.11+ only. Never executes target-repository code and never writes into
the target. For each code unit (a directory, cut at --depth), it counts lines per
language, test lines, git churn, and regex signals that raise or lower the case for a
Rust port, with path:line examples. It also lists runtime dependencies per manifest.
Signals are triage hints for the reviewer, not verdicts.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tomllib
from collections import defaultdict
from pathlib import Path
from typing import Any

LANG_BY_EXT = {
    ".py": "python", ".pyx": "cython", ".js": "javascript", ".mjs": "javascript",
    ".cjs": "javascript", ".jsx": "javascript", ".ts": "typescript", ".tsx": "typescript",
    ".go": "go", ".java": "java", ".kt": "kotlin", ".scala": "scala", ".cs": "csharp",
    ".rb": "ruby", ".php": "php", ".c": "c", ".h": "c", ".cc": "cpp", ".cpp": "cpp",
    ".cxx": "cpp", ".hpp": "cpp", ".hh": "cpp", ".rs": "rust", ".swift": "swift",
    ".zig": "zig",
}
SKIP_DIRS = {
    ".git", "node_modules", ".venv", "venv", "env", "__pycache__", "dist", "build",
    "target", "vendor", "third_party", ".tox", ".mypy_cache", ".pytest_cache", ".next",
    "coverage", ".plan-worktrees", "site-packages",
}
MAX_FILE_BYTES = 1_000_000  # larger files are usually generated or minified
TEST_PATH_RE = re.compile(
    r"(^|/)(tests?|__tests__|specs?|testing)(/|$)|(^|/)test_[^/]+\.py$|_test\.(py|go)$"
    r"|\.(test|spec)\.[cm]?[jt]sx?$|Tests?\.(java|kt|cs)$"
)
ALL = "*"
# name -> (direction, languages, pattern). "for" raises the case for Rust, "against"
# lowers benefit or feasibility. Keep each pattern cheap and anchored on real APIs.
SIGNALS: dict[str, tuple[str, set[str] | str, str]] = {
    "unsafe_memory_api": ("for", {"c", "cpp"},
                          r"\b(malloc|calloc|realloc|free|memcpy|memmove|strcpy|strcat|sprintf|gets)\s*\("),
    "parsing_serialization": ("for", ALL,
                              r"\b(json\.(loads|dumps)|JSON\.(parse|stringify)|re\.(compile|match|search|findall|sub)"
                              r"|new RegExp|struct\.(pack|unpack)|zlib|gzip|hashlib|xml\.etree|lxml|csv\.(reader|writer)"
                              r"|encoding/json|regexp\.MustCompile)"),
    "manual_concurrency": ("for", ALL,
                           r"(threading\.Thread|multiprocessing|ProcessPoolExecutor|ThreadPoolExecutor|worker_threads"
                           r"|pthread_create|std::thread|new Thread\(|sync\.Mutex)"),
    "native_bridge": ("for", ALL,
                      r"(\bctypes\b|\bcffi\b|Python\.h|pybind11|node-gyp|napi_|JNIEnv|import \"C\"|DllImport"
                      r"|extern \"C\"|\bcython\b)"),
    "already_native_lib": ("against", {"python", "javascript", "typescript"},
                           r"\b(import numpy|from numpy|import pandas|from pandas|import scipy|import torch"
                           r"|import tensorflow|import polars|from polars|require\(['\"]sharp['\"]\))"),
    "io_bound_call": ("against", ALL,
                      r"(requests\.(get|post|put)|httpx\.|aiohttp|\bfetch\(|axios\.|cursor\.execute|boto3"
                      r"|http\.Get|HttpClient|\.query\()"),
    "dynamic_python": ("against", {"python"},
                       r"(\b(eval|exec)\s*\(|\bgetattr\s*\(\s*\w+\s*,\s*[^'\"\s]|\bsetattr\s*\(|__getattr__"
                       r"|metaclass\s*=|importlib\.import_module|__import__\s*\()"),
    "dynamic_js": ("against", {"javascript", "typescript"},
                   r"(\beval\s*\(|new Function\s*\(|new Proxy\s*\(|require\s*\(\s*[^'\"\x60\s)]"
                   r"|\.prototype\.\w+\s*=|:\s*any\b|\bas any\b)"),
    "dynamic_other": ("against", {"java", "kotlin", "csharp", "go", "ruby", "php"},
                      r"(Class\.forName|getDeclaredMethod|System\.Reflection|\breflect\.|method_missing"
                      r"|define_method|instance_variable_set|class_eval|\$\$\w+|call_user_func)"),
    "c_hard_constructs": ("against", {"c", "cpp"},
                          r"(#define\s+\w+\([^)]*\)|\bgoto\b|\bsetjmp\b|\blongjmp\b|\bunion\b|__asm__|\basm\s*\()"),
    "framework_bound": ("against", ALL,
                        r"\b(django|rails|ActiveRecord|springframework|@Autowired|sqlalchemy|sequelize|typeorm"
                        r"|Hibernate|EntityFramework|laravel)\b"),
    "ui_framework": ("against", {"javascript", "typescript"},
                     r"(from ['\"](react|vue|svelte)['\"]|@angular/core|from ['\"]react-dom)"),
}
_COMPILED = {k: (d, langs, re.compile(p, re.IGNORECASE if k == "framework_bound" else 0))
             for k, (d, langs, p) in SIGNALS.items()}
MAX_EXAMPLES = 3


def rel(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def unit_of(rel_path: str, depth: int) -> str:
    parts = rel_path.split("/")[:-1]
    return "/".join(parts[:depth]) or "."


def iter_source_files(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
        for name in sorted(filenames):
            path = Path(dirpath) / name
            lang = LANG_BY_EXT.get(path.suffix.lower())
            if lang and not path.is_symlink():
                yield path, lang


def scan_file(path: Path, lang: str, rp: str, unit: dict[str, Any]) -> None:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    code = sum(1 for ln in lines if ln.strip())
    unit["files"] += 1
    if TEST_PATH_RE.search(rp):
        unit["test_lines"] += code
        return  # signals describe the code to port, not its tests
    unit["lines"][lang] += code
    for name, (_, langs, rx) in _COMPILED.items():
        if langs != ALL and lang not in langs:
            continue
        for no, ln in enumerate(lines, 1):
            if rx.search(ln):
                sig = unit["signals"][name]
                sig["count"] += 1
                if len(sig["examples"]) < MAX_EXAMPLES:
                    sig["examples"].append(f"{rp}:{no}")


def _new_unit() -> dict[str, Any]:
    return {"files": 0, "test_lines": 0, "lines": defaultdict(int),
            "signals": defaultdict(lambda: {"count": 0, "examples": []})}


def scan_units(root: Path, depth: int) -> tuple[dict[str, dict[str, Any]], list[str]]:
    units: dict[str, dict[str, Any]] = defaultdict(_new_unit)
    skipped: list[str] = []
    for path, lang in iter_source_files(root):
        rp = rel(path, root)
        if path.stat().st_size > MAX_FILE_BYTES:
            skipped.append(rp)
            continue
        scan_file(path, lang, rp, units[unit_of(rp, depth)])
    return units, skipped


def git_churn(root: Path, depth: int, days: int) -> dict[str, int] | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "log", f"--since={days}.days", "--name-only", "--pretty=format:"],
            capture_output=True, text=True, timeout=60, check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    churn: dict[str, int] = defaultdict(int)
    for line in out.splitlines():
        if line.strip() and Path(line).suffix.lower() in LANG_BY_EXT:
            churn[unit_of(line.strip(), depth)] += 1
    return dict(churn)


def _req_name(spec: str) -> str:
    return re.split(r"[<>=!~\[;@ ]", spec.strip(), maxsplit=1)[0].lower().replace("_", "-")


def _deps_requirements(text: str) -> list[str]:
    return [_req_name(ln) for ln in text.splitlines()
            if ln.strip() and not ln.lstrip().startswith(("#", "-"))]


def _deps_pyproject(text: str) -> list[str]:
    data = tomllib.loads(text)
    deps = [_req_name(d) for d in data.get("project", {}).get("dependencies", [])]
    poetry = data.get("tool", {}).get("poetry", {}).get("dependencies", {})
    return deps + [k.lower() for k in poetry if k.lower() != "python"]


def _deps_package_json(text: str) -> list[str]:
    return sorted(json.loads(text).get("dependencies", {}))  # devDependencies are tooling


def _deps_regex(pattern: str):
    return lambda text: sorted(set(re.findall(pattern, text, re.MULTILINE)))


MANIFESTS = {
    "pyproject.toml": ("pypi", _deps_pyproject),
    "package.json": ("npm", _deps_package_json),
    "go.mod": ("go", _deps_regex(r"^\s*(?:require\s+)?([\w.\-]+\.[\w.\-/]+)\s+v[\d.]")),
    "Gemfile": ("rubygems", _deps_regex(r"^\s*gem\s+['\"]([\w\-]+)['\"]")),
    "pom.xml": ("maven", _deps_regex(r"<artifactId>([\w.\-]+)</artifactId>")),
    "build.gradle": ("maven", _deps_regex(r"(?:implementation|api)\s*\(?\s*['\"][\w.\-]+:([\w.\-]+)")),
    "build.gradle.kts": ("maven", _deps_regex(r"(?:implementation|api)\s*\(?\s*['\"][\w.\-]+:([\w.\-]+)")),
}


def _manifest_parser(name: str):
    if name in MANIFESTS:
        return MANIFESTS[name]
    if name.startswith("requirements") and name.endswith(".txt"):
        return ("pypi", _deps_requirements)
    if name.endswith(".csproj"):
        return ("nuget", _deps_regex(r"PackageReference\s+Include=\"([\w.\-]+)\""))
    return None


def scan_dependencies(root: Path) -> tuple[list[dict[str, Any]], list[str]]:
    found: list[dict[str, Any]] = []
    cargo: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
        for name in sorted(filenames):
            path = Path(dirpath) / name
            if name == "Cargo.toml":
                cargo.append(rel(path, root))
            parser = _manifest_parser(name)
            if not parser:
                continue
            try:
                deps = parser[1](path.read_text(encoding="utf-8", errors="replace"))
            except (ValueError, tomllib.TOMLDecodeError) as exc:
                found.append({"manifest": rel(path, root), "ecosystem": parser[0], "error": str(exc)})
                continue
            found.append({"manifest": rel(path, root), "ecosystem": parser[0], "runtime_deps": deps})
    return found, cargo


def build_report(root: Path, depth: int = 2, churn_days: int = 365, use_git: bool = True) -> dict[str, Any]:
    units, skipped = scan_units(root, depth)
    churn = git_churn(root, depth, churn_days) if use_git else None
    languages: dict[str, int] = defaultdict(int)
    rows = []
    for name, u in units.items():
        for lang, n in u["lines"].items():
            languages[lang] += n
        rows.append({
            "unit": name, "files": u["files"], "code_lines": sum(u["lines"].values()),
            "test_lines": u["test_lines"], "lines_by_language": dict(u["lines"]),
            "churn": None if churn is None else churn.get(name, 0),
            "signals": {k: {"direction": SIGNALS[k][0], **v} for k, v in sorted(u["signals"].items())},
        })
    rows.sort(key=lambda r: (-r["code_lines"], r["unit"]))
    deps, cargo = scan_dependencies(root)
    return {
        "root": str(root), "depth": depth, "churn_window_days": churn_days if churn is not None else None,
        "languages": dict(sorted(languages.items(), key=lambda kv: -kv[1])),
        "total_code_lines": sum(languages.values()),
        "total_test_lines": sum(r["test_lines"] for r in rows),
        "units": rows, "dependencies": deps,
        "rust_present": {"cargo_manifests": cargo, "rust_lines": languages.get("rust", 0)},
        "skipped_large_files": skipped,
    }


def _signal_cell(signals: dict[str, Any], direction: str) -> str:
    items = [f"{k} {v['count']}" for k, v in signals.items() if v["direction"] == direction]
    return ", ".join(items) or "-"


def to_markdown(rep: dict[str, Any]) -> str:
    langs = ", ".join(f"{k} {v}" for k, v in rep["languages"].items()) or "none"
    out = [f"# Rust-migration inventory: {rep['root']}", "",
           f"- Code lines: {rep['total_code_lines']} ({langs}); test lines: {rep['total_test_lines']}",
           f"- Existing Rust: {rep['rust_present']['rust_lines']} lines, "
           f"Cargo manifests: {', '.join(rep['rust_present']['cargo_manifests']) or 'none'}",
           f"- Churn window: {rep['churn_window_days'] or 'git not available'} days", "",
           "| Unit | Code | Tests | Churn | Signals for | Signals against |", "|---|---|---|---|---|---|"]
    for r in rep["units"]:
        if r["code_lines"]:
            out.append(f"| {r['unit']} | {r['code_lines']} | {r['test_lines']} | {r['churn'] if r['churn'] is not None else '-'} "
                       f"| {_signal_cell(r['signals'], 'for')} | {_signal_cell(r['signals'], 'against')} |")
    out += ["", "## Runtime dependencies", ""]
    for d in rep["dependencies"]:
        deps = d.get("runtime_deps")
        out.append(f"- {d['manifest']} ({d['ecosystem']}): " + (", ".join(deps) if deps else d.get("error", "none")))
    if rep["skipped_large_files"]:
        out += ["", f"Skipped {len(rep['skipped_large_files'])} files over {MAX_FILE_BYTES} bytes."]
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("repo", type=Path)
    ap.add_argument("--depth", type=int, default=2, help="directory depth that defines a code unit (default 2)")
    ap.add_argument("--churn-days", type=int, default=365)
    ap.add_argument("--no-git", action="store_true", help="skip the git churn count")
    ap.add_argument("--format", choices=("json", "markdown"), default="json")
    args = ap.parse_args(argv)
    root = args.repo.resolve()
    if not root.is_dir():
        print(f"not a directory: {root}", file=sys.stderr)
        return 2
    rep = build_report(root, args.depth, args.churn_days, not args.no_git)
    print(json.dumps(rep, indent=2) if args.format == "json" else to_markdown(rep), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
