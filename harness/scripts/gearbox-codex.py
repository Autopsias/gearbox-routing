#!/usr/bin/env python3
"""gearbox-codex.py — the SECOND deploy target: the Codex skill ports in ~/.codex.

`gearbox deploy` moves the primary target (~/.claude) by fast-forwarding a git
clone. ~/.codex can never be that: it holds auth.json, sessions/, four sqlite
databases, plugins/cache/ and history.jsonl, and it is not a clone and must
never become one. So the Codex ports are RENDERED into it — an idempotent,
path-scoped copy that never deletes anything it did not write
(skills/plan-execute/references/dual-harness-contract.md § 6.2).

Three verbs, one policy:

    drift         what in the render target differs from its source, and why.
                  exit 0 clean · 1 blocking drift · 2 could not inspect.
    render        copy the manifest-named files into <codex-target>/<name>/,
                  stamp each managed dir, retire same-named shadow skills (§ 6.3).
    harvest-back  copy blocking drift back into the source tree (and `git add` a
                  file that exists only in the target), so the ordinary
                  `gearbox harvest` commits it like any other hotfix.

WHAT IT MAY TOUCH — this is the whole safety argument, and it is structural.
gearbox never globs the ~/.codex root. The only paths it reads or writes are
<codex-target>/<name>/<file>, where BOTH <name> and <file> come from a tracked
manifest.toml in this repo. Codex's runtime surface — sessions/, cache/,
plugins/, history.jsonl, the sqlite DBs, auth.json — is enumerated nowhere, so
it cannot be reported as drift and cannot be harvested. Widening that requires
editing a tracked manifest, which is a reviewable act.

Classification of a difference (fail-closed — an unexplained difference is a
hotfix, never "probably fine"):

    modified   target file differs from source AND from the revision its stamp
               says it was rendered at            -> HOTFIX, blocks a deploy
    unstamped  same, but the difference cannot even be explained: the managed dir
               carries no .gearbox-rendered stamp, or its stamp names a revision
               this tree does not have (an out-of-band render)
                                                  -> HOTFIX, blocks a deploy
    extra      a file inside a managed dir that the source does not render
                                                  -> HOTFIX, blocks a deploy
    stale      target still matches the revision it was rendered at; the source
               has simply moved on                -> `render` fixes it, never blocks
    missing    source renders a file the target does not have yet
                                                  -> `render` fixes it, never blocks

Usage:
    gearbox-codex.py {drift,render,harvest-back} [--claude-dir DIR]
                     [--pathspec FILE] [--codex-skills DIR] [--json]

`--codex-skills` (or $GEARBOX_CODEX_SKILLS) overrides the [codex-target] entry;
it exists so the regression check can exercise the real code against a fixture.
"""
from __future__ import annotations

import argparse
import datetime
import glob
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tomllib

SELF = os.path.dirname(os.path.abspath(__file__))

STAMP = ".gearbox-rendered"
# ponytail: .DS_Store is written by Finder into any directory a human opens. A
# gate that aborts a deploy over it gets switched off, which is worse than the
# hole. Everything else unexpected inside a managed dir is still `extra`.
IGNORE_FILES = {STAMP, ".DS_Store"}
IGNORE_DIRS = {"__pycache__"}

BLOCKING = ("modified", "unstamped", "extra")
INFO = ("stale", "missing")


class Fail(Exception):
    """Anything that makes the target uninspectable or the source malformed."""


