#!/usr/bin/env python3
"""List every mod this machine can load: installed plugins, kept mods, session mods.

usage: mod_inventory.py [extra plugin dir ...]

A plugin is a mod when its hooks/hooks.json has a `modules` key. Built-in mods are
not listed here; `/plugin` shows them under Built-in.
"""
import glob
import json
import os
import subprocess
import sys


def is_mod(d):
    try:
        with open(os.path.join(d, "hooks", "hooks.json")) as fh:
            return "modules" in json.load(fh)
    except (OSError, ValueError):
        return False


def notes(d):
    """The `hooks:` and `calls:` lines that `claude plugin validate` reads from the source."""
    r = subprocess.run(["claude", "plugin", "validate", "--json", d], capture_output=True, text=True)
    try:
        found = [n for c in json.loads(r.stdout).get("contents", []) for n in c.get("notes", [])]
    except ValueError:
        return ["validate gave no JSON: " + (r.stderr or r.stdout).strip()[:120]]
    return found or ["validate lists no hooks line"]


def main(extra):
    rows = []
    r = subprocess.run(["claude", "plugin", "list", "--json"], capture_output=True, text=True)
    plugins = json.loads(r.stdout) if r.returncode == 0 else []
    for p in plugins:
        d = p.get("installPath") or ""
        if d and is_mod(d):
            state = "enabled" if p.get("enabled") else "disabled"
            rows.append((f"installed, {state}", p.get("id"), p.get("version"), d))
    home = os.path.expanduser("~")
    for where, pattern in (("kept", f"{home}/your-private-harness/mods/*/"),
                           ("session, not kept", f"{home}/.claude/dev-mods/*/*/")):
        rows += [(where, os.path.basename(d.rstrip("/")), "-", d) for d in glob.glob(pattern) if is_mod(d)]
    rows += [("extra", os.path.basename(d.rstrip("/")), "-", d) for d in extra if is_mod(d)]
    print(f"{len(plugins)} installed plugins read; {len(rows)} mods found")
    for where, name, version, d in rows:
        print(f"\n{name}  [{where}]  version {version}\n  {d}")
        for n in notes(d):
            print("  " + n)


if __name__ == "__main__":
    main(sys.argv[1:])
