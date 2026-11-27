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
     "live_untracked": [...],   # new runtime files: harvestable by `gearbox harvest`
     "live_auto": [...],        # subset of live_untracked deploy may stage: memory notes
                                # and plan files outside _evidence/, each < 200 KB
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
import copy
import json
import os
import re
import stat
import subprocess
import sys
import tempfile

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
                # A non-empty block on one side only is reported by its LEAVES:
                # the first /effort on a new model id adds the whole
                # `modelSettings.<id>` block, and `modelSettings.*.effortLevel`
                # only matches the leaf. Reported as the block, it aborted a deploy.
                side = a[k] if k in a else b[k]
                if isinstance(side, dict) and side:
                    yield from changed_key_paths(a.get(k, {}), b.get(k, {}), p)
                else:
                    yield p
            else:
                yield from changed_key_paths(a[k], b[k], p)
    elif a != b:
        yield prefix or "<root>"


def _pattern_matches(pattern, parts):
    """A declared key, split on dots, against the key path's leading segments.

    ONE WILDCARD SEGMENT, `*`, matching exactly one segment and never a dot.
    It exists for keys the binary indexes BY MODEL ID —
    `modelSettings.<model>.effortLevel` — where naming each id would decay
    silently: a new model id would read as harness drift and block a deploy,
    and the fix would look identical to a real refusal. `*` is deliberately not
    a prefix match: `modelSettings` alone would make the WHOLE subtree churn,
    and this is the one file where over-broad classification is dangerous.
    """
    pat = pattern.split(".")
    if len(pat) > len(parts):
        return False
    return all(p == "*" or p == q for p, q in zip(pat, parts))


def is_churn_key(keypath, churn_keys):
    """Churn iff the key path, or any dotted ancestor of it, is declared churn."""
    parts = keypath.split(".")
    return any(_pattern_matches(pattern, parts) for pattern in churn_keys)


def classify_settings(old_text, new_text, churn_keys, errors, relpath="settings.json"):
    """-> ('churn'|'harness', {'churn': [...], 'harness': [...]})  fail-closed.

    `old_text` / `new_text` are the file before and after. None means it could
    not be read (missing, deleted): uninspectable, so harness."""
    try:
        if old_text is None or new_text is None:
            raise FileNotFoundError("no text on one side")
        old, new = json.loads(old_text), json.loads(new_text)
    except Exception as exc:  # noqa: BLE001 — fail closed: any read or parse error classifies the file as harness
        errors.append(f"{relpath}: cannot compare against HEAD ({exc.__class__.__name__}: {exc})")
        return "harness", {"churn": [], "harness": ["<uninspectable>"]}

    churn, harness = [], []
    for kp in changed_key_paths(old, new):
        (churn if is_churn_key(kp, churn_keys) else harness).append(kp)
    if not churn and not harness:  # whitespace-only / key-reordering rewrite
        churn.append("<formatting-only>")
    return ("harness" if harness else "churn"), {"churn": churn, "harness": harness}


def classify_paths(paths, old_settings, new_settings, spec, untracked=()):
    """The ONE per-path classification, pure: paths in, classes out.

    `spec` is `load_pathspec(...)`. `old_settings` / `new_settings` are the
    settings.json text before and after (None = unreadable). `untracked` names
    the paths that are new files. `main()` calls it for the dirty tree, and
    plan-execute's land_carry calls it for each commit of a main move, so the two
    can never classify one path differently. Anything not named is harness.
    """
    live_res = [glob_to_re(g) for g in spec.get("live-state", [])]
    churn_keys = set(spec.get("settings-churn-keys", []))
    res: dict = {"harness": [], "live_state": [], "churn": [],
                 "live_untracked": [], "triage_untracked": [],
                 "detail": {}, "errors": []}
    untracked = set(untracked)
    for path in dict.fromkeys(paths):
        live = any(r.match(path) for r in live_res)
        if path in untracked:
            (res["live_untracked"] if live else res["triage_untracked"]).append(path)
        elif live:
            res["live_state"].append(path)
        elif path == "settings.json":
            klass, detail = classify_settings(old_settings, new_settings, churn_keys, res["errors"])
            res["detail"][path] = detail
            res[klass].append(path)
        else:
            res["harness"].append(path)
    for k in ("harness", "live_state", "churn", "live_untracked", "triage_untracked"):
        res[k].sort()
    return res


def _settings_pair(claude_dir):
    """settings.json at HEAD and in the working tree, as text (None = unreadable)."""
    head = subprocess.run(["git", "-C", claude_dir, "show", "HEAD:settings.json"],
                          capture_output=True)
    old = None
    if head.returncode == 0:
        try:  # strict: a replaced byte could still parse and pass as churn
            old = head.stdout.decode("utf-8")
        except UnicodeDecodeError:
            old = None
    try:
        with open(os.path.join(claude_dir, "settings.json"), encoding="utf-8") as fh:
            new = fh.read()
    except (OSError, UnicodeDecodeError):
        new = None
    return old, new


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


