#!/usr/bin/env python3
"""sync-from-claude.py — manifest-driven export pipeline (PKG-02).

Copies the PUBLISHABLE surfaces of the PRIVATE SOURCE REPO into this repo's
harness/ tree, applies deterministic scrub transforms, stamps the export with
SYNCED-FROM provenance, then runs two independent scan layers over the WHOLE
Gearbox working tree (not just the copied subtree) before declaring green.
Nothing here ever pushes or commits — that stays a manual, reviewed step.

THE SOURCE IS THE SOURCE REPO, NOT THE DEPLOY TARGET:
The three tiers are  a PRIVATE SOURCE clone (the edit surface)  ->
~/.claude (DEPLOY TARGET, fast-forward only)  ->  this repo (PUBLIC EXPORT).
`--source` is REQUIRED and must name the source clone; SYNCED-FROM records
that repo's commit. There is no default, for the same reason the manifest has
no default path: a default would have to name one person's private layout.
Exporting from ~/.claude instead would stamp provenance against a tree that
only ever mirrors the source and additionally carries un-exportable runtime
state (projects/*/memory/**, _plans/**, caches). `--claude-home` is kept as a
deprecated alias so older runbooks still work.

WHAT THIS SCRIPT WRITES — harness/ ONLY (standing export policy):
claude/ is HAND-MAINTAINED (genericized EXAMPLE routing profiles, never real
calibration data or client names) and is refreshed as its own separately
reviewed step. This script never touches it.

WHY THE MANIFEST IS AN EXTERNAL INPUT (adv-codex-manifest-leaks):
The manifest holds confidential values as data, so it must not live in a
public repo. This script takes --manifest POINTING OUTSIDE this repo and
HARD-FAILS if the resolved manifest path is inside the Gearbox worktree. Only
a fake-placeholder schema example (sync-from-claude.manifest.example.json,
committed alongside this script) may ever live in Gearbox.

WHY THE SCRUB RULES ALSO LIVE IN THE MANIFEST (s06-fix, findings 1+2):
A scrub rule's MATCH SOURCE is the confidential value. A published rule that
rewrites a literal into a placeholder re-publishes that literal one directory
up from the file it cleans — and it does so in the very file every gate used
to exempt by name. So this script carries the MECHANISM and the manifest
carries the STRINGS. There are now ZERO scan exemptions: both layers cover
100% of the tree, this file included.

SCAN SCOPE (adv-codex-scan-whole-repo):
Both scan layers run over the ENTIRE Gearbox git worktree, not just
harness/. This session and its neighbor (s07) also touch changelog/docs/
install-surface files outside harness/ — sensitive strings can leak through
those paths too, so the gate must not be subtree-scoped.

TWO SCAN LAYERS (hard-fail on any hit, no soft-fail path):
  1. Secrets: gitleaks (using THIS repo's committed .gitleaks.toml, not
     gitleaks defaults, and NOT gitleaks's default --no-git file-system mode
     which skips dotfiles) if installed; else `semgrep --config p/secrets`;
     else this script prints a loud, unmissable gap and still hard-fails
     (never a silent skip) unless --allow-missing-scanner is passed for local
     iteration.
  2. Blocked-pattern grep: every regex in the manifest's blocked_patterns,
     grep -rniE across the whole tree (respecting .gitignore via git
     ls-files, so runtime-cache junk doesn't produce noise) — any hit is
     printed as file:line and is fatal.

PROVENANCE — SPLIT BY TIER (s06-fix3 item 1):
harness/SYNCED-FROM used to stamp the SOURCE REPO'S COMMIT SHA into the public
repo on every sync. Nobody reading this repo can resolve that SHA, so it was
never provenance a public reader could act on — it was a permanent, unique
token correlating this repo to a private history, republished on every export.
So the two audiences are now served separately:

  PUBLIC  harness/SYNCED-FROM carries only what a public reader can act on —
          the export date and the pipeline contract version. Its shape is
          enforced by check_provenance_stamp() as a KEY ALLOWLIST, so a
          re-added SHA (or any other new field) is a scan hit, at pre-push too.
  PRIVATE the source revision, whether it was the authoritative pushed SHA or
          a local-HEAD fallback, and whether the source worktree was dirty, are
          appended to an operator-side JSONL ledger written NEXT TO THE
          MANIFEST — the external, private-tier location that already exists
          for exactly this class of data. Join key to the public stamp is the
          timestamp: ledger `exported_at` == SYNCED-FROM `exported_at`.

So "which source revision produced this export?" is still answerable by the
operator, and unanswerable from the public repo alone. Ledger writes are
fail-closed: if the ledger cannot be written the export aborts (exit 2) rather
than producing an export whose provenance was silently never recorded.

IDEMPOTENCY: re-running with no upstream source changes produces an empty diff
in harness/ (SYNCED-FROM's exported_at timestamp is the only field allowed to
differ run-to-run; compare with `git diff -- harness/ ':!harness/SYNCED-FROM'`
if you want a byte-exact check).

ponytail: no daemon, no git hook wiring here — this is a single manually-run
script + one external manifest file. A future skill wrapper (e.g. a
"/harness-sync" slash command) would hook in right at main()'s argument
defaults; nothing about this script's contract needs to change for that.

Usage:
  sync-from-claude.py --manifest PATH --source DIR [--repo-dir DIR]
                       [--private-commit SHA] [--allow-missing-scanner]
                       [--scan-only]

Exit codes: 0 = synced + both scan layers green. 1 = scan layer found a hit
(secrets or blocked pattern) — always fails closed. 2 = structural/tooling
failure (bad manifest, manifest inside repo, missing source path, scanner
absent without --allow-missing-scanner).
"""
from __future__ import annotations

import argparse
import base64
import binascii
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.parse
from pathlib import Path

SCAN_STATUS_REL = "_scan-status.json"  # written by caller (sync-and-scan wrapper) if requested

# Bumped whenever the manifest contract changes shape. load_manifest() refuses
# any manifest that does not declare exactly this — a stale or foreign policy
# must fail closed rather than scan with rules this script cannot interpret.
MANIFEST_SCHEMA_VERSION = "2"

# Bumped when the SHAPE of what this pipeline produces changes (what it copies,
# what it scrubs, what it stamps). Published in harness/SYNCED-FROM so a reader
# can tell which export contract produced the tree they are looking at. It is a
# property of this script, not of any deployment — it discloses nothing.
PIPELINE_VERSION = "3"


class SyncError(Exception):
    """Structural failure — fail closed, exit 2."""


def fatal(msg: str) -> "SyncError":
    return SyncError(msg)


# ---------------------------------------------------------------------------
# Manifest loading + external-location guard
# ---------------------------------------------------------------------------

