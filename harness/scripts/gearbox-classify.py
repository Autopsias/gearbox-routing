#!/usr/bin/env python3
"""gearbox-classify.py — classify every dirty path in the deploy target.

The ONE place the two-class (three, counting machine churn) deploy policy is
decided. `scripts/gearbox` shells out to this for deploy, drift and harvest, so
all four surfaces agree by construction rather than by three copies of a regex.

Reads scripts/deploy.pathspec (see that file for the policy itself), runs
`git status` in CLAUDE_DIR, and prints one JSON object:

    {"harness": [...],          # loud: real drift, ABORTS a deploy
     "live_state": [...],       # routine: auto-harvested, never blocks
     "churn": [...],            # routine: machine-written, never blocks
     "live_untracked": [...],   # new runtime files: harvestable, never auto-added
     "triage_untracked": [...], # new paths outside every known surface: report only
     "detail": {"<path>": {"churn": [...], "harness": [...]}},
     "errors": [...]}           # inspection failures -> caller must treat as harness

Fail-closed: anything the pathspec does not explicitly name is `harness`, and any
file we cannot inspect (unparseable settings.json, missing HEAD blob) lands in
`harness` with a line in `errors`. A classifier that cannot decide must never
decide "routine".

Usage:  gearbox-classify.py [--claude-dir DIR] [--pathspec FILE] [--text]
Exit:   0 always when it could inspect the tree; 2 if it could not (git failure).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

SELF = os.path.dirname(os.path.abspath(__file__))


# --------------------------------------------------------------------------
# pathspec
# --------------------------------------------------------------------------
def load_pathspec(path):
    """Parse the [section] / one-entry-per-line pathspec into {section: [entries]}."""
    sections, cur = {}, None
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            line = re.split(r"\s+#", line, maxsplit=1)[0].strip()
            if not line:
                continue
            if line.startswith("[") and line.endswith("]"):
                cur = line[1:-1]
                sections.setdefault(cur, [])
                continue
            if cur is None:
                raise SystemExit(f"FATAL: {path}: entry before any [section]: {line}")
            sections[cur].append(line)
    return sections


def glob_to_re(glob):
    """`*` stays inside one path segment, `**` crosses them. Nothing else is magic."""
    out, i = [], 0
    while i < len(glob):
        c = glob[i]
        if c == "*":
            if glob[i : i + 2] == "**":
                out.append(".*")
                i += 2
                continue
            out.append("[^/]*")
        else:
            out.append(re.escape(c))
        i += 1
    return re.compile("^" + "".join(out) + "$")


# --------------------------------------------------------------------------
# settings.json key-path classification
# --------------------------------------------------------------------------
def changed_key_paths(a, b, prefix=""):
    """Dotted key paths where JSON values `a` and `b` differ."""
    if type(a) is not type(b):
        yield prefix or "<root>"
        return
    if isinstance(a, dict):
        for k in sorted(set(a) | set(b)):
            p = f"{prefix}.{k}" if prefix else k
            if k not in a or k not in b:
                yield p
            else:
                yield from changed_key_paths(a[k], b[k], p)
    elif a != b:
        yield prefix or "<root>"


def is_churn_key(keypath, churn_keys):
    """Churn iff the key path, or any dotted ancestor of it, is declared churn."""
    parts = keypath.split(".")
    return any(".".join(parts[: i + 1]) in churn_keys for i in range(len(parts)))


def classify_settings(claude_dir, relpath, churn_keys, errors):
    """-> ('churn'|'harness', {'churn': [...], 'harness': [...]})  fail-closed."""
    try:
        head = subprocess.run(
            ["git", "-C", claude_dir, "show", f"HEAD:{relpath}"],
            capture_output=True, check=True,
        ).stdout
        old = json.loads(head)
        with open(os.path.join(claude_dir, relpath), encoding="utf-8") as fh:
            new = json.load(fh)
    except Exception as exc:  # unparseable / missing / deleted -> loud
        errors.append(f"{relpath}: cannot compare against HEAD ({exc.__class__.__name__}: {exc})")
        return "harness", {"churn": [], "harness": ["<uninspectable>"]}

    churn, harness = [], []
    for kp in changed_key_paths(old, new):
        (churn if is_churn_key(kp, churn_keys) else harness).append(kp)
    if not churn and not harness:  # whitespace-only / key-reordering rewrite
        churn.append("<formatting-only>")
    return ("harness" if harness else "churn"), {"churn": churn, "harness": harness}


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def git_status(claude_dir):
    """`git status --porcelain=v1 -z -uall` -> [(xy, path), ...]; renames yield both sides."""
    proc = subprocess.run(
        ["git", "-C", claude_dir, "status", "--porcelain=v1", "-z", "--untracked-files=all"],
        capture_output=True,
    )
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr.decode("utf-8", "replace"))
        raise SystemExit(2)
    fields = proc.stdout.decode("utf-8", "surrogateescape").split("\0")
    out, i = [], 0
    while i < len(fields):
        entry = fields[i]
        i += 1
        if len(entry) < 4:
            continue
        xy, path = entry[:2], entry[3:]
        out.append((xy, path))
        if "R" in xy or "C" in xy:  # rename/copy: the ORIGINAL path is the next field
            if i < len(fields) and fields[i]:
                out.append((xy, fields[i]))
            i += 1
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--claude-dir", default=os.environ.get("CLAUDE_DIR") or os.path.expanduser("~/.claude"))
    ap.add_argument("--pathspec", default=os.path.join(SELF, "deploy.pathspec"))
    ap.add_argument("--text", action="store_true", help="human-readable report instead of JSON")
    ap.add_argument("--section", help="print the raw entries of one pathspec section and exit")
    ap.add_argument("--paths", help="print NUL-separated paths for these comma-separated classes and exit")
    ap.add_argument("--from-json", help="format an existing snapshot instead of re-running git status")
    args = ap.parse_args(argv)

    spec = load_pathspec(args.pathspec)
    if args.section:
        for entry in spec.get(args.section, []):
            print(entry)
        return 0
    live_res = [glob_to_re(g) for g in spec.get("live-state", [])]
    churn_keys = set(spec.get("settings-churn-keys", []))

    if args.from_json:
        res = json.load(open(args.from_json, encoding="utf-8"))
        return emit(res, args)

    res = {
        "claude_dir": args.claude_dir,
        "harness": [], "live_state": [], "churn": [],
        "live_untracked": [], "triage_untracked": [],
        "detail": {}, "errors": [],
    }
    seen = set()
    for xy, path in git_status(args.claude_dir):
        if path in seen:
            continue
        seen.add(path)
        live = any(r.match(path) for r in live_res)
        if xy == "??":
            (res["live_untracked"] if live else res["triage_untracked"]).append(path)
        elif live:
            res["live_state"].append(path)
        elif path == "settings.json":
            klass, detail = classify_settings(args.claude_dir, path, churn_keys, res["errors"])
            res["detail"][path] = detail
            res[klass].append(path)
        else:
            res["harness"].append(path)
    for k in ("harness", "live_state", "churn", "live_untracked", "triage_untracked"):
        res[k].sort()
    return emit(res, args)


def emit(res, args):
    if args.paths:
        out = []
        for k in args.paths.split(","):
            out.extend(res[k.strip()])
        sys.stdout.write("\0".join(out))
        return 0

    if not args.text:
        print(json.dumps(res, indent=2))
        return 0

    labels = [
        ("harness", "HARNESS-CODE  (real drift — aborts a deploy, harvest with review)"),
        ("churn", "MACHINE-CHURN (binary-written — auto-harvested with provenance)"),
        ("live_state", "LIVE-STATE    (runtime output — routine, auto-harvested)"),
        ("live_untracked", "LIVE-STATE/new (untracked runtime output — harvest adds it)"),
        ("triage_untracked", "TRIAGE        (untracked, outside every known surface — never auto-added)"),
    ]
    for key, label in labels:
        if res[key]:
            print(f"{label}: {len(res[key])}")
            for p in res[key]:
                d = res["detail"].get(p)
                extra = ""
                if d:
                    bits = [f"churn: {', '.join(d['churn'])}"] if d["churn"] else []
                    if d["harness"]:
                        bits.append(f"HARNESS: {', '.join(d['harness'])}")
                    extra = "   [" + " | ".join(bits) + "]"
                print(f"    {p}{extra}")
    for e in res["errors"]:
        print(f"  ERROR: {e}")
    if not any(res[k] for k, _ in labels):
        print("clean — nothing to classify")
    return 0


if __name__ == "__main__":
    sys.exit(main())
