#!/usr/bin/env python3
"""
mcp-health.py — surface dead/auth-failing user-level MCP servers fast.

Why: the Perplexity MCP sat dead (401/quota) for a full month across
3 projects before anyone noticed (see ~/.claude/reflection-notes.md #4).
This script closes that gap by scanning recent Claude Code session
transcripts for known auth/quota failure signatures and attributing
them to the MCP server that produced them, so a dead server surfaces
within a day instead of thirty.

Usage:
    python3 ~/.claude/scripts/mcp-health.py [--days N] [--json]

    --days N   lookback window over transcript mtimes (default 3)
    --json     machine-readable output instead of the human report

Exit code: 0 always (informational tool). Non-zero only on a hard
error (e.g. ~/.claude.json unreadable).

What it does:
    1. Reads ~/.claude.json -> mcpServers, lists configured servers.
    2. Checks ~/.claude.json (top-level + project entries), ~/.codex/config.toml,
       and ~/.cursor/mcp.json for unpinned npx/uvx/bunx/pnpm-dlx/pipx-run MCP
       launchers (never reads or prints env/headers).
    3. Walks ~/.claude/projects/**/*.jsonl transcripts modified within
       the lookback window (skipped when --pin-only is passed).
    4. For each line, looks for a tool_use of an mcp__<server>__<tool>
       tool followed (same or next few lines) by a tool_result whose
       content matches a known auth/quota-failure pattern (401, 403,
       Unauthorized, insufficient_quota, invalid_api_key, expired
       token, ECONNREFUSED for stdio spawn failures).
    5. Reports unpinned launchers and servers with 1+ failures in the window.

Cheap and best-effort: transcript JSON schemas drift across Claude
Code versions, so pattern matching is intentionally loose (regex over
raw lines, not strict JSON-schema parsing) to stay resilient.
"""
import argparse
import json
import os
import re
import sys
import time
import tomllib
from pathlib import Path

CLAUDE_HOME = Path(os.path.expanduser("~/.claude"))
CLAUDE_JSON = Path(os.path.expanduser("~/.claude.json"))
PROJECTS_DIR = CLAUDE_HOME / "projects"

FAILURE_PATTERNS = [
    re.compile(r"\b401\b"),
    re.compile(r"\b403\b"),
    re.compile(r"[Uu]nauthorized"),
    re.compile(r"insufficient_quota"),
    re.compile(r"invalid_api_key"),
    re.compile(r"[Ee]xpired token"),
    re.compile(r"ECONNREFUSED"),
    re.compile(r"authentication (failed|error)", re.I),
]

TOOL_USE_RE = re.compile(r'"name"\s*:\s*"(mcp__([a-zA-Z0-9_\-]+)__[a-zA-Z0-9_\-]+)"')


def unpinned_mcp_package(command, args):
    """Return the package spec if this MCP launcher pulls an unpinned registry package."""
    toks = [str(a) for a in ([command] if command else []) + (args if isinstance(args, list) else [])]
    toks = [t for t in toks if t]
    if not toks:
        return None
    if len(toks) == 1 and " " in toks[0]:
        toks = toks[0].split()
    exe = re.sub(r"\.(cmd|exe|bat)$", "", os.path.basename(toks[0]).lower())
    rest = toks[1:]
    if exe == "cmd" and rest and rest[0].lower() == "/c":
        return unpinned_mcp_package(None, rest[1:])
    if exe in ("pnpm", "yarn") and rest[:1] == ["dlx"]:
        kind, rest = "npm", rest[1:]
    elif exe == "pipx" and rest[:1] == ["run"]:
        kind, rest = "py", rest[1:]
    elif exe in ("npx", "bunx", "pnpx"):
        kind = "npm"
    elif exe == "uvx":
        kind = "py"
    else:
        return None
    pkg = _extract_pkg_arg(rest)
    if not pkg or pkg.startswith((".", "/", "~")) or re.match(r"^[A-Za-z]:[\\/]", pkg):
        return None
    if kind == "npm":
        body = pkg[1:] if pkg.startswith("@") else pkg
        ver = body.rsplit("@", 1)[1] if "@" in body else ""
        return None if re.match(r"\d", ver) else pkg
    return None if ("==" in pkg or re.search(r"@\s*\d", pkg)) else pkg


def _extract_pkg_arg(rest):
    it = iter(rest)
    for tok in it:
        if tok in ("--from", "-p", "--package"):
            return next(it, None)
        if tok.startswith("-"):
            continue
        return tok
    return None


def _collect_unpinned(source, servers, out):
    if not isinstance(servers, dict):
        return
    for name, cfg in sorted(servers.items()):
        if not isinstance(cfg, dict):
            continue
        pkg = unpinned_mcp_package(cfg.get("command"), cfg.get("args"))
        if pkg:
            out.append({
                "source": source,
                "server": name,
                "launcher": str(cfg.get("command") or ""),
                "package": pkg,
            })


