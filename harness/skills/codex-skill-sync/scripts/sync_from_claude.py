#!/usr/bin/env python3
"""Mirror the canonical Claude skills, agents, commands and plugin skills into
``~/.agents/skills`` so Codex can load them.

Claude is canonical. This script is one-way: it never writes back to
``~/.claude``, and it never touches ``~/.codex/skills`` (that directory is a
Gearbox deploy target holding deliberately authored Codex ports — see
``~/.claude/scripts/deploy.pathspec``).

Run it whenever the Claude side changes::

    python3 <skill>/scripts/sync_from_claude.py            # report, write nothing
    python3 <skill>/scripts/sync_from_claude.py --apply    # write

It is idempotent: a second run with nothing changed reports zero writes. That
is load-bearing — a sync that always claims work makes real drift invisible,
so the generated files carry NO timestamp (git records when; the file says
what).

Four rules keep the mirror honest:

1. **Codex ports win over Claude.** A name that ``~/.codex/skills`` already
   owns is skipped — that port was written for Codex on purpose.
2. **Hand-adapted mirrors are never overwritten.** A skill listed in
   ``PROTECTED`` carries Codex work the Claude source does not have. The
   script reports its drift and moves on.
3. **Claude-isms are translated, not copied.** ``CLAUDE.md`` becomes
   ``AGENTS.md``, a ``Skill(skill='bmad-bmm-x')`` call becomes ``$x``, and
   Claude attribution is dropped. Anything this table does not cover would be
   a lie about the harness the skill is running on.
4. **Only generated mirrors are removed.** When a Claude source disappears,
   its destination is removed only if its ``SKILL.md`` carries this script's
   provenance marker. Manual and protected Codex skills are never candidates.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import shutil
import yaml
import sys

HOME = pathlib.Path.home()
CLAUDE = pathlib.Path(os.environ.get("CLAUDE_DIR", HOME / ".claude")).expanduser()
DEST = pathlib.Path(os.environ.get("GEARBOX_AGENT_SKILLS", HOME / ".agents" / "skills")).expanduser()
CODEX_PORTS = pathlib.Path(os.environ.get("GEARBOX_CODEX_SKILLS", HOME / ".codex" / "skills")).expanduser()
# Reference docs cited by command .md files. They live outside commands/ because
# Claude Code lists every .md under it as a slash command. Skills and agents keep
# theirs beside them (skills/<name>/references/, agents/references/).
COMMAND_REFS = CLAUDE / "references"

# Claude-only agents: they exist so /plan-execute can pin a model+effort pair.
# In Codex the effort rides `-c model_reasoning_effort=`, so a mirror of these
# would advertise a dispatch tier that cannot be dispatched.
# Codex has no project-over-user precedence: a user-level skill and a project
# skill of the same name are BOTH offered, and the selector picks blind. Rename
# the user-level mirror where the two do genuinely different jobs.
RENAME = {
    "improve": "improve-harness",   # this one audits the AI harness config; the
                                    # vault project's skill of the same name runs a
                                    # vault retrospective. Different jobs, one name.
}

# Every plan-execute dispatch tier is named tier-*. A hand-kept list of them drifted:
# it missed tier-opus-xhigh, tier-opus-max, tier-sonnet-low and tier-sonnet-max, and
# all four got mirrored. The prefix covers every tier, present and future.
SKIP_PREFIX = "tier-"

# Mirrors carrying Codex work the Claude source lacks. Overwriting these
# silently destroys it, so the script reports their drift instead. This list is
# a floor, not the whole guard: `is_safe_to_overwrite` below catches the rest.
PROTECTED = {
    # Codex EDITIONS: the mirror says something true of Codex that the Claude
    # source does not say. Overwriting replaces right content with wrong.
    "routing-update",             # names the substitute for the unreachable claude-api skill
    "routing-retro",              # reads ~/.codex/history.jsonl
    "myusage-self-assessment",    # mines Codex sessions, not Claude ones
    "improve-harness",            # reads ~/.codex/improve-learnings.md
    "ci-orchestrate",             # sources $HOME/.codex/scripts/shared-discovery.sh
    "commit-orchestrate",         # same
    "code-quality",               # runs $HOME/.codex/scripts/quality/*
    "usertestgates",              # runs $HOME/.codex/lib/testgates_discovery.py
    "test-orchestrate",           # dispatches Codex subagents by name
    "epic-dev-conductor",         # drives `$epic-dev`, the Codex invocation form
    "review",                     # ditto: invoked as `$review`, not a slash command
    "review-adversarial-general", # ditto
    "ship-tail",                  # ditto
    "flake-detective",            # documents its own Codex-plugin promotion path
    "nlm-skill",                  # manages per-tool skill installs, Codex among them
}

# Reviewed and ruled the other way: these DO diverge, but every divergent line
# proved to be text the Claude source has since rewritten — zero of it Codex
# specific — so the canon wins and the auto-guard is overridden. Add a name here
# only after reading its diff; that is the whole point of the guard.
CANON_WINS = {
    "coverage",     # numbered lists and "specialist agents" wording, rewritten upstream
    "epic-dev",     # Ralph-loop flags and STEP 1.5, added upstream after the import
}

SYNC_MARKER = re.compile(r"(?m)^<!-- codex-skill-sync source=[^\n]+ -->$")


def is_generated_mirror(text: str) -> bool:
    return bool(SYNC_MARKER.search(text))


def require_safe_destination(path: pathlib.Path, root: pathlib.Path) -> None:
    """Refuse a destination path redirected by a nested symlink."""
    root_abs = root.absolute()
    path_abs = path.absolute()
    if path_abs != root_abs and root_abs not in path_abs.parents:
        raise RuntimeError(f"destination escapes mirror root: {path}")

    cursor = path_abs
    while cursor != root_abs:
        if cursor.is_symlink():
            raise RuntimeError(f"destination contains a symlink: {cursor}")
        cursor = cursor.parent

    resolved_root = root_abs.resolve()
    resolved_path = path_abs.resolve(strict=False)
    if resolved_path != resolved_root and resolved_root not in resolved_path.parents:
        raise RuntimeError(f"destination resolves outside mirror root: {path}")


def _instruction_lines(text: str) -> list[str]:
    """The lines that carry meaning, minus this script's own wrapper."""
    tail = text.partition("## Imported Instructions")[2] or text.split("---", 2)[-1]
    return [line.strip() for line in tail.splitlines() if line.strip()]