def load_manifest(manifest_path: Path, repo_dir: Path) -> dict:
    repo_dir = repo_dir.resolve()
    resolved = manifest_path.resolve()
    try:
        resolved.relative_to(repo_dir)
        inside_repo = True
    except ValueError:
        inside_repo = False
    if inside_repo:
        raise fatal(
            f"REFUSED: manifest path {resolved} resolves INSIDE the Gearbox worktree "
            f"({repo_dir}). The real manifest holds the confidential values as data and "
            "must live OUTSIDE this repo — pass --manifest pointing at your private-tier "
            "copy instead. Only sync-from-claude.manifest.example.json (a fake "
            "placeholder) may live in Gearbox."
        )
    if not resolved.is_file():
        raise fatal(f"manifest not found: {resolved}")
    with open(resolved, encoding="utf-8") as f:
        data = json.load(f)
    # FAIL CLOSED ON AN EMPTY OR UNVERSIONED POLICY (s06-fix finding D).
    # Presence-only checks accepted {"entries": [], "blocked_patterns": []} —
    # which compiles to zero patterns, finds zero hits, and reports GREEN having
    # checked nothing. A gate that cannot distinguish "clean" from "no policy
    # loaded" is not a gate. This matters more now that the manifest is the SOLE
    # home of the confidential literals.
    for key in ("entries", "blocked_patterns"):
        if key not in data:
            raise fatal(f"manifest missing required key: {key}")
        if not isinstance(data[key], list) or not data[key]:
            raise fatal(
                f"manifest key {key!r} is empty or not a list. An empty policy would "
                "produce a GREEN scan having checked nothing — refusing. Point "
                "--manifest / GEARBOX_EXPORT_MANIFEST at your real private-tier manifest."
            )
    schema = data.get("_meta", {}).get("schema_version")
    if schema != MANIFEST_SCHEMA_VERSION:
        raise fatal(
            f"manifest _meta.schema_version is {schema!r}, expected "
            f"{MANIFEST_SCHEMA_VERSION!r}. A stale or foreign manifest must fail "
            "closed, never scan with a policy this script does not understand."
        )
    return data


# ---------------------------------------------------------------------------
# Scrub rules — LOADED FROM THE MANIFEST, never hardcoded in this file.
#
# Rule shape: {"find": str, "replace": str, "regex": bool?, "ignorecase": bool?,
#              "path": str?}.  `path` is relative to harness/ and scopes the
# rule to that one file; absent means tree-wide. Rules are applied IN LIST
# ORDER, so a per-file rule must be listed before any tree-wide rule that would
# otherwise mangle its match.
#
# Replacement is LITERAL (applied via a lambda), so a replacement string
# containing backslashes or \g<...> is never re-interpreted as a backreference.
# ---------------------------------------------------------------------------

ScrubRule = "tuple[re.Pattern, str, str | None]"


def compile_scrub_rules(manifest: dict) -> list:
    raw = manifest.get("scrub_rules")
    if not isinstance(raw, list) or not raw:
        raise fatal(
            "manifest has no non-empty scrub_rules[] — refusing to export. The scrub "
            "strings are private-tier data and live ONLY in the manifest; an export "
            "with no rules would copy the source through verbatim."
        )
    rules = []
    for i, r in enumerate(raw):
        if not isinstance(r, dict) or "find" not in r or "replace" not in r:
            raise fatal(f"scrub_rules[{i}] must be an object with 'find' and 'replace'")
        pattern = r["find"] if r.get("regex") else re.escape(r["find"])
        flags = re.IGNORECASE if r.get("ignorecase") else 0
        try:
            rx = re.compile(pattern, flags)
        except re.error as e:
            raise fatal(f"scrub_rules[{i}] 'find' is not a valid regex: {r['find']!r} ({e})")
        rules.append((rx, r["replace"], r.get("path")))
    return rules


def apply_scrub_rules(text: str, rules: list, rel_path: str | None = None) -> str:
    out = text
    for rx, repl, scope in rules:
        if scope is not None and scope != rel_path:
            continue
        out = rx.sub(lambda _m, _r=repl: _r, out)
    return out


# ---------------------------------------------------------------------------
# Transform application: PER-FILE targeted transforms, keyed by relative path.
#
# The manifest's `transforms` field is free-text ("Replace L26 ... with ...").
# That prose isn't machine-parseable in general, so this script does NOT try
# to interpret arbitrary prose — it implements a hand-authored, targeted
# rewrite function for every specific scrub-then-publish entry the s05
# manifest actually lists, keyed by that entry's relative path. A manifest
# entry with verdict=scrub-then-publish and NO matching function here is a
# HARD FAILURE (fail-closed: an unhandled scrub instruction must never
# silently ship as a raw copy) unless it also carries a generic
# transform recognized by apply_generic_transforms (currently just the
# blanket personal-path scrub, which scrub_tree also applies tree-wide as a
# second pass).
# ---------------------------------------------------------------------------

# The ordered find/replace table that used to live here is now
# manifest["scrub_rules"] — see compile_scrub_rules(). Only transforms whose
# logic is STRUCTURAL (marker-delimited block replacement, JSON key
# allowlisting, placeholder stamping) remain in this file, because their code
# names no confidential value.


def _scrub_claude_md(text: str) -> str:
    out = text
    # s06 finding SC-06 — THE ROUTING DIGEST IS REAL CALIBRATION DATA.
    # The source's CLAUDE.md carries a rendered digest of the PRIVATE
    # model-routing.yaml: concrete model generations, the measured effort
    # ladder, the dead/operator-elected rungs, the peer-lane model id. That is
    # exactly the private-tier calibration the standing export policy says
    # never publishes (only the hand-maintained, EXAMPLE-marked profiles under
    # claude/ do). Replace the whole block with a pointer to the public,
    # genericized SSOT rather than shipping a second, real one.
    out = re.sub(
        r"^<!-- BEGIN ROUTING.*?-->\n.*?^<!-- END ROUTING -->\n?",
        "<!-- BEGIN ROUTING -->\n"
        "## Task routing — classify before you start\n"
        "\n"
        "This deployment's rendered routing digest is **deliberately not exported** — it is\n"
        "measured, provider-specific calibration data, not harness code. Render your own\n"
        "from the genericized SSOT that ships with this repo:\n"
        "\n"
        "    python3 claude/scripts/render-routing-digest.py --variant full\n"
        "\n"
        "Authority: `claude/model-routing.yaml` (EXAMPLE profiles — re-verify the model\n"
        "lineup, effort semantics and prices against live provider docs, then recalibrate\n"
        "with `/routing-update` before relying on any row).\n"
        "<!-- END ROUTING -->\n",
        out,
        flags=re.M | re.S,
    )
    if "<!-- BEGIN ROUTING -->" not in out:
        # Fail closed: the source no longer carries the markers this transform
        # keys on, which means the real digest may be shipping unreplaced.
        raise fatal(
            "CLAUDE.md scrub: BEGIN/END ROUTING markers not found — the private routing "
            "digest may be shipping unscrubbed. Fix _scrub_claude_md before exporting."
        )
    return out


def _scrub_settings_json(text: str) -> str:
    """Rebuild settings.json from an ALLOWLIST of keys, never a regex scrub.

    Dropped keys are dropped whole, not rewritten: some of them hold
    credential-adjacent values, and a rewrite of an unanticipated shape is a
    silent partial. An allowlist fails closed on anything new.
    """
    data = json.loads(text)
    allowed_keys = {"hooks", "statusLine"}
    return json.dumps({k: v for k, v in data.items() if k in allowed_keys}, indent=2) + "\n"


def _scrub_prod_status_pinned_sh(text: str) -> str:
    # The real pin only matches the operator's UNSCRUBBED script, so publishing
    # it would mislead an adopter into verifying against a file they can't have.
    return re.sub(
        r'PINNED_SHA256="[0-9a-f]{64}"',
        'PINNED_SHA256="REPLACE_ME_recompute_for_your_own_scrubbed_prod-status.sh"',
        text,
    )


