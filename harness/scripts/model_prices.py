"""Billing rates per served model id, read from the SSOT `model_prices:` block.

A transcript records the EXACT id a request ran on. The family `prices:` rows
describe only the model an alias serves TODAY, so pricing history by family
reprices an old model at the new one's rate: the routing retro once priced an
old model at its successor's rates and cache reads at 4x, and /cost-audit
dropped every request for the new model because its own table lacked the id.
This is the one table both read. Stdlib only.
"""
import re

_ROW = re.compile(
    r"^\s{2}([\w.\-]+):\s*\{\s*in:\s*([\d.]+),\s*out:\s*([\d.]+),\s*cache_read:\s*([\d.]+)"
    r"(?:,\s*cache_write_5m:\s*([\d.]+),\s*cache_write_1h:\s*([\d.]+))?",
    re.M,
)
# What may follow a table key inside a served id: nothing, a dated snapshot
# suffix, and/or a context marker. Anything else is a DIFFERENT model.
_SUFFIX = re.compile(r"(-\d{8})?(\[[^\]]*\])?")


def load(ssot_path):
    """{model_id: (in, out, cache_read, cache_write_5m, cache_write_1h)} in $/MTok; {} when the
    block is absent. A row without explicit write rates gets 1.25x / 2x of `in`."""
    with open(ssot_path, encoding="utf-8") as fh:
        text = fh.read()
    start = text.find("\nmodel_prices:\n")
    if start == -1:
        return {}
    body = text[start + len("\nmodel_prices:\n"):]
    end = re.search(r"^\S", body, re.M)
    block = body[: end.start()] if end else body
    table = {}
    for m in _ROW.finditer(block):
        inp, out, read = (float(m.group(i)) for i in (2, 3, 4))
        w5, w1 = (float(m.group(i)) if m.group(i) else inp * k for i, k in ((5, 1.25), (6, 2)))
        table[m.group(1)] = (inp, out, read, w5, w1)
    return table


def rates(table, model_id):
    """The (in, out, cache_read, cache_write_5m, cache_write_1h) row for a served id, or None.

    Longest key that the id starts with, and only when the rest of the id is a
    date or a `[1m]`-style marker: `claude-opus-5-5` never falls back to
    `claude-opus-5`, and an unknown `claude-opus-5-6` gets None, not Opus 5's
    rates."""
    mid = (model_id or "").lower()
    for key in sorted(table, key=len, reverse=True):
        if mid.startswith(key) and _SUFFIX.fullmatch(mid[len(key):]):
            return table[key]
    return None
