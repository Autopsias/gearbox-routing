#!/usr/bin/env python3
"""Vendored-skill pin tracker: detect drift, never apply an update.

Third-party skill content is prompt-injection surface, so this script only
ever REPORTS. Applying an update stays a human decision (see SKILL.md).

  vendor.py verify  — offline: does the local copy still match its pin?
  vendor.py check   — online: has upstream moved since the pin? (needs `gh`)
  vendor.py diff <name>  — print the upstream compare URL + changed files

Both comparisons are on the vendored directory's git TREE hash, not on a
commit sha. A commit sha answers "did anything in the repo move", which is
the wrong question and cries wolf on every unrelated push; the tree hash
answers "is this content byte-identical", which is the question. That also
makes `verify` work with no network at all: git computes the same hash for
the same bytes, so a local hand-edit shows up as a mismatch.

Manifest: skills/repo-health/vendor.json
  [{"name": ..., "repo": "owner/name", "path": "upstream/subdir",
    "pin": "<commit we vendored from>", "tree": "<subtree sha at that pin>",
    "why": ..., "reviewed": "YYYY-MM-DD", "license": ...}]
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import parse_json, typed_items  # noqa: E402

SKILL_DIR = Path(__file__).resolve().parent.parent
MANIFEST = SKILL_DIR / "vendor.json"
VENDOR_DIR = SKILL_DIR / "vendor"


# What every command below reads off an entry. An entry missing one of these is
# not a pin, and reading it would raise a KeyError deep inside verify/check/diff.
ENTRY_FIELDS = {"name", "repo", "path", "pin", "tree"}


class UnusableManifest(Exception):
    """The manifest EXISTS and cannot be read as pins.

    Not the same as absent, and the difference is the whole check. Returning []
    here made `check()` print "no vendored skills (vendor.json absent or empty)"
    and exit 0, which SKILL.md maps to `sec.vendor-pins = pass` — so a tampered
    vendored SKILL.md passed a security gate because the manifest naming the
    trees to hash was unreadable. A gate that cannot fail, in the file that
    exists to detect tampering. The docstring below already promised this
    behaviour; only the code disagreed (found by review).

    Skipping SOME entries is the same defect one step smaller: a dropped entry is
    a vendored tree nobody hash-checks. So is an EMPTY manifest next to a full
    vendor/ directory, and so is an absent one. All of them raise.
    """


def vendored_dirs():
    """Every directory sitting in vendor/ — the population any verdict is over.

    `verify` speaks for the trees it hashed. Asking the MANIFEST what those are
    and never asking the DISK is how an empty manifest came to mean "nothing to
    check": zero entries, zero mismatches, exit 0, and SKILL.md maps that to
    `sec.vendor-pins = pass` while unhashed third-party skill trees sit on
    disk. So the disk is the population and the manifest is the coverage claim.
    """
    if not VENDOR_DIR.is_dir():
        return []
    return sorted(p.name for p in VENDOR_DIR.iterdir() if p.is_dir())


def load():
    """The manifest, guarded on TYPE, on COMPLETENESS, and against the disk.

    `{"skills": {}}` is valid JSON and would iterate its KEYS, so every entry
    arrived as a string and `e["name"]` raised `TypeError: string indices must be
    integers` — the same defect family as a notebook that parses to a list. An
    unusable manifest says so out loud rather than reading as "no vendored
    skills", which is what `check()` prints for an empty one.

    And a manifest that does not cover what is on disk is unusable in exactly the
    same way, whether it is short one entry, empty, or missing entirely: the
    trees it leaves out are trees nothing hashes, and `verify` exiting 0 would
    speak for them anyway. Only an empty manifest over an EMPTY vendor/ is clean.
    """
    try:
        raw = MANIFEST.read_text() if MANIFEST.exists() else "[]"
    except OSError as exc:
        # A manifest that exists and will not open is "we could not look" — and
        # letting the OSError escape made main() exit 1 (a finding) instead of 2.
        raise UnusableManifest(f"{MANIFEST} cannot be read: {exc}") from exc
    data = parse_json(raw, want=list)
    if data is None:
        raise UnusableManifest(
            f"{MANIFEST} is not a JSON array of pin objects")
    entries = [e for e in typed_items(data) if ENTRY_FIELDS <= e.keys()]
    if len(entries) != len(data):
        raise UnusableManifest(
            f"{len(data) - len(entries)} entr(y/ies) in {MANIFEST} are not objects "
            f"carrying {', '.join(sorted(ENTRY_FIELDS))}")
    pinned = {e["name"] for e in entries}
    unpinned = [d for d in vendored_dirs() if d not in pinned]
    if unpinned:
        raise UnusableManifest(
            f"{len(unpinned)} vendored tree(s) with no pin in {MANIFEST}: "
            f"{', '.join(unpinned)} — nothing hashes them, so no verdict here "
            f"covers them")
    return entries


def gh_json(path):
    """Upstream's answer, or None for "we could not ask" — including no `gh`.

    A missing `gh` binary used to raise FileNotFoundError straight out of
    subprocess.run, killing the process with exit 1 — which SKILL.md maps to
    "verify red, a vendored file was edited → fail". So an absent tool accused
    the vendored trees of tampering, while an unauthenticated one (below) said
    they were fine. Both are "we could not look", and check() now returns 2.
    """
    try:
        p = subprocess.run(["gh", "api", path], capture_output=True, text=True)
    except OSError:
        return None
    if p.returncode != 0:
        return None
    try:
        return json.loads(p.stdout)
    except json.JSONDecodeError:
        return None


def upstream_tree(entry):
    """Tree sha of the vendored directory on upstream's default branch.

    The contents API reports a directory entry's sha as its tree sha, so the
    parent listing answers this in one request.
    """
    path = entry["path"].rstrip("/")
    parent, _, name = path.rpartition("/")
    data = gh_json(f"repos/{entry['repo']}/contents/{parent}")
    if not isinstance(data, list):
        return None
    for item in data:
        if item.get("name") == name and item.get("type") == "dir":
            return item.get("sha")
    return None


def local_tree(entry):
    """Tree sha git computes for our vendored copy as it sits on disk.

    Hashed through a throwaway index so the working tree is measured, not the
    last commit — a re-vendor verifies before it is committed, and a hand-edit
    shows up immediately instead of after someone commits it. The real index
    is never touched.
    """
    root = _repo_root()
    rel = (VENDOR_DIR / entry["name"]).relative_to(root)
    if not (root / rel).is_dir():
        return None
    with tempfile.TemporaryDirectory() as tmp:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(tmp) / "index")}
        # No --force. The pin covers what we COMMITTED, so the hash must be taken
        # over exactly what git tracks. Forcing pulls in ignored generated files
        # and reports them as tampering: ruff drops a .ruff_cache/ inside any
        # vendored directory holding its own pyproject.toml, and --force adds it.
        add = subprocess.run(
            ["git", "add", "--", str(rel)],
            capture_output=True, text=True, cwd=root, env=env,
        )
        if add.returncode != 0:
            return None
        out = subprocess.run(
            ["git", "write-tree", f"--prefix={rel}/"],
            capture_output=True, text=True, cwd=root, env=env,
        )
        return out.stdout.strip() if out.returncode == 0 else None


def _repo_root():
    p = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, cwd=SKILL_DIR,
    )
    return Path(p.stdout.strip()) if p.returncode == 0 else SKILL_DIR


def verify(entries):
    """Offline: local copy vs its recorded pin. Catches hand-edits."""
    bad = 0
    for e in entries:
        got = local_tree(e)
        if got is None:
            print(f"?  {e['name']}: no vendored copy on disk at vendor/{e['name']}")
            bad += 1
            continue
        if got == e["tree"]:
            print(f"ok {e['name']}: matches pin {e['pin'][:12]} (tree {got[:12]})")
        else:
            bad += 1
            print(f"EDITED {e['name']}: tree {got[:12]} != pinned {e['tree'][:12]}")
            print("       the local copy no longer matches upstream at the pin.")
    if bad:
        print(f"\n{bad} vendored tree(s) edited in place. Revert, or re-vendor "
              f"from a reviewed upstream commit and update the pin.")
    return bad


def check(entries):
    """Online: upstream content vs our pin. Returns the EXIT CODE, 0/1/2.

    Three-valued for the same reason main() is: an entry whose upstream could not
    be reached — no network, no `gh`, unauthenticated, rate-limited — was printed
    with a `?` and then dropped. Nothing landed in `stale`, so the function
    returned 0, and SKILL.md maps exit 0 to `sec.vendor-pins = pass`: "vendored
    skills at reviewed pins", with ZERO trees compared against upstream (found by
    review). `verify` had this right from the start — its own `?`
    branch counts as bad — and the reasoning was already written down in main()'s
    UnusableManifest handler. It just was not applied to the network.
    """
    if not entries:
        print("no vendored skills (vendor.json absent or empty)")
        return 0
    stale, blind = [], []
    for e in entries:
        head = upstream_tree(e)
        if head is None:
            blind.append(e["name"])
            print(f"?  {e['name']}: could not reach upstream ({e['repo']})")
            continue
        if head == e["tree"]:
            print(f"ok {e['name']}: pinned {e['pin'][:12]}, upstream content unchanged")
        else:
            stale.append(e)
            print(f"UPDATE {e['name']}: upstream content changed since {e['pin'][:12]}")
            print(f"       review: https://github.com/{e['repo']}/commits/HEAD/{e['path']}")
    if stale:
        print(f"\n{len(stale)} update(s) available. Read the diff, then re-vendor and "
              f"bump pin + tree in one commit. Never auto-apply — vendored SKILL.md "
              f"is prompt text the agent will follow.")
    if blind:
        print(f"\nREFUSED: {len(blind)} pin(s) were NOT compared against upstream "
              f"({', '.join(blind)}) — `gh` is missing, unauthenticated or offline. "
              f"This is not a clean result; fix `gh` and re-run.")
    return 1 if stale else (2 if blind else 0)


def diff(entries, name):
    e = next((x for x in entries if x["name"] == name), None)
    if not e:
        sys.exit(f"no vendored skill named {name}")
    data = gh_json(f"repos/{e['repo']}/commits?per_page=20&path={e['path']}")
    if not isinstance(data, list) or not data:
        sys.exit("could not reach upstream")
    head = data[0]["sha"]
    print(f"https://github.com/{e['repo']}/compare/{e['pin'][:12]}...{head[:12]}")
    print(f"commits touching {e['path']} (newest first):")
    for c in data:
        marker = "  <-- pinned" if c["sha"] == e["pin"] else ""
        subject = c["commit"]["message"].split("\n")[0]
        print(f"  {c['sha'][:12]}  {c['commit']['committer']['date'][:10]}  {subject}{marker}")


def main(argv):
    try:
        entries = load()
    except UnusableManifest as exc:
        # 2, never 0 and never 1: this is "we could not look", which is neither
        # clean nor a finding. Exiting 0 here is what let the gate pass blind.
        print(f"REFUSED: {exc} — cannot verify any vendored tree")
        return 2
    cmd = argv[0] if argv else "check"
    if cmd == "verify":
        return 1 if verify(entries) else 0
    if cmd == "check":
        return check(entries)          # already 0/1/2 — see its docstring
    if cmd == "diff":
        if len(argv) < 2:
            sys.exit("usage: vendor.py diff <name>")
        diff(entries, argv[1])
        return 0
    sys.exit(f"unknown command: {cmd}")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
