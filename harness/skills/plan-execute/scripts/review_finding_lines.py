"""The plain-text findings in a reviewer answer, one per match of the gate's pattern.

`classify` counts matches of `_FINDING_LINE` over the whole answer, and that
pattern's whitespace part crosses a line break, so `x.py:2` with `- text` on the next line
is one finding there. Reading the answer line by line found none, and a blocking
rule then passed it (s07, gap 1). Reading the same matches keeps both counts equal.
"""
from __future__ import annotations

import json
import re


def finding_texts(text, pattern):
    """Each match of `pattern` in `text`, through the end of the line it ends on,
    with a wrapped finding's line break turned into a space."""
    out = []
    for m in pattern.finditer(text):
        end = text.find("\n", m.end())
        out.append(text[m.start():None if end < 0 else end].strip().replace("\n", " "))
    return out


def _line_finding(text):
    m = re.match(r"\s*(\S+\.\w+):(\d+)", text)
    return {"file": m[1] if m else "", "line": int(m[2]) if m else None,
            "severity": None, "summary": text.strip(), "rule": None}


def untagged(result, pattern, json_fence):
    """The findings `classify` counts that carry no rule tag (a JSON string, a plain or wrapped
    `file:line - text` line) as dicts with `"rule": None`: they block and stay open."""
    s = (result or "").strip()
    for raw in reversed(json_fence.findall(s) or ([s] if s.startswith("[") else [])):
        try:
            parsed = json.loads(raw)
        except ValueError:
            continue
        if isinstance(parsed, list):
            return [_line_finding(str(x)) for x in parsed if not isinstance(x, dict)]
    return [_line_finding(t) for t in finding_texts(s, pattern)]
