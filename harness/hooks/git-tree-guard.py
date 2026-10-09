#!/usr/bin/env python3
"""git-tree-guard — PreToolUse(Bash): refuse a tree-wide destructive git command
while the working tree carries tracked modifications.

WHY THIS EXISTS. Testing whether a commit could be reverted, a session ran
`git revert --no-commit <sha>` (which writes conflict markers — it is NOT a dry
run), then cleaned up with `git revert --abort ; git reset -q --hard HEAD`. The
reset discarded EVERY tracked modification in the repo, including files that
were already dirty when the session opened and that it did not create. Unstaged
edits never enter the object database, so nothing could bring them back.

`rules/git-safety.md` already forbids deleting pre-existing work you did not
create. That rule is prose, and prose did not stop the command. This hook is the
mechanical half.

WHAT IT BLOCKS: only commands that discard tracked modifications tree-wide, and
only when such modifications actually exist. A clean tree is never blocked, so a
`git reset --hard` after a commit — the ordinary safe case — passes untouched.
Path-scoped forms (`git checkout -- some/file`) are never blocked: scoping is the
fix this hook is steering toward.

CHEAPNESS: the regex runs first and matches nothing on the overwhelming majority
of Bash calls; `git status` is spawned ONLY on a match. The common path is Python
startup and one regex.

KNOWN LIMIT, deliberate: this reads the command TEXT with regexes, so it is a
seatbelt against the careless form, not a sandbox. A git invocation assembled
from shell variables, or reached through an alias or a script file, is not seen.
That is acceptable — the incident this exists for was a plainly-written command,
and a guard cheap enough to run on every Bash call cannot also be a shell parser.
In short: this guard blocks known command shapes only. It is a best-effort safety
net, not a security boundary, and a differently written command can get past it.

Escape hatch, for when the discard is genuinely intended:
    GEARBOX_ALLOW_DIRTY_RESET=1 <command>

Wired from settings.json hooks.PreToolUse matcher "Bash".
Self-check: hooks/test_git_tree_guard.py
"""

import json
import os
import re
import subprocess
import sys

# A command POSITION: start of string, or after a separator. Keeps the pattern
# from firing on the same words quoted inside an echo/grep/heredoc body.
_POS = r"(?:^|[\n;&|]|&&|\|\|)\s*"
# `git -C <path>`, `-c k=v`, `--no-pager` ... any number of options, with or
# without their own argument, between `git` and the subcommand.
_OPTS = r"(?:-\S+(?:\s+[^\s-]\S*)?\s+)*"

# Each entry: (regex, short name). `git reset --hard` is the one that caused the
# incident; the others discard tracked modifications the same way.
PATTERNS = [
    (re.compile(_POS + r"git\s+" + _OPTS + r"reset\s+(?:[^\n;&|]*\s)?--hard\b"),
     "git reset --hard"),
    (re.compile(_POS + r"git\s+" + _OPTS + r"checkout\s+(?:--\s+)?\.(?:\s|$)"),
     "git checkout ."),
    (re.compile(_POS + r"git\s+" + _OPTS + r"restore\s+(?:[^\n;&|]*\s)?\.(?:\s|$)"),
     "git restore ."),
    (re.compile(_POS + r"git\s+" + _OPTS + r"clean\s+[^\n;&|]*-\S*f"),
     "git clean -f"),
]


# The opener may carry more of the command after the delimiter — `<<'EOF' 2>&1 |
# tail -2` is ordinary. Requiring a newline immediately after it left the body
# unstripped, and the guard refused a `git commit` whose MESSAGE described the
# command (observed live).
_HEREDOC = re.compile(r"<<-?\s*[\"']?(\w+)[\"']?[^\n]*\n(.*?)^\s*\1\s*$",
                      re.S | re.M)


def strip_heredocs(command):
    """A heredoc body is DATA the command writes, not a command line. Documenting
    `git reset --hard` in a file must never trip the guard — a guard that fires on
    prose gets switched off, and then it protects nothing."""
    return _HEREDOC.sub(lambda m: m.group(0).split("\n", 1)[0] + "\n", command)