# ---------------------------------------------------------------------------
# deploy.pathspec — publish the POLICY SHAPE, never the deployment's own map.
#
# The pathspec has three sections and they are NOT the same disclosure:
#
#   [live-state] / [settings-churn-keys] are generic categories over surfaces
#   this harness and the Claude Code binary already document publicly — glob
#   classes, never instances. They ship as-is, which is what lets the exported
#   classifier actually classify a real deploy target instead of being a toy.
#
#   [mode-0600] is different in kind. It is a list of which files on a deploy
#   target hold credentials — an attack-surface map, per-deployment, and of no
#   use to anyone else. Publishing it teaches nothing that generic placeholders
#   do not, so the export replaces it with fictional names.
#
# The obvious objection to fictional names is that they ship a classifier that
# silently protects nothing, which is worse than the disclosure. That is why
# `scripts/gearbox` REFUSES to deploy while a REPLACE_ME_ entry remains: the
# failure is loud and at the exact moment it matters, not silent.
#
# Everything outside the section bodies is REGENERATED from the template below
# rather than filtered, so private prose in the source's comments (decision
# ids, incident notes) cannot ride along, now or after a future edit. Unknown
# sections are a hard failure: a section this function has not classified could
# be anything, and defaulting to "publish it" is how maps leak.
#
# NOTE this function names NO string it removes. Naming the credential
# filenames here in order to match them would republish them one directory up
# from the file being cleaned — s06-fix finding 1+2, the reason scrub strings
# live in the manifest at all. Whole-section replacement needs no match string.
# ---------------------------------------------------------------------------

# Order matters: this tuple is compared to the section order as parsed, so it
# must track deploy.pathspec's own layout. `codex-target`/`codex-render` were
# added upstream when the Codex skill port became a second deploy target, and
# this transform had never classified them — the sync refused every run until
# each was decided (2026-08-15).
#
# Both ship REAL, for the same reason [live-state] does. `skills/*/codex` is a
# repo-relative glob and says nothing about any machine. `~/.codex/skills` is
# the standard Codex install path — identical for every user, so it reveals no
# deployment detail; it is not the [mode-0600] case, where the list IS a map of
# which files on one operator's box hold credentials. Templating it would ship
# a deploy.pathspec that cannot classify a real Codex target out of the box,
# which is the exact failure the [live-state] note below warns against.
_DEPLOY_PATHSPEC_SECTIONS = (
    "live-state", "settings-churn-keys", "codex-target", "codex-render", "mode-0600",
)

_DEPLOY_PATHSPEC_TEMPLATE = """\
# deploy.pathspec — the ONE tracked definition of how `scripts/gearbox` classifies
# a dirty path in the deploy target (~/.claude). Read by scripts/gearbox-classify.py;
# every consumer (deploy, drift, harvest, the SessionStart hook, the weekly loop)
# goes through that classifier, so this file is the single source of the policy.
#
# THREE CLASSES, fail-closed. Anything this file does not explicitly name is
# HARNESS-CODE — the loud class that aborts a deploy. Adding a path to a list
# below is a deliberate act of saying "a machine writes this; do not alarm on it".
#
#   HARNESS-CODE  authored harness source. Dirt here in the deploy target is a
#                 hotfix that has not been carried back. It ABORTS `gearbox deploy`
#                 and is only committed by an explicit `gearbox harvest`.
#   LIVE-STATE    runtime output that can ONLY be produced in the deploy target
#                 (auto-memory, plan trees, routing eval output). Dirt here is
#                 ROUTINE — it never blocks a deploy and is auto-harvested with
#                 provenance.
#   MACHINE-CHURN a value the Claude Code binary itself rewrites in response to a
#                 UI action (/model, /effort, "allow always", /plugin, /statusline).
#                 Same treatment as LIVE-STATE: auto-harvested, never blocking.
#                 Expressed as JSON key paths, not file paths — see below.
#
# Syntax: `[section]` headers; one entry per line; `#` comments; blank lines ignored.
# Globs: `*` matches within one path segment, `**` matches across segments.
#
# WHAT THIS EXPORTED COPY CARRIES, AND WHAT IT DOES NOT:
# [live-state], [settings-churn-keys], [codex-target] and [codex-render] are
# generic categories — glob classes over this harness's own documented runtime
# surfaces, the Claude Code binary's own settings keys, and the standard Codex
# skills location, which is the same path for everyone. They ship real, so this
# file classifies a live deploy target out of the box.
# [mode-0600] does not ship real. That list is a map of which files on a deploy
# target hold credentials; it is per-deployment and it is nobody else's
# business, so the names below are FICTIONAL PLACEHOLDERS. `gearbox deploy`
# refuses to run while any REPLACE_ME_ entry remains — an un-edited list would
# chmod nothing and protect nothing silently, which is worse than no list.

# --- LIVE-STATE: machine-written runtime surface -----------------------------
[live-state]
{live_state}

# --- MACHINE-CHURN: settings.json keys the binary rewrites on a UI action ------
# Dotted JSON key paths, one per line; each is a key some UI action rewrites
# under you (a model switch, an effort switch, an "allow always" click, a theme
# or plugin toggle). A changed key is churn iff it, or one of its dotted
# ancestors, is listed here. Everything else in settings.json — notably `hooks`,
# `env`, `permissions.deny`, `permissions.ask` and `permissions.defaultMode` —
# is HARNESS-CODE: a change there is real drift and MUST abort a deploy.
#
# Treating the whole file as harness source instead makes an ordinary model
# switch a deploy-aborting event AND makes `git merge --ff-only` refuse, which
# is the failure this split exists to prevent.
[settings-churn-keys]
{churn_keys}

# --- SECOND DEPLOY TARGET: the rendered Codex skill ports ---------------------
# `gearbox deploy` renders skills/<name>/codex/ into the Codex skills directory
# as a path-scoped copy that never deletes anything it did not write. A hand
# edit to a rendered skill there is a hotfix exactly like one in the primary
# deploy target: `gearbox drift` names it and `gearbox deploy` refuses until
# `gearbox harvest` carries it back. Nothing else under the Codex directory is
# touched, or even read.
#
# Both ship real: the target is the standard Codex location, identical for
# every user, and the render pattern is repo-relative.
[codex-target]
{codex_target}

[codex-render]
{codex_render}

# --- mode-sensitive files: git does not preserve 0600, so deploy re-applies it --
# FICTIONAL PLACEHOLDERS — replace all of them before you deploy. The categories
# to think about on your own target: a tool/server config holding tokens, a
# machine-local settings override, and an auth/session cache. Files like these
# are untracked by design (see .gitignore's never-track set) and a missing one
# is skipped silently, which is exactly why `scripts/gearbox` hard-fails on a
# leftover REPLACE_ME_ rather than quietly chmod-ing nothing.
[mode-0600]
REPLACE_ME_service-config-holding-tokens.json
REPLACE_ME_machine-local-overrides.json
REPLACE_ME_auth-session-cache.json
"""


def _scrub_deploy_pathspec(text: str) -> str:
    sections: dict[str, list[str]] = {}
    current = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        line = re.split(r"\s+#", line, maxsplit=1)[0].strip()  # drop trailing comments
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1]
            sections.setdefault(current, [])
            continue
        if current is None:
            raise fatal(f"deploy.pathspec scrub: entry before any [section]: {line!r}")
        sections[current].append(line)

    if tuple(sections) != _DEPLOY_PATHSPEC_SECTIONS:
        raise fatal(
            f"deploy.pathspec scrub: sections are {tuple(sections)!r}, expected "
            f"{_DEPLOY_PATHSPEC_SECTIONS!r}. A section this transform has not classified "
            "would be published verbatim — decide deliberately whether it is a generic "
            "category (ship it) or a deployment map (template it), then update "
            "_scrub_deploy_pathspec."
        )
    return _DEPLOY_PATHSPEC_TEMPLATE.format(
        live_state="\n".join(sections["live-state"]),
        churn_keys="\n".join(sections["settings-churn-keys"]),
        codex_target="\n".join(sections["codex-target"]),
        codex_render="\n".join(sections["codex-render"]),
    )


