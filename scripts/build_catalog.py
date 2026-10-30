#!/usr/bin/env python3
"""Generate docs/CATALOG.md: every skill, command, subagent and hook in harness/.

    python3 scripts/build_catalog.py            write docs/CATALOG.md
    python3 scripts/build_catalog.py --check    exit 1 if docs/CATALOG.md is stale

harness/ is re-exported from a private source, so a hand-written list of its
parts goes stale at the next export. This page is built from the files
themselves; scripts/test_docs.py fails CI when it is out of date.
Standard library only (CI installs nothing but pytest).
"""
import glob
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "harness"
OUT = ROOT / "docs" / "CATALOG.md"
CARDS = sorted((ROOT / "docs" / "modules").glob("*.md"))
MAX = 170


def frontmatter(path):
    """Return {key: value} for the simple keys of a YAML frontmatter block."""
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    out, key, block = {}, None, []
    for line in lines[1:]:
        if line.strip() == "---":
            break
        m = re.match(r"([A-Za-z_-]+):\s*(.*)$", line)
        if m and not line.startswith((" ", "\t")):
            if key and block:
                out[key] = " ".join(block)
            key, val = m.group(1), m.group(2).strip()
            block = []
            if val in ("|", ">", "|-", ">-", "|+", ">+"):
                continue  # block scalar: collect the indented lines below
            out[key], key = val.strip("'\""), None
        elif key:
            block.append(line.strip())
    if key and block:
        out[key] = " ".join(block)
    return out


def summary(text):
    """First sentence of a description, cut to MAX characters, safe in a table."""
    text = re.sub(r"\s+", " ", text or "").strip()
    m = re.match(r"(.+?[.!?])(\s|$)", text)
    text = m.group(1) if m else text
    if len(text) > MAX:
        text = text[:MAX].rsplit(" ", 1)[0] + " …"
    return text


def first_doc_line(path):
    """First paragraph of a hook's module docstring or leading comment block."""
    text = path.read_text(encoding="utf-8")
    m = re.search(r'(?:"""|\'\'\')\s*(.+?)(?:\n\s*\n|"""|\'\'\')', text, re.S)
    if m:
        para = m.group(1)
    else:
        lines = [ln.lstrip("# ").strip() for ln in text.splitlines()[1:12] if ln.startswith("#")]
        para = " ".join(lines[:3])
    # drop a "name — Event(matcher):" lead-in; the table already shows both
    para = re.sub(r"^[\w.-]+\s+—\s+\w+(\([^)]*\))?( hook)?:\s*", "", " ".join(para.split()))
    para = re.sub(r"^\w+ hook:\s*", "", para)
    return para[:1].upper() + para[1:]


def _card_globs(card):
    """harness/ paths a card names, braces and globs expanded (as scripts/test_docs.py does)."""
    found = set()
    for token in re.findall(r"harness/[A-Za-z0-9_.\-/{},*]*", card.read_text(encoding="utf-8")):
        token = token.rstrip(".,")
        stack = [token]
        while stack:
            tok = stack.pop()
            m = re.search(r"\{([^{}]*)\}", tok)
            if m:
                stack += [tok[:m.start()] + alt + tok[m.end():] for alt in m.group(1).split(",")]
            else:
                found |= {str(Path(g).relative_to(ROOT)) for g in glob.glob(str(ROOT / tok.rstrip("/")))}
    return found


def card_for(rel, stem):
    """The module card that names this harness path (or, failing that, its stem)."""
    for card in CARDS:
        if rel in _card_globs(card):
            return card
    for card in CARDS:
        if re.search(rf"(?<![\w-]){re.escape(stem)}(?![\w-])", card.read_text(encoding="utf-8")):
            return card
    return None


def link(card):
    if not card:
        return "—"
    title = card.read_text(encoding="utf-8").splitlines()[0].lstrip("# ").split(" (")[0]
    return f"[{title}](modules/{card.name})"


def rows_for(units, start):
    rows = []
    for path, name in units:
        rel = str(path.relative_to(ROOT))
        meta = frontmatter(path if path.is_file() else path / "SKILL.md")
        rows.append((name, start(meta, name), summary(meta.get("description", "")), link(card_for(rel, name))))
    return rows


def hook_rows():
    settings = json.loads((HARNESS / "settings.json").read_text(encoding="utf-8"))
    seen = {}
    for event, groups in settings.get("hooks", {}).items():
        for group in groups:
            for hook in group.get("hooks", []):
                m = re.search(r"hooks/([\w.-]+)", hook.get("command", ""))
                if not m:
                    continue
                when = event + (f" ({group['matcher']})" if group.get("matcher") not in (None, "", "*") else "")
                seen.setdefault(m.group(1), []).append(when)
    rows = []
    for name, events in sorted(seen.items()):
        path = HARNESS / "hooks" / name
        desc = summary(first_doc_line(path)) if path.exists() else ""
        rows.append((name, ", ".join(dict.fromkeys(events)), desc,
                     link(card_for(f"harness/hooks/{name}", Path(name).stem))))
    return rows


def table(head, rows):
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    cell = lambda i, v: f"`{v}`" if i == 0 else re.sub(r"(?<!\\)\|", r"\\|", v)
    out += ["| " + " | ".join(cell(i, v) for i, v in enumerate(r)) + " |" for r in rows]
    return "\n".join(out)


def render():
    skills = sorted((p, p.name) for p in (HARNESS / "skills").iterdir()
                    if (p / "SKILL.md").exists())
    commands = sorted((p, p.stem) for p in (HARNESS / "commands").glob("*.md"))
    agents = sorted((p, p.stem) for p in (HARNESS / "agents").glob("*.md"))

    def skill_start(meta, name):
        if meta.get("user-invocable") == "false":
            return "Claude starts it"
        if meta.get("disable-model-invocation") == "true":
            return f"`/{name}` only"
        return f"`/{name}`, or Claude starts it"

    s, c, a, h = (rows_for(skills, skill_start),
                  rows_for(commands, lambda m, n: f"`/{n}`"),
                  rows_for(agents, lambda m, n: "A command, a skill or Claude dispatches it"),
                  hook_rows())
    return f"""<!-- Generated by scripts/build_catalog.py from harness/. Do not edit by hand. -->
# Catalogue

Everything in `harness/`, one line each. The line is the first sentence of the
part's own description. To install a part, open its module card: the card
lists every file the part needs, and how to check and remove it.

**How you start each kind of part:**

- **Skill** — type `/name`, or Claude starts it when your request matches its
  description.
- **Command** — type `/name`.
- **Subagent** — you do not start it. A command, a skill or Claude sends work
  to it.
- **Hook** — Claude Code runs it on the event shown. It does nothing until
  you wire it in your own `~/.claude/settings.json`, as its card shows.

Counts: {len(s)} skills · {len(c)} commands · {len(a)} subagents · {len(h)} hooks.

## Skills

{table(("Skill", "How you start it", "What it does", "Module card"), s)}

## Commands

{table(("Command", "How you start it", "What it does", "Module card"), c)}

## Subagents

{table(("Subagent", "How it starts", "What it does", "Module card"), a)}

## Hooks

{table(("Hook", "Runs on", "What it does", "Module card"), h)}
"""


def main():
    text = render()
    if "--check" in sys.argv:
        if not OUT.exists() or OUT.read_text(encoding="utf-8") != text:
            print("docs/CATALOG.md is stale: run python3 scripts/build_catalog.py", file=sys.stderr)
            return 1
        return 0
    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
