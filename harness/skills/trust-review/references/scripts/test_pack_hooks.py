"""Pack parser and label hooks (s03). Kept apart from test_provider_packs.py, which is a locked check."""
from __future__ import annotations

import sys

from test_provider_packs import scratch, write_pack  # noqa: F401  (scratch is a fixture)

PARSER_PACK = '''
PARSERS = {"internal-memory": lambda r: {"locator": r["key"], "title": r["topic"],
                                         "snippet": r["body"], "score": r["rank"]}}
LABELS = [("acme_mem", "internal-memory")]
'''


def test_pack_parser_and_label_hooks(scratch, tmp_path, monkeypatch):  # noqa: F811
    """A pack's parser reads its own row shape; without it the row is still kept (generic shape)."""
    import internal_corpus_adapter as C
    payload = {"results": [{"key": "r1", "topic": "T", "body": "hello", "rank": 0.5}]}
    orig = sys.modules.get("adapter_registry")
    sys.modules.pop("adapter_registry", None)
    try:
        bare = C.parse_internal_search(payload, kind=C.TYPE_MEMORY)
        assert bare == [] and C.chain_with_pack([("a", "x")]) == [("a", "x")]   # no usable key: recorded as no rows
        monkeypatch.setenv("OTL_PROVIDER_PACKS", write_pack(tmp_path, PARSER_PACK))
        sys.modules.pop("adapter_registry", None)
        rows = C.parse_internal_search(payload, kind=C.TYPE_MEMORY)
        assert [(r["locator"], r["snippet"], r["score"]) for r in rows] == [("r1", "hello", 0.5)]
        assert ("acme_mem", "internal-memory") in C.chain_with_pack([("local_vault_rg", "internal-vault")])
    finally:
        sys.modules.pop("adapter_registry", None)
        if orig is not None:
            sys.modules["adapter_registry"] = orig