# Keyed by path RELATIVE TO harness/ (i.e. same as the manifest's rel_path).
# Only STRUCTURAL transforms live here — every find/replace whose match string
# is itself a confidential value now lives in manifest["scrub_rules"].
TARGETED_TRANSFORMS: dict[str, "callable"] = {
    "CLAUDE.md": _scrub_claude_md,
    "settings.json": _scrub_settings_json,
    "scripts/prod-status-pinned.sh": _scrub_prod_status_pinned_sh,
    "scripts/deploy.pathspec": _scrub_deploy_pathspec,
}


def prune_settings_hooks(harness_dir: Path) -> list[str]:
    """Drop settings.json hook entries that invoke a script this export does not ship.

    s06 finding SC-07. The source deployment's settings.json wires SessionStart /
    PreToolUse hooks by path. Some of those scripts are private-tier and have no
    manifest verdict, so they are (correctly) never copied — but the WIRING was
    still shipping. That is two bugs in one line: a fresh install references a
    file that does not exist, and the hook's name/statusMessage discloses an
    internal system the export deliberately excludes.

    Run AFTER the copy, so "does this export actually contain the script" is a
    fact read off disk rather than a hardcoded list that drifts every time a
    hook is added or reclassified.
    """
    settings = harness_dir / "settings.json"
    if not settings.is_file():
        return []
    data = json.loads(settings.read_text(encoding="utf-8"))
    dropped: list[str] = []

    def script_is_exported(command: str) -> bool:
        m = re.search(r"~/\.claude/(hooks/[A-Za-z0-9_.\-]+)", command)
        if not m:
            return True  # not a hooks/ script reference — inline shell, leave it
        return (harness_dir / m.group(1)).is_file()

    for event, groups in list(data.get("hooks", {}).items()):
        for group in groups:
            kept = []
            for hook in group.get("hooks", []):
                if script_is_exported(hook.get("command", "")):
                    kept.append(hook)
                else:
                    dropped.append(f"{event}: {hook.get('command')}")
            group["hooks"] = kept
        data["hooks"][event] = [g for g in groups if g.get("hooks")]

    if dropped:
        settings.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return dropped


def scrub_tree(dest: Path, rules: list) -> None:
    """Apply the manifest's scrub_rules to every text file just copied.

    Belt-and-suspenders on top of the per-entry `transforms` prose: entries can
    miss a literal occurrence the manifest author didn't anticipate, and this
    pass is also what catches a name landing in a NEWLY synced file. Binary
    files are skipped here (decode failure -> left untouched); the scan layers
    below still cover them.
    """
    for path in dest.rglob("*"):
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, ValueError):
            continue
        new_text = apply_scrub_rules(text, rules, str(path.relative_to(dest)))
        if new_text != text:
            path.chmod(path.stat().st_mode | 0o200)
            path.write_text(new_text, encoding="utf-8")


# ---------------------------------------------------------------------------
# Copy (publish + scrub-then-publish entries only)
# ---------------------------------------------------------------------------

def _parse_rel_path(raw_path: str) -> str:
    # Manifest paths carry human-readable suffixes like " (symlink -> ...)",
    # " (other files)", or " (69 of 70 top-level .md files, all except
    # ingest.md)" — strip to the real filesystem path (the part before " (").
    return raw_path.split(" (")[0].strip()


def _parse_excluded_children(raw_path: str) -> list[str]:
    """Extract filenames named as an exception in a directory-bucket entry's
    prose, e.g. "commands/ (69 of 70 top-level .md files, all except
    ingest.md)" -> ["ingest.md"]. These are files this directory-level
    'publish' bucket must NOT actually include (they have their OWN
    private-never entry elsewhere in the manifest) — a naive whole-directory
    copytree would otherwise ship them anyway.
    """
    excluded = []
    for m in re.finditer(r"except\s+([A-Za-z0-9_.\-]+(?:/[A-Za-z0-9_.\-]+)*)", raw_path):
        excluded.append(m.group(1))
    return excluded


def copy_entries(manifest: dict, source_dir: Path, harness_dir: Path, scrub_rules: list) -> list[str]:
    entries = manifest["entries"]
    private_never_rel_paths = {
        _parse_rel_path(e["path"]) for e in entries if e.get("verdict") == "private-never"
    }

    # s06: the PROJECTED_SOURCES special-case (plugins/installed_plugins.json ->
    # a hand-authored recommended-plugins.md) is RETIRED. That file is runtime
    # state and is not tracked in the source repo, so the projection could only
    # ever fire by accident — e.g. someone re-pointing --source at the deploy
    # target — and when it did it published the operator's PRIVATE marketplace
    # plugins by name (s06 finding SC-08). A leak-capable path with no remaining
    # caller is not worth keeping armed.
    PROJECTED_SOURCES: set[str] = set()

    copied: list[str] = []
    for entry in entries:
        verdict = entry.get("verdict")
        if verdict not in ("publish", "scrub-then-publish"):
            continue  # private-never entries are never touched by this script
        raw_path = entry["path"]
        rel_path = _parse_rel_path(raw_path)
        if rel_path in PROJECTED_SOURCES:
            continue  # never copied verbatim; the set is empty since s06
        excluded_children = _parse_excluded_children(raw_path)
        src = (source_dir / rel_path).resolve()
        if not src.exists():
            raise fatal(f"manifest entry source path does not exist: {src} (entry: {raw_path})")
        dest = harness_dir / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            if dest.exists():
                shutil.rmtree(dest)
            # Never ship compiled bytecode (2026-08-15). A plain copytree
            # carried `__pycache__/*.pyc` into harness/, and those are not
            # source: they are machine-generated, they go stale against the
            # .py beside them, and their high-entropy bytes tripped two
            # `aws-access-token` findings in the secret scan — false
            # positives that would have failed the export, and whose
            # non-UTF-8 content also crashed the scanner's own log decode.
            shutil.copytree(
                src, dest, symlinks=False, dirs_exist_ok=True,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
            )
            for p in dest.rglob("*"):
                if p.is_file():
                    p.chmod(p.stat().st_mode | 0o200)  # ensure owner-writable regardless of source perms
            # Directory-bucket "all except X" entries: remove the excepted
            # file/dir from the copy immediately (belt) — the private-never
            # sweep below is the suspenders in case this misses a naming
            # variant.
            for child_name in excluded_children:
                child_path = dest / child_name
                if child_path.is_file():
                    child_path.unlink()
                elif child_path.is_dir():
                    shutil.rmtree(child_path)
        else:
            shutil.copy2(src, dest)
            dest.chmod(dest.stat().st_mode | 0o200)
        copied.append(rel_path)

    # Apply targeted, hand-authored transforms to every file this script knows
    # needs one — regardless of which coarse bucket (publish vs
    # scrub-then-publish) the manifest happened to file it under. The manifest
    # is the source of what to copy; TARGETED_TRANSFORMS is the authoritative
    # source of what to rewrite once copied.
    for rel_path, fn in TARGETED_TRANSFORMS.items():
        dest = harness_dir / rel_path
        if not dest.is_file():
            continue
        text = dest.read_text(encoding="utf-8")
        new_text = fn(text)
        if new_text != text:
            dest.chmod(dest.stat().st_mode | 0o200)
            dest.write_text(new_text, encoding="utf-8")

    # Fail-closed check: every scrub-then-publish manifest entry must be
    # covered by one of three known-adequate mechanisms — a structural
    # transform (TARGETED_TRANSFORMS), a path-scoped manifest scrub rule, or
    # the tree-wide manifest rules (scrub_tree, run by the caller right after
    # this function). GENERIC_SCRUB_SUFFICES lists the entries whose manifest
    # `transforms` are purely a personal-path/pointer rewrite mechanical
    # enough for the blanket pass; anything else uncovered is a structural gap
    # and must fail loudly, never ship unscrubbed.
    path_scoped = {scope for _rx, _repl, scope in scrub_rules if scope}
    GENERIC_SCRUB_SUFFICES = {
        "skills/plan-builder/scripts/test_schema_hardening.py",
        "skills/plan-execute/scripts/test_structural_gate.py",
        "skills/routing-update/SKILL.md",
        # s06: fixture paths embed a real project name; PATH_SCRUB_RULES'
        # tree-wide pass rewrites it, no targeted function needed.
        "scripts/test_gearbox_classify.py",
    } | PROJECTED_SOURCES
    unhandled = [
        _parse_rel_path(e["path"])
        for e in entries
        if e.get("verdict") == "scrub-then-publish"
        and _parse_rel_path(e["path"]) not in TARGETED_TRANSFORMS
        and _parse_rel_path(e["path"]) not in path_scoped
        and _parse_rel_path(e["path"]) not in GENERIC_SCRUB_SUFFICES
    ]
    if unhandled:
        raise fatal(
            "scrub-then-publish entries with no implemented transform (fail-closed, "
            f"never ship unscrubbed): {unhandled}. Add a manifest scrub_rules entry "
            "scoped to that path, or a structural function to TARGETED_TRANSFORMS."
        )

    # SAFETY NET (suspenders): hard-remove any dest path that corresponds to a
    # private-never manifest entry, in case a directory-bucket copy included
    # it despite the exclusion parse above (e.g. an unrecognized "except"
    # phrasing). This must never be the ONLY defence — the exclusion parse
    # above is the primary one — but a copy that ships a private-never file
    # is exactly the failure this whole script exists to prevent.
    for rel_path in private_never_rel_paths:
        victim = harness_dir / rel_path
        if victim.exists():
            if victim.is_dir():
                shutil.rmtree(victim)
            else:
                victim.unlink()
            print(f"REMOVED private-never path that leaked via a directory bucket: {rel_path}", file=sys.stderr)

    return copied


