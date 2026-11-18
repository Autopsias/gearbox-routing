#!/usr/bin/env python3
"""Count plan-session commits that a fix touched again within 7 days.

Usage: followup_fix.py <repo> [--days 60] [--ref origin/main]

A session commit has the subject 'feat(<plan-slug>): sNN session work'. A fix
commit has a subject starting with fix, revert or hotfix. A pair is reported
when the fix changes or deletes lines the session commit added.

Line tracking uses `git blame` on the fix commit's parent, limited to the old
side of each diff hunk. Why not `git log -L`: it needs one run per session
line range, while one blame per touched file answers for every session at once.
Pure insertions (no old lines) touch nothing and never pair.
"""
import argparse
import re
import subprocess
import sys
from collections import defaultdict

WINDOW_H = 7 * 24
SESSION_RE = re.compile(r"^feat\([^)]+\): s\d+ session work")
FIX_RE = re.compile(r"^(fix|revert|hotfix)\b", re.I)
HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+")
BLAME_RE = re.compile(r"^([0-9a-f]{40}) \d+ (\d+)")


def git(repo, *args):
    r = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, errors="replace")
    return r.stdout if r.returncode == 0 else ""


def commits(repo, ref, days):
    out = git(repo, "log", "--no-merges", f"--since={days + 8} days ago", "--format=%H%x09%ct%x09%s", ref)
    rows = [ln.split("\t", 2) for ln in out.splitlines() if ln.count("\t") >= 2]
    return [(h, int(t), s) for h, t, s in rows]


def old_ranges(repo, fix):
    """{path: [(start, count)]} of old-side line ranges changed by the fix."""
    res, path = defaultdict(list), None
    for line in git(repo, "diff", "-U0", "--no-renames", f"{fix}^", fix).splitlines():
        if line.startswith("--- "):
            path = line[6:] if line.startswith("--- a/") else None
        elif path and (m := HUNK_RE.match(line)):
            n = 1 if m.group(2) is None else int(m.group(2))
            if n:
                res[path].append((int(m.group(1)), n))
    return res


def blamed(repo, fix):
    """{session_candidate_commit: {path: line_count}} of old lines the fix touched."""
    hit = defaultdict(lambda: defaultdict(int))
    for path, ranges in old_ranges(repo, fix).items():
        args = ["blame", "--line-porcelain"]
        for s, n in ranges:
            args += ["-L", f"{s},{s + n - 1}"]
        for line in git(repo, *args, f"{fix}^", "--", path).splitlines():
            if m := BLAME_RE.match(line):
                hit[m.group(1)][path] += 1
    return hit


def find_pairs(repo, ref="origin/main", days=60):
    rows = commits(repo, ref, days)
    sessions = {h: (t, s) for h, t, s in rows if SESSION_RE.match(s)}
    fixes = [(h, t, s) for h, t, s in rows if FIX_RE.match(s)]
    pairs = []
    for fh, ft, fs in fixes:
        cand = {h for h, (t, _) in sessions.items() if 0 < ft - t <= WINDOW_H * 3600}
        if not cand:
            continue
        for sh, files in blamed(repo, fh).items():
            if sh in cand:
                pairs.append({"session": sh, "fix": fh, "fix_subject": fs,
                              "hours": round((ft - sessions[sh][0]) / 3600, 1),
                              "lines": sum(files.values()), "files": sorted(files)})
    return pairs, len(sessions)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("repo")
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--ref", default="origin/main")
    a = ap.parse_args(argv)
    import time
    if not git(a.repo, "rev-parse", "--git-dir").strip():
        print(f"error: {a.repo} is not a git repository", file=sys.stderr)
        return 2
    ref = a.ref if git(a.repo, "rev-parse", "--verify", a.ref).strip() else "HEAD"
    old = time.time() - WINDOW_H * 3600
    rows = commits(a.repo, ref, a.days)
    # Sessions younger than 7 days may still collect fixes, so leave them out of the denominator.
    eligible = {h for h, t, s in rows if SESSION_RE.match(s) and t < old and t > time.time() - a.days * 86400}
    pairs, _ = find_pairs(a.repo, ref, a.days)
    pairs = [p for p in pairs if p["session"] in eligible]
    for p in pairs:
        print(f"{p['session'][:10]} {p['fix'][:10]} {p['hours']}h lines={p['lines']} {','.join(p['files'])}")
    paired = {p["session"] for p in pairs}
    n = len(eligible)
    pct = f"{100 * len(paired) // n}%" if n else "n/a"
    print(f"TOTAL {a.repo}: {len(paired)} of {n} session commits followed by a same-line fix within 7 days ({pct}); {len(pairs)} pairs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
