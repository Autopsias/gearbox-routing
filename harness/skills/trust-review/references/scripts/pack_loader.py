"""pack_loader.py - provider-pack discovery and merge for adapter_registry.py.

A pack is a module `provider_pack_*.py` beside the registry, or a path listed in
OTL_PROVIDER_PACKS (os.pathsep-separated). It declares data only (no registry import):
  PROVIDERS    {operation: [{"id","kind","match","note","preferred","before"}]}  inserted in order
               before the generic provider whose id is "before" (end of chain when absent)
  local_caps() -> [cap ids]            optional: capabilities the pack detects
  PARSERS {kind: callable}, LABELS [(token, label)], THEME {css-var: value}   optional hooks,
               collected in registry.PACK_HOOKS
A pack that fails to import or is malformed is skipped and recorded in registry.PACKS_FAILED,
never a crash. A pack is all-or-nothing: one bad provider id (not a non-empty str) rejects the whole pack. A pack cannot remove or reorder generic providers.
"""
from __future__ import annotations

import dataclasses
import glob
import importlib.util
import os

_KINDS = ("mcp", "local", "builtin")   # mirrors the registry KIND_* values


def _check_mcp(e) -> None:
    if e["kind"] == "mcp":   # an empty group or empty token would bind every tool
        m = e.get("match")
        if not (isinstance(m, (list, tuple)) and m and all(
                isinstance(g, (list, tuple)) and g and all(isinstance(t, str) and t.strip() for t in g)
                for g in m)):
            raise ValueError(f"mcp provider {e['id']!r} needs match = non-empty groups of non-blank strings")


def _label_rows(mod) -> list:
    raw = list(getattr(mod, "LABELS", []))
    if not all(isinstance(x, (list, tuple)) for x in raw):
        raise ValueError("LABELS rows must be (token, label) pairs of strings")
    rows = [tuple(x) for x in raw]
    if not all(len(r) == 2 and all(isinstance(v, str) for v in r) for r in rows):
        raise ValueError("LABELS rows must be (token, label) pairs of strings")
    return rows


def _merge(R, mod) -> None:
    """Validate and stage everything first; change registry state only once the whole pack is sound."""
    ops = dict(R.OPERATIONS)
    for opname, entries in dict(getattr(mod, "PROVIDERS", {})).items():
        op = ops[opname]
        chain = list(op.providers)
        for e in entries:
            if not (isinstance(e.get("id"), str) and e["id"]):
                raise ValueError(f"provider entry {e!r} needs a non-empty string id")
            if e["kind"] not in _KINDS:
                raise ValueError(f"provider {e['id']!r} has unknown kind {e['kind']!r}")
            _check_mcp(e)
            prov = R.Provider(e["id"], e["kind"], tuple(tuple(g) for g in e.get("match", ())),
                              e.get("note", ""), bool(e.get("preferred", False)))
            # No such "before" id: go ahead of the first always-on builtin, never behind it (unreachable).
            end = next((i for i, x in enumerate(chain) if x.kind == "builtin"), len(chain))
            at = next((i for i, x in enumerate(chain) if x.id == e.get("before")), end)
            if any(x.kind == "builtin" for x in chain[:at]):   # an always-on builtin answers first: it would never run
                raise ValueError(f"provider {e['id']!r} would sit behind an always-on builtin and never run")
            if prov.kind == "builtin" and any(x.kind != "builtin" for x in chain[at:]):   # it would hide those behind it
                raise ValueError(f"builtin provider {e['id']!r} would hide the providers after it")
            chain.insert(at, prov)
        ops[opname] = dataclasses.replace(op, providers=tuple(chain))
    parsers = {**R.PACK_HOOKS["parsers"], **dict(getattr(mod, "PARSERS", {}))}
    rows = _label_rows(mod)
    labels = list(R.PACK_HOOKS["labels"]) + rows
    theme = {**R.PACK_HOOKS["theme"], **dict(getattr(mod, "THEME", {}))}
    for k, fn in parsers.items():
        if not callable(fn):
            raise TypeError(f"parser {k!r} must be callable")
    local_caps = getattr(mod, "local_caps", None)
    if local_caps is not None and not callable(local_caps):
        raise TypeError("local_caps must be callable")
    R.OPERATIONS = ops
    R.PACK_HOOKS.update(parsers=parsers, labels=labels, theme=theme)


def load(R) -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    paths = sorted(glob.glob(os.path.join(here, "provider_pack_*.py")))
    paths += [p for p in os.environ.get("OTL_PROVIDER_PACKS", "").split(os.pathsep) if p]
    seen = {os.path.realpath(p) for p in R.PACKS_LOADED} | {os.path.realpath(f[0]) for f in R.PACKS_FAILED}
    for path in paths:
        real = os.path.realpath(path)   # one file reached by two spellings loads once
        if real in seen:
            continue
        seen.add(real)
        try:
            spec = importlib.util.spec_from_file_location("otl_pack_" + os.path.basename(path)[:-3], path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            _caps_of(mod)   # a pack whose capability answer is malformed never goes active
            _merge(R, mod)
        except (Exception, SystemExit) as exc:  # noqa: BLE001 - a broken pack must never break the registry
            R.PACKS_FAILED.append((path, f"{type(exc).__name__}: {exc}"))
            continue
        R._PACK_MODULES.append(mod)
        R.PACKS_LOADED.append(path)
    R.NO_TERMINAL_OPERATIONS = tuple(n for n, o in R.OPERATIONS.items() if not o.has_terminal)


def _caps_of(mod) -> list:
    """The capability ids a pack reports; raises on a malformed answer."""
    got = mod.local_caps() if hasattr(mod, "local_caps") else []
    if isinstance(got, (str, bytes)):
        raise ValueError(f"must return a list of strings, not {type(got).__name__}")
    got = list(got)
    if not all(isinstance(c, str) and c for c in got):
        raise ValueError(f"must return non-empty strings: {got!r}")
    return got


def local_caps(R) -> list:
    caps = []
    for mod in R._PACK_MODULES:
        try:
            caps += _caps_of(mod)
        except (Exception, SystemExit) as exc:  # noqa: BLE001
            R.PACKS_FAILED.append((getattr(mod, "__file__", "?"), f"local_caps: {exc}"))
    return caps