# ---------------------------------------------------------------------------
# Provenance stamp
# ---------------------------------------------------------------------------

def git_head(repo: Path) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True,
        )
        return out.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def git_dirty(repo: Path) -> bool:
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "status", "--porcelain"],
            capture_output=True, text=True, check=True,
        )
        return bool(out.stdout.strip())
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


PROVENANCE_LEDGER_NAME = "gearbox-export-provenance.jsonl"

SYNCED_FROM_REL = "harness/SYNCED-FROM"

# THE PUBLISHED PROVENANCE STAMP IS A CLOSED SET OF FIELDS.
# Enforced as a key allowlist rather than as a "no SHA-shaped token" regex: the
# thing being kept out is not one string, it is the whole class of private-tier
# facts about the source repo (its revision, its worktree state, its layout). An
# allowlist fails closed on a field nobody anticipated; a denylist only ever
# catches the one that was removed last time.
SYNCED_FROM_KEY_SHAPES = {
    "exported_at": re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$"),
    "pipeline_version": re.compile(r"^[0-9]+(?:\.[0-9]+)*$"),
}

SYNCED_FROM_HEADER = """\
# SYNCED-FROM — export provenance, written by scripts/sync-from-claude.py.
# Do not hand-edit; regenerated on every sync run.
#
# WHAT IS DELIBERATELY NOT HERE: the source revision this export was built from.
# The source repo is private, so its commit id is not resolvable by anyone
# reading this repo — it is not provenance you can act on, it is only a
# permanent token correlating this repo to a private history, republished on
# every sync. The source revision IS recorded, in an operator-side ledger held
# with the (private-tier) export manifest outside this repo, joined to this file
# by exported_at. See scripts/README.md.
#
# Fields here are a closed allowlist enforced by the pre-push scan
# (check_provenance_stamp in scripts/sync-from-claude.py): adding any other key
# to this file blocks the push.
"""