AUTO_RES = [glob_to_re("projects/*/memory/**.md"), glob_to_re("_plans/**")]
AUTO_MAX_BYTES = 200 * 1024


def is_auto(claude_dir, path):
    """A new file deploy may version by itself: a memory note or a plan file, never
    under an _evidence/ folder (those hold `claude -p` transcripts), small, regular."""
    if not any(r.match(path) for r in AUTO_RES) or "_evidence" in path.split("/"):
        return False
    try:
        st = os.lstat(os.path.join(claude_dir, path))
    except OSError:
        return False
    return stat.S_ISREG(st.st_mode) and st.st_size < AUTO_MAX_BYTES


def _find_orca_pretooluse_hooks(hooks):
    """Return every Orca PreToolUse hook dict inside a settings.json `hooks` map."""
    if not isinstance(hooks, dict):
        return []
    entries = hooks.get("PreToolUse")
    if not isinstance(entries, list):
        return []
    return [
        h
        for m in entries
        if isinstance(m, dict) and isinstance(m.get("hooks"), list)
        for h in m["hooks"]
        if isinstance(h, dict) and "agent-hooks/claude-hook" in str(h.get("command", ""))
    ]


def restore_orca_hook_timeout(claude_dir):
    """Restore Orca's PreToolUse timeout when Orca's installer lowered it below HEAD.

    Only acts when the SOLE difference inside `hooks` between HEAD:settings.json
    and the working copy is Orca's `agent-hooks/claude-hook` PreToolUse timeout
    dropping from >= 30 to a smaller integer. Leaves unrelated machine-churn
    keys intact and refuses (returns None) on any other `hooks` change.
    """
    old_text, new_text = _settings_pair(claude_dir)
    if old_text is None or new_text is None:
        return None
    try:
        old, new = json.loads(old_text), json.loads(new_text)
    except Exception:  # noqa: BLE001 — fail closed: never rewrite unparseable JSON
        return None
    if not isinstance(old, dict) or not isinstance(new, dict):
        return None
    old_hooks, new_hooks = old.get("hooks"), new.get("hooks")
    if not isinstance(old_hooks, dict) or not isinstance(new_hooks, dict) or old_hooks == new_hooks:
        return None

    old_orca = _find_orca_pretooluse_hooks(old_hooks)
    patched_hooks = copy.deepcopy(new_hooks)
    new_orca = _find_orca_pretooluse_hooks(patched_hooks)
    if len(old_orca) != 1 or len(new_orca) != 1:
        return None

    old_t, new_t = old_orca[0].get("timeout"), new_orca[0].get("timeout")
    if (
        isinstance(old_t, bool)
        or isinstance(new_t, bool)
        or not isinstance(old_t, int)
        or not isinstance(new_t, int)
        or old_t < 30
        or new_t >= old_t
    ):
        return None

    new_orca[0]["timeout"] = old_t
    if patched_hooks != old_hooks:
        return None

    new["hooks"] = patched_hooks
    target = os.path.join(claude_dir, "settings.json")
    fd, tmp = tempfile.mkstemp(prefix=".settings.", suffix=".tmp", dir=claude_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(new, fh, indent=2)
            fh.write("\n")
        os.replace(tmp, target)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return (new_t, old_t)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--claude-dir", default=os.environ.get("CLAUDE_DIR") or os.path.expanduser("~/.claude"))
    ap.add_argument("--pathspec", default=os.path.join(SELF, "deploy.pathspec"))
    ap.add_argument("--text", action="store_true", help="human-readable report instead of JSON")
    ap.add_argument("--section", help="print the raw entries of one pathspec section and exit")
    ap.add_argument("--paths", help="print NUL-separated paths for these comma-separated classes and exit")
    ap.add_argument("--from-json", help="format an existing snapshot instead of re-running git status")
    ap.add_argument("--restore-orca-timeout", action="store_true",
                    help="restore Orca's PreToolUse timeout if it is the sole hooks diff vs HEAD")
    args = ap.parse_args(argv)

    if args.restore_orca_timeout:
        restored = restore_orca_hook_timeout(args.claude_dir)
        if restored:
            print(f"[gearbox] restored Orca PreToolUse timeout {restored[0]} -> {restored[1]} (reinstall regression)")
        return 0

    spec = load_pathspec(args.pathspec)
    if args.section:
        for entry in spec.get(args.section, []):
            print(entry)
        return 0
    if args.from_json:
        res = json.load(open(args.from_json, encoding="utf-8"))
        return emit(res, args)

    status = git_status(args.claude_dir)
    paths = [p for _, p in status]
    untracked = {p for xy, p in status if xy == "??"}
    old, new = (_settings_pair(args.claude_dir)
                if "settings.json" in paths and "settings.json" not in untracked else (None, None))
    res = {"claude_dir": args.claude_dir,
           **classify_paths(paths, old, new, spec, untracked)}
    res["live_auto"] = sorted(p for p in res["live_untracked"] if is_auto(args.claude_dir, p))
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
        ("live_untracked", "LIVE-STATE/new (untracked runtime output — harvest adds it; deploy adds the live_auto subset)"),
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