# --------------------------------------------------------------------------
# config: the pathspec is the ONE tracked definition, shared with the classifier
# --------------------------------------------------------------------------
def load_pathspec(path):
    """Reuse gearbox-classify.py's parser — one pathspec, one parser."""
    spec = importlib.util.spec_from_file_location(
        "gearbox_classify", os.path.join(SELF, "gearbox-classify.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.load_pathspec(path)


def codex_skills_dir(spec, override):
    if override:
        return os.path.abspath(os.path.expanduser(override))
    entries = spec.get("codex-target", [])
    if len(entries) != 1:
        raise Fail("[codex-target] in the pathspec must name exactly one directory "
                   f"(found {len(entries)})")
    return os.path.abspath(os.path.expanduser(entries[0]))


def _safe_rel(entry, where):
    """A render entry may only ever address inside its own port directory."""
    if (not isinstance(entry, str) or not entry or os.path.isabs(entry)
            or entry.startswith("~") or ".." in entry.split("/")):
        raise Fail(f"{where}: unsafe `render` entry {entry!r} — must be a relative "
                   "path inside the port directory")
    return entry


class Port:
    """One Codex skill port: a source dir here, a managed dir in the target."""

    def __init__(self, name, src, src_rel, render, shadows):
        self.name, self.src, self.src_rel = name, src, src_rel
        self.render, self.shadows = render, shadows


def load_ports(claude_dir, spec):
    """Every source dir named by [codex-render], with its manifest read."""
    patterns = spec.get("codex-render", [])
    if not patterns:
        raise Fail("the pathspec has no [codex-render] section — nothing to render")
    ports, by_name = [], {}
    for pattern in patterns:
        for src in sorted(glob.glob(os.path.join(claude_dir, pattern))):
            if not os.path.isdir(src):
                continue
            src_rel = os.path.relpath(src, claude_dir)
            manifest = os.path.join(src, "manifest.toml")
            if not os.path.isfile(manifest):
                # Loud, never skipped: a port with no manifest would be silently
                # undeployed, which is exactly the "gate that cannot fail" shape.
                raise Fail(f"{src_rel} matches [codex-render] but has no manifest.toml")
            with open(manifest, "rb") as fh:
                data = tomllib.load(fh)
            name = data.get("name")
            if not isinstance(name, str) or not name or "/" in name or name.startswith("."):
                raise Fail(f"{src_rel}/manifest.toml: `name` must be a plain directory name")
            render = data.get("render")
            if not isinstance(render, list) or not render:
                raise Fail(f"{src_rel}/manifest.toml: `render` must be a non-empty list")
            for entry in render:
                _safe_rel(entry, f"{src_rel}/manifest.toml")
            shadows = data.get("shadows", [])
            if not isinstance(shadows, list):
                raise Fail(f"{src_rel}/manifest.toml: `shadows` must be a list")
            if name in by_name:
                raise Fail(f"two Codex ports claim the name {name!r}: "
                           f"{by_name[name]} and {src_rel}")
            by_name[name] = src_rel
            ports.append(Port(name, src, src_rel, render, shadows))
    return ports


# --------------------------------------------------------------------------
# file enumeration — bounded to manifest-named paths, always
# --------------------------------------------------------------------------
def _tree_files(root):
    """{relative path -> absolute path} for every file under `root`."""
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in IGNORE_DIRS)
        for fn in sorted(filenames):
            if fn in IGNORE_FILES:
                continue
            full = os.path.join(dirpath, fn)
            out[os.path.relpath(full, root)] = full
    return out


def source_files(port):
    """{relative path -> absolute source path} for everything `render` names."""
    out = {}
    for entry in port.render:
        p = os.path.join(port.src, entry)
        if os.path.isfile(p):
            out[entry] = p
        elif os.path.isdir(p):
            for rel, full in _tree_files(p).items():
                out[os.path.join(entry, rel)] = full
        else:
            raise Fail(f"{port.src_rel}/manifest.toml renders {entry!r}, "
                       "which does not exist")
    return out


def read_bytes(path):
    with open(path, "rb") as fh:
        return fh.read()


def read_stamp(target_dir):
    try:
        with open(os.path.join(target_dir, STAMP), encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def blob_at(claude_dir, commit, relpath):
    """Content of `relpath` at `commit`, or None if it is not there."""
    proc = subprocess.run(["git", "-C", claude_dir, "show", f"{commit}:{relpath}"],
                          capture_output=True)
    return proc.stdout if proc.returncode == 0 else None


def rev_exists(claude_dir, commit):
    """Is `commit` a commit this tree actually has? (An out-of-band render's is not.)"""
    return subprocess.run(["git", "-C", claude_dir, "cat-file", "-e", f"{commit}^{{commit}}"],
                          capture_output=True).returncode == 0


def head_commit(claude_dir):
    proc = subprocess.run(["git", "-C", claude_dir, "rev-parse", "HEAD"],
                          capture_output=True, text=True)
    return proc.stdout.strip() if proc.returncode == 0 else None


# --------------------------------------------------------------------------
# drift
# --------------------------------------------------------------------------
def scan(claude_dir, skills_dir, ports):
    findings = []
    for port in ports:
        target_dir = os.path.join(skills_dir, port.name)
        src = source_files(port)
        tgt = _tree_files(target_dir) if os.path.isdir(target_dir) else {}
        stamped_at = (read_stamp(target_dir) or {}).get("source_commit")

        for rel in sorted(set(src) | set(tgt)):
            s_path, t_path = src.get(rel), tgt.get(rel)
            if s_path and not t_path:
                kind = "missing"
            elif t_path and not s_path:
                kind = "extra"
            else:
                t_bytes = read_bytes(t_path)
                if read_bytes(s_path) == t_bytes:
                    continue
                # A difference is only excusable if the target still matches the
                # revision it was rendered at — i.e. the SOURCE moved, not the
                # target. Anything else is a hand edit in the deploy target.
                # No stamp, or a stamp naming a revision this tree does not have
                # (an out-of-band render from another clone), means provenance
                # cannot be established at all: `unstamped`, and still blocking.
                kind = "unstamped"
                if stamped_at and rev_exists(claude_dir, stamped_at):
                    was = blob_at(claude_dir, stamped_at,
                                  f"{port.src_rel}/{rel}".replace(os.sep, "/"))
                    kind = "stale" if was is not None and was == t_bytes else "modified"
            findings.append({
                "port": port.name,
                "source": f"{port.src_rel}/{rel}".replace(os.sep, "/"),
                "target": os.path.join(target_dir, rel),
                "rel": rel,
                "kind": kind,
                "blocking": kind in BLOCKING,
            })
    return findings


def report(skills_dir, ports, findings, as_json):
    blocking = [f for f in findings if f["blocking"]]
    if as_json:
        print(json.dumps({
            "codex_skills": skills_dir,
            "ports": [{"name": p.name, "source": p.src_rel} for p in ports],
            "findings": findings,
            "blocking": len(blocking),
        }, indent=2))
        return

    print(f"codex render target: {skills_dir}")
    if ports:
        print("  managed skills: " + ", ".join(p.name for p in ports))
    else:
        # Say why, so an empty green report can never be mistaken for "checked and
        # fine": before the ports land in this tree there is nothing to compare.
        print("  managed skills: (none) — this revision has no skills/*/codex port, "
              "so nothing in the render target is managed yet")
    if not findings:
        print("  clean — every rendered file matches its source")
        return
    labels = {
        "modified": "HOTFIX  edited in the deploy target (blocks a deploy)",
        "unstamped": "HOTFIX  differs, and its provenance cannot be established — no render "
                     "stamp, or a stamp naming a revision this tree does not have "
                     "(blocks a deploy)",
        "extra": "HOTFIX  present in the target, not rendered by the source (blocks a deploy)",
        "stale": "stale   target still matches the revision it was rendered at (render fixes)",
        "missing": "missing not rendered yet (render fixes)",
    }
    for kind in BLOCKING + INFO:
        rows = [f for f in findings if f["kind"] == kind]
        if not rows:
            continue
        print(f"  {labels[kind]}: {len(rows)}")
        for f in rows:
            print(f"      {f['port']}/{f['rel']}")
            print(f"          target {f['target']}")
            print(f"          source {f['source']}")


def do_drift(args, spec, claude_dir, skills_dir, ports):
    findings = scan(claude_dir, skills_dir, ports)
    report(skills_dir, ports, findings, args.json)
    return 1 if any(f["blocking"] for f in findings) else 0


# --------------------------------------------------------------------------
# harvest-back
# --------------------------------------------------------------------------
def do_harvest_back(args, spec, claude_dir, skills_dir, ports):
    findings = [f for f in scan(claude_dir, skills_dir, ports) if f["blocking"]]
    if not findings:
        print("codex harvest: nothing to carry back from the render target")
        return 0

    notes = []
    for f in findings:
        dest = os.path.join(claude_dir, f["source"])
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copy2(f["target"], dest)
        print(f"codex harvest: {f['kind']:<9} {f['target']}\n"
              f"                     -> {f['source']}")
        if f["kind"] == "extra":
            # A path git has never seen would land in `triage_untracked`, which
            # `gearbox harvest` deliberately never auto-stages — so stage it here,
            # where an operator has explicitly asked for the carry-back.
            subprocess.run(["git", "-C", claude_dir, "add", "--", f["source"]], check=True)
            notes.append(f)

    if notes:
        print("")
        print("NOTE: these files exist in the render target but no manifest renders them:")
        for f in notes:
            print(f"        {f['source']}")
        print("      They are now staged, but `drift` will KEEP reporting them until the")
        print("      `render` list in the port's manifest.toml names their top-level entry")
        print("      — or they are removed from the render target. That call is yours:")
        print("      a file the source does not render is not part of the skill.")
    return 0


# --------------------------------------------------------------------------
# render
# --------------------------------------------------------------------------
def retire_shadow(path, skills_dir):
    """§ 6.3: move a same-named skill in another root out of the way. Never delete."""
    p = os.path.abspath(os.path.expanduser(path))
    if not os.path.exists(p):
        return None
    # A `shadows` typo pointing back at the render root would relocate a skill we
    # just rendered. Shadows live in OTHER roots, by definition.
    if os.path.commonpath([p, skills_dir]) == skills_dir:
        raise Fail(f"shadow {path!r} resolves inside the render target {skills_dir} — "
                   "a shadow is a same-named skill in ANOTHER root; refusing to move it")
    parent = os.path.dirname(p)
    if os.path.basename(parent) != "skills":
        raise Fail(f"shadow {path!r}: refusing to retire — its parent is not a "
                   "`skills` directory, so there is no obvious `skills-disabled` "
                   "sibling to move it to")
    dest_root = os.path.join(os.path.dirname(parent), "skills-disabled")
    dest = os.path.join(dest_root, os.path.basename(p))
    os.makedirs(dest_root, exist_ok=True)
    if os.path.exists(dest):
        dest = f"{dest}.{datetime.datetime.now():%Y%m%dT%H%M%S}"
    shutil.move(p, dest)
    return dest


def do_render(args, spec, claude_dir, skills_dir, ports):
    commit = head_commit(claude_dir)
    total_written = 0
    for port in ports:
        target_dir = os.path.join(skills_dir, port.name)
        os.makedirs(target_dir, exist_ok=True)
        written = []
        for rel, s_path in sorted(source_files(port).items()):
            dest = os.path.join(target_dir, rel)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            if os.path.isfile(dest) and read_bytes(dest) == read_bytes(s_path):
                continue
            shutil.copy2(s_path, dest)
            written.append(rel)

        stamp_path = os.path.join(target_dir, STAMP)
        old = read_stamp(target_dir) or {}
        if written or old.get("source_commit") != commit:
            with open(stamp_path, "w", encoding="utf-8") as fh:
                json.dump({
                    "skill": port.name,
                    "source": port.src_rel,
                    "source_commit": commit,
                    "rendered_at": datetime.datetime.now(datetime.UTC)
                                   .strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "rendered_from": claude_dir,
                }, fh, indent=2)
                fh.write("\n")

        total_written += len(written)
        print(f"render {port.name}: {len(written)} file(s) written"
              + (f" ({', '.join(written)})" if written else " — already current"))

        for shadow in port.shadows:
            moved = retire_shadow(shadow, skills_dir)
            if moved:
                print(f"       shadow retired: {shadow} -> {moved}")

    print(f"render: {len(ports)} skill(s), {total_written} file(s) written into {skills_dir}")
    return 0


# --------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("verb", choices=["drift", "render", "harvest-back"])
    ap.add_argument("--claude-dir",
                    default=os.environ.get("CLAUDE_DIR") or os.path.expanduser("~/.claude"))
    ap.add_argument("--pathspec", default=os.path.join(SELF, "deploy.pathspec"))
    ap.add_argument("--codex-skills", default=os.environ.get("GEARBOX_CODEX_SKILLS"),
                    help="override the [codex-target] entry (regression checks only)")
    ap.add_argument("--json", action="store_true", help="machine-readable report")
    ap.add_argument("--text", action="store_true",
                    help="human-readable report (the default; accepted so callers can be explicit)")
    args = ap.parse_args(argv)

    try:
        spec = load_pathspec(args.pathspec)
        skills_dir = codex_skills_dir(spec, args.codex_skills)
        ports = load_ports(args.claude_dir, spec)
        verb = {"drift": do_drift, "render": do_render,
                "harvest-back": do_harvest_back}[args.verb]
        return verb(args, spec, args.claude_dir, skills_dir, ports)
    except Fail as exc:
        sys.stderr.write(f"gearbox-codex: {exc}\n")
        return 2
    except OSError as exc:
        sys.stderr.write(f"gearbox-codex: could not inspect the render target: {exc}\n")
        return 2


if __name__ == "__main__":
    sys.exit(main())