def write_provenance(
    harness_dir: Path, source_dir: Path, private_commit_arg: str | None, manifest_path: Path
) -> tuple[str, bool, Path]:
    """Stamp the public export date; record the private source revision privately.

    Returns (source_revision_recorded, is_authoritative_pushed_sha, ledger_path).
    The first two are for the operator's console output and never reach the repo.
    """
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if private_commit_arg:
        sha = private_commit_arg
        authoritative = True
        source_note = "source-repo (pushed) commit SHA, supplied via --private-commit"
    else:
        sha = git_head(source_dir) or "UNKNOWN"
        authoritative = False
        source_note = (
            "LOCAL source-repo git HEAD (no --private-commit given) — FALLBACK stamp; "
            "the 'no unpushed source changes' enforcement happens at push time, not here"
        )

    content = SYNCED_FROM_HEADER + f"exported_at: {now}\npipeline_version: {PIPELINE_VERSION}\n"
    (harness_dir / "SYNCED-FROM").write_text(content, encoding="utf-8")

    # The private half. Written beside the manifest because that is already the
    # agreed private-tier home for data this repo must not carry, and because it
    # keeps the mapping and the policy that produced it in one place.
    ledger = manifest_path.resolve().parent / PROVENANCE_LEDGER_NAME
    record = {
        "exported_at": now,                       # join key to harness/SYNCED-FROM
        "pipeline_version": PIPELINE_VERSION,
        "source_revision": sha,
        "source_revision_is_pushed": authoritative,
        "source_note": source_note,
        "source_worktree_dirty_at_export": git_dirty(source_dir),
        "source_dir": str(source_dir),
        "exported_into": str(harness_dir.parent),
    }
    try:
        with open(ledger, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    except OSError as e:
        # Fail closed. An export whose source revision was never recorded
        # anywhere cannot answer "which revision produced this?" later, and
        # silently degrading to "no provenance at all" is how the private half
        # ends up back in the public file next time.
        raise fatal(
            f"could not append to the provenance ledger {ledger}: {e}. The public stamp "
            "carries no source revision by design, so this ledger is the ONLY record of "
            "which source revision produced this export — refusing to export without it."
        )
    return sha, authoritative, ledger


def check_provenance_stamp(repo_dir: Path) -> list[str]:
    """Scan check: harness/SYNCED-FROM may carry ONLY the allowlisted fields.

    Runs inside the blocked-pattern layer, so the pre-push hook enforces it too.
    Comments are free text; every other non-blank line must be `key: value` with
    key on the allowlist and value matching that key's published shape.
    """
    stamp = repo_dir / SYNCED_FROM_REL
    if not stamp.is_file():
        return []  # nothing exported yet — not this check's business
    allowed = ", ".join(sorted(SYNCED_FROM_KEY_SHAPES))
    hits: list[str] = []
    for lineno, line in enumerate(
        stamp.read_text(encoding="utf-8", errors="replace").splitlines(), start=1
    ):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, sep, value = stripped.partition(":")
        key, value = key.strip(), value.strip()
        shape = SYNCED_FROM_KEY_SHAPES.get(key) if sep else None
        if shape is None:
            hits.append(
                f"{SYNCED_FROM_REL}:{lineno}: field {key!r} is not on the published-provenance "
                f"allowlist ({allowed}) — source-repo revisions and worktree state belong in "
                f"the operator-side ledger ({PROVENANCE_LEDGER_NAME}), never in this repo: "
                f"{stripped[:200]}"
            )
        elif not shape.match(value):
            hits.append(
                f"{SYNCED_FROM_REL}:{lineno}: {key} value {value!r} does not match its "
                f"published shape {shape.pattern!r}"
            )
    return hits


# ---------------------------------------------------------------------------
# Scan layer 1: secrets (gitleaks -> semgrep -> loud gap)
# ---------------------------------------------------------------------------

def run_secret_scan(repo_dir: Path, allow_missing: bool) -> tuple[bool, str, str, str, int]:
    """Returns (is_green, scanner_name, scanner_version, output_text, finding_count)."""
    gitleaks = shutil.which("gitleaks")
    if gitleaks:
        ver = subprocess.run([gitleaks, "version"], capture_output=True, text=True).stdout.strip()
        # NOTE: run with cwd=repo_dir and --source "." (relative), NOT an
        # absolute --source path. gitleaks's [rules.allowlist].paths regexes
        # (e.g. '^\.gitleaks\.toml$') match against the path AS REPORTED,
        # which is repo-relative only when --source is relative + cwd is the
        # repo root; an absolute --source makes every reported File: an
        # absolute path and silently defeats the allowlist (verified: the
        # committed .gitleaks.toml's own allowlist stopped matching its own
        # entries under an absolute --source).
        config_rel = ".gitleaks.toml"
        report_path = repo_dir / ".gitleaks-report.json"
        cmd = [
            gitleaks, "detect",
            "--source", ".",
            "--config", config_rel,
            "--no-git",  # scan working tree files as they sit, not just git-tracked diffs
            "--report-format", "json",
            "--report-path", str(report_path),
            "--redact",
            "-v",
        ]
        # errors="replace", not the default strict decode (2026-08-15): gitleaks
        # echoes a snippet of every match, so one non-UTF-8 byte anywhere in the
        # scanned tree raised UnicodeDecodeError HERE and took the whole run
        # down mid-scan — the loudest possible way to learn nothing. A scanner
        # that cannot report is worse than a scanner that reports mojibake.
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              errors="replace", cwd=str(repo_dir))
        output = proc.stdout + proc.stderr
        # gitleaks --no-git scans the filesystem tree directly, which WOULD include
        # dotfiles/hidden files (no shell-glob dotfile exclusion applied) — this is the
        # documented workaround for gitleaks#1927 (default git-log mode can miss
        # untracked/staged-only dotfiles); --no-git walks the raw filesystem instead.
        finding_count = 0
        if report_path.is_file():
            try:
                findings = json.loads(report_path.read_text(encoding="utf-8") or "[]")
                finding_count = len(findings) if isinstance(findings, list) else 0
            except (json.JSONDecodeError, ValueError):
                finding_count = -1  # report unparsable -> treat as unknown, never as 0/green
            finally:
                report_path.unlink(missing_ok=True)
        is_green = proc.returncode == 0 and finding_count == 0
        return is_green, "gitleaks", ver, output, finding_count
    semgrep = shutil.which("semgrep")
    if semgrep:
        report_path = repo_dir / ".semgrep-secrets-report.json"
        cmd = [semgrep, "--config", "p/secrets", "--json", "--output", str(report_path), "--quiet", str(repo_dir)]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        ver = subprocess.run([semgrep, "--version"], capture_output=True, text=True).stdout.strip()
        output = proc.stdout + proc.stderr
        finding_count = 0
        if report_path.is_file():
            try:
                data = json.loads(report_path.read_text(encoding="utf-8") or "{}")
                finding_count = len(data.get("results", []))
            except (json.JSONDecodeError, ValueError):
                finding_count = -1
            finally:
                report_path.unlink(missing_ok=True)
        is_green = proc.returncode == 0 and finding_count == 0
        return is_green, "semgrep-secrets(fallback)", ver, output, finding_count
    gap_msg = (
        "**** NO SECRET SCANNER INSTALLED **** neither gitleaks nor semgrep found on PATH.\n"
        "This is a LOUD, DOCUMENTED GAP, not a silent skip. Install gitleaks "
        "(brew install gitleaks) or semgrep (brew install semgrep, or pipx install semgrep).\n"
    )
    if allow_missing:
        return True, "NONE(allow-missing-scanner)", "n/a", gap_msg + "Proceeding anyway per --allow-missing-scanner (local iteration only).\n", 0
    return False, "NONE", "n/a", gap_msg, -1


# ---------------------------------------------------------------------------
# Scan layer 2: blocked-pattern grep, whole tree, git-ls-files-scoped
# ---------------------------------------------------------------------------

def tracked_and_untracked_files(repo_dir: Path) -> list[Path]:
    """Every file git would consider part of the tree: tracked + untracked-but-
    not-ignored. Deliberately whole-repo scope (adv-codex-scan-whole-repo) —
    not just harness/.
    """
    out = subprocess.run(
        ["git", "-C", str(repo_dir), "ls-files", "--cached", "--others", "--exclude-standard"],
        capture_output=True, text=True, check=True,
    )
    return [repo_dir / p for p in out.stdout.splitlines() if p.strip()]


# NO EXEMPTIONS. There used to be one — this very file was skipped by name, on
# the reasoning that a scrub rule legitimately contains the strings it matches.
# That reasoning was the defect: it made the single file holding every
# confidential literal the one file no gate could see (s06-fix finding 2). The
# literals now live in the external manifest instead, so the scan covers 100%
# of the tree and this set is deliberately gone. Do not reintroduce it.


def _decode_variants(raw: bytes) -> list[str]:
    """Every text rendering of a file the identifier scan must search.

    A blocked identifier only has to survive ONE encoding to get published, so
    a scan that looks at exactly one rendering is a scan with holes (s06-fix
    finding F). Returns the plain decode plus normalizations that fold known
    alternate spellings of the same identifier back onto the literal:
      * UTF-16 / latin-1 fallbacks for non-UTF-8 fixtures,
      * percent-decoding (`%2FUsers%2Fname`),
      * base64 decoding of any long base64-ish run,
      * the Claude project-key form `-Users-<user>-Dev-<Project>` -> slashes.
    """
    out: list[str] = []
    for enc in ("utf-8", "utf-16", "latin-1"):
        try:
            text = raw.decode(enc)
        except (UnicodeDecodeError, UnicodeError, ValueError):
            continue
        out.append(text)
        break  # first successful decode is the file's real text
    if not out:
        return []
    text = out[0]
    variants = [text]
    out = variants  # index 0 stays the file's real text, so line numbers are real
    out.append(urllib.parse.unquote(text))
    # Project-key form: Claude Code writes an absolute path as a dash-joined
    # directory name, which no slash-anchored pattern can match. Fold ONLY that
    # shape back to slashes — a blanket "-" -> "/" would corrupt ordinary
    # hyphenated words and manufacture false hits.
    out.append(re.sub(
        r"-Users-[A-Za-z0-9._-]+",
        lambda m: "/" + m.group(0).strip("-").replace("-", "/"),
        text,
    ))
    for chunk in re.findall(r"[A-Za-z0-9+/]{16,}={0,2}", text):
        try:
            decoded = base64.b64decode(chunk, validate=True).decode("utf-8", errors="ignore")
        except (ValueError, binascii.Error):
            continue
        if decoded.strip():
            out.append(decoded)
    # A normalization that changed nothing is not a second place to look — drop
    # it so one leak reports once, at its real line number.
    seen = set()
    return [v for v in out if not (v in seen or seen.add(v))]


def _compile_blocked(blocked_patterns: list[str]) -> list:
    compiled = []
    for pat in blocked_patterns:
        try:
            compiled.append((pat, re.compile(pat, re.IGNORECASE)))
        except re.error as e:
            raise fatal(f"manifest blocked_pattern is not a valid regex: {pat!r} ({e})")
    return compiled


