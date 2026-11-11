"""Provider-pack hooks for the internal corpus adapter: row parsers and chain labels.

Reads `adapter_registry.PACK_HOOKS`, which the pack loader fills from any installed provider
pack. With no pack every function returns the generic answer; none of them raises.
"""
from __future__ import annotations

from typing import List, Sequence


def pack_parser(kind: str):
    """The parser a provider pack registered for `kind`, or None. Never raises.

    shortcut: one parser per kind (the frozen pack contract is `PARSERS = {kind: callable}`),
    so a second pack that registers the same kind replaces the first; chain parsers in the
    pack loader when a second pack needs the same kind.
    """
    try:
        import adapter_registry as AR  # noqa: E402 — lazy; loads packs once
        fn = AR.PACK_HOOKS["parsers"].get(kind)
    except Exception:  # noqa: BLE001 — no registry or no pack ⇒ generic shape
        return None
    return fn if callable(fn) else None


def memory_fields(r: dict, pack_parse) -> tuple:
    """(locator, title, snippet, score) for one memory row: the pack's parser if it
    returns a dict, else the generic shape (a failing pack parser falls back, never raises)."""
    if pack_parse is not None:
        try:
            got = pack_parse(r)
        except Exception:  # noqa: BLE001 — a bad pack parser must not break the search path
            got = None
        if isinstance(got, dict):
            return (got.get("locator", ""), got.get("title", ""),
                    got.get("snippet", ""), got.get("score", 0.0))
    return (r.get("id") or r.get("episode_id") or r.get("record_id") or "",
            r.get("name") or r.get("title") or "",
            r.get("snippet") or r.get("text") or r.get("content") or "",
            r.get("score", 0.0))


def chain_with_pack(base: Sequence[tuple]) -> List[tuple]:
    """`base` chain of (provider-label, source-type) with the pack's LABELS rows added
    ahead of the local-vault lane (after the semantic lane). No pack ⇒ `base` unchanged."""
    try:
        import adapter_registry as AR  # noqa: E402 — lazy; loads packs once
        extra = [tuple(x) for x in AR.PACK_HOOKS["labels"]]
    except Exception:  # noqa: BLE001 — no registry or no pack ⇒ the generic chain
        return list(base)
    chain = [row for row in base if row not in extra]
    at = next((i for i, row in enumerate(chain) if row[0] == "local_vault_rg"), len(chain))
    return chain[:at] + extra + chain[at:]
