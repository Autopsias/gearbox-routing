#!/usr/bin/env python3
"""Count the transcript signals that can justify a Claude Code mod. Prints JSON.

usage: mod_signals.py [days] [--root DIR] [--min N] [--top N]

The script counts; it does not judge. Each list is one kind of evidence, and
SKILL.md ("Signals") says which mod shape, if any, each kind points at.
ponytail: substring match on known record shapes; a new Claude Code release can
rename a record, and the counts then drop to zero without an error. `records`
in the output shows what was seen, so a zero can be checked against it.
"""
import argparse
import collections
import glob
import json
import os
import re
import time

STOP_KINDS = (
    ("doesn't want to proceed", "owner_rejected"),
    ("auto mode classifier", "classifier_denied"),
    ("has been denied", "permission_denied"),
)
HOOK_TROUBLE = ("hook_cancelled", "hook_non_blocking_error", "hook_blocking_error")
HOOK_TEXT = ("hook_additional_context", "hook_system_message")


def norm(s, width=70):
    """One key for texts that differ only in a path, an id or a number."""
    s = re.sub(r"(?:~|\.)?(?:/[\w.@+-]+){2,}/?", "<path>", s.strip())
    s = re.sub(r"\b[0-9a-f]{8,}\b|\d+", "N", s)
    return re.sub(r"\s+", " ", s)[:width]


class Tally:
    def __init__(self):
        self.count = collections.Counter()
        self.sessions = collections.defaultdict(set)
        self.example = {}

    def add(self, key, session, example=None):
        self.count[key] += 1
        self.sessions[key].add(session)
        self.example.setdefault(key, (example or str(key))[:160])

    def rows(self, minimum, top):
        out = [
            {"key": k if isinstance(k, str) else " | ".join(k), "count": n,
             "sessions": len(self.sessions[k]), "example": self.example[k]}
            for k, n in self.count.most_common() if n >= minimum
        ]
        return out[:top]


def text_of(content):
    return content if isinstance(content, str) else json.dumps(content)


def on_attachment(a, t, path):
    kind = a.get("type") or ""
    t["records"][kind] += 1
    if kind in HOOK_TROUBLE:
        raw = a.get("command") or "?"  # often an inline shell line; name its script
        script = re.search(r"[\w.-]+\.(?:py|sh|mjs|js|ts)\b", raw)
        why = "timed_out" if a.get("timedOut") else kind
        t["hook_trouble"].add((a.get("hookName") or "?", script.group(0) if script else raw.split()[0], why), path)
    elif kind in HOOK_TEXT:
        c = a.get("content")
        for piece in c if isinstance(c, list) else [c]:
            if isinstance(piece, str) and piece.strip():
                t["hook_text"].add(norm(piece, 50), path, piece)


def on_owner_text(c, t, path):
    """A string message: a typed prompt, a typed shell line or a slash command. True for a prompt."""
    m = re.match(r"\s*<([a-z-]+)>", c)
    tag = m.group(1) if m else "plain"
    t["records"]["user:" + tag] += 1
    if tag == "bash-input":
        cmd = re.search(r"<bash-input>(.*?)</bash-input>", c, re.S)
        if cmd:
            t["typed_shell"].add(norm(cmd.group(1)), path, cmd.group(1))
    elif tag in ("command-name", "command-message"):
        name = re.search(r"<command-name>(/[\w:-]+)", c)
        if name:
            t["slash_commands"].add(name.group(1), path)
    elif tag == "plain" and len(c.strip()) <= 60:
        t["short_prompts"].add(norm(c.lower()), path, c.strip())
    return tag == "plain"


def on_blocks(blocks, t, path, stopped):
    for b in blocks:
        if not isinstance(b, dict):
            continue
        if b.get("type") == "text" and "[Request interrupted by user" in b.get("text", ""):
            t["stopped_calls"].add(("-", "interrupted"), path)
        elif b.get("type") == "tool_result" and b.get("is_error"):
            head = text_of(b.get("content"))[:400]
            kind = next((k for needle, k in STOP_KINDS if needle in head), None)
            if kind:
                stopped[b.get("tool_use_id")] = (kind, head)


def tool_names(lines, ids):
    """id -> tool name, read only for the rare file that has a stopped call."""
    names = {}
    for line in lines:
        if '"tool_use"' in line and any(i in line for i in ids if i):
            try:
                blocks = (json.loads(line).get("message") or {}).get("content") or []
            except ValueError:
                continue
            names.update({b.get("id"): b.get("name") for b in blocks
                          if isinstance(b, dict) and b.get("type") == "tool_use"})
    return names


def scan_file(path, t):
    stopped, human = {}, 0
    with open(path, errors="ignore") as fh:
        lines = fh.readlines()
    for line in lines:
        if '"type":"user"' not in line and '"type":"attachment"' not in line:
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get("isSidechain"):
            continue
        c = (d.get("message") or {}).get("content")
        if d.get("type") == "attachment":
            on_attachment(d.get("attachment") or {}, t, path)
        elif d.get("isMeta"):
            continue
        elif isinstance(c, str):
            human += on_owner_text(c, t, path)
        elif isinstance(c, list):
            on_blocks(c, t, path, stopped)
    if stopped:
        names = tool_names(lines, stopped)
        for i, (kind, head) in stopped.items():
            t["stopped_calls"].add((names.get(i) or "?", kind), path, head)
    return human


def scan(root, days, minimum=3, top=15):
    cut = time.time() - days * 86400
    t = {k: Tally() for k in ("typed_shell", "slash_commands", "short_prompts",
                              "stopped_calls", "hook_text", "hook_trouble")}
    t["records"] = collections.Counter()
    scanned = interactive = skipped_temp = 0
    projects = collections.Counter()
    for path in glob.glob(os.path.join(os.path.expanduser(root), "*", "*.jsonl")):
        if os.path.getmtime(path) < cut:
            continue
        project = os.path.basename(os.path.dirname(path))
        if project.startswith("-private-"):  # eval and temp-dir sessions, not the owner's work
            skipped_temp += 1
            continue
        scanned += 1
        if scan_file(path, t):
            interactive += 1
            projects[project] += 1
    out = {"window_days": days, "files_scanned": scanned, "files_skipped_temp": skipped_temp,
           "sessions_with_owner_prompt": interactive, "top_projects": projects.most_common(8),
           "records": dict(t.pop("records").most_common(40))}
    out.update({k: v.rows(minimum, top) for k, v in t.items()})
    return out


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("days", nargs="?", type=int, default=14)
    p.add_argument("--root", default="~/.claude/projects")
    p.add_argument("--min", type=int, default=3, help="smallest count that is reported")
    p.add_argument("--top", type=int, default=15, help="rows per list")
    a = p.parse_args()
    print(json.dumps(scan(a.root, a.days, a.min, a.top), indent=1))
