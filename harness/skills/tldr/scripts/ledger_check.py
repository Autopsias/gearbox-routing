#!/usr/bin/env python3
"""Consistency gate for tldr outputs.

    ledger_check.py LEDGER.md [--ask "text"] OUTPUT...

Every number in an output must appear in the ledger (or in the confirmed
ask, which the user owns); the ask must appear verbatim (case and whitespace
folded) in every output. Exit 1 on any
finding, 0 when clean. Standard library only.

What counts as a number: a digit run of two or more digits, or any digit run
followed by %, a currency sign/code or a unit word. Bare single digits ("3
options", "step 2") are structure, not claims, and are ignored.
Spelled-out numbers ("forty percent") are not checked.

.html  -> text content only (script, style, tags and attributes stripped),
          so viewBox and width values never count.
.json  -> every string value, recursively (video-creator script.json).
others -> read as text.
"""
import html as _html
import json
import re
import sys
from pathlib import Path

NUM = re.compile(
    r"(?<![\w.])"
    r"(?:[€$£]\s?)?"
    r"(?:(?P<thousands>\d{1,3}(?:[ ,.\u202f]\d{3})+)|(?P<plain>\d+(?:[.,]\d+)?))"
    r"\s?(?:%|percent|pp|bn|m|k|M|B|K|€|EUR|USD|GBP|\$|£|MW|GW|kWh|MWh|GWh|t|kt|Mt|ha|km|h|days?|weeks?|months?|years?|FTE|seats?)?"
    r"(?!\w|\.\d)"
)
TRIVIAL = re.compile(r"^\d$")


def html_text(s: str) -> str:
    s = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", s)
    s = re.sub(r"(?s)<[^>]+>", " ", s)  # drops attributes with the tag
    return _html.unescape(s)


def json_text(s: str) -> str:
    out = []

    def walk(v):
        if isinstance(v, str):
            out.append(v)
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)

    walk(json.loads(s))
    return "\n".join(out)


def load(path: Path) -> str:
    raw = path.read_text(encoding="utf-8", errors="replace")
    if path.suffix.lower() in {".html", ".htm", ".svg"}:
        return html_text(raw)
    if path.suffix.lower() == ".json":
        return json_text(raw)
    return raw


def norm_num(m: "re.Match[str]") -> str:
    if m.group("thousands"):
        return re.sub(r"[ ,.\u202f]", "", m.group("thousands"))
    return m.group("plain").replace(",", ".")  # 41,2 and 41.2 are one number


def numbers(text: str) -> set[str]:
    found = set()
    for m in NUM.finditer(text):
        core = norm_num(m)
        if TRIVIAL.match(core) and m.group(0).strip() == core:
            continue  # bare single digit
        found.add(core)
    return found


def fold(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().casefold()


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[0] in {"-h", "--help"}:
        print(__doc__)
        return 2
    ledger = Path(argv[0])
    ask = None
    outputs = []
    i = 1
    while i < len(argv):
        if argv[i] == "--ask":
            ask = argv[i + 1]
            i += 2
        else:
            outputs.append(Path(argv[i]))
            i += 1
    if not outputs:
        print("ledger_check: no outputs given")
        return 2

    ledger_text = load(ledger)
    ledger_nums = numbers(ledger_text)
    if ask is not None:
        ledger_nums |= numbers(ask)  # the user confirmed the ask; its date is theirs
    findings = 0
    for out in outputs:
        if not out.exists():
            print(f"MISSING  {out}")
            findings += 1
            continue
        text = load(out)
        unsupported = sorted(numbers(text) - ledger_nums, key=lambda s: (len(s), s))
        for n in unsupported:
            print(f"NUMBER   {out}: {n} is not in the ledger")
            findings += 1
        if ask is not None and fold(ask) not in fold(text):
            print(f"ASK      {out}: the confirmed ask does not appear verbatim")
            findings += 1
    print(f"{findings} finding(s) across {len(outputs)} output(s); ledger holds {len(ledger_nums)} numbers")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