def _match_all(label: str, raw: bytes, compiled: list, kind: str = "blocked_pattern") -> list[str]:
    found = []
    for variant_no, text in enumerate(_decode_variants(raw)):
        for lineno, line in enumerate(text.splitlines(), start=1):
            for pat, rx in compiled:
                if rx.search(line):
                    where = f"{label}:{lineno}" if variant_no == 0 else f"{label} (decoded form #{variant_no})"
                    found.append(f"{where}: matched {kind} {pat!r}: {line.strip()[:200]}")
    return found


# ---------------------------------------------------------------------------
# Repo-local commit-message policy — INDEPENDENT of the manifest.
#
# The surrounding environment's commit convention appends provenance trailers
# (a session URL and a co-author line) to every commit. On a private repo that
# is useful. On THIS repo it is a per-commit disclosure of a private session
# identifier and of which model wrote the change, published forever — it is why
# an earlier outgoing range had to be rebuilt. A convention that lives outside
# this repo cannot be relied on to make an exception for it, so the exception is
# enforced here, structurally, in both directions:
#
#   .githooks/commit-msg  refuses to CREATE such a commit.
#   .githooks/pre-push    (via this list, layer 2b) refuses to PUSH one, so a
#                         commit made with hooks disabled still cannot escape.
#
# These patterns are hardcoded, not manifest-driven: they are generic trailer
# key names, not confidential values, and the gate must work on any clone.
# The URL pattern is written escaped so this file does not itself contain the
# literal string an identifier sweep looks for.
# ---------------------------------------------------------------------------

FORBIDDEN_COMMIT_TRAILERS = [
    r"^\s*Claude-Session\s*:",
    r"^\s*Co-Authored-By\s*:",
    r"claude\.ai/code",
]
_FORBIDDEN_TRAILER_RX = [(p, re.compile(p, re.IGNORECASE)) for p in FORBIDDEN_COMMIT_TRAILERS]


# ---------------------------------------------------------------------------
# Scan layer 2b: THE OBJECTS ACTUALLY BEING PUSHED (s06-fix finding C)
#
# The working tree is not the thing a push publishes. A pre-push gate that
# scans `git ls-files` validates the CHECKOUT: an identifier in an EARLIER
# outgoing commit, in a commit MESSAGE, or in content since replaced by a clean
# uncommitted edit all sail through green. This scans the exact object set the
# push would create — every new commit's message and author/committer identity,
# and every blob reachable from the new commits but not from the remote.
# ---------------------------------------------------------------------------

def _git_out(repo_dir: Path, args: list[str]) -> str:
    return subprocess.run(
        ["git", "-C", str(repo_dir), *args], capture_output=True, text=True, check=True
    ).stdout


def parse_push_refs(stdin_text: str) -> list[tuple[str, str | None]]:
    """pre-push stdin is `<local_ref> <local_oid> <remote_ref> <remote_oid>`."""
    ranges = []
    for line in stdin_text.splitlines():
        parts = line.split()
        if len(parts) != 4:
            continue
        _local_ref, local_oid, _remote_ref, remote_oid = parts
        if set(local_oid) == {"0"}:
            continue  # branch deletion — publishes nothing
        ranges.append((local_oid, None if set(remote_oid) == {"0"} else remote_oid))
    return ranges


def run_outgoing_object_scan(
    repo_dir: Path, ranges: list, compiled: list, push_remote: str | None = None
) -> tuple[bool, str, int]:
    hits: list[str] = []
    scanned_commits = 0
    scanned_blobs = 0
    for local_oid, remote_oid in ranges:
        # A known remote tip bounds the range exactly. For a brand-new branch,
        # fall back to "not already on THE DESTINATION REMOTE" — scoped to that
        # remote by name, never a bare `--remotes`. A bare `--remotes` excludes
        # anything present on ANY remote, so a branch already fetched from some
        # other remote (a local mirror, a fork, a backup clone) is treated as
        # already-published and scanned as empty. That is exactly backwards: what
        # matters is whether THIS remote has it.
        if remote_oid:
            excl = ["--not", remote_oid]
        elif push_remote:
            excl = ["--not", f"--remotes={push_remote}"]
        else:
            # No destination known — scan the whole branch. Expensive and
            # deliberately so: unknown destination must not narrow the scan.
            excl = []

        # (a) commit messages + author/committer identity fields.
        # The record separator is emitted by GIT (`%x00`), never passed through
        # argv — an argv string cannot contain a NUL byte, and building the
        # separator on this side raises ValueError: embedded null byte.
        sep = "\x00"
        log = _git_out(repo_dir, [
            "log", "--format=%x00%H%n%an <%ae>%n%cn <%ce>%n%s%n%b", local_oid, *excl,
        ])
        for chunk in log.split(sep):
            if not chunk.strip():
                continue
            scanned_commits += 1
            sha = chunk.strip().splitlines()[0][:12]
            label = f"commit {sha} (message/identity)"
            raw_chunk = chunk.encode("utf-8")
            hits += _match_all(label, raw_chunk, compiled)
            # Repo-local trailer policy — manifest-independent, see
            # FORBIDDEN_COMMIT_TRAILERS. Commit messages only: the hook file and
            # the docs legitimately name these trailers, so blobs are exempt.
            hits += _match_all(label, raw_chunk, _FORBIDDEN_TRAILER_RX,
                               kind="forbidden commit trailer")

        # (b) every blob new in the range
        listing = _git_out(repo_dir, ["rev-list", "--objects", local_oid, *excl])
        oids = [ln.split(" ", 1)[0] for ln in listing.splitlines() if " " in ln]
        if not oids:
            continue
        types = subprocess.run(
            ["git", "-C", str(repo_dir), "cat-file", "--batch-check=%(objectname) %(objecttype)"],
            input="\n".join(oids) + "\n", capture_output=True, text=True,
        ).stdout
        paths = {ln.split(" ", 1)[0]: ln.split(" ", 1)[1] for ln in listing.splitlines() if " " in ln}
        for ln in types.splitlines():
            parts = ln.split()
            if len(parts) != 2 or parts[1] != "blob":
                continue
            oid = parts[0]
            raw = subprocess.run(
                ["git", "-C", str(repo_dir), "cat-file", "blob", oid], capture_output=True
            ).stdout
            scanned_blobs += 1
            hits += _match_all(f"outgoing blob {paths.get(oid, oid)}@{oid[:12]}", raw, compiled)

    hits = list(dict.fromkeys(hits))
    header = f"scanned {scanned_commits} outgoing commit(s), {scanned_blobs} new blob(s)"
    output = header + ("\n" + "\n".join(hits) if hits else " — no blocked-pattern hits")
    return (len(hits) == 0), output, len(hits)


