"""§6 of the plan-isolation contract — the version gate, and the sweep that
PROVES it looked.

Authority: ``../references/plan-isolation-contract.md`` (v1, frozen 2026-08-21).
Split out of ``plan_worktree.py`` for the same measured reason ``plan_hooks.py``
was: this repo's ``pre-commit`` runs ``check_file_sizes.py --staged`` with a
500-LOC default, and the s05 rework needed that room for git mechanism, not for
this self-contained gate. ``plan_worktree`` re-exports everything here, so
``run.py`` and the tests keep one import surface.
"""

import json
import time
from pathlib import Path

# §6.1 — a missing or non-integer version is treated as BELOW the threshold, the
# same fail-low pattern the parallel-group contract §6 uses.
ISOLATION_MIN_SCHEMA = 7
# route-at-dispatch-contract.md §1: from v8 the executor picks an unpinned
# session's model and effort. A manifest above SUPPORTED_MAX_SCHEMA is refused.
ROUTE_AT_DISPATCH_MIN_SCHEMA = 8
SUPPORTED_MAX_SCHEMA = 8

_UNREADABLE = object()   # distinct from None, which a manifest of `null` parses to


def isolation_enabled(manifest, override=None):
    """``(enabled, reason)``. ``override`` is True (--isolate), False
    (--no-isolate) or None. §6.2: --no-isolate wins over everything, including
    a manifest already stamped at or above the threshold."""
    version = (manifest or {}).get("plan_schema_version")
    stamped = isinstance(version, int) and not isinstance(version, bool) \
        and version >= ISOLATION_MIN_SCHEMA
    if override is False:
        return False, f"--no-isolate (manifest plan_schema_version={version!r})"
    if override is True:
        return True, f"--isolate (manifest plan_schema_version={version!r})"
    return stamped, f"plan_schema_version={version!r} (gate >= {ISOLATION_MIN_SCHEMA})"


def route_at_dispatch_enabled(manifest, override=None):
    """``(enabled, reason)``, the same shape as ``isolation_enabled``. ``override``
    False is --no-route-at-dispatch (or env PLAN_EXECUTE_ROUTE_AT_DISPATCH=0) and
    wins. There is no force-on: a plan below v8 must dispatch byte-identically."""
    version = (manifest or {}).get("plan_schema_version")
    if override is False:
        return False, f"--no-route-at-dispatch (manifest plan_schema_version={version!r})"
    stamped = isinstance(version, int) and not isinstance(version, bool) \
        and version >= ROUTE_AT_DISPATCH_MIN_SCHEMA
    return stamped, f"plan_schema_version={version!r} (gate >= {ROUTE_AT_DISPATCH_MIN_SCHEMA})"


def record_isolation_gate(plan_dir, enabled, reason, version):
    """Log §6's gate decision AND PERSIST it, so every later stage honours it.

    `--no-isolate` is a flag on `begin` alone. `apply`, verify and shipping never
    see it, so before this the override "wins over everything" only until the next
    command returned: dispatch, every gate cwd and the whole ship step still
    routed into a worktree an earlier run had created (found by review of s06,
    2026-08-22). `plan_scope.isolation_honoured` is the reader.
    """
    import run_state_io as rsi
    rsi.log_event(plan_dir, "plan_isolation_gate", session_ids=[], enabled=enabled,
                  reason=reason, plan_schema_version=version)
    state = rsi.load_state(plan_dir)
    state["plan_isolation"] = {"enabled": bool(enabled), "reason": reason}
    rsi.save_state(plan_dir, state)


def sweep_schema_versions(scan_root):
    """Every ``<scan_root>/*/manifest.json`` and its ``plan_schema_version``.
    Reports BOTH numbers — what was scanned and what crossed — because "zero
    crossings" and "I looked at nothing" are otherwise the same result. A wrong
    glob, a wrong root, or a sweep pointed at a frozen worktree copy all report
    zero crossings; only ``manifests_found`` separates them (§6.3)."""
    root = Path(scan_root)
    found = sorted(root.glob("*/manifest.json"))
    versions, crossings = {}, []
    for f in found:
        # Guard the PARSE and the TYPE, kept DISTINCT: JSON that is not an
        # object (`[]`, `"x"`, `null`) has no `.get`, and `null` parses fine —
        # so a `None` sentinel for "unreadable" would silently relabel it.
        parsed = _UNREADABLE
        try:
            parsed = json.loads(f.read_text())
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            pass
        if parsed is _UNREADABLE:
            value = "UNREADABLE"
        elif not isinstance(parsed, dict):
            value = "NOT-AN-OBJECT"
        else:
            value = parsed.get("plan_schema_version", "MISSING")
        integral = isinstance(value, int) and not isinstance(value, bool)
        key = str(value)
        versions[key] = versions.get(key, 0) + 1
        if integral and value >= ISOLATION_MIN_SCHEMA:
            crossings.append({"manifest": str(f), "plan_schema_version": value})
    return {
        "scan_root": str(root.resolve()),
        "glob": "*/manifest.json",
        "isolation_min_schema": ISOLATION_MIN_SCHEMA,
        "manifests_found": len(found),
        "versions": dict(sorted(versions.items())),
        "crossings": crossings,
        "swept_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def isolates_the_tree(plan_dir):
    """Does this plan keep its work OUT of the shared checkout? (§6, REG-02)

    The reader for ``begin``'s REG-02 guard, which asks it of every OTHER plan
    active in the repo. Two plans that both isolate never share a working tree,
    so the refusal that exists to stop them racing it has nothing to stop.

    Recorded answer first (``plan_isolation`` in run_state, written by
    ``record_isolation_gate``), because ``--no-isolate`` is a flag on ``begin``
    alone and the manifest cannot see it. Falls back to the manifest.

    Fail-LOW, unlike ``plan_scope.isolation_honoured``: an unreadable manifest is
    treated as NOT isolating. The two callers want opposite safe directions —
    that one is choosing a cwd and must not aim at the operator's checkout, this
    one is deciding whether it is safe to SHARE that checkout, and an unknown
    neighbour is exactly the one to keep refusing on.
    """
    import run_state_io as rsi
    try:
        recorded = (rsi.load_state(plan_dir).get("plan_isolation") or {})
    except Exception:                                  # noqa: BLE001
        recorded = {}
    if isinstance(recorded.get("enabled"), bool):
        return recorded["enabled"]
    try:
        import manifest_io as mio
        return bool(isolation_enabled(mio.load_manifest(plan_dir))[0])
    except Exception:                                  # noqa: BLE001
        return False