def references_of(source: pathlib.Path) -> pathlib.Path:
    return COMMAND_REFS if source.parent == CLAUDE / "commands" else source.parent / "references"


def is_safe_to_overwrite(current: str, rendered: str, source: pathlib.Path) -> bool:
    """May the sync replace this mirror?

    Only when the incoming text accounts for every non-blank line the mirror
    already has. A line present here and absent there is either a deliberate
    Codex adaptation or content the Claude source dropped, and the script cannot
    tell which — so it refuses and reports. Files this script wrote are exempt:
    it owns those, and regenerating them is the point.

    The corpus includes the source's REFERENCE FILES, because Claude skills
    routinely move a section out of SKILL.md into ``references/`` as they grow.
    Judging on SKILL.md alone reported a large loss for one skill when every
    line had simply moved to a file under ``references/``.
    """
    if is_generated_mirror(current):
        return True
    have = set(_instruction_lines(rendered))
    refs = references_of(source)
    if refs.is_dir():
        for f in refs.rglob("*.md"):
            have.update(ln.strip() for ln in f.read_text(errors="ignore").splitlines() if ln.strip())
    return all(line in have for line in _instruction_lines(current))

TRANSLATIONS = [
    (re.compile(r"\bRead CLAUDE\.md\b"), "Read AGENTS.md (fallback: CLAUDE.md)"),
    (re.compile(r"\bClaude Code session\b"), "Codex session"),
    (re.compile(r"\bClaude session\b"), "Codex session"),
    (re.compile(r"Generated with \[?Claude Code\]?(\(https://[^)]*\))?"), "Generated with Codex"),
    (re.compile(r"\bClaude subagent\b"), "Codex subagent"),
    (re.compile(r"\bDispatch Claude agent\b"), "Dispatch Codex subagent"),
    (re.compile(r"\bClaude-native Task tool\b"), "Codex subagent"),
    (re.compile(r"Co-Authored-By: Claude[^\n]*\n?"), ""),
    (re.compile(r"Skill\(skill='bmad-(?:bmm|tea)-([\w-]+)'\)"), r"$\1"),
    (re.compile(r"Skill\(skill='([\w-]+)'\)"), r"$\1"),
]
REF_PATH = re.compile(r"~/\.claude/(?:(?:agents|commands|skills/[\w-]+)/)?references/")
BOILERPLATE = re.compile(r"^Imported Claude (?:agent|command|skill) /?[\w:-]+\.\s*", re.I)

ADAPTATION = """## Codex Adaptation

- Mirrored from `{source}` by the codex-skill-sync skill. Claude is
  canonical: edit the source, then re-run the sync. Edits made here are lost.
- Invoke it by natural language, for example `Use {name}`, with whatever flags the
  request needs. Treat `$ARGUMENTS` as the user-supplied text.
- Ignore Claude-only frontmatter (`tools`, `allowed-tools`, `model`, `color`); follow
  Codex tool and repository instructions instead.
- Where the text delegates through Claude task/subagent primitives, use Codex subagents
  only when the user explicitly asks for delegated or parallel work; otherwise do it here.
- Where it says `AskUserQuestion`, ask a short plain-text question, and only when no safe
  assumption exists.
- Load anything under `references/` only when the workflow points at it.

## Imported Instructions
"""


