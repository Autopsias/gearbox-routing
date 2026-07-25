#!/usr/bin/env python3
"""Concurrency-safe append to ~/.claude/reflection-notes.md (Phase 4).

reflection-notes.md is a running log — every /self-assessment run adds ONE
dated section and never rewrites prior content. Because scheduled tasks and
interactive sessions can run concurrently, a blind read-modify-write would
lose a concurrent writer's section. This appender takes an advisory flock and
appends atomically (open 'a' + single write under lock), so the worst case is
two sections in arbitrary order — never a clobbered file.

Usage:
    python3 append_reflection.py --file PATH --section SECTION_MD_FILE
    echo "## ..." | python3 append_reflection.py --file PATH --section -
"""
from __future__ import annotations
import argparse
import os
import sys
import fcntl
import datetime
from pathlib import Path


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--file", required=True)
    ap.add_argument("--section", required=True,
                    help="path to the section markdown, or '-' for stdin")
    args = ap.parse_args(argv)

    if args.section == "-":
        body = sys.stdin.read()
    else:
        body = Path(args.section).read_text(encoding="utf-8")
    if not body.strip():
        sys.stderr.write("[append] refusing to append empty section\n")
        return 2

    target = Path(os.path.expanduser(args.file))
    target.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    block = f"\n\n---\n\n<!-- self-assessment run {stamp} -->\n" + body.rstrip() + "\n"

    # Open in append mode and hold an exclusive advisory lock for the write.
    with open(target, "a", encoding="utf-8") as fh:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        except OSError:
            # Some filesystems (network mounts) reject flock; the lone 'a'
            # write is still atomic enough for a single appender. Proceed.
            pass
        fh.write(block)
        fh.flush()
        os.fsync(fh.fileno())
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
    sys.stderr.write(f"[append] appended {len(block)} bytes to {target}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
