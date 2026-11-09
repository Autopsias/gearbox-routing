#!/usr/bin/env python3
"""Condense a Claude Code or Codex transcript to its meaning, for the /improve semantic preview.

Keeps owner prompts, agent prose, tool calls (one short line each), tool errors and
compaction markers; drops tool output. A digest over `cap` characters keeps its first
third and last two thirds. Used by hooks/improve-nudge.py (`finished` mode), which hands
the digest to a reader with references/improve/triage-prompt.md.

Two formats: Claude Code transcripts (`type` user/assistant rows) and Codex rollouts
(`session_meta` then `response_item` rows; Codex calls its format unstable, so a parse
miss yields a shorter digest, never an error). `origin` is the Claude `entrypoint` or the
Codex `originator`; the hook uses it to skip automated sessions.

CLI: improve_digest.py <transcript.jsonl> <out.txt> [cap]
"""

import json
import re
import sys
from typing import Any, NamedTuple

CAP = 160_000
IMPROVE_CMD = "<command-name>/improve</command-name>"
CODEX_IMPROVE = re.compile(r"\$improve-harness\b")
CODEX_CMD = re.compile(r'\bcmd\s*:\s*"((?:[^"\\]|\\.)*)"')
EXIT_CODE = re.compile(r'"exit_code\\?"\s*:\s*([1-9]\d*)')


class Digest(NamedTuple):
    text: str
    improve_ran: bool
    origin: str


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(x.get("text", "") for x in content if isinstance(x, dict) and x.get("type") in ("text", "input_text", "output_text"))
    return ""


def _claude_user(content, lines: list, state: dict) -> None:
    s = _text(content).strip()
    if IMPROVE_CMD in s[:300]:
        state["ran"] = True
    elif s.startswith("This session is being continued"):
        lines.append("[CONTEXT COMPACTED]")
    elif s and not s.startswith("<"):
        lines.append("OWNER: " + s[:1500])
    for x in content if isinstance(content, list) else []:
        if isinstance(x, dict) and x.get("type") == "tool_result" and x.get("is_error"):
            e = x.get("content")
            e = e if isinstance(e, str) else json.dumps(e)
            lines.append("TOOL ERROR: " + e[:300].replace("\n", " "))


def _claude(d: dict, lines: list, state: dict) -> None:
    if not state["origin"] and isinstance(d.get("entrypoint"), str):
        state["origin"] = d["entrypoint"]
    kind, msg = d.get("type"), d.get("message")
    content = msg.get("content") if isinstance(msg, dict) else None
    if kind == "user" and not d.get("isMeta"):
        _claude_user(content, lines, state)
    elif kind == "assistant" and isinstance(content, list):
        for x in content:
            if not isinstance(x, dict):
                continue
            if x.get("type") == "text" and x.get("text", "").strip():
                lines.append("AGENT: " + x["text"][:700])
            elif x.get("type") == "tool_use":
                i = x.get("input") if isinstance(x.get("input"), dict) else {}
                arg = i.get("command") or i.get("file_path") or i.get("description") or ""
                lines.append(f"TOOL {x.get('name')}: " + str(arg)[:160].replace("\n", " "))


def _codex(d: dict, lines: list, state: dict) -> None:
    p = d.get("payload")
    p = p if isinstance(p, dict) else {}
    if d.get("type") == "session_meta":
        state["origin"] = str(p.get("originator") or "")
        return
    if d.get("type") == "compacted" or p.get("type") in ("compacted", "context_compacted"):
        lines.append("[CONTEXT COMPACTED]")
        return
    t = p.get("type")
    if t == "message":
        s = _text(p.get("content")).strip()
        if p.get("role") == "user":
            if CODEX_IMPROVE.search(s):
                state["ran"] = True
            elif s and not s.startswith(("<", "# AGENTS.md")):
                lines.append("OWNER: " + s[:1500])
        elif p.get("role") == "assistant" and s:
            lines.append("AGENT: " + s[:700])
    elif t in ("custom_tool_call", "function_call"):
        raw = str(p.get("input") or p.get("arguments") or "")
        if "improve-harness/SKILL.md" in raw:
            state["ran"] = True
        m = CODEX_CMD.search(raw)
        lines.append(f"TOOL {p.get('name')}: " + (m.group(1) if m else raw)[:160].replace("\n", " "))
    elif t in ("custom_tool_call_output", "function_call_output"):
        out = json.dumps(p.get("output"))
        if EXIT_CODE.search(out):
            lines.append("TOOL ERROR: " + out[:300].replace("\\n", " "))


def condense(path: str, cap: int = CAP) -> Digest:
    lines: list = []
    state: dict[str, Any] = {"ran": False, "origin": ""}
    parse = None
    with open(path, errors="ignore") as f:
        for raw in f:
            try:
                d = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(d, dict):
                continue
            if parse is None:
                parse = _codex if "payload" in d or d.get("type") == "session_meta" else _claude
            parse(d, lines, state)
    s = "\n".join(lines)
    if len(s) > cap:
        s = s[: cap // 3] + "\n[... middle cut ...]\n" + s[-2 * cap // 3:]
    return Digest(s, state["ran"], state["origin"])


if __name__ == "__main__":
    digest = condense(sys.argv[1], int(sys.argv[3]) if len(sys.argv) > 3 else CAP)
    with open(sys.argv[2], "w") as out:
        out.write(digest.text)
    print(len(digest.text), digest.origin, sys.argv[2])