def translate(text: str) -> str:
    for pattern, replacement in TRANSLATIONS:
        text = pattern.sub(replacement, text)
    return text


def split_front_matter(text: str) -> tuple[dict, str]:
    """Return (frontmatter-as-text-lines, body). Deliberately not YAML-parsed:
    several sources carry values PyYAML rejects, and the body is what matters."""
    if not text.startswith("---"):
        return {}, text
    _, fm, body = text.split("---", 2)
    out: dict[str, str] = {}
    key, buf = None, []
    for line in fm.split("\n"):
        m = re.match(r"^([\w-]+):\s*(.*)$", line)
        if m and not line.startswith(" "):
            if key:
                out[key] = "\n".join(buf).strip()
            key, buf = m.group(1), [m.group(2)]
        elif key:
            buf.append(line.strip())
    if key:
        out[key] = "\n".join(buf).strip()
    return out, body


def clean_description(raw: str) -> str:
    d = raw.strip().lstrip("|->").strip()
    d = BOILERPLATE.sub("", d).strip()
    d = re.sub(r"\s*\n\s*", " ", d).strip()
    if len(d) > 1 and d[0] == d[-1] and d[0] in "\"'":
        d = d[1:-1].strip()          # source quoted the whole value; the quotes are not content
    return d


def short_description(desc: str) -> str:
    first = re.split(r"(?<=[.!?])\s", desc, maxsplit=1)[0].strip().rstrip(".").strip()
    if len(first) > 60:
        first = first[:60].rsplit(" ", 1)[0].rstrip(",;:-") + "…"
    return first


PLUGIN_VERSION = re.compile(r"(/plugins/cache/[^/]+/[^/]+/)[^/]+(/skills/)")


def source_label(source: pathlib.Path) -> str:
    """How the source is NAMED in the generated file.

    A plugin's install path carries its version, so every plugin upgrade would
    rewrite every mirrored skill of that plugin with identical content. The
    version is collapsed to `<version>` to keep "0 changed" meaning "nothing
    drifted" rather than "no plugin was updated lately".
    """
    return PLUGIN_VERSION.sub(r"\1<version>\2", str(source)).replace(str(HOME), "~")


def render(name: str, source: pathlib.Path, desc: str, body: str) -> str:
    # Emit the frontmatter with PyYAML rather than by hand. Descriptions carry
    # colons, quotes, ">" and apostrophes; hand-rolled quoting got many of them
    # wrong in one pass, and an unparseable skill is an absent skill.
    front = yaml.safe_dump(
        {"name": name,
         "description": desc,
         "metadata": {"short-description": short_description(desc)}},
        sort_keys=False, allow_unicode=True, width=96, default_flow_style=False,
    )
    return (
        "---\n"
        f"{front}"
        "---\n\n"
        f"# {name.replace('-', ' ').title()}\n\n"
        f"<!-- codex-skill-sync source={source_label(source)} -->\n\n"
        + ADAPTATION.format(source=source_label(source), name=name)
        + "\n" + translate(REF_PATH.sub("references/", body)).strip() + "\n"
    )


def collect() -> list[tuple[str, pathlib.Path]]:
    """(destination name, source file) for everything Claude considers canonical."""
    items: dict[str, pathlib.Path] = {}
    for path in sorted((CLAUDE / "skills").glob("*/SKILL.md")):
        items.setdefault(path.parent.name, path)
    for path in sorted((CLAUDE / "agents").glob("*.md")):
        items.setdefault(path.stem, path)
    for path in sorted((CLAUDE / "commands").glob("*.md")):
        # `bmad-bmm-create-story` has always been mirrored as `create-story`;
        # keeping that shape avoids renaming skills the user already invokes.
        items.setdefault(re.sub(r"^bmad-(?:agent-)?(?:bmm-|tea-)?", "", path.stem), path)

    registry = CLAUDE / "plugins" / "installed_plugins.json"
    settings = CLAUDE / "settings.json"
    enabled = {}
    if settings.exists():
        try:
            enabled = json.loads(settings.read_text()).get("enabledPlugins", {})
        except json.JSONDecodeError:
            pass
    if registry.exists():
        for key, entries in json.loads(registry.read_text())["plugins"].items():
            if not enabled.get(key):          # only plugins actually switched on
                continue
            for path in sorted(pathlib.Path(entries[0]["installPath"]).glob("skills/*/SKILL.md")):
                items.setdefault(path.parent.name, path)
    return sorted(items.items())