def run_blocked_pattern_scan(repo_dir: Path, blocked_patterns: list[str]) -> tuple[bool, str, int]:
    """Manifest blocked patterns over the whole tree, PLUS the published-provenance
    shape check (check_provenance_stamp) — folded in here so it runs everywhere
    this layer runs, the pre-push hook included, with no second call site to forget.
    """
    files = tracked_and_untracked_files(repo_dir)
    compiled = _compile_blocked(blocked_patterns)
    hits: list[str] = []
    for f in files:
        if not f.is_file():
            continue
        rel = f.relative_to(repo_dir)
        try:
            raw = f.read_bytes()
        except OSError as e:
            # Fail closed: an unreadable TRACKED file is a gap in the gate, not
            # a file to wave through.
            hits.append(f"{rel}: UNREADABLE tracked file — cannot be cleared ({e})")
            continue
        hits += _match_all(str(rel), raw, compiled)
    hits += check_provenance_stamp(repo_dir)
    # Same identifier found in several decodings is one leak, not many.
    hits = list(dict.fromkeys(hits))
    output = (
        "\n".join(hits)
        if hits
        else "no blocked-pattern hits across whole Gearbox tree; published provenance stamp clean"
    )
    return (len(hits) == 0), output, len(hits)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manifest", required=True, help="path to the REAL manifest.json (must be OUTSIDE repo-dir)")
    ap.add_argument(
        "--source", "--claude-home", dest="source", default=None,
        help="REQUIRED for a sync: the PRIVATE SOURCE REPO worktree to export from, "
             "e.g. ~/path/to/your-private-harness. No default — one would have to name "
             "a real private layout. --claude-home is a deprecated alias kept for older "
             "runbooks; the deploy target ~/.claude is NOT the export source "
             "(see module docstring).",
    )
    ap.add_argument("--repo-dir", default=str(Path(__file__).resolve().parents[1]), help="Gearbox repo root (default: parent of scripts/)")
    ap.add_argument("--private-commit", default=None, help="pushed private-repo commit SHA for the SYNCED-FROM stamp")
    ap.add_argument("--allow-missing-scanner", action="store_true", help="don't hard-fail if neither gitleaks nor semgrep is installed (local iteration only)")
    ap.add_argument("--scan-only", action="store_true", help="skip copy/scrub/provenance; just run both scan layers over repo-dir")
    ap.add_argument(
        "--push-remote", default=None,
        help="name of the remote being pushed to (pre-push hook argv[1]). Used to scope "
             "the outgoing-range exclusion to THAT remote's tracking refs; without it a "
             "new branch is scanned in full rather than narrowed.",
    )
    ap.add_argument(
        "--scan-push-input", action="store_true",
        help="read pre-push ref tuples from stdin and ALSO scan the exact objects the "
             "push would create (new commits' messages + author/committer identity, and "
             "every blob new in the range). The working tree is not what a push publishes.",
    )
    ap.add_argument("--scan-status-out", default=None, help="path to write machine-checkable scan-status.json")
    ap.add_argument("--scan-report-out", default=None, help="path to write the human-readable scan-report.txt")
    args = ap.parse_args(argv)

    repo_dir = Path(args.repo_dir).resolve()
    source_dir = Path(args.source).expanduser().resolve() if args.source else Path(".")
    manifest_path = Path(args.manifest).expanduser()

    try:
        manifest = load_manifest(manifest_path, repo_dir)
    except SyncError as e:
        print(f"FATAL: {e}", file=sys.stderr)
        return 2

    copied: list[str] = []
    private_sha = None
    authoritative = False
    ledger_path: Path | None = None

    if not args.scan_only:
        if args.source is None:
            print("FATAL: --source is required for a sync (only --scan-only may omit it). "
                  "Point it at your private source-repo worktree, e.g. "
                  "--source ~/path/to/your-private-harness", file=sys.stderr)
            return 2
        if not source_dir.is_dir():
            print(f"FATAL: --source not found: {source_dir}", file=sys.stderr)
            return 2
        harness_dir = repo_dir / "harness"
        harness_dir.mkdir(parents=True, exist_ok=True)
        try:
            scrub_rules = compile_scrub_rules(manifest)
            copied = copy_entries(manifest, source_dir, harness_dir, scrub_rules)
        except SyncError as e:
            print(f"FATAL: {e}", file=sys.stderr)
            return 2
        for entry in prune_settings_hooks(harness_dir):
            print(f"DROPPED settings.json hook wiring for a non-exported script: {entry}", file=sys.stderr)
        scrub_tree(harness_dir, scrub_rules)
        try:
            private_sha, authoritative, ledger_path = write_provenance(
                harness_dir, source_dir, args.private_commit, manifest_path
            )
        except SyncError as e:
            print(f"FATAL: {e}", file=sys.stderr)
            return 2
        print(f"== synced {len(copied)} entries into {harness_dir} ==")
        # Console only — the SHA is deliberately absent from the exported tree.
        print(f"   source revision {private_sha} (pushed: {authoritative}) recorded in {ledger_path}")
        print(f"   harness/SYNCED-FROM stamps export date + pipeline_version {PIPELINE_VERSION} only")

    # --- Scan layer 1: secrets, whole Gearbox tree ---
    secrets_green, scanner, scanner_ver, secrets_output, secret_findings = run_secret_scan(repo_dir, args.allow_missing_scanner)

    # --- Scan layer 2: blocked-pattern grep, whole Gearbox tree ---
    patterns_green, patterns_output, hit_count = run_blocked_pattern_scan(repo_dir, manifest["blocked_patterns"])

    # --- Scan layer 2b: the exact objects an in-flight push would create ---
    outgoing_green, outgoing_output, outgoing_hits = True, "not requested (--scan-push-input off)", 0
    if args.scan_push_input:
        ranges = parse_push_refs(sys.stdin.read())
        if not ranges:
            outgoing_output = "no non-deleting refs on stdin — nothing outgoing to scan"
        else:
            outgoing_green, outgoing_output, outgoing_hits = run_outgoing_object_scan(
                repo_dir, ranges, _compile_blocked(manifest["blocked_patterns"]),
                push_remote=args.push_remote,
            )

    tree_hash = git_head(repo_dir) or "UNKNOWN"
    overall_green = secrets_green and patterns_green and outgoing_green

    report_lines = [
        "=== sync-from-claude.py scan report ===",
        f"repo_dir: {repo_dir}",
        f"scan_scope: WHOLE Gearbox working tree (git ls-files --cached --others --exclude-standard)",
        f"tree_commit_hash: {tree_hash}",
        "",
        f"--- Layer 1: secrets ({scanner} {scanner_ver}) ---",
        secrets_output.strip(),
        f"LAYER 1 STATUS: {'GREEN' if secrets_green else 'RED'}",
        "",
        "--- Layer 2: blocked-pattern grep + published-provenance shape ---",
        patterns_output.strip(),
        f"LAYER 2 STATUS: {'GREEN' if patterns_green else 'RED'} ({hit_count} hit(s))",
        "",
        "--- Layer 2b: outgoing git objects (commits, messages, identities, blobs) ---",
        outgoing_output.strip(),
        f"LAYER 2b STATUS: {'GREEN' if outgoing_green else 'RED'} ({outgoing_hits} hit(s))",
        "",
        f"OVERALL: {'GREEN' if overall_green else 'RED'}",
    ]
    report_text = "\n".join(report_lines) + "\n"
    print(report_text)

    if args.scan_report_out:
        Path(args.scan_report_out).write_text(report_text, encoding="utf-8")

    if args.scan_status_out:
        status = {
            "status": "green" if overall_green else "red",
            "blocked_pattern_hits": hit_count,
            "outgoing_object_hits": outgoing_hits,
            "outgoing_objects_scanned": args.scan_push_input,
            "secret_findings": secret_findings,
            "scanner": scanner,
            "scanner_version": scanner_ver,
            "scan_scope": "whole-gearbox-tree",
            "tree_or_commit_hash": tree_hash,
            "synced_entries": len(copied),
            # The source revision is NOT reported here: this file is a scan
            # artifact that gets copied into evidence directories and pasted
            # into reports. It records only WHERE the revision was recorded.
            "provenance_ledger": str(ledger_path) if ledger_path else None,
            "pipeline_version": PIPELINE_VERSION,
            "generated_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        Path(args.scan_status_out).write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")

    if not overall_green:
        print("FATAL: scan found hits — see report above. NEVER weaken the scan; fix the source "
              "(transforms/manifest) instead.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
