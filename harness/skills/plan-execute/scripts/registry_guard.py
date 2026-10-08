"""Shared degrade-and-record guard for registry.py's read-only sub-modules.

Split out of registry.py (was 594 LOC, over the 500 file-size limit) because
`_guarded`/`_DEGRADE_ERRORS` are used by BOTH registry_plans.py (plan
lifecycle reads) and registry_owners.py (branch/worktree ownership reads),
and putting them in either one would make the other import from it —
circular, since both are in turn imported BY registry.py. This module has no
registry-internal imports, so it can sit underneath both.
"""

import manifest_io as mio
import ship_state_io as ssio

# --------------------------------------------------------------------------
# Shared per-plan read guard (gate llm-review-low, attempt 2 — third finding
# in the same family: a read-only, repo-wide status command must never let
# ONE plan's malformed file abort the read for every other plan).
# --------------------------------------------------------------------------
# ponytail: catches every shape a hand-edited or truncated plan-side file can
# take — bad JSON (json.JSONDecodeError, a ValueError subclass), an unreadable
# file (OSError), a wrong-shape-but-valid JSON payload the caller raises
# ValueError for (e.g. a list where a dict was expected), a malformed
# manifest.json (mio.ManifestError, or AttributeError/KeyError/TypeError from
# `dispatch.next_action` walking a manifest that parsed but is missing the
# keys it expects), and a corrupt shipping-state file with no usable `.bak`
# (ssio.ShipStateError). Deliberately NOT wt.WorktreeError: registry.py's own
# docstring on `parse_worktree_porcelain` says refusing to guess a porcelain
# parse (or a `git` subprocess itself failing) is a real, repo-wide
# environment problem worth surfacing loudly — not a single plan's bad file.
_DEGRADE_ERRORS = (
    OSError, ValueError, TypeError, AttributeError, KeyError,
    mio.ManifestError, ssio.ShipStateError,
)


def _guarded(errors, plan_name, what, fn):
    """Run `fn()`; on a _DEGRADE_ERRORS failure, record it under `plan_name`
    in `errors` (mutated in place) and return None instead of propagating."""
    try:
        return fn()
    except _DEGRADE_ERRORS as e:
        errors.setdefault(plan_name, []).append(f"{what}: {e}")
        return None