def collect_support_files(
    source: pathlib.Path, target: pathlib.Path, rendered: str
) -> list[tuple[pathlib.Path, pathlib.Path]]:
    """Return validated source/destination pairs before any mirror write."""
    if source.name == "SKILL.md":
        trees = [
            (d, target.parent / d.name)
            for d in source.parent.iterdir()
            if d.is_dir() and d.name in {"references", "assets", "scripts", "agents"}
        ]
    else:
        refs = sorted({m.group(1) for m in re.finditer(r"references/([\w.-]+)/", rendered)})
        trees = [(references_of(source) / ref, target.parent / "references" / ref) for ref in refs]

    files = []
    for src_dir, dst_dir in trees:
        if not src_dir.is_dir():
            continue
        for src in src_dir.rglob("*"):
            if not src.is_file():
                continue
            dest = dst_dir / src.relative_to(src_dir)
            require_safe_destination(dest, DEST)
            files.append((src, dest))
    return files


def stale_mirrors(mirrored_names: set[str], apply: bool) -> list[str]:
    """Remove only provenance-marked mirrors that no source should now own."""
    removed = []
    if not DEST.is_dir():
        return removed
    for skill_dir in sorted(DEST.iterdir()):
        if (skill_dir.name in mirrored_names or skill_dir.name in PROTECTED
                or skill_dir.is_symlink() or not skill_dir.is_dir()):
            continue
        skill_file = skill_dir / "SKILL.md"
        if skill_file.is_symlink() or not skill_file.is_file():
            continue
        if not is_generated_mirror(skill_file.read_text(errors="ignore")):
            continue
        removed.append(skill_dir.name)
        if apply:
            shutil.rmtree(skill_dir)
    return removed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="write; otherwise report only")
    args = ap.parse_args()

    ports = {d.name for d in CODEX_PORTS.iterdir() if (d / "SKILL.md").exists()} if CODEX_PORTS.exists() else set()
    written, skipped, protected, drifted, removed, refs = [], [], [], [], [], 0
    mirrored_names = set()

    for name, source in collect():
        name = RENAME.get(name, name)
        if name.startswith(SKIP_PREFIX):
            skipped.append((name, "Claude-only dispatch tier"))
            continue
        if name in ports:
            skipped.append((name, "a Gearbox Codex port owns this name"))
            continue
        # Some skills are installed HERE and symlinked INTO ~/.claude, so this
        # tree is their canon and the arrow runs the other way. Writing one
        # would edit the Claude side through the link.
        link = CLAUDE / "skills" / name
        if link.is_symlink() and link.resolve() == (DEST / name).resolve():
            mirrored_names.add(name)  # canonical destination; never stale-delete it
            skipped.append((name, "canonical here; ~/.claude symlinks to it"))
            continue

        mirrored_names.add(name)

        front, body = split_front_matter(source.read_text())
        desc = clean_description(front.get("description", "")) or name.replace("-", " ")
        new = render(name, source, desc, body)
        target = DEST / name / "SKILL.md"
        require_safe_destination(target, DEST)

        # Validate every supporting destination before the first write, so one
        # nested symlink cannot leave a half-applied skill.
        support_files = collect_support_files(source, target, new)

        if not target.exists():
            written.append(name)
            if args.apply:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(new)
        elif target.read_text() == new:
            pass
        elif name in PROTECTED or (name not in CANON_WINS
                                   and not is_safe_to_overwrite(target.read_text(), new, source)):
            protected.append(name)
            continue                      # do not touch its references either
        else:
            drifted.append(name)
            if args.apply:
                target.write_text(new)

        # Supporting files. A skill DIRECTORY bundles its own references/,
        # assets/ and scripts/, so mirror those wholesale; a bare agent or
        # command .md only ever cites references/<its-own-name>/.
        for f, out in support_files:
            if out.exists() and out.read_bytes() == f.read_bytes():
                continue
            refs += 1
            if args.apply:
                out.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, out)

    # Retire mirrors whose canonical source disappeared, became Claude-only, or
    # is now owned by a Gearbox Codex port. The marker is the authority: a
    # directory without it is user-owned, even when its name looks familiar.
    # PROTECTED stays forever unless a human changes that verdict.
    removed = stale_mirrors(mirrored_names, args.apply)

    mode = "APPLIED" if args.apply else "DRY RUN — nothing written"
    print(f"{mode}\n")
    print(f"new skills:        {len(written)}")
    if written:
        print("   " + ", ".join(written))
    print(f"updated skills:    {len(drifted)}")
    if drifted:
        print("   " + ", ".join(drifted))
    print(f"reference files:   {refs}")
    print(f"removed skills:    {len(removed)}")
    if removed:
        print("   " + ", ".join(removed))
    print(f"skipped:           {len(skipped)}")
    for name, why in skipped:
        print(f"   {name:26} {why}")
    print(f"protected (hand-adapted, review by hand): {len(protected)}")
    if protected:
        print("   " + ", ".join(sorted(protected)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