_OPT_OUT = re.compile(_POS + r"GEARBOX_ALLOW_DIRTY_RESET=\S*\s+(?=\S)")
# `bash -c '<payload>'` — the payload is a command line, so it is scanned too.
_WRAPPED = re.compile(r"(?:ba|z|k)?sh\s+-c\s+(['\"])(.*?)\1", re.S)


def opted_out(command):
    """The escape hatch counts only as a real environment-assignment PREFIX to a
    command, or as an actual exported variable — never as the token appearing
    anywhere in the text, which `echo GEARBOX_ALLOW_DIRTY_RESET=1 && git reset
    --hard` would have satisfied."""
    if os.environ.get("GEARBOX_ALLOW_DIRTY_RESET"):
        return True
    return bool(_OPT_OUT.search(command))


def matched(command):
    command = strip_heredocs(command)
    for _, payload in _WRAPPED.findall(command):
        for rx, name in PATTERNS:
            if rx.search("\n" + payload):
                return name
    for rx, name in PATTERNS:
        if rx.search(command):
            return name
    return None


_CD = re.compile(_POS + r"cd\s+(?:--\s+)?([^\s;&|]+)")
_GIT_C = re.compile(r"git\s+(?:-\S+\s+)*-C\s+([^\s;&|]+)")


def candidate_dirs(command, cwd):
    """Every directory the command could be discarding in.

    The payload's `cwd` is the SESSION directory, not necessarily the repository
    the command acts on: `cd /elsewhere && git reset --hard` was judged against a
    clean session tree and allowed, proven by an end-to-end run.
    So the `cd` and `git -C` targets in the command are checked as well, and ANY
    dirty candidate refuses. Over-inclusive on purpose — the escape hatch is the
    release valve, a silent allow is not."""
    out, seen = [], set()
    for raw in [cwd] + _CD.findall(command) + _GIT_C.findall(command):
        if not raw:
            continue
        path = os.path.expanduser(raw.strip("'\""))
        if not os.path.isabs(path):
            path = os.path.normpath(os.path.join(cwd, path))
        if path not in seen and os.path.isdir(path):
            seen.add(path)
            out.append(path)
    return out


def dirty_tracked(cwd):
    """Tracked paths with unstaged or staged modifications. Untracked excluded —
    `reset --hard` leaves those alone, so they are not what is at risk."""
    try:
        out = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            cwd=cwd or None, capture_output=True, text=True, timeout=10,
        )
    except Exception:  # noqa: BLE001 — a guard that crashes must not block work
        return None
    if out.returncode != 0:
        return None                      # not a repo, or git unavailable
    return [ln for ln in out.stdout.splitlines() if ln.strip()]


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:  # noqa: BLE001
        return 0
    command = ((payload.get("tool_input") or {}).get("command")) or ""
    if not isinstance(command, str) or not command:
        return 0
    name = matched(command)
    if not name:
        return 0
    if opted_out(command):
        return 0
    cwd = payload.get("cwd") or os.getcwd()
    for target in candidate_dirs(command, cwd):
        dirty = dirty_tracked(target)
        if dirty:
            break
    else:                                # every candidate clean, or none a repo
        return 0
    cwd = target

    shown = dirty[:15]
    more = len(dirty) - len(shown)
    sys.stderr.write(
        "BLOCKED: `%s` would discard %d tracked modification(s) in %s.\n\n"
        "Unstaged edits are not in git's object database. Once discarded they are\n"
        "gone — `git fsck` cannot recover them. A command like this has destroyed\n"
        "uncommitted work that the session did not create.\n\n"
        "At risk:\n%s%s\n\n"
        "Do one of these instead:\n"
        "  1. Scope it to your own paths:   git checkout -- <the paths you changed>\n"
        "  2. Undo one operation properly:  git revert --abort  /  git merge --abort\n"
        "  3. Keep the work first:          git switch -c wip/<name> && git commit -am wip\n"
        "  4. Genuinely intend the discard: re-run with GEARBOX_ALLOW_DIRTY_RESET=1\n"
        % (name, len(dirty), cwd,
           "\n".join("  " + ln for ln in shown),
           "\n  ... and %d more" % more if more > 0 else "")
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
