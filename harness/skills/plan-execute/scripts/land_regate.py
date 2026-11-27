"""Keep a land's gate results across a main move that touched only files no gate reads.

`apply_or_reuse` replaces the single `land_carry.decide`/`apply` call in `step_gate`.
Whatever `decide` would carry (a runtime-only harvest, which keeps the ack) is left to
it. Otherwise, when main moved ONLY in docs and record files, the previous candidate's
gate results are copied to the new key and the gates are not run again.

Reused only when ALL hold:
  * every check of `land_carry` passes except the two that classify the move
    (`harness-class`, `force-reset-path`): main moved, the plan side is unchanged, the
    gate set and the registry are the same;
  * every path main touched is under `docs/` or `_plans/`, contains `/memory/`, or is
    `evals/routing/outcomes.ndjson` or `_plans_index.md`; none is a `.py`, a `spec.json` or a
    `manifest.json` (tests read the real `_plans/*/spec.json`); none is under `skills/ hooks/ rules/ agents/
    scripts/ commands/`, none is a `CLAUDE.md` or `settings*.json`, and none is on
    `land_carry`'s force-reset list (`conftest.py`, `Makefile`, `requirements*.txt` ...);
  * every gate of the new set holds a settled pass stamped with the previous key.

The ack binding is NOT touched: the candidate's shas change, so the ack goes to
`invalidated_from` exactly as on a reset, and `step_ack` asks again. What carries over
is the gate results, and so the digest.
"""

from fnmatch import fnmatch

import land_carry as lcy
import land_main_tests as lmt
import run_state_io as rsi
from land_state import now, save

precheck = lmt.precheck
REASON = "docs-only-reuse"
_DENY_PREFIXES = ("skills/", "hooks/", "rules/", "agents/", "scripts/", "commands/")
_ALLOW_PREFIXES = ("docs/", "_plans/")
_RECORDS = ("evals/routing/outcomes.ndjson", "_plans_index.md")
_READ_BY_TESTS = ("spec.json", "manifest.json")     # tests glob the real _plans/*/spec.json
_CLASS_ONLY = ("harness-class", "force-reset-path")
_TABLES = (("skill_gates", "pass"), ("argv_gates", "pass"), ("on_box_gates", "verified-on-box"))


def reads_no_gate(path):
    """True when a move of `path` cannot change a gate result. Denied first, allowed second."""
    name = path.rsplit("/", 1)[-1]
    if (path.startswith(_DENY_PREFIXES) or name == "CLAUDE.md" or fnmatch(name, "settings*.json")
            or name.endswith(".py") or name in _READ_BY_TESTS):
        return False
    return path.startswith(_ALLOW_PREFIXES) or "/memory/" in path or path in _RECORDS


def _kept(st, gates, old_key):
    """{table: {gate_id: record}} of the settled passes stamped `old_key`, or None when any
    gate of the new set lacks one."""
    kept = {field: {} for field, _ in _TABLES}
    for gid, _ in gates:
        for field, settled in _TABLES:
            rec = (st.get(field) or {}).get(gid)
            if isinstance(rec, dict) and rec.get("gate_key") == old_key and rec.get("outcome") == settled:
                kept[field][gid] = rec
                break
        else:
            return None
    return kept


def _files(plan_dir, ctx, st, b, n):
    """The paths main moved when only the move itself differs from a carry, else None."""
    out = {}
    for reason, failed, _ in lcy._checks(plan_dir, ctx, st, b, n, out):
        if failed and reason not in _CLASS_ONLY:
            return None
    files = out.get("moved_paths") or []
    ok = files and all(reads_no_gate(p) for p in files) and not lcy.force_reset_hits(files, set(files))
    return files if ok else None


def _reusable(plan_dir, ctx, st, gates, verdict):
    """(files, kept) when the previous results may stand for the new candidate, else None."""
    b, n = st.get("carry_basis"), verdict.get("basis")
    if lcy._legacy(b) or not n:
        return None
    try:
        files = _files(plan_dir, ctx, st, b, n)
    except Exception:  # noqa: BLE001 — a fault never reuses a result
        return None
    kept = None if files is None else _kept(st, gates, lcy._basis_key(b))
    return None if kept is None else (files, kept)


def apply_or_reuse(plan_dir, ctx, st, gates, key, set_key):
    """`land_carry.apply(decide)`, or the docs-only reuse described above."""
    verdict = lcy.decide(plan_dir, ctx, st, set_key)
    got = None if verdict["decision"] == "carry" else _reusable(plan_dir, ctx, st, gates, verdict)
    st.pop("regate_reuse", None)
    if got is None:
        return lcy.apply(plan_dir, ctx, st, gates, key, verdict)
    files, kept = got
    old_key, old_main, digest = lcy._basis_key(st["carry_basis"]), st["carry_basis"]["main_head"], st.get("gate_digest")
    lcy.apply(plan_dir, ctx, st, gates, key, {**verdict, "reason": REASON, "route": "docs",
                                              "moved_paths": files})
    for field, recs in kept.items():
        st[field] = {gid: {**rec, "gate_key": key, "reused_from": old_key} for gid, rec in recs.items()}
    st["regate_reuse"] = {"at": now(), "files": files, "from_main": old_main,
                          "to_main": st["expected"], "from_key": old_key, "gate_digest": digest}
    save(plan_dir, ctx, st)
    rsi.log_event(plan_dir, "plan_land_gates_reused", session_ids=[], slug=ctx["slug"],
                  files=files[:100], count=len(files), from_main=old_main, to_main=st["expected"],
                  from_key=old_key, to_key=key, gate_digest=digest)
    return None


def note(st):
    """The brief line for a candidate whose gate results were reused, else None."""
    r = st.get("regate_reuse")
    return f"gates reused: main moved only in {len(r['files'])} docs/record files" if r else None