def find_unpinned_servers(home=None):
    """Scan user Claude, Codex, and Cursor MCP configs for unpinned launchers."""
    base = Path(home).expanduser() if home else Path.home()
    out = []
    cj = base / ".claude.json"
    if cj.is_file():
        try:
            data = json.loads(cj.read_text(encoding="utf-8", errors="replace"))
            _collect_unpinned("~/.claude.json", data.get("mcpServers"), out)
            for proj, pcfg in sorted((data.get("projects") or {}).items()):
                if isinstance(pcfg, dict):
                    _collect_unpinned(f"~/.claude.json [{proj}]", pcfg.get("mcpServers"), out)
        except (OSError, ValueError, RecursionError):
            pass
    ct = base / ".codex" / "config.toml"
    if ct.is_file():
        try:
            data = tomllib.loads(ct.read_text(encoding="utf-8", errors="replace"))
            _collect_unpinned("~/.codex/config.toml", data.get("mcp_servers"), out)
        except (OSError, ValueError):
            pass
    cm = base / ".cursor" / "mcp.json"
    if cm.is_file():
        try:
            data = json.loads(cm.read_text(encoding="utf-8", errors="replace"))
            _collect_unpinned("~/.cursor/mcp.json", data.get("mcpServers"), out)
        except (OSError, ValueError, RecursionError):
            pass
    return out


def load_configured_servers():
    if not CLAUDE_JSON.exists():
        return {}
    try:
        data = json.loads(CLAUDE_JSON.read_text())
    except (OSError, ValueError, RecursionError) as e:
        print(f"ERROR: could not parse {CLAUDE_JSON}: {e}", file=sys.stderr)
        return {}
    return data.get("mcpServers", {})


def iter_recent_transcripts(days):
    if not PROJECTS_DIR.exists():
        return
    cutoff = time.time() - days * 86400
    for path in PROJECTS_DIR.rglob("*.jsonl"):
        try:
            if path.stat().st_mtime >= cutoff:
                yield path
        except OSError:
            continue


def scan_transcript(path, counts, samples):
    """Best-effort line-window scan: track the last-seen mcp server name,
    and when a failure pattern appears within the next few lines, credit it."""
    try:
        lines = path.read_text(errors="ignore").splitlines()
    except OSError:
        return
    last_server = None
    last_server_idx = -1
    WINDOW = 6
    for i, line in enumerate(lines):
        m = TOOL_USE_RE.search(line)
        if m:
            last_server = m.group(2)
            last_server_idx = i
            continue
        if last_server and (i - last_server_idx) <= WINDOW:
            for pat in FAILURE_PATTERNS:
                if pat.search(line):
                    counts[last_server] = counts.get(last_server, 0) + 1
                    snippet = line.strip()
                    if len(snippet) > 200:
                        snippet = snippet[:200] + "..."
                    samples.setdefault(last_server, []).append(
                        {"file": str(path), "line": i + 1, "snippet": snippet}
                    )
                    break


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--pin-only", action="store_true",
                    help="check MCP package pinning only (skip transcript scan)")
    args = ap.parse_args()

    configured = load_configured_servers()
    unpinned = find_unpinned_servers()
    counts = {}
    samples = {}

    if not args.pin_only:
        for path in iter_recent_transcripts(args.days):
            scan_transcript(path, counts, samples)

    result = {
        "configured_servers": sorted(configured.keys()),
        "unpinned_servers": unpinned,
        "lookback_days": 0 if args.pin_only else args.days,
        "failures": {
            server: {
                "count": n,
                "most_recent": samples.get(server, [])[-1] if samples.get(server) else None,
            }
            for server, n in sorted(counts.items(), key=lambda kv: -kv[1])
        },
    }

    if args.json:
        print(json.dumps(result, indent=2))
        return 0

    print(f"MCP health probe — {len(configured)} configured server(s), "
          f"{len(unpinned)} unpinned launcher(s), "
          f"{'pin-only' if args.pin_only else f'{args.days}-day transcript lookback'}")
    print(f"Configured: {', '.join(sorted(configured.keys())) or '(none)'}")
    if unpinned:
        print("\nUnpinned MCP package launchers (pin to an exact version):")
        for row in unpinned:
            print(f"  [UNPINNED] {row['source']} | {row['server']}: "
                  f"{row['launcher']} {row['package']}")
    else:
        print("All npx/uvx/bunx MCP launchers are pinned.")
    if args.pin_only:
        return 0
    print()
    if not counts:
        print("No auth/quota failure signatures found in recent transcripts.")
        return 0

    print("Servers with auth/quota failure signatures:")
    for server, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        flag = "DEAD?" if server not in configured else "auth-flagged"
        print(f"  [{flag}] {server}: {n} failure line(s) in window")
        recent = samples.get(server, [])[-1]
        if recent:
            print(f"      last: {recent['file']}:{recent['line']}")
            print(f"      {recent['snippet']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
