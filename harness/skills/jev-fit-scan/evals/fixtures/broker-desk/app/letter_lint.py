"""House-style checks on outbound letters. Plain rules; the letters team has no complaint on record."""
import re

RULES = [("passive", re.compile(r"\b(was|were) \w+ed\b")), ("hedge", re.compile(r"\b(perhaps|maybe)\b"))]


def lint(paragraphs):
    return [(i, name) for i, p in enumerate(paragraphs) for name, rx in RULES if rx.search(p)]
